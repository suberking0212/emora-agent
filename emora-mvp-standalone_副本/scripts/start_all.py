"""Start local Guard before Web, with one foreground lifecycle for both."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from dotenv import dotenv_values

from launch import ROOT, listeners, processes, terminate

SCRIPT = Path(__file__).resolve()
PID_FILE = ROOT / ".emora-stack.pid"
PYTHON = ROOT / ".venv/bin/python"
LAUNCH = ROOT / "scripts/launch.py"


def stop_previous():
    rows = processes()
    saved = PID_FILE.read_text().strip() if PID_FILE.exists() else ""
    targets = set()
    for pid, (_, command) in rows.items():
        # ps does not quote argv paths containing spaces. Match the full script
        # suffix and require the saved PID or a proven service-launcher child.
        script_command = command.endswith(f" {SCRIPT}") or command.endswith(f" {SCRIPT} --stop")
        has_launcher = any(parent == pid and str(LAUNCH) in child_command
                           for parent, child_command in rows.values())
        if pid != os.getpid() and script_command and (saved == str(pid) or has_launcher):
            targets.add(pid)
    if targets:
        print(f"Stopping previous Emora stack PID(s): {sorted(targets)}", flush=True)
        terminate(targets)
    PID_FILE.unlink(missing_ok=True)


def launch_command(guard=False):
    return [str(PYTHON), "-u", str(LAUNCH)] + (["--guard"] if guard else [])


def wait_service(process, *, port, url, pid_file, timeout, label):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} launcher exited; inspect its terminal logs")
        saved = pid_file.read_text().strip() if pid_file.exists() else ""
        if saved.isdecimal():
            pid = int(saved)
            parent = processes().get(pid, (None, ""))[0]
            if parent == process.pid and pid in listeners(port):
                try:
                    with opener.open(f"{url}/health", timeout=1) as response:
                        if response.status == 200:
                            return
                except OSError:
                    pass
        time.sleep(0.2)
    raise RuntimeError(f"{label} startup timed out")


def main():
    if sys.argv[1:] not in ([], ["--stop"]):
        raise RuntimeError("Usage: ./start.sh [--stop]")
    os.chdir(ROOT)
    children = []

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with (ROOT / ".emora-stack-start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            stop_previous()
            # Also recover services left by legacy or manual launchers.
            subprocess.run(launch_command() + ["--stop"], check=True)
            config = {**dotenv_values(ROOT / ".env"), **os.environ}
            endpoint = urlsplit(config.get("EMORA_QWEN_GUARD_BASE_URL", "http://127.0.0.1:8001/v1"))
            local_guard = endpoint.scheme == "http" and endpoint.hostname in ("127.0.0.1", "localhost")
            if local_guard:
                subprocess.run(launch_command(True) + ["--stop"], check=True)
            if sys.argv[1:] == ["--stop"]:
                return 0

            PID_FILE.write_text(f"{os.getpid()}\n")
            if local_guard:
                guard_port = endpoint.port or 80
                if guard_port == int(os.environ.get("EMORA_PORT", "8000")):
                    raise RuntimeError("Guard and Web must use different ports")
                print("Starting local Guard first; waiting for model readiness…", flush=True)
                guard = subprocess.Popen(launch_command(True), start_new_session=True)
                children.append(guard)
                wait_service(guard, port=guard_port, url=f"http://127.0.0.1:{guard_port}",
                             pid_file=ROOT / ".emora-guard.pid", timeout=185, label="Guard")
                print(f"Emora Guard ready at http://127.0.0.1:{guard_port}/; starting Web…", flush=True)
            else:
                print("Using configured external Guard; no local model process started.", flush=True)

            web = subprocess.Popen(launch_command(), start_new_session=True)
            children.append(web)
            port = int(os.environ.get("EMORA_PORT", "8000"))
            host = os.environ.get("EMORA_HOST", "127.0.0.1")
            probe = "127.0.0.1" if host == "0.0.0.0" else "::1" if host == "::" else host
            authority = f"[{probe}]" if ":" in probe else probe
            wait_service(web, port=port, url=f"http://{authority}:{port}",
                         pid_file=ROOT / ".emora-uvicorn.pid", timeout=35, label="Web")
            print("Emora stack ready. Guard and Web logs appear here. Press Ctrl+C to stop both.", flush=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
            while True:
                for child in children:
                    if child.poll() is not None:
                        raise RuntimeError("An Emora service exited; stopping the remaining service")
                time.sleep(0.2)
        except KeyboardInterrupt:
            return 0
        finally:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            for child in reversed(children):
                if child.poll() is None:
                    terminate({child.pid})
                child.wait()
            if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, PermissionError, subprocess.CalledProcessError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
