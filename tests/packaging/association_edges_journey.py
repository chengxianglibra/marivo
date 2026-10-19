"""Pandas complete-pair association with independent facts and terminal inputs."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.materialization.domain_recovery_worker import snapshot
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.packaging.graph_journeys import (
    NAMES,
    _association,
    _create,
    _frame,
    _offline,
    _remove_sources,
)
from tests.shared_fixtures import DslCase, analysis_dsl_rows, export_dsl_parquet_models, run_ids
from tests.support.json import Json, checked, encode, read


def _verify(left: mv.AnalysisFrame, right: mv.AnalysisFrame) -> dict[str, Json]:
    lhs, rhs = _frame(left), _frame(right)
    assert lhs.value.notna().tolist() == [True] * 3 + [False]
    result = _association(lhs, rhs)
    assert result == {"coefficient": -0.5, "pairs": 3, "null_pairs": 1}
    assert _association(lhs, rhs) == result
    with pytest.raises(ValueError, match="different complete composite key sets"):
        _association(lhs, rhs.loc[rhs.value > 1].reset_index(drop=True))
    return result


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("produce", "fixed", "cold")
    root = root.resolve()
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state_path = root / "null-pairs.json"
    if phase == "produce":
        session = _create(root, "j4", "table")
        with duckdb.connect(str(root / "warehouse.duckdb")) as connection:
            connection.execute("UPDATE reading SET energy=NULL WHERE device_id='D'")
        catalog = ms.load(workspace_dir=root)
        export_dsl_parquet_models(
            DslCase("j4", NAMES, root, root / "warehouse.duckdb", catalog, session), root
        )
        ms.load(workspace_dir=root)
        population = session.members(ms.ref.entity("operations.device"))
        window = session.time_scope(start="2026-08-01", end="2026-09-01")
        amount = session.catalog.fields.get("operations.reading.energy")
        time = session.catalog.fields.get("operations.reading.recorded_at")
        left = session.observe(
            {"value": amount.sum(time=time, empty="null")},
            during=window,
            by=population,
            members=population,
        ).persist()
        right = session.observe(
            {"value": session.catalog.metrics.get("operations.reading_count")},
            during=window,
            by=population,
            members=population,
        ).persist()
        before = publication_counts(session)
        result = _verify(left, right)
        assert publication_counts(session) == before
        state: dict[str, Json] = {
            "session": session.id,
            "left": left.artifact.id,
            "right": right.artifact.id,
            "left_snapshot": snapshot(left),
            "right_snapshot": snapshot(right),
            "result": result,
            "raw_facts": checked(
                [
                    [*row[:-1], None if row[1] == "D" else row[-1]]
                    for row in analysis_dsl_rows("j4").orders
                ]
            ),
            "oracle": {"coefficient": -0.5, "complete_pairs": 3, "null_pairs": 1},
        }
        state_path.write_bytes(encode(state))
        _remove_sources(root)
    else:
        state = read(state_path)
        identity = state["session"]
        assert isinstance(identity, str)
        with _offline(root):
            session = mv.session.resume(identity, by="id")
            before_runs = run_ids(session)
            before = publication_counts(session)
            left_ref, right_ref = state["left"], state["right"]
            assert isinstance(left_ref, str) and isinstance(right_ref, str)
            left, right = session.persisted_frame(left_ref), session.persisted_frame(right_ref)
            assert (
                snapshot(left) == state["left_snapshot"]
                and snapshot(right) == state["right_snapshot"]
            )
            result = _verify(left, right)
            assert result == state["result"]
            assert run_ids(session) == before_runs and publication_counts(session) == before
    assert session._runtime.store.resources(session.id) == ()
    return {
        "result": result,
        "oracle": state["oracle"],
        "wrong_domain": "refused",
        "left_snapshot": state["left_snapshot"],
        "right_snapshot": state["right_snapshot"],
    }
