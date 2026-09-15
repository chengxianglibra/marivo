"""Exact resource reservations and guarded publication cleanup."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from uuid import uuid4

from marivo.analysis.materialization.contracts import ResourceRecord
from marivo.analysis.materialization.errors import IntegrityError, RecoveryPendingError
from marivo.analysis.materialization.object_termination import (
    OBJECT_REQUEST_CAPABILITY,
    object_request_is_terminal,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import ObjectBinding

_LOCAL_CAPABILITY = "local_owned_path@v1"
_READ_CAPABILITY = "read_only_execution@v1"


def backend_reservation(run_ref: str, domain: str) -> ResourceRecord:
    nonce = uuid4().hex
    return ResourceRecord(
        run_ref=run_ref,
        resource_kind="backend_execution",
        execution_domain_id=domain,
        ownership_nonce=nonce,
        cleanup_capability_id=_READ_CAPABILITY,
        safe_locator=f"execution/{nonce}",
    )


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
    store: SessionStore,
    resources: tuple[ResourceRecord, ...],
    object_bindings: tuple[ObjectBinding, ...] = (),
) -> tuple[ResourceRecord, ...]:
    """Discharge guarded local recovery without certifying remote read termination.

    The caller holds the Session writer guard and has resolved Store commit state.
    Object requests can still write and therefore retain their exact proof gate.
    Read-only queries and connection-scoped temporary relations cannot publish.
    """
    runs = tuple(store.run(ref) for ref in {item.run_ref for item in resources})
    journal = tuple(
        item for run in runs if run is not None for item in store.resources(run.session_ref)
    )
    for resource in resources:
        if resource not in journal:
            raise _invalid_resource(resource)
        if resource.resource_kind not in ("backend_execution", "planner_temporary_relation"):
            continue
        if resource.cleanup_capability_id == _READ_CAPABILITY:
            parts = resource.safe_locator.split("/")
            expected_length = 2 if resource.resource_kind == "backend_execution" else 3
            if (
                len(parts) != expected_length
                or parts[0] != "execution"
                or parts[1] != resource.ownership_nonce
                or any(not part or part in (".", "..") for part in parts)
            ):
                raise _invalid_resource(resource)
            continue
        if (
            resource.cleanup_capability_id == OBJECT_REQUEST_CAPABILITY
            and object_request_is_terminal(resource)
        ):
            continue
        raise RecoveryPendingError(
            expected="resolved publication ownership and exact write-capable resource cleanup",
            received="an unresolved object write or unsupported resource obligation",
            repair="Resolve the recorded publication or object-write obligation before retrying Session recovery.",
            stage="reconciliation",
            run_ref=resource.run_ref,
        )
    resolved: list[ResourceRecord] = []
    for resource in resources:
        if resource.resource_kind in ("backend_execution", "planner_temporary_relation"):
            resolved.append(resource)
            continue
        if resource.cleanup_capability_id == "s3_versioned_key@v1":
            from marivo.analysis.materialization.errors import (
                MaterializationError,
                StorageAccessError,
            )
            from marivo.analysis.materialization.object_storage import cleanup_object
            from marivo.analysis.materialization.targets import object_access

            try:
                if cleanup_object(
                    store, resource, object_access(object_bindings, resource.execution_domain_id)
                ):
                    resolved.append(resource)
            except StorageAccessError:
                # Unavailable access does not revive proven-terminal object work.
                pass
            except IntegrityError:
                raise
            except MaterializationError:
                # Proven-terminal exact object garbage can be maintained later.
                pass
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
