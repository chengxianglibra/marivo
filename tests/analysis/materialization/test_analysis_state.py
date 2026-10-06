"""Independent current-graph state, integrity and offline recovery regressions."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from marivo.analysis.materialization import graph_store
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.store import SessionStore
from marivo.datasource.adapters import SourceSession
from tests.analysis.materialization.state_fixtures import _snapshot, _values
from tests.shared_fixtures import DslCaseFactory


@pytest.mark.runtime
@pytest.mark.parametrize(
    "fault", ("none", "producer", "evidence", "descriptor", "finding", "missing", "mutated")
)
def test_revalidation_axes_are_independent_read_only_and_source_free(
    analysis_dsl_case_factory: DslCaseFactory,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    fixed = _values(case).execute()
    assert fixed.to_pandas().set_index("member")["value"].to_dict() == {
        "A": 60,
        "B": 120,
        "C": 0,
        "D": 0,
    }
    store, reference = case.session._runtime.store, fixed.state.artifact_ref
    with store._read() as connection:
        record = graph_store.artifact(store, connection, reference.ref)
    assert record is not None
    if fault in {"producer", "evidence", "descriptor", "finding"}:
        with store._write() as connection:
            if fault == "producer":
                connection.execute(
                    "UPDATE analysis_action_runs SET execution_key_digest=? WHERE run_ref=?",
                    ("0" * 64, record.producing_run_ref),
                )
            elif fault == "evidence":
                connection.execute(
                    "UPDATE dataset_evidence SET evidence_digest=? WHERE artifact_ref=?",
                    ("0" * 64, reference.ref),
                )
            elif fault == "descriptor":
                connection.execute(
                    "UPDATE dataset_artifacts SET descriptor_payload='{}' WHERE artifact_ref=?",
                    (reference.ref,),
                )
            else:
                connection.execute(
                    "INSERT INTO findings VALUES(?,?,?,?,?)",
                    ("bad", reference.ref, 0, "0" * 64, "private-canary"),
                )
    elif fault in {"missing", "mutated"}:
        receipt = record.descriptor.primary_receipt.local
        path = case.root / receipt.project_relative_path / receipt.file_manifest[0].relative_path
        if fault == "missing":
            path.unlink()
        else:
            path.write_bytes(b"changed bytes")
    before = _snapshot(store)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("revalidation opened the business source")

    monkeypatch.setattr(SourceSession, "batches", forbidden)
    inspection = case.session.revalidate(reference)
    expected = {
        "none": ("valid", "readable", "valid"),
        "producer": ("invalid", "readable", "valid"),
        "evidence": ("valid", "readable", "invalid"),
        "descriptor": ("invalid", "unknown", "unverifiable"),
        "finding": ("valid", "readable", "invalid"),
        "missing": ("valid", "missing", "unverifiable"),
        "mutated": ("valid", "mutated", "unverifiable"),
    }[fault]
    assert (
        inspection.artifact_integrity,
        inspection.storage_authority,
        inspection.evidence_integrity,
    ) == expected
    assert _snapshot(store) == before
    assert "private-canary" not in repr(inspection)
    assert all("private-canary" not in issue.safe_message for issue in inspection.issues)


@pytest.mark.runtime
def test_source_fixed_exact_hit_and_cold_recovery_keep_complete_keys(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    logical = _values(case)
    first, second = logical.execute(), logical.execute()
    assert first.state.artifact_ref != second.state.artifact_ref
    assert first.to_pandas().equals(second.to_pandas())
    reduced = first.rollup().execute()
    assert reduced.to_pandas()["value"].tolist() == [180]
    count = len(case.session.runs().items)
    assert first.rollup().execute().state.artifact_ref == reduced.state.artifact_ref
    assert len(case.session.runs().items) == count
    case.database_path.rename(case.database_path.with_suffix(".offline"))
    source = """import json, sys
import marivo.analysis as mv
session = mv.session.resume(sys.argv[1], by="id")
value = session.artifact(sys.argv[2])
assert isinstance(value, mv.MaterializedNumericRelation)
assert value.to_pandas().set_index("member")["value"].to_dict() == {"A":60,"B":120,"C":0,"D":0}
assert value.rollup().execute().to_pandas().value.tolist() == [180]
assert session.revalidate(value.state.artifact_ref).storage_authority == "readable"
assert session._runtime.statistics.statements == []
print(json.dumps({"runs":len(session.runs().items)}))
"""
    env = os.environ.copy()
    env["MARIVO_PROJECT_ROOT"] = str(case.root)
    completed = subprocess.run(
        [sys.executable, "-c", source, case.session.id, first.state.artifact_ref.ref],
        cwd=case.root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["runs"] == count


@pytest.mark.parametrize("state", ("missing", "empty", "old"))
def test_read_factory_never_initializes_missing_or_unversioned_state(
    tmp_path: Path, state: str
) -> None:
    path = tmp_path / ".marivo/analysis/generations/v7/session_store.db"
    if state != "missing":
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as connection:
            if state == "old":
                connection.execute("PRAGMA user_version=6")
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    with pytest.raises(IntegrityError):
        SessionStore.open_existing(tmp_path)
    assert {
        str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before


@pytest.mark.runtime
def test_current_continuation_registry_resolves_to_actual_bound_methods(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from marivo.analysis._capabilities.registry import REGISTRY

    case = analysis_dsl_case_factory("j2")
    logical = _values(case)
    fixed = logical.execute()
    for value in (logical, fixed, fixed.rollup()):
        contract = value.contract()
        assert contract.actions
        for action in contract.actions:
            route = REGISTRY.resolve(action.help_target.removeprefix("analysis."))
            assert route.descriptor is not None
            method = action.call.split("(", 1)[0].rsplit(".", 1)[-1]
            if hasattr(value, method):
                assert REGISTRY.by_callable(getattr(value, method)) is route.descriptor
