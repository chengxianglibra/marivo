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
from app.evidence_engine.readiness import compute_readiness, load_live_claims
from app.execution.feedback import compile_failure_from_error
from app.execution.orchestrator import WorkflowOrchestrator
from app.execution.routing_runtime import RoutingRuntime
from app.planner import ReplanningService
from app.semantic_runtime import SemanticRuntimeRepository
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
        self._query_router = query_router
        self.governance = governance
        self.metrics = metrics
        self.approvals = approvals
        self.session_manager = SessionManager(metadata_store)
        self.step_registry = build_service_step_registry(self)
        self.evidence_pipeline = EvidencePipeline(synthesize_claims)
        self.semantic_repository = SemanticRuntimeRepository(metadata_store)
        self.semantic_resolver = self.semantic_repository.resolver
        self.planner_context_provider = self.semantic_repository.planner_context_provider
        self.workflow_runtime = CompositeWorkflowRuntime()
        self.replanner = replanner or ReplanningService(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )
        self._incremental_synthesizer: Any | None = None  # IncrementalSynthesizer, injected post-construction
        self._governance_context: dict[str, Any] | None = None
        self._routing_feedback_context: dict[str, Any] | None = None
        self.routing_runtime = RoutingRuntime(query_router, analytics_engine)
        self.workflow_orchestrator = WorkflowOrchestrator(
            workflow_runtime=self.workflow_runtime,
            replanner=self.replanner,
            analytics_engine=self.analytics,
            query_router=self.query_router,
            step_executor=_ServiceWorkflowStepExecutor(self),
            approval_service=self.approvals,
        )

    @property
    def query_router(self) -> QueryRouter | None:
        return self._query_router

    @query_router.setter
    def query_router(self, router: QueryRouter | None) -> None:
        self._query_router = router
        self.routing_runtime.query_router = router
        self.replanner.cost_model.query_router = router
        self.workflow_orchestrator.query_router = router

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
        # Entities — all published semantic entities
        entity_rows = self.metadata.query_rows(
            "SELECT name, keys_json FROM semantic_entities WHERE status = 'published' ORDER BY name"
        )
        entities = [
            {"id": row["name"], "keys": json.loads(row["keys_json"])}
            for row in entity_rows
        ]

        # Metrics — all published semantic metrics
        metric_rows = self.metadata.query_rows(
            "SELECT name, display_name, definition_sql, dimensions_json "
            "FROM semantic_metrics WHERE status = 'published' ORDER BY name"
        )
        metrics = [
            {
                "id": row["name"],
                "label": row["display_name"],
                "definition": row["definition_sql"],
                "dimensions": json.loads(row["dimensions_json"]),
            }
            for row in metric_rows
        ]

        # Assets — all synced tables from source_objects
        asset_rows = self.metadata.query_rows(
            "SELECT native_name, fqn, source_id FROM source_objects "
            "WHERE object_type = 'table' ORDER BY fqn"
        )
        assets: list[dict[str, Any]] = []
        for row in asset_rows:
            asset: dict[str, Any] = {
                "id": row["native_name"],
                "kind": "table",
                "fqn": row["fqn"],
                "source_id": row["source_id"],
            }
            # Best-effort row count from the analytics engine
            try:
                asset["row_count"] = self.analytics.table_row_count(row["fqn"])
            except Exception:
                try:
                    asset["row_count"] = self.analytics.table_row_count(
                        f"analytics.{row['native_name']}"
                    )
                except Exception:
                    asset["row_count"] = None
            assets.append(asset)

        # Policies — from governance service if available
        policies: list[str] = []
        if self.governance:
            for pol in self.governance.list_policies(enabled_only=True):
                policies.append(f"{pol['policy_type']}: {pol['name']}")
        if not policies:
            policies = [
                "Results are aggregate-only in the MVP.",
                "Evidence graph keeps support and contradiction links for every claim.",
            ]

        return {
            "entities": entities,
            "metrics": metrics,
            "assets": assets,
            "policies": policies,
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
            available = self.step_registry.keys()
            raise ValueError(f"Unsupported step type: {step_type}. Available: {available}") from error
        finally:
            self._governance_context = None
            self._routing_feedback_context = None
        duration_ms = (time.perf_counter() - start) * 1000

        if self.metrics:
            self.metrics.record_step(
                normalized,
                duration_ms,
                engine=result.get("provenance", {}).get("engine"),
                stage="executor",
            )

        if governance_result:
            result["governance"] = {
                "decisions": governance_result.get("decisions", []),
                "transforms": governance_result.get("transforms", {}),
                "hard_constraints": governance_result.get("hard_constraints", []),
                "soft_signals": governance_result.get("soft_signals", []),
            }

        # M-03: incremental synthesis after each primitive step.
        # synthesize_findings itself is excluded — it handles promotion.
        if self._incremental_synthesizer is not None and normalized != "synthesize_findings":
            self._incremental_synthesizer.process(session_id)

        # M-04: readiness signal after each primitive step.
        if normalized != "synthesize_findings":
            session = self.get_session(session_id)
            result["readiness"] = compute_readiness(
                self.metadata, session_id, session.get("budget", {}) or {}
            )
            result["live_claims"] = load_live_claims(self.metadata, session_id)

        return result

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
                   supporting_observation_ids_json, contradicting_observation_ids_json, confidence_breakdown_json,
                   inference_level, inference_justification_json
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
            claim["inference_justification"] = json.loads(claim.pop("inference_justification_json"))
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
        return self.semantic_repository.resolve_metric_sql(metric_name)

    def resolve_metric_dimensions(self, metric_name: str) -> list[str] | None:
        """Look up a published metric's dimensions from semantic runtime."""
        return self.semantic_repository.resolve_metric_dimensions(metric_name)

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

    def _resolve_engine(self, table_names: list[str]) -> tuple[AnalyticsEngine, str, dict[str, str]]:
        """Resolve the analytics engine, its type, and qualified table names.

        Uses QueryRouter when available, falls back to self.analytics.
        Returns ``(engine, engine_type, qualified_names)`` tuple where
        qualified_names maps native table names to engine-qualified names.
        """
        resolution = self.routing_runtime.resolve_tables(table_names)
        self._routing_feedback_context = (
            resolution.feedback.to_dict() if resolution.feedback is not None else None
        )
        qualified = (
            resolution.route.qualified_names
            if resolution.route is not None
            else {}
        )
        return resolution.engine, resolution.engine_type, qualified

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

    def _session_constraints_to_filter(self, session_id: str) -> str | None:
        """Convert simple scalar session constraints to a SQL filter expression.

        Non-scalar constraints (dicts, lists) are silently ignored.
        Returns None when no scalar constraints exist.
        """
        session = self.get_session(session_id)
        constraints = session.get("constraints", {})
        if not constraints or not isinstance(constraints, dict):
            return None
        parts: list[str] = []
        for key, value in constraints.items():
            if isinstance(value, (dict, list)):
                continue
            parts.append(f"{key} = '{value}'")
        return " AND ".join(parts) if parts else None

    @staticmethod
    def _merge_filters(*filters: str | None) -> str | None:
        """AND-merge multiple filter expressions, ignoring None values."""
        parts = [f for f in filters if f]
        if not parts:
            return None
        return " AND ".join(f"({p})" for p in parts)

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
        step_id = self._new_step_id()

        metric_sql = self.resolve_metric_sql(metric_name)
        all_dimensions = self.resolve_metric_dimensions(metric_name)
        if metric_sql is None or all_dimensions is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published in semantic_metrics")

        # Infer date column BEFORE dimension selection so it can be excluded
        date_column = params.get("date_column") or self._infer_date_column(all_dimensions)

        # Allow caller to select a subset of dimensions for grouping
        requested_dims = params.get("dimensions")
        if requested_dims:
            invalid = set(requested_dims) - set(all_dimensions)
            if invalid:
                raise ValueError(f"Invalid dimensions {invalid}; valid: {all_dimensions}")

        dimensions = self._comparison_dimensions(
            all_dimensions, date_column, requested=requested_dims,
        )
        if requested_dims and not dimensions:
            filtered_out = [d for d in requested_dims if d == date_column]
            raise ValueError(
                f"Cannot use '{filtered_out[0]}' as comparison dimension because "
                f"it is the period-splitting column (date_column='{date_column}'). "
                f"Use a different dimension or omit dimensions for overall aggregate comparison."
            )
        obs_type = params.get("observation_type", "metric_change")
        limit = params.get("limit", 10)

        # Merge session constraints into filter
        constraints_filter = self._session_constraints_to_filter(session_id)
        user_filter = params.get("filter")
        merged_filter = self._merge_filters(user_filter, constraints_filter)
        if merged_filter:
            params = {**params, "filter": merged_filter}

        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)

        # Support user-provided period_start/period_end
        user_period_start = params.get("period_start")
        user_period_end = params.get("period_end")
        if user_period_start and user_period_end:
            # Parse user-provided dates
            ps = date.fromisoformat(str(user_period_start))
            pe = date.fromisoformat(str(user_period_end))
            period_length = (pe - ps).days
            baseline_end_d = ps - timedelta(days=1)
            baseline_start_d = baseline_end_d - timedelta(days=period_length)
            # Detect date format from the engine data
            try:
                row = engine.query_rows(
                    f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}"
                )[0]
                date_fmt = self._detect_date_format(row["max_date"])
            except Exception:
                date_fmt = self._detect_date_format(str(user_period_start))
            current_start, current_end = ps, pe
            baseline_start, baseline_end = baseline_start_d, baseline_end_d
        else:
            current_start, current_end, baseline_start, baseline_end, date_fmt = self._period_bounds(
                engine, table_name=qualified_table, date_column=date_column,
            )

        def _fmt(d: date) -> str | date:
            return d.strftime(date_fmt) if date_fmt else d

        period_params = [_fmt(current_start), _fmt(current_end), _fmt(baseline_start), _fmt(baseline_end), _fmt(baseline_start), _fmt(current_end)]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {
                    "step_type": step_type,
                    "params": {
                        **params,
                        "metric_name": metric_name,
                        "table_name": qualified_table,
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
        rows = [r for r in execute_compiled(engine, compiled_query).rows if r.get("delta_pct") is not None]

        observations = self.evidence_pipeline.extract_observations(
            "comparison_rows",
            rows,
            context={
                "metric": metric_name,
                "observation_type": obs_type,
                "dimensions": dimensions,
                "payload_fields": {
                    "current_value": "current_value",
                    "baseline_value": "baseline_value",
                    "delta_pct": "delta_pct",
                    "current_sessions": "current_sessions",
                    "baseline_sessions": "baseline_sessions",
                },
                "quality_builder": lambda row: {
                    "freshness_ok": True,
                    "sample_size_ok": min(row["current_sessions"] or 0, row["baseline_sessions"] or 0) >= 150,
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
        step_id = self._new_step_id()

        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)

        row_count_query = self._compile_step_with_feedback(
            AnalysisStepIR(index=0, step_type="profile_table_row_count", params={"table_name": qualified_table}),
            engine_type=engine_type,
        )
        row_count_row = execute_compiled(engine, row_count_query).rows[0]
        row_count = row_count_row["row_count"]

        try:
            columns_query = self._compile_step_with_feedback(
                AnalysisStepIR(
                    index=0,
                    step_type="profile_table_columns",
                    params={"table_name": qualified_table, "short_name": short_name},
                ),
                engine_type=engine_type,
            )
            col_rows = execute_compiled(engine, columns_query).rows
            columns = [r["column_name"] for r in col_rows]
        except Exception:
            columns = []

        # Infer date column + recent value for partition-filtered profiling (Trino)
        profile_date_column: str | None = None
        profile_date_value: str | None = None
        _date_candidates = ("log_date", "event_date", "dt", "date", "day")
        for dc in _date_candidates:
            if dc in [c for c in columns]:
                try:
                    max_row = engine.query_rows(
                        f"SELECT MAX({dc}) AS max_date FROM {qualified_table}"
                    )
                    if max_row and max_row[0].get("max_date") is not None:
                        profile_date_column = dc
                        profile_date_value = str(max_row[0]["max_date"])
                        break
                except Exception:
                    continue

        col_profiles = []
        for col in columns[:20]:  # cap at 20 columns for safety
            try:
                profile_params: dict[str, Any] = {"table_name": qualified_table, "column_name": col}
                if profile_date_column and profile_date_value:
                    profile_params["date_column"] = profile_date_column
                    profile_params["date_value"] = profile_date_value
                stats_query = self._compile_step_with_feedback(
                    AnalysisStepIR(
                        index=0,
                        step_type="profile_table_column_profile",
                        params=profile_params,
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

        profile_scope = None
        if profile_date_column:
            profile_scope = {
                "date_column": profile_date_column,
                "date_value": profile_date_value,
                "scoped_row_count": col_profiles[0]["total"] if col_profiles and "total" in col_profiles[0] else None,
            }
        artifact = {"table_name": table_name, "row_count": row_count, "profile_scope": profile_scope, "columns": col_profiles}
        artifact_id = self._insert_artifact(session_id, step_id, "profile", f"{short_name}_profile", artifact)

        scope_note = f" (column stats scoped to {profile_date_column}={profile_date_value})" if profile_date_column else ""
        summary = f"Table '{table_name}' has {row_count} rows and {len(columns)} columns{scope_note}."
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
            filter: SQL WHERE clause expression (e.g. "status = 'active'")
            columns: list of column names to select (default: all)
        """
        table_name = params.get("table_name")
        if not table_name:
            raise ValueError("sample_rows requires 'table_name' param")

        # Merge session constraints into filter
        constraints_filter = self._session_constraints_to_filter(session_id)
        user_filter = params.get("filter")
        merged_filter = self._merge_filters(user_filter, constraints_filter)
        if merged_filter:
            params = {**params, "filter": merged_filter}

        step_type = "sample_rows"
        step_id = self._new_step_id()

        limit = int(params.get("limit", 10))
        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)

        # Build compiler params with filter/columns passthrough
        compiler_params: dict[str, Any] = {"table_name": qualified_table, "limit": limit}

        if params.get("filter"):
            compiler_params["filter"] = params["filter"]
        if params.get("columns"):
            compiler_params["columns"] = params["columns"]

        # Auto-detect partition column for Trino-like engines (same logic as profile_table)
        if not params.get("filter") and not params.get("date_column"):
            _date_candidates = ("log_date", "event_date", "dt", "date", "day")
            try:
                col_query = self._compile_step_with_feedback(
                    AnalysisStepIR(
                        index=0,
                        step_type="profile_table_columns",
                        params={"table_name": qualified_table, "short_name": short_name},
                    ),
                    engine_type=engine_type,
                )
                col_rows = execute_compiled(engine, col_query).rows
                columns_list = [r["column_name"] for r in col_rows]
                for dc in _date_candidates:
                    if dc in columns_list:
                        try:
                            max_row = engine.query_rows(
                                f"SELECT MAX({dc}) AS max_date FROM {qualified_table}"
                            )
                            if max_row and max_row[0].get("max_date") is not None:
                                compiler_params["date_column"] = dc
                                compiler_params["date_value"] = str(max_row[0]["max_date"])
                                break
                        except Exception:
                            continue
            except Exception:
                pass
        elif params.get("date_column"):
            compiler_params["date_column"] = params["date_column"]
            if params.get("date_value"):
                compiler_params["date_value"] = params["date_value"]
            elif params.get("period_end"):
                compiler_params["date_value"] = params["period_end"]

        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {"step_type": step_type, "params": compiler_params},
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

    def _run_aggregate_query(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run an ad-hoc GROUP BY + aggregation query.

        Required params:
            table_name: fully qualified table name
            select: list of SQL expressions (e.g. ["platform", "count(*) as cnt"])
            group_by: list of column names to group by
        Optional params:
            where: SQL WHERE clause expression
            order_by: SQL ORDER BY expression
            limit: max rows (default: 100)
        """
        table_name = params.get("table_name")
        if not table_name:
            raise ValueError("aggregate_query requires 'table_name' param")
        if not params.get("select"):
            raise ValueError("aggregate_query requires 'select' param")
        if not params.get("group_by"):
            raise ValueError("aggregate_query requires 'group_by' param")

        # Merge session constraints into where clause
        constraints_filter = self._session_constraints_to_filter(session_id)
        user_where = params.get("where")
        merged_where = self._merge_filters(user_where, constraints_filter)
        if merged_where:
            params = {**params, "where": merged_where}

        step_type = "aggregate_query"
        step_id = self._new_step_id()
        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)

        compiler_params: dict[str, Any] = {
            "table_name": qualified_table,
            "select": params["select"],
            "group_by": params["group_by"],
        }
        if params.get("where"):
            compiler_params["where"] = params["where"]
        if params.get("order_by"):
            compiler_params["order_by"] = params["order_by"]
        if params.get("limit"):
            compiler_params["limit"] = params["limit"]

        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(0, {"step_type": step_type, "params": compiler_params}),
            engine_type=engine_type,
        )
        rows = execute_compiled(engine, compiled_query).rows

        # Extract observations from aggregate rows (opt-out via extract_observations=false)
        if params.get("extract_observations", True):
            group_by = params.get("group_by", [])
            observations = self.evidence_pipeline.extract_observations(
                "aggregate_rows",
                rows,
                context={
                    "group_by": group_by,
                    "observation_type": params.get("observation_type", "metric_change"),
                    "metric": params.get("metric", "aggregate"),
                    "value_column": params.get("value_column"),
                },
            )
            for observation in observations:
                self._insert_observation(session_id, step_id, observation)
        else:
            observations = []

        artifact_id = self._insert_artifact(session_id, step_id, "aggregate", f"{short_name}_aggregate", rows)
        summary = f"Aggregate query on '{table_name}' returned {len(rows)} rows."
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "rows": rows}
        if observations:
            result["observations"] = observations
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_synthesis(self, session_id: str) -> dict[str, Any]:
        step_type = "synthesize_findings"
        step_id = self._new_step_id()
        observations = self._load_observations(session_id)
        tentative_claims = self._load_tentative_claims(session_id)

        if tentative_claims:
            # M-03 PROMOTION MODE: tentative claims exist from IncrementalSynthesizer.
            # Clear any confirmed/insufficient claims and recs/edges from prior synthesis,
            # then promote tentative claims to confirmed or insufficient.
            self._delete_non_tentative_synthesis_outputs(session_id)
            self.metadata.execute(
                "DELETE FROM steps WHERE session_id = ? AND step_type = ?",
                [session_id, step_type],
            )
            promoted = self._promote_claims(session_id, tentative_claims, observations)
            synthesis = self.evidence_pipeline.build_synthesis(
                observations,
                existing_claims=promoted,
            )
            # Claims already in DB (promoted in place); only insert recs + edges.
            for recommendation in synthesis["recommendations"]:
                self._insert_recommendation(session_id, recommendation)
            for edge in synthesis["edges"]:
                self._insert_edge(session_id, **edge)
        else:
            # FALLBACK MODE: no IncrementalSynthesizer in use — from-scratch synthesis.
            self._delete_step_outputs(session_id, step_type)
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

    def _load_tentative_claims(self, session_id: str) -> list[dict[str, Any]]:
        """Return all tentative claims for a session (created by IncrementalSynthesizer)."""
        rows = self.metadata.query_rows(
            """
            SELECT claim_id, claim_type, text, scope_json, confidence, status,
                   supporting_observation_ids_json, contradicting_observation_ids_json,
                   confidence_breakdown_json, inference_level, inference_justification_json
            FROM claims
            WHERE session_id = ? AND status = 'tentative'
            ORDER BY created_at
            """,
            [session_id],
        )
        result = []
        for row in rows:
            claim = dict(row)
            claim["type"] = claim.pop("claim_type")
            claim["scope"] = json.loads(claim.pop("scope_json"))
            claim["supporting_observations"] = json.loads(
                claim.pop("supporting_observation_ids_json")
            )
            claim["contradicting_observations"] = json.loads(
                claim.pop("contradicting_observation_ids_json")
            )
            claim["confidence_breakdown"] = json.loads(claim.pop("confidence_breakdown_json"))
            claim["inference_justification"] = json.loads(
                claim.pop("inference_justification_json")
            )
            result.append(claim)
        return result

    def _delete_non_tentative_synthesis_outputs(self, session_id: str) -> None:
        """Delete confirmed/insufficient claims + recommendations + edges from a previous
        synthesize_findings run, but preserve tentative claims created by IncrementalSynthesizer."""
        self.metadata.execute(
            "DELETE FROM claims WHERE session_id = ? AND status != 'tentative'",
            [session_id],
        )
        self.metadata.execute("DELETE FROM recommendations WHERE session_id = ?", [session_id])
        self.metadata.execute("DELETE FROM evidence_edges WHERE session_id = ?", [session_id])

    def _promote_claims(
        self,
        session_id: str,
        tentative_claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Promote tentative claims to confirmed or insufficient and return promoted list.

        Promotion criteria:
        - confidence >= 0.5 AND no contradicting observations → ``confirmed``
        - otherwise → ``insufficient``
        """
        obs_map = {o["observation_id"]: o for o in observations}
        promoted: list[dict[str, Any]] = []
        for claim in tentative_claims:
            has_contradictions = bool(claim["contradicting_observations"])
            new_status = (
                "confirmed"
                if claim["confidence"] >= 0.5 and not has_contradictions
                else "insufficient"
            )
            self.metadata.execute(
                "UPDATE claims SET status = ? WHERE claim_id = ?",
                [new_status, claim["claim_id"]],
            )
            promoted.append({**claim, "status": new_status})
        return promoted

    def _assert_session_exists(self, session_id: str) -> None:
        self.session_manager.assert_session_exists(session_id)

    _TEMPORAL_DIMENSIONS: frozenset[str] = frozenset({
        "log_date", "event_date", "dt", "date", "day",
        "log_hour", "event_hour", "hour", "minute",
        "event_time", "timestamp", "ts",
    })

    _MAX_DEFAULT_DIMENSIONS: int = 2

    @staticmethod
    def _infer_date_column(dimensions: list[str]) -> str:
        """Infer the date column from a metric's semantic dimensions.

        Checks for common date column names in priority order and falls back
        to ``event_date`` when no match is found.
        """
        candidates = ("log_date", "event_date", "dt", "date", "day")
        for candidate in candidates:
            if candidate in dimensions:
                return candidate
        return "event_date"

    @staticmethod
    def _comparison_dimensions(
        all_dimensions: list[str],
        date_column: str,
        *,
        requested: list[str] | None = None,
    ) -> list[str]:
        """Select dimensions suitable for a comparison GROUP BY.

        * Always excludes *date_column* (grouping by the period-splitting
          column produces NULL pivots).
        * When the caller supplied explicit *requested* dimensions, only
          *date_column* is removed — the caller made a deliberate choice.
        * When no explicit dimensions are requested, all temporal
          dimensions (``_TEMPORAL_DIMENSIONS``) are stripped and the result
          is capped at ``_MAX_DEFAULT_DIMENSIONS``.
        """
        if requested:
            return [d for d in requested if d != date_column]

        excluded = SemanticLayerService._TEMPORAL_DIMENSIONS | {date_column}
        dims = [d for d in all_dimensions if d not in excluded]
        return dims[:SemanticLayerService._MAX_DEFAULT_DIMENSIONS]

    @staticmethod
    def _detect_date_format(raw_value: Any) -> str | None:
        """Detect whether a raw date value is YYYYMMDD or ISO format.

        Returns a strftime format string if the value is a compact date
        string, or ``None`` for native DATE / ISO strings.
        """
        if isinstance(raw_value, str) and len(raw_value) == 8 and raw_value.isdigit():
            return "%Y%m%d"
        return None

    def _period_bounds(
        self,
        engine: AnalyticsEngine | None = None,
        table_name: str = "analytics.watch_events",
        date_column: str = "event_date",
    ) -> tuple[date, date, date, date, str | None]:
        """Compute current and baseline period boundaries.

        Returns ``(current_start, current_end, baseline_start, baseline_end, date_fmt)``
        where *date_fmt* is a strftime pattern (e.g. ``'%Y%m%d'``) when
        the column stores dates as compact strings, or ``None`` when
        native DATE / ISO strings are used.  Callers must apply the
        format when building parameterised queries.
        """
        engine = engine or self.analytics
        try:
            row = engine.query_rows(
                f"SELECT MAX({date_column}) AS max_date FROM {table_name}"
            )[0]
        except Exception:
            # Trino clusters may require a partition filter on date columns.
            # Fall back to a bounded query covering the last 90 days using
            # both YYYYMMDD and YYYY-MM-DD formats so the filter works
            # regardless of the column's storage format.
            cutoff = date.today() - timedelta(days=90)
            cutoff_compact = cutoff.strftime("%Y%m%d")
            cutoff_iso = cutoff.isoformat()
            row = engine.query_rows(
                f"SELECT MAX({date_column}) AS max_date FROM {table_name} "
                f"WHERE {date_column} >= '{cutoff_compact}' "
                f"OR {date_column} >= '{cutoff_iso}'"
            )[0]
        raw_max = row["max_date"]
        date_fmt = self._detect_date_format(raw_max)
        current_end = raw_max
        if isinstance(current_end, str):
            current_end = date.fromisoformat(current_end)
        current_start = current_end - timedelta(days=13)
        baseline_end = current_start - timedelta(days=1)
        baseline_start = baseline_end - timedelta(days=13)
        return current_start, current_end, baseline_start, baseline_end, date_fmt

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
        return []

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
                supporting_observation_ids_json, contradicting_observation_ids_json, confidence_breakdown_json,
                inference_level, inference_justification_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                claim.get("inference_level", "L0"),
                self._dump(claim.get("inference_justification", [])),
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


class _ServiceWorkflowStepExecutor:
    def __init__(self, service: SemanticLayerService) -> None:
        self._service = service

    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]:
        return self._service.run_step(
            session_id,
            step_ir.step_type,
            params=step_ir.params if step_ir.params else None,
        )

    def attach_replanning_provenance(
        self,
        session_id: str,
        step_type: str,
        decisions: list[dict[str, Any]],
    ) -> None:
        self._service._attach_replanning_provenance(session_id, step_type, decisions)
