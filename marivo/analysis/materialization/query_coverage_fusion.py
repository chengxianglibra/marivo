"""Check complete output coverage with bounded native witnesses."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.relations import (
    population_keys,
)
from marivo._definition.types import AxisBinding
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.query_stages import (
    CommonRead,
    LocalCompute,
    RuleDecision,
    SourceReadStage,
    read_components,
    read_members,
    read_reduction,
)
from marivo.analysis.compiler.source_time import temporal_bound
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
)
from marivo.semantic.spi import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


from marivo.analysis.materialization.native_query_relation import (
    _physical_owner,
    native_query_relation,
)
from marivo.analysis.materialization.query_key_values import _same_key


def _coverage_domain(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    stage: LocalCompute,
    owner: SourceSession,
) -> ir.Table | None:
    """Represent the exact caller-selected tuples without uploading a key domain."""
    program = stage.program
    assert isinstance(program, FormulaProgram) and program.output_population is not None
    finite = population_keys(program.output_population)
    if finite is not None:
        columns = {
            column.slot_id: column for column in stage.columns if column.slot_id in stage.keys
        }
        unit = ibis.literal(True).name("__population_unit").as_table()
        rows: list[ir.Table] = []
        for row in finite:
            flattened = tuple(
                component
                for axis, value in zip(program.output_population.axes, row, strict=True)
                for component in (
                    value
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and isinstance(value, tuple)
                    else (value,)
                )
            )
            rows.append(
                unit.select(
                    **{
                        key: ibis.literal(value, type=ibis_type(columns[key].dtype))
                        for key, value in zip(stage.keys, flattened, strict=True)
                    }
                )
                if stage.keys
                else unit
            )
        if rows:
            return ibis.union(rows[0], *rows[1:])
        return (
            unit.select(
                **{key: ibis.null().cast(ibis_type(columns[key].dtype)) for key in stage.keys}
            )
            if stage.keys
            else unit
        ).filter(ibis.literal(False))
    binding = next(
        (item for item in program.populations if item.definition == program.output_population), None
    )
    if binding is None:
        return None
    native = native_query_relation(
        prepared,
        lowered,
        binding.stage_id,
        owner,
        shared=True,
        allowed_consumers=frozenset(item.stage_id for item in lowered.plan.stages),
    )
    if isinstance(native, str) or native.parameters:
        return None
    return native.expression.select(
        **dict(zip(stage.keys, (native.expression[key] for key in binding.keys), strict=True))
    )


def _required_coordinates(
    prepared: PreparedSources,
    lowered: LoweredQuery,
    source: SourceReadStage,
    keys: tuple[str, ...],
    owner: SourceSession,
) -> ir.Table | None:
    """Retain contribution coordinates before internal selection and output cropping."""
    read = lowered.read(source.stage_id)
    if source.parameters or not _physical_owner(read.expression, owner, prepared):
        return None
    expression = read.expression
    if any(key not in expression.columns for key in keys):
        return None
    marker = read.obligations.coverage_marker(len(keys))
    if marker is not None:
        expression = expression.filter(expression[marker].fill_null(False))
    if keys:
        return expression.select(*keys).distinct()
    if marker is not None:
        return expression.select(__population_unit=ibis.literal(True)).distinct()
    # An ungrouped reduction always returns one row, even without facts. Its
    # zero-axis coverage obligation comes from the actual filtered source rows.
    reduction = read_reduction(source)
    table = prepared.relations.get(source.stage_id, prepared.relations.get(source.source_id))
    if reduction is None or table is None or not _physical_owner(table, owner, prepared):
        return None
    if reduction.calendar or reduction.calendars or reduction.window_key is not None:
        return None
    if source.period is not None and not source.period.predicate_prepared:
        period = source.period
        time = table[period.column]
        table = table.filter(time < temporal_bound(time, period.end, period.timezone))
        if period.start is not None:
            table = table.filter(time >= temporal_bound(time, period.start, period.timezone))
    fields = {path: table[name] for path, name in source.field_columns}
    if source.predicates:
        table = table.filter(
            *(lower_condition(predicate, fields).value for predicate in source.predicates)
        )
    return table.select(__population_unit=ibis.literal(True)).distinct()


def _source_required_key_domains(stage: SourceReadStage) -> tuple[SourceReadStage, ...]:
    """Expose each reduction's required key domain inside a shared source read."""
    if isinstance(stage.program, CommonRead):
        return stage.program.members
    if read_members(stage) or read_components(stage) is not None:
        return ()
    return (stage,)


