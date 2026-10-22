"""Closed registry and consumed-type catalog for ``marivo.datasource``."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringInputRole,
    ConnectionEffect,
    DataAccessEffect,
    EffectFlag,
    MutationEffect,
    RepairKind,
)
from marivo._data_render import _display_help
from marivo.datasource._capabilities.model import (
    DatasourceCapabilityRegistry,
    DatasourceNavigationRoute,
    DatasourceRootGroup,
    DatasourceTypeContract,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.introspection.live.targets import canonical_target

INPUT_FAMILIES = frozenset(
    {
        "CredentialResolver",
        "CredentialRequest",
        "SecretValue",
        "DatasourceSpec",
        "DatasourceSymbol",
        "DatasourceName",
        "DatasourceReferenceInput",
        "DatasourceCatalog",
        "TableSource",
        "ProjectionColumns",
        "PhysicalColumnName",
        "PartitionScope",
        "UnprunedScope",
        "AuthoringScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "DatasourceList",
        "Columns",
        "Column",
        "TableName",
        "SourcePath",
        "SourceParameterName",
        "SourceParameter",
        "SourceParameters",
        "PartitionValues",
        "PartitionOrder",
        "TemporalColumn",
        "TemporalBound",
        "PositiveRowGuard",
        "PositiveTimeoutGuard",
        "PositiveLimit",
        "SnapshotPersistencePolicy",
        "SqlText",
        "RawSqlReason",
        "HelpTarget",
    }
)

OUTPUT_FAMILIES = frozenset(
    {
        "CredentialScope",
        "str",
        "CredentialResolver",
        "CredentialRequest",
        "SecretValue",
        "DatasourceSpec",
        "DatasourceSymbol",
        "DatasourceSummary",
        "DatasourceList",
        "DatasourceDescription",
        "DatasourceCatalog",
        "DatasourceTestResult",
        "TableSource",
        "SourceParameter",
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "PhysicalColumnName",
        "PartitionInspection",
        "DiscoverySnapshot",
        "RawSqlResult",
        "Text",
        "None",
        "bool",
        "list[str]",
    }
)


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="datasource", canonical_id=canonical_id)


def _inputs(
    *families: tuple[AuthoringInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: AuthoringInputRole, family: str) -> AuthoringInputRequirement:
    return AuthoringInputRequirement(role=role, family=family, min_count=0)


def _effects(
    data_access: DataAccessEffect = "none",
    connection: ConnectionEffect = "none",
    mutations: tuple[MutationEffect, ...] = (),
    flags: tuple[EffectFlag, ...] = (),
) -> AuthoringEffects:
    if connection == "opens_connection" and "project_state" not in mutations:
        mutations = (*mutations, "project_state")
    return AuthoringEffects(
        data_access=data_access,
        connection=connection,
        mutations=mutations,
        flags=flags,
    )


_NONE = _effects()
_LOCAL = _effects("local_metadata_read")
_TEST = _effects(
    "local_metadata_read",
    "opens_connection",
    mutations=("user_global_state",),
    flags=("may_cache_resolved_secret",),
)


def _capability(
    canonical_id: str,
    callable_path: str | None,
    summary: str,
    *,
    kind: AuthoringCapabilityKind = "callable",
    output: str | None = None,
    inputs: tuple[AuthoringInputRequirement, ...] = (),
    effects: AuthoringEffects = _NONE,
    constraints: tuple[str, ...] = (),
    example: str | None = None,
    preconditions: tuple[str, ...] = (),
    repair_kinds: tuple[RepairKind, ...] = (),
    see_also: tuple[LiveHelpTarget, ...] = (),
    public_entrypoint: str | None = None,
) -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id=canonical_id,
        kind=kind,
        surface="datasource",
        public_entrypoint=(public_entrypoint if callable_path is not None else None)
        or (f"md.{canonical_id}" if callable_path is not None else None),
        callable_path=callable_path,
        summary=summary,
        input_requirements=inputs,
        output_family=output,
        preconditions=preconditions,
        effects=effects,
        constraints=constraints,
        minimal_example=example,
        see_also=see_also,
        repair_kinds=repair_kinds,
    )


def _build_registry() -> DatasourceCapabilityRegistry:
    """Build the immutable datasource descriptor catalog from live callables."""
    constraints = {
        "declare": (
            "datasource_name_global",
            "datasource_backend_type_required",
            "datasource_field_jsonable",
            "datasource_secret_env_ref",
        ),
        "configured": ("datasource_configured",),
    }
    descriptor_rows: tuple[AuthoringCapability, ...] = (
        _capability(
            "duckdb",
            "marivo.datasource.authoring.duckdb",
            "Build a DuckDB datasource specification with optional URL-scoped, environment-backed HTTP authentication.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=(*constraints["declare"], "duckdb_http_auth_scoped"),
            example=(
                'md.duckdb(name="api", http_scope="https://api.example/v1/", '
                'http_bearer_token_env="API_TOKEN")'
            ),
        ),
        _capability(
            "trino",
            "marivo.datasource.authoring.trino",
            "Build a Trino datasource specification. Marivo's source-native route does not "
            "support exact median/percentile; explicitly declare approx_median or "
            "('approx_percentile', q) when approximation is acceptable. Scoped semantic "
            "readiness blocks incompatible aggregates; execution never substitutes them.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example=(
                'md.trino(name="warehouse", host="trino.example", catalog="hive", '
                'user_env="TRINO_USER", auth_env="TRINO_AUTH")'
            ),
        ),
        _capability(
            "mysql",
            "marivo.datasource.authoring.mysql",
            "Build a MySQL datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.mysql(name="warehouse", host="mysql.example", database="sales")',
        ),
        _capability(
            "postgres",
            "marivo.datasource.authoring.postgres",
            "Build a Postgres datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.postgres(name="warehouse", host="postgres.example", database="sales")',
        ),
        _capability(
            "clickhouse",
            "marivo.datasource.authoring.clickhouse",
            "Build a ClickHouse datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.clickhouse(name="warehouse", host="clickhouse.example")',
        ),
        _capability(
            "register",
            "marivo.datasource.manage.register",
            "Persist a user datasource specification; default is reserved for built-in in-memory DuckDB.",
            output="DatasourceSummary",
            inputs=_inputs(("subject", "DatasourceSpec")),
            effects=_effects("local_metadata_read", mutations=("project_state",)),
            constraints=(
                "datasource_secret_env_ref",
                "datasource_unique_name",
                "datasource_register_outside_loader",
            ),
            example='md.register(md.duckdb(name="warehouse", path=":memory:"))',
            preconditions=("a validated DatasourceSpec", "execution outside model loading"),
        ),
        _capability(
            "remove",
            "marivo.datasource.manage.remove",
            "Remove one persisted datasource declaration; the built-in default cannot be removed.",
            output="bool",
            inputs=_inputs(("subject", "DatasourceName")),
            effects=_effects("local_metadata_read", mutations=("project_state",)),
            constraints=("datasource_unique_name",),
            example='md.remove("warehouse")',
        ),
        _capability(
            "load",
            "marivo.datasource.catalog.load",
            "Load the read-only datasource catalog, including built-in default.",
            output="DatasourceCatalog",
            effects=_LOCAL,
            constraints=("datasource_project_roots",),
            preconditions=(
                "Restart Python after package upgrades or model edits/deletions; ordinary imports are not guaranteed to hot-reload.",
                "Execution failures expose exception_type and the complete original traceback.",
            ),
            example="md.load()",
        ),
        _capability(
            "credential_scope",
            "marivo.datasource.credentials.credential_scope",
            "Select a host resolver for new operations/runtimes; exit restores selection only.",
            output="CredentialScope",
            inputs=_inputs(("dependency", "CredentialResolver")),
            constraints=("credential_resolution", "credential_runtime_binding"),
            example=(
                "class HostResolver:\n"
                "    def resolve(self, request: md.CredentialRequest) -> md.SecretValue:\n"
                "        raise md.DatasourceCredentialError(\n"
                "            reason='missing', reference=request.reference,\n"
                "            datasource=request.datasource, fields=request.fields)\n"
                "with md.credential_scope(resolver=HostResolver()):\n"
                "    result = md.load().test('warehouse')"
            ),
            see_also=(
                _target("CredentialRequest"),
                _target("CredentialResolver"),
                _target("SecretValue"),
                _target("DatasourceCredentialError"),
                _target("DatasourceCredentialScopeError"),
            ),
            repair_kinds=("configure", "reconnect"),
        ),
        _capability(
            "CredentialResolver.resolve",
            "marivo.datasource.credentials.CredentialResolver.resolve",
            "Resolve one reference using host authorization and host-managed service timeouts; return SecretValue or a typed credential failure.",
            kind="method",
            output="SecretValue",
            inputs=_inputs(("receiver", "CredentialResolver"), ("subject", "CredentialRequest")),
            example="value = host_resolver.resolve(request)",
            constraints=("credential_resolution",),
            public_entrypoint="resolver.resolve",
        ),
        _capability(
            "CredentialRequest.show",
            "marivo.datasource.credentials.CredentialRequest.show",
            "Print bounded request facts without resolving credentials or disclosing secret material.",
            kind="method",
            output="None",
            inputs=_inputs(("receiver", "CredentialRequest")),
            example="request.show()",
            public_entrypoint="request.show",
        ),
        _capability(
            "SecretValue.reveal",
            "marivo.datasource.credentials.SecretValue.reveal",
            "Read secret material explicitly for a trusted driver adapter; never print or persist it.",
            kind="method",
            output="str",
            inputs=_inputs(("receiver", "SecretValue")),
            example="value = md.SecretValue('example-only').reveal()",
            public_entrypoint="secret.reveal",
        ),
        _capability(
            "table",
            "marivo.datasource.source.table",
            (
                "Build a catalog-backed table source. columns maps output names to physical "
                "column names (Mapping[str, str]); it does not declare types. Types come from "
                "source metadata when needed at execution; md.inspect exposes current physical "
                "names and types. Values must be physical names, not objects describing "
                "a column or its type."
            ),
            output="TableSource",
            inputs=(
                *_inputs(("subject", "TableName")),
                _optional_input("dependency", "ProjectionColumns"),
            ),
            constraints=("projected_source_runtime_evidence",),
            example=(
                'catalog_source = md.table("orders")\n'
                'projected_source = md.table("events", columns={"event_time": "event.timestamp"})'
            ),
            see_also=(_target("inspect"), _target("raw_sql")),
        ),
        _capability(
            "parquet",
            "marivo.datasource.source.parquet",
            "Build a Parquet file source descriptor.",
            output="TableSource",
            inputs=_inputs(("subject", "SourcePath")),
            example='md.parquet("data/orders.parquet")',
        ),
        _capability(
            "csv",
            "marivo.datasource.source.csv",
            "Build a CSV source descriptor with inferred types and an optional projection.",
            output="TableSource",
            inputs=_inputs(("subject", "SourcePath"), ("dependency", "ProjectionColumns")),
            example='md.csv("data/orders.csv", columns={"order_id": "Order ID"})',
        ),
        _capability(
            "source_param",
            "marivo.datasource.source.source_param",
            "Declare one required runtime query-string or JSON-body value for a JSON source.",
            output="SourceParameter",
            inputs=_inputs(("subject", "SourceParameterName")),
            example='md.source_param("start")',
        ),
        _capability(
            "json",
            "marivo.datasource.source.json",
            "Build a JSON source with inferred types, optional projected JSON paths, and scalar-or-list request bindings.",
            output="TableSource",
            inputs=(
                *_inputs(("subject", "SourcePath"), ("dependency", "ProjectionColumns")),
                _optional_input("dependency", "SourceParameter"),
            ),
            constraints=("json_request_shape",),
            example=(
                'md.json("https://api.example/orders", '
                'columns={"order_id": "id", "app_name": "apps[].name"}, '
                'records_path="$.data", '
                'query_params={"app": md.source_param("apps")})'
            ),
        ),
        _capability(
            "partition",
            "marivo.datasource.source.partition",
            "Build an explicitly partitioned acquisition scope.",
            output="PartitionScope",
            inputs=_inputs(
                ("mapping_key", "PartitionValues"),
                ("scope", "PositiveRowGuard"),
                ("scope", "PositiveTimeoutGuard"),
            ),
            example='md.partition({"dt": "20260710"}, max_rows=1000, timeout_seconds=30)',
        ),
        _capability(
            "time_range",
            "marivo.datasource.source.time_range",
            "Build a half-open temporal scope.",
            output="PartitionScope",
            inputs=_inputs(
                ("subject", "TemporalColumn"),
                ("scope", "TemporalBound"),
                ("scope", "PositiveRowGuard"),
                ("scope", "PositiveTimeoutGuard"),
            ),
            example=(
                'md.time_range("timestamp", start="2026-08-01", end="2026-08-02", '
                "max_rows=1000, timeout_seconds=30)"
            ),
            see_also=(_target("partition"), _target("SourceInspection.sample")),
        ),
        _capability(
            "unpruned",
            "marivo.datasource.source.unpruned",
            "Build an explicitly unpruned acquisition scope.",
            output="UnprunedScope",
            inputs=_inputs(("scope", "PositiveRowGuard"), ("scope", "PositiveTimeoutGuard")),
            example="md.unpruned(max_rows=1000, timeout_seconds=30)",
        ),
        _capability(
            "inspect",
            "marivo.datasource.inspection.inspect",
            "Read physical metadata with operation-owned connections and requested native 30s connection settings.",
            output="SourceInspection",
            inputs=_inputs(("subject", "DatasourceSymbol"), ("dependency", "TableSource")),
            effects=_effects("live_metadata_read", "opens_connection"),
            constraints=(*constraints["configured"], "datasource_project_roots"),
            example=(
                'inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\n'
                "inspection.show()"
            ),
            see_also=(_target("credential_scope"),),
            preconditions=("a built-in or registered datasource ref",),
            repair_kinds=("register", "reconnect"),
        ),
        _capability(
            "raw_sql",
            "marivo.datasource.manage.raw_sql",
            "Run terminal SQL exploration; all returned rows load into client memory without "
            "a row or byte cap. Control query size with filters, partition predicates, aggregation, "
            "and SQL LIMIT; LIMIT does not bound scan cost. Use read-only SQL and credentials: "
            "Trino relies on database-side permissions, not Marivo write prevention. "
            "Native query and connection timeout requests remain; unsupported query timeouts are skipped. "
            "Results cannot enter typed analysis. Actual submitted SQL is recorded in .marivo/logs/ independently of telemetry.",
            output="RawSqlResult",
            inputs=_inputs(
                ("subject", "DatasourceSymbol"),
                ("dependency", "SqlText"),
                ("dependency", "RawSqlReason"),
                ("scope", "PositiveTimeoutGuard"),
            ),
            effects=_effects(
                "potentially_unbounded_read",
                "opens_connection",
                flags=(),
            ),
            constraints=constraints["configured"],
            example='md.raw_sql(ms.ref.datasource("default"), "SELECT 1 LIMIT 1", reason="check connectivity")',
            preconditions=("read-only SQL; Trino credentials must deny writes at the database",),
        ),
        _capability(
            "DatasourceCatalog.list",
            "marivo.datasource.catalog.DatasourceCatalog.list",
            "List built-in default and configured datasources from a loaded catalog.",
            kind="method",
            output="DatasourceList",
            inputs=_inputs(("receiver", "DatasourceCatalog")),
            effects=_LOCAL,
            example="md.load().list()",
            public_entrypoint="catalog.list",
        ),
        _capability(
            "DatasourceCatalog.describe",
            "marivo.datasource.catalog.DatasourceCatalog.describe",
            "Describe built-in default or configured datasource fields and env references from a loaded catalog.",
            kind="method",
            output="DatasourceDescription",
            inputs=_inputs(("receiver", "DatasourceCatalog"), ("subject", "DatasourceName")),
            effects=_LOCAL,
            constraints=constraints["configured"],
            example='md.load().describe("warehouse")',
            public_entrypoint="catalog.describe",
        ),
        _capability(
            "DatasourceCatalog.test",
            "marivo.datasource.catalog.DatasourceCatalog.test",
            "Round-trip an Ibis-compiled literal in the Catalog's bound workspace with native timeout requests and best-effort caching of validated env secrets.",
            kind="method",
            output="DatasourceTestResult",
            inputs=_inputs(
                ("receiver", "DatasourceCatalog"), ("subject", "DatasourceReferenceInput")
            ),
            effects=_TEST,
            constraints=constraints["configured"],
            example=('result = md.load().test("warehouse")\nresult.show()'),
            public_entrypoint="catalog.test",
            see_also=(_target("credential_scope"),),
        ),
        _capability(
            "SourceInspection.partitions",
            "marivo.datasource.inspection.SourceInspection.partitions",
            "Read one bounded ordered edge of partition metadata; status distinguishes complete, truncated, and incomplete metadata.",
            kind="method",
            output="PartitionInspection",
            inputs=(
                *_inputs(("receiver", "SourceInspection")),
                _optional_input("scope", "PositiveLimit"),
                _optional_input("scope", "PartitionOrder"),
            ),
            effects=_effects("live_metadata_read", "opens_connection"),
            constraints=("partition_listing_bounded",),
            example=(
                'inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\n'
                'inspection.partitions(limit=1, order="asc").show()  # ascending edge\n'
                'inspection.partitions(limit=100, order="desc").show()  # descending edge'
            ),
            public_entrypoint="inspection.partitions",
        ),
        _capability(
            "SourceInspection.source_column",
            "marivo.datasource.inspection.SourceInspection.source_column",
            "Return one physical field name from inspected metadata without reading rows.",
            kind="method",
            output="PhysicalColumnName",
            inputs=_inputs(
                ("receiver", "SourceInspection"),
                ("subject", "PhysicalColumnName"),
            ),
            effects=_NONE,
            example=(
                'inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\n'
                'physical_name = inspection.source_column("order_id")\n'
                'source = md.table("orders", columns={"order_id": physical_name})'
            ),
            preconditions=("a table SourceInspection with the requested physical column",),
            repair_kinds=("reauthor",),
            public_entrypoint="inspection.source_column",
            see_also=(_target("inspect"), _target("table")),
        ),
        _capability(
            "SourceInspection.sample",
            "marivo.datasource.inspection.SourceInspection.sample",
            "Acquire scoped evidence; native query timeouts are optional and exclude connection/complete-call timing.",
            kind="method",
            output="DiscoverySnapshot",
            inputs=(
                *_inputs(
                    ("receiver", "SourceInspection"),
                    ("scope", "AuthoringScope"),
                    ("dependency", "Columns"),
                ),
                _optional_input("dependency", "SourceParameters"),
                _optional_input("scope", "SnapshotPersistencePolicy"),
            ),
            effects=_effects(
                "scoped_data_read",
                "opens_connection",
                mutations=("project_state",),
                flags=(
                    "requires_explicit_scope",
                    "requires_positive_row_guard",
                    "may_persist_plaintext_values",
                ),
            ),
            constraints=("json_source_params_exact", "snapshot_value_persistence"),
            example=(
                'inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\n'
                "snapshot = inspection.sample(\n"
                "    scope=md.unpruned(max_rows=1000, timeout_seconds=30),\n"
                '    columns=("order_id", "status", "amount"),\n'
                "    persist_values=True,\n"
                ")\n"
                "snapshot.show()\n"
                "# Retained rows use selected column names, for example:\n"
                'snapshot.retained_values[0]["order_id"]\n'
                '# For md.source_param("apps"), add '
                'source_params={"apps": ["app-1", "app-2"]}.'
            ),
            preconditions=("a current SourceInspection", "an explicit AuthoringScope"),
            repair_kinds=("rescope", "reacquire"),
            public_entrypoint="inspection.sample",
        ),
        *(
            _capability(canonical_id, None, summary, kind="boundary")
            for canonical_id, summary in (
                (
                    "connections",
                    "Choose an existing connection or declare, register, and test a backend.",
                ),
                (
                    "sources",
                    "Choose a physical table or file source and any required JSON parameters.",
                ),
                (
                    "inspection",
                    "Choose metadata, partition bounds, or a sample under an explicit scope.",
                ),
            )
        ),
        _capability(
            "boundary.semantic_authoring",
            None,
            "Hand current scoped physical evidence into semantic authoring. Schema and samples establish physical observations; business authority supplies reusable meaning.",
            kind="boundary",
            output=None,
            effects=_effects(mutations=("semantic_source",)),
            see_also=(LiveHelpTarget(surface="semantic", canonical_id=None),),
        ),
    )

    def route(label: str, target: str, summary: str) -> DatasourceNavigationRoute:
        return DatasourceNavigationRoute(label, _target(canonical_target(target)), summary)

    navigation_routes = {
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
            route(
                "remove declaration", "remove", "Delete an unneeded local connection declaration."
            ),
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
    groups: Mapping[DatasourceRootGroup, tuple[str, ...]] = MappingProxyType(
        {
            "connections": tuple(
                route.target.canonical_id or "" for route in navigation_routes["connections"]
            ),
            "sources": tuple(
                route.target.canonical_id or "" for route in navigation_routes["sources"]
            ),
            "inspection": tuple(
                route.target.canonical_id or ""
                for route in navigation_routes["inspection"]
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
    descriptor_rows = tuple(
        row.model_copy(
            update={
                "canonical_id": canonical_target(row.canonical_id),
                "see_also": tuple(
                    t.model_copy(update={"canonical_id": canonical_target(t.canonical_id)})
                    if t.canonical_id
                    else t
                    for t in row.see_also
                ),
            }
        )
        for row in descriptor_rows
    )
    groups = MappingProxyType(
        {key: tuple(canonical_target(v) for v in values) for key, values in groups.items()}
    )
    return DatasourceCapabilityRegistry(
        surface="datasource",
        _descriptors=descriptor_rows,
        _groups=groups,
        _navigation_routes=MappingProxyType(navigation_routes),
        _by_id=MappingProxyType({row.canonical_id: row for row in descriptor_rows}),
        _by_callable_path=MappingProxyType(
            {row.callable_path: row for row in descriptor_rows if row.callable_path is not None}
        ),
    )


REGISTRY = _build_registry()


def _type_contracts() -> Mapping[type, DatasourceTypeContract]:
    """Build private type contracts without exposing constructors as help targets."""
    from marivo.datasource.authoring import (
        ClickHouseSpec,
        DuckDBSpec,
        MySQLSpec,
        PostgresSpec,
        TrinoSpec,
    )
    from marivo.datasource.catalog import DatasourceCatalog
    from marivo.datasource.credentials import CredentialRequest, CredentialResolver, SecretValue
    from marivo.datasource.inspection import (
        ExecutionCapabilities,
        Partitioning,
        PartitionInspection,
        PhysicalExtent,
        SourceInspection,
    )
    from marivo.datasource.ir import (
        CsvSourceIR,
        JsonSourceIR,
        ParquetSourceIR,
        SourceParamIR,
        TableSourceIR,
    )
    from marivo.datasource.manage import (
        DatasourceDescription,
        DatasourceFailure,
        DatasourceList,
        DatasourceSummary,
        DatasourceTestResult,
        RawSqlResult,
    )
    from marivo.datasource.snapshot import DiscoverySnapshot
    from marivo.datasource.source import PartitionScope, UnprunedScope

    show_render = ("show", "render")
    contracts: dict[type, DatasourceTypeContract] = {}

    def add(
        cls: type,
        name: str,
        producers: tuple[str, ...],
        *,
        properties: tuple[str, ...] = (),
        methods: tuple[str, ...] = (),
        type_page_methods: tuple[str, ...] = (),
        consumers: tuple[str, ...] = (),
        guidance: tuple[str, ...] = (),
    ) -> None:
        contracts[cls] = DatasourceTypeContract(
            name=name,
            producers=tuple(_target(canonical_target(value)) for value in producers),
            public_properties=properties,
            public_methods=methods,
            type_page_methods=type_page_methods,
            guidance=guidance,
            consumers=tuple(_target(canonical_target(value)) for value in consumers),
        )

    add(
        CredentialRequest,
        "CredentialRequest",
        ("credential_scope",),
        properties=(
            "reference",
            "project_root",
            "datasource",
            "fields",
        ),
        methods=("show",),
        consumers=("CredentialResolver.resolve",),
    )
    add(
        CredentialResolver,
        "CredentialResolver",
        (),
        methods=("resolve",),
        consumers=("credential_scope",),
    )
    add(
        SecretValue,
        "SecretValue",
        ("CredentialResolver.resolve",),
        methods=("reveal",),
        consumers=("SecretValue.reveal",),
    )
    spec_producers: tuple[tuple[type, str], ...] = (
        (DuckDBSpec, "duckdb"),
        (TrinoSpec, "trino"),
        (MySQLSpec, "mysql"),
        (PostgresSpec, "postgres"),
        (ClickHouseSpec, "clickhouse"),
    )
    for spec_type, producer in spec_producers:
        add(
            spec_type,
            spec_type.__name__,
            (producer,),
            properties=("name", "backend_type", "fields", "env_refs", "ref"),
            methods=show_render,
            consumers=("register",),
        )
    add(
        DatasourceCatalog,
        "DatasourceCatalog",
        ("load",),
        methods=("list", "describe", "test", *show_render),
    )
    add(
        DatasourceSummary,
        "DatasourceSummary",
        ("register", "DatasourceCatalog.list"),
        properties=("name", "backend_type"),
        methods=show_render,
    )
    add(
        DatasourceList,
        "DatasourceList",
        ("DatasourceCatalog.list",),
        properties=("items",),
        methods=show_render,
    )
    add(
        DatasourceDescription,
        "DatasourceDescription",
        ("DatasourceCatalog.describe",),
        properties=("name", "backend_type", "literal_fields", "env_refs"),
        methods=show_render,
    )
    add(
        DatasourceFailure,
        "DatasourceFailure",
        ("DatasourceCatalog.test",),
        guidance=("Read result.failure from a failed datasource test result.",),
        properties=(
            "code",
            "exception_type",
            "backend_code",
            "backend_name",
            "message",
            "timeout_seconds",
        ),
    )
    add(
        DatasourceTestResult,
        "DatasourceTestResult",
        ("DatasourceCatalog.test",),
        properties=("name", "ok", "latency_ms", "failure", "repair"),
        methods=show_render,
    )
    add(
        RawSqlResult,
        "RawSqlResult",
        ("raw_sql",),
        properties=(
            "datasource",
            "backend_type",
            "sql",
            "reason",
            "columns",
            "types",
            "rows",
            "returned_row_count",
            "warnings",
        ),
        methods=show_render,
        type_page_methods=show_render,
        guidance=_display_help(),
    )
    source_types: tuple[type, ...] = (TableSourceIR, ParquetSourceIR, CsvSourceIR, JsonSourceIR)
    for source_type in source_types:
        add(
            source_type,
            source_type.__name__,
            (source_type.__name__.removesuffix("SourceIR").lower(),),
            properties=(
                ("kind", "table", "database", "columns")
                if source_type is TableSourceIR
                else ("kind",)
            ),
            consumers=("inspect",),
        )
    add(
        SourceParamIR,
        "SourceParamIR",
        ("source_param",),
        properties=("name",),
        consumers=("json",),
    )
    add(
        PartitionScope,
        "PartitionScope",
        ("partition", "time_range"),
        properties=("values", "max_rows", "timeout_seconds"),
        methods=show_render,
        consumers=("SourceInspection.sample",),
    )
    add(
        UnprunedScope,
        "UnprunedScope",
        ("unpruned",),
        properties=("max_rows", "timeout_seconds"),
        methods=show_render,
        consumers=("SourceInspection.sample",),
    )
    add(
        PhysicalExtent,
        "PhysicalExtent",
        (),
        guidance=("Read inspection.physical_extent from md.inspect(...).",),
        properties=("row_count", "row_count_kind", "size_bytes", "size_kind", "source", "notes"),
    )
    add(
        Partitioning,
        "Partitioning",
        (),
        guidance=("Read inspection.partitioning or inspection.partitions().partitioning.",),
        properties=("state", "fields", "value_source", "values", "values_complete", "truncated"),
    )
    add(
        ExecutionCapabilities,
        "ExecutionCapabilities",
        (),
        guidance=("Read inspection.execution_capabilities from md.inspect(...).",),
        properties=(
            "partition_predicate_supported",
            "transformed_partition_supported",
            "timeout_enforced",
            "byte_estimate_supported",
        ),
    )
    add(
        PartitionInspection,
        "PartitionInspection",
        ("SourceInspection.partitions",),
        properties=(
            "datasource",
            "source",
            "partitioning",
            "limit",
            "order",
            "status",
            "issues",
        ),
        methods=show_render,
    )
    add(
        SourceInspection,
        "SourceInspection",
        ("inspect",),
        properties=(
            "datasource",
            "source",
            "physical_extent",
            "partitioning",
            "execution_capabilities",
            "schema",
            "projectable_columns",
            "warnings",
        ),
        methods=("partitions", "source_column", "sample", *show_render),
        consumers=(
            "SourceInspection.partitions",
            "SourceInspection.source_column",
            "SourceInspection.sample",
        ),
    )
    add(
        DiscoverySnapshot,
        "DiscoverySnapshot",
        ("SourceInspection.sample",),
        properties=(
            "id",
            "datasource",
            "source",
            "scope",
            "source_params",
            "columns",
            "schema_fingerprint",
            "profiles",
            "coverage",
            "persist_values",
            "value_evidence_state",
            "cache_status",
            "created_at",
            "expires_at",
            "retained_values",
        ),
        methods=show_render,
    )
    return MappingProxyType(contracts)


TYPE_CONTRACTS = _type_contracts()


def _error_types() -> Mapping[str, type]:
    from marivo.datasource import errors

    return MappingProxyType(
        {
            name: value
            for name, value in vars(errors).items()
            if isinstance(value, type)
            and issubclass(value, errors.DatasourceError)
            and value is not errors.DatasourceError
        }
    )


ERROR_TYPES = _error_types()
