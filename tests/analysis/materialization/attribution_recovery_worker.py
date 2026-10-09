"""Offline allocation retains original target, basis and reconciliation scope."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Protocol
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.refs import DimensionKind, Ref
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.materialization.reference_recovery_worker import saved
from tests.shared_fixtures import run_ids
from tests.support.json import Json, arr, checked, digest, encode, obj, read


class Continuation(Protocol):
    def execute(self) -> _MaterializedRead | mv.MaterializedTable: ...


def proof(value: mv.MaterializedAttributionResult) -> dict[str, Json]:
    assert value._dataset is not None
    parts = value._dataset.verified().parts
    kept: dict[str, Json] = {
        part.role: digest(
            json.dumps(
                {"schema": str(part.table.schema), "rows": part.table.to_pylist()},
                sort_keys=True,
                default=str,
            ).encode()
        )
        for part in parts
        if part.role != "selection_scope"
    }
    assert set(kept) == {
        "current_endpoint",
        "baseline_endpoint",
        "current_endpoint_coordinates",
        "baseline_endpoint_coordinates",
        "basis",
        "allocation",
        "reconciliation",
    }
    reconciliation = next(part.table.to_pylist() for part in parts if part.role == "reconciliation")
    return {"parts": kept, "reconciliation": checked(reconciliation)}


def run(root: Path, phase: str) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    state = read(root / "r94-attribution.json")
    identity = state["session"]
    assert isinstance(identity, str) and not (root / "models").exists()
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(ibis.sqlite, "connect", forbidden),
        patch.object(
            graph_local_execution,
            "execute_verified_fixed",
            wraps=graph_local_execution.execute_verified_fixed,
        ) as kernels,
    ):
        session = mv.session.resume(identity, by="id")
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        views: dict[str, Json] = {}
        proofs: dict[str, Json] = {}
        originals = obj(state["originals"])

        def execute(name: str, operation: Continuation) -> _MaterializedRead | mv.MaterializedTable:
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            outputs[name] = saved(result)
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                repeated = operation.execute()
                assert repeated._dataset is not None
                assert repeated._dataset.artifact.artifact_ref == obj(outputs[name])["artifact"]
            assert run_ids(session) == seen
            return result

        def record(name: str, value: mv.MaterializedAttributionResult) -> None:
            seen = run_ids(session)
            for field in ("current", "baseline", "contribution"):
                relation = getattr(value, field)
                assert isinstance(relation, mv.MaterializedNumericRelation)
                assert relation._dataset is not None
                views[name + ":" + field] = checked(
                    {
                        "artifact": relation._dataset.artifact.artifact_ref,
                        "rows": json.loads(
                            relation.to_pandas().to_json(orient="table", index=False)
                        ),
                        "contract": json.loads(
                            json.dumps(asdict(relation.contract()), default=str)
                        ),
                    }
                )
            assert run_ids(session) == seen
            proofs[name] = proof(value)

        for raw in arr(state["variants"]):
            variant = obj(raw)
            name, metric, axis_kind = variant["name"], variant["metric_kind"], variant["axis_kind"]
            assert (
                isinstance(name, str)
                and metric in ("sum", "mean")
                and axis_kind in ("single", "joint")
            )
            restored: dict[str, _MaterializedRead] = {}
            for label in ("change", "allocation"):
                original = obj(originals[name + ":" + label])
                reference = original["artifact"]
                assert isinstance(reference, str)
                result = session.artifact(reference)
                assert isinstance(result, _MaterializedRead) and snapshot(result) == original
                restored[label] = result
            change, source = restored["change"], restored["allocation"]
            assert isinstance(change, mv.MaterializedDifferenceRelation)
            assert isinstance(source, mv.MaterializedAttributionResult)
            axes: tuple[Ref[DimensionKind], ...] = (ms.ref.dimension("sales.facts.bucket"),)
            if axis_kind == "joint":
                axes += (ms.ref.dimension("sales.facts.region"),)
            expected = (
                {"a": 2, "b": 1, "c": -5} if metric == "sum" else {"a": 1, "b": 0.5, "c": -2.5}
            )
            current = {"a": 2, "b": 4, "c": 0} if metric == "sum" else {"a": 1, "b": 2, "c": 0}
            baseline = {"a": 0, "b": 3, "c": 5} if metric == "sum" else {"a": 0, "b": 1.5, "c": 2.5}
            fixed = execute(name + ":fixed", change.attribute(axes=axes))
            assert isinstance(fixed, mv.MaterializedAttributionResult)
            for owner, value in (("source", source), ("fixed", fixed)):
                record(name + ":" + owner, value)
                assert dict(value.contract()._facts)["complete_partition"] == "True"
                for field, oracle in (
                    ("current", current),
                    ("baseline", baseline),
                    ("contribution", expected),
                ):
                    relation = getattr(value, field)
                    assert isinstance(relation, mv.MaterializedNumericRelation)
                    frame_rows = arr(
                        obj(obj(views[name + ":" + owner + ":" + field])["rows"])["data"]
                    )
                    assert {
                        str(obj(row)["coord_0"]): obj(row)["value"] for row in frame_rows
                    } == oracle
                if axis_kind == "joint":
                    # Joint endpoint recovery is the core witness here. The extra
                    # selected-view definitions exceed the existing graph budget.
                    continue
                selected = execute(
                    name + ":" + owner + "_selected", value.where(value.contribution.value.gt(0))
                )
                assert isinstance(selected, mv.MaterializedAttributionResult)
                record(name + ":" + owner + "_selected", selected)
                assert proofs[name + ":" + owner] == proofs[name + ":" + owner + "_selected"]
                assert dict(selected.contract()._facts)["complete_partition"] == "False"
                selected_rows = arr(
                    obj(obj(views[name + ":" + owner + "_selected:contribution"])["rows"])["data"]
                )
                assert [obj(row)["coord_0"] for row in selected_rows] == ["a", "b"]
                assert [obj(row)["value"] for row in selected_rows] == [
                    expected["a"],
                    expected["b"],
                ]
                if owner == "fixed":
                    summary = execute(
                        name + ":selected_sum", selected.contribution.summarize(mv.sum())
                    )
                    assert summary.to_pandas().value.tolist() == [3 if metric == "sum" else 1.5]
            if axis_kind == "joint":
                assert snapshot(source) == originals[name + ":allocation"]
                assert snapshot(change) == originals[name + ":change"]
                continue
            top = execute(name + ":top", change.attribute(axes=axes, top_k=1))
            assert isinstance(top, mv.MaterializedAttributionResult)
            record(name + ":top", top)
            frame = top.contribution.to_pandas()
            assert sorted(frame.value.tolist()) == ([-3, 1] if metric == "sum" else [-1.5, 0.5])
            assert frame[frame.coord_0 == "b"].value.tolist() == ([1] if metric == "sum" else [0.5])
            assert top._dataset is not None
            exchange = top._dataset.verified()
            keys = exchange.contract.key_fields
            rows = exchange.primary.to_pylist()
            assert any(row[keys[1]] is None and row[keys[-1]] == 1 for row in rows)
            assert any(row[keys[1]] == "b" and row[keys[-1]] == 0 for row in rows)
            table = execute(
                name + ":top_table",
                mv.table(current=top.current, baseline=top.baseline, contribution=top.contribution),
            )
            assert isinstance(table, mv.MaterializedTable)
            frame = table.to_pandas()
            assert sorted(frame.contribution.tolist()) == (
                [-3, 1] if metric == "sum" else [-1.5, 0.5]
            )
            empty = execute(name + ":empty", source.where(source.contribution.value.gt(100)))
            assert isinstance(empty, mv.MaterializedAttributionResult)
            record(name + ":empty", empty)
            assert not arr(obj(obj(views[name + ":empty:contribution"])["rows"])["data"])
            assert proofs[name + ":empty"] == proofs[name + ":source"]
            assert dict(empty.contract()._facts)["complete_partition"] == "False"
            if axis_kind == "joint":
                hierarchy = execute(
                    name + ":hierarchy", change.attribute(axes=axes, mode="hierarchy")
                )
                assert isinstance(hierarchy, mv.MaterializedAttributionResult)
                record(name + ":hierarchy", hierarchy)
                assert sorted(hierarchy.contribution.to_pandas().value.tolist()) == sorted(
                    list(expected.values()) * 2
                )
            assert (
                snapshot(source) == originals[name + ":allocation"]
                and snapshot(change) == originals[name + ":change"]
            )
        assert session._runtime.store.resources(session.id) == ()
        count = sum(
            1 if obj(variant)["axis_kind"] == "joint" else 7 for variant in arr(state["variants"])
        )
        if phase == "fixed":
            assert len(run_ids(session) - before) == kernels.call_count == count
            state["outputs"], state["views"], state["proofs"] = outputs, views, proofs
            (root / "r94-attribution.json").write_bytes(encode(state))
        else:
            assert (
                outputs == state["outputs"]
                and views == state["views"]
                and proofs == state["proofs"]
            )
            assert run_ids(session) == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "views": views,
            "proofs": proofs,
            "new_runs": len(run_ids(session) - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "original_snapshot_preserved": True,
            "source_and_semantic_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