def _attach_coverage_witnesses(
    original: LoweredRead, missing: ir.Table, keys: tuple[str, ...], name: str
) -> ir.Table:
    witnesses = (
        missing.order_by(*(missing[key].asc(nulls_first=False) for key in keys)).limit(6)
        if keys
        else missing.limit(1)
    )
    expression = original.expression
    if original.obligations.numeric_presence is None:
        expression = expression.mutate(__numeric_present=ibis.literal(True))
    checks = tuple(alias for alias, _ in original.numeric_checks)
    failures = (
        expression.aggregate(
            **{alias: expression[alias].any().fill_null(False) for alias in checks}
        )
        if checks
        else None
    )
    diagnostics = witnesses
    if failures is not None:
        diagnostics = diagnostics.cross_join(failures)
    rows = expression.anti_join(witnesses, ibis.literal(True)).mutate(**{name: ibis.literal(False)})
    diagnostics = diagnostics.select(
        **{
            column: diagnostics[column]
            if column in keys or column in checks
            else ibis.literal(False)
            if expression[column].type().is_boolean()
            # The anti-join makes carrier columns nullable.
            else ibis.null().cast(rows[column].type())
            for column in expression.columns
        },
        **{name: ibis.literal(True)},
    )
    return rows.union(diagnostics)


def _fold_output_coverage(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Check complete output tuples at their existing source, with six witnesses."""
    for candidate in tuple(lowered.plan.stages):
        if not isinstance(candidate, LocalCompute) or not isinstance(
            candidate.program, FormulaProgram
        ):
            continue
        program = candidate.program
        if (
            program.output_population is None
            or program.population_checked
            or not program.values
            or program.calendar is not None
            or program.counts
        ):
            continue
        by_id = {stage.stage_id: stage for stage in lowered.plan.stages}
        sources: list[SourceReadStage] = []
        for binding in program.values:
            stage = by_id.get(binding.stage_id)
            if not isinstance(stage, SourceReadStage):
                break
            domains = _source_required_key_domains(stage)
            # A shared block needs member-specific support columns. Keep that
            # boundary until the shared-domain coverage pass handles them.
            if not domains or domains[0] is not stage:
                break
            if stage not in sources:
                sources.append(stage)
        else:
            owner = prepared.sources.get(sources[0].source_id, prepared.source)
            if not qualified_query_provider(owner) or any(
                prepared.sources.get(stage.source_id, prepared.source) is not owner
                for stage in sources
            ):
                continue
            domain = _coverage_domain(prepared, lowered, candidate, owner)
            if domain is None:
                continue
            required = tuple(
                _required_coordinates(prepared, lowered, stage, candidate.keys, owner)
                for stage in sources
            )
            if any(table is None for table in required):
                continue
            tables = tuple(table for table in required if table is not None)
            needed = ibis.union(tables[0], *tables[1:]) if len(tables) > 1 else tables[0]
            missing = needed.anti_join(
                domain,
                [_same_key(needed[key], domain[key], owner.provider.name) for key in candidate.keys]
                or ibis.literal(True),
            )
            carrier = sources[0]
            name = "__query_population_uncovered"
            expression = _attach_coverage_witnesses(
                lowered.read(carrier.stage_id), missing, candidate.keys, name
            )
            expected = "an output Population covering every required complete coordinate"
            received = "missing output coordinates"
            updated_source = replace(
                carrier,
                preparation_checks=(*carrier.preparation_checks, (name, expected, received)),
            )
            updated_program = replace(program, population_checked=True)
            stages = tuple(
                replace(candidate, program=updated_program)
                if stage.stage_id == candidate.stage_id
                else updated_source
                if stage.stage_id == carrier.stage_id
                else stage
                for stage in lowered.plan.stages
            )
            plan = replace(
                lowered.plan,
                stages=stages,
                optimization_decisions=(
                    *lowered.plan.optimization_decisions,
                    RuleDecision(
                        "source_output_population_coverage",
                        "adopted",
                        (candidate.stage_id, carrier.stage_id),
                        "Complete required tuples are checked with a NULL-safe source anti-join; failures return at most six keys.",
                    ),
                ),
            )
            lowered = LoweredQuery(
                plan,
                tuple(
                    replace(
                        read,
                        expression=expression,
                        numeric_checks=(
                            *read.numeric_checks,
                            (name, NumericCheck(expression[name], expected, received)),
                        ),
                    )
                    if read.stage_id == carrier.stage_id
                    else read
                    for read in lowered.reads
                ),
            )
    return lowered
