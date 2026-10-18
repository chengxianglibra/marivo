"""Optimized Query execution preserves the unoptimized stage/check contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import LiteralValues
from marivo._definition.types import AxisBinding
from marivo.analysis.compiler.graph_lowering import LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    LocalCompute,
    PlannedCheck,
    QueryPlan,
    SourceCheckRoute,
    SourceRead,
    plan_query,
)
from marivo.analysis.compiler.optimization import (
    InvocationMemo,
    compilation_key,
    optimize_query,
    required_for,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import Binding, Fact, Obligation
from marivo.analysis.dsl.query import Query
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_execution import run_query_plan
from marivo.analysis.materialization.graph_local_execution import run_local
from marivo.analysis.materialization.graph_source_execution import (
    execute_numeric_query,
    open_query_sources,
)
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models


@pytest.fixture
def optimization_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "optimization.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, detail VARCHAR, exact DECIMAL(38,2), approximate DOUBLE, ordered_at DATE)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A',10,'x',10.01,10,'2026-01-01'),(2,'A',30,'y',30.02,30,'2026-01-02'),(3,'B',20,'x',20.03,20,'2026-01-01'),(4,NULL,NULL,'z',NULL,NULL,'2026-01-02')"
        )
    other_database = tmp_path / "other.duckdb"
    with duckdb.connect(str(other_database)) as connection:
        connection.execute("CREATE TABLE orders(id BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO orders VALUES (1,600)")
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "datasources/second.py": f"import marivo.datasource as md\nmd.duckdb(name='second', path={str(other_database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/models.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
composite_orders = ms.entity(name='composite_orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id', 'detail'])
region = ms.dimension_column(name='region', entity=orders, column='region')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='CNY')
detail = ms.dimension_column(name='detail', entity=orders, column='detail')
exact = ms.measure_column(name='exact', entity=orders, column='exact', additivity=ms.additive_all(), unit='CNY')
approximate = ms.measure_column(name='approximate', entity=orders, column='approximate', additivity=ms.additive_all(), unit='CNY')
ordered_at = ms.time_dimension_column(name='ordered_at', entity=orders, column='ordered_at', granularity='day', is_default=True)
other = ms.entity(name='other', datasource=ms.ref.datasource('second'), source=md.table('orders'), primary_key=['id'])
other_amount = ms.measure_column(name='amount', entity=other, column='amount', additivity=ms.additive_all(), unit='CNY')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("optimization", report_timezone="UTC")


def _execute(
    session: Session, query: Query, *, optimized: bool
) -> tuple[StageOutput, QueryPlan, tuple[str, ...], dict[str, tuple[str, ...]]]:
    graph = query._graph
    events: list[str] = []
    actual: dict[str, tuple[str, ...]] = {}
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        plan = plan_query(graph, prepared.schemas, context=prepared.context)
        if optimized:
            plan = optimize_query(plan)

        def observe(stage: LocalCompute, output: StageOutput) -> None:
            events.append(stage.stage_id)
            actual[stage.stage_id] = tuple(output.table.columns)

        def observe_read(request: LoweredRead, output: StageOutput) -> None:
            actual[request.stage.stage_id] = tuple(output.table.columns)

        result = execute_numeric_query(
            prepared,
            lower_query(plan, prepared.relations),
            observe_local=observe,
            observe_read=observe_read,
        )
    return result, plan, tuple(events), actual


def _same(
    session: Session, query: Query
) -> tuple[QueryPlan, tuple[str, ...], dict[str, tuple[str, ...]]]:
    baseline, original, _, _ = _execute(session, query, optimized=False)
    result, planned, events, actual = _execute(session, query, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    assert result.keys == baseline.keys
    assert result.columns == baseline.columns
    assert result.layout == baseline.layout
    assert not original.required_slots and not original.fusion_groups
    return planned, events, actual


def _wide(session: Session) -> Query:
    region = session.catalog.fields.get("sales.orders.region")
    return session.values(
        by=(region,),
        columns={"a": "CNY", "b": "CNY", "unused": "CNY"},
        rows=(
            (("A",), {"a": 10, "b": 100, "unused": 7}),
            (("B",), {"a": 20, "b": 200, "unused": 8}),
            ((None,), {"a": 30, "b": 300, "unused": 9}),
        ),
    )


def test_backward_demand_really_prunes_wide_literal_input(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    query = source.select({"kept": source["a"]})
    plan, _, actual = _same(optimization_session, query)
    literal = next(
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and stage.result is not None
        and isinstance(stage.result.operation, LiteralValues)
    )
    assert tuple(column.slot_id for column in literal.columns) == ("key.0", "value.0")
    assert actual[literal.stage_id] == ("key.0", "value.0")
    assert required_for(plan, literal.stage_id).columns == ("key.0", "value.0")


def test_repeated_consumers_union_their_real_column_requirements(
    optimization_session: Session,
) -> None:
    source = _wide(optimization_session)
    left = source.select({"kept": source["a"]})
    right = source.select({"kept": source["b"]})
    query = left.align(right)
    plan, _, actual = _same(optimization_session, query)
    literal = next(
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and stage.result is not None
        and isinstance(stage.result.operation, LiteralValues)
    )
    assert len(plan.consumers_of(literal.stage_id)) == 2
    assert actual[literal.stage_id] == ("key.0", "value.0", "value.1")
    assert "value.2" not in actual[literal.stage_id]
    endpoint = plan.stages[-1]
    assert isinstance(endpoint, LocalCompute) and endpoint.result is not None
    assert endpoint.result.output_layout is not None
    assert endpoint.result.output_layout.presence


def test_source_attributes_keep_complete_keys_and_lookup_checks(
    optimization_session: Session,
) -> None:
    session = optimization_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.read(
        mv.identities(entity, keys=((1,), (2,))), {"amount": amount, "region": region}
    )
    query = source.select({"kept": source["amount"]})
    baseline, before, _, before_actual = _execute(session, query, optimized=False)
    result, after, _, after_actual = _execute(session, query, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    reads = tuple(
        stage for stage in after.stages if isinstance(stage, SourceRead) and stage.projections
    )
    assert reads
    for stage in reads:
        original = next(item for item in before.stages if item.stage_id == stage.stage_id)
        assert isinstance(original, SourceRead)
        # Attribute fields belong to this source's complete Population key.
        # They cannot be treated as removable value slots before ReadFields.
        assert stage.keys == original.keys
        assert stage.projections == original.projections
        assert after_actual[stage.stage_id] == before_actual[stage.stage_id]
        assert stage.preparation_checks == original.preparation_checks
        assert stage.preparation_checks


def test_registered_selection_fusion_is_executed_on_native_source(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.observe({"sales": amount.sum()}, by=(region,))
    first = source.where(source["sales"] >= 0)
    second = first.where(first["sales"] < 35)
    plan, events, _ = _same(session, second)
    assert len(plan.fusion_groups) == 1
    group = plan.fusion_groups[0]
    assert group.law == "L1" and group.output in events
    assert group.members[0] not in events
    assert any(
        decision.status == "adopted" and decision.rule == "L1"
        for decision in plan.optimization_decisions
    )


def test_multiple_consumers_prevent_selection_fusion(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    first = source.where(source["a"] > 0)
    left = first.where(first["a"] < 25)
    right = first.where(first["a"] > 15)
    plan, _, _ = _same(optimization_session, left.align(right, how="outer"))
    assert any(
        decision.rule == "L1"
        and decision.status == "refused"
        and "multiple consumers" in decision.reason
        for decision in plan.optimization_decisions
    )


def test_top_and_share_boundaries_preserve_order_and_denominator(
    optimization_session: Session,
) -> None:
    session = optimization_session
    source = _wide(session)
    top = source.top(2, by=source["a"])
    selected = top.where(top["a"] > 0)
    query = selected.select({"kept": selected["a"]})
    plan, _, _ = _same(session, query)
    assert any("TopValues" in decision.reason for decision in plan.optimization_decisions)
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    observed = session.observe({"sales": amount.sum(), "aov": amount.mean()}, by=(region,))
    shared = observed.share("sales")
    query = shared.where(shared["sales"] > 20)
    plan, _, _ = _same(session, query)
    assert any("ShareValues" in decision.reason for decision in plan.optimization_decisions)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.layout is not None
    share = result.layout.values[-1].slot_id
    assert result.table[share].tolist() == pytest.approx([2 / 3])


def test_nullable_integer_fusion_preserves_the_true_only_mask(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.observe({"smallest": amount.min()}, by=(region,))
    first = source.where(source["smallest"] >= 0)
    query = first.where(first["smallest"] < 25)
    plan, _, _ = _same(session, query)
    assert len(plan.fusion_groups) == 1
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["key.0"].tolist() == ["A", "B"]


def test_dynamic_predicate_has_no_fusion_proof(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    first = source.where(source["a"] > 0)
    query = first.where(first["b"].divide(first["a"], zero_denominator="error") > 1)
    plan, _, _ = _same(optimization_session, query)
    assert not plan.fusion_groups
    assert any(
        decision.rule == "L1" and decision.status == "refused"
        for decision in plan.optimization_decisions
    )


@pytest.mark.parametrize("fail_check", (False, True))
def test_pending_check_is_kept_on_its_exact_fine_stage(
    optimization_session: Session, fail_check: bool
) -> None:
    source = _wide(optimization_session)
    first = source.where(source["a"] > 0)
    query = first.where(first["a"] < 0)
    graph = query._graph
    owner = graph.signature.owner
    obligation = Obligation(
        Fact(
            "finite_numeric",
            Binding(owner.session_id, owner.owner_id, graph.fingerprint, graph.fingerprint),
            "literal.a",
            "v1",
        ),
        "source.finite_numeric@v1",
        "consume",
    )
    plans: list[QueryPlan] = []
    for optimized in (False, True):
        with open_query_sources(
            optimization_session._runtime.connection_service(),
            graph,
            optimization_session.catalog._state,
        ) as prepared:
            plan = plan_query(graph, prepared.schemas, context=prepared.context)
        fine = plan.stages[-2]
        assert isinstance(fine, LocalCompute)
        plan = replace(plan, checks=(PlannedCheck(obligation, fine.stage_id),))
        if optimized:
            plan = optimize_query(plan)
        plans.append(plan)
        events: list[str] = []

        def forbidden_read(stage: SourceRead) -> StageOutput:
            raise AssertionError(stage.stage_id)

        def local(
            stage: LocalCompute,
            inputs: tuple[StageOutput, ...],
            events: list[str] = events,
        ) -> StageOutput:
            events.append(stage.stage_id)
            return run_local(stage, inputs)

        def check(
            requirement: PlannedCheck,
            output: StageOutput,
            fine_id: str = fine.stage_id,
            events: list[str] = events,
        ) -> None:
            assert requirement.stage_id == fine_id
            assert len(output.table) == 3
            events.append("check")
            if fail_check:
                raise ValueError("fine cell rejected")

        if fail_check:
            with pytest.raises(ValueError, match="fine cell rejected"):
                run_query_plan(plan, forbidden_read, local, check)
            assert events[-1] == "check" and plan.publish not in events
        else:
            result = run_query_plan(plan, forbidden_read, local, check)
            assert result.table.empty
            assert events.index("check") < events.index(plan.publish)
    assert not plans[1].fusion_groups
    assert any("check" in decision.reason for decision in plans[1].optimization_decisions)


def test_completed_strict_projection_cannot_be_pruned(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    completed = source.select(
        {"kept": source["a"], "bad": source["b"].divide(0, zero_denominator="error")}
    )
    query = completed.select({"kept": completed["kept"]})
    errors: list[tuple[type[BaseException], str]] = []
    for optimized in (False, True):
        with pytest.raises(
            (AnalysisError, SemanticExpressionError), match="nonzero denominator"
        ) as caught:
            _execute(optimization_session, query, optimized=optimized)
        errors.append((type(caught.value), str(caught.value)))
    assert errors[0] == errors[1]
    query = optimization_session.query(completed.population())
    errors.clear()
    for optimized in (False, True):
        with pytest.raises(
            (AnalysisError, SemanticExpressionError), match="nonzero denominator"
        ) as caught:
            _execute(optimization_session, query, optimized=optimized)
        errors.append((type(caught.value), str(caught.value)))
    assert errors[0] == errors[1]


def test_unselected_native_branch_keeps_its_dynamic_checks_masked(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum(empty="zero")
    guarded = ibis.ifelse(total >= 0, total, total.divide(0, zero_denominator="error"))
    query = session.observe({"safe": guarded}, by=(region,))
    _same(session, query)


def test_source_occurrences_and_original_finish_are_never_collapsed(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum()
    query = (
        session.observe({"twice": total + total, "shifted": total + 10}, by=(region,))
        .rollup()
        .rollup()
    )
    plan, _, _ = _same(session, query)
    sources = tuple(
        stage
        for stage in plan.stages
        if isinstance(stage, SourceRead) and stage.reduction is not None
    )
    logical = tuple(read for stage in sources for read in (stage.common_reads or (stage,)))
    assert len(logical) == 3 and len({stage.occurrence for stage in logical}) == 3
    assert any(
        decision.rule == "source_merge" and decision.status == "adopted"
        for decision in plan.optimization_decisions
    )
    assert any(
        decision.rule == "L8" and decision.status == "refused"
        for decision in plan.optimization_decisions
    )
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["value.0"].tolist() == [120]
    assert result.table["value.1"].tolist() == [70]


def test_actual_plan_memo_hits_and_binding_changes_invalidate(
    optimization_session: Session,
) -> None:
    session = optimization_session
    source = _wide(session)
    query = source.select({"kept": source["a"]})
    graph = query._graph
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        memo = InvocationMemo()
        first = plan_query(graph, prepared.schemas, context=prepared.context, memo=memo)
        before = memo.hits
        assert plan_query(graph, prepared.schemas, context=prepared.context, memo=memo) is first
        assert memo.hits > before
        changed = tuple(
            replace(schema, scope_id=schema.scope_id + "-changed") for schema in prepared.schemas
        )
        fresh = plan_query(graph, changed, context=prepared.context, memo=memo)
        assert fresh is not first
        key = compilation_key(
            graph,
            prepared.schemas,
            prepared.context,
            required=("value.0",),
            relations=prepared.relations,
        )
        planned = optimize_query(first)
        lowered = memo.lower(key, lambda: lower_query(planned, prepared.relations))
        assert memo.lower(key, lambda: lower_query(planned, prepared.relations)) is lowered
        another = replace(key, required=("value.0", "presence"))
        assert memo.lower(another, lambda: lower_query(planned, prepared.relations)) is not lowered
        changed_policy = replace(key, policy_version=key.policy_version + 1)
        assert (
            memo.lower(changed_policy, lambda: lower_query(planned, prepared.relations))
            is not lowered
        )
        changed_kernel = replace(key, implementations=(*key.implementations, "test@v2"))
        assert (
            memo.lower(changed_kernel, lambda: lower_query(planned, prepared.relations))
            is not lowered
        )


def test_memo_node_identity_does_not_serialize_the_definition_subgraph(
    optimization_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _wide(optimization_session)
    graph = source._graph
    with open_query_sources(
        optimization_session._runtime.connection_service(),
        graph,
        optimization_session.catalog._state,
    ) as prepared:
        context, schemas = prepared.context, prepared.schemas

    def forbidden_fingerprint(graph: BoundGraph) -> str:
        raise AssertionError("memo serialized a captured definition")

    monkeypatch.setattr(BoundGraph, "fingerprint", property(forbidden_fingerprint))
    first = compilation_key(graph, schemas, context)
    assert compilation_key(graph, schemas, context) == first
    changed = replace(
        graph,
        signature=replace(
            graph.signature,
            quantities=(
                replace(graph.signature.quantities[0], unit="USD"),
                *graph.signature.quantities[1:],
            ),
        ),
    )
    assert compilation_key(changed, schemas, context).node_id != first.node_id


def test_actual_plan_memo_keeps_full_check_scope(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    total = amount.sum()
    query = session.observe({"sales": total + total})
    graph = query._graph
    owner = graph.signature.owner
    obligation = Obligation(
        Fact(
            "finite_numeric",
            Binding(owner.session_id, owner.owner_id, graph.fingerprint, graph.fingerprint),
            "sales.orders.amount",
            "v1",
        ),
        "source.finite_numeric@v1",
        "consume",
    )
    observation = graph.signature.observation
    assert observation is not None
    route = SourceCheckRoute(obligation, observation.contributions[0].occurrence)
    other_scope = replace(
        route,
        obligation=replace(obligation, fact=replace(obligation.fact, version="v2")),
        occurrence=observation.contributions[1].occurrence,
    )
    graph = replace(
        graph, signature=replace(graph.signature, obligations=(obligation, other_scope.obligation))
    )
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        memo = InvocationMemo()
        checks = (route, other_scope)
        first = plan_query(
            graph, prepared.schemas, context=prepared.context, checks=checks, memo=memo
        )
        assert (
            plan_query(graph, prepared.schemas, context=prepared.context, checks=checks, memo=memo)
            is first
        )
        second = plan_query(
            graph,
            prepared.schemas,
            context=prepared.context,
            checks=(
                replace(route, occurrence=other_scope.occurrence),
                replace(other_scope, occurrence=route.occurrence),
            ),
            memo=memo,
        )
        assert second is not first
        assert first.checks[0].obligation.fact.version == "v1"
        assert first.checks[0].stage_id != second.checks[0].stage_id
        assert second.checks[1].obligation.fact.version == "v2"


def test_each_public_execute_owns_a_fresh_compilation_memo(
    optimization_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.compiler import optimization

    memos: list[InvocationMemo] = []

    class CapturingMemo(InvocationMemo):
        def __init__(self) -> None:
            super().__init__()
            memos.append(self)

    monkeypatch.setattr(optimization, "InvocationMemo", CapturingMemo)
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe({"sales": amount.sum()})
    assert query.execute().to_pandas()[("value", "sales")].tolist() == [60]
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=amount*2")
    assert query.execute().to_pandas()[("value", "sales")].tolist() == [120]
    assert len(memos) == 2 and memos[0] is not memos[1]


@pytest.mark.parametrize("zero", (False, True))
def test_zero_axis_original_empty_sum_and_ratio_keep_one_nullable_cell(
    optimization_session: Session,
    zero: bool,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum(empty="zero") if zero else amount.sum()
    source = session.observe({"sum": total, "ratio": total / amount.count()}, by=(region,))
    selected = source.top(0, by=source["sum"])
    query = selected.rollup()
    result, _, _, _ = _execute(session, query, optimized=False)
    assert len(result.table) == 1
    if zero:
        assert result.table["value.0"].tolist() == [0]
    else:
        assert result.table["value.0"].isna().all()
    assert result.table["value.1"].isna().all()
    _same(session, query)


@pytest.mark.parametrize("field", ("amount", "exact"))
def test_exact_original_group_and_common_source_block_execute_fewer_stages(
    optimization_session: Session, field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    value = session.catalog.fields.get("sales.orders." + field)
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    source = session.observe(
        {"sum": value.sum(empty="zero"), "count": value.count()}, by=(region, detail)
    )
    query = source.rollup(by=(region,)).rollup()
    calls: list[CompiledRead] = []
    execute = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        calls.append(read)
        return execute(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    with localcontext() as context:
        context.prec = 7
        baseline, before, before_events, _ = _execute(session, query, optimized=False)
        first_count = len(calls)
        result, after, after_events, _ = _execute(session, query, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    assert result.keys == baseline.keys and result.columns == baseline.columns
    assert result.layout == baseline.layout
    assert result.table["value.0"].tolist() == ([60] if field == "amount" else [Decimal("60.06")])
    assert result.table["value.1"].tolist() == [3]
    assert first_count == 2 and len(calls) - first_count == 1
    assert calls[-1].sql.upper().count("GROUP BY") == 1
    assert len(after.stages) == len(before.stages) - 2
    assert len(after_events) == len(before_events) - 1
    rollup = next(group for group in after.fusion_groups if group.law == "L8")
    assert rollup.members[0] not in after_events and rollup.output in after_events
    block = next(
        stage for stage in after.stages if isinstance(stage, SourceRead) and stage.common_reads
    )
    assert len(block.common_reads) == 2
    assert len({read.occurrence for read in block.common_reads}) == 2
    assert len(result.components) == 0


@pytest.mark.parametrize("field", ("amount", "exact"))
def test_ordered_original_group_keeps_middle_overflow_before_cancellation(
    optimization_session: Session,
    field: str,
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute(
            "UPDATE orders SET region=CASE WHEN id<=2 THEN 'A' ELSE 'B' END, detail=CAST(id AS VARCHAR)"
        )
        if field == "amount":
            connection.execute(
                "UPDATE orders SET amount=CASE WHEN id<=2 THEN 4611686018427387904 ELSE -4611686018427387904 END"
            )
        else:
            connection.execute(
                "UPDATE orders SET exact=CASE WHEN id<=2 THEN 500000000000000000000000000000000000.00 ELSE -500000000000000000000000000000000000.00 END"
            )
    amount = session.catalog.fields.get("sales.orders." + field)
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    source = session.observe({"sum": amount.sum()}, by=(region, detail))
    query = source.rollup(by=(region,)).rollup()
    failures: list[tuple[type[BaseException], str]] = []
    for optimized in (False, True):
        with pytest.raises(
            (AnalysisError, SemanticExpressionError), match="reduction overflow"
        ) as error:
            _execute(session, query, optimized=optimized)
        failures.append((type(error.value), str(error.value)))
    assert failures[0] == failures[1]
    with open_query_sources(
        session._runtime.connection_service(), query._graph, session.catalog._state
    ) as prepared:
        plan = optimize_query(plan_query(query._graph, prepared.schemas, context=prepared.context))
    assert any(group.law == "L8" for group in plan.fusion_groups)


def test_original_multi_consumer_and_filter_boundaries_prevent_fusion(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    source = session.observe({"sum": amount.sum()}, by=(region, detail))
    middle = source.rollup(by=(region,))
    selected = middle.where(middle["sum"] > 0)
    query = selected.rollup()
    plan, _, _ = _same(session, query)
    assert not any(group.law == "L8" for group in plan.fusion_groups)
    query = middle.rollup()
    with open_query_sources(
        session._runtime.connection_service(), query._graph, session.catalog._state
    ) as prepared:
        baseline = plan_query(query._graph, prepared.schemas, context=prepared.context)
        final = baseline.stages[-1]
        assert isinstance(final, LocalCompute)
        shared = final.inputs[0]
        branch = replace(final, stage_id=final.stage_id + ":other-consumer")
        baseline = replace(
            baseline,
            stages=(*baseline.stages[:-1], branch, final),
            consumers=(
                *(
                    (stage, (*consumers, branch.stage_id) if stage == shared else consumers)
                    for stage, consumers in baseline.consumers
                ),
                (branch.stage_id, ()),
            ),
        )
        plan = optimize_query(baseline)
        result = execute_numeric_query(prepared, lower_query(plan, prepared.relations))
        control = execute_numeric_query(prepared, lower_query(baseline, prepared.relations))
    pd.testing.assert_frame_equal(result.table, control.table)
    assert not any(group.law == "L8" for group in plan.fusion_groups)
    assert any(
        decision.rule == "L8" and "multiple consumers" in decision.reason
        for decision in plan.optimization_decisions
    )


def test_float_source_reductions_have_no_exact_fusion_grant(
    optimization_session: Session,
) -> None:
    session = optimization_session
    value = session.catalog.fields.get("sales.orders.approximate")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    query = (
        session.observe({"a": value.sum(), "b": value.sum()}, by=(region, detail))
        .rollup(by=(region,))
        .rollup()
    )
    plan, _, _ = _same(session, query)
    assert not any(group.law in ("L8", "source_block") for group in plan.fusion_groups)


def test_null_empty_policy_and_different_source_fibers_remain_independent(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    query = (
        session.observe({"sum": amount.sum(empty="null")}, by=(region, detail))
        .rollup(by=(region,))
        .rollup()
    )
    plan, _, _ = _same(session, query)
    assert any(group.law == "L8" for group in plan.fusion_groups)
    entity = session.catalog.entities.get("sales.orders")
    one = session.observe({"sum": amount.sum()}, members=mv.identities(entity, keys=((1,),)))
    two = session.observe({"sum": amount.sum()}, members=mv.identities(entity, keys=((2,),)))
    query = one.align(two)
    plan, _, _ = _same(session, query)
    assert not any(group.law == "source_block" for group in plan.fusion_groups)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.layout is not None
    assert result.table[result.layout.values[0].slot_id].tolist() == [10]
    assert result.table[result.layout.values[1].slot_id].tolist() == [30]


@pytest.mark.parametrize("kind", ("per", "window"))
def test_internal_samples_and_windows_keep_their_physical_boundaries(
    optimization_session: Session, kind: str
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    if kind == "per":
        metric = amount.sum().per(detail).sum()
        source = session.observe({"a": metric, "b": metric}, by=(region,))
        query = source.rollup().rollup()
    else:
        time = session.catalog.fields.get("sales.orders.ordered_at")
        calendar = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
        metric = amount.sum(time=time).window(mv.all_history())
        source = session.observe({"a": metric, "b": metric}, during=calendar, by=(region, detail))
        query = source.rollup(by=(calendar, region)).rollup(by=(calendar,))
    plan, _, _ = _same(session, query)
    assert not any(group.law in ("L8", "source_block") for group in plan.fusion_groups)


def test_common_block_keeps_same_period_filter_and_exact_checks(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    time = session.catalog.fields.get("sales.orders.ordered_at")
    region = session.catalog.fields.get("sales.orders.region")
    query = session.observe(
        {"a": amount.sum(time=time, empty="zero"), "b": amount.count(time=time)},
        during=session.time_scope(start="2026-01-01", end="2026-01-02"),
        by=(region,),
        where=amount > 0,
    )
    plan, _, _ = _same(session, query)
    assert any(group.law == "source_block" for group in plan.fusion_groups)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["value.0"].tolist() == [10, 20]
    assert result.table["value.1"].tolist() == [1, 1]
    query = session.observe(
        {"a": amount.sum(nulls="ignore"), "b": amount.sum(nulls="error")},
        by=(region,),
        where=amount > 0,
    )
    plan, _, _ = _same(session, query)
    assert not any(group.law == "source_block" for group in plan.fusion_groups)


def test_decimal_column_division_finishes_once_in_declared_float_carrier(
    optimization_session: Session,
) -> None:
    from fractions import Fraction

    session = optimization_session
    exact = session.catalog.fields.get("sales.orders.exact")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.observe({"a": exact.sum(), "b": exact.count()}, by=(region,))
    query = source.select({"ratio": source["a"] / source["b"]})
    _same(session, query)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["value.0"].dtype == "double[pyarrow]"
    assert result.table["value.0"].iloc[0] == float(Fraction(Decimal("40.03")) / 2)


def test_same_shape_different_datasources_never_share_a_physical_block(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    other = session.catalog.fields.get("sales.other.amount")
    query = session.observe({"primary": amount.sum(), "other": other.sum()})
    plan, _, _ = _same(session, query)
    reads = tuple(stage for stage in plan.stages if isinstance(stage, SourceRead))
    assert len(reads) == 2 and len({stage.binding_id for stage in reads}) == 2
    assert not any(group.law == "source_block" for group in plan.fusion_groups)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["value.0"].tolist() == [60]
    assert result.table["value.1"].tolist() == [600]


def test_physical_binding_ids_do_not_change_logical_fingerprints_or_artifacts(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe({"a": amount.sum(), "b": amount.count()})
    fingerprint = query._graph.fingerprint
    bindings: list[str] = []
    for _ in range(2):
        with open_query_sources(
            session._runtime.connection_service(), query._graph, session.catalog._state
        ) as prepared:
            plan = optimize_query(
                plan_query(query._graph, prepared.schemas, context=prepared.context)
            )
            bindings.append(
                next(stage.binding_id for stage in plan.stages if isinstance(stage, SourceRead))
            )
        assert query._graph.fingerprint == fingerprint
    assert bindings[0] != bindings[1]
    first, second = query.execute(), query.execute()
    assert first.id != second.id
    pd.testing.assert_frame_equal(first.to_pandas(), second.to_pandas())
    assert query._graph.fingerprint == fingerprint
    assert not hasattr(first, "components") and not hasattr(first, "fusion_groups")


@pytest.mark.parametrize("clock", ("00:00:00", "12:34:56"))
def test_shared_session_scope_keeps_frozen_fields_and_neutral_roundtrip(
    optimization_session: Session,
    clock: str,
) -> None:
    from datetime import date

    from marivo._temporal import (
        TimeScope,
        _SessionTimeScope,
        _time_scope_from_contract_data,
        time_scope,
    )

    scope = optimization_session.time_scope(
        start=f"2026-01-01T{clock}+00:00", end=f"2026-01-03T{clock}+00:00"
    )
    assert isinstance(scope, TimeScope)
    assert type(scope) is _SessionTimeScope
    axis = scope.by(mv.grain("day"))
    coarser = axis.coarsen(mv.grain("month"))
    assert coarser.scope is scope
    restored = _time_scope_from_contract_data(scope.contract().model_dump(mode="json"))
    assert isinstance(restored, TimeScope)
    assert restored.start == scope.start and restored.end == scope.end
    assert restored.boundary_timezone == scope.boundary_timezone
    assert restored.by(mv.grain("day")).grid.identity == axis.grid.identity
    civil = time_scope(start="2026-01-01", end="2026-01-03")
    civil_restored = _time_scope_from_contract_data(civil.contract().model_dump(mode="json"))
    assert type(civil_restored.start) is date and type(civil_restored.end) is date
    assert civil_restored == civil and civil_restored.contract().boundary_timezone is None
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    time = optimization_session.catalog.fields.get("sales.orders.ordered_at")
    observed = optimization_session.observe({"sum": amount.sum()}, where=time.within(scope))
    assert observed.execute().to_pandas()[("value", "sum")].tolist() == [
        60 if clock == "00:00:00" else 30
    ]


def test_broadcast_after_original_share_uses_only_target_coordinate_schema(
    optimization_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import graph_local_execution

    session = optimization_session
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, NULL, '2026-01-01')",
            [
                (1, "A", 900, "Easy", 90),
                (2, "A", 100, "Hard", 30),
                (3, "B", 100, "Easy", 5),
                (4, "B", 900, "Hard", 180),
            ],
        )
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    inspected = session.catalog.fields.get("sales.orders.amount").sum()
    failed = session.catalog.fields.get("sales.orders.exact").sum()
    cells = session.observe(
        {"inspected": inspected, "failed": failed, "rate": failed.divide(inspected)},
        by=(region, detail),
    )
    sizes = cells.select({"inspected": cells["inspected"]}).rollup(by=(detail,))
    reference = sizes.share("inspected")
    fixed = session.values(
        by=(detail,),
        columns={"weight": "1"},
        rows=((("Easy",), {"weight": Decimal("0.2")}), (("Hard",), {"weight": Decimal("0.8")})),
    )
    weighted = cells.select(
        {
            "reference": cells["rate"] * mv.broadcast(reference["inspected.share"], to=cells),
            "fixed": cells["rate"] * mv.broadcast(fixed["weight"], to=cells),
        }
    )
    query = weighted.reduce(
        {"standardized": weighted["reference"].sum(), "fixed": weighted["fixed"].sum()},
        by=(region,),
    )
    transported: list[tuple[str, ...]] = []
    original_run = graph_local_execution.run_local

    def inspect(stage: LocalCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        if stage.result is not None and len(stage.result.references) == 2:
            transported.append(tuple(inputs[0].table.columns))
        return original_run(stage, inputs)

    monkeypatch.setattr(graph_local_execution, "run_local", inspect)
    plan, _, _ = _same(session, query)
    selected = next(
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and stage.result is not None
        and len(stage.result.references) == 2
    )
    assert required_for(plan, selected.inputs[0]).columns == ("key.0", "key.1", "value.2")
    assert transported == [
        ("key.0", "key.1", "value.0", "value.1", "value.2"),
        ("key.0", "key.1", "value.2"),
    ]
    result = query.execute().to_pandas()
    assert result[("value", "standardized")].tolist() == [
        float(Fraction(1, 5)),
        float(Fraction(1, 8)),
    ]
    assert result[("value", "fixed")].tolist() == pytest.approx(
        [float(Fraction(13, 50)), float(Fraction(17, 100))]
    )


@pytest.mark.parametrize("precision", (12, 38))
def test_decimal_quantile_keeps_local_endpoints_and_native_rollup(
    optimization_session: Session,
    precision: int,
) -> None:
    session = optimization_session
    with localcontext() as context:
        context.prec = 120
        base = (
            Decimal("0") if precision == 12 else Decimal("123456789012345678901234567890123400.00")
        )
        values = [base + value for value in (1, 1, 1, 100)]
        expected = base + Decimal(Fraction(1703, 20).numerator) / Decimal(20)
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(f"ALTER TABLE orders ALTER exact TYPE DECIMAL({precision},2)")
        connection.executemany(
            "INSERT INTO orders VALUES (?, 'A', 0, ?, ?, NULL, '2026-01-01')",
            [(index, "x" if index < 3 else "y", value) for index, value in enumerate(values)],
        )
    value = session.catalog.fields.get("sales.orders.exact")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    time = session.catalog.fields.get("sales.orders.ordered_at")
    quantile = value.quantile(0.95, time=time)
    direct = session.observe({"p95": quantile}, by=(region,))
    fine = session.observe({"p95": quantile}, by=(region, detail)).rollup(by=(region,))
    # Original recomputation accepts the native Decimal quantile and handoff;
    # ordinary local endpoint interpolation keeps its exact rational finish.
    native = Decimal("85.14" if precision == 12 else "123456789012345675254913246067970539.52")
    for query, target in ((direct, expected), (fine, native)):
        _same(session, query)
        result = query.execute().to_pandas()
        assert result[("value", "p95")].tolist() == [target]
        assert str(result[("value", "p95")].dtype) == f"decimal128({precision}, 2)[pyarrow]"
    days = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
    windowed = session.observe(
        {"p95": quantile.window(mv.trailing_periods(count=2))},
        during=days,
        by=(region,),
    )
    assert windowed.execute().to_pandas()[("value", "p95")].tolist() == [expected, expected]


@pytest.mark.parametrize(("first", "expected"), (("1.00", "1.00"), ("1.01", "1.02")))
def test_decimal_median_and_result_mean_finish_declared_scale_half_even(
    optimization_session: Session,
    first: str,
    expected: str,
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders VALUES (?, 'A', 0, 'x', ?, NULL, '2026-01-01')",
            [(1, Decimal(first)), (2, Decimal(first) + Decimal("0.01"))],
        )
    exact = session.catalog.fields.get("sales.orders.exact")
    source = session.observe({"value": exact.median()})
    assert source.execute().to_pandas()[("value", "value")].tolist() == [Decimal(expected)]
    region = session.catalog.fields.get("sales.orders.region")
    values = session.values(
        by=(region,),
        columns={"value": "CNY"},
        rows=(
            (("A",), {"value": Decimal(first)}),
            (("B",), {"value": Decimal(first) + Decimal("0.01")}),
        ),
    )
    mean = values.reduce({"mean": values["value"].mean()})
    assert mean.execute().to_pandas()[("value", "mean")].tolist() == [Decimal(expected)]


def test_nanosecond_quantile_preserves_exact_half_even_finish(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    import marivo.semantic as ms

    case = analysis_dsl_case_factory("j1")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            "INSERT INTO \"order\" VALUES (?, 'A', 'web', 'paid', '2026-08-15', ?)",
            [(str(index), ticks) for index, ticks in enumerate((0, 0, 0, 30))],
        )
    export_dsl_parquet_models(case, case.root)
    path = case.root / "source_files/order.parquet"
    data = pq.read_table(path)
    pq.write_table(
        data.set_column(
            data.schema.get_field_index("amount"), "amount", data["amount"].cast(pa.duration("ns"))
        ),
        path,
    )
    ms.load(workspace_dir=case.root)
    value = case.session.catalog.fields.get("sales.order.amount")
    time = case.session.catalog.fields.get("sales.order.ordered_at")
    query = case.session.observe({"p95": value.quantile(0.95, time=time)})
    assert query.execute().to_pandas()[("value", "p95")].tolist() == [pd.Timedelta(26, unit="ns")]


def test_decimal_quantile_probability_boundaries_samples_and_strict_nulls(
    optimization_session: Session,
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute(
            "UPDATE orders SET exact=CASE WHEN id=4 THEN 100 ELSE 1 END, "
            "ordered_at=DATE '2026-01-01' + CAST(id-1 AS INTEGER)"
        )
    value = session.catalog.fields.get("sales.orders.exact")
    time = session.catalog.fields.get("sales.orders.ordered_at")
    probabilities = (0.0, 1.0, 1e-30, 5e-324, 0.95)
    direct = session.observe({str(q): value.quantile(q, time=time) for q in probabilities})
    frame = direct.execute().to_pandas()
    assert [frame[("value", str(q))].iloc[0] for q in probabilities] == [
        Decimal("1.00"),
        Decimal("100.00"),
        Decimal("1.00"),
        Decimal("1.00"),
        Decimal("85.15"),
    ]
    sampled = session.observe(
        {"p95": value.sum(time=time).per(time).quantile(0.95)},
        during=session.time_scope(start="2026-01-01", end="2026-01-05"),
    )
    assert sampled.execute().to_pandas()[("value", "p95")].tolist() == [Decimal("85.15")]
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("UPDATE orders SET exact=NULL WHERE id=4")
    strict = session.observe({"p95": value.quantile(0.95, time=time, nulls="error")})
    with pytest.raises(AnalysisError) as error:
        strict.execute()
    assert isinstance(error.value.__cause__, SemanticExpressionError)
    assert error.value.__cause__.expected == "non-NA source values"


def test_decimal_repeating_result_mean_finishes_before_comparison(
    optimization_session: Session,
) -> None:
    session = optimization_session
    region = session.catalog.fields.get("sales.orders.region")
    values = session.values(
        by=(region,),
        columns={"value": "CNY"},
        rows=tuple(
            ((key,), {"value": Decimal(value)})
            for key, value in (("A", "100.00"), ("B", "0.00"), ("C", "0.00"))
        ),
    )
    mean = values.reduce({"mean": values["value"].mean()})
    _same(session, mean)
    frame = mean.execute().to_pandas()
    assert frame[("value", "mean")].tolist() == [Decimal("33.33")]
    assert str(frame[("value", "mean")].dtype) == "decimal128(5, 2)[pyarrow]"
    comparison = mean.compare(mean).execute().to_pandas()
    assert comparison[("value", "mean.absolute")].tolist() == [Decimal("0.00")]
    assert comparison[("value", "mean.relative")].tolist() == [0.0]


def test_composite_coord_column_refuses_during_preparation_before_read_or_run(
    optimization_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic as ms
    from marivo.analysis.materialization import terminal_store

    session = optimization_session
    composite = session.catalog.entities.get("sales.composite_orders")
    members = session.members(ms.ref.entity("sales.composite_orders"))
    query = session.query(members)
    coordinate = query.coord(members.axes[0])
    invalid = (
        query.select({"identity": coordinate}),
        query.where(coordinate.is_null()),
    )
    runs_before = session.runs().items
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("business read or Run admission")
        raise AssertionError("Preparation refusal crossed a business read or Run boundary")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "compile", forbidden)
        guarded.setattr(SourceSession, "execute", forbidden)
        guarded.setattr(terminal_store, "admit", forbidden)
        for selected in invalid:
            with pytest.raises(AnalysisError) as error:
                selected.execute()
            assert error.value.location == "Query.coord"
            assert error.value.expected == "a single-component scalar coordinate Column"
            assert error.value.received == "axis 0 has 2 components: ('key.0', 'key.1')"
            assert error.value.repair is not None
            assert "mv.in_population" in error.value.repair.action
        assert session.runs().items == runs_before
    assert calls == []
    exported = query.execute().to_pandas()
    assert exported.iloc[:, 0].tolist() == [(1, "x"), (2, "y"), (3, "x"), (4, "z")]
    requested = mv.identities(composite, keys=((1, "x"), (2, "x")))
    selected_frame = session.query(members.where(mv.in_population(requested))).execute().to_pandas()
    assert selected_frame.iloc[:, 0].tolist() == [(1, "x")]
    scalar_members = session.members(ms.ref.entity("sales.orders"))
    scalar = session.query(scalar_members)
    assert scalar.select({"identity": scalar.coord(scalar_members.axes[0])}).execute().to_pandas()[
        ("value", "identity")
    ].tolist() == [1, 2, 3, 4]


def test_identities_requires_resolved_entity_without_source_lookup(
    optimization_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic as ms

    session = optimization_session
    entity = session.catalog.entities.get("sales.composite_orders")
    before = session.runs().items

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Literal identities must not read a business source")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "__enter__", forbidden)
        domain = mv.identities(entity, keys=((1, "x"), (999, "unconfirmed")))
        axis = domain.axes[0]
        assert isinstance(axis, AxisBinding) and axis.entity == entity.ref
        with pytest.raises(AnalysisError) as error:
            mv.identities(ms.ref.entity("sales.composite_orders"), keys=())  # type: ignore[arg-type]  # Exercise the runtime unresolved-Ref refusal.
        assert error.value.location == "identities"
        assert error.value.expected == "a resolved Entity"
        assert error.value.received == "Ref"
        assert error.value.repair is not None
        assert "session.catalog.entities.get" in error.value.repair.action
        assert session.runs().items == before
    assert session.query(domain).execute().to_pandas().iloc[:, 0].tolist() == [
        (1, "x"),
        (999, "unconfirmed"),
    ]
