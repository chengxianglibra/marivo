"""SessionKnowledge projection over judgment.db."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from marivo.analysis.evidence.identity import (
    canonical_json,
    canonical_subject_key,
)
from marivo.analysis.evidence.types import (
    AssociationSummary,
    AttributedDriver,
    BlockedFollowup,
    ChangeFact,
    EvidenceCompleteness,
    FactKind,
    ForecastSummary,
    LagSweepSummary,
    ObservationDigest,
    ObservationSummary,
    OpenAnomaly,
    OpenItemKind,
    OpenQuestion,
    Subject,
    TestedHypothesis,
    TimeWindow,
)
from marivo.analysis.followups import FollowupAction


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _completeness(conn: sqlite3.Connection, session_id: str) -> EvidenceCompleteness:
    rows = conn.execute(
        "SELECT evidence_status, count(*) FROM artifacts "
        "WHERE session_id=? GROUP BY evidence_status",
        (session_id,),
    ).fetchall()
    statuses = {row[0]: row[1] for row in rows}
    if not statuses:
        return "complete"
    if "unavailable" in statuses:
        return "unavailable"
    if "partial" in statuses:
        return "partial"
    return "complete"


def _proposition_with_assessment(
    conn: sqlite3.Connection, session_id: str, proposition_type: str
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.proposition_id, p.subject_key, p.payload AS prop_payload,
               p.seed_finding_refs,
               a.snapshot_id, a.status, a.confidence, a.confidence_basis,
               a.payload AS assess_payload,
               f.payload AS finding_payload, f.subject_payload, f.artifact_id
        FROM propositions p
        LEFT JOIN assessment_snapshots a
          ON a.proposition_id = p.proposition_id AND a.is_latest = 1
        LEFT JOIN findings f
          ON f.finding_id = json_extract(p.seed_finding_refs, '$[0]')
        WHERE p.session_id = ? AND p.proposition_type = ?
        ORDER BY p.created_at_us, p.proposition_id
        """,
        (session_id, proposition_type),
    ).fetchall()


def _row_subject(row: sqlite3.Row) -> Subject:
    subject_payload = _loads(row["subject_payload"])
    if subject_payload:
        return Subject.model_validate(subject_payload)
    return Subject(analysis_axis="scalar")


def _row_payloads(
    row: sqlite3.Row,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _loads(row["prop_payload"]),
        _loads(row["finding_payload"]),
        _loads(row["assess_payload"]),
    )


def _base_fact_kwargs(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["proposition_id"],
        "subject": _row_subject(row),
        "window": None,
        "status": row["status"] or "pending",
        "confidence": row["confidence"],
        "confidence_basis": row["confidence_basis"] or "",
        "source_refs": [row["artifact_id"]] if row["artifact_id"] else [],
        "latest_assessment_id": row["snapshot_id"] or "",
    }


def _change_facts(conn: sqlite3.Connection, session_id: str) -> list[ChangeFact]:
    facts: list[ChangeFact] = []
    for row in _proposition_with_assessment(conn, session_id, "change"):
        prop, finding, _assess = _row_payloads(row)
        facts.append(
            ChangeFact(
                **_base_fact_kwargs(row),
                direction=finding.get("direction", "undefined"),
                magnitude=finding.get("magnitude"),
                comparison_window=None,
                comparison_basis=prop.get("comparison_basis", "left_vs_right"),
                dimension_keys=prop.get("dimension_keys"),
            )
        )
    return facts


def _driver_facts(conn: sqlite3.Connection, session_id: str) -> list[AttributedDriver]:
    facts: list[AttributedDriver] = []
    for row in _proposition_with_assessment(conn, session_id, "driver"):
        prop, finding, assess = _row_payloads(row)
        facts.append(
            AttributedDriver(
                **_base_fact_kwargs(row),
                dimension=prop.get("dimension") or finding.get("dimension") or "",
                dimension_keys=(prop.get("dimension_keys") or finding.get("dimension_keys") or {}),
                contribution_value=(
                    assess.get("contribution_value")
                    if "contribution_value" in assess
                    else finding.get("contribution_value")
                ),
                contribution_share=(
                    assess.get("contribution_share")
                    if "contribution_share" in assess
                    else finding.get("contribution_share")
                ),
                contribution_role=prop.get("contribution_role", "material_component"),
                scope_change_id=prop.get("scope_change_id")
                or prop.get("scope_delta_ref")
                or finding.get("scope_delta_ref"),
            )
        )
    return facts


