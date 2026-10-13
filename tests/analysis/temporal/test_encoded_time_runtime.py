"""Public source consumers preserve results and reads under encoded filtering."""

from dataclasses import replace
from datetime import datetime, timezone

import duckdb
import ibis.expr.types as ir
import pandas as pd
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler import source_time
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.analysis.errors import AnalysisError
from marivo.datasource import engines
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from marivo.semantic.ir import TargetDimensionContract
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.source_trace import SourceTrace


def _encode_orders(case: DslCase, fmt: str, *, integer: bool = False) -> None:
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("SET TimeZone='UTC'")
        value = f"strftime(ordered_at, '{fmt}')"
        if integer:
            value = f"CAST({value} AS BIGINT)"
        connection.execute(
            f'ALTER TABLE "{case.names.order}" ALTER COLUMN ordered_at TYPE '
            f"{'BIGINT' if integer else 'VARCHAR'} USING {value}"
        )
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    parse = f"ms.strptime({fmt!r}" + (", timezone='UTC')" if "%H" in fmt else ")")
    path.write_text(
        path.read_text()
        .replace("ms.timestamp(timezone='UTC')", parse)
        .replace(
            "granularity='second'", "granularity='second'" if "%H" in fmt else "granularity='day'"
        )
    )
    ms.load(workspace_dir=case.root)


def _no_inverse(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    start: object,
    end: object,
) -> None:
    return None


def _forbid_partition_probe(request: PartitionProbeRequest) -> PartitionProbeResult:
    raise AssertionError("Encoded filtering must not inspect partition values")


@pytest.mark.parametrize("integer", [False, True])
def test_observation_preserves_population_windows_and_query_count(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    integer: bool,
    source_trace: SourceTrace,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d", integer=integer)
    monkeypatch.setattr(
        engines,
        "ENGINE_PROFILES",
        {
            **engines.ENGINE_PROFILES,
            "duckdb": replace(
                engines.ENGINE_PROFILES["duckdb"], inspect_partition_values=_forbid_partition_probe
            ),
        },
    )
    reads: list[tuple[str, str]] = []
    original = SourceSession.batches

    def traced(self: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        reads.append((read.purpose, read.sql))
        return original(self, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", traced)
    population = case.session.members(ms.ref.entity("sales.customer"))

    def observe(start: str, end: str) -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.revenue"),
                during=mv.time_scope(start=start, end=end),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .execute()
            .to_pandas()
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = observe("2026-08-01", "2026-09-01")
    optimized_reads = tuple(reads)
    native_count = len(source_trace.native_sql)
    assert optimized.member.tolist() == ["A", "B", "C", "D"]
    assert optimized.value.iloc[:3].tolist() == [450, 150, 400]
    assert pd.isna(optimized.value.iloc[3])
    stages = [sql for purpose, sql in reads if purpose == "analysis.graph.stage"]
    assert len(stages) == 1
    ast = sqlglot.parse_one(stages[0], read="duckdb")
    ranges = [
        comparison
        for where in ast.find_all(sge.Where)
        for comparison in where.find_all((sge.GTE, sge.LT))
        if isinstance(comparison.expression, sge.Literal)
        and comparison.expression.this in {"20260801", "20260901"}
    ]
    assert len(ranges) == 2
    assert all(isinstance(item.this, sge.Column) for item in ranges)
    assert "strptime" not in " ".join(where.sql() for where in ast.find_all(sge.Where)).lower()
    reads.clear()
    source_trace.native_sql.clear()
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = observe("2026-08-01", "2026-09-01")
    pd.testing.assert_frame_equal(optimized, previous)
    assert [purpose for purpose, _ in reads] == [purpose for purpose, _ in optimized_reads]
    assert len(reads) > 0
    assert len(source_trace.native_sql) == native_count
    september = observe("2026-09-01", "2026-10-01")
    assert september.member.tolist() == ["A", "B", "C", "D"]
    assert september.value.iloc[0] == 99
    assert september.value.iloc[1:].isna().all()
    # When the member source is the event source, the window still restricts
    # contributions only; identities before/after the window remain present.
    direct = (
        case.session.members(ms.ref.entity("sales.order"))
        .observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            by=(mv.member(),),
        )
        .execute()
        .to_pandas()
        .sort_values("member")
    )
    assert len(direct) == 5
    assert direct.value.notna().sum() == 3


@pytest.mark.parametrize("integer", [False, True])
def test_grid_window_has_scan_envelope_and_preserves_cells(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    integer: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d", integer=integer)
    reads: list[tuple[str, str]] = []
    original = SourceSession.batches

    def traced(self: SourceSession, read: CompiledRead, *, chunk_size: int) -> SourceBatchStream:
        reads.append((read.purpose, read.sql))
        return original(self, read, chunk_size=chunk_size)

    monkeypatch.setattr(SourceSession, "batches", traced)
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-10-01"), grain=mv.grain("month")
    )
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.revenue"),
                during=grid,
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .execute()
            .to_pandas()
            .sort_values(["member", "coord_0"])
            .reset_index(drop=True)
        )

    optimized = execute()
    optimized_purposes = [purpose for purpose, _ in reads]
    assert len(optimized) == 8
    assert optimized.value.dropna().tolist() == [450, 99, 150, 400]
    sql = next(sql for purpose, sql in reads if purpose == "analysis.graph.stage")
    ast = sqlglot.parse_one(sql, read="duckdb")
    ranges = [
        comparison
        for where in ast.find_all(sge.Where)
        for comparison in where.find_all((sge.GTE, sge.LT))
        if isinstance(comparison.expression, sge.Literal)
        and comparison.expression.this in {"20260801", "20261001"}
    ]
    assert len(ranges) == 2
    assert all(isinstance(item.this, sge.Column) for item in ranges)
    assert any(join.find(sge.Case) is not None for join in ast.find_all(sge.Join))
    reads.clear()
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(optimized, previous)
    assert [purpose for purpose, _ in reads] == optimized_purposes


def test_cumulative_grid_and_endpoint_match_original_temporal_filter(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    path.write_text(path.read_text() + "\nrunning = ms.cumulative(name='running', base=revenue)\n")
    ms.load(workspace_dir=case.root)
    population = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-15", end="2026-10-01"), grain=mv.grain("month")
    )

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.running"),
                at=grid.end,
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .group_by(grid)
            .rollup()
            .execute()
            .to_pandas()
        )

    optimized = execute()
    assert optimized.value.tolist() == [1077, 1176]
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(optimized, previous)
    scalar = (
        population.observe(
            ms.ref.metric("sales.running"),
            at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        )
        .rollup()
        .execute()
        .to_pandas()
    )
    assert scalar.value.tolist() == [1176]


