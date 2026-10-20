"""Complete-tuple coordinate predicates over invocation-local pandas rows."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pyarrow as pa

from marivo._definition.correspondence import ComparisonAxis, coordinate_position
from marivo._definition.relations import (
    PopulationContains,
    PopulationDependency,
    PopulationFilter,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    population_keys,
)
from marivo._definition.types import AxisBinding
from marivo.analysis.core.model import reject

_TICK_SCALES = {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}


def _token(value: object) -> object:
    if isinstance(value, tuple):
        return tuple(_token(component) for component in value)
    import math

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return (
            "timestamp",
            value.tzinfo is not None,
            int(value.asm8.view("i8")) * _TICK_SCALES[value.unit],
        )
    if isinstance(value, pd.Timedelta):
        return ("duration", int(value.asm8.view("i8")) * _TICK_SCALES[value.unit])
    if isinstance(value, (date, datetime, timedelta)):
        return _token(pa.scalar(value))
    if isinstance(value, pa.Scalar):
        if not value.is_valid:
            return None
        physical = value.type
        if pa.types.is_timestamp(physical) or pa.types.is_duration(physical):
            scale = _TICK_SCALES[physical.unit]
            kind = (
                ("timestamp", physical.tz is not None)
                if pa.types.is_timestamp(physical)
                else ("duration",)
            )
            return (*kind, int(value.cast(pa.int64()).as_py()) * scale)
        if pa.types.is_date32(physical):
            return ("date", int(value.cast(pa.int32()).as_py()))
        return _token(value.as_py())
    return value


def _coordinate_rows(
    frame: pd.DataFrame,
    axes: tuple[ComparisonAxis, ...],
    groups: tuple[tuple[str, ...], ...],
) -> tuple[tuple[object, ...], ...]:
    """Extract complete joint keys with Arrow temporal values and null validity."""
    arrays = {
        column: pa.array(frame[column], from_pandas=True) for group in groups for column in group
    }

    def scalar(column: str, index: int) -> object:
        value = arrays[column][index]
        if not value.is_valid:
            return None
        return value if pa.types.is_temporal(value.type) else value.as_py()

    return tuple(
        tuple(
            tuple(scalar(column, index) for column in group)
            if len(group) > 1 or (isinstance(axis, AxisBinding) and axis.field is None)
            else scalar(group[0], index)
            for axis, group in zip(axes, groups, strict=True)
        )
        for index in range(len(frame))
    )


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
            return unique(_coordinate_rows(frame, domain.axes, groups))
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
            # Resolve every leaf first so constant branches retain producer checks.
            members = {
                index: {_token(key) for key in rows(leaf.population)}
                for index, leaf in enumerate(node.predicate.inputs)
                if isinstance(leaf, PopulationContains)
            }

            def matches(row: tuple[object, ...]) -> bool:
                values: dict[str, object] = {}
                for index, leaf in enumerate(node.predicate.inputs):
                    assert isinstance(leaf, PopulationContains)
                    projected = tuple(
                        row[coordinate_position(domain.axes, axis)] for axis in leaf.population.axes
                    )
                    values[f"slot_{index}"] = pd.Series(
                        [_token(projected) in members[index]], dtype="boolean"
                    )
                result = node.predicate.recipe.resolve(values)
                assert isinstance(result, pd.Series)
                return bool(result.iloc[0])

            return tuple(row for row in rows(node.operand) if matches(row))
        reject(
            "a planned complete Population input",
            type(node).__name__,
            "Schedule every source/Query domain before set operations.",
            "Population.execute",
        )

    def unique(values: tuple[tuple[object, ...], ...]) -> tuple[tuple[object, ...], ...]:
        return tuple({_token(row): row for row in values}.values())

    return rows(definition)
