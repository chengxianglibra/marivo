"""Closed Population and Query definition variants referenced by Metric definitions.

Only the data structures needed to capture dependencies and detect cycles
live here; evaluation belongs to analysis. Records never hold public
Population objects, Sessions, Catalogs, Ibis expressions, or Artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from marivo._definition.types import (
    AxisBinding,
    CoordinateValue,
    ExactRef,
    Owner,
    check_literal,
    invalid,
    literal_token,
)
from marivo.refs import SemanticKind

if TYPE_CHECKING:
    from marivo._definition.expressions import MetricNode


def _coordinate_token(
    axis: AxisBinding, value: CoordinateValue, position: int, lengths: dict[int, int]
) -> tuple[str, str]:
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
    by: tuple[AxisBinding, ...]
    owner: Owner | None


QueryNode: TypeAlias = ObserveQuery


@dataclass(frozen=True, slots=True)
class QueryKeys:
    """Keys of a Query's output coordinates projected onto the given axes."""

    query: QueryNode
    axes: tuple[AxisBinding, ...]


PopulationNode: TypeAlias = SourceIdentities | QueryKeys


@dataclass(frozen=True, slots=True)
class PopulationProgram:
    """Complete lazy Population definition with its existing Session ownership."""

    root: PopulationNode
    owner: Owner | None


PopulationDependency: TypeAlias = LiteralPopulation | PopulationProgram
