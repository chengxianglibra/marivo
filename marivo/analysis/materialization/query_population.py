"""Prepare captured Query coordinate images without evaluating unused values."""

from __future__ import annotations

from collections.abc import Mapping

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import ObserveQuery, QueryKeys
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    FieldRead,
    coordinate_read_keys,
    field_read_key,
)
from marivo.analysis.compiler.observation import temporal_bound
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.population_preparation import (
    PreparedPopulation,
    prepare_population,
)
from marivo.analysis.materialization.query_preparation import prepare_fields
from marivo.datasource.timezone import probe_engine_timezone
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.metric_graph_lowering import lower_field_predicate


def query_population_entities(node: QueryKeys, state: CompiledSemanticState) -> tuple[str, ...]:
    from marivo._definition.query import ResultQuery
    from marivo.analysis.materialization.population_preparation import population_entities

    if isinstance(node.query, ResultQuery):
        return tuple(
            sorted(
                {
                    path
                    for item in node.query.inputs
                    for path in query_population_entities(item, state)
                }
            )
        )
    entities: set[str] = set()
    if node.query.population is not None:
        entities.update(population_entities(node.query.population, state))
    for contribution in node.observation.contributions if node.observation else ():
        entities.add(contribution.root.ref.path)
        for read in (*contribution.reads, *contribution.coordinates):
            entities.add(read.entity.ref.path)
            for step in read.path:
                relation = state.registry.relationships[step.ref.path]
                entities.update((relation.from_entity, relation.to_entity))
    return tuple(sorted(entities))


