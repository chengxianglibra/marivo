"""Datasource inspection Help specifications."""

from marivo._authoring.model import AuthoringCapability, authoring_effects
from marivo.datasource._help_specs.builders import (
    _LOCAL,
    _NONE,
    _TEST,
    _capability,
    _inputs,
    _optional_input,
    _target,
)
from marivo.introspection.live.model import LiveHelpTarget

constraints = {
    "declare": (
        "datasource_name_global",
        "datasource_backend_type_required",
        "datasource_field_jsonable",
        "datasource_secret_env_ref",
    ),
    "configured": ("datasource_configured",),
}

SPECS: tuple[AuthoringCapability, ...] = (
    _capability(
        "inspect",
        "marivo.datasource.inspection.inspection.inspect",
        "Read physical metadata with operation-owned connections and requested native 30s connection settings.",
        output="SourceInspection",
        inputs=_inputs(("subject", "DatasourceSymbol"), ("dependency", "TableSource")),
        effects=authoring_effects("live_metadata_read", "opens_connection"),
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
        "marivo.datasource.inspection.raw_sql.raw_sql",
        "Run terminal SQL exploration; all returned rows load into client memory without "
        "a row or byte cap. Control query size with filters, partition predicates, aggregation, "
        "and SQL LIMIT; LIMIT does not bound scan cost. Use read-only SQL and credentials. "
        "The backend enforces its native permissions, and write-capable credentials can allow changes. "
        "Native query timeout defaults to 600 seconds; connection settings remain 30 seconds. "
        "Unsupported query timeouts are skipped. Call result.to_pandas() to convert all "
        "returned rows to a pandas DataFrame. "
        "For Trino, quote catalog, schema, and table separately, and verify columns with SHOW COLUMNS. "
        "Results cannot enter typed analysis. Actual submitted SQL is recorded in .marivo/logs/ independently of telemetry.",
        output="RawSqlResult",
        inputs=(
            *_inputs(
                ("subject", "DatasourceSymbol"),
                ("dependency", "SqlText"),
                ("dependency", "RawSqlReason"),
            ),
            _optional_input("scope", "PositiveTimeoutGuard"),
            _optional_input("dependency", "RawSqlTypeLabels"),
            _optional_input("scope", "WorkspaceRoot"),
        ),
        effects=authoring_effects(
            "potentially_unbounded_read",
            "opens_connection",
            flags=(),
        ),
        constraints=constraints["configured"],
        example=(
            'result = md.raw_sql(ms.ref.datasource("default"), "SELECT 1 AS ok", '
            'reason="check connectivity")\n'
            "df = result.to_pandas()"
        ),
        preconditions=("read-only SQL and credentials scoped to source reads",),
        repair_kinds=("retry", "inspect", "configure", "reconnect"),
    ),
    _capability(
        "RawSqlResult.to_pandas",
        "marivo.datasource.inspection.raw_sql.RawSqlResult.to_pandas",
        "Convert all retained query rows to an isolated pandas DataFrame in result column "
        "order without re-executing SQL. pandas infers dtypes from the retained values.",
        kind="method",
        output="pandas.DataFrame",
        inputs=_inputs(("receiver", "RawSqlResult")),
        example="df = result.to_pandas()",
        public_entrypoint="result.to_pandas",
    ),
    _capability(
        "DatasourceCatalog.list",
        "marivo.datasource.inspection.catalog.DatasourceCatalog.list",
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
        "marivo.datasource.inspection.catalog.DatasourceCatalog.describe",
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
        "marivo.datasource.inspection.catalog.DatasourceCatalog.test",
        "Round-trip an Ibis-compiled literal in the Catalog's bound workspace with native timeout requests and best-effort caching of validated env secrets.",
        kind="method",
        output="DatasourceTestResult",
        inputs=_inputs(("receiver", "DatasourceCatalog"), ("subject", "DatasourceReferenceInput")),
        effects=_TEST,
        constraints=constraints["configured"],
        example=('result = md.load().test("warehouse")\nresult.show()'),
        public_entrypoint="catalog.test",
        see_also=(_target("credential_scope"),),
    ),
    _capability(
        "SourceInspection.partitions",
        "marivo.datasource.inspection.source.SourceInspection.partitions",
        "Read one bounded ordered edge of partition metadata; status distinguishes complete, truncated, and incomplete metadata.",
        kind="method",
        output="PartitionInspection",
        inputs=(
            *_inputs(("receiver", "SourceInspection")),
            _optional_input("scope", "PositiveLimit"),
            _optional_input("scope", "PartitionOrder"),
        ),
        effects=authoring_effects("live_metadata_read", "opens_connection"),
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
        "marivo.datasource.inspection.source.SourceInspection.source_column",
        "Return a table's physical field name from inspected metadata for constructing "
        "md.table(columns=...); sample and time_range instead use inspected output names.",
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
        "marivo.datasource.inspection.source.SourceInspection.sample",
        "Acquire scoped evidence; native query timeouts are optional and exclude connection/complete-call timing. "
        "By default a matching snapshot is reused, including an expired snapshot marked stale; "
        "refresh=True forces another read. A source read uses the credential scope active at the call; "
        "a cache hit does not resolve credentials.",
        kind="method",
        output="DiscoveryEvidence",
        inputs=(
            *_inputs(
                ("receiver", "SourceInspection"),
                ("scope", "AuthoringScope"),
                ("dependency", "Columns"),
            ),
            _optional_input("dependency", "SourceParameters"),
            _optional_input("scope", "SnapshotPersistencePolicy"),
        ),
        effects=authoring_effects(
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
        effects=authoring_effects(mutations=("semantic_source",)),
        see_also=(LiveHelpTarget(surface="semantic", canonical_id=None),),
    ),
)
