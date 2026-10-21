"""Exact typed distribution exports survive independent terminal reads."""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.shared_fixtures import terminal_runs
from tests.support.json import encode, obj, read


def check_original(
    value: mv.AnalysisFrame, physical: str, kind: str, *, exported: pd.DataFrame | None = None
) -> None:
    exported = value.to_pandas() if exported is None else exported
    frame = exported.set_index(ordinary_columns(exported)[0])
    assert frame.index.tolist() == ["A", "B", "C", "D"]
    values = frame[("value", "value")]
    if physical == "decimal":
        assert kind == "quantile"
        assert values.iloc[:3].tolist() == [Decimal("1.01"), Decimal("1.02"), Decimal("1.04")]
        assert all(isinstance(item, Decimal) for item in values.iloc[:3])
    elif "distinct" in kind:
        assert values.tolist() == [1, 1, 1, 0]
        assert not values.isna().any()
    else:
        assert values.iloc[:3].tolist() == [450, 150, 400]
    if "distinct" not in kind:
        assert pd.isna(values.iloc[3])
    assert not hasattr(value, "rollup")


def run(root: Path, phase: str, output: Path) -> None:
    os.chdir(root)
    state = read(root / "r94-distribution-carrier.json")
    session_id, physical = state["session"], state["physical"]
    assert isinstance(session_id, str) and isinstance(physical, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__enter__", forbidden),
        patch.object(SourceSession, "execute", forbidden),
    ):
        session = mv.session.resume(session_id)
        before = len(terminal_runs(session))
        for name, item in obj(state["originals"]).items():
            saved = obj(item)
            artifact = saved["artifact"]
            assert isinstance(artifact, str)
            result = session.persisted_frame(artifact)
            exported = result.to_pandas()
            assert snapshot(result, frame=exported) == saved["snapshot"]
            check_original(result, physical, name, exported=exported)
        assert len(terminal_runs(session)) == before
    output.write_bytes(encode({"pid": os.getpid(), "phase": phase}))


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
