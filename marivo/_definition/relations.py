"""Validation and source-free operations over closed Population definitions."""

from __future__ import annotations

import operator
from dataclasses import replace
from itertools import product as cartesian_product
from typing import TYPE_CHECKING, TypeVar

from ibis.common.deferred import BinaryOperator, Just, Resolver, UnaryOperator, Variable

from marivo._definition.correspondence import (
    ComparisonAxis,
    coordinate_matches,
    coordinate_position,
)
from marivo._definition.population_types import AttributeKeys as AttributeKeys
from marivo._definition.population_types import (
    CalendarKeys as CalendarKeys,
)
from marivo._definition.population_types import FrameKeyAxis as FrameKeyAxis
from marivo._definition.population_types import FrameKeys as FrameKeys
from marivo._definition.population_types import (
    IntervalKeys as IntervalKeys,
)
from marivo._definition.population_types import (
    LiteralPopulation as LiteralPopulation,
)
from marivo._definition.population_types import (
    MembershipBinding as MembershipBinding,
)
from marivo._definition.population_types import PopulationClassify as PopulationClassify
from marivo._definition.population_types import (
    PopulationContains as PopulationContains,
)
from marivo._definition.population_types import (
    PopulationDependency as PopulationDependency,
)
from marivo._definition.population_types import (
    PopulationFilter as PopulationFilter,
)
from marivo._definition.population_types import PopulationImage as PopulationImage
from marivo._definition.population_types import PopulationNode as PopulationNode
from marivo._definition.population_types import PopulationParameter as PopulationParameter
from marivo._definition.population_types import PopulationPreimage as PopulationPreimage
from marivo._definition.population_types import (
    PopulationProduct as PopulationProduct,
)
from marivo._definition.population_types import (
    PopulationProgram as PopulationProgram,
)
from marivo._definition.population_types import (
    PopulationProject as PopulationProject,
)
from marivo._definition.population_types import (
    PopulationSet as PopulationSet,
)
from marivo._definition.population_types import (
    PredicateExpression,
    _coordinate_token,
)
from marivo._definition.population_types import (
    SampleDomain as SampleDomain,
)
from marivo._definition.population_types import SourceIdentities as SourceIdentities
from marivo._definition.query import ObserveQuery as ObserveQuery
from marivo._definition.query import QueryNode as QueryNode
from marivo._definition.query import ReadQuery as ReadQuery
from marivo._definition.types import (
    CalendarCoordinate,
    CoordinateValue,
    IntervalCoordinate,
    _merge_time_ranges,
    field_axis,
)
from marivo._kernel.errors import invalid

if TYPE_CHECKING:
    from marivo._definition.expressions import SampleDomainSpec


def validate_memberships(
    axes: tuple[ComparisonAxis, ...], bindings: tuple[MembershipBinding, ...]
) -> None:
    """Require each consumer coordinate to have at most one explicit allocation."""
    allocated: set[int] = set()
    for binding in bindings:
        positions: list[int] = []
        for read in binding.fields:
            axis = field_axis(read)
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


def merge_time_axis(left: ComparisonAxis, right: ComparisonAxis) -> ComparisonAxis:
    """Merge compatible directories without deriving membership from their bounds."""
    if left == right:
        return left
    if isinstance(left, IntervalCoordinate) and isinstance(right, IntervalCoordinate):
        bounds = tuple(sorted({*left.bounds, *right.bounds}))
        return replace(left, intervals=bounds)
    if isinstance(left, CalendarCoordinate) and isinstance(right, CalendarCoordinate):
        positions = {
            cell[0]: (cell, civil)
            for axis in (left, right)
            for cell, civil in zip(axis.cells, axis.civil_bounds, strict=True)
        }
        entries = tuple(positions[key] for key in sorted(positions))
        return replace(
            left,
            cells=tuple(cell for cell, _ in entries),
            civil_bounds=tuple(civil for _, civil in entries),
            ranges=_merge_time_ranges((*left.ranges, *right.ranges)),
        )
    return left


_Coordinate = TypeVar("_Coordinate")


def project_population_row(
    project: PopulationProject, values: tuple[_Coordinate, ...]
) -> tuple[_Coordinate | int, ...]:
    keys = dict(project.interval_keys)
    result = tuple(values[index] for index in project.positions)
    return tuple(
        keys[index][value] if index in keys and isinstance(value, int) else value
        for index, value in enumerate(result)
    )


def sample_population(
    samples: SampleDomainSpec,
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


def finite_population_domain(definition: PopulationDependency) -> bool:
    """Determine finite product availability without enumerating its tuples."""
    if isinstance(definition, LiteralPopulation):
        return True
    if isinstance(definition.root, (IntervalKeys, CalendarKeys)):
        return True
    if isinstance(definition.root, PopulationProduct):
        return all(finite_population_domain(factor) for factor in definition.root.factors)
    return population_keys(definition) is not None


def population_keys(
    definition: PopulationDependency,
) -> tuple[tuple[CoordinateValue, ...], ...] | None:
    """Evaluate a source-free finite domain; None means a source program is needed."""
    if isinstance(definition, LiteralPopulation):
        return definition.keys
    node = definition.root
    if isinstance(node, IntervalKeys):
        return ((0,),)
    if isinstance(node, CalendarKeys):
        return tuple((cell[0],) for cell in node.calendar.cells)
    if isinstance(node, PopulationFilter):
        return _filtered_keys(definition, node)

    if isinstance(node, PopulationSet):
        return _set_keys(definition.axes, node)
    if isinstance(node, PopulationProject):
        source = population_keys(node.operand)
        if source is None:
            return None
        rows = tuple(project_population_row(node, row) for row in source)
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

    result = {_key_token(definition.axes, row): row for row in rows}
    return tuple(result[key] for key in sorted(result))


def _key_token(
    axes: tuple[ComparisonAxis, ...], row: tuple[CoordinateValue, ...]
) -> tuple[tuple[str, str], ...]:
    return tuple(
        _coordinate_token(axis, value, index, {})
        for index, (axis, value) in enumerate(zip(axes, row, strict=True))
    )


def _set_keys(
    axes: tuple[ComparisonAxis, ...], node: PopulationSet
) -> tuple[tuple[CoordinateValue, ...], ...] | None:
    left, right = population_keys(node.left), population_keys(node.right)
    if left is None or right is None:
        return None
    left_map = {_key_token(axes, row): row for row in left}
    right_map = {_key_token(axes, row): row for row in right}
    if node.operation == "intersection":
        result = {key: row for key, row in left_map.items() if key in right_map}
    elif node.operation == "difference":
        result = {key: row for key, row in left_map.items() if key not in right_map}
    else:
        result = left_map | right_map
    return tuple(result[key] for key in sorted(result))


def _filtered_keys(
    definition: PopulationDependency, node: PopulationFilter
) -> tuple[tuple[CoordinateValue, ...], ...] | None:
    if any(not isinstance(item, PopulationContains) for item in node.predicate.inputs):
        return None  # Field conditions require the consuming physical key types.
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

            projected_axes = tuple(
                definition.axes[coordinate_position(definition.axes, axis)]
                for axis in condition.population.axes
            )
            values.append(
                _key_token(projected_axes, projection)
                in {_key_token(condition.population.axes, key) for key in keys}
            )
        return _condition_value(node.predicate.recipe, tuple(values))

    return tuple(row for row in source if matches(row))
