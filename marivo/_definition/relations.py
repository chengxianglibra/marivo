"""Closed Population and Query definition variants referenced by Metric definitions.

Only the data structures needed to capture dependencies and detect cycles
live here; evaluation belongs to analysis. Records never hold public
Population objects, Sessions, Catalogs, Ibis expressions, or Artifacts.
"""

from __future__ import annotations

import json
import operator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from ibis.common.deferred import BinaryOperator, Just, Resolver, UnaryOperator, Variable

from marivo._definition.correspondence import (
    ComparisonAxis,
    coordinate_matches,
    coordinate_position,
)
from marivo._definition.deferred import RelationshipParameter
from marivo._definition.query import ResultQuery
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    ExactRef,
    FieldRead,
    InputContext,
    IntervalCoordinate,
    LiteralValue,
    Owner,
    check_literal,
    invalid,
    literal_token,
)
from marivo.refs import SemanticKind

if TYPE_CHECKING:
    from marivo._definition.expressions import MetricNode, PredicateExpression
    from marivo._temporal import Grain, InstantAnchor


@dataclass(frozen=True, slots=True)
class MembershipBinding:
    """One correlated member tuple and explicit contribution allocation paths."""

    fields: tuple[FieldRead, ...]
    paths: tuple[tuple[RelationshipParameter, ...], ...]
    unmatched: Literal["exclude", "null"]


@dataclass(frozen=True, slots=True)
class SampleDomain:
    """Observed internal coordinates with their membership allocation groups."""

    axes: tuple[ComparisonAxis, ...]
    memberships: tuple[MembershipBinding, ...] = ()
    population: PopulationDependency | None = None


def validate_memberships(
    axes: tuple[ComparisonAxis, ...], bindings: tuple[MembershipBinding, ...]
) -> None:
    """Require each consumer coordinate to have at most one explicit allocation."""
    allocated: set[int] = set()
    for binding in bindings:
        positions: list[int] = []
        for read in binding.fields:
            axis = AxisBinding(read.entity, read.field, read.path, "field", read.at)
            if not any(coordinate_matches(axis, candidate) for candidate in axes):
                invalid("membership fields must already occur in the consuming coordinates")
            position = coordinate_position(axes, axis)
            if position in allocated:
                invalid("a coordinate cannot have repeated membership allocation rules")
            positions.append(position)
            allocated.add(position)
        if positions != sorted(positions):
            invalid(
                "membership fields must retain their captured order in the consuming coordinates"
            )


