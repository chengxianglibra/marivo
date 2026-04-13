from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.evidence_engine.family_contract import ALLOWS_EMPTY_ARTIFACT_TYPES
from app.evidence_engine.finding_extractor_registry import default_finding_registry
from app.storage.evidence_repositories import ActionProposalRepository, AssessmentRepository
from app.storage.metadata import MetadataStore

# Columns selected for canonical session root reads (Phase 5a).
_SESSION_SELECT = (
    "session_id, goal, status, constraints_json, budget_json, policy_json, "
    "raw_filter, created_at, "
    "terminal_reason, ended_at, rollover_from_session_id, updated_at"
)


class SessionManager:
    """Own session CRUD so the orchestration service can slim down incrementally."""

    def __init__(self, metadata_store: MetadataStore) -> None:
        self.metadata = metadata_store

    def create_session(
        self,
        goal: str,
        constraints: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | list[dict[str, Any]] | None = None,
        raw_filter: str | None = None,
    ) -> dict[str, Any]:
        session_id = f"sess_{uuid4().hex[:12]}"
        legacy_constraints = constraints or {}
        budget_payload = budget or {}
        policy_payload: dict[str, Any] | list[dict[str, Any]] = policy or {}
        self.metadata.execute(
            """
            INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status, raw_filter)
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
            [
                session_id,
                goal,
                self._dump(legacy_constraints),
                self._dump(budget_payload),
                self._dump(policy_payload),
                raw_filter,
            ],
        )
        row = self.metadata.query_one(
            f"SELECT {_SESSION_SELECT} FROM sessions WHERE session_id = ?",
            [session_id],
        )
        assert row is not None
        return self._session_from_row(row)

    def list_sessions(
        self,
        status: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        offset = self._decode_page_token(page_token)
        normalized_limit = self._normalize_limit(limit)

        sql = f"SELECT {_SESSION_SELECT} FROM sessions"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if session_id:
            clauses.append("session_id LIKE ?")
            params.append(f"{session_id}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, session_id DESC LIMIT ? OFFSET ?"
        params.extend([normalized_limit + 1, offset])
        rows = self.metadata.query_rows(sql, params)

        has_next_page = len(rows) > normalized_limit
        items = [self._session_from_row(row) for row in rows[:normalized_limit]]
        next_page_token = (
            self._encode_page_token(offset + normalized_limit) if has_next_page else None
        )
        return {"items": items, "next_page_token": next_page_token}

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.metadata.query_one(
            f"SELECT {_SESSION_SELECT} FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        return self._session_from_row(row)

    def assert_session_exists(self, session_id: str) -> None:
        row = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None or row["cnt"] == 0:
            raise KeyError(f"Unknown session: {session_id}")

    def assert_session_is_open(self, session_id: str) -> None:
        """Raise KeyError if session is unknown; ValueError if session is not open."""
        row = self.metadata.query_one(
            "SELECT status FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        if row["status"] != "open":
            raise ValueError(
                f"Session {session_id!r} is not open (status={row['status']!r}). "
                "Write operations require an open session."
            )

    def terminate_session(
        self, session_id: str, terminal_reason: str = "user_closed"
    ) -> dict[str, Any]:
        """Terminate a session, preventing further write operations.

        Raises
        ------
        KeyError
            When *session_id* does not exist.
        ValueError
            When the session is already in a terminal state.
        """
        row = self.metadata.query_one(
            "SELECT status FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if row is None:
            raise KeyError(f"Unknown session: {session_id}")
        if row["status"] != "open":
            raise ValueError(
                f"Session {session_id!r} is already in a terminal state (status={row['status']!r})."
            )
        self.metadata.execute(
            """
            UPDATE sessions
            SET status = 'closed',
                terminal_reason = ?,
                ended_at = datetime('now'),
                updated_at = datetime('now')
            WHERE session_id = ?
            """,
            [terminal_reason, session_id],
        )
        return self.get_session(session_id)

    def get_session_runtime_status(self, session_id: str) -> dict[str, Any]:
        """Return session-level operator runtime status (Phase 5a).

        Derives status from canonical DB state.  v1 does not maintain a real
        queue / lease / retry system, so:
        - 'blocked' and 'degraded' overall_status values are never emitted;
          only 'idle' and 'running' are used.
        - blocked_reason is always 'none'.
        - backpressured_propositions and failed_items are always 0.
        - updated_at reflects the session row's updated_at (set at creation;
          future UPDATEs on session fields must refresh this column).

        D4-allows-empty artifact types ('observation', 'anomaly_candidates') are
        excluded from queued_artifact counts because zero findings is a legal
        committed outcome for those families.  In v1 (synchronous pipeline),
        this means a completed-empty extraction is indistinguishable from an
        unprocessed artifact without an extraction-status flag.
        """
        session_row = self.metadata.query_one(
            f"SELECT {_SESSION_SELECT} FROM sessions WHERE session_id = ?",
            [session_id],
        )
        if session_row is None:
            raise KeyError(f"Unknown session: {session_id}")

        def _count(table: str, extra_where: str = "") -> int:
            where = f"session_id = ? {extra_where}".strip()
            row = self.metadata.query_one(
                f"SELECT COUNT(*) AS cnt FROM {table} WHERE {where}",
                [session_id],
            )
            return int(row["cnt"]) if row else 0

        artifact_count = _count("artifacts")
        finding_count = _count("findings")
        proposition_count = _count("propositions")
        assessment_count = _count("assessments")
        proposal_count = _count("action_proposals")
        published_count = _count("propositions", "AND externally_visible_assessment_id IS NOT NULL")

        # Artifacts pending extraction: exclude D4-allows-empty families
        # ('observation', 'anomaly_candidates') because zero findings is a valid
        # committed outcome for those types.  Non-empty-required types with no
        # findings are genuinely pending extraction.
        queued_artifact_row = self.metadata.query_one(
            """
            SELECT COUNT(*) AS cnt
            FROM artifacts
            WHERE session_id = ?
              AND artifact_type NOT IN ('observation', 'anomaly_candidates')
              AND artifact_id NOT IN (
                  SELECT DISTINCT artifact_id FROM findings WHERE session_id = ?
              )
            """,
            [session_id, session_id],
        )
        queued_artifacts = int(queued_artifact_row["cnt"]) if queued_artifact_row else 0

        unpublished_propositions = max(0, proposition_count - published_count)

        # last_successful_stage: highest pipeline stage with committed output.
        if published_count > 0:
            last_stage: str | None = "publish"
        elif proposal_count > 0:
            last_stage = "proposal_refresh"
        elif assessment_count > 0:
            last_stage = "assessment_recompute"
        elif proposition_count > 0:
            last_stage = "proposition_seeding"
        elif finding_count > 0:
            last_stage = "finding_extraction"
        elif artifact_count > 0:
            last_stage = "artifact_commit"
        else:
            last_stage = None

        # overall_status: idle = nothing committed; running = pipeline has
        # pending work (unprocessed non-empty-family artifacts, findings awaiting
        # seeding, or propositions not fully published).
        # v1 does not emit 'blocked' or 'degraded' (no real queue/lease system).
        if artifact_count == 0:
            overall_status = "idle"
        elif (
            queued_artifacts > 0
            or (finding_count > 0 and proposition_count == 0)
            or unpublished_propositions > 0
        ):
            overall_status = "running"
        else:
            overall_status = "idle"

        updated_at: str = session_row.get("updated_at") or session_row["created_at"]

        return {
            "session_id": session_id,
            "overall_status": overall_status,
            "last_successful_stage": last_stage,
            "blocked_reason": "none",
            "backlog_summary": {
                "queued_artifacts": queued_artifacts,
                "queued_propositions": unpublished_propositions,
                "backpressured_propositions": 0,
                "failed_items": 0,
            },
            "updated_at": updated_at,
            "schema_version": "session_runtime_status.v1",
        }

    def get_artifact_runtime_status(
        self,
        session_id: str,
        artifact_id: str,
        finding_registry: Any = None,
    ) -> dict[str, Any]:
        """Return artifact-level operator runtime status (Phase 5b).

        Derives status from canonical DB state.  v1 does not maintain a real
        queue / attempt / retry system, so:

        - ``artifact_stage`` is one of ``"staged"`` or ``"findings_committed"``
          only.  ``"extracting"``, ``"seeding_handoff_pending"``, and
          ``"failed"`` are reserved for future versions with state tracking.
        - ``correlation_id`` is set to ``artifact_id`` (stable v1 handle).
        - ``attempt_id``, ``last_failure_reason``, and ``last_failure_at``
          are always ``null`` in v1.

        D4-allows-empty artifact types (``observation``, ``anomaly_candidates``)
        always return ``"findings_committed"`` because zero findings is a valid
        committed outcome and is indistinguishable from a pending extraction
        without an extraction-status column.

        Raises
        ------
        KeyError
            When *artifact_id* is not found in *session_id*.
        """
        row = self.metadata.query_one(
            """
            SELECT artifact_id, session_id, artifact_type, artifact_schema_version
            FROM artifacts
            WHERE artifact_id = ? AND session_id = ?
            """,
            [artifact_id, session_id],
        )
        if row is None:
            raise KeyError(f"artifact {artifact_id!r} not found in session {session_id!r}")

        artifact_type: str = row["artifact_type"]
        artifact_schema_version: str | None = row.get("artifact_schema_version")

        # Count findings for this artifact in the session.
        finding_row = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM findings WHERE artifact_id = ? AND session_id = ?",
            [artifact_id, session_id],
        )
        finding_count: int = int(finding_row["cnt"]) if finding_row else 0

        # Derive artifact_stage.
        if artifact_type in ALLOWS_EMPTY_ARTIFACT_TYPES or finding_count > 0:
            artifact_stage = "findings_committed"
        else:
            artifact_stage = "staged"

        # Extractor lookup.
        registry = finding_registry if finding_registry is not None else default_finding_registry
        extractor = registry.find(artifact_type, artifact_schema_version)
        extractor_version: str | None = (
            extractor.extractor_version if extractor is not None else None
        )

        return {
            "session_id": session_id,
            "artifact_id": artifact_id,
            "artifact_stage": artifact_stage,
            "extractor_key": {
                "artifact_type": artifact_type,
                "artifact_schema_version": artifact_schema_version,
                "extractor_version": extractor_version,
            },
            "correlation_id": artifact_id,
            "attempt_id": None,
            "last_failure_reason": None,
            "last_failure_at": None,
            "schema_version": "artifact_runtime_status.v1",
        }

    def get_proposition_runtime_status(
        self,
        session_id: str,
        proposition_id: str,
        proposal_repo: ActionProposalRepository | None = None,
    ) -> dict[str, Any]:
        """Return proposition-level operator runtime status (Phase 5c).

        Derives stage from committed canonical DB state.  v1 does not maintain
        a real queue / claim / lease / retry system, so:

        - ``current_attempt`` is always ``null``.
        - ``backlog_state`` is always ``"none"``.
        - ``last_failure_reason`` is always ``"none"``.
        - ``last_failure_at`` is always ``null``.
        - ``current_stage`` is inferred from which DB write stages have
          completed: queued → assessment_committed → publish_ready →
          externally_visible.

        Raises
        ------
        KeyError
            When *proposition_id* is not found in *session_id*.
        """
        row = self.metadata.query_one(
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

        assessment_repo = AssessmentRepository(self.metadata)
        latest = assessment_repo.get_latest(proposition_id)

        # Probe proposals only when there is a committed assessment.
        proposals: list[dict[str, Any]] = []
        if latest is not None:
            _proposal_repo = (
                proposal_repo
                if proposal_repo is not None
                else ActionProposalRepository(self.metadata)
            )
            proposals = _proposal_repo.list_by_assessment(session_id, latest["assessment_id"])

        # Derive current_stage and last_successful_stage.
        #
        # externally_visible is checked first and is unconditional.
        # The publish pointer is monotonically advancing: once set it always
        # refers to a committed assessment.  A later re-triggered assessment
        # (latest != ev_assessment_id) does not change the externally visible
        # canonical state until execute_publish_switch fires again — so
        # reporting "externally_visible" is correct even when a newer
        # unpublished assessment exists.  Operators can compare
        # current_assessment_id against the session state surface to detect
        # pending re-publish work.
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Return canonical AnalysisSession dict from a DB row (Phase 5a)."""
        session_id: str = row["session_id"]

        # governance.budget — pass through whatever was stored
        budget = json.loads(row["budget_json"]) if row.get("budget_json") else None

        # governance.policy_refs — only surface a list-typed policy value;
        # older sessions store arbitrary dicts, which do not match the typed
        # [{"policy_id": ..., "policy_version": ...}] contract.
        raw_policy = json.loads(row["policy_json"]) if row.get("policy_json") else None
        policy_refs = raw_policy if isinstance(raw_policy, list) else None

        return {
            "session_id": session_id,
            "goal": {"question": row["goal"]},
            "scope": {
                "constraints": json.loads(row["constraints_json"])
                if row.get("constraints_json")
                else None,
            },
            "governance": {
                "policy_refs": policy_refs,
                "budget": budget,
                "warnings": None,
            },
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

    def _dump(self, value: Any) -> str:
        return json.dumps(value, default=str, sort_keys=True)

    def _decode_page_token(self, page_token: str | None) -> int:
        if page_token is None:
            return 0
        try:
            offset = int(page_token)
        except ValueError as error:
            raise ValueError(
                "Invalid page_token. Expected a non-negative integer offset."
            ) from error
        if offset < 0:
            raise ValueError("Invalid page_token. Expected a non-negative integer offset.")
        return offset

    def _encode_page_token(self, offset: int) -> str:
        return str(offset)

    def _normalize_limit(self, limit: int | None) -> int:
        if limit is None:
            return 25
        if limit <= 0:
            raise ValueError("Invalid limit. Expected a positive integer.")
        return min(limit, 100)
