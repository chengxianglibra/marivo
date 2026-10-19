"""Strong invocation inputs kept outside inert Population definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ibis.common.deferred import (
    Attr,
    BinaryOperator,
    Call,
    Deferred,
    Just,
    Resolver,
    Sequence,
    UnaryOperator,
)

from marivo._definition.deferred import resolver_of
from marivo._definition.relations import PopulationParameter, PopulationPredicateParameter
from marivo._definition.types import Owner
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame


@dataclass(frozen=True, slots=True)
class FrameKeyInput:
    """The exact selected Frame, retained until the consuming source is evaluated."""

    input_id: str
    frame: AnalysisFrame

    def __post_init__(self) -> None:
        from marivo.analysis.dsl.frame import AnalysisFrame

        if not isinstance(self.frame, AnalysisFrame) or self.input_id != self.frame._input_id:
            reject(
                "a Frame key input with its exact input identity",
                repr(self.input_id),
                "Retain the selected Frame with its own input ID.",
                "frame.population",
            )


def _compatible(left: AnalysisFrame, right: AnalysisFrame) -> bool:
    from marivo.analysis.core.frame_data import StoredFrameBacking

    if left._local.owner != right._local.owner or left._schema != right._schema:
        return False
    if left is right:
        return True
    if isinstance(left._backing, StoredFrameBacking) and isinstance(
        right._backing, StoredFrameBacking
    ):
        # Stored binding IDs are stable across metadata-only opens. Their full
        # committed descriptors, not Python handle identity, authorize sharing.
        return left._backing == right._backing
    return False


def merge_frame_key_inputs(
    *groups: tuple[FrameKeyInput, ...], owner: Owner | None = None
) -> tuple[FrameKeyInput, ...]:
    """Retain compatible exact inputs once, without reading stored payloads."""
    result: dict[str, FrameKeyInput] = {}
    expected = owner
    for group in groups:
        for item in group:
            if expected is None:
                expected = item.frame._local.owner
            if item.frame._local.owner != expected:
                reject(
                    "Frame key inputs owned by one Store and Session",
                    item.input_id,
                    "Build the Population and its consumer in the Frame's owning Session.",
                    "frame.population",
                )
            previous = result.get(item.input_id)
            if previous is not None and not _compatible(previous.frame, item.frame):
                reject(
                    "one compatible exact descriptor per Frame input identity",
                    item.input_id,
                    "Reopen the same committed Artifact descriptor or use distinct input bindings.",
                    "frame.population",
                )
            result.setdefault(item.input_id, item)
    return tuple(result.values())


def _recipe_inputs(recipe: Resolver) -> tuple[FrameKeyInput, ...]:
    from marivo.analysis.dsl.population import Population, PopulationPredicate

    if isinstance(recipe, Just):
        value = recipe.value
        return value._frame_inputs if isinstance(value, (Population, PopulationPredicate)) else ()
    if isinstance(recipe, BinaryOperator):
        return (*_recipe_inputs(recipe.left), *_recipe_inputs(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return _recipe_inputs(recipe.arg)
    if isinstance(recipe, Sequence):
        return tuple(item for value in recipe.values for item in _recipe_inputs(value))
    if isinstance(recipe, Attr):
        return _recipe_inputs(recipe.obj)
    if isinstance(recipe, Call):
        return tuple(
            item
            for value in (recipe.func, *recipe.args, *recipe.kwargs.values())
            for item in _recipe_inputs(value)
        )
    return ()


def collect_frame_key_inputs(
    *values: Deferred | PopulationParameter | PopulationPredicateParameter | None,
    owner: Owner | None = None,
) -> tuple[FrameKeyInput, ...]:
    """Extract original special literals before semantic consumption drops wrappers."""
    from marivo.analysis.dsl.population import Population, PopulationPredicate

    groups: list[tuple[FrameKeyInput, ...]] = []
    for value in values:
        if isinstance(value, (Population, PopulationPredicate)):
            groups.append(value._frame_inputs)
        elif isinstance(value, Deferred):
            groups.append(_recipe_inputs(resolver_of(value)))
    return merge_frame_key_inputs(*groups, owner=owner)
