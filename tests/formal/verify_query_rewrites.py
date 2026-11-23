"""Check admitted compiler rewrites using emitted plans and real execution."""

import operator
from collections.abc import Callable
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import (
    FilterValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
)
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.optimization import optimize_query
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    QueryPlan,
    SourceReadStage,
    checked_output,
)
from marivo.analysis.core.frame_data import ExpressionFrameBacking, SourceFrameBinding
from marivo.analysis.materialization import (
    frame_source_preparation,
    query_key_fusion,
    query_terminal_fusion,
    sample_fusion,
)
from marivo.analysis.materialization.graph_source_execution import execute_numeric_query
from marivo.analysis.materialization.query_sources import open_query_sources
from marivo.analysis.methods.query_values import evaluate_result
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.formal._smt import prove_unsat
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace

_INT = ScalarDataType("int64")
_COLUMNS = (
    DataColumn("key", "key", _INT, False),
    DataColumn("x", "x", _INT, True),
    DataColumn("y", "y", _INT, True),
)
_KEY = ("key",)
_X, _Y = ValueSlot(0), ValueSlot(1)
_ZERO = ValueLiteral(0)


def _selection_plan(first: ValueExpression, second: ValueExpression) -> QueryPlan:
    # Verify the compiler's admitted same-layout plan slice. Public Frame
    # composition has its own completed-input boundaries.
    stages = tuple(
        LocalCompute(
            name,
            (upstream,),
            _COLUMNS,
            _KEY,
            ResultProgram(FilterValues(predicate), _COLUMNS, _KEY, (), (_KEY,), (_KEY,)),
        )
        for name, upstream, predicate in (("first", "input", first), ("second", "first", second))
    )
    return QueryPlan(
        (checked_output("input", _COLUMNS, _KEY), *stages),
        (("input", ("first",)), ("first", ("second",)), ("second", ())),
        "second",
        (_KEY,),
    )


