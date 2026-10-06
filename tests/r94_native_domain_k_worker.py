"""Supplement archived native producers with source-free disclosed continuations."""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import (
    graph_local_execution,
    history_execution,
    journey_execution,
)
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, obj, read
from tests.lifecycle_r75_fixtures import END, START
from tests.r94_domain_recovery_worker import checked_bytes, forbidden, snapshot


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def run_ids(session: Session) -> set[str]:
    identities: set[str] = set()
    cursor: str | None = None
    while True:
        page = session.runs(limit=100, cursor=cursor)
        identities.update(item.run_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return identities


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    manifest = read(root / "r94-domain.json")
    session_id = manifest["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(history_execution, "execute", forbidden),
        patch.object(journey_execution, "execute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        restored: dict[str, _MaterializedRead] = {}
        for name, raw in obj(manifest["sources"]).items():
            saved = obj(raw)
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead)
            assert snapshot(value) == saved
            restored[name] = value
        history = restored["history"]
        retention = restored["retention"]
        observed = restored["observation"]
        journey = restored["journey"]
        anchors = restored["anchors"]
        assert isinstance(history, mv.MaterializedHistoryResult)
        assert isinstance(retention, mv.MaterializedRetentionResult)
        assert isinstance(observed, mv.MaterializedNumericRelation)
        assert isinstance(journey, mv.MaterializedJourneyResult)
        assert isinstance(anchors, mv.MaterializedAnchorDomain)
        role = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
        assert journey.subjects(role) == journey.subject_binding
        assert anchors.subjects(role) == anchors.subject_binding
        duration = journey.time_to_event(
            from_step=mv.step(participant=role, key="start"),
            to_step=mv.step(
                participant=ms.participant_role(
                    event=ms.ref.event("commerce.finished"), name="subject"
                ),
                key="finish",
            ),
        )
        defined = observed.where(observed.value.is_defined())
        operations: dict[str, Continuation] = {
            "history_read": history.read(
                mv.in_state(
                    ms.model_state(model=ms.ref.state_model("commerce.model"), name="done"), at=END
                )
            ),
            "history_transitions": history.transitions(),
            "history_violations": history.violations(),
            "retention_status": retention.status,
            "retention_true": retention.known_true(),
            "retention_false": retention.known_false(),
            "retention_unknown": retention.unknown(),
            "retention_members": retention.known_true().members(through=retention.subject_binding),
            "observation_where": defined,
            "observation_count": defined.summarize(mv.count()),
            "observation_count_defined": defined.summarize(mv.count_defined()),
            "observation_sum": defined.summarize(mv.sum()),
            "observation_min": defined.summarize(mv.min()),
            "observation_max": defined.summarize(mv.max()),
            "journey_status": duration.status,
            "journey_started_at": duration.started_at,
            "journey_completed_at": duration.completed_at,
            "journey_duration": duration.duration,
            "journey_observed_duration": duration.observed_duration,
            "journey_followup_until": duration.followup_until,
            "journey_completed": duration.completed(),
        }
        # The other root actions were executed in the archived seven-output packet.
        consumed = {
            "history": {
                "relation.read(field)",
                "relation.distribution(at=at)",
                "relation.transitions()",
                "relation.violations()",
                "relation.intervals()",
                "relation.dwell()",
            },
            "anchors": {"relation.subjects(role)"},
            "journey": {
                "relation.subjects(role)",
                "relation.time_to_event(from_step=from_step, to_step=to_step)",
            },
            "retention": {
                "relation.status",
                "relation.known_true()",
                "relation.known_false()",
                "relation.unknown()",
                "relation.by_subject(rule=rule)",
            },
            "observation": {"relation.where(predicate)"}
            | {
                f"relation.where(relation.value.is_defined()).summarize(mv.{method}())"
                for method in ("count", "count_defined", "sum", "min", "max")
            },
        }
        for name, result in restored.items():
            assert {action.call for action in result.contract().actions} == consumed[name]
        outputs: dict[str, Json] = {}
        for name, logical in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            frame = result.to_pandas()
            if name == "history_read":
                assert frame.value.tolist() == [True, True, False]
            elif name in ("retention_status", "retention_true"):
                assert frame.value.tolist() == [True, True], (name, frame.to_dict())
            elif name in ("retention_false", "retention_unknown", "history_violations"):
                assert len(frame) == 0
            elif name.startswith("observation_"):
                expected = {
                    "where": [2, 2],
                    "count": [2],
                    "count_defined": [2],
                    "sum": [4],
                    "min": [2],
                    "max": [2],
                }
                assert frame.value.tolist() == expected[name.removeprefix("observation_")]
            elif name in ("journey_duration", "journey_observed_duration"):
                assert frame.value.tolist() == [timedelta(seconds=5)] * 2
            elif name == "retention_members":
                assert frame.member.tolist() == [9007199254740993, 9007199254740994]
            elif name == "journey_status":
                assert frame.value.tolist() == ["complete", "complete"]
            if name == "history_transitions":
                assert {
                    (row[0], row[1]): row[2]
                    for row in frame[["from_state", "to_state", "count"]].itertuples(
                        index=False, name=None
                    )
                } == {
                    ("open", "done"): 2,
                    ("open", "open"): 0,
                    ("open", "paid"): 0,
                    ("paid", "done"): 0,
                }
            elif name in ("journey_started_at", "journey_completed_at", "journey_followup_until"):
                offsets = (0, 2) if name == "journey_started_at" else (5, 7)
                assert frame.value.tolist() == [
                    (START + timedelta(seconds=offset)).replace(tzinfo=None) for offset in offsets
                ]
            elif name == "journey_completed":
                assert list(frame.itertuples(index=False, name=None)) == [
                    (9007199254740993, "commerce.started", 9007199254740993),
                    (9007199254740994, "commerce.started", 9007199254740995),
                ]
            saved = snapshot(result)
            repeat_before = run_ids(session)
            if phase == "cold":
                assert saved == obj(manifest["supplement"])[name]
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(logical.execute()) == saved
            assert run_ids(session) == repeat_before
            outputs[name] = saved
        if phase == "fixed":
            assert kernels.call_count > 0
            manifest["supplement"] = outputs
            (root / "r94-domain.json").write_bytes(checked_bytes(manifest))
        else:
            assert kernels.call_count == 0
            assert run_ids(session) == before
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "source_offline": True,
            "resources": 0,
            "root_actions": checked({name: sorted(calls) for name, calls in consumed.items()}),
            "outputs": outputs,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(checked_bytes(run(Path(sys.argv[1]), sys.argv[2])))
