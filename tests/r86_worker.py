"""Independent R8.6 producer, offline fixed kernels and cold kernels."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.rules import AssociationFit, DeviationFit, ForecastFit, TimeRuns
from marivo.analysis.materialization import (
    deviation_execution,
    runs_execution,
    statistical_execution,
)
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.json_support import Json
from tests.json_support import arr as array_json
from tests.json_support import obj as object_json
from tests.json_support import read as read_json
from tests.r86_journeys import Numeric, Result, check, create, graphs, inputs, proof, snapshot


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline R8.6 touched current Semantic, calendar or source")


def text(value: Json) -> str:
    assert isinstance(value, str)
    return value


def run(root: Path, phase: str, form: str) -> dict[str, Json]:
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.environ["MARIVO_TELEMETRY"] = "off"
    state_path = root / "r86.json"
    proofs: list[Json] = []
    originals: tuple[Numeric, Numeric, Numeric]
    connect = ibis.duckdb.connect
    with (
        patch.object(
            ibis.duckdb,
            "connect",
            lambda *args, **kwargs: connect(*args, **{**kwargs, "threads": 1}),
        ),
        patch.object(deviation_execution, "execute", wraps=deviation_execution.execute) as d,
        patch.object(runs_execution, "execute", wraps=runs_execution.execute) as r,
        patch.object(statistical_execution, "execute", wraps=statistical_execution.execute) as s,
    ):
        if phase == "produce":
            session = create(root, form)
            originals = inputs(session)
            # Distinct source realizations provide independent fresh cold-kernel inputs.
            saved_inputs: list[Json] = []
            for _ in range(2):
                references: list[Json] = []
                for value in originals:
                    assert isinstance(
                        value, (mv.LogicalNumericRelation, mv.LogicalRolledNumericRelation)
                    )
                    references.append(value.execute().state.artifact_ref.ref)
                saved_inputs.append(references)
            entries: dict[str, Json] = {}
            for method, logical in graphs(originals).items():
                result = logical.execute()
                check(result, method)
                first = snapshot(result)
                repeated = logical.execute()
                check(repeated, method)
                assert repeated.state.artifact_ref != result.state.artifact_ref
                entries[method] = first
                row = proof(result, method, phase, form, first)
                row["scenarios"] = ["producer_process", "source_new_realization"]
                proofs.append(row)
            state: dict[str, Json] = {
                "session": session.id,
                "inputs": saved_inputs,
                "source": entries,
                "proofs": proofs,
            }
            (root / "warehouse.duckdb").unlink()
            shutil.rmtree(root / "models")
            if (root / "source_files").exists():
                shutil.rmtree(root / "source_files")
        else:
            state = read_json(state_path)
            assert not (root / "warehouse.duckdb").exists()
            assert not (root / "models").exists()
            assert not (root / "source_files").exists()
            with (
                patch.object(ms, "load", forbidden),
                patch.object(SemanticProject, "load", forbidden),
                patch.object(SourceSession, "__init__", forbidden),
                patch.object(duckdb, "connect", forbidden),
                patch.object(ibis.duckdb, "connect", forbidden),
            ):
                session = mv.session.resume(text(state["session"]), by="id")
                for method, raw in object_json(state["source"]).items():
                    saved = object_json(raw)
                    restored = session.artifact(text(saved["artifact"]))
                    assert isinstance(restored, Result)
                    with (
                        patch.object(deviation_numeric, "fit", forbidden),
                        patch.object(runs_execution, "compute", forbidden),
                        patch.object(association_numeric, "score", forbidden),
                        patch.object(forecast_numeric, "train", forbidden),
                    ):
                        assert snapshot(restored) == saved
                        check(restored, method)
                index = 0 if phase == "fixed" else 1
                refs = array_json(array_json(state["inputs"])[index])
                restored_inputs: list[Numeric] = []
                for ref in refs:
                    original = session.artifact(text(ref))
                    assert isinstance(
                        original,
                        (mv.MaterializedNumericRelation, mv.MaterializedGroupedNumericRelation),
                    )
                    restored_inputs.append(original)
                originals = restored_inputs[0], restored_inputs[1], restored_inputs[2]
                entries = {}
                for method, logical in graphs(originals).items():
                    before = sum(
                        isinstance(
                            call.args[0].parameters,
                            (DeviationFit, TimeRuns, AssociationFit, ForecastFit),
                        )
                        for consumer in (d, r, s)
                        for call in consumer.call_args_list
                    )
                    result = logical.execute()
                    after = sum(
                        isinstance(
                            call.args[0].parameters,
                            (DeviationFit, TimeRuns, AssociationFit, ForecastFit),
                        )
                        for consumer in (d, r, s)
                        for call in consumer.call_args_list
                    )
                    assert after == before + 1, "new fixed/cold method must execute one kernel"
                    check(result, method)
                    saved = snapshot(result)
                    runs = session.runs().items
                    with (
                        patch.object(deviation_numeric, "fit", forbidden),
                        patch.object(runs_execution, "compute", forbidden),
                        patch.object(association_numeric, "score", forbidden),
                        patch.object(forecast_numeric, "train", forbidden),
                    ):
                        assert snapshot(logical.execute()) == saved
                    assert session.runs().items == runs
                    row = proof(result, method, phase, form, saved)
                    row["scenarios"] = [
                        "fixed_kernel_process" if phase == "fixed" else "cold_kernel_process",
                        "fixed_exact_hit" if phase == "fixed" else "offline_no_semantic_duckdb",
                    ]
                    proofs.append(row)
                    entries[method] = saved
                state[phase] = entries
                array_json(state["proofs"]).extend(proofs)
        state_path.write_text(json.dumps(state, sort_keys=True))
    return {
        "phase": phase,
        "pid": os.getpid(),
        "proofs": proofs,
        "source_offline": phase != "produce",
    }


if __name__ == "__main__":
    report = run(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    Path(sys.argv[4]).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
