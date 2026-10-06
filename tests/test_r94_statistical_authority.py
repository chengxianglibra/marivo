"""Archived native statistical semantic parts and public Finding authority."""

import json
import os
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
from marivo.analysis.materialization.graph_exchange import ExchangePart, ExchangeResult, from_arrow
from marivo.analysis.methods import association_numeric, deviation_numeric, forecast_numeric
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from scripts.r9_qualification_requirements import Json, checked, encode, obj, read
from tests.r86_journeys import METHODS, Result
from tests.r94_native_domain_k_worker import run_ids
from tests.r94_statistical_recovery_worker import snapshot
from tests.test_r94_archived_domain_recovery import restore_project
from tests.test_r94_public_refusals import publication_counts


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_archived_native_statistical_semantic_parts_and_finding_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
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
        raise AssertionError("Archived authority validation admitted work")

    for owner, name in (
        (ms, "load"),
        (SemanticProject, "load"),
        (SourceSession, "__init__"),
        (duckdb, "connect"),
        (ibis.duckdb, "connect"),
        (deviation_numeric, "fit"),
        (association_numeric, "score"),
        (forecast_numeric, "train"),
        (runs_execution, "compute"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    session = mv.session.resume(identity, by="id")
    store = session._runtime.store
    monkeypatch.setattr(store, "admit", forbidden)
    before = run_ids(session)
    publications = publication_counts(session)
    witnesses: list[Json] = []

    def error_row(error: AnalysisError) -> dict[str, Json]:
        assert error.expected and error.received and error.repair is not None
        assert error.repair.action
        return {
            "type": type(error).__name__,
            "expected": error.expected,
            "received": error.received,
            "repair": error.repair.action,
        }

    def unchanged() -> None:
        assert run_ids(session) == before and publication_counts(session) == publications
        assert store.resources(session.id) == () and CURRENT.get() is None and calls == []

    for phase in ("source", "fixed"):
        saved = obj(obj(manifest[phase])[method])
        reference = saved["artifact"]
        assert isinstance(reference, str)
        result = session.artifact(reference)
        assert isinstance(result, Result) and snapshot(result) == saved
        assert result._dataset is not None
        retained = result._dataset.verified()

        def reject(
            primary: pa.Table,
            parts: tuple[ExchangePart, ...],
            fault: str,
            retained: ExchangeResult = retained,
            phase: str = phase,
        ) -> None:
            with pytest.raises(AnalysisError) as caught:
                from_arrow(
                    primary, retained.contract, parts=parts, method_state=retained.method_state
                )
            witnesses.append(
                {"fault": phase + ":exchange:" + fault, "errors": [error_row(caught.value)]}
            )
            unchanged()

        for part in retained.parts:
            reject(
                retained.primary,
                tuple(value for value in retained.parts if value.role != part.role),
                part.role + ":missing",
            )
            damaged = ExchangePart(part.role, pa.table({part.role + "__retained": ["{}"]}))
            reject(
                retained.primary,
                tuple(damaged if value.role == part.role else value for value in retained.parts),
                part.role + ":malformed",
            )
        policy = next(part for part in retained.parts if part.role == "finding_policy")
        payload = obj(checked(json.loads(policy.table[0][0].as_py())))
        for fault, field, value in (
            ("version", "version", "v999"),
            ("cap", "emitted", 1001),
            ("binding", "ordered_input_bindings", ["capture:foreign-input"]),
        ):
            changed: dict[str, Json] = {**payload, field: checked(value)}
            damaged = ExchangePart(
                policy.role, pa.table({policy.role + "__retained": [encode(changed).decode()]})
            )
            reject(
                retained.primary,
                tuple(damaged if part.role == policy.role else part for part in retained.parts),
                "finding_policy:" + fault,
            )
        captured = next(
            part
            for part in retained.parts
            if part.role in ("fit_inputs", "condition_cells", "pair_inputs", "training_inputs")
        )
        captured_payload = obj(checked(json.loads(captured.table[0][0].as_py())))
        domain = (
            obj(obj(captured_payload["signature"])["domain"])
            if "signature" in captured_payload
            else obj(obj(captured_payload["declaration"])["input_domain"])
        )
        obj(domain["binding"])["scope_id"] = "foreign-scope"
        damaged = ExchangePart(
            captured.role,
            pa.table({captured.role + "__retained": [encode(captured_payload).decode()]}),
        )
        reject(
            retained.primary,
            tuple(damaged if part.role == captured.role else part for part in retained.parts),
            captured.role + ":scope",
        )
        key = retained.primary.column_names[0]
        keys = retained.primary[key].to_pylist()
        assert keys
        keys[0] = "foreign-output-coordinate" if isinstance(keys[0], str) else 999999
        reject(
            retained.primary.set_column(
                0, key, pa.array(keys, type=retained.primary.schema.field(key).type)
            ),
            retained.parts,
            "primary:key",
        )
        for fault in ("digest", "body", "order"):
            with store._connection() as connection:
                evidence = connection.execute(
                    "SELECT finding_set_digest FROM dataset_evidence WHERE artifact_ref=?",
                    (reference,),
                ).fetchone()
                rows = connection.execute(
                    "SELECT finding_ordinal,finding_body_payload FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal",
                    (reference,),
                ).fetchall()
                assert evidence is not None
                if fault == "digest":
                    connection.execute(
                        "UPDATE dataset_evidence SET finding_set_digest=? WHERE artifact_ref=?",
                        ("foreign-digest", reference),
                    )
                elif not rows:
                    connection.execute(
                        "INSERT INTO findings(artifact_ref,finding_ordinal,finding_identity_digest,finding_body_payload,finding_ref) VALUES(?,?,?,?,?)",
                        (reference, 10, "foreign", "{}", "finding_foreign"),
                    )
                elif fault == "body":
                    connection.execute(
                        "UPDATE findings SET finding_body_payload=? WHERE artifact_ref=?",
                        ("{}", reference),
                    )
                else:
                    connection.execute(
                        "UPDATE findings SET finding_ordinal=finding_ordinal+10 WHERE artifact_ref=?",
                        (reference,),
                    )
            injected_counts = publication_counts(session)
            try:
                errors: list[Json] = []
                for action in (result.evidence_digest, result.findings):
                    with pytest.raises(AnalysisError) as caught:
                        action()
                    errors.append(error_row(caught.value))
                witnesses.append({"fault": phase + ":public-findings:" + fault, "errors": errors})
                assert publication_counts(session) == injected_counts
                assert run_ids(session) == before and calls == []
                assert store.resources(session.id) == () and CURRENT.get() is None
            finally:
                with store._connection() as connection:
                    if fault == "digest":
                        connection.execute(
                            "UPDATE dataset_evidence SET finding_set_digest=? WHERE artifact_ref=?",
                            (evidence[0], reference),
                        )
                    elif not rows:
                        connection.execute(
                            "DELETE FROM findings WHERE artifact_ref=?", (reference,)
                        )
                    elif fault == "body":
                        connection.executemany(
                            "UPDATE findings SET finding_body_payload=? WHERE artifact_ref=? AND finding_ordinal=?",
                            [(row[1], reference, row[0]) for row in rows],
                        )
                    else:
                        connection.execute(
                            "UPDATE findings SET finding_ordinal=finding_ordinal-10 WHERE artifact_ref=?",
                            (reference,),
                        )
            assert snapshot(result) == saved
            unchanged()
        unchanged()
    if destination := os.environ.get("MARIVO_R93_EVIDENCE_DIR"):
        Path(destination, "statistical-authority-" + method + ".json").write_bytes(
            encode(
                {
                    "method": method,
                    "archive_sha256": archive_sha,
                    "witnesses": witnesses,
                    "new_runs": 0,
                    "resources": 0,
                    "tripwire_calls": list(calls),
                    "publication_counts_before": list(publications),
                    "publication_counts_after": list(publication_counts(session)),
                    "original_snapshots_preserved": True,
                    "boundary": "Shared controlled-exchange semantic validation of actual archived ClickHouse parts and public committed Finding reads; no re-signed backing or transaction-fault grant.",
                }
            )
        )
