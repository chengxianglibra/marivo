"""Immutable typed coordinate domains and explicit membership allocation."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from ibis.common.deferred import Deferred

from marivo._definition.correspondence import ComparisonAxis, CorrespondenceAxis
from marivo._definition.deferred import RelationshipParameter
from marivo._definition.graph import computation_fingerprint
from marivo._definition.relations import (
    CalendarKeys,
    LiteralPopulation,
    MembershipBinding,
    PopulationAnd,
    PopulationClassify,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationNot,
    PopulationOr,
    PopulationParameter,
    PopulationPredicateParameter,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    QueryKeys,
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
    LiteralValue,
    Owner,
)
from marivo._temporal import CalendarAxis, InstantAnchor, anchor_from_datetime
from marivo.analysis._contract import AnalysisAction, AnalysisContract
from marivo.analysis.core.model import reject
from marivo.semantic._deferred_semantics import consume_field
from marivo.semantic.expressions import Entity


@dataclass(frozen=True, slots=True, repr=False)
class MemberAxis:
    """Locate the unique compatible complete identity axis on a receiver."""

    def __repr__(self) -> str:
        return "<MemberAxis kind=member; use marivo.help('analysis.member')>"


def member() -> MemberAxis:
    """Locate a complete identity already provided by the receiving domain.

    Args: None.
    Returns: An immutable MemberAxis marker.
    Example: ``session.observe({'sales':sales},members=people,by=(mv.member(),))``.
    Constraints: Requires one compatible bound identity; it never guesses an Entity.
    """
    return MemberAxis()


@dataclass(frozen=True, slots=True, repr=False)
class MembershipAxis(MembershipBinding):
    """A correlated member bucket binding; construct with ``mv.membership``."""

    def __repr__(self) -> str:
        return f"<MembershipAxis fields={len(self.fields)} paths={len(self.paths)} unmatched={self.unmatched}; use .show()>"

    def show(self) -> str:
        """Return bounded source-free member fields, paths and unmatched policy."""
        return "\n".join((repr(self), *(field.field.ref.key for field in self.fields[:8])))


def _axis(read: FieldRead) -> AxisBinding:
    return AxisBinding(read.entity, read.field, read.path, "field", read.at)


def _axes(
    by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...],
    *,
    available: tuple[ComparisonAxis, ...] = (),
) -> tuple[tuple[ComparisonAxis, ...], tuple[MembershipBinding, ...]]:
    axes: list[ComparisonAxis] = []
    memberships: list[MembershipBinding] = []
    for item in by:
        if isinstance(item, MemberAxis):
            candidates = tuple(
                axis for axis in available if isinstance(axis, AxisBinding) and axis.field is None
            )
            if len(candidates) != 1:
                reject(
                    "one compatible bound identity axis",
                    repr(available),
                    "Provide explicit members=population or select one exact identity binding.",
                    "analysis.member",
                )
            axes.append(candidates[0])
        elif isinstance(item, MembershipAxis):
            memberships.append(item)
            axes.extend(_axis(read) for read in item.fields)
        elif isinstance(item, (AxisBinding, CalendarCoordinate, CorrespondenceAxis)):
            axes.append(item)
        else:
            field = consume_field(item)
            axes.append(_axis(field.field_read or FieldRead(field.reference, field.root)))
    identities = tuple(
        ("calendar", axis.identity)
        if isinstance(axis, (CalendarCoordinate, CorrespondenceAxis))
        else (axis.entity.ref, axis.field.ref if axis.field else None, axis.path, axis.at)
        for axis in axes
    )
    if len(set(identities)) != len(identities):
        reject(
            "distinct coordinate axes",
            "duplicate or conflicting field bindings",
            "Remove repeated axes.",
            "population",
        )
    return tuple(axes), tuple(memberships)


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
    """A lazy finite set of complete coordinates; use a Session to execute it."""

    @property
    def axes(self) -> tuple[ComparisonAxis, ...]:
        """Return the ordered bound axes without reading any source."""
        return self.definition.axes

    @property
    def memberships(self) -> tuple[MembershipBinding, ...]:
        return self.definition.memberships

    def __repr__(self) -> str:
        return f"<Population id={computation_fingerprint(self.definition)[:16]} axes={len(self.axes)}; use .show()>"

    def show(self) -> str:
        """Return bounded captured domain facts without enumerating source keys."""
        return "\n".join(
            (
                repr(self),
                *(
                    axis.identity
                    if isinstance(axis, (CalendarCoordinate, CorrespondenceAxis))
                    else axis.field.ref.key
                    if axis.field
                    else axis.entity.ref.key
                    for axis in self.axes[:8]
                ),
            )
        )

    def contract(self) -> AnalysisContract:
        """Return source-free domain facts and valid next operations.

        Returns: An AnalysisContract describing the captured finite coordinate program.
        Example: ``population.contract().show()``.
        Constraints: This does not read business rows or infer unknown keys.
        """
        return AnalysisContract(
            kind="Population",
            actions=(
                AnalysisAction("session.query(population)", "analysis.session.query"),
                AnalysisAction(
                    "population.project(by=population.axes)", "analysis.Population.project"
                ),
                AnalysisAction("population.union(population)", "analysis.Population.union"),
                AnalysisAction(
                    "population.intersection(population)", "analysis.Population.intersection"
                ),
                AnalysisAction(
                    "population.difference(population)", "analysis.Population.difference"
                ),
            ),
            domain="complete coordinates",
            quantity="keys only",
            _facts=(
                ("axes", str(len(self.axes))),
                ("definition", computation_fingerprint(self.definition)),
                ("owner", repr(_owner(self.definition))),
                (
                    "keys",
                    f"{len(self.definition.keys)} captured complete literal coordinates"
                    if isinstance(self.definition, LiteralPopulation)
                    else "unevaluated program; contract does not enumerate keys",
                ),
                *((f"axis[{index}]", repr(axis)[:400]) for index, axis in enumerate(self.axes[:6])),
                (
                    "next",
                    "project, union, intersection, difference, product; session.query(population)",
                ),
            ),
        )

    def project(
        self, *, by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...]
    ) -> Population:
        """Select/reorder existing axes, preserving membership bindings.

        Args: by: Existing bound axes or their captured fields.
        Returns: A lazy domain with complete projected tuples deduplicated.
        Example: ``population.project(by=(population.axes[0],))``.
        Constraints: Projection cannot read new attributes.
        """
        axes, _ = _axes(by, available=self.axes)
        if any(axis not in self.axes for axis in axes):
            endpoint_positions: list[int] = []
            paths: list[tuple[str, ...] | None] = []
            for axis in axes:
                if axis in self.axes:
                    endpoint_positions.append(self.axes.index(axis))
                    paths.append(None)
                    continue
                candidates = tuple(
                    (index, path)
                    for index, source in enumerate(self.axes)
                    if isinstance(source, CorrespondenceAxis)
                    for path, target in source.projections.items()
                    if target == axis
                )
                if len(candidates) != 1:
                    reject(
                        "one retained axis or existing endpoint projection",
                        repr(axis),
                        "Choose an unambiguous original endpoint binding.",
                        "Population.project",
                    )
                index, path = candidates[0]
                endpoint_positions.append(index)
                paths.append(path)
            if isinstance(self.definition, PopulationProgram) and isinstance(
                self.definition.root, QueryKeys
            ):
                from marivo._definition.query import ProjectKeys, ResultQuery

                captured = self.definition.root
                assert self.definition.owner is not None
                root = ResultQuery(
                    (captured,),
                    ProjectKeys(tuple(endpoint_positions), tuple(paths)),
                    axes,
                    self.definition.owner,
                )
                return Population(
                    PopulationProgram(QueryKeys(root, axes), self.definition.owner, axes)
                )
            reject(
                "a captured Query endpoint domain",
                type(self.definition).__name__,
                "Apply selection on the Query, then project its population endpoint.",
                "Population.project",
            )
        positions = tuple(self.axes.index(axis) for axis in axes)
        memberships = tuple(
            MembershipBinding(
                tuple(read for read in binding.fields if _axis(read) in axes),
                binding.paths,
                binding.unmatched,
            )
            for binding in self.memberships
            if any(_axis(read) in axes for read in binding.fields)
        )
        return Population(
            PopulationProgram(
                PopulationProject(self.definition, positions),
                _owner(self.definition),
                axes,
                memberships,
            )
        )

    def _set(self, other: Population, operation: str) -> Population:
        if self.axes != other.axes or self.memberships != other.memberships:
            reject(
                "identical ordered axes and membership bindings",
                "incompatible domains",
                "Project to matching axes and bindings before combining.",
                f"population.{operation}",
            )
        owner = _combined_owner((self.definition, other.definition))
        if operation == "union":
            node = PopulationSet(self.definition, other.definition, "union")
        elif operation == "intersection":
            node = PopulationSet(self.definition, other.definition, "intersection")
        else:
            node = PopulationSet(self.definition, other.definition, "difference")
        return Population(PopulationProgram(node, owner, self.axes, self.memberships))

    def union(self, other: Population) -> Population:
        """Union matching complete domains; keys remain lazy and deduplicated."""
        return self._set(other, "union")

    def intersection(self, other: Population) -> Population:
        """Intersect matching complete domains without reading additional fields."""
        return self._set(other, "intersection")

    def difference(self, other: Population) -> Population:
        """Subtract matching complete domain keys from this domain."""
        return self._set(other, "difference")

    def classify(
        self, *, by: tuple[Deferred | MembershipAxis | MemberAxis | ComparisonAxis, ...]
    ) -> Population:
        """Read member classifications directly and retain allocation bindings.

        Args: by: Fields or membership descriptors owned by this identity Entity.
        Returns: A lazy complete domain of correlated classification tuples.
        Example: ``members.classify(by=(tag_buckets,))``.
        Constraints: Requires one identity axis; membership paths are not traversed.
        """
        if (
            len(self.axes) != 1
            or not isinstance(self.axes[0], AxisBinding)
            or self.axes[0].role != "identity"
        ):
            reject(
                "one Entity identity axis",
                str(len(self.axes)),
                "Project a single identity axis first.",
                "population.classify",
            )
        axes, memberships = _axes(by, available=self.axes)
        entity = self.axes[0].entity
        if any(isinstance(axis, CalendarCoordinate) for axis in axes):
            reject(
                "member fields",
                "calendar axis",
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
        if any(axis.role == "identity" and axis != self.axes[0] for axis in member_axes):
            reject(
                "the existing member identity axis",
                "a different identity binding",
                "Use the receiver's identity axis.",
                "population.classify",
            )
        return Population(
            PopulationProgram(
                PopulationClassify(self.definition, reads),
                _owner(self.definition),
                axes,
                memberships,
            )
        )

    def where(self, predicate: PopulationPredicate) -> Population:
        """Filter complete coordinates using closed Population membership logic.

        Args: predicate: ``in_population(other)`` combined with ``&``, ``|`` or ``~``.
        Returns: A lazy subset with unchanged axes and allocation bindings.
        Example: ``domain.where(mv.in_population(allowed))``.
        Constraints: Every predicate domain must project from the receiver axes.
        """
        dependencies = condition_populations(predicate.condition)
        for item in dependencies:
            if any(axis not in self.axes for axis in item.axes):
                reject(
                    "a subset of receiver axes",
                    "incompatible predicate domain",
                    "Project the predicate onto matching bound axes.",
                    "population.where",
                )
        owner = _combined_owner((self.definition, *dependencies))
        return Population(
            PopulationProgram(
                PopulationFilter(self.definition, predicate.condition),
                owner,
                self.axes,
                self.memberships,
            )
        )

    def image(
        self, *, via: tuple[RelationshipParameter, ...], at: InstantAnchor | None = None
    ) -> Population:
        """Map selected identities to reached identities through a directed path.

        Args: via: Nonempty directed relationship path. at: Exact target version anchor.
        Returns: A deduplicated target identity domain, preserving missing-reference keys.
        Example: ``orders.image(via=(order_customer,))``.
        Constraints: Requires one identity axis and explicit connected relationships.
        """
        _identity_path(self, via)
        if at is not None and not isinstance(at, InstantAnchor):
            reject(
                "an exact InstantAnchor",
                type(at).__name__,
                "Use session.instant(...).",
                "population.image",
            )
        axis = AxisBinding(via[-1].to_entity, None, (), "identity", at)
        return Population(
            PopulationProgram(
                PopulationImage(self.definition, via, at), _owner(self.definition), (axis,)
            )
        )

    def preimage(
        self, targets: Population, *, via: tuple[RelationshipParameter, ...]
    ) -> Population:
        """Keep receiver identities that reach a selected target identity.

        Args: targets: One compatible target identity domain. via: Directed path.
        Returns: A lazy subset of this domain.
        Example: ``orders.preimage(customers, via=(order_customer,))``.
        Constraints: Relationship mapping uses complete declared identity keys.
        """
        _identity_path(self, via)
        if (
            len(targets.axes) != 1
            or not isinstance(targets.axes[0], AxisBinding)
            or targets.axes[0].role != "identity"
            or targets.axes[0].entity.ref != via[-1].to_entity.ref
        ):
            reject(
                "the path target identity axis",
                "incompatible target domain",
                "Select target Entity identities.",
                "population.preimage",
            )
        return Population(
            PopulationProgram(
                PopulationPreimage(self.definition, targets.definition, via),
                _combined_owner((self.definition, targets.definition)),
                self.axes,
                self.memberships,
            )
        )


def _identity_path(population: Population, path: tuple[RelationshipParameter, ...]) -> None:
    if (
        len(population.axes) != 1
        or not isinstance(population.axes[0], AxisBinding)
        or population.axes[0].role != "identity"
        or type(path) is not tuple
        or not path
        or path[0].from_entity.ref != population.axes[0].entity.ref
        or any(left.to_entity.ref != right.from_entity.ref for left, right in pairwise(path))
    ):
        reject(
            "one identity axis and a connected directed path",
            "incompatible relationship mapping",
            "Start the path at the receiver Entity.",
            "population.image",
        )


@dataclass(frozen=True, slots=True, repr=False)
class PopulationPredicate(PopulationPredicateParameter):
    """Closed coordinate membership logic; construct with ``in_population``."""

    def __and__(self, other: PopulationPredicate) -> PopulationPredicate:
        return PopulationPredicate(PopulationAnd(self.condition, other.condition))

    def __or__(self, other: PopulationPredicate) -> PopulationPredicate:
        return PopulationPredicate(PopulationOr(self.condition, other.condition))

    def __invert__(self) -> PopulationPredicate:
        return PopulationPredicate(PopulationNot(self.condition))

    def __bool__(self) -> bool:
        reject(
            "a deferred coordinate predicate",
            "Python truth evaluation",
            "Use &, | or ~ and pass the result to where.",
            "population.predicate",
        )

    def __repr__(self) -> str:
        return f"<PopulationPredicate domains={len(condition_populations(self.condition))}; use .show()>"

    def show(self) -> str:
        """Return the closed predicate kind without enumerating domain keys."""
        return repr(self)


def in_population(population: Population) -> PopulationPredicate:
    """Match complete coordinates projected onto a Population's bound axes.

    Args: population: The allowed finite or lazy coordinate domain.
    Returns: A closed predicate accepted by Population.where and Metric.where.
    Example: ``domain.where(mv.in_population(allowed))``.
    Constraints: This captures a domain definition and performs no source access.
    """
    return PopulationPredicate(PopulationContains(population.definition))


def _calendar_population(calendar: CalendarAxis) -> Population:
    grid = calendar.grid
    coordinate = CalendarCoordinate(
        grid.identity,
        grid.grain_token,
        grid.boundary_timezone,
        grid.snapshot_digest,
        tuple(
            (
                anchor_from_datetime(cell.original_start),
                anchor_from_datetime(cell.original_end),
                anchor_from_datetime(cell.start),
                anchor_from_datetime(cell.end),
            )
            for cell in grid.cells
        ),
        grid.calendar_snapshot,
    )
    return Population(PopulationProgram(CalendarKeys(coordinate), None, (coordinate,)))


def membership(
    by: Deferred | tuple[Deferred, ...],
    *,
    via: tuple[tuple[RelationshipParameter, ...], ...],
    unmatched: Literal["exclude", "null"],
) -> MembershipAxis:
    """Capture correlated member fields and explicit allocation paths.

    Args: by: One field or nonempty tuple from one member Entity. via: Ordered
        directed paths, possibly empty. unmatched: ``exclude`` or ``null``.

    Returns: An immutable MembershipAxis for observe or Metric.per.
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
    result = MembershipAxis(reads, via, "exclude" if unmatched == "exclude" else "null")
    _axes((result,))
    return result


