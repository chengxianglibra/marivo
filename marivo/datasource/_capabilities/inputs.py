"""Native physical-evidence input acquisition; no runtime inspection."""

from marivo._authoring.model import AuthoringInputRequirement
from marivo.introspection.live.model import LiveHelpTarget


def _links(*targets: str) -> tuple[LiveHelpTarget, ...]:
    return tuple(LiveHelpTarget(surface="datasource", canonical_id=target) for target in targets)


_FACTS: dict[str, tuple[str, tuple[LiveHelpTarget, ...]]] = {
    "DatasourceSpec": (
        "Choose a backend constructor; registration saves the declaration, not a validated connection.",
        _links("connections"),
    ),
    "DatasourceSymbol": (
        "Use a loaded semantic Datasource object, a spec's .ref or ms.ref.datasource(name); default is available without credentials.",
        (
            *_links("load"),
            LiveHelpTarget(surface="semantic", canonical_id="datasource_object"),
            LiveHelpTarget(surface="semantic", canonical_id="ref.datasource"),
        ),
    ),
    "DatasourceReferenceInput": (
        "Choose an exact datasource name, a loaded semantic Datasource object or its explicit declaration selector.",
        _links("datasource_catalog.list"),
    ),
    "DatasourceCatalog": ("Load the intended workspace's connection declarations.", _links("load")),
    "TableSource": (
        "Construct the physical table/file descriptor for this question.",
        _links("sources"),
    ),
    "SourceInspection": (
        "Acquire current physical metadata before requesting its fields, partitions or sampled rows.",
        _links("inspect"),
    ),
    "AuthoringScope": (
        "Choose pruning and explicit positive row/timeout guards; a returned-row limit is not a scan bound.",
        _links("partition", "time_range", "unpruned"),
    ),
    "ProjectionColumns": (
        "Map output column names to exact physical names; omit only where this source permits an unprojected relation.",
        (),
    ),
    "PhysicalColumnName": ("Use an exact observed name from this inspection.", _links("inspect")),
    "SourceParameters": (
        "Bind the declared JSON source parameters for this request; omit when no values are needed.",
        _links("source_param", "json"),
    ),
    "SourceParameter": (
        "Use a declared JSON request parameter where a dynamic value is needed.",
        _links("source_param"),
    ),
    "Columns": (
        "Choose exact inspected columns for the retained sample.",
        _links("source_inspection.source_column"),
    ),
    "TemporalColumn": ("Select an exact physical time column from metadata.", _links("inspect")),
    "PartitionValues": (
        "Choose exact partition values from source metadata.",
        _links("source_inspection.partitions"),
    ),
    "PositiveRowGuard": (
        "Pass a positive integer row guard; this does not bound rows scanned by the source.",
        (),
    ),
    "PositiveTimeoutGuard": (
        "Request a positive native timeout in seconds; support is backend-specific and this is not an end-to-end deadline.",
        (),
    ),
    "SnapshotPersistencePolicy": (
        "persist_values=False retains no row values in project state; True explicitly permits retained plaintext sample values.",
        (),
    ),
}

# Bind existing family facts to actual public parameters, independently of the
# renderer. Connection constructor signatures continue to own backend fields.
_BINDINGS: dict[str, dict[str, tuple[str, ...]]] = {
    "inspect": {"DatasourceSymbol": ("datasource",), "TableSource": ("source",)},
    "register": {"DatasourceSpec": ("spec",)},
    "table": {"TableName": ("name",), "ProjectionColumns": ("columns",)},
    "parquet": {"SourcePath": ("path",)},
    "csv": {"SourcePath": ("path",), "ProjectionColumns": ("columns",)},
    "json": {
        "SourcePath": ("path",),
        "ProjectionColumns": ("columns",),
        "SourceParameter": ("query_params", "body"),
    },
    "source_param": {"SourceParameterName": ("name",)},
    "partition": {
        "PartitionValues": ("values",),
        "PositiveRowGuard": ("max_rows",),
        "PositiveTimeoutGuard": ("timeout_seconds",),
    },
    "time_range": {
        "TemporalColumn": ("column",),
        "TemporalBound": ("start", "end"),
        "PositiveRowGuard": ("max_rows",),
        "PositiveTimeoutGuard": ("timeout_seconds",),
    },
    "unpruned": {"PositiveRowGuard": ("max_rows",), "PositiveTimeoutGuard": ("timeout_seconds",)},
    "raw_sql": {
        "DatasourceSymbol": ("datasource",),
        "SqlText": ("sql",),
        "RawSqlReason": ("reason",),
        "PositiveTimeoutGuard": ("timeout_seconds",),
    },
    "datasource_catalog.test": {"DatasourceReferenceInput": ("name",)},
    "source_inspection.source_column": {"PhysicalColumnName": ("name",)},
    "source_inspection.sample": {
        "AuthoringScope": ("scope",),
        "Columns": ("columns",),
        "SourceParameters": ("source_params",),
        "SnapshotPersistencePolicy": ("persist_values",),
    },
}


