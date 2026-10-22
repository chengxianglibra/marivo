"""Pure construction of closed coordinate domains and allocation rules.

Public adapters and symbolic binding use these same builders. They consume
definition facts only; no Session, source reader or public semantic wrapper
belongs in this module.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

from marivo._definition.correspondence import (
    ComparisonAxis,
    coordinate_matches,
    coordinate_position,
)
from marivo._definition.deferred import RelationshipParameter
from marivo._definition.expressions import PredicateExpression
from marivo._definition.relations import (
    LiteralPopulation,
    MembershipBinding,
    PopulationClassify,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationImage,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    condition_populations,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    Owner,
    invalid,
)
from marivo._temporal import InstantAnchor


def axis(read: FieldRead) -> AxisBinding:
    return AxisBinding(read.entity, read.field, read.path, "field", read.at)


def axes(values: tuple[ComparisonAxis, ...]) -> tuple[ComparisonAxis, ...]:
    if sum(isinstance(item, (CalendarCoordinate, IntervalCoordinate)) for item in values) > 1:
        invalid("coordinates allow at most one output time axis")
    if any(
        coordinate_matches(left, right)
        for index, left in enumerate(values)
        for right in values[index + 1 :]
    ):
        invalid("coordinate axes must be distinct; remove duplicate or conflicting bindings")
    return values


def anchor(value: InstantAnchor | None) -> None:
    if value is not None and (not isinstance(value, InstantAnchor) or value.utcoffset() is None):
        invalid("an identity version selection requires an exact InstantAnchor")


def connected(path: tuple[RelationshipParameter, ...]) -> None:
    if not path or any(
        left.to_entity.ref != right.from_entity.ref for left, right in pairwise(path)
    ):
        invalid("relationships must form a nonempty connected directed path")


def identity(
    target: ExactRef | tuple[RelationshipParameter, ...], at: InstantAnchor | None = None
) -> AxisBinding:
    anchor(at)
    path: tuple[ExactRef, ...]
    if isinstance(target, ExactRef):
        entity, path = target, ()
    else:
        connected(target)
        entity, path = target[-1].to_entity, tuple(step.ref for step in target)
    return AxisBinding(entity, None, path, "identity", at)


def membership(
    reads: tuple[FieldRead, ...],
    paths: tuple[tuple[RelationshipParameter, ...], ...],
    unmatched: Literal["exclude", "null"],
) -> MembershipBinding:
    if unmatched not in ("exclude", "null"):
        invalid("membership unmatched must be exclude or null")
    if not reads or len({read.entity.ref for read in reads}) != 1:
        invalid("membership requires nonempty correlated fields from one member Entity")
    if any(read.path for read in reads):
        invalid("membership fields must be direct; put allocation paths in via")
    starts = set()
    for path in paths:
        connected(path)
        if path[-1].to_entity.ref != reads[0].entity.ref:
            invalid("membership paths must end at the member Entity")
        if path[0].from_entity.ref in starts:
            invalid("membership accepts one explicit path per contribution root")
        starts.add(path[0].from_entity.ref)
    axes(tuple(axis(read) for read in reads))
    return MembershipBinding(reads, paths, unmatched)


def owner(definition: PopulationDependency) -> Owner | None:
    return definition.owner if isinstance(definition, PopulationProgram) else None


def combined_owner(definitions: tuple[PopulationDependency, ...]) -> Owner | None:
    owners = {owner(item) for item in definitions} - {None}
    if len(owners) > 1:
        invalid("populations must have one Session owner", kind="ownership_conflict")
    return next(iter(owners), None)


def coordinates(
    values: tuple[ComparisonAxis, ...], keys: tuple[tuple[CoordinateValue, ...], ...]
) -> LiteralPopulation:
    axes(values)
    if any(
        not isinstance(value, AxisBinding) or (value.role == "identity" and value.path)
        for value in values
    ):
        invalid("literal coordinates require fields or direct Entity identity axes")
    return LiteralPopulation(
        tuple(value for value in values if isinstance(value, AxisBinding)), keys, "coordinates-v1"
    )


def project(
    definition: PopulationDependency, values: tuple[ComparisonAxis, ...]
) -> PopulationProgram:
    axes(values)
    if any(
        not any(coordinate_matches(value, original) for original in definition.axes)
        for value in values
    ):
        invalid("projection requires complete axes already present in the Population")
    positions = tuple(coordinate_position(definition.axes, value) for value in values)
    return PopulationProgram(
        PopulationProject(definition, positions),
        owner(definition),
        tuple(definition.axes[position] for position in positions),
    )


def set_operation(
    left: PopulationDependency,
    right: PopulationDependency,
    operation: Literal["union", "intersection", "difference"],
) -> PopulationProgram:
    if len(left.axes) != len(right.axes) or any(
        not coordinate_matches(a, b) for a, b in zip(left.axes, right.axes, strict=True)
    ):
        invalid("set operations require identical ordered coordinate axes")
    return PopulationProgram(
        PopulationSet(left, right, operation), combined_owner((left, right)), left.axes
    )


def product(factors: tuple[PopulationDependency, ...]) -> PopulationProgram:
    if len(factors) < 2:
        invalid("product requires at least two complete Population factors")
    values = axes(tuple(value for factor in factors for value in factor.axes))
    return PopulationProgram(PopulationProduct(factors), combined_owner(factors), values)


def classify(
    definition: PopulationDependency, values: tuple[ComparisonAxis, ...]
) -> PopulationProgram:
    if (
        len(definition.axes) != 1
        or not isinstance(definition.axes[0], AxisBinding)
        or definition.axes[0].role != "identity"
    ):
        invalid("classification requires one Entity identity axis")
    axes(values)
    entity = definition.axes[0].entity
    if any(not isinstance(value, AxisBinding) for value in values):
        invalid("classification requires member fields, without calendar or interval axes")
    member_axes = tuple(value for value in values if isinstance(value, AxisBinding))
    if any(value.entity.ref != entity.ref or value.path for value in member_axes):
        invalid("classification requires direct fields on the member Entity")
    if any(
        value.role == "identity" and not coordinate_matches(value, definition.axes[0])
        for value in member_axes
    ):
        invalid("classification must retain the existing member identity binding")
    reads = tuple(
        FieldRead(value.field, value.entity, (), value.at)
        for value in member_axes
        if value.field is not None
    )
    if not reads:
        return project(definition, values)
    return PopulationProgram(PopulationClassify(definition, reads), owner(definition), values)


def where(definition: PopulationDependency, predicate: PredicateExpression) -> PopulationProgram:
    if any(not isinstance(item, PopulationContains) for item in predicate.inputs):
        invalid(
            "Population filtering rejects a source field condition; use membership leaves over existing coordinates"
        )
    dependencies = condition_populations(predicate)
    for dependency in dependencies:
        if any(
            not any(coordinate_matches(value, original) for original in definition.axes)
            for value in dependency.axes
        ):
            invalid("predicate domains must project onto a subset of receiver axes")
    return PopulationProgram(
        PopulationFilter(definition, predicate),
        combined_owner((definition, *dependencies)),
        definition.axes,
    )


def identity_path(
    definition: PopulationDependency, path: tuple[RelationshipParameter, ...]
) -> None:
    connected(path)
    if (
        len(definition.axes) != 1
        or not isinstance(definition.axes[0], AxisBinding)
        or definition.axes[0].role != "identity"
        or path[0].from_entity.ref != definition.axes[0].entity.ref
    ):
        invalid("relationship mapping requires one identity axis and a path starting at its Entity")


def image(
    definition: PopulationDependency,
    path: tuple[RelationshipParameter, ...],
    at: InstantAnchor | None = None,
) -> PopulationProgram:
    identity_path(definition, path)
    anchor(at)
    return PopulationProgram(
        PopulationImage(definition, path, at),
        owner(definition),
        (AxisBinding(path[-1].to_entity, None, (), "identity", at),),
    )


def preimage(
    definition: PopulationDependency,
    targets: PopulationDependency,
    path: tuple[RelationshipParameter, ...],
) -> PopulationProgram:
    identity_path(definition, path)
    if (
        len(targets.axes) != 1
        or not isinstance(targets.axes[0], AxisBinding)
        or targets.axes[0].role != "identity"
        or targets.axes[0].entity.ref != path[-1].to_entity.ref
    ):
        invalid("preimage requires the path target identity axis")
    return PopulationProgram(
        PopulationPreimage(definition, targets, path),
        combined_owner((definition, targets)),
        definition.axes,
    )
