"""Qualify single-owner field selection as a native Query-key subquery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pandas as pd

from marivo._definition.expressions import MemberCount, PerReduce, PerWeightedMean
from marivo._definition.graph import walk
from marivo._definition.query import (
    CoordinateSlot,
    FilterValues,
    KeyImage,
    PopulationValues,
    ProjectKeys,
    ReadFields,
    SelectValues,
    ValueBinary,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
    ValueUnary,
)
from marivo.analysis.compiler.graph_lowering import LoweredQuery, LoweredRead
from marivo.analysis.compiler.graph_plan import LocalCompute, QueryPlan, QueryStage, SourceRead
from marivo.analysis.compiler.optimization import RuleDecision, required_slots
from marivo.analysis.compiler.queries import expression_value
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.materialization.query_parameters import _replace_parameter
from marivo.semantic._numeric_lowering import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.materialization.graph_source_execution import PreparedSources
    from marivo.datasource.adapters import SourceSession


def _postgres_json(value: str) -> object:
    raise NotImplementedError("native PostgreSQL to_json")


def _postgres_encode(value: str, encoding: str) -> bytes:
    raise NotImplementedError("native PostgreSQL convert_to")


def _mysql_quote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_QUOTE")


def _mysql_unquote(value: str) -> str:
    raise NotImplementedError("native MySQL JSON_UNQUOTE")


def _mysql_hex(value: str) -> str:
    raise NotImplementedError("native MySQL HEX")


def _mysql_unhex(value: str) -> bytes:
    raise NotImplementedError("native MySQL UNHEX")


_POSTGRES_JSON: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="to_json", signature=(("string",), "json")
)(_postgres_json)
_POSTGRES_ENCODE: Callable[[ir.Value, str], ir.Value] = ibis.udf.scalar.builtin(
    name="convert_to", signature=(("string", "string"), "binary")
)(_postgres_encode)
_MYSQL_QUOTE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="JSON_QUOTE", signature=(("string",), "string")
)(_mysql_quote)
_MYSQL_UNQUOTE: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="JSON_UNQUOTE", signature=(("string",), "string")
)(_mysql_unquote)
_MYSQL_HEX: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="HEX", signature=(("string",), "string")
)(_mysql_hex)
_MYSQL_UNHEX: Callable[[ir.Value], ir.Value] = ibis.udf.scalar.builtin(
    name="UNHEX", signature=(("string",), "binary")
)(_mysql_unhex)


def _string_bytes(value: ir.Value, provider: str) -> ir.Value:
    if provider == "postgres":
        # JSON preserves CHAR padding as returned to the reader; CHAR -> text does not.
        if not isinstance(value.op(), ops.Literal):
            encoded = _POSTGRES_JSON(value)
            assert isinstance(encoded, ir.JSONValue)
            value = encoded.unwrap_as("str")
        result = _POSTGRES_ENCODE(value, "UTF8")
        assert isinstance(result, ir.Value)
        return result
    if provider == "mysql":
        # JSON strings normalize source character sets before byte comparison.
        result = _MYSQL_UNHEX(_MYSQL_HEX(_MYSQL_UNQUOTE(_MYSQL_QUOTE(value))))
        assert isinstance(result, ir.Value)
        return result
    return value


def _predicate_value(
    value: ValueExpression,
    values: tuple[ir.Value, ...],
    coordinates: tuple[ir.Value, ...],
    provider: str,
) -> ir.Value:
    if isinstance(value, ValueBinary):
        left = _predicate_value(value.left, values, coordinates, provider)
        right = _predicate_value(value.right, values, coordinates, provider)
        if (
            value.operation in ("eq", "ne", "lt", "le", "gt", "ge")
            and left.type().is_string()
            and right.type().is_string()
        ):
            left, right = _string_bytes(left, provider), _string_bytes(right, provider)
        return expression_value(
            ValueBinary(value.operation, ValueSlot(0), ValueSlot(1)), (left, right), ()
        )
    if isinstance(value, ValueUnary):
        operand = _predicate_value(value.operand, values, coordinates, provider)
        return expression_value(ValueUnary(value.operation, ValueSlot(0)), (operand,), ())
    return expression_value(value, values, coordinates)


@dataclass(frozen=True, slots=True)
class _NativeKeys:
    expression: ir.Table
    stages: frozenset[str]
    sources: tuple[LoweredRead, ...]


def _consumers(stages: tuple[QueryStage, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (stage.stage_id, tuple(item.stage_id for item in stages if stage.stage_id in item.inputs))
        for stage in stages
    )


def _prune_transport(plan: QueryPlan, producer: str) -> tuple[QueryPlan, tuple[str, ...]]:
    """Drop only an unused published formula's redundant population transport."""
    final = next(stage for stage in plan.stages if stage.stage_id == plan.publish)
    if not isinstance(final, LocalCompute) or final.program is None:
        return plan, ()
    program = final.program
    if program.output_population is not None or program.members is not None:
        return plan, ()
    closure = walk((node for _, node in program.outputs), resolve=dict(program.declared).get)
    if any(isinstance(node, (MemberCount, PerReduce, PerWeightedMean)) for node in closure.order):
        return plan, ()
    by_id = {stage.stage_id: stage for stage in plan.stages}
    removed = tuple(
        binding.stage_id
        for binding in program.populations
        if isinstance((stage := by_id[binding.stage_id]), LocalCompute)
        and stage.result is not None
        and isinstance(stage.result.operation, PopulationValues)
        and stage.inputs == (producer,)
        and plan.consumers_of(stage.stage_id) == (final.stage_id,)
        and not any(check.stage_id == stage.stage_id for check in plan.checks)
    )
    if not removed:
        return plan, ()
    final = replace(
        final,
        inputs=tuple(value for value in final.inputs if value not in removed),
        program=replace(
            program,
            populations=tuple(
                value for value in program.populations if value.stage_id not in removed
            ),
        ),
    )
    stages = tuple(
        final if stage.stage_id == final.stage_id else stage
        for stage in plan.stages
        if stage.stage_id not in removed
    )
    return replace(plan, stages=stages, consumers=_consumers(stages)), removed


