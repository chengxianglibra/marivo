"""Offline native domain continuations and exact fresh-process recovery."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import NoReturn, TypeAlias
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import (
    graph_local_execution,
    history_execution,
    journey_execution,
)
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json, checked, digest, obj, read
from tests.lifecycle_r75_fixtures import END, START

Continuation: TypeAlias = (
    mv.LogicalStateDistributionResult
    | mv.LogicalStateIntervalResult
    | mv.LogicalDwellSummary
    | mv.LogicalSubjectRetentionResult
    | mv.LogicalRankingResult
)


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Offline native domain accessed source, Semantic or event replay")


def snapshot(result: _MaterializedRead) -> dict[str, Json]:
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    verified = result._dataset.verified()
    evidence = result.evidence_digest()
    return obj(
        checked(
            {
                "artifact": result._dataset.artifact.artifact_ref,
                "descriptor_sha256": digest(encode(descriptor, DESCRIPTOR).encode()),
                "schema": descriptor.realized_schema,
                "execution_key_digest": descriptor.execution_key_digest,
                "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
                "rows": json.loads(result.to_pandas().to_json(orient="table", index=False)),
                "parts": [part.role for part in verified.parts],
                "contract": json.loads(json.dumps(asdict(result.contract()), default=str)),
                "evidence": json.loads(json.dumps(asdict(evidence), default=str)),
                "findings": json.loads(json.dumps(asdict(result.findings(limit=100)), default=str)),
            }
        )
    )


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
        before = len(session.runs().items)
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
        try:
            anchors.observe(
                ms.ref.metric("commerce.score_sum"),
                within=mv.elapsed(mv.duration(seconds=10)),
                via=ms.ref.relationship("commerce.participant"),
            )
        except AnalysisError as error:
            assert "fixed starts" in str(error)
        else:
            raise AssertionError("Fixed Anchor admitted a live Metric")
        assert len(session.runs().items) == before
        steps = (
            mv.step(
                participant=ms.participant_role(
                    event=ms.ref.event("commerce.started"), name="subject"
                ),
                key="start",
            ),
            mv.step(
                participant=ms.participant_role(
                    event=ms.ref.event("commerce.finished"), name="subject"
                ),
                key="finish",
            ),
        )
        duration = journey.time_to_event(from_step=steps[0], to_step=steps[1])
        operations: dict[str, Continuation] = {
            "history_distribution": history.distribution(at=(START, END)),
            "history_intervals": history.intervals(),
            "history_dwell": history.dwell(),
            "retention_any": retention.by_subject(rule=mv.any_anchor()),
            "retention_every": retention.by_subject(rule=mv.every_anchor()),
            "observation_rank": observed.rank(order="descending", ties="ordinal"),
            "journey_completed_rank": duration.completed().observed_duration.rank(
                order="descending", ties="ordinal"
            ),
        }
        outputs: dict[str, Json] = {}
        for name, logical in operations.items():
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = logical.execute()
            else:
                result = logical.execute()
            if isinstance(result, mv.MaterializedSubjectRetentionResult):
                assert result.to_pandas().value.tolist() == [True, True]
                assert dict(result.contract()._facts)["omega_count"] == "2"
            elif isinstance(result, mv.MaterializedRankingResult):
                assert result.ranks.to_pandas().value.tolist() == [1, 2]
                assert result.values.to_pandas().value.tolist() == (
                    [2, 2] if name == "observation_rank" else [timedelta(seconds=5)] * 2
                )
            elif isinstance(result, mv.MaterializedStateDistributionResult):
                assert result._dataset is not None
                verified = result._dataset.verified()
                assert verified.primary is not None
                keys = verified.contract.key_fields
                actual = {
                    tuple(row[key] for key in keys): (
                        row["known_state_count"],
                        row["seeded_subject_count"],
                        row["coverage_censored_count"],
                        row["share_among_seeded"],
                    )
                    for row in verified.primary.to_pylist()
                }
                assert actual == {
                    (at, state): (
                        int(state == ("open" if at == START else "done")) * seeded,
                        seeded,
                        0,
                        float(state == ("open" if at == START else "done")),
                    )
                    for at, seeded in ((START, 1), (END, 2))
                    for state in ("open", "paid", "done")
                }
            elif isinstance(result, mv.MaterializedStateIntervalResult):
                frame = result.to_pandas()
                assert sorted(frame["observed_duration"].tolist()) == [
                    timedelta(seconds=5),
                    timedelta(seconds=5),
                    timedelta(seconds=93),
                    timedelta(seconds=95),
                ]
            elif isinstance(result, mv.MaterializedDwellSummary):
                assert result._dataset is not None
                verified = result._dataset.verified()
                assert verified.primary is not None
                fields = (
                    "interval_count",
                    "completed_count",
                    "right_censored_count",
                    "coverage_censored_count",
                    "left_clipped_completed_count",
                    "mean_duration",
                    "median_duration",
                    "p90_duration",
                )
                actual = {
                    tuple(row[key] for key in verified.contract.key_fields): tuple(
                        row[field] for field in fields
                    )
                    for row in verified.primary.to_pylist()
                }
                assert actual == {
                    ("open",): (2, 2, 0, 0, 0, *([timedelta(seconds=5)] * 3)),
                    ("paid",): (0, 0, 0, 0, 0, None, None, None),
                    ("done",): (2, 0, 2, 0, 0, None, None, None),
                }
            saved = snapshot(result)
            if phase == "cold":
                assert saved == obj(manifest["fixed"])[name]
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(logical.execute()) == saved
            outputs[name] = saved
        if phase == "fixed":
            assert kernels.call_count > 0
            assert len(session.runs().items) == before + len(operations)
            manifest["fixed"] = outputs
            (root / "r94-domain.json").write_bytes(checked_bytes(manifest))
        else:
            assert kernels.call_count == 0
            assert len(session.runs().items) == before
        assert session._runtime.store.resources(session.id) == ()
        return {
            "phase": phase,
            "pid": os.getpid(),
            "new_runs": len(session.runs().items) - before,
            "kernels": kernels.call_count,
            "source_offline": True,
            "outputs": outputs,
            "resources": 0,
        }


def checked_bytes(value: dict[str, Json]) -> bytes:
    return json.dumps(value, sort_keys=True).encode()


if __name__ == "__main__":
    result = run(Path(sys.argv[1]), sys.argv[2])
    Path(sys.argv[3]).write_bytes(checked_bytes(result))
