"""Exercise Query-key rewrites at their prepared-plan interface."""

from collections.abc import Iterator
from dataclasses import replace
from typing import Literal

import ibis
import ibis.expr.types as ir
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo._definition.expressions import MemberCount
from marivo._definition.query import PopulationValues
from marivo._definition.relations import LiteralPopulation
from marivo.analysis.binding.programs import FormulaProgram, PopulationInput
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.query_planning import plan_query
from marivo.analysis.compiler.query_program import ResultProgram, SourceParameter
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    NativeRead,
    QueryPlan,
    SourceReadStage,
)
from marivo.analysis.materialization.graph_source_execution import execute_numeric_query
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_key_fusion import fuse_query_keys
from marivo.analysis.materialization.query_plan_transport import _consumers, _prune_transport
from marivo.analysis.materialization.query_sources import open_query_sources
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from tests.formal.verify_query_rewrites import _source_graph
from tests.formal.verify_query_rewrites import query_session as query_session
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

_INT = ScalarDataType("int64")
_KEY = (DataColumn("key", "key", _INT, False),)
_PARAMETER = (DataColumn("selected", "selected", _INT, False),)


@pytest.fixture
def prepared_key_fixture(
    query_session: mv.Session,
) -> Iterator[tuple[PreparedSources, LoweredQuery]]:
    catalog = query_session.catalog
    identifier = catalog_value(catalog, "sales.orders.id", kind="dimension")
    amount = catalog_value(catalog, "sales.orders.amount", kind="measure")
    extra = catalog_value(catalog, "sales.orders.extra", kind="measure")
    query = query_session.observe({"amount": amount.sum(), "extra": extra.sum()}, by=(identifier,))
    graph = _source_graph(query).graph
    with (
        query_session._runtime.connection_service().operation() as connections,
        open_query_sources(connections, graph, catalog._state) as prepared,
    ):
        yield prepared, lower_query(plan_query(graph, prepared.schemas), prepared.relations)


def _key_read(
    stage_id: str, source_id: str, expression: ir.Table
) -> tuple[SourceReadStage, LoweredRead]:
    return (
        SourceReadStage(stage_id, source_id, (), _KEY, ("key",), NativeRead(()), (), (), None),
        LoweredRead(stage_id, expression),
    )


def _membership(
    expression: ir.Table, key: str, name: str, producer: str
) -> tuple[ir.Table, SourceParameter]:
    relation = ibis.table({"selected": "int64"}, name=name)
    return (
        expression.semi_join(relation, expression[key] == relation["selected"]),
        SourceParameter(producer, name, _PARAMETER),
    )


@pytest.mark.parametrize("rule", ("source_key_fusion", "source_parameter_block"))
def test_native_query_key_parameters_preserve_complete_membership(
    prepared_key_fixture: tuple[PreparedSources, LoweredQuery],
    monkeypatch: pytest.MonkeyPatch,
    rule: Literal["source_key_fusion", "source_parameter_block"],
) -> None:
    prepared, original = prepared_key_fixture
    first_stage = next(item for item in original.plan.stages if isinstance(item, SourceReadStage))
    table = next(iter(prepared.bound.values())).relation
    base = table.select(key=table["id"])
    first, first_read = _key_read("left_filter", first_stage.source_id, base.filter(base.key > 1))
    second, second_read = _key_read(
        "right_filter", first_stage.source_id, base.filter((base.key < 5) & (base.key != 3))
    )
    combined, left = _membership(base, "key", "left_keys", first.stage_id)
    combined, right = _membership(combined, "key", "right_keys", second.stage_id)
    consumer, read = _key_read("result", first_stage.source_id, combined)
    consumer = replace(consumer, inputs=(first.stage_id, second.stage_id), parameters=(left, right))
    stages = (first, second, consumer)
    off = OptimizationPolicy.disabled()
    plan = QueryPlan(stages, _consumers(stages), consumer.stage_id, optimization_policy=off)
    lowered = LoweredQuery(plan, (first_read, second_read, read))
    trace = capture_source(monkeypatch)
    control = execute_numeric_query(prepared, lowered)
    assert sorted(control.table["key"].tolist()) == [2, 4]
    assert len(trace.native_sql) == 3

    fused = fuse_query_keys(prepared, lowered, policy=off.with_rule(rule, enabled=True))
    assert [stage.stage_id for stage in fused.plan.stages] == ["result"]
    assert any(
        item.rule == rule and item.status == "adopted" for item in fused.plan.optimization_decisions
    )
    assert fused.plan.source_read_stage("result").parameters == ()
    result = execute_numeric_query(prepared, fused)
    assert sorted(result.table["key"].tolist()) == [2, 4]
    assert len(trace.native_sql) == 4


