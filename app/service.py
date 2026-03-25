from __future__ import annotations

import hashlib
import json
import math
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
from app.evidence_engine import EvidencePipeline
from app.evidence_engine.claim_relations import (
    _claim_direction,
    _complementary_dimension,
    _is_subset,
    _shared_values,
    _slice_dict,
)
from app.evidence_engine.causal_checkers import _pearson_correlation, _rank, _spearman_correlation
from app.evidence_engine.synthesizers.default import DefaultClaimSynthesizer
from app.evidence_engine.readiness import compute_readiness, load_live_claims
from app.evidence_engine.schemas import ALL_EDGE_TYPES, EDGE_TYPE_JUSTIFIES
from app.execution.feedback import compile_failure_from_error
from app.execution.orchestrator import WorkflowOrchestrator
from app.execution.routing_runtime import RoutingRuntime
from app.planner import ReplanningService
from app.semantic_runtime import SemanticRuntimeRepository
from app.session import SessionManager
from app.storage.analytics import AnalyticsEngine
from app.storage.metadata import MetadataStore
from app.time_scope import AdHocAggregateValueSpec
from app.time_scope import ResolvedWindowedQueryRequest
from app.time_scope import SemanticMetricValueSpec
from app.time_scope import normalize_aggregate_query_request
from app.time_scope import normalize_compare_metric_request

if TYPE_CHECKING:
    from app.approvals import ApprovalService
    from app.governance import GovernanceService
    from app.observability import MetricsCollector
    from app.routing import QueryRouter


