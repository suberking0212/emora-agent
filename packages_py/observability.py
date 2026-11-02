"""通过显式字段白名单序列化运行事件，避免记录隐私正文。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from packages_py.schemas import RunEvent

_FIELDS = set(RunEvent.model_fields)


class EventWriter:
    """追加白名单内的运行元数据，不记录用户正文。"""
    def __init__(self, path: Path | None):
        self.path = path

    @contextmanager
    def _lock(self):
        import fcntl

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def emit(self, event: RunEvent) -> None:
        if self.path is None:
            return
        payload = event.model_dump(mode="json", include=_FIELDS)
        payload["input_summary"] = None
        with self._lock():
            fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def purge_session(self, session_id) -> None:
        if self.path is None or not self.path.exists():
            return
        with self._lock():
            # 在与追加操作相同的文件锁内重写日志，避免删除与并发追加相互覆盖。
            retained = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if json.loads(line).get("session_id") != str(session_id):
                    retained.append(line)
            fd, name = tempfile.mkstemp(dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write("\n".join(retained) + ("\n" if retained else ""))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)


class Timer:
    """使用单调时钟计算耗时，避免系统时间调整影响结果。"""
    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, *_: object) -> None:
        self.duration_ms = int((time.monotonic() - self.started) * 1000)