def _integer(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


def _operand(expression: ValueExpression) -> tuple[str, str]:
    if isinstance(expression, ValueSlot) and expression.index in (0, 1):
        name = ("x", "y")[expression.index]
        return name, f"{name}_null"
    if isinstance(expression, ValueLiteral) and type(expression.value) is int:
        return _integer(expression.value), "false"
    raise AssertionError(f"Unmodeled selection operand: {expression!r}")


def _kleene(expression: ValueExpression) -> str:
    """Translate actual plan nodes. -1/0/1 are FALSE/NULL/TRUE."""
    assert isinstance(expression, ValueBinary), expression
    if expression.operation in ("and", "or"):
        left, right = _kleene(expression.left), _kleene(expression.right)
        relation = "<=" if expression.operation == "and" else ">="
        return f"(ite ({relation} {left} {right}) {left} {right})"
    comparisons = {"eq": "=", "ne": "distinct", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
    assert expression.operation in comparisons, expression
    lhs, lhs_null = _operand(expression.left)
    rhs, rhs_null = _operand(expression.right)
    return (
        f"(ite (or {lhs_null} {rhs_null}) 0 "
        f"(ite ({comparisons[expression.operation]} {lhs} {rhs}) 1 (- 1)))"
    )


def _filters(plan: QueryPlan) -> tuple[LocalCompute, ...]:
    # Follow the executable predecessor chain rather than picking an unused node.
    stages = {stage.stage_id: stage for stage in plan.stages}
    stage = stages[plan.publish]
    filters = []
    while isinstance(stage, LocalCompute):
        (parent,) = stage.inputs
        filters.append(stage)
        stage = stages[parent]
    assert stage.stage_id == "input"
    return tuple(reversed(filters))


def _predicate(stage: LocalCompute) -> ValueExpression:
    assert isinstance(stage.program, ResultProgram)
    operation = stage.program.operation
    assert isinstance(operation, FilterValues) and isinstance(operation.predicate, ValueExpression)
    return operation.predicate


@pytest.mark.parametrize(
    "comparison,constant",
    (("ne", True), *((name, False) for name in ("eq", "ne", "lt", "le", "gt", "ge"))),
)
def test_l1_actual_rewrite_retains_the_same_rows_with_nullable_comparisons(
    comparison: Literal["eq", "ne", "lt", "le", "gt", "ge"],
    constant: bool,
) -> None:
    # Independent Python guard below also exercises the real pandas kernel.
    first = ValueBinary("or", ValueBinary("lt", _X, _ZERO), ValueBinary("eq", _Y, _ZERO))
    second = ValueBinary(comparison, _Y, ValueLiteral(-1) if constant else _X)
    original = _selection_plan(first, second)
    optimized = optimize_query(original)
    assert any(group.law == "L1" for group in optimized.fusion_groups)
    original_chain = _filters(original)
    (rewritten,) = _filters(optimized)
    sequential = " ".join(f"(= {_kleene(_predicate(stage))} 1)" for stage in original_chain)
    emitted = _kleene(_predicate(rewritten))
    prove_unsat(
        f"""(set-logic QF_LIA)
(declare-const x Int)
(declare-const y Int)
(declare-const x_null Bool)
(declare-const y_null Bool)
(define-fun sequential () Bool (and {sequential}))
(define-fun fused () Bool (= {emitted} 1))
(assert (distinct sequential fused))""",
        claim=f"L1 emitted filter ({comparison}, constant={constant})",
        witness=("x", "y", "x_null", "y_null", "sequential", "fused"),
    )
    points = (None, -(2**63), -1, 0, 1, 2**63 - 1)
    rows = list(product(points, repeat=2))
    table = pd.DataFrame(
        {
            "key": range(len(rows)),
            "x": pd.Series([x for x, _ in rows], dtype="int64[pyarrow]"),
            "y": pd.Series([y for _, y in rows], dtype="int64[pyarrow]"),
        }
    )
    compare = {
        "eq": operator.eq,
        "ne": operator.ne,
        "lt": operator.lt,
        "le": operator.le,
        "gt": operator.gt,
        "ge": operator.ge,
    }[comparison]
    expected = [
        idx
        for idx, (x, y) in enumerate(rows)
        if ((x is not None and x < 0) or y == 0)
        and y is not None
        and (compare(y, -1) if constant else x is not None and compare(y, x))
    ]
    plain = table
    for stage in original_chain:
        plain = evaluate_result(stage, (plain,))
    fused = evaluate_result(rewritten, (table,))
    assert plain["key"].tolist() == expected
    pd.testing.assert_frame_equal(fused, plain)


def test_l1_keeps_unqualified_arithmetic_on_its_own_boundary() -> None:
    first = ValueBinary("lt", _X, _ZERO)
    unsafe = ValueBinary("gt", ValueBinary("add", _X, _Y), _ZERO)
    optimized = optimize_query(_selection_plan(first, unsafe))
    assert len(_filters(optimized)) == 2
    assert any(
        decision.rule == "L1" and decision.status == "refused"
        for decision in optimized.optimization_decisions
    )


_ROWS: tuple[tuple[int, str | None, int | None, int | None], ...] = (
    (1, "east", 10, 3),
    (2, "east", 10, None),  # Equal contributions must keep their multiplicity.
    (3, None, None, 8),
    (4, "west", 0, 9),
    (5, "west", -4, 9),
)
_DECIMAL_ROWS = (
    (1, "east", 1, Decimal("9007199254740992.01"), None),
    (2, "east", None, Decimal("0.09"), Decimal("2.30")),
    (3, "west", 0, None, Decimal("-1.20")),
    (4, "west", 1, Decimal("4.50"), Decimal("1.05")),
    (5, None, None, Decimal("-0.05"), Decimal("8.00")),
    (6, None, 0, None, Decimal("2.00")),
    (7, "nulls", 1, None, None),
    (8, "other", 2, Decimal("4.00"), Decimal("3.00")),
)


@pytest.fixture
def query_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> mv.Session:
    path = tmp_path / "formal-rewrites.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, extra BIGINT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", _ROWS)
        conn.execute(
            "CREATE TABLE settlements(id BIGINT, region VARCHAR, lane BIGINT, "
            "amount DECIMAL(20,2), extra DECIMAL(20,2))"
        )
        conn.executemany("INSERT INTO settlements VALUES (?, ?, ?, ?, ?)", _DECIMAL_ROWS)
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(path)!r}, wall_clock_timezone='UTC')"
            ),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Test')",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"), primary_key=["id"])
key = ms.dimension_column(name="id", entity=orders, column="id")
region = ms.dimension_column(name="region", entity=orders, column="region")
amount = ms.measure_column(name="amount", entity=orders, column="amount", unit="USD", additivity=ms.additive_all())
extra = ms.measure_column(name="extra", entity=orders, column="extra", unit="USD", additivity=ms.additive_all())
settlements = ms.entity(name="settlements", datasource=ms.ref.datasource("warehouse"),
    source=md.table("settlements"), primary_key=["id"])
