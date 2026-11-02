"""Exercise real processes/sockets in isolated project directories, not live data."""
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    pytest.fail("Process condition did not become true before timeout")


def running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


@pytest.fixture
def project(tmp_path):
    tmp_path = tmp_path / "standalone with spaces"
    tmp_path.mkdir()
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/launch.py", tmp_path / "scripts/launch.py")
    (tmp_path / ".venv").symlink_to(ROOT / ".venv", target_is_directory=True)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/__init__.py").touch()
    (tmp_path / "backend/app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n'
        '@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "EMORA_PORT": str(port)}
    command = [str(tmp_path / ".venv/bin/python"), "-u", str(tmp_path / "scripts/launch.py")]
    launched, handles = [], []

    def start():
        handle = (tmp_path / f"launch-{len(launched)}.log").open("w+")
        handles.append(handle)
        process = subprocess.Popen(command, cwd=tmp_path, env=env,
                                   stdout=handle, stderr=subprocess.STDOUT)
        launched.append(process)
        wait_for(lambda: "ready at" in Path(handle.name).read_text() or process.poll() is not None)
        assert process.poll() is None, Path(handle.name).read_text()
        return process, int((tmp_path / ".emora-uvicorn.pid").read_text())

    yield tmp_path, port, command, env, start
    subprocess.run(command + ["--stop"], cwd=tmp_path, env=env, capture_output=True, timeout=20)
    for process in launched:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
    for handle in handles:
        handle.close()


def test_three_restarts_replace_server_and_keep_one_listener(project):
    path, port, _, _, start = project
    previous = None
    for _ in range(3):
        launcher, server = start()
        if previous:
            wait_for(lambda: not running(previous))
        assert int((path / ".emora-uvicorn.pid").read_text()) == server
        listeners = subprocess.check_output(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True,
        ).split()
        assert listeners == [str(server)]
        assert launcher.pid != server
        previous = server


def test_missing_pid_file_still_cleans_project_server(project):
    path, _, _, _, start = project
    _, old = start()
    (path / ".emora-uvicorn.pid").unlink()
    _, new = start()
    assert old != new
    wait_for(lambda: not running(old))


def test_sigint_cleans_server_and_pid_file(project):
    path, _, _, _, start = project
    launcher, server = start()
    launcher.send_signal(signal.SIGINT)
    assert launcher.wait(timeout=15) == 0
    assert not running(server)
    assert not (path / ".emora-uvicorn.pid").exists()
    assert "GET /health" in (path / "backend/data/server.log").read_text()
    assert "GET /health" in (path / "launch-0.log").read_text()


def test_foreign_port_and_stale_pid_are_preserved(project):
    path, port, command, env, _ = project
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", port))
        foreign.listen()
        (path / ".emora-uvicorn.pid").write_text(str(os.getpid()))
        result = subprocess.run(command, cwd=path, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 1
        assert "refusing to switch ports" in result.stderr
        assert running(os.getpid())
        assert foreign.getsockname()[1] == port
        assert not (path / ".emora-uvicorn.pid").exists()


def test_manual_relative_uvicorn_launch_is_replaced(project):
    path, port, _, _, start = project
    manual = subprocess.Popen([str(path / ".venv/bin/python"), "-m", "uvicorn",
                               "backend.app:app", "--port", str(port)], cwd=path,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                                       capture_output=True).returncode == 0)
        _, replacement = start()
        assert replacement != manual.pid
        wait_for(lambda: manual.poll() is not None)
    finally:
        if manual.poll() is None:
            manual.kill()
        manual.wait()


def test_unresponsive_parent_and_child_are_forcibly_stopped(project):
    path, _, _, _, start = project
    script = (
        'import signal,subprocess,sys,time; '
        'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
        'child=subprocess.Popen([sys.executable,"-c",'
        '"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"]); '
        'print(child.pid,flush=True); time.sleep(60)'
    )
    old = subprocess.Popen([str(path / ".venv/bin/python"), "-c", script,
                            "uvicorn", "backend.app:app"], cwd=path,
                           stdout=subprocess.PIPE, text=True)
    child = int(old.stdout.readline())
    try:
        _, replacement = start()
        assert replacement != old.pid
        wait_for(lambda: old.poll() is not None and not running(child))
        assert old.returncode == -signal.SIGKILL
    finally:
        if old.poll() is None:
            old.kill()
        old.wait()
        if running(child):
            os.kill(child, signal.SIGKILL)


@pytest.fixture
def stack_project(project):
    path, web_port, _, env, _ = project
    shutil.copy(ROOT / "scripts/start_all.py", path / "scripts/start_all.py")
    # Lightweight health service: exercise process orchestration without model inference.
    shutil.copy(path / "backend/app.py", path / "scripts/guard_server.py")
    (path / "scripts/__init__.py").touch()
    (path / ".guard-venv").symlink_to(ROOT / ".venv", target_is_directory=True)
    (path / ".guard-models/Qwen3Guard-Gen-0.6B").mkdir(parents=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        guard_port = probe.getsockname()[1]
    env = {**env, "EMORA_QWEN_GUARD_BASE_URL": f"http://127.0.0.1:{guard_port}/v1",
           "EMORA_QWEN_GUARD_MODEL": "Qwen/Qwen3Guard-Gen-0.6B"}
    command = [str(path / ".venv/bin/python"), "-u", str(path / "scripts/start_all.py")]
    launched, handles = [], []

    def start():
        handle = (path / f"stack-{len(launched)}.log").open("w+")
        handles.append(handle)
        process = subprocess.Popen(command, cwd=path, env=env,
                                   stdout=handle, stderr=subprocess.STDOUT)
        launched.append(process)
        wait_for(lambda: "Emora stack ready" in Path(handle.name).read_text() or process.poll() is not None)
        assert process.poll() is None, Path(handle.name).read_text()
        web = int((path / ".emora-uvicorn.pid").read_text())
        guard = int((path / ".emora-guard.pid").read_text())
        return process, web, guard

    yield path, web_port, guard_port, command, env, start
    subprocess.run(command + ["--stop"], cwd=path, env=env, capture_output=True, timeout=25)
    for process in launched:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
    for handle in handles:
        handle.close()


def test_stack_restarts_both_services_with_one_listener_each(stack_project):
    path, web_port, guard_port, _, _, start = stack_project
    previous = None
    for _ in range(3):
        _, web, guard = start()
        if previous:
            wait_for(lambda: all(not running(pid) for pid in previous))
        for port, pid in ((web_port, web), (guard_port, guard)):
            assert subprocess.check_output(
                ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True,
            ).split() == [str(pid)]
        previous = (web, guard)
    log = (path / "stack-2.log").read_text()
    assert log.index("Emora Guard ready") < log.index("Starting backend PID")
    assert "GET /health" in log


def test_stack_sigint_stops_guard_and_web(stack_project):
    path, _, _, _, _, start = stack_project
    process, web, guard = start()
    process.send_signal(signal.SIGINT)
    assert process.wait(timeout=20) == 0
    assert not running(web) and not running(guard)
    for name in (".emora-stack.pid", ".emora-uvicorn.pid", ".emora-guard.pid"):
        assert not (path / name).exists()


def test_stack_refuses_missing_guard_without_starting_web(stack_project):
    path, _, _, command, env, _ = stack_project
    shutil.rmtree(path / ".guard-models")
    result = subprocess.run(command, cwd=path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert "Guard not installed" in result.stderr
    assert not (path / ".emora-uvicorn.pid").exists()


def test_local_guard_http_transport_ignores_proxy(project, monkeypatch):
    import asyncio
    from backend.safety import Qwen3GuardClient

    _, port, _, _, start = project
    start()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")

    async def probe():
        client = Qwen3GuardClient(base_url=f"http://127.0.0.1:{port}", api_key="EMPTY",
                                  model_id="unused", timeout_seconds=2)
        try:
            assert await client.client.get("/health", cast_to=dict[str, str]) == {"status": "ok"}
        finally:
            await client.close()

    asyncio.run(probe())
