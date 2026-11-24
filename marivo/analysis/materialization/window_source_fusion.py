"""Replace qualified independent endpoint reads with a shared window relation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._definition.expressions import EntityCount, SourceWeightedMean
from marivo._definition.observations import ContributionBinding, WindowGroup
from marivo._definition.rows import RowField
from marivo.analysis.binding.programs import FormulaProgram
from marivo.analysis.compiler.lowered_query import LoweredQuery, LoweredRead
from marivo.analysis.compiler.optimization import required_slots
from marivo.analysis.compiler.query_stages import (
    LocalCompute,
    NativeRead,
    ReductionRead,
    RuleDecision,
    SourceReadStage,
)
from marivo.analysis.compiler.source_time import endpoint_rewrite_safe
from marivo.analysis.compiler.window_prereduction import (
    DailyWindowGrid,
    WindowReduction,
    daily_window_grid,
    lower_daily_window,
)
from marivo.analysis.materialization.prepared_sources import PreparedSources
from marivo.analysis.materialization.query_parameters import parameter_data
from marivo.analysis.materialization.query_plan_transport import _consumers
from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.time_authority import CivilDateSourceTime
from marivo.datasource.spi import calendar_data
from marivo.semantic.spi import CompiledSemanticState, lower_row, normalize_target_entity


@dataclass(frozen=True, slots=True)
class _WindowSource:
    table: ir.Table
    clock: str
    groups: tuple[tuple[str, str], ...]
    spec: WindowReduction
    range_ordinals: bool


def _direct_field(value: ir.Value, table: ir.Table) -> ops.Field | None:
    operation = value.op()
    while isinstance(operation, ops.Field):
        relation = operation.rel
        if relation == table.op():
            return operation
        if isinstance(relation, (ops.Reference, ops.Filter)):
            operation = ops.Field(relation.parent, operation.name)
        elif isinstance(relation, ops.Project):
            operation = relation.values[operation.name]
        else:
            break
    return None


def _signed_bits(dtype: dt.DataType) -> int | None:
    if isinstance(dtype, dt.SignedInteger):
        return 8 * int(dtype.nbytes)
    return None


def _identity_bits(table: ir.Table, identity: tuple[str, ...]) -> int | None:
    if not identity or any(key not in table.columns for key in identity):
        return None
    widths = tuple(_signed_bits(table[key].type()) for key in identity)
    if None in widths:
        return None
    return sum(width for width in widths if width is not None)


def _decimal_carrier(dtype: dt.DataType) -> dt.Decimal | None:
    if not (
        isinstance(dtype, dt.Decimal)
        and dtype.precision is not None
        and dtype.scale is not None
        and 0 <= dtype.scale <= dtype.precision <= 38
        and (dtype.precision, dtype.scale) != (38, 0)
    ):
        return None
    return dtype


def _sum_fits(dtype: dt.DataType, identity_bits: int | None) -> bool:
    # The declared complete identity bounds represented source rows by its
    # physical key space. The full absolute mass bounds every native partial sum.
    if identity_bits is None or identity_bits > 128:
        return False
    bits = _signed_bits(dtype)
    if bits is not None:
        return identity_bits + bits <= 64
    decimal = _decimal_carrier(dtype)
    return (
        decimal is not None
        and decimal.precision is not None
        and 2**identity_bits * (10**decimal.precision - 1) < 10**38
    )


def _stored_operand(
    operand: RowField,
    table: ir.Table,
    original: ir.Table,
    stage: SourceReadStage,
) -> ops.Field | None:
    lowered = lower_row(operand, {path: original[name] for path, name in stage.field_columns})
    return None if lowered.checks else _direct_field(lowered.value, table)


def _weighted_spec(
    node: SourceWeightedMean,
    stage: SourceReadStage,
    table: ir.Table,
    original: ir.Table,
    identity_bits: int | None,
    value_slot: str,
    check_slot: str | None,
) -> WindowReduction | None:
    if not isinstance(node.value, RowField) or not isinstance(node.weight, RowField):
        return None
    value = _stored_operand(node.value, table, original, stage)
    weight = _stored_operand(node.weight, table, original, stage)
    if value is None or weight is None or identity_bits is None:
        return None
    left = _signed_bits(value.dtype)
    right = _signed_bits(weight.dtype)
    if left is None or right is None or identity_bits + left + right > 64:
        return None
    return WindowReduction("weighted_mean", value.name, value_slot, check_slot, weight=weight.name)


def _reduction_spec(
    stage: SourceReadStage,
    template: LoweredRead,
    table: ir.Table,
    original: ir.Table,
    identity: tuple[str, ...],
    grouped: frozenset[str],
) -> WindowReduction | None:
    if not isinstance(stage.program, ReductionRead):
        return None
    reduction = stage.program.reduction
    if reduction.duration is not None or reduction.sampled or reduction.window_coordinates:
        return None
    node = reduction.node
    if isinstance(node, EntityCount):
        return WindowReduction("entity_count", None, reduction.value_slot, None)
    expected_checks = int(node.nulls == "error")
    if len(template.numeric_checks) != expected_checks:
        return None
    check_slot = template.numeric_checks[0][0] if expected_checks else None
    identity_bits = _identity_bits(table, identity)
    if isinstance(node, SourceWeightedMean):
        return _weighted_spec(
            node, stage, table, original, identity_bits, reduction.value_slot, check_slot
        )
    if not isinstance(node.operand, RowField) or node.reducer not in (
        "count",
        "min",
        "max",
        "sum",
        "mean",
    ):
        return None
    field = _stored_operand(node.operand, table, original, stage)
    if field is None:
        return None
    decimal = _decimal_carrier(field.dtype)
    if node.reducer != "count" and _signed_bits(field.dtype) is None and decimal is None:
        return None
    if node.reducer == "sum" and not _sum_fits(field.dtype, identity_bits):
        return None
    if node.reducer == "mean" and not (identity and grouped.issuperset(identity)):
        return None
    native_type = template.expression[reduction.value_slot].type()
    return WindowReduction(
        node.reducer,
        field.name,
        reduction.value_slot,
        check_slot,
        sum_type=native_type
        if node.reducer == "sum" and isinstance(native_type, dt.Decimal)
        else None,
    )


def _clock_column(
    contribution: ContributionBinding,
    state: CompiledSemanticState,
    prepared: PreparedSources,
    table: ir.Table,
    timezone: str,
) -> str | None:
    clock = contribution.time
    if clock is None or clock.path or clock.at is not None or clock.source_at is not None:
        return None
    root = contribution.root.ref.path
    column = state.registry.dimensions.get(clock.field.ref.path)
    if (
        column is None
        or column.entity != root
        or column.parse is not None
        or column.source_column not in table.columns
    ):
        return None
    schemas = (
        schema
        for schema in prepared.schemas
        if schema.source_id == root and schema.occurrence == contribution.occurrence
    )
    if not any(
        isinstance(authority, CivilDateSourceTime) and authority.timezone == timezone
        for schema in schemas
        for authority in schema.time_authorities
    ):
        return None
    name = column.source_column
    assert name is not None
    return name if table[name].type().is_date() else None


def _raw_date_cutoffs(group: WindowGroup, timezone: str, provider: str) -> bool:
    return all(
        endpoint_rewrite_safe(boundary, timezone, provider)
        for endpoint in group.endpoint_grid
        for boundary in (endpoint.period.start, endpoint.period.end)
        if boundary is not None
    )


def _window_source(
    prepared: PreparedSources,
    state: CompiledSemanticState,
    contribution: ContributionBinding,
    first: SourceReadStage,
    template: LoweredRead,
    calendar_key: str,
    timezone: str,
    group: WindowGroup,
) -> _WindowSource | None:
    root = first.source_id
    owner = prepared.sources.get(root, prepared.source)
    bound = prepared.bound.get(root)
    original = prepared.relations.get(first.stage_id)
    if (
        bound is None
        or original is None
        or prepared.entities.get(root) is not bound.relation
        or not owner.uses_native_provider()
        or not owner.provider.dialect.supports_sql_window_frames
        or not owner.stored_projection.proves(bound)
        or contribution.memberships
        or contribution.sample_axes
        or contribution.intrinsic_conditions
        or contribution.common_condition is not None
    ):
        return None
    range_ordinals = owner.provider.dialect.stored_date_can_be_incomplete
    if range_ordinals and not _raw_date_cutoffs(group, timezone, owner.provider.name):
        return None
    entity = normalize_target_entity(state.registry, root)
    if entity.version is not None:
        return None
    table = bound.relation
    assert isinstance(table, ir.Table)
    clock = _clock_column(contribution, state, prepared, table, timezone)
    if clock is None or not isinstance(first.program, ReductionRead):
        return None
    if calendar_key not in first.keys or not first.keys:
        return None
    fields = dict(first.field_columns)
    groups: list[tuple[str, str]] = []
    for key, path in first.program.reduction.axes:
        if key == calendar_key or key not in first.keys or path not in fields:
            return None
        field = _direct_field(original[fields[path]], table)
        if field is None or _signed_bits(field.dtype) is None:
            return None
        groups.append((key, field.name))
    if set(first.keys) != {calendar_key, *(key for key, _ in groups)}:
        return None
    group_names = frozenset(name for _, name in groups)
    if len(group_names) != len(groups):
        return None
    spec = _reduction_spec(first, template, table, original, entity.primary_key, group_names)
    if spec is None:
        return None
    payload = () if spec.field is None else (spec.field,)
    operands = (*payload, *((spec.weight,) if spec.weight is not None else ()))
    table_operation = table.op()
    if spec.reducer != "count" and any(
        not owner.stored_projection.preserves_reduction_carrier(
            bound, ops.Field(table_operation, name)
        )
        for name in operands
    ):
        return None
    return _WindowSource(table, clock, tuple(groups), spec, range_ordinals)


def _explicit_members(lowered: LoweredQuery, group: WindowGroup) -> tuple[SourceReadStage, ...]:
    members = tuple(
        stage
        for stage in lowered.plan.stages
        if isinstance(stage, SourceReadStage)
        and stage.occurrence[:-1] == group.window_group_id
        and isinstance(stage.program, ReductionRead)
        and stage.program.reduction.window_key is not None
    )
    if (
        len(members) < 2
        or len(members) != len(group.endpoint_grid)
        or any(
            member.inputs
            or member.parameters
            or member.predicates
            or member.preparation_checks
            or lowered.read(member.stage_id).population_checks
            for member in members
        )
    ):
        return ()
    return members


_BEGIN = "__captured_window_start"
_END = "__captured_window_end"
_CLOCK = "__source_day"


def _captured_window_keys(
    stage: SourceReadStage, calendar: str, grid: DailyWindowGrid, authorized: StageOutput
) -> ir.Table | None:
    """Attach the original contribution interval to every captured report tuple."""
    names = stage.keys
    if not set(names).issubset(authorized.keys):
        return None
    by_name = {item.slot_id: item for item in authorized.columns}
    columns = tuple(by_name[name] for name in names)
    keys = StageOutput.from_frame(authorized.table.loc[:, list(names)], columns, names)
    data = parameter_data(columns, keys)
    periods = {key: (start, end + timedelta(days=1)) for key, end, start in grid.periods}
    records = data.to_pylist()
    if periods.keys() != {record[calendar] for record in records}:
        return None
    rows = tuple(
        (*(record[name] for name in names), *periods[record[calendar]]) for record in records
    )
    schema = ibis.Schema.from_pyarrow(data.schema)
    return calendar_data(ibis.schema({**dict(schema.items()), _BEGIN: "date", _END: "!date"}), rows)


def _captured_input(source: ir.Table, authority: ir.Table, keys: tuple[str, ...]) -> ir.Table:
    """Retain only the union of original contribution ranges for these complete keys."""
    return source.semi_join(
        authority,
        (
            *(source[key].identical_to(authority[key]) for key in keys),
            authority[_BEGIN].isnull() | (source[_CLOCK] >= authority[_BEGIN]),
            source[_CLOCK] < authority[_END],
        ),
    )


def _replace_group(
    lowered: LoweredQuery,
    endpoint: LocalCompute,
    program: FormulaProgram,
    members: tuple[SourceReadStage, ...],
    source: _WindowSource,
    grid: DailyWindowGrid,
    authority: ir.Table | None,
) -> LoweredQuery:
    first = members[0]
    assert isinstance(first.program, ReductionRead)
    identifiers = tuple(member.stage_id for member in members)
    dropped = frozenset(identifiers[1:])
    columns = {key: source.table[name] for key, name in source.groups}
    columns[_CLOCK] = source.table[source.clock]
    spec = source.spec
    if spec.field is not None:
        columns["__source_value"] = source.table[spec.field]
        spec = replace(spec, field="__source_value")
    if spec.weight is not None:
        columns["__source_weight"] = source.table[spec.weight]
        spec = replace(spec, weight="__source_weight")
    relation = source.table.select(**columns)
    ordinary = tuple(key for key, _ in source.groups)
    if authority is not None and ordinary:
        # A scalar has every resolved endpoint. Its grid already covers their
        # exact interval union, including any unused gaps between reset periods.
        relation = _captured_input(relation, authority, ordinary)
    expression = lower_daily_window(
        relation,
        _CLOCK,
        ordinary,
        program.calendar_key,
        grid,
        spec,
        range_ordinals=source.range_ordinals,
    ).select(*(column.slot_id for column in first.columns))
    if authority is not None:
        expression = expression.semi_join(
            authority, tuple(expression[name].identical_to(authority[name]) for name in first.keys)
        )
    replacement = replace(
        first,
        program=NativeRead((first.program.reduction,)),
        period=None,
        native_reads=members,
    )
    published = replace(
        program, values=tuple(value for value in program.values if value.stage_id not in dropped)
    )
    consumer = replace(
        endpoint,
        inputs=tuple(identifier for identifier in endpoint.inputs if identifier not in dropped),
        program=published,
    )
    stages = tuple(
        consumer
        if item.stage_id == endpoint.stage_id
        else replacement
        if item.stage_id == first.stage_id
        else item
        for item in lowered.plan.stages
        if item.stage_id not in dropped
    )
    plan = replace(
        lowered.plan,
        stages=stages,
        consumers=_consumers(stages),
        original=published if lowered.plan.original == program else lowered.plan.original,
        optimization_decisions=(
            *lowered.plan.optimization_decisions,
            RuleDecision(
                "l6_window_prereduction",
                "adopted",
                identifiers,
                "Exact civil DATE buckets combine on a dense native row frame before sparse publication.",
            ),
        ),
    )
    read = lowered.read(first.stage_id)
    native = replace(
        read,
        expression=expression,
        numeric_checks=tuple(
            (name, replace(check, violation=expression[name]))
            for name, check in read.numeric_checks
        ),
    )
    return LoweredQuery(
        replace(plan, required_slots=required_slots(plan)),
        tuple(
            native if item.stage_id == first.stage_id else item
            for item in lowered.reads
            if item.stage_id not in dropped
        ),
    )


def fuse_window_groups(
    prepared: PreparedSources,
    state: CompiledSemanticState,
    lowered: LoweredQuery,
    *,
    authorized: StageOutput | None = None,
) -> LoweredQuery:
    """Use only qualified authored windows with the same source and carrier."""
    observation = None if prepared.graph is None else prepared.graph.signature.observation
    if observation is None or not lowered.plan.optimization_policy.allows("l6_window_prereduction"):
        return lowered
    for group in observation.window_groups:
        endpoint = next(
            stage for stage in lowered.plan.stages if stage.stage_id == lowered.plan.publish
        )
        if not isinstance(endpoint, LocalCompute) or not isinstance(
            endpoint.program, FormulaProgram
        ):
            continue
        program = endpoint.program
        grid = daily_window_grid(group)
        members = _explicit_members(lowered, group)
        contribution = next(
            (
                item
                for item in observation.contributions
                if item.window_group_id == group.window_group_id
            ),
            None,
        )
        if grid is None or group.report_calendar is None or not members or contribution is None:
            continue
        first = members[0]
        authority = (
            None
            if authorized is None
            else _captured_window_keys(first, program.calendar_key, grid, authorized)
        )
        if authorized is not None and authority is None:
            continue
        if any(
            lowered.plan.consumers_of(member.stage_id) != (endpoint.stage_id,) for member in members
        ):
            continue
        source = _window_source(
            prepared,
            state,
            contribution,
            first,
            lowered.read(first.stage_id),
            program.calendar_key,
            group.report_calendar.timezone,
            group,
        )
        if source is not None:
            lowered = _replace_group(lowered, endpoint, program, members, source, grid, authority)
    return lowered
