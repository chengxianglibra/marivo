"""Cold terminal reads feed an explicit pandas contribution recipe."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.attribution_workflows import allocation, basis
from tests.analysis.statistics.pandas_workflows import plain
from tests.shared_fixtures import terminal_runs
from tests.support.json import encode, obj, read


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-attribution.json")
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(session_id)
        before = len(terminal_runs(session))
        outputs: dict[str, mv.AnalysisFrame] = {}
        for name, saved in obj(state["outputs"]).items():
            item = obj(saved)
            identity = item["artifact"]
            assert isinstance(identity, str)
            value = session.persisted_frame(identity)
            assert snapshot(value) == item["snapshot"]
            outputs[name] = value
        current, baseline = basis(outputs["current"]), basis(outputs["baseline"])
        allocated = allocation(current, baseline, parents=())
        assert dict(zip(allocated.channel, allocated.contribution, strict=True)) == {
            "a": 2,
            "b": 1,
            "c": -5,
        }
        assert allocated.contribution.sum() == -2
        grouped = plain(outputs["groups"])
        assert dict(zip(grouped.key_1, grouped["reference.absolute.value"], strict=True)) == {
            "a": 2,
            "b": 1,
            "c": -5,
        }
        assert plain(outputs["difference"])["reference.absolute.value"].tolist() == [-2]
        assert len(terminal_runs(session)) == before
        assert all(value._derivation is None for value in outputs.values())
    output.write_bytes(encode({"phase": phase, "pid": os.getpid()}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
