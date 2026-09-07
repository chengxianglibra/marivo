"""Actual thread/process exclusion independent of SQLite publication."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import pytest

from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.writer_guard import session_writer_guard


def test_guard_reentrant_thread_and_process_contention(tmp_path: Path) -> None:
    lock = tmp_path / "session.lock"

    def contend() -> None:
        with pytest.raises(SessionBusyError), session_writer_guard(lock):
            pytest.fail("contender entered")

    with session_writer_guard(lock):
        contend()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contend).result(timeout=5)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from pathlib import Path\n"
                "import sys\n"
                "from marivo.analysis.materialization.writer_guard import session_writer_guard\n"
                "from marivo.analysis.materialization.errors import SessionBusyError\n"
                "try:\n"
                "    with session_writer_guard(Path(sys.argv[1])): pass\n"
                "except SessionBusyError:\n"
                "    print('busy')\n",
                str(lock),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.stdout.strip() == "busy"
        with session_writer_guard(tmp_path / "independent.lock"):
            pass
    with session_writer_guard(lock):
        pass


def test_guard_releases_on_failure(tmp_path: Path) -> None:
    lock = tmp_path / "session.lock"
    with pytest.raises(RuntimeError, match="injected"), session_writer_guard(lock):
        raise RuntimeError("injected")
    with session_writer_guard(lock):
        pass


def test_fork_child_cannot_keep_dead_owner_session_locked(tmp_path: Path) -> None:
    lock = tmp_path / "session.lock"
    script = """
import os
import sys
import time
from pathlib import Path
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.writer_guard import session_writer_guard

with session_writer_guard(Path(sys.argv[1])):
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            with session_writer_guard(Path(sys.argv[1])):
                status = b"unlocked"
        except SessionBusyError:
            status = b"busy"
        os.write(write_fd, status)
        os.close(write_fd)
        os.close(1)
        os.close(2)
        time.sleep(30)
        os._exit(0)
    os.close(write_fd)
    status = os.read(read_fd, 16).decode()
    os.close(read_fd)
    print(child, status, flush=True)
    os._exit(0)
"""
    producer = subprocess.run(
        [sys.executable, "-B", "-c", script, str(lock)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    child_text, status = producer.stdout.split()
    child = int(child_text)
    try:
        # The child must close its inherited descriptor without unlocking the parent.
        assert status == "busy"
        # The child remains alive, but only the exited producer owned the lock.
        os.kill(child, 0)
        with session_writer_guard(lock):
            pass
    finally:
        with suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)