def test_event_column_on_relationship_destination_keeps_its_own_range(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    population = case.session.members(ms.ref.entity("sales.customer"))
    routes = (
        mv.path(ms.ref.relationship("sales.line_order"), ms.ref.relationship("sales.order_buyer")),
    )

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.line_revenue"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=routes,
                by=(mv.member(),),
            )
            .execute()
            .to_pandas()
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = execute()
    assert optimized.value.tolist() == [100, 60]
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(optimized, previous)


def test_outside_window_relationship_mismatch_preserves_observation_scope(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET customer_id='missing' WHERE order_id='j1_july'")
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.revenue"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(mv.member(),),
            )
            .execute()
            .to_pandas()
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = execute()
    assert optimized.set_index("member").value.dropna().to_dict() == {
        "A": 450,
        "B": 150,
        "C": 400,
    }
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(optimized, previous)


def test_encoded_and_parsed_time_failures_preserve_error_fields_and_run_ownership(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j2")
    _encode_orders(case, "%Y%m%d")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            'UPDATE "order" SET amount=9223372036854775807 '
            "WHERE customer_id='A' AND ordered_at LIKE '202608%'"
        )
        connection.execute(
            'INSERT INTO "order" VALUES '
            "('overflow','A','web','paid','20260815',9223372036854775807)"
        )
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> None:
        population.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
            by=(mv.member(),),
        ).execute()

    with pytest.raises(AnalysisError) as optimized:
        execute()
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        with pytest.raises(type(optimized.value)) as previous:
            execute()
    first, second = optimized.value, previous.value
    assert (first.message, first.expected, first.received, first.location, first.repair) == (
        second.message,
        second.expected,
        second.received,
        second.location,
        second.repair,
    )
    for error in (first, second):
        assert error.run_ref is not None
        assert case.session.get_run(error.run_ref).lifecycle == "failed"
    assert first.run_ref != second.run_ref
