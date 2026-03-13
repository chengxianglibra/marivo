from __future__ import annotations

from typing import Any

from app.analysis_core.ir import AnalysisStepIR
from app.execution.costing import CostModel
from app.execution.errors import ExecutionFailure
from app.runtime_contracts import (
    CostEstimate,
    ExecutionFeedback,
    ReplanDecision,
    ReplanTrigger,
)
from app.storage.analytics import AnalyticsEngine

if False:  # pragma: no cover
    from app.routing import QueryRouter


ANALYSIS_STEPS = {
    "compare_watch_time",
    "analyze_qoe",
    "analyze_ads",
    "analyze_recommendation",
}

OPTIONAL_STEPS = {
    "analyze_ads",
    "analyze_recommendation",
}


class ReplanningService:
    """Deterministic Phase 2 replanning rules.

    The goal is not a full planner loop yet. This service only emits structured
    replan decisions that higher-level runtimes can apply while keeping the
    legacy workflow contract intact.
    """

    def __init__(
        self,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
        cost_model: CostModel | None = None,
    ) -> None:
        self.cost_model = cost_model or CostModel(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )

    def estimate_step(
        self,
        step: AnalysisStepIR,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
    ) -> CostEstimate:
        return self.cost_model.estimate_step(
            step,
            analytics_engine=analytics_engine,
            query_router=query_router,
        )

    def build_feedback(
        self,
        step: AnalysisStepIR,
        result: dict[str, Any],
        duration_ms: float,
        estimate: CostEstimate | None = None,
    ) -> ExecutionFeedback:
        observation_count = len(result.get("observations", []))
        claim_count = len(result.get("claims", []))
        recommendation_count = len(result.get("recommendations", []))
        summary_present = bool(result.get("summary"))

        if not summary_present:
            return ExecutionFeedback(
                code="missing_summary",
                category="result_shape",
                message=f"Step '{step.step_type}' completed without a summary payload.",
                replan_candidate=True,
                detail={
                    "duration_ms": round(duration_ms, 3),
                    "step_type": step.step_type,
                },
            )

        if (
            step.step_type in ANALYSIS_STEPS
            and observation_count == 0
            and claim_count == 0
            and recommendation_count == 0
        ):
            return ExecutionFeedback(
                code="insufficient_evidence",
                category="evidence",
                message=f"Step '{step.step_type}' produced no observations or claims.",
                replan_candidate=True,
                detail={
                    "duration_ms": round(duration_ms, 3),
                    "step_type": step.step_type,
                    "cost_confidence": estimate.confidence if estimate else "unknown",
                    "engine_locality": estimate.engine_locality if estimate else "unknown",
                },
            )

        conflicting_claims = [
            claim
            for claim in result.get("claims", [])
            if claim.get("contradicting_observations")
        ]
        if conflicting_claims:
            return ExecutionFeedback(
                code="conflicting_evidence",
                category="evidence",
                message=f"Step '{step.step_type}' produced claims with contradictory support.",
                replan_candidate=True,
                detail={
                    "duration_ms": round(duration_ms, 3),
                    "step_type": step.step_type,
                    "conflicting_claim_count": len(conflicting_claims),
                },
            )

        return ExecutionFeedback(
            code="step_completed",
            category="execution",
            message=f"Step '{step.step_type}' completed without replanning triggers.",
            detail={
                "duration_ms": round(duration_ms, 3),
                "step_type": step.step_type,
                "cost_confidence": estimate.confidence if estimate else "unknown",
                "engine_locality": estimate.engine_locality if estimate else "unknown",
                "observation_count": observation_count,
                "claim_count": claim_count,
                "recommendation_count": recommendation_count,
            },
        )

    def decide_before_step(
        self,
        step: AnalysisStepIR,
        estimate: CostEstimate,
    ) -> ReplanDecision:
        replacement_step = self._profile_step_for(step)

        if (
            step.step_type == "sample_rows"
            and replacement_step is not None
            and (
                estimate.confidence == "low"
                or estimate.engine_locality == "default_engine_fallback"
            )
        ):
            trigger = ReplanTrigger(
                code="budget_or_routing_risk",
                source="cost_model",
                message=(
                    f"Step '{step.step_type}' is high risk under current routing/cost hints; "
                    "replace with profile_table."
                ),
                detail=estimate.to_dict(),
            )
            return ReplanDecision(
                action="replace_step",
                reason="Replace high-risk sampling with a cheaper profile step.",
                triggers=[trigger],
                detail={"replacement_step": replacement_step},
            )

        if (
            step.is_optional()
            and estimate.confidence == "low"
            and estimate.engine_locality == "default_engine_fallback"
        ):
            trigger = ReplanTrigger(
                code="engine_unavailable",
                source="routing",
                message=(
                    f"Optional step '{step.step_type}' only has low-confidence fallback routing."
                ),
                detail=estimate.to_dict(),
            )
            return ReplanDecision(
                action="skip_step",
                reason="Skip optional high-risk step under low-confidence fallback routing.",
                triggers=[trigger],
                detail={"skipped_step_type": step.step_type},
            )

        return ReplanDecision(action="continue", reason="No pre-execution replanning needed.")

    def decide_after_step(
        self,
        step: AnalysisStepIR,
        result: dict[str, Any],
        estimate: CostEstimate,
        feedback: ExecutionFeedback,
    ) -> ReplanDecision:
        supplementary_step = self._profile_step_for(step)

        if (
            feedback.code in {"insufficient_evidence", "conflicting_evidence"}
            and supplementary_step is not None
            and step.step_type in ANALYSIS_STEPS
        ):
            trigger = ReplanTrigger(
                code=feedback.code,
                source=feedback.category,
                message=feedback.message,
                detail=feedback.to_dict(),
            )
            return ReplanDecision(
                action="insert_steps",
                reason="Insert a lower-level profiling step to gather more evidence.",
                triggers=[trigger],
                detail={
                    "insert_steps": [supplementary_step],
                    "cost_estimate": estimate.to_dict(),
                    "feedback": feedback.to_dict(),
                },
            )

        if feedback.code == "missing_summary":
            trigger = ReplanTrigger(
                code="result_shape_incomplete",
                source=feedback.category,
                message=feedback.message,
                detail=feedback.to_dict(),
            )
            return ReplanDecision(
                action="skip_step",
                reason="Skip malformed step output and continue with the remaining workflow.",
                triggers=[trigger],
                detail={"skipped_step_type": step.step_type},
            )

        return ReplanDecision(action="continue", reason="No post-execution replanning needed.")

    def decide_on_error(
        self,
        step: AnalysisStepIR,
        error: Exception,
        estimate: CostEstimate | None = None,
    ) -> ReplanDecision:
        if isinstance(error, ExecutionFailure):
            trigger = ReplanTrigger(
                code=error.code,
                source=error.category,
                message=error.message,
                detail=error.to_feedback().to_dict(),
            )
            replacement_step = self._profile_step_for(step)
            if replacement_step is not None and error.code in {
                "compile_failure",
                "translation_error",
                "capability_mismatch",
            }:
                return ReplanDecision(
                    action="replace_step",
                    reason="Execution failure can be degraded to a simpler profile step.",
                    triggers=[trigger],
                    detail={"replacement_step": replacement_step},
                )
            if step.is_optional() or error.replan_candidate:
                return ReplanDecision(
                    action="skip_step",
                    reason="Structured execution feedback marked the step as replannable.",
                    triggers=[trigger],
                    detail={
                        "skipped_step_type": step.step_type,
                        "fallback_candidates": list(error.fallback_candidates),
                    },
                )
            return ReplanDecision(
                action="abort",
                reason="Structured execution failure had no safe local fallback.",
                triggers=[trigger],
            )

        message = str(error)
        normalized = message.lower()
        replacement_step = self._profile_step_for(step)

        if replacement_step is not None and (
            "unsupported step type" in normalized
            or "compile" in normalized
            or "parser" in normalized
        ):
            trigger = ReplanTrigger(
                code="compile_failure",
                source="execution",
                message=message,
                detail={"step_type": step.step_type, "cost_estimate": estimate.to_dict() if estimate else None},
            )
            return ReplanDecision(
                action="replace_step",
                reason="Compile failure encountered; retry with a simpler profile step.",
                triggers=[trigger],
                detail={"replacement_step": replacement_step},
            )

        if step.is_optional():
            trigger = ReplanTrigger(
                code="step_execution_failed",
                source="execution",
                message=message,
                detail={"step_type": step.step_type, "cost_estimate": estimate.to_dict() if estimate else None},
            )
            return ReplanDecision(
                action="skip_step",
                reason="Optional step failed; skip and continue the workflow.",
                triggers=[trigger],
                detail={"skipped_step_type": step.step_type},
            )

        trigger = ReplanTrigger(
            code="workflow_abort",
            source="execution",
            message=message,
            detail={"step_type": step.step_type, "cost_estimate": estimate.to_dict() if estimate else None},
        )
        return ReplanDecision(
            action="abort",
            reason="No safe fallback was available for the failed step.",
            triggers=[trigger],
        )

    @staticmethod
    def _profile_step_for(step: AnalysisStepIR) -> dict[str, Any] | None:
        if step.step_type in {"profile_table", "synthesize_findings"}:
            return None
        table_name = step.table_name()
        if not table_name:
            return None
        return {
            "step_type": "profile_table",
            "params": {"table_name": str(table_name)},
        }
