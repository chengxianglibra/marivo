"""Independent source, fixed continuation and cold interpretation receipts."""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from typing import TypeAlias
from unittest.mock import patch

import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import statistical_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.lifecycle.history_public_recovery_worker import (
    CHECKPOINTS,
    END,
    assert_distribution,
    author,
    replay,
)
from tests.analysis.statistics.journeys import create, inputs
from tests.support.json import Json, checked, encode, obj, read

Result: TypeAlias = _MaterializedRead | mv.MaterializedTable
PAIRING_KEY = "sales.order.order_id (identity), sales.order.member_seq (identity)"


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("interpretation recovery accessed a source or current Semantic")


def snapshot(result: Result) -> dict[str, Json]:
    output = io.StringIO()
    with redirect_stdout(output):
        result.show(n=0, max_output_bytes=None)
    facts = dict(result.contract()._facts) if isinstance(result, _MaterializedRead) else {}
    bounded = io.StringIO()
    with redirect_stdout(bounded):
        result.show(n=2, max_output_bytes=8192)
    assert len(bounded.getvalue().encode()) <= 8192
    return obj(
        checked(
            json.loads(
                json.dumps(
                    {
                        "facts": facts,
                        "card": output.getvalue(),
                        "rows": result.to_pandas().to_dict("records"),
                    },
                    default=str,
                )
            )
        )
    )


def reference(result: Result) -> str:
    return (
        result.evidence_digest().artifact_ref.ref
        if isinstance(result, _MaterializedRead)
        else result.artifact_ref.ref
    )


def inspect_results(results: dict[str, Result]) -> dict[str, Json]:
    saved: dict[str, Json] = {name: snapshot(result) for name, result in results.items()}
    for name in ("association", "coefficient", "selected"):
        assert obj(obj(saved[name])["facts"])["pairing_key"] == PAIRING_KEY
        assert PAIRING_KEY in str(obj(saved[name])["card"])
    assert PAIRING_KEY in str(obj(saved["association_table"])["card"])
    assert "[coefficient, selected].pairing_key" in str(obj(saved["association_table"])["card"])
    distribution = obj(obj(saved["distribution"])["facts"])
    assert distribution["row_grain"] == "checkpoint, model_state, commerce.profiles.region"
    assert "do not sum across states" in str(distribution["count_scope"])
    assert "alternate states of seeded Subjects" in str(distribution["zero_state_cells"])
    assert "[seeded, censored].count_scope" in str(obj(saved["distribution_table"])["card"])
    wide_card = str(obj(saved["wide_table"])["card"])
    assert wide_card.count("do not sum across states") == 1
    assert "[" + ", ".join(f"count_{i}" for i in range(16)) + "].count_scope" in wide_card
    mean = obj(obj(saved["mean"])["facts"])
    assert mean["duration_unit"] == "us"
    assert mean["duration_seconds"] == "seconds = ticks / 1000000"
    assert obj(obj(saved["dwell"])["facts"])["mean_duration.duration_unit"] == "us"
    assert obj(obj(saved["dwell"])["facts"])["row_grain"] == "model_state"
    assert "mean.duration_unit: us" in str(obj(saved["duration_table"])["card"])
    dwell = results["dwell"]
    assert isinstance(dwell, mv.MaterializedDwellSummary)
    logical = dwell.where(dwell.completed_count.value.eq(1))
    expected_units = {
        name: value for name, value in dwell.contract()._facts if ".duration_" in name
    }
    assert expected_units == {
        name: value for name, value in logical.contract()._facts if ".duration_" in name
    }
    output = io.StringIO()
    with redirect_stdout(output):
        logical.contract().show()
    assert "seconds = ticks / 1000000" in output.getvalue()
    return saved


