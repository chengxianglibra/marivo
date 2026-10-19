"""Public source consumers preserve results and reads under encoded filtering."""

from dataclasses import replace

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
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from marivo.semantic.ir import TargetDimensionContract
from tests.shared_fixtures import DslCase, DslCaseFactory
from tests.support.source_trace import SourceTrace


def _member_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({"value": frame[("value", "value")]})
    for axis in frame.attrs["axes"]:
        result["coord_0" if axis["role"] == "calendar" else "member"] = frame[
            ("coordinate", axis["token"])
        ]
    return result


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
    original = SourceSession.execute

    def traced(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append((read.purpose, read.sql))
        return original(self, read)

    monkeypatch.setattr(SourceSession, "execute", traced)
    population = case.session.members(ms.ref.entity("sales.customer"))

    def observe(start: str, end: str) -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": case.session.catalog.metrics.get("sales.revenue")},
                during=case.session.time_scope(start=start, end=end),
                by=population,
            )
            .compute()
            .to_pandas()
            .pipe(_member_frame)
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = observe("2026-08-01", "2026-09-01")
    optimized_reads = tuple(reads)
    native_count = len(source_trace.native_sql)
    assert optimized.member.tolist() == ["A", "B", "C", "D"]
    assert optimized.value.iloc[:3].tolist() == [450, 150, 400]
    assert optimized.value.iloc[3] == 0
    stages = [sql for purpose, sql in reads if purpose.startswith("query.")]
    assert len(stages) == 2  # The Metric and the complete member domain have distinct sources.
    business = [sql for sql in stages if "ordered_at" in sql]
    assert len(business) == 1
    ast = sqlglot.parse_one(business[0], read="duckdb")
    ranges = [
        comparison
        for where in ast.find_all(sge.Where)
        for comparison in where.find_all(sge.GTE, sge.LT)
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
    assert september.value.iloc[1:].eq(0).all()
    # When the member source is the event source, the window still restricts
    # contributions only; identities before/after the window remain present.
    orders = case.session.members(ms.ref.entity("sales.order"))
    direct = (
        case.session.observe(
            {"value": case.session.catalog.metrics.get("sales.revenue")},
            during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=orders,
        )
        .compute()
        .to_pandas()
    )
    assert len(direct) == 5
    assert direct[("value", "value")].ne(0).sum() == 3


@pytest.mark.parametrize("integer", [False, True])
def test_grid_window_has_scan_envelope_and_preserves_cells(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    integer: bool,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d", integer=integer)
    reads: list[tuple[str, str]] = []
    original = SourceSession.execute

    def traced(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append((read.purpose, read.sql))
        return original(self, read)

    monkeypatch.setattr(SourceSession, "execute", traced)
    grid = case.session.time_scope(start="2026-08-01", end="2026-10-01").by(mv.grain("month"))
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> pd.DataFrame:
        rows = (
            case.session.observe(
                {"value": case.session.catalog.metrics.get("sales.revenue")},
                during=grid,
                by=mv.product(grid, population),
            )
            .compute()
            .to_pandas()
        )
        # Check producer order before the separate membership normalization oracle.
        assert rows.iloc[:, 0].tolist() == [0] * 4 + [1] * 4
        assert rows.iloc[:, 1].tolist() == ["A", "B", "C", "D"] * 2
        directory = rows.attrs["frozen_coordinates"][0]["rows"]
        assert [stamp.isoformat() for stamp in directory["nominal_start"]] == [
            "2026-08-01T00:00:00+00:00",
            "2026-09-01T00:00:00+00:00",
        ]
        assert [stamp.isoformat() for stamp in directory["actual_end"]] == [
            "2026-09-01T00:00:00+00:00",
            "2026-10-01T00:00:00+00:00",
        ]
        return _member_frame(rows).sort_values(["member", "coord_0"]).reset_index(drop=True)

    optimized = execute()
    optimized_purposes = [purpose for purpose, _ in reads]
    assert len(optimized) == 8
    assert optimized.value.tolist() == [450, 99, 150, 0, 400, 0, 0, 0]
    sql = next(sql for purpose, sql in reads if purpose.startswith("query."))
    ast = sqlglot.parse_one(sql, read="duckdb")
    ranges = [
        comparison
        for where in ast.find_all(sge.Where)
        for comparison in where.find_all(sge.GTE, sge.LT)
        if isinstance(comparison.expression, sge.Literal)
        and comparison.expression.this in {"20260801", "20261001"}
    ]
    assert len(ranges) == 2
    assert all(isinstance(item.this, sge.Column) for item in ranges)
    bucket_branches = [
        branch
        for node in ast.find_all(sge.Case)
        for branch in node.args["ifs"]
        if isinstance(branch.args["true"], sge.Literal) and branch.args["true"].this in {"0", "1"}
    ]
    bucket_branches.sort(key=lambda branch: int(branch.args["true"].this))
    assert [branch.args["true"].this for branch in bucket_branches] == ["0", "1"]
    # The current source lowering puts bucket assignment in SELECT. Placement
    # under a JOIN was a legacy plan detail; both exact half-open intervals matter.
    expected_bounds = (
        ("MAKE_TIMESTAMP(2026, 8, 1, 0, 0, 0.0)", "MAKE_TIMESTAMP(2026, 9, 1, 0, 0, 0.0)"),
        ("MAKE_TIMESTAMP(2026, 9, 1, 0, 0, 0.0)", "MAKE_TIMESTAMP(2026, 10, 1, 0, 0, 0.0)"),
    )
    for branch, (start, end) in zip(bucket_branches, expected_bounds, strict=True):
        lower, upper = branch.this.find(sge.GTE), branch.this.find(sge.LT)
        assert lower is not None and upper is not None
        assert isinstance(lower.this, sge.Column) and lower.this == upper.this
        assert lower.expression.sql(dialect="duckdb") == start
        assert upper.expression.sql(dialect="duckdb") == end
    for node in ast.find_all(sge.Case):
        if any(branch in bucket_branches for branch in node.args["ifs"]):
            default = node.args.get("default")
            assert (
                default is None
                or isinstance(default, (sge.Null, sge.Case))
                or (isinstance(default, sge.Cast) and isinstance(default.this, sge.Null))
            )
    assert optimized.coord_0.tolist() == [0, 1] * 4
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
    grid = case.session.time_scope(start="2026-08-15", end="2026-10-01").by(mv.grain("month"))
    metric = case.session.catalog.metrics.get("sales.revenue").window(mv.all_history())

    def execute() -> pd.DataFrame:
        query = case.session.observe({"value": metric}, during=grid)
        return query.compute().to_pandas()

    optimized = execute()
    assert optimized[("value", "value")].tolist() == [1077, 1176]
    with monkeypatch.context() as baseline:
        baseline.setattr(source_time, "encoded_time_predicate", _no_inverse)
        previous = execute()
    pd.testing.assert_frame_equal(optimized, previous)
    scalar = (
        case.session.observe(
            {"value": metric},
            during=case.session.time_scope(start="2026-09-30", end="2026-10-01"),
        )
        .compute()
        .to_pandas()
    )
    assert scalar[("value", "value")].tolist() == [1176]


def test_event_column_on_relationship_destination_keeps_its_own_range(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    population = case.session.members(ms.ref.entity("sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": case.session.catalog.metrics.get("sales.line_revenue")},
                during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
                by=population,
            )
            .compute()
            .to_pandas()
            .pipe(_member_frame)
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
            case.session.observe(
                {"value": case.session.catalog.metrics.get("sales.revenue")},
                during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
                by=population,
            )
            .compute()
            .to_pandas()
            .pipe(_member_frame)
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = execute()
    assert optimized.set_index("member").value.dropna().to_dict() == {
        "A": 450,
        "B": 150,
        "C": 400,
        "D": 0,
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
        case.session.observe(
            {"value": case.session.catalog.metrics.get("sales.revenue")},
            during=case.session.time_scope(start="2026-08-01", end="2026-09-01"),
            by=population,
        ).compute()

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
    assert first.run_ref is second.run_ref is None
    assert case.session.runs().items == ()
