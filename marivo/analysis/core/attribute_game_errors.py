"""Structured binding and fixed-state replacement-game failures."""

from __future__ import annotations

from typing import Literal

from marivo.analysis.core.frozen_calendar import FrozenKey
from marivo.analysis.core.model import CoreRuleError


class AttributeGameError(CoreRuleError):
    """A whole-Metric game lacks a qualified route or a complete defined domain."""

    def __init__(
        self,
        *,
        reason: Literal[
            "unsupported_state",
            "formula_mismatch",
            "incomplete_domain",
            "invalid_value",
            "closure_failure",
        ],
        metric: str,
        received: str,
        parent: FrozenKey | None = None,
        occurrence: tuple[int, ...] = (),
    ) -> None:
        self.reason = reason
        self.metric = metric
        self.parent = parent
        self.occurrence = occurrence
        expected = {
            "unsupported_state": "a numeric Metric with a qualified fixed-state source route",
            "formula_mismatch": "matching endpoint formulas, original source roles, types, units and NULL policies",
            "incomplete_domain": "a finite defined whole Metric over every group replacement",
            "invalid_value": "valid demanded original Metric values in their owning physical carrier",
            "closure_failure": "sampled contributions closing within typed conversion and summation roundoff",
        }[reason]
        super().__init__(
            expected=expected,
            received=f"{metric}; parent={parent!r}; occurrence={occurrence}: {received}",
            repair="Inspect the named Metric and input support with ordinary observe/compare. Use a business-justified Metric definition or restrict the analysis scope so every required mixed state is defined; the bound rule is not changed after data validation.",
            location="frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
