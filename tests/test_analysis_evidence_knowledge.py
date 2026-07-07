"""SessionKnowledge projection: change facts, next_steps, typed fact kinds, open items."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.analysis.evidence.identity import canonical_subject_key, to_microseconds_utc
from marivo.analysis.evidence.knowledge import SessionKnowledge, build_session_knowledge
from marivo.analysis.evidence.store import open_judgment_store
from marivo.analysis.evidence.types import (
    AssociationSummary,
    AttributedDriver,
    ChangeFact,
    ForecastSummary,
    ObservationSummary,
    OpenAnomaly,
    OpenQuestion,
    Subject,
    TestedHypothesis,
)


def _seed_session(db_path: Path, *, evidence_status: str = "complete") -> None:
    """Insert one artifact + finding + change proposition + latest assessment + followup."""
    store = open_judgment_store(db_path)
    try:
        committed = to_microseconds_utc(datetime(2026, 5, 27, 12, tzinfo=UTC))
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO artifacts(artifact_id, session_id, step_type, artifact_type, "
                "artifact_schema_version, subject_payload, lineage_payload, confidence_scope, "
                "quality_summary, evidence_status, frame_path, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "art_1",
                    "sess_1",
                    "compare",
                    "delta_frame",
                    "v1",
                    json.dumps({"metric": "sales.revenue", "analysis_axis": "change"}),
                    "{}",
                    "{}",
                    "{}",
                    evidence_status,
                    "/tmp/data.parquet",
                    committed,
                ),
            )
            tx.execute(
                "INSERT INTO findings(finding_id, session_id, artifact_id, finding_type, "
                "canonical_item_key, subject_axis, subject_payload, payload, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "fnd_1",
                    "sess_1",
                    "art_1",
                    "delta",
                    "value",
                    "change",
                    json.dumps({"metric": "sales.revenue", "analysis_axis": "change"}),
                    json.dumps(
                        {
                            "direction": "increase",
                            "magnitude": 20.0,
                            "delta_kind": "scalar_delta",
                        }
                    ),
                    committed,
                ),
            )
            tx.execute(
                "INSERT INTO propositions(proposition_id, session_id, proposition_type, "
                "origin_kind, derivation_version, subject_key, payload, seed_finding_refs, "
                "created_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "prop_1",
                    "sess_1",
                    "change",
                    "system_seeded",
                    "v1",
                    "subjkey",
                    json.dumps(
                        {
                            "change_kind": "scalar_change",
                            "comparison_basis": "left_vs_right",
                        }
                    ),
                    json.dumps(["fnd_1"]),
                    committed,
                ),
            )
            tx.execute(
                "INSERT INTO assessment_snapshots(snapshot_id, proposition_id, session_id, "
                "status, confidence, confidence_basis, payload, created_at_us, is_latest) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "ass_1",
                    "prop_1",
                    "sess_1",
                    "validated",
                    0.9,
                    "seed_delta_direction_matches",
                    "{}",
                    committed,
                    1,
                ),
            )
            tx.execute(
                "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
                "category, source_issue_id, operator, payload, created_at_us) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "act_a",
                    "sess_1",
                    "art_1",
                    "dag_continuation",
                    None,
                    "assess_quality",
                    json.dumps(
                        {
                            "action_id": "act_a",
                            "kind": "submit_step",
                            "operator": "assess_quality",
                            "input_refs": ["art_1"],
                            "params": {},
                            "category": "dag_continuation",
                            "source_issue_id": None,
                        }
                    ),
                    committed,
                ),
            )
            tx.execute(
                "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
                "category, source_issue_id, operator, payload, executed_step_id, "
                "created_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "act_b",
                    "sess_1",
                    "art_1",
                    "dag_continuation",
                    None,
                    "discover",
                    json.dumps(
                        {
                            "action_id": "act_b",
                            "kind": "submit_step",
                            "operator": "discover",
                            "input_refs": ["art_1"],
                            "params": {"objective": "driver_axes"},
                            "category": "dag_continuation",
                            "source_issue_id": None,
                        }
                    ),
                    "step_already_done",
                    committed,
                ),
            )
    finally:
        store.close()


def _seed_observation_finding(
    db_path: Path,
    *,
    artifact_id: str,
    finding_id: str,
    metric: str = "sales.revenue",
    committed_us: int,
    digest: dict[str, Any] | None = None,
    analysis_purpose: str | None = None,
) -> None:
    """Insert one observe artifact + observation digest finding."""
    subject_payload = json.dumps({"metric": metric, "analysis_axis": "scalar"})
    store = open_judgment_store(db_path)
    try:
        with store.transaction() as tx:
            tx.execute(
                "INSERT INTO artifacts(artifact_id, session_id, step_type, artifact_type, "
                "artifact_schema_version, subject_payload, lineage_payload, confidence_scope, "
                "quality_summary, evidence_status, frame_path, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    "sess_1",
                    "observe",
                    "metric_frame",
                    "v1",
                    subject_payload,
                    "{}",
                    "{}",
                    "{}",
                    "complete",
                    "/tmp/data.parquet",
                    committed_us,
                ),
            )
            tx.execute(
                "INSERT INTO findings(finding_id, session_id, artifact_id, finding_type, "
                "canonical_item_key, subject_axis, subject_payload, payload, committed_at_us) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    finding_id,
                    "sess_1",
                    artifact_id,
                    "observation",
                    "digest",
                    "scalar",
                    subject_payload,
                    json.dumps(
                        {
                            "digest": digest or {"shape": "scalar", "value": 42.0},
                            "window": {
                                "field": "order_date",
                                "start": "2026-05-01",
                                "end": "2026-06-01",
                            },
                            "semantic_kind": "scalar",
                            "analysis_purpose": analysis_purpose,
                            "row_count": 1,
                        }
                    ),
                    committed_us,
                ),
            )
    finally:
        store.close()


def test_knowledge_observations_projects_digest_finding(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    committed = to_microseconds_utc(datetime(2026, 5, 27, 12, tzinfo=UTC))
    _seed_observation_finding(
        db_path,
        artifact_id="art_o1",
        finding_id="fnd_o1",
        committed_us=committed,
        analysis_purpose="check level",
    )
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")

    observations = knowledge.observations()
    assert len(observations) == 1
    obs = observations[0]
    assert isinstance(obs, ObservationSummary)
    assert obs.id == "fnd_o1"
    assert obs.subject.metric == "sales.revenue"
    assert obs.semantic_kind == "scalar"
    assert obs.analysis_purpose == "check level"
    assert obs.row_count == 1
    assert obs.digest.shape == "scalar"
    assert obs.digest.value == 42.0
    assert obs.window is not None
    assert obs.window.start == "2026-05-01"
    assert obs.source_refs == ["art_o1"]


def test_knowledge_observations_ordered_by_commit_time(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    later = to_microseconds_utc(datetime(2026, 5, 27, 13, tzinfo=UTC))
    earlier = to_microseconds_utc(datetime(2026, 5, 27, 12, tzinfo=UTC))
    _seed_observation_finding(
        db_path, artifact_id="art_late", finding_id="fnd_late", committed_us=later
    )
    _seed_observation_finding(
        db_path, artifact_id="art_early", finding_id="fnd_early", committed_us=earlier
    )
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")

    assert [obs.id for obs in knowledge.observations()] == ["fnd_early", "fnd_late"]


def test_knowledge_for_subject_filters_observations(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    committed = to_microseconds_utc(datetime(2026, 5, 27, 12, tzinfo=UTC))
    _seed_observation_finding(
        db_path,
        artifact_id="art_rev",
        finding_id="fnd_rev",
        metric="sales.revenue",
        committed_us=committed,
    )
    _seed_observation_finding(
        db_path,
        artifact_id="art_ord",
        finding_id="fnd_ord",
        metric="sales.orders_count",
        committed_us=committed,
    )
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")

    filtered = knowledge.for_subject(Subject(metric="sales.revenue", analysis_axis="scalar"))
    assert [obs.id for obs in filtered.observations()] == ["fnd_rev"]


def test_knowledge_observations_empty_when_unavailable() -> None:
    knowledge = SessionKnowledge(
        session_id="sess_1",
        snapshot_id="snap_x",
        snapshot_at=datetime(2026, 5, 27, 12, tzinfo=UTC),
        evidence_completeness="unavailable",
    )
    assert knowledge.observations() == []


def test_evidence_completeness_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path, evidence_status="complete")
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    assert knowledge.evidence_completeness == "complete"


def test_evidence_completeness_partial(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path, evidence_status="partial")
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    assert knowledge.evidence_completeness == "partial"


def test_facts_change_returns_change_fact(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    facts = knowledge.facts(kind="change")
    assert len(facts) == 1
    fact = facts[0]
    assert isinstance(fact, ChangeFact)
    assert fact.id == "prop_1"
    assert fact.status == "validated"
    assert fact.confidence == 0.9
    assert fact.direction == "increase"
    assert fact.magnitude == 20.0
    assert fact.latest_assessment_id == "ass_1"


def test_next_steps_filters_executed_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    actions = knowledge.next_steps(top=5)
    operators = [a.operator for a in actions]
    assert operators == ["assess_quality"]


def test_next_steps_top_caps_results(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_session(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_1")
    actions = knowledge.next_steps(top=0)
    assert actions == []


# ---------------------------------------------------------------------------
# Full surface: all typed fact kinds, open items, blocked followups, JSON round-trip
# ---------------------------------------------------------------------------

_COMMITTED = datetime(2026, 5, 27, 12, tzinfo=UTC)
_COMMITTED_US = to_microseconds_utc(_COMMITTED)


def _insert_artifact(
    tx: Any,
    *,
    artifact_id: str,
    session_id: str = "sess_full",
    subject: Subject,
    step_type: str,
    artifact_type: str,
) -> None:
    tx.execute(
        "INSERT INTO artifacts(artifact_id, session_id, step_type, artifact_type, "
        "artifact_schema_version, subject_payload, lineage_payload, confidence_scope, "
        "quality_summary, evidence_status, frame_path, committed_at_us) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            artifact_id,
            session_id,
            step_type,
            artifact_type,
            "v1",
            subject.model_dump_json(),
            "{}",
            "{}",
            "{}",
            "complete",
            f"/tmp/{artifact_id}.parquet",
            _COMMITTED_US,
        ),
    )


def _insert_proposition_bundle(
    tx: Any,
    *,
    proposition_id: str,
    proposition_type: str,
    artifact_id: str,
    finding_id: str,
    finding_type: str,
    canonical_item_key: str,
    subject: Subject,
    prop_payload: dict[str, Any],
    finding_payload: dict[str, Any],
    status: str = "validated",
    assessment_payload: dict[str, Any] | None = None,
) -> None:
    tx.execute(
        "INSERT INTO findings(finding_id, session_id, artifact_id, finding_type, "
        "canonical_item_key, subject_axis, subject_payload, payload, committed_at_us) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            finding_id,
            "sess_full",
            artifact_id,
            finding_type,
            canonical_item_key,
            subject.analysis_axis,
            subject.model_dump_json(),
            json.dumps(finding_payload),
            _COMMITTED_US,
        ),
    )
    tx.execute(
        "INSERT INTO propositions(proposition_id, session_id, proposition_type, "
        "origin_kind, derivation_version, subject_key, payload, seed_finding_refs, "
        "created_at_us) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            proposition_id,
            "sess_full",
            proposition_type,
            "system_seeded",
            "v1",
            canonical_subject_key(subject),
            json.dumps(prop_payload),
            json.dumps([finding_id]),
            _COMMITTED_US,
        ),
    )
    tx.execute(
        "INSERT INTO assessment_snapshots(snapshot_id, proposition_id, session_id, "
        "status, confidence, confidence_basis, payload, created_at_us, is_latest) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            f"ass_{proposition_id}",
            proposition_id,
            "sess_full",
            status,
            0.8,
            f"{proposition_type}_basis",
            json.dumps(assessment_payload or {}),
            _COMMITTED_US,
            1,
        ),
    )


def _seed_full_knowledge(db_path: Path) -> None:
    store = open_judgment_store(db_path)
    try:
        driver_subject = Subject(metric="dau", analysis_axis="decomposition")
        test_subject = Subject(metric="conversion", analysis_axis="scalar")
        forecast_subject = Subject(metric="revenue", analysis_axis="forecast")
        association_subject = Subject(metric="dau", analysis_axis="correlation")
        anomaly_subject = Subject(metric="dau", analysis_axis="anomaly")

        with store.transaction() as tx:
            for artifact_id, subject, step_type, artifact_type in [
                ("art_driver", driver_subject, "decompose", "attribution_frame"),
                ("art_test", test_subject, "hypothesis_test", "hypothesis_test_result"),
                ("art_forecast", forecast_subject, "forecast", "forecast_frame"),
                (
                    "art_association",
                    association_subject,
                    "correlate",
                    "association_result",
                ),
                ("art_anomaly", anomaly_subject, "discover", "candidate_set"),
            ]:
                _insert_artifact(
                    tx,
                    artifact_id=artifact_id,
                    subject=subject,
                    step_type=step_type,
                    artifact_type=artifact_type,
                )

            _insert_proposition_bundle(
                tx,
                proposition_id="prop_driver",
                proposition_type="driver",
                artifact_id="art_driver",
                finding_id="fnd_driver",
                finding_type="decomposition_item",
                canonical_item_key="country|country=us",
                subject=driver_subject,
                prop_payload={
                    "dimension": "country",
                    "dimension_keys": {"country": "us"},
                    "contribution_role": "primary_driver",
                    "scope_change_id": "prop_change_parent",
                },
                finding_payload={
                    "contribution_value": 42.0,
                    "contribution_share": 0.7,
                },
            )
            _insert_proposition_bundle(
                tx,
                proposition_id="prop_test",
                proposition_type="tested_hypothesis",
                artifact_id="art_test",
                finding_id="fnd_test",
                finding_type="test_result",
                canonical_item_key="conversion_test",
                subject=test_subject,
                prop_payload={
                    "hypothesis_family": "difference",
                    "alternative": "greater",
                    "method_family": "t_test",
                    "alpha": 0.01,
                },
                finding_payload={"p_value": 0.004, "reject_null": True},
            )
            _insert_proposition_bundle(
                tx,
                proposition_id="prop_forecast",
                proposition_type="forecast",
                artifact_id="art_forecast",
                finding_id="fnd_forecast",
                finding_type="forecast_point",
                canonical_item_key="horizon=1",
                subject=forecast_subject,
                prop_payload={
                    "forecast_window": {
                        "field": "ds",
                        "start": "2026-06-01",
                        "end": "2026-06-07",
                    },
                    "horizon_index": 1,
                    "forecast_kind": "interval",
                },
                finding_payload={"prediction_interval": [90.0, 120.0]},
            )
            _insert_proposition_bundle(
                tx,
                proposition_id="prop_association",
                proposition_type="association",
                artifact_id="art_association",
                finding_id="fnd_association",
                finding_type="correlation_result",
                canonical_item_key="dau~revenue",
                subject=association_subject,
                prop_payload={
                    "left_subject": {"metric": "dau"},
                    "right_subject": {"metric": "revenue"},
                    "method_family": "pearson",
                    "lag_mode": "sweep",
                    "lag_sweep": {
                        "grid_min": -7,
                        "grid_max": 7,
                        "step": 1,
                        "selected_lag": 2,
                    },
                    "join_basis": "date",
                },
                finding_payload={"coefficient": 0.82},
            )
            _insert_proposition_bundle(
                tx,
                proposition_id="prop_anomaly",
                proposition_type="anomaly",
                artifact_id="art_anomaly",
                finding_id="fnd_anomaly",
                finding_type="anomaly_candidate",
                canonical_item_key="bucket=2026-05-20",
                subject=anomaly_subject,
                prop_payload={},
                finding_payload={"score": 4.2},
                status="pending",
            )

            for artifact_id in ["art_block_a", "art_block_b"]:
                _insert_artifact(
                    tx,
                    artifact_id=artifact_id,
                    subject=Subject(metric="dau", analysis_axis="scalar"),
                    step_type="observe",
                    artifact_type="metric_frame",
                )
            for issue_id, artifact_id in [
                ("iss_block_a", "art_block_a"),
                ("iss_block_b", "art_block_b"),
            ]:
                tx.execute(
                    "INSERT INTO blocking_issues(issue_id, session_id, artifact_id, "
                    "kind, severity, payload, created_at_us) VALUES (?,?,?,?,?,?,?)",
                    (
                        issue_id,
                        "sess_full",
                        artifact_id,
                        "sample_size_low",
                        "blocking",
                        json.dumps({"message": "too few rows"}),
                        _COMMITTED_US,
                    ),
                )
            tx.execute(
                "INSERT INTO followups(followup_id, session_id, source_artifact_id, "
                "category, source_issue_id, operator, payload, created_at_us) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "act_blocked",
                    "sess_full",
                    "art_block_a",
                    "quality_remediation",
                    "iss_block_a",
                    "observe",
                    json.dumps(
                        {
                            "action_id": "act_blocked",
                            "kind": "submit_step",
                            "operator": "observe",
                            "input_refs": ["art_block_a"],
                            "params": {"time": "longer_window"},
                            "category": "quality_remediation",
                            "source_issue_id": "iss_block_a",
                        }
                    ),
                    _COMMITTED_US,
                ),
            )
    finally:
        store.close()


def test_knowledge_projects_all_typed_fact_kinds(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_full_knowledge(db_path)

    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_full")

    driver = knowledge.facts(kind="driver")[0]
    assert isinstance(driver, AttributedDriver)
    assert driver.dimension == "country"
    assert driver.dimension_keys == {"country": "us"}
    assert driver.contribution_value == 42.0
    assert driver.contribution_role == "primary_driver"
    assert driver.scope_change_id == "prop_change_parent"

    tested = knowledge.facts(kind="tested_hypothesis")[0]
    assert isinstance(tested, TestedHypothesis)
    assert tested.alternative == "greater"
    assert tested.alpha == 0.01
    assert tested.p_value == 0.004
    assert tested.reject_null is True

    forecast = knowledge.facts(kind="forecast")[0]
    assert isinstance(forecast, ForecastSummary)
    assert forecast.forecast_window.field == "ds"
    assert forecast.forecast_window.start == "2026-06-01"
    assert forecast.horizon_index == 1
    assert forecast.prediction_interval == [90.0, 120.0]

    association = knowledge.facts(kind="association")[0]
    assert isinstance(association, AssociationSummary)
    assert association.coefficient == 0.82
    assert association.lag_mode == "sweep"
    assert association.lag_sweep is not None
    assert association.lag_sweep.selected_lag == 2


def test_knowledge_open_items_and_blocked_followups(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_full_knowledge(db_path)

    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_full")

    anomalies = knowledge.open_items(kind="anomaly")
    assert len(anomalies) == 1
    assert isinstance(anomalies[0], OpenAnomaly)
    assert anomalies[0].id == "prop_anomaly"

    questions = knowledge.open_items(kind="question")
    assert len(questions) == 1
    assert isinstance(questions[0], OpenQuestion)
    assert questions[0].reason == "persistent_blocking_issue"

    blocked = knowledge.blocked_followups()
    assert len(blocked) == 1
    assert blocked[0].action_id == "act_blocked"
    assert blocked[0].reason == "blocking_issue_unresolved"
    assert blocked[0].blocking_issue_kind == "sample_size_low"


def test_knowledge_for_subject_filters_typed_facts_and_open_anomalies(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_full_knowledge(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_full")

    dau_drivers = knowledge.for_subject(Subject(metric="dau", analysis_axis="decomposition"))

    assert len(dau_drivers.facts(kind="driver")) == 1
    assert dau_drivers.facts(kind="tested_hypothesis") == []
    assert dau_drivers.open_items(kind="anomaly") == []
    assert dau_drivers.open_items(kind="question") == knowledge.open_items(kind="question")
    assert dau_drivers.blocked_followups() == knowledge.blocked_followups()


def test_knowledge_snapshot_json_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "judgment.db"
    _seed_full_knowledge(db_path)
    knowledge = build_session_knowledge(db_path=db_path, session_id="sess_full")

    restored = SessionKnowledge.model_validate_json(knowledge.model_dump_json())

    assert restored == knowledge
    assert isinstance(restored.facts(kind="driver")[0], AttributedDriver)
