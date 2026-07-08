"""Constraint catalog for ``marivo.datasource`` authoring and validation."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from marivo.datasource.engines import SUPPORTED_BACKEND_TYPES
from marivo.introspection.constraints import Constraint, Phase

__all__ = [
    "CONSTRAINTS",
    "Constraint",
    "ConstraintId",
    "constraints_for_error_kind",
    "constraints_for_symbol",
    "default_constraint_for_error",
    "default_constraint_for_error_kind",
    "default_hint_for_error_kind",
    "get_constraint",
    "iter_constraints",
]


class ConstraintId(StrEnum):
    """Stable identifiers for datasource constraints."""

    DATASOURCE_NAME_GLOBAL = "datasource_name_global"
    DATASOURCE_BACKEND_TYPE_REQUIRED = "datasource_backend_type_required"
    DATASOURCE_FIELD_JSONABLE = "datasource_field_jsonable"
    DATASOURCE_SECRET_ENV_REF = "datasource_secret_env_ref"
    DATASOURCE_LOADER_CONTEXT = "datasource_loader_context"
    DATASOURCE_UNIQUE_NAME = "datasource_unique_name"
    DATASOURCE_FILE_LOADABLE = "datasource_file_loadable"
    DATASOURCE_CONFIGURED = "datasource_configured"
    DATASOURCE_ENV_AVAILABLE = "datasource_env_available"
    DATASOURCE_BACKEND_SUPPORTED = "datasource_backend_supported"


def _constraint(
    id: ConstraintId,
    error_kind: str,
    phase: Phase,
    applies_to: tuple[str, ...],
    title: str,
    why: str,
    hint: str,
    *,
    example: str | None = None,
    docs_ref: str | None = None,
) -> Constraint:
    return Constraint(
        id=id.value,
        error_kind=error_kind,
        phase=phase,
        applies_to=applies_to,
        title=title,
        why=why,
        hint=hint,
        example=example,
        docs_ref=docs_ref,
    )


_DATASOURCE_DOC = "marivo/skills/marivo-semantic/references/datasource.md"

CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.DATASOURCE_NAME_GLOBAL: _constraint(
        ConstraintId.DATASOURCE_NAME_GLOBAL,
        "DatasourceFieldInvalid",
        "decorator",
        (*SUPPORTED_BACKEND_TYPES, "DatasourceRef", "ref"),
        "Datasource spec names are global storage keys.",
        "Semantic declarations refer to datasources by stable kind-qualified ids.",
        "Define specs with names like 'warehouse' and reference them with md.ref('datasource.warehouse').",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_BACKEND_TYPE_REQUIRED: _constraint(
        ConstraintId.DATASOURCE_BACKEND_TYPE_REQUIRED,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource backend is selected by the convenience function.",
        "Agents should choose the backend function directly instead of passing backend_type as a string.",
        "Use md.trino(name='warehouse', host='...', catalog='...') or md.duckdb(name='warehouse').",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_FIELD_JSONABLE: _constraint(
        ConstraintId.DATASOURCE_FIELD_JSONABLE,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource literal fields must be JSON-compatible values.",
        "Datasource project state is persisted as portable metadata and cannot store arbitrary Python objects.",
        "Use strings, numbers, booleans, null, lists, and string-keyed objects for non-secret fields.",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_SECRET_ENV_REF: _constraint(
        ConstraintId.DATASOURCE_SECRET_ENV_REF,
        "DatasourceSecretInPlaintext",
        "decorator",
        ("trino", "mysql", "postgres", "clickhouse"),
        "Datasource secrets must be authored as environment-variable references.",
        "Datasource files are project metadata; plaintext credentials in them can leak into git and agent context.",
        'Use *_env fields such as password_env="ENV_VAR_NAME" for password, token, auth, api_key, private_key, and similar fields.',
        example='md.trino(name="warehouse", host="trino.example", catalog="hive", auth_env="TRINO_AUTH")',
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_LOADER_CONTEXT: _constraint(
        ConstraintId.DATASOURCE_LOADER_CONTEXT,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource declarations can only be made while loading models/datasources/ files.",
        "Datasource declarations are collected by the project loader, not registered into global process state.",
        "Put datasource declarations under models/datasources/*.py and load them with md.load_datasources(...).",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_UNIQUE_NAME: _constraint(
        ConstraintId.DATASOURCE_UNIQUE_NAME,
        "DatasourceDuplicate",
        "assembly",
        (*SUPPORTED_BACKEND_TYPES, "load_datasources"),
        "Datasource names must be unique within a project.",
        "Duplicate project-level datasource ids make source references ambiguous.",
        "Rename one datasource file entry or merge duplicate declarations into one declaration.",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_FILE_LOADABLE: _constraint(
        ConstraintId.DATASOURCE_FILE_LOADABLE,
        "DatasourceLoad",
        "assembly",
        ("load_datasources",),
        "Datasource files must load as valid datasource declarations.",
        "Project datasource metadata is executable Python collected by the loader; syntax or runtime failures prevent deterministic datasource discovery.",
        "Open the failing models/datasources/ file, fix the reported error, then rerun md.load_datasources(...).",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_CONFIGURED: _constraint(
        ConstraintId.DATASOURCE_CONFIGURED,
        "DatasourceMissing",
        "runtime",
        ("datasources", "session", "observe"),
        "Named datasources must exist before analysis runtime lookup.",
        "Datasource-backed sessions resolve semantic source refs through persisted datasource metadata.",
        "Register the datasource with md.register(...) before creating or attaching the session.",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_ENV_AVAILABLE: _constraint(
        ConstraintId.DATASOURCE_ENV_AVAILABLE,
        "DatasourceEnvVarMissing",
        "runtime",
        ("datasources", "session"),
        "Datasource secret environment variables must be available at runtime.",
        "The datasource contract stores secret references, not plaintext credentials.",
        "Export the referenced environment variable or validate and remember it with md.test(...).",
        docs_ref=_DATASOURCE_DOC,
    ),
    ConstraintId.DATASOURCE_BACKEND_SUPPORTED: _constraint(
        ConstraintId.DATASOURCE_BACKEND_SUPPORTED,
        "DatasourceBackendTypeUnsupported",
        "runtime",
        ("datasources", "session"),
        "Datasource backend_type must have a registered backend adapter.",
        "The analysis runtime can only create ibis connections for supported datasource backend types.",
        "Use a supported backend_type or add an adapter before relying on datasource auto-loading.",
        docs_ref=_DATASOURCE_DOC,
    ),
}

_DEFAULT_BY_ERROR_KIND: dict[str, ConstraintId] = {
    "DatasourceFieldInvalid": ConstraintId.DATASOURCE_FIELD_JSONABLE,
    "DatasourceSecretInPlaintext": ConstraintId.DATASOURCE_SECRET_ENV_REF,
    "DatasourceLoad": ConstraintId.DATASOURCE_FILE_LOADABLE,
    "DatasourceDuplicate": ConstraintId.DATASOURCE_UNIQUE_NAME,
    "DatasourceMissing": ConstraintId.DATASOURCE_CONFIGURED,
    "DatasourceEnvVarMissing": ConstraintId.DATASOURCE_ENV_AVAILABLE,
    "DatasourceBackendTypeUnsupported": ConstraintId.DATASOURCE_BACKEND_SUPPORTED,
}


def get_constraint(id: ConstraintId | str) -> Constraint | None:
    """Return a constraint by id."""

    try:
        constraint_id = id if isinstance(id, ConstraintId) else ConstraintId(id)
    except ValueError:
        return None
    return CONSTRAINTS.get(constraint_id)


def iter_constraints() -> tuple[Constraint, ...]:
    """Return all constraints in declaration order."""

    return tuple(CONSTRAINTS.values())


def constraints_for_symbol(symbol: str) -> tuple[Constraint, ...]:
    """Return constraints whose applies_to includes *symbol*."""

    return tuple(c for c in CONSTRAINTS.values() if symbol in c.applies_to)


def constraints_for_error_kind(error_kind: str) -> tuple[Constraint, ...]:
    """Return constraints that map to a datasource error kind."""

    return tuple(c for c in CONSTRAINTS.values() if c.error_kind == error_kind)


def _detail_text(details: Mapping[str, Any] | None, key: str) -> str:
    if details is None:
        return ""
    value = details.get(key)
    return value if isinstance(value, str) else ""


def default_constraint_for_error(
    error_kind: str,
    details: Mapping[str, Any] | None = None,
) -> Constraint | None:
    """Return the most specific default constraint for a datasource error."""

    if error_kind == "DatasourceFieldInvalid":
        field = _detail_text(details, "field")
        reason = _detail_text(details, "reason")
        if field == "backend_type" or "backend_type" in reason:
            return CONSTRAINTS[ConstraintId.DATASOURCE_BACKEND_TYPE_REQUIRED]
        if field == "<context>" or "outside loader" in reason:
            return CONSTRAINTS[ConstraintId.DATASOURCE_LOADER_CONTEXT]
        if field == "<name>" or "datasource name" in reason:
            return CONSTRAINTS[ConstraintId.DATASOURCE_NAME_GLOBAL]
        if "JSON" in reason or "json" in reason:
            return CONSTRAINTS[ConstraintId.DATASOURCE_FIELD_JSONABLE]

    constraint_id = _DEFAULT_BY_ERROR_KIND.get(error_kind)
    if constraint_id is None:
        return None
    return get_constraint(constraint_id)


def default_constraint_for_error_kind(error_kind: str) -> Constraint | None:
    """Return the generic default constraint for a datasource error kind."""

    return default_constraint_for_error(error_kind)


def default_hint_for_error_kind(error_kind: str) -> str | None:
    """Return the catalog-backed default hint for a datasource error kind."""

    constraint = default_constraint_for_error_kind(error_kind)
    return constraint.hint if constraint is not None else None
