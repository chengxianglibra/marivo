"""Pure shared source graph capture; no Session callback or evaluation owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TypeAlias

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.expressions import DeclaredMetricRef, MetricNode
from marivo._definition.grouping import normalize_groupings
from marivo._definition.query import (
    ResultQuery,
)
from marivo._definition.relations import (
    MembershipBinding,
    ObserveQuery,
    PopulationContains,
    PopulationProgram,
    ReadQuery,
    condition_populations,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    DefinitionGraphError,
    ExactRef,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
    LiteralValue,
    Owner,
)
from marivo._grouping import Grouping
from marivo._temporal import PeriodCalendarSnapshotV2, TimeScope
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.frame_structure import value_name
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import (
    BoundQuantity,
    BoundSignature,
    CoreRuleError,
    ResultQuantity,
    reject,
)
from marivo.analysis.core.value_types import literal_kind
from marivo.analysis.dsl.population import (
    Population,
    _axes,
    _owner,
)
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._deferred_semantics import (
    consume_metric,
    consume_metric_in_state,
    consume_predicate,
)
from marivo.semantic._expression_values import MetricExpression, NumericMetric
from marivo.semantic.catalog import Metric

ValueRow: TypeAlias = tuple[tuple[CoordinateValue, ...], Mapping[str, LiteralValue]]
_MetricInputs: TypeAlias = tuple[tuple[str | None, Deferred], ...]


def _metric_inputs(
    metrics: NumericMetric
    | list[NumericMetric]
    | tuple[NumericMetric, ...]
    | Mapping[str, NumericMetric],
) -> tuple[tuple[str, NumericMetric], ...]:
    """Freeze public Metric inputs and assign names only to named root objects."""
    items: tuple[tuple[str | None, NumericMetric], ...]
    if isinstance(metrics, MetricExpression):
        items = ((None, metrics),)
    elif isinstance(metrics, Mapping) and metrics:
        for output_name in metrics:
            value_name(
                output_name,
                location=f"Session.observe.metrics[{output_name!r}]",
                canonical_id="session.observe",
            )
        items = tuple(metrics.items())
    elif isinstance(metrics, (tuple, list)) and metrics:
        items = tuple((None, metric) for metric in metrics)
    else:
        reject(
            "one named Metric, a nonempty list/tuple, or a nonempty named Metric mapping",
            type(metrics).__name__,
            "Pass a named Metric, [revenue, order_count], or {'output': expression}.",
            "Session.observe.metrics",
            canonical_id="session.observe",
        )
    result: list[tuple[str, NumericMetric]] = []
    for index, (name, metric) in enumerate(items):
        location = (
            f"Session.observe.metrics[{name!r}]"
            if name is not None
            else f"Session.observe.metrics[{index}]"
        )
        if not isinstance(metric, MetricExpression) or metric._role != "numeric":
            reject(
                "a numeric Metric expression",
                type(metric).__name__,
                "Obtain a Metric from session.catalog; use {'output': expression} for anonymous expressions.",
                location,
                canonical_id="session.observe",
            )
        if name is None:
            if not isinstance(metric, Metric):
                reject(
                    "a directly named Metric for automatic output naming",
                    "an anonymous Metric expression",
                    "Pass session.observe({'output': expression}) with a user-chosen output name.",
                    location,
                    canonical_id="session.observe",
                )
            name = metric.name
        try:
            value_name(name, location=location, canonical_id="session.observe")
        except CoreRuleError as error:
            reject(
                error.expected or "a valid output name",
                f"{location}: {metric.id if isinstance(metric, Metric) else name} has local name {name!r}",
                "Pass session.observe({'output': metric}) with a user-chosen valid output name.",
                location,
                canonical_id="session.observe",
            )
        result.append((name, metric))
    return tuple(result)


def capture_read(
    population: Population,
    fields: Mapping[str, Deferred],
    owner: Owner,
    state: CompiledSemanticState,
) -> BoundGraph:
    from marivo._definition.observations import CoordinateBinding, ObservationBinding
    from marivo._definition.relations import PopulationClassify
    from marivo.analysis.binding.coordinates import bind_read
    from marivo.analysis.observation.relationship_binding import RelationshipResolver
    from marivo.semantic._deferred_semantics import consume_field
    from marivo.semantic.expressions import _Resolver

    if _owner(population._definition) not in (None, owner):
        reject(
            "a Population from this Session",
            "another Session owner",
            "Read the domain through its owning Session.",
            "Session.read",
        )
    if (
        len(population._definition.axes) != 1
        or not isinstance(population._definition.axes[0], AxisBinding)
        or population._definition.axes[0].field is not None
    ):
        reject(
            "one Entity identity axis",
            repr(population._definition.axes),
            "Project one complete identity axis before reading attributes.",
            "Session.read",
        )
    if not isinstance(fields, Mapping) or not fields:
        reject(
            "a nonempty named Field mapping",
            type(fields).__name__,
            "Pass session.read(people, {'region': region}).",
            "Session.read",
        )
    identity = population._definition.axes[0]
    resolver = _Resolver(state)
    paths = RelationshipResolver.build(state.registry)
    reads = []
    quantities = []
    for name, value in fields.items():
        field = consume_field(value)
        read = field.field_read or FieldRead(field.reference, field.root)
        if read.at is None and identity.at is not None and read.entity.ref == identity.entity.ref:
            from dataclasses import replace

            read = replace(read, at=identity.at)
        reads.append(bind_read(read, identity.entity, state.registry, resolver, paths))
        quantities.append(
            ResultQuantity(
                name,
                field.unit,
                "unresolved",
            )
        )
    unique = tuple(dict.fromkeys(reads))
    axes = (
        identity,
        *(AxisBinding(read.entity, read.field, read.path, "field", read.at) for read in unique),
    )
    definition = PopulationProgram(PopulationClassify(population._definition, unique), owner, axes)
    binding = ObservationBinding(None, None, CoordinateBinding(axes, "grouped", definition), ())
    return BoundGraph(
        ReadQuery(definition, tuple(unique.index(read) + 1 for read in reads), (identity,), owner),
        BoundSignature(owner, tuple(quantities), (identity,), observation=binding),
    )


def capture_values(
    *,
    by: tuple[Deferred | Grouping, ...],
    columns: Mapping[str, str | None],
    rows: tuple[ValueRow, ...],
    owner: Owner,
) -> BoundGraph:
    from datetime import datetime

    from marivo._definition.query import LiteralValues
    from marivo._definition.relations import LiteralPopulation
    from marivo._definition.types import DefinitionGraphError, check_literal

    def literal(value: LiteralValue) -> LiteralValue:
        try:
            result = check_literal(value, role="values literal")
        except DefinitionGraphError as error:
            reject(
                "a finite immutable literal",
                str(error),
                "Use a finite typed value.",
                "Session.values",
            )
        if isinstance(result, datetime) and (result.tzinfo is None or result.utcoffset() is None):
            reject(
                "a timezone-aware timestamp literal",
                repr(result),
                "Pass an aware datetime or a date-valued key.",
                "Session.values",
            )
        return result

    axes = _axes(by)
    if any(isinstance(axis, CalendarCoordinate) for axis in axes):
        reject(
            "explicit field or identity axes",
            repr(axes),
            "Reuse calendars through Population, rather than hand-writing calendar keys.",
            "Session.values",
        )
    if not columns or not isinstance(rows, tuple) or not rows:
        reject(
            "named columns and finite typed literal rows",
            repr((columns, rows)),
            "Provide at least one literal row to establish value kinds; filter the Frame when an empty typed result is needed.",
            "Session.values",
        )
    names = tuple(columns)
    keys = tuple(key for key, _ in rows)
    try:
        for coordinate_row in keys:
            for coordinate_key in coordinate_row:
                for component in (
                    coordinate_key if isinstance(coordinate_key, tuple) else (coordinate_key,)
                ):
                    literal(component)
        domain = LiteralPopulation(
            tuple(axis for axis in axes if isinstance(axis, AxisBinding)), keys, "values@v1"
        )
    except DefinitionGraphError as error:
        reject(
            "complete immutable coordinate tuples",
            str(error),
            "Provide every typed key component in declared axis order.",
            "Session.values",
        )
    if len(domain.keys) != len(keys):
        reject(
            "unique complete input keys",
            "duplicate literal coordinates",
            "Provide one value row per complete key.",
            "Session.values",
        )
    values = []
    kinds = []
    for name in names:
        if any(set(row) != set(names) for _, row in rows):
            reject(
                "exactly the declared value names in every row",
                repr(names),
                "Provide each columns name once in every ValueRow.",
                "Session.values",
            )
        literals = tuple(literal(row[name]) for _, row in rows)
        if any(value is None for value in literals):
            reject(
                "non-null literal values",
                name,
                "Use nullable source values or derive NA with a declared operator.",
                "Session.values",
            )
        column_kinds = {literal_kind(value) for value in literals}
        if len(column_kinds) != 1:
            reject(
                "one compatible literal kind per column",
                repr(column_kinds),
                "Use homogeneous values in each input column.",
                "Session.values",
            )
        kinds.append(next(iter(column_kinds)))
        unit = columns[name]
        if unit is not None and (
            type(unit) is not str or not unit or kinds[-1] not in ("numeric", "duration")
        ):
            reject(
                "an explicit unit string for numeric values, or None",
                repr(unit),
                "Declare units only for numeric or Duration columns.",
                "Session.values",
            )
    for key, row in rows:
        values.append((key, tuple(literal(row[name]) for name in names)))
    root = ResultQuery(LiteralValues(names, tuple(values)), axes, owner)
    signature = BoundSignature(
        owner,
        tuple(
            ResultQuantity(name, columns[name], kind)
            for name, kind in zip(names, kinds, strict=True)
        ),
        axes,
    )
    return BoundGraph(root, signature)


def capture_metrics(
    metrics: _MetricInputs,
    owner: Owner,
    *,
    by: tuple[Deferred | Grouping | TimeScope | Population, ...] | Population = (),
    where: Deferred | None = None,
    timezone: str = "UTC",
    state: CompiledSemanticState | None = None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
    _parent_filter: Deferred | None = None,
    _complete_calendar: bool = True,
    _endpoint_scope: TimeScope | None = None,
) -> BoundGraph:
    quantities: list[BoundQuantity] = []
    definitions: dict[ExactRef, MetricNode] = {}
    names: dict[str, tuple[int, str]] = {}
    for position, (name, metric) in enumerate(metrics, start=1):
        facts = (
            consume_metric_in_state(metric, state) if state is not None else consume_metric(metric)
        )
        reference = (
            facts.node.ref.ref.key
            if isinstance(facts.node, DeclaredMetricRef)
            else type(facts.node).__name__
        )
        if name is None:
            if not isinstance(facts.node, DeclaredMetricRef):
                reject(
                    "a directly referenced declared Metric for automatic output naming",
                    f"input {position}: {reference}",
                    "Pass session.observe({'output': expression}) with a user-chosen output name.",
                    "Session.observe",
                    canonical_id="session.observe",
                )
            name = facts.node.ref.ref.path.rsplit(".", 1)[-1]
            try:
                value_name(name, location="Session.observe", canonical_id="session.observe")
            except CoreRuleError as error:
                reject(
                    error.expected or "a valid single output name segment",
                    f"input {position}: {reference} has local name {name!r}",
                    "Pass session.observe({'output': metric}) with a user-chosen valid output name.",
                    "Session.observe",
                    canonical_id="session.observe",
                )
        if name in names:
            previous, previous_reference = names[name]
            reject(
                "distinct output names in one observation",
                f"{name!r} at inputs {previous} ({previous_reference}) and {position} ({reference})",
                "Pass an explicit Mapping with distinct user-chosen output names for every Metric.",
                "Session.observe",
                canonical_id="session.observe",
            )
        names[name] = (position, reference)
        quantities.append(BoundQuantity(name, facts.node, facts.unit))
        definitions.update(facts._declared)
    if not isinstance(by, (tuple, Population)):
        reject(
            "an ordered tuple of semantic fields",
            type(by).__name__,
            "Pass by=(field,) using resolved semantic fields.",
            "session.observe",
        )
    domain = (
        by
        if isinstance(by, Population)
        else by[0]
        if by and isinstance(by[0], Population)
        else None
    )
    if domain is not None:
        if _owner(domain._definition) not in (None, owner):
            reject(
                "a Population from this Session",
                "different Session owner",
                "Rebuild the domain in this Session.",
                "session.observe",
            )
        axes = domain._definition.axes
        population = domain._definition
    else:
        population = None
    items = () if isinstance(by, Population) else by[1:] if domain is not None else by
    captured: list[ComparisonAxis | MembershipBinding] = []
    for item in items:
        if isinstance(item, Grouping) and isinstance(item._binding, MembershipBinding):
            captured.append(item._binding)
        elif domain is None and isinstance(item, (Deferred, Grouping, TimeScope)):
            captured.extend(_axes((item,)))
        else:
            reject(
                "ordinary coordinates or a first Population followed only by membership rules",
                type(item).__name__,
                "Use by=(field, rule, ...) or by=(population, rule, ...).",
                "Session.observe.by",
            )
    bindings: tuple[MembershipBinding, ...]
    try:
        if isinstance(by, Population):
            bindings = ()
        else:
            axes, bindings = normalize_groupings(
                tuple(captured),
                population_axes=domain._definition.axes if domain is not None else None,
            )
    except DefinitionGraphError as error:
        reject(
            "distinct coordinates with membership fields in captured order",
            str(error),
            "Put each rule at its first field position; position its later fields explicitly when needed.",
            "Session.observe.by",
        )

    source_axes = tuple(
        axis
        for axis in axes
        if isinstance(axis, (AxisBinding, CalendarCoordinate, IntervalCoordinate))
    )
    reads = tuple(
        axis
        if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        else FieldRead(axis.field, axis.entity, axis.path, axis.at)
        if axis.field is not None
        else IdentityRead(axis.entity, axis.path, axis.at)
        for axis in source_axes
    )
    if _parent_filter is not None:
        from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
        from marivo._definition.graph import walk

        has_facts = any(
            isinstance(node, (EntityCount, SourceAggregate, SourceWeightedMean))
            for node in walk(
                tuple(quantity.definition for quantity in quantities), resolve=definitions.get
            ).order
        )
        if has_facts:
            where = _parent_filter if where is None else where & _parent_filter
    condition = consume_predicate(where).node if where is not None else None
    if condition is not None:
        for dependency in condition_populations(condition):
            if _owner(dependency) not in (None, owner):
                reject(
                    "predicate Population dependencies owned by this Session",
                    "a Population from another Store or Session",
                    "Construct the condition and observation in the Population's owning Session.",
                    "session.observe",
                )
    signature = BoundSignature(
        owner, tuple(quantities), declared_metrics=tuple(definitions.items())
    )
    root = ObserveQuery(tuple((q.name, q.definition) for q in quantities), (), owner)
    BoundGraph(root, signature)
    parent_scope = None
    if _parent_filter is not None:
        parent_condition = consume_predicate(_parent_filter).node
        assert len(parent_condition.inputs) == 1
        parent_leaf = parent_condition.inputs[0]
        assert isinstance(parent_leaf, PopulationContains)
        parent_scope = parent_leaf.population
    private_interval = None
    if _endpoint_scope is not None:
        private_interval = _axes((_endpoint_scope,))[0]
    binding = bind_observation(
        root.metrics,
        definitions,
        by=(*reads, *((private_interval,) if private_interval is not None else ())),
        where=condition,
        timezone=timezone,
        state=state,
        calendar_snapshots=calendar_snapshots,
        memberships=bindings,
        population=population,
    )
    if private_interval is not None:
        binding = replace(
            binding,
            coordinates=replace(binding.coordinates, axes=axes),
            contributions=tuple(
                replace(
                    contribution,
                    calendars=tuple(
                        (slot, axis)
                        for slot, axis in contribution.calendars
                        if axis != private_interval
                    ),
                )
                for contribution in binding.contributions
            ),
        )
    binding = replace(
        binding,
        coordinates=replace(
            binding.coordinates, complete_calendar=_complete_calendar, parent_scope=parent_scope
        ),
    )
    signature = BoundSignature(
        owner,
        tuple(quantities),
        binding.coordinates.axes,
        tuple(definitions.items()),
        observation=binding,
    )
    root = ObserveQuery(
        root.metrics,
        signature.axes,
        owner,
        population=binding.coordinates.population,
        count_populations=tuple(domain for _, domain in binding.count_populations),
    )
    return BoundGraph(root, signature)


def capture_population(population: Population, owner: Owner) -> BoundGraph:
    from marivo._definition.observations import CoordinateBinding, ObservationBinding

    existing_owner = _owner(population._definition)
    if existing_owner is not None and existing_owner != owner:
        reject(
            "a Population from this Session",
            str(existing_owner),
            "Rebuild the Population in this Session.",
            "session.frame",
        )
    axes = population._definition.axes
    binding = ObservationBinding(
        None,
        None,
        CoordinateBinding(axes, "grouped", population._definition),
        (),
    )
    signature = BoundSignature(owner, (), axes, observation=binding)
    root = ObserveQuery((), axes, owner, population._definition)
    return BoundGraph(root, signature)
