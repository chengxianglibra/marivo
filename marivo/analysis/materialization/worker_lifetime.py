"""Exact inherited file-description lifetime proofs for private local workers.

Under the Session writer guard, reservation precedes exclusive file creation,
and spawning requires the locked exact file. Runtime cleanup may remove that
file or its workspace only after proving every execution terminal; cleanup
must never unlink a live lifetime file. Consequently an absent file means the
worker was never started or its proven-terminal workspace was already cleaned.
The resource journal may outlive either condition when durable discharge fails.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from marivo.analysis.materialization.contracts import ResourceRecord
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore

WORKER_CAPABILITY = "pandas_worker_file_lifetime@v1"
WORKSPACE_CAPABILITY = "pandas_worker_workspace@v1"


@dataclass(frozen=True, slots=True, repr=False)
class WorkerReservation:
    execution: ResourceRecord
    workspace: ResourceRecord
    path: Path


def reserve_worker(store: SessionStore, run_ref: str, session_ref: str) -> WorkerReservation:
    """Journal both obligations before the workspace or worker can be created."""
    nonce = uuid4().hex
    workspace = store.layout.run_dir(session_ref, run_ref) / f"worker-{nonce}"
    path = workspace / f"lifetime-{nonce}.lock"
    result = WorkerReservation(
        ResourceRecord(
            run_ref,
            "backend_execution",
            "pandas@v1",
            nonce,
            WORKER_CAPABILITY,
            path.relative_to(store.layout.project_root).as_posix(),
        ),
        ResourceRecord(
            run_ref,
            "local_storage_staging",
            "pandas@v1",
            nonce,
            WORKSPACE_CAPABILITY,
            workspace.relative_to(store.layout.project_root).as_posix(),
        ),
        path,
    )
    store.reserve(result.workspace)
    store.reserve(result.execution)
    return result


def _invalid(resource: ResourceRecord) -> IntegrityError:
    return IntegrityError(
        expected="an exact Run-owned worker lifetime file with matching ownership nonce",
        received="an inconsistent worker lifetime obligation",
        repair="Inspect the recorded worker resource integrity; do not delete guessed resources.",
        stage="reconciliation",
        run_ref=resource.run_ref,
    )


def worker_resource_path(store: SessionStore, resource: ResourceRecord) -> Path:
    """Resolve only the registered Run and nonce; never follow symbolic links."""
    run = store.run(resource.run_ref)
    relative = PurePosixPath(resource.safe_locator)
    if (
        run is None
        or re.fullmatch(r"[0-9a-f]{32}", resource.ownership_nonce) is None
        or resource.execution_domain_id != "pandas@v1"
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise _invalid(resource)
    expected = store.layout.run_dir(run.session_ref, run.run_ref) / (
        f"worker-{resource.ownership_nonce}"
    )
    if resource.cleanup_capability_id == WORKER_CAPABILITY:
        if resource.resource_kind != "backend_execution":
            raise _invalid(resource)
        expected /= f"lifetime-{resource.ownership_nonce}.lock"
    elif (
        resource.cleanup_capability_id != WORKSPACE_CAPABILITY
        or resource.resource_kind != "local_storage_staging"
    ):
        raise _invalid(resource)
    path = store.layout.project_root / relative
    unsafe = path != expected
    try:
        unsafe = unsafe or any(parent.is_symlink() for parent in (path, *path.parents))
    except OSError:
        unsafe = True
    if unsafe:
        raise _invalid(resource)
    return path


def _check_file(fd: int, path: Path, nonce: str, *, allow_uninitialized: bool) -> None:
    actual = os.fstat(fd)
    selected = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(actual.st_mode)
        or actual.st_nlink != 1
        or (actual.st_dev, actual.st_ino) != (selected.st_dev, selected.st_ino)
        or os.pread(fd, 128, 0)
        not in ((b"", nonce.encode()) if allow_uninitialized else (nonce.encode(),))
    ):
        raise ValueError("invalid exact worker lifetime file")


def acquire_worker_lifetime(reservation: WorkerReservation) -> int:
    """Create a fresh file and hold its description until all users have exited."""
    path = reservation.path
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise _invalid(reservation.execution)
    path.parent.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir()
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        content = reservation.execution.ownership_nonce.encode()
        if os.write(fd, content) != len(content):
            raise OSError("incomplete worker lifetime identity write")
        os.fsync(fd)
        _check_file(fd, path, reservation.execution.ownership_nonce, allow_uninitialized=False)
        return fd
    except BaseException:
        os.close(fd)
        raise


def validate_worker_lifetime(fd: int, path: Path, nonce: str) -> None:
    """The child must receive the already locked exact description before computing."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("invalid worker lifetime path")
    _check_file(fd, path, nonce, allow_uninitialized=False)
    probe = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # The received description must own that lock, rather than merely
            # referring to a file locked through an unrelated description.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        raise ValueError("worker lifetime descriptor was not locked before spawning")
    finally:
        # Never unlock a description inherited by another process or thread.
        os.close(probe)


def worker_is_terminal(store: SessionStore, resource: ResourceRecord) -> bool:
    """A fresh lock proves no parent, worker, or transfer-thread holder survives."""
    path = worker_resource_path(store, resource)
    fd: int | None = None
    absent = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        absent = True
    except OSError:
        pass
    if absent:
        # By the module lifecycle invariant, absence is either pre-spawn or
        # post-proof cleanup, never evidence inferred from a missing live file.
        return True
    if fd is None:
        # Raise outside the handler: suppressed exception contexts can still be
        # traversed by a caller, and raw filesystem errors carry local paths.
        raise _invalid(resource)
    try:
        busy = False
        invalid = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            busy = True
        except OSError:
            invalid = True
        if busy:
            return False
        if not invalid:
            try:
                _check_file(fd, path, resource.ownership_nonce, allow_uninitialized=True)
            except (OSError, ValueError):
                invalid = True
        if invalid:
            raise _invalid(resource)
        return True
    finally:
        os.close(fd)