def run(root: Path, phase: str) -> dict[str, Json]:
    os.environ["MARIVO_TELEMETRY"] = "off"
    state_path = root / "interpretation.json"
    if phase == "produce":
        root.mkdir(parents=True, exist_ok=True)
        history_root = root / "history"
        author(history_root)
        os.environ["MARIVO_PROJECT_ROOT"] = str(history_root)
        ms.load(workspace_dir=history_root)
        history_session = mv.session.get_or_create("interpretation", report_timezone="UTC")
        logical = replay(history_session, "prefix")
        distribution = logical.distribution(
            at=CHECKPOINTS, axes=(ms.ref.dimension("commerce.profiles.region"),)
        ).execute()
        assert_distribution(distribution, "prefix")
        frame = distribution.to_pandas()
        last = frame.loc[frame["checkpoint"] == END]
        assert last["coverage_censored_count"].sum() == 9
        assert last.drop_duplicates("axis_0")["coverage_censored_count"].sum() == 3
        dwell = replay(history_session, "complete").dwell().execute()
        results: dict[str, Result] = {
            "distribution": distribution,
            "dwell": dwell,
            "mean": dwell.mean_duration.execute(),
            "distribution_table": mv.table(
                seeded=distribution.seeded_subject_count,
                censored=distribution.coverage_censored_count,
            ).execute(),
            "duration_table": mv.table(mean=dwell.mean_duration).execute(),
            "wide_table": mv.table(
                **{f"count_{i}": distribution.seeded_subject_count for i in range(16)}
            ).execute(),
        }
        history_ids: dict[str, Json] = {name: reference(result) for name, result in results.items()}
        statistics_root = root / "statistics"
        os.environ["MARIVO_PROJECT_ROOT"] = str(statistics_root)
        statistics_session = create(statistics_root, "parquet")
        a, b, _ = inputs(statistics_session)
        left, right = a.execute(), b.execute()
        association = a.correlate(b, method="pearson").execute()
        assert association.coefficient.to_pandas()["value"].tolist() == [1.0]
        results.update(
            association=association,
            coefficient=association.coefficient,
            selected=association.selected,
            association_table=mv.table(
                coefficient=association.coefficient, selected=association.selected
            ).execute(),
        )
        statistics_ids: dict[str, Json] = {
            name: reference(result) for name, result in results.items() if name not in history_ids
        }
        before = (history_session.runs().items, statistics_session.runs().items)
        saved = inspect_results(results)
        assert before == (history_session.runs().items, statistics_session.runs().items)
        state_path.write_bytes(
            encode(
                {
                    "history_session": history_session.id,
                    "statistics_session": statistics_session.id,
                    "history_ids": history_ids,
                    "statistics_ids": statistics_ids,
                    "inputs": [reference(left), reference(right)],
                    "snapshots": saved,
                }
            )
        )
    else:
        state = read(state_path)
        results = {}
        sessions: dict[str, mv.Session] = {}
        with (
            patch.object(SourceSession, "__enter__", forbidden),
            patch.object(ms, "load", forbidden),
            patch.object(SemanticProject, "load", forbidden),
        ):
            for family in ("history", "statistics"):
                os.environ["MARIVO_PROJECT_ROOT"] = str(root / family)
                session_id = state[family + "_session"]
                assert isinstance(session_id, str)
                session = mv.session.resume(session_id, by="id")
                sessions[family] = session
                for name, artifact_id in obj(state[family + "_ids"]).items():
                    if name in ("coefficient", "selected"):
                        continue
                    assert isinstance(artifact_id, str)
                    value = session.artifact(artifact_id)
                    assert isinstance(value, (_MaterializedRead, mv.MaterializedTable))
                    results[name] = value
            restored_association = results["association"]
            assert isinstance(restored_association, mv.MaterializedAssociationResult)
            results["coefficient"] = restored_association.coefficient
            results["selected"] = restored_association.selected
            recovered_runs = tuple(session.runs().items for session in sessions.values())
            assert inspect_results(results) == obj(state["snapshots"])
            assert recovered_runs == tuple(session.runs().items for session in sessions.values())
            input_refs = state["inputs"]
            assert isinstance(input_refs, list) and all(
                isinstance(item, str) for item in input_refs
            )
            restored_left = sessions["statistics"].artifact(str(input_refs[0]))
            restored_right = sessions["statistics"].artifact(str(input_refs[1]))
            assert isinstance(restored_left, mv.MaterializedNumericRelation)
            assert isinstance(restored_right, mv.MaterializedNumericRelation)
            logical_association = restored_left.correlate(restored_right, method="pearson")
            if phase == "cold":
                with patch.object(statistical_execution, "execute", forbidden):
                    fixed = logical_association.execute()
                assert recovered_runs == tuple(
                    session.runs().items for session in sessions.values()
                )
            else:
                fixed = logical_association.execute()
                state["fixed_association"] = reference(fixed)
                state_path.write_bytes(encode(state))
            assert dict(fixed.contract()._facts)["pairing_key"] == PAIRING_KEY
            assert fixed.coefficient.to_pandas()["value"].tolist() == [1.0]
            assert reference(fixed) == state["fixed_association"]
    return {"phase": phase, "pid": os.getpid()}


if __name__ == "__main__":
    with patch.object(ibis.duckdb, "connect", partial(ibis.duckdb.connect, threads=1)):
        print(json.dumps(run(Path(sys.argv[1]), sys.argv[2])))
