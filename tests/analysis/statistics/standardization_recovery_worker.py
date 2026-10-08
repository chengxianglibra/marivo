"""Recover actual float/Decimal standardization producers without source state."""

import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import graph_local_execution
from marivo.analysis.public_dsl import _MaterializedRead
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.domain_recovery_worker import forbidden, snapshot
from tests.analysis.materialization.reference_recovery_worker import reference_parts
from tests.shared_fixtures import run_ids
from tests.support.json import Json, checked, encode, obj, read


def parts(value: _MaterializedRead) -> dict[str, Json]:
    assert value._dataset is not None
    return {
        part.role: checked(
            {
                "schema": str(part.table.schema),
                "rows": json.loads(json.dumps(part.table.to_pylist(), default=str)),
            }
        )
        for part in value._dataset.verified().parts
    }


def run(root: Path, phase: Literal["fixed", "cold"]) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    path = root / "r94-standardization.json"
    state = read(path)
    identity = state["session"]
    assert isinstance(identity, str)
    assert not (root / "models").exists() and not (root / "warehouse.duckdb").exists()
    assert not (root / "source_files").exists()
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
        originals: dict[str, _MaterializedRead] = {}
        for name, raw in obj(state["originals"]).items():
            saved = obj(raw)
            artifact_reference = saved["artifact"]
            assert isinstance(artifact_reference, str)
            value = session.artifact(artifact_reference)
            assert isinstance(value, _MaterializedRead)
            assert snapshot(value) == saved and parts(value) == obj(state["parts"])[name]
            originals[name] = value
        weights, categories = originals["weights"], originals["categories"]
        assert isinstance(weights, mv.MaterializedNumericRelation)
        assert isinstance(categories, mv.MaterializedCategoryRelation)
        before = run_ids(session)
        outputs: dict[str, Json] = {}
        output_parts: dict[str, Json] = {}
        retained: dict[str, Json] = {}
        values: dict[str, Json] = {}
        for kind in ("sum", "mean", "weighted_mean", "ratio", "linear"):
            groups = originals["groups:" + kind]
            assert isinstance(
                groups, (mv.MaterializedGroupedNumericRelation, mv.MaterializedRolledRatioRelation)
            ), (kind, type(groups).__name__)
            reference = mv.reference_weights(
                weights, strata=(categories,), unit=ms.ref.entity("sales.order")
            )
            operation = groups.standardize(reference=reference)
            if phase == "cold":
                with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                    result = operation.execute()
            else:
                result = operation.execute()
            assert isinstance(result, mv.MaterializedNumericRelation)
            actual = result.to_pandas().value.tolist()
            expected = obj(state["expected"])[kind]
            assert isinstance(expected, (str, float))
            assert actual == [Decimal(expected) if isinstance(expected, str) else expected]
            assert result._dataset is not None
            assert result._dataset.verified().contract.state_kind == "standardized"
            assert not any(
                action.call == "relation.rollup()" for action in result.contract().actions
            )
            outputs[kind] = snapshot(result)
            output_parts[kind] = parts(result)
            retained[kind] = reference_parts(result)
            values[kind] = str(actual[0]) if isinstance(expected, str) else checked(actual[0])
            seen = run_ids(session)
            with patch.object(graph_local_execution, "execute_verified_fixed", forbidden):
                assert snapshot(operation.execute()) == outputs[kind]
            assert run_ids(session) == seen
        current = run_ids(session)
        assert session._runtime.store.resources(session.id) == ()
        for name, original in originals.items():
            assert snapshot(original) == obj(state["originals"])[name]
            assert parts(original) == obj(state["parts"])[name]
        if phase == "fixed":
            assert len(current - before) == kernels.call_count == 5
            state["outputs"], state["retained"] = outputs, retained
            path.write_bytes(encode(state))
        else:
            assert outputs == state["outputs"] and retained == state["retained"]
            assert current == before and kernels.call_count == 0
        return {
            "phase": phase,
            "pid": os.getpid(),
            "outputs": outputs,
            "output_parts": output_parts,
            "retained": retained,
            "values": values,
            "new_runs": len(current - before),
            "kernels": kernels.call_count,
            "resources": 0,
            "source_semantic_reconnect_forbidden": True,
            "original_snapshots_and_parts_preserved": True,
        }


if __name__ == "__main__":
    raw = sys.argv[2]
    assert raw in ("fixed", "cold")
    phase: Literal["fixed", "cold"] = "fixed" if raw == "fixed" else "cold"
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), phase)))
