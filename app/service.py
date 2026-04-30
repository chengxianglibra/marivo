from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from app.analysis_core import (
    CompositeWorkflowRuntime,
    IntentRunnerRegistry,
    build_service_step_registry,
)
from app.analysis_core.calendar_data_runtime import CalendarDataReader, CalendarDataResolutionError
from app.analysis_core.compiler import (
    CompiledQuery,
    SemanticRequestCompatibilityError,
    compile_step,
)
from app.analysis_core.compiler import build_metric_query as compile_metric_query
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR
from app.config import MarivoConfig
from app.evidence_engine.canonical_finding import StepRef
from app.evidence_engine.canonical_pipeline_runtime import run_canonical_downstream
from app.evidence_engine.finding_extractor_registry import (
    FindingExtractorRegistry,
    default_finding_registry,
    validate_for_commit,
)
from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload
from app.evidence_engine.state_view import materialize_session_state_view
from app.execution.feedback import compile_failure_from_error
from app.execution.orchestrator import WorkflowOrchestrator
from app.execution.routing_runtime import RoutingRuntime
from app.intents.attribute import run_attribute_intent
from app.intents.compare import run_compare_intent
from app.intents.correlate import run_correlate_intent
from app.intents.decompose import run_decompose_intent
from app.intents.detect import run_detect_intent
from app.intents.diagnose import run_diagnose_intent
from app.intents.forecast import run_forecast_intent
from app.intents.observe import run_observe_intent
from app.intents.test import run_test_intent
from app.intents.validate import run_validate_intent
from app.metric_inputs import required_metric_input_slots
from app.semantic_runtime import SemanticRuntimeRepository
from app.semantic_runtime.dimensions import resolve_entity_binding_dimensions
from app.semantic_runtime.errors import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject
from app.session import SessionManager
from app.source_object_locator import (
    normalize_source_object_authority_locator,
    qualify_execution_locator,
)
from app.storage.analytics import AnalyticsEngine
from app.storage.evidence_repositories import (
    ActionProposalRepository,
    AssessmentRepository,
    EvidenceGapRepository,
    FindingRepository,
    InferenceRecordRepository,
    PropositionRepository,
)
from app.storage.metadata import MetadataStore
from app.storage.step_metadata_repository import StepMetadataRepository
from app.time_axis_metadata import TimeAxisMetadataContext, TimeAxisMetadataProvider
from app.time_scope import (
    AdHocAggregateValueSpec,
    ResolvedWindowedQueryRequest,
    SemanticMetricValueSpec,
    TimeAxisResolver,
    normalize_aggregate_query_request,
    normalize_metric_query_request,
)

if TYPE_CHECKING:
    from app.approvals import ApprovalService
    from app.governance import GovernanceService
    from app.observability import MetricsCollector
    from app.routing import QueryRouter


_STUB_INTENT_TYPES: frozenset[str] = frozenset()


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MetricExecutionContext:
    metric_ref: str
    table_name: str
    binding_ref: str
    carrier_binding_key: str | None = None
    source_object_ref: str | None = None
    carrier_locator: dict[str, Any] | None = None
    authority_locator: dict[str, Any] | None = None
    mapping_id: str | None = None
    execution_locator: dict[str, Any] | None = None
    routing_detail: dict[str, Any] | None = None
    input_field_map: dict[str, str] | None = None
    additivity_constraints: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MetricBindingResolution:
    metric_ref: str
    binding_ref: str
    carrier_binding_key: str | None
    source_object_ref: str | None
    carrier_locator: dict[str, Any] | None
    authority_locator: dict[str, Any] | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any] | None
    table_name: str | None
    input_field_map: dict[str, str]


@dataclass(frozen=True, slots=True)
class MetricCarrierRoutePreflight:
    table_name: str | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any]
    readiness_blockers: list[dict[str, Any]]


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _carrier_locator_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "catalog": _optional_str(value.get("catalog")),
            "schema": _optional_str(value.get("schema")) or _optional_str(value.get("schema_name")),
            "table": _optional_str(value.get("table")),
        }
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            parts = [part.strip() for part in normalized.split(".") if part.strip()]
            if len(parts) >= 3:
                return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
            if len(parts) == 2:
                return {"catalog": None, "schema": parts[0], "table": parts[1]}
            if len(parts) == 1:
                return {"catalog": None, "schema": None, "table": parts[0]}
            return None
        if isinstance(payload, dict):
            return _carrier_locator_dict(payload)
        if isinstance(payload, str):
            return _carrier_locator_dict(payload)
    return None


def _require_metric_ref(value: str, *, field_name: str = "metric") -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"'{field_name}' is required")
    if normalized.startswith("metric.") and len(normalized) > len("metric."):
        return normalized
    raise ValueError(
        f"'{field_name}' must be a canonical metric ref like 'metric.watch_time', got: {normalized}"
    )


def _metric_name_from_ref(metric_ref: str) -> str:
    return metric_ref.removeprefix("metric.")


