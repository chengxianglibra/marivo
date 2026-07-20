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
from marivo.telemetry import staged

if TYPE_CHECKING:
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.session._connections import AnalysisConnectionRuntime

from marivo.analysis.frames.base import BaseFrameMeta
from marivo.refs import SemanticKind, _decode_ref_payload

# ---------------------------------------------------------------------------
# Process-level current session
# ---------------------------------------------------------------------------

_CURRENT_SESSION: Session | None = None


def _require_exact_object(value: object, *, fields: set[str], role: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"analysis job {role} must contain exactly {sorted(fields)}")
    return value


def _validate_metric_identity_payload(value: object, *, role: str) -> None:
    if type(value) is not dict:
        raise ValueError(f"analysis job {role} must be an object")
    kind = value.get("kind")
    if kind == "catalog":
        payload = _require_exact_object(
            value,
            fields={"kind", "metric_ref"},
            role=role,
        )
        ref = _decode_ref_payload(payload["metric_ref"])
        if ref.kind is not SemanticKind.METRIC:
            raise ValueError(f"analysis job {role}.metric_ref must be metric")
        return
    if kind == "runtime_expression":
        payload = _require_exact_object(
            value,
            fields={"kind", "expression_schema", "expression_fingerprint"},
            role=role,
        )
        if payload["expression_schema"] != "metric-expression/v1":
            raise ValueError(
                f"analysis job {role}.expression_schema must be 'metric-expression/v1'"
            )
        if (
            type(payload["expression_fingerprint"]) is not str
            or not payload["expression_fingerprint"]
        ):
            raise ValueError(f"analysis job {role}.expression_fingerprint must be non-empty")
        return
    raise ValueError(f"analysis job {role}.kind is invalid")


def _validate_job_subject(value: object, *, role: str) -> None:
    if type(value) is not dict:
        raise ValueError(f"analysis job {role} must be an object")
    kind = value.get("kind")
    if kind == "catalog_metric":
        payload = _require_exact_object(
            value,
            fields={"kind", "metric_ref"},
            role=role,
        )
        ref = _decode_ref_payload(payload["metric_ref"])
        if ref.kind is not SemanticKind.METRIC:
            raise ValueError(f"analysis job {role}.metric_ref must be metric")
        return
    if kind == "runtime_expression":
        payload = _require_exact_object(
            value,
            fields={"kind", "expression_schema", "expression_fingerprint"},
            role=role,
        )
        _validate_metric_identity_payload(
            {"kind": "runtime_expression", **payload},
            role=role,
        )
        return
    if kind == "delta_metric":
        payload = _require_exact_object(
            value,
            fields={"kind", "comparison"},
            role=role,
        )
        comparison = _require_exact_object(
            payload["comparison"],
            fields={
                "schema",
                "current",
                "baseline",
                "current_artifact_id",
                "baseline_artifact_id",
                "comparable_semantics_fingerprint",
                "alignment_policy_fingerprint",
            },
            role=f"{role}.comparison",
        )
        if comparison["schema"] != "delta-comparison/v1":
            raise ValueError(f"analysis job {role}.comparison.schema must be 'delta-comparison/v1'")
        _validate_metric_identity_payload(comparison["current"], role=f"{role}.comparison.current")
        _validate_metric_identity_payload(
            comparison["baseline"], role=f"{role}.comparison.baseline"
        )
        for field in (
            "current_artifact_id",
            "baseline_artifact_id",
            "comparable_semantics_fingerprint",
            "alignment_policy_fingerprint",
        ):
            if type(comparison[field]) is not str or not comparison[field]:
                raise ValueError(f"analysis job {role}.comparison.{field} must be non-empty")
        return
    raise ValueError(f"analysis job {role}.kind is invalid")


def _validate_dependency_digest_payload(value: object, *, role: str) -> None:
    payload = _require_exact_object(
        value,
        fields={"schema", "entries", "digest"},
        role=role,
    )
    if payload["schema"] != "marivo.semantic_dependency_digest/v1":
        raise ValueError(
            f"analysis job {role}.schema must be 'marivo.semantic_dependency_digest/v1'"
        )
    if type(payload["digest"]) is not str or not payload["digest"].startswith("sha256:"):
        raise ValueError(f"analysis job {role}.digest must use the sha256: prefix")
    entries = payload["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"analysis job {role}.entries must be a non-empty list")
    for index, entry_value in enumerate(entries):
        entry = _require_exact_object(
            entry_value,
            fields={"ref", "body_digest", "fields", "bindings"},
            role=f"{role}.entries[{index}]",
        )
        _decode_ref_payload(entry["ref"])
        if entry["body_digest"] is not None and (
            type(entry["body_digest"]) is not str or not entry["body_digest"]
        ):
            raise ValueError(f"analysis job {role}.entries[{index}].body_digest is invalid")
        if not isinstance(entry["fields"], list):
            raise ValueError(f"analysis job {role}.entries[{index}].fields must be a list")
        bindings = entry["bindings"]
        if not isinstance(bindings, list):
            raise ValueError(f"analysis job {role}.entries[{index}].bindings must be a list")
        for binding_index, binding_value in enumerate(bindings):
            binding = _require_exact_object(
                binding_value,
                fields={"field_ref", "entity_position"},
                role=f"{role}.entries[{index}].bindings[{binding_index}]",
            )
            field_ref = _decode_ref_payload(binding["field_ref"])
            if field_ref.kind not in {
                SemanticKind.DIMENSION,
                SemanticKind.TIME_DIMENSION,
                SemanticKind.MEASURE,
            }:
                raise ValueError(f"analysis job {role} expression binding requires a field ref")
            if type(binding["entity_position"]) is not int or binding["entity_position"] < 0:
                raise ValueError(f"analysis job {role} expression binding position is invalid")


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
            include_semantic_layers=use_datasources,
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


@staged("persist")
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
        evidence_status=updated.evidence_status,
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
        evidence_status=meta.evidence_status,
    )


