"""Consume native History view cards with independent boundary and Cell oracles."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, obj, read
from tests.r94_domain_recovery_worker import checked_bytes, forbidden, snapshot
from tests.r94_native_domain_k_worker import Continuation, run_ids
from tests.r94_statistic_k_worker import compact

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = START + timedelta(seconds=100)
SUBJECTS = (9007199254740993, 9007199254740994)


def field_oracles() -> dict[str, list[object]]:
    return {
        "distribution:known_state_count": [0, 1, 0, 2, 0, 0],
        "distribution:seeded_subject_count": [1, 1, 1, 2, 2, 2],
        "distribution:coverage_censored_count": [0] * 6,
        "distribution:share_among_seeded": [0, 1, 0, 1, 0, 0],
        "transitions:count": [2, 0, 0, 0],
        "transitions:share_of_modeled_transitions": [1, 0, 0, 0],
        "dwell:interval_count": [2, 2, 0],
        "dwell:completed_count": [0, 2, 0],
        "dwell:right_censored_count": [2, 0, 0],
        "dwell:coverage_censored_count": [0, 0, 0],
        "dwell:left_clipped_completed_count": [0, 0, 0],
        "dwell:mean_duration": [None, timedelta(seconds=5), None],
        "dwell:median_duration": [None, timedelta(seconds=5), None],
        "dwell:p90_duration": [None, timedelta(seconds=5), None],
        "intervals:state": ["open", "done", "open", "done"],
        "intervals:start": [
            START,
            START + timedelta(seconds=5),
            START + timedelta(seconds=2),
            START + timedelta(seconds=7),
        ],
        "intervals:end": [START + timedelta(seconds=5), END, START + timedelta(seconds=7), END],
        "intervals:observed_duration": [timedelta(seconds=s) for s in (5, 95, 5, 93)],
        "intervals:left_clipped": [False] * 4,
        "intervals:status": ["completed", "right_censored", "completed", "right_censored"],
        "violations:trigger": [],
        "violations:occurred_at": [],
        "violations:state_at_event": [],
        "violations:kind": [],
    }


def assert_field(label: str, result: _MaterializedRead, expected: list[object]) -> None:
    frame = result.to_pandas()
    assert len(frame) == len(expected)
    missing = [value is None for value in expected]
    assert frame.value.isna().tolist() == missing
    assert frame.value[~frame.value.isna()].tolist() == [v for v in expected if v is not None]
    if "cell_tag" in frame:
        assert frame.cell_tag.tolist() == [
            "undefined" if absent else "defined" for absent in missing
        ]
        assert frame.cell_reason.tolist() == [
            "empty_completed_set" if absent else None for absent in missing
        ]
    family = label.split(":")[0]
    if family == "intervals":
        assert frame.group.tolist() == [SUBJECTS[0]] * 2 + [SUBJECTS[1]] * 2
        assert frame.coord_0.tolist() == [1, 2, 1, 2]
    elif family == "distribution":
        assert frame.group.tolist() == [START] * 3 + [END] * 3
        assert frame.coord_0.tolist() == ["done", "open", "paid"] * 2
    elif family == "transitions":
        assert frame.group.tolist() == ["open", "open", "open", "paid"]
        assert frame.coord_0.tolist() == ["done", "open", "paid", "done"]
    elif family == "dwell":
        assert frame.group.tolist() == ["done", "open", "paid"]


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold", "development")
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
        for section, name in (
            ("fixed", "distribution"),
            ("fixed", "intervals"),
            ("fixed", "dwell"),
            ("supplement", "transitions"),
            ("supplement", "violations"),
        ):
            saved = obj(obj(manifest[section])["history_" + name])
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead) and snapshot(value) == saved
            restored[name] = value
        distribution = restored["distribution"]
        intervals = restored["intervals"]
        dwell = restored["dwell"]
        transitions = restored["transitions"]
        violations = restored["violations"]
        assert isinstance(distribution, mv.MaterializedStateDistributionResult)
        assert isinstance(intervals, mv.MaterializedStateIntervalResult)
        assert isinstance(dwell, mv.MaterializedDwellSummary)
        assert isinstance(transitions, mv.MaterializedTransitionSummary)
        assert isinstance(violations, mv.MaterializedViolationResult)
        fields: dict[str, Continuation] = {
            "distribution:known_state_count": distribution.known_state_count,
            "distribution:seeded_subject_count": distribution.seeded_subject_count,
            "distribution:coverage_censored_count": distribution.coverage_censored_count,
            "distribution:share_among_seeded": distribution.share_among_seeded,
            "transitions:count": transitions.count,
            "transitions:share_of_modeled_transitions": transitions.share_of_modeled_transitions,
            "dwell:interval_count": dwell.interval_count,
            "dwell:completed_count": dwell.completed_count,
            "dwell:right_censored_count": dwell.right_censored_count,
            "dwell:coverage_censored_count": dwell.coverage_censored_count,
            "dwell:left_clipped_completed_count": dwell.left_clipped_completed_count,
            "dwell:mean_duration": dwell.mean_duration,
            "dwell:median_duration": dwell.median_duration,
            "dwell:p90_duration": dwell.p90_duration,
            "intervals:state": intervals.state,
            "intervals:start": intervals.start,
            "intervals:end": intervals.end,
            "intervals:observed_duration": intervals.observed_duration,
            "intervals:left_clipped": intervals.left_clipped,
            "intervals:status": intervals.status,
            "violations:trigger": violations.trigger,
            "violations:occurred_at": violations.occurred_at,
            "violations:state_at_event": violations.state_at_event,
            "violations:kind": violations.kind,
        }
        operations: dict[str, Continuation] = dict(fields)
        operations.update(
            {
                "distribution:where": distribution.where(
                    distribution.known_state_count.value.gt(0)
                ),
                "transitions:where": transitions.where(transitions.count.value.gt(0)),
                "dwell:where": dwell.where(dwell.mean_duration.value.is_defined()),
                "intervals:where": intervals.where(intervals.status.value.eq("completed")),
                "violations:where": violations.where(violations.kind.value.eq("unmodeled")),
                "intervals:members": intervals.members(through=intervals.subjects()),
                "violations:members": violations.members(through=violations.subjects()),
            }
        )
        assert intervals.subjects() == intervals.subject_binding
        assert violations.subjects() == violations.subject_binding
        consumed: dict[str, Json] = {}
        for family, card_value in restored.items():
            actions = {action.call for action in card_value.contract().actions}
            expected_actions = {
                "relation." + name.split(":")[1] for name in fields if name.startswith(family + ":")
            }
            expected_actions.add("relation.where(predicate)")
            if family in ("intervals", "violations"):
                expected_actions.update(
                    ("relation.subjects()", "relation.members(through=through)")
                )
            assert actions == expected_actions
            consumed[family] = checked(sorted(actions))
        outputs: dict[str, Json] = {}
        oracles = field_oracles()
        for label, logical in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            if label in oracles:
                assert_field(label, result, oracles[label])
            elif label == "intervals:members":
                assert result.to_pandas().member.tolist() == list(SUBJECTS)
            elif label == "violations:members" or label == "violations:where":
                assert result.to_pandas().empty
            elif label == "distribution:where":
                assert result.to_pandas().known_state_count.tolist() == [1, 2]
            elif label == "transitions:where":
                assert result.to_pandas()["count"].tolist() == [2]
            elif label == "dwell:where":
                assert result.to_pandas().model_state.tolist() == ["open"]
            else:
                assert label == "intervals:where"
                assert result.to_pandas().state.tolist() == ["open", "open"]
            saved = compact(result)
            if phase == "cold":
                assert saved == obj(manifest["history_field_K"])[label]
            prior = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                repeated = logical.execute()
                assert repeated._dataset is not None
                assert repeated._dataset.artifact.artifact_ref == saved["artifact"]
            assert run_ids(session) == prior
            outputs[label] = saved
        assert len(outputs) == 31
        new_runs = len(run_ids(session) - before)
        if phase == "cold":
            assert new_runs == kernels.call_count == 0
        else:
            assert new_runs == kernels.call_count
            assert new_runs == 31 if phase == "fixed" else 0 <= new_runs <= 31
            manifest["history_field_K"] = outputs
            (root / "r94-domain.json").write_bytes(checked_bytes(manifest))
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "source_offline": True,
            "new_runs": new_runs,
            "kernels": kernels.call_count,
            "outputs": outputs,
            "consumed_K": consumed,
            "resources": 0,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(checked_bytes(run(Path(sys.argv[1]), sys.argv[2])))
