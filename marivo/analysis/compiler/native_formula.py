"""Native composition of already completed ordinary source reductions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import AttributeKeys, PopulationProgram
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.core.data_types import DurationDataType
from marivo.semantic._numeric_lowering import NumericCheck, NumericValue, lower_metric_formula


def align_native_inputs(
    tables: tuple[ir.Table, ...],
    keys: tuple[str, ...],
    zeros: tuple[str, ...],
    *,
    alignment: Literal["outer", "domain"] = "outer",
) -> ir.Table:
    """Align completed root results; never combine their raw contribution rows."""
    combined = tables[0]
    remaining = tables[1:]
    if alignment == "domain" and keys and remaining:
        domain = combined.select(*keys)
        for table in remaining:
            domain = domain.union(table.select(*keys), distinct=True)
        combined, remaining = domain, tables
    for table in remaining:
        combined, table = combined.view(), table.view()
        joined = combined.join(
            table,
            [combined[key].identical_to(table[key]) for key in keys]
            if keys
            else ibis.literal(True),
            how=("outer" if alignment == "outer" else "left") if keys else "inner",
        )
        combined = joined.select(
            **{
                **{key: ibis.coalesce(combined[key], table[key]) for key in keys},
                **{name: combined[name] for name in combined.columns if name not in keys},
                **{name: table[name] for name in table.columns if name not in keys},
            }
        )
    return (
        combined.mutate(**{name: combined[name].fill_null(0) for name in zeros})
        if zeros
        else combined
    )


def lower_native_formula(
    program: FormulaProgram,
    relations: Mapping[str, ir.Table],
    keys: tuple[str, ...],
    *,
    alignment: Literal["outer", "domain"] = "outer",
) -> tuple[ir.Table, tuple[tuple[str, NumericCheck], ...]] | str:
    """Compose qualified completed reductions with the owning numeric lowerer.

    The caller retains the input reducers' complete checks before applying any
    selection. Native arithmetic is deliberately separate from local finishers.
    """
    if program.calendar or program.output_population or program.counts:
        return "formula retains a calendar, sample or explicit output domain"
    parent: ir.Table | None = None
    if program.populations or program.parent_scope is not None:
        scope = program.parent_scope
        if (
            not isinstance(scope, PopulationProgram)
            or not isinstance(scope.root, AttributeKeys)
            or scope.root.calendar_token is not None
            or len(program.populations) != 1
            or program.populations[0].definition != scope
        ):
            return "formula retains a non-Attribute or additional Population domain"
        parent_binding = program.populations[0]
        parent = relations.get(parent_binding.stage_id)
        if (
            parent is None
            or len(parent_binding.keys) != len(program.parent_slots)
            or len(set(program.parent_slots)) != len(program.parent_slots)
            or any(slot not in keys for slot in program.parent_slots)
            or any(slot not in parent.columns for slot in parent_binding.keys)
        ):
            return "formula has no exact complete Attribute parent relation"
    if not program.values:
        return "formula has no ordinary source reductions"
    tables: list[ir.Table] = []
    for binding in program.values:
        if isinstance(binding.column.dtype, DurationDataType):
            return "Duration requires its exact tick finisher"
        table = relations.get(binding.stage_id)
        if table is None or binding.keys != keys:
            return "formula inputs have different complete coordinate grids"
        tables.append(table.select(*keys, binding.column.slot_id))
    combined = align_native_inputs(
        tuple(tables),
        keys,
        tuple(binding.column.slot_id for binding in program.values if binding.empty_zero),
        alignment=alignment,
    )
    if parent is not None:
        combined, parent = combined.view(), parent.view()
        combined = combined.semi_join(
            parent,
            [
                combined[slot].identical_to(parent[key])
                for slot, key in zip(program.parent_slots, program.populations[0].keys, strict=True)
            ]
            or ibis.literal(True),
        )
    values = {
        binding.occurrence: NumericValue(combined[binding.column.slot_id])
        for binding in program.values
    }
    outputs = tuple(
        lower_metric_formula(node, (index,), values, dict(program.declared))
        for index, (_, node) in enumerate(program.outputs)
    )
    checks = tuple(check for value in outputs for check in value.checks)
    names = tuple(f"__formula_check.{index}" for index in range(len(checks)))
    return combined.select(
        *keys,
        **{f"value.{index}": value.value for index, value in enumerate(outputs)},
        **{name: check.violation for name, check in zip(names, checks, strict=True)},
    ), tuple(zip(names, checks, strict=True))
