"""Captured private source preparation request for Frame attribution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.binding.attribute_game import AttributeGame
    from marivo.analysis.binding.attribute_rules import AttributeRule
    from marivo.analysis.core.frame_data import FrameNode
    from marivo.analysis.core.frame_operations import AttributeFrameValues


@dataclass(frozen=True, slots=True)
class AttributeInputRequest:
    """Private explicit preparation request; this is not an Attribute result."""

    parent: FrameNode
    endpoints: tuple[FrameNode, FrameNode]
    operation: AttributeFrameValues
    rules: tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]]
    source_rules: (
        tuple[tuple[tuple[str, AttributeRule], ...], tuple[tuple[str, AttributeRule], ...]] | None
    ) = None
    source_indices: tuple[int, int] | None = None
    source_sides: tuple[int, int] | None = None

    games: tuple[AttributeGame, AttributeGame] | None = None
