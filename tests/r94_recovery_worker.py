"""Fresh-process public continuations with source and Semantic access forbidden."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR, encode
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, checked, digest, obj, read


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("R9.4 retained execution accessed a source or current Semantic")


def snapshot(result: mv.MaterializedNumericRelation | mv.MaterializedRatioRelation) -> Json:
    assert result._dataset is not None
    verified = result._dataset.verified()
    evidence = result.evidence_digest()
    descriptor = result._dataset.artifact.descriptor
    return checked(
        {
            "artifact": result.state.artifact_ref.ref,
            "run": result._dataset.artifact.producing_run_ref,
            "descriptor_sha256": digest(encode(descriptor, DESCRIPTOR).encode()),
            "execution_key_digest": descriptor.execution_key_digest,
            "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
            "schema": descriptor.realized_schema,
            "frame": json.loads(result.to_pandas().to_json(orient="split")),
            "parts": [part.role for part in verified.parts],
            "required_parts": list(result.contract().required_parts),
            "evidence": {
                "evidence_digest": evidence.evidence_digest,
                "finding_set_digest": evidence.finding_set_digest,
                "finding_count": evidence.finding_count,
            },
            "contract_facts": dict(result.contract()._facts),
        }
    )


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-state.json")
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernel,
    ):
        session = mv.session.resume(session_id, by="id")
        before = len(session.runs().items)
        if phase == "cold":
            prior = obj(state["fixed"])
            artifact = prior["artifact"]
            assert isinstance(artifact, str)
            restored = session.artifact(artifact)
            assert isinstance(restored, mv.MaterializedNumericRelation)
            assert snapshot(restored) == prior
            first = arr(state["inputs"])[0]
            assert isinstance(first, str)
            original = session.artifact(first)
            assert isinstance(original, mv.MaterializedNumericRelation)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(original.ratio(original).execute()) == prior
            assert len(session.runs().items) == before
        reference = arr(state["inputs"])[0 if phase == "fixed" else 1]
        assert isinstance(reference, str)
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert snapshot(value) == arr(state["input_snapshots"])[0 if phase == "fixed" else 1]
        logical = value.ratio(value)
        result = logical.execute()
        assert isinstance(result, mv.MaterializedNumericRelation)
        frame = result.to_pandas().set_index(["member", "coord_0", "coord_1"])
        assert frame.cell_tag.to_dict() == {
            ("a", 9007199254740992, 1): "defined",
            ("a", 9007199254740993, 2): "undefined",
            ("b", 9007199254740993, 1): "defined",
        }
        assert frame.loc[frame.cell_tag == "defined", "value"].tolist() == [1, 1]
        assert frame.loc[frame.cell_tag == "undefined", "cell_reason"].tolist() == [
            "zero_denominator"
        ]
        assert kernel.call_count > 0
        assert len(session.runs().items) == before + 1
        saved = snapshot(result)
        with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
            assert snapshot(logical.execute()) == saved
        assert len(session.runs().items) == before + 1
        assert session._runtime.store.resources(session.id) == ()
        state[phase] = saved
        (root / "r94-state.json").write_text(json.dumps(state, sort_keys=True))
        return {
            "phase": phase,
            "pid": os.getpid(),
            "new_runs": 1,
            "kernel_calls": kernel.call_count,
            "exact_hit_new_runs": 0,
            "source_semantic_duckdb_forbidden": True,
            "result": saved,
            "resources": 0,
        }


if __name__ == "__main__":
    report = run(Path(sys.argv[1]), sys.argv[2])
    Path(sys.argv[3]).write_text(json.dumps(report, sort_keys=True))
