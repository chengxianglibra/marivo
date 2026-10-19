"""Independent terminal producer reads with all live dependencies forbidden."""

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
from tests.support.json import arr, encode, obj, read


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-state.json")
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
        for artifact, saved in zip(
            arr(state["inputs"]), arr(state["input_snapshots"]), strict=True
        ):
            assert isinstance(artifact, str)
            result = session.persisted_frame(artifact)
            frame = result.to_pandas()
            assert snapshot(result, frame=frame) == obj(saved)
            assert frame[("value", "value")].tolist() == [2, 0, 4]
            assert frame[("value", "value")].sum() == 6
            assert frame.loc[frame[("value", "value")] > 0].shape[0] == 2
            result.show(n=1)
        assert len(session.runs().items) == before
        assert session._runtime.store.resources(session.id) == ()
    output.write_bytes(encode({"pid": os.getpid(), "phase": phase}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