def _safe_expression(value: ValueExpression) -> bool:
    if isinstance(value, (ValueSlot, CoordinateSlot, ValueLiteral)):
        return not isinstance(value, ValueLiteral) or not (
            isinstance(value.value, (float, timedelta))
            or (isinstance(value.value, str) and "\x00" in value.value)
            or (isinstance(value.value, pd.Timestamp) and value.value.nanosecond != 0)
        )
    if isinstance(value, ValueUnary):
        return value.operation in ("not", "is_null") and _safe_expression(value.operand)
    if isinstance(value, ValueBinary):
        return (
            value.operation in ("eq", "ne", "lt", "le", "gt", "ge", "and", "or")
            and _safe_expression(value.left)
            and _safe_expression(value.right)
        )
    return False


def _physical_owner(expression: ir.Table, owner: SourceSession, prepared: PreparedSources) -> bool:
    physical = (ops.DatabaseTable, ops.UnboundTable, ops.InMemoryTable, ops.SQLQueryResult)
    allowed = {
        leaf
        for path, binding in prepared.bound.items()
        if prepared.sources.get(path, prepared.source) is owner
        for leaf in binding.relation.op().find(physical)
    }
    leaves = set(expression.op().find(physical))
    return bool(leaves) and leaves <= allowed


def _native_keys(
    lowered: LoweredQuery,
    producer: str,
    consumer: str,
    owner: SourceSession,
    prepared: PreparedSources,
) -> _NativeKeys | str:
    """Lower only field relations and direct nullable selections; no numeric finish."""
    plan = lowered.plan
    by_id = {stage.stage_id: stage for stage in plan.stages}
    checked = {check.stage_id for check in plan.checks}

    def lower(identifier: str, parent: str) -> _NativeKeys | str:
        stage = by_id[identifier]
        if plan.consumers_of(identifier) != (parent,) or identifier == plan.publish:
            return "producer has shared consumers or a publication boundary"
        if identifier in checked:
            return "producer has a pending planned check"
        if isinstance(stage, SourceRead):
            read = lowered.read(identifier)
            if prepared.sources.get(
                stage.source_id, prepared.source
            ) is not owner or not _physical_owner(read.expression, owner, prepared):
                return "producer physical inputs do not belong to the consumer SourceSession"
            admitted = {"__identity_null", "__population_classify_invalid"}
            if any(column.dtype == ScalarDataType("float64") for column in stage.columns):
                return "floating producer slots retain pandas missing-value semantics"
            if (
                stage.inputs
                or stage.parameters
                or stage.reduction is not None
                or stage.common_reads
                or stage.component_layout is not None
                or any(name not in admitted for name, _, _ in stage.preparation_checks)
                or any(name not in admitted for name, _ in read.numeric_checks)
                or {name for name, _ in read.numeric_checks}
                != {name for name, _, _ in stage.preparation_checks}
            ):
                return "producer retains an aggregate, numeric finish or unsupported check"
            expression = read.expression
            if "__numeric_present" in expression.columns:
                expression = expression.filter(expression["__numeric_present"].fill_null(False))
            expression = expression.select(*(column.slot_id for column in stage.columns))
            return _NativeKeys(expression, frozenset({identifier}), (read,))
        if stage.fused_steps:
            return "producer retains a registered local fusion boundary"
        inputs: list[_NativeKeys] = []
        for dependency in stage.inputs:
            result = lower(dependency, identifier)
            if isinstance(result, str):
                return result
            inputs.append(result)
        sources = tuple(
            {item.stage.stage_id: item for value in inputs for item in value.sources}.values()
        )
        members = frozenset({identifier}).union(*(value.stages for value in inputs))
        if stage.program is not None:
            formula = stage.program
            if (
                formula.values
                or formula.outputs
                or formula.calendar is not None
                or formula.members is not None
                or formula.output_population is None
                or tuple(column.slot_id for column in stage.columns) != stage.keys
            ):
                return "producer formula is not a field-only complete population"
            selected = next(
                (
                    item
                    for item in formula.populations
                    if item.definition == formula.output_population
                ),
                None,
            )
            if selected is None:
                return "producer formula has no exact complete population input"
            expression = inputs[stage.inputs.index(selected.stage_id)].expression
            expression = expression.select(
                **dict(zip(stage.keys, (expression[key] for key in selected.keys), strict=True))
            ).distinct()
            return _NativeKeys(expression, members, sources)
        program = stage.result
        if (
            program is None
            or len(inputs) != 1
            or program.references
            or program.duration_operations
            or program.drills
            or program.original is not None
        ):
            return "producer is outside the admitted field relation grammar"
        expression = inputs[0].expression
        operation = program.operation
        if isinstance(operation, ReadFields):
            columns = (
                *program.key_groups[0],
                *(program.key_groups[i][0] for i in operation.positions),
            )
            expression = expression.select(
                **dict(
                    zip(
                        (column.slot_id for column in stage.columns),
                        (expression[name] for name in columns),
                        strict=True,
                    )
                )
            )
        elif isinstance(operation, KeyImage):
            expression = expression.select(
                **dict(
                    zip(
                        (column.slot_id for column in stage.columns),
                        (expression[name] for name in program.keys),
                        strict=True,
                    )
                )
            )
        elif isinstance(operation, ProjectKeys) and all(path is None for path in operation.paths):
            columns = tuple(
                key for index in operation.positions for key in program.key_groups[index]
            )
            expression = expression.select(
                **dict(
                    zip(
                        (column.slot_id for column in stage.columns),
                        (expression[name] for name in columns),
                        strict=True,
                    )
                )
            ).distinct()
        elif isinstance(operation, (FilterValues, SelectValues)):
            values = tuple(
                expression[column.slot_id]
                for column in (
                    program.input_layout.values
                    if program.input_layout is not None
                    else tuple(
                        column for column in program.columns if column.slot_id not in program.keys
                    )
                )
            )
            if any(len(group) != 1 for group in program.key_groups):
                coordinates = ()
            else:
                coordinates = tuple(expression[group[0]] for group in program.key_groups)
            if isinstance(operation, FilterValues):
                if not isinstance(operation.predicate, ValueExpression) or not _safe_expression(
                    operation.predicate
                ):
                    return "producer filter has nonlocal or numeric expression obligations"
                condition = _predicate_value(
                    operation.predicate, values, coordinates, owner.provider.name
                )
                if not isinstance(condition, ir.BooleanValue):
                    return "producer filter is not a boolean value"
                expression = expression.filter(condition.fill_null(False)).select(
                    *(column.slot_id for column in stage.columns)
                )
            else:
                if any(
                    not isinstance(value, (ValueSlot, ValueLiteral)) or not _safe_expression(value)
                    for _, value in operation.values
                ) or any(column.dtype == ScalarDataType("float64") for column in stage.columns):
                    return "producer select retains a numeric finish or computed value"
                projection = {key: expression[key] for key in program.keys}
                projection.update(
                    (f"value.{index}", expression_value(value, values, coordinates))
                    for index, (_, value) in enumerate(operation.values)
                )
                expression = expression.select(**projection)
        else:
            return "producer operation is outside the admitted field relation grammar"
        return _NativeKeys(expression, members, sources)

    return lower(producer, consumer)