def datasource_inputs(
    canonical_id: str, inputs: tuple[AuthoringInputRequirement, ...]
) -> tuple[AuthoringInputRequirement, ...]:
    """Bind native family facts to exact acquisition edges."""
    result: list[AuthoringInputRequirement] = []
    for requirement in inputs:
        description, acquisition = _FACTS.get(requirement.family, (None, ()))
        result.append(
            requirement.model_copy(
                update={
                    "description": description,
                    "acquisition": acquisition,
                    "parameter_names": ("name",)
                    if canonical_id in _BACKENDS and requirement.family == "DatasourceName"
                    else _BINDINGS.get(canonical_id, {}).get(requirement.family, ()),
                }
            )
        )
    if canonical_id in {"load", "inspect"}:
        result.append(
            AuthoringInputRequirement(
                role="dependency",
                family="WorkspaceRoot",
                parameter_names=("workspace_dir",),
                min_count=0,
                description="Exact workspace root; None uses MARIVO_PROJECT_ROOT, nearest ancestor manifest, then cwd. Empty projects include default.",
            )
        )
    return (*result, *connection_inputs(canonical_id))


_BACKENDS: dict[str, tuple[str, ...]] = {
    "duckdb": (
        "path",
        "read_only",
        "http_scope",
        "http_bearer_token_env",
        "http_headers_env",
        "ai_context",
        "extra",
    ),
    "trino": (
        "host",
        "catalog",
        "user_env",
        "port",
        "schema",
        "source",
        "timezone",
        "http_scheme",
        "client_tags",
        "session_properties",
        "auth_env",
        "ai_context",
        "extra",
    ),
    "mysql": (
        "host",
        "database",
        "port",
        "autocommit",
        "user_env",
        "password_env",
        "ai_context",
        "extra",
    ),
    "postgres": (
        "host",
        "database",
        "port",
        "schema",
        "autocommit",
        "user_env",
        "password_env",
        "ai_context",
        "extra",
    ),
    "clickhouse": (
        "host",
        "port",
        "database",
        "secure",
        "settings",
        "user_env",
        "password_env",
        "ai_context",
        "extra",
    ),
}
_CONNECTION_FACTS: dict[str, str] = {
    "path": "DuckDB database path; :memory: is connection-local and non-persistent.",
    "read_only": "Choose whether the DuckDB database opens read-only.",
    "http_scope": "Explicit URL prefix that constrains HTTP credential use.",
    "http_bearer_token_env": "Environment variable name for the bearer token; mutually exclusive with custom headers.",
    "http_headers_env": "Map HTTP header names to environment variable names; mutually exclusive with bearer credentials.",
    "host": "Exact server host supplied by connection authority.",
    "port": "Server port; omission uses the reflected backend default.",
    "database": "Physical database selected by the connection; omission uses the reflected backend default where allowed.",
    "catalog": "Required Trino connection catalog, mapped to the backend database.",
    "schema": "Default physical schema; None leaves schema selection to the backend/source descriptor.",
    "source": "Trino client source tag; this is connection configuration.",
    "timezone": "Trino connection timezone; omission uses the reflected default.",
    "http_scheme": "HTTP transport scheme configured for the server.",
    "client_tags": "Optional sequence of Trino client tags.",
    "session_properties": "Optional Trino session property mapping.",
    "user_env": "Environment variable name for the account; explicit user_env is required by Trino.",
    "password_env": "Environment variable name for the password; omit only when the backend's authentication allows it.",
    "auth_env": "Environment variable name for Trino authentication; omit for an unauthenticated deployment.",
    "autocommit": "Choose driver autocommit behavior; omission uses the reflected default.",
    "secure": "Choose the ClickHouse secure transport setting.",
    "settings": "Optional ClickHouse settings mapping.",
    "extra": "Optional JSON-safe backend connection options; never put resolved secrets in project declarations.",
    "ai_context": "Omit for no additional business context, or construct an ms.ai_context value.",
}


def connection_inputs(canonical_id: str) -> tuple[AuthoringInputRequirement, ...]:
    """Own connection-field omission and secret-reference meaning."""
    required = {"host"} if canonical_id in {"trino", "mysql", "postgres", "clickhouse"} else set()
    if canonical_id in {"mysql", "postgres"}:
        required.add("database")
    if canonical_id == "trino":
        required.update(("catalog", "user_env"))
    return tuple(
        AuthoringInputRequirement(
            role="dependency",
            family="ConnectionField",
            parameter_names=(name,),
            min_count=1 if name in required else 0,
            description=_CONNECTION_FACTS[name],
            acquisition=(LiveHelpTarget(surface="semantic", canonical_id="ai_context"),)
            if name == "ai_context"
            else (),
        )
        for name in _BACKENDS.get(canonical_id, ())
    )