def _tested_hypothesis_facts(conn: sqlite3.Connection, session_id: str) -> list[TestedHypothesis]:
    facts: list[TestedHypothesis] = []
    for row in _proposition_with_assessment(conn, session_id, "tested_hypothesis"):
        prop, finding, assess = _row_payloads(row)
        facts.append(
            TestedHypothesis(
                **_base_fact_kwargs(row),
                hypothesis_family=prop.get("hypothesis_family", "difference"),
                alternative=prop.get("alternative", "two_sided"),
                method_family=prop.get("method_family", ""),
                alpha=float(prop.get("alpha", 0.05)),
                p_value=assess.get("p_value", finding.get("p_value")),
                reject_null=assess.get("reject_null", finding.get("reject_null")),
            )
        )
    return facts


def _forecast_window(payload: dict[str, Any]) -> TimeWindow:
    return TimeWindow(
        field=str(payload.get("field", "ds")),
        start=str(payload.get("start", "")),
        end=str(payload.get("end", "")),
    )


def _forecast_facts(conn: sqlite3.Connection, session_id: str) -> list[ForecastSummary]:
    facts: list[ForecastSummary] = []
    for row in _proposition_with_assessment(conn, session_id, "forecast"):
        prop, finding, assess = _row_payloads(row)
        facts.append(
            ForecastSummary(
                **_base_fact_kwargs(row),
                forecast_window=_forecast_window(prop.get("forecast_window") or {}),
                horizon_index=int(prop.get("horizon_index", 0)),
                forecast_kind=prop.get("forecast_kind", "point"),
                prediction_interval=assess.get(
                    "prediction_interval", finding.get("prediction_interval")
                ),
            )
        )
    return facts


def _lag_sweep(payload: Any) -> LagSweepSummary | None:
    if not isinstance(payload, dict):
        return None
    return LagSweepSummary.model_validate(payload)


def _association_facts(conn: sqlite3.Connection, session_id: str) -> list[AssociationSummary]:
    facts: list[AssociationSummary] = []
    for row in _proposition_with_assessment(conn, session_id, "association"):
        prop, finding, assess = _row_payloads(row)
        facts.append(
            AssociationSummary(
                **_base_fact_kwargs(row),
                left_subject=prop.get("left_subject", {}),
                right_subject=prop.get("right_subject", {}),
                method_family=prop.get("method_family", finding.get("method", "")),
                coefficient=assess.get("coefficient", finding.get("coefficient")),
                lag_mode=prop.get("lag_mode", finding.get("lag_mode", "single")),
                lag=prop.get("lag", finding.get("lag")),
                lag_sweep=_lag_sweep(prop.get("lag_sweep") or finding.get("lag_sweep")),
                join_basis=prop.get("join_basis", finding.get("join_basis", "")),
            )
        )
    return facts


_DIGEST_ADAPTER: TypeAdapter[ObservationDigest] = TypeAdapter(ObservationDigest)


def _observation_window(payload: Any) -> TimeWindow | None:
    if not isinstance(payload, dict):
        return None
    start = payload.get("start")
    end = payload.get("end")
    if start is None or end is None:
        return None
    return TimeWindow(field=str(payload.get("field", "")), start=str(start), end=str(end))


def _observation_summaries(conn: sqlite3.Connection, session_id: str) -> list[ObservationSummary]:
    rows = conn.execute(
        "SELECT finding_id, artifact_id, subject_payload, payload FROM findings "
        "WHERE session_id = ? AND finding_type = 'observation' "
        "ORDER BY committed_at_us, finding_id",
        (session_id,),
    ).fetchall()
    summaries: list[ObservationSummary] = []
    for row in rows:
        payload = _loads(row["payload"])
        summaries.append(
            ObservationSummary(
                id=row["finding_id"],
                subject=_row_subject(row),
                window=_observation_window(payload.get("window")),
                semantic_kind=payload.get("semantic_kind") or "scalar",
                analysis_purpose=payload.get("analysis_purpose"),
                row_count=int(payload.get("row_count") or 0),
                digest=_DIGEST_ADAPTER.validate_python(
                    payload.get("digest") or {"shape": "scalar"}
                ),
                source_refs=[row["artifact_id"]] if row["artifact_id"] else [],
            )
        )
    return summaries


def _open_anomalies(conn: sqlite3.Connection, session_id: str) -> list[OpenAnomaly]:
    items: list[OpenAnomaly] = []
    for row in _proposition_with_assessment(conn, session_id, "anomaly"):
        if row["status"] in ("validated", "refuted"):
            continue
        items.append(OpenAnomaly(**_base_fact_kwargs(row)))
    return items


