"""Scoped restart, actual server PID, fixed port, readiness and foreground logs."""
import fcntl
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
GUARD = "--guard" in sys.argv[1:]
if GUARD:
    sys.argv.remove("--guard")
MODULE = "scripts.guard_server:app" if GUARD else "backend.app:app"
LABEL = "Guard" if GUARD else "backend"
PID_FILE = ROOT / (".emora-guard.pid" if GUARD else ".emora-uvicorn.pid")


def output(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Process inspection failed: {command[0]}")
    return result.stdout


def processes():
    rows = {}
    for line in output(["ps", "-axo", "pid=,ppid=,command="]).splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows[int(parts[0])] = (int(parts[1]), parts[2])
    return rows


def owned(pid, command):
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False
    if MODULE not in arguments or not any(
        argument == "uvicorn" or argument.endswith("/uvicorn") for argument in arguments
    ):
        return False
    if f"{ROOT}/" in command:
        return True
    # Recognize manual / legacy launches with relative command paths too.
    cwd = output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return f"n{ROOT}" in cwd.splitlines()


def alive(pid):
    try:
        os.kill(pid, 0)
        return output(["ps", "-p", str(pid), "-o", "stat="]).strip()[:1] != "Z"
    except ProcessLookupError:
        return False


def terminate(pids):
    # Save descendants BEFORE signalling, since they can become orphans.
    rows = processes()
    targets = set(pids)
    while True:
        children = {pid for pid, (parent, _) in rows.items() if parent in targets}
        if children <= targets:
            break
        targets |= children
    targets -= {os.getpid(), os.getppid()}
    for sig, seconds in ((signal.SIGTERM, 6), (signal.SIGKILL, 3)):
        pending = {pid for pid in targets if alive(pid)}
        for pid in pending:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + seconds
        while pending and time.monotonic() < deadline:
            time.sleep(0.1)
            pending = {pid for pid in pending if alive(pid)}
        if not pending:
            return
    raise RuntimeError(f"Could not stop old processes: {sorted(pending)}")


def listeners(port):
    return {int(pid) for pid in output([
        "lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t",
    ]).split()}


def stop_old():
    rows = processes()
    servers = {pid for pid, (_, command) in rows.items() if owned(pid, command)}
    if PID_FILE.exists():
        saved = PID_FILE.read_text().strip()
        if saved.isdecimal() and int(saved) in rows:
            pid = int(saved)
            _, command = rows[pid]
            # Legacy PID files point to bash; require a proven project child.
            if pid in servers or (any(rows[s][0] == pid for s in servers)
                    and ("start.sh" in command or "launch.py" in command)):
                servers.add(pid)
    if servers:
        print(f"Stopping previous standalone PID(s): {sorted(servers)}", flush=True)
        terminate(servers)
        print("Previous standalone processes stopped.", flush=True)
    else:
        print(f"No previous standalone {LABEL} process found.", flush=True)
    PID_FILE.unlink(missing_ok=True)


def main():
    if sys.argv[1:] not in ([], ["--stop"]):
        raise RuntimeError("Usage: ./start.sh [--stop]")
    os.chdir(ROOT)
    environment = os.environ.copy()
    if GUARD:
        from dotenv import dotenv_values
        config = {**dotenv_values(ROOT / ".env"), **environment}
        endpoint = urlparse(config.get("EMORA_QWEN_GUARD_BASE_URL", "http://127.0.0.1:8001/v1"))
        if endpoint.scheme != "http" or endpoint.hostname not in ("127.0.0.1", "localhost"):
            raise RuntimeError("Local Guard launcher requires an http://127.0.0.1:<port>/v1 endpoint")
        host, port = "127.0.0.1", endpoint.port or 80
        environment.update({key: value for key, value in config.items()
                            if key.startswith("EMORA_QWEN_GUARD_") and value is not None})
        python = ROOT / ".guard-venv/bin/python"
        model = config.get("EMORA_QWEN_GUARD_MODEL", "Qwen/Qwen3Guard-Gen-0.6B")
        if sys.argv[1:] != ["--stop"] and (
            not python.exists() or not (ROOT / ".guard-models" / model.rsplit("/", 1)[-1]).exists()
        ):
            raise RuntimeError("Guard not installed. Run ./install-guard.sh first")
    else:
        host = os.environ.get("EMORA_HOST", "127.0.0.1")
        port = int(os.environ.get("EMORA_PORT", "8000"))
        python = ROOT / ".venv/bin/python"
    if not 1 <= port <= 65535:
        raise RuntimeError("EMORA_PORT must be between 1 and 65535")
    # Never unlink this file: a new inode would defeat the startup lock.
    with (ROOT / (".emora-guard-start.lock" if GUARD else ".emora-start.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stop_old()
        if sys.argv[1:] == ["--stop"]:
            return 0
        occupied = listeners(port)
        if occupied:
            raise RuntimeError(f"Port {port} is occupied by unrelated PID(s) {sorted(occupied)}; refusing to switch ports or kill another application.")
        log_path = ROOT / ("backend/data/guard.log" if GUARD else "backend/data/server.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", buffering=1) as log:
            process = subprocess.Popen([
                str(python), "-u", "-m", "uvicorn",
                MODULE, "--host", host, "--port", str(port),
            ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True, env=environment)
            PID_FILE.write_text(f"{process.pid}\n")

            def interrupted(signum, frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)

            def relay():
                for line in process.stdout:
                    log.write(line)
                    if GUARD and "Uvicorn running on http://" in line:
                        continue
                    try:
                        print(line, end="", flush=True)
                    except (BrokenPipeError, OSError):
                        pass

            thread = threading.Thread(target=relay, daemon=True)
            thread.start()
            try:
                print(f"Starting {LABEL} PID {process.pid} on fixed port {port}…", flush=True)
                probe = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
                authority = f"[{probe}]" if ":" in probe else probe
                url = f"http://{authority}:{port}"
                deadline = time.monotonic() + (180 if GUARD else 30)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"{LABEL} exited during startup (status {process.returncode}); see {log_path}")
                    if process.pid in listeners(port):
                        try:
                            with opener.open(f"{url}/health", timeout=1) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"{LABEL} did not become healthy before startup timeout")
                if GUARD:
                    print(f"Qwen3Guard internal service ready on port {port}; no web UI (PID {process.pid}). Press Ctrl+C to stop. Logs: {log_path}", flush=True)
                else:
                    print(f"Emora Web ready at {url}/ (PID {process.pid}). Press Ctrl+C to stop. Logs: {log_path}", flush=True)
                fcntl.flock(lock, fcntl.LOCK_UN)
                return process.wait()
            except KeyboardInterrupt:
                return 0
            finally:
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                if process.poll() is None:
                    terminate({process.pid})
                process.wait()
                thread.join(timeout=3)
                # Superseded launchers must not erase their replacement's PID.
                fcntl.flock(lock, fcntl.LOCK_EX)
                if PID_FILE.exists() and PID_FILE.read_text().strip() == str(process.pid):
                    PID_FILE.unlink()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, PermissionError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
