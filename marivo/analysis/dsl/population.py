"""Immutable typed coordinate domains and explicit contribution mappings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeVar, overload

from ibis.common.deferred import Deferred, Just

from marivo._definition import population_builders as build
from marivo._definition.correspondence import (
    ComparisonAxis,
)
from marivo._definition.deferred import (
    PopulationContainsResolver,
    PopulationCountResolver,
    inspect_capture,
)
from marivo._definition.graph import computation_fingerprint
from marivo._definition.grouping import _CoordinateGrouping, _MembershipGrouping, grouping_binding
from marivo._definition.population_parameters import (
    ContainsParameter,
    CoordinatesParameter,
    IdentityParameter,
    MembershipParameter,
    ProductParameter,
    parameter,
)
from marivo._definition.population_parameters import has_parameters as _native_parameters
from marivo._definition.relations import (
    CalendarKeys,
    IntervalKeys,
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
    InputContext,
    IntervalCoordinate,
    Owner,
    PathParameter,
    field_axis,
    merge_input_contexts,
)
from marivo._kernel.grouping import Grouping
from marivo._kernel.symbolic import EntitySymbol, PathSymbol
from marivo._kernel.temporal import CalendarAxis, TimeScope
from marivo.analysis._scope import scope_summary
from marivo.analysis.core.frame_data import (
    FrameKeyDependency,
    MemoryFrameBacking,
    StoredFrameBacking,
)
from marivo.analysis.dsl._frame_inputs import PopulationFrameInputs, collect_frame_key_inputs
from marivo.analysis.dsl.population_identities import validate_identity_keys
from marivo.analysis.dsl.temporal import interval_coordinate
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import merge_frame_key_inputs
from marivo.semantic import (
    Entity,
    MetricExpression,
    RowExpression,
)
from marivo.semantic.spi import (
    FieldExpression,
    NumericMetric,
    Path,
    RowPredicate,
    as_deferred,
    bind_field,
    catalog_entity_identity,
    consume_field,
    consume_predicate,
    validate_context,
)
from marivo.telemetry import traced

if TYPE_CHECKING:
    from pathlib import Path as FilesystemPath

    from marivo.semantic import SemanticCatalog


def _has_parameters(value: object) -> bool:
    if isinstance(value, (RowExpression, MetricExpression)):
        return _native_parameters(as_deferred(value))
    if isinstance(value, (tuple, list)):
        return any(_has_parameters(item) for item in value)
    return _native_parameters(value)


def _native_axes(
    by: tuple[Deferred | FieldExpression | Grouping, ...],
) -> tuple[Deferred | Grouping, ...]:
    return tuple(as_deferred(item) if isinstance(item, RowExpression) else item for item in by)


def _context(*values: object) -> InputContext:
    contexts: list[InputContext] = []
    for value in values:
        if isinstance(
            value,
            (Entity, Path, RowExpression, MetricExpression, Grouping, PopulationParameter),
        ):
            contexts.append(value._context)
        elif isinstance(value, Deferred):
            contexts.append(inspect_capture(value).context)
        elif isinstance(value, (tuple, list)):
            contexts.append(_context(*value))
    return _closed(lambda: merge_input_contexts(*contexts), "population.context")


def _identity_keys(*values: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result: dict[str, tuple[str, ...]] = {}
    for value in values:
        items = (
            value._identity_keys
            if isinstance(value, (Grouping, PopulationParameter))
            else ((value.id, value.primary_key),)
            if isinstance(value, Entity)
            else _identity_keys(*value)
            if isinstance(value, (tuple, list))
            else ()
        )
        for identity, keys in items:
            if identity in result and result[identity] != keys:
                reject(
                    "one captured complete identity",
                    identity,
                    "Rebuild the coordinate domain with consistent primary keys.",
                    "population",
                )
            result[identity] = keys
    return tuple(result.items())


def _validate_population(
    value: PopulationParameter, catalog: SemanticCatalog, owner: Owner
) -> None:

    if value._context.owner not in (None, owner):
        reject(
            "a Population owned by this Session",
            "another Store or Session",
            "Rebuild the Population in its owning Session.",
            "population",
        )
    validate_context(value._context, project_root=catalog.workspace_dir, owner=owner)
    validate_identity_keys(value._identity_keys, catalog)


def _bind_by(
    by: tuple[FieldExpression | Grouping | TimeScope | Population, ...] | Population,
    catalog: SemanticCatalog,
    owner: Owner,
    *,
    location: str,
) -> tuple[Deferred | Grouping | TimeScope | Population, ...] | Population:

    if isinstance(by, Population):
        _validate_population(by, catalog, owner)
        return by
    if not isinstance(by, tuple):
        reject(
            "an ordered tuple of fields/groupings or a Population",
            type(by).__name__,
            "Pass by=(field,) or one complete Population.",
            location,
        )
    result: list[Deferred | Grouping | TimeScope | Population] = []
    for index, value in enumerate(by):
        if isinstance(value, RowExpression) and value._role in ("dimension", "time", "numeric"):
            result.append(bind_field(value, catalog, owner=owner))
        elif isinstance(value, Grouping):
            validate_context(value._context, project_root=catalog.workspace_dir, owner=owner)
            validate_identity_keys(value._identity_keys, catalog)
            result.append(value)
        elif isinstance(value, Population):
            _validate_population(value, catalog, owner)
            result.append(value)
        elif isinstance(value, TimeScope):
            result.append(value)
        else:
            reject(
                "a semantic field, Grouping, TimeScope or Population",
                type(value).__name__,
                "Obtain a field from the receiving Catalog and pass by=(field,).",
                f"{location}[{index}]",
            )
    return tuple(result)


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
            canonical_id=location
            if location
            in {
                "population.classify",
                "population.identities",
                "population.project",
                "population.where",
                "population.image",
                "population.preimage",
                "population.union",
                "population.intersection",
                "population.difference",
                "session.read",
            }
            else None,
        )


def _read(value: Deferred | FieldExpression) -> FieldRead:
    field = consume_field(as_deferred(value) if isinstance(value, RowExpression) else value)
    return field.field_read or FieldRead(
        field.reference, field.root, versioned=field.versioned, unit=field.unit
    )


def _axis(read: FieldRead) -> AxisBinding:
    return field_axis(read)


def _axes(
    by: tuple[Deferred | FieldExpression | Grouping | TimeScope, ...],
    *,
    distinct: bool = True,
) -> tuple[ComparisonAxis, ...]:
    axes: list[ComparisonAxis] = []
    for item in by:
        if isinstance(item, TimeScope):
            axes.append(interval_coordinate(item))
        elif isinstance(item, Grouping):
            binding = grouping_binding(item)
            if isinstance(binding, MembershipBinding):
                reject(
                    "ordinary coordinate groupings",
                    "membership grouping",
                    "Put membership rules directly in Session.observe.by or Metric.per samples.",
                    "population",
                )
            else:
                axes.append(binding)
        elif isinstance(item, (Deferred, RowExpression)):
            axes.append(_axis(_read(item)))
        else:
            reject(
                "a semantic field or Grouping",
                type(item).__name__,
                "Use a resolved field, mv.identity(entity), or population.axes.",
                "population",
            )
    return _closed(lambda: build.axes(tuple(axes)), "population") if distinct else tuple(axes)


def _subject(on: Grouping | None, location: str) -> ComparisonAxis | None:
    if on is None and location != "population.identities":
        return None
    binding = grouping_binding(on) if isinstance(on, Grouping) else None
    if not isinstance(binding, AxisBinding) or binding.role != "identity":
        reject(
            "an existing complete identity Grouping",
            on.kind if isinstance(on, Grouping) else type(on).__name__,
            "Choose an identity from population.axes and pass it as on=.",
            location,
            canonical_id=location,
        )
    return binding


def _native(value: object) -> Deferred:
    assert isinstance(value, Deferred)
    return value


def _capture_population_call(
    receiver: Population, method: str, *args: object, **kwargs: object
) -> Deferred:
    value = _native(getattr(Deferred(Just(receiver)), method)(*args, **kwargs))
    inspect_capture(value)
    return value


def _capture_path(value: Path | PathSymbol) -> PathParameter | Deferred:
    if not isinstance(value, (Path, Deferred)):
        reject(
            "a Path from mv.path(...)",
            type(value).__name__,
            "Pass each directed Relationship as an argument to mv.path(...).",
            "path.define",
            canonical_id="path.define",
        )
    return value if isinstance(value, Deferred) else value._parameter


@overload
def identity(target: Entity | Path) -> Grouping: ...


@overload
def identity(target: EntitySymbol | PathSymbol) -> Deferred: ...


@traced(surface="analysis", operation="identity", capture="short")
def identity(target: Entity | EntitySymbol | Path | PathSymbol) -> Grouping | Deferred:
    """Capture grouping by one complete Entity identity, without selecting members.

    Args:
        target: A Catalog Entity or a directed Path from mv.path(...).

    Returns:
        An immutable Grouping for closed input, or a Deferred parameter for symbols; no source keys are enumerated.

    Example:
        ``session.observe({'sales': sales}, by=(mv.identity(customer),))``.

    Constraints: Explicit paths infer their target Entity. Observation binds every
        primary-key component through a single-valued route. Use mv.coordinates
        for a finite requested key domain, or session.members for source members.
    """
    if not isinstance(target, (Entity, Deferred, Path)):
        reject(
            "a closed Entity, native Entity symbol or relationship path",
            type(target).__name__,
            "Use a Catalog Entity, ms.ref.entity(...), or mv.path(...).",
            "identity",
        )
    captured = (
        target
        if isinstance(target, Deferred)
        else target._parameter
        if isinstance(target, Path)
        else catalog_entity_identity(target)
    )
    if isinstance(captured, Deferred):
        return _closed(
            lambda: parameter(IdentityParameter(captured, _context=_context(target))),
            "identity",
        )
    return _closed(
        lambda: _CoordinateGrouping(
            build.identity(captured),
            _context(target),
            _identity_keys(target.target if isinstance(target, Path) else target),
        ),
        "identity",
    )


def _owner(definition: PopulationDependency) -> Owner | None:
    return build.owner(definition)


@dataclass(frozen=True, slots=True, repr=False)
class Population(PopulationParameter, PopulationFrameInputs):
    """A lazy finite set of complete coordinates; use a Session to execute it.

    Acquire through mv.coordinates/product, session.members/population_from or
    existing Population operations. Internal definition/frame inputs are not a
    supported constructor.
    """

    _frame_inputs: tuple[FrameKeyDependency, ...] = field(default=(), compare=False, repr=False)

    @property
    def _project_root(self) -> FilesystemPath | None:
        return self._context.project_root

    @property
    def _session_id(self) -> str | None:
        owner = self._context.owner
        return owner.session_id if owner is not None else None

    @property
    def axes(self) -> tuple[Grouping, ...]:
        """Return all complete coordinate axes in definition order.

        Returns: Ordered single-coordinate Grouping descriptors; reading is source/schema free.
        Example: ``population.project(by=population.axes)``.
        Constraints: Does not import keys, selection or membership allocation bindings.
        """
        return tuple(
            _CoordinateGrouping(
                axis,
                InputContext(self._context.project_root),
                tuple(
                    (identity, keys)
                    for identity, keys in self._identity_keys
                    if isinstance(axis, AxisBinding)
                    and axis.role == "identity"
                    and identity == axis.entity.ref.key
                ),
            )
            for axis in self._definition.axes
        )

    @traced(surface="analysis", operation="population.count", capture="short")
    def count(self) -> NumericMetric:
        """Capture the cardinality of this complete coordinate domain.

        Args: None.
        Returns: A numeric Metric expression, independent of fact contributions.
        Example: ``session.observe({'people': people.count()})``.
        Constraints: Counts complete tuples, including field Nulls; performs no reads.
        Grouping coordinates must already belong to the domain; use classify for new fields.
        Existing compatible time keys support rows; missing report time broadcasts only
        over established output coordinates. Use an explicit by=Population for a full time domain.
        """

        return MetricExpression(PopulationCountResolver(self), self._context, "numeric")

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
        fixed_frames = sum(
            isinstance(item.frame._backing, (MemoryFrameBacking, StoredFrameBacking))
            for item in self._frame_inputs
        )
        facts = (
            ("axes", str(len(self._definition.axes))),
            ("definition", computation_fingerprint(self._definition)),
            ("scope", scope_summary(self._definition, max_bytes=600)),
            ("owner", owner.session_id if owner is not None else "unbound"),
            (
                "frame_inputs",
                f"{fixed_frames} fixed, {len(self._frame_inputs) - fixed_frames} pending",
            ),
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
                    else f"intervals={len(axis.bounds)} timezone={axis.timezone}"
                    if isinstance(axis, IntervalCoordinate)
                    else f"{axis.field.ref.key if axis.field else axis.entity.ref.key}; path={tuple(step.ref.key for step in axis.path)}; source_at={axis.source_at}; intermediate_at={axis.step_ats}; at={axis.at}",
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

    @traced(surface="analysis", operation="population.project", capture="short")
    def project(
        self, *, by: tuple[Deferred | FieldExpression | Grouping, ...]
    ) -> Population | Deferred:
        """Select/reorder existing axes and deduplicate complete projected tuples.

        Args: by: Existing bound axes or their captured fields.
        Returns: A lazy domain with complete projected tuples deduplicated.
        Example: ``population.project(by=(population.axes[0],))``.
        Constraints: Projection cannot read new attributes.
        """
        context = _context(self, by)
        if _has_parameters(by):
            return _native(Deferred(Just(self)).project(by=_native_axes(by)))
        return Population(
            _closed(lambda: build.project(self._definition, _axes(by)), "population.project"),
            self._frame_inputs,
            _context=context,
            _identity_keys=_identity_keys(self, by),
        )

    def _set(
        self,
        other: Population | Deferred,
        operation: Literal["union", "intersection", "difference"],
    ) -> Population | Deferred:
        context = _context(self, other)
        if isinstance(other, Deferred):
            return _native(getattr(Deferred(Just(self)), operation)(other))
        definition = _closed(
            lambda: build.set_operation(self._definition, other._definition, operation),
            f"population.{operation}",
        )
        return Population(
            definition,
            merge_frame_key_inputs(self._frame_inputs, other._frame_inputs, owner=definition.owner),
            _context=context,
            _identity_keys=_identity_keys(self, other),
        )

    @traced(surface="analysis", operation="population.union", capture="short")
    def union(self, other: Population | Deferred) -> Population | Deferred:
        """Union matching complete domains; keys remain lazy and deduplicated."""
        return self._set(other, "union")

    @traced(surface="analysis", operation="population.intersection", capture="short")
    def intersection(self, other: Population | Deferred) -> Population | Deferred:
        """Intersect matching complete domains without reading additional fields."""
        return self._set(other, "intersection")

    @traced(surface="analysis", operation="population.difference", capture="short")
    def difference(self, other: Population | Deferred) -> Population | Deferred:
        """Subtract matching complete domain keys from this domain."""
        return self._set(other, "difference")

    @overload
    def classify(
        self, *, by: tuple[FieldExpression | Grouping, ...], on: Grouping | None = None
    ) -> Population: ...

    @overload
    def classify(
        self,
        *,
        by: tuple[Deferred | FieldExpression | Grouping, ...] | Deferred,
        on: Deferred | Grouping | None = None,
    ) -> Population | Deferred: ...

    @traced(surface="analysis", operation="population.classify", capture="short")
    def classify(
        self,
        *,
        by: tuple[Deferred | FieldExpression | Grouping, ...] | Deferred,
        on: Deferred | Grouping | None = None,
    ) -> Population | Deferred:
        """Append new single-valued coordinates while retaining all existing axes.

        Args:
            by: Fields, explicit path identities or existing axes to reuse.
            on: Existing identity Grouping for new reads when multiple subjects qualify.
        Returns: A lazy domain retaining its complete input tuples.
        Example: ``members.classify(by=(region,))``.
        Constraints: New identity roles require a complete non-null result from a
        single-valued Path. Missing field matches are NULL.
        """
        context = _context(self, by, on)
        if isinstance(by, Deferred) or _has_parameters(by) or isinstance(on, Deferred):
            native_by = by if isinstance(by, Deferred) else _native_axes(by)
            return _native(Deferred(Just(self)).classify(by=native_by, on=on))
        return Population(
            _closed(
                lambda: build.classify(
                    self._definition,
                    _axes(by, distinct=False),
                    _subject(on, "population.classify"),
                    exact=tuple(isinstance(value, Grouping) for value in by),
                ),
                "population.classify",
            ),
            self._frame_inputs,
            _context=context,
            _identity_keys=_identity_keys(self, by, on),
        )

    @overload
    def identities(self, *, on: Grouping) -> Population: ...

    @overload
    def identities(self, *, on: Deferred) -> Deferred: ...

    @traced(surface="analysis", operation="population.identities", capture="short")
    def identities(self, *, on: Grouping | Deferred) -> Population | Deferred:
        """Project the captured complete keys of one identity role as direct identities."""
        if isinstance(on, Deferred):
            return _native(Deferred(Just(self)).identities(on=on))
        location = "population.identities"
        subject = _subject(on, location)
        assert subject is not None
        return Population(
            _closed(lambda: build.identities(self._definition, subject), location),
            self._frame_inputs,
            _context=_context(self, on),
            _identity_keys=_identity_keys(self, on),
        )

    @traced(surface="analysis", operation="population.where", capture="short")
    def where(self, predicate: Deferred | RowPredicate) -> Population | Deferred:
        """Filter complete coordinates using predicates over their captured values.

        Args: predicate: Existing fields and complete membership predicates combined with ``&``, ``|`` or ``~``.
        Returns: A lazy subset with unchanged axes.
        Example: ``domain.where(mv.in_population(allowed))``.
        Constraints: Every predicate input must bind to existing receiver axes. Only True is retained.
        """
        context = _context(self, predicate)
        if _has_parameters(predicate):
            return _native(
                Deferred(Just(self)).where(
                    as_deferred(predicate) if isinstance(predicate, RowExpression) else predicate
                )
            )
        native = as_deferred(predicate) if isinstance(predicate, RowExpression) else predicate
        frame_inputs = collect_frame_key_inputs(native)
        definition = _closed(
            lambda: build.where(self._definition, consume_predicate(native, coordinates=True).node),
            "population.where",
        )
        return Population(
            definition,
            merge_frame_key_inputs(self._frame_inputs, frame_inputs, owner=definition.owner),
            _context=context,
            _identity_keys=self._identity_keys,
        )

    @overload
    def image(self, *, via: Path, on: Grouping | None = None) -> Population: ...

    @overload
    def image(
        self, *, via: Path | PathSymbol, on: Grouping | Deferred | None = None
    ) -> Population | Deferred: ...

    @traced(surface="analysis", operation="population.image", capture="short")
    def image(
        self, *, via: Path | PathSymbol, on: Grouping | Deferred | None = None
    ) -> Population | Deferred:
        """Map selected identities to reached identities through a directed path.

        Args: via: Directed Path from mv.path(...). on: Explicit receiver identity role when needed.
        Returns: A deduplicated target identity domain, preserving missing-reference keys.
        Example: ``orders.image(via=mv.path(order_customer))``.
        Constraints: Selects one compatible identity role and returns a direct target identity domain.
        """
        context = _context(self, via, on)
        captured = _capture_path(via)
        if isinstance(captured, Deferred) or isinstance(on, Deferred):
            return _closed(
                lambda: _capture_population_call(self, "image", via=captured, on=on),
                "population.image",
            )
        return Population(
            _closed(
                lambda: build.image(self._definition, captured, _subject(on, "population.image")),
                "population.image",
            ),
            self._frame_inputs,
            _context=context,
            _identity_keys=(
                (captured.target.ref.key, captured.steps[-1].relationship.target_keys),
            ),
        )

    @overload
    def preimage(
        self, targets: Population, *, via: Path, on: Grouping | None = None
    ) -> Population: ...

    @overload
    def preimage(
        self,
        targets: Population | Deferred,
        *,
        via: Path | PathSymbol,
        on: Grouping | Deferred | None = None,
    ) -> Population | Deferred: ...

    @traced(surface="analysis", operation="population.preimage", capture="short")
    def preimage(
        self,
        targets: Population | Deferred,
        *,
        via: Path | PathSymbol,
        on: Grouping | Deferred | None = None,
    ) -> Population | Deferred:
        """Keep receiver identities that reach a selected target identity.

        Args: targets: One direct target identity domain. via: Directed Path. on: Explicit receiver identity role when needed.
        Returns: A lazy subset of this domain.
        Example: ``orders.preimage(customers, via=mv.path(order_customer))``.
        Constraints: Relationship mapping uses complete declared identity keys.
        """
        captured = _capture_path(via)
        if (
            isinstance(targets, Deferred)
            or isinstance(captured, Deferred)
            or isinstance(on, Deferred)
        ):
            return _closed(
                lambda: _capture_population_call(self, "preimage", targets, via=captured, on=on),
                "population.preimage",
            )
        definition = _closed(
            lambda: build.preimage(
                self._definition, targets._definition, captured, _subject(on, "population.preimage")
            ),
            "population.preimage",
        )
        return Population(
            definition,
            merge_frame_key_inputs(
                self._frame_inputs, targets._frame_inputs, owner=definition.owner
            ),
            _context=_context(self, targets, via, on),
            _identity_keys=_identity_keys(self, targets),
        )


@overload
def in_population(population: Population) -> RowPredicate: ...


@overload
def in_population(population: Deferred) -> Deferred: ...


@traced(surface="analysis", operation="in_population", capture="short")
def in_population(population: Population | Deferred) -> Deferred | RowPredicate:
    """Match complete coordinates projected onto a Population's bound axes.

    Args:
        population: The allowed finite or lazy coordinate domain.

    Returns:
        A row Predicate for closed input, or an authoring Deferred for symbols; combine conditions with &, | and ~.

    Example:
        ``domain.where(mv.in_population(allowed))``.

    Constraints:
        Captures complete tuples with null-safe matching and performs no
        source access. Population.where binds membership and field predicates to
        existing axes. Source consumers may read the required coordinates.
    """
    if isinstance(population, Deferred):
        return _closed(
            lambda: parameter(ContainsParameter(population, _context=_context(population))),
            "in_population",
        )
    if not isinstance(population, Population):
        reject(
            "a Population coordinate domain",
            type(population).__name__,
            "Construct a Population with coordinates, members or population_from.",
            "in_population",
        )
    return RowExpression(PopulationContainsResolver(population), population._context, "boolean")


def _interval_population(scope: TimeScope) -> Population:

    coordinate = interval_coordinate(scope)
    return Population(PopulationProgram(IntervalKeys(coordinate), None, (coordinate,)))


def _calendar_population(calendar: CalendarAxis) -> Population:
    coordinate = grouping_binding(calendar)
    return Population(PopulationProgram(CalendarKeys(coordinate), None, (coordinate,)))


@overload
def membership(
    by: FieldExpression | tuple[FieldExpression, ...],
    *,
    via: tuple[Path, ...],
    unmatched: Literal["exclude", "null"],
) -> Grouping: ...


@overload
def membership(
    by: Deferred | FieldExpression | tuple[Deferred | FieldExpression, ...],
    *,
    via: tuple[Path | PathSymbol, ...],
    unmatched: Literal["exclude", "null"],
) -> Grouping | Deferred: ...


@traced(surface="analysis", operation="membership", capture="short")
def membership(
    by: Deferred | FieldExpression | tuple[Deferred | FieldExpression, ...],
    *,
    via: tuple[Path | PathSymbol, ...],
    unmatched: Literal["exclude", "null"],
) -> Grouping | Deferred:
    """Capture correlated member fields and explicit allocation paths.

    Args: by: One field or nonempty tuple from one member Entity. via: Ordered
        directed paths, possibly empty. unmatched: ``exclude`` or ``null``.

    Returns: An immutable Grouping for closed input, or a Deferred parameter for symbols.
    Example: ``mv.membership(tag, via=(mv.path(order_tags),), unmatched='exclude')``.
    Constraints: Captures metadata only; no source or Session is opened.
    """
    fields = tuple(by) if isinstance(by, tuple) else (by,)
    context = _context(fields, via)
    paths = tuple(_capture_path(path) for path in via)
    if _has_parameters(fields) or any(isinstance(path, Deferred) for path in paths):
        return _closed(
            lambda: parameter(
                MembershipParameter(
                    tuple(
                        as_deferred(item) if isinstance(item, RowExpression) else item
                        for item in fields
                    ),
                    paths,
                    unmatched,
                    _context=_context(fields, via),
                )
            ),
            "membership",
        )
    reads = tuple(_read(item) for item in fields)
    return _closed(
        lambda: _MembershipGrouping(
            build.membership(
                reads, tuple(path for path in paths if isinstance(path, PathParameter)), unmatched
            ),
            context,
        ),
        "membership",
    )


@overload
def coordinates(
    *, by: tuple[FieldExpression | Grouping, ...], keys: tuple[tuple[CoordinateValue, ...], ...]
) -> Population: ...


@overload
def coordinates(
    *,
    by: tuple[Deferred | FieldExpression | Grouping, ...],
    keys: tuple[tuple[CoordinateValue, ...], ...],
) -> Population | Deferred: ...


@traced(surface="analysis", operation="coordinates", capture="short")
def coordinates(
    *,
    by: tuple[Deferred | FieldExpression | Grouping, ...],
    keys: tuple[tuple[CoordinateValue, ...], ...],
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
    context = _context(captured_by)
    if _has_parameters(captured_by):
        return _closed(
            lambda: parameter(
                CoordinatesParameter(
                    tuple(
                        as_deferred(item) if isinstance(item, RowExpression) else item
                        for item in captured_by
                    ),
                    captured_keys,
                    _context=context,
                    _identity_keys=_identity_keys(captured_by),
                )
            ),
            "coordinates",
        )
    return Population(
        _closed(lambda: build.coordinates(_axes(captured_by), captured_keys), "coordinates"),
        _context=context,
        _identity_keys=_identity_keys(captured_by),
    )


@overload
def product(*factors: Population | CalendarAxis | TimeScope) -> Population: ...


@overload
def product(
    *factors: Population | Deferred | CalendarAxis | TimeScope,
) -> Population | Deferred: ...


@traced(surface="analysis", operation="product", capture="short")
def product(*factors: Population | Deferred | CalendarAxis | TimeScope) -> Population | Deferred:
    """Form a lazy Cartesian product of disjoint coordinate domains.

    Args: factors: At least two Population, CalendarAxis, TimeScope or Deferred factors with distinct axes and compatible owners.
    Returns: A Population for closed factors, or a Deferred parameter for symbols, with complete factor-key combinations.
    Example: ``mv.product(regions, channels)``.
    Constraints: An empty factor makes the complete product empty.
    """
    captured = tuple(factors)
    context = _context(captured)
    if _has_parameters(captured):
        return _closed(lambda: parameter(ProductParameter(captured, _context=context)), "product")
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
        _context=context,
        _identity_keys=_identity_keys(populations),
    )
