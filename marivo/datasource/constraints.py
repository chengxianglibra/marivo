"""Constraint catalog for ``marivo.datasource`` authoring and validation."""

from __future__ import annotations

from marivo._compat import StrEnum
from marivo.datasource.engines import SUPPORTED_BACKEND_TYPES
from marivo.introspection.constraints import Constraint, Phase

__all__ = [
    "CONSTRAINTS",
    "Constraint",
    "ConstraintId",
    "constraints_for_error_kind",
    "constraints_for_symbol",
    "default_constraint_for_error_kind",
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
    DUCKDB_HTTP_AUTH_SCOPED = "duckdb_http_auth_scoped"
    JSON_REQUEST_SHAPE = "json_request_shape"
    JSON_SOURCE_PARAMS_EXACT = "json_source_params_exact"
    TABLE_COLUMN_BINDINGS_CLOSED = "table_column_bindings_closed"
    TABLE_COLUMN_TYPE_ASSERTION = "table_column_type_assertion"
    PROJECTED_SOURCE_RUNTIME_EVIDENCE = "projected_source_runtime_evidence"
    PARTITION_LISTING_BOUNDED = "partition_listing_bounded"
    SNAPSHOT_VALUE_PERSISTENCE = "snapshot_value_persistence"


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


CONSTRAINTS: dict[ConstraintId, Constraint] = {
    ConstraintId.DATASOURCE_NAME_GLOBAL: _constraint(
        ConstraintId.DATASOURCE_NAME_GLOBAL,
        "DatasourceFieldInvalid",
        "decorator",
        (*SUPPORTED_BACKEND_TYPES, "Ref[datasource]", "ref"),
        "Datasource spec names are global storage keys.",
        "Semantic declarations refer to datasources by stable kind-qualified ids.",
        "Define specs with names like 'warehouse' and reference them with ms.ref.datasource('warehouse').",
    ),
    ConstraintId.DATASOURCE_BACKEND_TYPE_REQUIRED: _constraint(
        ConstraintId.DATASOURCE_BACKEND_TYPE_REQUIRED,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource backend is selected by the convenience function.",
        "Agents should choose the backend function directly instead of passing backend_type as a string.",
        "Use md.trino(name='warehouse', host='...', catalog='...') or md.duckdb(name='warehouse').",
    ),
    ConstraintId.DATASOURCE_FIELD_JSONABLE: _constraint(
        ConstraintId.DATASOURCE_FIELD_JSONABLE,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource literal fields must be JSON-compatible values.",
        "Datasource project state is persisted as portable metadata and cannot store arbitrary Python objects.",
        "Use strings, numbers, booleans, null, lists, and string-keyed objects for non-secret fields.",
    ),
    ConstraintId.DATASOURCE_SECRET_ENV_REF: _constraint(
        ConstraintId.DATASOURCE_SECRET_ENV_REF,
        "DatasourceSecretInPlaintext",
        "decorator",
        ("duckdb", "trino", "mysql", "postgres", "clickhouse", "register"),
        "Datasource secrets resolve only from explicit *_env references; ambient MARIVO_* names are ignored.",
        "Datasource files are project metadata; plaintext credentials in them can leak into git and agent context.",
        'Use *_env fields such as password_env="ENV_VAR_NAME"; each named variable resolves from the environment and then ~/.marivo/secrets.toml.',
        example='md.trino(name="warehouse", host="trino.example", catalog="hive", auth_env="TRINO_AUTH")',
    ),
    ConstraintId.DATASOURCE_LOADER_CONTEXT: _constraint(
        ConstraintId.DATASOURCE_LOADER_CONTEXT,
        "DatasourceFieldInvalid",
        "decorator",
        SUPPORTED_BACKEND_TYPES,
        "Datasource declarations can only be made while loading models/datasources/ files.",
        "Datasource declarations are collected by the project loader, not registered into global process state.",
        "Put datasource declarations under models/datasources/*.py and load them with md.load_datasources(...).",
    ),
    ConstraintId.DATASOURCE_UNIQUE_NAME: _constraint(
        ConstraintId.DATASOURCE_UNIQUE_NAME,
        "DatasourceDuplicate",
        "assembly",
        (*SUPPORTED_BACKEND_TYPES, "load_datasources"),
        "Datasource names must be unique within a project.",
        "Duplicate project-level datasource ids make source references ambiguous.",
        "Rename one datasource file entry or merge duplicate declarations into one declaration.",
    ),
    ConstraintId.DATASOURCE_FILE_LOADABLE: _constraint(
        ConstraintId.DATASOURCE_FILE_LOADABLE,
        "DatasourceLoad",
        "assembly",
        ("load_datasources",),
        "Datasource files must load as valid datasource declarations.",
        "Project datasource metadata is executable Python collected by the loader; syntax or runtime failures prevent deterministic datasource discovery.",
        "Open the failing models/datasources/ file, fix the reported error, then rerun md.load_datasources(...).",
    ),
    ConstraintId.DATASOURCE_CONFIGURED: _constraint(
        ConstraintId.DATASOURCE_CONFIGURED,
        "DatasourceMissing",
        "runtime",
        ("datasources", "session", "observe"),
        "Named datasources must exist before analysis runtime lookup.",
        "Datasource-backed sessions resolve semantic source refs through persisted datasource metadata.",
        "Register the datasource with md.register(...) before creating or attaching the session.",
    ),
    ConstraintId.DATASOURCE_ENV_AVAILABLE: _constraint(
        ConstraintId.DATASOURCE_ENV_AVAILABLE,
        "DatasourceEnvVarMissing",
        "runtime",
        ("datasources", "session"),
        "Datasource secret environment variables must be available at runtime.",
        "The datasource contract stores secret references, not plaintext credentials.",
        "Export the referenced environment variable or validate and remember it with md.test(...).",
    ),
    ConstraintId.DATASOURCE_BACKEND_SUPPORTED: _constraint(
        ConstraintId.DATASOURCE_BACKEND_SUPPORTED,
        "DatasourceBackendTypeUnsupported",
        "runtime",
        ("datasources", "session"),
        "Datasource backend_type must have a registered backend adapter.",
        "The analysis runtime can only create ibis connections for supported datasource backend types.",
        "Use a supported backend_type or add an adapter before relying on datasource auto-loading.",
    ),
    ConstraintId.DUCKDB_HTTP_AUTH_SCOPED: _constraint(
        ConstraintId.DUCKDB_HTTP_AUTH_SCOPED,
        "DatasourceFieldInvalid",
        "decorator",
        ("duckdb",),
        "DuckDB HTTP credentials require one explicit URL scope and one auth mode.",
        "Remote JSON credentials must not be sent outside their declared host and path boundary.",
        "Set http_scope plus exactly one of http_bearer_token_env or http_headers_env; keep secret values in the referenced environment variables.",
        example=(
            'md.duckdb(name="api", http_scope="https://api.example/v1/", '
            'http_bearer_token_env="API_TOKEN")'
        ),
    ),
    ConstraintId.JSON_REQUEST_SHAPE: _constraint(
        ConstraintId.JSON_REQUEST_SHAPE,
        "DatasourceFieldInvalid",
        "decorator",
        ("json", "source_param"),
        "JSON sources keep stable output aliases and correlate one shared array traversal.",
        "A stable physical request shape can be inspected without fetching data and bound without API-specific analysis arguments.",
        "Use schema for output names and Ibis types, field_paths for nested selectors, and one flat non-empty scalar list when a request parameter needs repeated values.",
        example=(
            'md.json("https://api.example/items", '
            'schema={"id": "string", "app_name": "string"}, '
            'records_path="$.data", field_paths={"app_name": "apps[].name"})'
        ),
    ),
    ConstraintId.JSON_SOURCE_PARAMS_EXACT: _constraint(
        ConstraintId.JSON_SOURCE_PARAMS_EXACT,
        "DatasourceFieldInvalid",
        "runtime",
        ("SourceInspection.sample",),
        "Parameterized JSON reads require exact scalar or flat non-empty scalar-list values.",
        "Missing or extra values would make snapshot identity differ from the physical request that produced it.",
        "Pass source_params={...} with exactly every md.source_param(...) name; use a flat non-empty list for repeated query keys or a JSON array body value.",
    ),
    ConstraintId.TABLE_COLUMN_BINDINGS_CLOSED: _constraint(
        ConstraintId.TABLE_COLUMN_BINDINGS_CLOSED,
        "DatasourceFieldInvalid",
        "decorator",
        ("table", "source_column"),
        "Projected tables require complete identifier-only bindings; arbitrary SQL remains terminal through md.raw_sql(...).",
        "Mixing inferred and declared columns would make the source schema depend on live metadata.",
        "Bind every projected output with md.source_column(...); use md.raw_sql(...) only for terminal arbitrary SQL.",
        example=(
            'md.table("events", columns={"event_time": '
            'md.source_column("event.timestamp", data_type="timestamp")})'
        ),
    ),
    ConstraintId.TABLE_COLUMN_TYPE_ASSERTION: _constraint(
        ConstraintId.TABLE_COLUMN_TYPE_ASSERTION,
        "DatasourceFieldInvalid",
        "decorator",
        ("table", "source_column"),
        "A table column data_type asserts the output schema and never casts the physical value.",
        "A declared type keeps projected materialization typed without introducing an authored expression.",
        "Declare the canonical physical type accepted by ibis.dtype(...); change the source or use a view when a cast is required.",
    ),
    ConstraintId.PROJECTED_SOURCE_RUNTIME_EVIDENCE: _constraint(
        ConstraintId.PROJECTED_SOURCE_RUNTIME_EVIDENCE,
        "DatasourceFieldInvalid",
        "runtime",
        ("table", "source_column", "inspect", "SourceInspection.sample"),
        "Projected inspection is metadata-only and declared-only bindings require bounded runtime evidence.",
        "Catalog absence does not prove that a physical identifier is queryable.",
        "Inspect first, then acquire an explicit bounded sample before semantic preview or readiness.",
    ),
    ConstraintId.PARTITION_LISTING_BOUNDED: _constraint(
        ConstraintId.PARTITION_LISTING_BOUNDED,
        "DatasourceFieldInvalid",
        "runtime",
        ("SourceInspection.partitions",),
        "Partition metadata listings expose one bounded ordered edge.",
        "Agents need bounded physical-value boundaries without turning discovery into an unbounded partition dump or inventing temporal meaning.",
        "Use order='asc' or order='desc' for one physical-value edge; string and numeric ordering are not automatically chronological, and a truncated result does not include every middle value.",
    ),
    ConstraintId.SNAPSHOT_VALUE_PERSISTENCE: _constraint(
        ConstraintId.SNAPSHOT_VALUE_PERSISTENCE,
        "DatasourceFieldInvalid",
        "runtime",
        ("SourceInspection.sample",),
        "Snapshot values are memory-only unless plaintext persistence is explicitly requested.",
        "The default avoids writing observed values to project-local state, so another process can recover metadata but not retained value evidence.",
        "Keep persist_values=False for same-process projections; use persist_values=True only when a later process needs value projections or retained-row certification and plaintext project-local caching is acceptable.",
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


def default_constraint_for_error_kind(error_kind: str) -> Constraint | None:
    """Return the generic default constraint for a datasource error kind."""

    constraint_id = _DEFAULT_BY_ERROR_KIND.get(error_kind)
    return get_constraint(constraint_id) if constraint_id is not None else None
