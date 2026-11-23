"""Structured failure for unavailable contribution inputs."""

from typing import Literal

from marivo.analysis.model.attribute_failures import AttributeFailureContext
from marivo.analysis.model.errors import CoreRuleError

AttributeInputReason = Literal[
    "missing_component_proof",
    "unqualified_contribution_route",
    "undefined_input",
    "overlapping_groups",
    "root_denominator_zero",
    "closure_mismatch",
    "invalid_rate_support",
    "component_mismatch",
]


class AttributeInputError(CoreRuleError):
    """An explicit contribution input lacks a captured or runtime premise."""

    def __init__(
        self,
        *,
        reason: AttributeInputReason,
        received: str,
        context: AttributeFailureContext | None = None,
    ) -> None:
        self.reason = reason
        self.attribute_context = context
        repairs = {
            "invalid_rate_support": "Provide positive parent denominators, nonnegative group denominators and effective weights, and zero numerator mass wherever group support is zero; inspect a finer observe/compare to repair the Metric.",
            "component_mismatch": "Inspect the original parent and fine Metric and its numerator, denominator and NULL masks; correct inconsistent definitions or changed source facts before rerunning attribution.",
            "closure_mismatch": "Inspect the independent parent total and fine contributions, then explicitly rerun the request if the source facts changed.",
            "unqualified_contribution_route": "Use qualified native physical tables with the captured complete parent parameter dependencies.",
            "missing_component_proof": "Retain the exact component, complete parent mapping and qualified sample/window proof before requesting contribution.",
            "undefined_input": "Cover every scoped root identity with a complete joint classification; restrict the endpoint with where or members when needed.",
            "overlapping_groups": "Provide one joint classification tuple per counted identity inside each selected parent, or submit a new explicit finer observation.",
            "root_denominator_zero": "Provide nonzero complete-parent denominators for both endpoints, or build a finer observation to inspect the original nullable Metric.",
        }
        super().__init__(
            expected=(
                "closure_mismatch: independent parent and fine final target values within the numeric closure policy"
                if reason == "closure_mismatch"
                else f"{reason}: a qualified complete Attribute input"
            ),
            received=received,
            repair=repairs[reason],
            location="frame.attribute",
            canonical_id="analysis_frame.attribute",
        )
