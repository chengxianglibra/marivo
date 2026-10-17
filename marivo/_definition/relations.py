"""Closed Population and Query definition variants referenced by Metric definitions.

Only the data structures needed to capture dependencies and detect cycles
live here; evaluation belongs to analysis. Records never hold public
Population objects, Sessions, Catalogs, Ibis expressions, or Artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._definition.deferred import RelationshipParameter
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    ExactRef,
    FieldRead,
    Owner,
    check_literal,
    invalid,
    literal_token,
)
from marivo.refs import SemanticKind

if TYPE_CHECKING:
    from marivo._definition.expressions import FieldPredicate, MetricNode
    from marivo._definition.observations import ObservationBinding
    from marivo._temporal import InstantAnchor


@dataclass(frozen=True, slots=True)
class MembershipBinding:
    """One correlated member tuple and explicit contribution allocation paths."""

    fields: tuple[FieldRead, ...]
    paths: tuple[tuple[RelationshipParameter, ...], ...]
    unmatched: Literal["exclude", "null"]


@dataclass(frozen=True, slots=True)
class SampleDomain:
    """Observed internal coordinates with their membership allocation groups."""

    axes: tuple[AxisBinding, ...]
    memberships: tuple[MembershipBinding, ...] = ()


def _coordinate_token(
    axis: AxisBinding | CalendarCoordinate,
    value: CoordinateValue,
    position: int,
    lengths: dict[int, int],
) -> tuple[str, str]:
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
    memberships: tuple[MembershipBinding, ...] = ()

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
    predicate: FieldPredicate | None = None

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
    by: tuple[AxisBinding | CalendarCoordinate, ...]
    owner: Owner | None
    population: PopulationDependency | None = None


QueryNode: TypeAlias = ObserveQuery


@dataclass(frozen=True, slots=True)
class QueryKeys:
    """Keys of a Query's output coordinates projected onto the given axes."""

    query: QueryNode
    axes: tuple[AxisBinding | CalendarCoordinate, ...]
    observation: ObservationBinding | None = None
    declared: tuple[tuple[ExactRef, MetricNode], ...] = ()
    units: tuple[str | None, ...] = ()


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
class PopulationAnd:
    left: PopulationCondition
    right: PopulationCondition


@dataclass(frozen=True, slots=True)
class PopulationOr:
    left: PopulationCondition
    right: PopulationCondition


@dataclass(frozen=True, slots=True)
class PopulationNot:
    operand: PopulationCondition


PopulationCondition: TypeAlias = PopulationContains | PopulationAnd | PopulationOr | PopulationNot


@dataclass(frozen=True, slots=True)
class PopulationFilter:
    operand: PopulationDependency
    predicate: PopulationCondition


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
class CalendarKeys:
    calendar: CalendarCoordinate


PopulationNode: TypeAlias = (
    SourceIdentities
    | QueryKeys
    | PopulationSet
    | PopulationProject
    | PopulationProduct
    | PopulationClassify
    | PopulationFilter
    | PopulationImage
    | PopulationPreimage
    | CalendarKeys
)


@dataclass(frozen=True, slots=True)
class PopulationProgram:
    """Complete lazy Population definition with its existing Session ownership."""

    root: PopulationNode
    owner: Owner | None
    axes: tuple[AxisBinding | CalendarCoordinate, ...] = ()
    memberships: tuple[MembershipBinding, ...] = ()


PopulationDependency: TypeAlias = LiteralPopulation | PopulationProgram


@dataclass(frozen=True, slots=True)
class PopulationParameter:
    """Immutable population capture shared with semantic deferred consumers."""

    definition: PopulationDependency


@dataclass(frozen=True, slots=True)
class PopulationPredicateParameter:
    """Closed coordinate membership predicate shared with semantic consumers."""

    condition: PopulationCondition


def condition_populations(condition: PopulationCondition) -> tuple[PopulationDependency, ...]:
    """Return all immutable population operands in a closed predicate."""
    if isinstance(condition, PopulationContains):
        return (condition.population,)
    if isinstance(condition, PopulationNot):
        return condition_populations(condition.operand)
    return (*condition_populations(condition.left), *condition_populations(condition.right))


def population_keys(
    definition: PopulationDependency,
) -> tuple[tuple[CoordinateValue, ...], ...] | None:
    """Evaluate a source-free finite domain; None means a source program is needed."""
    from itertools import product as cartesian_product

    if isinstance(definition, LiteralPopulation):
        return definition.keys
    node = definition.root
    if isinstance(node, CalendarKeys):
        return tuple((index,) for index in range(len(node.calendar.cells)))
    if isinstance(node, PopulationFilter):
        source = population_keys(node.operand)
        domains = condition_populations(node.predicate)
        if source is None or any(population_keys(domain) is None for domain in domains):
            return None

        def matches(condition: PopulationCondition, row: tuple[CoordinateValue, ...]) -> bool:
            if isinstance(condition, PopulationContains):
                keys = population_keys(condition.population)
                assert keys is not None
                projection = tuple(
                    row[definition.axes.index(axis)] for axis in condition.population.axes
                )

                def token_key(key: tuple[CoordinateValue, ...]) -> tuple[tuple[str, str], ...]:
                    return tuple(
                        _coordinate_token(axis, value, index, {})
                        for index, (axis, value) in enumerate(
                            zip(condition.population.axes, key, strict=True)
                        )
                    )

                return token_key(projection) in {token_key(key) for key in keys}
            if isinstance(condition, PopulationNot):
                return not matches(condition.operand, row)
            if isinstance(condition, PopulationAnd):
                return matches(condition.left, row) and matches(condition.right, row)
            return matches(condition.left, row) or matches(condition.right, row)

        return tuple(row for row in source if matches(node.predicate, row))
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
