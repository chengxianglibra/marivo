from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.analysis_core import build_service_step_registry
from app.analysis_core.compiler import build_comparison_query as compile_comparison_query
from app.analysis_core.compiler import compile_step
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR, from_legacy_step
from app.dialect import translate
from app.evidence import make_observation, synthesize_claims
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
    ) -> None:
        self.metadata = metadata_store
        self.analytics = analytics_engine
        self.query_router = query_router
        self.governance = governance
        self.metrics = metrics
        self.approvals = approvals
        self.session_manager = SessionManager(metadata_store)
        self.step_registry = build_service_step_registry(self)

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

        # Governance check
        if self.governance:
            tables = []
            if params and params.get("table_name"):
                tables.append(params["table_name"])
            gov_result = self.governance.check_step(session_id, normalized, params, tables=tables or None)
            if not gov_result["passed"]:
                raise ValueError(f"Governance check failed: {gov_result['violations']}")

        start = time.perf_counter()
        try:
            result = self.step_registry.run(session_id, normalized, params)
        except KeyError as error:
            raise ValueError(f"Unsupported step type: {step_type}") from error
        duration_ms = (time.perf_counter() - start) * 1000

        if self.metrics:
            self.metrics.record_step(normalized, duration_ms)

        return result

    def run_watch_time_drop_workflow(self, session_id: str) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        self._reset_session_outputs(session_id)
        results = [
            self._run_compare_watch_time(session_id),
            self._run_qoe_analysis(session_id),
            self._run_ad_analysis(session_id),
            self._run_recommendation_analysis(session_id),
            self._run_synthesis(session_id),
        ]
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
        """Look up a published metric's definition_sql from semantic_metrics.

        Returns None if the metric is not found or not published.
        """
        row = self.metadata.query_one(
            "SELECT definition_sql FROM semantic_metrics WHERE name = ? AND status = 'published'",
            [metric_name],
        )
        return row["definition_sql"] if row else None

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        """Look up a published metric's dimensions from semantic_metrics.

        Returns None if the metric is not found or not published.
        """
        row = self.metadata.query_one(
            "SELECT dimensions_json FROM semantic_metrics WHERE name = ? AND status = 'published'",
            [metric_name],
        )
        return json.loads(row["dimensions_json"]) if row else None

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
        if self.query_router is not None:
            try:
                route = self.query_router.resolve_tables(table_names)
                eng_info = self.query_router.engine_service.get_engine(route.engine_id)
                return route.engine, eng_info["engine_type"]
            except (KeyError, ValueError):
                # Table not found in source_objects or no bindings — fall back
                pass
        return self.analytics, "duckdb"

    # ── Step runners ──────────────────────────────────────────────────

    def _run_compare_watch_time(self, session_id: str) -> dict[str, Any]:
        step_type = "compare_watch_time"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["watch_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        top_slices = engine.query_rows(
            translate("""
            WITH periodized AS (
                SELECT
                    CASE
                        WHEN event_date BETWEEN ? AND ? THEN 'current'
                        WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                    END AS period,
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    play_duration_seconds
                FROM analytics.watch_events
                WHERE event_date BETWEEN ? AND ?
            ),
            aggregated AS (
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    AVG(play_duration_seconds) FILTER (WHERE period = 'current') AS current_watch_time,
                    AVG(play_duration_seconds) FILTER (WHERE period = 'baseline') AS baseline_watch_time,
                    COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                    COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                FROM periodized
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                platform,
                app_version,
                network_type,
                content_type,
                ROUND(current_watch_time, 2) AS current_watch_time,
                ROUND(baseline_watch_time, 2) AS baseline_watch_time,
                ROUND(((current_watch_time - baseline_watch_time) / baseline_watch_time) * 100, 2) AS delta_pct,
                current_sessions,
                baseline_sessions
            FROM aggregated
            ORDER BY delta_pct ASC
            LIMIT 3
            """, engine_type),
            [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end],
        )
        overall = engine.query_rows(
            translate("""
            WITH periodized AS (
                SELECT
                    CASE
                        WHEN event_date BETWEEN ? AND ? THEN 'current'
                        WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                    END AS period,
                    play_duration_seconds
                FROM analytics.watch_events
                WHERE event_date BETWEEN ? AND ?
            )
            SELECT
                ROUND(AVG(play_duration_seconds) FILTER (WHERE period = 'current'), 2) AS current_watch_time,
                ROUND(AVG(play_duration_seconds) FILTER (WHERE period = 'baseline'), 2) AS baseline_watch_time,
                ROUND(
                    (
                        (AVG(play_duration_seconds) FILTER (WHERE period = 'current'))
                        - (AVG(play_duration_seconds) FILTER (WHERE period = 'baseline'))
                    ) / (AVG(play_duration_seconds) FILTER (WHERE period = 'baseline')) * 100,
                    2
                ) AS delta_pct
            FROM periodized
            """, engine_type),
            [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end],
        )[0]

        observations = []
        for row in top_slices:
            observation = make_observation(
                "metric_change",
                "watch_time",
                row,
                {
                    "current_value": row["current_watch_time"],
                    "baseline_value": row["baseline_watch_time"],
                    "delta_pct": row["delta_pct"],
                    "current_sessions": row["current_sessions"],
                    "baseline_sessions": row["baseline_sessions"],
                },
                {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            )
            self._insert_observation(session_id, step_id, observation)
            observations.append(observation)

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
        provenance = self._make_provenance("compare_watch_time", engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_qoe_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_qoe"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["player_qoe"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        rows = engine.query_rows(
            translate("""
            WITH periodized AS (
                SELECT
                    CASE
                        WHEN event_date BETWEEN ? AND ? THEN 'current'
                        WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                    END AS period,
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    first_frame_time_ms
                FROM analytics.player_qoe
                WHERE event_date BETWEEN ? AND ?
            ),
            aggregated AS (
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    AVG(first_frame_time_ms) FILTER (WHERE period = 'current') AS current_first_frame_ms,
                    AVG(first_frame_time_ms) FILTER (WHERE period = 'baseline') AS baseline_first_frame_ms,
                    COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                    COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                FROM periodized
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                platform,
                app_version,
                network_type,
                content_type,
                ROUND(current_first_frame_ms, 2) AS current_first_frame_ms,
                ROUND(baseline_first_frame_ms, 2) AS baseline_first_frame_ms,
                ROUND(((current_first_frame_ms - baseline_first_frame_ms) / baseline_first_frame_ms) * 100, 2) AS delta_pct,
                ROUND(current_first_frame_ms - baseline_first_frame_ms, 2) AS delta_ms,
                current_sessions,
                baseline_sessions
            FROM aggregated
            ORDER BY delta_pct DESC
            LIMIT 3
            """, engine_type),
            [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end],
        )

        observations = []
        for row in rows:
            observation = make_observation(
                "qoe_regression",
                "first_frame_time",
                row,
                {
                    "current_value": row["current_first_frame_ms"],
                    "baseline_value": row["baseline_first_frame_ms"],
                    "delta_pct": row["delta_pct"],
                    "delta_ms": row["delta_ms"],
                    "current_sessions": row["current_sessions"],
                    "baseline_sessions": row["baseline_sessions"],
                },
                {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            )
            self._insert_observation(session_id, step_id, observation)
            observations.append(observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "qoe_comparison", rows)
        summary = (
            f"QoE regression is strongest in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic, where first-frame time rose "
            f"{rows[0]['delta_pct']}%."
        )
        provenance = self._make_provenance("analyze_qoe", engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_ad_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_ads"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["ad_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        rows = engine.query_rows(
            translate("""
            WITH periodized AS (
                SELECT
                    CASE
                        WHEN event_date BETWEEN ? AND ? THEN 'current'
                        WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                    END AS period,
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    preroll_timeout
                FROM analytics.ad_events
                WHERE event_date BETWEEN ? AND ?
            ),
            aggregated AS (
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'current') AS current_timeout_rate,
                    AVG(preroll_timeout::DOUBLE) FILTER (WHERE period = 'baseline') AS baseline_timeout_rate,
                    COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                    COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                FROM periodized
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                platform,
                app_version,
                network_type,
                content_type,
                ROUND(current_timeout_rate, 4) AS current_timeout_rate,
                ROUND(baseline_timeout_rate, 4) AS baseline_timeout_rate,
                ROUND(current_timeout_rate - baseline_timeout_rate, 4) AS delta_rate,
                current_sessions,
                baseline_sessions
            FROM aggregated
            ORDER BY delta_rate DESC
            LIMIT 3
            """, engine_type),
            [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end],
        )

        observations = []
        for row in rows:
            observation = make_observation(
                "ad_regression",
                "preroll_timeout_rate",
                row,
                {
                    "current_value": row["current_timeout_rate"],
                    "baseline_value": row["baseline_timeout_rate"],
                    "delta_rate": row["delta_rate"],
                    "current_sessions": row["current_sessions"],
                    "baseline_sessions": row["baseline_sessions"],
                },
                {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            )
            self._insert_observation(session_id, step_id, observation)
            observations.append(observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "ad_timeout_comparison", rows)
        summary = (
            f"Preroll timeout pressure increased most in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic (+{rows[0]['delta_rate']})."
        )
        provenance = self._make_provenance("analyze_ads", engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "observations": observations}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_recommendation_analysis(self, session_id: str) -> dict[str, Any]:
        step_type = "analyze_recommendation"
        self._delete_step_outputs(session_id, step_type)
        step_id = self._new_step_id()
        engine, engine_type = self._resolve_engine(["recommendation_events"])
        current_start, current_end, baseline_start, baseline_end = self._period_bounds(engine)
        rows = engine.query_rows(
            translate("""
            WITH periodized AS (
                SELECT
                    CASE
                        WHEN event_date BETWEEN ? AND ? THEN 'current'
                        WHEN event_date BETWEEN ? AND ? THEN 'baseline'
                    END AS period,
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    impressions,
                    clicks
                FROM analytics.recommendation_events
                WHERE event_date BETWEEN ? AND ?
            ),
            aggregated AS (
                SELECT
                    platform,
                    app_version,
                    network_type,
                    content_type,
                    SUM(clicks) FILTER (WHERE period = 'current')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'current') AS current_ctr,
                    SUM(clicks) FILTER (WHERE period = 'baseline')::DOUBLE / SUM(impressions) FILTER (WHERE period = 'baseline') AS baseline_ctr,
                    COUNT(*) FILTER (WHERE period = 'current') AS current_sessions,
                    COUNT(*) FILTER (WHERE period = 'baseline') AS baseline_sessions
                FROM periodized
                GROUP BY 1, 2, 3, 4
            )
            SELECT
                platform,
                app_version,
                network_type,
                content_type,
                ROUND(current_ctr, 4) AS current_ctr,
                ROUND(baseline_ctr, 4) AS baseline_ctr,
                ROUND(((current_ctr - baseline_ctr) / baseline_ctr) * 100, 2) AS delta_ctr_pct,
                current_sessions,
                baseline_sessions
            FROM aggregated
            ORDER BY delta_ctr_pct DESC
            LIMIT 3
            """, engine_type),
            [current_start, current_end, baseline_start, baseline_end, baseline_start, current_end],
        )

        observations = []
        for row in rows:
            observation = make_observation(
                "recommendation_signal",
                "recommendation_ctr",
                row,
                {
                    "current_value": row["current_ctr"],
                    "baseline_value": row["baseline_ctr"],
                    "delta_ctr_pct": row["delta_ctr_pct"],
                    "current_sessions": row["current_sessions"],
                    "baseline_sessions": row["baseline_sessions"],
                },
                {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            )
            self._insert_observation(session_id, step_id, observation)
            observations.append(observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", "recommendation_ctr_comparison", rows)
        summary = (
            f"Recommendation CTR did not show a broad collapse; the strongest movement is "
            f"{rows[0]['delta_ctr_pct']}% in {rows[0]['platform']} {rows[0]['app_version']} "
            f"{rows[0]['network_type']} {rows[0]['content_type']} traffic."
        )
        provenance = self._make_provenance("analyze_recommendation", engine_type=engine_type)
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
        compiled_query = compile_step(
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

        observations = []
        for row in rows:
            observation = make_observation(
                obs_type,
                metric_name,
                row,
                {
                    "current_value": row["current_value"],
                    "baseline_value": row["baseline_value"],
                    "delta_pct": row["delta_pct"],
                    "current_sessions": row["current_sessions"],
                    "baseline_sessions": row["baseline_sessions"],
                },
                {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"], row["baseline_sessions"]) >= 150,
                },
            )
            self._insert_observation(session_id, step_id, observation)
            observations.append(observation)

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

        row_count_query = compile_step(
            AnalysisStepIR(index=0, step_type="profile_table_row_count", params={"table_name": table_name}),
            engine_type=engine_type,
        )
        row_count_row = execute_compiled(engine, row_count_query).rows[0]
        row_count = row_count_row["row_count"]

        try:
            columns_query = compile_step(
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
                stats_query = compile_step(
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

        compiled_query = compile_step(
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
        claims, recommendations, _ = synthesize_claims(observations)
        for claim in claims:
            self._insert_claim(session_id, claim)
            for observation_id in claim["supporting_observations"]:
                self._insert_edge(
                    session_id,
                    from_node_id=observation_id,
                    from_node_type="observation",
                    to_node_id=claim["claim_id"],
                    to_node_type="claim",
                    edge_type="supports",
                    weight=claim["confidence"],
                    explanation="Observation strengthens the claim.",
                )
            for observation_id in claim["contradicting_observations"]:
                self._insert_edge(
                    session_id,
                    from_node_id=observation_id,
                    from_node_type="observation",
                    to_node_id=claim["claim_id"],
                    to_node_type="claim",
                    edge_type="contradicts",
                    weight=0.35,
                    explanation="Observation weakens the claim.",
                )

        for recommendation in recommendations:
            self._insert_recommendation(session_id, recommendation)
            self._insert_edge(
                session_id,
                from_node_id=recommendation["claim_id"],
                from_node_type="claim",
                to_node_id=recommendation["rec_id"],
                to_node_type="recommendation",
                edge_type="justifies",
                weight=0.9,
                explanation="Claim justifies the recommendation.",
            )

        summary = claims[0]["text"] if claims else "No supported claims were generated."
        provenance = self._make_provenance("synthesize_findings", engine_type="heuristic")
        result = {
            "step_type": step_type,
            "summary": summary,
            "claims": claims,
            "recommendations": recommendations,
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
        return {
            "query_hash": query_hash,
            "engine": engine_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "param_count": len(params) if params else 0,
        }

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
