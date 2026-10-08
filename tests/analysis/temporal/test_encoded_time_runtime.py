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
from marivo.analysis.compiler import domain_preparation, source_time
from marivo.analysis.core.time_authority import SourceTimeAuthority
from marivo.analysis.errors import AnalysisError
from marivo.datasource import engines
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from marivo.semantic.ir import TargetDimensionContract
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.source_trace import SourceTrace

pytestmark = pytest.mark.runtime


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
            by=(ms.ref.entity("sales.order"),),
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
    population = case.session.members(ms.ref.entity("sales.customer")).each(grid)

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.revenue"),
                during=grid.window,
                via=ms.ref.relationship("sales.order_buyer"),
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
            population.each(grid)
            .observe(
                ms.ref.metric("sales.running"),
                at=grid.end,
                via=ms.ref.relationship("sales.order_buyer"),
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
            by=(ms.ref.entity("sales.customer"),),
        )
        .rollup()
        .execute()
        .to_pandas()
    )
    assert scalar.value.tolist() == [1176]


@pytest.mark.parametrize("versioned", [False, True])
def test_event_preparation_reads_encoded_clock_with_exact_window(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    versioned: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y-%m-%d %H:%M:%S")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(
            "INSERT INTO \"order\" VALUES ('follow', 'A', 'web', 'paid', '2026-08-01 00:00:01', 91)"
        )
    path = case.root / "models" / "semantic" / "sales" / "models.py"
    if versioned:
        with duckdb.connect(str(case.database_path)) as connection:
            connection.execute(
                "ALTER TABLE customer ADD COLUMN beginning DATE DEFAULT DATE '2026-07-01'"
            )
            connection.execute("ALTER TABLE customer ADD COLUMN ending DATE")
            connection.execute("UPDATE customer SET ending=DATE '2026-08-02' WHERE customer_id='A'")
            connection.execute("INSERT INTO customer VALUES ('A', 'new', DATE '2026-08-02', NULL)")
        path.write_text(
            path.read_text().replace(
                "primary_key=['customer_id'])",
                "primary_key=['customer_id'], versioning=ms.validity("
                "valid_from=ms.ref.time_dimension('sales.customer.beginning'),"
                "valid_to=ms.ref.time_dimension('sales.customer.ending'),"
                "interval='closed_open', open_end=(None,), timezone='UTC'))",
            )
            + """
beginning = ms.time_dimension_column(name='beginning', entity=customer, column='beginning', granularity='day')
ending = ms.time_dimension_column(name='ending', entity=customer, column='ending', granularity='day')
"""
        )
    path.write_text(
        path.read_text()
        + """
sequence = ms.dimension_column(name='sequence', entity=orders, column='amount')
@ms.event(name='entry', identity=(order_id,), occurred_at=ordered_at,
          participants=(ms.participant(name='subject', path=(buyer,), cardinality='one'),))
def entry(rows):
    return ms.all_rows()
ordering = ms.business_order(name='ordering', subject=customer,
    sequences=(ms.event_sequence(entry, sequence, order='integer'),),
    ai_context=ms.ai_context(business_definition='Per Subject event amount sequence.'))
"""
    )
    ms.load(workspace_dir=case.root)
    anchors = case.session.anchors(
        ms.participant_role(event=ms.ref.event("sales.entry"), name="subject"),
        population=case.session.members(
            ms.ref.entity("sales.customer"),
            at=datetime(2026, 8, 2, tzinfo=timezone.utc) if versioned else None,
        ),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        business_order=ms.ref.business_order("sales.ordering"),
    )

    def execute() -> pd.DataFrame:
        return (
            anchors.observe(
                ms.ref.metric("sales.revenue"),
                within=mv.elapsed(mv.duration(seconds=2)),
                via=ms.ref.relationship("sales.order_buyer"),
            )
            .execute()
            .to_pandas()
        )

    result = execute()
    assert len(result) == 4
    assert result.value.dropna().tolist() == [91]
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        baseline.setattr(domain_preparation, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(result, previous)


def test_event_column_on_relationship_destination_keeps_its_own_range(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    population = case.session.members(ms.ref.entity("sales.customer"))
    routes = mv.routes(
        mv.route(
            ms.ref.entity("sales.order_line"),
            through=(
                ms.ref.relationship("sales.line_order"),
                ms.ref.relationship("sales.order_buyer"),
            ),
        )
    )

    def execute() -> pd.DataFrame:
        return (
            population.observe(
                ms.ref.metric("sales.line_revenue"),
                during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
                via=routes,
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


def test_outside_window_relationship_violation_is_still_checked(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET customer_id='missing' WHERE order_id='j1_july'")
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> None:
        population.observe(
            ms.ref.metric("sales.revenue"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        ).execute()

    with pytest.raises(AnalysisError) as optimized:
        execute()
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        with pytest.raises(type(optimized.value)) as previous:
            execute()
    assert str(optimized.value) == str(previous.value)
