"""Metadata-only authoring inspection contract tests."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import ibis
import pytest

import marivo.datasource as md
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from marivo.datasource.engines.duckdb import PROFILE as DUCKDB_PROFILE
from marivo.datasource.errors import DatasourceError
from marivo.datasource.metadata import ColumnMetadata, PartitionMetadata, TableMetadata


@dataclass
class _QuerySpy:
    user_data_queries: int = 0
    user_data_sql: tuple[str, ...] = ()


@pytest.fixture
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "inspection-test"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def query_spy(monkeypatch: pytest.MonkeyPatch) -> _QuerySpy:
    from ibis.backends.duckdb import Backend

    spy = _QuerySpy()
    original_execute = Backend.execute
    original_raw_sql = Backend.raw_sql

    def counted_execute(self: Backend, *args: object, **kwargs: object) -> object:
        spy.user_data_queries += 1
        return original_execute(self, *args, **kwargs)

    def counted_raw_sql(self: Backend, query: object, *args: object, **kwargs: object) -> object:
        query_text = str(query)
        if re.search(r"\bFROM\s+(?:\"?main\"?\.)?\"?orders\"?\b", query_text, re.IGNORECASE):
            spy.user_data_queries += 1
            spy.user_data_sql = (*spy.user_data_sql, query_text)
        return original_raw_sql(self, query, *args, **kwargs)

    monkeypatch.setattr(Backend, "execute", counted_execute)
    monkeypatch.setattr(Backend, "raw_sql", counted_raw_sql)
    return spy


def _create_orders(path: Path) -> None:
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql("CREATE TABLE orders (order_id VARCHAR, amount DOUBLE)")
    backend.disconnect()


def _register_duckdb(project_root: Path, *, name: str = "warehouse") -> Path:
    path = project_root / f"{name}.duckdb"
    md.register(md.duckdb(name=name, path=str(path)), project_root=project_root)
    return path


def test_inspect_exposes_cost_partition_and_capabilities_without_data_query(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = _register_duckdb(project_root)
    _create_orders(path)

    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))

    assert inspection.partitioning.state in {"known", "none", "unknown"}
    assert inspection.physical_extent.row_count_kind in {"exact", "estimated", "unknown"}
    assert isinstance(inspection.execution_capabilities.timeout_enforced, bool)
    assert inspection.schema
    assert query_spy.user_data_queries == 0


@pytest.mark.parametrize(
    ("factory", "expected_name"),
    [
        (lambda: md.csv("orders.csv", schema={"order_id": "string"}), "order_id"),
        (lambda: md.json("orders.json", schema={"event_id": "string"}), "event_id"),
    ],
)
def test_typed_text_inspection_uses_declared_schema_without_opening_source(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[], md.TableSource],
    expected_name: str,
) -> None:
    _register_duckdb(project_root)
    monkeypatch.setattr(
        "marivo.datasource.backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    source = factory()
    inspection = md.inspect(md.ref("datasource.warehouse"), source)

    assert inspection.schema[0].name == expected_name
    assert inspection.partitioning.state == "none"


def test_partition_states_remain_distinct(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _register_duckdb(project_root)
    _create_orders(path)
    unknown = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))
    none = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string"}),
    )

    known_metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(
            ColumnMetadata(
                name="order_id",
                type="varchar",
                nullable=False,
                comment=None,
                ordinal_position=1,
            ),
            ColumnMetadata(
                name="dt",
                type="date",
                nullable=False,
                comment=None,
                ordinal_position=2,
            ),
        ),
        partitions=(PartitionMetadata(name="dt", type="date"),),
        partition_state="known",
        warnings=(),
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: known_metadata,
    )
    known = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))

    assert (known.partitioning.state, none.partitioning.state, unknown.partitioning.state) == (
        "known",
        "none",
        "unknown",
    )


def test_parquet_inspection_reads_schema_without_executing_user_data_query(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = _register_duckdb(project_root)
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql("CREATE TABLE parquet_source (order_id VARCHAR, amount DOUBLE)")
    parquet_path = project_root / "orders.parquet"
    backend.raw_sql(f"COPY parquet_source TO '{parquet_path}' (FORMAT PARQUET)")
    backend.disconnect()

    inspection = md.inspect(
        md.ref("datasource.warehouse"),
        md.parquet(str(parquet_path)),
    )

    assert tuple(column.name for column in inspection.schema) == ("order_id", "amount")
    assert inspection.partitioning.state == "none"
    assert query_spy.user_data_queries == 0


def test_hive_parquet_unknown_partition_state_is_visible_in_warnings(
    project_root: Path,
) -> None:
    path = _register_duckdb(project_root)
    backend = ibis.duckdb.connect(str(path))
    backend.raw_sql("CREATE TABLE parquet_source (order_id VARCHAR, amount DOUBLE)")
    parquet_path = project_root / "orders.parquet"
    backend.raw_sql(f"COPY parquet_source TO '{parquet_path}' (FORMAT PARQUET)")
    backend.disconnect()

    inspection = md.inspect(
        md.ref("datasource.warehouse"),
        md.parquet(str(parquet_path), hive_partitioning=True),
    )

    assert inspection.partitioning.state == "unknown"
    assert any("unknown" in warning.lower() for warning in inspection.warnings)


def test_unknown_partition_state_rejects_partition_scope_before_field_validation(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = _register_duckdb(project_root)
    _create_orders(path)
    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))
    inspection = replace(
        inspection,
        partitioning=replace(inspection.partitioning, state="unknown", fields=()),
    )

    with pytest.raises(DatasourceError) as exc_info:
        inspection.sample(
            scope=md.partition({"dt": "2026-07-10"}, max_rows=10, timeout_seconds=30),
            columns=("order_id", "amount"),
        )

    assert exc_info.value.details == {
        "code": "partition_state_unknown",
        "stage": "preflight",
        "status": "blocked",
        "expected": "an explicit unpruned scope acknowledging unknown partition state",
        "received": "partition scope",
        "reason": "metadata could not prove whether the source is partitioned",
        "query_executed": False,
        "scope_state": "unknown",
        "next_calls": (
            "inspection.show()",
            "inspection.sample(scope=md.unpruned(max_rows=1000, timeout_seconds=30), "
            "columns=('order_id', 'amount'))",
        ),
    }
    assert query_spy.user_data_queries == 0


def test_unknown_partition_affordances_offer_explicit_unpruned_sample(
    project_root: Path,
) -> None:
    path = _register_duckdb(project_root)
    _create_orders(path)
    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))
    inspection = replace(
        inspection,
        partitioning=replace(inspection.partitioning, state="unknown", fields=()),
    )

    assert inspection.partitions().next_calls == (
        "inspection.show()",
        "inspection.sample(scope=md.unpruned(max_rows=1000, timeout_seconds=30), "
        "columns=('order_id', 'amount'))",
    )


def test_unknown_partition_state_allows_explicit_unpruned_scope(
    project_root: Path,
    query_spy: _QuerySpy,
) -> None:
    path = _register_duckdb(project_root)
    _create_orders(path)
    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))
    inspection = replace(
        inspection,
        partitioning=replace(inspection.partitioning, state="unknown", fields=()),
    )

    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=10, timeout_seconds=30),
        columns=("order_id",),
        refresh=True,
    )

    assert snapshot.scope == md.unpruned(max_rows=10, timeout_seconds=30)
    assert any("unknown" in warning.lower() for warning in inspection.warnings)
    assert query_spy.user_data_queries == 1


def test_partitions_only_reshapes_captured_metadata(
    project_root: Path,
) -> None:
    _register_duckdb(project_root)
    source = md.csv("orders.csv", schema={"order_id": "string"})
    result = md.inspect(md.ref("datasource.warehouse"), source).partitions()

    assert result.partitioning.state == "none"
    assert result.status == "complete"


def test_file_source_requires_duckdb_without_opening_backend(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    md.register(
        md.trino(name="warehouse", host="localhost", catalog="hive"),
        project_root=project_root,
    )
    monkeypatch.setattr(
        "marivo.datasource.backends.build_backend",
        lambda *_args, **_kwargs: pytest.fail("backend opened"),
    )

    with pytest.raises(DatasourceError) as exc_info:
        md.inspect(
            md.ref("datasource.warehouse"),
            md.csv("orders.csv", schema={"order_id": "string"}),
        )

    assert exc_info.value.details["code"] == "source_mismatch"
    assert exc_info.value.details["query_executed"] is False


def test_sample_rejects_unknown_source_column_before_executor(project_root: Path) -> None:
    _register_duckdb(project_root)
    inspection = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string"}),
    )

    with pytest.raises(DatasourceError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
            columns=("missing",),
        )

    assert exc_info.value.details == {
        "code": "unknown_source_column",
        "stage": "preflight",
        "status": "blocked",
        "expected": "columns from the inspected source schema",
        "received": "missing",
        "reason": "selected column 'missing' is not present in the inspected source schema",
        "query_executed": False,
        "scope_state": "none",
        "next_calls": ("md.inspect(...)",),
    }


def test_sample_rejects_unenforceable_timeout_before_executor(project_root: Path) -> None:
    _register_duckdb(project_root)
    base = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string"}),
    )
    inspection = replace(
        base,
        execution_capabilities=replace(
            base.execution_capabilities,
            timeout_enforced=False,
        ),
    )

    with pytest.raises(DatasourceError) as exc_info:
        inspection.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
            columns=("order_id",),
        )

    assert exc_info.value.details["code"] == "timeout_not_enforceable"
    assert exc_info.value.details["query_executed"] is False


def test_sample_rejects_transform_and_incomplete_partition_scope(project_root: Path) -> None:
    _register_duckdb(project_root)
    base = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string", "dt": "date"}),
    )
    known = replace(
        base,
        partitioning=replace(
            base.partitioning,
            state="known",
            fields=(PartitionMetadata(name="dt", type="date"),),
            values_complete=False,
        ),
    )

    with pytest.raises(DatasourceError) as incomplete:
        known.sample(
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
            columns=("order_id",),
        )
    assert incomplete.value.details["code"] == "incomplete_partition_fields"
    assert incomplete.value.details["query_executed"] is False

    transformed = replace(
        known,
        partitioning=replace(
            known.partitioning,
            fields=(PartitionMetadata(name="dt", type="date", transform="day"),),
        ),
    )
    with pytest.raises(DatasourceError) as unsupported:
        transformed.sample(
            scope=md.partition({"dt": "2026-07-10"}, max_rows=10, timeout_seconds=30),
            columns=("order_id",),
        )
    assert unsupported.value.details["code"] == "transformed_partition_unsupported"
    assert unsupported.value.details["query_executed"] is False


def test_identity_partition_transform_is_rejected_in_v1(project_root: Path) -> None:
    _register_duckdb(project_root)
    base = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string", "dt": "date"}),
    )
    identity = replace(
        base,
        partitioning=replace(
            base.partitioning,
            state="known",
            fields=(PartitionMetadata(name="dt", type="date", transform="identity"),),
            values_complete=False,
        ),
        execution_capabilities=replace(
            base.execution_capabilities,
            timeout_enforced=False,
        ),
    )

    with pytest.raises(DatasourceError) as exc_info:
        identity.sample(
            scope=md.partition({"dt": "2026-07-10"}, max_rows=10, timeout_seconds=30),
            columns=("order_id",),
        )

    assert exc_info.value.details["code"] == "transformed_partition_unsupported"


def test_transformed_partition_inspection_does_not_call_value_hook(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register_duckdb(project_root)
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(ColumnMetadata("dt", "date", False, None, 1),),
        partitions=(PartitionMetadata(name="dt", type="date", transform="month"),),
        partition_state="known",
        warnings=(),
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: metadata,
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection.require_profile_for_backend_type",
        lambda _backend_type: replace(
            DUCKDB_PROFILE,
            inspect_partition_values=lambda _request: pytest.fail("partition hook called"),
        ),
    )

    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))
    partition_result = inspection.partitions()

    assert inspection.partitioning.values == ()
    assert inspection.partitioning.values_complete is False
    assert partition_result.status == "incomplete"
    assert all("md.partition" not in call for call in partition_result.next_calls)


def test_affordances_advertise_available_sampling_without_self_refresh(
    project_root: Path,
) -> None:
    _register_duckdb(project_root)
    inspection = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string"}),
    )

    assert "sample(" in inspection.next_safe_action
    assert inspection.partitions().next_calls == ("md.unpruned(max_rows=..., timeout_seconds=...)",)


def test_direct_partition_scope_rejects_duplicate_fields(project_root: Path) -> None:
    _register_duckdb(project_root)
    base = md.inspect(
        md.ref("datasource.warehouse"),
        md.csv("orders.csv", schema={"order_id": "string", "dt": "date"}),
    )
    known = replace(
        base,
        partitioning=replace(
            base.partitioning,
            state="known",
            fields=(PartitionMetadata(name="dt", type="date"),),
            values_complete=False,
        ),
    )
    duplicate_scope = md.PartitionScope(
        values=(("dt", "2026-07-10"), ("dt", "2026-07-11")),
        max_rows=10,
        timeout_seconds=30,
    )

    with pytest.raises(DatasourceError) as exc_info:
        known.sample(scope=duplicate_scope, columns=("order_id",))

    assert exc_info.value.details["code"] == "incomplete_partition_fields"
    assert exc_info.value.details["query_executed"] is False


@pytest.mark.parametrize(
    ("row_count", "expected_truncated", "expected_complete"),
    [(100, False, True), (101, True, False)],
)
def test_partition_hook_uses_extra_row_to_detect_exact_boundary(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_count: int,
    expected_truncated: bool,
    expected_complete: bool,
) -> None:
    _register_duckdb(project_root)
    metadata = TableMetadata(
        datasource="warehouse",
        table="orders",
        database=None,
        backend_type="duckdb",
        comment=None,
        columns=(ColumnMetadata("dt", "date", False, None, 1),),
        partitions=(PartitionMetadata(name="dt", type="date"),),
        partition_state="known",
        warnings=(),
    )
    requested_limits: list[int] = []

    def partition_hook(request: PartitionProbeRequest) -> PartitionProbeResult:
        requested_limits.append(request.limit)
        return PartitionProbeResult(
            rows=tuple({"dt": f"partition-{index:03d}"} for index in range(row_count)),
            value_source="metadata",
        )

    monkeypatch.setattr(
        "marivo.datasource.inspection._inspect_source",
        lambda *_args, **_kwargs: metadata,
    )
    monkeypatch.setattr(
        "marivo.datasource.inspection.require_profile_for_backend_type",
        lambda _backend_type: replace(
            DUCKDB_PROFILE,
            inspect_partition_values=partition_hook,
        ),
    )

    inspection = md.inspect(md.ref("datasource.warehouse"), md.table("orders"))

    assert requested_limits == [101]
    assert len(inspection.partitioning.values) == 100
    assert inspection.partitioning.truncated is expected_truncated
    assert inspection.partitioning.values_complete is expected_complete
