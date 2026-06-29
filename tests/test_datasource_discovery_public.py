"""Public datasource discovery entry point tests."""

from __future__ import annotations

import inspect
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.authoring import DuckDBSpec


def _register_orders(project_root: Path) -> None:
    db_path = project_root / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.create_table(
        "orders",
        {
            "order_id": [1, 2, 3, 4],
            "customer_id": [10, 20, 20, 30],
            "status": ["paid", "paid", "", "void"],
            "amount": [10.0, -5.0, 0.0, None],
            "created_at": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        },
    )
    con.create_table("customers", {"customer_id": [10, 20, 20, 40]})
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=project_root)


def test_public_discover_column_families_return_display_results(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    warehouse = md.ref("datasource.warehouse")
    source = md.table("orders")
    scope = md.unpruned(max_rows=10)

    entity = md.discover_entity(warehouse, source, scope=scope, project_root=tmp_path)
    assert isinstance(entity, md.DatasourceResult)
    entity_render = entity.render()
    assert "schema columns:" in entity_render
    assert "order_id |" in entity_render
    assert "primary key evidence:" in entity_render
    assert "order_id" in entity_render
    assert "sampled_unique" in entity_render
    assert "created_at" in entity_render
    assert "distinct=" in entity_render
    assert "nulls=" in entity_render

    dimensions = md.discover_dimensions(
        warehouse,
        source,
        columns=("status",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(dimensions, md.DatasourceResult)
    dimensions_render = dimensions.render()
    assert "status" in dimensions_render
    assert "dimension_empty_values_present" in dimensions_render
    assert ".columns" not in dimensions_render
    assert ".profile" not in dimensions_render

    times = md.discover_time_dimensions(
        warehouse,
        source,
        columns=("created_at",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(times, md.DatasourceResult)
    times_render = times.render()
    assert "time column evidence:" in times_render
    assert "created_at" in times_render
    assert "%Y-%m-%d" in times_render
    assert "range=" in times_render
    assert ".columns" not in times_render

    measures = md.discover_measures(
        warehouse,
        source,
        columns=("amount",),
        scope=scope,
        project_root=tmp_path,
    )
    assert isinstance(measures, md.DatasourceResult)
    measures_render = measures.render()
    assert "amount" in measures_render
    assert "measure_numeric_type" in measures_render
    assert ".columns" not in measures_render


def test_public_discover_measures_reports_missing_column_not_type_mismatch(
    tmp_path: Path,
) -> None:
    _register_orders(tmp_path)

    result = md.discover_measures(
        md.ref("datasource.warehouse"),
        md.table("orders"),
        columns=("elapsed_time_millis",),
        scope=md.unpruned(max_rows=10),
        project_root=tmp_path,
    )

    rendered = result.render()
    assert "elapsed_time_millis" in rendered
    assert "column_not_found" in rendered
    assert "measure_non_numeric_type" not in rendered


def test_public_discover_relationship_replaces_probe_join_keys(tmp_path: Path) -> None:
    _register_orders(tmp_path)
    warehouse = md.ref("datasource.warehouse")

    result = md.discover_relationship(
        from_side=md.JoinSide(warehouse, md.table("orders"), columns=("customer_id",)),
        to_side=md.JoinSide(warehouse, md.table("customers"), columns=("customer_id",)),
        scope=md.unpruned(max_rows=100),
        key_sample_size=10,
        project_root=tmp_path,
    )

    assert isinstance(result, md.DatasourceResult)
    rendered = result.render()
    assert "sampled_keys=3" in rendered
    assert "matched=2" in rendered
    assert "cardinality=many_to_one" in rendered
    assert "key type evidence:" in rendered
    assert ".evidence" not in rendered


def test_public_discover_dimension_values_are_bounded_runtime_evidence(tmp_path: Path) -> None:
    _register_orders(tmp_path)

    result = md.discover_dimension_values(
        md.ref("datasource.warehouse"),
        md.table("orders"),
        column="status",
        scope=md.unpruned(max_rows=10),
        limit=2,
        project_root=tmp_path,
    )

    assert isinstance(result, md.DatasourceResult)
    rendered = result.render()
    assert "paid" in rendered
    assert "not_exhaustive" in rendered
    assert "dimension_values_truncated" in rendered
    assert ".values" not in rendered
    assert ".issues" not in rendered


def test_datasource_public_surface_exposes_discovery_and_inspection() -> None:
    public_names = set(md.__all__)
    assert {
        "DatasourceResult",
        "discover_entity",
        "discover_dimensions",
        "discover_time_dimensions",
        "discover_measures",
        "discover_relationship",
        "discover_dimension_values",
        "inspect_table",
        "inspect_partitions",
        "raw_sql",
        "partition",
        "unpruned",
    }.issubset(public_names)
    assert (
        not {
            "DiscoveryResult",
            "inspect_source",
            "inspect_columns",
            "probe_join_keys",
            "ColumnInspection",
            "JoinKeyProbe",
            "latest_partition",
            "RawSqlResult",
        }
        & public_names
    )

    text = md.help_text()
    assert "md.inspect_table" in text
    assert "md.inspect_partitions" in text
    assert "md.discover_entity" in text
    assert "md.raw_sql" in text
    assert "md.inspect_columns" not in text
    assert "md.latest_partition" not in text


def test_discover_entity_has_no_include_partitions_parameter() -> None:
    signature = inspect.signature(md.discover_entity)

    assert "include_partitions" not in signature.parameters


def test_public_inspect_table_returns_metadata_only_result(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.raw_sql("COMMENT ON TABLE orders IS 'One row per order'")
    con.raw_sql("COMMENT ON COLUMN orders.amount IS 'Gross amount'")
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=tmp_path)

    result = md.inspect_table(
        md.ref("datasource.warehouse"),
        md.table("orders"),
        project_root=tmp_path,
    )

    assert isinstance(result, md.DatasourceResult)
    rendered = result.render()
    assert "TableMetadata" in rendered
    assert "One row per order" in rendered
    assert "amount" in rendered
    assert "Gross amount" in rendered
    assert "distinct=" not in rendered
    assert "sample_values" not in rendered


def test_public_inspect_table_requires_datasource_ref(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"datasource must be md\.DatasourceRef"):
        md.inspect_table("warehouse", md.table("orders"), project_root=tmp_path)  # type: ignore[arg-type]


def test_public_inspect_partitions_non_partitioned_table_is_not_unavailable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "warehouse.duckdb"
    con = ibis.duckdb.connect(db_path)
    con.raw_sql("CREATE TABLE orders (order_id INTEGER NOT NULL, amount DOUBLE)")
    con.disconnect()
    md.register(DuckDBSpec(name="warehouse", path=str(db_path)), project_root=tmp_path)

    result = md.inspect_partitions(
        md.ref("datasource.warehouse"),
        md.table("orders"),
        project_root=tmp_path,
    )

    rendered = result.render()
    assert "columns=none" in rendered
    assert "No partition columns" in rendered
    assert "Discovery does not require md.partition" in rendered
    assert "Partition values unavailable" not in rendered
    assert "Discovery still requires" not in rendered


@pytest.mark.parametrize("scope", [None, md.unpruned(max_rows=10)])
def test_partitioned_table_discovery_requires_explicit_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: md.ScanScope | None,
) -> None:
    _register_orders(tmp_path)

    from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata

    inspected = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, None, 1),
            ColumnMetadata("log_date", "VARCHAR", False, None, 2),
            ColumnMetadata("log_hour", "VARCHAR", False, None, 3),
        ),
        partitions=(
            PartitionMetadata("log_date", "VARCHAR", None, None),
            PartitionMetadata("log_hour", "VARCHAR", None, None),
        ),
        warnings=(),
    )
    sampled = False

    def _fake_inspect_source(*_args: object, **_kwargs: object) -> TableMetadata:
        return inspected

    def _fail_inspect_columns(*_args: object, **_kwargs: object) -> object:
        nonlocal sampled
        sampled = True
        raise AssertionError("sample execution should not run without explicit partition")

    import marivo.datasource.discover as discover_mod

    monkeypatch.setattr(discover_mod, "_inspect_source", _fake_inspect_source)
    monkeypatch.setattr(discover_mod, "_inspect_columns", _fail_inspect_columns)

    with pytest.raises(Exception, match="Partition filter required") as exc_info:
        md.discover_entity(
            md.ref("datasource.warehouse"),
            md.table("orders"),
            scope=scope,
            project_root=tmp_path,
        )

    message = str(exc_info.value)
    assert "The table is partitioned by: log_date, log_hour." in message
    assert 'md.inspect_partitions(ds, md.table("orders"), limit=50).show()' in message
    assert 'scope = md.partition({"log_date": "...", "log_hour": "..."})' in message
    assert sampled is False


def test_partitioned_table_discovery_rejects_incomplete_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_orders(tmp_path)

    from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata

    inspected = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, None, 1),
            ColumnMetadata("log_date", "VARCHAR", False, None, 2),
            ColumnMetadata("log_hour", "VARCHAR", False, None, 3),
        ),
        partitions=(
            PartitionMetadata("log_date", "VARCHAR", None, None),
            PartitionMetadata("log_hour", "VARCHAR", None, None),
        ),
        warnings=(),
    )

    import marivo.datasource.discover as discover_mod

    monkeypatch.setattr(discover_mod, "_inspect_source", lambda *_args, **_kwargs: inspected)

    with pytest.raises(Exception, match="missing: log_hour"):
        md.discover_entity(
            md.ref("datasource.warehouse"),
            md.table("orders"),
            scope=md.partition({"log_date": "20260629"}),
            project_root=tmp_path,
        )


