"""Closed registry and consumed-type catalog for ``marivo.datasource``."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringCapabilityKind,
    AuthoringEffects,
    AuthoringInputRequirement,
    AuthoringStateId,
    AuthoringStateRef,
    ConnectionEffect,
    DataAccessEffect,
    EffectFlag,
    MutationEffect,
    RepairKind,
    TransitionInputRole,
)
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
        "DatasourceConnection",
        "TableSource",
        "PartitionScope",
        "UnprunedScope",
        "AuthoringScope",
        "SourceInspection",
        "DiscoverySnapshot",
        "Columns",
        "Column",
        "TableName",
        "SourcePath",
        "TypedSchema",
        "PartitionValues",
        "PositiveRowGuard",
        "PositiveTimeoutGuard",
        "PositiveLimit",
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
        "DatasourceConnection",
        "DatasourceTestResult",
        "TableSource",
        "PartitionScope",
        "UnprunedScope",
        "SourceInspection",
        "PartitionInspection",
        "DiscoverySnapshot",
        "EntityEvidenceResult",
        "DimensionEvidenceResult",
        "DimensionValuesResult",
        "TimeEvidenceResult",
        "MeasureEvidenceResult",
        "RelationshipEvidenceResult",
        "RawSqlResult",
        "Text",
        "None",
        "bool",
    }
)


def _target(canonical_id: str) -> LiveHelpTarget:
    return LiveHelpTarget(surface="datasource", canonical_id=canonical_id)


def _states(*state_ids: AuthoringStateId) -> tuple[AuthoringStateRef, ...]:
    return tuple(AuthoringStateRef(id=state_id) for state_id in state_ids)


def _inputs(
    *families: tuple[TransitionInputRole, str],
) -> tuple[AuthoringInputRequirement, ...]:
    return tuple(AuthoringInputRequirement(role=role, family=family) for role, family in families)


def _optional_input(role: TransitionInputRole, family: str) -> AuthoringInputRequirement:
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
_CONNECT = _effects("local_metadata_read", "opens_connection", flags=("may_cache_resolved_secret",))
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
    produced_state: AuthoringStateId | None = None,
    required_states: tuple[AuthoringStateRef, ...] = (),
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
        produced_state=(
            AuthoringStateRef(id=produced_state) if produced_state is not None else None
        ),
        required_states=required_states,
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
            "Build a DuckDB datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.duckdb(name="warehouse", path=":memory:")',
            produced_state="datasource.declared",
        ),
        _capability(
            "sqlite",
            "marivo.datasource.authoring.sqlite",
            "Build a SQLite table/view datasource; median, percentile, and string strptime are unsupported.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.sqlite(name="app", path="data/app.sqlite", read_only=True)',
            produced_state="datasource.declared",
        ),
        _capability(
            "trino",
            "marivo.datasource.authoring.trino",
            "Build a Trino datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.trino(name="warehouse", host="trino.example", catalog="hive", auth_env="TRINO_AUTH")',
            produced_state="datasource.declared",
        ),
        _capability(
            "mysql",
            "marivo.datasource.authoring.mysql",
            "Build a MySQL datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.mysql(name="warehouse", host="mysql.example", database="sales")',
            produced_state="datasource.declared",
        ),
        _capability(
            "postgres",
            "marivo.datasource.authoring.postgres",
            "Build a Postgres datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.postgres(name="warehouse", host="postgres.example", database="sales")',
            produced_state="datasource.declared",
        ),
        _capability(
            "clickhouse",
            "marivo.datasource.authoring.clickhouse",
            "Build a ClickHouse datasource specification.",
            output="DatasourceSpec",
            inputs=_inputs(("mapping_key", "DatasourceName")),
            constraints=constraints["declare"],
            example='md.clickhouse(name="warehouse", host="clickhouse.example")',
            produced_state="datasource.declared",
        ),
        _capability(
            "register",
            "marivo.datasource.manage.register",
            "Persist a datasource specification in project metadata.",
            output="DatasourceSummary",
            inputs=_inputs(("subject", "DatasourceSpec")),
            effects=_effects("local_metadata_read", mutations=("project_state",)),
            example='md.register(md.duckdb(name="warehouse", path=":memory:"))',
            preconditions=("datasource.declared",),
            produced_state="datasource.registered",
            required_states=_states("datasource.declared"),
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
            "connect",
            "marivo.datasource.manage.connect",
            "Open a managed live datasource connection.",
            output="DatasourceConnection",
            inputs=_inputs(("subject", "DatasourceName")),
            effects=_CONNECT,
            constraints=constraints["configured"],
            example='with md.connect("warehouse") as con:\n    con.raw_sql("SELECT 1")',
        ),
        _capability(
            "test",
            "marivo.datasource.manage.test",
            "Round-trip a datasource and cache validated env secrets.",
            output="DatasourceTestResult",
            inputs=_inputs(("subject", "DatasourceReferenceInput")),
            effects=_TEST,
            constraints=constraints["configured"],
            example='md.test(ms.ref.datasource("warehouse"))',
            produced_state="datasource.connection_validated",
        ),
        _capability(
            "table",
            "marivo.datasource.source.table",
            "Build a physical table source descriptor.",
            output="TableSource",
            inputs=_inputs(("subject", "TableName")),
            example='md.table("orders")',
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
            "Build a typed CSV source descriptor.",
            output="TableSource",
            inputs=_inputs(("subject", "SourcePath"), ("dependency", "TypedSchema")),
            example='md.csv("data/orders.csv", schema={"order_id": "string"})',
        ),
        _capability(
            "json",
            "marivo.datasource.source.json",
            "Build a typed JSON source descriptor.",
            output="TableSource",
            inputs=_inputs(("subject", "SourcePath"), ("dependency", "TypedSchema")),
            example='md.json("data/orders.json", schema={"order_id": "string"})',
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
            produced_state="scope.explicit",
        ),
        _capability(
            "unpruned",
            "marivo.datasource.source.unpruned",
            "Build an explicitly unpruned acquisition scope.",
            output="UnprunedScope",
            inputs=_inputs(("scope", "PositiveRowGuard"), ("scope", "PositiveTimeoutGuard")),
            example="md.unpruned(max_rows=1000, timeout_seconds=30)",
            produced_state="scope.explicit",
        ),
        _capability(
            "inspect",
            "marivo.datasource.inspection.inspect",
            "Read live datasource metadata for one physical source.",
            output="SourceInspection",
            inputs=_inputs(("subject", "Ref[datasource]"), ("dependency", "TableSource")),
            effects=_effects("live_metadata_read", "opens_connection"),
            constraints=constraints["configured"],
            example='md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))',
            preconditions=("datasource.registered",),
            produced_state="source.inspected",
            required_states=_states("datasource.registered"),
            repair_kinds=("register", "reconnect"),
        ),
        _capability(
            "raw_sql",
            "marivo.datasource.manage.raw_sql",
            "Run bounded read-only terminal analysis, including semantic-gap escape; "
            "results cannot become canonical metrics.",
            output="RawSqlResult",
            inputs=_inputs(
                ("subject", "Ref[datasource]"),
                ("dependency", "SqlText"),
                ("dependency", "RawSqlReason"),
            ),
            effects=_effects(
                "potentially_unbounded_read",
                "opens_connection",
                flags=("requires_positive_row_guard",),
            ),
            constraints=constraints["configured"],
            example='md.raw_sql(ms.ref.datasource("warehouse"), "SELECT 1", reason="check connectivity")',
        ),
        _capability(
            "help",
            "marivo.datasource.help.help",
            "Render the datasource help surface or one target.",
            output="Text",
            inputs=(_optional_input("subject", "HelpTarget"),),
            example="md.help()",
        ),
        _capability(
            "help_text",
            "marivo.datasource.help.help_text",
            "Return datasource help as plain text.",
            output="Text",
            inputs=(_optional_input("subject", "HelpTarget"),),
            example='md.help_text("inspect")',
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
            "DatasourceCatalog.connect",
            "marivo.datasource.catalog.DatasourceCatalog.connect",
            "Connect to one configured datasource from a loaded catalog.",
            kind="method",
            output="DatasourceConnection",
            inputs=_inputs(("receiver", "DatasourceCatalog"), ("subject", "DatasourceName")),
            effects=_CONNECT,
            constraints=constraints["configured"],
            example='with md.load().connect("warehouse") as con:\n    con.raw_sql("SELECT 1")',
            public_entrypoint="catalog.connect",
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
            example='md.load().test("warehouse")',
            public_entrypoint="catalog.test",
        ),
        _capability(
            "DatasourceConnection.disconnect",
            "marivo.datasource.manage.DatasourceConnection.disconnect",
            "Close a managed datasource connection.",
            kind="method",
            output="None",
            inputs=_inputs(("receiver", "DatasourceConnection")),
            example='connection = md.connect("warehouse")\nconnection.disconnect()',
            public_entrypoint="connection.disconnect",
        ),
        _capability(
            "SourceInspection.partitions",
            "marivo.datasource.inspection.SourceInspection.partitions",
            "Read partition evidence captured during inspection.",
            kind="method",
            output="PartitionInspection",
            inputs=_inputs(("receiver", "SourceInspection")),
            example='inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))\ninspection.partitions()',
            public_entrypoint="inspection.partitions",
        ),
        _capability(
            "SourceInspection.sample",
            "marivo.datasource.inspection.SourceInspection.sample",
            "Acquire scoped bounded evidence from an inspected source.",
            kind="method",
            output="DiscoverySnapshot",
            inputs=_inputs(
                ("receiver", "SourceInspection"),
                ("scope", "AuthoringScope"),
                ("dependency", "Columns"),
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
            example='inspection.sample(scope=md.unpruned(max_rows=1000, timeout_seconds=30), columns=("order_id", "amount"))',
            preconditions=("source.inspected", "scope.explicit"),
            produced_state="evidence.acquired",
            required_states=_states("source.inspected", "scope.explicit"),
            repair_kinds=("rescope", "reacquire"),
            public_entrypoint="inspection.sample",
        ),
        _capability(
            "DiscoverySnapshot.entity",
            "marivo.datasource.snapshot.DiscoverySnapshot.entity",
            "Project entity evidence from retained snapshot values.",
            kind="method",
            output="EntityEvidenceResult",
            inputs=_inputs(("receiver", "DiscoverySnapshot"), ("dependency", "Columns")),
            example='snapshot.entity(columns=("order_id",))',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("reacquire",),
            public_entrypoint="snapshot.entity",
        ),
        _capability(
            "DiscoverySnapshot.dimensions",
            "marivo.datasource.snapshot.DiscoverySnapshot.dimensions",
            "Project dimension evidence from retained snapshot values.",
            kind="method",
            output="DimensionEvidenceResult",
            inputs=_inputs(("receiver", "DiscoverySnapshot"), ("dependency", "Columns")),
            example='snapshot.dimensions(columns=("status",))',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("reacquire",),
            public_entrypoint="snapshot.dimensions",
        ),
        _capability(
            "DiscoverySnapshot.values",
            "marivo.datasource.snapshot.DiscoverySnapshot.values",
            "Project bounded retained value frequency evidence.",
            kind="method",
            output="DimensionValuesResult",
            inputs=_inputs(
                ("receiver", "DiscoverySnapshot"), ("subject", "Column"), ("scope", "PositiveLimit")
            ),
            example='snapshot.values("status", limit=10)',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("reacquire",),
            public_entrypoint="snapshot.values",
        ),
        _capability(
            "DiscoverySnapshot.time_dimensions",
            "marivo.datasource.snapshot.DiscoverySnapshot.time_dimensions",
            "Project deterministic time evidence from a snapshot.",
            kind="method",
            output="TimeEvidenceResult",
            inputs=_inputs(("receiver", "DiscoverySnapshot"), ("dependency", "Columns")),
            example='snapshot.time_dimensions(columns=("event_date",))',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("reacquire",),
            public_entrypoint="snapshot.time_dimensions",
        ),
        _capability(
            "DiscoverySnapshot.measures",
            "marivo.datasource.snapshot.DiscoverySnapshot.measures",
            "Project numeric measure evidence from a snapshot.",
            kind="method",
            output="MeasureEvidenceResult",
            inputs=_inputs(("receiver", "DiscoverySnapshot"), ("dependency", "Columns")),
            example='snapshot.measures(columns=("amount",))',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("reacquire",),
            public_entrypoint="snapshot.measures",
        ),
        _capability(
            "DiscoverySnapshot.relationships",
            "marivo.datasource.snapshot.DiscoverySnapshot.relationships",
            "Compare retained evidence from two snapshots.",
            kind="method",
            output="RelationshipEvidenceResult",
            inputs=(
                *_inputs(
                    ("receiver", "DiscoverySnapshot"),
                    ("dependency", "DiscoverySnapshot"),
                ),
                AuthoringInputRequirement(
                    role="mapping_key", family="Columns", exact_keys=("left", "right")
                ),
            ),
            example='snapshot.relationships(other, left=("customer_id",), right=("id",))',
            preconditions=("evidence.acquired",),
            produced_state="evidence.projected",
            required_states=_states("evidence.acquired"),
            repair_kinds=("retry", "reacquire"),
            public_entrypoint="snapshot.relationships",
        ),
        _capability(
            "authoring",
            None,
            "Describe the datasource authoring workflow boundary.",
            kind="transition",
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
                "connect",
                "test",
            ),
            "physical_sources": ("table", "parquet", "csv", "json"),
            "inspect_scope": ("inspect", "SourceInspection.partitions", "partition", "unpruned"),
            "acquire_project": (
                "SourceInspection.sample",
                "DiscoverySnapshot.entity",
                "DiscoverySnapshot.dimensions",
                "DiscoverySnapshot.values",
                "DiscoverySnapshot.time_dimensions",
                "DiscoverySnapshot.measures",
                "DiscoverySnapshot.relationships",
            ),
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
    from marivo.datasource.evidence import (
        DimensionEvidenceResult,
        DimensionValuesResult,
        EntityEvidenceResult,
        MeasureEvidenceResult,
        RelationshipEvidenceResult,
        TimeEvidenceResult,
    )
    from marivo.datasource.inspection import (
        ExecutionCapabilities,
        Partitioning,
        PartitionInspection,
        PhysicalExtent,
        SourceInspection,
    )
    from marivo.datasource.ir import CsvSourceIR, JsonSourceIR, ParquetSourceIR, TableSourceIR
    from marivo.datasource.manage import (
        DatasourceConnection,
        DatasourceDescription,
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
        state_bearing: bool = False,
    ) -> None:
        contracts[cls] = DatasourceTypeContract(
            name=name,
            producers=tuple(_target(value) for value in producers),
            public_properties=properties,
            public_methods=methods,
            consumers=tuple(_target(value) for value in consumers),
            state_bearing=state_bearing,
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
            methods=("contract",),
            consumers=("register",),
            state_bearing=True,
        )
    add(
        DatasourceCatalog,
        "DatasourceCatalog",
        ("load",),
        methods=("list", "get", "describe", "connect", "test", *show_render),
    )
    add(
        DatasourceConnection,
        "DatasourceConnection",
        ("connect", "DatasourceCatalog.connect"),
        properties=("backend",),
        methods=("disconnect",),
    )
    add(
        DatasourceSummary,
        "DatasourceSummary",
        ("register", "DatasourceCatalog.get"),
        properties=("name", "backend_type", "semantic_id"),
        methods=("contract", *show_render),
        state_bearing=True,
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
        methods=("contract", *show_render),
        state_bearing=True,
    )
    add(
        DatasourceTestResult,
        "DatasourceTestResult",
        ("test", "DatasourceCatalog.test"),
        properties=("name", "ok", "latency_ms", "repair"),
        methods=("contract", *show_render),
        state_bearing=True,
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
    )
    source_types: tuple[type, ...] = (TableSourceIR, ParquetSourceIR, CsvSourceIR, JsonSourceIR)
    for source_type in source_types:
        add(
            source_type,
            source_type.__name__,
            (source_type.__name__.removesuffix("SourceIR").lower(),),
            properties=("kind",),
            consumers=("inspect",),
        )
    add(
        PartitionScope,
        "PartitionScope",
        ("partition",),
        properties=("values", "max_rows", "timeout_seconds"),
        methods=("contract",),
        consumers=("SourceInspection.sample",),
        state_bearing=True,
    )
    add(
        UnprunedScope,
        "UnprunedScope",
        ("unpruned",),
        properties=("max_rows", "timeout_seconds"),
        methods=("contract",),
        consumers=("SourceInspection.sample",),
        state_bearing=True,
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
        properties=("datasource", "source", "partitioning", "status", "issues"),
        methods=("contract", *show_render),
        state_bearing=True,
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
            "warnings",
        ),
        methods=("contract", "partitions", "sample", *show_render),
        consumers=("SourceInspection.partitions", "SourceInspection.sample"),
        state_bearing=True,
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
            "columns",
            "schema_fingerprint",
            "profiles",
            "coverage",
            "persist_values",
            "value_evidence_state",
            "cache_status",
            "created_at",
            "expires_at",
        ),
        methods=(
            "entity",
            "dimensions",
            "values",
            "time_dimensions",
            "measures",
            "relationships",
            "contract",
            *show_render,
        ),
        consumers=(
            "DiscoverySnapshot.entity",
            "DiscoverySnapshot.dimensions",
            "DiscoverySnapshot.values",
            "DiscoverySnapshot.time_dimensions",
            "DiscoverySnapshot.measures",
            "DiscoverySnapshot.relationships",
        ),
        state_bearing=True,
    )
    evidence_contracts: tuple[tuple[type, str, tuple[str, ...]], ...] = (
        (
            EntityEvidenceResult,
            "DiscoverySnapshot.entity",
            ("status", "snapshot_id", "columns", "evidence_by_column", "issues", "repair"),
        ),
        (
            DimensionEvidenceResult,
            "DiscoverySnapshot.dimensions",
            ("status", "snapshot_id", "columns", "evidence_by_column", "issues", "repair"),
        ),
        (
            DimensionValuesResult,
            "DiscoverySnapshot.values",
            (
                "status",
                "snapshot_id",
                "column",
                "sample_distinct_count",
                "returned_value_count",
                "sample_values_complete",
                "scope_values_complete",
                "value_evidence_state",
                "frequency_capacity",
                "values",
                "issues",
                "repair",
            ),
        ),
        (
            TimeEvidenceResult,
            "DiscoverySnapshot.time_dimensions",
            ("status", "snapshot_id", "columns", "evidence_by_column", "issues", "repair"),
        ),
        (
            MeasureEvidenceResult,
            "DiscoverySnapshot.measures",
            ("status", "snapshot_id", "columns", "evidence_by_column", "issues", "repair"),
        ),
        (
            RelationshipEvidenceResult,
            "DiscoverySnapshot.relationships",
            (
                "status",
                "left_snapshot_id",
                "right_snapshot_id",
                "left_scope",
                "right_scope",
                "left",
                "right",
                "left_profile",
                "right_profile",
                "type_compatible",
                "evidence_state",
                "retained_overlap_count",
                "retained_left_orphan_count",
                "retained_right_orphan_count",
                "scope_comparability",
                "issues",
                "repair",
            ),
        ),
    )
    for evidence_type, producer, properties in evidence_contracts:
        add(
            evidence_type,
            evidence_type.__name__,
            (producer,),
            properties=properties,
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