def test_shared_query_key_grid_preserves_each_group_value(
    prepared_key_fixture: tuple[PreparedSources, LoweredQuery],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, original = prepared_key_fixture
    reads = tuple(stage for stage in original.plan.stages if isinstance(stage, SourceReadStage))
    assert len(reads) == 2
    table = next(iter(prepared.bound.values())).relation
    producer, read = _key_read(
        "retained_keys",
        reads[0].source_id,
        table.filter(table.id.isin((1, 3, 4))).select(key=table.id),
    )
    branches: list[SourceReadStage] = []
    native: list[LoweredRead] = [read]
    for index, stage in enumerate(reads):
        source = original.read(stage.stage_id)
        expression, parameter = _membership(
            source.expression, stage.keys[0], f"selected_keys_{index}", producer.stage_id
        )
        branches.append(replace(stage, inputs=(producer.stage_id,), parameters=(parameter,)))
        native.append(replace(source, expression=expression))
    final = next(stage for stage in original.plan.stages if stage.stage_id == original.plan.publish)
    stages = (producer, *branches, final)
    off = OptimizationPolicy.disabled()
    lowered = LoweredQuery(
        replace(
            original.plan, stages=stages, consumers=_consumers(stages), optimization_policy=off
        ),
        tuple(native),
    )
    trace = capture_source(monkeypatch)
    expected = {1: (10, 3), 3: (None, 8), 4: (0, 9)}
    fused = fuse_query_keys(
        prepared, lowered, policy=off.with_rule("source_shared_key_fusion", enabled=True)
    )
    assert any(
        decision.rule == "source_shared_key_fusion" and decision.status == "adopted"
        for decision in fused.plan.optimization_decisions
    )
    for candidate, count in ((lowered, 3), (fused, 1)):
        before = len(trace.native_sql)
        result = execute_numeric_query(prepared, candidate)
        actual = {
            row[0]: tuple(None if pd.isna(cell) else cell for cell in row[1:])
            for row in result.table.itertuples(index=False, name=None)
        }
        assert actual == expected
        assert len(trace.native_sql) - before == count


@pytest.mark.parametrize("demand", ("unused", "output", "member-count"))
def test_dead_query_transport_removes_only_an_unused_population(
    prepared_key_fixture: tuple[PreparedSources, LoweredQuery],
    demand: Literal["unused", "output", "member-count"],
) -> None:
    _, original = prepared_key_fixture
    native = next(stage for stage in original.plan.stages if isinstance(stage, SourceReadStage))
    final = next(stage for stage in original.plan.stages if stage.stage_id == original.plan.publish)
    assert isinstance(final, LocalCompute) and isinstance(final.program, FormulaProgram)
    population = LiteralPopulation((), ((),), "1")
    carrier = LocalCompute(
        "population",
        (native.stage_id,),
        _KEY,
        ("key",),
        ResultProgram(PopulationValues(population), _KEY, ("key",), (), (("key",),), (("key",),)),
    )
    program = replace(
        final.program,
        populations=(PopulationInput(population, carrier.stage_id, carrier.keys),),
        output_population=population if demand == "output" else None,
        outputs=(("count", MemberCount(population)),)
        if demand == "member-count"
        else final.program.outputs,
    )
    output = replace(final, inputs=(*final.inputs, carrier.stage_id), program=program)
    stages = (*original.plan.stages[:-1], carrier, output)
    off = OptimizationPolicy.disabled()
    plan = replace(
        original.plan, stages=stages, consumers=_consumers(stages), optimization_policy=off
    )
    assert _prune_transport(plan, native.stage_id) == (plan, ())
    on = replace(plan, optimization_policy=off.with_rule("dead_population_transport", enabled=True))
    result, discarded = _prune_transport(on, native.stage_id)
    if demand != "unused":
        assert (result, discarded) == (on, ())
    else:
        assert discarded == (carrier.stage_id,)
        assert result.consumers_of(carrier.stage_id) == ()
        rewritten = result.stages[-1]
        assert isinstance(rewritten, LocalCompute) and isinstance(rewritten.program, FormulaProgram)
        assert rewritten.program.outputs == program.outputs
        assert rewritten.inputs == final.inputs and not rewritten.program.populations