def _coerce_metric_ref(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("'metric' is required")
    if normalized.startswith("metric."):
        return normalized
    return f"metric.{normalized}"


def _make_stub_runner(intent_type: str) -> Any:
    """Return a runner that raises NotImplementedError for unimplemented intents."""

    def runner(session_id: str, params: dict[str, Any] | None) -> dict[str, Any]:
        raise NotImplementedError(
            f"Intent '{intent_type}' execution is not yet implemented. "
            "This endpoint validates requests but execution requires the intent registry "
            "and derived expansion built in Phase 3."
        )

    return runner


class SemanticLayerService:
    _METRIC_QUERY_MODE_CONTRACTS: ClassVar[dict[str, Any]] = {
        "compare": {
            "payload_fields": {
                "current_value": "current_value",
                "baseline_value": "baseline_value",
                "delta_pct": "delta_pct",
                "current_sessions": "current_sessions",
                "baseline_sessions": "baseline_sessions",
            },
            "required_payload_keys": (
                "current_value",
                "baseline_value",
                "delta_pct",
                "current_sessions",
                "baseline_sessions",
            ),
        },
        "single_window": {
            "payload_fields": {
                "current_value": "current_value",
                "current_sessions": "current_sessions",
            },
            "required_payload_keys": (
                "current_value",
                "current_sessions",
            ),
        },
    }

    def __init__(
        self,
        metadata_store: MetadataStore,
        analytics_engine: AnalyticsEngine,
        query_router: QueryRouter | None = None,
        config: MarivoConfig | None = None,
        governance: GovernanceService | None = None,
        metrics: MetricsCollector | None = None,
        approvals: ApprovalService | None = None,
    ) -> None:
        self.metadata = metadata_store
        self.analytics = analytics_engine
        self.config = config or MarivoConfig()
        self._query_router = query_router
        self.calendar_data_reader: CalendarDataReader | None = None
        self.governance = governance
        self.metrics = metrics
        self.approvals = approvals
        self.session_manager = SessionManager(metadata_store)
        self.step_registry = build_service_step_registry(self)
        self.intent_registry = IntentRunnerRegistry()
        self.intent_registry.register("observe", lambda sid, p: run_observe_intent(self, sid, p))
        self.intent_registry.register("compare", lambda sid, p: run_compare_intent(self, sid, p))
        self.intent_registry.register(
            "correlate", lambda sid, p: run_correlate_intent(self, sid, p)
        )
        self.intent_registry.register(
            "decompose", lambda sid, p: run_decompose_intent(self, sid, p)
        )
        self.intent_registry.register("detect", lambda sid, p: run_detect_intent(self, sid, p))
        self.intent_registry.register("test", lambda sid, p: run_test_intent(self, sid, p))
        self.intent_registry.register("forecast", lambda sid, p: run_forecast_intent(self, sid, p))
        self.intent_registry.register(
            "attribute", lambda sid, p: run_attribute_intent(self, sid, p)
        )
        self.intent_registry.register("diagnose", lambda sid, p: run_diagnose_intent(self, sid, p))
        self.intent_registry.register("validate", lambda sid, p: run_validate_intent(self, sid, p))
        for _stub_type in _STUB_INTENT_TYPES:
            self.intent_registry.register(_stub_type, _make_stub_runner(_stub_type))
        self.semantic_repository = SemanticRuntimeRepository(metadata_store)
        self.semantic_resolver = self.semantic_repository.resolver
        self.time_axis_metadata_provider = TimeAxisMetadataProvider(metadata_store)
        self.planner_context_provider = self.semantic_repository.planner_context_provider
        self.workflow_runtime = CompositeWorkflowRuntime()
        # Canonical evidence repositories (Phase 4g-3)
        self._finding_repo = FindingRepository(metadata_store)
        self._proposition_repo = PropositionRepository(metadata_store)
        self._assessment_repo = AssessmentRepository(metadata_store)
        self._gap_repo = EvidenceGapRepository(metadata_store)
        self._inference_record_repo = InferenceRecordRepository(metadata_store)
        self._proposal_repo = ActionProposalRepository(metadata_store)
        self._step_metadata_repo = StepMetadataRepository(metadata_store)
        self._governance_context: dict[str, Any] | None = None
        self._routing_feedback_context: dict[str, Any] | None = None
        self.routing_runtime = RoutingRuntime(query_router, analytics_engine)
        self.workflow_orchestrator = WorkflowOrchestrator(
            workflow_runtime=self.workflow_runtime,
            step_executor=_ServiceWorkflowStepExecutor(self),
            approval_service=self.approvals,
        )
        self._refresh_calendar_data_reader()

    @property
    def query_router(self) -> QueryRouter | None:
        return self._query_router

    @query_router.setter
    def query_router(self, router: QueryRouter | None) -> None:
        self._query_router = router
        self.routing_runtime.query_router = router

    def _refresh_calendar_data_reader(self) -> None:
        try:
            self.calendar_data_reader = CalendarDataReader(
                metadata=self.metadata,
                config=self.config.calendar,
            )
        except CalendarDataResolutionError as error:
            logger.warning("Calendar data reader unavailable: %s", error)
            self.calendar_data_reader = None

    def create_session(
        self,
        goal: str,
        constraints: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | list[dict[str, Any]] | None = None,
        execution_identity: dict[str, Any] | None = None,
        raw_filter: str | None = None,
    ) -> dict[str, Any]:
        del constraints, raw_filter
        return self.session_manager.create_session(
            goal,
            budget=budget or {},
            policy=policy or {},
            execution_identity=execution_identity or {},
        )

    def list_sessions(
        self,
        status: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        return self.session_manager.list_sessions(
            status=status,
            session_id=session_id,
            limit=limit,
            page_token=page_token,
        )

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.session_manager.get_session(session_id)

    def get_session_runtime_status(self, session_id: str) -> dict[str, Any]:
        return self.session_manager.get_session_runtime_status(session_id)

    def terminate_session(
        self, session_id: str, terminal_reason: str = "user_closed"
    ) -> dict[str, Any]:
        return self.session_manager.terminate_session(session_id, terminal_reason=terminal_reason)

    def get_session_state(self, session_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical SessionStateView for *session_id* (Phase 5b)."""
        self.session_manager.assert_session_exists(session_id)
        return materialize_session_state_view(
            session_id=session_id,
            query=query,
            proposition_repo=self._proposition_repo,
            assessment_repo=self._assessment_repo,
            finding_repo=self._finding_repo,
            gap_repo=self._gap_repo,
            inference_record_repo=self._inference_record_repo,
            proposal_repo=self._proposal_repo,
        )

    def query_session_state(self, session_id: str, query: dict[str, Any]) -> dict[str, Any]:
        """Return the canonical SessionStateView with a structured query body (Phase 5b).

        Identical to :meth:`get_session_state`; the HTTP layer separates GET
        and POST but the service does not distinguish.
        """
        return self.get_session_state(session_id, query)

    def get_artifact_runtime_status(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        """Return artifact-level operator runtime status (Phase 5b)."""
        return self.session_manager.get_artifact_runtime_status(session_id, artifact_id)

    def get_proposition_context(self, session_id: str, proposition_id: str) -> dict[str, Any]:
        """Return PropositionContextView for *proposition_id* (Phase 5c)."""
        from app.evidence_engine.context_view import materialize_proposition_context_view

        return materialize_proposition_context_view(
            session_id=session_id,
            proposition_id=proposition_id,
            proposition_repo=self._proposition_repo,
            assessment_repo=self._assessment_repo,
            finding_repo=self._finding_repo,
            gap_repo=self._gap_repo,
            inference_record_repo=self._inference_record_repo,
            proposal_repo=self._proposal_repo,
        )

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status (Phase 5c)."""
        return self.session_manager.get_proposition_runtime_status(
            session_id,
            proposition_id,
            proposal_repo=self._proposal_repo,
        )

    def discover_catalog(self) -> dict[str, Any]:
        # Entities — all published typed semantic entities
        entity_rows = self.metadata.query_rows(
            """
            SELECT entity_ref, entity_contract_id
            FROM semantic_entity_contracts
            WHERE status = 'published'
            ORDER BY entity_ref
            """
        )
        entities = []
        for row in entity_rows:
            resolved_entity = self.semantic_repository.resolve_entity(
                str(row["entity_ref"]).removeprefix("entity.")
            )
            if resolved_entity is None:
                continue
            entities.append({"id": resolved_entity.name, "keys": list(resolved_entity.key_refs)})

        # Metrics — all published typed semantic metrics
        metric_rows = self.metadata.query_rows(
            """
            SELECT metric_ref
            FROM semantic_metric_contracts
            WHERE status = 'published'
            ORDER BY metric_ref
            """
        )
        metrics = []
        for row in metric_rows:
            resolved_metric = self.semantic_repository.resolve_metric(
                str(row["metric_ref"]).removeprefix("metric.")
            )
            if resolved_metric is None:
                continue
            metrics.append(
                {
                    "id": resolved_metric.name,
                    "label": resolved_metric.display_name,
                    "definition": resolved_metric.definition_sql,
                    "dimensions": list(resolved_metric.dimensions),
                }
            )

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

    def run_step(
        self, session_id: str, step_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
            raise ValueError(
                f"Unsupported step type: {step_type}. Available: {available}"
            ) from error
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

        result["constraints_applied"] = self._build_constraints_applied(session_id, normalized)

        return result

    def run_intent(
        self, session_id: str, intent_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a typed intent step within a session via the IntentRunnerRegistry."""
        self.session_manager.assert_session_is_open(session_id)
        try:
            return self.intent_registry.run(session_id, intent_type, params)
        except KeyError:
            raise ValueError(f"Unknown intent type: '{intent_type}'") from None

    def normalize_intent_metric_ref(self, metric_ref: str) -> str:
        """Normalize a typed-intent metric parameter to canonical ref form for runtime use."""
        return _coerce_metric_ref(metric_ref)

    def metric_name_from_ref(self, metric_ref: str) -> str:
        """Return the short metric name for display or legacy internals."""
        return _metric_name_from_ref(_coerce_metric_ref(metric_ref))

    def _resolve_metric_table(
        self,
        metric_ref: str,
        *,
        session_id: str | None = None,
    ) -> str | None:
        """Resolve an execution-ready table for a metric, if one can be derived."""
        try:
            return self._resolve_metric_execution_context(
                metric_ref,
                session_id=session_id,
            ).table_name
        except (SemanticRuntimeNotReadyError, ValueError):
            return None

    def _resolve_metric_execution_context(
        self,
        metric_ref: str,
        *,
        session_id: str | None = None,
    ) -> MetricExecutionContext:
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_name = _metric_name_from_ref(metric_ref)
        try:
            availability = self.semantic_repository.inspect_ref(metric_ref)
        except (SemanticRuntimeInvalidRefError, SemanticRuntimeNotFoundError):
            raise ValueError(f"Metric '{metric_name}' not found or not published") from None

        if availability.lifecycle_status != "active":
            raise ValueError(f"Metric '{metric_name}' not found or not published")
        if availability.readiness_status != "ready":
            raise SemanticRuntimeNotReadyError(
                f"Semantic ref is not ready: {metric_ref}",
                semantic_ref=metric_ref,
                object_kind=availability.resolved.object_kind,
                lifecycle_status=availability.lifecycle_status,
                readiness_status=availability.readiness_status,
                blocking_requirements=availability.blocking_requirements,
                capabilities=availability.capabilities,
                dependency_refs=availability.dependency_refs,
            )

        metric_family = self._metric_family_for_ref(metric_ref)
        if metric_family is None:
            raise ValueError(f"Metric '{metric_name}' is missing metric_family metadata")
        required_slots = self._required_metric_input_slots(metric_family)
        resolution = self._select_metric_binding_resolution(
            metric_ref,
            required_slots=required_slots,
            session_id=session_id,
        )
        metric_header = dict(availability.resolved.semantic_object.get("header") or {})
        metric_additivity_constraints = metric_header.get("additivity_constraints")
        if resolution is not None and resolution.table_name is not None:
            return MetricExecutionContext(
                metric_ref=metric_ref,
                table_name=resolution.table_name,
                binding_ref=resolution.binding_ref,
                carrier_binding_key=resolution.carrier_binding_key,
                source_object_ref=resolution.source_object_ref,
                carrier_locator=resolution.carrier_locator,
                authority_locator=resolution.authority_locator,
                mapping_id=resolution.mapping_id,
                execution_locator=resolution.execution_locator,
                routing_detail=resolution.routing_detail,
                input_field_map=dict(resolution.input_field_map),
                additivity_constraints=metric_additivity_constraints,
            )
        candidate_bindings = self._metric_binding_candidates(metric_ref, session_id=session_id)
        metric_input_failures = [
            candidate
            for candidate in candidate_bindings
            if candidate.get("failure_stage") == "metric_input_coverage"
        ]
        if metric_input_failures:
            missing_slots = sorted(
                {
                    str(slot)
                    for candidate in metric_input_failures
                    for slot in list(candidate.get("missing_metric_input_slots") or [])
                    if str(slot).strip()
                }
            )
            raise ValueError(
                f"Metric execution binding for '{metric_name}' is missing required metric_input "
                f"coverage ({', '.join(missing_slots)})"
            )
        has_mapping_failures = any(
            candidate.get("failure_stage") == "mapping_route_preflight"
            for candidate in candidate_bindings
        )

        raise SemanticRuntimeNotReadyError(
            f"Metric execution preflight failed: {metric_ref}",
            semantic_ref=metric_ref,
            object_kind=availability.resolved.object_kind,
            lifecycle_status=availability.lifecycle_status,
            readiness_status=availability.readiness_status,
            blocking_requirements=[
                {
                    "code": "METRIC_EXECUTION_BINDING_UNRESOLVED",
                    "message": (
                        "Metric is ready in the semantic layer, but execution could not resolve "
                        "any published binding carrier to an execution route."
                        if has_mapping_failures
                        else "Metric is ready in the semantic layer, but execution could not "
                        "resolve any published binding carrier to a synced source object."
                    ),
                    "subject_ref": metric_ref,
                    "details": {
                        "failure_stage": "metric_execution_preflight",
                        "candidate_bindings": candidate_bindings,
                    },
                }
            ],
            capabilities=availability.capabilities,
            dependency_refs=availability.dependency_refs,
        )

    def _metric_binding_candidates(
        self,
        metric_ref: str,
        *,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_family = self._metric_family_for_ref(metric_ref)
        required_slots = self._required_metric_input_slots(metric_family) if metric_family else ()
        candidates: list[dict[str, Any]] = []
        for binding in self._published_bindings_for_object_ref(metric_ref):
            interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
            carriers = list(interface_contract.get("carrier_bindings") or [])
            input_field_map = self._metric_input_field_map_from_binding(binding)
            missing_slots = [
                required_slot
                for required_slot in required_slots
                if input_field_map.get(required_slot) is None
            ]
            ordered_carriers = sorted(
                carriers,
                key=lambda carrier: str(carrier.get("binding_role") or "") != "primary",
            )
            for carrier in ordered_carriers:
                source_row = self._resolve_metric_carrier_source_object(carrier)
                route_resolution = (
                    self._resolve_metric_binding_route(session_id, source_row)
                    if source_row is not None
                    else None
                )
                runtime_table_name = (
                    route_resolution.table_name if route_resolution is not None else None
                )
                authority_locator = (
                    dict(source_row["authority_locator"]) if source_row is not None else None
                )
                candidates.append(
                    {
                        "binding_ref": binding.ref,
                        "carrier_binding_key": carrier.get("binding_key"),
                        "binding_role": carrier.get("binding_role"),
                        "source_object_ref": carrier.get("source_object_ref"),
                        "carrier_locator": carrier.get("carrier_locator"),
                        "authority_locator": authority_locator,
                        "resolved_source_object_ref": (
                            str(source_row["object_id"]) if source_row is not None else None
                        ),
                        "resolved_table_name": runtime_table_name,
                        "mapping_id": (
                            route_resolution.mapping_id if route_resolution is not None else None
                        ),
                        "execution_locator": (
                            dict(route_resolution.execution_locator)
                            if route_resolution is not None
                            and route_resolution.execution_locator is not None
                            else None
                        ),
                        "routing_detail": (
                            dict(route_resolution.routing_detail)
                            if route_resolution is not None and route_resolution.routing_detail
                            else None
                        ),
                        "readiness_blockers": (
                            list(route_resolution.readiness_blockers)
                            if route_resolution is not None
                            else None
                        ),
                        "metric_input_slots": dict(input_field_map),
                        "missing_metric_input_slots": missing_slots,
                        "failure_stage": (
                            "metric_input_coverage"
                            if missing_slots
                            else "source_object_lookup"
                            if source_row is None
                            else "mapping_route_preflight"
                            if (
                                route_resolution is None
                                or route_resolution.table_name is None
                                or route_resolution.execution_locator is None
                            )
                            else None
                        ),
                    }
                )
        return candidates

    def _metric_family_for_ref(self, metric_ref: str) -> str | None:
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            return None
        header = resolved.semantic_object.get("header") or {}
        return _optional_str(header.get("metric_family"))

    def _required_metric_input_slots(self, metric_family: str) -> tuple[str, ...]:
        return required_metric_input_slots(metric_family)

    def _metric_input_field_map_from_binding(
        self, binding: ResolvedSemanticObject
    ) -> dict[str, str]:
        interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
        field_bindings = list(interface_contract.get("field_bindings") or [])
        input_field_map: dict[str, str] = {}
        for field_binding in field_bindings:
            target = field_binding.get("target") or {}
            if target.get("target_kind") != "metric_input":
                continue
            target_key = _optional_str(target.get("target_key"))
            surface_ref = _optional_str(field_binding.get("surface_ref"))
            if target_key is None or surface_ref is None:
                continue
            physical_name = surface_ref.split(".", 1)[-1] if "." in surface_ref else surface_ref
            input_field_map[target_key] = physical_name
        return input_field_map

    def _select_metric_binding_resolution(
        self,
        metric_ref: str,
        *,
        required_slots: tuple[str, ...] = (),
        session_id: str | None = None,
    ) -> MetricBindingResolution | None:
        metric_ref = _coerce_metric_ref(metric_ref)
        viable_resolutions: list[MetricBindingResolution] = []
        ambiguous_resolutions: list[MetricBindingResolution] = []
        preferred_order_seen = False
        for binding in self._published_bindings_for_object_ref(metric_ref):
            if binding.object_kind != "binding":
                continue
            binding_header = dict(binding.semantic_object.get("header") or {})
            if _optional_str(binding_header.get("binding_scope")) != "metric":
                continue
            if _optional_str(binding_header.get("bound_object_ref")) != metric_ref:
                continue
            input_field_map = self._metric_input_field_map_from_binding(binding)
            if any(input_field_map.get(required_slot) is None for required_slot in required_slots):
                continue
            interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
            carriers = sorted(
                interface_contract.get("carrier_bindings") or [],
                key=lambda carrier: str(carrier.get("binding_role") or "") != "primary",
            )
            for carrier in carriers:
                source_row = self._resolve_metric_carrier_source_object(carrier)
                route_resolution = (
                    self._resolve_metric_binding_route(session_id, source_row)
                    if source_row is not None
                    else None
                )
                if (
                    source_row is None
                    or route_resolution is None
                    or route_resolution.table_name is None
                    or route_resolution.execution_locator is None
                ):
                    continue
                resolution = MetricBindingResolution(
                    metric_ref=metric_ref,
                    binding_ref=binding.ref,
                    carrier_binding_key=_optional_str(carrier.get("binding_key")),
                    source_object_ref=_optional_str(carrier.get("source_object_ref")),
                    carrier_locator=_carrier_locator_dict(carrier.get("carrier_locator")),
                    authority_locator=dict(source_row["authority_locator"]),
                    mapping_id=route_resolution.mapping_id,
                    execution_locator=dict(route_resolution.execution_locator),
                    routing_detail=dict(route_resolution.routing_detail),
                    table_name=route_resolution.table_name,
                    input_field_map=input_field_map,
                )
                is_primary = _optional_str(carrier.get("binding_role")) == "primary"
                if is_primary:
                    if not preferred_order_seen:
                        viable_resolutions.clear()
                        preferred_order_seen = True
                    ambiguous_resolutions.append(resolution)
                elif not preferred_order_seen:
                    viable_resolutions.append(resolution)
                break

        selected_pool = ambiguous_resolutions if preferred_order_seen else viable_resolutions
        if not selected_pool:
            return None
        if len(selected_pool) > 1:
            binding_refs = sorted({resolution.binding_ref for resolution in selected_pool})
            raise ValueError(
                "Metric execution binding is ambiguous for "
                f"{metric_ref}: multiple published bindings satisfy typed execution "
                f"requirements ({', '.join(binding_refs)})"
            )
        return selected_pool[0]

    def _resolve_metric_carrier_source_object(
        self, carrier_binding: dict[str, Any]
    ) -> dict[str, Any] | None:
        source_object_ref = _optional_str(carrier_binding.get("source_object_ref"))
        if source_object_ref is not None:
            row = self.metadata.query_one(
                "SELECT * FROM source_objects WHERE object_id = ? OR fqn = ?",
                [source_object_ref, source_object_ref],
            )
            if row is not None:
                source_object = dict(row)
                source_object["authority_locator"] = normalize_source_object_authority_locator(
                    self.metadata,
                    source_object,
                )
                return source_object

        carrier_locator = _carrier_locator_dict(carrier_binding.get("carrier_locator"))
        if carrier_locator is None:
            return None
        rows = self.metadata.query_rows(
            "SELECT * FROM source_objects WHERE object_type = ?", ["table"]
        )
        matches: list[dict[str, Any]] = []
        for row in rows:
            source_object = dict(row)
            source_object["authority_locator"] = normalize_source_object_authority_locator(
                self.metadata,
                source_object,
            )
            authority_locator = source_object["authority_locator"]
            if all(
                carrier_locator.get(key) is None
                or authority_locator.get(key) == carrier_locator.get(key)
                for key in ("catalog", "schema", "table")
            ):
                matches.append(source_object)
        if len(matches) == 1:
            return matches[0]
        return None

    def _resolve_metric_binding_route(
        self,
        session_id: str | None,
        source_object: dict[str, Any],
    ) -> MetricCarrierRoutePreflight | None:
        query_router = self.routing_runtime.query_router
        if query_router is None:
            return None
        authority_locator = normalize_source_object_authority_locator(
            self.metadata,
            source_object,
        )
        authority_table_name = ".".join(
            value
            for value in [
                _optional_str(authority_locator.get("catalog")),
                _optional_str(authority_locator.get("schema")),
                _optional_str(authority_locator.get("table")),
            ]
            if value is not None
        )
        if not authority_table_name:
            return None
        route_resolution = query_router.resolve_route(
            [authority_table_name],
            session_id=session_id,
        )
        route_detail = (
            dict(route_resolution.route.routing_detail)
            if route_resolution.route is not None
            else dict(route_resolution.failure.routing_detail)
            if route_resolution.failure is not None
            else {}
        )
        execution_locator = dict(
            route_detail.get("execution_locators", {}).get(authority_table_name) or {}
        )
        readiness_blockers = [
            blocker
            for blocker in list(route_detail.get("readiness_blockers") or [])
            if isinstance(blocker, dict)
        ]
        selected_mapping_ids = [
            str(mapping_id)
            for mapping_id in list(route_detail.get("selected_mapping_ids") or [])
            if str(mapping_id).strip()
        ]
        return MetricCarrierRoutePreflight(
            table_name=(
                self._executable_metric_table_name(
                    route_resolution.route.datasource_id,
                    execution_locator,
                )
                if route_resolution.route is not None
                else None
            ),
            mapping_id=_optional_str(execution_locator.get("mapping_id"))
            or (selected_mapping_ids[0] if selected_mapping_ids else None),
            execution_locator=execution_locator or None,
            routing_detail=route_detail,
            readiness_blockers=readiness_blockers,
        )

    def _executable_metric_table_name(
        self,
        engine_id: str,
        execution_locator: dict[str, Any],
    ) -> str:
        engine = self.metadata.query_one(
            "SELECT engine_type FROM engines WHERE engine_id = ?",
            [engine_id],
        )
        engine_type = _optional_str(engine["engine_type"]) if engine is not None else None
        return qualify_execution_locator(execution_locator, engine_type=engine_type)

    # ── Metric resolution ────────────────────────────────────────────

    def _resolve_metric_direction(self, metric_ref: str) -> str | None:
        """Look up a published metric's desired_direction for recommendation policy."""
        metric_ref = _coerce_metric_ref(metric_ref)
        resolved = self.semantic_resolver.resolve_metric(_metric_name_from_ref(metric_ref))
        return resolved.desired_direction if resolved else None

    def _resolve_runtime_metric_contract(self, metric_ref: str) -> ResolvedSemanticObject | None:
        metric_ref = _coerce_metric_ref(metric_ref)
        try:
            return self.semantic_repository.resolve_metric_ref(metric_ref)
        except (
            SemanticRuntimeInvalidRefError,
            SemanticRuntimeNotFoundError,
            SemanticRuntimeUnpublishedError,
        ):
            return None

    def resolve_metric_sql(self, metric_ref: str) -> str | None:
        """Resolve an aggregate SQL expression for a published metric."""
        metric_ref = _coerce_metric_ref(metric_ref)
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            return None
        semantic_object = resolved.semantic_object
        header = semantic_object.get("header") or {}
        payload = semantic_object.get("payload") or {}

        # Legacy: definition_sql in payload
        definition_sql = payload.get("definition_sql")
        if definition_sql is not None:
            return str(definition_sql)

        metric_family = _optional_str(header.get("metric_family"))
        typed_metric_ref = _optional_str(header.get("metric_ref"))
        if metric_family is not None and typed_metric_ref is not None:
            return self._compile_typed_metric_sql(
                metric_family,
                payload,
                typed_metric_ref,
            )

        return None

    def resolve_metric_value_sql(self, metric_ref: str) -> str | None:
        """Resolve a per-row value expression for sample-summary style execution."""
        metric_ref = _coerce_metric_ref(metric_ref)
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            return None
        semantic_object = resolved.semantic_object
        header = semantic_object.get("header") or {}
        payload = semantic_object.get("payload") or {}

        definition_sql = payload.get("definition_sql")
        if definition_sql is not None:
            return str(definition_sql)

        metric_family = _optional_str(header.get("metric_family"))
        typed_metric_ref = _optional_str(header.get("metric_ref"))
        if metric_family is not None and typed_metric_ref is not None:
            return self._compile_typed_metric_value_sql(
                metric_family,
                payload,
                typed_metric_ref,
            )

        return None

    def _compile_typed_metric_sql(
        self,
        metric_family: str,
        payload: dict[str, Any],
        metric_ref: str,
        *,
        input_field_map: dict[str, str] | None = None,
        engine_type: str | None = None,
    ) -> str | None:
        """Compile an aggregate SQL expression from a typed metric contract."""
        input_field_map = input_field_map or self._metric_input_field_map(metric_ref)
        if input_field_map is None:
            return None

        if metric_family == "count_metric":
            count_target = payload.get("count_target") or {}
            aggregation = _optional_str(count_target.get("aggregation")) or "count"
            field_name = input_field_map.get("count_target")
            if aggregation == "count_distinct" and field_name:
                return f"COUNT(DISTINCT {field_name})"
            if aggregation == "count" and field_name:
                return f"COUNT({field_name})"
            return None

        if metric_family == "sum_metric":
            field_name = input_field_map.get("measure")
            if field_name:
                return f"SUM({field_name})"
            return None

        if metric_family == "average_metric":
            num_field = input_field_map.get("numerator")
            den_field = input_field_map.get("denominator")
            if num_field and den_field:
                return f"SUM({num_field}) / NULLIF(COUNT({den_field}), 0)"
            return None

        if metric_family == "rate_metric":
            num_field = input_field_map.get("numerator")
            den_field = input_field_map.get("denominator")
            if num_field and den_field:
                return f"SUM({num_field}) / NULLIF(SUM({den_field}), 0)"

        if metric_family == "distribution_metric":
            value_field = input_field_map.get("value_component")
            if value_field is None:
                return None
            distribution_spec = payload.get("distribution_spec") or {}
            kind = _optional_str(distribution_spec.get("kind"))
            if kind in {"percentile", "quantile"}:
                percentile = distribution_spec.get("percentile")
                if percentile is None:
                    raise ValueError(
                        f"Metric '{_metric_name_from_ref(metric_ref)}' is missing "
                        "distribution_spec.percentile for distribution_metric compilation"
                    )
                try:
                    percentile_value = float(percentile)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        f"Metric '{_metric_name_from_ref(metric_ref)}' has a non-numeric "
                        "distribution_spec.percentile"
                    ) from error
                if engine_type == "trino":
                    return f"APPROX_PERCENTILE({value_field}, {percentile_value})"
                if engine_type == "duckdb":
                    return f"QUANTILE_CONT({value_field}, {percentile_value})"
                raise ValueError(
                    f"Metric '{_metric_name_from_ref(metric_ref)}' requires an engine-specific "
                    f"distribution kernel, unsupported engine_type='{engine_type}'"
                )
            if kind == "histogram_ready":
                raise ValueError(
                    f"observe: UNSUPPORTED_OPERATION - metric '{_metric_name_from_ref(metric_ref)}' "
                    "uses distribution_spec.kind='histogram_ready', which standard observe does "
                    "not compile in v1"
                )
            raise ValueError(
                f"observe: UNSUPPORTED_OPERATION - metric '{_metric_name_from_ref(metric_ref)}' "
                f"uses unsupported distribution_spec.kind='{kind}'"
            )

        return None

    def _compile_typed_metric_value_sql(
        self,
        metric_family: str,
        payload: dict[str, Any],
        metric_ref: str,
        *,
        input_field_map: dict[str, str] | None = None,
    ) -> str | None:
        """Compile a per-row value expression from a typed metric contract."""
        input_field_map = input_field_map or self._metric_input_field_map(metric_ref)
        if input_field_map is None:
            return None

        if metric_family == "sum_metric":
            return input_field_map.get("measure")

        if metric_family == "average_metric":
            numerator = payload.get("numerator") or {}
            denominator = payload.get("denominator") or {}
            if (_optional_str(numerator.get("aggregation")) or "sum") == "sum" and (
                _optional_str(denominator.get("aggregation")) or "count"
            ) == "count":
                return input_field_map.get("numerator")

        return None

    def _metric_input_field_map(self, metric_ref: str) -> dict[str, str] | None:
        """Resolve metric_input slot names from the execution binding chosen for the metric."""
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_family = self._metric_family_for_ref(metric_ref)
        if metric_family is None:
            return None
        resolution = self._select_metric_binding_resolution(
            metric_ref,
            required_slots=self._required_metric_input_slots(metric_family),
        )
        if resolution is None:
            return None
        return dict(resolution.input_field_map)

    def resolve_metric_sql_for_execution(
        self,
        metric_ref: str,
        execution_context: MetricExecutionContext | None = None,
        *,
        engine_type: str | None = None,
    ) -> str:
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_name = _metric_name_from_ref(metric_ref)
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published")
        header = resolved.semantic_object.get("header") or {}
        payload = resolved.semantic_object.get("payload") or {}
        definition_sql = payload.get("definition_sql")
        if definition_sql is not None:
            return str(definition_sql)
        metric_family = _optional_str(header.get("metric_family"))
        typed_metric_ref = _optional_str(header.get("metric_ref"))
        if metric_family is None or typed_metric_ref is None:
            raise ValueError(f"Metric '{metric_name}' is missing typed metric metadata")
        input_field_map = (
            dict(execution_context.input_field_map)
            if execution_context is not None and execution_context.input_field_map is not None
            else self._metric_input_field_map(metric_ref)
        )
        sql = self._compile_typed_metric_sql(
            metric_family,
            payload,
            typed_metric_ref,
            input_field_map=input_field_map,
            engine_type=engine_type,
        )
        if sql is None:
            required_slots = ", ".join(self._required_metric_input_slots(metric_family))
            raise ValueError(
                f"Metric execution binding for '{metric_name}' is missing required metric_input "
                f"coverage ({required_slots})"
            )
        return sql

    def resolve_metric_value_sql_for_execution(
        self,
        metric_ref: str,
        execution_context: MetricExecutionContext | None = None,
    ) -> str | None:
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_name = _metric_name_from_ref(metric_ref)
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            raise ValueError(f"Metric '{metric_name}' not found or not published")
        header = resolved.semantic_object.get("header") or {}
        payload = resolved.semantic_object.get("payload") or {}
        definition_sql = payload.get("definition_sql")
        if definition_sql is not None:
            return str(definition_sql)
        metric_family = _optional_str(header.get("metric_family"))
        typed_metric_ref = _optional_str(header.get("metric_ref"))
        if metric_family is None or typed_metric_ref is None:
            raise ValueError(f"Metric '{metric_name}' is missing typed metric metadata")
        input_field_map = (
            dict(execution_context.input_field_map)
            if execution_context is not None and execution_context.input_field_map is not None
            else self._metric_input_field_map(metric_ref)
        )
        return self._compile_typed_metric_value_sql(
            metric_family,
            payload,
            typed_metric_ref,
            input_field_map=input_field_map,
        )

    def resolve_metric_dimensions(self, metric_ref: str) -> list[str] | None:
        """Look up a published metric's dimensions from semantic runtime or entity binding."""
        metric_ref = _coerce_metric_ref(metric_ref)
        resolved = self._resolve_runtime_metric_contract(metric_ref)
        if resolved is None:
            return None
        semantic_object = resolved.semantic_object
        header = semantic_object.get("header") or {}
        payload = semantic_object.get("payload") or {}

        legacy_dims = payload.get("dimensions")
        if legacy_dims is not None:
            return [str(dimension) for dimension in list(legacy_dims)]

        observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
        if observed_entity_ref is not None:
            return self._resolve_entity_dimensions(observed_entity_ref)

        return []

    def _resolve_entity_dimensions(self, entity_ref: str) -> list[str]:
        """Get canonical dimensions exposed by published entity bindings."""
        return resolve_entity_binding_dimensions(self.metadata, entity_ref)

    def build_metric_query(
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
        return compile_metric_query(
            metric_name=metric_name,
            table_name=table_name,
            metric_sql=metric_sql,
            dimensions=dimensions,
            date_column=date_column,
            order=order,
            limit=limit,
        )

    # ── Engine resolution ─────────────────────────────────────────────

    def _resolve_engine(
        self,
        table_names: list[str],
        *,
        session_id: str | None = None,
    ) -> tuple[AnalyticsEngine, str, dict[str, str]]:
        """Resolve the analytics engine, its type, and qualified table names.

        Uses QueryRouter when available, falls back to self.analytics.
        Returns ``(engine, engine_type, qualified_names)`` tuple where
        qualified_names maps native table names to engine-qualified names.
        """
        resolution = self.routing_runtime.resolve_tables(table_names, session_id=session_id)
        self._routing_feedback_context = (
            resolution.feedback.to_dict() if resolution.feedback is not None else None
        )
        qualified = resolution.route.qualified_names if resolution.route is not None else {}
        return resolution.engine, resolution.datasource_type, qualified

    def _resolve_engine_for_session(
        self,
        session_id: str,
        table_names: list[str],
    ) -> tuple[AnalyticsEngine, str, dict[str, str]]:
        try:
            return self._resolve_engine(table_names, session_id=session_id)
        except TypeError as error:
            if "unexpected keyword argument 'session_id'" not in str(error):
                raise
            return self._resolve_engine(table_names)

    def _compile_step_with_feedback(
        self,
        step: AnalysisStepIR,
        *,
        engine_type: str,
        semantic_context: dict[str, Any] | None = None,
    ) -> CompiledQuery:
        effective_semantic_context = dict(semantic_context or {})
        effective_semantic_context.setdefault("semantic_repository", self.semantic_repository)
        effective_semantic_context.setdefault(
            "binding_reader", self._published_bindings_for_object_ref
        )
        effective_semantic_context.setdefault(
            "compatibility_profile_reader",
            self._published_compatibility_profiles_for_subject_ref,
        )
        if self.calendar_data_reader is not None:
            effective_semantic_context.setdefault("calendar_data_reader", self.calendar_data_reader)
        # governance_repository is intentionally NOT injected here.
        # Intent execution (run_intent) does not enforce governance row-filters,
        # so freezing governance_policy_refs into lineage would create a false
        # audit trail.  Wire governance enforcement into the intent path first,
        # then inject governance_repository to populate the lineage.
        try:
            compiled = compile_step(
                step,
                engine_type=engine_type,
                semantic_context=effective_semantic_context,
            )
            execution_context = effective_semantic_context.get("metric_execution_context")
            if isinstance(execution_context, MetricExecutionContext):
                compiled.metadata["metric_execution_context"] = {
                    "metric_ref": execution_context.metric_ref,
                    "binding_ref": execution_context.binding_ref,
                    "carrier_binding_key": execution_context.carrier_binding_key,
                    "source_object_ref": execution_context.source_object_ref,
                    "carrier_locator": dict(execution_context.carrier_locator or {}),
                    "authority_locator": dict(execution_context.authority_locator or {}),
                    "mapping_id": execution_context.mapping_id,
                    "execution_locator": dict(execution_context.execution_locator or {}),
                    "routing_detail": dict(execution_context.routing_detail or {}),
                    "table_name": execution_context.table_name,
                }
            return compiled
        except (
            SemanticRuntimeNotReadyError,
            SemanticRequestCompatibilityError,
            ValueError,
        ) as error:
            raise compile_failure_from_error(
                step,
                error,
                semantic_context=effective_semantic_context,
            ) from error

    def _published_bindings_for_object_ref(self, object_ref: str) -> list[ResolvedSemanticObject]:
        rows = self.metadata.query_rows(
            """
            SELECT binding_ref, MAX(revision) AS revision
            FROM typed_bindings
            WHERE bound_object_ref = ? AND status = 'published'
            GROUP BY binding_ref
            ORDER BY binding_ref
            """,
            [object_ref],
        )
        return [
            self.semantic_repository.resolve_binding_ref(str(row["binding_ref"])) for row in rows
        ]

    def _published_compatibility_profiles_for_subject_ref(
        self, subject_ref: str
    ) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            """
            SELECT *
            FROM compiler_compatibility_profiles
            WHERE subject_ref = ? AND status = 'published'
            ORDER BY profile_ref
            """,
            [subject_ref],
        )
        profiles: list[dict[str, Any]] = []
        for row in rows:
            profiles.append(
                {
                    "profile_id": row["profile_id"],
                    "profile_ref": row["profile_ref"],
                    "profile_kind": row["profile_kind"],
                    "schema_version": row["schema_version"],
                    "subject_kind": row["subject_kind"],
                    "subject_ref": row["subject_ref"],
                    "subject_revision": row["subject_revision"],
                    "requirement": json.loads(row["requirement_json"])
                    if row["requirement_json"]
                    else None,
                    "capability": json.loads(row["capability_json"])
                    if row["capability_json"]
                    else None,
                    "status": row["status"],
                    "revision": row["revision"],
                }
            )
        return profiles

    @staticmethod
    def _table_name_matches_locator(table_name: str, locator: dict[str, Any] | str | None) -> bool:
        normalized_table = table_name.strip()
        locator_dict = _carrier_locator_dict(locator)
        normalized_locator = ""
        if locator_dict is not None:
            normalized_locator = ".".join(
                value
                for value in [
                    _optional_str(locator_dict.get("catalog")),
                    _optional_str(locator_dict.get("schema")),
                    _optional_str(locator_dict.get("table")),
                ]
                if value is not None
            )
        else:
            normalized_locator = str(locator or "").strip()
        if not normalized_table or not normalized_locator:
            return False
        if normalized_table == normalized_locator:
            return True
        return normalized_locator.endswith(f".{normalized_table}") or normalized_table.endswith(
            f".{normalized_locator}"
        )

    def _binding_matches_table(self, binding: ResolvedSemanticObject, table_name: str) -> bool:
        interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
        carrier_bindings = list(interface_contract.get("carrier_bindings") or [])
        if not carrier_bindings:
            return False
        return any(
            self._table_name_matches_locator(
                table_name,
                carrier_binding.get("carrier_locator")
                or _optional_str(carrier_binding.get("source_object_ref")),
            )
            for carrier_binding in carrier_bindings
        )

    @staticmethod
    def _binding_dimension_sources(binding: ResolvedSemanticObject) -> dict[str, set[str]]:
        interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
        carrier_surfaces: dict[tuple[str, str], str] = {}
        for carrier_binding in interface_contract.get("carrier_bindings") or []:
            binding_key = _optional_str(carrier_binding.get("binding_key"))
            if binding_key is None:
                continue
            for field_surface in carrier_binding.get("field_surfaces") or []:
                surface_ref = _optional_str(field_surface.get("surface_ref"))
                physical_name = _optional_str(field_surface.get("physical_name"))
                if surface_ref is None or physical_name is None:
                    continue
                carrier_surfaces[(binding_key, surface_ref)] = physical_name

        dimension_sources: dict[str, set[str]] = {}
        for field_binding in interface_contract.get("field_bindings") or []:
            target = dict(field_binding.get("target") or {})
            if _optional_str(target.get("target_kind")) != "stable_descriptor":
                continue
            dimension_ref = _optional_str(field_binding.get("semantic_ref"))
            carrier_binding_key = _optional_str(field_binding.get("carrier_binding_key"))
            surface_ref = _optional_str(field_binding.get("surface_ref"))
            if (
                dimension_ref is None
                or not dimension_ref.startswith("dimension.")
                or carrier_binding_key is None
                or surface_ref is None
            ):
                continue
            physical_name = carrier_surfaces.get((carrier_binding_key, surface_ref))
            if physical_name is None:
                continue
            dimension_sources.setdefault(dimension_ref, set()).add(physical_name)
        return dimension_sources

    def _metric_scope_dimension_sources(
        self,
        metric_ref: str,
        table_name: str,
    ) -> dict[str, set[str]]:
        metric_ref = _coerce_metric_ref(metric_ref)
        metric_bindings = list(self._published_bindings_for_object_ref(metric_ref))
        matching_bindings = [
            binding
            for binding in metric_bindings
            if self._binding_matches_table(binding, table_name)
        ]
        candidate_bindings = matching_bindings or metric_bindings
        dimension_sources: dict[str, set[str]] = {}

        for binding in candidate_bindings:
            for dimension_ref, physical_names in self._binding_dimension_sources(binding).items():
                dimension_sources.setdefault(dimension_ref, set()).update(physical_names)

            interface_contract = dict(binding.semantic_object.get("interface_contract") or {})
            for binding_import in interface_contract.get("imports") or []:
                imported_binding_ref = _optional_str(
                    binding_import.get("binding_ref") or binding_import.get("imported_binding_ref")
                )
                if imported_binding_ref is None:
                    continue
                imported_binding = self.semantic_repository.resolve_binding_ref(
                    imported_binding_ref
                )
                for dimension_ref, physical_names in self._binding_dimension_sources(
                    imported_binding
                ).items():
                    dimension_sources.setdefault(dimension_ref, set()).update(physical_names)

        return dimension_sources

    def _resolve_scope_constraint_column(
        self,
        constraint_key: str,
        *,
        metric_ref: str | None,
        table_name: str | None,
    ) -> str:
        if "." not in constraint_key:
            return constraint_key
        if not constraint_key.startswith("dimension."):
            raise ValueError(
                f"scope.constraints key '{constraint_key}' must be a physical column or "
                "a canonical dimension ref like 'dimension.cluster'"
            )
        if metric_ref is None or table_name is None:
            raise ValueError(
                f"scope.constraints key '{constraint_key}' requires a semantic metric scope"
            )

        dimension_sources = self._metric_scope_dimension_sources(metric_ref, table_name)
        physical_names = sorted(dimension_sources.get(constraint_key) or [])
        if not physical_names:
            raise ValueError(
                f"scope.constraints key '{constraint_key}' is not available in metric semantic scope"
            )
        if len(physical_names) > 1:
            raise ValueError(
                f"scope.constraints key '{constraint_key}' does not resolve to a unique physical column"
            )
        return physical_names[0]

    @staticmethod
    def _merge_filters(*filters: str | None) -> str | None:
        """AND-merge multiple filter expressions, ignoring None values."""
        parts = [f for f in filters if f]
        if not parts:
            return None
        return " AND ".join(f"({p})" for p in parts)

    def _constraints_dict_to_filter(
        self,
        constraints: dict[str, Any],
        *,
        resolve_semantic_refs: bool = False,
        metric_ref: str | None = None,
        table_name: str | None = None,
    ) -> str | None:
        parts: list[str] = []
        for key, value in constraints.items():
            if isinstance(value, (dict, list)):
                continue
            column_name = key
            if resolve_semantic_refs:
                column_name = self._resolve_scope_constraint_column(
                    key,
                    metric_ref=metric_ref,
                    table_name=table_name,
                )
            parts.append(f"{column_name} = '{value}'")
        return " AND ".join(parts) if parts else None

    def _resolved_scope_filter(
        self,
        session_id: str,
        request: ResolvedWindowedQueryRequest,
    ) -> str | None:
        metric_ref = None
        if isinstance(request.value_spec, SemanticMetricValueSpec):
            metric_ref = request.value_spec.metric
        scope_constraints = self._constraints_dict_to_filter(
            request.scope.constraints,
            resolve_semantic_refs=True,
            metric_ref=metric_ref,
            table_name=request.table,
        )
        scope_predicate = (
            self._resolve_predicate_ref_to_filter(
                request.scope.predicate_ref,
                metric_ref=metric_ref,
                table_name=request.table,
            )
            if request.scope.predicate_ref is not None
            else request.scope.predicate
        )
        return self._merge_filters(
            scope_constraints,
            scope_predicate,
        )

    def _resolve_predicate_ref_to_filter(
        self,
        predicate_ref: str,
        *,
        metric_ref: str | None = None,
        table_name: str | None = None,
    ) -> str | None:
        row = self.metadata.query_one(
            "SELECT payload_json FROM semantic_predicate_contracts "
            "WHERE predicate_ref = ? AND status = 'published'",
            [predicate_ref],
        )
        if row is None:
            raise ValueError(
                f"predicate_ref '{predicate_ref}' does not reference a published predicate"
            )
        payload = json.loads(row["payload_json"] or "{}")
        expression = payload.get("expression")
        if expression is None:
            return None
        return self._predicate_expression_to_sql(
            expression, metric_ref=metric_ref, table_name=table_name
        )

    def _resolve_predicate_target_column(
        self,
        target_ref: str,
        *,
        metric_ref: str | None = None,
        table_name: str | None = None,
    ) -> str:
        if "." not in target_ref:
            return target_ref
        if target_ref.startswith("dimension."):
            return self._resolve_scope_constraint_column(
                target_ref,
                metric_ref=metric_ref,
                table_name=table_name,
            )
        # For entity.*, key.*, field.*, etc. — resolve via binding field surfaces.
        if metric_ref is not None:
            metric_ref = _coerce_metric_ref(metric_ref)
            metric_bindings = list(self._published_bindings_for_object_ref(metric_ref))
            if table_name is not None:
                metric_bindings = [
                    b for b in metric_bindings if self._binding_matches_table(b, table_name)
                ] or metric_bindings
            for binding in metric_bindings:
                for surface in (binding.semantic_object.get("interface_contract") or {}).get(
                    "carrier_bindings"
                ) or []:
                    for fs in surface.get("field_surfaces") or []:
                        if fs.get("semantic_ref") == target_ref:
                            return str(fs.get("physical_name", target_ref))
                for fb in (binding.semantic_object.get("interface_contract") or {}).get(
                    "field_bindings"
                ) or []:
                    if fb.get("semantic_ref") == target_ref:
                        return str(fb.get("surface_ref", target_ref))
        # Fall back: strip prefix and use as column hint (entity.user → user)
        return target_ref.split(".", 1)[-1].replace(".", "_")

    def _predicate_expression_to_sql(
        self,
        expr: dict[str, Any],
        *,
        metric_ref: str | None = None,
        table_name: str | None = None,
    ) -> str:
        op = expr.get("op")
        if op == "and":
            items = expr.get("items") or []
            parts = [
                self._predicate_expression_to_sql(
                    item, metric_ref=metric_ref, table_name=table_name
                )
                for item in items
            ]
            return " AND ".join(parts)
        target_ref = expr.get("target_ref", "")
        column = self._resolve_predicate_target_column(
            target_ref,
            metric_ref=metric_ref,
            table_name=table_name,
        )
        value: Any = expr.get("value")
        if op in ("is_null", "is_not_null"):
            return f"{column} IS NULL" if op == "is_null" else f"{column} IS NOT NULL"
        if op == "between":
            lo, hi = value[0], value[1]
            return f"{column} BETWEEN '{lo}' AND '{hi}'"
        if op in ("in", "not_in"):
            vals = ", ".join(f"'{v}'" for v in value)
            sql_in = f"{column} IN ({vals})"
            return sql_in if op == "in" else f"NOT {sql_in}"
        if value is not None:
            return f"{column} {op} '{value}'"
        return f"{column} {op}"

    def _build_scoped_query(
        self,
        session_id: str,
        request: ResolvedWindowedQueryRequest,
        *,
        engine_type: str,
    ) -> dict[str, Any]:
        analysis_time_expr = request.resolved_time_axis.analysis_time_expr
        if not analysis_time_expr:
            raise ValueError("windowed execution requires resolved_time_axis.analysis_time_expr")
        metric_ref = None
        if isinstance(request.value_spec, SemanticMetricValueSpec):
            metric_ref = request.value_spec.metric
        return {
            "mode": request.time_scope.mode,
            "engine_type": engine_type,
            "analysis_time_kind": request.resolved_time_axis.analysis_time_kind,
            "analysis_time_expr": analysis_time_expr,
            "analysis_time_format": request.resolved_time_axis.analysis_time_format,
            "partition_pruning_predicate": request.resolved_time_axis.partition_pruning_predicate,
            "current": {
                "start": request.time_scope.current.start,
                "end": request.time_scope.current.end,
            },
            "baseline": (
                {
                    "start": request.time_scope.baseline.start,
                    "end": request.time_scope.baseline.end,
                }
                if request.time_scope.baseline is not None
                else None
            ),
            "session_constraints_filter": None,
            "session_raw_filter": None,
            "scope_constraints_filter": self._constraints_dict_to_filter(
                request.scope.constraints,
                resolve_semantic_refs=True,
                metric_ref=metric_ref,
                table_name=request.table,
            ),
            "scope_predicate_filter": (
                self._resolve_predicate_ref_to_filter(
                    request.scope.predicate_ref,
                    metric_ref=metric_ref,
                    table_name=request.table,
                )
                if request.scope.predicate_ref is not None
                else request.scope.predicate
            ),
        }

    @staticmethod
    def _observation_window_for_request(request: ResolvedWindowedQueryRequest) -> dict[str, Any]:
        return {
            "start": request.time_scope.current.start,
            "end": request.time_scope.current.end,
            "granularity": request.resolved_time_axis.observation_grain,
        }

    @classmethod
    def _metric_query_mode_contract(cls, mode: str) -> dict[str, Any]:
        normalized = str(mode).strip().lower()
        contract = cls._METRIC_QUERY_MODE_CONTRACTS.get(normalized)
        if contract is None:
            raise ValueError(f"Unsupported metric_query mode: {mode}")
        payload_fields = dict(contract["payload_fields"])
        required_payload_keys = tuple(contract["required_payload_keys"])
        return {
            "mode": normalized,
            "payload_fields": payload_fields,
            "required_payload_keys": required_payload_keys,
            "required_row_fields": tuple(payload_fields[key] for key in required_payload_keys),
        }

    @classmethod
    def _build_metric_query_extractor_context(
        cls,
        *,
        mode: str,
        metric_name: str,
        observation_type: str,
        dimensions: list[str],
        quality_builder: Any,
    ) -> dict[str, Any]:
        contract = cls._metric_query_mode_contract(mode)
        return {
            "metric": metric_name,
            "observation_type": observation_type,
            "dimensions": dimensions,
            "payload_fields": contract["payload_fields"],
            "required_payload_keys": contract["required_payload_keys"],
            "quality_builder": quality_builder,
        }

    @classmethod
    def _metric_query_quality_builder(cls, mode: str) -> Any:
        normalized = cls._metric_query_mode_contract(mode)["mode"]
        if normalized == "compare":
            return lambda row: {
                "freshness_ok": True,
                "sample_size_ok": min(row["current_sessions"] or 0, row["baseline_sessions"] or 0)
                >= 150,
            }
        return lambda row: {
            "freshness_ok": True,
            "sample_size_ok": (row.get("current_sessions") or 0) >= 150,
        }

    @classmethod
    def _normalize_metric_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        mode: str,
    ) -> list[dict[str, Any]]:
        contract = cls._metric_query_mode_contract(mode)
        normalized: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            row_dict = dict(row)
            missing = [field for field in contract["required_row_fields"] if field not in row_dict]
            if missing:
                missing_str = ", ".join(missing)
                raise ValueError(
                    f"metric_query rows missing required columns at row {index}: {missing_str}"
                )
            normalized.append(row_dict)
        return normalized

    @staticmethod
    def _comparison_slice_label(row: dict[str, Any], dimensions: list[str]) -> str:
        if not dimensions:
            return "overall"
        parts = [
            f"{dimension}={row[dimension]}"
            for dimension in dimensions
            if row.get(dimension) is not None
        ]
        return ", ".join(parts) if parts else "overall"

    @classmethod
    def _metric_query_debug_payload(
        cls,
        request: ResolvedWindowedQueryRequest,
        *,
        all_rows: list[dict[str, Any]],
        window_length_match: bool | None = None,
    ) -> dict[str, Any]:
        debug = {
            "current_window": [request.time_scope.current.start, request.time_scope.current.end],
            "current_has_data": any(row.get("current_sessions") for row in all_rows),
        }
        if request.time_scope.mode == "single_window":
            return debug
        if request.time_scope.baseline is None:
            raise ValueError("metric_query debug payload requires baseline window")
        debug.update(
            {
                "baseline_window": [
                    request.time_scope.baseline.start,
                    request.time_scope.baseline.end,
                ],
                "baseline_has_data": any(row.get("baseline_sessions") for row in all_rows),
                "window_length_match": bool(window_length_match),
            }
        )
        return debug

    @classmethod
    def _metric_query_summary(
        cls,
        metric_name: str,
        rows: list[dict[str, Any]],
        *,
        mode: str,
        debug: dict[str, Any],
        dimensions: list[str],
        grain: str,
        current_len: int | None = None,
        baseline_len: int | None = None,
    ) -> str:
        if mode == "single_window":
            if rows:
                top = rows[0]
                slice_label = cls._comparison_slice_label(top, dimensions)
                return (
                    f"Metric '{metric_name}' current window observation: highest value is "
                    f"{top['current_value']} for {slice_label} "
                    f"(current_sessions={top['current_sessions']})."
                )
            if debug["current_has_data"]:
                return (
                    f"Metric '{metric_name}' current window observation returned no retained rows. "
                    f"current_window={debug['current_window']}."
                )
            return (
                f"Metric '{metric_name}' current window has no data. "
                f"current_window={debug['current_window']}."
            )

        if rows:
            top = rows[0]
            direction = "decline" if (top.get("delta_pct") or 0) < 0 else "increase"
            slice_label = cls._comparison_slice_label(top, dimensions)
            summary = (
                f"Metric '{metric_name}' comparison: top {direction} is "
                f"{top['delta_pct']}% for {slice_label} "
                f"(current_value={top['current_value']}, baseline_value={top['baseline_value']})."
            )
            if not debug["window_length_match"]:
                if current_len is None or baseline_len is None:
                    raise ValueError("metric_query compare summary requires both window lengths")
                unit = "h" if grain == "hour" else "d"
                summary += (
                    f" Window size mismatch: current={current_len}{unit}, "
                    f"baseline={baseline_len}{unit}; count/sum metrics may not be comparable."
                )
            return summary

        if debug["current_has_data"] or debug["baseline_has_data"]:
            missing = []
            if not debug["current_has_data"]:
                missing.append("current")
            if not debug["baseline_has_data"]:
                missing.append("baseline")
            missing_str = " and ".join(missing) if missing else "one"
            return (
                f"Metric '{metric_name}' comparison: {missing_str} window has no data. "
                f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
            )

        return (
            f"Metric '{metric_name}' comparison returned no results. "
            f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
        )

    @staticmethod
    def _window_length(request: ResolvedWindowedQueryRequest, which: str) -> int:
        if which == "current":
            window = request.time_scope.current
        else:
            if request.time_scope.baseline is None:
                raise ValueError("baseline window is not available")
            window = request.time_scope.baseline
        if request.time_scope.grain == "hour":
            start_dt = datetime.fromisoformat(window.start)
            end_dt = datetime.fromisoformat(window.end)
            return int((end_dt - start_dt).total_seconds() // 3600)
        start_day = date.fromisoformat(window.start)
        end_day = date.fromisoformat(window.end)
        return (end_day - start_day).days

    def _resolve_windowed_query_time_axis(
        self,
        request: ResolvedWindowedQueryRequest,
        *,
        engine_type: str,
        metric_name: str | None = None,
        fallback_columns: list[str] | None = None,
    ) -> None:
        has_explicit_override = any(
            (
                request.resolved_time_axis.override_analysis_time_column,
                request.resolved_time_axis.override_partition_date_column,
                request.resolved_time_axis.override_partition_hour_column,
            )
        )
        try:
            metadata_context = self.time_axis_metadata_provider.load_for_windowed_query(
                table_name=request.table,
                metric_name=metric_name,
            )
        except ValueError:
            if not has_explicit_override:
                raise
            metadata_context = TimeAxisMetadataContext(
                available_columns=self.time_axis_metadata_provider.load_available_columns(
                    request.table
                )
            )

        available_columns = list(metadata_context.available_columns)
        if available_columns:
            for column in fallback_columns or []:
                name = str(column).strip()
                if name and name not in available_columns:
                    available_columns.append(name)

        resolver = TimeAxisResolver(
            request=request,
            engine_type=engine_type,
            available_columns=available_columns,
            entity_time_capabilities=metadata_context.entity_time_capabilities,
            source_time_capabilities=metadata_context.source_time_capabilities,
        )
        request.resolved_time_axis = resolver.resolve()

    @classmethod
    def _normalize_metric_query_order(cls, order: str | None, *, mode: str) -> str | None:
        normalized_mode = cls._metric_query_mode_contract(mode)["mode"]
        if order is None:
            return "CURRENT_VALUE DESC" if normalized_mode == "single_window" else None
        normalized = order.strip().upper()
        if normalized_mode == "compare":
            if normalized in {"ASC", "DESC"}:
                return f"DELTA_PCT {normalized}"
            if normalized in {"DELTA_PCT ASC", "DELTA_PCT DESC"}:
                return normalized
            raise ValueError("metric_query compare mode supports only delta_pct ASC/DESC")
        if normalized in {
            "CURRENT_VALUE ASC",
            "CURRENT_VALUE DESC",
            "CURRENT_SESSIONS ASC",
            "CURRENT_SESSIONS DESC",
        }:
            return normalized
        raise ValueError(
            "metric_query single_window mode supports only current_value ASC/DESC or current_sessions ASC/DESC"
        )

    _CONSTRAINT_APPLYING_STEPS = frozenset(
        {"metric_query", "sample_rows", "aggregate_query", "attribute_change"}
    )

    _CONSTRAINT_SKIP_REASONS: ClassVar[dict[str, str]] = {
        "profile_table": "profile_table scans the full table; session filters are not applied",
    }

    def _build_constraints_applied(self, session_id: str, step_type: str) -> dict[str, Any]:
        del session_id, step_type
        return {"applied": [], "skipped": [], "note": None}

    def _run_metric_query(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Generic metric comparison step driven by semantic metric definitions.

        Externally and internally uses the TSU typed contract, with
        `scoped_query` carrying the resolved time/window execution context.
        """
        resolved = normalize_metric_query_request(params)
        if not isinstance(resolved.value_spec, SemanticMetricValueSpec):
            raise ValueError("metric_query requires a semantic metric request")
        mode = resolved.time_scope.mode
        metric_name = resolved.value_spec.metric

        step_type = "metric_query"
        step_id = self._new_step_id()

        metric_sql = self.resolve_metric_sql(metric_name)
        all_dimensions = self.resolve_metric_dimensions(metric_name)
        if metric_sql is None or all_dimensions is None:
            raise ValueError(
                f"Metric '{metric_name}' not found, not published, or missing typed execution metadata"
            )

        engine, engine_type, qualified = self._resolve_engine_for_session(
            session_id,
            [resolved.table],
        )
        self._resolve_windowed_query_time_axis(
            resolved,
            engine_type=engine_type,
            metric_name=metric_name,
            fallback_columns=all_dimensions,
        )
        scoped_query = self._build_scoped_query(session_id, resolved, engine_type=engine_type)
        comparison_time_column = self._comparison_time_dimension_column(resolved, all_dimensions)

        # Allow caller to select a subset of dimensions for grouping
        requested_dims = list(resolved.grouping)
        if requested_dims:
            invalid = set(requested_dims) - set(all_dimensions)
            if invalid:
                raise ValueError(f"Invalid dimensions {invalid}; valid: {all_dimensions}")

        dimensions = self._comparison_dimensions(
            all_dimensions,
            comparison_time_column,
            requested=requested_dims,
        )
        if requested_dims and not dimensions:
            filtered_out = [d for d in requested_dims if d == comparison_time_column]
            raise ValueError(
                f"Cannot use '{filtered_out[0]}' as comparison dimension because "
                f"it is the period-splitting column (date_column='{comparison_time_column}'). "
                f"Use a different dimension or omit dimensions for overall aggregate comparison."
            )
        limit = resolved.limit or 10

        qualified_table = qualified.get(resolved.table, resolved.table)
        current_len = self._window_length(resolved, "current")
        baseline_len: int | None = None
        window_size_mismatch = False
        if mode == "compare":
            baseline_len = self._window_length(resolved, "baseline")
            window_size_mismatch = current_len != baseline_len
        compiled_query = self._compile_step_with_feedback(
            AnalysisStepIR(
                index=0,
                step_type=step_type,
                params={
                    key: value
                    for key, value in {
                        "table": qualified_table,
                        "metric": metric_name,
                        "limit": limit,
                        "order": self._normalize_metric_query_order(resolved.order, mode=mode),
                        "scoped_query": scoped_query,
                    }.items()
                    if value is not None
                },
            ),
            engine_type=engine_type,
            semantic_context={
                "metric_sql": metric_sql,
                "dimensions": dimensions,
            },
        )
        all_rows = self._normalize_metric_rows(
            execute_compiled(engine, compiled_query).rows,
            mode=mode,
        )
        if mode == "compare":
            rows = [row for row in all_rows if row.get("delta_pct") is not None]
        else:
            rows = list(all_rows)
        artifact_id = self._insert_artifact(
            session_id, step_id, "table", f"{metric_name}_metric_query", rows
        )

        _debug = self._metric_query_debug_payload(
            resolved,
            all_rows=all_rows,
            window_length_match=(not window_size_mismatch) if mode == "compare" else None,
        )
        summary = self._metric_query_summary(
            metric_name,
            rows,
            mode=mode,
            debug=_debug,
            dimensions=dimensions,
            grain=resolved.time_scope.grain,
            current_len=current_len,
            baseline_len=baseline_len,
        )

        provenance = self._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )

        # G-5e: resolve unit for the metric from confirmed hints or entity properties
        unit_note = self._resolve_metric_unit_note(metric_name)

        result: dict[str, Any] = {
            "step_type": step_type,
            "metric_name": metric_name,
            "summary": summary,
            "artifact_id": artifact_id,
        }
        if unit_note:
            result["unit_note"] = unit_note
        if not rows:
            result["debug"] = _debug
        elif mode == "compare" and window_size_mismatch:
            result["debug"] = {
                k: _debug[k] for k in ("current_window", "baseline_window", "window_length_match")
            }
        self._insert_step(
            step_id,
            session_id,
            step_type,
            summary,
            result,
            provenance=provenance,
        )
        return result

    def _resolve_metric_unit_note(self, metric_ref: str) -> str | None:
        """G-5e: Return a concise unit note for a metric if one is available.

        Returns field-level units from the published entity properties, or None.
        """
        try:
            # Priority 1: published entity field-level units (properties.fields.<col>.unit)
            entity = self._resolve_entity_for_metric(metric_ref)
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

        except Exception:
            pass
        return None

    def _fetch_column_metadata(
        self, short_name: str, columns: list[str]
    ) -> dict[str, dict[str, str]]:
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
        engine, engine_type, qualified = self._resolve_engine_for_session(session_id, [table_name])
        qualified_table = qualified.get(table_name, table_name)

        row_count_query = self._compile_step_with_feedback(
            AnalysisStepIR(
                index=0, step_type="profile_table_row_count", params={"table_name": qualified_table}
            ),
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
            if dc in columns:
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
                "scoped_row_count": col_profiles[0]["total"]
                if col_profiles and "total" in col_profiles[0]
                else None,
            }
        # If the row-count query failed and no columns were found, the table
        # does not exist (or is otherwise completely inaccessible).  Raise so
        # that plan execution marks this step as failed.  When a table exists
        # but COUNT(*) is merely rejected (e.g. Trino mandatory partition
        # filter), the info_schema columns query still returns rows, so
        # `columns` will be non-empty and we fall through to the partial
        # profile path.
        if row_count_error is not None and not columns:
            raise ValueError(f"Table '{table_name}' is inaccessible: {row_count_error}")

        profile_errors: dict[str, str] = {}
        if row_count_error is not None:
            profile_errors["row_count"] = row_count_error
        if not columns_available and columns_error is not None:
            profile_errors["columns"] = columns_error
        artifact: dict[str, Any] = {
            "table_name": table_name,
            "row_count": row_count,
            "profile_scope": profile_scope,
            "columns": col_profiles,
        }
        if profile_errors:
            artifact["errors"] = profile_errors
        artifact_id = self._insert_artifact(
            session_id, step_id, "profile", f"{short_name}_profile", artifact
        )

        scope_note = (
            f" (column stats scoped to {profile_date_column}={profile_date_value})"
            if profile_date_column
            else ""
        )
        failure_notes: list[str] = []
        if row_count_error is not None:
            failure_notes.append(f"row_count unavailable: {row_count_error}")
        if not columns_available:
            col_detail = f": {columns_error}" if columns_error else ""
            failure_notes.append(
                f"columns unavailable (schema query failed{col_detail}; use sample_rows limit=1 to inspect columns)"
            )
        if failure_notes:
            failure_str = "; ".join(failure_notes)
            summary = f"Table '{table_name}' profile incomplete — {failure_str}."
        else:
            summary = (
                f"Table '{table_name}' has {row_count} rows and {len(columns)} columns{scope_note}."
            )
        provenance = self._make_provenance(f"profile:{table_name}", engine_type=engine_type)
        result = {
            "step_type": step_type,
            "summary": summary,
            "artifact_id": artifact_id,
            "profile": artifact,
        }
        self._insert_step(
            step_id,
            session_id,
            step_type,
            summary,
            result,
            provenance=provenance,
        )
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

        user_filter = params.get("filter")
        if user_filter:
            params = {**params, "filter": user_filter}

        step_type = "sample_rows"
        step_id = self._new_step_id()

        limit = int(params.get("limit", 10))
        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine_for_session(session_id, [table_name])
        qualified_table = qualified.get(table_name, table_name)

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
            AnalysisStepIR(index=0, step_type=step_type, params=compiler_params),
            engine_type=engine_type,
        )
        rows = execute_compiled(engine, compiled_query).rows

        actual_columns = list(rows[0].keys()) if rows else list(params.get("columns") or [])
        col_metadata = self._fetch_column_metadata(short_name, actual_columns)

        artifact_id = self._insert_artifact(
            session_id, step_id, "sample", f"{short_name}_sample", rows
        )
        summary = f"Sampled {len(rows)} rows from '{table_name}'."
        provenance = self._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        result = {
            "step_type": step_type,
            "summary": summary,
            "artifact_id": artifact_id,
            "rows": rows,
            "columns_metadata": col_metadata,
        }
        self._insert_step(
            step_id,
            session_id,
            step_type,
            summary,
            result,
            provenance=provenance,
            semantic_metadata=self.build_step_semantic_metadata(compiled_query),
        )
        return result

    def _run_aggregate_query(self, session_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run an ad-hoc GROUP BY + aggregation query.

        Public contract:
            table: fully qualified table name
            measures: list of aggregate expressions with explicit aliases
            time_scope: typed time window contract
        Optional:
            group_by: grouping columns
            scope: non-time constraints / predicate
            time_axis: advanced analysis-time override
            order: output ordering expression
            limit: max rows (default: 100)

        """
        resolved = normalize_aggregate_query_request(params)
        table_name = resolved.table

        step_type = "aggregate_query"
        step_id = self._new_step_id()
        short_name = table_name.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine_for_session(session_id, [table_name])
        self._resolve_windowed_query_time_axis(
            resolved,
            engine_type=engine_type,
            fallback_columns=list(resolved.grouping),
        )
        scoped_query = self._build_scoped_query(session_id, resolved, engine_type=engine_type)
        qualified_table = qualified.get(table_name, table_name)

        measures = (
            resolved.value_spec.measures
            if isinstance(resolved.value_spec, AdHocAggregateValueSpec)
            else []
        )
        compiler_params: dict[str, Any] = {
            "table": qualified_table,
            "measures": [{"expr": measure.expr, "as": measure.alias} for measure in measures],
            "group_by": list(resolved.grouping),
            "limit": resolved.limit or 100,
            "scoped_query": scoped_query,
        }
        if resolved.order:
            compiler_params["order"] = resolved.order

        compiled_query = self._compile_step_with_feedback(
            AnalysisStepIR(index=0, step_type=step_type, params=compiler_params),
            engine_type=engine_type,
        )
        rows = execute_compiled(engine, compiled_query).rows
        compare_period = resolved.time_scope.mode == "compare"

        artifact_id = self._insert_artifact(
            session_id, step_id, "aggregate", f"{short_name}_aggregate", rows
        )
        if not rows:
            _partition_cols = {"log_date", "event_date", "dt", "date", "day"}
            where_lower = str(scoped_query.get("partition_pruning_predicate") or "").lower()
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
            _baseline = resolved.time_scope.baseline
            summary = (
                f"Period-over-period aggregate on '{table_name}': "
                f"{len(rows)} dimension slice(s) compared "
                f"(current {resolved.time_scope.current.start}–{resolved.time_scope.current.end} vs "
                f"baseline {_baseline.start if _baseline else '?'}–{_baseline.end if _baseline else '?'})."
            )
        else:
            summary = f"Aggregate query on '{table_name}' returned {len(rows)} rows."
        provenance = self._make_provenance(
            compiled_query.sql, compiled_query.params, engine_type=engine_type
        )
        result = {
            "step_type": step_type,
            "summary": summary,
            "artifact_id": artifact_id,
            "rows": rows,
        }
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
        candidate_dimensions = [
            str(dim).strip() for dim in candidate_dimensions_raw if str(dim).strip()
        ]
        candidate_dimensions = list(dict.fromkeys(candidate_dimensions))
        if not candidate_dimensions:
            raise ValueError("candidate_dimensions must not be empty")

        metric_sql = self.resolve_metric_sql(str(metric_name))
        if metric_sql is None:
            raise ValueError(
                f"Metric '{metric_name}' not found, not published, or missing typed execution metadata"
            )

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
        merged_where = user_where

        table_name_str = str(table_name)
        short_name = table_name_str.split(".")[-1]
        engine, engine_type, qualified = self._resolve_engine_for_session(
            session_id,
            [table_name_str],
        )
        qualified_table = qualified.get(table_name_str, table_name_str)

        try:
            row = engine.query_rows(
                f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}"
            )[0]
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

        contributions: list[dict[str, Any]] = []
        query_sql_parts: list[str] = []
        query_params: list[Any] = []
        compiled_queries: list[CompiledQuery] = []
        current_has_data = False
        baseline_has_data = False

        for dimension in candidate_dimensions:
            select_exprs = [dimension, f"{metric_sql} AS metric_value"]
            step_ir = AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table_name": qualified_table,
                    "select": select_exprs,
                    "group_by": [dimension],
                    "compare_period": True,
                    "date_column": date_column,
                    "limit": query_limit,
                    **({"where": merged_where} if merged_where else {}),
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
            compiled_queries.append(compiled_query)

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
                delta_pct = (
                    None if baseline_value == 0.0 else (delta_value / baseline_value) * 100.0
                )
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
                    (abs(entry["delta_value"]) / total_abs_delta) * 100.0
                    if total_abs_delta > 0
                    else 0.0
                )

            sorted_contributors = sorted(
                dim_contributors,
                key=lambda entry: (
                    abs(entry["delta_pct"])
                    if entry["delta_pct"] is not None
                    else abs(entry["delta_value"]),
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
            "debug": debug,
        }

        self._insert_step(
            step_id,
            session_id,
            "attribute_change",
            summary,
            result,
            provenance=provenance,
            semantic_metadata=self.build_step_semantic_metadata(compiled_queries),
        )
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
                raise ValueError(
                    f"No artifact found for step_id={step_id!r} in session {session_id!r}"
                )
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

    def _resolve_entity_for_metric(self, metric_ref: str) -> dict[str, Any] | None:
        """Return the published entity linked to the given metric name, or None."""
        try:
            metric_ref = _coerce_metric_ref(metric_ref)
            resolved_metric = self.semantic_repository.resolve_metric_ref(metric_ref)
            observed_entity_ref = resolved_metric.semantic_object.get("header", {}).get(
                "observed_entity_ref"
            )
            if not observed_entity_ref:
                return None
            resolved_entity = self.semantic_repository.resolve_entity(
                str(observed_entity_ref).removeprefix("entity.")
            )
            if resolved_entity is None:
                return None
            return {
                "entity_contract_id": resolved_entity.metadata.get("entity_contract_id"),
                "name": resolved_entity.name,
                "status": resolved_entity.metadata.get("status"),
                "properties": dict(resolved_entity.metadata.get("properties") or {}),
            }
        except Exception:
            return None

    # ── Metadata helpers ──────────────────────────────────────────────

    def _reset_session_outputs(self, session_id: str) -> None:
        for table in ["artifacts", "steps"]:
            self.metadata.execute(f"DELETE FROM {table} WHERE session_id = ?", [session_id])

    def _delete_step_outputs(self, session_id: str, step_type: str) -> None:
        rows = self.metadata.query_rows(
            "SELECT step_id FROM steps WHERE session_id = ? AND step_type = ?",
            [session_id, step_type],
        )
        step_ids = [row["step_id"] for row in rows]
        for sid in step_ids:
            self.metadata.execute("DELETE FROM artifacts WHERE step_id = ?", [sid])
        self.metadata.execute(
            "DELETE FROM steps WHERE session_id = ? AND step_type = ?",
            [session_id, step_type],
        )

    def _assert_session_exists(self, session_id: str) -> None:
        self.session_manager.assert_session_exists(session_id)

    _TEMPORAL_DIMENSIONS: frozenset[str] = frozenset(
        {
            "log_date",
            "event_date",
            "dt",
            "date",
            "day",
            "log_hour",
            "event_hour",
            "hour",
            "minute",
            "event_time",
            "timestamp",
            "ts",
        }
    )

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
        return dims[: SemanticLayerService._MAX_DEFAULT_DIMENSIONS]

    @staticmethod
    def _comparison_time_dimension_column(
        request: ResolvedWindowedQueryRequest,
        all_dimensions: list[str],
    ) -> str:
        analysis_expr = str(request.resolved_time_axis.analysis_time_expr or "").strip()
        if analysis_expr in all_dimensions:
            return analysis_expr
        override = request.resolved_time_axis.override_analysis_time_column
        if override:
            return override
        return SemanticLayerService._infer_date_column(all_dimensions)

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
            f"Unknown comparison_type '{comparison_type}'. Supported values: dod, wow, mom, yoy."
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
            row = engine.query_rows(f"SELECT MAX({date_column}) AS max_date FROM {table_name}")[0]
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

    def _make_provenance(
        self, sql: str = "", params: list[Any] | None = None, engine_type: str = "duckdb"
    ) -> dict[str, Any]:
        """Build a provenance token for a step execution."""
        query_hash = hashlib.sha256(sql.encode()).hexdigest()[:16] if sql else ""
        provenance = {
            "query_hash": query_hash,
            "engine": engine_type,
            "timestamp": datetime.now(UTC).isoformat(),
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
        semantic_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.metadata.execute(
            """
            INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json, provenance_json)
            VALUES (?, ?, ?, 'succeeded', ?, ?, ?)
            """,
            [
                step_id,
                session_id,
                step_type,
                summary,
                self._dump(result),
                self._dump(provenance or {}),
            ],
        )
        if semantic_metadata is not None:
            self._step_metadata_repo.upsert(
                step_id=step_id,
                metadata_kind="typed_semantic_snapshot",
                semantic_snapshot=semantic_metadata,
            )

    @staticmethod
    def _merge_unique_str(values: list[str | None]) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for value in values:
            if value is None or value in seen:
                continue
            seen.add(value)
            merged.append(value)
        return merged

    @staticmethod
    def _build_calendar_policy_binding(
        resolved_calendar_alignments: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not resolved_calendar_alignments:
            return None

        def require_string(alignment: dict[str, Any], field: str) -> str:
            value = alignment.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"resolved_calendar_alignment missing {field}")
            return value

        def require_source_lineage(alignment: dict[str, Any]) -> dict[str, str]:
            source_lineage = alignment.get("source_lineage")
            if not isinstance(source_lineage, dict) or not source_lineage:
                raise ValueError("resolved_calendar_alignment missing source_lineage metadata")
            normalized: dict[str, str] = {}
            for field in ("table_fqn", "calendar_version"):
                value = source_lineage.get(field)
                if not isinstance(value, str) or not value:
                    raise ValueError(f"resolved_calendar_alignment source_lineage missing {field}")
                normalized[field] = value
            return normalized

        bindings: list[dict[str, Any]] = []
        for alignment in resolved_calendar_alignments:
            bindings.append(
                {
                    "policy_ref": require_string(alignment, "policy_ref"),
                    "comparison_basis": require_string(alignment, "comparison_basis"),
                    "resolved_calendar_source": require_string(
                        alignment, "resolved_calendar_source"
                    ),
                    "resolved_calendar_version": require_string(
                        alignment, "resolved_calendar_version"
                    ),
                    "source_lineage": require_source_lineage(alignment),
                }
            )

        first_binding = bindings[0]
        for binding in bindings[1:]:
            if binding != first_binding:
                raise ValueError("conflicting calendar policy bindings in compiled step metadata")
        return first_binding

    def build_step_semantic_metadata(
        self,
        compiled_queries: CompiledQuery | list[CompiledQuery],
    ) -> dict[str, Any] | None:
        compiled_list = (
            compiled_queries if isinstance(compiled_queries, list) else [compiled_queries]
        )
        if not compiled_list:
            return None

        metric_refs = self._merge_unique_str(
            [
                str(compiled.metadata.get("resolved_metric_ref"))
                if compiled.metadata.get("resolved_metric_ref")
                else None
                for compiled in compiled_list
            ]
        )
        metric_revisions = [
            int(compiled.metadata["resolved_metric_revision"])
            for compiled in compiled_list
            if compiled.metadata.get("resolved_metric_revision") is not None
        ]
        metric_object_ids = self._merge_unique_str(
            [
                str(compiled.metadata.get("resolved_metric_object_id"))
                if compiled.metadata.get("resolved_metric_object_id")
                else None
                for compiled in compiled_list
            ]
        )
        process_refs = self._merge_unique_str(
            [
                str(compiled.metadata.get("resolved_process_ref"))
                if compiled.metadata.get("resolved_process_ref")
                else None
                for compiled in compiled_list
            ]
        )
        filter_time_refs = self._merge_unique_str(
            [
                str(compiled.metadata.get("resolved_filter_time_ref"))
                if compiled.metadata.get("resolved_filter_time_ref")
                else None
                for compiled in compiled_list
            ]
        )
        binding_refs = self._merge_unique_str(
            [
                binding_ref
                for compiled in compiled_list
                for binding_ref in list(compiled.metadata.get("resolved_binding_refs") or [])
            ]
        )
        dimension_refs = self._merge_unique_str(
            [
                dimension_ref
                for compiled in compiled_list
                for dimension_ref in list(compiled.metadata.get("resolved_dimension_refs") or [])
            ]
        )
        ir_plan_ids = self._merge_unique_str(
            [
                str(compiled.metadata.get("ir_plan_id"))
                if compiled.metadata.get("ir_plan_id")
                else None
                for compiled in compiled_list
            ]
        )
        request_classes = self._merge_unique_str(
            [
                str(compiled.metadata.get("normalized_request_class"))
                if compiled.metadata.get("normalized_request_class")
                else None
                for compiled in compiled_list
            ]
        )
        compiler_summaries = [
            dict(summary)
            for compiled in compiled_list
            for summary in [compiled.metadata.get("compiler_summary")]
            if isinstance(summary, dict)
        ]
        resolved_calendar_alignments = [
            dict(summary)
            for compiled in compiled_list
            for summary in [compiled.metadata.get("resolved_calendar_alignment")]
            if isinstance(summary, dict)
        ]
        imported_dimension_lineage = [
            dict(summary)
            for compiled in compiled_list
            for summary in [compiled.metadata.get("resolved_imported_dimensions")]
            if isinstance(summary, list)
            for summary in summary
            if isinstance(summary, dict)
        ]
        imported_dimension_conflicts = [
            {
                "dimension_ref": dimension_ref,
                "candidates": [
                    dict(candidate) for candidate in candidates if isinstance(candidate, dict)
                ],
            }
            for compiled in compiled_list
            for conflict_map in [compiled.metadata.get("imported_dimension_conflicts")]
            if isinstance(conflict_map, dict)
            for dimension_ref, candidates in conflict_map.items()
            if isinstance(candidates, list)
        ]
        imported_dimension_sources = [
            dict(source)
            for compiled in compiled_list
            for source_list in [compiled.metadata.get("resolved_imported_dimension_sources")]
            if isinstance(source_list, list)
            for source in source_list
            if isinstance(source, dict)
        ]
        metric_execution_contexts = [
            dict(context)
            for compiled in compiled_list
            for context in [compiled.metadata.get("metric_execution_context")]
            if isinstance(context, dict)
        ]
        metric_entity_anchor_refs = self._merge_unique_str(
            [
                str(compiled.metadata.get("metric_entity_anchor_ref"))
                if compiled.metadata.get("metric_entity_anchor_ref")
                else None
                for compiled in compiled_list
            ]
        )
        resolved_refs: dict[str, dict[str, Any]] = {}
        for compiled in compiled_list:
            metric_ref = compiled.metadata.get("resolved_metric_ref")
            if not metric_ref:
                continue
            metric_ref_text = str(metric_ref)
            resolved = resolved_refs.setdefault(
                metric_ref_text,
                {
                    "ref": metric_ref_text,
                },
            )
            metric_revision = compiled.metadata.get("resolved_metric_revision")
            if metric_revision is not None:
                resolved["revision"] = int(metric_revision)
            metric_object_id = compiled.metadata.get("resolved_metric_object_id")
            if metric_object_id:
                resolved["object_id"] = str(metric_object_id)
        calendar_policy_binding = self._build_calendar_policy_binding(resolved_calendar_alignments)

        if not any(
            (
                metric_refs,
                metric_revisions,
                metric_object_ids,
                process_refs,
                filter_time_refs,
                binding_refs,
                dimension_refs,
                ir_plan_ids,
                request_classes,
                compiler_summaries,
                resolved_calendar_alignments,
                imported_dimension_lineage,
                imported_dimension_conflicts,
                imported_dimension_sources,
                metric_execution_contexts,
                metric_entity_anchor_refs,
                resolved_refs,
                calendar_policy_binding,
            )
        ):
            return None

        snapshot: dict[str, Any] = {
            "schema_version": "step_semantic_metadata.v1",
            "metadata_kind": "typed_semantic_snapshot",
            "typed_inputs": {
                "metric_ref": metric_refs[0] if metric_refs else None,
                "resolved_metric_revision": metric_revisions[0] if metric_revisions else None,
                "resolved_metric_object_id": metric_object_ids[0] if metric_object_ids else None,
                "process_ref": process_refs[0] if process_refs else None,
                "dimension_refs": dimension_refs,
                "filter_time_ref": filter_time_refs[0] if filter_time_refs else None,
                "metric_entity_anchor_ref": (
                    metric_entity_anchor_refs[0] if metric_entity_anchor_refs else None
                ),
                "request_classes": request_classes,
            },
            "binding_refs": binding_refs,
            "compile_context": {
                "ir_plan_ids": ir_plan_ids,
                "compiler_summaries": compiler_summaries,
                "resolved_calendar_alignments": resolved_calendar_alignments,
                "imported_dimension_lineage": imported_dimension_lineage,
                "imported_dimension_conflicts": imported_dimension_conflicts,
                "imported_dimension_sources": imported_dimension_sources,
                "metric_execution_contexts": metric_execution_contexts,
                "calendar_policy_binding": calendar_policy_binding,
            },
            "resolved_refs": resolved_refs,
        }
        assert_no_canonical_refs_in_semantic_payload(snapshot, surface="step_semantic_metadata")
        return snapshot

    def _insert_artifact(
        self,
        session_id: str,
        step_id: str,
        artifact_type: str,
        name: str,
        content: Any,
        *,
        lifecycle: str = "committed",
        artifact_schema_version: str | None = None,
    ) -> str:
        artifact_id = f"art_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO artifacts
                (artifact_id, session_id, step_id, artifact_type, name,
                 content_json, lifecycle, artifact_schema_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                artifact_id,
                session_id,
                step_id,
                artifact_type,
                name,
                self._dump(content),
                lifecycle,
                artifact_schema_version,
            ],
        )
        return artifact_id

    def _commit_artifact(self, artifact_id: str) -> None:
        """Transition a staged artifact to committed state."""
        self.metadata.execute(
            "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
            [artifact_id],
        )

    def _commit_artifact_with_extraction(
        self,
        session_id: str,
        step_id: str,
        artifact_type: str,
        name: str,
        content: Any,
        *,
        artifact_schema_version: str | None = None,
        step_ref: StepRef | None = None,
        step_type: str | None = None,
        _registry: FindingExtractorRegistry | None = None,
    ) -> str:
        """Canonical commit boundary for mandatory-extraction artifacts (Phase 4c-1).

        Algorithm:
        1. Look up extractor via registry.find(artifact_type, artifact_schema_version).
        2. If no extractor (non-mandatory family): insert artifact as committed directly.
        3. If extractor found:
           a. Build effective_step_ref from the supplied step_ref, or construct one from
              (session_id, step_id, step_type or artifact_type).
              Mandatory-extraction runners (4c-2) always pass step_type so that
              StepRef.step_type reflects the actual step type rather than artifact_type.
           b. Run extractor.extract(artifact_id, content, effective_step_ref, session_id).
              Raises on extraction crash — no DB write happens.
           c. Call validate_for_commit(family, result).
              Raises ValueError (count mismatch) or FamilyEmptyError (empty not allowed)
              — no DB write happens.
           d. In a single DB transaction: INSERT artifact (staged) + idempotent finding insert
              each finding + UPDATE artifact lifecycle to 'committed'.  Either all three
              succeed together or none do.
        4. Return artifact_id.

        Atomicity guarantee: extraction and validation run outside the transaction.
        Only after both succeed are any rows written.  An extraction crash or validation
        failure leaves no artifact row and no finding rows in the DB.
        """
        registry = _registry if _registry is not None else default_finding_registry
        extractor = registry.find(artifact_type, artifact_schema_version)

        if extractor is None:
            # Non-mandatory family: insert as committed directly (backward compatible).
            return self._insert_artifact(
                session_id,
                step_id,
                artifact_type,
                name,
                content,
                lifecycle="committed",
                artifact_schema_version=artifact_schema_version,
            )

        # Mandatory extraction family — run extraction and validation BEFORE any DB write.
        artifact_id = f"art_{uuid4().hex[:12]}"
        effective_step_ref: StepRef = step_ref or StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type=step_type or artifact_type,
        )
        result = extractor.extract(artifact_id, content, effective_step_ref, session_id)
        # Raises ValueError (count mismatch) or FamilyEmptyError (empty not allowed).
        # Either exception aborts before any DB write.
        validate_for_commit(extractor.family, result)

        # All writes in a single transaction: artifact row + findings + lifecycle flip.
        with self.metadata.connect() as con:
            self.metadata.execute_sql(
                con,
                """
                INSERT INTO artifacts
                    (artifact_id, session_id, step_id, artifact_type, name,
                     content_json, lifecycle, artifact_schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'staged', ?)
                """,
                [
                    artifact_id,
                    session_id,
                    step_id,
                    artifact_type,
                    name,
                    self._dump(content),
                    artifact_schema_version,
                ],
            )
            for f in result["findings"]:
                self.metadata.execute_sql(
                    con,
                    self.metadata.insert_ignore_sql(
                        "findings",
                        [
                            "finding_id",
                            "session_id",
                            "artifact_id",
                            "step_ref_json",
                            "finding_type",
                            "canonical_item_key",
                            "subject_json",
                            "observed_window_json",
                            "quality_json",
                            "provenance_json",
                            "payload_json",
                            "schema_version",
                        ],
                    ),
                    [
                        f["finding_id"],
                        session_id,
                        artifact_id,
                        json.dumps(f["step_ref"]),
                        f["finding_type"],
                        f["provenance"]["canonical_item_key"],
                        json.dumps(f["subject"]),
                        json.dumps(f["observed_window"])
                        if f.get("observed_window") is not None
                        else None,
                        json.dumps(f["quality"]),
                        json.dumps(f["provenance"]),
                        json.dumps(f["payload"]),
                        "v1",
                    ],
                )
            self.metadata.execute_sql(
                con,
                "UPDATE artifacts SET lifecycle = 'committed' WHERE artifact_id = ?",
                [artifact_id],
            )
            con.commit()

        # Phase 4g-3: trigger the canonical downstream pipeline for the
        # committed findings (seeding → recompute → proposal refresh → publish).
        if result["findings"]:
            committed_finding_ids = [f["finding_id"] for f in result["findings"]]
            downstream_result = run_canonical_downstream(
                session_id=session_id,
                trigger_finding_ids=committed_finding_ids,
                finding_repo=self._finding_repo,
                proposition_repo=self._proposition_repo,
                assessment_repo=self._assessment_repo,
                gap_repo=self._gap_repo,
                inference_record_repo=self._inference_record_repo,
                proposal_repo=self._proposal_repo,
                metadata_store=self.metadata,
            )
            for slot in downstream_result["proposition_results"]:
                if slot["error"]:
                    logger.warning(
                        "canonical downstream error for proposition %s (artifact %s): %s",
                        slot["proposition_id"],
                        artifact_id,
                        slot["error"],
                    )

        return artifact_id

    def _resolve_artifact_for_ref(self, session_id: str, step_id: str) -> dict[str, Any] | None:
        """Return the content of the most recent committed artifact for a step ref.

        Used by 3b runners to look up upstream observe/compare artifact data.
        Returns None if no committed artifact exists for the given step.
        """
        row = self.metadata.query_one(
            """
            SELECT content_json FROM artifacts
            WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [step_id, session_id],
        )
        return json.loads(row["content_json"]) if row else None

    def _resolve_artifact_id_for_step(self, session_id: str, step_id: str) -> str | None:
        """Return the artifact_id of the most recent committed artifact for a step."""
        row = self.metadata.query_one(
            "SELECT artifact_id FROM artifacts "
            "WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at DESC LIMIT 1",
            [step_id, session_id],
        )
        return str(row["artifact_id"]) if row else None

    def _resolve_artifact_with_id(
        self, session_id: str, step_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return (artifact_id, content) for the most recent committed artifact for a step.

        Single query replacing separate _resolve_artifact_for_ref + _resolve_artifact_id_for_step
        calls for callers that need both.
        """
        row = self.metadata.query_one(
            "SELECT artifact_id, content_json FROM artifacts "
            "WHERE step_id = ? AND session_id = ? AND lifecycle = 'committed' "
            "ORDER BY created_at DESC LIMIT 1",
            [step_id, session_id],
        )
        if row is None:
            return None
        return str(row["artifact_id"]), json.loads(row["content_json"])

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def _new_step_id(self) -> str:
        return f"step_{uuid4().hex[:12]}"


class _ServiceWorkflowStepExecutor:
    def __init__(self, service: SemanticLayerService) -> None:
        self._service = service

    def execute_step(self, session_id: str, step_ir: AnalysisStepIR) -> dict[str, Any]:
        return self._service.run_step(
            session_id,
            step_ir.step_type,
            params=step_ir.params if step_ir.params else None,
        )
