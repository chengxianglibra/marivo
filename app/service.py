from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.analysis_core import CompositeWorkflowRuntime, build_service_step_registry
from app.analysis_core.compiler import build_comparison_query as compile_comparison_query
from app.analysis_core.compiler import compile_step
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR, from_legacy_step
from app.evidence import synthesize_claims
from app.evidence_engine import EvidencePipeline
from app.execution.feedback import compile_failure_from_error
from app.execution.routing_runtime import RoutingRuntime
from app.planner import ReplanningService
from app.runtime_contracts import DEFAULT_STEP_TABLES
from app.semantic_runtime import PlannerContextProvider, SemanticResolver
from app.session import SessionManager
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore

if TYPE_CHECKING:
    from app.approvals import ApprovalService
    from app.governance import GovernanceService
    from app.observability import MetricsCollector
    from app.routing import QueryRouter


class SemanticLayerService:
    def __init__(
        self,
        metadata_store: MetadataStore,
        analytics_engine: AnalyticsEngine,
        query_router: QueryRouter | None = None,
        governance: GovernanceService | None = None,
        metrics: MetricsCollector | None = None,
        approvals: ApprovalService | None = None,
        replanner: ReplanningService | None = None,
    ) -> None:
        self.metadata = metadata_store
        self.analytics = analytics_engine
        self.query_router = query_router
        self.governance = governance
        self.metrics = metrics
        self.approvals = approvals
        self.session_manager = SessionManager(metadata_store)
        self.step_registry = build_service_step_registry(self)
        self.evidence_pipeline = EvidencePipeline(synthesize_claims)
        self.semantic_resolver = SemanticResolver(metadata_store)
        self.planner_context_provider = PlannerContextProvider(metadata_store)
        self.workflow_runtime = CompositeWorkflowRuntime()
        self.replanner = replanner or ReplanningService(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )
        self._governance_context: dict[str, Any] | None = None
        self._routing_feedback_context: dict[str, Any] | None = None
        self.routing_runtime = RoutingRuntime(query_router, analytics_engine)

    def create_session(
        self,
        goal: str,
        constraints: dict[str, Any],
        budget: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        return self.session_manager.create_session(goal, constraints, budget, policy)

    def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.session_manager.list_sessions(status=status)

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.session_manager.get_session(session_id)

    def discover_catalog(self) -> dict[str, Any]:
        tables = ["analytics.watch_events", "analytics.player_qoe", "analytics.ad_events", "analytics.recommendation_events"]
        table_counts = {t: self.analytics.table_row_count(t) for t in tables}
        return {
            "engine": "duckdb",
            "entities": [
                {"id": "user", "keys": ["user_id"]},
                {"id": "session", "keys": ["session_id"]},
                {"id": "video_session", "keys": ["session_id", "content_type"]},
            ],
            "metrics": [
                {
                    "id": "watch_time",
                    "label": "Watch time",
                    "definition": "avg(play_duration_seconds)",
                    "dimensions": ["platform", "app_version", "network_type", "content_type"],
                },
                {
                    "id": "first_frame_time",
                    "label": "First frame time",
                    "definition": "avg(first_frame_time_ms)",
                    "dimensions": ["platform", "app_version", "network_type", "content_type"],
                },
                {
                    "id": "preroll_timeout_rate",
                    "label": "Preroll timeout rate",
                    "definition": "avg(preroll_timeout)",
                    "dimensions": ["platform", "app_version", "network_type", "content_type"],
                },
                {
                    "id": "recommendation_ctr",
                    "label": "Recommendation CTR",
                    "definition": "sum(clicks) / sum(impressions)",
                    "dimensions": ["platform", "app_version", "network_type", "content_type"],
                },
            ],
            "assets": [
                {"id": "watch_events", "engine": "duckdb", "kind": "table", "row_count": table_counts["analytics.watch_events"]},
                {"id": "player_qoe", "engine": "duckdb", "kind": "table", "row_count": table_counts["analytics.player_qoe"]},
                {"id": "ad_events", "engine": "duckdb", "kind": "table", "row_count": table_counts["analytics.ad_events"]},
                {"id": "recommendation_events", "engine": "duckdb", "kind": "table", "row_count": table_counts["analytics.recommendation_events"]},
            ],
            "policies": [
                "Results are aggregate-only in the MVP.",
                "Evidence graph keeps support and contradiction links for every claim.",
            ],
        }

    def run_step(self, session_id: str, step_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        normalized = step_type.strip().lower()
        governance_result: dict[str, Any] | None = None

        # Governance check
        if self.governance:
            governance_params = dict(params or {})
            tables = self._governance_tables(normalized, governance_params)
            if len(tables) == 1 and not governance_params.get("table_name"):
                governance_params["table_name"] = tables[0]
            governance_result = self.governance.check_step(
                session_id,
                normalized,
                governance_params if governance_params else None,
                tables=tables or None,
            )
            if not governance_result["passed"]:
                raise ValueError(f"Governance check failed: {governance_result['violations']}")

        start = time.perf_counter()
        try:
            self._governance_context = governance_result
            self._routing_feedback_context = None
            result = self.step_registry.run(session_id, normalized, params)
        except KeyError as error:
            raise ValueError(f"Unsupported step type: {step_type}") from error
        finally:
            self._governance_context = None
            self._routing_feedback_context = None
        duration_ms = (time.perf_counter() - start) * 1000

        if self.metrics:
            self.metrics.record_step(normalized, duration_ms)

        if governance_result:
            result["governance"] = {
                "decisions": governance_result.get("decisions", []),
                "transforms": governance_result.get("transforms", {}),
                "hard_constraints": governance_result.get("hard_constraints", []),
                "soft_signals": governance_result.get("soft_signals", []),
            }

        return result

    def run_watch_time_drop_workflow(self, session_id: str) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        self._reset_session_outputs(session_id)
        workflow_plan = self.workflow_runtime.expand_workflow("watch_time_drop")
        results: list[dict[str, Any]] = []
        replan_decisions: list[dict[str, Any]] = []
        executed_step_types: list[str] = []
        plan_cursor = 0

        while plan_cursor < len(workflow_plan):
            step_ir = workflow_plan[plan_cursor]
            estimate = self.replanner.estimate_step(
                step_ir,
                analytics_engine=self.analytics,
                query_router=self.query_router,
            )
            applied_decisions: list[dict[str, Any]] = []

            before_decision = self.replanner.decide_before_step(step_ir, estimate)
            if before_decision.action == "replace_step":
                replacement_step = before_decision.detail.get("replacement_step")
                if isinstance(replacement_step, dict):
                    step_ir = self.workflow_runtime.materialize_runtime_step(
                        replacement_step,
                        index=step_ir.index,
                    )
                    workflow_plan[plan_cursor] = step_ir
                    estimate = self.replanner.estimate_step(
                        step_ir,
                        analytics_engine=self.analytics,
                        query_router=self.query_router,
                    )
                    applied_decisions.append(before_decision.to_dict())
                    replan_decisions.append(before_decision.to_dict())
            elif before_decision.action == "skip_step":
                replan_decisions.append(before_decision.to_dict())
                plan_cursor += 1
                continue

            started = time.perf_counter()
            try:
                result = self.run_step(
                    session_id,
                    step_ir.step_type,
                    params=step_ir.params if step_ir.params else None,
                )
            except Exception as error:
                failure_decision = self.replanner.decide_on_error(step_ir, error, estimate=estimate)
                replan_decisions.append(failure_decision.to_dict())
                if failure_decision.action == "replace_step":
                    replacement_step = failure_decision.detail.get("replacement_step")
                    if isinstance(replacement_step, dict):
                        workflow_plan[plan_cursor] = self.workflow_runtime.materialize_runtime_step(
                            replacement_step,
                            index=step_ir.index,
                        )
                        continue
                if failure_decision.action == "skip_step":
                    plan_cursor += 1
                    continue
                raise

            duration_ms = (time.perf_counter() - started) * 1000
            feedback = self.replanner.build_feedback(step_ir, result, duration_ms, estimate=estimate)
            after_decision = self.replanner.decide_after_step(step_ir, result, estimate, feedback)
            if after_decision.action == "insert_steps":
                insert_steps = after_decision.detail.get("insert_steps", [])
                if isinstance(insert_steps, list) and insert_steps:
                    workflow_plan[plan_cursor + 1:plan_cursor + 1] = self.workflow_runtime.materialize_runtime_steps(
                        insert_steps,
                        start_index=self.workflow_runtime.next_step_index(workflow_plan),
                    )
                    applied_decisions.append(after_decision.to_dict())
                    replan_decisions.append(after_decision.to_dict())
            elif after_decision.action == "skip_step":
                applied_decisions.append(after_decision.to_dict())
                replan_decisions.append(after_decision.to_dict())

            result["execution_feedback"] = feedback.to_dict()
            if applied_decisions:
                result["replanning"] = applied_decisions
                self._attach_replanning_provenance(session_id, step_ir.step_type, applied_decisions)

            results.append(result)
            executed_step_types.append(step_ir.step_type)
            plan_cursor += 1

        final_result = results[-1]

        # Auto-flag high-risk recommendations for approval
        if self.approvals:
            self.approvals.auto_flag_recommendations(session_id, risk_threshold="P0")

        return {
            "session_id": session_id,
            "workflow": "watch_time_drop",
            "steps": results,
            "final_summary": final_result["summary"],
            "claims": final_result.get("claims", []),
            "recommendations": final_result.get("recommendations", []),
            "replanning": {
                "decisions": replan_decisions,
                "executed_step_types": executed_step_types,
                "final_plan": [step.step_type for step in workflow_plan],
            },
        }

    def get_evidence_graph(self, session_id: str) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        observations = self._load_observations(session_id)
        steps = self.metadata.query_rows(
            """
            SELECT step_id, step_type, status, summary, provenance_json
            FROM steps
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        for step in steps:
            step["provenance"] = json.loads(step.pop("provenance_json"))
        claims = self.metadata.query_rows(
            """
            SELECT claim_id, claim_type, text, scope_json, confidence, status,
                   supporting_observation_ids_json, contradicting_observation_ids_json, confidence_breakdown_json
            FROM claims
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        edges = self.metadata.query_rows(
            """
            SELECT edge_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight, explanation
            FROM evidence_edges
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        recommendations = self.metadata.query_rows(
            """
            SELECT rec_id, claim_id, action_text, priority, expected_impact, risk, validation_metric_json
            FROM recommendations
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )

        for claim in claims:
            claim["scope"] = json.loads(claim.pop("scope_json"))
            claim["supporting_observations"] = json.loads(claim.pop("supporting_observation_ids_json"))
            claim["contradicting_observations"] = json.loads(claim.pop("contradicting_observation_ids_json"))
            claim["confidence_breakdown"] = json.loads(claim.pop("confidence_breakdown_json"))
        for recommendation in recommendations:
            recommendation["validation_metric"] = json.loads(recommendation.pop("validation_metric_json"))

        return {
            "session_id": session_id,
            "steps": steps,
            "observations": observations,
            "claims": claims,
            "edges": edges,
            "recommendations": recommendations,
        }

    # ── Metric resolution ────────────────────────────────────────────

    def resolve_metric_sql(self, metric_name: str) -> str | None:
        """Look up a published metric's definition_sql from semantic runtime."""
        resolved = self.semantic_resolver.resolve_metric(metric_name)
        return resolved.definition_sql if resolved else None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        """Look up a published metric's dimensions from semantic runtime."""
        resolved = self.semantic_resolver.resolve_metric(metric_name)
        return list(resolved.dimensions) if resolved else None

    def build_comparison_query(
        self,
        metric_name: str,
        table_name: str,
        metric_sql: str,
        dimensions: list[str],
        date_column: str = "event_date",
        order: str = "ASC",
        limit: int = 3,
    ) -> str:
        """Build a current-vs-baseline comparison SQL query from metric definition.

        Uses the metric's SQL expression and dimensions to generate a
        sliced comparison query with delta_pct calculation.
        """
        return compile_comparison_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=metric_sql,
            dimensions=dimensions,
            date_column=date_column,
            order=order,
            limit=limit,
        )

    # ── Engine resolution ─────────────────────────────────────────────

    def _resolve_engine(self, table_names: list[str]) -> tuple[AnalyticsEngine, str]:
        """Resolve the analytics engine and its type for the given tables.

        Uses QueryRouter when available, falls back to self.analytics.
        Returns ``(engine, engine_type)`` tuple.
        """
        resolution = self.routing_runtime.resolve_tables(table_names)
        self._routing_feedback_context = (
            resolution.feedback.to_dict() if resolution.feedback is not None else None
        )
        return resolution.engine, resolution.engine_type

    def _compile_step_with_feedback(
        self,
        step: AnalysisStepIR,
        *,
        engine_type: str,
        semantic_context: dict[str, Any] | None = None,
    ):
        try:
            return compile_step(
                step,
                engine_type=engine_type,
                semantic_context=semantic_context,
            )
        except ValueError as error:
            raise compile_failure_from_error(
                step,
                error,
                semantic_context=semantic_context,
            ) from error

    # ── Step runners ──────────────────────────────────────────────────

    def _run_compare_watch_time(self, session_id: str) -> dict[str, Any]:
        step_type = "compare_watch_time"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["watch_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        period_params = [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end]
        top_slices_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": "compare_watch_time_top_slices", "params": {"table_name": "analytics.watch_events", "limit": 3}},
            ),
            engine_type=engine_type,
            semantic_context={"period_params": period_params},
        )
        top_slices = execute_compiled(engine, top_slices_query).rows
        overall_query = self._compile_step_with_feedback(
            from_legacy_step(
                1,
                {"step_type": "compare_watch_time_overall", "params": {"table_name": "analytics.watch_events"}},
            ),
            engine_type=engine_type,
            semantic_context={"period_params": period_params},
        )
        overall = execute_compiled(engine, overall_query).rows[0]

        observations = []
        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            top_slices,
            context={
                "metric": "watch_time",
                "observation_type": "metric_change",
                "payload_fields": {
                    "current_value": "current_watch_time",
                    "baseline_value": "baseline_watch_time",
                    "delta_pct": "delta_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact = {
            "overall_delta_pct": overall["delta_pct"],
            "overall_current_watch_time": overall["current_watch_time"],
            "overall_baseline_watch_time": overall["baseline_watch_time"],
            "top_slices": top_slices,
            "window": {
                "current": [str(current_start), str(current_end)],
                "baseline": [str(baseline_start), str(baseline_end)],
            },
        }
        artifact_id = self._insert_artifact(session_id, step_id, "table", "watch_time_comparison", artifact)
        summary = (
            f"Overall watch time moved {overall['delta_pct']}%, with the worst slice in "
            f"{top_slices[0]['platform']} {top_slices[0]['app_version']} {top_slices[0]['network_type']} "
            f"{top_slices[0]['content_type']} traffic ({top_slices[0]['delta_pct']}%)."
        )
        provenance = self._make_provenance(
            f"{top_slices_query.sql}\n{overall_query.sql}",
            [*top_slices_query.params, *overall_query.params],
            engine_type=engine_type,
        )
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_qoe_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_qoe"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["player_qoe"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        period_params = [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": step_type, "params": {"table_name": "analytics.player_qoe", "limit": 3}},
            ),
            engine_type=engine_type,
            semantic_context={"period_params": period_params},
        )
        rows = execute_compiled(engine, compiled_query).rows

        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            rows,
            context={
                "metric": "first_frame_time",
                "observation_type": "qoe_regression",
                "payload_fields": {
                    "current_value": "current_first_frame_ms",
                    "baseline_value": "baseline_first_frame_ms",
                    "delta_pct": "delta_pct",
                    "delta_ms": "delta_ms",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "qoe_comparison", rows)
        summary = (
            f"QoE regression is strongest in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic, where first-frame time rose "
            f"{rows[0]['delta_pct']}%."
        )
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_ad_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_ads"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["ad_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        period_params = [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": step_type, "params": {"table_name": "analytics.ad_events", "limit": 3}},
            ),
            engine_type=engine_type,
            semantic_context={"period_params": period_params},
        )
        rows = execute_compiled(engine, compiled_query).rows

        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            rows,
            context={
                "metric": "preroll_timeout_rate",
                "observation_type": "ad_regression",
                "payload_fields": {
                    "current_value": "current_timeout_rate",
                    "baseline_value": "baseline_timeout_rate",
                    "delta_rate": "delta_rate",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "ad_timeout_comparison", rows)
        summary = (
            f"Preroll timeout pressure increased most in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic (+{rows[0]['delta_rate']})."
        )
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_recommendation_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_recommendation"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["recommendation_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        period_params = [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": step_type, "params": {"table_name": "analytics.recommendation_events", "limit": 3}},
            ),
            engine_type=engine_type,
            semantic_context={"period_params": period_params},
        )
        rows = execute_compiled(engine, compiled_query).rows

        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            rows,
            context={
                "metric": "recommendation_ctr",
                "observation_type": "recommendation_signal",
                "payload_fields": {
                    "current_value": "current_ctr",
                    "baseline_value": "baseline_ctr",
                    "delta_ctr_pct": "delta_ctr_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "recommendation_ctr_comparison", rows)
        summary = (
            f"Recommendation CTR did not show a broad collapse; the strongest movement is "
            f"{rows[0]['delta_ctr_pct']}% in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic."
        )
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_compare_metric(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Generic metric comparison step driven by semantic metric definitions.

        Required params:
            metric_name: name of a published semantic metric
            table_name: table to query (in analytics schema)
        Optional params:
            date_column: column for period comparison (default: event_date)
            observation_type: type for observations (default: metric_change)
            limit: number of top slices (default: 3)
        """
        metric_name = params.get("metric_name")
        table_name = params.get("table_name")
        if not metric_name or not table_name:
            raise ValueError("compare_metric requires 'metric_name' and 'table_name' params")

        step_type = "compare_metric"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()

        metric_sql = self.resolve_metric_sql(metric_name)
        dimensions = self.resolve_metric_dimensions(metric_name)
        if metric_sql is None or dimensions is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published in semantic_metrics")

        date_column = params.get("date_column", "event_date")
        obs_type = params.get("observation_type", "metric_change")
        limit = params.get("limit", 3)

        engine, engine_type = self._resolve_engine([table_name.split(".")[-1]])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)

        period_params = [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {
                    "step_type": step_type,
                    "params": {
                        **params,
                        "metric_name": metric_name,
                        "table_name": table_name,
                        "date_column": date_column,
                        "limit": limit,
                    },
                },
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": dimensions,
                "period_params": period_params,
            },
        )
        rows = execute_compiled(engine, compiled_query).rows

        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            rows,
            context={
                "metric": metric_name,
                "observation_type": obs_type,
                "payload_fields": {
                    "current_value": "current_value",
                    "baseline_value": "baseline_value",
                    "delta_pct": "delta_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            },
        )
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", f"{metric_name}_comparison", rows)
        summary = (
            f"Metric '{metric_name}' comparison: top decline is {rows[0]['delta_pct']}% "
            f"in {' / '.join(str(rows[0].get(d, '')) for d in dimensions)} traffic."
            if rows else f"Metric '{metric_name}' comparison returned no results."
        )
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {
            "step_type": step_type,
            "metric_name": metric_name,
            "summary": summary,
            "artifact_id": artifact_id,
            "observations": observations,
        }
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_profile_table(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Profile a table: row count, column stats (null rate, distinct count).

        Required params:
            table_name: fully qualified table name (e.g. analytics.watch_events)
        """
        table_name = params.get("table_name")
        if not table_name:
            raise ValueError("profile_table requires 'table_name' param")

        step_type = "profile_table"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()

        short_name = table_name.split(".")[-1]
        engine, engine_type = self._resolve_engine([short_name])

        row_count_query = self._compile_step_with_feedback(
            AnalysisStepIR(index=0, step_type="profile_table_row_count", params={"table_name": table_name}),
            engine_type=engine_type,
        )
        row_count_row = execute_compiled(engine, row_count_query).rows[0]
        row_count = row_count_row["row_count"]

        try:
            columns_query = self._compile_step_with_feedback(
                AnalysisStepIR(
                    index=0,
                    step_type="profile_table_columns",
                    params={"table_name": table_name, "short_name": short_name},
                ),
                engine_type=engine_type,
            )
            col_rows = execute_compiled(engine, columns_query).rows
            columns = [r["column_name"] for r in col_rows]
        except Exception:
            columns = []

        col_profiles = []
        for col in columns[:20]:  # cap at 20 columns for safety
            try:
                stats_query = self._compile_step_with_feedback(
                    AnalysisStepIR(
                        index=0,
                        step_type="profile_table_column_profile",
                        params={"table_name": table_name, "column_name": col},
                    ),
                    engine_type=engine_type,
                )
                stats = execute_compiled(engine, stats_query).rows[0]
                col_profiles.append({
                    "column": col,
                    "total": stats["total"],
                    "non_null": stats["non_null"],
                    "null_rate": round(1 - stats["non_null"] / max(stats["total"], 1), 4),
                    "distinct_count": stats["distinct_count"],
                })
            except Exception:
                col_profiles.append({"column": col, "error": "failed to profile"})

        artifact = {"table_name": table_name, "row_count": row_count, "columns": col_profiles}
        artifact_id = self._insert_artifact(session_id, step_id, "profile", f"{short_name}_profile", artifact)

        summary = f"Table '{table_name}' has {row_count} rows and {len(columns)} columns."
        provenance = self._make_provenance(f"profile:{table_name}", engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "profile": artifact}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_sample_rows(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return a sample of rows from a table.

        Required params:
            table_name: fully qualified table name
        Optional params:
            limit: number of rows (default: 10)
        """
        table_name = params.get("table_name")
        if not table_name:
            raise ValueError("sample_rows requires 'table_name' param")

        step_type = "sample_rows"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()

        limit = int(params.get("limit", 10))
        short_name = table_name.split(".")[-1]
        engine, engine_type = self._resolve_engine([short_name])

        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": step_type, "params": {"table_name": table_name, "limit": limit}},
            ),
            engine_type=engine_type,
        )
        rows = execute_compiled(engine, compiled_query).rows

        artifact_id = self._insert_artifact(session_id, step_id, "sample", f"{short_name}_sample", rows)
        summary = f"Sampled {len(rows)} rows from '{table_name}'."
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "rows": rows}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_synthesis(self, session_id: str) -> dict[str, Any]:
        step_type = "synthesize_findings"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        observations = self._load_observations(session_id)
        synthesis = self.evidence_pipeline.build_synthesis(observations)

        for claim in synthesis["claims"]:
            self._insert_claim(session_id, claim)

        for recommendation in synthesis["recommendations"]:
            self._insert_recommendation(session_id, recommendation)

        for edge in synthesis["edges"]:
            self._insert_edge(session_id, **edge)

        summary = synthesis["summary"]
        provenance = self._make_provenance("synthesize_findings", engine_type="heuristic")
        result = {
            "step_type": step_type,
            "summary": summary,
            "claims": synthesis["claims"],
            "recommendations": synthesis["recommendations"],
        }
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    # ── Metadata helpers ──────────────────────────────────────────────

    def _reset_session_outputs(self, session_id: str) -> None:
        for table in ["recommendations", "evidence_edges", "claims", "observations", "artifacts", "steps"]:
            self.metadata.execute(f"DELETE FROM {table} WHERE session_id = ?", [session_id])

    def _delete_step_outputs(self, session_id: str, step_type: str) -> None:
        rows = self.metadata.query_rows(
            "SELECT step_id FROM steps WHERE session_id = ? AND step_type = ?",
            [session_id, step_type],
        )
        step_ids = [row["step_id"] for row in rows]
        for sid in step_ids:
            self.metadata.execute("DELETE FROM artifacts WHERE step_id = ?", [sid])
            self.metadata.execute("DELETE FROM observations WHERE step_id = ?", [sid])
        if step_type == "synthesize_findings":
            self.metadata.execute("DELETE FROM recommendations WHERE session_id = ?", [session_id])
            self.metadata.execute("DELETE FROM evidence_edges WHERE session_id = ?", [session_id])
            self.metadata.execute("DELETE FROM claims WHERE session_id = ?", [session_id])
        self.metadata.execute(
            "DELETE FROM steps WHERE session_id = ? AND step_type = ?",
            [session_id, step_type],
        )

    def _assert_session_exists(self, session_id: str) -> None:
        self.session_manager.assert_session_exists(session_id)

    def _period_bounds(self, engine: AnalyticsEngine | None = None) -> tuple[date, date, date, date]:
        engine = engine or self.analytics
        row = engine.query_rows("SELECT MAX(event_date) AS max_date FROM analytics.watch_events")[0]
        current_end = row["max_date"]
        current_start = current_end - timedelta(days=13)
        baseline_end = current_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=13)
        return current_start, current_end, baseline_start, baseline_end

    def _load_observations(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT observation_id, observation_type, subject_json, payload_json, significance_json, quality_json
            FROM observations
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        observations = []
        for row in rows:
            observations.append(
                {
                    "observation_id": row["observation_id"],
                    "type": row["observation_type"],
                    "subject": json.loads(row["subject_json"]),
                    "payload": json.loads(row["payload_json"]),
                    "significance": json.loads(row["significance_json"]),
                    "quality": json.loads(row["quality_json"]),
                }
            )
        return observations

    def _make_provenance(self, sql: str = "", params: list[Any] | None = None, engine_type: str = "duckdb") -> dict[str, Any]:
        """Build a provenance token for a step execution."""
        query_hash = hashlib.sha256(sql.encode()).hexdigest()[:16] if sql else ""
        provenance = {
            "query_hash": query_hash,
            "engine": engine_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "param_count": len(params) if params else 0,
        }
        if self._governance_context:
            provenance["governance"] = {
                "decisions": self._governance_context.get("decisions", []),
                "transforms": self._governance_context.get("transforms", {}),
                "hard_constraints": self._governance_context.get("hard_constraints", []),
                "soft_signals": self._governance_context.get("soft_signals", []),
            }
        if self._routing_feedback_context:
            provenance["routing"] = dict(self._routing_feedback_context)
        return provenance

    def _attach_replanning_provenance(
        self,
        session_id: str,
        step_type: str,
        decisions: list[dict[str, Any]],
    ) -> None:
        row = self.metadata.query_one(
            """
            SELECT step_id, provenance_json
            FROM steps
            WHERE session_id = ? AND step_type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [session_id, step_type],
        )
        if row is None:
            return

        provenance = json.loads(row["provenance_json"])
        history = provenance.get("replanning", [])
        if not isinstance(history, list):
            history = [history]
        history.extend(decisions)
        provenance["replanning"] = history
        self.metadata.execute(
            "UPDATE steps SET provenance_json = ? WHERE step_id = ?",
            [self._dump(provenance), row["step_id"]],
        )

    def _governance_tables(self, step_type: str, params: dict[str, Any]) -> list[str]:
        table_name = params.get("table_name")
        if table_name:
            return [str(table_name)]
        default_table = DEFAULT_STEP_TABLES.get(step_type)
        return [default_table] if default_table else []

    def _insert_step(
        self,
        step_id: str,
        session_id: str,
        step_type: str,
        summary: str,
        result: dict[str, Any],
        provenance: dict[str, Any] | None = None,
    ) -> None:
        self.metadata.execute(
            """
            INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json, provenance_json)
            VALUES (?, ?, ?, 'succeeded', ?, ?, ?)
            """,
            [step_id, session_id, step_type, summary, self._dump(result), self._dump(provenance or {})],
        )

    def _insert_artifact(self, session_id: str, step_id: str, artifact_type: str, name: str, content: Any) -> str:
        artifact_id = f"art_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [artifact_id, session_id, step_id, artifact_type, name, self._dump(content)],
        )
        return artifact_id

    def _insert_observation(self, session_id: str, step_id: str, observation: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation["observation_id"],
                session_id,
                step_id,
                observation["type"],
                self._dump(observation["subject"]),
                self._dump(observation["payload"]),
                self._dump(observation["significance"]),
                self._dump(observation["quality"]),
            ],
        )

    def _insert_claim(self, session_id: str, claim: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO claims (
                claim_id, session_id, claim_type, text, scope_json, confidence, status,
                supporting_observation_ids_json, contradicting_observation_ids_json, confidence_breakdown_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                claim["claim_id"],
                session_id,
                claim["type"],
                claim["text"],
                self._dump(claim["scope"]),
                claim["confidence"],
                claim["status"],
                self._dump(claim["supporting_observations"]),
                self._dump(claim["contradicting_observations"]),
                self._dump(claim["confidence_breakdown"]),
            ],
        )

    def _insert_edge(
        self,
        session_id: str,
        from_node_id: str,
        from_node_type: str,
        to_node_id: str,
        to_node_type: str,
        edge_type: str,
        weight: float,
        explanation: str,
    ) -> None:
        self.metadata.execute(
            """
            INSERT INTO evidence_edges (
                edge_id, session_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight, explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [f"edge_{uuid4().hex[:12]}", session_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight, explanation],
        )

    def _insert_recommendation(self, session_id: str, recommendation: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO recommendations (
                rec_id, session_id, claim_id, action_text, priority, expected_impact, risk, validation_metric_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                recommendation["rec_id"],
                session_id,
                recommendation["claim_id"],
                recommendation["action_text"],
                recommendation["priority"],
                recommendation["expected_impact"],
                recommendation["risk"],
                self._dump(recommendation["validation_metric"]),
            ],
        )

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def _new_step_id(self) -> str:
        return f"step_{uuid4().hex[:12]}"


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "mvp.duckdb"
