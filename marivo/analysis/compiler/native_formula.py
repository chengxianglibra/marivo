"""Native composition of already completed ordinary source reductions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.relations import AttributeKeys, PopulationProgram
from marivo.analysis.compiler.observation import FormulaProgram
from marivo.analysis.compiler.source_predicates import (
    distinct_returned_keys,
    postgres_returned_text,
    same_returned_key,
)
from marivo.analysis.core.data_types import DurationDataType
from marivo.analysis.core.value_types import data_type, ibis_type
from marivo.semantic._numeric_lowering import (
    NumericCheck,
    NumericValue,
    force_cast,
    lower_metric_formula,
)


def normalized_returned_key(value: ir.Value, provider: str) -> ir.Value:
    """Normalize numeric key widths without casting away string padding."""
    if value.type().is_string() and provider == "postgres":
        return postgres_returned_text(value)
    dtype = ibis_type(data_type(value.type())).copy(nullable=value.type().nullable)
    if dtype != value.type():
        value = force_cast(value, dtype)
    # NULLIF(value, NULL) is an identity with a nullable result. Unlike a text
    # CAST, it preserves native CHAR padding before returned-byte comparisons.
    return value if value.type().nullable else value.nullif(ibis.null())


def align_native_inputs(
    tables: tuple[ir.Table, ...],
    keys: tuple[str, ...],
    zeros: tuple[str, ...],
    *,
    provider: str,
    alignment: Literal["outer", "domain"] = "outer",
) -> ir.Table:
    """Align completed root results; never combine their raw contribution rows."""
    tables = tuple(
        table.mutate(**{key: normalized_returned_key(table[key], provider) for key in keys})
        for table in tables
    )
    markers = {name: f"__formula_present_{index}" for index, name in enumerate(zeros)}
    tables = tuple(
        table.mutate(
            **{
                marker: ibis.literal(True)
                for name, marker in markers.items()
                if name in table.columns
            }
        )
        for table in tables
    )
    combined = tables[0]
    remaining = tables[1:]
    if alignment == "domain" and keys and remaining:
        domain = combined.select(*keys)
        for table in remaining:
            domain = distinct_returned_keys(
                domain.union(table.select(*keys), distinct=False), provider
            )
        combined, remaining = domain, tables
    for table in remaining:
        combined, table = combined.view(), table.view()
        joined = combined.join(
            table,
            [same_returned_key(combined[key], table[key], provider) for key in keys]
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
    if markers:
        combined = combined.mutate(
            **{
                name: combined[marker]
                .fill_null(False)
                .ifelse(combined[name], ibis.literal(0).cast(combined[name].type()))
                for name, marker in markers.items()
            }
        ).drop(*markers.values())
    return combined


def lower_native_formula(
    program: FormulaProgram,
    relations: Mapping[str, ir.Table],
    keys: tuple[str, ...],
    *,
    provider: str,
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
    grouped_tables: dict[ops.Relation, tuple[ir.Table, list[str]]] = {}
    for binding in program.values:
        if isinstance(binding.column.dtype, DurationDataType):
            return "Duration requires its exact tick finisher"
        table = relations.get(binding.stage_id)
        if table is None or binding.keys != keys:
            return "formula inputs have different complete coordinate grids"
        _, slots = grouped_tables.setdefault(table.op(), (table, []))
        if binding.column.slot_id not in slots:
            slots.append(binding.column.slot_id)
    combined = align_native_inputs(
        tuple(table.select(*keys, *names) for table, names in grouped_tables.values()),
        keys,
        tuple(binding.column.slot_id for binding in program.values if binding.zero_when_absent),
        provider=provider,
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