def test_partitioned_table_discovery_allows_complete_explicit_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_orders(tmp_path)

    from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata
    from marivo.datasource.scan import ColumnInspection, ColumnProfile, ScanReport

    inspected = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata("order_id", "INTEGER", False, None, 1),
            ColumnMetadata("log_date", "VARCHAR", False, None, 2),
            ColumnMetadata("log_hour", "VARCHAR", False, None, 3),
        ),
        partitions=(
            PartitionMetadata("log_date", "VARCHAR", None, None),
            PartitionMetadata("log_hour", "VARCHAR", None, None),
        ),
        warnings=(),
    )
    seen_partition: dict[str, str] | None = None

    def _fake_inspect_columns(
        _datasource: str,
        source: md.TableSource,
        *,
        columns: tuple[str, ...] | None,
        scope: md.ScanScope,
        project_root: Path | None,
    ) -> ColumnInspection:
        del columns, project_root
        nonlocal seen_partition
        seen_partition = dict(scope.partition or {})
        return ColumnInspection(
            datasource="warehouse",
            source=source,
            profiles=(
                ColumnProfile(
                    name="order_id",
                    data_type="INTEGER",
                    nullable=False,
                    comment=None,
                    null_count=0,
                    empty_count=0,
                    distinct_count=1,
                    top_values=(),
                    sample_values=(),
                    min_value=1,
                    max_value=1,
                    type_family="numeric",
                ),
            ),
            scan=ScanReport(
                partition_used=scope.partition,
                partition_resolution="explicit",
                rows_scanned=1,
                columns_scanned=("order_id",),
                truncated=False,
                elapsed_seconds=0.01,
                warnings=(),
            ),
        )

    import marivo.datasource.discover as discover_mod

    monkeypatch.setattr(discover_mod, "_inspect_source", lambda *_args, **_kwargs: inspected)
    monkeypatch.setattr(discover_mod, "_inspect_columns", _fake_inspect_columns)

    result = md.discover_entity(
        md.ref("datasource.warehouse"),
        md.table("orders"),
        scope=md.partition({"log_date": "20260629", "log_hour": "15"}),
        project_root=tmp_path,
    )

    assert isinstance(result, md.DatasourceResult)
    assert seen_partition == {"log_date": "20260629", "log_hour": "15"}
