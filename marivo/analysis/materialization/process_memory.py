"""Read worker resident memory without spawning a process for every sample."""

from __future__ import annotations

import ctypes
import errno
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


class _TaskInfo(ctypes.Structure):
    # Darwin's proc_taskinfo from sys/proc_info.h: six uint64_t values followed
    # by twelve int32_t counters. The second uint64_t is resident bytes.
    _fields_ = [("sizes", ctypes.c_uint64 * 6), ("counters", ctypes.c_int32 * 12)]


@lru_cache(maxsize=1)
def _libproc() -> ctypes.CDLL:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidinfo.restype = ctypes.c_int
    return library


def _darwin_rss(pid: int) -> int:
    info = _TaskInfo()
    ctypes.set_errno(0)
    size: object = _libproc().proc_pidinfo(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info):
        code = ctypes.get_errno()
        if code == errno.ESRCH:
            return 0
        raise OSError(code or errno.EIO, "Cannot read worker resident memory")
    return int(info.sizes[1])


def _linux_rss(pid: int) -> int:
    try:
        resident_pages = Path(f"/proc/{pid}/statm").read_text().split()[1]
    except (FileNotFoundError, ProcessLookupError):
        return 0
    return int(resident_pages) * os.sysconf("SC_PAGE_SIZE")


def resident_bytes(pid: int) -> int:
    """Read current RSS; only a vanished process is treated as zero on native paths."""
    if sys.platform == "darwin":
        return _darwin_rss(pid)
    if sys.platform.startswith("linux"):
        return _linux_rss(pid)
    value = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        timeout=0.5,
        check=False,
    )
    return int(value.stdout.strip() or b"0") * 1024