def _open_questions(conn: sqlite3.Connection, session_id: str) -> list[OpenQuestion]:
    rows = conn.execute(
        """
        SELECT kind, count(DISTINCT artifact_id) AS artifact_count,
               group_concat(DISTINCT artifact_id) AS artifact_ids
        FROM blocking_issues
        WHERE session_id = ? AND resolved_by_step_id IS NULL
        GROUP BY kind
        HAVING artifact_count >= 2
        ORDER BY kind
        """,
        (session_id,),
    ).fetchall()
    questions: list[OpenQuestion] = []
    for row in rows:
        source_refs = [
            artifact_id for artifact_id in str(row["artifact_ids"] or "").split(",") if artifact_id
        ]
        questions.append(
            OpenQuestion(
                id=f"oq_persistent_blocking_issue_{row['kind']}",
                subject=Subject(analysis_axis="scalar"),
                window=None,
                status="pending",
                confidence=None,
                confidence_basis=f"persistent_blocking_issue:{row['kind']}",
                source_refs=source_refs,
                latest_assessment_id="",
                reason="persistent_blocking_issue",
            )
        )
    return questions


def _blocked_followups(conn: sqlite3.Connection, session_id: str) -> list[BlockedFollowup]:
    rows = conn.execute(
        """
        SELECT f.followup_id, f.operator, f.source_artifact_id,
               b.kind AS blocking_issue_kind
        FROM followups f
        JOIN blocking_issues b ON b.issue_id = f.source_issue_id
        WHERE f.session_id = ?
          AND f.executed_step_id IS NULL
          AND b.resolved_by_step_id IS NULL
        ORDER BY f.created_at_us, f.followup_id
        """,
        (session_id,),
    ).fetchall()
    return [
        BlockedFollowup(
            action_id=row["followup_id"],
            operator=row["operator"],
            source_artifact_id=row["source_artifact_id"],
            reason="blocking_issue_unresolved",
            blocking_issue_kind=row["blocking_issue_kind"],
        )
        for row in rows
    ]


