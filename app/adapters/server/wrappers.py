"""Port adapter wrappers bridging existing infrastructure to Port Protocol interfaces.

Phase 3a provides thin wrappers that:
1. Accept existing infrastructure objects in their constructor.
2. Implement the Port Protocol by delegating to those objects.
3. Translate infrastructure exceptions to DomainError where practical.
4. Convert types between ``app.api.models.*`` / storage rows and
   ``app.contracts.*`` where needed.

For infrastructure-backed adapters where the mapping is unclear or the
existing infrastructure uses a fundamentally different paradigm (e.g.
CRUD vs. event-sourced), ``NotImplementedError`` with a clear message
is acceptable. These will be filled in during integration testing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import MarivoConfig
from app.contracts.errors import DomainError, ErrorCode, NotFoundError
from app.contracts.evidence import Assessment, Evidence, Finding, Proposition
from app.contracts.ids import (
    Action,
    CacheKey,
    EvidenceRef,
    ModelId,
    ResourceId,
    RevisionId,
    SessionId,
    UserId,
)
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent, SessionState
from app.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    ColumnInfo,
    LogicalQuery,
    QueryResult,
    SourceRef,
    SourceSchema,
    TelemetryEvent,
)
from app.routing import QueryRouter
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub adapters (no infrastructure dependency)
# ---------------------------------------------------------------------------


class NoopAuthZAdapter:
    """Always allows, returns ``AuthZDecision(allowed=True)``."""

    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)


class FileAuditLogAdapter:
    """Logs to the Python ``logging`` module."""

    def __init__(self, logger_name: str = "marivo.audit") -> None:
        self._logger = logging.getLogger(logger_name)

    def record(self, entry: AuditEntry) -> None:
        self._logger.info(
            "audit actor=%s action=%s resource_type=%s resource_id=%s detail=%s",
            entry.actor,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            entry.detail,
        )


class LocalTelemetryAdapter:
    """No-op telemetry adapter; does nothing."""

    def emit(self, event: TelemetryEvent) -> None:
        pass


class TomlRuntimeConfigAdapter:
    """Wraps ``MarivoConfig``, delegates ``get(key)`` to ``getattr(config, key, None)``.

    The returned value is always converted to ``str`` (or ``None`` if absent)
    to satisfy the ``RuntimeConfig`` protocol.
    """

    def __init__(self, config: MarivoConfig) -> None:
        self._config = config

    def get(self, key: str) -> str | None:
        value = getattr(self._config, key, None)
        if value is None:
            return None
        return str(value)


# ---------------------------------------------------------------------------
# Infrastructure-backed adapters
# ---------------------------------------------------------------------------


class SqlModelStoreAdapter:
    """Wraps ``SemanticModelV2Service`` + ``MetadataStore`` -> ``ModelStore``.

    Delegates get/save/list to the existing semantic model service,
    converting between storage-level dicts and domain ``SemanticModel`` /
    ``ModelSummary`` instances.

    Phase 3a limitation: ``save`` is a placeholder because the existing
    ``SemanticModelV2Service.create_semantic_model`` expects an OSI-conformant
    dict rather than a ``SemanticModel`` domain object. Full mapping will be
    completed during integration testing.
    """

    def __init__(
        self,
        service: Any,  # SemanticModelV2Service (late-bound to avoid circular import)
        metadata: MetadataStore,
    ) -> None:
        self._service = service
        self._metadata = metadata

    def get(self, selector: Any) -> SemanticModel | None:
        """Look up a model by name from the selector.

        The ``ModelSelector`` protocol exposes ``model_id``, ``name``, and
        ``revision`` attributes. We delegate by name since the existing
        service uses name-based lookup.
        """
        name = getattr(selector, "name", None)
        if name is None:
            return None
        try:
            model_dict = self._service.get_semantic_model(name)
        except Exception as exc:
            if "not found" in str(exc).lower():
                return None
            raise DomainError(ErrorCode.MODEL_NOT_FOUND, str(exc)) from exc
        return self._dict_to_semantic_model(model_dict)

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        """Persist a semantic model.

        Phase 3a: raises ``NotImplementedError`` because the existing
        service expects a full OSI dict rather than a domain object.
        """
        raise NotImplementedError(
            "SqlModelStoreAdapter.save: mapping from SemanticModel domain object "
            "to OSI-conformant dict not yet implemented. Use the existing "
            "SemanticModelV2Service directly until integration testing completes."
        )

    def list(self, query: Any) -> list[ModelSummary]:
        """List models according to the query criteria."""
        owner = getattr(query, "owner", None)
        try:
            models = self._service.list_semantic_models(
                requesting_user=owner,
            )
        except Exception as exc:
            raise DomainError(ErrorCode.MODEL_NOT_FOUND, str(exc)) from exc
        return [self._dict_to_model_summary(m) for m in models]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dict_to_semantic_model(model_dict: dict[str, Any]) -> SemanticModel:
        """Convert a storage-level model dict to a domain SemanticModel."""
        marivo_exts = model_dict.get("custom_extensions") or []
        visibility = "private"
        owner: str | None = None
        revision: str | None = None
        for ext in marivo_exts:
            if ext.get("vendor_name") == "MARIVO":
                import json

                data = ext.get("data")
                parsed = json.loads(data) if isinstance(data, str) else data
                if parsed:
                    visibility = parsed.get("visibility", "private")
                    owner = parsed.get("owner_user")
                    revision = str(parsed.get("revision", "")) if parsed.get("revision") else None

        return SemanticModel(
            model_id=None,
            name=model_dict.get("name", ""),
            revision=RevisionId(revision) if revision else None,
            description=model_dict.get("description"),
            osi_document=model_dict,
            visibility=visibility,
            owner=UserId(owner) if owner else None,
        )

    @staticmethod
    def _dict_to_model_summary(model_dict: dict[str, Any]) -> ModelSummary:
        """Convert a storage-level model dict to a domain ModelSummary."""
        marivo_exts = model_dict.get("custom_extensions") or []
        visibility = "private"
        owner: str | None = None
        revision: str | None = None
        for ext in marivo_exts:
            if ext.get("vendor_name") == "MARIVO":
                import json

                data = ext.get("data")
                parsed = json.loads(data) if isinstance(data, str) else data
                if parsed:
                    visibility = parsed.get("visibility", "private")
                    owner = parsed.get("owner_user")
                    revision = str(parsed.get("revision", "")) if parsed.get("revision") else None

        # model_id from storage dict - extract from the dict if available
        model_id = ModelId(model_dict.get("model_id", 0))

        return ModelSummary(
            model_id=model_id,
            name=model_dict.get("name", ""),
            revision=RevisionId(revision) if revision else None,
            description=model_dict.get("description"),
            visibility=visibility,
            owner=UserId(owner) if owner else None,
            updated_at=model_dict.get("updated_at"),
        )


def _decode_page_token(page_token: str | None) -> int:
    if page_token is None:
        return 0
    try:
        offset = int(page_token)
    except ValueError as error:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.") from error
    if offset < 0:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.")
    return offset


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 25
    if limit <= 0:
        raise ValueError("Invalid limit. Expected a positive integer.")
    return min(limit, 100)


def _session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    session_id: str = row["session_id"]
    return {
        "session_id": session_id,
        "goal": {"question": row["goal"]},
        "scope": {
            "constraints": json.loads(row["constraints_json"])
            if row.get("constraints_json")
            else None,
        },
        "owner_user": row.get("owner_user"),
        "lifecycle": {
            "status": row["status"],
            "terminal_reason": row.get("terminal_reason"),
            "ended_at": row.get("ended_at"),
            "rollover_from_session_id": row.get("rollover_from_session_id"),
        },
        "state_summary": {
            "state_view_ref": {
                "session_id": session_id,
                "view_type": "session_state_view",
            },
        },
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at") or row["created_at"],
        "schema_version": "analysis_session.v1",
    }


class SqlSessionStoreAdapter:
    """Wraps ``MetadataStore`` -> ``SessionStore``.

    Implements the ``SessionStore`` port interface using direct
    ``MetadataStore`` SQL queries (absorbs former SessionManager logic):

    * ``append_event`` translates session_created / session_terminated
      events into CRUD INSERT/UPDATE calls.
    * ``load_events`` synthesizes events from the sessions table row.
    * ``list_sessions`` queries sessions filtered by owner.
    * ``list_sessions_paginated`` returns filtered, paginated sessions.
    * ``get_proposition_runtime_status`` derives proposition stage from
      committed canonical DB state.
    """

    def __init__(
        self,
        metadata: MetadataStore,
    ) -> None:
        self._metadata = metadata

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        """Append an event to the session event log.

        Translates ``session_created`` and ``session_terminated`` events
        into CRUD operations on the sessions table.
        """
        if event.event_type == "session_created":
            goal = event.payload.get("goal", "")
            constraints = event.payload.get("constraints")
            budget = event.payload.get("budget")
            raw_filter = event.payload.get("raw_filter")
            owner_user = str(event.actor) if event.actor else ""
            self._metadata.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    goal,
                    constraints_json,
                    budget_json,
                    owner_user,
                    status,
                    raw_filter
                )
                VALUES (?, ?, ?, ?, ?, 'open', ?)
                """,
                [
                    str(session_id),
                    goal,
                    json.dumps(constraints or {}, default=str, sort_keys=True),
                    json.dumps(budget or {}, default=str, sort_keys=True),
                    owner_user,
                    raw_filter,
                ],
            )
            return

        if event.event_type == "session_terminated":
            terminal_reason = event.payload.get("terminal_reason", "user_closed")
            self._metadata.execute(
                f"""
                UPDATE sessions
                SET status = 'closed',
                    terminal_reason = ?,
                    ended_at = {self._metadata.dialect.now_sql()},
                    updated_at = {self._metadata.dialect.now_sql()}
                WHERE session_id = ?
                """,
                [terminal_reason, str(session_id)],
            )
            return

        # Other event types are silently ignored in the CRUD bridge
        # because the sessions table only tracks created/terminated state.
        logger.debug(
            "SqlSessionStoreAdapter.append_event: ignoring event_type=%s "
            "for session_id=%s (CRUD bridge only handles session_created/"
            "session_terminated)",
            event.event_type,
            session_id,
        )

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        """Load events from the session event log.

        Synthesizes events from the sessions table row.  Returns a
        ``session_created`` event for every session, and a
        ``session_terminated`` event if the session is closed.
        Raises NotFoundError when the session does not exist.
        """
        row = self._metadata.query_one(
            "SELECT * FROM sessions WHERE session_id = ?",
            [str(session_id)],
        )
        if row is None:
            raise NotFoundError(
                code=ErrorCode.NOT_FOUND,
                message=f"Session {session_id!r} not found",
            )

        events: list[SessionEvent] = []
        created_payload: dict[str, Any] = {"goal": row["goal"]}
        constraints_json = row.get("constraints_json")
        if constraints_json:
            created_payload["constraints"] = json.loads(constraints_json)
        budget_json = row.get("budget_json")
        if budget_json:
            created_payload["budget"] = json.loads(budget_json)

        owner_user = row.get("owner_user") or ""
        actor = UserId(owner_user) if owner_user else None

        events.append(
            SessionEvent(
                session_id=session_id,
                event_type="session_created",
                timestamp=row["created_at"],
                payload=created_payload,
                actor=actor,
            )
        )

        if row["status"] in ("closed", "terminated"):
            terminal_reason = row.get("terminal_reason", "user_closed")
            ended_at = row.get("ended_at") or row.get("updated_at") or row["created_at"]
            events.append(
                SessionEvent(
                    session_id=session_id,
                    event_type="session_terminated",
                    timestamp=ended_at,
                    payload={"terminal_reason": terminal_reason},
                    actor=None,
                )
            )

        return events

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        """List sessions owned by ``owner``.

        Queries the sessions table filtered by owner_user.
        """
        rows = self._metadata.query_rows(
            "SELECT * FROM sessions WHERE owner_user = ? ORDER BY created_at DESC",
            [str(owner)],
        )
        result: list[SessionState] = []
        for row in rows:
            sid = SessionId(row["session_id"])
            status = "active" if row["status"] == "open" else row["status"]
            if status == "closed":
                status = "terminated"
            constraints_json = row.get("constraints_json")
            budget_json = row.get("budget_json")
            constraints = json.loads(constraints_json) if constraints_json else None
            budget = json.loads(budget_json) if budget_json else None
            owner_user_val = row.get("owner_user") or None
            result.append(
                SessionState(
                    session_id=sid,
                    status=status,
                    goal=row["goal"],
                    owner_user=UserId(owner_user_val) if owner_user_val else None,
                    constraints=constraints,
                    budget=budget,
                    created_at=row["created_at"],
                    updated_at=row.get("updated_at") or row["created_at"],
                )
            )
        return result

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status.

        Derives stage from committed canonical DB state.  v1 does not maintain
        a real queue / claim / lease / retry system.
        """
        from app.storage.evidence_repositories import ActionProposalRepository, AssessmentRepository

        row = self._metadata.query_one(
            """
            SELECT proposition_id, session_id, externally_visible_assessment_id
            FROM propositions
            WHERE proposition_id = ? AND session_id = ?
            """,
            [proposition_id, session_id],
        )
        if row is None:
            raise KeyError(f"proposition {proposition_id!r} not found in session {session_id!r}")

        ev_assessment_id: str | None = row.get("externally_visible_assessment_id")

        assessment_repo = AssessmentRepository(self._metadata)
        latest = assessment_repo.get_latest(proposition_id)

        proposals: list[dict[str, Any]] = []
        if latest is not None:
            proposal_repo = ActionProposalRepository(self._metadata)
            proposals = proposal_repo.list_by_assessment(session_id, latest["assessment_id"])

        if ev_assessment_id:
            current_stage = "externally_visible"
            last_successful_stage: str | None = "publish"
        elif latest is not None and proposals:
            current_stage = "publish_ready"
            last_successful_stage = "proposal_refresh"
        elif latest is not None:
            current_stage = "assessment_committed"
            last_successful_stage = "assessment_committed"
        else:
            current_stage = "queued"
            last_successful_stage = None

        return {
            "session_id": session_id,
            "proposition_id": proposition_id,
            "current_stage": current_stage,
            "last_successful_stage": last_successful_stage,
            "current_assessment_id": latest["assessment_id"] if latest is not None else None,
            "current_attempt": None,
            "backlog_state": "none",
            "last_failure_reason": "none",
            "last_failure_at": None,
            "schema_version": "proposition_runtime_status.v1",
        }

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        """Return a paginated list of sessions (server-mode only).

        Supports filtering by status, session_id prefix, and owner_user.
        """
        from app.identity import resolve_user

        status = kwargs.get("status")
        session_id = kwargs.get("session_id")
        limit = kwargs.get("limit")
        page_token = kwargs.get("page_token")

        offset = _decode_page_token(page_token)
        normalized_limit = _normalize_limit(limit)

        sql = "SELECT * FROM sessions"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if session_id:
            clauses.append("session_id LIKE ?")
            params.append(f"{session_id}%")
        current_user = resolve_user()
        if current_user is not None:
            clauses.append("owner_user = ?")
            params.append(current_user)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, session_id DESC LIMIT ? OFFSET ?"
        params.extend([normalized_limit + 1, offset])
        rows = self._metadata.query_rows(sql, params)

        has_next_page = len(rows) > normalized_limit
        items = [_session_from_row(row) for row in rows[:normalized_limit]]
        next_page_token = str(offset + normalized_limit) if has_next_page else None
        return {"items": items, "next_page_token": next_page_token}


class DataSourceAdapter:
    """Wraps ``AnalyticsEngine`` + ``QueryRouter`` -> ``DataSource``.

    Delegates ``execute`` to the analytics engine and ``schema`` to the
    catalog adapter via the router's metadata store.
    """

    def __init__(
        self,
        engine: AnalyticsEngine,
        router: QueryRouter,
    ) -> None:
        self._engine = engine
        self._router = router

    def execute(self, query: LogicalQuery) -> QueryResult:
        """Execute a logical query against the analytics engine."""
        try:
            rows = self._engine.query_rows(
                query.sql, list(query.params.values()) if query.params else None
            )
        except Exception as exc:
            raise DomainError(ErrorCode.QUERY_EXECUTION_FAILED, str(exc)) from exc
        if not rows:
            return QueryResult(columns=[], rows=[], row_count=0, query_sql=query.sql)
        columns = list(rows[0].keys())
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query_sql=query.sql,
        )

    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any:
        """Delegate table resolution to the QueryRouter via RoutingRuntime."""
        from app.execution.routing_runtime import RoutingRuntime

        routing_runtime = RoutingRuntime(self._router, self._engine)
        return routing_runtime.resolve_tables(table_names, session_id=session_id)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        """Return the schema for the referenced source table.

        Delegates to ``DatasourceRegistry.browse_catalog_columns`` via
        the router's ``datasource_service``. Falls back to an empty
        schema if the datasource or table cannot be resolved.
        """
        try:
            datasource_id = source_ref.datasource_id
            datasource_service = self._router.datasource_service
            try:
                col_dicts = datasource_service.browse_catalog_columns(
                    datasource_id,
                    source_ref.schema_name,
                    source_ref.table_name,
                )
            except (KeyError, NotImplementedError, ValueError):
                # Datasource or table not found; return empty schema
                return SourceSchema(columns=[])
            columns = [
                ColumnInfo(
                    name=col.get("name", "unknown"),
                    dtype=col.get("data_type", "unknown"),
                    nullable=col.get("properties", {}).get("nullable", True),
                )
                for col in col_dicts
            ]
            return SourceSchema(columns=columns)
        except KeyError as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc
        except Exception as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc


class MetadataEvidenceStoreAdapter:
    """Wraps evidence repositories -> ``EvidenceStore``.

    Delegates write/read to the existing repository classes for findings,
    propositions, and assessments.

    Phase 3a: ``write`` stores evidence by persisting its findings and
    proposition/assessment via the respective repositories. ``read`` is
    a minimal bridge that reconstructs an ``Evidence`` from stored data.
    """

    def __init__(
        self,
        finding_repo: FindingRepository,
        proposition_repo: PropositionRepository,
        assessment_repo: AssessmentRepository,
        gap_repo: EvidenceGapRepository | None = None,
        inference_repo: InferenceRecordRepository | None = None,
        action_proposal_repo: ActionProposalRepository | None = None,
    ) -> None:
        self._finding_repo = finding_repo
        self._proposition_repo = proposition_repo
        self._assessment_repo = assessment_repo
        self._gap_repo = gap_repo
        self._inference_repo = inference_repo
        self._action_proposal_repo = action_proposal_repo

    def write(self, evidence: Evidence) -> EvidenceRef:
        """Persist evidence by writing its findings and proposition/assessment.

        Each finding is created via the FindingRepository (idempotent).
        If a proposition is present, it is created via the PropositionRepository.
        If an assessment is present, it is created via the AssessmentRepository.
        """
        ref = evidence.ref

        for finding in evidence.findings:
            try:
                self._finding_repo.create(self._finding_to_storage_dict(finding))
            except Exception:
                logger.debug("Finding %s may already exist (idempotent)", finding.finding_id)

        if evidence.proposition is not None:
            try:
                self._proposition_repo.create(
                    self._proposition_to_storage_dict(evidence.proposition)
                )
            except Exception:
                logger.debug(
                    "Proposition %s may already exist", evidence.proposition.proposition_id
                )

        if evidence.assessment is not None:
            try:
                self._assessment_repo.create(self._assessment_to_storage_dict(evidence.assessment))
            except Exception:
                logger.debug("Assessment %s may already exist", evidence.assessment.assessment_id)

        return ref

    def read(self, ref: EvidenceRef) -> Evidence:
        """Read evidence by ref.

        Phase 3a: raises ``NotImplementedError`` because evidence ref
        resolution requires a coherent lookup across multiple tables.
        """
        raise NotImplementedError(
            "MetadataEvidenceStoreAdapter.read: cross-table evidence "
            "reconstruction from EvidenceRef not yet implemented. Use the "
            "individual repository classes for reads until integration "
            "testing completes."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _finding_to_storage_dict(finding: Finding) -> dict[str, Any]:
        """Convert a domain Finding to a storage-compatible dict."""
        import json

        return {
            "finding_id": finding.finding_id,
            "session_id": finding.session_id,
            "artifact_id": finding.artifact_id,
            "step_ref_json": json.dumps({}),
            "finding_type": finding.finding_type,
            "canonical_item_key": finding.finding_id,
            "subject_json": json.dumps({}),
            "observed_window_json": None,
            "quality_json": json.dumps({}),
            "provenance_json": json.dumps({}),
            "payload_json": json.dumps(finding.content),
            "schema_version": "v1",
            "proposition_id": finding.proposition_id,
        }

    @staticmethod
    def _proposition_to_storage_dict(proposition: Proposition) -> dict[str, Any]:
        """Convert a domain Proposition to a storage-compatible dict."""
        import json

        return {
            "proposition_id": proposition.proposition_id,
            "session_id": proposition.session_id,
            "proposition_type": "generic",
            "subject_json": json.dumps({}),
            "origin_json": json.dumps({}),
            "assessment_anchor_json": json.dumps({}),
            "lineage_json": json.dumps({}),
            "seed_finding_refs_json": "[]",
            "payload_json": json.dumps({"description": proposition.description}),
            "schema_version": "v1",
            "identity_key": proposition.identity_key,
        }

    @staticmethod
    def _assessment_to_storage_dict(assessment: Assessment) -> dict[str, Any]:
        """Convert a domain Assessment to a storage-compatible dict."""
        import json

        return {
            "assessment_id": assessment.assessment_id,
            "session_id": "",
            "proposition_id": assessment.proposition_id,
            "assessment_type": "auto",
            "snapshot_seq": assessment.snapshot_seq,
            "status": assessment.status,
            "confidence_grade": None,
            "confidence_rationale_json": json.dumps(
                {"rationale": assessment.rationale} if assessment.rationale else {}
            ),
            "supporting_finding_ids_json": "[]",
            "opposing_finding_ids_json": "[]",
            "gap_memberships_json": "[]",
            "applied_inference_record_ids_json": "[]",
            "supersedes_assessment_id": None,
            "payload_json": "{}",
            "schema_version": "v1",
        }


class MetadataCacheStoreAdapter:
    """Wraps ``MetadataStore`` -> ``CacheStore``.

    ``MetadataStore`` does not have cache semantics, so this adapter uses
    an in-memory dict for Phase 3a. A proper cache backend will be added
    in a later phase.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata
        self._cache: dict[str, bytes] = {}

    def get(self, key: CacheKey) -> CacheValue | None:
        """Retrieve a cached value by key."""
        raw = self._cache.get(key)
        if raw is None:
            return None
        return CacheValue(raw)

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        """Store a value in the cache. TTL is ignored in Phase 3a."""
        self._cache[key] = bytes(value)


__all__ = [
    "DataSourceAdapter",
    "FileAuditLogAdapter",
    "LocalTelemetryAdapter",
    "MetadataCacheStoreAdapter",
    "MetadataEvidenceStoreAdapter",
    "NoopAuthZAdapter",
    "SqlModelStoreAdapter",
    "SqlSessionStoreAdapter",
    "TomlRuntimeConfigAdapter",
]
