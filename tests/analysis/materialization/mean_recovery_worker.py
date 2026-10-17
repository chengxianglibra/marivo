"""Source-free current-cell mean and explicit original-component finishing."""

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
from tests.support.json import encode, obj, read


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-mean.json")
    session_id = state["session"]
    assert isinstance(session_id, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(session_id, by="id")
        before = len(session.runs().items)
        results: dict[str, mv.Artifact] = {}
        for name, item in obj(state["outputs"]).items():
            saved = obj(item)
            artifact = saved["artifact"]
            assert isinstance(artifact, str)
            result = session.artifact(artifact)
            assert snapshot(result) == saved["snapshot"]
            results[name] = result
        groups = results["groups"].to_pandas()
        assert groups[("value", "value")].tolist() == [1.0, 100.0]
        assert groups[("value", "sum")].tolist() == [2, 100]
        assert groups[("value", "count")].tolist() == [2, 1]
        assert groups[("value", "value")].mean() == 50.5
        assert groups[("value", "sum")].sum() / groups[("value", "count")].sum() == 34.0
        assert results["original"].to_pandas()[("value", "value")].tolist() == [34.0]
        assert results["current"].to_pandas()[("value", "value")].tolist() == [50.5]
        assert len(session.runs().items) == before
    output.write_bytes(encode({"pid": os.getpid(), "phase": phase}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