def _coordinate_token(
    axis: ComparisonAxis,
    value: CoordinateValue,
    position: int,
    lengths: dict[int, int],
) -> tuple[str, str]:
    if isinstance(axis, IntervalCoordinate):
        if type(value) is not int or value != 0:
            invalid("interval coordinates must come from their frozen TimeScope")
        return ("interval", str(value))
    if isinstance(axis, CalendarCoordinate):
        if type(value) is not int or not 0 <= value < len(axis.cells):
            invalid("calendar coordinates must come from their frozen CalendarAxis")
        return ("calendar", str(value))
    entity = axis.entity.ref.key
    if axis.role == "field":
        if isinstance(value, tuple):
            invalid(
                f"field axis on {entity} expected one literal value; received tuple {value!r}. "
                "Pass primary-key tuples only on identity axes."
            )
        return literal_token(check_literal(value, role="literal population key"))
    if not isinstance(value, tuple) or not value:
        invalid(
            f"identity axis {entity} expected a complete primary-key tuple; received "
            f"{value!r}. Pass every primary-key component in declared order, e.g. ('O1',)."
        )
    for component in value:
        if component is None:
            invalid(
                f"identity axis {entity} primary-key component cannot be None; received "
                f"{value!r}. Pass a declared identity."
            )
        check_literal(component, role="literal population key")
    expected = lengths.setdefault(position, len(value))
    if len(value) != expected:
        invalid(
            f"identity axis {entity} expected every key to have the same number of components "
            f"({expected}); received {value!r}. Pass the complete primary key for each identity."
        )
    tokens = [list(literal_token(component)) for component in value]
    return ("identity", json.dumps(tokens, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class LiteralPopulation:
    """Immutable key set over ordered axes; keys are de-duplicated as a set.

    Field axes take one literal (``None`` is the source Null category); identity
    axes take the complete primary-key tuple, so composite identities keep their
    boundary inside the key instead of being flattened into extra axes.
    """

    axes: tuple[AxisBinding, ...]
    keys: tuple[tuple[CoordinateValue, ...], ...]
    version: str

    def __post_init__(self) -> None:
        unique: dict[tuple[tuple[str, str], ...], tuple[CoordinateValue, ...]] = {}
        lengths: dict[int, int] = {}
        for key in self.keys:
            if len(key) != len(self.axes):
                invalid(
                    f"literal population expected keys with {len(self.axes)} values; "
                    f"received {key!r}. Give one value per axis."
                )
            tokens = tuple(
                _coordinate_token(axis, value, position, lengths)
                for position, (axis, value) in enumerate(zip(self.axes, key, strict=True))
            )
            unique.setdefault(tokens, key)
        object.__setattr__(self, "keys", tuple(unique[token] for token in sorted(unique)))


@dataclass(frozen=True, slots=True)
class SourceIdentities:
    """Identities of one Entity as selected from its source."""

    entity: ExactRef
    at: InstantAnchor | None = None
    predicate: PredicateExpression | None = None

    def __post_init__(self) -> None:
        if self.entity.ref.kind is not SemanticKind.ENTITY:
            invalid(
                f"source identities expected an entity reference; received "
                f"{self.entity.ref.key}. Select identities of a declared entity."
            )


@dataclass(frozen=True, slots=True)
class ObserveQuery:
    """Lazy observation of labelled metrics over output axes."""

    metrics: tuple[tuple[str, MetricNode], ...]
    by: tuple[ComparisonAxis, ...]
    owner: Owner | None
    population: PopulationDependency | None = None
    count_populations: tuple[PopulationDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadQuery:
    """Explicit identity-domain attribute projection over a classified Population."""

    population: PopulationDependency
    positions: tuple[int, ...]
    by: tuple[ComparisonAxis, ...]
    owner: Owner


QueryNode: TypeAlias = ObserveQuery | ReadQuery | ResultQuery


@dataclass(frozen=True, slots=True)
class FrameKeyAxis:
    """One complete Frame coordinate explicitly mapped to a semantic axis."""

    token: str
    key_names: tuple[str, ...]
    target: AxisBinding

    def __post_init__(self) -> None:
        if (
            type(self.token) is not str
            or not self.token
            or type(self.key_names) is not tuple
            or not self.key_names
            or any(type(name) is not str or not name for name in self.key_names)
            or len(set(self.key_names)) != len(self.key_names)
            or type(self.target) is not AxisBinding
            or (self.target.role == "field" and len(self.key_names) != 1)
        ):
            invalid(
                "Frame key mapping requires one exact token, complete component names and "
                "a field or identity target. Select a complete ordinary Frame axis."
            )


@dataclass(frozen=True, slots=True)
class FrameKeys:
    """Inert joint-key projection; the analysis owner retains its actual Frame."""

    input_id: str
    bindings: tuple[FrameKeyAxis, ...]

    def __post_init__(self) -> None:
        if (
            type(self.input_id) is not str
            or not self.input_id
            or type(self.bindings) is not tuple
            or any(type(binding) is not FrameKeyAxis for binding in self.bindings)
            or len({binding.token for binding in self.bindings}) != len(self.bindings)
            or len({binding.target for binding in self.bindings}) != len(self.bindings)
        ):
            invalid(
                "Frame keys require an exact input identity and distinct complete mappings. "
                "Select each input and target axis once; an empty mapping is valid."
            )


@dataclass(frozen=True, slots=True)
class AttributeKeys:
    """Private endpoint projection of one complete selected parent domain."""

    input_id: str
    bindings: tuple[FrameKeyAxis, ...]
    calendar_token: str | None = None
    calendar: CalendarCoordinate | IntervalCoordinate | None = None
    side: Literal["left", "right"] = "left"
    how: Literal["exact", "outer"] = "exact"
    comparison_alias: str | None = None
    fixed: tuple[tuple[AxisBinding, tuple[LiteralValue, ...]], ...] = ()
    presence: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PopulationSet:
    left: PopulationDependency
    right: PopulationDependency
    operation: Literal["union", "intersection", "difference"]


@dataclass(frozen=True, slots=True)
class PopulationProject:
    operand: PopulationDependency
    positions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PopulationProduct:
    factors: tuple[PopulationDependency, ...]


@dataclass(frozen=True, slots=True)
class PopulationClassify:
    operand: PopulationDependency
    fields: tuple[FieldRead, ...]


@dataclass(frozen=True, slots=True)
class PopulationContains:
    population: PopulationDependency


@dataclass(frozen=True, slots=True)
class PopulationFilter:
    operand: PopulationDependency
    predicate: PredicateExpression


@dataclass(frozen=True, slots=True)
class PopulationImage:
    operand: PopulationDependency
    path: tuple[RelationshipParameter, ...]
    at: InstantAnchor | None = None


@dataclass(frozen=True, slots=True)
class PopulationPreimage:
    operand: PopulationDependency
    targets: PopulationDependency
    path: tuple[RelationshipParameter, ...]


@dataclass(frozen=True, slots=True)
class IntervalKeys:
    interval: IntervalCoordinate


@dataclass(frozen=True, slots=True)
class CalendarKeys:
    calendar: CalendarCoordinate


PopulationNode: TypeAlias = (
    SourceIdentities
    | FrameKeys
    | AttributeKeys
    | PopulationSet
    | PopulationProject
    | PopulationProduct
    | PopulationClassify
    | PopulationFilter
    | PopulationImage
    | PopulationPreimage
    | CalendarKeys
    | IntervalKeys
)


@dataclass(frozen=True, slots=True)
class PopulationProgram:
    """Complete lazy Population definition with its existing Session ownership."""

    root: PopulationNode
    owner: Owner | None
    axes: tuple[ComparisonAxis, ...] = ()


PopulationDependency: TypeAlias = LiteralPopulation | PopulationProgram


@dataclass(frozen=True, slots=True)
class PopulationParameter:
    """Private immutable population capture shared with semantic deferred consumers."""

    _definition: PopulationDependency
    _context: InputContext = field(default=InputContext(), kw_only=True, compare=False)
    _identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = field(
        default=(), kw_only=True, compare=False
    )


def sample_population(
    samples: tuple[AxisBinding, ...] | SampleDomain | LiteralPopulation | PopulationProgram | Grain,
) -> PopulationDependency | None:
    """Return the separately captured finite sample domain, when supplied."""
    if isinstance(samples, SampleDomain):
        return samples.population
    return samples if isinstance(samples, (LiteralPopulation, PopulationProgram)) else None


def condition_populations(condition: PredicateExpression) -> tuple[PopulationDependency, ...]:
    """Return ordered inert Population operands without evaluating their programs."""
    return tuple(
        item.population for item in condition.inputs if isinstance(item, PopulationContains)
    )


def _condition_value(recipe: Resolver, values: tuple[bool, ...]) -> bool:
    """Interpret the admitted native boolean structure on complete coordinate keys."""
    if isinstance(recipe, Variable):
        return values[int(str(recipe.name).removeprefix("slot_"))]
    if isinstance(recipe, Just) and type(recipe.value) is bool:
        return recipe.value
    if isinstance(recipe, UnaryOperator) and recipe.func is operator.invert:
        return not _condition_value(recipe.arg, values)
    if isinstance(recipe, BinaryOperator) and recipe.func in (operator.and_, operator.or_):
        left, right = _condition_value(recipe.left, values), _condition_value(recipe.right, values)
        return left and right if recipe.func is operator.and_ else left or right
    invalid("Expected an admitted membership boolean recipe; capture it through in_population.")


def population_keys(
    definition: PopulationDependency,
) -> tuple[tuple[CoordinateValue, ...], ...] | None:
    """Evaluate a source-free finite domain; None means a source program is needed."""
    from itertools import product as cartesian_product

    if isinstance(definition, LiteralPopulation):
        return definition.keys
    node = definition.root
    if isinstance(node, IntervalKeys):
        return ((0,),)
    if isinstance(node, CalendarKeys):
        return tuple((index,) for index in range(len(node.calendar.cells)))
    if isinstance(node, PopulationFilter):
        source = population_keys(node.operand)
        domains = condition_populations(node.predicate)
        if source is None or any(population_keys(domain) is None for domain in domains):
            return None

        def matches(row: tuple[CoordinateValue, ...]) -> bool:
            values: list[bool] = []
            for condition in node.predicate.inputs:
                if not isinstance(condition, PopulationContains):
                    invalid("Population.where requires membership leaves; use members for fields.")
                keys = population_keys(condition.population)
                assert keys is not None
                projection = tuple(
                    row[coordinate_position(definition.axes, axis)]
                    for axis in condition.population.axes
                )

                def token_key(
                    key: tuple[CoordinateValue, ...],
                    axes: tuple[ComparisonAxis, ...] = condition.population.axes,
                ) -> tuple[tuple[str, str], ...]:
                    return tuple(
                        _coordinate_token(axis, value, index, {})
                        for index, (axis, value) in enumerate(zip(axes, key, strict=True))
                    )

                values.append(token_key(projection) in {token_key(key) for key in keys})
            return _condition_value(node.predicate.recipe, tuple(values))

        return tuple(row for row in source if matches(row))

    if isinstance(node, PopulationSet):
        left, right = population_keys(node.left), population_keys(node.right)
        if left is None or right is None:
            return None
        rows = (*left, *right)
    elif isinstance(node, PopulationProject):
        source = population_keys(node.operand)
        if source is None:
            return None
        rows = tuple(tuple(row[index] for index in node.positions) for row in source)
    elif isinstance(node, PopulationProduct):
        factors = tuple(population_keys(factor) for factor in node.factors)
        if any(factor is None for factor in factors):
            return None
        known = tuple(factor for factor in factors if factor is not None)
        rows = tuple(
            tuple(value for row in combination for value in row)
            for combination in cartesian_product(*known)
        )
    else:
        return None

    def token(row: tuple[CoordinateValue, ...]) -> tuple[tuple[str, str], ...]:
        return tuple(
            _coordinate_token(axis, value, index, {})
            for index, (axis, value) in enumerate(zip(definition.axes, row, strict=True))
        )

    if isinstance(node, PopulationSet):
        assert left is not None and right is not None
        left_map = {token(row): row for row in left}
        right_map = {token(row): row for row in right}
        if node.operation == "intersection":
            result = {key: row for key, row in left_map.items() if key in right_map}
        elif node.operation == "difference":
            result = {key: row for key, row in left_map.items() if key not in right_map}
        else:
            result = left_map | right_map
    else:
        result = {token(row): row for row in rows}
    return tuple(result[key] for key in sorted(result))
