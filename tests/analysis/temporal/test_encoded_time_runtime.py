"""Public source consumers preserve results and reads under encoded filtering."""

import operator
from dataclasses import replace
from datetime import datetime

import duckdb
import pandas as pd
import pytest
import sqlglot
from sqlglot import expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource import engines
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.datasource.engines.base import PartitionProbeRequest, PartitionProbeResult
from tests.shared_fixtures import DslCase, DslCaseFactory, frozen_coordinates, terminal_runs
from tests.support.source_trace import SourceTrace


def _entity(catalog: ms.SemanticCatalog, path: str) -> ms.Entity:
    result = catalog.get(path)
    assert result.kind == "entity"
    return result


def _metric(catalog: ms.SemanticCatalog, path: str) -> ms.Metric:
    result = catalog.get(path)
    assert result.kind == "metric"
    return result


def _time_dimension(catalog: ms.SemanticCatalog, path: str) -> ms.TimeDimension:
    result = catalog.get(path)
    assert result.kind == "time_dimension"
    return result


def _member_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"value": frame["value"], "member": frame["customer"]})


def _encode_orders(case: DslCase, fmt: str, *, integer: bool = False, zone: str = "UTC") -> None:
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
    parse = f"ms.strptime({fmt!r}" + (f", timezone={zone!r})" if "%H" in fmt else ")")
    path.write_text(path.read_text().replace("ms.timestamp()", parse))
    ms.load(workspace_dir=case.root)


def _forbid_partition_probe(request: PartitionProbeRequest) -> PartitionProbeResult:
    raise AssertionError("Encoded filtering must not inspect partition values")


