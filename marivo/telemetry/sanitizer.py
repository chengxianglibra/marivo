"""Select bounded, permitted input, result and error facts for usage events."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from marivo._kernel.config import ANALYSIS_STORE_GENERATION, STATE_DIR

TelemetryScalar = str | int | float | bool
TelemetryValue = TelemetryScalar | tuple[str, ...]


_SENSITIVE_PARAMETER_PARTS = (
    "credential",
    "dsn",
    "host",
    "password",
    "path",
    "row",
    "secret",
    "slice_by",
    "sql",
    "token",
)
_SAFE_STRING_PARAMETERS = frozenset(
    {
        "additivity",
        "agg",
        "alignment",
        "backend_type",
        "calendar",
        "column",
        "datasource",
        "dimension",
        "entity",
        "expect_shape",
        "format",
        "fanout_policy",
        "grain",
        "granularity",
        "id",
        "kind",
        "method",
        "metric",
        "mode",
        "name",
        "op",
        "ref",
        "source",
        "status",
        "target",
        "time_dimension",
        "track",
        "unit",
    }
)
_SAFE_IDENTIFIER_COLLECTION_PARAMETERS = frozenset(
    {
        "agg",
        "axes",
        "columns",
        "dimensions",
        "domains",
        "entities",
        "fields",
        "measures",
        "metrics",
        "primary_key",
        "refs",
        "targets",
    }
)
_SAFE_DIRECT_RESULT_FIELDS = (
    "backend_type",
    "cache_status",
    "id",
    "is_truncated",
    "latency_ms",
    "ok",
    "overall_status",
    "ref",
    "requested_limit",
    "returned_row_count",
    "schema_fingerprint",
    "status",
    "timeout_seconds",
)
_SAFE_META_FIELDS = (
    "artifact_id",
    "content_hash",
    "evidence_status",
    "kind",
    "produced_by_job",
    "ref",
    "row_count",
    "semantic_kind",
    "session_id",
)


def _safe_getattr(value: object, name: str) -> object | None:
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _session_attributes(
    arguments: Mapping[str, object], *, include_question: bool = True
) -> dict[str, TelemetryValue]:
    for value in arguments.values():
        session_id = _safe_getattr(value, "id")
        project_root = _safe_getattr(value, "project_root")
        if isinstance(session_id, str) and isinstance(project_root, (str, Path)):
            attrs: dict[str, TelemetryValue] = {"marivo.session.id": session_id}
            if include_question:
                question = _safe_getattr(value, "question")
                if isinstance(question, str) and question:
                    attrs["marivo.session.question"] = question
            return attrs
        if isinstance(_safe_getattr(value, "_project_root"), Path):
            for name in ("session_id", "_session_id"):
                session_id = _safe_getattr(value, name)
                if isinstance(session_id, str) and session_id:
                    return {"marivo.session.id": session_id}
        meta = _safe_getattr(value, "meta")
        session_id = _safe_getattr(meta, "session_id") if meta is not None else None
        if isinstance(session_id, str) and session_id:
            attrs = {"marivo.session.id": session_id}
            return attrs
    return {}


def _session_creation_attributes(
    root: Path, capability_id: str, arguments: Mapping[str, object]
) -> dict[str, TelemetryValue]:
    if capability_id != "session.get_or_create":
        return {}
    attrs: dict[str, TelemetryValue] = {}
    requested = arguments.get("question")
    if isinstance(requested, str) and requested:
        attrs["marivo.session.requested_question"] = requested
    name = arguments.get("name")
    if not isinstance(name, str):
        return attrs
    db_path = (
        root
        / STATE_DIR
        / "analysis"
        / "generations"
        / f"v{ANALYSIS_STORE_GENERATION}"
        / "session_store.db"
    )
    if not db_path.is_file():
        attrs["marivo.session.created"] = True
        return attrs
    import sqlite3  # noqa: PLC0415 - only inspect SQLite for enabled Session creation telemetry

    try:
        with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT question FROM sessions WHERE name = ?", (name,)
            ).fetchone()
    except sqlite3.Error:
        return attrs
    created = row is None
    attrs["marivo.session.created"] = created
    if row is not None and isinstance(row[0], str) and row[0]:
        attrs["marivo.session.question"] = row[0]
    return attrs


def _is_sensitive_parameter(name: str) -> bool:
    normalized = name.lower()
    return any(part in normalized for part in _SENSITIVE_PARAMETER_PARTS)


def _safe_identifier(value: object) -> str | None:
    if isinstance(value, str):
        return value
    for field_name in ("id", "ref", "name"):
        candidate = _safe_getattr(value, field_name)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _safe_reference_attributes(prefix: str, value: object) -> dict[str, TelemetryValue]:
    """Summarize stable object identity without inspecting arbitrary values."""
    attrs: dict[str, TelemetryValue] = {}
    meta = _safe_getattr(value, "meta")
    if meta is not None:
        for field_name in _SAFE_META_FIELDS:
            field_value = _safe_getattr(meta, field_name)
            if isinstance(field_value, (str, int, float, bool)) and field_value is not None:
                attrs[f"{prefix}.{field_name}"] = field_value
        return attrs
    for field_name in ("id", "ref"):
        field_value = _safe_getattr(value, field_name)
        if isinstance(field_value, str) and field_value:
            attrs[f"{prefix}.{field_name}"] = field_value
    return attrs


def _parameter_attributes(name: str, value: object) -> dict[str, TelemetryValue]:
    if name in {"self", "session", "project_root"} or _is_sensitive_parameter(name):
        return {}
    prefix = f"marivo.input.{name}"
    if value is None:
        return {f"{prefix}.present": False}
    if isinstance(value, bool):
        return {prefix: value}
    if isinstance(value, (int, float)):
        return {prefix: value}
    if isinstance(value, str):
        if name in _SAFE_STRING_PARAMETERS or name.endswith(("_id", "_ref")):
            return {prefix: value}
        return {f"{prefix}.type": "str", f"{prefix}.length": len(value)}
    if isinstance(value, Mapping):
        attrs: dict[str, TelemetryValue] = {f"{prefix}.count": len(value)}
        operators = tuple(
            sorted(
                {
                    str(item.get("op"))
                    for item in value.values()
                    if isinstance(item, Mapping) and isinstance(item.get("op"), str)
                }
            )
        )
        if operators:
            attrs[f"{prefix}.operators"] = operators
        return attrs
    if isinstance(value, (list, tuple, set, frozenset)):
        attrs = {f"{prefix}.count": len(value)}
        item_types = tuple(sorted({type(item).__name__ for item in value}))
        if item_types:
            attrs[f"{prefix}.item_types"] = item_types
        if name in _SAFE_IDENTIFIER_COLLECTION_PARAMETERS:
            identifiers = tuple(
                identifier for item in value if (identifier := _safe_identifier(item)) is not None
            )
            if identifiers:
                attrs[f"{prefix}.ids"] = identifiers
        return attrs
    attrs = {f"{prefix}.type": type(value).__name__}
    references = _safe_reference_attributes(prefix, value)
    if references:
        attrs.update(references)
    else:
        identifier = _safe_identifier(value)
        if identifier is not None:
            attrs[f"{prefix}.id"] = identifier
    return attrs


def _input_attributes(
    capability_id: str, arguments: Mapping[str, object]
) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {}
    receiver = arguments.get("self")
    if receiver is not None:
        attrs.update(_safe_reference_attributes("marivo.input", receiver))
    reason = arguments.get("reason")
    if capability_id == "raw_sql" and isinstance(reason, str) and reason:
        attrs["marivo.datasource.raw_sql.reason"] = reason
    for name, value in arguments.items():
        if name in {"question", "reason"}:
            continue
        if capability_id in {"session.resume", "session.inspect"} and name == "session_id":
            attrs["marivo.input.session_id.type"] = type(value).__name__
            if isinstance(value, str):
                attrs["marivo.input.session_id.length"] = len(value)
            continue
        attrs.update(_parameter_attributes(name, value))
    attrs.update(_session_attributes(arguments))
    return attrs


def _counted_kinds(value: object) -> tuple[int, tuple[str, ...]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    kinds = tuple(
        sorted(
            {
                kind
                for item in value
                if isinstance((kind := _safe_getattr(item, "kind")), str) and kind
            }
        )
    )
    return len(value), kinds


def _result_attributes(result: object) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {
        "marivo.result.type": type(result).__name__,
    }
    if result is None:
        return attrs
    shape = _safe_getattr(result, "shape")
    if (
        isinstance(shape, tuple)
        and len(shape) == 2
        and all(isinstance(item, int) for item in shape)
    ):
        attrs["marivo.result.row_count"] = shape[0]
        attrs["marivo.result.column_count"] = shape[1]
    meta = _safe_getattr(result, "meta")
    if meta is not None:
        for field_name in _SAFE_META_FIELDS:
            value = _safe_getattr(meta, field_name)
            if isinstance(value, (str, int, float, bool)) and value is not None:
                attrs[f"marivo.result.{field_name}"] = value
    for field_name in _SAFE_DIRECT_RESULT_FIELDS:
        value = _safe_getattr(result, field_name)
        if isinstance(value, (str, int, float, bool)) and value is not None:
            attrs[f"marivo.result.{field_name}"] = value
        else:
            identifier = _safe_identifier(value) if value is not None else None
            if identifier is not None:
                attrs[f"marivo.result.{field_name}"] = identifier
    for field_name in ("blockers", "warnings", "issues"):
        counted = _counted_kinds(_safe_getattr(result, field_name))
        if counted is None:
            continue
        count, kinds = counted
        attrs[f"marivo.result.{field_name}_count"] = count
        if kinds:
            attrs[f"marivo.result.{field_name}_kinds"] = kinds
    if isinstance(result, (list, tuple)):
        attrs["marivo.result.count"] = len(result)
    session_id = _safe_getattr(result, "id")
    session_root = _safe_getattr(result, "project_root")
    if isinstance(session_id, str) and isinstance(session_root, (str, Path)):
        attrs["marivo.session.id"] = session_id
        question = _safe_getattr(result, "question")
        if isinstance(question, str) and question:
            attrs["marivo.session.question"] = question
    return attrs


def _error_domain(exc: BaseException) -> str:
    module = type(exc).__module__
    root, _, remainder = module.partition(".")
    domain = remainder.partition(".")[0]
    return (
        domain
        if root == "marivo" and domain in {"analysis", "datasource", "semantic", "ontology"}
        else "runtime"
    )


def _error_attributes(exc: BaseException) -> dict[str, TelemetryValue]:
    attrs: dict[str, TelemetryValue] = {
        "marivo.error.domain": _error_domain(exc),
        "marivo.error.class": type(exc).__name__,
    }
    for field_name in ("kind", "code", "stage", "constraint_id"):
        value = _safe_getattr(exc, field_name)
        if isinstance(value, str) and value:
            attrs[f"marivo.error.{field_name}"] = value
    repair = _safe_getattr(exc, "repair")
    if repair is not None:
        repair_kind = _safe_getattr(repair, "kind")
        if isinstance(repair_kind, str):
            attrs["marivo.repair.kind"] = repair_kind
        target = _safe_getattr(repair, "help_target")
        target_surface = _safe_getattr(target, "surface") if target is not None else None
        target_id = _safe_getattr(target, "canonical_id") if target is not None else None
        if isinstance(target_surface, str):
            attrs["marivo.repair.help_surface"] = target_surface
        if isinstance(target_id, str):
            attrs["marivo.repair.help_target"] = target_id
    effect = _safe_getattr(exc, "effect_observed")
    if effect is not None:
        query_executed = _safe_getattr(effect, "query_executed")
        scope_state = _safe_getattr(effect, "scope_state")
        if isinstance(query_executed, bool):
            attrs["marivo.error.query_executed"] = query_executed
        if isinstance(scope_state, str):
            attrs["marivo.error.scope_state"] = scope_state
    return attrs
