"""Public corruption deadlines on nine actual archived native statistical producers."""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

import duckdb
import ibis
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import runs_execution
from marivo.analysis.materialization.execute_deadline import CURRENT
from marivo.analysis.materialization.graph_protocol import schema_text
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, arr, checked, encode, obj, read
from tests.r86_journeys import METHODS, Result, graphs
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_statistical_recovery_worker import restore_inputs, snapshot
from tests.test_r94_archived_domain_recovery import restore_project
from tests.test_r94_public_refusals import publication_counts


@pytest.mark.runtime
def test_archived_native_statistical_portable_cold_exact_hits(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    directory = repository / (
        "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-statistical-bindings-01"
    )
    archive_sha = restore_project(
        directory,
        "clickhouse-statistical-project.json",
        "clickhouse",
        tmp_path,
    )
    output = tmp_path / "portable.json"
    completed = subprocess.run(
        [sys.executable, "-m", "tests.r94_statistical_portable_worker", str(tmp_path), str(output)],
        cwd=repository,
        env=dict(os.environ, PYTHONPATH=str(repository), MARIVO_TELEMETRY="off"),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = read(output)
    assert report["pid"] != os.getpid() and report["exact_hits"] == 18
    assert report["new_runs"] == report["kernels"] == report["resources"] == 0
    assert len(obj(report["outputs"])) == 27
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "statistical-portable-cold.json").write_bytes(
            encode(
                {
                    "archive_sha256": archive_sha,
                    "report": report,
                }
            )
        )


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_archived_native_statistical_receipts_and_metadata_refuse_before_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    directory = Path(__file__).resolve().parents[1] / (
        "docs/superpowers/specs/2026-10-06-marivo-r94-evidence/native-statistical-bindings-01"
    )
    archive_sha = restore_project(
        directory, "clickhouse-statistical-project.json", "clickhouse", tmp_path
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    manifest = read(tmp_path / "r94-statistical.json")
    identity = manifest["session"]
    assert isinstance(identity, str)
    calls: list[str] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        calls.append("source-kernel-or-admission")
        raise AssertionError("Archived corruption admitted source or kernel work")

    monkeypatch.setattr(ms, "load", forbidden)
    monkeypatch.setattr(SemanticProject, "load", forbidden)
    monkeypatch.setattr(SourceSession, "__init__", forbidden)
    monkeypatch.setattr(duckdb, "connect", forbidden)
    monkeypatch.setattr(ibis.duckdb, "connect", forbidden)
    monkeypatch.setattr(deviation_numeric, "fit", forbidden)
    monkeypatch.setattr(association_numeric, "score", forbidden)
    monkeypatch.setattr(forecast_numeric, "train", forbidden)
    monkeypatch.setattr(runs_execution, "compute", forbidden)
    session = mv.session.resume(identity, by="id")
    monkeypatch.setattr(session._runtime.store, "admit", forbidden)
    before = run_ids(session)
    publications = publication_counts(session)
    originals = restore_inputs(session, arr(manifest["inputs"])[0])
    logical = graphs(originals)[method]
    witnesses: list[Json] = []

    def refuse(actions: tuple[Callable[[], object], ...], label: str) -> None:
        errors: list[Json] = []
        for action in actions:
            with pytest.raises(AnalysisError) as caught:
                action()
            error = caught.value
            assert error.expected and error.received and error.repair is not None
            assert error.repair.action
            errors.append(
                {
                    "type": type(error).__name__,
                    "expected": error.expected,
                    "received": error.received,
                    "repair": error.repair.action,
                }
            )
        assert run_ids(session) == before and publication_counts(session) == publications
        assert session._runtime.store.resources(session.id) == () and CURRENT.get() is None
        assert calls == []
        witnesses.append({"fault": label, "errors": errors})

    for phase in ("source", "fixed"):
        saved = obj(obj(manifest[phase])[method])
        reference = saved["artifact"]
        assert isinstance(reference, str)
        result = session.artifact(reference)
        assert isinstance(result, Result) and snapshot(result) == saved
        if phase == "fixed":
            assert snapshot(logical.execute()) == saved
        assert result._dataset is not None
        descriptor = result._dataset.artifact.descriptor
        actions: tuple[Callable[[], object], ...] = (
            partial(session.artifact, reference),
            result.to_pandas,
        )
        if phase == "fixed":
            actions += (logical.execute,)
        receipts = [("primary", descriptor.primary_receipt.local)] + [
            (part.role, part.local) for part in descriptor.parts
        ]
        for role, receipt in receipts:
            path = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
            original = path.read_bytes()
            for damage in ("missing", "corrupt"):
                if damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"R9.4 corrupted native statistical receipt")
                try:
                    refuse(actions, phase + ":" + role + ":" + damage)
                finally:
                    path.write_bytes(original)
        with session._runtime.store._connection() as connection:
            row = connection.execute(
                "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?",
                (reference,),
            ).fetchone()
            assert row is not None and isinstance(row[0], str)
            payload = row[0]
        for fault in ("schema", "version", "key", "scope", "implementation"):
            altered = obj(checked(json.loads(payload)))
            if fault == "schema":
                altered["realized_schema"] = schema_text(pa.schema([]))
            elif fault == "version":
                obj(altered["method_state"])["contract_version"] = 999
            elif fault in ("key", "scope"):
                domain = obj(obj(altered["signature"])["domain"])
                if fault == "scope":
                    obj(domain["binding"])["scope_id"] = "foreign-scope"
                else:
                    obj(arr(domain["instance_key"])[0])["field"] = "foreign-key"
            else:
                obj(arr(altered["method_bindings"])[0])["implementation_id"] = (
                    "foreign-implementation"
                )
            try:
                with session._runtime.store._connection() as connection:
                    connection.execute(
                        "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                        (encode(altered).decode(), reference),
                    )
                refuse(actions, phase + ":metadata:" + fault)
            finally:
                with session._runtime.store._connection() as connection:
                    connection.execute(
                        "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
                        (payload, reference),
                    )
        assert snapshot(result) == saved
    assert snapshot(logical.execute()) == obj(obj(manifest["fixed"])[method])
    assert calls == [] and run_ids(session) == before
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "statistical-fault-" + method + ".json").write_bytes(
            encode(
                {
                    "method": method,
                    "producer": "clickhouse",
                    "archive_sha256": archive_sha,
                    "witnesses": witnesses,
                    "tripwire_calls": [],
                    "new_runs": 0,
                    "publication_counts_before": list(publications),
                    "publication_counts_after": list(publication_counts(session)),
                    "original_snapshots_preserved": True,
                    "resources": 0,
                    "boundary": "Actual archived native int64 source and fixed artifacts; public recovery/read and fixed exact-hit refusal only. Other producer/profile and transaction faults remain independent.",
                }
            )
        )
