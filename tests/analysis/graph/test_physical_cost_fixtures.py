"""Full-row local files and actual distributed roots for cost fixtures."""

import ast
from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import PhysicalRequirement
from marivo.datasource.ir import TableSourceIR
from tests.analysis.graph.physical_workloads import _data, _rows, _tables, workload
from tests.support.paths import PROJECT_ROOT


@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
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


def test_cluster_reader_policy_matches_single_node_deadline_controls() -> None:
    path = PROJECT_ROOT / "tests/datasource/environment/clickhouse_analysis.py"
    tree = ast.parse(path.read_text())

    def policy(name: str) -> str:
        functions = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        assert len(functions) == 1
        settings = [
            node.args[0].value
            for node in ast.walk(functions[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "command"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("ALTER USER analysis_reader SETTINGS")
        ]
        assert len(settings) == 1
        return settings[0]

    expected = (
        "ALTER USER analysis_reader SETTINGS readonly=1, join_use_nulls=1, "
        "enable_materialized_cte=0 CHANGEABLE_IN_READONLY, "
        "max_execution_time=0 CHANGEABLE_IN_READONLY, "
        "cancel_http_readonly_queries_on_client_close=0 CHANGEABLE_IN_READONLY, "
        "timeout_before_checking_execution_speed=0 CHANGEABLE_IN_READONLY"
    )
    assert policy("setup") == policy("setup_cluster") == expected


@pytest.mark.parametrize("profile", ("csv", "parquet", "local-json"))
def test_local_file_profiles_retain_full_original_facts_and_fixed_total(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with workload("duckdb", profile, 1000, "baseline", tmp_path, monkeypatch) as work:
        logical = work.session.members(ms.ref.entity("cost.facts")).observe(
            ms.ref.metric("cost.facts_total"),
            during=mv.time_scope(start="2026-08-01", end="2026-08-02"),
            by=(ms.ref.entity("cost.facts"),),
        )
        assert isinstance(logical, mv.LogicalNumericRelation)
        captured = logical.execute()
        frame = captured.to_pandas()
        assert len(frame) == 1000
        assert set(frame["coord_0"]) == set(range(9007199254740992, 9007199254740992 + 1000))
        assert frame.cell_tag.tolist() == ["defined"] * 1000
        total = captured.rollup().execute()
        expected = sum(index % 17 + 1 for index in range(1000) if index % 23)
        assert total.to_pandas().value.tolist() == [expected]
        assert work.identity(captured)["root_route"] == "ibis"


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
            stream = case.session.batches(read, chunk_size=128)
            try:
                actual = [row for batch in stream for row in batch.to_pylist()]
            finally:
                stream.close()
            assert len(actual) == expected
            if index != 1:
                assert {row["id"] for row in actual} == {row["id"] for row in rows}
            else:
                assert {row["sid"] for row in actual} == set(range(16))
