"""Complete-tuple coordinate predicates over invocation-local pandas rows."""

from __future__ import annotations

import pandas as pd

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.relations import (
    PopulationAnd,
    PopulationCondition,
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationNot,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    population_keys,
)
from marivo._definition.types import AxisBinding
from marivo.analysis.core.model import reject


def _token(value: object) -> object:
    if isinstance(value, tuple):
        return tuple(_token(component) for component in value)
    import math

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def condition_mask(
    condition: PopulationCondition,
    frame: pd.DataFrame,
    axes: tuple[ComparisonAxis, ...],
    groups: tuple[tuple[str, ...], ...],
    atoms: tuple[PopulationDependency, ...] = (),
    frames: tuple[pd.DataFrame, ...] = (),
    layouts: tuple[tuple[tuple[str, ...], ...], ...] = (),
) -> pd.Series:
    if isinstance(condition, PopulationContains):
        finite_keys = population_keys(condition.population)
        keys = (
            evaluate_population(condition.population, atoms, frames, layouts)
            if finite_keys is None
            else finite_keys
        )
        members = {_token(key) for key in keys}
        selected = tuple(groups[axes.index(axis)] for axis in condition.population.axes)
        values = [
            tuple(
                tuple(row[column] for column in group)
                if len(group) > 1 or (isinstance(axis, AxisBinding) and axis.field is None)
                else row[group[0]]
                for axis, group in zip(condition.population.axes, selected, strict=True)
            )
            for row in frame.to_dict(orient="records")
        ]
        return pd.Series([_token(key) in members for key in values], dtype="boolean")
    if isinstance(condition, PopulationNot):
        return ~condition_mask(condition.operand, frame, axes, groups, atoms, frames, layouts)
    left, right = (
        condition_mask(condition.left, frame, axes, groups, atoms, frames, layouts),
        condition_mask(condition.right, frame, axes, groups, atoms, frames, layouts),
    )
    return left & right if isinstance(condition, PopulationAnd) else left | right


def evaluate_population(
    definition: PopulationDependency,
    atoms: tuple[PopulationDependency, ...],
    frames: tuple[pd.DataFrame, ...],
    layouts: tuple[tuple[tuple[str, ...], ...], ...],
) -> tuple[tuple[object, ...], ...]:
    """Evaluate finite set algebra without discarding upstream Query checks."""
    from itertools import product

    from marivo._definition.graph import computation_fingerprint

    available = {
        computation_fingerprint(atom): (frame, groups)
        for atom, frame, groups in zip(atoms, frames, layouts, strict=True)
    }

    def rows(domain: PopulationDependency) -> tuple[tuple[object, ...], ...]:
        found = available.get(computation_fingerprint(domain))
        if found is not None:
            frame, groups = found
            if not groups:
                return ((),) if len(frame) else ()
            return unique(
                tuple(
                    tuple(
                        tuple(row[column] for column in group)
                        if len(group) > 1 or (isinstance(axis, AxisBinding) and axis.field is None)
                        else row[group[0]]
                        for axis, group in zip(domain.axes, groups, strict=True)
                    )
                    for row in frame.to_dict(orient="records")
                )
            )
        finite = population_keys(domain)
        if finite is not None:
            return tuple(finite)
        assert isinstance(domain, PopulationProgram)
        node = domain.root
        if isinstance(node, PopulationSet):
            left, right = rows(node.left), rows(node.right)
            lhs, rhs = {_token(row): row for row in left}, {_token(row): row for row in right}
            if node.operation == "union":
                return unique((*left, *right))
            return tuple(
                row
                for key, row in lhs.items()
                if (key in rhs) == (node.operation == "intersection")
            )
        if isinstance(node, PopulationProject):
            return unique(
                tuple(tuple(row[index] for index in node.positions) for row in rows(node.operand))
            )
        if isinstance(node, PopulationProduct):
            return unique(
                tuple(
                    tuple(value for row in combination for value in row)
                    for combination in product(*(rows(factor) for factor in node.factors))
                )
            )
        if isinstance(node, PopulationFilter):

            def matches(condition: PopulationCondition, row: tuple[object, ...]) -> bool:
                if isinstance(condition, PopulationContains):
                    projected = tuple(
                        row[domain.axes.index(axis)] for axis in condition.population.axes
                    )
                    return _token(projected) in {_token(key) for key in rows(condition.population)}
                if isinstance(condition, PopulationNot):
                    return not matches(condition.operand, row)
                left, right = matches(condition.left, row), matches(condition.right, row)
                return left and right if isinstance(condition, PopulationAnd) else left or right

            return tuple(row for row in rows(node.operand) if matches(node.predicate, row))
        reject(
            "a planned complete Population input",
            type(node).__name__,
            "Schedule every source/Query domain before set operations.",
            "Population.execute",
        )

    def unique(values: tuple[tuple[object, ...], ...]) -> tuple[tuple[object, ...], ...]:
        return tuple({_token(row): row for row in values}.values())

    return rows(definition)
