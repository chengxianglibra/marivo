"""Process-local session state and runtime helpers for the session facade.

This module owns:
- The process-level current session pointer (``_CURRENT_SESSION``).
- ``current()`` which resolves the current session from process state or
  the persisted store pointer.
- ``require_current_session()`` for callers that need a live session.
- ``_build_connection_runtime`` and ``_build_semantic_catalog`` which are
  runtime-only and must not be persisted.
- ``_session_from_row`` which builds a live ``Session`` from store metadata
  plus a runtime connection runtime.
- ``persist_frame`` and ``persist_job_record`` which combine layout I/O
  with store registration.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marivo.analysis.errors import NoActiveSessionError, SessionStateError
from marivo.analysis.session._layout import (
    PersistenceLayout,
    write_frame_to_disk,
    write_job_record,
)
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import Session
from marivo.analysis.timezone import ResolvedTimezone, resolve_system_timezone, zoneinfo_from_name

if TYPE_CHECKING:
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.session._connections import AnalysisConnectionRuntime

from marivo.analysis.frames.base import BaseFrameMeta

# ---------------------------------------------------------------------------
# Process-level current session
# ---------------------------------------------------------------------------

_CURRENT_SESSION: Session | None = None


def get_process_current() -> Session | None:
    """Return the process-level current session, if any."""
    return _CURRENT_SESSION


def set_process_current(session: Session | None) -> None:
    """Set the process-level current session."""
    global _CURRENT_SESSION
    _CURRENT_SESSION = session


def reset_process_state() -> None:
    """Reset the process-level current session to ``None``.

    Used by test fixtures and teardown helpers.
    """
    set_process_current(None)


# ---------------------------------------------------------------------------
# current() — resolves from process state or store
# ---------------------------------------------------------------------------


def current() -> Session | None:
    """Return the current session, or ``None`` when no session is current.

    Resolution order:
    1. Process-current session (set by ``get_or_create`` or ``attach``).
    2. Persisted ``current_session_id`` in the store — load the session by id.
    3. If the stored id no longer matches a session row, clear the stale
       pointer and return ``None``.
    """
    proc = get_process_current()
    if proc is not None:
        return proc

    store = SessionStore()
    current_id = store.get_current_session_id()
    if current_id is None:
        return None

    row = store.get_session_by_id(current_id)
    if row is None:
        # Stale pointer — the session was deleted
        store.clear_current_session_id()
        return None

    connection_runtime = _build_connection_runtime(
        store.project_root, None, None, use_datasources=True
    )
    session = _session_from_row(store, row, connection_runtime)
    set_process_current(session)
    return session


def require_current_session() -> Session:
    """Return the current session, raising if none is current."""
    session = current()
    if session is None:
        raise NoActiveSessionError(
            message="no current analysis session",
            hint="Call mv.session.get_or_create(name='analysis') before running analysis intents.",
        )
    return session


# ---------------------------------------------------------------------------
# Runtime-only helpers (never persisted)
# ---------------------------------------------------------------------------


def _build_connection_runtime(
    project_root: Path,
    backends: dict[str, Callable[[], Any]] | None,
    backend_factory: Callable[[str], Any] | None,
    *,
    use_datasources: bool = True,
) -> AnalysisConnectionRuntime:
    """Build the session-owned datasource connection runtime."""
    if backends is not None and backend_factory is not None:
        raise SessionStateError(
            message="supply either backends={...} or backend_factory=..., not both",
        )
    from marivo.analysis.session._connections import AnalysisConnectionRuntime
    from marivo.datasource.runtime import DatasourceConnectionService

    return AnalysisConnectionRuntime(
        DatasourceConnectionService(
            project_root=project_root,
            backends=backends,
            backend_factory=backend_factory,
            use_datasources=use_datasources,
        )
    )


def _compile_backend_factory(
    backends: dict[str, Callable[[], Any]] | None,
    backend_factory: Callable[[str], Any] | None,
    *,
    use_datasources: bool = True,
) -> AnalysisConnectionRuntime:
    """Compatibility shim for internal callers not yet moved to connection runtimes."""
    return _build_connection_runtime(
        SessionStore().project_root,
        backends,
        backend_factory,
        use_datasources=use_datasources,
    )


def _build_semantic_catalog(project_root: Path) -> Any:
    """Build a SemanticCatalog from the project root, preserving not-ready state."""
    from marivo.semantic.catalog import SemanticCatalog
    from marivo.semantic.reader import SemanticProject

    project = SemanticProject(workspace_dir=project_root)
    project.load()
    return SemanticCatalog(project)


# ---------------------------------------------------------------------------
# Session construction from store row
# ---------------------------------------------------------------------------


def _read_report_timezone(layout: PersistenceLayout) -> ResolvedTimezone:
    meta_path = layout.session_dir / "meta.json"
    if not meta_path.is_file():
        return resolve_system_timezone()
    meta = json.loads(meta_path.read_text())
    name = meta.get("report_tz")
    if not isinstance(name, str) or not name:
        return resolve_system_timezone()
    return ResolvedTimezone(
        name=name,
        tz=zoneinfo_from_name(name),
        resolution=str(meta.get("report_tz_resolution") or "iana"),
        warning=meta.get("report_tz_warning")
        if isinstance(meta.get("report_tz_warning"), str)
        else None,
    )


def _session_from_row(
    store: SessionStore,
    row: Sqlite3RowLike,
    connection_runtime: Any,
) -> Session:
    """Build a live ``Session`` from a store row and a runtime connection runtime.

    Only persisted metadata is used: id, name, question, cwd, created_at,
    updated_at, default_calendar, and report timezone from session meta.
    """
    # sqlite3.Row is not importable at type-check time; accept a duck-typed row.
    session_id = row["id"]
    project_root = store.project_root
    layout = PersistenceLayout(project_root=project_root, session_id=session_id)
    semantic_catalog = _build_semantic_catalog(project_root)

    resolved_report_tz = _read_report_timezone(layout)
    return Session(
        id=session_id,
        name=row["name"],
        question=row["question"],
        cwd=Path(row["cwd"]),
        project_root=project_root,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        connection_runtime=connection_runtime,
        layout=layout,
        semantic_catalog=semantic_catalog,
        store=store,
        report_tz=resolved_report_tz.tz,
        report_tz_name=resolved_report_tz.name,
        report_tz_resolution=resolved_report_tz.resolution,
        report_tz_warning=resolved_report_tz.warning,
        default_calendar=row["default_calendar"],
    )


# Type alias for duck-typed sqlite3.Row objects
Sqlite3RowLike = Any  # sqlite3.Row is not available at type-check time


# ---------------------------------------------------------------------------
# Persistence helpers: write to disk + register in store
# ---------------------------------------------------------------------------


def persist_frame(session: Session, frame: BaseFrame) -> BaseFrameMeta:
    """Write a frame to disk and register it in the session store.

    Writes parquet and ``meta.json`` first, then inserts or replaces the
    ``artifacts`` row.  If the store write fails, the file may remain as
    an orphan; this is acceptable because the store is the source of truth.

    Args:
        session: The owning session.
        frame: The frame to persist.

    Returns:
        Updated ``BaseFrameMeta`` with on-disk ``byte_size`` populated.
    """
    updated = write_frame_to_disk(session._layout, frame)
    session._store.record_artifact(
        session_id=session.id,
        artifact_id=updated.ref,
        kind=updated.kind,
        path=session._layout.relative_path(
            session._layout.frames_dir / updated.ref / "data.parquet"
        ),
        meta_path=session._layout.relative_path(
            session._layout.frames_dir / updated.ref / "meta.json"
        ),
        content_hash=updated.content_hash,
        produced_by_job=updated.produced_by_job,
    )
    return updated


def register_frame_artifact(session: Session, frame: BaseFrame | BaseFrameMeta) -> None:
    """Register an already-persisted frame in the session store.

    Use this when the frame data and meta.json are already on disk
    (e.g. written by the evidence pipeline) and only the store
    registration is missing.  For new frames that need both disk write
    and registration, prefer :func:`persist_frame`.

    Args:
        session: The owning session.
        frame: The frame or frame meta whose files are already on disk.
    """
    meta = frame if isinstance(frame, BaseFrameMeta) else frame.meta
    session._store.record_artifact(
        session_id=session.id,
        artifact_id=meta.ref,
        kind=meta.kind,
        path=session._layout.relative_path(session._layout.frames_dir / meta.ref / "data.parquet"),
        meta_path=session._layout.relative_path(
            session._layout.frames_dir / meta.ref / "meta.json"
        ),
        content_hash=meta.content_hash,
        produced_by_job=meta.produced_by_job,
    )


def persist_job_record(session: Session, record: dict[str, Any]) -> None:
    """Write a job record to disk and register it in the session store.

    Writes the JSON file first, then inserts a ``jobs`` row.

    Args:
        session: The owning session.
        record: Job record dict; must contain ``"id"``, ``"intent"``,
            ``"status"``, ``"started_at"``, and optionally ``"finished_at"``
            and ``"output_frame_ref"`` or ``"output_artifact_id"``.
    """
    write_job_record(session._layout, record)
    finished_at = record.get("finished_at")
    session._store.record_job(
        session_id=session.id,
        job_id=record["id"],
        intent=record["intent"],
        status=record["status"],
        started_at=record["started_at"],
        finished_at=finished_at if isinstance(finished_at, str) else None,
        output_artifact_id=record.get("output_frame_ref") or record.get("output_artifact_id"),
        record_path=session._layout.relative_path(
            session._layout.jobs_dir / f"{record['id']}.json"
        ),
    )