def identities(entity: Entity, *, keys: tuple[tuple[LiteralValue, ...], ...]) -> Population:
    """Capture explicit complete Entity keys, including unconfirmed references.

    Args: entity: A resolved Entity. keys: Complete primary-key tuples.
    Returns: An immutable lazy Population with one identity axis.
    Example: ``mv.identities(customers, keys=(('C1',), ('C2',)))``.
    Constraints: Pass a resolved Entity, not an EntityRef. Keys do not prove
    source presence; null primary keys are invalid.
    """
    if not isinstance(entity, Entity):
        reject(
            "a resolved Entity",
            type(entity).__name__,
            "Resolve the Entity with session.catalog.entities.get(entity_ref), then pass that Entity.",
            "identities",
        )
    axis = AxisBinding(entity.ref, None, (), "identity")
    rows: tuple[tuple[CoordinateValue, ...], ...] = tuple((key,) for key in keys)
    try:
        return Population(LiteralPopulation((axis,), rows, "coordinates-v1"))
    except DefinitionGraphError as error:
        reject(
            "complete non-null Entity primary-key tuples",
            str(error),
            "Pass every primary-key component in declared order, without null identity components.",
            "identities",
        )


def coordinates(
    *, by: tuple[Deferred | MembershipAxis, ...], keys: tuple[tuple[LiteralValue, ...], ...]
) -> Population:
    """Capture a complete finite field domain with optional membership bindings.

    Args: by: Ordered fields or membership groups. keys: Expanded complete tuples.
    Returns: A lazy Population preserving empty and null classification keys.
    Example: ``mv.coordinates(by=(region,), keys=(('east',), ('west',)))``.
    Constraints: The external observe domain must cover consumed contributions.
    """
    axes, memberships = _axes(by)
    try:
        return Population(
            LiteralPopulation(
                tuple(axis for axis in axes if isinstance(axis, AxisBinding)),
                keys,
                "coordinates-v1",
                memberships,
            )
        )
    except DefinitionGraphError as error:
        reject(
            "complete literal coordinate tuples matching the ordered fields",
            str(error),
            "Give one supported literal per field in each key tuple; use None only for field null categories.",
            "coordinates",
        )


def product(*factors: Population | CalendarAxis) -> Population:
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
        _calendar_population(factor) if isinstance(factor, CalendarAxis) else factor
        for factor in factors
    )
    axes = tuple(axis for factor in populations for axis in factor.axes)
    _axes(axes)
    definitions = tuple(factor.definition for factor in populations)
    return Population(
        PopulationProgram(
            PopulationProduct(definitions),
            _combined_owner(definitions),
            axes,
            tuple(binding for factor in populations for binding in factor.memberships),
        )
    )