def test_explicit_boundaries_filter_the_original_encoded_column(
    analysis_dsl_case_factory: DslCaseFactory, source_trace: SourceTrace
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d")
    point = _time_dimension(
        case.session.catalog, f"time_dimension:sales.{case.names.order}.{case.names.ordered_at}"
    )
    scope = case.session.time_scope(start="2026-08-01", end="2026-09-01")
    result = case.session.observe(
        {"value": _metric(case.session.catalog, "metric:sales.revenue")},
        by=(),
        where=((point >= scope.start) & (point < scope.end)),
    ).compute()
    assert result.to_pandas()["value"].tolist() == [1000]
    sql = next(sql for sql in source_trace.native_sql if "ordered_at" in sql and "WHERE" in sql)
    ast = sqlglot.parse_one(sql, read="duckdb")
    for where in ast.find_all(sge.Where):
        assert "TRY_STRPTIME" not in where.sql(dialect="duckdb")
    assert any(
        isinstance(item.this, sge.Column)
        and isinstance(item.expression, sge.Literal)
        and item.expression.this == "20260801"
        for item in ast.find_all(sge.GTE)
    ), sql


@pytest.mark.parametrize("integer", [False, True])
@pytest.mark.parametrize("mode", ["or", "not", "isnull"])
def test_encoded_explicit_comparisons_preserve_boolean_nulls(
    analysis_dsl_case_factory: DslCaseFactory,
    source_trace: SourceTrace,
    integer: bool,
    mode: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d", integer=integer)
    table = case.names.order
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'UPDATE "{table}" SET ordered_at=NULL WHERE order_id=?', ["j1_july"])
        parsed = "try_strptime(CAST(ordered_at AS VARCHAR), '%Y%m%d')"
        august = f"({parsed} >= TIMESTAMP '2026-08-01' AND {parsed} < TIMESTAMP '2026-09-01')"
        native = {
            "or": f"({august} OR {parsed} IS NULL)",
            "not": f"NOT {august}",
            "isnull": f"({august}) IS NULL",
        }[mode]
        expected = connection.execute(f'SELECT count(*) FROM "{table}" WHERE {native}').fetchone()
    assert expected is not None
    session = case.session
    point = _time_dimension(
        session.catalog, f"time_dimension:sales.{table}.{case.names.ordered_at}"
    )
    entity = _entity(session.catalog, f"entity:sales.{table}")
    interval = (point >= "2026-08-01") & (point < "2026-09-01")
    condition = {"or": interval | point.isnull(), "not": ~interval, "isnull": interval.isnull()}[
        mode
    ]
    result = session.observe({"count": entity.count(time=point)}, where=condition).compute()
    assert result.to_pandas()["count"].tolist() == [expected[0]]
    sql = next(sql for sql in source_trace.native_sql if "ordered_at" in sql and "WHERE" in sql)
    where = next(sqlglot.parse_one(sql, read="duckdb").find_all(sge.Where))
    assert any(
        isinstance(node.this, sge.Column) and node.this.name == "ordered_at"
        for node in where.find_all(sge.GTE, sge.LT)
    ), sql


@pytest.mark.parametrize("integer", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "compare,sql_operator",
    [
        (operator.eq, "="),
        (operator.ne, "<>"),
        (operator.lt, "<"),
        (operator.le, "<="),
        (operator.gt, ">"),
        (operator.ge, ">="),
    ],
)
def test_encoded_comparisons_preserve_unaligned_instants_and_reversed_operands(
    analysis_dsl_case_factory: DslCaseFactory,
    integer: bool,
    reverse: bool,
    compare: object,
    sql_operator: str,
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d", integer=integer)
    table = case.names.order
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'UPDATE "{table}" SET ordered_at=NULL WHERE order_id=?', ["j1_july"])
        parsed = "try_strptime(CAST(ordered_at AS VARCHAR), '%Y%m%d')"
        expression = f"? {sql_operator} {parsed}" if reverse else f"{parsed} {sql_operator} ?"
        expected = connection.execute(
            f'SELECT count(*) FROM "{table}" WHERE {expression}',
            [datetime.fromisoformat("2026-08-01T12:00:00")],
        ).fetchone()
    assert expected is not None
    session = case.session
    point = _time_dimension(
        session.catalog, f"time_dimension:sales.{table}.{case.names.ordered_at}"
    )
    entity = _entity(session.catalog, f"entity:sales.{table}")
    anchor = session.instant("2026-08-01T12:00:00Z")
    assert callable(compare)
    condition = compare(anchor, point) if reverse else compare(point, anchor)
    result = session.observe({"count": entity.count(time=point)}, where=condition).compute()
    assert result.to_pandas()["count"].tolist() == [expected[0]]


def test_unqualified_encoded_time_keeps_native_parser_near_dst(
    analysis_dsl_case_factory: DslCaseFactory, source_trace: SourceTrace
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d%H%M%S", zone="America/New_York")
    table = case.names.order
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute(f'UPDATE "{table}" SET ordered_at=?', ["20261101010000"])
        connection.execute(
            f'UPDATE "{table}" SET ordered_at=? WHERE order_id=?', ["20261101020000", "j1_july"]
        )
        parsed = "timezone('America/New_York', try_strptime(ordered_at, '%Y%m%d%H%M%S'))"
        expected = connection.execute(
            f'SELECT count(*) FROM "{table}" WHERE {parsed} >= ?',
            [datetime.fromisoformat("2026-11-01T05:30:00+00:00")],
        ).fetchone()
    assert expected is not None
    session = case.session
    point = _time_dimension(
        session.catalog, f"time_dimension:sales.{table}.{case.names.ordered_at}"
    )
    entity = _entity(session.catalog, f"entity:sales.{table}")
    start = session.instant("2026-11-01T05:30:00Z")
    result = session.observe({"count": entity.count(time=point)}, where=point >= start).compute()
    assert result.to_pandas()["count"].tolist() == [expected[0]]
    sql = next(sql for sql in source_trace.native_sql if "ordered_at" in sql and "WHERE" in sql)
    ast = sqlglot.parse_one(sql, read="duckdb")
    assert "TRY_STRPTIME" in ast.sql(dialect="duckdb"), sql
    where = next(ast.find_all(sge.Where))
    assert not any(
        isinstance(node.this, sge.Column) and node.this.name == "ordered_at"
        for node in where.find_all(sge.GTE, sge.LT)
    ), sql


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
    population = case.session.members(_entity(case.session.catalog, "entity:sales.customer"))

    def observe(start: str, end: str) -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": _metric(case.session.catalog, "metric:sales.revenue")},
                by=mv.product(case.session.time_scope(start=start, end=end), population),
            )
            .compute()
            .to_pandas(time_details=True)
            .pipe(_member_frame)
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = observe("2026-08-01", "2026-09-01")
    assert optimized.member.tolist() == ["A", "B", "C", "D"]
    assert optimized.value.iloc[:3].tolist() == [450, 150, 400]
    assert optimized.value.iloc[3] == 0
    stages = [sql for purpose, sql in reads if purpose.startswith("query.")]
    assert len(stages) == 2, reads  # The Metric and complete domain have distinct sources.
    business = [sql for sql in stages if "ordered_at" in sql]
    assert len(business) == 1
    ast = sqlglot.parse_one(business[0], read="duckdb")
    assert "TRY_STRPTIME" in ast.sql(dialect="duckdb")
    assert any(
        isinstance(comparison.this, sge.Column)
        and isinstance(comparison.expression, sge.Literal)
        and comparison.expression.this in {"20260801", "20260901"}
        for where in ast.find_all(sge.Where)
        for comparison in where.find_all(sge.GTE, sge.LT)
    )
    september = observe("2026-09-01", "2026-10-01")
    assert september.member.tolist() == ["A", "B", "C", "D"]
    assert september.value.iloc[0] == 99
    assert september.value.iloc[1:].eq(0).all()
    # When the member source is the event source, the window still restricts
    # contributions only; identities before/after the window remain present.
    orders = case.session.members(_entity(case.session.catalog, "entity:sales.order"))
    direct = (
        case.session.observe(
            {"value": _metric(case.session.catalog, "metric:sales.revenue")},
            by=mv.product(case.session.time_scope(start="2026-08-01", end="2026-09-01"), orders),
        )
        .compute()
        .to_pandas(time_details=True)
    )
    assert len(direct) == 5
    assert direct["value"].ne(0).sum() == 3


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
    population = case.session.members(_entity(case.session.catalog, "entity:sales.customer"))

    def execute() -> pd.DataFrame:
        frame = case.session.observe(
            {"value": _metric(case.session.catalog, "metric:sales.revenue")},
            by=mv.product(grid, population),
        ).compute()
        rows = frame.to_pandas(time_details=True)
        # Check producer order before the separate membership normalization oracle.
        assert rows["calendar:month.label"].tolist() == ["2026-08-01"] * 4 + ["2026-09-01"] * 4
        assert rows["customer"].tolist() == ["A", "B", "C", "D"] * 2
        directory = frozen_coordinates(frame)[0].rows
        assert [stamp.isoformat() for stamp in directory.iloc[:, 0]] == [
            "2026-08-01T00:00:00+00:00",
            "2026-09-01T00:00:00+00:00",
        ]
        assert directory["selection"].tolist() == ["full", "full"]
        assert not {"nominal_start", "actual_end"} & set(directory.columns)
        return (
            _member_frame(rows)
            .assign(coord_0=rows["calendar:month.label"])
            .sort_values(["member", "coord_0"])
            .reset_index(drop=True)
        )

    optimized = execute()
    assert len(optimized) == 8
    assert optimized.value.tolist() == [450, 99, 150, 0, 400, 0, 0, 0]
    sql = next(sql for purpose, sql in reads if purpose.startswith("query."))
    ast = sqlglot.parse_one(sql, read="duckdb")
    assert "TRY_STRPTIME" in ast.sql(dialect="duckdb")
    # Native calendar assignment does not require a calendar directory JOIN.
    assert not list(ast.find_all(sge.Join))
    assert all(len(node.args["ifs"]) <= 1 for node in ast.find_all(sge.Case))
    assert optimized.coord_0.tolist() == ["2026-08-01", "2026-09-01"] * 4


def test_cumulative_grid_and_endpoint_match_original_temporal_filter(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    scope = case.session.time_scope(start="2026-08-15", end="2026-10-01")
    metric = _metric(case.session.catalog, "metric:sales.revenue").window(mv.all_history())

    def execute() -> pd.DataFrame:
        query = case.session.observe({"value": metric}, by=(scope.by(mv.grain("month")),))
        return query.compute().to_pandas(time_details=True)

    optimized = execute()
    assert optimized["value"].tolist() == [1077, 1176]
    scalar = (
        case.session.observe(
            {"value": metric}, where=case.session.time_scope(start="2026-09-30", end="2026-10-01")
        )
        .compute()
        .to_pandas(time_details=True)
    )
    assert scalar["value"].tolist() == [1176]


def test_event_column_on_relationship_destination_keeps_its_own_range(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3")
    _encode_orders(case, "%Y%m%d%H%M%S", integer=True)
    population = case.session.members(_entity(case.session.catalog, "entity:sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": _metric(case.session.catalog, "metric:sales.line_revenue")},
                by=mv.product(
                    case.session.time_scope(start="2026-08-01", end="2026-09-01"), population
                ),
            )
            .compute()
            .to_pandas(time_details=True)
            .pipe(_member_frame)
            .sort_values("member")
            .reset_index(drop=True)
        )

    optimized = execute()
    assert optimized.value.tolist() == [100, 60]


def test_outside_window_relationship_mismatch_preserves_observation_scope(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    _encode_orders(case, "%Y%m%d")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("UPDATE \"order\" SET customer_id='missing' WHERE order_id='j1_july'")
    population = case.session.members(_entity(case.session.catalog, "entity:sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": _metric(case.session.catalog, "metric:sales.revenue")},
                by=mv.product(
                    case.session.time_scope(start="2026-08-01", end="2026-09-01"), population
                ),
            )
            .compute()
            .to_pandas(time_details=True)
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


def test_encoded_and_parsed_time_wide_sums_preserve_values_and_run_ownership(
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
    population = case.session.members(_entity(case.session.catalog, "entity:sales.customer"))

    def execute() -> pd.DataFrame:
        return (
            case.session.observe(
                {"value": _metric(case.session.catalog, "metric:sales.revenue")},
                by=mv.product(
                    case.session.time_scope(start="2026-08-01", end="2026-09-01"), population
                ),
            )
            .compute()
            .to_pandas(time_details=True)
        )

    # Safe parsing retains native Ibis's int64 transport boundary.
    from marivo.analysis.errors import AnalysisError

    with pytest.raises(AnalysisError, match="Integer value out of bounds"):
        execute()
    assert terminal_runs(case.session) == ()
