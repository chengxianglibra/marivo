"""Execute nine retained statistical methods with every source access forbidden."""

import os
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
from marivo.analysis.materialization.graph_protocol import DESCRIPTOR
from marivo.analysis.materialization.graph_protocol import encode as descriptor_encode
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, digest, encode, obj, read
from tests.r86_journeys import Numeric, Result, check, graphs, proof
from tests.r86_journeys import snapshot as method_snapshot
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_recovery_worker import forbidden
from tests.r94_recovery_worker import snapshot as input_snapshot


def snapshot(result: Result) -> dict[str, Json]:
    assert result._dataset is not None
    descriptor = result._dataset.artifact.descriptor
    result._dataset.verified()
    return {
        **method_snapshot(result),
        "descriptor_sha256": digest(descriptor_encode(descriptor, DESCRIPTOR).encode()),
        "execution_key_digest": descriptor.execution_key_digest,
        "continuation_snapshot_digest": descriptor.continuation_snapshot_digest,
        "required_parts": list(result.contract().required_parts),
    }


def method_proof(result: Result, method: str, phase: str) -> dict[str, Json]:
    row = proof(result, method, phase, "table", snapshot(result))
    # The shared oracle's fixture labels describe its original two-field keys.
    # Actual producer/profile and full keys are bound by this journey's manifest.
    for name in ("origin_profile", "time_profile", "key_profile"):
        row.pop(name)
    return row


def restore_inputs(session: mv.Session, raw: Json) -> tuple[Numeric, Numeric, Numeric]:
    values: list[Numeric] = []
    for entry in arr(raw):
        saved = obj(entry)
        reference = saved["artifact"]
        assert isinstance(reference, str)
        value = session.artifact(reference)
        assert isinstance(value, mv.MaterializedNumericRelation)
        assert input_snapshot(value) == saved
        values.append(value)
    assert len(values) == 3
    return values[0], values[1], values[2]


def run(root: Path, phase: str) -> dict[str, Json]:
    assert phase in ("fixed", "cold")
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    os.chdir(root)
    assert not (root / "models").exists() and not (root / "source.duckdb").exists()
    path = root / "r94-statistical.json"
    state = read(path)
    identity = state["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(deviation_execution, "execute", wraps=deviation_execution.execute) as d,
        patch.object(runs_execution, "execute", wraps=runs_execution.execute) as r,
        patch.object(statistical_execution, "execute", wraps=statistical_execution.execute) as s,
    ):
        session = mv.session.resume(identity, by="id")
        before = run_ids(session)
        with (
            patch.object(deviation_numeric, "fit", forbidden),
            patch.object(runs_execution, "compute", forbidden),
            patch.object(association_numeric, "score", forbidden),
            patch.object(forecast_numeric, "train", forbidden),
        ):
            for method, raw in obj(state["source"]).items():
                saved = obj(raw)
                reference = saved["artifact"]
                assert isinstance(reference, str)
                result = session.artifact(reference)
                assert isinstance(result, Result)
                assert snapshot(result) == saved
                check(result, method)
            if phase == "cold":
                first = graphs(restore_inputs(session, arr(state["inputs"])[0]))
                for method, logical in first.items():
                    assert snapshot(logical.execute()) == obj(state["fixed"])[method]
        assert run_ids(session) == before
        entries: dict[str, Json] = {}
        proofs: list[Json] = []
        originals = restore_inputs(session, arr(state["inputs"])[0 if phase == "fixed" else 1])
        for method, logical in graphs(originals).items():
            old_runs = run_ids(session)
            result = logical.execute()
            check(result, method)
            assert len(run_ids(session) - old_runs) == 1
            saved = snapshot(result)
            with (
                patch.object(deviation_numeric, "fit", forbidden),
                patch.object(runs_execution, "compute", forbidden),
                patch.object(association_numeric, "score", forbidden),
                patch.object(forecast_numeric, "train", forbidden),
            ):
                assert snapshot(logical.execute()) == saved
            assert len(run_ids(session) - old_runs) == 1
            entries[method] = saved
            proofs.append(method_proof(result, method, phase))
        kernels = sum(
            isinstance(
                call.args[0].parameters, (DeviationFit, TimeRuns, AssociationFit, ForecastFit)
            )
            for consumer in (d, r, s)
            for call in consumer.call_args_list
        )
        assert kernels == len(entries) == len(run_ids(session) - before) == 9
        assert session._runtime.store.resources(session.id) == ()
        state[phase] = entries
        path.write_bytes(encode(state))
        return {
            "phase": phase,
            "pid": os.getpid(),
            "new_runs": 9,
            "kernels": kernels,
            "exact_hit_new_runs": 0,
            "resources": 0,
            "source_semantic_duckdb_forbidden": True,
            "source_snapshots_unchanged": True,
            "proofs": proofs,
            "outputs": entries,
        }


if __name__ == "__main__":
    Path(sys.argv[3]).write_bytes(encode(run(Path(sys.argv[1]), sys.argv[2])))
