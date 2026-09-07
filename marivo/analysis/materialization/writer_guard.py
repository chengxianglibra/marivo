"""Non-blocking local Session exclusion, separate from SQLite transactions."""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from marivo.analysis.materialization.errors import MaterializationError, SessionBusyError

_MUTEX = threading.Lock()
_LOCKS: dict[tuple[int, str], threading.Lock] = {}
_DESCRIPTORS: set[int] = set()


def _after_fork_child() -> None:
    global _MUTEX, _LOCKS, _DESCRIPTORS
    # Closing the inherited descriptor drops this child's reference. LOCK_UN
    # would instead unlock the parent's shared open-file description.
    for descriptor in _DESCRIPTORS:
        os.close(descriptor)
    _DESCRIPTORS = set()
    _LOCKS = {}
    _MUTEX = threading.Lock()


os.register_at_fork(
    before=lambda: _MUTEX.acquire(),
    after_in_parent=lambda: _MUTEX.release(),
    after_in_child=_after_fork_child,
)


def _busy() -> SessionBusyError:
    return SessionBusyError(
        expected="one active writer for this Session",
        received="an active Session writer",
        repair="Wait for the current Session action to finish, then retry the same definition.",
        stage="writer_guard",
    )


@contextmanager
def session_writer_guard(lock_path: Path) -> Iterator[None]:
    """Hold process-local and OS exclusion without waiting or admitting a contender."""
    canonical = lock_path.absolute()
    if canonical.is_symlink() or any(parent.is_symlink() for parent in canonical.parents):
        raise MaterializationError(
            expected="a local non-symlink Session lock path",
            received="an indirect lock path",
            repair="Place the project generation on a supported local filesystem.",
            stage="writer_guard",
        )
    key = (os.getpid(), str(canonical))
    with _MUTEX:
        process_lock = _LOCKS.setdefault(key, threading.Lock())
    if not process_lock.acquire(blocking=False):
        raise _busy()
    descriptor: int | None = None
    locked = False
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        with _MUTEX:
            descriptor = os.open(canonical, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            _DESCRIPTORS.add(descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _busy() from None
        locked = True
        yield
    finally:
        try:
            if descriptor is not None:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                with _MUTEX:
                    os.close(descriptor)
                    _DESCRIPTORS.remove(descriptor)
        finally:
            process_lock.release()
