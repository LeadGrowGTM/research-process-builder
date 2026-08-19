from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time
from typing import Iterator


_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[Path, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(resolved, threading.RLock())


@contextmanager
def file_lock(path: Path, *, timeout_seconds: float = 10) -> Iterator[None]:
    """Hold a thread- and process-safe lock released automatically on process exit."""
    path = Path(path)
    with _thread_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    _lock_stream(stream)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out waiting for file lock: {path}")
                    time.sleep(0.01)
            try:
                yield
            finally:
                _unlock_stream(stream)


def _lock_stream(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