sid = ms.dimension_column(name="id", entity=settlements, column="id")
group = ms.dimension_column(name="region", entity=settlements, column="region")
lane = ms.dimension_column(name="lane", entity=settlements, column="lane")
decimal_amount = ms.measure_column(name="amount", entity=settlements, column="amount", unit="USD", additivity=ms.additive_all())
decimal_extra = ms.measure_column(name="extra", entity=settlements, column="extra", unit="USD", additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("formal-rewrites", timezone="UTC")


def _source_graph(query: mv.AnalysisFrame) -> SourceFrameBinding:
    backing = query._backing
    assert isinstance(backing, ExpressionFrameBacking) and isinstance(
        backing.root, SourceFrameBinding
    )
    return backing.root


def _bag_sum(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present or not values else None


def _verify_native_differential(
    session: mv.Session, query: mv.AnalysisFrame, trace: SourceTrace, *, reads: tuple[int, int]
) -> None:
    graph = _source_graph(query).graph
    with (
        session._runtime.connection_service().operation() as connections,
        open_query_sources(connections, graph, session.catalog._state) as prepared,
    ):
        original = plan_query(graph, prepared.schemas)
        optimized = optimize_query(original)
        assert any(group.law == "source_block" for group in optimized.fusion_groups)
        assert (
            sum(isinstance(stage, SourceReadStage) for stage in original.stages),
            sum(isinstance(stage, SourceReadStage) for stage in optimized.stages),
        ) == reads
        start = len(trace.native_sql)
        first = execute_numeric_query(prepared, lower_query(original, prepared.relations))
        middle = len(trace.native_sql)
        second = execute_numeric_query(prepared, lower_query(optimized, prepared.relations))
    assert (middle - start, len(trace.native_sql) - middle) == reads
    assert (
        first.keys == second.keys
        and first.layout == second.layout
        and first.columns == second.columns
    )
    pd.testing.assert_frame_equal(first.table, second.table)


@pytest.mark.parametrize(
    "grouped, selected",
    ((False, (1, 2, 3, 4, 5)), (True, (1, 2, 3, 4, 5)), (False, (3,)), (False, (99,))),
)
def test_actual_common_aggregate_preserves_bag_nulls_and_empty_domain(
    query_session: mv.Session,
    source_trace: SourceTrace,
    grouped: bool,
    selected: tuple[int, ...],
) -> None:
    session = query_session
    catalog = session.catalog
    key = catalog_value(catalog, "sales.orders.id", kind="dimension")
    region = catalog_value(catalog, "sales.orders.region", kind="dimension")
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(catalog, "sales.orders.extra", kind="measure")
    query = session.observe(
        {"total": amount.sum(), "extras": extra.sum(), "sample_count": amount.count()},
        by=(region,) if grouped else (),
        where=key.isin(selected),
    )
    _verify_native_differential(session, query, source_trace, reads=(3, 1))

    groups: dict[str | None, list[tuple[int | None, int | None]]] = {} if grouped else {None: []}
    for identifier, category, q, e in _ROWS:
        if identifier in selected:
            groups.setdefault(category if grouped else None, []).append((q, e))
    expected = {
        (
            label,
            _bag_sum([q for q, _ in rows]),
            _bag_sum([e for _, e in rows]),
            sum(q is not None for q, _ in rows),
        )
        for label, rows in groups.items()
    }
    result = query.compute().to_pandas()
    actual = {
        tuple(None if pd.isna(value) else value for value in (row if grouped else (None, *row)))
        for row in result.itertuples(index=False, name=None)
    }
    assert len(result) == len(expected) and actual == expected, (
        selected,
        grouped,
        actual,
        expected,
    )


def test_only_aggregates_on_the_same_contribution_domain_merge(
    query_session: mv.Session, source_trace: SourceTrace
) -> None:
    catalog = query_session.catalog
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(catalog, "sales.orders.extra", kind="measure")
    key = catalog_value(catalog, "sales.orders.id", kind="dimension")
    query = query_session.observe(
        {
            "first": amount.sum().where(key <= 2),
            "other": amount.sum().where(key >= 4),
            "first_extra": extra.sum().where(key <= 2),
        },
    )
    _verify_native_differential(query_session, query, source_trace, reads=(3, 2))
    assert query.compute().to_pandas().iloc[0].tolist() == [20, -4, 3]


def test_projection_and_empty_top_cannot_erase_discarded_strict_check(
    query_session: mv.Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    amount = catalog_value(query_session.catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(query_session.catalog, "sales.orders.extra", kind="measure")
    source = query_session.observe({"strict": amount.sum(nulls="error"), "kept": extra.sum()})
    projected = source.select({"kept": source["kept"]})
    empty = projected.top(0, by=projected["kept"])
    for candidate in (projected, empty):
        with pytest.raises(MaterializationError) as optimized:
            candidate.compute()
        with monkeypatch.context() as patch:
            patch.setattr(frame_source_preparation, "optimize_query", lambda plan: plan)
            with pytest.raises(MaterializationError) as original:
                candidate.compute()
        assert type(optimized.value) is type(original.value)
        assert optimized.value.expected == original.value.expected
        assert optimized.value.stage == original.value.stage == "numeric_value"
        assert "nulls='error'" in (optimized.value.received or ""), optimized.value


def _without_query_rewrites(patch: pytest.MonkeyPatch) -> None:
    # Keep capture, preparation, lowering, source execution and Frame completion.
    patch.setattr(frame_source_preparation, "optimize_query", lambda plan: plan)
    patch.setattr(sample_fusion, "fuse_sample_reductions", lambda prepared, lowered: lowered)
    patch.setattr(query_key_fusion, "fuse_query_keys", lambda prepared, lowered: lowered)
    patch.setattr(query_terminal_fusion, "fuse_query_terminal", lambda prepared, lowered: lowered)


@pytest.mark.parametrize("loose_lane,split,alternate", product((False, True), repeat=3))
def test_full_decimal_query_with_nullable_branch_filters(
    query_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    loose_lane: bool,
    split: bool,
    alternate: bool,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    left_filter = ((lane == 1) | (key <= 2)) if loose_lane else ((lane == 1) & (key > 1))
    right_filter = ((lane != 1) | (key == 5)) if split else left_filter
    left = amount.sum(where=left_filter)
    side = extra.sum(where=left_filter)
    right = extra.sum(where=right_filter)
    choose_left = key.count(where=left_filter) > (1 if alternate else 0)
    observed = session.observe(
        {"left": left, "side": side, "chosen": choose_left.ifelse(left, right)},
        by=(region,),
        where=key < 8,
    )
    filtered = observed.where((observed["chosen"] > 0) | (observed["side"] > 0))
    query = filtered.select({"chosen": filtered["chosen"], "side": filtered["side"]})
    start = len(source_trace.native_sql)
    optimized = query.compute().to_pandas()
    middle = len(source_trace.native_sql)
    with monkeypatch.context() as patch:
        _without_query_rewrites(patch)
        control = query.compute().to_pandas()
    plain_reads = len(source_trace.native_sql) - middle
    optimized_reads = middle - start
    assert 0 < optimized_reads < plain_reads, (optimized_reads, plain_reads)
    pd.testing.assert_frame_equal(optimized, control)

    def subtotal(values: list[Decimal | None]) -> Decimal | None:
        known = [value for value in values if value is not None]
        return sum(known, Decimal(0)) if known or not values else None

    expected = {}
    for label in ("east", "west", "nulls", None):
        domain = [row for row in _DECIMAL_ROWS if row[0] < 8 and row[1] == label]
        lhs = (
            [row for row in domain if row[2] == 1 or row[0] <= 2]
            if loose_lane
            else [row for row in domain if row[2] == 1 and row[0] > 1]
        )
        rhs = [row for row in domain if row[2] in (0, 2) or row[0] == 5] if split else lhs
        # Absent aggregate groups are completed as if the branch saw zero rows.
        side_value = subtotal([row[4] for row in lhs])
        chosen = (
            subtotal([row[3] for row in lhs])
            if len(lhs) > (1 if alternate else 0)
            else subtotal([row[4] for row in rhs])
        )
        if (chosen is not None and chosen > 0) or (side_value is not None and side_value > 0):
            expected[label] = (chosen, side_value)
    actual = {
        None if pd.isna(label) else label: tuple(
            None if pd.isna(value) else value for value in cells
        )
        for label, *cells in optimized.itertuples(index=False, name=None)
    }
    assert len(actual) == len(optimized) and actual == expected


@pytest.mark.parametrize(
    "threshold",
    (float(2**53), Decimal("9007199254740992.0000000000000000000001")),
    ids=("mixed-float", "mixed-scale"),
)
def test_full_decimal_query_preserves_exact_comparison_boundaries(
    query_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    threshold: float | Decimal,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    one = (lane == 1) | (key == 2)
    two = lane.isnull() | (key == 6)
    observed = session.observe(
        {
            "amount": amount.sum(where=one),
            "side": extra.sum(where=one),
            "other": extra.sum(where=two),
        },
        by=(region,),
    )
    query = observed.where(
        (observed["amount"] > threshold) | (observed["amount"].is_null() & (observed["side"] > 0))
    )
    start = len(source_trace.native_sql)
    with monkeypatch.context() as patch:
        _without_query_rewrites(patch)
        plain = query.compute().to_pandas()
    middle = len(source_trace.native_sql)
    optimized = query.compute().to_pandas()
    assert list(plain["region"]) == ["east"]
    assert plain["amount"].tolist() == [Decimal("9007199254740992.10")]
    assert 0 < len(source_trace.native_sql) - middle < middle - start
    pd.testing.assert_frame_equal(optimized, plain)


@pytest.mark.parametrize("supported", (True, False), ids=("exact-comparison", "type-rejection"))
def test_metric_predicate_compares_decimal_values_at_different_scales(
    query_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    supported: bool,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    total = amount.sum(where=(lane == 1) | (key == 2))
    other = extra.sum(where=lane.isnull() | (key == 6))
    threshold = Decimal("9007199254740992" if supported else "9007199254740992.00001")
    query = session.observe({"chosen": (total > threshold).ifelse(total, other)}, by=(region,))
    if not supported:
        before = len(source_trace.native_sql)
        for optimized in (False, True):
            with monkeypatch.context() as patch:
                if not optimized:
                    _without_query_rewrites(patch)
                with pytest.raises(SemanticExpressionError) as rejected:
                    query.compute()
            error = rejected.value
            assert error.expected == "physical types admitted by the numeric recipe"
            assert "decimal(38, 2)" in (error.received or "")
            assert "decimal(21, 5)" in (error.received or "")
            assert error.repair is not None
        assert len(source_trace.native_sql) == before
        return
    with monkeypatch.context() as patch:
        _without_query_rewrites(patch)
        plain = query.compute().to_pandas()
    assert plain.loc[plain["region"] == "east", "chosen"].tolist() == [
        Decimal("9007199254740992.10")
    ]
    pd.testing.assert_frame_equal(query.compute().to_pandas(), plain)


@pytest.mark.parametrize("demand", ("masked", "west-only", "demanded"))
def test_full_decimal_query_keeps_strict_checks_on_their_branch(
    query_session: mv.Session,
    monkeypatch: pytest.MonkeyPatch,
    source_trace: SourceTrace,
    demand: str,
) -> None:
    session = query_session
    name = "sales.settlements."
    key = catalog_value(session.catalog, name + "id", kind="dimension")
    region = catalog_value(session.catalog, name + "region", kind="dimension")
    lane = catalog_value(session.catalog, name + "lane", kind="dimension")
    amount = catalog_value(session.catalog, name + "amount", kind="measure")
    extra = catalog_value(session.catalog, name + "extra", kind="measure")
    left = (lane == 1) | (key == 2)
    right = (lane == 0) | (key == 5)
    strict = extra.sum(where=left, nulls="error")
    peak = extra.max(where=left, nulls="error")
    good = amount.sum(where=left)
    fallback = extra.sum(where=right)
    keys = {"masked": (99,), "west-only": (3,), "demanded": (3, 1)}[demand]
    gate = key.count(where=key.isin(keys)) > 0
    query = session.observe(
        {"chosen": gate.ifelse(strict + peak, fallback), "good": good}, by=(region,)
    )
    if demand != "demanded":
        before = len(source_trace.native_sql)
        with monkeypatch.context() as patch:
            _without_query_rewrites(patch)
            plain = query.compute().to_pandas()
        expected = {
            "east": Decimal(0),
            "west": Decimal("-1.20") if demand == "masked" else Decimal("2.10"),
            "nulls": Decimal(0),
            None: Decimal("10.00"),
        }
        assert {
            None if pd.isna(label) else label: value
            for label, value in plain[["region", "chosen"]].itertuples(index=False, name=None)
        } == expected
        middle = len(source_trace.native_sql)
        pd.testing.assert_frame_equal(query.compute().to_pandas(), plain)
        assert 0 < len(source_trace.native_sql) - middle < middle - before
    else:
        with monkeypatch.context() as patch:
            _without_query_rewrites(patch)
            with pytest.raises(MaterializationError) as plain_error:
                query.compute()
        with pytest.raises(MaterializationError) as optimized_error:
            query.compute()
        for error in (plain_error.value, optimized_error.value):
            assert error.stage == "numeric_value"
            assert "nulls='error'" in (error.received or "")
