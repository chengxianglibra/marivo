"""Exact relationship and path facts independent of a catalog or recipe."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from itertools import pairwise
from typing import Literal

from marivo._kernel.errors import invalid
from marivo._kernel.input_context import InputContext
from marivo._kernel.refs import ExactRef
from marivo._kernel.time_types import InstantAnchor


@dataclass(frozen=True, slots=True)
class RelationshipParameter:
    """Captured relationship and endpoint facts used in path admission."""

    ref: ExactRef
    from_entity: ExactRef
    to_entity: ExactRef
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]
    _from_default_time: ExactRef | None = dataclass_field(default=None, compare=False, repr=False)
    from_versioned: bool = False
    to_versioned: bool = False
    target_keys: tuple[str, ...] = ()
    target_keys_covered: bool = True


@dataclass(frozen=True, slots=True)
class RelationshipStepParameter:
    relationship: RelationshipParameter
    target_at: InstantAnchor | None = None

    def __post_init__(self) -> None:
        check_path_anchor(
            self.target_at, self.relationship.to_entity, self.relationship.to_versioned
        )


@dataclass(frozen=True, slots=True)
class PathParameter:
    steps: tuple[RelationshipStepParameter, ...]
    source_at: InstantAnchor | None = None
    _context: InputContext = dataclass_field(
        default_factory=InputContext, compare=False, repr=False, kw_only=True
    )

    def __post_init__(self) -> None:
        if not self.steps or any(
            left.relationship.to_entity.ref != right.relationship.from_entity.ref
            for left, right in pairwise(self.steps)
        ):
            invalid("Use mv.path with a nonempty sequence of connected directed Relationships.")
        check_path_anchor(self.source_at, self.source, self.steps[0].relationship.from_versioned)

    @property
    def source(self) -> ExactRef:
        return self.steps[0].relationship.from_entity

    @property
    def target(self) -> ExactRef:
        return self.steps[-1].relationship.to_entity

    @property
    def relationships(self) -> tuple[RelationshipParameter, ...]:
        return tuple(step.relationship for step in self.steps)

    @property
    def step_ats(self) -> tuple[InstantAnchor | None, ...]:
        """Intermediate node anchors, with trailing unanchored positions omitted."""
        anchors = tuple(step.target_at for step in self.steps[:-1])
        last = max((i for i, anchor in enumerate(anchors) if anchor is not None), default=-1)
        return anchors[: last + 1]


def check_path_anchor(value: InstantAnchor | None, entity: ExactRef, versioned: bool) -> None:
    if value is None:
        return
    if not isinstance(value, InstantAnchor) or value.utcoffset() is None:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
        invalid(f"Use session.instant(...) to anchor {entity.ref.key} in mv.path.")
    if not versioned:
        invalid(f"Remove the version anchor for unversioned node {entity.ref.key}.")


class _RelationshipValue:
    """Controlled nominal bridge for captured directed relationship parameters."""

    __slots__ = ()

    @property
    def _parameter(self) -> RelationshipParameter:
        raise NotImplementedError("Use a captured Relationship value.")

    @property
    def _context(self) -> InputContext:
        return InputContext()


class _PathValue:
    """Controlled literal bridge for a closed Path in native recipes."""

    __slots__ = ()
    _parameter: PathParameter
