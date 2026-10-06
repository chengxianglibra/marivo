"""Public SQLite retention production and independent three-valued recovery."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from unittest.mock import patch

import duckdb
import ibis
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import (
    graph_local_execution,
    journey_execution,
    retention_execution,
)
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import CompiledRead, SourceBatchStream, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, encode, read
from tests.lifecycle_r75_fixtures import END, START
from tests.r94_domain_recovery_worker import forbidden, snapshot
from tests.retention_r78_fixtures import build_retention
from tests.shared_fixtures import run_ids


@runtime_checkable
class TraceConnection(Protocol):
    def set_trace_callback(self, callback: Callable[[str], None]) -> None: ...


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead: ...


def produce(
    root: Path, subject_kind: Literal["i", "s"], occurrence_kind: Literal["i", "s"]
) -> dict[str, Json]:
    build_retention(root, backend_name="sqlite", subject=subject_kind, occurrence=occurrence_kind)
    session = mv.session.get_or_create("r94-retention-states", report_timezone="UTC")
    anchors = session.anchors(
        ms.participant_role(event=ms.ref.event("commerce.started"), name="subject"),
        population=session.members(ms.ref.entity("commerce.subjects")),
        during=mv.time_scope(start=START, end=END),
        business_order=ms.ref.business_order("commerce.order"),
    )
    from datetime import timedelta

    claims = (
        mv.SourceOriginCompletenessDeclarationV1(
            inputs=(ms.ref.event("commerce.finished"),),
            source_origin_ref=ms.ref.datasource("warehouse"),
            complete_through=START + timedelta(seconds=10),
            rationale="All fixture return events through ten seconds are retained.",
        ),
    )
    owners: list[SourceSession] = []
    native_sql: list[str] = []
    batches = SourceSession.batches

    def traced(
        source: SourceSession, compiled: CompiledRead, *, chunk_size: int
    ) -> SourceBatchStream:
        connection = getattr(source._backend, "con", None)
        assert isinstance(connection, TraceConnection)
        connection.set_trace_callback(native_sql.append)
        if all(source is not owner for owner in owners):
            owners.append(source)
        return batches(source, compiled, chunk_size=chunk_size)

    with patch.object(SourceSession, "batches", traced):
        retained = anchors.retention(
            ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject"),
            within=mv.elapsed(mv.duration(seconds=10)),
            completeness=claims,
        ).execute()
    assert retained.to_pandas().value.tolist() == [True, None, False, None]
    frame = retained.to_pandas()
    key_columns = [
        name for name in frame.columns if name not in {"value", "cell_tag", "cell_reason"}
    ]
    assert len(key_columns) == 3
    subject_keys: list[int] | list[str] = (
        [9007199254740993, 9007199254740994] if subject_kind == "i" else ["sid0", "sid1"]
    )
    occurrence_keys: list[int] | list[str] = (
        [9007199254740993, 9007199254740995, 9007199254740996, 9007199254740997]
        if occurrence_kind == "i"
        else ["oid0", "oid2", "oid3", "oid4"]
    )
    assert list(frame[key_columns].itertuples(index=False, name=None)) == [
        (subject_keys[index // 2], "commerce.started", occurrence_keys[index]) for index in range(4)
    ]
    assert owners and all(source._closed for source in owners)
    submissions = [submission for source in owners for submission in source.submissions]
    assert submissions and all(item.sql in native_sql for item in submissions)
    manifest: dict[str, Json] = {
        "session": session.id,
        "subject_kind": subject_kind,
        "occurrence_kind": occurrence_kind,
        "source": retained.state.artifact_ref.ref,
        "original": snapshot(retained),
        "native_sql": list(native_sql),
        "source_closed": True,
        "source_business_submissions": len(submissions),
    }
    (root / "r94-retention-states.json").write_bytes(encode(manifest))
    return {"phase": "produce", "pid": os.getpid(), "manifest": manifest}


def recover(root: Path, phase: str) -> dict[str, Json]:
    state = read(root / "r94-retention-states.json")
    identity, reference = state["session"], state["source"]
    assert isinstance(identity, str) and isinstance(reference, str)
    assert not (root / "models").exists() and not (root / "source.sqlite").exists()
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(journey_execution, "execute", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(identity, by="id")
        retained = session.artifact(reference)
        assert isinstance(retained, mv.MaterializedRetentionResult)
        assert snapshot(retained) == state["original"]
        before = run_ids(session)
        outputs: dict[str, Json] = {}

        def save(
            name: str,
            operation: Continuation,
        ) -> _MaterializedRead:
            if phase == "cold":
                with (
                    patch.object(graph_local_execution, "execute_verified_fixed", forbidden),
                    patch.object(retention_execution, "execute", forbidden),
                ):
                    value = operation.execute()
            else:
                value = operation.execute()
            outputs[name] = snapshot(value)
            current = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == outputs[name]
            assert run_ids(session) == current
            return value

        parents: dict[
            str, mv.MaterializedRetentionResult | mv.MaterializedSubjectRetentionResult
        ] = {"instance": retained}
        for rule, expected in (("any", [True, None]), ("every", [None, False])):
            value = save(
                rule,
                retained.by_subject(rule=mv.any_anchor() if rule == "any" else mv.every_anchor()),
            )
            assert isinstance(value, mv.MaterializedSubjectRetentionResult)
            assert value.to_pandas().value.tolist() == expected
            parents[rule] = value
        counts = {"instance": (1, 1, 2), "any": (1, 0, 1), "every": (0, 1, 1)}
        refusals = 0
        for label, parent in parents.items():
            original = snapshot(parent)
            facts = dict(parent.contract()._facts)
            expected_facts = {
                "instance": ("4", "1", "1", "2", "[0.25,0.75]"),
                "any": ("2", "1", "0", "1", "[0.5,1]"),
                "every": ("2", "0", "1", "1", "[0,0.5]"),
            }[label]
            fact_names = (
                "omega_count",
                "known_true_count",
                "known_false_count",
                "unknown_count",
                "deterministic_bounds",
            )
            assert tuple(facts[name] for name in fact_names[:-1]) == expected_facts[:-1]
            assert json.loads(facts["deterministic_bounds"]) == json.loads(expected_facts[-1])
            views: dict[str, mv.LogicalBooleanRelation | mv.LogicalSelectedBooleanRelation] = {
                "true": parent.known_true(),
                "false": parent.known_false(),
                "unknown": parent.unknown(),
                "status": parent.status,
            }
            for name, operation in views.items():
                view = save(label + ":" + name, operation)
                assert isinstance(
                    view, (mv.MaterializedBooleanRelation, mv.MaterializedSelectedBooleanRelation)
                )
                view_facts = dict(view.contract()._facts)
                assert tuple(view_facts[name] for name in fact_names) == tuple(
                    facts[name] for name in fact_names
                )
                if name == "status":
                    continue
                assert (
                    len(view.to_pandas()) == counts[label][("true", "false", "unknown").index(name)]
                )
                count = save(label + ":" + name + ":count", view.summarize(mv.count()))
                assert count.to_pandas().value.tolist() == [len(view.to_pandas())]
                if name == "true":
                    image = save(label + ":members", view.members(through=view.subject_binding))
                    assert len(image.to_pandas()) == (0 if label == "every" else 1)
                else:
                    with pytest.raises(AnalysisError) as error:
                        view.members(through=view.subject_binding)
                    assert error.value.expected and error.value.received and error.value.repair
                    refusals += 1
            assert snapshot(parent) == original
        assert snapshot(retained) == state["original"] and refusals == 6
        if phase == "fixed":
            assert kernels.call_count == len(run_ids(session) - before) == len(outputs)
            state["outputs"] = outputs
            (root / "r94-retention-states.json").write_bytes(encode(state))
        else:
            assert (
                outputs == state["outputs"]
                and run_ids(session) == before
                and kernels.call_count == 0
            )
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "member_refusals": refusals,
        }


if __name__ == "__main__":
    root, phase, destination = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    raw_subject, raw_occurrence = sys.argv[4], sys.argv[5]
    assert raw_subject in ("i", "s") and raw_occurrence in ("i", "s")
    subject_kind: Literal["i", "s"] = "i" if raw_subject == "i" else "s"
    occurrence_kind: Literal["i", "s"] = "i" if raw_occurrence == "i" else "s"
    destination.write_bytes(
        encode(
            produce(root, subject_kind, occurrence_kind)
            if phase == "produce"
            else recover(root, phase)
        )
    )
