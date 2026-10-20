"""Reopen complete persisted Frame facts and run explicit pandas allocation offline."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.attribution_workflows import Method, allocation, basis
from tests.support.json import Json, arr, encode, obj, read


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-attribution-carrier.json")
    session_id = state["session"]
    assert isinstance(session_id, str)
    methods: tuple[Method, ...] = ("sum", "count", "linear", "mean", "weighted", "ratio")
    top_k = state["top_k"]
    assert top_k is None or isinstance(top_k, int)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(session_id)
        before = len(session.runs().items)
        inputs: list[mv.AnalysisFrame] = []
        for saved in arr(state["inputs"]):
            item = obj(saved)
            artifact = item["artifact"]
            assert isinstance(artifact, str)
            value = session.persisted_frame(artifact)
            assert snapshot(value) == item["snapshot"]
            inputs.append(value)
        assert len(inputs) == 2
        current, baseline = (basis(value) for value in inputs)
        outputs: dict[str, Json] = {}
        for method in methods:
            if method not in arr(state["methods"]):
                continue
            left, right = current, baseline
            if method == "ratio":
                # This fixture's original ratio is sum(amount)/sum(amount).
                left, right = (frame.assign(weight=frame.amount) for frame in (left, right))
            result = allocation(left, right, method=method, top_k=top_k)
            assert result.current.shape == result.baseline.shape == result.contribution.shape
            assert len(result) > 0
            pd.testing.assert_frame_equal(
                result, allocation(left.copy(), right.copy(), method=method, top_k=top_k)
            )
            outputs[method] = result.to_json(orient="split", date_format="iso", double_precision=15)
        assert outputs == obj(state["recipe_outputs"])
        assert len(session.runs().items) == before
        assert all(
            not hasattr(value, "attribute") and not hasattr(value, "drill") for value in inputs
        )
    output.write_bytes(encode({"pid": os.getpid(), "phase": phase, "outputs": outputs}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