@staged("persist")
def persist_job_record(session: Session, record: dict[str, Any]) -> None:
    """Write a job record to disk and register it in the session store.

    Writes the JSON file first, then inserts a ``jobs`` row.

    Args:
        session: The owning session.
        record: Job record dict; must contain ``"id"``, ``"intent"``,
            ``"status"``, ``"started_at"``, and optionally ``"finished_at"``
            and ``"output_frame_ref"`` or ``"output_artifact_id"``.
    """
    supplied_schema = record.get("schema")
    if supplied_schema not in {None, "marivo.analysis_job/v1"}:
        raise ValueError(
            f"job record schema must be 'marivo.analysis_job/v1'; received {supplied_schema!r}"
        )
    forbidden = {"semantic_model", "semantic_anchors", "metric_id", "metric_ids"} & set(record)
    if forbidden:
        raise ValueError(
            f"analysis job semantic identity must use named structured roles; got {sorted(forbidden)}"
        )
    fingerprint = record.get("catalog_definition_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("analysis job requires catalog_definition_fingerprint")
    has_subject = "subject" in record
    has_subjects = "subjects" in record
    if has_subject == has_subjects:
        raise ValueError("analysis job requires exactly one subject or subjects role")
    if has_subject:
        _validate_job_subject(record["subject"], role="subject")
    else:
        subjects = record["subjects"]
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("analysis job subjects must be a non-empty list")
        for index, subject in enumerate(subjects):
            _validate_job_subject(subject, role=f"subjects[{index}]")
    has_digest = "semantic_dependency_digest" in record
    has_digests = "semantic_dependency_digests" in record
    if has_digest == has_digests:
        raise ValueError(
            "analysis job requires exactly one semantic_dependency_digest or "
            "semantic_dependency_digests role"
        )
    if has_digest:
        _validate_dependency_digest_payload(
            record["semantic_dependency_digest"],
            role="semantic_dependency_digest",
        )
    else:
        digests = record["semantic_dependency_digests"]
        if not isinstance(digests, list) or not digests:
            raise ValueError("analysis job semantic_dependency_digests must be non-empty")
        for index, digest in enumerate(digests):
            _validate_dependency_digest_payload(
                digest,
                role=f"semantic_dependency_digests[{index}]",
            )
    for field in ("dimension_refs",):
        values = record.get(field)
        if not isinstance(values, list):
            raise ValueError(f"analysis job {field} must be a list")
        for payload in values:
            decoded = _decode_ref_payload(payload)
            if decoded.kind is not SemanticKind.DIMENSION:
                raise ValueError("analysis job dimension_refs entries must be dimension refs")
    time_payload = record.get("time_dimension_ref")
    if time_payload is not None:
        decoded = _decode_ref_payload(time_payload)
        if decoded.kind.value != "time_dimension":
            raise ValueError("analysis job time_dimension_ref must be time_dimension")
    predicates = record.get("slice_predicates")
    if not isinstance(predicates, list):
        raise ValueError("analysis job slice_predicates must be a list")
    for predicate in predicates:
        if not isinstance(predicate, dict) or set(predicate) != {"dimension_ref", "value"}:
            raise ValueError("analysis job slice predicate fields are invalid")
        decoded = _decode_ref_payload(predicate["dimension_ref"])
        if decoded.kind.value not in {"dimension", "time_dimension"}:
            raise ValueError("analysis job slice predicate requires a dimension ref")
    persisted = {"schema": "marivo.analysis_job/v1", **record}
    write_job_record(session._layout, persisted)
    finished_at = persisted.get("finished_at")
    session._store.record_job(
        session_id=session.id,
        job_id=persisted["id"],
        intent=persisted["intent"],
        status=persisted["status"],
        started_at=persisted["started_at"],
        finished_at=finished_at if isinstance(finished_at, str) else None,
        output_artifact_id=persisted.get("output_frame_ref") or persisted.get("output_artifact_id"),
        record_path=session._layout.relative_path(
            session._layout.jobs_dir / f"{persisted['id']}.json"
        ),
    )
