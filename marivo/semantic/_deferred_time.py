"""Resolve captured contribution clocks and finite sample domains."""

from __future__ import annotations

from ibis.common.deferred import (
    Deferred,
    Just,
    Resolver,
    Sequence,
)

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.deferred import (
    CapturedDefaultTimeResolver,
    NamedResolver,
    inspect_capture,
    relationship_carrier,
)
from marivo._definition.deferred_sample_validation import captured_field_kind
from marivo._definition.expressions import (
    FieldCalendarSample,
)
from marivo._definition.grouping import grouping_binding, normalize_groupings
from marivo._definition.observation_time import ContributionClockBinding
from marivo._definition.population_types import population_definition
from marivo._definition.relations import (
    LiteralPopulation,
    MembershipBinding,
    PopulationParameter,
    PopulationProgram,
    SampleDomain,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    DefinitionGraphError,
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    RowAttributeTime,
    field_axis,
    fixed_attribute_time,
)
from marivo._kernel.grouping import Grouping
from marivo._kernel.paths import relationship_binding
from marivo._kernel.refs import SemanticKind
from marivo._kernel.temporal import Grain, TimeScope
from marivo.semantic._deferred_facts import _REDUCERS, InspectRecipe, RowFacts, _fail
from marivo.semantic.errors import ErrorKind, SemanticExpressionError
from marivo.semantic.expressions.metric_time import (
    calendar_sample_domain,
)
from marivo.semantic.expressions.objects import _Relationship as Relationship


def _constant(recipe: Resolver, default: object = None) -> object:
    if isinstance(recipe, NamedResolver):
        return _constant(recipe.recipe, default)
    relationship = relationship_carrier(recipe)
    if relationship is not None:
        return relationship_binding(relationship)
    if isinstance(recipe, Just):
        return (
            relationship_binding(recipe.value)
            if isinstance(recipe.value, Relationship)
            else recipe.value
        )
    return default


def _native_sample_axis(axis: ComparisonAxis) -> AxisBinding:
    if (
        not isinstance(axis, AxisBinding)
        or axis.role != "field"
        or axis.path
        or axis.field is None
        or axis.field.ref.kind not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
    ):
        _fail(
            "Internal samples require native Dimension or TimeField groupings.",
            expected="native field, Grain or explicit Population",
            received=f"{type(axis).__name__}: {axis!r}",
            action='For calendar samples use m.per(mv.grain("day")).<reducer>() or pass a native TimeField with grain=.'
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
            else "Use a native Dimension/TimeField or an explicit Population as the sample domain.",
        )
    assert isinstance(axis, AxisBinding)
    return axis


def _sample_item(
    member: Resolver, domain: PopulationParameter | None, consume: InspectRecipe
) -> ComparisonAxis | MembershipBinding:
    value = _constant(member)
    binding = grouping_binding(value) if isinstance(value, Grouping) else None
    if isinstance(binding, MembershipBinding):
        if domain is None:
            for read in binding.fields:
                _native_sample_axis(field_axis(read))
        return binding
    if domain is not None or isinstance(value, PopulationParameter):
        _fail(
            "A sample Population must be first and followed only by membership rules.",
            expected="per(P) or per((P, rule, ...))",
            received=type(value).__name__,
        )
    if binding is not None:
        return _native_sample_axis(binding)
    axis = consume(member)
    if (
        not isinstance(axis, RowFacts)
        or axis.field is None
        or axis.field.reference.ref.kind
        not in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}
    ):
        _fail(
            "Internal samples require semantic fields.",
            expected="resolved field",
            received=type(axis).__name__,
        )
    assert axis.field is not None
    axis_read = axis.field.field_read
    if axis_read is not None and axis_read.path:
        _fail(
            "A scalar via field is not an internal sample domain.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected="native field or explicit Population",
            received=axis.field.reference.ref.key,
        )
    return (
        field_axis(axis_read)
        if axis_read is not None
        else AxisBinding(axis.root, axis.field.reference, (), "field")
    )


def _sample_domain(
    recipe: Resolver,
    consume: InspectRecipe,
) -> tuple[AxisBinding, ...] | LiteralPopulation | PopulationProgram | SampleDomain | Grain:
    domain = _constant(recipe)
    if isinstance(domain, Grain):
        return domain
    if isinstance(domain, TimeScope):
        _fail(
            "Internal calendar samples take a Grain, not a time scope.",
            expected="a Grain inheriting the observation time coordinate",
            received=type(domain).__name__,
            action='Use m.per(mv.grain("day")).<reducer>() and pass the finite scope in observe(where=...).',
        )
    if isinstance(domain, PopulationParameter):
        return population_definition(domain)
    if isinstance(recipe, Sequence) and recipe.typ is not tuple:
        _fail(
            "Internal sample tuples must use a built-in tuple.",
            expected="a field, membership, nonempty tuple or Population",
            received=repr(recipe),
        )
    members = recipe.values if isinstance(recipe, Sequence) else (recipe,)
    population = _constant(members[0]) if members else None
    domain = population if isinstance(population, PopulationParameter) else None
    items: list[ComparisonAxis | MembershipBinding] = []
    for member in members[1:] if domain is not None else members:
        items.append(_sample_item(member, domain, consume))
    try:
        axes, memberships = normalize_groupings(
            tuple(items),
            population_axes=population_definition(domain).axes if domain is not None else None,
        )
    except DefinitionGraphError as error:
        _fail(
            "Sample coordinates and membership rules must be unique and retain captured order.",
            expected="distinct sample coordinates with one allocation per field",
            received=str(error),
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        )
    if domain is not None:
        return SampleDomain(axes, memberships, population_definition(domain))
    if not axes:
        _fail(
            "Internal sample axes must be nonempty and unique.",
            expected="distinct semantic axes",
            received="empty axes",
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
        )
    native_axes = tuple(_native_sample_axis(axis) for axis in axes)
    return SampleDomain(native_axes, memberships) if memberships else native_axes


