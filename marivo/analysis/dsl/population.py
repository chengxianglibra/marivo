"""Immutable typed coordinate domains and explicit contribution mappings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from ibis.common.deferred import Deferred, Just

from marivo._definition import population_builders as build
from marivo._definition.correspondence import (
    ComparisonAxis,
)
from marivo._definition.deferred import PopulationContainsResolver, RelationshipParameter
from marivo._definition.graph import computation_fingerprint
from marivo._definition.grouping import _CoordinateGrouping, _MembershipGrouping
from marivo._definition.population_parameters import (
    ContainsParameter,
    CoordinatesParameter,
    IdentityParameter,
    MembershipParameter,
    ProductParameter,
    has_parameters,
    parameter,
)
from marivo._definition.relations import (
    CalendarKeys,
    LiteralPopulation,
    MembershipBinding,
    PopulationDependency,
    PopulationParameter,
    PopulationProgram,
)
from marivo._definition.relations import (
    condition_populations as condition_populations,
)
from marivo._definition.relations import (
    population_keys as population_keys,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    DefinitionGraphError,
    FieldRead,
    IntervalCoordinate,
    Owner,
)
from marivo._grouping import Grouping
from marivo._symbolic import EntitySymbol, RelationshipSymbol
from marivo._temporal import CalendarAxis, InstantAnchor, TimeScope
from marivo.analysis._scope import scope_summary
from marivo.analysis.core.model import reject
from marivo.analysis.dsl._frame_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.semantic._deferred_semantics import consume_field, consume_predicate
from marivo.semantic.expressions import Entity, Relationship, entity_exact, relationship_parameter

_T = TypeVar("_T")


def _closed(action: Callable[[], _T], location: str) -> _T:
    try:
        return action()
    except DefinitionGraphError as error:
        reject(
            "a valid complete coordinate domain",
            str(error),
            "Use compatible complete axes, connected paths and one Session owner.",
            location,
        )


def _read(value: Deferred) -> FieldRead:
    field = consume_field(value)
    return field.field_read or FieldRead(field.reference, field.root)


def _axis(read: FieldRead) -> AxisBinding:
    return build.axis(read)


def _axes(
    by: tuple[Deferred | Grouping | TimeScope, ...],
) -> tuple[ComparisonAxis, ...]:
    axes: list[ComparisonAxis] = []
    for item in by:
        if isinstance(item, TimeScope):
            axes.append(_interval_coordinate(item))
        elif isinstance(item, Grouping):
            binding = item._binding
            if isinstance(binding, MembershipBinding):
                reject(
                    "ordinary coordinate groupings",
                    "membership grouping",
                    "Put membership rules directly in Session.observe.by or Metric.per samples.",
                    "population",
                )
            else:
                axes.append(binding)
        elif isinstance(item, Deferred):
            field = consume_field(item)
            axes.append(_axis(field.field_read or FieldRead(field.reference, field.root)))
        else:
            reject(
                "a semantic field or Grouping",
                type(item).__name__,
                "Use a resolved field, mv.identity(entity), or population.axes.",
                "population",
            )
    return _closed(lambda: build.axes(tuple(axes)), "population")


def _native(value: object) -> Deferred:
    assert isinstance(value, Deferred)
    return value


def _facts_path(
    values: tuple[Deferred | RelationshipParameter, ...],
) -> tuple[RelationshipParameter, ...]:
    if any(not isinstance(value, RelationshipParameter) for value in values):
        reject(
            "closed relationship facts",
            repr(values),
            "Load the symbolic model and obtain closed Catalog relationships.",
            "population",
        )
    return tuple(value for value in values if isinstance(value, RelationshipParameter))


def _capture_path(
    values: tuple[Relationship | RelationshipSymbol, ...],
) -> tuple[Deferred | RelationshipParameter, ...]:
    if any(not isinstance(value, (Relationship, Deferred)) for value in values):
        reject(
            "a directed path of Relationship values or native symbols",
            repr(values),
            "Use Catalog relationships or ms.ref.relationship(...).",
            "population",
        )
    return tuple(
        value if isinstance(value, Deferred) else relationship_parameter(value) for value in values
    )


def identity(
    target: Entity | EntitySymbol | tuple[Relationship | RelationshipSymbol, ...],
    *,
    at: InstantAnchor | None = None,
) -> Grouping | Deferred:
    """Capture grouping by one complete Entity identity, without selecting members.

    Args:
        target: A closed Entity, native Entity symbol, or nonempty directed relationship path.
        at: Exact version selection instant for a versioned target.

    Returns:
        An immutable Grouping for closed input, or a Deferred parameter for symbols; no source keys are enumerated.

    Example:
        ``session.observe({'sales': sales}, by=(mv.identity(customer),))``.

    Constraints: Explicit paths infer their target Entity. Observation binds every
        primary-key component through a single-valued route. Use mv.coordinates
        for a finite requested key domain, or session.members for source members.
    """
    if not isinstance(target, (Entity, Deferred, tuple)):
        reject(
            "a closed Entity, native Entity symbol or relationship path",
            type(target).__name__,
            "Use a Catalog Entity, ms.ref.entity(...), or explicit relationship tuple.",
            "identity",
        )
    captured = (
        target
        if isinstance(target, Deferred)
        else _capture_path(target)
        if isinstance(target, tuple)
        else entity_exact(target)
    )
    if isinstance(captured, Deferred) or has_parameters(captured):
        return _closed(lambda: parameter(IdentityParameter(captured, at)), "identity")
    facts = _facts_path(captured) if isinstance(captured, tuple) else captured
    return _closed(lambda: _CoordinateGrouping(build.identity(facts, at)), "identity")


def _owner(definition: PopulationDependency) -> Owner | None:
    return build.owner(definition)


@dataclass(frozen=True, slots=True, repr=False)
class Population(PopulationParameter):
    """A lazy finite set of complete coordinates; use a Session to execute it.

    Acquire through mv.coordinates/product, session.members/population_from or
    existing Population operations. Internal definition/frame inputs are not a
    supported constructor.
    """

    _frame_inputs: tuple[FrameKeyInput, ...] = field(default=(), compare=False, repr=False)

    @property
    def axes(self) -> tuple[Grouping, ...]:
        """Return all complete coordinate axes in definition order.

        Returns: Ordered single-coordinate Grouping descriptors; reading is source/schema free.
        Example: ``population.project(by=population.axes)``.
        Constraints: Does not import keys, selection or membership allocation bindings.
        """
        return tuple(_CoordinateGrouping(axis) for axis in self._definition.axes)

    def count(self) -> Deferred:
        """Capture the cardinality of this complete coordinate domain.

        Args: None.
        Returns: A closed Metric Deferred, independent of fact contributions.
        Example: ``session.observe({'people': people.count()})``.
        Constraints: Counts complete tuples, including field Nulls; performs no reads.
        Grouping coordinates must already belong to the domain; use classify for new fields.
        """
        from marivo._definition.deferred import PopulationCountResolver

        return Deferred(PopulationCountResolver(self))

    def __bool__(self) -> bool:
        reject(
            "a lazy Population definition",
            "Python truth evaluation",
            "Compute population.count() through session.observe to inspect its size.",
            "Population",
        )

    def __repr__(self) -> str:
        return f"<Population id={computation_fingerprint(self._definition)[:16]} axes={len(self._definition.axes)}; use .show()>"

    def show(self) -> None:
        """Print bounded captured domain facts without enumerating source keys.

        Returns: None; prints coordinates, scope, ownership and captured key count.
        Example: ``population.show()``.
        Constraints: No schema/source reads or evaluation; full axes remain in axes.
        """
        owner = _owner(self._definition)
        facts = (
            ("axes", str(len(self._definition.axes))),
            ("definition", computation_fingerprint(self._definition)),
            ("scope", scope_summary(self._definition, max_bytes=600)),
            ("owner", owner.session_id if owner is not None else "unbound"),
            (
                "keys",
                f"{len(self._definition.keys)} captured complete literal coordinates"
                if isinstance(self._definition, LiteralPopulation)
                else "unevaluated program; show does not enumerate keys",
            ),
            *(
                (
                    f"axis[{index}]",
                    f"calendar={axis.grain} timezone={axis.timezone} cells={len(axis.cells)}"
                    if isinstance(axis, CalendarCoordinate)
                    else f"interval={axis.start}/{axis.end} timezone={axis.timezone}"
                    if isinstance(axis, IntervalCoordinate)
                    else f"{axis.field.ref.key if axis.field else axis.entity.ref.key}; path={axis.path}; at={axis.at}",
                )
                for index, axis in enumerate(self._definition.axes[:6])
            ),
        )
        print("logical Population")
        omitted = 0
        for name, value in facts:
            if len(value.encode("utf-8")) > 600:
                print(f"{name}=omitted ({len(value)} characters)")
                omitted += 1
            else:
                print(f"{name}={value}")
        if omitted:
            print(f"facts_omitted={omitted}; complete coordinates: population.axes")
        if len(self._definition.axes) > 6:
            print(f"axes_omitted={len(self._definition.axes) - 6}; complete axes: population.axes")
        print("Help: analysis.population")

    def project(self, *, by: tuple[Deferred | Grouping, ...]) -> Population | Deferred:
        """Select/reorder existing axes and deduplicate complete projected tuples.

        Args: by: Existing bound axes or their captured fields.
        Returns: A lazy domain with complete projected tuples deduplicated.
        Example: ``population.project(by=(population.axes[0],))``.
        Constraints: Projection cannot read new attributes.
        """
        if has_parameters(by):
            return _native(Deferred(Just(self)).project(by=by))
        return Population(
            _closed(lambda: build.project(self._definition, _axes(by)), "Population.project"),
            self._frame_inputs,
        )

    def _set(
        self,
        other: Population | Deferred,
        operation: Literal["union", "intersection", "difference"],
    ) -> Population | Deferred:
        if isinstance(other, Deferred):
            return _native(getattr(Deferred(Just(self)), operation)(other))
        definition = _closed(
            lambda: build.set_operation(self._definition, other._definition, operation),
            f"population.{operation}",
        )
        return Population(
            definition,
            merge_frame_key_inputs(self._frame_inputs, other._frame_inputs, owner=definition.owner),
        )

    def union(self, other: Population | Deferred) -> Population | Deferred:
        """Union matching complete domains; keys remain lazy and deduplicated."""
        return self._set(other, "union")

    def intersection(self, other: Population | Deferred) -> Population | Deferred:
        """Intersect matching complete domains without reading additional fields."""
        return self._set(other, "intersection")

    def difference(self, other: Population | Deferred) -> Population | Deferred:
        """Subtract matching complete domain keys from this domain."""
        return self._set(other, "difference")

    def classify(self, *, by: tuple[Deferred | Grouping, ...]) -> Population | Deferred:
        """Read direct member attributes into a correlated coordinate domain.

        Args: by: Direct fields owned by this identity Entity, or its existing identity.
        Returns: A lazy complete domain of correlated classification tuples.
        Example: ``members.classify(by=(members.axes[0], region))``.
        Constraints: Requires one identity axis; routed fields and time axes are unsupported.
        """
        if has_parameters(by):
            return _native(Deferred(Just(self)).classify(by=by))
        return Population(
            _closed(lambda: build.classify(self._definition, _axes(by)), "population.classify"),
            self._frame_inputs,
        )

    def where(self, predicate: Deferred) -> Population | Deferred:
        """Filter complete coordinates using closed Population membership logic.

        Args: predicate: ``in_population(other)`` combined with ``&``, ``|`` or ``~``.
        Returns: A lazy subset with unchanged axes.
        Example: ``domain.where(mv.in_population(allowed))``.
        Constraints: Every predicate domain must project from the receiver axes.
        """
        if has_parameters(predicate):
            return _native(Deferred(Just(self)).where(predicate))
        frame_inputs = collect_frame_key_inputs(predicate)
        definition = _closed(
            lambda: build.where(self._definition, consume_predicate(predicate).node),
            "population.where",
        )
        return Population(
            definition,
            merge_frame_key_inputs(self._frame_inputs, frame_inputs, owner=definition.owner),
        )

    def image(
        self, *, via: tuple[Relationship | RelationshipSymbol, ...], at: InstantAnchor | None = None
    ) -> Population | Deferred:
        """Map selected identities to reached identities through a directed path.

        Args: via: Nonempty directed relationship path. at: Exact target version anchor.
        Returns: A deduplicated target identity domain, preserving missing-reference keys.
        Example: ``orders.image(via=(order_customer,))``.
        Constraints: Requires one identity axis and explicit connected relationships.
        """
        captured = _capture_path(via)
        if has_parameters(captured):
            return _native(Deferred(Just(self)).image(via=captured, at=at))
        return Population(
            _closed(
                lambda: build.image(self._definition, _facts_path(captured), at), "population.image"
            ),
            self._frame_inputs,
        )

    def preimage(
        self, targets: Population | Deferred, *, via: tuple[Relationship | RelationshipSymbol, ...]
    ) -> Population | Deferred:
        """Keep receiver identities that reach a selected target identity.

        Args: targets: One compatible target identity domain. via: Directed path.
        Returns: A lazy subset of this domain.
        Example: ``orders.preimage(customers, via=(order_customer,))``.
        Constraints: Relationship mapping uses complete declared identity keys.
        """
        captured = _capture_path(via)
        if isinstance(targets, Deferred) or has_parameters(captured):
            return _native(Deferred(Just(self)).preimage(targets, via=captured))
        definition = _closed(
            lambda: build.preimage(self._definition, targets._definition, _facts_path(captured)),
            "population.preimage",
        )
        return Population(
            definition,
            merge_frame_key_inputs(
                self._frame_inputs, targets._frame_inputs, owner=definition.owner
            ),
        )


def in_population(population: Population | Deferred) -> Deferred:
    """Match complete coordinates projected onto a Population's bound axes.

    Args:
        population: The allowed finite or lazy coordinate domain.

    Returns:
        A native Deferred boolean leaf, combined with fields using &, | and ~.

    Example:
        ``domain.where(mv.in_population(allowed))``.

    Constraints:
        Captures complete tuples with null-safe matching and performs no
        source access. Population.where accepts only membership combinations;
        source consumers may also combine field conditions.
    """
    if isinstance(population, Deferred):
        return _closed(lambda: parameter(ContainsParameter(population)), "in_population")
    if not isinstance(population, Population):
        reject(
            "a Population coordinate domain",
            type(population).__name__,
            "Construct a Population with coordinates, members or population_from.",
            "in_population",
        )
    return Deferred(PopulationContainsResolver(population))


def _interval_coordinate(scope: TimeScope) -> IntervalCoordinate:
    from marivo._temporal import _SessionTimeScope, anchor_from_datetime
    from marivo.analysis.core.time_grid import instant

    if scope.kind == "absolute" and not isinstance(scope, _SessionTimeScope):
        reject(
            "a TimeScope with a frozen timezone",
            repr(scope),
            "Construct the scope with session.time_scope(...).",
            "population.interval",
        )
    zone = scope.boundary_timezone
    start = anchor_from_datetime(instant(scope.start, zone))
    end = anchor_from_datetime(instant(scope.end, zone))
    return IntervalCoordinate(
        f"interval:{start.isoformat()}:{end.isoformat()}:{zone}", start, end, zone
    )


def _interval_population(scope: TimeScope) -> Population:
    from marivo._definition.relations import IntervalKeys

    coordinate = _interval_coordinate(scope)
    return Population(PopulationProgram(IntervalKeys(coordinate), None, (coordinate,)))


def _calendar_population(calendar: CalendarAxis) -> Population:
    coordinate = calendar._binding
    return Population(PopulationProgram(CalendarKeys(coordinate), None, (coordinate,)))


def membership(
    by: Deferred | tuple[Deferred, ...],
    *,
    via: tuple[tuple[Relationship | RelationshipSymbol, ...], ...],
    unmatched: Literal["exclude", "null"],
) -> Grouping | Deferred:
    """Capture correlated member fields and explicit allocation paths.

    Args: by: One field or nonempty tuple from one member Entity. via: Ordered
        directed paths, possibly empty. unmatched: ``exclude`` or ``null``.

    Returns: An immutable Grouping for closed input, or a Deferred parameter for symbols.
    Example: ``mv.membership(tag, via=((order_tags,),), unmatched='exclude')``.
    Constraints: Captures metadata only; no source or Session is opened.
    """
    fields = tuple(by) if isinstance(by, tuple) else (by,)
    paths = tuple(_capture_path(path) for path in via)
    if has_parameters((fields, paths)):
        return _closed(
            lambda: parameter(MembershipParameter(fields, paths, unmatched)), "membership"
        )
    reads = tuple(_read(item) for item in fields)
    return _closed(
        lambda: _MembershipGrouping(
            build.membership(reads, tuple(_facts_path(path) for path in paths), unmatched)
        ),
        "membership",
    )


def coordinates(
    *, by: tuple[Deferred | Grouping, ...], keys: tuple[tuple[CoordinateValue, ...], ...]
) -> Population | Deferred:
    """Capture a complete finite field and identity coordinate domain.

    Args:
        by: Ordered fields or identity groupings.
        keys: Complete coordinate tuples.

    Returns:
        A Population for closed axes, or a Deferred parameter for symbols, preserving empty and null classification keys.

    Example:
        ``mv.coordinates(by=(region,), keys=(('east',), ('west',)))``.

    Constraints:
        Each identity axis value is a complete non-null primary-key tuple.
        Literal identities do not prove source presence. Use product for time domains.
    """
    captured_keys = tuple(
        tuple(tuple(value) if isinstance(value, (tuple, list)) else value for value in key)
        for key in keys
    )
    captured_by = tuple(by)
    if has_parameters(captured_by):
        return _closed(
            lambda: parameter(CoordinatesParameter(captured_by, captured_keys)), "coordinates"
        )
    return Population(
        _closed(lambda: build.coordinates(_axes(captured_by), captured_keys), "coordinates")
    )


def product(*factors: Population | Deferred | CalendarAxis | TimeScope) -> Population | Deferred:
    """Form a lazy Cartesian product of disjoint coordinate domains.

    Args: factors: At least two Populations with distinct axes and one owner.
    Returns: A Population for closed factors, or a Deferred parameter for symbols, with complete factor-key combinations.
    Example: ``mv.product(regions, channels)``.
    Constraints: An empty factor makes the complete product empty.
    """
    captured = tuple(factors)
    if has_parameters(captured):
        return _closed(lambda: parameter(ProductParameter(captured)), "product")
    populations: list[Population] = []
    for factor in captured:
        if isinstance(factor, CalendarAxis):
            populations.append(_calendar_population(factor))
        elif isinstance(factor, TimeScope):
            populations.append(_interval_population(factor))
        elif isinstance(factor, Population):
            populations.append(factor)
        else:
            reject(
                "a Population, CalendarAxis or TimeScope",
                type(factor).__name__,
                "Load symbolic parameters before execution.",
                "product",
            )
    definition = _closed(
        lambda: build.product(tuple(factor._definition for factor in populations)), "product"
    )
    return Population(
        definition,
        merge_frame_key_inputs(
            *(factor._frame_inputs for factor in populations), owner=definition.owner
        ),
    )
