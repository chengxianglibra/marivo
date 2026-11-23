"""Closed Population nodes and predicate records that can reference them."""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias

from ibis.common.deferred import Resolver

from marivo._definition.correspondence import ComparisonAxis
from marivo._definition.rows import RowExpression
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    CoordinateValue,
    EntityIdentityRead,
    FieldRead,
    IntervalCoordinate,
    LiteralValue,
    check_literal,
    literal_token,
)
from marivo._kernel.errors import invalid
from marivo._kernel.input_context import (
    InputContext,
    Owner,
)
from marivo._kernel.paths import PathParameter
from marivo._kernel.refs import ExactRef, SemanticKind
from marivo._kernel.time_types import InstantAnchor


@dataclass(frozen=True, slots=True)
class PredicateExpression:
    """Native boolean recipe over inert field subtrees and membership leaves."""

    recipe: Resolver
    inputs: tuple[RowExpression | PopulationContains, ...]


@dataclass(frozen=True, slots=True)
class MembershipBinding:
    """One correlated member tuple and explicit contribution allocation paths."""

    fields: tuple[FieldRead, ...]
    paths: tuple[PathParameter, ...]
    unmatched: Literal["exclude", "null"]


@dataclass(frozen=True, slots=True)
class SampleDomain:
    """Observed internal coordinates with their membership allocation groups."""

    axes: tuple[ComparisonAxis, ...]
    memberships: tuple[MembershipBinding, ...] = ()
    population: PopulationDependency | None = None


def _coordinate_token(
    axis: ComparisonAxis,
    value: CoordinateValue,
    position: int,
    lengths: dict[int, int],
) -> tuple[str, str]:
    if isinstance(axis, IntervalCoordinate):
        if type(value) is not int or not 0 <= value < len(axis.bounds):
            invalid("interval coordinates must come from their frozen TimeScope")
        return ("interval", repr(tuple(point.isoformat() for point in axis.bounds[value])))
    if isinstance(axis, CalendarCoordinate):
        if not isinstance(value, datetime) or value.tzinfo is None:
            invalid("calendar coordinates must come from their frozen CalendarAxis")
        index = bisect_left(axis.cells, value, key=lambda cell: cell[0])
        if index == len(axis.cells) or axis.cells[index][0] != value:
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
    interval_keys: tuple[tuple[int, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class PopulationProduct:
    factors: tuple[PopulationDependency, ...]


@dataclass(frozen=True, slots=True)
class PopulationClassify:
    operand: PopulationDependency
    subject: int
    reads: tuple[FieldRead | EntityIdentityRead, ...]


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
    path: PathParameter
    subject: int


@dataclass(frozen=True, slots=True)
class PopulationPreimage:
    operand: PopulationDependency
    targets: PopulationDependency
    path: PathParameter
    subject: int


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