def _next_steps(conn: sqlite3.Connection, session_id: str, top: int) -> list[FollowupAction]:
    if top <= 0:
        return []
    rows = conn.execute(
        "SELECT payload FROM followups WHERE session_id=? "
        "AND executed_step_id IS NULL "
        "ORDER BY created_at_us, followup_id",
        (session_id,),
    ).fetchall()
    seen: set[str] = set()
    actions: list[FollowupAction] = []
    for row in rows:
        action = FollowupAction.model_validate(json.loads(row["payload"]))
        dedupe_key = canonical_json(
            {
                "operator": action.operator,
                "input_refs": sorted(action.input_refs),
                "params": action.params,
            }
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        actions.append(action)
        if len(actions) >= top:
            break
    return actions


class SessionKnowledge(BaseModel):
    """Immutable snapshot of session-level evidence knowledge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    snapshot_id: str
    snapshot_at: datetime
    evidence_completeness: EvidenceCompleteness
    observation_summaries: tuple[ObservationSummary, ...] = Field(default_factory=tuple)
    change_facts: tuple[ChangeFact, ...] = Field(default_factory=tuple)
    driver_facts: tuple[AttributedDriver, ...] = Field(default_factory=tuple)
    tested_hypothesis_facts: tuple[TestedHypothesis, ...] = Field(default_factory=tuple)
    forecast_facts: tuple[ForecastSummary, ...] = Field(default_factory=tuple)
    association_facts: tuple[AssociationSummary, ...] = Field(default_factory=tuple)
    open_anomalies: tuple[OpenAnomaly, ...] = Field(default_factory=tuple)
    open_questions: tuple[OpenQuestion, ...] = Field(default_factory=tuple)
    blocked_followup_items: tuple[BlockedFollowup, ...] = Field(default_factory=tuple)
    next_steps_payload: tuple[FollowupAction, ...] = Field(default_factory=tuple)

    def facts(self, kind: FactKind | None = None) -> list[Any]:
        """Return validated facts, optionally filtered by kind."""
        if kind == "change":
            return list(self.change_facts)
        if kind == "driver":
            return list(self.driver_facts)
        if kind == "tested_hypothesis":
            return list(self.tested_hypothesis_facts)
        if kind == "forecast":
            return list(self.forecast_facts)
        if kind == "association":
            return list(self.association_facts)
        if kind is None:
            return [
                *self.change_facts,
                *self.driver_facts,
                *self.tested_hypothesis_facts,
                *self.forecast_facts,
                *self.association_facts,
            ]
        return []

    def observations(self) -> list[ObservationSummary]:
        """Return observation digests for observe / derive_metric_frame commits, oldest first."""
        return list(self.observation_summaries)

    def open_items(self, kind: OpenItemKind | None = None) -> list[Any]:
        """Return unresolved open items, optionally filtered by kind."""
        if kind == "anomaly":
            return list(self.open_anomalies)
        if kind == "question":
            return list(self.open_questions)
        if kind is None:
            return [*self.open_anomalies, *self.open_questions]
        return []

    def blocked_followups(self) -> list[BlockedFollowup]:
        """Return unexecuted followups blocked by unresolved issues."""
        return list(self.blocked_followup_items)

    def next_steps(self, top: int = 5) -> list[FollowupAction]:
        """Return unexecuted followup actions, capped at *top*."""
        if top <= 0:
            return []
        return list(self.next_steps_payload[:top])

    def for_subject(self, subject: Subject) -> SessionKnowledge:
        """Return a filtered view for one canonical subject key."""
        target = canonical_subject_key(subject)

        def matches(candidate: Subject) -> bool:
            return canonical_subject_key(candidate) == target

        return SessionKnowledge(
            session_id=self.session_id,
            snapshot_id=self.snapshot_id,
            snapshot_at=self.snapshot_at,
            evidence_completeness=self.evidence_completeness,
            observation_summaries=tuple(
                o for o in self.observation_summaries if matches(o.subject)
            ),
            change_facts=tuple(f for f in self.change_facts if matches(f.subject)),
            driver_facts=tuple(f for f in self.driver_facts if matches(f.subject)),
            tested_hypothesis_facts=tuple(
                f for f in self.tested_hypothesis_facts if matches(f.subject)
            ),
            forecast_facts=tuple(f for f in self.forecast_facts if matches(f.subject)),
            association_facts=tuple(f for f in self.association_facts if matches(f.subject)),
            open_anomalies=tuple(item for item in self.open_anomalies if matches(item.subject)),
            open_questions=self.open_questions,
            blocked_followup_items=self.blocked_followup_items,
            next_steps_payload=self.next_steps_payload,
        )


def build_session_knowledge(*, db_path: Path, session_id: str) -> SessionKnowledge:
    """Build a SessionKnowledge snapshot from judgment.db."""
    conn = _open_readonly(db_path)
    try:
        snapshot_at = datetime.now(UTC)
        completeness = _completeness(conn, session_id)
        observation_summaries = tuple(_observation_summaries(conn, session_id))
        change_facts = tuple(_change_facts(conn, session_id))
        driver_facts = tuple(_driver_facts(conn, session_id))
        tested_hypothesis_facts = tuple(_tested_hypothesis_facts(conn, session_id))
        forecast_facts = tuple(_forecast_facts(conn, session_id))
        association_facts = tuple(_association_facts(conn, session_id))
        open_anomalies = tuple(_open_anomalies(conn, session_id))
        open_questions = tuple(_open_questions(conn, session_id))
        blocked_followup_items = tuple(_blocked_followups(conn, session_id))
        next_steps_payload = tuple(_next_steps(conn, session_id, top=100))
        snapshot_payload = {
            "evidence_completeness": completeness,
            "observations": [item.model_dump(mode="json") for item in observation_summaries],
            "facts": [
                item.model_dump(mode="json")
                for item in (
                    *change_facts,
                    *driver_facts,
                    *tested_hypothesis_facts,
                    *forecast_facts,
                    *association_facts,
                )
            ],
            "open_items": [
                item.model_dump(mode="json") for item in (*open_anomalies, *open_questions)
            ],
            "blocked_followups": [item.model_dump(mode="json") for item in blocked_followup_items],
            "next_steps": [item.model_dump(mode="json") for item in next_steps_payload],
        }
        digest = hashlib.sha256(canonical_json(snapshot_payload).encode()).hexdigest()[:16]
        snapshot_id = f"snap_{session_id}_{digest}"
        return SessionKnowledge(
            session_id=session_id,
            snapshot_id=snapshot_id,
            snapshot_at=snapshot_at,
            evidence_completeness=completeness,
            observation_summaries=observation_summaries,
            change_facts=change_facts,
            driver_facts=driver_facts,
            tested_hypothesis_facts=tested_hypothesis_facts,
            forecast_facts=forecast_facts,
            association_facts=association_facts,
            open_anomalies=open_anomalies,
            open_questions=open_questions,
            blocked_followup_items=blocked_followup_items,
            next_steps_payload=next_steps_payload,
        )
    finally:
        conn.close()


__all__ = ["SessionKnowledge", "build_session_knowledge"]
