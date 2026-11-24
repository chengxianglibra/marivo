"""Public ordinary-key demand and its native GROUP BY boundary."""

from collections.abc import Iterable
from contextlib import ExitStack
from datetime import datetime, timezone

import duckdb
import pandas as pd
import pytest
from sqlglot import exp, parse_one

import marivo.analysis as mv
from marivo._definition.relations import LiteralPopulation
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.query_stages import LocalCompute, RuleDecision
from marivo.analysis.core.frame_data import CompletedFrameData
from marivo.analysis.materialization.frame_evaluation import evaluate_frame
from marivo.analysis.materialization.frame_inputs import capture_frame_inputs
from marivo.analysis.materialization.frame_source_preparation import prepare_frame_sources
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.semantic.errors import SemanticExpressionError
from tests.analysis.materialization.test_query_optimization import (
    optimization_session as optimization_session,
)
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import capture_source

_ON = OptimizationPolicy.default()
_OFF = _ON.with_rule("L7_key_pushdown", enabled=False)


def _decisions(
    query: mv.AnalysisFrame, *, policy: OptimizationPolicy = _ON
) -> tuple[RuleDecision, ...]:
    with ExitStack() as stack:
        plans = prepare_frame_sources(capture_frame_inputs(query), stack, policy=policy).plans
    return tuple(
        decision
        for source in plans.values()
        for decision in source.lowered.plan.optimization_decisions
        if decision.rule == "L7_key_pushdown"
    )


def _before_group(sqls: Iterable[str]) -> tuple[exp.Where, ...]:
    predicates: list[exp.Where] = []
    for sql in sqls:
        statement = parse_one(sql, read="duckdb")
        for group in statement.find_all(exp.Group):
            if isinstance(group.parent, exp.Select):
                predicates.extend(group.parent.find_all(exp.Where))
    return tuple(predicates)


