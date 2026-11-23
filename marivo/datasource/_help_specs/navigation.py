"""Datasource discovery routes and root membership."""

from collections.abc import Mapping
from types import MappingProxyType

from marivo.datasource._help_specs.builders import _target
from marivo.datasource._help_specs.model import DatasourceNavigationRoute, DatasourceRootGroup
from marivo.introspection.live.targets import canonical_target


def route(label: str, target: str, summary: str) -> DatasourceNavigationRoute:
    return DatasourceNavigationRoute(label, _target(canonical_target(target)), summary)


NAVIGATION_ROUTES = {
    "": (
        route(
            "connections",
            "connections",
            "Find a connection, select a backend, or change connection declarations.",
        ),
        route(
            "physical sources",
            "sources",
            "Describe the table or file that supplies physical rows.",
        ),
        route(
            "inspect or sample",
            "inspection",
            "Resolve a physical metadata or scoped-row question.",
        ),
        route(
            "source-specific SQL",
            "raw_sql",
            "Use explicit SQL when public inspection cannot answer the physical question.",
        ),
        route(
            "semantic handoff",
            "boundary.semantic_authoring",
            "Turn scoped physical evidence into an authored business definition.",
        ),
    ),
    "connections": (
        route(
            "credentials",
            "env",
            "Declare lookup names; DuckDB secrets scope S3 and HTTP credentials.",
        ),
        route(
            "load existing declarations",
            "load",
            "Browse the project connection set; default supports credential-free file sources.",
        ),
        route(
            "list connections",
            "DatasourceCatalog.list",
            "Choose among loaded datasource identities.",
        ),
        route(
            "inspect one connection",
            "DatasourceCatalog.describe",
            "Read captured configuration without opening the backend.",
        ),
        *(
            route("choose backend", backend, f"Build a {label} connection declaration.")
            for backend, label in (
                ("duckdb", "DuckDB"),
                ("trino", "Trino"),
                ("mysql", "MySQL"),
                ("postgres", "Postgres"),
                ("clickhouse", "ClickHouse"),
            )
        ),
        route(
            "register declaration",
            "register",
            "Save a custom datasource declaration in the local project.",
        ),
        route(
            "test connection",
            "DatasourceCatalog.test",
            "Check a connection and literal round-trip before source work.",
        ),
        route("remove declaration", "remove", "Delete an unneeded local connection declaration."),
        route(
            "host credentials",
            "credential_scope",
            "Supply a host resolver when connection acquisition needs injected credentials.",
        ),
    ),
    "sources": tuple(
        route(label, target, summary)
        for label, target, summary in (
            ("database table", "table", "Select a physical table."),
            ("Parquet files", "parquet", "Select a Parquet path or pattern."),
            ("CSV files", "csv", "Select a CSV path or pattern."),
            (
                "JSON source",
                "json",
                "Select a JSON resource with declared columns and parameters.",
            ),
            ("JSON parameter", "source_param", "Declare a required JSON source parameter."),
        )
    ),
    "inspection": tuple(
        route(label, target, summary)
        for label, target, summary in (
            (
                "physical metadata",
                "inspect",
                "Inspect available schema, columns, and physical types.",
            ),
            (
                "partition bounds",
                "SourceInspection.partitions",
                "Inspect available partitions before choosing a bounded scope.",
            ),
            (
                "exact physical column",
                "SourceInspection.source_column",
                "Read the observed name of one physical column.",
            ),
            (
                "partition scope",
                "partition",
                "Select explicit partition values under row and timeout guards.",
            ),
            (
                "time range scope",
                "time_range",
                "Select a physical half-open time range under explicit guards.",
            ),
            (
                "unpruned scope",
                "unpruned",
                "Use explicit guards when no pruning boundary is available.",
            ),
            (
                "sample scoped rows",
                "SourceInspection.sample",
                "Observe bounded rows; samples do not determine business meaning.",
            ),
            (
                "source-specific question",
                "raw_sql",
                "Use SQL if the public physical checks cannot answer the question.",
            ),
        )
    ),
}
GROUPS: Mapping[DatasourceRootGroup, tuple[str, ...]] = MappingProxyType(
    {
        "connections": (
            *tuple(route.target.canonical_id or "" for route in NAVIGATION_ROUTES["connections"]),
            "s3_secret",
            "http_bearer_secret",
            "http_headers_secret",
        ),
        "sources": tuple(route.target.canonical_id or "" for route in NAVIGATION_ROUTES["sources"]),
        "inspection": tuple(
            route.target.canonical_id or ""
            for route in NAVIGATION_ROUTES["inspection"]
            if route.target.canonical_id != "raw_sql"
        ),
        "root": (
            "connections",
            "sources",
            "inspection",
            "raw_sql",
            "boundary.semantic_authoring",
        ),
    }
)
