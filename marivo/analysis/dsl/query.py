"""Immutable multi-Metric Query construction, separate from terminal data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis, Correspondence, CorrespondenceAxis
from marivo._definition.expressions import MetricNode
from marivo._definition.query import (
    CombineValues,
    CoordinateSlot,
    DrillValues,
    FilterValues,
    PresenceSlot,
    QueryOperation,
    ReduceValues,
    ResultQuery,
    RollupValues,
    SelectValues,
    ShareValues,
    TopValues,
    ValueExpression,
    ValueLiteral,
    ValueSlot,
)
from marivo._definition.relations import (
    ObserveQuery,
    PopulationCondition,
    PopulationDependency,
    PopulationProgram,
    QueryKeys,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    ExactRef,
    FieldRead,
    IdentityRead,
    LiteralValue,
    Owner,
)
from marivo._temporal import TimeScope
from marivo.analysis._contract import AnalysisAction, AnalysisContract
from marivo.analysis.binding.observe import bind_observation
from marivo.analysis.core.graph import BoundGraph, capture_query
from marivo.analysis.core.model import BoundQuantity, BoundSignature, ResultQuantity, reject
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.column import Column, _Statistic, literal_kind
from marivo.analysis.dsl.population import (
    MemberAxis,
    MembershipAxis,
    Population,
    PopulationPredicate,
    _axes,
    _owner,
)
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._deferred_semantics import (
    consume_metric,
    consume_metric_in_state,
    consume_predicate,
)

ValueRow: TypeAlias = tuple[tuple[CoordinateValue, ...], Mapping[str, LiteralValue]]


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Query:
    """A lazy named Metric definition owned by one Session.

    Acquire through session.observe(metrics). This surface captures definitions and ordinary observation bindings;
    execute() evaluates ordinary numeric stages and atomically publishes terminal output in Store 10; committed output reads require no current source or Catalog.
    """

    _graph: BoundGraph
    _execute: Callable[[BoundGraph], Artifact]

    def __init__(self) -> None:
        raise TypeError("Use session.observe(metrics).")

    @classmethod
    def _from_read(
        cls,
        population: Population,
        fields: Mapping[str, Deferred],
        owner: Owner,
        execute: Callable[[BoundGraph], Artifact],
        state: CompiledSemanticState,
    ) -> Query:
        from marivo._definition.observations import CoordinateBinding, ObservationBinding
        from marivo._definition.query import ReadFields
        from marivo._definition.relations import PopulationClassify
        from marivo.analysis.binding.coordinates import bind_read
        from marivo.analysis.observation.relationship_binding import RelationshipResolver
        from marivo.semantic._deferred_semantics import consume_field
        from marivo.semantic.expressions import _Resolver

        if _owner(population.definition) not in (None, owner):
            reject(
                "a Population from this Session",
                "another Session owner",
                "Read the domain through its owning Session.",
                "Session.read",
            )
        if (
            len(population.axes) != 1
            or not isinstance(population.axes[0], AxisBinding)
            or population.axes[0].field is not None
        ):
            reject(
                "one Entity identity axis",
                repr(population.axes),
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
        identity = population.axes[0]
        resolver = _Resolver(state)
        paths = RelationshipResolver.build(state.registry)
        reads = []
        quantities = []
        for name, value in fields.items():
            field = consume_field(value)
            read = field.field_read or FieldRead(field.reference, field.root)
            if (
                read.at is None
                and identity.at is not None
                and read.entity.ref == identity.entity.ref
            ):
                from dataclasses import replace

                read = replace(read, at=identity.at)
            reads.append(bind_read(read, identity.entity, state.registry, resolver, paths))
            quantities.append(
                ResultQuantity(
                    name,
                    field.unit,
                    "numeric" if field.reference.ref.kind.value == "measure" else "unresolved",
                )
            )
        unique = tuple(dict.fromkeys(reads))
        axes = (
            identity,
            *(AxisBinding(read.entity, read.field, read.path, "field", read.at) for read in unique),
        )
        definition = PopulationProgram(
            PopulationClassify(population.definition, unique), owner, axes
        )
        binding = ObservationBinding(None, None, CoordinateBinding(axes, "grouped", definition), ())
        base = object.__new__(cls)
        object.__setattr__(
            base,
            "_graph",
            BoundGraph(
                ObserveQuery((), axes, owner, definition),
                BoundSignature(owner, (), axes, observation=binding),
            ),
        )
        object.__setattr__(base, "_execute", execute)
        return base._result(
            ReadFields(tuple(unique.index(read) + 1 for read in reads)),
            tuple(quantities),
            (identity,),
        )

    @classmethod
    def _from_values(
        cls,
        *,
        by: tuple[Deferred | AxisBinding, ...],
        columns: Mapping[str, str | None],
        rows: tuple[ValueRow, ...],
        owner: Owner,
        execute: Callable[[BoundGraph], Artifact],
    ) -> Query:
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
            if isinstance(result, datetime) and (
                result.tzinfo is None or result.utcoffset() is None
            ):
                reject(
                    "a timezone-aware timestamp literal",
                    repr(result),
                    "Pass an aware datetime or a date-valued key.",
                    "Session.values",
                )
            return result

        axes, memberships = _axes(by)
        if memberships or any(isinstance(axis, CalendarCoordinate) for axis in axes):
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
                "Provide at least one literal row to establish value kinds; filter the Query when an empty typed result is needed.",
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
        root = ResultQuery((), LiteralValues(names, tuple(values)), axes, owner)
        signature = BoundSignature(
            owner,
            tuple(
                ResultQuantity(name, columns[name], kind)
                for name, kind in zip(names, kinds, strict=True)
            ),
            axes,
        )
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    @classmethod
    def _from_metrics(
        cls,
        metrics: Mapping[str, Deferred],
        owner: Owner,
        execute: Callable[[BoundGraph], Artifact],
        *,
        during: TimeScope | CalendarAxis | None = None,
        by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...] | Population = (),
        where: Deferred | PopulationPredicate | None = None,
        members: Population | None = None,
        timezone: str = "UTC",
        state: CompiledSemanticState | None = None,
    ) -> Query:
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
            if isinstance(where, PopulationPredicate):
                metric = metric.where(where)
            facts = (
                consume_metric_in_state(metric, state)
                if state is not None
                else consume_metric(metric)
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
            if _owner(by.definition) not in (None, owner):
                reject(
                    "a Population from this Session",
                    "different Session owner",
                    "Rebuild the domain in this Session.",
                    "session.observe",
                )
            axes, memberships = by.axes, by.memberships
            population = by.definition
        else:
            axes, memberships = _axes(by, available=members.axes if members is not None else ())
            population = None
            if members is not None and any(axis in members.axes for axis in axes):
                population = (
                    members.classify(by=axes).definition
                    if axes != members.axes
                    else members.definition
                )
        if any(isinstance(axis, CorrespondenceAxis) for axis in axes):
            reject(
                "original field or calendar observation axes",
                "a correspondence axis",
                "Project a complete original endpoint before observing source Metrics.",
                "Session.observe",
            )
        source_axes = tuple(
            axis for axis in axes if isinstance(axis, (AxisBinding, CalendarCoordinate))
        )
        reads = tuple(
            axis
            if isinstance(axis, CalendarCoordinate)
            else FieldRead(axis.field, axis.entity, axis.path, axis.at)
            if axis.field is not None
            else IdentityRead(axis.entity, axis.path, axis.at)
            for axis in source_axes
        )
        condition = consume_predicate(where).node if isinstance(where, Deferred) else None
        signature = BoundSignature(
            owner, tuple(quantities), declared_metrics=tuple(definitions.items())
        )
        root = ObserveQuery(tuple((q.name, q.definition) for q in quantities), (), owner)
        BoundGraph(root, signature)
        binding = bind_observation(
            root.metrics,
            definitions,
            during=during,
            by=reads,
            where=condition,
            timezone=timezone,
            state=state,
            memberships=memberships,
            population=population,
            members=members.definition if members is not None else None,
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
            members=binding.member_population or binding.members,
        )
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    def __repr__(self) -> str:
        signature = self._graph.signature
        contributions = signature.observation.contributions if signature.observation else ()
        sources = len({item.root.ref for item in contributions})
        return f"<Query id={self._graph.fingerprint[:16]} session={signature.owner.session_id[:80]} outputs={len(signature.quantities)} sources={sources} contributions={len(contributions)}; use .contract().show()>"

    def __getitem__(self, name: str) -> Column:
        """Locate a named value without executing this Query.

        Args: name: Exact output name. Returns: A Column bound to this input.
        Example: ``score = query['revenue']``.
        Constraints: Another Query's Columns require explicit alignment.
        """
        for index, quantity in enumerate(self._graph.signature.quantities):
            if quantity.name == name:
                return Column(
                    self._graph.fingerprint,
                    ValueSlot(index),
                    quantity.unit,
                    quantity.kind if isinstance(quantity, ResultQuantity) else "numeric",
                    capture_query(self._graph),
                )

        def presence(root: object) -> tuple[tuple[str, ...], ...]:
            if not isinstance(root, ResultQuery):
                return ()
            if isinstance(root.operation, CombineValues):
                roles = (
                    ("left", "right")
                    if root.operation.operation == "align"
                    else ("current", "baseline")
                )
                return tuple(
                    path
                    for role, captured in zip(roles, root.inputs[:2], strict=True)
                    for path in ((role,), *((role, *nested) for nested in presence(captured.query)))
                )
            return (
                presence(root.inputs[0].query)
                if root.inputs
                and isinstance(root.operation, (SelectValues, FilterValues, TopValues, ShareValues))
                else ()
            )

        for path in presence(self._graph.root):
            if name == ".".join((*path[:-1], path[-1] + "_present")):
                return Column(
                    self._graph.fingerprint,
                    PresenceSlot(path),
                    None,
                    "boolean",
                    capture_query(self._graph),
                )
        reject(
            "an existing Query value name",
            repr(name),
            f"Choose from {tuple(q.name for q in self._graph.signature.quantities)!r}.",
            "Query.__getitem__",
        )

    def _positions(
        self, axes: tuple[Deferred | MemberAxis | ComparisonAxis, ...]
    ) -> tuple[int, ...]:
        resolved = tuple(
            item
            if isinstance(item, CorrespondenceAxis)
            else _axes((item,), available=self._graph.signature.axes)[0][0]
            for item in axes
        )
        existing = self._graph.signature.axes
        if len(set(resolved)) != len(resolved):
            reject(
                "distinct bound coordinate axes",
                repr(resolved),
                "Use each retained axis once.",
                "Query.axes",
            )
        if any(axis not in existing for axis in resolved):
            reject(
                "existing bound Query axes",
                repr(resolved),
                "Use retained axes; read new attributes through Session.read or Query.drill.",
                "Query.axes",
            )
        return tuple(existing.index(axis) for axis in resolved)

    def coord(self, axis: Deferred | MemberAxis | ComparisonAxis) -> Column:
        """Locate an existing scalar coordinate as a value expression.

        Args: axis: Exact retained binding. Returns: A domain-bound Column.
        Example: ``query.coord(region) == 'West'``.
        Constraints: This reads no new fields. Preparation rejects composite
        coordinates as scalar Columns; complete identity keys remain output
        coordinates and support Population tuple predicates.
        """
        position = self._positions((axis,))[0]
        return Column(
            self._graph.fingerprint,
            CoordinateSlot(position),
            None,
            "unresolved",
            capture_query(self._graph),
        )

    def _require_column(self, value: Column) -> None:
        if value._input_id != self._graph.fingerprint:
            reject(
                "a Column from this exact Query",
                "different input domain",
                "Locate the Column on this Query, or explicitly align/broadcast.",
                "Query",
            )

    def _result(
        self,
        operation: QueryOperation,
        quantities: tuple[BoundQuantity | ResultQuantity, ...],
        axes: tuple[ComparisonAxis, ...] | None = None,
        inputs: tuple[QueryKeys, ...] = (),
    ) -> Query:
        signature = self._graph.signature
        output_axes = signature.axes if axes is None else axes
        root = ResultQuery(
            (capture_query(self._graph), *inputs), operation, output_axes, signature.owner
        )
        result = object.__new__(Query)
        object.__setattr__(
            result,
            "_graph",
            BoundGraph(
                root,
                BoundSignature(
                    signature.owner, quantities, output_axes, signature.declared_metrics
                ),
            ),
        )
        object.__setattr__(result, "_execute", self._execute)
        return result

    def _combine(
        self,
        other: Query,
        operation: Literal["align", "compare"],
        *,
        match: Correspondence | None,
        how: Literal["exact", "outer"],
        time_axis: Literal["paired", "current"] = "paired",
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Query:
        from marivo.analysis.binding.correspondence import comparison_axes

        if (
            not isinstance(other, Query)
            or other._graph.signature.owner != self._graph.signature.owner
        ):
            reject(
                "a Query from this Session",
                type(other).__name__,
                "Build both endpoints in the same Session.",
                f"Query.{operation}",
            )
        if (
            how not in ("exact", "outer")
            or time_axis not in ("paired", "current")
            or zero_denominator not in ("null", "error")
        ):
            reject(
                "closed correspondence and division policies",
                repr((how, time_axis, zero_denominator)),
                "Choose exact/outer, paired/current and null/error.",
                f"Query.{operation}",
            )
        left, right = self._graph.signature, other._graph.signature
        axes = comparison_axes(
            left.axes, right.axes, match=match, operation=operation, time_axis=time_axis
        )
        if operation == "align":
            quantities = tuple(
                ResultQuantity(
                    f"{role}.{quantity.name}",
                    quantity.unit,
                    quantity.kind if isinstance(quantity, ResultQuantity) else "numeric",
                )
                for role, signature in (("left", left), ("right", right))
                for quantity in signature.quantities
            )
        else:
            lookup = {quantity.name: quantity for quantity in right.quantities}
            if not left.quantities or set(lookup) != {
                quantity.name for quantity in left.quantities
            }:
                reject(
                    "equal nonempty endpoint quantity name sets",
                    repr(tuple(lookup)),
                    "Select and name comparable values on both endpoints.",
                    "Query.compare",
                )
            quantities_list: list[ResultQuantity] = []
            for quantity in left.quantities:
                if quantity.unit != lookup[quantity.name].unit:
                    reject(
                        "matching endpoint units",
                        repr((quantity.unit, lookup[quantity.name].unit)),
                        "Compare quantities with the same declared unit.",
                        "Query.compare",
                    )
                quantities_list.extend(
                    ResultQuantity(
                        f"{quantity.name}.{role}", None if role == "relative" else quantity.unit
                    )
                    for role in ("current", "baseline", "absolute", "relative")
                )
            quantities = tuple(quantities_list)
        root = ResultQuery(
            (capture_query(self._graph), capture_query(other._graph)),
            CombineValues(operation, match, how, time_axis, zero_denominator),
            axes,
            left.owner,
        )
        result = object.__new__(Query)
        object.__setattr__(
            result, "_graph", BoundGraph(root, BoundSignature(left.owner, quantities, axes))
        )
        object.__setattr__(result, "_execute", self._execute)
        return result

    def align(
        self,
        other: Query,
        *,
        match: Correspondence | None = None,
        how: Literal["exact", "outer"] = "exact",
    ) -> Query:
        """Align complete endpoint coordinates and retain independent presence.

        Args: other: Same-Session Query. match: Explicit correspondence or exact bindings.
            how: exact or outer. Returns: A Query with left/right named values.

        Example: ``current.align(previous, match=mv.ordinal())``.
        Constraints: Required endpoint checks complete before correspondence.
        """
        return self._combine(other, "align", match=match, how=how)

    def compare(
        self,
        baseline: Query,
        *,
        match: Correspondence | None = None,
        how: Literal["exact", "outer"] = "exact",
        time_axis: Literal["paired", "current"] = "paired",
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Query:
        """Compare named numeric quantities after complete coordinate correspondence.

        Args: baseline: Same-Session Query. match: Explicit correspondence.
            how: exact or outer. time_axis: paired or current calendar projection.
            zero_denominator: Policy for new relative differences only.

        Returns: current/baseline/absolute/relative values and independent presence.
        Example: ``current.compare(previous, match=mv.previous_year())``.
        Constraints: Relative difference divides by the absolute baseline.
        """
        return self._combine(
            baseline,
            "compare",
            match=match,
            how=how,
            time_axis=time_axis,
            zero_denominator=zero_denominator,
        )

    def select(self, values: Mapping[str, Column | LiteralValue]) -> Query:
        """Project named values while preserving every complete coordinate.

        Args: values: Nonempty mapping of same-input Columns or literals.
        Returns: A lazy Query. Example: ``q.select({'net': q['revenue'] - 10})``.
        Constraints: Selection cannot merge keys or remove upstream checks.
        """
        if not isinstance(values, Mapping) or not values:
            reject(
                "a nonempty named value mapping",
                type(values).__name__,
                "Pass select({'name': column}).",
                "Query.select",
            )
        expressions = []
        quantities = []
        for name, value in values.items():
            if isinstance(value, Column):
                self._require_column(value)
                expressions.append((name, value._expression))
                original = (
                    self._graph.signature.quantities[value._expression.index]
                    if isinstance(value._expression, ValueSlot)
                    else None
                )
                quantities.append(
                    BoundQuantity(name, original.definition, original.unit)
                    if isinstance(original, BoundQuantity)
                    else ResultQuantity(name, value._unit, value._kind)
                )
            else:
                from marivo._definition.types import DefinitionGraphError, check_literal

                try:
                    literal = check_literal(value, role="select value")
                except DefinitionGraphError as error:
                    reject(
                        "a finite immutable literal or same-input Column",
                        str(error),
                        "Select a Column on this Query or pass a supported literal.",
                        "Query.select",
                    )
                if literal is None:
                    reject(
                        "a non-null value literal",
                        "None",
                        "Select a nullable Column or construct a typed nullable operation.",
                        "Query.select",
                    )
                from datetime import datetime, timezone

                if isinstance(literal, datetime) and (
                    literal.tzinfo is None or literal.utcoffset() is None
                ):
                    reject(
                        "a timezone-aware timestamp literal",
                        repr(literal),
                        "Pass an aware datetime; use date for date-valued Columns.",
                        "Query.select",
                    )
                if isinstance(literal, datetime):
                    literal = literal.astimezone(timezone.utc)
                expressions.append((name, ValueLiteral(literal)))
                quantities.append(ResultQuantity(name, None, literal_kind(literal)))
        return self._result(SelectValues(tuple(expressions)), tuple(quantities))

    def _coarsening(
        self,
        by: tuple[Deferred | MemberAxis | ComparisonAxis | CalendarAxis, ...],
        location: Literal["Query.rollup", "Query.share", "Query.reduce"],
    ) -> tuple[
        tuple[ComparisonAxis, ...], tuple[int, ...], tuple[tuple[int, tuple[int, ...]], ...]
    ]:
        from marivo.analysis.dsl.population import _calendar_population

        signature = self._graph.signature
        axes = tuple(
            _calendar_population(axis).axes[0]
            if isinstance(axis, CalendarAxis)
            else axis
            if isinstance(axis, (AxisBinding, CalendarCoordinate, CorrespondenceAxis))
            else _axes((axis,), available=signature.axes)[0][0]
            for axis in by
        )
        positions = []
        maps = []
        for axis in axes:
            if axis in signature.axes:
                positions.append(signature.axes.index(axis))
                continue
            calendars = tuple(
                (index, item)
                for index, item in enumerate(signature.axes)
                if isinstance(item, CalendarCoordinate)
            )
            if not isinstance(axis, CalendarCoordinate) or len(calendars) != 1:
                reject(
                    "retained axes or one lawful calendar coarsening",
                    repr(axis),
                    "Use existing axes; read new attributes with drill.",
                    location,
                )
            position, source = calendars[0]
            mapping = []
            if source.timezone != axis.timezone or source.snapshot_digest != axis.snapshot_digest:
                reject(
                    "the same frozen calendar authority",
                    repr(axis),
                    "Coarsen the original CalendarAxis.",
                    location,
                )
            for nominal_start, nominal_end, start, end in source.cells:
                choices = tuple(
                    index
                    for index, (nominal_a, nominal_b, a, b) in enumerate(axis.cells)
                    if nominal_a <= nominal_start
                    and nominal_end <= nominal_b
                    and a <= start
                    and end <= b
                )
                if len(choices) != 1:
                    reject(
                        "one containing target bucket per original actual interval",
                        repr((start, end)),
                        "Use a lawful coarsening without changing scope.",
                        location,
                    )
                mapping.append(choices[0])
            if source.cells and (
                min(cell[2] for cell in source.cells),
                max(cell[3] for cell in source.cells),
            ) != (min(cell[2] for cell in axis.cells), max(cell[3] for cell in axis.cells)):
                reject(
                    "the original complete scope",
                    repr(axis),
                    "Coarsen without extending or truncating the scope.",
                    location,
                )
            positions.append(position)
            maps.append((len(positions) - 1, tuple(mapping)))
        if len(set(positions)) != len(positions):
            reject("distinct output axes", repr(axes), "Retain each axis once.", location)
        return axes, tuple(positions), tuple(maps)

    def rollup(
        self, *, by: tuple[Deferred | MemberAxis | ComparisonAxis | CalendarAxis, ...] = ()
    ) -> Query:
        """Recompute original Metrics from this invocation's selected contributions.

        Args: by: Retained axes or a lawful coarsened calendar.
        Returns: A Query at the requested grain. Example: ``q.rollup(by=())``.
        Constraints: Fine-grid checks complete first; completed values are never summed.
        """
        from marivo._definition.expressions import MetricWindow
        from marivo._definition.graph import walk

        signature = self._graph.signature
        if not signature.quantities or any(
            not isinstance(quantity, BoundQuantity) for quantity in signature.quantities
        ):
            reject(
                "only original Metric quantities",
                "derived or absent quantities",
                "Select original columns from a source Query before rollup.",
                "Query.rollup",
            )
        axes, positions, maps = self._coarsening(by, "Query.rollup")
        nodes = tuple(
            quantity.definition
            for quantity in signature.quantities
            if isinstance(quantity, BoundQuantity)
        )
        if any(
            isinstance(node, MetricWindow)
            for node in walk(nodes, resolve=dict(signature.declared_metrics).get).order
        ):
            for axis in signature.axes:
                if isinstance(axis, CalendarCoordinate) and axis not in axes:
                    reject(
                        "the unchanged window time axis and endpoints",
                        repr(axes),
                        "Retain the exact calendar axis; create a new observe for another window grain.",
                        "Query.rollup",
                    )
        return self._result(RollupValues(tuple(positions), tuple(maps)), signature.quantities, axes)

    def share(
        self,
        name: str,
        *,
        partition_by: tuple[Deferred | MemberAxis | ComparisonAxis | CalendarAxis, ...] = (),
        zero_denominator: Literal["null", "error"] = "null",
    ) -> Query:
        """Add an additive contribution share using the same actual receiver input.

        Args: name: Existing original numeric quantity. partition_by: Retained axes.
            zero_denominator: null or error for the new share only.

        Returns: Original values and name.share. Example: ``q.share('sales')``.
        Constraints: Later where/top retains this denominator; nonadditive quantities reject.
        """
        signature = self._graph.signature
        names = tuple(quantity.name for quantity in signature.quantities)
        if name not in names or not isinstance(
            signature.quantities[names.index(name)], BoundQuantity
        ):
            reject(
                "one existing original additive Metric",
                repr(name),
                f"Choose an original quantity from {names!r}.",
                "Query.share",
            )
        if zero_denominator not in ("null", "error"):
            reject(
                "null or error",
                repr(zero_denominator),
                "Choose the new share division policy.",
                "Query.share",
            )
        _, partitions, calendar_maps = self._coarsening(partition_by, "Query.share")
        from marivo._definition.expressions import MetricWindow
        from marivo._definition.graph import walk

        quantity = signature.quantities[names.index(name)]
        assert isinstance(quantity, BoundQuantity)
        if any(
            isinstance(node, MetricWindow)
            for node in walk(
                (quantity.definition,), resolve=dict(signature.declared_metrics).get
            ).order
        ) and any(
            isinstance(axis, CalendarCoordinate)
            and (
                index not in partitions
                or any(partitions[position] == index for position, _ in calendar_maps)
            )
            for index, axis in enumerate(signature.axes)
        ):
            reject(
                "the unchanged window time axis in partition_by",
                repr(partition_by),
                "Retain the window calendar axis when computing spatial shares.",
                "Query.share",
            )
        return self._result(
            ShareValues(names.index(name), partitions, zero_denominator, calendar_maps),
            (*signature.quantities, ResultQuantity(name + ".share", None)),
        )

    def drill(self, *, add: tuple[Deferred | AxisBinding, ...]) -> Query:
        """Read new classifications only for this source Query's selected fibers.

        Args: add: New single-valued Field bindings.
        Returns: A Query retaining original metrics and parent coordinates.
        Example: ``q.top(5,by=q['sales']).drill(add=(channel,))``.
        Constraints: Parent checks complete before version-exact attribute reads.
        """
        signature = self._graph.signature
        if not signature.quantities or any(
            not isinstance(quantity, BoundQuantity) for quantity in signature.quantities
        ):
            reject(
                "original source Metric columns",
                "derived or absent quantities",
                "Drill a source Query, selecting only original columns when needed.",
                "Query.drill",
            )
        axes, _ = _axes(add)
        if not axes or any(
            not isinstance(axis, AxisBinding) or axis.field is None for axis in axes
        ):
            reject(
                "new single-valued Field axes",
                repr(axes),
                "Pass declared classification fields in add=(field,).",
                "Query.drill",
            )
        if any(axis in signature.axes for axis in axes):
            reject(
                "new distinct classification axes",
                repr(axes),
                "Retain existing axes and add each new field once.",
                "Query.drill",
            )
        reads = tuple(
            FieldRead(axis.field, axis.entity, axis.path, axis.at)
            for axis in axes
            if isinstance(axis, AxisBinding) and axis.field is not None
        )
        return self._result(DrillValues(reads), signature.quantities, (*signature.axes, *axes))

    def where(self, predicate: Column | PopulationPredicate) -> Query:
        """Select True coordinates; nullable predicate values are excluded.

        Args: predicate: Same-input Column predicate or existing-coordinate membership.
        Returns: A lazy Query. Example: ``q.where(q['revenue'] > 100)``.
        Constraints: Preserves complete keys, relative display order and prior checks.
        """
        condition: ValueExpression | PopulationCondition
        domains: tuple[PopulationDependency, ...] = ()
        inputs: tuple[QueryKeys, ...] = ()
        if isinstance(predicate, Column):
            self._require_column(predicate)
            if predicate._kind != "boolean":
                reject(
                    "a boolean Column",
                    predicate._kind,
                    "Compare values to form a predicate.",
                    "Query.where",
                )
            condition = predicate._expression
        elif isinstance(predicate, PopulationPredicate):
            # Population owns the coordinate-subset and Session checks.
            self.population().where(predicate)
            condition = predicate.condition
            from marivo._definition.relations import condition_populations
            from marivo.analysis.compiler.populations import population_atoms, population_inputs

            domains = tuple(
                dict.fromkeys(
                    atom
                    for domain in condition_populations(condition)
                    for atom in population_atoms(domain)
                )
            )
            inputs = tuple(
                captured
                for domain in domains
                for captured in population_inputs(domain, self._graph.signature.owner)
            )
        else:
            reject(
                "a Column or Population predicate",
                type(predicate).__name__,
                "Use a comparison or mv.in_population(...).",
                "Query.where",
            )
        return self._result(
            FilterValues(condition, domains), self._graph.signature.quantities, inputs=inputs
        )

    def top(
        self,
        n: int,
        *,
        by: Column,
        descending: bool = True,
        partition_by: tuple[Deferred | MemberAxis | ComparisonAxis, ...] = (),
    ) -> Query:
        """Select at most n complete keys per partition from one actual input.

        Args: n: Nonnegative integer. by: Same-input score. descending: Largest first.
            partition_by: Existing axes; empty means global ranking.

        Returns: A lazy Query with saved display order.
        Example: ``q.top(10, by=q['revenue'])``.
        Constraints: NA sorts last; full canonical keys break ties.
        """
        if type(n) is not int or n < 0 or type(descending) is not bool:
            reject(
                "a nonnegative integer n and boolean descending",
                repr((n, descending)),
                "Choose a finite Top-N selection.",
                "Query.top",
            )
        self._require_column(by)
        return self._result(
            TopValues(n, by._expression, descending, self._positions(partition_by)),
            self._graph.signature.quantities,
        )

    def reduce(
        self,
        values: Mapping[str, _Statistic],
        *,
        by: tuple[Deferred | MemberAxis | ComparisonAxis | CalendarAxis, ...] = (),
    ) -> Query:
        """Complete ignore-NA value statistics over current complete coordinates.

        Args: values: Nonempty named unfinished statistics. by: Existing grouping axes.
        Returns: A new Query; each statistic uses its independent valid mask.
        Example: ``q.reduce({'mean': q['revenue'].mean(), 'rows': mv.row_count()})``.
        Constraints: Empty scalar inputs still produce one result coordinate.
        """
        if not isinstance(values, Mapping) or not values:
            reject(
                "a nonempty named statistic mapping",
                type(values).__name__,
                "Pass reduce({'total': q['value'].sum()}).",
                "Query.reduce",
            )
        for statistic in values.values():
            if not isinstance(statistic, _Statistic) or statistic.input_id not in (
                None,
                self._graph.fingerprint,
            ):
                reject(
                    "statistics from this exact Query",
                    type(statistic).__name__,
                    "Build Column.sum/mean on this Query or use mv.row_count().",
                    "Query.reduce",
                )
        axes, positions, calendar_maps = self._coarsening(by, "Query.reduce")
        return self._result(
            ReduceValues(
                tuple((name, statistic.definition) for name, statistic in values.items()),
                positions,
                calendar_maps,
            ),
            tuple(ResultQuantity(name, statistic.unit) for name, statistic in values.items()),
            axes,
        )

    @classmethod
    def _from_population(
        cls, population: Population, owner: Owner, execute: Callable[[BoundGraph], Artifact]
    ) -> Query:
        from marivo._definition.observations import CoordinateBinding, ObservationBinding

        existing_owner = _owner(population.definition)
        if existing_owner is not None and existing_owner != owner:
            reject(
                "a Population from this Session",
                str(existing_owner),
                "Rebuild the Population in this Session.",
                "session.query",
            )
        axes = population.definition.axes
        from marivo._definition.graph import walk

        closure = walk((population.definition,), resolve=lambda _: None)
        if any(isinstance(node, QueryKeys) for node in closure.order):
            from marivo._definition.query import PopulationValues
            from marivo.analysis.compiler.populations import population_inputs

            inputs = population_inputs(population.definition, owner)
            result = object.__new__(cls)
            object.__setattr__(
                result,
                "_graph",
                BoundGraph(
                    ResultQuery(inputs, PopulationValues(population.definition), axes, owner),
                    BoundSignature(owner, (), axes),
                ),
            )
            object.__setattr__(result, "_execute", execute)
            return result
        binding = ObservationBinding(
            None,
            None,
            CoordinateBinding(axes, "grouped", population.definition, population.memberships),
            (),
        )
        signature = BoundSignature(owner, (), axes, observation=binding)
        root = ObserveQuery((), axes, owner, population.definition)
        result = object.__new__(cls)
        object.__setattr__(result, "_graph", BoundGraph(root, signature))
        object.__setattr__(result, "_execute", execute)
        return result

    def population(self) -> Population:
        """Capture this Query's complete output coordinates as a lazy domain.

        Returns: A Population retaining this Query's selection, period and Session.
        Example: ``cohort = query.population()``.
        Constraints: This does not execute or publish the Query.
        """
        signature = self._graph.signature
        return Population(
            PopulationProgram(
                capture_query(self._graph),
                signature.owner,
                signature.axes,
                signature.observation.coordinates.memberships if signature.observation else (),
            )
        )

    def contract(self) -> AnalysisContract:
        """Describe captured outputs, bound context and unresolved physical types.

        Args: None.
        Returns: A bounded AnalysisContract with known definition facts.
        Example: ``query.contract().show()``.
        Constraints: No business or schema reads, Run admission or capability guessing.
        """
        quantities = self._graph.signature.quantities
        observation = self._graph.signature.observation
        axes = self._graph.signature.axes
        actions = [
            AnalysisAction("query.execute()", "analysis.Query.execute"),
            AnalysisAction("query.population()", "analysis.Query.population"),
            AnalysisAction("query.reduce({'rows': mv.row_count()})", "analysis.Query.reduce"),
        ]
        if quantities:
            name = quantities[0].name
            actions.extend(
                (
                    AnalysisAction(
                        f"query.select({{{name!r}: query[{name!r}]}})", "analysis.Query.select"
                    ),
                    AnalysisAction(
                        f"query.where(~query[{name!r}].is_null())", "analysis.Query.where"
                    ),
                )
            )
        return AnalysisContract(
            kind="Query",
            phase="logical",
            actions=tuple(actions),
            domain=self._graph.signature.observation.coordinates.mode
            if self._graph.signature.observation
            else "result coordinates"
            if axes
            else "scalar singleton",
            quantity=f"{len(quantities)} named Metric outputs",
            required_parts=(),
            retained_parts=(),
            _facts=(
                ("definition", self._graph.fingerprint),
                ("physical_dtype", "unresolved; schema-only preparation belongs to execution"),
                ("execution", "Query.execute() atomically publishes terminal output"),
                ("output", "terminal Artifact; preview, complete pandas export and saved facts"),
                ("owner", self._graph.signature.owner.session_id),
                ("dependencies", str(len(self._graph.root.inputs)))
                if isinstance(self._graph.root, ResultQuery)
                else ("dependencies", "source definitions"),
                ("operation", type(self._graph.root.operation).__name__)
                if isinstance(self._graph.root, ResultQuery)
                else ("operation", "observe"),
                (
                    "period",
                    repr(self._graph.signature.observation.period)
                    if self._graph.signature.observation
                    and self._graph.signature.observation.period
                    else "None; no additional time filter",
                ),
                ("axes", str(len(self._graph.signature.axes))),
                (
                    "where",
                    repr(observation.condition)[:400]
                    if observation and observation.condition is not None
                    else "None",
                ),
                *(
                    (
                        f"axis[{i}]",
                        (
                            axis.field.ref.path[:100]
                            if axis.field is not None
                            else axis.entity.ref.path[:100]
                        )
                        if isinstance(axis, AxisBinding)
                        else f"calendar={axis.grain[:80]} timezone={axis.timezone} buckets={len(axis.cells)}"
                        if isinstance(axis, CalendarCoordinate)
                        else f"correspondence={axis.identity[:16]}",
                    )
                    for i, axis in enumerate(axes[:6])
                ),
                ("axes_omitted", str(max(0, len(axes) - 6))),
                (
                    "contributions",
                    str(len(self._graph.signature.observation.contributions))
                    if self._graph.signature.observation
                    else "0",
                ),
                *(
                    (
                        f"source[{i}]",
                        f"{c.root.ref.path[:100]} occurrence={c.occurrence} intrinsic_conditions={len(c.intrinsic_conditions)} reads={len(c.reads)}",
                    )
                    for i, c in enumerate(
                        self._graph.signature.observation.contributions[:6]
                        if self._graph.signature.observation
                        else ()
                    )
                ),
                (
                    "sources_omitted",
                    str(max(0, len(observation.contributions) - 6)) if observation else "0",
                ),
                *(
                    (f"output[{index}]", f"{q.name[:100]} unit={q.unit or 'undeclared'}")
                    for index, q in enumerate(quantities[:8])
                ),
                ("outputs_omitted", str(max(0, len(quantities) - 8))),
            ),
        )

    def execute(self) -> Artifact:
        """Explicitly request evaluation and committed terminal output.

        Args: None.
        Returns: A terminal Artifact after checked execution and atomic publication.
        Example: ``artifact = query.execute()``.
        Constraints: Preparation failures create no Run. Each admitted call is a new evaluation; only committed Parquet and output records survive publication. The returned Artifact reads committed output without current-source or Catalog access.
        """
        return self._execute(self._graph)

    def __bool__(self) -> bool:
        reject(
            "an explicitly executed Query",
            "implicit truth conversion",
            "Call query.execute() explicitly when execution is available.",
            "Query.__bool__",
        )
