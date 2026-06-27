"""SessionKnowledge full Surface 2 projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marivo.analysis.evidence.identity import (
    canonical_subject_key,
    to_microseconds_utc,
)
from marivo.analysis.evidence.knowledge import (
    SessionKnowledge,
    build_session_knowledge,
)
from marivo.analysis.evidence.store import open_judgment_store
from marivo.analysis.evidence.types import (
    AssociationSummary,
    AttributedDriver,
    ForecastSummary,
    OpenAnomaly,
    OpenQuestion,
    Subject,
    TestedHypothesis,
)

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
