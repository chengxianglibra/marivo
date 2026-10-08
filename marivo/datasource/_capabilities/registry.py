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
    DatasourceRootGroup,
    DatasourceTypeContract,
)
from marivo.introspection.live.model import LiveHelpTarget

INPUT_FAMILIES = frozenset(
    {
        "DatasourceSpec",
        "Ref[datasource]",
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
        "DatasourceSpec",
        "Ref[datasource]",
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
    descriptor_rows = (
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
            "sqlite",
            "marivo.datasource.authoring.sqlite",
            "Build a SQLite table/view datasource; native NUMERIC cannot supply exact Decimal. Median, percentile, and string strptime are unsupported.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.sqlite(name="app", path="data/app.sqlite", read_only=True)',
        ),
        _capability(
            "trino",
            "marivo.datasource.authoring.trino",
            "Build a Trino datasource specification.",
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
            "Persist a datasource specification in project metadata.",
            output="DatasourceSummary",
            inputs=_inputs(("subject", "DatasourceSpec")),
            effects=_effects("local_metadata_read", mutations=("project_state",)),
            constraints=("datasource_secret_env_ref", "datasource_register_outside_loader"),
            example='md.register(md.duckdb(name="warehouse", path=":memory:"))',
            preconditions=("a validated DatasourceSpec", "execution outside model loading"),
        ),
        _capability(
            "remove",
            "marivo.datasource.manage.remove",
            "Remove one persisted datasource declaration.",
            output="bool",
            inputs=_inputs(("subject", "DatasourceName")),
            effects=_effects("local_metadata_read", mutations=("project_state",)),
            example='md.remove("warehouse")',
        ),
        _capability(
            "load",
            "marivo.datasource.catalog.load",
            "Load the read-only datasource catalog.",
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
            "list",
            "marivo.datasource.manage.list",
            "List persisted project datasources.",
            output="DatasourceList",
            effects=_LOCAL,
            example="md.list()",
        ),
        _capability(
            "describe",
            "marivo.datasource.manage.describe",
            "Describe persisted datasource fields and env references.",
            output="DatasourceDescription",
            inputs=_inputs(("subject", "DatasourceName")),
            effects=_LOCAL,
            constraints=constraints["configured"],
            example='md.describe("warehouse")',
        ),
        _capability(
            "test",
            "marivo.datasource.manage.test",
            "Round-trip an Ibis-compiled literal within a 30s wall-clock deadline and best-effort cache validated env secrets.",
            output="DatasourceTestResult",
            inputs=_inputs(("subject", "DatasourceReferenceInput")),
            effects=_TEST,
            constraints=constraints["configured"],
            example=('result = md.test(ms.ref.datasource("warehouse"))\nresult.show()\n'),
        ),
        _capability(
            "table",
            "marivo.datasource.source.table",
            "Build a catalog-backed table source with an optional output-name projection.",
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
            "Read live datasource metadata for one physical source.",
            output="SourceInspection",
            inputs=_inputs(("subject", "Ref[datasource]"), ("dependency", "TableSource")),
            effects=_effects("live_metadata_read", "opens_connection"),
            constraints=(*constraints["configured"], "datasource_project_roots"),
            example=(
                'inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\n'
                "inspection.show()"
            ),
            preconditions=("a registered datasource ref",),
            repair_kinds=("register", "reconnect"),
        ),
        _capability(
            "raw_sql",
            "marivo.datasource.manage.raw_sql",
            "Submit one terminal SQL diagnostic verbatim with bounded returned rows and an "
            "enforced timeout. Read-only behavior relies on connection permissions and is best effort. "
            "Results cannot enter typed analysis; "
            "always check is_truncated before drawing conclusions.",
            output="RawSqlResult",
            inputs=_inputs(
                ("subject", "Ref[datasource]"),
                ("dependency", "SqlText"),
                ("dependency", "RawSqlReason"),
                ("scope", "PositiveLimit"),
                ("scope", "PositiveTimeoutGuard"),
            ),
            effects=_effects(
                "potentially_unbounded_read",
                "opens_connection",
                flags=("requires_positive_row_guard", "requires_positive_timeout_guard"),
            ),
            constraints=constraints["configured"],
            example='md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="check connectivity")',
        ),
        _capability(
            "DatasourceCatalog.list",
            "marivo.datasource.catalog.DatasourceCatalog.list",
            "List configured datasources from a loaded catalog.",
            kind="method",
            output="DatasourceList",
            inputs=_inputs(("receiver", "DatasourceCatalog")),
            effects=_LOCAL,
            example="md.load().list()",
            public_entrypoint="catalog.list",
        ),
        _capability(
            "DatasourceCatalog.get",
            "marivo.datasource.catalog.DatasourceCatalog.get",
            "Get one configured datasource summary from a loaded catalog.",
            kind="method",
            output="DatasourceSummary",
            inputs=_inputs(("receiver", "DatasourceCatalog"), ("subject", "DatasourceName")),
            effects=_LOCAL,
            constraints=constraints["configured"],
            example='md.load().get("warehouse")',
            public_entrypoint="catalog.get",
        ),
        _capability(
            "DatasourceCatalog.describe",
            "marivo.datasource.catalog.DatasourceCatalog.describe",
            "Describe one configured datasource from a loaded catalog.",
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
            "Round-trip a configured datasource from a loaded catalog.",
            kind="method",
            output="DatasourceTestResult",
            inputs=_inputs(("receiver", "DatasourceCatalog"), ("subject", "DatasourceName")),
            effects=_TEST,
            constraints=constraints["configured"],
            example=('result = md.load().test("warehouse")\nresult.show()'),
            public_entrypoint="catalog.test",
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
            "Acquire scoped bounded evidence from an inspected source.",
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
                    "requires_positive_timeout_guard",
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
                "snapshot.contract().show()\n"
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
        _capability(
            "authoring",
            None,
            "Describe the datasource authoring workflow boundary.",
            kind="boundary",
            output=None,
            effects=_NONE,
            see_also=(_target("inspect"), _target("SourceInspection.sample")),
        ),
        _capability(
            "boundary.semantic_authoring",
            None,
            "Hand scoped datasource evidence into semantic authoring.",
            kind="boundary",
            output=None,
            effects=_effects(mutations=("semantic_source",)),
            see_also=(LiveHelpTarget(surface="semantic", canonical_id="authoring"),),
        ),
    )
    groups: Mapping[DatasourceRootGroup, tuple[str, ...]] = MappingProxyType(
        {
            "declare_manage": (
                "duckdb",
                "sqlite",
                "trino",
                "mysql",
                "postgres",
                "clickhouse",
                "register",
                "remove",
                "load",
                "list",
                "describe",
                "test",
            ),
            "physical_sources": (
                "table",
                "parquet",
                "csv",
                "source_param",
                "json",
            ),
            "inspect_scope": (
                "inspect",
                "SourceInspection.partitions",
                "SourceInspection.source_column",
                "partition",
                "time_range",
                "unpruned",
            ),
            "acquire_project": ("SourceInspection.sample",),
            "diagnostics_boundaries": ("raw_sql", "authoring", "boundary.semantic_authoring"),
        }
    )
    return DatasourceCapabilityRegistry(
        surface="datasource",
        _descriptors=descriptor_rows,
        _groups=groups,
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
        SQLiteSpec,
        TrinoSpec,
    )
    from marivo.datasource.catalog import DatasourceCatalog
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
        consumers: tuple[str, ...] = (),
        guidance: tuple[str, ...] = (),
    ) -> None:
        contracts[cls] = DatasourceTypeContract(
            name=name,
            producers=tuple(_target(value) for value in producers),
            public_properties=properties,
            public_methods=methods,
            guidance=guidance,
            consumers=tuple(_target(value) for value in consumers),
        )

    spec_producers: tuple[tuple[type, str], ...] = (
        (DuckDBSpec, "duckdb"),
        (SQLiteSpec, "sqlite"),
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
        methods=("list", "get", "describe", "test", *show_render),
    )
    add(
        DatasourceSummary,
        "DatasourceSummary",
        ("register", "DatasourceCatalog.get"),
        properties=("name", "backend_type", "semantic_id"),
        methods=show_render,
    )
    add(
        DatasourceList,
        "DatasourceList",
        ("list", "DatasourceCatalog.list"),
        properties=("items",),
        methods=("ids", *show_render),
    )
    add(
        DatasourceDescription,
        "DatasourceDescription",
        ("describe", "DatasourceCatalog.describe"),
        properties=("name", "backend_type", "literal_fields", "env_refs"),
        methods=show_render,
    )
    add(
        DatasourceFailure,
        "DatasourceFailure",
        ("test", "DatasourceCatalog.test"),
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
        ("test", "DatasourceCatalog.test"),
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
            "requested_limit",
            "returned_row_count",
            "is_truncated",
            "warnings",
        ),
        methods=show_render,
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
        properties=("row_count", "row_count_kind", "size_bytes", "size_kind", "source", "notes"),
    )
    add(
        Partitioning,
        "Partitioning",
        (),
        properties=("state", "fields", "value_source", "values", "values_complete", "truncated"),
    )
    add(
        ExecutionCapabilities,
        "ExecutionCapabilities",
        (),
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
        methods=("contract", *show_render),
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