def prepare_query_keys(
    node: QueryKeys, entities: Mapping[str, ir.Table], state: CompiledSemanticState
) -> PreparedPopulation:
    """Keep the captured period, filters, paths and key domain of an observation."""
    assert isinstance(node.query, ObserveQuery)
    if node.query.population is not None:
        return prepare_population(node.query.population, entities, state)
    observation = node.observation
    if observation is None:
        reject(
            "a captured Query observation",
            "missing coordinate binding",
            "Use query.population() on a constructed Query.",
            "Query.population",
        )
    groups: list[tuple[str, ...]] = []
    for index, axis in enumerate(node.axes):
        width = (
            len(state.registry.entities[axis.entity.ref.path].primary_key)
            if isinstance(axis, AxisBinding) and axis.field is None
            else 1
        )
        groups.append(tuple(f"__population_{index}_{i}" for i in range(width)))
    columns = tuple(column for group in groups for column in group)
    tables: list[ir.Table] = []
    checks: list[tuple[str, str, str]] = []
    for ordinal, contribution in enumerate(observation.contributions):
        # Bound source times are normalized by the same field preparation owner.
        reader = probe_engine_timezone(entities[contribution.root.ref.path]._find_backend())
        prepared = prepare_fields(contribution, entities, state, reader)
        table = prepared.table
        fields = {path: table[column] for path, column in prepared.columns}
        predicates = (
            *contribution.intrinsic_conditions,
            *(
                (contribution.common_condition,)
                if contribution.common_condition is not None
                else ()
            ),
        )
        conditions = [lower_field_predicate(predicate, fields) for predicate in predicates]
        period = contribution.period
        if period is not None and contribution.time is not None:
            time = fields[
                field_read_key(
                    FieldRead(
                        contribution.time.field,
                        contribution.root,
                        contribution.time.path,
                        contribution.time.at,
                    )
                )
            ]
            table = table.filter(time < temporal_bound(time, period.end, period.timezone))
            if period.start is not None:
                table = table.filter(time >= temporal_bound(time, period.start, period.timezone))
        if conditions:
            table = table.filter(*(condition.value for condition in conditions))
        fields = {path: table[column] for path, column in prepared.columns}
        values: dict[str, ir.Value] = {}
        coordinate_index = 0
        for axis, group in zip(node.axes, groups, strict=True):
            if isinstance(axis, CalendarCoordinate):
                if contribution.window_key is not None:
                    values[group[0]] = ibis.literal(contribution.window_key)
                else:
                    assert contribution.time is not None
                    time = fields[
                        field_read_key(
                            FieldRead(
                                contribution.time.field,
                                contribution.root,
                                contribution.time.path,
                                contribution.time.at,
                            )
                        )
                    ]
                    bucket: ir.Value = ibis.null().cast("int64")
                    for index, (_, _, start, end) in reversed(tuple(enumerate(axis.cells))):
                        bucket = (
                            (time >= temporal_bound(time, start, axis.timezone))
                            & (time < temporal_bound(time, end, axis.timezone))
                        ).ifelse(index, bucket)
                    values[group[0]] = bucket
            else:
                read = contribution.coordinates[coordinate_index]
                coordinate_index += 1
                values.update(
                    (column, fields[key])
                    for column, key in zip(group, coordinate_read_keys(read), strict=True)
                )
        selected = table.select(**values).distinct()
        own_checks = [
            (f"__query_check_{ordinal}_{i}", check.expected, check.received, check.violation)
            for i, check in enumerate(
                check for condition in conditions for check in condition.checks
            )
        ]
        own_checks.extend(
            (f"__query_prepare_{ordinal}_{i}", expected, received, prepared.table[name])
            for i, (name, expected, received) in enumerate(prepared.checks)
        )
        if own_checks:
            summary = prepared.table.aggregate(
                **{name: violation.any().fill_null(False) for name, _, _, violation in own_checks}
            )
            selected = summary.left_join(
                selected.mutate(__population_present=ibis.literal(True)), ibis.literal(True)
            )
            checks.extend((name, expected, received) for name, expected, received, _ in own_checks)
        else:
            selected = selected.mutate(__population_present=ibis.literal(True))
        tables.append(selected)
    if not tables:
        reject(
            "source contributions or an explicit output Population",
            "a coordinate-free Query",
            "Construct a grouped observation before selecting its keys.",
            "Query.population",
        )
    all_checks = {name for name, _, _ in checks}
    tables = [
        table.mutate(
            **{name: ibis.literal(False) for name in all_checks if name not in table.columns}
        )
        for table in tables
    ]
    union_columns = (*columns, *sorted(all_checks), "__population_present")
    combined = tables[0].select(*union_columns)
    for table in tables[1:]:
        combined = combined.union(table.select(*union_columns), distinct=True)
    summary = (
        combined.aggregate(**{name: combined[name].any().fill_null(False) for name in all_checks})
        if checks
        else None
    )
    combined = (
        combined.filter(combined.__population_present.fill_null(False)).select(*columns).distinct()
    )
    calendar = next(
        (
            (axis, group[0])
            for axis, group in zip(node.axes, groups, strict=True)
            if isinstance(axis, CalendarCoordinate)
        ),
        None,
    )
    if calendar is not None:
        axis, name = calendar
        buckets = ibis.literal(0).cast("int64").name(name).as_table()
        if not axis.cells:
            buckets = buckets.filter(ibis.literal(False))
        for index in range(1, len(axis.cells)):
            buckets = buckets.union(ibis.literal(index).cast("int64").name(name).as_table())
        field_columns = tuple(column for column in columns if column != name)
        if field_columns:
            combined = combined.select(*field_columns).distinct().cross_join(buckets)
        else:
            combined = buckets
    if observation.coordinates.population is not None:
        # Retain the original observation's coverage obligation even when only
        # its keys are consumed. Returning the requested domain alone would hide
        # observed coordinates that it omitted, and discard predicate checks.
        complete = prepare_population(observation.coordinates.population, entities, state)
        unmatched = combined.anti_join(
            complete.table,
            [
                combined[left].identical_to(complete.table[right])
                for left, right in zip(columns, complete.key_columns, strict=True)
            ],
        )
        coverage_name = "__query_population_uncovered"
        coverage = unmatched.aggregate(**{coverage_name: unmatched.count() > 0})
        summary = coverage if summary is None else summary.cross_join(coverage)
        checks.append(
            (
                coverage_name,
                "an output Population covering every observed coordinate",
                "observed coordinates outside captured by=Population",
            )
        )
        presence = "__query_complete_present"
        selected = complete.table.mutate(
            **{
                presence: complete.table[complete.presence].fill_null(False)
                if complete.presence is not None
                else ibis.literal(True)
            }
        )
        result = summary.left_join(selected, ibis.literal(True))
        return PreparedPopulation(
            result, complete.key_groups, (*checks, *complete.checks), presence
        )
    if summary is not None:
        combined = summary.left_join(
            combined.mutate(__numeric_present=ibis.literal(True)), ibis.literal(True)
        )
    return PreparedPopulation(
        combined, tuple(groups), tuple(checks), "__numeric_present" if checks else None
    )
