"""Verify all cached native statistical outputs in an isolated restored project."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import duckdb
import ibis

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization import runs_execution
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, encode, obj, read
from tests.r86_journeys import Result, graphs
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_recovery_worker import forbidden
from tests.r94_statistical_recovery_worker import restore_inputs, snapshot
from tests.test_r94_public_refusals import publication_counts


def run(root: Path) -> dict[str, Json]:
    os.chdir(root)
    os.environ["MARIVO_PROJECT_ROOT"] = str(root)
    assert not (root / "models").exists() and not (root / "source.duckdb").exists()
    manifest = read(root / "r94-statistical.json")
    identity = manifest["session"]
    assert isinstance(identity, str)
    with (
        patch.object(ms, "load", forbidden),
        patch.object(SemanticProject, "load", forbidden),
        patch.object(SourceSession, "__init__", forbidden),
        patch.object(duckdb, "connect", forbidden),
        patch.object(ibis.duckdb, "connect", forbidden),
        patch.object(deviation_numeric, "fit", forbidden),
        patch.object(association_numeric, "score", forbidden),
        patch.object(forecast_numeric, "train", forbidden),
        patch.object(runs_execution, "compute", forbidden),
    ):
        session = mv.session.resume(identity, by="id")
        before = run_ids(session)
        published = publication_counts(session)
        snapshots: dict[str, Json] = {}
        with patch.object(session._runtime.store, "admit", forbidden):
            for phase in ("source", "fixed", "cold"):
                for method, raw in obj(manifest[phase]).items():
                    saved = obj(raw)
                    reference = saved["artifact"]
                    assert isinstance(reference, str)
                    value = session.artifact(reference)
                    assert isinstance(value, Result) and snapshot(value) == saved
                    snapshots[phase + ":" + method] = saved
            for index, phase in enumerate(("fixed", "cold")):
                originals = restore_inputs(session, arr(manifest["inputs"])[index])
                for method, logical in graphs(originals).items():
                    assert snapshot(logical.execute()) == obj(manifest[phase])[method]
        assert run_ids(session) == before and publication_counts(session) == published
        assert session._runtime.store.resources(session.id) == ()
        return {
            "pid": os.getpid(),
            "outputs": snapshots,
            "exact_hits": 18,
            "new_runs": 0,
            "kernels": 0,
            "resources": 0,
            "publication_counts": list(published),
            "source_semantic_duckdb_admission_forbidden": True,
        }


if __name__ == "__main__":
    Path(sys.argv[2]).write_bytes(encode(run(Path(sys.argv[1]))))
