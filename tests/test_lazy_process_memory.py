"""Resident-memory probes preserve worker admission without monitor subprocesses."""

import ctypes
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.analysis.materialization import process_memory as memory


def test_native_probe_reads_current_process_without_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        pytest.skip("native resident-memory probe requires Darwin or Linux")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Resident-memory sampling spawned a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert memory.resident_bytes(os.getpid()) > 0


@pytest.mark.parametrize("contents", ["100 7 4 0 0 0 0", "100 0 0 0 0 0 0"])
def test_linux_probe_converts_resident_pages(
    monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    monkeypatch.setattr(Path, "read_text", lambda self: contents)
    monkeypatch.setattr(os, "sysconf", lambda name: 4096)
    assert memory._linux_rss(42) == int(contents.split()[1]) * 4096


@pytest.mark.parametrize("error", [FileNotFoundError(), ProcessLookupError(), PermissionError()])
def test_linux_probe_only_ignores_vanished_process(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    def unreadable(self: Path) -> str:
        raise error

    monkeypatch.setattr(Path, "read_text", unreadable)
    if isinstance(error, (FileNotFoundError, ProcessLookupError)):
        assert memory._linux_rss(42) == 0
    else:
        with pytest.raises(PermissionError):
            memory._linux_rss(42)


@pytest.mark.parametrize("code", [errno.ESRCH, errno.EPERM, 0])
def test_darwin_probe_rejects_unreadable_or_incomplete_task_info(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    class Unavailable:
        def proc_pidinfo(self, *args: object) -> int:
            ctypes.set_errno(code)
            return 0

    monkeypatch.setattr(memory, "_libproc", Unavailable)
    if code == errno.ESRCH:
        assert memory._darwin_rss(42) == 0
    else:
        with pytest.raises(OSError) as caught:
            memory._darwin_rss(42)
        assert caught.value.errno == (code or errno.EIO)
