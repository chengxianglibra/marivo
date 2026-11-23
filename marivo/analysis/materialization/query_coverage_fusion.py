"""Check complete output coverage with bounded native witnesses."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import ibis
import ibis.expr.types as ir

from marivo._definition.graph import definition_fingerprint
from marivo._definition.relations import (
    population_keys,
)
from marivo._definition.types import AxisBinding
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.observation import common_slot
from marivo.analysis.compiler.predicate import lower_condition
from marivo.analysis.compiler.query_stages import (
    CommonRead,
    LocalCompute,
    PeriodFilter,
    ReductionRead,
    RuleDecision,
    SourceReadStage,
    read_components,
    read_members,
    read_reduction,
)
from marivo.analysis.compiler.source_predicates import distinct_returned_keys
from marivo.analysis.compiler.source_time import temporal_bound
from marivo.analysis.core.value_types import ibis_type
from marivo.analysis.materialization.source_query_policy import (
    qualified_query_provider,
)
from marivo.analysis.model.slot_roles import StageObligations
from marivo.semantic.spi import NumericCheck

if TYPE_CHECKING:
    from marivo.analysis.materialization.prepared_sources import PreparedSources
    from marivo.datasource.spi import SourceSession


from marivo.analysis.materialization.native_query_relation import (
    _physical_owner,
    native_query_relation,
)
from marivo.analysis.materialization.query_key_values import _same_key


@dataclass(frozen=True, slots=True)
class _CoverageRead:
    source: SourceReadStage
    member: SourceReadStage
    block_index: int | None = None

    def marker(self, read: LoweredRead, width: int) -> str | None:
        if self.block_index is None:
            return read.obligations.coverage_marker(width)
        suffix = common_slot(self.block_index, "")
        member_slots = StageObligations.from_aliases(
            tuple(
                name.removesuffix(suffix)
                for name in read.expression.columns
                if name.endswith(suffix)
            )
        )
        marker = member_slots.coverage_marker(width)
        return None if marker is None else common_slot(self.block_index, marker)


@dataclass(frozen=True, slots=True)
class CoverageDomainKey:
    """Exact support identity, separate from the Metric finisher."""

    source_id: str
    binding_id: str
    period: PeriodFilter | None
    predicates: str
    axes_and_scope: str
    keys: tuple[str, ...]


def _coverage_domain_key(stage: SourceReadStage, keys: tuple[str, ...]) -> CoverageDomainKey | None:
    if not isinstance(stage.program, ReductionRead) or not stage.binding_id or stage.parameters:
        return None
    reduction = stage.program.reduction
    fields = stage.field_columns
    if (
        not stage.predicates
        and reduction.calendar is None
        and not reduction.calendars
        and not reduction.sampled
        and not reduction.preserve_sample_presence
        and reduction.window_key is None
        and not any(path.startswith("__sample_assigned_") for path, _ in fields)
    ):
        # Plain grouped support reads coordinates and row presence.
        fields = tuple((path, dict(fields)[path]) for _, path in reduction.axes)
    # The prepared binding already includes membership allocations, row
    # dependencies, time authorities and sample scopes.
    return CoverageDomainKey(
        stage.source_id,
        stage.binding_id,
        stage.period,
        definition_fingerprint(stage.predicates),
        definition_fingerprint(
            (
                reduction.axes,
                reduction.calendar,
                reduction.calendars,
                reduction.window_key,
                reduction.window_coordinates,
                reduction.time_column,
                reduction.sampled,
                reduction.sample_calendar_reads,
                reduction.preserve_sample_presence,
                fields,
                stage.preparation_checks,
            )
        ),
        keys,
    )


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
    member: _CoverageRead,
    keys: tuple[str, ...],
    owner: SourceSession,
) -> ir.Table | None:
    """Retain contribution coordinates before internal selection and output cropping."""
    source = member.member
    read = lowered.read(member.source.stage_id)
    if source.parameters or not _physical_owner(read.expression, owner, prepared):
        return None
    expression = read.expression
    if any(key not in expression.columns for key in keys):
        return None
    marker = member.marker(read, len(keys))
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


def _source_required_key_domains(
    stage: SourceReadStage, keys: tuple[str, ...], *, sharing: bool
) -> tuple[_CoverageRead, ...]:
    """Expose an exact support relation for each qualified logical reduction."""
    if isinstance(stage.program, CommonRead):
        if not sharing or not keys or stage.keys != keys:
            return ()
        domains = tuple(_coverage_domain_key(member, keys) for member in stage.program.members)
        if len(set(domains)) != 1 or domains[0] is None:
            # Conditional/heterogeneous blocks must keep each branch's own
            # receipt until their exact support relation has been qualified.
            return ()
        return tuple(
            _CoverageRead(stage, member, index)
            for index, member in enumerate(stage.program.members)
        )
    if read_members(stage) or read_components(stage) is not None:
        return ()
    return (_CoverageRead(stage, stage),)


def _shared_domains(
    members: tuple[_CoverageRead, ...], keys: tuple[str, ...]
) -> tuple[tuple[_CoverageRead, ...], tuple[str, ...]]:
    """Select one witness per exact key domain in original occurrence order."""
    seen: dict[CoverageDomainKey, _CoverageRead] = {}
    retained: list[_CoverageRead] = []
    repeated: dict[str, None] = {}
    for member in members:
        key = _coverage_domain_key(member.member, keys)
        if key is None or key not in seen:
            retained.append(member)
            if key is not None:
                seen[key] = member
        else:
            repeated[seen[key].member.stage_id] = None
            repeated[member.member.stage_id] = None
    return tuple(retained), tuple(repeated)


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


def _with_coverage_witnesses(
    lowered: LoweredQuery,
    candidate: LocalCompute,
    program: FormulaProgram,
    carrier: SourceReadStage,
    missing: ir.Table,
    selected: tuple[_CoverageRead, ...],
    repeated: tuple[str, ...],
    *,
    sharing: bool,
) -> LoweredQuery:
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
    decisions = [
        RuleDecision(
            "source_output_population_coverage",
            "adopted",
            (candidate.stage_id, carrier.stage_id),
            "Complete required tuples are checked with a NULL-safe source anti-join; failures return at most six keys.",
        )
    ]
    if repeated:
        decisions.append(
            RuleDecision(
                "shared_output_coverage_check",
                "adopted",
                repeated,
                "The exact bound source, predicate and complete coordinate domains share one coverage witness.",
            )
        )
    if sharing and len(selected) > 1:
        decisions.append(
            RuleDecision(
                "shared_output_coverage_check",
                "refused",
                tuple(item.member.stage_id for item in selected),
                "Distinct or unproven support domains retain their own required coordinates.",
            )
        )
    plan = replace(
        lowered.plan,
        stages=stages,
        optimization_decisions=(*lowered.plan.optimization_decisions, *decisions),
    )
    return LoweredQuery(
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


def _fold_output_coverage(prepared: PreparedSources, lowered: LoweredQuery) -> LoweredQuery:
    """Check complete output tuples at their existing source, with six witnesses."""
    sharing = lowered.plan.optimization_policy.allows("shared_output_coverage_check")
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
        members: list[_CoverageRead] = []
        # Formula occurrences keep their logical IDs after CommonRead maps
        # them to a single physical input in the schedule.
        for identifier in candidate.inputs[: len(program.values)]:
            stage = by_id.get(identifier)
            if not isinstance(stage, SourceReadStage):
                break
            if stage not in sources:
                domains = _source_required_key_domains(stage, candidate.keys, sharing=sharing)
                if not domains:
                    break
                sources.append(stage)
                members.extend(domains)
        else:
            same_root = len({stage.source_id for stage in sources}) == 1
            if not same_root and any(isinstance(stage.program, CommonRead) for stage in sources):
                # The carrier's diagnostic identifies one root. Let formula
                # completion report the mixed-root union without attribution.
                continue
            owner = prepared.sources.get(sources[0].source_id, prepared.source)
            if not qualified_query_provider(owner) or any(
                prepared.sources.get(stage.source_id, prepared.source) is not owner
                for stage in sources
            ):
                continue
            domain = _coverage_domain(prepared, lowered, candidate, owner)
            if domain is None:
                continue
            selected, repeated = (
                _shared_domains(tuple(members), candidate.keys) if sharing else (tuple(members), ())
            )
            required = tuple(
                _required_coordinates(prepared, lowered, member, candidate.keys, owner)
                for member in selected
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
            if sharing and same_root and candidate.keys and len(tables) > 1:
                # Distinct physical domains can still owe the same coordinate.
                missing = distinct_returned_keys(missing, owner.provider.name)
            lowered = _with_coverage_witnesses(
                lowered,
                candidate,
                program,
                sources[0],
                missing,
                selected,
                repeated,
                sharing=sharing,
            )
    return lowered