def _calendar_sample_recipe(sample: Resolver) -> bool:
    if isinstance(sample, Sequence) and sample.values:
        sample = sample.values[0]
    value = _constant(sample)
    return isinstance(value, Grain) or (
        isinstance(value, PopulationParameter)
        and calendar_sample_domain(population_definition(value))
    )


def _field_calendar_sample(
    recipe: Resolver, grain_recipe: Resolver, roots: tuple[ExactRef, ...], consume: InspectRecipe
) -> FieldCalendarSample:
    grain = _constant(grain_recipe)
    field = consume(recipe)
    if (
        not isinstance(grain, Grain)
        or not isinstance(field, RowFacts)
        or field.field is None
        or (field.field.reference.ref.kind is not SemanticKind.TIME_DIMENSION)
    ):
        _fail(
            "Explicit calendar samples require one native TimeField and a builtin Grain.",
            expected="Metric.per(TimeField, grain=mv.grain(...))",
            received=f"{type(field).__name__} with grain={grain!r}",
            action='Use a native TimeField with mv.grain("day"), or omit grain for ordinary samples.',
            help_target="metric.per",
        )
    read = field.field.field_read
    if (read is not None and (read.path or read.at is not None)) or {
        root.ref for root in roots
    } != {field.root.ref}:
        _fail(
            "The calendar sample field must be native to every contribution root.",
            expected="one native TimeField from the child Metric's contribution root",
            received=field.field.reference.ref.key,
            action="Apply a separate field calendar fold to each root before combining Metrics.",
            help_target="metric.per",
        )
    return FieldCalendarSample(FieldRead(field.field.reference, field.root), grain)


def _capture_source_time(
    recipe: Resolver, time: Resolver | None, consume: InspectRecipe, *, operation: str | None = None
) -> CapturedDefaultTimeResolver | None:
    """Check explicit time or freeze a closed receiver's default selection."""

    if operation is not None and operation not in _REDUCERS | {"weighted_mean"}:
        return None
    if inspect_capture(Deferred(recipe)).symbols:
        return None
    if time is not None and not (isinstance(time, Just) and time.value is None):
        if captured_field_kind(time) is not SemanticKind.TIME_DIMENSION:
            # The complete reduction inspector owns errors on unsupported time argument shapes.
            return None
        if inspect_capture(Deferred(time)).symbols:
            return None
    try:
        row = consume(recipe)
    except SemanticExpressionError as error:
        # Named inputs can be rebound for non-time checks in a receiving scope.
        if error.details.get("defer_to_scope") or error.kind in {
            ErrorKind.CROSS_ROOT_ROW_EXPRESSION,
            ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
            ErrorKind.MISSING_SUM_PERMISSION,
            ErrorKind.INVALID_WHERE_SCOPE,
            ErrorKind.INVALID_TIME_PATH,
        }:
            return None
        raise
    if not isinstance(row, RowFacts):
        return None
    if time is not None:
        _time(time, row, consume)
        return None
    return CapturedDefaultTimeResolver(row.default_time)


def _time(
    recipe: Resolver | None, row: RowFacts, consume: InspectRecipe
) -> ContributionClockBinding | None:
    if recipe is None or isinstance(recipe, CapturedDefaultTimeResolver):
        default = row.default_time if recipe is None else recipe.field
        if default is None:
            return None
        if default.ref.path.rsplit(".", 1)[0] != row.root.ref.path:
            _fail(
                "Captured default time belongs to another contribution root.",
                kind=ErrorKind.INVALID_TIME_PATH,
                expected=row.root.ref.key,
                received=default.ref.key,
                action="Construct the reduction again from its current contribution fields.",
            )
        return ContributionClockBinding(default, source="entity_default")
    if isinstance(recipe, Just) and recipe.value is None:
        return None
    value = consume(recipe)
    if (
        not isinstance(value, RowFacts)
        or value.field is None
        or value.field.reference.ref.kind is not SemanticKind.TIME_DIMENSION
    ):
        _fail(
            "Reduction time must be a semantic time field.",
            expected="a resolved TimeField",
            received=type(value).__name__,
        )
    assert value.field is not None
    read = value.field.field_read
    if read is not None and isinstance(read.at, RowAttributeTime):
        _fail(
            "Row attribute time is only available for observe classification.",
            expected="a native time field or a fixed attribute anchor",
            received=value.field.reference.ref.key,
        )
    if value.root.ref != row.root.ref:
        _fail(
            "Time field belongs to another root.",
            kind=ErrorKind.INVALID_TIME_PATH,
            expected=row.root.ref.key,
            received=value.root.ref.key,
        )
    return ContributionClockBinding(
        value.field.reference,
        read.path if read is not None else (),
        fixed_attribute_time(read.at) if read is not None else None,
        source_at=read.source_at if read is not None else None,
        step_ats=read.step_ats if read is not None else (),
    )