def fuse_query_keys(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Replace an eligible single-consumer key transport with its native subquery."""
    decisions = list(lowered.plan.optimization_decisions)
    for original in lowered.reads:
        for parameter in original.stage.parameters:
            current = lowered.read(original.stage.stage_id)
            owner = prepared.sources.get(current.stage.source_id, prepared.source)
            pair = (parameter.stage_id, current.stage.stage_id)
            if sum(value.stage_id == parameter.stage_id for value in current.stage.parameters) != 1:
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "producer supplies multiple parameter uses in the same consumer",
                    )
                )
                continue
            candidate, pruned = _prune_transport(lowered.plan, parameter.stage_id)
            native: _NativeKeys | str = "selected provider has no qualified source-key fusion route"
            if owner.provider.name in ("duckdb", "postgres", "mysql", "trino", "clickhouse"):
                native = _native_keys(replace(lowered, plan=candidate), *pair, owner, prepared)
            if isinstance(native, str):
                decisions.append(RuleDecision("source_key_fusion", "refused", pair, native))
                continue
            producer = next(
                stage for stage in candidate.stages if stage.stage_id == parameter.stage_id
            )
            if len(producer.keys) != len(parameter.columns):
                decisions.append(
                    RuleDecision(
                        "source_key_fusion",
                        "refused",
                        pair,
                        "complete key width differs from the planned parameter",
                    )
                )
                continue
            keys = native.expression.select(
                **dict(
                    zip(
                        (column.slot_id for column in parameter.columns),
                        (native.expression[key] for key in producer.keys),
                        strict=True,
                    )
                )
            ).distinct()
            expression = _replace_parameter(current.expression, parameter.placeholder, keys)
            preparation = list(current.stage.preparation_checks)
            checks = list(current.numeric_checks)
            for source in native.sources:
                aliases = tuple(
                    (
                        name,
                        f"__key_preparation_{len(preparation) + index}_{name.lstrip('_')}",
                        expected,
                        received,
                    )
                    for index, (name, expected, received) in enumerate(
                        source.stage.preparation_checks
                    )
                )
                if not aliases:
                    continue
                summary = source.expression.aggregate(
                    **{
                        renamed: source.expression[name].any().fill_null(False)
                        for name, renamed, _, _ in aliases
                    }
                )
                if "__numeric_present" not in expression.columns:
                    expression = expression.mutate(__numeric_present=ibis.literal(True))
                expression = summary.left_join(expression, ibis.literal(True))
                for _, renamed, expected, received in aliases:
                    preparation.append((renamed, expected, received))
                    checks.append((renamed, NumericCheck(summary[renamed], expected, received)))
            parameters = tuple(value for value in current.stage.parameters if value != parameter)
            stage = replace(
                current.stage,
                inputs=tuple(
                    value for value in current.stage.inputs if value != parameter.stage_id
                ),
                parameters=parameters,
                preparation_checks=tuple(preparation),
            )
            removed = native.stages | frozenset(pruned)
            stages = tuple(
                stage if item.stage_id == stage.stage_id else item
                for item in candidate.stages
                if item.stage_id not in removed
            )
            decisions.append(
                RuleDecision(
                    "source_key_fusion",
                    "adopted",
                    (*sorted(removed), stage.stage_id),
                    "Same SourceSession field selection stays in the null-safe membership subquery; full-scope identity/classification checks are retained.",
                )
            )
            if pruned:
                decisions.append(
                    RuleDecision(
                        "dead_population_transport",
                        "adopted",
                        pruned,
                        "Published formula consumes no output/member/sample domain and retains no downstream original state.",
                    )
                )
            plan = replace(
                candidate,
                stages=stages,
                consumers=_consumers(stages),
                fusion_groups=tuple(
                    group for group in candidate.fusion_groups if group.output not in removed
                ),
            )
            plan = replace(plan, required_slots=required_slots(plan))
            read = replace(
                current, stage=stage, expression=expression, numeric_checks=tuple(checks)
            )
            lowered = LoweredQuery(
                plan,
                tuple(
                    read if item.stage.stage_id == stage.stage_id else item
                    for item in lowered.reads
                    if item.stage.stage_id not in removed
                ),
            )
    return replace(lowered, plan=replace(lowered.plan, optimization_decisions=tuple(decisions)))
