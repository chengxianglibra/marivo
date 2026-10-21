"""Optimized Query execution preserves the unoptimized stage/check contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.query import LiteralValues
from marivo._definition.types import AxisBinding
from marivo.analysis.compiler.frame_plan import CompiledFramePlan
from marivo.analysis.compiler.graph_lowering import LoweredRead, lower_query
from marivo.analysis.compiler.graph_plan import (
    FrameCompute,
    FrameInput,
    LocalCompute,
    QueryPlan,
    SourceRead,
    plan_query,
    read_members,
    read_projections,
)
from marivo.analysis.compiler.optimization import (
    InvocationMemo,
    compilation_key,
    optimize_query,
)
from marivo.analysis.compiler.query_program import ResultProgram
from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    PreparedSources,
    execute_numeric_query,
    open_query_sources,
)
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import DslCaseFactory, export_dsl_parquet_models, terminal_runs


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
ordered_at = ms.time_dimension_column(name='ordered_at', entity=orders, column='ordered_at', is_default=True)
other = ms.entity(name='other', datasource=ms.ref.datasource('second'), source=md.table('orders'), primary_key=['id'])
other_amount = ms.measure_column(name='amount', entity=other, column='amount', additivity=ms.additive_all(), unit='CNY')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("optimization", timezone="UTC")


def _source_graph(frame: AnalysisFrame) -> BoundGraph:
    assert isinstance(frame._backing, ExpressionFrameBacking)
    assert isinstance(frame._backing.root, (SourceFrameBinding, LiteralFrameBinding))
    return frame._backing.root.graph


def _execute(
    session: Session, query: AnalysisFrame, *, optimized: bool
) -> tuple[StageOutput, QueryPlan, tuple[str, ...], dict[str, tuple[str, ...]]]:
    from marivo.analysis.materialization import frame_evaluation, frame_source_preparation

    if (
        isinstance(query._backing, ExpressionFrameBacking)
        and isinstance(query._backing.root, (SourceFrameBinding, LiteralFrameBinding))
        and not (
            isinstance(query._backing.root, SourceFrameBinding)
            and query._backing.root.frame_key_inputs
        )
    ):
        graph = _source_graph(query)
        events: list[str] = []
        actual: dict[str, tuple[str, ...]] = {}
        with open_query_sources(
            session._runtime.connection_service(), graph, session.catalog._state
        ) as prepared:
            plan = plan_query(graph, prepared.schemas)
            if optimized:
                plan = optimize_query(plan)

            def local(stage: LocalCompute, output: StageOutput) -> None:
                events.append(stage.stage_id)
                actual[stage.stage_id] = tuple(output.table.columns)

            def source(stage: SourceRead, request: LoweredRead, output: StageOutput) -> None:
                actual[stage.stage_id] = tuple(output.table.columns)

            result = execute_numeric_query(
                prepared,
                lower_query(plan, prepared.relations),
                observe_local=local,
                observe_read=source,
            )
        return result, plan, tuple(events), actual
    frame_events: list[str] = []
    frame_actual: dict[str, tuple[str, ...]] = {}
    captured: list[tuple[StageOutput, QueryPlan]] = []
    from marivo.analysis.materialization.graph_source_execution import run_frame_plan

    run = run_frame_plan

    def observe(
        compiled: CompiledFramePlan,
        *,
        source_prepared: Mapping[str, PreparedSources],
        read_fixed: Callable[[FrameInput], CompletedFrameData],
        adapt_source: Callable[[str, StageOutput], StageOutput],
        observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None = None,
        observe_local: Callable[[LocalCompute | FrameCompute, StageOutput], None] | None = None,
    ) -> StageOutput:
        def local(stage: LocalCompute | FrameCompute, output: StageOutput) -> None:
            frame_events.append(stage.stage_id)
            frame_actual[stage.stage_id] = tuple(output.table.columns)
            if observe_local is not None:
                observe_local(stage, output)

        def source(stage: SourceRead, request: LoweredRead, output: StageOutput) -> None:
            frame_actual[stage.stage_id] = tuple(output.table.columns)
            if observe_read is not None:
                observe_read(stage, request, output)

        output = run(
            compiled,
            source_prepared=source_prepared,
            read_fixed=read_fixed,
            adapt_source=adapt_source,
            observe_read=source,
            observe_local=local,
        )
        captured.append((output, compiled.lowered.plan))
        return output

    with patch.object(frame_evaluation, "run_frame_plan", observe):
        if optimized:
            query.compute()
        else:
            with patch.object(frame_source_preparation, "optimize_query", lambda plan: plan):
                query.compute()
    assert len(captured) == 1
    output, plan = captured[0]
    assert query._local.owner == session._runtime.local.owner
    return output, plan, tuple(frame_events), frame_actual


def _same(
    session: Session, query: AnalysisFrame
) -> tuple[QueryPlan, tuple[str, ...], dict[str, tuple[str, ...]]]:
    baseline, original, _, _ = _execute(session, query, optimized=False)
    result, planned, events, actual = _execute(session, query, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    assert result.keys == baseline.keys
    assert result.columns == baseline.columns
    assert result.layout == baseline.layout
    assert not original.required_slots and not original.fusion_groups
    return planned, events, actual


def _wide(session: Session) -> AnalysisFrame:
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


def test_complete_literal_input_remains_checked_before_projection(
    optimization_session: Session,
) -> None:
    source = _wide(optimization_session)
    query = source.select({"kept": source["a"]})
    plan, _, actual = _same(optimization_session, query)
    literal = next(
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and isinstance(stage.program, ResultProgram)
        and isinstance(stage.program.operation, LiteralValues)
    )
    assert actual[literal.stage_id] == ("key.0", "value.0", "value.1", "value.2")
    result = query.compute().to_pandas()
    assert result.columns.equals(
        pd.MultiIndex.from_tuples([("coordinate", "axis.0"), ("value", "kept")])
    )
    assert result[("value", "kept")].tolist() == [10, 20, 30]


def test_repeated_consumers_share_complete_input_without_mutation(
    optimization_session: Session,
) -> None:
    source = _wide(optimization_session)
    left = source.select({"left": source["a"]})
    right = source.select({"right": source["b"]})
    query = left.align(right, on=tuple(zip(left.axes, right.axes, strict=True)))
    plan, _, actual = _same(optimization_session, query)
    literals = [
        stage
        for stage in plan.stages
        if isinstance(stage, LocalCompute)
        and isinstance(stage.program, ResultProgram)
        and isinstance(stage.program.operation, LiteralValues)
    ]
    assert len(literals) == 1
    assert actual[literals[0].stage_id] == ("key.0", "value.0", "value.1", "value.2")
    rows = query.compute().to_pandas()
    assert rows[("value", "left.left")].tolist() == [10, 20, 30]
    assert rows[("value", "right.right")].tolist() == [100, 200, 300]


def test_source_attributes_keep_complete_keys_and_lookup_checks(
    optimization_session: Session,
) -> None:
    session = optimization_session
    entity = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    source = session.read(
        mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((2,),))),
        {"amount": amount, "region": region},
    )
    query = source.select({"kept": source["amount"]})
    baseline, before, _, before_actual = _execute(session, source, optimized=False)
    result, after, _, after_actual = _execute(session, source, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    reads = tuple(
        stage for stage in after.stages if isinstance(stage, SourceRead) and read_projections(stage)
    )
    assert reads
    for stage in reads:
        original = next(item for item in before.stages if item.stage_id == stage.stage_id)
        assert isinstance(original, SourceRead)
        # Attribute fields belong to this source's complete Population key.
        # They cannot be treated as removable value slots before ReadFields.
        assert stage.keys == original.keys
        assert read_projections(stage) == read_projections(original)
        assert after_actual[stage.stage_id] == before_actual[stage.stage_id]
        assert stage.preparation_checks == original.preparation_checks
        assert stage.preparation_checks

    assert query.compute().to_pandas()[("value", "kept")].tolist() == [10, 30]


def test_current_frame_filters_preserve_real_source_domain(optimization_session: Session) -> None:
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    region = optimization_session.catalog.fields.get("sales.orders.region")
    source = optimization_session.observe({"sales": amount.sum()}, by=(region,))
    first = source.where(source["sales"] >= 0)
    second = first.where(first["sales"] < 35)
    plan, events, _ = _same(optimization_session, second)
    filters = [stage for stage in plan.stages if isinstance(stage, FrameCompute)]
    assert len(filters) == 2 and all(stage.stage_id in events for stage in filters)
    assert second.compute().to_pandas()[("value", "sales")].tolist() == [20]


def test_multiple_consumers_keep_independent_filter_domains(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    first = source.where(source["a"] > 0)
    # Select binds to the exact filtered input, rather than its ancestor.
    right = first.where(first["a"] > 15)
    left_input = first.where(first["a"] < 25)
    left = left_input.select({"left": left_input["a"]})
    right = right.select({"right": right["a"]})
    query = left.align(right, how="outer", on=tuple(zip(left.axes, right.axes, strict=True)))
    _same(optimization_session, query)
    result = query.compute().to_pandas()
    assert result[("value", "left.left")].iloc[:2].tolist() == [10, 20]
    assert result[("value", "right.right")].iloc[1:].tolist() == [20, 30]
    assert pd.isna(result[("value", "left.left")].iloc[2]) and pd.isna(
        result[("value", "right.right")].iloc[0]
    )


def test_top_and_explicit_source_denominator_preserve_values(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    top = source.top(2, by=source["a"])
    selected = top.where(top["a"] > 0)
    query = selected.select({"kept": selected["a"]})
    _same(optimization_session, query)
    assert query.compute().to_pandas()[("value", "kept")].tolist() == [30, 20]
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    region = optimization_session.catalog.fields.get("sales.orders.region")
    denominator = (
        optimization_session.observe({"total": amount.sum()})
        .compute()
        .to_pandas()[("value", "total")]
        .iloc[0]
    )
    observed = optimization_session.observe({"sales": amount.sum()}, by=(region,))
    shares = observed.select({"sales": observed["sales"], "share": observed["sales"] / denominator})
    selected = shares.where(shares["sales"] > 20)
    assert selected.compute().to_pandas()[("value", "share")].tolist() == pytest.approx([2 / 3])


def test_nullable_integer_filters_preserve_true_only_mask(optimization_session: Session) -> None:
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    region = optimization_session.catalog.fields.get("sales.orders.region")
    source = optimization_session.observe({"smallest": amount.min()}, by=(region,))
    first = source.where(source["smallest"] >= 0)
    query = first.where(first["smallest"] < 25)
    _same(optimization_session, query)
    assert query.compute().to_pandas().iloc[:, 0].tolist() == ["A", "B"]


def test_dynamic_predicate_checks_before_empty_filter(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    first = source.where(source["a"] > 0)
    query = first.where(first["b"].divide(first["a"], zero_denominator="error") > 1)
    _same(optimization_session, query)
    assert query.compute().to_pandas()[("value", "a")].tolist() == [10, 20, 30]


def test_completed_strict_projection_cannot_be_pruned(
    optimization_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import graph_local_execution

    events: list[tuple[str, tuple[str, ...], tuple[int, ...]]] = []
    submissions: list[CompiledRead] = []
    run = graph_local_execution.run_local
    execute = SourceSession.execute

    def checked(stage: LocalCompute | FrameCompute, inputs: tuple[StageOutput, ...]) -> StageOutput:
        columns = tuple(column.name for column in stage.columns)
        rows = tuple(len(output.table) for output in inputs)
        events.append(("start", columns, rows))
        try:
            output = run(stage, inputs)
        except (AnalysisError, SemanticExpressionError):
            events.append(("failed", columns, rows))
            raise
        events.append(("complete", columns, rows))
        return output

    def submitted(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
        submissions.append(read)
        return execute(self, read)

    monkeypatch.setattr(graph_local_execution, "run_local", checked)
    monkeypatch.setattr(SourceSession, "execute", submitted)
    source = _wide(optimization_session)
    completed = source.select(
        {"kept": source["a"], "bad": source["b"].divide(0, zero_denominator="error")}
    )
    query = completed.select({"kept": completed["kept"]})
    errors: list[tuple[type[BaseException], str]] = []
    for optimized in (False, True):
        events.clear()
        with pytest.raises(
            (AnalysisError, SemanticExpressionError), match="nonzero denominator"
        ) as caught:
            _execute(optimization_session, query, optimized=optimized)
        errors.append((type(caught.value), str(caught.value)))
        assert events[-1][0] == "failed" and "bad" in events[-1][1]
        assert events[-1][2] == (3,)
        assert sum(event[0] == "failed" for event in events) == 1
        assert submissions == [] and terminal_runs(optimization_session) == ()
    assert errors[0] == errors[1]
    selection = optimization_session.population_from(
        completed,
        on=((completed.axes[0], optimization_session.catalog.fields.get("sales.orders.region")),),
    )
    query = optimization_session.frame(selection)
    errors.clear()
    for optimized in (False, True):
        events.clear()
        with pytest.raises(
            (AnalysisError, SemanticExpressionError), match="nonzero denominator"
        ) as caught:
            _execute(optimization_session, query, optimized=optimized)
        errors.append((type(caught.value), str(caught.value)))
        assert events[-1][0] == "failed" and "bad" in events[-1][1]
        assert events[-1][2] == (3,)
        assert sum(event[0] == "failed" for event in events) == 1
        assert submissions == [] and terminal_runs(optimization_session) == ()
    assert errors[0] == errors[1]


def test_unselected_native_branch_keeps_its_dynamic_checks_masked(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum()
    guarded = ibis.ifelse(total >= 0, total, total.divide(0, zero_denominator="error"))
    query = session.observe({"safe": guarded}, by=(region,))
    _same(session, query)


def test_explicit_source_occurrences_and_formula_finish_are_never_collapsed(
    optimization_session: Session,
) -> None:
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    total = amount.sum()
    query = optimization_session.observe({"twice": total + total, "shifted": total + 10})
    plan, _, _ = _same(optimization_session, query)
    reads = [
        item
        for stage in plan.stages
        if isinstance(stage, SourceRead)
        for item in (read_members(stage) or (stage,))
    ]
    assert len(reads) == 3 and len({stage.occurrence for stage in reads}) == 3
    frame = query.compute().to_pandas()
    assert frame[("value", "twice")].tolist() == [120]
    assert frame[("value", "shifted")].tolist() == [70]


def test_actual_plan_memo_hits_and_binding_changes_invalidate(
    optimization_session: Session,
) -> None:
    session = optimization_session
    source = session.observe({"kept": session.catalog.fields.get("sales.orders.amount").sum()})
    graph = _source_graph(source)
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        memo = InvocationMemo()
        first = plan_query(graph, prepared.schemas, memo=memo)
        before = memo.hits
        assert plan_query(graph, prepared.schemas, memo=memo) is first
        assert memo.hits > before
        changed = tuple(
            replace(
                schema,
                fields=tuple(
                    replace(column, nullable=not column.nullable) for column in schema.fields
                ),
            )
            for schema in prepared.schemas
        )
        fresh = plan_query(graph, changed, memo=memo)
        assert fresh is not first
        key = compilation_key(
            graph,
            prepared.schemas,
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
    graph = _source_graph(source)
    with open_query_sources(
        optimization_session._runtime.connection_service(),
        graph,
        optimization_session.catalog._state,
    ) as prepared:
        schemas = prepared.schemas

    def forbidden_fingerprint(graph: BoundGraph) -> str:
        raise AssertionError("memo serialized a captured definition")

    monkeypatch.setattr(BoundGraph, "fingerprint", property(forbidden_fingerprint))
    first = compilation_key(graph, schemas)
    assert compilation_key(graph, schemas) == first
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
    assert compilation_key(changed, schemas).node_id != first.node_id


def test_actual_plan_memo_keeps_consumed_policy_and_relation_scope(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    graph = _source_graph(session.observe({"sales": amount.sum()}))
    strict = _source_graph(session.observe({"sales": amount.sum(nulls="error")}))
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        memo = InvocationMemo()
        first = plan_query(graph, prepared.schemas, memo=memo)
        assert plan_query(graph, prepared.schemas, memo=memo) is first
        original = compilation_key(graph, prepared.schemas, relations=prepared.relations)
        assert compilation_key(graph, prepared.schemas, relations=prepared.relations) == original
        rebound = {name: table.view() for name, table in prepared.relations.items()}
        assert compilation_key(graph, prepared.schemas, relations=rebound) != original
        with open_query_sources(
            session._runtime.connection_service(), strict, session.catalog._state
        ) as scoped_sources:
            second = plan_query(strict, scoped_sources.schemas, memo=memo)
            assert second is not first
            assert plan_query(strict, scoped_sources.schemas, memo=memo) is second
            assert strict.fingerprint != graph.fingerprint
            assert scoped_sources.schemas != prepared.schemas


def test_each_public_compute_owns_a_fresh_compilation_memo(
    optimization_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:

    memos: list[InvocationMemo] = []

    class CapturingMemo(InvocationMemo):
        def __init__(self) -> None:
            super().__init__()
            memos.append(self)

    monkeypatch.setattr(
        "marivo.analysis.materialization.frame_source_preparation.InvocationMemo", CapturingMemo
    )
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    query = session.observe({"sales": amount.sum()})
    assert query.compute().to_pandas()[("value", "sales")].tolist() == [60]
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=amount*2")
    assert query.compute().to_pandas()[("value", "sales")].tolist() == [120]
    assert len(memos) == 2 and memos[0] is not memos[1]


def test_explicit_empty_source_sum_and_ratio_keep_one_nullable_cell(
    optimization_session: Session,
) -> None:
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    total = amount.sum()
    query = optimization_session.observe(
        {"sum": total, "ratio": total / amount.count()}, where=amount > 1000
    )
    _same(optimization_session, query)
    frame = query.compute().to_pandas()
    assert len(frame) == 1
    assert frame[("value", "sum")].tolist() == [0]
    assert frame[("value", "ratio")].isna().all()


@pytest.mark.parametrize("field", ("amount", "exact"))
def test_exact_source_common_block_preserves_values_and_occurrences(
    optimization_session: Session, field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = optimization_session
    value = session.catalog.fields.get("sales.orders." + field)
    query = session.observe({"sum": value.sum(), "count": value.count()})
    calls: list[CompiledRead] = []
    execute = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        calls.append(read)
        return execute(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    with localcontext() as context:
        context.prec = 7
        baseline, _, _, _ = _execute(session, query, optimized=False)
        count = len(calls)
        result, plan, _, _ = _execute(session, query, optimized=True)
    pd.testing.assert_frame_equal(result.table, baseline.table)
    assert result.table["value.0"].tolist() == ([60] if field == "amount" else [Decimal("60.06")])
    assert result.table["value.1"].tolist() == [3]
    assert count == 2 and len(calls) - count == 1
    assert any(
        isinstance(stage, SourceRead) and len(read_members(stage)) == 2 for stage in plan.stages
    )


@pytest.mark.parametrize("field", ("amount", "exact"))
def test_source_per_group_keeps_middle_overflow_before_cancellation(
    optimization_session: Session, field: str
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:
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
    value = session.catalog.fields.get("sales.orders." + field)
    region = session.catalog.fields.get("sales.orders.region")
    query = session.observe({"sum": value.sum().per(region).sum()})
    if field == "amount":
        for optimized in (False, True):
            output, _, _, _ = _execute(session, query, optimized=optimized)
            assert output.table["value.0"].tolist() == [Decimal(0)]
    else:
        errors: list[tuple[type[BaseException], str]] = []
        for optimized in (False, True):
            with pytest.raises(
                (AnalysisError, SemanticExpressionError), match=r"precision|Decimal|decimal"
            ) as error:
                _execute(session, query, optimized=optimized)
            errors.append((type(error.value), str(error.value)))
        assert errors[0] == errors[1]
    assert terminal_runs(session) == ()


def test_multiple_current_consumers_preserve_filter_barriers(optimization_session: Session) -> None:
    source = _wide(optimization_session)
    left = source.where(source["a"] > 10)
    right = source.where(source["a"] < 30)
    query = left.compare(
        {"base": right}, how="outer", on={"base": tuple(zip(left.axes, right.axes, strict=True))}
    )
    _same(optimization_session, query)
    frame = query.compute().to_pandas()
    assert frame[("presence", "current.present")].tolist() == [True, True, False]
    assert frame[("presence", "base.present")].tolist() == [True, False, True]


def test_float_source_reductions_have_no_exact_fusion_grant(optimization_session: Session) -> None:
    value = optimization_session.catalog.fields.get("sales.orders.approximate")
    query = optimization_session.observe({"a": value.sum(), "b": value.sum()})
    plan, _, _ = _same(optimization_session, query)
    assert not any(group.law == "source_block" for group in plan.fusion_groups)


def test_null_empty_policy_and_different_source_fibers_remain_independent(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    detail = session.catalog.fields.get("sales.orders.detail")
    query = session.observe({"sum": amount.sum()})
    plan, _, _ = _same(session, query)
    assert not any(group.law == "L8" for group in plan.fusion_groups)
    entity = session.catalog.entities.get("sales.orders")
    one = session.observe(
        {"sum": amount.sum()},
        where=mv.in_population(mv.coordinates(by=(mv.identity(entity),), keys=(((1,),),))),
    )
    two = session.observe(
        {"other": amount.sum()},
        where=mv.in_population(mv.coordinates(by=(mv.identity(entity),), keys=(((2,),),))),
    )
    query = one.align(two, on=tuple(zip(one.axes, two.axes, strict=True)))
    plan, _, _ = _same(session, query)
    assert not any(group.law == "source_block" for group in plan.fusion_groups)
    result, _, _, _ = _execute(session, query, optimized=True)
    assert result.table["value.0"].tolist() == [10]
    assert result.table["value.1"].tolist() == [30]


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
        query = source
    else:
        time = session.catalog.fields.get("sales.orders.ordered_at")
        calendar = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
        metric = amount.sum(time=time).window(mv.all_history())
        source = session.observe({"a": metric, "b": metric}, by=(calendar, region, detail))
        query = source
    plan, _, _ = _same(session, query)
    assert not any(group.law in ("L8", "source_block") for group in plan.fusion_groups)


def test_common_block_keeps_same_period_filter_and_exact_checks(
    optimization_session: Session,
) -> None:
    session = optimization_session
    amount = session.catalog.fields.get("sales.orders.amount")
    time = session.catalog.fields.get("sales.orders.ordered_at")
    region = session.catalog.fields.get("sales.orders.region")
    scope = session.time_scope(start="2026-01-01", end="2026-01-02")
    query = session.observe(
        {"a": amount.sum(time=time), "b": amount.count(time=time)},
        by=(region,),
        where=(amount > 0) & time.within(scope),
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
    fingerprint = _source_graph(query).fingerprint
    bindings: list[str] = []
    for _ in range(2):
        with open_query_sources(
            session._runtime.connection_service(), _source_graph(query), session.catalog._state
        ) as prepared:
            plan = optimize_query(plan_query(_source_graph(query), prepared.schemas))
            bindings.append(
                next(stage.binding_id for stage in plan.stages if isinstance(stage, SourceRead))
            )
        assert _source_graph(query).fingerprint == fingerprint
    assert bindings[0] != bindings[1]
    first, second = query.persist(), query.persist()
    assert first.artifact.id != second.artifact.id
    pd.testing.assert_frame_equal(first.to_pandas(), second.to_pandas())
    assert _source_graph(query).fingerprint == fingerprint
    assert not hasattr(first, "components") and not hasattr(first, "fusion_groups")


@pytest.mark.parametrize("clock", ("00:00:00", "12:34:56"))
def test_shared_session_scope_keeps_frozen_fields_and_neutral_roundtrip(
    optimization_session: Session,
    clock: str,
) -> None:

    from marivo._temporal import (
        TimeScope,
        _SessionTimeScope,
        _time_scope_from_contract_data,
    )

    scope = optimization_session.time_scope(
        start=f"2026-01-01T{clock}+00:00", end=f"2026-01-03T{clock}+00:00"
    )
    assert isinstance(scope, TimeScope)
    assert type(scope) is _SessionTimeScope
    axis = scope.by(mv.grain("day"))
    assert scope.by(mv.grain("month")).scope is scope
    restored = _time_scope_from_contract_data(scope._contract().model_dump(mode="json"))
    assert isinstance(restored, TimeScope)
    assert restored.start == scope.start and restored.end == scope.end
    assert restored.boundary_timezone == scope.boundary_timezone
    assert restored.by(mv.grain("day")).grid.identity == axis.grid.identity
    with pytest.raises(ValueError, match="frozen boundary_timezone"):
        _time_scope_from_contract_data(
            {
                "schema": "time-scope/v1",
                "kind": "absolute",
                "start": "2026-01-01",
                "end": "2026-01-03",
            }
        )
    amount = optimization_session.catalog.fields.get("sales.orders.amount")
    time = optimization_session.catalog.fields.get("sales.orders.ordered_at")
    observed = optimization_session.observe({"sum": amount.sum()}, where=time.within(scope))
    assert observed.compute().to_pandas()[("value", "sum")].tolist() == [
        60 if clock == "00:00:00" else 30
    ]


def test_explicit_source_weights_preserve_standardized_and_fixed_results(
    optimization_session: Session,
) -> None:
    session = optimization_session
    with duckdb.connect(str(session.project_root / "optimization.duckdb")) as connection:
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
    _same(session, cells)
    rows = cells.compute().to_pandas()
    sizes = session.observe({"inspected": inspected}, by=(detail,)).compute().to_pandas()
    denominator = (
        session.observe({"inspected": inspected})
        .compute()
        .to_pandas()[("value", "inspected")]
        .iloc[0]
    )
    assert denominator == 2000
    weights = dict(
        zip(
            sizes.iloc[:, 0],
            sizes[("value", "inspected")].astype("float64") / float(denominator),
            strict=True,
        )
    )
    fixed = (
        session.values(
            by=(detail,),
            columns={"weight": "1"},
            rows=((("Easy",), {"weight": Decimal("0.2")}), (("Hard",), {"weight": Decimal("0.8")})),
        )
        .compute()
        .to_pandas()
    )
    fixed_weights = dict(zip(fixed.iloc[:, 0], fixed[("value", "weight")], strict=True))
    standardized: dict[str, float] = {}
    fixed_totals: dict[str, float] = {}
    for key, category, rate in zip(
        rows.iloc[:, 0], rows.iloc[:, 1], rows[("value", "rate")], strict=True
    ):
        standardized[key] = standardized.get(key, 0) + rate * weights[category]
        fixed_totals[key] = fixed_totals.get(key, 0) + rate * float(fixed_weights[category])
    assert list(standardized) == ["A", "B"]
    assert list(standardized.values()) == [float(Fraction(1, 5)), float(Fraction(1, 8))]
    assert list(fixed_totals.values()) == pytest.approx(
        [float(Fraction(13, 50)), float(Fraction(17, 100))]
    )


@pytest.mark.parametrize("precision", (12, 38))
def test_decimal_source_quantile_keeps_exact_endpoints_and_windows(
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
    # Original recomputation accepts the native Decimal quantile and handoff;
    # ordinary local endpoint interpolation keeps its exact rational finish.
    native = Decimal("85.14" if precision == 12 else "123456789012345675254913246067970539.52")
    for query, target in ((direct, expected),):
        _same(session, query)
        result = query.compute().to_pandas()
        assert result[("value", "p95")].tolist() == [target]
        assert str(result[("value", "p95")].dtype) == f"decimal128({precision}, 2)[pyarrow]"
    days = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
    windowed = session.observe(
        {"p95": quantile.window(mv.trailing_periods(count=2))}, by=(days, region)
    )
    assert windowed.compute().to_pandas()[("value", "p95")].tolist() == [expected, expected]


@pytest.mark.parametrize(("first", "expected"), (("1.00", "1.01"), ("1.01", "1.02")))
def test_decimal_median_and_result_mean_finish_declared_scale_half_up(
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
    assert source.compute().to_pandas()[("value", "value")].tolist() == [Decimal(expected)]
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
    assert mean.compute().to_pandas()[("value", "mean")].tolist() == [Decimal(expected)]


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
    assert query.compute().to_pandas()[("value", "p95")].tolist() == [pd.Timedelta(26, unit="ns")]


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
    direct = session.observe(
        {f"q{index}": value.quantile(q, time=time) for index, q in enumerate(probabilities)}
    )
    frame = direct.compute().to_pandas()
    assert [frame[("value", f"q{index}")].iloc[0] for index, _ in enumerate(probabilities)] == [
        Decimal("1.00"),
        Decimal("100.00"),
        Decimal("1.00"),
        Decimal("1.00"),
        Decimal("85.15"),
    ]
    sampled = session.observe(
        {"p95": value.sum(time=time).per(time).quantile(0.95)},
        by=(session.time_scope(start="2026-01-01", end="2026-01-05"),),
    )
    assert sampled.compute().to_pandas()[("value", "p95")].tolist() == [Decimal("85.15")]
    with duckdb.connect(str(session.catalog.workspace_dir / "optimization.duckdb")) as connection:
        connection.execute("UPDATE orders SET exact=NULL WHERE id=4")
    strict = session.observe({"p95": value.quantile(0.95, time=time, nulls="error")})
    with pytest.raises(AnalysisError) as error:
        strict.compute()
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
    frame = mean.compute().to_pandas()
    assert frame[("value", "mean")].tolist() == [Decimal("33.33")]
    assert str(frame[("value", "mean")].dtype) == "decimal128(5, 2)[pyarrow]"
    comparison = (
        mean.compare({"base": mean}, on={"base": tuple(zip(mean.axes, mean.axes, strict=True))})
        .compute()
        .to_pandas()
    )
    assert comparison[("value", "base.absolute.mean")].tolist() == [Decimal("0.00")]
    assert comparison[("value", "base.relative.mean")].tolist() == [0.0]


def test_composite_coord_column_refuses_during_preparation_before_read_or_run(
    optimization_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marivo.analysis.materialization import terminal_store

    session = optimization_session
    composite = session.catalog.entities.get("sales.composite_orders")
    members = session.members(session.catalog.entities.get("sales.composite_orders"))
    query = session.frame(members)
    runs_before = terminal_runs(session)
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("business read or Run admission")
        raise AssertionError("Preparation refusal crossed a business read or Run boundary")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "compile", forbidden)
        guarded.setattr(SourceSession, "execute", forbidden)
        guarded.setattr(terminal_store, "admit", forbidden)
        with pytest.raises(AnalysisError) as error:
            query.coord(query.axes[0])
        assert error.value.location == "frame.coord"
        assert error.value.expected == "a single physical component coordinate"
        assert error.value.received == repr(query.axes[0].key_names)
        assert error.value.repair is not None
        assert "complete axis" in error.value.repair.action
        assert terminal_runs(session) == runs_before
    assert calls == []
    exported = query.compute().to_pandas()
    assert exported.iloc[:, 0].tolist() == [(1, "x"), (2, "y"), (3, "x"), (4, "z")]
    requested = mv.coordinates(by=(mv.identity(composite),), keys=(((1, "x"),), ((2, "x"),)))
    selected_frame = session.frame(members.where(mv.in_population(requested))).compute().to_pandas()
    assert selected_frame.iloc[:, 0].tolist() == [(1, "x")]
    scalar_members = session.members(session.catalog.entities.get("sales.orders"))
    scalar = session.frame(scalar_members)
    assert scalar.select({"identity": scalar.coord(scalar.axes[0])}).compute().to_pandas()[
        ("value", "identity")
    ].tolist() == [1, 2, 3, 4]


def test_literal_identity_coordinates_require_resolved_entity_without_source_lookup(
    optimization_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic as ms

    session = optimization_session
    entity = session.catalog.entities.get("sales.composite_orders")
    before = terminal_runs(session)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Literal identities must not read a business source")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "__enter__", forbidden)
        domain = mv.coordinates(
            by=(mv.identity(entity),), keys=(((1, "x"),), ((999, "unconfirmed"),))
        )
        axis = domain._definition.axes[0]
        assert isinstance(axis, AxisBinding) and axis.entity == entity.ref
        with pytest.raises(AnalysisError) as error:
            mv.coordinates(by=(mv.identity(ms.ref.entity("sales.composite_orders")),), keys=())  # type: ignore[arg-type]  # Exercise the runtime unresolved-Ref refusal.
        assert error.value.location == "identity"
        assert error.value.expected == "a resolved Entity or nonempty relationship path"
        assert error.value.received == "Ref"
        assert error.value.repair is not None
        assert "mv.identity(entity)" in error.value.repair.action
        assert terminal_runs(session) == before
    assert session.frame(domain).compute().to_pandas().iloc[:, 0].tolist() == [
        (1, "x"),
        (999, "unconfirmed"),
    ]
