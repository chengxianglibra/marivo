"""Full-row local files and actual distributed roots for cost fixtures."""

from pathlib import Path

import pytest

from marivo.analysis.model.data_types import ScalarDataType
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.runtime.read_types import PhysicalRequirement
from tests.analysis.graph.physical_workloads import _data, _rows, _tables, workload
from tests.analysis.numeric.pandas_coordinates import ordinary_columns


@pytest.mark.parametrize("backend", ("duckdb", "trino"))
def test_cost_fixture_declares_trino_microsecond_time_only(backend: str) -> None:
    rows = _rows(2, "baseline")
    data = _data(rows, backend)
    timestamp = "TIMESTAMP(6)" if backend == "trino" else "TIMESTAMP"
    assert data.columns == (
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), owner BIGINT, amount BIGINT, "
        f"y BIGINT, happened {timestamp}, kind VARCHAR(20), seq BIGINT"
    )
    assert data.rows is rows
    assert "DateTime64(6, 'UTC')" in data.clickhouse_columns


@pytest.mark.parametrize("profile", ("csv", "parquet", "local-json"))
def test_local_file_profiles_retain_full_original_facts_and_fixed_total(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with workload("duckdb", profile, 16, "baseline", tmp_path, monkeypatch) as work:
        logical = work.values()
        captured = logical.persist()
        frame = captured.to_pandas()
        assert len(frame) == 16
        expected_keys = {("a", 2**53 + index, 1) for index in range(16)}
        assert set(frame[ordinary_columns(captured)[0]]) == expected_keys
        assert frame["value"].isna().sum() == 1
        total = logical.reduce({"value": logical["value"].sum()}).persist()
        expected = sum(index % 17 + 1 for index in range(16) if index % 23)
        assert total.to_pandas()["value"].tolist() == [expected]
        assert work.session.persisted_frame(captured.artifact_id).to_pandas().equals(frame)
        assert [
            column.dtype
            for column in captured._local._manifest(captured._require_stored("artifact_id"))
            .data.schema.axes[1]
            .columns
        ] == [
            ScalarDataType("string"),
            ScalarDataType("int64"),
            ScalarDataType("int64"),
        ]


@pytest.mark.runtime
def test_distributed_cost_fixture_populates_every_root_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _rows(1000, "baseline")
    with _tables("clickhouse", "distributed", tmp_path, monkeypatch, rows) as (
        case,
        subjects,
        other,
    ):
        assert isinstance(case.source, TableSourceIR)
        assert case.source.table.endswith("_distributed")
        for index, (source, expected) in enumerate(
            (
                (case.source, 1000),
                (TableSourceIR(subjects, database=case.source.database), 16),
                (TableSourceIR(other, database=case.source.database), 1000),
            )
        ):
            binding = case.session.bind(source, source_identity="root_" + str(index))
            qualified = case.session.qualify(
                binding, PhysicalRequirement("cost_fixture", 1, frozenset({"scan"}))
            )
            read = case.session.compile(
                qualified,
                binding.relation,
                purpose="cost_fixture",
                expected_schema=binding.relation.schema().to_pyarrow(),
            )
            actual = case.session.execute(read).to_dict(orient="records")
            assert len(actual) == expected
            if index != 1:
                assert {row["id"] for row in actual} == {row["id"] for row in rows}
            else:
                assert {row["sid"] for row in actual} == set(range(16))
