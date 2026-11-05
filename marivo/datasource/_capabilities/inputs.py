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
        "Map output column names to exact physical names; omit for an unprojected source.",
        (),
    ),
    "JsonProjectionColumns": (
        "Map output names to relative JSON field paths; omit for an unprojected source. Array traversal requires records_path; one shared traversal expands one row per element, and a missing, null or empty array yields no rows.",
        (),
    ),
    "PhysicalColumnName": (
        "Use an inspected physical name or an existing projected output alias.",
        _links("inspect"),
    ),
    "SourceParameters": (
        "Bind the declared JSON source parameters for this request; omit when no values are needed.",
        _links("source_param", "json"),
    ),
    "SourceParameter": (
        "Use a declared JSON request parameter where a dynamic value is needed.",
        _links("source_param"),
    ),
    "Columns": (
        "Choose output column names from inspection.schema; for projected sources use the output aliases, not physical names.",
        _links("source_inspection.schema"),
    ),
    "TemporalBound": (
        "Match the physical column: aware datetime for aware timestamps, naive datetime or date for naive timestamps, and date for DATE. Both endpoints must have the same kind and awareness; datasource defaults do not fill boundaries.",
        _links("source_inspection.schema"),
    ),
    "TemporalColumn": (
        "Select a date or timestamp output column from inspection.schema; use its output alias for a projected source.",
        _links("source_inspection.schema"),
    ),
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
        "False keeps newly acquired profile values in process memory but does not expose retained rows; True retains named rows and stores bounded plaintext values in project state.",
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
        "JsonProjectionColumns": ("columns",),
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
    "env": ("name",),
    "s3_secret": (
        "scope",
        "region",
        "key_id",
        "secret",
        "session_token",
        "endpoint",
        "url_style",
        "use_ssl",
    ),
    "http_bearer_secret": ("scope", "token"),
    "http_headers_secret": ("scope", "headers"),
    "duckdb": (
        "path",
        "read_only",
        "secrets",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    ),
    "trino": (
        "host",
        "catalog",
        "user",
        "port",
        "schema",
        "source",
        "http_scheme",
        "client_tags",
        "session_properties",
        "auth",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    ),
    "mysql": (
        "host",
        "database",
        "port",
        "autocommit",
        "user",
        "password",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    ),
    "postgres": (
        "host",
        "database",
        "port",
        "schema",
        "autocommit",
        "user",
        "password",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    ),
    "clickhouse": (
        "host",
        "port",
        "database",
        "secure",
        "settings",
        "user",
        "password",
        "wall_clock_timezone",
        "ai_context",
        "extra",
    ),
}
_CONNECTION_FACTS: dict[str, str] = {
    "path": "DuckDB database file path or :memory: for a connection-local database.",
    "read_only": "Choose whether a DuckDB database file opens in native read-only mode.",
    "secrets": "List of scoped md.s3_secret, md.http_bearer_secret and md.http_headers_secret values. File and named in-memory databases with scoped secrets require exclusive managed access.",
    "scope": "Explicit S3 or HTTP(S) URL prefix; unique per type in one datasource.",
    "region": "Required non-empty S3 region.",
    "key_id": "Access key EnvRef from md.env.",
    "secret": "Secret access key EnvRef from md.env.",
    "session_token": "Optional session token EnvRef from md.env.",
    "endpoint": "Optional S3 endpoint host and port; defaults to s3.amazonaws.com.",
    "url_style": "Optional vhost (default) or path style.",
    "use_ssl": "Optional bool; defaults to True.",
    "token": "Bearer token EnvRef from md.env.",
    "headers": "Non-empty mapping of HTTP header names to EnvRef values from md.env.",
    "name": "Non-empty credential lookup name; never a resolved secret.",
    "host": "Exact server host supplied by connection authority.",
    "port": "Server port; omission uses the reflected backend default.",
    "database": "Physical database selected by the connection; omission uses the reflected backend default where allowed.",
    "catalog": "Required Trino connection catalog, mapped to the backend database.",
    "schema": "Default physical schema; None leaves schema selection to the backend/source descriptor.",
    "source": "Trino client source tag; this is connection configuration.",
    "http_scheme": "HTTP transport scheme configured for the server.",
    "client_tags": "Optional sequence of Trino client tags.",
    "session_properties": "Optional Trino session property mapping.",
    "user": "EnvRef from md.env for the account; required by Trino.",
    "password": "EnvRef from md.env for the password; omit only when the backend's authentication allows it.",
    "auth": "EnvRef from md.env for Trino authentication; omit for an unauthenticated deployment.",
    "autocommit": "Use True or omit this option to use autocommit.",
    "secure": "Choose the ClickHouse secure transport setting.",
    "settings": "Optional ClickHouse settings mapping.",
    "extra": "Optional JSON-safe backend connection options; never put resolved secrets in project declarations.",
    "wall_clock_timezone": "Optional declared source timezone for naive timestamps, encoded local clocks and business dates; accepts IANA names or fixed offsets. Does not change physical instants or connection settings.",
    "ai_context": "Omit for no additional business context, or construct an ms.ai_context value.",
}


def connection_inputs(canonical_id: str) -> tuple[AuthoringInputRequirement, ...]:
    """Own connection-field omission and secret-reference meaning."""
    required = {"host"} if canonical_id in {"trino", "mysql", "postgres", "clickhouse"} else set()
    if canonical_id in {"mysql", "postgres"}:
        required.add("database")
    if canonical_id == "trino":
        required.update(("catalog", "user"))
    required.update(
        {
            "env": {"name"},
            "s3_secret": {"scope", "region", "key_id", "secret"},
            "http_bearer_secret": {"scope", "token"},
            "http_headers_secret": {"scope", "headers"},
        }.get(canonical_id, set())
    )
    return tuple(
        AuthoringInputRequirement(
            role="dependency",
            family="SourceTimezone" if name == "wall_clock_timezone" else "ConnectionField",
            parameter_names=(name,),
            min_count=1 if name in required else 0,
            description=_CONNECTION_FACTS[name],
            acquisition=(LiveHelpTarget(surface="semantic", canonical_id="ai_context"),)
            if name == "ai_context"
            else _links("s3_secret", "http_bearer_secret", "http_headers_secret")
            if name == "secrets"
            else _links("env")
            if name
            in {"user", "password", "auth", "key_id", "secret", "session_token", "token", "headers"}
            else (),
        )
        for name in _BACKENDS.get(canonical_id, ())
    )