@pytest.mark.parametrize("operation", ("where", "fix", "fix_null", "where_null", "where_value"))
def test_pending_ordinary_where_and_fix_reach_the_original_grouping_input(
    optimization_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    detail = catalog_value(session.catalog, "sales.orders.detail", kind="dimension")
    source = session.observe({"revenue": amount.min()}, by=(region, detail))
    coordinate = source.coord(source.axes[0])
    expected: list[tuple[object, ...]]
    if operation == "where":
        query = source.where(coordinate == "A")
        expected = [("A", "x", 10), ("A", "y", 30)]
    elif operation == "where_value":
        query = source.where((coordinate == "A") & (source["revenue"] > 15))
        expected = [("A", "y", 30)]
    elif operation == "fix":
        query = source.fix(source.axes[0], "A")
        expected = [("x", 10), ("y", 30)]
    elif operation == "fix_null":
        query = source.fix(source.axes[0], None)
        expected = [("z", None)]
    else:
        query = source.where(coordinate.is_null())
        expected = [(None, "z", None)]

    trace = capture_source(monkeypatch)
    # Isolate the ordinary-key pushdown from the independent post-group terminal Fix.
    baseline = _OFF.with_rule("frame_terminal_fix", enabled=False)
    ordinary = compute_with_policy(query, baseline)
    assert not _before_group(trace.native_sql)
    assert trace.returned_rows == [4]
    trace.native_sql.clear()
    trace.returned_rows.clear()
    result = query.compute()
    projected = result.to_pandas()
    pd.testing.assert_frame_equal(projected, ordinary.to_pandas())
    rows = [
        tuple(None if pd.isna(cell) else cell for cell in row)
        for row in projected.itertuples(index=False, name=None)
    ]
    assert rows == expected
    assert len(trace.native_sql) == 1
    assert trace.returned_rows == [2 if operation == "where_value" else len(expected)]
    assert _before_group(trace.native_sql)
    assert any(decision.status == "adopted" for decision in _decisions(query))


def test_strict_failure_in_excluded_region_retains_its_full_group_domain(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    # The fixture writes its seed data before the governed read opens it.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("UPDATE orders SET amount = NULL WHERE region = 'B'")
        connection.execute("UPDATE orders SET amount = 1 WHERE region IS NULL")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    source = session.observe({"revenue": amount.min(nulls="error")}, by=(region,))
    query = source.where(source.coord(source.axes[0]) == "A")
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert isinstance(caught.value.__cause__, SemanticExpressionError)
        assert caught.value.__cause__.expected == "non-NA source values"
        assert "B" in (caught.value.__cause__.received or "")
    assert any(decision.status == "refused" for decision in _decisions(query))


def test_explicit_output_population_cannot_hide_unselected_coverage_failure(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    domain = mv.coordinates(by=(region,), keys=(("A",), (None,)))
    source = session.observe({"revenue": amount.sum()}, by=domain)
    query = source.where(source.coord(source.axes[0]) == "A")
    assert any(decision.status == "refused" for decision in _decisions(query))
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert "coverage" in caught.value.stage or "cover" in (caught.value.expected or "")
        assert "B" in (caught.value.received or "")


def test_calendar_predicate_retains_window_contributions_before_selected_endpoint(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    clock = catalog_value(session.catalog, "sales.orders.ordered_at", kind="time_dimension")
    reporting = session.time_scope(start="2026-01-01", end="2026-01-03")
    metric = amount.sum(time=clock).window(mv.trailing_periods(count=2))
    source = session.observe({"revenue": metric}, by=(reporting.by(mv.grain("day")),))
    second = datetime(2026, 1, 2, tzinfo=timezone.utc)
    query = source.where(source.coord(source.axes[0]) >= second)
    for policy in (_ON, _OFF):
        rows = compute_with_policy(query, policy).to_pandas()
        assert rows["revenue"].tolist() == [60]
        assert rows.iloc[:, 0].tolist() == [second]
    assert any(decision.status == "refused" for decision in _decisions(query))


def test_native_nocase_grouping_is_not_split_by_byte_filter(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    # Native DDL is required to supply real inherited collation metadata.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders RENAME TO initial_orders")
        connection.execute(
            "CREATE TABLE orders("
            "id BIGINT, region VARCHAR COLLATE NOCASE, amount BIGINT, detail VARCHAR, "
            "exact DECIMAL(38,2), approximate DOUBLE, ordered_at DATE)"
        )
        connection.execute("INSERT INTO orders SELECT * FROM initial_orders WHERE id <= 2")
        connection.execute("UPDATE orders SET region = 'a' WHERE id = 2")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    source = session.observe({"revenue": amount.count()}, by=(region,))
    trace = capture_source(monkeypatch)
    grouped = source.compute().to_pandas()
    assert grouped["revenue"].tolist() == [2]
    representative = grouped.iloc[0, 0]
    assert isinstance(representative, str)
    selected = source.fix(source.axes[0], representative)
    trace.native_sql.clear()
    assert selected.compute().to_pandas()["revenue"].tolist() == [2]
    assert not _before_group(trace.native_sql)
    assert any(d.status == "refused" and "collation" in d.reason for d in _decisions(selected))


def test_protected_source_checkpoint_is_the_complete_original_domain(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    source = session.observe({"revenue": amount.sum()}, by=(region,))
    query = source.fix(source.axes[0], "A")
    (identifier,) = capture_frame_inputs(query).source_frames
    checkpoints: dict[str, CompletedFrameData] = {}
    trace = capture_source(monkeypatch)
    result = evaluate_frame(query, declaration_keys=checkpoints)
    assert result.data.copy_rows().iloc[:, 0].tolist() == [40]
    assert not _before_group(trace.native_sql)
    source_keys = checkpoints[identifier].copy_rows().iloc[:, 0]
    assert len(source_keys) == 3 and source_keys.iloc[:2].tolist() == ["A", "B"]
    assert pd.isna(source_keys.iloc[2])


def test_unselected_nonnullable_identity_failure_stays_in_source_domain(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    # This fixture invalidates a declared identity in an unselected key group.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("UPDATE orders SET id = NULL WHERE region = 'B'")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    source = session.observe({"revenue": amount.sum()}, by=(mv.identity(entity), region))
    query = source.where(source.coord(source.axes[1]) == "A")
    assert any(decision.status == "refused" for decision in _decisions(query))
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert "nonnullable" in (caught.value.expected or "")


def test_fallible_nonkey_conjunct_still_evaluates_unselected_region(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    # Seed a region whose raw amount is valid but whose local +1 overflows.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("UPDATE orders SET amount = ? WHERE region = 'B'", [2**63 - 1])
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    source = session.observe({"revenue": amount.min()}, by=(region,))
    query = source.where((source.coord(source.axes[0]) == "A") & (source["revenue"] + 1 > 0))
    assert any(decision.status == "refused" for decision in _decisions(query))
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert caught.value.stage == "numeric_value"
        assert (caught.value.received or "").startswith("add:")


def test_total_integer_and_string_key_conjunction_preserves_nulls_and_order(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    # Recreate a real, nonnullable identity column to discharge the full-domain key check.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders ALTER COLUMN id SET NOT NULL")
    source = session.observe({"revenue": amount.sum()}, by=(mv.identity(entity), region))
    query = source.where((source.coord(source.axes[0]) > 0) & (source.coord(source.axes[1]) != "B"))
    trace = capture_source(monkeypatch)
    rows = query.compute().to_pandas()
    assert rows["revenue"].tolist() == [10, 30]
    assert any(decision.status == "adopted" for decision in _decisions(query))
    assert _before_group(trace.native_sql)


@pytest.mark.parametrize(
    "field, injected, failure_stage",
    (
        ("amount", "9223372036854775807", "source_conversion"),
        (
            "exact",
            "'900000000000000000000000000000000000.00'::DECIMAL(38,2)",
            "source_execution",
        ),
    ),
)
def test_excluded_group_retains_native_sum_failure(
    optimization_session: mv.Session, field: str, injected: str, failure_stage: str
) -> None:
    session = optimization_session
    # Only the excluded group overflows its selected numeric carrier.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute(f"UPDATE orders SET {field} = {injected} WHERE region = 'B'")
        connection.execute(
            "INSERT INTO orders SELECT 708, region, amount, detail, exact, approximate, ordered_at "
            "FROM orders WHERE region = 'B' LIMIT 1"
        )
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    measure = catalog_value(session.catalog, f"sales.orders.{field}", kind="measure")
    observed = session.observe({"v": measure.sum()}, by=(region,))
    selected = observed.where(observed.coord(observed.axes[0]) == "A")
    assert any(d.status == "refused" and "SUM" in d.reason for d in _decisions(selected))
    failures = []
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(selected, policy)
        assert caught.value.stage == failure_stage
        failures.append(caught.value.received)
    assert failures[0] == failures[1]


def test_excluded_group_in_view_retains_native_conversion_failure(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    # The source is still declared as a TableSourceIR but binds a computed view.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders RENAME TO initial_orders")
        connection.execute(
            "CREATE VIEW orders AS SELECT * REPLACE ("
            "CASE WHEN region = 'B' THEN CAST('bad'||id AS BIGINT) ELSE amount END AS amount"
            ") FROM initial_orders"
        )
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    measure = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    observed = session.observe({"v": measure.min()}, by=(region,))
    selected = observed.where(observed.coord(observed.axes[0]) == "A")
    assert any(d.status == "refused" and "stored" in d.reason for d in _decisions(selected))
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(selected, policy)
        assert caught.value.stage == "source_execution"
        assert "bad3" in (caught.value.received or "")


def test_checked_literal_population_crops_output_keys_and_source_groups_together(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    detail = catalog_value(session.catalog, "sales.orders.detail", kind="dimension")
    complete = (("A", "x"), ("A", "y"), (None, "z"), ("B", "x"), ("A", "empty"))
    domain = mv.coordinates(by=(region, detail), keys=complete)
    observed = session.observe({"revenue": amount.min()}, by=domain)
    selected = observed.where(observed.coord(observed.axes[0]) == "A")

    trace = capture_source(monkeypatch)
    baseline = compute_with_policy(selected, _OFF)
    assert trace.returned_rows == [4]
    trace.native_sql.clear()
    trace.returned_rows.clear()
    actual = selected.compute()
    pd.testing.assert_frame_equal(actual.to_pandas(), baseline.to_pandas())
    rows = [
        tuple(None if pd.isna(value) else value for value in row)
        for row in actual.to_pandas().itertuples(index=False, name=None)
    ]
    assert rows == [("A", "empty", None), ("A", "x", 10), ("A", "y", 30)]
    assert trace.returned_rows == [2]
    assert len(trace.native_sql) == 1
    assert _before_group(trace.native_sql)
    assert any(d.status == "adopted" for d in _decisions(selected))
    with ExitStack() as stack:
        prepared = prepare_frame_sources(capture_frame_inputs(selected), stack).plans
    (program,) = (
        stage.program
        for plan in prepared.values()
        for stage in plan.lowered.plan.stages
        if isinstance(stage, LocalCompute) and isinstance(stage.program, FormulaProgram)
    )
    assert program.population_checked and isinstance(program.output_population, LiteralPopulation)
    assert program.output_population.keys == (("A", "empty"), ("A", "x"), ("A", "y"))


def test_checked_literal_population_keeps_unselected_composite_coverage_witness(
    optimization_session: mv.Session,
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    detail = catalog_value(session.catalog, "sales.orders.detail", kind="dimension")
    domain = mv.coordinates(by=(region, detail), keys=(("A", "x"), ("A", "y"), (None, "z")))
    source = session.observe({"value": amount.min()}, by=domain)
    query = source.fix(source.axes[0], "A")
    assert any(d.status == "adopted" for d in _decisions(query))

    witnessed = []
    for policy in (_ON, _OFF):
        with pytest.raises(MaterializationError) as caught:
            compute_with_policy(query, policy)
        assert caught.value.stage == "population_coverage"
        received = caught.value.received or ""
        assert "{'key.0': 'B', 'key.1': 'x'}" in received
        witnessed.append(received)
    assert witnessed[0] == witnessed[1]


@pytest.mark.parametrize("broadcast", (False, True))
@pytest.mark.parametrize("select_endpoints", (False, True))
def test_ordinary_window_key_crops_every_branch_and_keeps_historical_rows(
    optimization_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    broadcast: bool,
    select_endpoints: bool,
) -> None:
    session = optimization_session
    # The prior December date contributes to the first displayed endpoint.
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("INSERT INTO orders VALUES (17, 'A', 5, 'k', 5, 5, DATE '2025-12-31')")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    clock = catalog_value(session.catalog, "sales.orders.ordered_at", kind="time_dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-05").by(mv.grain("day"))
    metrics = {"rolling": amount.min(time=clock).window(mv.trailing_periods(count=2))}
    if broadcast:
        metrics["independent"] = amount.min(time=None)
    observed = session.observe(metrics, by=(region, calendar))
    predicate = observed.coord(observed.axes[0]) == "A"
    if select_endpoints:
        predicate = predicate & (
            observed.coord(observed.axes[1]) >= datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
    query = observed.where(predicate)

    trace = capture_source(monkeypatch)
    baseline = compute_with_policy(query, _OFF)
    ordinary_rows = sum(trace.returned_rows)
    trace.native_sql.clear()
    trace.returned_rows.clear()
    actual = query.compute()
    pd.testing.assert_frame_equal(actual.to_pandas(), baseline.to_pandas())
    df = actual.to_pandas()
    observed_amounts = [None if pd.isna(value) else value for value in df["rolling"]]
    assert observed_amounts == ([10, 30] if select_endpoints else [5, 10, 30])
    assert df.iloc[:, 0].tolist() == ["A"] * len(observed_amounts)
    if broadcast:
        assert df["independent"].tolist() == [5] * len(observed_amounts)
    assert sum(trace.returned_rows) < ordinary_rows
    calendar_reads = [sql for sql in trace.native_sql if '"native_key" AS "key"' in sql]
    grouped_reads = [sql for sql in trace.native_sql if "GROUP BY" in sql]
    assert len(calendar_reads) == 1
    assert len(grouped_reads) == (5 if broadcast else 4)
    assert all("ENCODE('A')" in sql for sql in grouped_reads)
    assert any(d.status == "adopted" for d in _decisions(query))


def test_ordinary_date_day_key_crops_clocked_and_independent_native_groups(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    clock = catalog_value(session.catalog, "sales.orders.ordered_at", kind="time_dimension")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-04").by(mv.grain("day"))
    source = session.observe(
        {"clocked": amount.min(time=clock), "independent": amount.min(time=None)},
        by=(region, calendar),
    )
    query = source.fix(source.axes[0], "A")

    trace = capture_source(monkeypatch)
    control = compute_with_policy(query, _OFF).to_pandas()
    assert trace.returned_rows == [3, 4, 3]
    trace.returned_rows.clear()
    trace.native_sql.clear()
    result = query.compute().to_pandas()
    pd.testing.assert_frame_equal(control, result)
    assert result["clocked"].tolist() == [10, 30]
    assert result["independent"].tolist() == [10, 10]
    assert trace.returned_rows == [3, 2, 1]
    grouped_reads = [sql for sql in trace.native_sql if "GROUP BY" in sql]
    assert len(grouped_reads) == 2
    assert all("ENCODE('A')" in sql for sql in grouped_reads)
    assert any(d.status == "adopted" for d in _decisions(query))


@pytest.mark.parametrize("reducer", ("min", "max", "sum"))
@pytest.mark.parametrize("ambiguous", (False, True), ids=("bigint", "hugeint"))
def test_reduction_does_not_hide_physical_carrier_failure_in_excluded_group(
    optimization_session: mv.Session, reducer: str, ambiguous: bool
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders ALTER COLUMN id SET NOT NULL")
        if ambiguous:
            connection.execute("ALTER TABLE orders ALTER COLUMN amount TYPE HUGEINT")
            connection.execute(
                "UPDATE orders SET amount = 170141183460469231731687303715884105727 "
                "WHERE region = 'B'"
            )
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
    if reducer == "sum":
        source = session.observe({"value": amount.sum()}, by=(mv.identity(entity), region))
        axis = source.axes[1]
    else:
        metric = amount.min() if reducer == "min" else amount.max()
        source = session.observe({"value": metric}, by=(region,))
        axis = source.axes[0]
    query = source.fix(axis, "A")

    if ambiguous:
        for policy in (_ON, _OFF):
            with pytest.raises(MaterializationError) as caught:
                compute_with_policy(query, policy)
            assert caught.value.stage == "source_execution"
            assert "DECIMAL(38,0)" in (caught.value.received or "")
        assert any(d.status == "refused" for d in _decisions(query))
    else:
        assert any(d.status == "adopted" for d in _decisions(query))
        expected = {"min": [10], "max": [30], "sum": [10, 30]}[reducer]
        for policy in (_ON, _OFF):
            assert compute_with_policy(query, policy).to_pandas()["value"].tolist() == expected


@pytest.mark.parametrize("domain", ("identity", "classified", "output-only"))
def test_checked_source_members_select_business_and_output_reads(
    optimization_session: mv.Session, monkeypatch: pytest.MonkeyPatch, domain: str
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:  # noqa: TID251
        connection.execute("ALTER TABLE orders ALTER COLUMN id SET NOT NULL")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
    entity = catalog_value(session.catalog, "sales.orders", kind="entity")
    members = session.members(entity)
    before, after = [4, 4], [1, 1]
    if domain == "classified":
        region = catalog_value(session.catalog, "sales.orders.region", kind="dimension")
        classified = members.classify(by=(region,)).project(by=(region,))
        assert isinstance(classified, mv.Population)
        source = session.observe({"value": amount.min()}, by=classified)
        query = source.fix(source.axes[0], "A")
        before, after = [3, 4, 4, 3], [1, 4, 4, 1]
    else:
        source = session.observe(
            {"value": amount.min()},
            by=members,
            where=amount > 10 if domain == "output-only" else None,
        )
        query = source.fix(source.axes[0], 1)

    trace = capture_source(monkeypatch)
    baseline = compute_with_policy(query, _OFF).to_pandas()
    assert trace.returned_rows == before
    trace.returned_rows.clear()
    trace.native_sql.clear()
    actual = query.compute().to_pandas()
    pd.testing.assert_frame_equal(actual, baseline)
    assert len(actual) == 1
    if domain == "output-only":
        assert actual["value"].isna().all()
    else:
        assert actual["value"].tolist() == [10]
    assert trace.returned_rows == after
    assert any(d.status == "adopted" for d in _decisions(query))
