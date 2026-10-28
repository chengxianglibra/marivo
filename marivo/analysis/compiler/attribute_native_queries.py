"""Native coalition read plans and original-reducer lowering over selected rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, NoReturn

import ibis
import ibis.expr.types as ir

from marivo.analysis.compiler.graph_plan import (
    AttributeCoalitionRead,
    NativeEndpoint,
    ReductionRead,
    SourceRead,
    read_reduction,
)
from marivo.analysis.compiler.observation import _reduction_axes, lower_observation, temporal_bound
from marivo.analysis.errors import AnalysisStageUnavailableError
from marivo.semantic._numeric_lowering import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.compiler.frame_plan import CompiledSourceFrame
    from marivo.analysis.compiler.graph_lowering import LoweredRead
    from marivo.analysis.core.attribute_game import AttributeGameProgram


def native_failure(received: str) -> NoReturn:
    raise AnalysisStageUnavailableError(
        stage="attribute_native_binding",
        expected="compatible captured native source recipes for each paired reducer",
        received=received,
        repair="Use paired observations with compatible native datasource, input types and complete key scopes, or inspect the original observations. No replacement algorithm is substituted.",
        target="analysis_frame.attribute",
    )


def native_game_stage(
    stage_id: str,
    inputs: tuple[str, ...],
    program: AttributeGameProgram,
    endpoint_ids: tuple[str, str],
    source_plans: Mapping[str, CompiledSourceFrame],
) -> SourceRead:
    """Capture original leaf reads without carrying source handles into a core program."""
    endpoints = []
    dependencies = list(inputs)
    for side, input_id in enumerate(endpoint_ids):
        templates = source_plans[input_id].native_reads
        reads = []
        for occurrence, slot in program.original_value_slots[side]:
            read = next(
                (
                    item
                    for item in templates
                    if (r := read_reduction(item)) is not None and r.value_slot == slot
                ),
                None,
            )
            if read is None:
                native_failure(f"missing original source reduction for {occurrence!r}/{slot}")
                raise AssertionError("unreachable")
            reads.append((occurrence, read))
            for dependency in read.inputs:
                name = dependency
                if name not in dependencies:
                    dependencies.append(name)
        endpoints.append(NativeEndpoint(input_id, tuple(reads)))
    return SourceRead(
        stage_id,
        endpoint_ids[0],
        (),
        program.output_schema.columns,
        tuple(c.slot_id for axis in program.output_schema.axes for c in axis.columns),
        AttributeCoalitionRead(program, (endpoints[0], endpoints[1])),
        (),
        (),
        None,
        inputs=tuple(dependencies),
    )


def _selected_rows(
    stage: SourceRead, table: ir.Table, keys: tuple[tuple[object, ...], ...], prefix: str
) -> tuple[ir.Table, ir.Table | None, tuple[tuple[str, NumericCheck], ...]]:
    from marivo.analysis.compiler.predicate import lower_condition

    reduction = read_reduction(stage)
    assert reduction is not None
    original = table
    if stage.period is not None and not stage.period.predicate_prepared:
        time = table[stage.period.column]
        conditions = [time < temporal_bound(time, stage.period.end, stage.period.timezone)]
        if stage.period.start is not None:
            conditions.append(
                time >= temporal_bound(time, stage.period.start, stage.period.timezone)
            )
        table = table.filter(*conditions)
    fields = {path: table[name] for path, name in stage.field_columns}
    window_slot = None
    if reduction.window_key is not None:
        window_slot = "__native_window"
        fields[window_slot] = ibis.literal(reduction.window_key)
    axes = _reduction_axes(reduction, fields, window_slot)
    selected = (
        ibis.or_(
            *(
                ibis.and_(
                    *(
                        axes[slot].identical_to(ibis.literal(value, type=axes[slot].type()))
                        for slot, value in zip(stage.keys, key, strict=True)
                    )
                )
                for key in keys
            )
        )
        if keys
        else ibis.literal(False)
    )
    table = table.filter(selected)
    fields = {path: table[name] for path, name in stage.field_columns}
    predicates = tuple(lower_condition(node, fields) for node in stage.predicates)
    checks = tuple(check for item in predicates for check in item.checks)
    summaries = {
        f"{prefix}.predicate.{i}": check.violation.any().fill_null(False)
        for i, check in enumerate(checks)
    }
    summary = table.aggregate(**summaries) if summaries else None
    named = tuple((name, check) for name, check in zip(summaries, checks, strict=True))
    if stage.preparation_checks:
        prep = original.aggregate(
            **{
                f"{prefix}.preparation.{i}": original[name].any().fill_null(False)
                for i, (name, _, _) in enumerate(stage.preparation_checks)
            }
        )
        summary = prep if summary is None else summary.cross_join(prep)
        named = (
            *named,
            *(
                (
                    f"{prefix}.preparation.{i}",
                    NumericCheck(prep[f"{prefix}.preparation.{i}"], expected, received),
                )
                for i, (_, expected, received) in enumerate(stage.preparation_checks)
            ),
        )
    values = (
        *tuple(item.value for item in predicates),
        *(
            table[name]
            for path, name in stage.field_columns
            if path.startswith("__sample_assigned_")
        ),
    )
    return (table.filter(*values) if values else table), summary, named


def lower_native_coalition(
    current: SourceRead,
    baseline: SourceRead,
    current_table: ir.Table,
    baseline_table: ir.Table,
    current_keys: tuple[tuple[object, ...], ...],
    baseline_keys: tuple[tuple[object, ...], ...],
    *,
    stage_id: str,
) -> tuple[SourceRead, LoweredRead]:
    """Reduce the native UNION ALL with the original typed reducer and checks."""
    from marivo.analysis.compiler.graph_lowering import LoweredRead

    a, ac, ach = _selected_rows(current, current_table, current_keys, "current")
    b, bc, bch = _selected_rows(baseline, baseline_table, baseline_keys, "baseline")
    from marivo._definition.expressions import RowField
    from marivo._definition.graph import DefinitionNode, children
    from marivo._definition.types import field_read_key

    def paths(node: DefinitionNode) -> tuple[str, ...]:
        if isinstance(node, RowField):
            return (field_read_key(node.read) if node.read is not None else node.ref.ref.path,)
        return tuple(path for edge in children(node) for path in paths(edge.child))

    ar, br = read_reduction(current), read_reduction(baseline)
    assert ar is not None and br is not None
    ap, bp = paths(ar.node), paths(br.node)
    if len(ap) != len(bp):
        native_failure("paired original reducer operand roles differ")
    af, bf = dict(current.field_columns), dict(baseline.field_columns)
    roles = dict(zip(ap, bp, strict=True))
    names = {path: f"__native_field_{i}" for i, path in enumerate(roles)}
    for path, other in roles.items():
        if a[af[path]].type().copy(nullable=True) != b[bf[other]].type().copy(nullable=True):
            native_failure(
                f"paired field {path}: {a[af[path]].type()} versus {b[bf[other]].type()}"
            )
    rows = a.select(
        **({name: a[af[path]] for path, name in names.items()} or {"__native_row": ibis.literal(1)})
    ).union(
        b.select(
            **(
                {name: b[bf[roles[path]]] for path, name in names.items()}
                or {"__native_row": ibis.literal(1)}
            )
        ),
        distinct=False,
    )
    reduction = read_reduction(current)
    assert reduction is not None
    reduction = replace(
        reduction,
        axes=(),
        calendar=None,
        calendars=(),
        window_key=None,
        sampled=False,
        preserve_sample_presence=False,
        mean_fold=None,
    )
    expression, checks = lower_observation(reduction, rows, names, ())
    for summary in (ac, bc):
        if summary is not None:
            expression = expression.cross_join(summary)
    column = next(c for c in current.columns if c.slot_id == reduction.value_slot)
    stage = replace(
        current,
        stage_id=stage_id,
        columns=(column,),
        keys=(),
        program=ReductionRead(reduction),
        field_columns=tuple(names.items()),
        predicates=(),
        period=None,
        preparation_checks=(),
        inputs=(),
        parameters=(),
        attribute_state=False,
    )
    return stage, LoweredRead(stage_id, expression, (*checks, *ach, *bch))
