"""Source-owned independent native coalition queries and invocation-local caching."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, TypeAlias

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
from marivo.analysis.binding.attribute_game import formula_id
from marivo.analysis.compiler.attribute_native_queries import lower_native_coalition, native_failure
from marivo.analysis.compiler.graph_lowering import LoweredRead
from marivo.analysis.compiler.graph_plan import AttributeCoalitionRead, SourceRead, read_reduction
from marivo.analysis.core.attribute_game import CoalitionValue, CompletedGame, GameContext
from marivo.analysis.core.data_types import DataColumn, DataType
from marivo.analysis.core.frame_method_facts import (
    FrameMethodValue,
    FrameShapleyLeaf,
    FrameShapleyMethod,
    FrameShapleyNativeQuantile,
    FrameShapleyQuery,
    FrameShapleyQueryGame,
    FrameShapleyReduction,
)
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.methods._ibis_numeric import evaluate_numeric
from marivo.analysis.methods.frame_attribute_contribution import _numeric_binary
from marivo.analysis.methods.frame_attribute_shapley import (
    _attribute_numeric_context,
    _null,
    _number,
    _series,
    assemble_game_report,
    game_contexts,
    sample_game,
)
from marivo.semantic._numeric_lowering import NumericValue, lower_metric_formula, lower_row

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources

Keys: TypeAlias = tuple[tuple[object, ...], ...]
NativeCacheKey: TypeAlias = tuple[tuple[object, ...], Keys, Keys]
NativeCache: TypeAlias = dict[NativeCacheKey, StageOutput]


@dataclass(frozen=True, slots=True)
class NativePair:
    occurrence: tuple[int, ...]
    current: SourceRead
    baseline: SourceRead
    current_table: ir.Table
    baseline_table: ir.Table
    prepared: PreparedSources
    identity: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class PreparedNativeGame:
    pairs: tuple[NativePair, ...]


def prepare_native_game(
    stage: SourceRead, prepared_sources: Mapping[str, PreparedSources]
) -> PreparedNativeGame:
    """Rebind foreign physical recipes to one compatible owner and precompile."""
    from marivo.analysis.compiler.graph_lowering import LoweredQuery
    from marivo.analysis.compiler.graph_plan import QueryPlan
    from marivo.analysis.materialization.graph_source_execution import (
        PreparedSources,
        qualify_query_sources,
    )
    from marivo.analysis.materialization.query_parameters import instantiate

    assert isinstance(stage.program, AttributeCoalitionRead)
    current, baseline = stage.program.endpoints
    cp, bp = prepared_sources[current.input_id], prepared_sources[baseline.input_id]
    pairs: list[NativePair] = []
    for (occurrence, a), (other, b) in zip(current.reads, baseline.reads, strict=True):
        if occurrence != other:
            native_failure("paired original reducer occurrences differ")
        owner = cp.sources.get(a.source_id, cp.source)
        foreign = bp.sources.get(b.source_id, bp.source)
        if (
            owner.datasource.backend_type != foreign.datasource.backend_type
            or owner.datasource.fields != foreign.datasource.fields
            or owner.datasource.env_refs != foreign.datasource.env_refs
        ):
            native_failure("a paired leaf uses different captured datasource configurations")
        at = cp.relations.get(a.native_relation_id or a.stage_id)
        bt = bp.relations.get(b.native_relation_id or b.stage_id)
        if at is None:
            at = cp.relations[a.source_id]
        if bt is None:
            bt = bp.relations[b.source_id]
        replacements: dict[ops.Node, ops.Node] = {}
        bound = dict(cp.bound)
        sources = dict(cp.sources)
        physical = set(bt.op().find(ops.DatabaseTable))
        for path, original in bp.bound.items():
            if original.relation.op() not in physical:
                continue
            rebound = owner.bind(
                original.source, source_identity=f"attribute.native:{baseline.input_id}:{path}"
            )
            replacements[original.relation.op()] = rebound.relation.op()
            name = f"attribute.native:{path}"
            bound[name] = rebound
            sources[name] = owner
        bt = bt.op().replace(replacements).to_expr()
        assert isinstance(bt, ir.Table)
        selected = PreparedSources(owner, cp.schemas, cp.relations, bound, sources, cp.entities)
        # Empty typed parameters qualify the complete native expression before
        # any business-row statement, without refreshing an analysis directory.
        probe_tables = []
        for read, relation in ((a, at), (b, bt)):
            for parameter in read.parameters:
                empty = StageOutput.from_frame(
                    pd.DataFrame(columns=[c.slot_id for c in parameter.columns]),
                    parameter.columns,
                    tuple(c.slot_id for c in parameter.columns),
                )
                relation = instantiate(
                    relation,
                    parameter.placeholder,
                    parameter.columns,
                    empty,
                    provider=owner.provider.name,
                    source=owner,
                )
            probe_tables.append(relation)
        probe, lowered = lower_native_coalition(
            a,
            b,
            probe_tables[0],
            probe_tables[1],
            (),
            (),
            stage_id=stage.stage_id + ":prepare:" + str(len(pairs)),
        )
        plan = QueryPlan((probe,), (), probe.stage_id)
        qualify_query_sources(selected, LoweredQuery(plan, (lowered,)))
        from marivo._definition.graph import computation_fingerprint

        def identity(read: SourceRead, relation: ir.Table) -> tuple[object, ...]:
            reduction = read_reduction(read)
            assert reduction is not None
            return (
                read.binding_id or read.native_relation_id,
                read.source_id,
                None if read.binding_id else relation.op(),
                read.field_columns,
                tuple(computation_fingerprint(p) for p in read.predicates),
                read.period,
                read.preparation_checks,
                read.parameters,
                computation_fingerprint(reduction.node),
                (
                    reduction.axes,
                    reduction.calendar,
                    reduction.time_column,
                    reduction.duration,
                    reduction.window_key,
                    reduction.preserve_sample_presence,
                    reduction.calendars,
                    reduction.sampled,
                    reduction.retain_quantile_bounds,
                    reduction.native_quantile_value,
                    reduction.coverage,
                    reduction.provider,
                    reduction.mean_fold,
                ),
            )

        pair_identity = (id(cp), id(bp), identity(a, at), identity(b, bt))
        pairs.append(NativePair(occurrence, a, b, at, bt, selected, pair_identity))
    return PreparedNativeGame(tuple(pairs))


def _query_fact(
    output: StageOutput,
    endpoint: Literal["current", "baseline", "both"],
    purpose: Literal["player_domain", "group_diagnostic", "coalition"],
) -> tuple[FrameShapleyQuery, ...]:
    return tuple(
        FrameShapleyQuery(
            c.stage_id,
            endpoint,
            c.provider,
            None,
            purpose,
            c.returned_rows,
            c.decoded_arrow_bytes,
            1,
            0,
            0,
            None,
            None,
            None,
        )
        for c in output.attribute_state_captures
    )


def run_native_game(
    stage: SourceRead,
    inputs: tuple[StageOutput, ...],
    prepared: PreparedNativeGame,
    cache: NativeCache,
    observe_read: Callable[[SourceRead, LoweredRead, StageOutput], None] | None,
) -> StageOutput:
    """Execute unseen native leaf requests and assemble the shared pure game report."""
    from marivo.analysis.materialization.graph_source_execution import read_stage
    from marivo.analysis.materialization.query_parameters import instantiate

    assert isinstance(stage.program, AttributeCoalitionRead)
    from marivo.analysis.materialization.query_runtime_types import effective_game_program

    source_inputs = inputs[:5]
    program = effective_game_program(stage.program.game, source_inputs)
    game = program.operation.games[0]
    directory = dict(zip(stage.inputs, inputs, strict=True))
    receipts: dict[str, FrameShapleyQuery] = {}
    for index, source in enumerate(source_inputs[1:3]):
        for fact in _query_fact(
            source, "current" if index == 0 else "baseline", "group_diagnostic"
        ):
            previous = receipts.get(fact.capture_id)
            receipts[fact.capture_id] = (
                replace(fact, endpoint="both") if previous is not None else fact
            )
    directory_receipts = dict(receipts)
    complete = []
    for context in game_contexts(program, source_inputs):
        receipts = dict(directory_receipts)
        leaf_outputs: dict[tuple[int, ...], DataColumn] = {}
        leaf_inputs: dict[tuple[int, ...], tuple[DataType, ...]] = {}

        def evaluate(
            mask: int,
            context: GameContext = context,
            leaf_outputs: dict[tuple[int, ...], DataColumn] = leaf_outputs,
            leaf_inputs: dict[tuple[int, ...], tuple[DataType, ...]] = leaf_inputs,
            receipts: dict[str, FrameShapleyQuery] = receipts,
        ) -> CoalitionValue:
            leaf_values = {}
            for pair in prepared.pairs:
                selected_keys = []
                for side in (0, 1):
                    source = source_inputs[side + 1]
                    schema = program.left_schema if side == 0 else program.right_schema
                    rows = dict(context.current_rows if side == 0 else context.baseline_rows)
                    keys = tuple(
                        tuple(
                            None
                            if _null(value := source.table[column.slot_id].iloc[rows[player]])
                            else value
                            for axis in schema.axes
                            for column in axis.columns
                        )
                        for i, player in enumerate(context.players)
                        if player in rows and bool(mask & (1 << i)) == (side == 0)
                    )
                    selected_keys.append(keys)
                cache_key = (pair.identity, selected_keys[0], selected_keys[1])
                output = cache.get(cache_key)
                if output is None:
                    tables = []
                    for read, relation in (
                        (pair.current, pair.current_table),
                        (pair.baseline, pair.baseline_table),
                    ):
                        owner = pair.prepared.source
                        for parameter in read.parameters:
                            relation = instantiate(
                                relation,
                                parameter.placeholder,
                                parameter.columns,
                                directory[parameter.stage_id],
                                provider=owner.provider.name,
                                source=owner,
                            )
                        tables.append(relation)
                    query_id = f"{stage.stage_id}:native:{len(cache)}"
                    read, request = lower_native_coalition(
                        pair.current,
                        pair.baseline,
                        tables[0],
                        tables[1],
                        selected_keys[0],
                        selected_keys[1],
                        stage_id=query_id,
                    )
                    read = replace(
                        read, attribute_native=True, native_reads=(pair.current, pair.baseline)
                    )
                    output = read_stage(pair.prepared, read, request)
                    cache[cache_key] = output
                    if observe_read is not None:
                        observe_read(read, request, output)
                for fact in _query_fact(
                    output,
                    "baseline"
                    if mask == 0
                    else "current"
                    if mask == (1 << len(context.players)) - 1
                    else "both",
                    "coalition",
                ):
                    receipts[fact.capture_id] = fact
                column = output.columns[0]
                leaf_outputs[pair.occurrence] = column
                reduction = read_reduction(pair.current)
                assert reduction is not None
                fields = {
                    path: pair.current_table[name] for path, name in pair.current.field_columns
                }
                node = reduction.node
                operands = (
                    (node.operand,)
                    if isinstance(node, SourceAggregate)
                    else (node.value, node.weight)
                    if isinstance(node, SourceWeightedMean)
                    else ()
                )
                leaf_inputs[pair.occurrence] = tuple(
                    data_type(lower_row(operand, fields).value.type()) for operand in operands
                )
                leaf_values[pair.occurrence] = output.table[column.slot_id]
            table = ibis.table(
                {
                    f"leaf_{i}": ibis_type(leaf_outputs[leaf.occurrence].dtype)
                    for i, leaf in enumerate(game.leaves)
                },
                name="_attribute_native_formula",
            )
            values = {
                f"leaf_{i}": leaf_values[leaf.occurrence] for i, leaf in enumerate(game.leaves)
            }
            expression = lower_metric_formula(
                game.node,
                (0,),
                {
                    leaf.occurrence: NumericValue(table[f"leaf_{i}"])
                    for i, leaf in enumerate(game.leaves)
                },
                dict(game.declared),
            )
            result = evaluate_numeric(expression.value.op(), values, pd.RangeIndex(1))
            for check in expression.checks:
                if bool(
                    evaluate_numeric(check.violation.op(), values, pd.RangeIndex(1))
                    .fillna(False)
                    .any()
                ):
                    from marivo.semantic._numeric_lowering import numeric_failure

                    numeric_failure(check.expected, check.received)
            return CoalitionValue(
                DataColumn("coalition", "coalition", data_type(expression.value.type()), False),
                _number(result.iloc[0], reason="invalid_value"),
            )

        from marivo.analysis.core.attribute_game_errors import AttributeGameError

        parent_columns = tuple(c for axis in program.parent_schema.axes for c in axis.columns)
        with _attribute_numeric_context(
            source_inputs[1:3], game.metric, context.parent, parent_columns
        ):
            try:
                sample = sample_game(
                    context.parent,
                    context.players,
                    program.operation.permutations,
                    program.operation.seed,
                    evaluate,
                )
            except AttributeGameError as error:
                raise AttributeGameError(
                    reason=error.reason,
                    metric=game.metric,
                    parent=context.parent,
                    occurrence=error.occurrence,
                    received=error.received if error.received is not None else str(error),
                ) from error
        leaf_facts = []
        for leaf in game.leaves:
            pair = next(p for p in prepared.pairs if p.occurrence == leaf.occurrence)
            original_reduction = read_reduction(pair.current)
            assert original_reduction is not None
            node = original_reduction.node
            reduction_fact: FrameShapleyNativeQuantile | FrameShapleyReduction
            if isinstance(node, SourceAggregate) and node.reducer in (
                "median",
                "quantile",
                "approx_median",
                "approx_quantile",
            ):
                q = 0.5 if "median" in node.reducer else node.q
                assert q is not None
                reduction_fact = FrameShapleyNativeQuantile(
                    "native_quantile", node.reducer, float(q), "original_native_reducer/v1"
                )
            else:
                if isinstance(node, EntityCount):
                    reduction_fact = FrameShapleyReduction("aggregate", "entity_count")
                elif isinstance(node, SourceWeightedMean):
                    reduction_fact = FrameShapleyReduction("aggregate", "weighted_mean")
                elif node.reducer in ("sum", "count", "count_distinct", "mean", "min", "max"):
                    reduction_fact = FrameShapleyReduction("aggregate", node.reducer)
                else:
                    native_failure(f"unadmitted native reducer fact: {node.reducer}")
            dependencies = []
            for side in (0, 1):
                slot = dict(program.original_value_slots[side])[leaf.occurrence]
                dependencies.append(
                    tuple(
                        c.stage_id
                        for c in source_inputs[side + 1].attribute_state_captures
                        if slot in c.value_slots
                    )
                )
            policy: Literal["error", "skip"] = "error" if leaf.nulls == "error" else "skip"
            leaf_facts.append(
                FrameShapleyLeaf(
                    leaf.occurrence,
                    dependencies[0],
                    dependencies[1],
                    reduction_fact,
                    leaf_inputs[leaf.occurrence],
                    leaf_outputs[leaf.occurrence],
                    "rows"
                    if isinstance(node, EntityCount)
                    else "co_valid_value_weight"
                    if leaf.kind == "weighted_mean"
                    else "non_null_value",
                    "not_applicable" if isinstance(node, EntityCount) else policy,
                    policy if leaf.kind == "weighted_mean" else "not_applicable",
                )
            )
        paths = dict(
            zip(program.output_schema.value_paths, program.output_schema.values, strict=True)
        )
        change = _numeric_binary(
            "sub",
            _series(sample.current.value, sample.current.column),
            _series(sample.baseline.value, sample.baseline.column),
        )
        facts = tuple(receipts.values())
        method = FrameShapleyMethod(
            "shapley",
            FrameMethodValue(
                paths[("detail", "current", *program.operation.target.path)], sample.current.value
            ),
            FrameMethodValue(
                paths[("detail", "baseline", *program.operation.target.path)], sample.baseline.value
            ),
            FrameMethodValue(
                paths[("detail", "absolute", *program.operation.target.path)],
                _number(change.values.iloc[0]),
            ),
            program.operation.permutations,
            program.operation.seed,
            "permutation/v1",
            "native_queries/v1",
            0,
            len(context.players),
            sample.unique_coalitions,
            sample.unique_coalitions,
            sum(f.source_reads for f in facts),
            program.operation.permutations * len(context.players),
            FrameShapleyQueryGame(
                "group_replacement",
                "group_replacement/v1",
                formula_id(game),
                formula_id(program.operation.games[1]),
                "joint_key_union/v1",
                "independent_reads/v1",
                "evaluated_coalitions/v1",
                "native_approximate_metric/v1"
                if any(
                    isinstance(leaf.source, SourceAggregate)
                    and leaf.source.reducer.startswith("approx_")
                    for leaf in game.leaves
                )
                else "exact_metric/v1",
                "observed_marginal_mean/v1",
                "typed_marginal_float64_sum/v1",
            ),
            tuple(leaf_facts),
            facts,
        )
        complete.append(CompletedGame(context, sample, method))
    frame, payload = assemble_game_report(program, source_inputs, tuple(complete))
    from marivo.analysis.core.frame_data import required_attachments

    required = required_attachments(program.output_schema)
    return StageOutput.from_frame(
        frame,
        program.output_schema.columns,
        program.output_schema.key_fields,
        attribute_methods=(payload,),
        frame_schema=program.output_schema,
        frame_attachments=tuple(
            a for a in source_inputs[0].frame_attachments if a.name in required
        ),
    )
