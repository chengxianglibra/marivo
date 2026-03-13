from __future__ import annotations

from dataclasses import asdict
from math import inf
from typing import TYPE_CHECKING, Any

from app.analysis_core.ir import AnalysisStepIR
from app.runtime_contracts import BudgetCheckResult, CostEstimate
from app.storage.analytics import AnalyticsEngine

if TYPE_CHECKING:
    from app.routing import QueryRouter


STEP_BYTES_PER_ROW = {
    "sample_rows": 256,
    "profile_table": 160,
    "compare_metric": 96,
    "compare_watch_time": 96,
    "compare_watch_time_top_slices": 96,
    "compare_watch_time_overall": 96,
    "analyze_qoe": 96,
    "analyze_ads": 96,
    "analyze_recommendation": 96,
    "synthesize_findings": 0,
}


class CostModel:
    """Low-friction v1 cost model based on row-count proxies and runtime hints."""

    def __init__(
        self,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
    ) -> None:
        self.analytics = analytics_engine
        self.query_router = query_router

    def estimate_step(
        self,
        step: AnalysisStepIR,
        analytics_engine: AnalyticsEngine | None = None,
        query_router: QueryRouter | None = None,
    ) -> CostEstimate:
        engine = analytics_engine or self.analytics
        router = query_router or self.query_router
        table_name = self._table_name_for_step(step)
        subject = f"step:{step.index}"

        if table_name is None:
            if step.step_type == "synthesize_findings":
                return CostEstimate(
                    subject=subject,
                    estimated_rows=0,
                    estimated_bytes=0,
                    confidence="high",
                    engine_locality="artifact_only",
                    join_fanout_risk="low",
                    cache_signals=["artifact_reuse", "no_scan"],
                    detail={"step_type": step.step_type},
                )
            return CostEstimate(
                subject=subject,
                confidence="unknown",
                join_fanout_risk=self._join_fanout_risk(step),
                cache_signals=["shape_unknown"],
                suggested_fallbacks=["add_table_name_context"],
                detail={"step_type": step.step_type},
            )

        route_detail = self._resolve_route(table_name, router)
        bytes_per_row = STEP_BYTES_PER_ROW.get(step.step_type, 128)

        if engine is None:
            return CostEstimate(
                subject=subject,
                confidence="unknown",
                engine_id=route_detail.get("engine_id"),
                engine_locality=route_detail["engine_locality"],
                join_fanout_risk=self._join_fanout_risk(step),
                cache_signals=self._cache_signals(step, table_name, rows_known=False),
                suggested_fallbacks=self._fallbacks(step, rows_known=False),
                detail={"step_type": step.step_type, "table_name": table_name, **route_detail},
            )

        try:
            row_count = engine.table_row_count(table_name)
        except Exception as error:
            return CostEstimate(
                subject=subject,
                confidence="low",
                engine_id=route_detail.get("engine_id"),
                engine_locality=route_detail["engine_locality"],
                join_fanout_risk=self._join_fanout_risk(step),
                cache_signals=self._cache_signals(step, table_name, rows_known=False),
                suggested_fallbacks=self._fallbacks(step, rows_known=False),
                detail={
                    "step_type": step.step_type,
                    "table_name": table_name,
                    "error": str(error),
                    **route_detail,
                },
            )

        estimated_bytes = row_count * bytes_per_row
        confidence = "high" if route_detail["engine_locality"] == "bound_engine" else "medium"
        return CostEstimate(
            subject=subject,
            estimated_rows=row_count,
            estimated_bytes=estimated_bytes,
            confidence=confidence,
            engine_id=route_detail.get("engine_id"),
            engine_locality=route_detail["engine_locality"],
            join_fanout_risk=self._join_fanout_risk(step),
            cache_signals=self._cache_signals(step, table_name, rows_known=True),
            suggested_fallbacks=self._fallbacks(step, rows_known=True),
            detail={
                "step_type": step.step_type,
                "table_name": table_name,
                **route_detail,
            },
        )

    def check_budget(
        self,
        plan_id: str,
        budget_max_rows: float | int,
        cost_estimates: list[CostEstimate],
    ) -> BudgetCheckResult:
        total_rows = sum(estimate.estimated_rows or 0 for estimate in cost_estimates)
        total_bytes = sum(estimate.estimated_bytes or 0 for estimate in cost_estimates)
        unknown_subjects = [
            estimate.subject for estimate in cost_estimates if estimate.estimated_rows is None
        ]
        suggested_fallbacks: list[str] = []
        for estimate in cost_estimates:
            for fallback in estimate.suggested_fallbacks:
                if fallback not in suggested_fallbacks:
                    suggested_fallbacks.append(fallback)

        within_budget = total_rows <= budget_max_rows
        if not within_budget:
            risk_level = "high"
        elif unknown_subjects:
            risk_level = "medium"
        else:
            risk_level = "low"

        confidence = "low" if unknown_subjects else "high"
        if budget_max_rows == inf:
            confidence = "medium" if unknown_subjects else "high"

        return BudgetCheckResult(
            plan_id=plan_id,
            total_estimated_rows=total_rows,
            total_estimated_bytes=total_bytes,
            budget_max_rows=budget_max_rows,
            within_budget=within_budget,
            confidence=confidence,
            risk_level=risk_level,
            unknown_subjects=unknown_subjects,
            suggested_fallbacks=suggested_fallbacks,
            cost_estimates=cost_estimates,
        )

    def build_actual_feedback(
        self,
        step: AnalysisStepIR,
        result: dict[str, Any],
        duration_ms: float,
        estimate: CostEstimate | None = None,
    ) -> dict[str, Any]:
        return {
            "step_type": step.step_type,
            "duration_ms": round(duration_ms, 3),
            "observation_count": len(result.get("observations", [])),
            "claim_count": len(result.get("claims", [])),
            "recommendation_count": len(result.get("recommendations", [])),
            "summary_present": bool(result.get("summary")),
            "estimate_confidence": estimate.confidence if estimate is not None else "unknown",
        }

    @staticmethod
    def serialize_estimate(estimate: CostEstimate | None) -> dict[str, Any] | None:
        if estimate is None:
            return None
        return asdict(estimate)

    @staticmethod
    def _table_name_for_step(step: AnalysisStepIR) -> str | None:
        return step.table_name()

    @staticmethod
    def _routing_table_name(table_name: str) -> str:
        return table_name.split(".")[-1]

    def _resolve_route(
        self,
        table_name: str,
        router: QueryRouter | None,
    ) -> dict[str, Any]:
        if router is None:
            return {
                "engine_locality": "default_analytics",
                "routing_strategy": "no_router",
            }

        native_table_name = self._routing_table_name(table_name)
        try:
            route = router.resolve_tables([native_table_name])
            return {
                "engine_locality": "bound_engine",
                "routing_strategy": "bound_route",
                "engine_id": route.engine_id,
                "qualified_name": route.qualified_names.get(native_table_name, table_name),
            }
        except KeyError as error:
            return {
                "engine_locality": "default_engine_fallback",
                "routing_strategy": "fallback_missing_table",
                "routing_error": str(error),
            }
        except ValueError as error:
            return {
                "engine_locality": "default_engine_fallback",
                "routing_strategy": "fallback_no_common_engine",
                "routing_error": str(error),
            }

    @staticmethod
    def _join_fanout_risk(step: AnalysisStepIR) -> str:
        if len(step.dependencies) > 1:
            return "high"
        if len(step.dependencies) == 1:
            return "medium"
        return "low"

    @staticmethod
    def _cache_signals(
        step: AnalysisStepIR,
        table_name: str,
        rows_known: bool,
    ) -> list[str]:
        signals = ["table_scan", f"table:{table_name}"]
        if rows_known:
            signals.append("row_count_resolved")
        else:
            signals.append("row_count_unknown")
        if step.step_type == "sample_rows":
            signals.append("limit_pushdown_candidate")
        if step.step_type == "compare_metric":
            signals.append("semantic_metric_projection")
        primary_metric = step.primary_metric_name()
        if primary_metric:
            signals.append(f"metric:{primary_metric}")
        if step.step_category == "composite":
            signals.append("composite_step")
        return signals

    @staticmethod
    def _fallbacks(step: AnalysisStepIR, rows_known: bool) -> list[str]:
        fallbacks: list[str] = []
        if not rows_known:
            fallbacks.append("prefer_lower_granularity_step")
        if step.step_type == "sample_rows":
            fallbacks.append("reduce_sample_limit")
        elif step.step_type == "profile_table":
            fallbacks.append("limit_profile_scope")
        else:
            fallbacks.append("prefer_aggregate_path")
        return fallbacks