_AUTO_INCREMENTAL_SYNTHESIZER = object()


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
        incremental_synthesizer: Any | None = _AUTO_INCREMENTAL_SYNTHESIZER,
    ) -> None:
        self.metadata = metadata_store
        self.analytics = analytics_engine
        self._query_router = query_router
        self.governance = governance
        self.metrics = metrics
        self.approvals = approvals
        self.session_manager = SessionManager(metadata_store)
        self.step_registry = build_service_step_registry(self)
        self._default_synthesizer = DefaultClaimSynthesizer()
        self.semantic_repository = SemanticRuntimeRepository(metadata_store)
        self.semantic_resolver = self.semantic_repository.resolver
        self.evidence_pipeline = EvidencePipeline(
            self._default_synthesizer,
            metric_direction_resolver=self._resolve_metric_direction,
        )
        self.planner_context_provider = self.semantic_repository.planner_context_provider
        self.workflow_runtime = CompositeWorkflowRuntime()
        self.replanner = replanner or ReplanningService(
            analytics_engine=analytics_engine,
            query_router=query_router,
        )
        if incremental_synthesizer is _AUTO_INCREMENTAL_SYNTHESIZER:
            from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer

            incremental_synthesizer = IncrementalSynthesizer(metadata_store)
        self._incremental_synthesizer: Any | None = incremental_synthesizer
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
        raw_filter: str | None = None,
    ) -> dict[str, Any]:
        return self.session_manager.create_session(goal, constraints, budget, policy, raw_filter=raw_filter)

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

        result["constraints_applied"] = self._build_constraints_applied(session_id, normalized)

        return result

    def get_evidence_graph(
        self,
        session_id: str,
        *,
        claims_only: str | None = None,
        edge_types: list[str] | None = None,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        if claims_only not in {None, "confirmed"}:
            raise ValueError("claims_only currently supports only 'confirmed'")
        if edge_types:
            invalid_edge_types = sorted({edge_type for edge_type in edge_types if edge_type not in ALL_EDGE_TYPES})
            if invalid_edge_types:
                raise ValueError(
                    "Unknown edge_types: " + ", ".join(invalid_edge_types)
                )

        graph = self._load_evidence_graph_components(session_id)
        filtered = self._filter_evidence_graph(
            graph,
            claims_only=claims_only,
            edge_types=edge_types,
        )
        if include_debug:
            filtered["debug"] = self._build_session_debug_payload(
                session_id,
                filtered["observations"],
                filtered["claims"],
                filtered["edges"],
            )
        return filtered

    def get_session_debug(self, session_id: str) -> dict[str, Any]:
        self._assert_session_exists(session_id)
        graph = self._load_evidence_graph_components(session_id)
        return self._build_session_debug_payload(
            session_id,
            graph["observations"],
            graph["claims"],
            graph["edges"],
        )

    def _load_evidence_graph_components(self, session_id: str) -> dict[str, Any]:
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
            SELECT edge_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight, explanation,
                   match_basis_json, score_components_json, supporting_observation_ids_json
            FROM evidence_edges
            WHERE session_id = ?
            ORDER BY created_at
            """,
            [session_id],
        )
        recommendations = self.metadata.query_rows(
            """
            SELECT rec_id, type, claim_id, action_text, priority, expected_impact, risk,
                   template_id, validation_metric_json, causal_basis_json, entity_patch_json,
                   supporting_claims_json
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
            raw_cb = recommendation.pop("causal_basis_json")
            recommendation["causal_basis"] = json.loads(raw_cb) if raw_cb is not None else None
            raw_ep = recommendation.pop("entity_patch_json", None)
            recommendation["entity_patch"] = json.loads(raw_ep) if raw_ep is not None else None
            raw_sc = recommendation.pop("supporting_claims_json", None)
            recommendation["supporting_claims"] = json.loads(raw_sc) if raw_sc is not None else None
            recommendation["action"] = recommendation["action_text"]  # alias for agent compatibility
        for edge in edges:
            edge["match_basis"] = json.loads(edge.pop("match_basis_json") or "{}")
            edge["score_components"] = json.loads(edge.pop("score_components_json") or "{}")
            edge["supporting_observation_ids"] = json.loads(
                edge.pop("supporting_observation_ids_json") or "[]"
            )

        return {
            "session_id": session_id,
            "steps": steps,
            "observations": observations,
            "claims": claims,
            "edges": edges,
            "recommendations": recommendations,
        }

    def _filter_evidence_graph(
        self,
        graph: dict[str, Any],
        *,
        claims_only: str | None,
        edge_types: list[str] | None,
    ) -> dict[str, Any]:
        claims = list(graph["claims"])
        if claims_only == "confirmed":
            claims = [claim for claim in claims if claim.get("status") == "confirmed"]
        kept_claim_ids = {claim["claim_id"] for claim in claims}
        enforce_claim_subgraph = claims_only is not None

        edges = list(graph["edges"])
        if edge_types:
            allowed_edge_types = set(edge_types)
            edges = [edge for edge in edges if edge.get("edge_type") in allowed_edge_types]

        if enforce_claim_subgraph:
            edges = [
                edge
                for edge in edges
                if self._edge_survives_claim_filter(edge, kept_claim_ids)
            ]

        recommendations: list[dict[str, Any]] = []
        for recommendation in graph["recommendations"]:
            primary_claim_id = recommendation.get("claim_id")
            if enforce_claim_subgraph and primary_claim_id not in kept_claim_ids:
                continue

            supporting_claims = recommendation.get("supporting_claims")
            updated = dict(recommendation)
            if enforce_claim_subgraph and supporting_claims is not None:
                trimmed_support = [claim_id for claim_id in supporting_claims if claim_id in kept_claim_ids]
                if not trimmed_support:
                    continue
                updated["supporting_claims"] = trimmed_support
            recommendations.append(updated)

        return {
            "session_id": graph["session_id"],
            "steps": list(graph["steps"]),
            "observations": list(graph["observations"]),
            "claims": claims,
            "edges": edges,
            "recommendations": recommendations,
        }

    @staticmethod
    def _edge_survives_claim_filter(edge: dict[str, Any], kept_claim_ids: set[str]) -> bool:
        from_type = edge.get("from_node_type")
        to_type = edge.get("to_node_type")
        from_id = edge.get("from_node_id")
        to_id = edge.get("to_node_id")
        if from_type == "claim" and from_id not in kept_claim_ids:
            return False
        if to_type == "claim" and to_id not in kept_claim_ids:
            return False
        return True

    def _build_session_debug_payload(
        self,
        session_id: str,
        observations: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        relations = self._extract_persisted_relations(edges)
        relation_debug = self._summarize_relation_discovery(claims, observations, relations)
        checker_logs = self._summarize_checker_runs(claims, observations, edges, relations)
        return {
            "session_id": session_id,
            "relation_discovery": relation_debug,
            "checker_logs": checker_logs,
        }

    @staticmethod
    def _extract_persisted_relations(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "from_claim_id": edge.get("from_node_id"),
                "to_claim_id": edge.get("to_node_id"),
                "relation_type": edge.get("edge_type"),
                "weight": edge.get("weight", 0.0),
                "match_basis": edge.get("match_basis", {}),
                "score_components": edge.get("score_components", {}),
                "supporting_observation_ids": edge.get("supporting_observation_ids", []),
                "explanation": edge.get("explanation", ""),
            }
            for edge in edges
            if edge.get("from_node_type") == "claim"
            and edge.get("to_node_type") == "claim"
            and edge.get("edge_type") == "correlates_with"
        ]

    def _summarize_relation_discovery(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        confirmed_claims = [claim for claim in claims if claim.get("status") == "confirmed"]
        observation_by_id = {
            str(observation.get("observation_id")): observation
            for observation in observations
            if observation.get("observation_id")
        }

        reasons: dict[str, int] = {}
        pair_samples: list[dict[str, Any]] = []
        candidate_pairs_checked = 0
        relation_keys = {
            (
                relation.get("from_claim_id"),
                relation.get("to_claim_id"),
                relation.get("relation_type"),
            )
            for relation in relations
        }

        for left_index in range(len(confirmed_claims)):
            for right_index in range(left_index + 1, len(confirmed_claims)):
                claim_a = confirmed_claims[left_index]
                claim_b = confirmed_claims[right_index]
                candidate_pairs_checked += 1
                reason_code, sample = self._explain_claim_pair(claim_a, claim_b, observation_by_id, relation_keys)
                reasons[reason_code] = reasons.get(reason_code, 0) + 1
                if len(pair_samples) < 8:
                    pair_samples.append(sample)

        if not confirmed_claims:
            reasons = {"not_enough_confirmed_claims": 1}
        elif candidate_pairs_checked == 0:
            reasons = {"not_enough_confirmed_claims": 1}

        return {
            "claims_considered": len(claims),
            "confirmed_claims_considered": len(confirmed_claims),
            "candidate_pairs_checked": candidate_pairs_checked,
            "relations_emitted": len(relations),
            "reasons": reasons,
            "pair_samples": pair_samples,
        }

    def _explain_claim_pair(
        self,
        claim_a: dict[str, Any],
        claim_b: dict[str, Any],
        observation_by_id: dict[str, dict[str, Any]],
        relation_keys: set[tuple[str | None, str | None, str | None]],
    ) -> tuple[str, dict[str, Any]]:
        scope_a = claim_a.get("scope", {}) or {}
        scope_b = claim_b.get("scope", {}) or {}
        metric_a = str(scope_a.get("metric", "") or "")
        metric_b = str(scope_b.get("metric", "") or "")
        slice_a = _slice_dict(scope_a)
        slice_b = _slice_dict(scope_b)
        direction_a = _claim_direction(claim_a, observation_by_id)
        direction_b = _claim_direction(claim_b, observation_by_id)

        shared_keys = sorted(set(slice_a).intersection(slice_b))
        exact = slice_a == slice_b
        subset = _is_subset(slice_a, slice_b) or _is_subset(slice_b, slice_a)
        overlap_values = _shared_values(slice_a, slice_b)
        overlap = bool(shared_keys) and bool(overlap_values)
        complementary = _complementary_dimension(slice_a, slice_b)

        emitted = False
        category: str | None = None
        if direction_a is not None and direction_a == direction_b:
            if metric_a and metric_b and metric_a != metric_b and exact:
                emitted = True
                category = "exact_match"
            elif metric_a and metric_b and metric_a != metric_b and (subset or overlap):
                emitted = True
                category = "subset_or_overlap"
            elif metric_a and metric_a == metric_b and complementary:
                emitted = True
                category = "complementary_dimension"

        ordered_claims = sorted(
            [claim_a, claim_b],
            key=lambda claim: (
                str(claim.get("scope", {}).get("metric", "")),
                sorted((claim.get("scope", {}).get("slice", {}) or {}).items()),
                str(claim.get("claim_id", "")),
            ),
        )
        relation_key = (
            ordered_claims[0].get("claim_id"),
            ordered_claims[1].get("claim_id"),
            "correlates_with",
        )
        if emitted and relation_key in relation_keys:
            reason_code = "relation_emitted"
        elif direction_a is None or direction_b is None or direction_a != direction_b:
            reason_code = "no_directional_consistency"
        elif not overlap and not exact and not subset and not complementary:
            reason_code = "no_scope_overlap"
        else:
            reason_code = "unsupported_relation_category"

        return reason_code, {
            "from_claim_id": claim_a.get("claim_id"),
            "to_claim_id": claim_b.get("claim_id"),
            "reason_code": reason_code,
            "match_basis": {
                "category": category,
                "shared_scope_keys": shared_keys,
                "shared_scope_values": overlap_values,
                "left_metric": metric_a,
                "right_metric": metric_b,
                "direction_left": direction_a,
                "direction_right": direction_b,
            },
        }

    def _summarize_checker_runs(
        self,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checker_specs = [
            ("CrossSliceConsistencyChecker", "cross_slice_consistency"),
            ("CrossScopeCorrelationChecker", "cross_scope_correlation"),
            ("CrossMetricCorrelationChecker", "cross_metric_correlation"),
            ("MechanisticExplanationChecker", "mechanistic_explanation"),
            ("TemporalPrecedenceChecker", "temporal_precedence"),
            ("DoseResponseChecker", "dose_response"),
            ("ReversalChecker", "reversal"),
        ]
        checker_logs: list[dict[str, Any]] = []
        for checker_class_name, checker_name in checker_specs:
            persisted = self._persisted_checker_contributions(
                checker_name,
                claims,
                edges,
            )
            reason_code, reason = self._checker_reason_summary(
                checker_name,
                claims,
                observations,
                edges,
                relations,
                persisted,
            )
            checker_logs.append(
                {
                    "checker": checker_class_name,
                    "checker_name": checker_name,
                    "claims_checked": self._claims_checked_for_checker(checker_name, claims),
                    "result": "upgrade" if persisted["claims"] or persisted["edges"] else "no_upgrade",
                    "reason_code": reason_code,
                    "reason": reason,
                    "claims_upgraded": len(persisted["claims"]),
                    "causal_edges_emitted": len(persisted["edges"]),
                    "claim_ids": persisted["claims"],
                    "edge_types": persisted["edge_types"],
                }
            )
        return checker_logs

    def _persisted_checker_contributions(
        self,
        checker_name: str,
        claims: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        token_prefixes: dict[str, tuple[str, ...]] = {
            "cross_slice_consistency": ("cross_slice_consistency:",),
            "cross_scope_correlation": ("cross_scope_explicit:", "cross_scope_temporal:"),
            "cross_metric_correlation": ("cross_metric_consistency:",),
            "mechanistic_explanation": ("mechanistic_explanation:",),
            "temporal_precedence": ("temporal_precedence:",),
            "dose_response": ("dose_response:", "dose_response_precomputed:"),
            "reversal": ("reversal:",),
        }
        checker_edge_types: dict[str, tuple[str, ...]] = {
            "cross_scope_correlation": ("correlates_with",),
            "temporal_precedence": ("temporally_precedes",),
            "mechanistic_explanation": ("mechanistically_explains",),
            "dose_response": (),
            "reversal": (),
            "cross_slice_consistency": (),
            "cross_metric_correlation": (),
        }

        prefixes = token_prefixes.get(checker_name, ())
        claim_ids = [
            claim["claim_id"]
            for claim in claims
            if any(
                any(str(token).startswith(prefix) for prefix in prefixes)
                for token in claim.get("inference_justification", [])
            )
        ]
        edge_types = checker_edge_types.get(checker_name, ())
        checker_edges = [
            edge
            for edge in edges
            if edge.get("from_node_type") in {"claim", "observation"}
            and edge.get("to_node_type") == "claim"
            and edge.get("edge_type") in edge_types
        ]
        return {
            "claims": claim_ids,
            "edges": checker_edges,
            "edge_types": sorted({str(edge.get("edge_type")) for edge in checker_edges}),
        }

    @staticmethod
    def _claims_checked_for_checker(checker_name: str, claims: list[dict[str, Any]]) -> int:
        if checker_name in {"cross_metric_correlation", "temporal_precedence"}:
            return len([claim for claim in claims if claim.get("status") == "confirmed"])
        return len(claims)

    def _checker_reason_summary(
        self,
        checker_name: str,
        claims: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        persisted: dict[str, Any],
    ) -> tuple[str, str]:
        if persisted["claims"] or persisted["edges"]:
            parts: list[str] = []
            if persisted["claims"]:
                parts.append(f"{len(persisted['claims'])} claims carry this checker's justification tokens")
            if persisted["edges"]:
                parts.append(f"{len(persisted['edges'])} causal edges from this checker are present")
            return "already_materialized", "; ".join(parts) + "."

        if checker_name == "cross_slice_consistency":
            metrics_with_deltas: dict[str, int] = {}
            for observation in observations:
                metric = observation.get("subject", {}).get("metric")
                if not metric or observation.get("payload", {}).get("delta_pct") is None:
                    continue
                metrics_with_deltas[str(metric)] = metrics_with_deltas.get(str(metric), 0) + 1
            if not any(count >= 2 for count in metrics_with_deltas.values()):
                return (
                    "insufficient_cross_slice_observations",
                    "No metric has at least two delta-bearing observations across slices.",
                )
            return (
                "no_directional_consistency",
                "Observed slice deltas do not meet the consistency threshold for promotion.",
            )

        if checker_name == "cross_scope_correlation":
            causal_candidates = [obs for obs in observations if obs.get("type") == "causal_candidate"]
            windowed_obs = [obs for obs in observations if obs.get("observed_window") is not None]
            if not causal_candidates and len(windowed_obs) < 2:
                return (
                    "missing_observed_window",
                    "Cross-scope correlation needs explicit causal candidates or at least two real observed windows.",
                )
            return (
                "no_matching_relations",
                "No explicit causal candidate or temporal predecessor matched a claim strongly enough.",
            )

        if checker_name == "cross_metric_correlation":
            if not relations:
                return (
                    "no_cross_metric_relation",
                    "No persisted claim-to-claim correlates_with edges were available for cross-metric grouping.",
                )
            return (
                "unsupported_relation_category",
                "Available relations did not form an eligible same-direction multi-metric component.",
            )

        if checker_name == "temporal_precedence":
            if not relations:
                return (
                    "no_matching_relations",
                    "Temporal precedence only evaluates relation-backed claim pairs; none are present in this graph.",
                )
            if not any(observation.get("observed_window") is not None for observation in observations):
                return (
                    "missing_observed_window",
                    "No supporting observations carried real observed_window values.",
                )
            return (
                "no_non_overlapping_windows",
                "Related claims did not establish a strict non-overlapping time precedence.",
            )

        if checker_name == "mechanistic_explanation":
            if not any(edge.get("edge_type") == "correlates_with" for edge in edges):
                return (
                    "no_matching_relations",
                    "No correlates_with edge is present to support a mechanistic promotion.",
                )
            return (
                "no_mechanistic_signal",
                "No mechanistic explanation signal linked claim relations to an identified causal pathway.",
            )

        if checker_name == "dose_response":
            return (
                "no_numeric_gradient",
                "No eligible numeric dimension showed a strong monotonic dose-response pattern.",
            )

        if checker_name == "reversal":
            return (
                "no_temporal_reversal",
                "No sustained multi-period reversal pattern was detected.",
            )

        return ("no_upgrade", "Checker ran without producing an inference upgrade.")

    # ── Metric resolution ────────────────────────────────────────────

    def _resolve_metric_direction(self, metric_name: str) -> str | None:
        """Look up a published metric's desired_direction for recommendation policy."""
        resolved = self.semantic_resolver.resolve_metric(metric_name)
        return resolved.desired_direction if resolved else None

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
        """Convert session constraints and raw_filter to a SQL filter expression.

        Non-scalar constraints (dicts, lists) are silently ignored.
        raw_filter is appended as-is (AND-merged) after scalar constraints.
        Returns None when no constraints exist.
        """
        session = self.get_session(session_id)
        constraints = session.get("constraints", {})
        parts: list[str] = []
        if constraints and isinstance(constraints, dict):
            for key, value in constraints.items():
                if isinstance(value, (dict, list)):
                    continue
                parts.append(f"{key} = '{value}'")
        raw_filter = session.get("raw_filter")
        if raw_filter:
            parts.append(raw_filter)
        return " AND ".join(parts) if parts else None

    @staticmethod
    def _merge_filters(*filters: str | None) -> str | None:
        """AND-merge multiple filter expressions, ignoring None values."""
        parts = [f for f in filters if f]
        if not parts:
            return None
        return " AND ".join(f"({p})" for p in parts)

    @staticmethod
    def _constraints_dict_to_filter(constraints: dict[str, Any]) -> str | None:
        parts: list[str] = []
        for key, value in constraints.items():
            if isinstance(value, (dict, list)):
                continue
            parts.append(f"{key} = '{value}'")
        return " AND ".join(parts) if parts else None

    def _resolved_scope_filter(
        self,
        session_id: str,
        request: ResolvedWindowedQueryRequest,
    ) -> str | None:
        session_filter = self._session_constraints_to_filter(session_id)
        scope_constraints = self._constraints_dict_to_filter(request.scope.constraints)
        return self._merge_filters(session_filter, scope_constraints, request.scope.predicate)

    @staticmethod
    def _day_window_to_legacy_bounds(start: str, end: str) -> tuple[str, str]:
        start_day = date.fromisoformat(start[:10]).isoformat()
        end_day = (date.fromisoformat(end[:10]) - timedelta(days=1)).isoformat()
        return start_day, end_day

    def _bridge_compare_metric_request(
        self,
        session_id: str,
        request: ResolvedWindowedQueryRequest,
        metric_dimensions: list[str],
    ) -> dict[str, Any]:
        if request.time_scope.grain != "day":
            raise ValueError("compare_metric hour-grain execution is not implemented yet")
        if request.time_scope.mode != "compare" or request.time_scope.baseline is None:
            raise ValueError("compare_metric currently requires time_scope.mode='compare' for execution")
        if not isinstance(request.value_spec, SemanticMetricValueSpec):
            raise ValueError("compare_metric requires a semantic metric value_spec")

        date_column = (
            request.resolved_time_axis.override_analysis_time_column
            or self._infer_date_column(metric_dimensions)
        )
        period_start, period_end = self._day_window_to_legacy_bounds(
            request.time_scope.current.start,
            request.time_scope.current.end,
        )
        baseline_start, baseline_end = self._day_window_to_legacy_bounds(
            request.time_scope.baseline.start,
            request.time_scope.baseline.end,
        )
        return {
            "metric_name": request.value_spec.metric,
            "table_name": request.table,
            "dimensions": list(request.grouping),
            "date_column": date_column,
            "period_start": period_start,
            "period_end": period_end,
            "baseline_start": baseline_start,
            "baseline_end": baseline_end,
            "filter": self._resolved_scope_filter(session_id, request),
            "limit": request.limit or 10,
            "order": self._normalize_compare_metric_order(request.order),
        }

    @staticmethod
    def _normalize_compare_metric_order(order: str | None) -> str | None:
        if order is None:
            return None
        normalized = order.strip().upper()
        if normalized in {"ASC", "DESC"}:
            return normalized
        if normalized in {"DELTA_PCT ASC", "DELTA_PCT DESC"}:
            return normalized.split()[-1]
        raise ValueError("compare_metric order currently supports only delta_pct ASC/DESC")

    def _bridge_aggregate_query_request(
        self,
        session_id: str,
        request: ResolvedWindowedQueryRequest,
    ) -> dict[str, Any]:
        if not isinstance(request.value_spec, AdHocAggregateValueSpec):
            raise ValueError("aggregate_query requires an ad-hoc aggregate value_spec")
        if request.time_scope.grain != "day":
            raise ValueError("aggregate_query hour-grain execution is not implemented yet")

        select_exprs = list(request.grouping) + [
            f"{measure.expr} AS {measure.alias}" for measure in request.value_spec.measures
        ]
        date_column = request.resolved_time_axis.override_analysis_time_column or "event_date"
        merged_where = self._resolved_scope_filter(session_id, request)
        bridged: dict[str, Any] = {
            "table_name": request.table,
            "select": select_exprs,
            "group_by": list(request.grouping),
            "metric": request.value_spec.measures[0].alias if request.value_spec.measures else "aggregate",
            "limit": request.limit or 100,
        }
        if request.order:
            # The typed contract uses `order`; the legacy aggregate compiler still expects `order_by`.
            bridged["order_by"] = request.order

        if request.time_scope.mode == "compare":
            if request.time_scope.baseline is None:
                raise ValueError("aggregate_query compare mode requires baseline")
            if not request.grouping:
                raise ValueError("aggregate_query compare mode currently requires non-empty group_by")
            period_start, period_end = self._day_window_to_legacy_bounds(
                request.time_scope.current.start,
                request.time_scope.current.end,
            )
            baseline_start, baseline_end = self._day_window_to_legacy_bounds(
                request.time_scope.baseline.start,
                request.time_scope.baseline.end,
            )
            bridged.update({
                "compare_period": True,
                "date_column": date_column,
                "period_start": period_start,
                "period_end": period_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
            })
            if merged_where:
                bridged["where"] = merged_where
            return bridged

        window_filter = (
            f"{date_column} >= '{request.time_scope.current.start[:10]}' "
            f"AND {date_column} < '{request.time_scope.current.end[:10]}'"
        )
        bridged["where"] = self._merge_filters(merged_where, window_filter)
        return bridged

    _CONSTRAINT_APPLYING_STEPS = frozenset({"compare_metric", "sample_rows", "aggregate_query", "attribute_change"})

    _CONSTRAINT_SKIP_REASONS: dict[str, str] = {
        "profile_table": "profile_table scans the full table; session filters are not applied",
        "synthesize_findings": "synthesize_findings operates on existing evidence, not raw data",
    }

    def _build_constraints_applied(self, session_id: str, step_type: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        constraints = session.get("constraints") or {}
        raw_filter = session.get("raw_filter")

        descriptors: list[str] = []
        if constraints and isinstance(constraints, dict):
            for key, value in constraints.items():
                if not isinstance(value, (dict, list)):
                    descriptors.append(f"constraint: {key} = '{value}'")
        if raw_filter:
            descriptors.append(f"raw_filter: {raw_filter}")

        if not descriptors:
            return {"applied": [], "skipped": [], "note": None}

        if step_type in self._CONSTRAINT_APPLYING_STEPS:
            return {"applied": descriptors, "skipped": [], "note": None}
        else:
            note = self._CONSTRAINT_SKIP_REASONS.get(step_type)
            return {"applied": [], "skipped": descriptors, "note": note}

    def _run_compare_metric(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Generic metric comparison step driven by semantic metric definitions.

        Externally accepts the TSU typed contract. Internally bridges that
        request into the legacy comparison compiler inputs until the shared
        time-scope compiler lands.
        """
        resolved = normalize_compare_metric_request(params)
        if not isinstance(resolved.value_spec, SemanticMetricValueSpec):
            raise ValueError("compare_metric requires a semantic metric request")

        metric_name = resolved.value_spec.metric

        step_type = "compare_metric"
        step_id = self._new_step_id()

        metric_sql = self.resolve_metric_sql(metric_name)
        all_dimensions = self.resolve_metric_dimensions(metric_name)
        if metric_sql is None or all_dimensions is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published in semantic_metrics")

        bridged_params = self._bridge_compare_metric_request(session_id, resolved, all_dimensions)
        table_name = bridged_params["table_name"]
        date_column = bridged_params["date_column"]

        # Allow caller to select a subset of dimensions for grouping
        requested_dims = bridged_params.get("dimensions")
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
        obs_type = "metric_change"
        limit = bridged_params.get("limit", 10)

        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)
        current_start = date.fromisoformat(str(bridged_params["period_start"]))
        current_end = date.fromisoformat(str(bridged_params["period_end"]))
        baseline_start = date.fromisoformat(str(bridged_params["baseline_start"]))
        baseline_end = date.fromisoformat(str(bridged_params["baseline_end"]))
        try:
            row = engine.query_rows(
                f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}"
            )[0]
            date_fmt = self._detect_date_format(row["max_date"])
        except Exception:
            date_fmt = self._detect_date_format(str(bridged_params["period_end"]))

        current_len = (current_end - current_start).days
        baseline_len = (baseline_end - baseline_start).days
        window_size_mismatch = current_len != baseline_len

        def _fmt(d: date) -> str | date:
            return d.strftime(date_fmt) if date_fmt else d

        scan_start = min(baseline_start, current_start)
        scan_end = max(baseline_end, current_end)
        period_params = [_fmt(current_start), _fmt(current_end), _fmt(baseline_start), _fmt(baseline_end), _fmt(scan_start), _fmt(scan_end)]
        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(
                0,
                {
                    "step_type": step_type,
                    "params": {
                        **bridged_params,
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
        all_rows = execute_compiled(engine, compiled_query).rows
        rows = [r for r in all_rows if r.get("delta_pct") is not None]
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
        window = {
            "start": str(current_start),
            "end": str(current_end),
            "granularity": "day",
        }
        self._annotate_temporal(observations, session_id, window)
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        artifact_id = self._insert_artifact(session_id, step_id, "table", f"{metric_name}_comparison", rows)

        _debug = {
            "current_window": [str(_fmt(current_start)), str(_fmt(current_end))],
            "baseline_window": [str(_fmt(baseline_start)), str(_fmt(baseline_end))],
            "current_has_data": any(r.get("current_sessions") for r in all_rows),
            "baseline_has_data": any(r.get("baseline_sessions") for r in all_rows),
            "window_length_match": not window_size_mismatch,
        }

        if rows:
            top = rows[0]
            direction = "decline" if (top.get("delta_pct") or 0) < 0 else "increase"
            summary = (
                f"Metric '{metric_name}' comparison: top {direction} is "
                f"{top['delta_pct']}% for {top}."
            )
            if window_size_mismatch:
                summary += (
                    f" \u26a0 Window size mismatch: current={current_len + 1}d, "
                    f"baseline={baseline_len + 1}d — count/sum metrics may not be comparable."
                )
        elif all_rows:
            missing = []
            if not _debug["current_has_data"]:
                missing.append("current")
            if not _debug["baseline_has_data"]:
                missing.append("baseline")
            missing_str = " and ".join(missing) if missing else "one"
            hint = ""
            if not comparison_type and not (baseline_start_p and baseline_end_p):
                hint = " Tip: use comparison_type=wow|dod|mom|yoy or explicit baseline_start/end."
            summary = (
                f"Metric '{metric_name}' comparison: {missing_str} window has no data. "
                f"current={_debug['current_window']}, baseline={_debug['baseline_window']}.{hint}"
            )
        else:
            summary = (
                f"Metric '{metric_name}' comparison returned no results. "
                f"current={_debug['current_window']}, baseline={_debug['baseline_window']}."
            )

        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)

        # G-5e: resolve unit for the metric from confirmed hints or entity properties
        unit_note = self._resolve_metric_unit_note(metric_name, session_id)

        result = {
            "step_type": step_type,
            "metric_name": metric_name,
            "summary": summary,
            "artifact_id": artifact_id,
            "observations": observations,
        }
        if unit_note:
            result["unit_note"] = unit_note
        if not rows:
            result["debug"] = _debug
        elif window_size_mismatch:
            result["debug"] = {k: _debug[k] for k in ("current_window", "baseline_window", "window_length_match")}
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _resolve_metric_unit_note(self, metric_name: str, session_id: str) -> str | None:
        """G-5e: Return a concise unit note for a metric if one is available.

        Priority:
        1. Applied entity properties.unit (authoritative)
        2. Confirmed hint in session recommendations' entity_patch

        Returns None if no unit is known.
        """
        try:
            # Priority 1: published entity field-level units (properties.fields.<col>.unit)
            entity = self._resolve_entity_for_metric(metric_name)
            if entity:
                fields = entity.get("properties", {}).get("fields", {})
                field_units = {
                    col: info["unit"]
                    for col, info in fields.items()
                    if isinstance(info, dict) and info.get("unit")
                }
                if field_units:
                    parts = ", ".join(f"{col}: {u}" for col, u in field_units.items())
                    return f"Unit (from entity): {parts}"

            # Priority 2: confirmed hint from session recommendations
            rows = self.metadata.query_rows(
                """
                SELECT r.entity_patch_json
                FROM recommendations r
                WHERE r.session_id = ? AND r.entity_patch_json IS NOT NULL
                ORDER BY r.created_at DESC
                LIMIT 10
                """,
                [session_id],
            )
            for row in rows:
                patch = json.loads(row["entity_patch_json"])
                if patch.get("metric_name") == metric_name and patch.get("suggested_value"):
                    confidence = patch.get("confidence", 0.0)
                    source = patch.get("source", "heuristic")
                    unit = patch["suggested_value"]
                    return f"Unit (inferred, {source}, confidence={confidence:.2f}): {unit}"
        except Exception:
            pass
        return None

    def _fetch_column_metadata(self, short_name: str, columns: list[str]) -> dict[str, dict[str, str]]:
        """Look up synced column source_objects to get data_type and unit.

        Uses the table short_name (last FQN segment) for a LIKE lookup.
        Returns {} gracefully if no column objects are synced.
        """
        if not columns:
            return {}
        try:
            rows = self.metadata.query_rows(
                "SELECT native_name, properties_json FROM source_objects "
                "WHERE object_type = 'column' AND fqn LIKE ?",
                [f"%.{short_name}.%"],
            )
            result: dict[str, dict[str, str]] = {}
            for row in rows:
                col_name = row["native_name"]
                if col_name in columns:
                    props = json.loads(row["properties_json"])
                    entry = {k: props[k] for k in ("data_type", "unit") if k in props}
                    if entry:
                        result[col_name] = entry
            return result
        except Exception:
            return {}

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
        row_count: int | None = None
        row_count_error: str | None = None
        try:
            row_count_row = execute_compiled(engine, row_count_query).rows[0]
            row_count = row_count_row["row_count"]
        except Exception as exc:
            row_count_error = str(exc)

        columns_available = True
        columns_error: str | None = None
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
            # Fallback: derive column names from SELECT * LIMIT 0 result schema
            try:
                schema_rows = engine.query_rows(f"SELECT * FROM {qualified_table} LIMIT 0")
                columns = list(schema_rows[0].keys()) if schema_rows else []
            except Exception as exc:
                columns = []
                columns_available = False
                columns_error = str(exc)

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

        col_metadata = self._fetch_column_metadata(short_name, columns)
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
                entry: dict[str, Any] = {
                    "column": col,
                    "total": stats["total"],
                    "non_null": stats["non_null"],
                    "null_rate": round(1 - stats["non_null"] / max(stats["total"], 1), 4),
                    "distinct_count": stats["distinct_count"],
                }
                if col in col_metadata:
                    entry.update(col_metadata[col])
                col_profiles.append(entry)
            except Exception:
                err_entry: dict[str, Any] = {"column": col, "error": "failed to profile"}
                if col in col_metadata:
                    err_entry.update(col_metadata[col])
                col_profiles.append(err_entry)

        profile_scope = None
        if profile_date_column:
            profile_scope = {
                "date_column": profile_date_column,
                "date_value": profile_date_value,
                "scoped_row_count": col_profiles[0]["total"] if col_profiles and "total" in col_profiles[0] else None,
            }
        # If the row-count query failed and no columns were found, the table
        # does not exist (or is otherwise completely inaccessible).  Raise so
        # that plan execution marks this step as failed.  When a table exists
        # but COUNT(*) is merely rejected (e.g. Trino mandatory partition
        # filter), the info_schema columns query still returns rows, so
        # `columns` will be non-empty and we fall through to the partial
        # profile path.
        if row_count_error is not None and not columns:
            raise ValueError(
                f"Table '{table_name}' is inaccessible: {row_count_error}"
            )

        profile_errors: dict[str, str] = {}
        if row_count_error is not None:
            profile_errors["row_count"] = row_count_error
        if not columns_available and columns_error is not None:
            profile_errors["columns"] = columns_error
        artifact: dict[str, Any] = {"table_name": table_name, "row_count": row_count, "profile_scope": profile_scope, "columns": col_profiles}
        if profile_errors:
            artifact["errors"] = profile_errors
        artifact_id = self._insert_artifact(session_id, step_id, "profile", f"{short_name}_profile", artifact)

        scope_note = f" (column stats scoped to {profile_date_column}={profile_date_value})" if profile_date_column else ""
        failure_notes: list[str] = []
        if row_count_error is not None:
            failure_notes.append(f"row_count unavailable: {row_count_error}")
        if not columns_available:
            col_detail = f": {columns_error}" if columns_error else ""
            failure_notes.append(f"columns unavailable (schema query failed{col_detail}; use sample_rows limit=1 to inspect columns)")
        if failure_notes:
            failure_str = "; ".join(failure_notes)
            summary = f"Table '{table_name}' profile incomplete — {failure_str}."
        else:
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

        actual_columns = list(rows[0].keys()) if rows else list(params.get("columns") or [])
        col_metadata = self._fetch_column_metadata(short_name, actual_columns)

        artifact_id = self._insert_artifact(session_id, step_id, "sample", f"{short_name}_sample", rows)
        summary = f"Sampled {len(rows)} rows from '{table_name}'."
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "rows": rows, "columns_metadata": col_metadata}
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
            extract_observations: extract observations from result (default: true)
            observed_window_column: explicit column for observed_window inference (G-2)
        """
        resolved = normalize_aggregate_query_request(params)
        bridged_params = self._bridge_aggregate_query_request(session_id, resolved)

        table_name = bridged_params.get("table_name")
        if not table_name:
            raise ValueError("aggregate_query requires 'table_name' param")
        if not bridged_params.get("select"):
            raise ValueError("aggregate_query requires 'select' param")

        step_type = "aggregate_query"
        step_id = self._new_step_id()
        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, table_name)

        compare_period = bridged_params.get("compare_period", False)
        period_params: list[Any] = []
        if compare_period:
            date_column = bridged_params.get("date_column")
            if not date_column:
                raise ValueError("aggregate_query with compare_period=True requires 'date_column' param")
            ps = date.fromisoformat(str(bridged_params["period_start"]))
            pe = date.fromisoformat(str(bridged_params["period_end"]))
            baseline_start = date.fromisoformat(str(bridged_params["baseline_start"]))
            baseline_end = date.fromisoformat(str(bridged_params["baseline_end"]))
            try:
                row = engine.query_rows(
                    f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}"
                )[0]
                date_fmt = self._detect_date_format(row["max_date"])
            except Exception:
                date_fmt = self._detect_date_format(str(bridged_params["period_start"]))
            current_start, current_end = ps, pe

            def _fmt(d: date) -> str:
                return d.strftime(date_fmt) if date_fmt else d.isoformat()

            period_params = [
                _fmt(current_start), _fmt(current_end),
                _fmt(baseline_start), _fmt(baseline_end),
                _fmt(baseline_start), _fmt(current_end),
            ]

        compiler_params: dict[str, Any] = {
            "table_name": qualified_table,
            "select": bridged_params["select"],
            "group_by": bridged_params["group_by"],
        }
        if bridged_params.get("where"):
            compiler_params["where"] = bridged_params["where"]
        if bridged_params.get("order_by"):
            compiler_params["order_by"] = bridged_params["order_by"]
        if bridged_params.get("limit"):
            compiler_params["limit"] = bridged_params["limit"]
        if compare_period:
            compiler_params["compare_period"] = True
            compiler_params["date_column"] = bridged_params["date_column"]

        compiled_query = self._compile_step_with_feedback(
            from_legacy_step(0, {"step_type": step_type, "params": compiler_params}),
            engine_type=engine_type,
            semantic_context={"period_params": period_params} if compare_period else None,
        )
        rows = execute_compiled(engine, compiled_query).rows

        # Extract observations from aggregate rows (opt-out via extract_observations=false)
        if bridged_params.get("extract_observations", True):
            group_by_cols = bridged_params.get("group_by", [])
            # For compare_period, auto-select the first delta_pct column as value_column
            value_column = bridged_params.get("value_column")
            if compare_period and not value_column and rows:
                first_key = next(
                    (k for k in rows[0] if k.endswith("_delta_pct")), None
                )
                value_column = first_key
            # G-5a: fetch synced column metadata so AggregateRowExtractor can use
            # authoritative unit information instead of falling back to heuristics.
            all_cols = list(rows[0].keys()) if rows else []
            col_metadata = self._fetch_column_metadata(short_name, all_cols)
            observation_context = {
                "group_by": group_by_cols,
                "observation_type": bridged_params.get("observation_type", "metric_change"),
                "metric": bridged_params.get("metric", "aggregate"),
                "value_column": value_column,
                "observed_window_column": bridged_params.get("observed_window_column"),  # G-2: explicit override
                "column_metadata": col_metadata,  # G-5a: authoritative unit source
            }
            if bridged_params.get("temporal_group_by_columns") is not None:
                observation_context["temporal_group_by_columns"] = bridged_params["temporal_group_by_columns"]

            observations = self.evidence_pipeline.extract_observations(
                "aggregate_rows",
                rows,
                context=observation_context,
            )
            # M-08: annotate temporal info. For compare_period, use the current window.
            # G-2: Only set window if not already inferred by extractor from temporal column.
            agg_window: dict[str, Any] | None = None
            if compare_period and period_params:
                agg_window = {
                    "start": str(period_params[0]),
                    "end": str(period_params[1]),
                    "granularity": "day",
                }
            self._annotate_temporal(observations, session_id, agg_window)
            for observation in observations:
                self._insert_observation(session_id, step_id, observation)
        else:
            observations = []

        artifact_id = self._insert_artifact(session_id, step_id, "aggregate", f"{short_name}_aggregate", rows)
        if not rows:
            _partition_cols = {"log_date", "event_date", "dt", "date", "day"}
            where_lower = str(bridged_params.get("where") or "").lower()
            has_partition_hint = any(col in where_lower for col in _partition_cols)
            if has_partition_hint:
                summary = (
                    f"Aggregate query on '{table_name}' returned 0 rows. "
                    "Possible cause: partition filter syntax or date range contains no data. "
                    "Verify the date format matches the engine (e.g. YYYYMMDD for Trino/Iceberg)."
                )
            else:
                summary = f"Aggregate query on '{table_name}' returned 0 rows."
        elif compare_period:
            summary = (
                f"Period-over-period aggregate on '{table_name}': "
                f"{len(rows)} dimension slice(s) compared "
                f"(current {period_params[0]}–{period_params[1]} vs "
                f"baseline {period_params[2]}–{period_params[3]})."
            )
        else:
            summary = f"Aggregate query on '{table_name}' returned {len(rows)} rows."
        provenance = self._make_provenance(compiled_query.sql, compiled_query.params, engine_type=engine_type)
        result = {"step_type": step_type, "summary": summary, "artifact_id": artifact_id, "rows": rows}
        if observations:
            result["observations"] = observations
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_attribute_change(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Attribute a metric change across candidate dimensions.

        Required params:
            metric_name: published semantic metric name
            table_name: backing table
            period_end: current window end date
            baseline_start / baseline_end: baseline window boundaries
            candidate_dimensions: list of dimensions to attribute across
        Optional params:
            period_start: current window start date (defaults to period_end)
            anomaly_observation_id: upstream anomaly observation to link with a justifies edge
            top_k: number of top contributors to return per dimension
            min_contribution_pct: minimum contribution share to keep a contributor
            date_column: explicit date column override
            where / filter: ad-hoc filter merged with session constraints
            limit: max rows returned per dimension query (default 1000)
        """
        metric_name = params.get("metric_name")
        table_name = params.get("table_name")
        if not metric_name or not table_name:
            raise ValueError("attribute_change requires 'metric_name' and 'table_name' params")

        candidate_dimensions_raw = params.get("candidate_dimensions")
        if not isinstance(candidate_dimensions_raw, list):
            raise ValueError("candidate_dimensions must not be empty")
        candidate_dimensions = [str(dim).strip() for dim in candidate_dimensions_raw if str(dim).strip()]
        candidate_dimensions = list(dict.fromkeys(candidate_dimensions))
        if not candidate_dimensions:
            raise ValueError("candidate_dimensions must not be empty")

        metric_sql = self.resolve_metric_sql(str(metric_name))
        if metric_sql is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published in semantic_metrics")

        period_end_p = params.get("period_end")
        baseline_start_p = params.get("baseline_start")
        baseline_end_p = params.get("baseline_end")
        if not period_end_p or not baseline_start_p or not baseline_end_p:
            raise ValueError(
                "attribute_change requires 'period_end', 'baseline_start', and 'baseline_end' params"
            )

        period_start_p = params.get("period_start") or period_end_p
        period_start = date.fromisoformat(str(period_start_p))
        period_end = date.fromisoformat(str(period_end_p))
        baseline_start = date.fromisoformat(str(baseline_start_p))
        baseline_end = date.fromisoformat(str(baseline_end_p))
        step_id = self._new_step_id()

        metric_dimensions = self.resolve_metric_dimensions(str(metric_name)) or []
        date_column = str(params.get("date_column") or self._infer_date_column(metric_dimensions))
        top_k = max(1, int(params.get("top_k", 5)))
        min_contribution_pct = max(0.0, float(params.get("min_contribution_pct", 5.0)))
        min_contribution_fraction = min_contribution_pct / 100.0
        query_limit = max(top_k, int(params.get("limit", 1000)))

        user_where = params.get("where") or params.get("filter")
        constraints_filter = self._session_constraints_to_filter(session_id)
        merged_where = self._merge_filters(user_where, constraints_filter)

        short_name = str(table_name).split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine([short_name])
        qualified_table = qualified.get(short_name, str(table_name))

        try:
            row = engine.query_rows(f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}")[0]
            date_fmt = self._detect_date_format(row["max_date"])
        except Exception:
            date_fmt = self._detect_date_format(str(period_end_p))

        def _fmt(d: date) -> str | date:
            return d.strftime(date_fmt) if date_fmt else d

        period_params = [
            _fmt(period_start),
            _fmt(period_end),
            _fmt(baseline_start),
            _fmt(baseline_end),
            _fmt(baseline_start),
            _fmt(period_end),
        ]

        anomaly_observation_id = params.get("anomaly_observation_id")
        if anomaly_observation_id:
            anomaly_row = self.metadata.query_one(
                """
                SELECT observation_id
                FROM observations
                WHERE observation_id = ? AND session_id = ?
                """,
                [anomaly_observation_id, session_id],
            )
            if anomaly_row is None:
                raise ValueError(f"anomaly_observation_id not found: {anomaly_observation_id}")

        observations: list[dict[str, Any]] = []
        contributions: list[dict[str, Any]] = []
        query_sql_parts: list[str] = []
        query_params: list[Any] = []
        current_has_data = False
        baseline_has_data = False

        for dimension in candidate_dimensions:
            select_exprs = [dimension, f"{metric_sql} AS metric_value"]
            step_ir = from_legacy_step(
                0,
                {
                    "step_type": "aggregate_query",
                    "params": {
                        "table_name": qualified_table,
                        "select": select_exprs,
                        "group_by": [dimension],
                        "compare_period": True,
                        "date_column": date_column,
                        "limit": query_limit,
                        **({"where": merged_where} if merged_where else {}),
                    },
                },
            )
            compiled_query = self._compile_step_with_feedback(
                step_ir,
                engine_type=engine_type,
                semantic_context={"period_params": period_params},
            )
            rows = execute_compiled(engine, compiled_query).rows
            query_sql_parts.append(compiled_query.sql)
            query_params.extend(compiled_query.params)

            has_current_rows = any(row.get("metric_value_current") is not None for row in rows)
            has_baseline_rows = any(row.get("metric_value_baseline") is not None for row in rows)
            baseline_has_data = baseline_has_data or has_baseline_rows
            if not has_current_rows:
                continue

            dim_contributors: list[dict[str, Any]] = []
            for row in rows:
                current_value_raw = row.get("metric_value_current")
                baseline_value_raw = row.get("metric_value_baseline")
                if current_value_raw is None and baseline_value_raw is None:
                    continue

                current_value = float(current_value_raw or 0.0)
                baseline_value = float(baseline_value_raw or 0.0)
                delta_value = current_value - baseline_value
                delta_pct = None if baseline_value == 0.0 else (delta_value / baseline_value) * 100.0
                dim_value = row.get(dimension)
                if current_value_raw is not None:
                    current_has_data = True
                if baseline_value_raw is not None:
                    baseline_has_data = True
                dim_contributors.append(
                    {
                        "value": dim_value,
                        "current_value": current_value,
                        "baseline_value": baseline_value,
                        "delta_value": delta_value,
                        "delta_pct": delta_pct,
                        "current_row_count": None,
                        "baseline_row_count": None,
                    }
                )

            total_abs_delta = sum(abs(entry["delta_value"]) for entry in dim_contributors)
            for entry in dim_contributors:
                entry["contribution_pct"] = (
                    (abs(entry["delta_value"]) / total_abs_delta) * 100.0 if total_abs_delta > 0 else 0.0
                )

            sorted_contributors = sorted(
                dim_contributors,
                key=lambda entry: (
                    abs(entry["delta_pct"]) if entry["delta_pct"] is not None else abs(entry["delta_value"]),
                    abs(entry["delta_value"]),
                ),
                reverse=True,
            )
            top_contributors = [
                {
                    "value": entry["value"],
                    "current_value": entry["current_value"],
                    "baseline_value": entry["baseline_value"],
                    "delta_pct": entry["delta_pct"],
                    "contribution_pct": entry["contribution_pct"],
                    "current_row_count": entry["current_row_count"],
                    "baseline_row_count": entry["baseline_row_count"],
                }
                for entry in sorted_contributors
                if entry["contribution_pct"] >= min_contribution_fraction
            ][:top_k]

            contributions.append(
                {
                    "dimension": dimension,
                    "top_contributors": top_contributors,
                }
            )

            extractor_rows = [
                {
                    dimension: entry["value"],
                    "baseline_value": entry["baseline_value"],
                    "current_value": entry["current_value"],
                }
                for entry in dim_contributors
            ]
            extracted = self.evidence_pipeline.extract_observations(
                "contribution_shift_rows",
                extractor_rows,
                context={
                    "dim_col": dimension,
                    "baseline_col": "baseline_value",
                    "current_col": "current_value",
                    "metric": str(metric_name),
                    "share_threshold": min_contribution_fraction,
                },
            )
            self._annotate_temporal(
                extracted,
                session_id,
                {
                    "start": str(period_start),
                    "end": str(period_end),
                    "granularity": "day",
                },
            )
            for observation in extracted:
                self._insert_observation(session_id, step_id, observation)
                observations.append(observation)
                if anomaly_observation_id:
                    self._insert_edge(
                        session_id,
                        observation["observation_id"],
                        "observation",
                        str(anomaly_observation_id),
                        "observation",
                        EDGE_TYPE_JUSTIFIES,
                        0.7,
                        "Attributed contribution is justified by the upstream anomaly.",
                    )

        artifact_id = self._insert_artifact(
            session_id,
            step_id,
            "table",
            f"{short_name}_attribution",
            {
                "metric_name": metric_name,
                "table_name": qualified_table,
                "candidate_dimensions": candidate_dimensions,
                "contributions": contributions,
            },
        )

        query_blob = "\n".join(query_sql_parts)
        provenance = self._make_provenance(query_blob, query_params, engine_type=engine_type)
        summary = (
            f"Attributed metric '{metric_name}' across {len(candidate_dimensions)} dimension(s)."
            if contributions
            else f"Attribute change on '{metric_name}' returned no results."
        )

        debug = {
            "current_window": [str(period_start), str(period_end)],
            "baseline_window": [str(baseline_start), str(baseline_end)],
            "current_has_data": current_has_data,
            "baseline_has_data": baseline_has_data,
            "dimensions": candidate_dimensions,
        }

        result = {
            "step_type": "attribute_change",
            "metric_name": metric_name,
            "table_name": qualified_table,
            "summary": summary,
            "artifact_id": artifact_id,
            "contributions": contributions,
            "observations": observations,
            "debug": debug,
        }

        self._insert_step(step_id, session_id, "attribute_change", summary, result, provenance=provenance)
        return result

    # ── Artifact-to-artifact helpers ──────────────────────────────────────────

    def _load_artifact_rows(
        self,
        session_id: str,
        artifact_id: str | None = None,
        step_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Load artifact content as a list of row dicts, scoped to session_id."""
        if artifact_id is None and step_id is None:
            raise ValueError("Provide either artifact_id or step_id")
        if artifact_id is None:
            row = self.metadata.query_one(
                "SELECT artifact_id FROM artifacts WHERE session_id = ? AND step_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                [session_id, step_id],
            )
            if row is None:
                raise ValueError(f"No artifact found for step_id={step_id!r} in session {session_id!r}")
            artifact_id = str(row["artifact_id"])
        row = self.metadata.query_one(
            "SELECT content_json FROM artifacts WHERE artifact_id = ? AND session_id = ?",
            [artifact_id, session_id],
        )
        if row is None:
            raise ValueError(f"Artifact {artifact_id!r} not found in session {session_id!r}")
        content = json.loads(str(row["content_json"]))
        if isinstance(content, list):
            return [dict(r) for r in content]
        return [dict(content)]

    def _run_correlate_metrics(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Compute Spearman/Pearson correlation between two artifact series.

        Required params:
            left_artifact_id or left_step_id: source of series A
            right_artifact_id or right_step_id: source of series B
            left_value_column: numeric column in series A
            right_value_column: numeric column in series B
            join_on: shared key column to align both series
        Optional params:
            method: "spearman" (default) | "pearson" | "both"
            min_pairs: minimum matched rows required (default: 3)
            left_metric: label for series A metric (default: left_value_column)
            right_metric: label for series B metric (default: right_value_column)
        """
        left_artifact_id = params.get("left_artifact_id")
        left_step_id = params.get("left_step_id")
        right_artifact_id = params.get("right_artifact_id")
        right_step_id = params.get("right_step_id")
        if not left_artifact_id and not left_step_id:
            raise ValueError("correlate_metrics requires 'left_artifact_id' or 'left_step_id'")
        if not right_artifact_id and not right_step_id:
            raise ValueError("correlate_metrics requires 'right_artifact_id' or 'right_step_id'")

        left_value_column = params.get("left_value_column")
        right_value_column = params.get("right_value_column")
        join_on = params.get("join_on")
        if not left_value_column:
            raise ValueError("correlate_metrics requires 'left_value_column'")
        if not right_value_column:
            raise ValueError("correlate_metrics requires 'right_value_column'")
        if not join_on:
            raise ValueError("correlate_metrics requires 'join_on'")

        method = str(params.get("method", "spearman")).lower()
        min_pairs = int(params.get("min_pairs", 3))
        left_metric = params.get("left_metric")
        right_metric = params.get("right_metric")
        if not left_metric:
            raise ValueError(
                "correlate_metrics requires 'left_metric' param. "
                "Set it to match the metric name used in the source aggregate_query step "
                "(e.g., the 'metric' param passed to aggregate_query, or 'aggregate' if omitted)."
            )
        if not right_metric:
            raise ValueError(
                "correlate_metrics requires 'right_metric' param. "
                "Set it to match the metric name used in the source aggregate_query step."
            )
        left_scope_slice = params.get("left_scope_slice", {})
        right_scope_slice = params.get("right_scope_slice", {})

        left_rows = self._load_artifact_rows(
            session_id, artifact_id=left_artifact_id, step_id=left_step_id
        )
        right_rows = self._load_artifact_rows(
            session_id, artifact_id=right_artifact_id, step_id=right_step_id
        )

        # Extract dates from both series for observed_window (union, not intersection)
        left_dates: list[date] = []
        right_dates: list[date] = []
        for r in left_rows:
            key = str(r.get(join_on, ""))
            d = _try_parse_date(key)
            if d is not None:
                left_dates.append(d)
        for r in right_rows:
            key = str(r.get(join_on, ""))
            d = _try_parse_date(key)
            if d is not None:
                right_dates.append(d)
        all_dates = left_dates + right_dates

        # Inner-join on join_on key
        right_index: dict[str, dict[str, Any]] = {}
        for r in right_rows:
            key = str(r.get(join_on, ""))
            if key:
                right_index[key] = r

        xs: list[float] = []
        ys: list[float] = []
        join_keys: list[str] = []
        for lr in left_rows:
            key = str(lr.get(join_on, ""))
            if key and key in right_index:
                rr = right_index[key]
                try:
                    xv = float(lr[left_value_column])
                    yv = float(rr[right_value_column])
                except (KeyError, TypeError, ValueError):
                    continue
                xs.append(xv)
                ys.append(yv)
                join_keys.append(key)

        n = len(xs)
        if n < min_pairs:
            raise ValueError(
                f"correlate_metrics: only {n} matched pairs on '{join_on}' "
                f"(minimum {min_pairs}). Check that both artifacts share values in '{join_on}' "
                f"and that '{left_value_column}' / '{right_value_column}' are numeric."
            )

        # Compute statistics
        results: dict[str, Any] = {"n": n, "method": method, "join_on": join_on,
                                    "left_metric": left_metric, "right_metric": right_metric}
        if method in ("spearman", "both"):
            rho_s = _spearman_correlation(xs, ys)
            p_s = _correlation_p_value(rho_s, n)
            results["rho"] = rho_s
            results["p_value"] = p_s
            if method == "both":
                results["spearman_rho"] = rho_s
                results["spearman_p_value"] = p_s
        if method in ("pearson", "both"):
            rho_p = _pearson_correlation(xs, ys)
            p_p = _correlation_p_value(rho_p, n)
            if method == "pearson":
                results["rho"] = rho_p
                results["p_value"] = p_p
            else:
                results["pearson_rho"] = rho_p
                results["pearson_p_value"] = p_p

        # Derive observed_window from union of dates from both series
        observed_window: dict[str, Any] | None = None
        if all_dates:
            observed_window = {
                "start": str(min(all_dates)),
                "end": str(max(all_dates)),
                "granularity": "day",
            }
            results["observed_window"] = observed_window
            results["left_series_size"] = len(left_rows)
            results["right_series_size"] = len(right_rows)
            results["matched_pairs"] = n

        # Insert artifact
        step_type = "correlate_metrics"
        step_id = self._new_step_id()
        artifact_id = self._insert_artifact(
            session_id, step_id, "correlation",
            f"{left_metric}_vs_{right_metric}_correlation",
            [results],
        )

        # Extract observations
        context: dict[str, Any] = {
            "left_metric": left_metric,
            "right_metric": right_metric,
            "join_on": join_on,
            "left_scope_slice": left_scope_slice,
            "right_scope_slice": right_scope_slice,
        }
        observations = self.evidence_pipeline.extract_observations(
            "correlation_observations", [results], context=context
        )
        self._annotate_temporal(observations, session_id, observed_window)
        for observation in observations:
            self._insert_observation(session_id, step_id, observation)

        rho = results.get("rho", 0.0)
        p_value = results.get("p_value", 1.0)
        summary = (
            f"Correlation between '{left_metric}' and '{right_metric}' over {n} paired "
            f"observations on '{join_on}': ρ={rho:.3f}, p={p_value:.4f} ({method})."
        )
        if observed_window:
            summary += f" Window: {observed_window['start']} – {observed_window['end']}."

        provenance = self._make_provenance(engine_type="artifact_only")
        result: dict[str, Any] = {
            "step_type": step_type,
            "summary": summary,
            "artifact_id": artifact_id,
            "correlation": results,
        }
        if observations:
            result["observations"] = observations
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _run_synthesis(self, session_id: str) -> dict[str, Any]:
        step_type = "synthesize_findings"
        step_id = self._new_step_id()
        self._delete_non_tentative_synthesis_outputs(session_id)
        observations = self._load_observations(session_id)
        tentative_claims = self._load_tentative_claims(session_id)
        promoted = self._promote_claims(session_id, tentative_claims, observations)
        promotion_audit = {
            "stage": "promotion",
            "claims_promoted": [
                {
                    "claim_id": c["claim_id"],
                    "new_status": c["status"],
                    "confidence": c["confidence"],
                    "promotion_reason": (
                        "confidence >= 0.5 and no contradictions"
                        if c["status"] == "confirmed"
                        else "confidence < 0.5 or has contradictions"
                    ),
                }
                for c in promoted
            ],
            "confirmed_count": sum(1 for c in promoted if c["status"] == "confirmed"),
            "insufficient_count": sum(1 for c in promoted if c["status"] == "insufficient"),
        }
        self._insert_artifact(session_id, step_id, "synthesis_audit", "promotion_audit", promotion_audit)
        synthesis = self.evidence_pipeline.build_synthesis(
            observations,
            existing_claims=promoted,
        )
        self._persist_synthesized_claim_updates(synthesis["claims"])
        claim_map = {c["claim_id"]: c for c in promoted}
        self._attach_entity_patches(synthesis["recommendations"], observations, claim_map)
        derived_observations = synthesis.get("derived_observations", [])
        if derived_observations:
            self._annotate_temporal(derived_observations, session_id, None)
            for observation in derived_observations:
                self._insert_observation(session_id, step_id, observation)
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
            "derived_observations": derived_observations,
        }
        self._insert_step(step_id, session_id, step_type, summary, result, provenance=provenance)
        return result

    def _persist_synthesized_claim_updates(self, claims: list[dict[str, Any]]) -> None:
        """Persist post-promotion inference and confidence updates from the evidence pipeline."""
        for claim in claims:
            self.metadata.execute(
                """
                UPDATE claims
                SET confidence = ?,
                    inference_level = ?,
                    inference_justification_json = ?
                WHERE claim_id = ?
                """,
                [
                    claim.get("confidence"),
                    claim.get("inference_level", "L0"),
                    self._dump(claim.get("inference_justification", [])),
                    claim.get("claim_id"),
                ],
            )

    # ── Entity patch helpers (G-5b) ───────────────────────────────────────────

    def _attach_entity_patches(
        self,
        recommendations: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        claim_map: dict[str, dict[str, Any]],
    ) -> None:
        """Attach entity_patch proposals to recommendations backed by confirmed claims.

        For each recommendation whose claim is confirmed (or better), inspect
        its supporting observations for `column_unit_hint` payloads.  When a
        strong hint is found (confidence >= 0.6) and the claim's metric maps to
        a published entity, build a machine-readable patch proposal and attach it
        as `entity_patch` on the recommendation dict.

        The patch proposal shape:
            {
                "entity_id": str,
                "entity_name": str,
                "field": "unit",
                "current_value": str | None,   # existing entity properties.unit
                "suggested_value": str,         # unit from the hint
                "confidence": float,
                "source": str,                 # from column_unit_hint.source
                "evidence_step_id": str | None, # step that produced the hint obs
                "metric_name": str,
            }

        This does not write to the DB — the caller persists via _insert_recommendation.
        """
        obs_map = {o["observation_id"]: o for o in observations}

        for rec in recommendations:
            if rec.get("entity_patch") is not None:
                continue  # already set
            claim = claim_map.get(rec.get("claim_id", ""))
            if claim is None:
                continue
            if claim.get("status") not in ("confirmed", "supported"):
                continue

            metric_name = claim.get("scope", {}).get("metric")
            if not metric_name:
                continue

            # Find the strongest unit hint among supporting observations
            best_hint: dict[str, Any] | None = None
            best_obs_id: str | None = None
            for obs_id in claim.get("supporting_observations", []):
                obs = obs_map.get(obs_id)
                if obs is None:
                    continue
                hint = obs.get("payload", {}).get("column_unit_hint")
                if hint and isinstance(hint, dict):
                    confidence = hint.get("confidence", 0.0)
                    if confidence >= 0.6:
                        if best_hint is None or confidence > best_hint.get("confidence", 0.0):
                            best_hint = hint
                            best_obs_id = obs_id

            if best_hint is None:
                continue

            # Don't generate patch if metadata was the source (metadata already authoritative)
            if best_hint.get("source") == "metadata":
                continue

            # Require column name so we can generate a field-level (not entity-level) patch.
            column_name = best_hint.get("column")
            if not column_name:
                continue

            # Resolve metric → entity
            entity = self._resolve_entity_for_metric(metric_name)
            if entity is None or entity.get("status") != "published":
                continue

            # Read existing field-level unit (properties.fields.<col>.unit) not entity-level
            current_unit = (
                entity.get("properties", {})
                .get("fields", {})
                .get(column_name, {})
                .get("unit")
            )
            suggested_unit = best_hint["unit"]

            # If metadata conflicts with heuristic, confidence should already be low;
            # here we also skip if current field unit differs from hint (conflict)
            if current_unit and current_unit != suggested_unit:
                continue

            # Look up the step_id for the best observation
            step_id = self._obs_step_id(best_obs_id) if best_obs_id else None

            rec["entity_patch"] = {
                "entity_id": entity["entity_id"],
                "entity_name": entity["name"],
                "column_name": column_name,
                "field": f"fields.{column_name}.unit",
                "current_value": current_unit,
                "suggested_value": suggested_unit,
                "confidence": best_hint["confidence"],
                "source": best_hint.get("source", "heuristic"),
                "evidence_step_id": step_id,
                "metric_name": metric_name,
            }

    def _resolve_entity_for_metric(self, metric_name: str) -> dict[str, Any] | None:
        """Return the published entity linked to the given metric name, or None."""
        try:
            metric_row = self.metadata.query_one(
                "SELECT entity_id FROM semantic_metrics WHERE name = ? AND status = 'published'",
                [metric_name],
            )
            if metric_row is None or not metric_row.get("entity_id"):
                return None
            entity_row = self.metadata.query_one(
                "SELECT entity_id, name, status, properties_json FROM semantic_entities WHERE entity_id = ?",
                [metric_row["entity_id"]],
            )
            if entity_row is None:
                return None
            entity = dict(entity_row)
            entity["properties"] = json.loads(entity.pop("properties_json", "{}"))
            return entity
        except Exception:
            return None

    def _obs_step_id(self, observation_id: str) -> str | None:
        """Return the step_id that produced the given observation_id, or None."""
        try:
            row = self.metadata.query_one(
                "SELECT step_id FROM observations WHERE observation_id = ?",
                [observation_id],
            )
            return row["step_id"] if row else None
        except Exception:
            return None

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
        synth_step_rows = self.metadata.query_rows(
            "SELECT step_id FROM steps WHERE session_id = ? AND step_type = 'synthesize_findings'",
            [session_id],
        )
        for row in synth_step_rows:
            step_id = row["step_id"]
            self.metadata.execute("DELETE FROM artifacts WHERE step_id = ?", [step_id])
            self.metadata.execute("DELETE FROM observations WHERE step_id = ?", [step_id])
        self.metadata.execute(
            "DELETE FROM steps WHERE session_id = ? AND step_type = 'synthesize_findings'",
            [session_id],
        )
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
            # Strip "(tentative)" suffix from claim text on promotion
            new_text = claim["text"].replace(" (tentative)", "")
            self.metadata.execute(
                "UPDATE claims SET status = ?, text = ? WHERE claim_id = ?",
                [new_status, new_text, claim["claim_id"]],
            )
            promoted.append({**claim, "status": new_status, "text": new_text})
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

    @staticmethod
    def _shift_calendar_date(d: date, *, months: int = 0, years: int = 0) -> date:
        """Calendar shift with end-of-month clamp (e.g. 2026-03-31 → 2026-02-28)."""
        from calendar import monthrange
        target_month = d.month + months
        target_year = d.year + years + (target_month - 1) // 12
        target_month = (target_month - 1) % 12 + 1
        target_day = min(d.day, monthrange(target_year, target_month)[1])
        return date(target_year, target_month, target_day)

    @staticmethod
    def _compute_baseline_from_type(
        current_start: date, current_end: date, comparison_type: str
    ) -> tuple[date, date]:
        """Compute baseline window from a comparison_type enum.

        dod: shift -1 day  wow: shift -7 days
        mom: shift -1 calendar month  yoy: shift -1 calendar year
        The baseline window preserves the same span as the current window.
        """
        ct = comparison_type.lower()
        if ct == "dod":
            delta = timedelta(days=1)
            return current_start - delta, current_end - delta
        if ct == "wow":
            delta = timedelta(days=7)
            return current_start - delta, current_end - delta
        if ct == "mom":
            bs = SemanticLayerService._shift_calendar_date(current_start, months=-1)
            return bs, bs + (current_end - current_start)
        if ct == "yoy":
            bs = SemanticLayerService._shift_calendar_date(current_start, years=-1)
            return bs, bs + (current_end - current_start)
        raise ValueError(
            f"Unknown comparison_type '{comparison_type}'. "
            "Supported values: dod, wow, mom, yoy."
        )

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
            SELECT observation_id, observation_type, subject_json, payload_json,
                   significance_json, quality_json, observed_window_json, temporal_order
            FROM observations
            WHERE session_id = ?
            ORDER BY temporal_order, created_at
            """,
            [session_id],
        )
        observations = []
        for row in rows:
            obs = {
                "observation_id": row["observation_id"],
                "type": row["observation_type"],
                "subject": json.loads(row["subject_json"]),
                "payload": json.loads(row["payload_json"]),
                "significance": json.loads(row["significance_json"]),
                "quality": json.loads(row["quality_json"]),
                "temporal_order": row["temporal_order"],
            }
            if row["observed_window_json"] is not None:
                obs["observed_window"] = json.loads(row["observed_window_json"])
            observations.append(obs)
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
        table_name = params.get("table_name") or params.get("table")
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

    def _observation_count(self, session_id: str) -> int:
        """Return the number of observations already recorded for a session."""
        row = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM observations WHERE session_id = ?",
            [session_id],
        )
        return row["cnt"] if row else 0

    def _annotate_temporal(
        self,
        observations: list[dict[str, Any]],
        session_id: str,
        observed_window: dict[str, Any] | None,
    ) -> None:
        """In-place: assign observed_window (when available) and temporal_order to each observation.

        G-2: Only sets observed_window if not already present (preserves extractor-inferred windows).
        """
        base = self._observation_count(session_id)
        for i, obs in enumerate(observations):
            # G-2: Only set window if not already inferred by extractor
            if observed_window is not None and "observed_window" not in obs:
                obs["observed_window"] = observed_window
            obs["temporal_order"] = base + i

    def _insert_observation(self, session_id: str, step_id: str, observation: dict[str, Any]) -> None:
        self.metadata.execute(
            """
            INSERT INTO observations (
                observation_id, session_id, step_id, observation_type,
                subject_json, payload_json, significance_json, quality_json,
                observed_window_json, temporal_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                self._dump(observation["observed_window"]) if observation.get("observed_window") is not None else None,
                observation.get("temporal_order", 0),
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
        match_basis: dict[str, Any] | None = None,
        score_components: dict[str, Any] | None = None,
        supporting_observation_ids: list[str] | None = None,
    ) -> None:
        self.metadata.execute(
            """
            INSERT INTO evidence_edges (
                edge_id, session_id, from_node_id, from_node_type, to_node_id, to_node_type, edge_type, weight, explanation,
                match_basis_json, score_components_json, supporting_observation_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"edge_{uuid4().hex[:12]}",
                session_id,
                from_node_id,
                from_node_type,
                to_node_id,
                to_node_type,
                edge_type,
                weight,
                explanation,
                self._dump(match_basis or {}),
                self._dump(score_components or {}),
                self._dump(supporting_observation_ids or []),
            ],
        )

    def _insert_recommendation(self, session_id: str, recommendation: dict[str, Any]) -> None:
        causal_basis = recommendation.get("causal_basis")
        entity_patch = recommendation.get("entity_patch")
        supporting_claims = recommendation.get("supporting_claims")
        rec_type = recommendation.get("type", "action_required")
        self.metadata.execute(
            """
            INSERT INTO recommendations (
                rec_id, session_id, claim_id, action_text, template_id, priority, expected_impact, risk,
                validation_metric_json, causal_basis_json, entity_patch_json, supporting_claims_json,
                type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                recommendation["rec_id"],
                session_id,
                recommendation["claim_id"],
                recommendation["action_text"],
                recommendation.get("template_id"),
                recommendation["priority"],
                recommendation["expected_impact"],
                recommendation["risk"],
                self._dump(recommendation["validation_metric"]),
                self._dump(causal_basis) if causal_basis is not None else None,
                self._dump(entity_patch) if entity_patch is not None else None,
                self._dump(supporting_claims) if supporting_claims is not None else None,
                rec_type,
            ],
        )

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def _new_step_id(self) -> str:
        return f"step_{uuid4().hex[:12]}"


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "mvp.duckdb"


def _norm_cdf(z: float) -> float:
    """Dependency-free approximation of the standard normal CDF (Hart, 1968)."""
    # Abramowitz & Stegun 26.2.17 approximation; max error < 7.5e-8
    a = abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * a)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    cdf = 0.5 * (1.0 + math.erf(a))  # use erf from math (stdlib, no deps)
    return cdf if z >= 0 else 1.0 - cdf


def _correlation_p_value(rho: float, n: int) -> float:
    """Two-tailed p-value for a Pearson/Spearman correlation via t-distribution approximation."""
    if n <= 2:
        return 1.0
    denom = 1.0 - rho * rho
    if denom <= 0.0:
        return 0.0
    t_stat = rho * math.sqrt((n - 2) / denom)
    # Two-tailed p-value using normal approximation (adequate for n >= 3)
    return 2.0 * (1.0 - _norm_cdf(abs(t_stat)))


def _try_parse_date(value: str) -> "date | None":
    """Try to parse a string as a date (YYYYMMDD or ISO 8601); return None on failure."""
    import re
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


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
