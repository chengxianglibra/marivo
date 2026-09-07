"""Exact resource reservations and process-lifetime proof for the first route."""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath
from uuid import uuid4

from marivo.analysis.materialization.contracts import ResourceRecord
from marivo.analysis.materialization.errors import IntegrityError, RecoveryPendingError
from marivo.analysis.materialization.store import SessionStore

_TERMINATED: set[str] = set()
_LOCAL_CAPABILITY = "local_owned_path@v1"
_DUCKDB_CAPABILITY = "duckdb_process_lifetime@v1"


def backend_reservation(run_ref: str, domain: str) -> ResourceRecord:
    nonce = uuid4().hex
    return ResourceRecord(
        run_ref=run_ref,
        resource_kind="backend_execution",
        execution_domain_id=domain,
        ownership_nonce=nonce,
        cleanup_capability_id=_DUCKDB_CAPABILITY,
        safe_locator=f"process/{os.getpid()}/{nonce}",
    )


def prove_local_termination(resource: ResourceRecord) -> None:
    """Record proof only after synchronous work and strict connection close finish."""
    _TERMINATED.add(resource.ownership_nonce)


def execution_is_terminal(resource: ResourceRecord) -> bool:
    """Only this registered process-owned DuckDB route inherits process lifetime."""
    if (
        resource.resource_kind not in ("backend_execution", "planner_temporary_relation")
        or resource.cleanup_capability_id != _DUCKDB_CAPABILITY
    ):
        return False
    parts = resource.safe_locator.split("/")
    expected_length = 3 if resource.resource_kind == "backend_execution" else 4
    if (
        len(parts) != expected_length
        or parts[0] != "process"
        or parts[2] != resource.ownership_nonce
    ):
        return False
    try:
        pid = int(parts[1])
    except ValueError:
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return resource.ownership_nonce in _TERMINATED
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    # A live or reused PID is never treated as proof of termination.
    return False


def reserve_output(
    store: SessionStore,
    *,
    run_ref: str,
    session_ref: str,
    artifact_ref: str,
    nonce: str,
) -> tuple[Path, Path, tuple[ResourceRecord, ...]]:
    layout = store.layout
    staging = layout.run_dir(session_ref, run_ref) / f"output-{nonce}"
    final = layout.artifact_dir(session_ref, artifact_ref)
    records = tuple(
        ResourceRecord(
            run_ref=run_ref,
            resource_kind="local_storage_staging",
            execution_domain_id="local_parquet@v1",
            ownership_nonce=nonce,
            cleanup_capability_id=_LOCAL_CAPABILITY,
            safe_locator=path.relative_to(layout.project_root).as_posix(),
        )
        for path in (staging, final)
    )
    for path in (staging, final):
        if path.exists() or path.is_symlink():
            raise IntegrityError(
                expected="new uniquely reserved output locations",
                received="an existing output location",
                repair="Inspect the selected generation integrity before retrying.",
                stage="storage_staging",
                run_ref=run_ref,
            )
    for record in records:
        store.reserve(record)
    return staging, final, records


def discharge_resources(
    store: SessionStore, resources: tuple[ResourceRecord, ...]
) -> tuple[ResourceRecord, ...]:
    """Clean exact unpublished paths only after every execution is proven terminal."""
    for resource in resources:
        if resource.resource_kind in (
            "backend_execution",
            "planner_temporary_relation",
        ) and not execution_is_terminal(resource):
            raise RecoveryPendingError(
                expected="authoritative process or connection termination proof",
                received="execution termination remains unproved",
                repair="Restore the recorded execution's termination proof and retry Session recovery.",
                stage="reconciliation",
                run_ref=resource.run_ref,
            )
    resolved: list[ResourceRecord] = []
    for resource in resources:
        if resource.resource_kind in ("backend_execution", "planner_temporary_relation"):
            resolved.append(resource)
            continue
        if resource.cleanup_capability_id != _LOCAL_CAPABILITY:
            raise RecoveryPendingError(
                expected="the registered exact resource cleanup capability",
                received="an unsupported resource obligation",
                repair="Restore the recorded cleanup capability before retrying.",
                stage="reconciliation",
                run_ref=resource.run_ref,
            )
        relative = PurePosixPath(resource.safe_locator)
        run = store.run(resource.run_ref)
        if run is None:
            raise _invalid_resource(resource)
        allowed = (
            store.layout.run_dir(run.session_ref, run.run_ref)
            / f"output-{resource.ownership_nonce}",
            store.layout.artifact_dir(run.session_ref, f"artifact_{resource.ownership_nonce}"),
        )
        path = store.layout.project_root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path not in allowed
            or any(parent.is_symlink() for parent in (path, *path.parents))
        ):
            raise _invalid_resource(resource)
        try:
            if path.exists():
                shutil.rmtree(path)
            resolved.append(resource)
        except OSError:
            # Harmless, precisely owned garbage stays journaled for later maintenance.
            continue
    return tuple(resolved)


def _invalid_resource(resource: ResourceRecord) -> IntegrityError:
    return IntegrityError(
        expected="an exact Run-owned local path with matching ownership nonce",
        received="an inconsistent resource obligation",
        repair="Inspect the selected generation integrity; do not delete guessed resources.",
        stage="reconciliation",
        run_ref=resource.run_ref,
    )
