"""Bound one direct pending field-read Top without changing its consumed domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import ibis
import ibis.expr.types as ir

from marivo._definition.query import TopValues, ValueSlot
from marivo._definition.relations import ReadQuery
from marivo._kernel.validation import is_instance
from marivo.analysis.compiler.compiled_frame import CompiledFramePlan
from marivo.analysis.compiler.lowered_query import LoweredRead
from marivo.analysis.compiler.native_query import NativeFrameTopFinish, NativeQueryFinish
from marivo.analysis.compiler.query_program import FrameProgram
from marivo.analysis.compiler.query_stages import (
    FrameCompute,
    NativeRead,
    RuleDecision,
    SourceReadStage,
)
from marivo.analysis.compiler.source_predicates import string_bytes
from marivo.analysis.core.frame_operations import SourceFrameInput
from marivo.analysis.model.data_schema import DataAxis, DataSchema
from marivo.analysis.model.data_types import ScalarDataType
from marivo.analysis.model.frame_contracts import CheckedStageInputT
from marivo.datasource.spi import query_dialect
from marivo.semantic.spi import NumericCheck


def native_frame_key_types(schema: DataSchema, *, exact_strings: bool) -> bool:
    return all(
        column.dtype == ScalarDataType("int64")
        or (exact_strings and column.dtype == ScalarDataType("string"))
        for axis in schema.axes
        for column in axis.columns
    )


def native_top_order(
    data: ir.Table,
    score: ir.Value,
    keys: tuple[str, ...],
    provider: str,
    *,
    descending: bool,
) -> tuple[ir.Value, ...] | None:
    """Rank typed scores with a complete, byte-exact deterministic tie breaker."""
    if score.type().is_floating():
        floating_order = query_dialect(provider).float_order_keys
        if floating_order is None:
            return None
        score_order = floating_order(score, descending)
    else:
        if score.type().is_string():
            score = string_bytes(score, provider)
        score_order = (
            score.desc(nulls_first=False) if descending else score.asc(nulls_first=False),
        )
    return (
        *score_order,
        *(
            (string_bytes(data[key], provider) if data[key].type().is_string() else data[key]).asc(
                nulls_first=True
            )
            for key in keys
        ),
    )


def _bounded_read(
    stage: SourceReadStage,
    read: LoweredRead,
    schema: DataSchema,
    operation: TopValues,
    provider: str,
) -> tuple[SourceReadStage, LoweredRead]:
    expression = read.expression
    obligations = read.obligations
    marker = obligations.numeric_presence
    data = (
        expression.filter(expression[marker].fill_null(False)) if marker is not None else expression
    )
    summaries: dict[str, ir.Value] = {"__frame_top_input_count": expression.count()}
    checks: list[tuple[str, NumericCheck]] = []
    witnesses: list[tuple[str, tuple[str, ...]]] = []
    for index, (name, check) in enumerate(read.numeric_checks):
        alias = f"__frame_top_numeric_{index}"
        summaries[alias] = expression[name].any().fill_null(False)
        checks.append((alias, check))
        original_keys = obligations.witnesses(name, width=len(stage.keys))
        if len(original_keys) == len(stage.keys):
            witness_keys = tuple(f"{alias}.witness.{i}" for i in range(len(stage.keys)))
            summaries.update(
                {
                    target: expression[original].min()
                    for target, original in zip(witness_keys, original_keys, strict=True)
                }
            )
            witnesses.append((alias, witness_keys))
    integrity: list[tuple[str, str, str]] = []
    for index, column in enumerate(schema.columns):
        if column.nullable:
            continue
        alias = f"__frame_top_null_{index}"
        invalid = expression[column.slot_id].isnull()
        if marker is not None:
            invalid = invalid & expression[marker].fill_null(False)
        summaries[alias] = invalid.any().fill_null(False)
        integrity.append(
            (
                alias,
                f"{column.slot_id}: a nonnullable column without NA",
                "NA values in the consumed Frame input",
            )
        )
    summary = expression.aggregate(**summaries)
    if not stage.unique_keys.proves(stage.keys):
        keys = data.select(
            **{
                key: string_bytes(data[key], provider)
                if data[key].type().is_string()
                else data[key]
                for key in stage.keys
            }
        )
        counted = keys.group_by(*stage.keys).aggregate(__frame_top_key_count=keys.count())
        duplicate = counted.aggregate(
            __frame_top_duplicate=(counted["__frame_top_key_count"] > 1).any().fill_null(False)
        )
        summary = summary.cross_join(duplicate)
        integrity.append(
            (
                "__frame_top_duplicate",
                f"a complete unique stage key {stage.keys!r}",
                "null or duplicate key values",
            )
        )
    assert isinstance(operation.score, ValueSlot)
    score = data[schema.values[operation.score.index].slot_id]
    order = native_top_order(data, score, stage.keys, provider, descending=operation.descending)
    assert order is not None
    selected = (
        data.order_by(*order)
        .limit(operation.n)
        .select(
            *(column.slot_id for column in stage.columns),
            __frame_top_present=ibis.literal(True),
        )
    )
    nullable_value = query_dialect(provider).join_value
    if nullable_value is not None:
        selected = selected.select(
            **{name: nullable_value(selected[name]) for name in selected.columns}
        )
    # The existing local Top restores presentation order over at most K rows.
    # The left join retains the full-input summaries even for Top(0).
    bounded = summary.left_join(selected, ibis.literal(True))
    finish = NativeQueryFinish(
        stage.columns,
        (),
        None,
        NativeFrameTopFinish("__frame_top_present", tuple(integrity), tuple(witnesses)),
    )
    return (
        replace(
            stage,
            program=NativeRead((), finish),
            preparation_checks=tuple(
                (f"__frame_top_numeric_{index}", expected, received)
                for index, (name, _) in enumerate(read.numeric_checks)
                for original, expected, received in stage.preparation_checks
                if name == original
            ),
        ),
        replace(read, expression=bounded, numeric_checks=tuple(checks)),
    )


def bound_pending_frame_top(
    compiled: CompiledFramePlan[CheckedStageInputT],
    providers: Mapping[str, str],
    *,
    protected_inputs: frozenset[int] = frozenset(),
) -> CompiledFramePlan[CheckedStageInputT]:
    """Admit direct integer-field reads with one complete consumer."""
    plan = compiled.lowered.plan
    by_id = {stage.stage_id: stage for stage in plan.stages}
    nodes = dict(compiled.frame_operations)
    owners = dict(compiled.source_stages)
    boundaries = dict(compiled.source_outputs)
    decisions: list[RuleDecision] = []
    replacements: dict[str, SourceReadStage] = {}
    reads: dict[str, LoweredRead] = {}
    for top in plan.stages:
        if not (
            isinstance(top, FrameCompute)
            and isinstance(top.program, FrameProgram)
            and isinstance(top.program.operation, TopValues)
        ):
            continue
        node = nodes[top.stage_id]
        source = by_id[top.inputs[0]]
        schema = top.program.input_schema
        operation = top.program.operation
        if operation.n >= 2**63:
            decisions.append(
                RuleDecision(
                    "pending_frame_top",
                    "refused",
                    (top.stage_id,),
                    "Limit exceeds the native signed int64 row-count parameter.",
                )
            )
            continue
        reason = "the Top input is not one direct pending source field read"
        if (
            isinstance(node.inputs[0], SourceFrameInput)
            and isinstance(node.inputs[0].graph.root, ReadQuery)
            and id(node) not in protected_inputs
            and id(node.inputs[0]) not in protected_inputs
            and isinstance(source, SourceReadStage)
            and source.stage_id in owners
            and boundaries.get(source.stage_id) == owners[source.stage_id]
        ):
            reason = "this provider has no admitted native field-read Top route"
            provider = providers.get(owners[source.stage_id], "")
            dialect = query_dialect(provider)
            if dialect.supports_native_frame_top:
                reason = "the complete source input has multiple consumers or parameter inputs"
                if plan.consumers_of(source.stage_id) == (top.stage_id,) and not (
                    source.inputs or source.parameters
                ):
                    reason = "Top requires ordinary complete identity axes and a direct int64 field"
                    if (
                        schema.axes
                        and all(
                            is_instance(axis, DataAxis) and axis.kind == "member"
                            for axis in schema.axes
                        )
                        and native_frame_key_types(
                            schema, exact_strings=dialect.native_frame_string_keys_match_transport
                        )
                        and all(
                            column.dtype
                            in (
                                ScalarDataType("int64"),
                                ScalarDataType("float64"),
                                ScalarDataType("boolean"),
                                ScalarDataType("string"),
                            )
                            for column in schema.values
                        )
                        and isinstance(operation.score, ValueSlot)
                        and schema.values[operation.score.index].dtype == ScalarDataType("int64")
                        and isinstance(source.program, NativeRead)
                        and not source.program.reductions
                        and source.program.finish is None
                    ):
                        replacements[source.stage_id], reads[source.stage_id] = _bounded_read(
                            source,
                            compiled.lowered.read(source.stage_id),
                            schema,
                            operation,
                            provider,
                        )
                        decisions.append(
                            RuleDecision(
                                "pending_frame_top",
                                "adopted",
                                (source.stage_id, top.stage_id),
                                f"{provider} returns bounded rows after required full-input numeric and NULL summaries; proven complete keys omit duplicate aggregation; local Top restores order.",
                            )
                        )
                        continue
        decisions.append(RuleDecision("pending_frame_top", "refused", (top.stage_id,), reason))
    return replace(
        compiled,
        lowered=replace(
            compiled.lowered,
            plan=replace(
                plan,
                stages=tuple(replacements.get(stage.stage_id, stage) for stage in plan.stages),
                optimization_decisions=(*plan.optimization_decisions, *decisions),
            ),
            reads=tuple(reads.get(read.stage_id, read) for read in compiled.lowered.reads),
        ),
    )
