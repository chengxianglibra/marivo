"""Atomic reducer publication and cold selected-authority failure boundaries."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from marivo.analysis import time_scope
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.domains.completeness import BoundedCompletenessDeclarationV1
from marivo.analysis.domains.contracts import EventJourneySemantics
from marivo.analysis.domains.event import LogicalEventDataset
from marivo.analysis.event import first_per_subject, sequence, step
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json, parse_json
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.event_publication import bind_event_summary
from marivo.analysis.materialization.event_reducer_codec import EventTimeToEventEvidenceSummary
from marivo.analysis.subject import dropped_before
from marivo.refs import ref
from marivo.semantic.event import participant_role
from tests.lazy_adapter_runtime_worker import forbidden, snapshot
from tests.lazy_event_runtime_fixtures import (
    END,
    OCCURRENCE_CANARY,
    START,
    THROUGH,
    journey,
    setup_event,
)
from tests.lazy_event_runtime_worker import assert_identity_private

pytestmark = pytest.mark.runtime


def test_time_to_event_entry_coverage_propagates_and_anchor_is_observed(tmp_path: Path) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    pattern = sequence(
        *(
            step(
                participant=participant_role(event=ref.event(f"sales.{event}"), name="buyer"),
                key=key,
            )
            for key, event in (
                ("a", "started"),
                ("b", "finished"),
                ("c", "started"),
                ("d", "finished"),
            )
        )
    )
    retained = sources.events.match(
        pattern,
        cohort_window=time_scope(start=START, end=END),
        completion_through=THROUGH,
        matching=first_per_subject(),
        completeness=(
            BoundedCompletenessDeclarationV1(
                inputs=(ref.event("sales.started"),),
                complete_from=START,
                complete_through=THROUGH,
                rationale="Complete anchor and repeated start coverage.",
            ),
        ),
    ).execute()
    later = retained.time_to_event(from_step=pattern.steps[2], to_step=pattern.steps[3]).execute()
    anchor = retained.time_to_event(from_step=pattern.steps[0], to_step=pattern.steps[1]).execute()
    assert later.to_pandas().completion_status.tolist() == ["not_entered", "entry_unknown"]
    assert anchor.to_pandas().completion_status.tolist() == ["complete", "coverage_censored"]
    record = runtime.store.artifact(anchor.state.artifact_ref.ref)
    assert record is not None
    summary = record.descriptor.event_evidence
    assert isinstance(summary, EventTimeToEventEvidenceSummary)
    with pytest.raises(MaterializationError):
        bind_event_summary(
            record.descriptor,
            replace(
                summary,
                coverage_censored_count=0,
                entry_unknown_count=1,
            ),
        )
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    assert cold.artifact(later.state.artifact_ref).to_pandas().completion_status.tolist() == [
        "not_entered",
        "entry_unknown",
    ]
    assert cold.artifact(anchor.state.artifact_ref).to_pandas().completion_status.tolist() == [
        "complete",
        "coverage_censored",
    ]


def _reducer(value: LogicalEventDataset, kind: str) -> LogicalDataset:
    semantics = value.row_contract.family_semantics
    assert isinstance(semantics, EventJourneySemantics)
    first, last = semantics.pattern.steps
    if kind == "funnel":
        return value.funnel()
    if kind == "time_to_event":
        return value.time_to_event(from_step=first, to_step=last)
    return value.select_subjects(dropped_before(step=last))


@pytest.mark.parametrize("kind", ["funnel", "time_to_event", "selection"])
def test_reducer_cancel_after_staging_publishes_no_authority(tmp_path: Path, kind: str) -> None:
    def cancel(point: str) -> None:
        if point == "before_commit":
            raise KeyboardInterrupt("private-reducer-cancellation-canary")

    runtime, sources, _ = setup_event(tmp_path, engine=True, event=cancel)
    with pytest.raises(MaterializationError) as caught:
        _reducer(journey(sources), kind).execute()
    assert "canary" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    counts = snapshot(runtime)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    assert counts["analysis_action_run_terminals"] == 1
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))
    assert not list(runtime.store.layout.session_dir(runtime.session_ref).rglob("payload.duckdb"))
    assert_identity_private(runtime)


@pytest.mark.parametrize("kind", ["funnel", "time_to_event", "selection"])
def test_cold_reducer_metadata_corruption_fails_without_origin_or_partial_output(
    tmp_path: Path, kind: str
) -> None:
    runtime, sources, database = setup_event(tmp_path, engine=True)
    output = _reducer(journey(sources), kind).execute()
    artifact = output.state.artifact_ref.ref
    with sqlite3.connect(runtime.store.db_path) as connection:
        row = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (artifact,)
        ).fetchone()
        assert row is not None and isinstance(row[0], str)
        payload = parse_json(row[0])
        assert isinstance(payload, dict)
        summary = payload["subject_selection_evidence" if kind == "selection" else "event_evidence"]
        assert isinstance(summary, dict)
        summary["unknown_subject_count" if kind == "selection" else "row_count"] = 999
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), artifact),
        )
    database.unlink()
    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, target=runtime.target)
    with (
        patch("marivo.analysis.materialization.admission.supervise", forbidden),
        pytest.raises(IntegrityError) as caught,
    ):
        cold.artifact(output.state.artifact_ref)
    assert str(OCCURRENCE_CANARY) not in str(caught.value)
    assert snapshot(cold) == before
    assert cold.store.resources(cold.session_ref) == ()
