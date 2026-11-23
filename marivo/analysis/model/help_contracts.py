"""Retained catalog read facts and shared native analysis Help budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

AnalysisHelpRenderClass = Literal[
    "root",
    "decision_hub",
    "navigation",
    "population_workflow",
    "service_reference",
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
        "root": AnalysisHelpRenderBudget(32, 3_000, 8, 1),
        "decision_hub": AnalysisHelpRenderBudget(44, 4_500, 10, 0),
        "navigation": AnalysisHelpRenderBudget(64, 6_500, 18, 0),
        "population_workflow": AnalysisHelpRenderBudget(64, 6_500, 18, 1),
        "service_reference": AnalysisHelpRenderBudget(64, 6_500, 8, 1),
        "exact_callable": AnalysisHelpRenderBudget(104, 9_000, 10, 1),
        "public_type": AnalysisHelpRenderBudget(72, 7_000, 12, 0),
        "current_briefing": AnalysisHelpRenderBudget(72, 7_000, 6, 1),
    }
)
