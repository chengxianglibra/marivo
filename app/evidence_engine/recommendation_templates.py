from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.evidence_engine.schemas import INFERENCE_LEVEL_ORDER


def _render(template: str, context: dict[str, Any]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", "" if value is None else str(value))
    rendered = re.sub(r"\{\{[^{}]+\}\}", "", rendered)
    rendered = re.sub(r"\s{2,}", " ", rendered)
    return rendered.strip()


@dataclass(frozen=True)
class RecommendationTemplate:
    template_id: str
    entry_type: str
    claim_type: str
    min_inference_level: str = "L0"
    required_relation_types: tuple[str, ...] = ()
    action_template: str = ""
    expected_impact_template: str = ""
    risk_template: str = ""
    fixed_priority: str | None = None

    def matches(
        self,
        *,
        entry_type: str,
        claim_type: str,
        inference_level: str,
        relation_types: set[str],
    ) -> bool:
        if self.entry_type != entry_type:
            return False
        if self.claim_type != "*" and self.claim_type != claim_type:
            return False
        if INFERENCE_LEVEL_ORDER.index(inference_level) < INFERENCE_LEVEL_ORDER.index(
            self.min_inference_level
        ):
            return False
        return all(relation_type in relation_types for relation_type in self.required_relation_types)

    def render_action(self, context: dict[str, Any]) -> str:
        return _render(self.action_template, context)

    def render_expected_impact(self, context: dict[str, Any]) -> str:
        return _render(self.expected_impact_template, context)

    def render_risk(self, context: dict[str, Any]) -> str:
        return _render(self.risk_template, context)


TEMPLATES: tuple[RecommendationTemplate, ...] = (
    RecommendationTemplate(
        template_id="single_claim_action_v1",
        entry_type="single_claim",
        claim_type="root_cause_candidate",
        action_template=(
            "{{slice_desc}}: {{primary_claim_text}}. "
            "Investigate the driver behind {{primary_metric}} and validate the change before broad rollout."
        ),
        expected_impact_template=(
            "Confirms whether {{primary_metric}} recovers in {{slice_desc}} after the targeted fix."
        ),
        risk_template=(
            "A targeted intervention may not address other unobserved contributors in the same slice."
        ),
        fixed_priority="P1",
    ),
    RecommendationTemplate(
        template_id="multi_claim_correlated_action_v1",
        entry_type="multi_claim",
        claim_type="root_cause_candidate",
        required_relation_types=("correlates_with",),
        action_template=(
            "{{slice_desc}}: correlated signals across {{metrics_csv}}. "
            "{{primary_claim_text}} Prioritize a shared-driver investigation for {{primary_metric}} and adjacent metrics."
        ),
        expected_impact_template=(
            "Validates recovery across correlated metrics in {{slice_desc}} instead of treating each signal independently."
        ),
        risk_template=(
            "If the shared-driver hypothesis is wrong, a grouped mitigation can hide metric-specific causes."
        ),
        fixed_priority="P1",
    ),
    RecommendationTemplate(
        template_id="no_action_v1",
        entry_type="no_action",
        claim_type="root_cause_candidate",
        action_template=(
            "{{slice_desc}}: {{primary_metric}} is within expected bounds or aligned with the desired direction. No intervention needed."
        ),
        expected_impact_template=(
            "Avoids unnecessary investigation while keeping the metric under normal monitoring."
        ),
        risk_template="none",
        fixed_priority="P3",
    ),
    RecommendationTemplate(
        template_id="generic_fallback_v1",
        entry_type="single_claim",
        claim_type="*",
        action_template="Investigate {{primary_claim_text}}.",
        expected_impact_template="Further analysis recommended.",
        risk_template="Unknown risk profile.",
        fixed_priority="P2",
    ),
)


class RecommendationTemplateRegistry:
    def __init__(self, templates: tuple[RecommendationTemplate, ...] = TEMPLATES) -> None:
        self._templates = templates

    def resolve(
        self,
        *,
        entry_type: str,
        claim_type: str,
        inference_level: str,
        relation_types: set[str],
    ) -> RecommendationTemplate:
        fallback: RecommendationTemplate | None = None
        for template in self._templates:
            if template.matches(
                entry_type=entry_type,
                claim_type=claim_type,
                inference_level=inference_level,
                relation_types=relation_types,
            ):
                if template.claim_type == "*":
                    fallback = template
                    continue
                return template
        if fallback is not None:
            return fallback
        raise KeyError(
            "No recommendation template matched "
            f"entry_type={entry_type} claim_type={claim_type} inference_level={inference_level}"
        )
