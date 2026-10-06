"""Continue retained native Journey fields and retention Boolean protocols offline."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
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

SUBJECTS = [9007199254740993, 9007199254740994]
START = datetime(2026, 2, 1)


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
        for section, label in (
            ("supplement", "journey_completed"),
            ("fixed", "retention_any"),
            ("fixed", "retention_every"),
            *(
                ("supplement", "journey_" + name)
                for name in (
                    "status",
                    "started_at",
                    "completed_at",
                    "duration",
                    "observed_duration",
                    "followup_until",
                )
            ),
            *(
                ("supplement", "retention_" + name)
                for name in ("status", "true", "false", "unknown")
            ),
        ):
            saved = obj(obj(manifest[section])[label])
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, _MaterializedRead)
            assert snapshot(value) == saved
            restored[label] = value
        completed = restored["journey_completed"]
        assert isinstance(completed, mv.MaterializedCompletedJourneys)
        role = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
        assert completed.subjects(role) == completed.subject_binding
        operations: dict[str, Continuation] = {
            "completed:status": completed.status,
            "completed:started_at": completed.started_at,
            "completed:completed_at": completed.completed_at,
            "completed:duration": completed.duration,
            "completed:observed_duration": completed.observed_duration,
            "completed:followup_until": completed.followup_until,
            "completed:completed": completed.completed(),
        }
        consumed: dict[str, Json] = {}
        consumed["completed"] = checked(
            sorted(action.call for action in completed.contract().actions)
        )
        assert {action.call for action in completed.contract().actions} == {
            "relation.status",
            "relation.started_at",
            "relation.completed_at",
            "relation.duration",
            "relation.observed_duration",
            "relation.followup_until",
            "relation.completed()",
            "relation.subjects(role)",
        }
        group_key = ms.ref.entity("commerce.subjects")
        for name in (
            "status",
            "started_at",
            "completed_at",
            "duration",
            "observed_duration",
            "followup_until",
        ):
            field = restored["journey_" + name]
            assert isinstance(
                field,
                (
                    mv.MaterializedCategoryRelation,
                    mv.MaterializedTemporalRelation,
                    mv.MaterializedNumericRelation,
                ),
            )
            actions = {action.call for action in field.contract().actions}
            expected = {
                "relation.where(predicate)",
                "relation.members()",
                "relation.summarize(method)",
            }
            if not isinstance(field, mv.MaterializedNumericRelation):
                expected.add("relation.group_by(*keys)")
            assert actions == expected
            consumed["journey_" + name] = checked(sorted(actions))
            selected = field.where(field.value.is_defined())
            operations[name + ":where"] = selected
            operations[name + ":where_twice"] = selected.where(selected.value.is_defined())
            operations[name + ":members"] = field.members(through=field.subject_binding)
            operations[name + ":count"] = field.summarize(mv.count())
            operations[name + ":count_defined"] = field.summarize(mv.count_defined())
            if isinstance(field, mv.MaterializedNumericRelation):
                operations[name + ":mean"] = field.summarize(mv.mean())
            elif isinstance(field, mv.MaterializedCategoryRelation):
                operations[name + ":group"] = field.group_by(group_key).summarize(mv.count())
            else:
                operations[name + ":group"] = field.group_by(group_key).summarize(mv.count())
        status = restored["journey_status"]
        assert isinstance(status, mv.MaterializedCategoryRelation)
        empty_status = status.where(status.value.eq("not_entered"))
        operations["status:empty"] = empty_status
        operations["status:empty:count"] = empty_status.summarize(mv.count())
        operations["status:empty:count_defined"] = empty_status.summarize(mv.count_defined())
        for rule in ("any", "every"):
            retained = restored["retention_" + rule]
            assert isinstance(retained, mv.MaterializedSubjectRetentionResult)
            actions = {action.call for action in retained.contract().actions}
            assert actions == {
                "relation.status",
                "relation.known_true()",
                "relation.known_false()",
                "relation.unknown()",
            }
            consumed["retention_" + rule] = checked(sorted(actions))
            operations[rule + ":status"] = retained.status
            operations[rule + ":true"] = retained.known_true()
            operations[rule + ":false"] = retained.known_false()
            operations[rule + ":unknown"] = retained.unknown()
            operations[rule + ":members"] = retained.known_true().members()
        for name in ("status", "true", "false", "unknown"):
            field = restored["retention_" + name]
            assert isinstance(
                field, (mv.MaterializedBooleanRelation, mv.MaterializedSelectedBooleanRelation)
            )
            actions = {action.call for action in field.contract().actions}
            expected = {"relation.where(predicate)"}
            if name == "true":
                expected.add("relation.members()")
                operations["instance:true:members"] = field.members(through=field.subject_binding)
            assert actions == expected
            consumed["retention_" + name] = checked(sorted(actions))
            operations["instance:" + name + ":where"] = field.where(field.value.eq(True))
        outputs: dict[str, Json] = {}
        for label, logical in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            frame = result.to_pandas()
            if label.endswith(":members"):
                assert frame.member.tolist() == SUBJECTS
            elif label.startswith(("any:", "every:", "instance:")):
                empty = ":false" in label or ":unknown" in label
                assert frame.value.tolist() == ([] if empty else [True, True])
                assert isinstance(
                    result, (mv.MaterializedBooleanRelation, mv.MaterializedSelectedBooleanRelation)
                )
                retained_dataset = result._dataset
                assert retained_dataset is not None
                recovered = session.artifact(retained_dataset.artifact.artifact_ref)
                assert type(recovered) is type(result)
                assert isinstance(
                    recovered,
                    (mv.MaterializedBooleanRelation, mv.MaterializedSelectedBooleanRelation),
                )
                recovered.value.eq(True)
                assert compact(recovered) == compact(result)
                if not label.startswith("instance:"):
                    assert dict(result.contract()._facts)["omega_count"] == "2"
            elif label.endswith((":count", ":count_defined")):
                assert frame.value.tolist() == ([0] if ":empty:" in label else [2])
            elif label.endswith(":empty"):
                assert frame.empty
            elif label.endswith(":mean"):
                assert frame.value.tolist() == [timedelta(seconds=5)]
            elif label.endswith(":group"):
                assert frame.value.tolist() == [1, 1]
            elif label == "completed:completed":
                assert frame.group.tolist() == SUBJECTS
                assert frame.coord_0.tolist() == ["commerce.started"] * 2
                assert frame.coord_1.tolist() == [9007199254740993, 9007199254740995]
            else:
                name = (
                    label.split(":")[1] if label.startswith("completed:") else label.split(":")[0]
                )
                expected_values: dict[str, list[object]] = {
                    "status": ["complete"] * 2,
                    "started_at": [START, START + timedelta(seconds=2)],
                    "completed_at": [START + timedelta(seconds=5), START + timedelta(seconds=7)],
                    "followup_until": [START + timedelta(seconds=5), START + timedelta(seconds=7)],
                    "duration": [timedelta(seconds=5)] * 2,
                    "observed_duration": [timedelta(seconds=5)] * 2,
                }
                assert "value" in frame, (label, list(frame.columns))
                assert frame.value.tolist() == expected_values[name]
                assert frame.group.tolist() == SUBJECTS
                assert frame.coord_0.tolist() == ["commerce.started"] * 2
                assert frame.coord_1.tolist() == [9007199254740993, 9007199254740995]
                assert frame.cell_tag.tolist() == ["defined"] * 2
            saved = compact(result)
            if phase == "cold":
                assert saved == obj(manifest["journey_retention_K"])[label]
            prior = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                repeated = logical.execute()
                assert repeated._dataset is not None
                assert repeated._dataset.artifact.artifact_ref == saved["artifact"]
            assert run_ids(session) == prior
            outputs[label] = saved
        assert len(outputs) == 61
        new_runs = len(run_ids(session) - before)
        if phase == "cold":
            assert new_runs == kernels.call_count == 0
        else:
            assert new_runs == kernels.call_count
            if phase == "fixed":
                assert new_runs == 61
            manifest["journey_retention_K"] = outputs
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
