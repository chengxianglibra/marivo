"""Immutable typed coordinate domains and explicit contribution mappings."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import (
    ComparisonAxis,
    coordinate_matches,
    coordinate_position,
)
from marivo._definition.deferred import PopulationContainsResolver, RelationshipParameter
from marivo._definition.graph import computation_fingerprint
from marivo._definition.grouping import _CoordinateGrouping, _MembershipGrouping
from marivo._definition.relations import (
    CalendarKeys,
    LiteralPopulation,
    MembershipBinding,
    PopulationClassify,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationParameter,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
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
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    Owner,
)
from marivo._grouping import Grouping
from marivo._temporal import CalendarAxis, InstantAnchor, TimeScope
from marivo.analysis._scope import scope_summary
from marivo.analysis.core.model import reject
from marivo.analysis.dsl._frame_inputs import (
    FrameKeyInput,
    collect_frame_key_inputs,
    merge_frame_key_inputs,
)
from marivo.semantic._deferred_semantics import consume_field, consume_predicate
from marivo.semantic.expressions import Entity, Relationship


def _axis(read: FieldRead) -> AxisBinding:
    return AxisBinding(read.entity, read.field, read.path, "field", read.at)


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
    if sum(isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in axes) > 1:
        reject(
            "at most one output time axis",
            repr(axes),
            "Use one TimeScope or CalendarAxis.",
            "population",
        )
    if any(
        coordinate_matches(left, right)
        for index, left in enumerate(axes)
        for right in axes[index + 1 :]
    ):
        reject(
            "distinct coordinate axes",
            "duplicate or conflicting field bindings",
            "Remove repeated axes.",
            "population",
        )
    return tuple(axes)


def identity(
    target: Entity | tuple[Relationship, ...],
    *,
    at: InstantAnchor | None = None,
) -> Grouping:
    """Capture grouping by one complete Entity identity, without selecting members.

    Args:
        target: A resolved Entity or nonempty connected directed relationship path.
        at: Exact version selection instant for a versioned target.

    Returns:
        An immutable identity Grouping; no source keys are enumerated.

    Example:
        ``session.observe({'sales': sales}, by=(mv.identity(customer),))``.

    Constraints: Explicit paths infer their target Entity. Observation binds every
        primary-key component through a single-valued route. Use mv.coordinates
        for a finite requested key domain, or session.members for source members.
    """
    if at is not None and (not isinstance(at, InstantAnchor) or at.utcoffset() is None):
        reject(
            "an exact InstantAnchor", type(at).__name__, "Use session.instant(...).", "identity.at"
        )
    path: tuple[ExactRef, ...]
    if isinstance(target, Entity):
        entity, path = target.ref, ()
    elif (
        isinstance(target, tuple)
        and target
        and all(isinstance(step, Relationship) for step in target)
    ):
        if any(left.to_entity.ref != right.from_entity.ref for left, right in pairwise(target)):
            reject(
                "a connected directed relationship path",
                repr(target),
                "Pass relationships in source-to-target order.",
                "identity",
            )
        entity, path = target[-1].to_entity, tuple(step.ref for step in target)
    else:
        reject(
            "a resolved Entity or nonempty relationship path",
            type(target).__name__,
            "Use mv.identity(entity) or mv.identity((relationship, ...)).",
            "identity",
        )
    return _CoordinateGrouping(AxisBinding(entity, None, path, "identity", at))


def _owner(definition: PopulationDependency) -> Owner | None:
    return definition.owner if isinstance(definition, PopulationProgram) else None


def _combined_owner(definitions: tuple[PopulationDependency, ...]) -> Owner | None:
    owners = {_owner(item) for item in definitions} - {None}
    if len(owners) > 1:
        reject(
            "one Session owner",
            "populations from different Sessions",
            "Rebuild domains in one Session.",
            "population",
        )
    return next(iter(owners), None)


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

    def project(self, *, by: tuple[Deferred | Grouping, ...]) -> Population:
        """Select/reorder existing axes and deduplicate complete projected tuples.

        Args: by: Existing bound axes or their captured fields.
        Returns: A lazy domain with complete projected tuples deduplicated.
        Example: ``population.project(by=(population.axes[0],))``.
        Constraints: Projection cannot read new attributes.
        """
        axes = _axes(by)
        if any(
            not any(coordinate_matches(axis, original) for original in self._definition.axes)
            for axis in axes
        ):
            reject(
                "complete axes already present in this Population",
                repr(axes),
                "Select existing complete axes; use explicit source relationships for new coordinates.",
                "Population.project",
            )
        positions = tuple(coordinate_position(self._definition.axes, axis) for axis in axes)
        axes = tuple(self._definition.axes[position] for position in positions)
        return Population(
            PopulationProgram(
                PopulationProject(self._definition, positions),
                _owner(self._definition),
                axes,
            ),
            self._frame_inputs,
        )

    def _set(self, other: Population, operation: str) -> Population:
        if len(self._definition.axes) != len(other._definition.axes) or any(
            not coordinate_matches(left, right)
            for left, right in zip(self._definition.axes, other._definition.axes, strict=True)
        ):
            reject(
                "identical ordered axes",
                "incompatible domains",
                "Project to matching axes before combining.",
                f"population.{operation}",
            )
        owner = _combined_owner((self._definition, other._definition))
        if operation == "union":
            node = PopulationSet(self._definition, other._definition, "union")
        elif operation == "intersection":
            node = PopulationSet(self._definition, other._definition, "intersection")
        else:
            node = PopulationSet(self._definition, other._definition, "difference")
        return Population(
            PopulationProgram(node, owner, self._definition.axes),
            merge_frame_key_inputs(self._frame_inputs, other._frame_inputs, owner=owner),
        )

    def union(self, other: Population) -> Population:
        """Union matching complete domains; keys remain lazy and deduplicated."""
        return self._set(other, "union")

    def intersection(self, other: Population) -> Population:
        """Intersect matching complete domains without reading additional fields."""
        return self._set(other, "intersection")

    def difference(self, other: Population) -> Population:
        """Subtract matching complete domain keys from this domain."""
        return self._set(other, "difference")

    def classify(self, *, by: tuple[Deferred | Grouping, ...]) -> Population:
        """Read direct member attributes into a correlated coordinate domain.

        Args: by: Direct fields owned by this identity Entity, or its existing identity.
        Returns: A lazy complete domain of correlated classification tuples.
        Example: ``members.classify(by=(members.axes[0], region))``.
        Constraints: Requires one identity axis; routed fields and time axes are unsupported.
        """
        if (
            len(self._definition.axes) != 1
            or not isinstance(self._definition.axes[0], AxisBinding)
            or self._definition.axes[0].role != "identity"
        ):
            reject(
                "one Entity identity axis",
                str(len(self._definition.axes)),
                "Project a single identity axis first.",
                "population.classify",
            )
        axes = _axes(by)
        entity = self._definition.axes[0].entity
        if any(isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in axes):
            reject(
                "member fields",
                "calendar or interval axis",
                "Use product for a calendar domain.",
                "population.classify",
            )
        member_axes = tuple(axis for axis in axes if isinstance(axis, AxisBinding))
        if any(axis.entity.ref != entity.ref or axis.path for axis in member_axes):
            reject(
                "direct classification fields on the member Entity",
                "foreign owner or routed field",
                "Classify using fields of the identity Entity.",
                "population.classify",
            )
        reads = tuple(
            FieldRead(axis.field, axis.entity, (), axis.at)
            for axis in member_axes
            if axis.field is not None
        )
        if any(
            axis.role == "identity" and not coordinate_matches(axis, self._definition.axes[0])
            for axis in member_axes
        ):
            reject(
                "the existing member identity axis",
                "a different identity binding",
                "Use the receiver's identity axis.",
                "population.classify",
            )
        if not reads:
            return self.project(by=by)
        return Population(
            PopulationProgram(
                PopulationClassify(self._definition, reads),
                _owner(self._definition),
                axes,
            ),
            self._frame_inputs,
        )

    def where(self, predicate: Deferred) -> Population:
        """Filter complete coordinates using closed Population membership logic.

        Args: predicate: ``in_population(other)`` combined with ``&``, ``|`` or ``~``.
        Returns: A lazy subset with unchanged axes.
        Example: ``domain.where(mv.in_population(allowed))``.
        Constraints: Every predicate domain must project from the receiver axes.
        """
        frame_inputs = collect_frame_key_inputs(predicate)
        condition = consume_predicate(predicate).node
        if any(not isinstance(item, PopulationContains) for item in condition.inputs):
            reject(
                "membership leaves over existing Population coordinates",
                "a source field condition",
                "Use session.members(..., where=...) for source fields, or filter a Frame with its boolean Column.",
                "population.where",
            )
        dependencies = condition_populations(condition)
        for item in dependencies:
            if any(
                not any(coordinate_matches(axis, original) for original in self._definition.axes)
                for axis in item.axes
            ):
                reject(
                    "a subset of receiver axes",
                    "incompatible predicate domain",
                    "Project the predicate onto matching bound axes.",
                    "population.where",
                )
        owner = _combined_owner((self._definition, *dependencies))
        return Population(
            PopulationProgram(
                PopulationFilter(self._definition, condition),
                owner,
                self._definition.axes,
            ),
            merge_frame_key_inputs(self._frame_inputs, frame_inputs, owner=owner),
        )

    def image(
        self, *, via: tuple[Relationship, ...], at: InstantAnchor | None = None
    ) -> Population:
        """Map selected identities to reached identities through a directed path.

        Args: via: Nonempty directed relationship path. at: Exact target version anchor.
        Returns: A deduplicated target identity domain, preserving missing-reference keys.
        Example: ``orders.image(via=(order_customer,))``.
        Constraints: Requires one identity axis and explicit connected relationships.
        """
        _identity_path(self, via)
        if at is not None and (not isinstance(at, InstantAnchor) or at.utcoffset() is None):
            reject(
                "an exact InstantAnchor",
                type(at).__name__,
                "Use session.instant(...).",
                "population.image",
            )
        axis = AxisBinding(via[-1].to_entity, None, (), "identity", at)
        return Population(
            PopulationProgram(
                PopulationImage(self._definition, via, at), _owner(self._definition), (axis,)
            ),
            self._frame_inputs,
        )

    def preimage(self, targets: Population, *, via: tuple[Relationship, ...]) -> Population:
        """Keep receiver identities that reach a selected target identity.

        Args: targets: One compatible target identity domain. via: Directed path.
        Returns: A lazy subset of this domain.
        Example: ``orders.preimage(customers, via=(order_customer,))``.
        Constraints: Relationship mapping uses complete declared identity keys.
        """
        _identity_path(self, via)
        if (
            len(targets._definition.axes) != 1
            or not isinstance(targets._definition.axes[0], AxisBinding)
            or targets._definition.axes[0].role != "identity"
            or targets._definition.axes[0].entity.ref != via[-1].to_entity.ref
        ):
            reject(
                "the path target identity axis",
                "incompatible target domain",
                "Select target Entity identities.",
                "population.preimage",
            )
        return Population(
            PopulationProgram(
                PopulationPreimage(self._definition, targets._definition, via),
                _combined_owner((self._definition, targets._definition)),
                self._definition.axes,
            ),
            merge_frame_key_inputs(
                self._frame_inputs,
                targets._frame_inputs,
                owner=_combined_owner((self._definition, targets._definition)),
            ),
        )


def _identity_path(population: Population, path: tuple[Relationship, ...]) -> None:
    if (
        len(population._definition.axes) != 1
        or not isinstance(population._definition.axes[0], AxisBinding)
        or population._definition.axes[0].role != "identity"
        or type(path) is not tuple
        or not path
        or any(not isinstance(step, Relationship) for step in path)
        or path[0].from_entity.ref != population._definition.axes[0].entity.ref
        or any(left.to_entity.ref != right.from_entity.ref for left, right in pairwise(path))
    ):
        reject(
            "one identity axis and a connected directed path",
            "incompatible relationship mapping",
            "Start the path at the receiver Entity.",
            "population.image",
        )


def in_population(population: Population) -> Deferred:
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
    via: tuple[tuple[RelationshipParameter, ...], ...],
    unmatched: Literal["exclude", "null"],
) -> Grouping:
    """Capture correlated member fields and explicit allocation paths.

    Args: by: One field or nonempty tuple from one member Entity. via: Ordered
        directed paths, possibly empty. unmatched: ``exclude`` or ``null``.

    Returns: An immutable membership Grouping for observe or Metric.per.
    Example: ``mv.membership(tag, via=((order_tags,),), unmatched='exclude')``.
    Constraints: Captures metadata only; no source or Session is opened.
    """
    if unmatched not in ("exclude", "null"):
        reject("exclude or null", unmatched, "Choose an explicit unmatched policy.", "membership")
    fields = tuple(consume_field(item) for item in (by if isinstance(by, tuple) else (by,)))
    if not fields or len({field.root.ref for field in fields}) != 1:
        reject(
            "nonempty correlated fields from one member Entity",
            "empty or mixed owners",
            "Use one descriptor per member Entity.",
            "membership",
        )
    reads = tuple(field.field_read or FieldRead(field.reference, field.root) for field in fields)
    if any(read.path for read in reads):
        reject(
            "direct member fields",
            "routed member field",
            "Put allocation paths in via=.",
            "membership",
        )
    starts = set()
    for path in via:
        for relationship in path:
            if not isinstance(relationship, RelationshipParameter):
                reject(
                    "resolved Relationship values in each via path",
                    type(relationship).__name__,
                    "Resolve each relationship with session.catalog.relationships.get(relationship_ref.path), "
                    "then pass the resolved values in via=((relationship,),).",
                    "membership.via",
                )
        if (
            not path
            or any(left.to_entity.ref != right.from_entity.ref for left, right in pairwise(path))
            or path[-1].to_entity.ref != fields[0].root.ref
        ):
            reject(
                "nonempty directed paths ending at the member Entity",
                "disconnected path",
                "Provide the complete declared relationship path.",
                "membership",
            )
        if path[0].from_entity.ref in starts:
            reject(
                "one explicit path per contribution root",
                "competing paths",
                "Choose one path for each root.",
                "membership",
            )
        starts.add(path[0].from_entity.ref)
    result = _MembershipGrouping(MembershipBinding(reads, via, unmatched))
    _axes(tuple(_CoordinateGrouping(_axis(read)) for read in reads))
    return result


def coordinates(
    *, by: tuple[Deferred | Grouping, ...], keys: tuple[tuple[CoordinateValue, ...], ...]
) -> Population:
    """Capture a complete finite field and identity coordinate domain.

    Args:
        by: Ordered fields or identity groupings.
        keys: Complete coordinate tuples.

    Returns:
        A lazy Population preserving empty and null classification keys.

    Example:
        ``mv.coordinates(by=(region,), keys=(('east',), ('west',)))``.

    Constraints:
        Each identity axis value is a complete non-null primary-key tuple.
        Literal identities do not prove source presence. Use product for time domains.
    """
    axes = _axes(by)
    if any(
        not isinstance(axis, AxisBinding) or (axis.role == "identity" and axis.path)
        for axis in axes
    ):
        reject(
            "fields or direct Entity identity groupings",
            repr(by),
            "Use mv.identity(entity) for complete identity keys and product for time domains.",
            "coordinates",
        )
    try:
        return Population(
            LiteralPopulation(
                tuple(axis for axis in axes if isinstance(axis, AxisBinding)),
                keys,
                "coordinates-v1",
            )
        )
    except DefinitionGraphError as error:
        reject(
            "complete literal coordinate tuples matching the ordered axes",
            str(error),
            "Give one literal per field and a complete non-null primary-key tuple per identity axis.",
            "coordinates",
        )


def product(*factors: Population | CalendarAxis | TimeScope) -> Population:
    """Form a lazy Cartesian product of disjoint coordinate domains.

    Args: factors: At least two Populations with distinct axes and one owner.
    Returns: A Population whose keys are complete factor-key combinations.
    Example: ``mv.product(regions, channels)``.
    Constraints: An empty factor makes the complete product empty.
    """
    if len(factors) < 2:
        reject(
            "at least two population factors",
            str(len(factors)),
            "Supply two complete domains.",
            "product",
        )
    populations = tuple(
        _calendar_population(factor)
        if isinstance(factor, CalendarAxis)
        else _interval_population(factor)
        if isinstance(factor, TimeScope)
        else factor
        for factor in factors
    )
    axes = tuple(axis for factor in populations for axis in factor._definition.axes)
    _axes(tuple(_CoordinateGrouping(axis) for axis in axes))
    definitions = tuple(factor._definition for factor in populations)
    return Population(
        PopulationProgram(
            PopulationProduct(definitions),
            _combined_owner(definitions),
            axes,
        ),
        merge_frame_key_inputs(*(factor._frame_inputs for factor in populations)),
    )
