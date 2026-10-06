"""Consume every disclosed K of native Anchor current-row statistics offline."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.errors import StatisticalRelationError
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, checked, digest, encode, obj, read
from tests.r94_domain_recovery_worker import checked_bytes, forbidden, snapshot
from tests.r94_native_domain_k_worker import Continuation, run_ids

METHODS = ("count", "count_defined", "sum", "min", "max")
REASON = "quantity template has no registered comparison rule"


def counts(session: Session) -> list[Json]:
    values: list[Json] = []
    with session._runtime.store._read() as connection:
        for table in ("dataset_artifacts", "dataset_evidence", "findings"):
            row = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()
            assert row is not None and isinstance(row[0], int)
            values.append(row[0])
    return values


def compact(result: _MaterializedRead) -> dict[str, Json]:
    full = snapshot(result)
    return {
        "artifact": full["artifact"],
        "snapshot_sha256": digest(encode(full)),
        "schema": full["schema"],
        "rows": full["rows"],
        "parts": full["parts"],
    }


def check_input(value: mv.MaterializedStatisticRelation, saved: dict[str, Json]) -> None:
    current = snapshot(value)
    previous_card = obj(saved["contract"])
    current_card = obj(current["contract"])
    expected_card = dict(previous_card)
    expected_card["actions"] = [
        action
        for action in arr(previous_card["actions"])
        if obj(action)["call"] not in ("relation.compare(baseline)", "relation.correlate(*others)")
    ]
    unavailable = {
        "comparison_unavailable": REASON,
        "correlation_unavailable": "Scalar has no Entity/category/time statistical units",
    }
    facts = arr(current_card["_facts"])
    added = {str(arr(raw)[0]): arr(raw)[1] for raw in facts if arr(raw)[0] in unavailable}
    assert added == unavailable
    assert [raw for raw in facts if arr(raw)[0] not in unavailable] == previous_card["_facts"]
    expected_card["_facts"] = facts
    assert current_card == expected_card
    expected = dict(saved)
    expected["contract"] = expected_card
    assert current == expected


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
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(session_id, by="id")
        before = run_ids(session)
        values: dict[str, mv.MaterializedStatisticRelation] = {}
        for method in METHODS:
            saved = obj(obj(manifest["supplement"])[f"observation_{method}"])
            reference = saved["artifact"]
            assert isinstance(reference, str)
            value = session.artifact(reference)
            assert isinstance(value, mv.MaterializedStatisticRelation)
            check_input(value, saved)
            values[method] = value
        outputs: dict[str, Json] = {}
        consumed: dict[str, Json] = {}
        static_refusals: dict[str, Json] = {}
        for method, value in values.items():
            actions = {action.call for action in value.contract().actions}
            assert actions == {
                "relation.rollup()",
                "relation.rank(order=order, ties=ties)",
                "relation.deviation(method=method)",
                "relation.ratio(other)",
            }
            consumed[method] = checked(sorted(actions))
            static_before = counts(session)
            runs_before = run_ids(session)
            try:
                value.compare(value, design=mv.CohortContrast())
            except DatasetConstructionError as error:
                assert error.received == REASON
                assert (
                    error.expected == "a registered comparison rule for every frozen quantity node"
                )
                assert error.repair is not None and "relation.contract()" in error.repair.action
                static_refusals[method] = {
                    "kind": type(error).__name__,
                    "expected": error.expected,
                    "received": error.received,
                    "repair": error.repair.action,
                    "unchanged_counts": static_before,
                    "new_runs": 0,
                }
            else:
                raise AssertionError("Unregistered frozen comparison template was admitted")
            assert counts(session) == static_before and run_ids(session) == runs_before
            operations: dict[str, Continuation] = {
                "rollup": value.rollup(),
                "rank": value.rank(order="descending", ties="ordinal"),
                "zscore": value.deviation(method="zscore"),
                "mad": value.deviation(method="mad"),
                "ratio": value.ratio(value),
            }
            for name, logical in operations.items():
                label = method + ":" + name
                if phase == "cold":
                    with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                        result = logical.execute()
                else:
                    result = logical.execute()
                expected_value = 4 if method == "sum" else 2
                if name == "rank":
                    assert isinstance(result, mv.MaterializedRankingResult)
                    assert result.ranks.to_pandas().value.tolist() == [1]
                    assert result.values.to_pandas().value.tolist() == [expected_value]
                elif name in ("zscore", "mad"):
                    frame = result.to_pandas()
                    assert frame.cell_tag.tolist() == ["undefined"]
                    assert frame.cell_reason.tolist() == ["insufficient_samples"]
                else:
                    assert result.to_pandas().value.tolist() == (
                        [1] if name == "ratio" else [expected_value]
                    )
                saved = compact(result)
                if phase == "cold":
                    assert saved == obj(manifest["statistic_K"])[label]
                repeat_before = run_ids(session)
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    repeated = logical.execute()
                    assert repeated._dataset is not None
                    assert repeated._dataset.artifact.artifact_ref == saved["artifact"]
                assert run_ids(session) == repeat_before
                outputs[label] = saved
        successful_kernels = kernels.call_count
        successful_new_runs = len(run_ids(session) - before)
        if phase == "cold":
            assert successful_kernels == successful_new_runs == 0
        else:
            assert successful_kernels > 0 and successful_new_runs > 0
        correlation_refusals: dict[str, Json] = {}
        for method, value in values.items():
            earlier = counts(session)
            prior_runs = run_ids(session)
            prior_kernels = kernels.call_count
            try:
                value.correlate(values["sum" if method != "sum" else "count"]).execute()
            except StatisticalRelationError as error:
                assert error.code == "r8.correspondence"
                assert error.expected == "Entity/category/time statistical units"
                assert error.received == "Scalar"
                assert error.expected is not None and error.received is not None
                assert error.repair is not None and error.repair.action
                assert counts(session) == earlier
                failed_ids = run_ids(session) - prior_runs
                assert not failed_ids
                assert kernels.call_count == prior_kernels
                assert session._runtime.store.resources(session.id) == ()
                correlation_refusals[method] = {
                    "code": error.code,
                    "expected": error.expected,
                    "received": error.received,
                    "repair": error.repair.action,
                    "unchanged_counts": earlier,
                    "new_failed_runs": len(failed_ids),
                    "failed_run_ids": checked(sorted(failed_ids)),
                    "kernels": kernels.call_count - prior_kernels,
                }
            else:
                raise AssertionError("Scalar without statistical units admitted correlation")
        assert session._runtime.store.resources(session.id) == ()
        if phase == "fixed":
            manifest["statistic_K"] = outputs
            (root / "r94-domain.json").write_bytes(checked_bytes(manifest))
        return {
            "phase": phase,
            "pid": os.getpid(),
            "source_offline": True,
            "successful_new_runs": successful_new_runs,
            "successful_kernels": successful_kernels,
            "outputs": outputs,
            "consumed_K": consumed,
            "static_refusals": static_refusals,
            "correlation_refusals": correlation_refusals,
            "resources": 0,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(checked_bytes(run(Path(sys.argv[1]), sys.argv[2])))
