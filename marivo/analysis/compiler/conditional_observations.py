"""One grouped source scan with an independent predicate for each contribution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.observation import (
    CommonObservation,
    _project_common_aliases,
    _reduction_axes,
    _reduction_cse_key,
    _ReductionCSEKey,
    bind_reduction_calendars,
    common_slot,
)
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.query_stages import SourceReadStage, require_reduction
from marivo.analysis.compiler.source_aggregates import admit_source_aggregate
from marivo.analysis.model.data_types import DataColumn
from marivo.semantic.spi import NumericCheck, lower_source_reduction, source_contribution_count


@dataclass(frozen=True, slots=True)
class _BoundPredicate:
    condition: ir.BooleanValue | None
    checks: tuple[NumericCheck, ...]


def _bind_predicates(
    members: tuple[SourceReadStage, ...], fields: Mapping[str, ir.Value]
) -> tuple[_BoundPredicate, ...]:
    result: list[_BoundPredicate] = []
    for member in members:
        filters = tuple(lower_condition(node, fields) for node in member.predicates)
        condition = (
            ibis.and_(*(item.value for item in filters)).fill_null(False) if filters else None
        )
        assert condition is None or isinstance(condition, ir.BooleanValue)
        result.append(
            _BoundPredicate(condition, tuple(check for item in filters for check in item.checks))
        )
    return tuple(result)


def _admitted_rows(table: ir.Table, predicates: tuple[_BoundPredicate, ...]) -> ir.Table:
    conditions = tuple(item.condition for item in predicates if item.condition is not None)
    if len(conditions) != len(predicates):
        return table
    # A failure on an excluded row still belongs to the original predicate
    # domain. Include those witnesses before gating payloads and numeric checks.
    failures = tuple(check.violation for item in predicates for check in item.checks)
    return table.filter(ibis.or_(*conditions, *failures).fill_null(False))


def lower_conditional_observations(
    members: tuple[SourceReadStage, ...],
    table: ir.Table,
    columns: Mapping[str, str],
    value_columns: Mapping[str, DataColumn],
    *,
    cse: bool,
) -> CommonObservation:
    """Retain sparse support and independent physical diagnostic columns."""
    first = require_reduction(members[0])
    table, columns = bind_reduction_calendars(first, table, columns)
    fields = {path: table[name] for path, name in columns.items()}
    predicates = _bind_predicates(members, fields)
    source = _admitted_rows(table, predicates)
    if source is not table:
        fields = {path: source[name] for path, name in columns.items()}
        predicates = _bind_predicates(members, fields)
    axes = _reduction_axes(first, fields)
    metrics: dict[str, ir.Value] = {}
    checks: list[tuple[str, NumericCheck]] = []
    aliases: dict[str, str] = {}
    summaries: dict[ops.Value, str] = {}
    reductions: dict[_ReductionCSEKey, tuple[str, int]] = {}
    for index, (member, predicate) in enumerate(zip(members, predicates, strict=True)):
        reduction = require_reduction(member)
        admit_source_aggregate(reduction, fields)
        condition = predicate.condition
        value = lower_source_reduction(reduction.node, source, fields, where=condition)
        slot = reduction.value_slot
        key = _reduction_cse_key(reduction, value, value_columns[slot]) if cse else None
        matching = reductions.get(key) if key is not None else None
        if matching is None:
            matching = (slot, index)
            if key is not None:
                reductions[key] = matching
            metrics[slot] = ops.Cast(value.value, to=value.value.type()).to_expr()
            for position, check in enumerate(value.checks):
                metrics[common_slot(index, f"numeric_check.{position}")] = check.violation
        aliases[slot] = matching[0]
        for position, check in enumerate(value.checks):
            name = f"numeric_check.{position}"
            aliases[common_slot(index, name)] = common_slot(matching[1], name)
            checks.append((common_slot(index, name), check))
        for position, check in enumerate(predicate.checks):
            logical = common_slot(index, f"predicate_check.{position}")
            physical = summaries.setdefault(check.violation.op(), logical) if cse else logical
            aliases[logical] = physical
            if logical == physical:
                metrics[logical] = check.violation.any().fill_null(False)
            checks.append((logical, check))
        presence = common_slot(index, "__numeric_present")
        metrics[presence] = (
            source_contribution_count(source, condition) > 0 if axes else ibis.literal(True)
        )
        aliases[presence] = presence
    result = source.group_by(**axes).aggregate(**metrics) if axes else source.aggregate(**metrics)
    return CommonObservation(
        _project_common_aliases(result, tuple(axes), aliases),
        tuple(checks),
        len(aliases) - len(metrics),
    )
