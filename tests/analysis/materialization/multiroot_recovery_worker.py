"""Explicit independent source components survive terminal cold reads."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.support.json import Json, encode, obj, read


def verify(outputs: Mapping[str, mv.Artifact]) -> dict[str, Json]:
    components = outputs["components"].to_pandas()
    assert components.iloc[:, 0].tolist() == [
        ("a", 9007199254740992),
        ("a", 9007199254740993),
        ("b", 9007199254740992),
        ("c", 9007199254740992),
    ]
    assert components[("value", "left")].tolist() == [30, 90, 0, 0]
    assert components[("value", "right")].tolist() == [5, 20, 4, 0]
    linear = outputs["linear"].to_pandas()
    ratio = outputs["ratio"].to_pandas()
    assert linear.iloc[:, 0].tolist() == ratio.iloc[:, 0].tolist() == components.iloc[:, 0].tolist()
    assert linear[("value", "value")].tolist() == [35, 110, 4, 0]
    values = ratio[("value", "value")].tolist()
    assert values[:3] == [6, 4.5, 0] and pd.isna(values[3])
    assert outputs["auto_read"].to_pandas().equals(outputs["explicit_read"].to_pandas())
    assert outputs["auto_read"].to_pandas()[("value", "value")].tolist() == ["a"] * 4
    left = int(components[("value", "left")].sum())
    right = int(components[("value", "right")].sum())
    assert left == 120 and right == 29
    assert outputs["linear_total"].to_pandas()[("value", "value")].tolist() == [149]
    assert math.isclose(outputs["ratio_total"].to_pandas()[("value", "value")].iloc[0], 120 / 29)
    return {"left": left, "right": right, "sum": left + right, "ratio": left / right}


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-multiroot.json")
    identity = state["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(identity, by="id")
        before = len(session.runs().items)
        outputs: dict[str, mv.Artifact] = {}
        for name, saved in obj(state["outputs"]).items():
            item = obj(saved)
            artifact = item["artifact"]
            assert isinstance(artifact, str)
            value = session.artifact(artifact)
            assert snapshot(value) == item["snapshot"]
            outputs[name] = value
        derived = verify(outputs)
        assert derived == obj(state["derived"])
        assert len(session.runs().items) == before
    output.write_bytes(encode({"pid": os.getpid(), "phase": phase, "derived": derived}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
