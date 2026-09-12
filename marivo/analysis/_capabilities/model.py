"""Retained catalog read facts and shared native analysis Help budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class ReadCapability:
    """A bounded semantic catalog read used to construct analysis inputs."""

    id: str
    public_entrypoint: str
    help_target: str
    summary: str
    constraint_ids: tuple[str, ...] = ()
    callable_path: str | None = None
    kind: Literal["read"] = "read"
    receiver_family: str = ""
    result_kind: Literal["terminal_text", "immutable_metadata", "defensive_copy"] = (
        "immutable_metadata"
    )
    read_bound: Literal["bounded", "terminal"] = "bounded"
    produced_input_family: Literal["TimeScopeInput"] | None = None
    output_type: str = ""
    example: str = ""

    @property
    def canonical_id(self) -> str:
        return self.id


AnalysisHelpRenderClass = Literal[
    "root",
    "decision_hub",
    "navigation",
    "exact_callable",
    "public_type",
    "current_briefing",
]


@dataclass(frozen=True)
class AnalysisHelpRenderBudget:
    """Closed structural budget for one static analysis Help page class."""

    max_lines: int
    max_codepoints: int
    max_outgoing_routes: int
    max_examples_or_snippets: int


ANALYSIS_HELP_RENDER_BUDGETS: Mapping[
    AnalysisHelpRenderClass,
    AnalysisHelpRenderBudget,
] = MappingProxyType(
    {
        "root": AnalysisHelpRenderBudget(32, 3_000, 8, 0),
        "decision_hub": AnalysisHelpRenderBudget(44, 4_500, 10, 0),
        "navigation": AnalysisHelpRenderBudget(64, 6_500, 16, 0),
        "exact_callable": AnalysisHelpRenderBudget(104, 9_000, 10, 1),
        "public_type": AnalysisHelpRenderBudget(72, 7_000, 10, 0),
        "current_briefing": AnalysisHelpRenderBudget(72, 7_000, 6, 1),
    }
)
