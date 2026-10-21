"""Pure shared source graph capture; no Session callback or evaluation owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TypeAlias

from ibis.common.deferred import Deferred

from marivo._definition.expressions import MetricNode
from marivo._definition.query import (
    ResultQuery,
)
from marivo._definition.relations import (
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
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import BoundQuantity, BoundSignature, ResultQuantity, reject
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

ValueRow: TypeAlias = tuple[tuple[CoordinateValue, ...], Mapping[str, LiteralValue]]


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
    metrics: Mapping[str, Deferred],
    owner: Owner,
    *,
    by: tuple[Deferred | Grouping | TimeScope, ...] | Population = (),
    where: Deferred | None = None,
    memberships: tuple[Grouping, ...] = (),
    timezone: str = "UTC",
    state: CompiledSemanticState | None = None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
    _parent_filter: Deferred | None = None,
    _complete_calendar: bool = True,
    _endpoint_scope: TimeScope | None = None,
) -> BoundGraph:
    if not isinstance(metrics, Mapping) or not metrics:
        reject(
            "a nonempty named mapping of Metric expressions",
            type(metrics).__name__,
            "Pass session.observe({'revenue': revenue}).",
            "session.observe",
        )
    quantities: list[BoundQuantity] = []
    definitions: dict[ExactRef, MetricNode] = {}
    for name, metric in metrics.items():
        if not isinstance(metric, Deferred):
            reject(
                "a Metric expression for each output",
                type(metric).__name__,
                "Resolve a Metric expression; Artifact objects and IDs are terminal outputs, not computation inputs.",
                "session.observe",
            )
        facts = (
            consume_metric_in_state(metric, state) if state is not None else consume_metric(metric)
        )
        quantities.append(BoundQuantity(name, facts.node, facts.unit))
        definitions.update(facts._declared)
    if not isinstance(by, (tuple, Population)):
        reject(
            "an ordered tuple of semantic fields",
            type(by).__name__,
            "Pass by=(field,) using resolved semantic fields.",
            "session.observe",
        )
    if isinstance(by, Population):
        if _owner(by._definition) not in (None, owner):
            reject(
                "a Population from this Session",
                "different Session owner",
                "Rebuild the domain in this Session.",
                "session.observe",
            )
        axes = by._definition.axes
        population = by._definition
    else:
        axes = _axes(by)
        population = None

    from marivo._definition.relations import MembershipBinding, validate_memberships
    from marivo._definition.types import DefinitionGraphError

    if not isinstance(memberships, tuple) or any(
        not isinstance(rule, Grouping) or not isinstance(rule._binding, MembershipBinding)
        for rule in memberships
    ):
        reject(
            "a tuple of membership Grouping rules",
            repr(memberships),
            "Pass memberships=(mv.membership(...),).",
            "Session.observe.memberships",
        )
    bindings = tuple(
        rule._binding for rule in memberships if isinstance(rule._binding, MembershipBinding)
    )
    try:
        validate_memberships(axes, bindings)
    except DefinitionGraphError as error:
        reject(
            "membership rules for distinct existing coordinates in captured order",
            str(error),
            "Match each rule to the ordered by coordinates and allocate each coordinate once.",
            "Session.observe.memberships",
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
