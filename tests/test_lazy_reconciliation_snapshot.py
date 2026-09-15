"""Independent snapshot, integrity and proof-lifetime tests for guarded recovery."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization import reconciliation
from marivo.analysis.materialization.contracts import (
    ResourceRecord,
    RunDatasetInput,
    RunFailure,
    RunRecord,
)
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.resources import (
    backend_reservation,
    confirm_execution_termination,
    discharge_resources,
    prove_local_termination,
    reserve_output,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import S3Access
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.introspection.live.model import LiveHelpTarget
from tests.lazy_materialization_fixtures import descriptor


def _input() -> RunDatasetInput:
    value = descriptor()
    return RunDatasetInput(
        value.definition_fingerprint,
        value.row_contract.shape_id,
        value.row_contract_fingerprint,
        value.row_set_contract_fingerprint,
        ("session.population",),
        ("entity:sales.customers",),
    )


def _store(project: Path) -> SessionStore:
    store = SessionStore(project)
    store.create_session("selected", session_ref="session")
    store.admit("session", "e" * 64, _input(), run_ref="run")
    return store


def _failure() -> RunFailure:
    return RunFailure(
        "publication",
        "execution_failed",
        "safe",
        "run",
        "success",
        "error",
        AnalysisRepair(
            kind="retry",
            action="retry",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
        ),
    )


def test_recovery_entry_uses_one_snapshot_despite_a_separate_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    resource = backend_reservation("run", "duckdb@v1")
    store.reserve(resource)
    original = store._resources
    independent = SessionStore(tmp_path)

    def commit_between_reads(
        connection: sqlite3.Connection, session_ref: str
    ) -> tuple[ResourceRecord, ...]:
        # Deliberately bypass the guard to verify the SQLite snapshot itself.
        independent.fail("run", _failure(), resolved_resources=(resource,))
        return original(connection, session_ref)

    monkeypatch.setattr(store, "_resources", commit_between_reads)
    entries = store.recovery_snapshot("session")
    assert len(entries) == 1
    assert entries[0].run.lifecycle == "incomplete"
    assert entries[0].resources == (resource,)
    current = independent.run("run")
    assert current is not None and current.lifecycle == "failed"
    assert independent.resources("session") == ()


def test_recovery_does_not_decode_unrelated_session_or_unobligated_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.create_session("foreign", session_ref="foreign")
    store.admit("foreign", "f" * 64, _input(), run_ref="foreign_run")
    store.fail("foreign_run", _failure())
    with store._write() as connection:
        connection.execute(
            "UPDATE analysis_action_run_terminals SET failure_payload='invalid' WHERE run_ref='foreign_run'"
        )
    with pytest.raises(IntegrityError):
        store.run("foreign_run")
    decoded: list[str] = []
    original = store._run

    def selected(connection: sqlite3.Connection, run_ref: str) -> RunRecord | None:
        decoded.append(run_ref)
        return original(connection, run_ref)

    monkeypatch.setattr(store, "_run", selected)
    assert tuple(entry.run.run_ref for entry in store.recovery_snapshot("session")) == ("run",)
    assert decoded == ["run"]


def test_all_selected_metadata_is_validated_before_any_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.publish("run", "artifact", descriptor())
    store.admit("session", "f" * 64, _input(), run_ref="pending")
    # Simulate contradictory committed ownership after publication.
    with store._write() as connection:
        connection.execute(
            "INSERT INTO action_resource_journal VALUES(?,?,?,?,?,?)",
            (
                "run",
                "local_storage_staging",
                "local_parquet@v1",
                "artifact",
                "local_owned_path@v1",
                ".marivo/analysis/generations/v4/sessions/session/artifacts/artifact",
            ),
        )

    def forbidden(*args: object, **kwargs: object) -> tuple[ResourceRecord, ...]:
        pytest.fail("selected integrity must fail before touching any external resource")

    monkeypatch.setattr(reconciliation, "discharge_resources", forbidden)
    with (
        session_writer_guard(store.layout.lock_path("session")),
        pytest.raises(IntegrityError) as caught,
    ):
        reconciliation.reconcile_session(store, "session", event=lambda _: None)
    assert caught.value.stage == "reconciliation"
    assert caught.value.run_ref == "run"
    assert caught.value.expected is not None and "Session recovery" in caught.value.expected
    assert caught.value.received == "committed output remains a cleanup obligation"
    assert caught.value.repair is not None
    assert "Preserve" in caught.value.repair.action and "outputs" in caught.value.repair.action
    assert store.incomplete("session")[0].run_ref == "pending"


@pytest.mark.parametrize(
    ("corruption", "received", "run_ref"),
    (
        ("missing_session", "missing recovery Session", None),
        ("multiple_incomplete", "multiple incomplete Runs in one Session", "other"),
        ("partial_output", "incomplete producer has a committed output", "run"),
        ("missing_output", "succeeded Run has no Artifact", "run"),
        ("admission", "invalid JSON metadata", "run"),
        ("terminal", "invalid JSON metadata", "run"),
        ("artifact", "invalid JSON metadata", "run"),
        ("evidence", "incomplete or inconsistent Evidence summary", "run"),
        ("resource", "invalid safe_locator relation field", None),
    ),
)
def test_snapshot_integrity_errors_teach_recovery_without_external_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
    received: str,
    run_ref: str | None,
) -> None:
    store = _store(tmp_path)
    resource = backend_reservation("run", "duckdb@v1")
    store.reserve(resource)
    if corruption in {"partial_output", "missing_output", "artifact", "evidence"}:
        store.publish("run", "artifact", descriptor())
    elif corruption == "terminal":
        store.fail("run", _failure())

    # Corruption bypasses production writes deliberately, including FK checks.
    with sqlite3.connect(store.db_path) as connection:
        if corruption == "multiple_incomplete":
            connection.execute(
                "INSERT INTO analysis_action_runs SELECT 'other',session_ref,?,strftime('%Y-%m-%dT%H:%M:%fZ',admitted_at,'+1 second'),dataset_input_payload FROM analysis_action_runs WHERE run_ref='run'",
                ("f" * 64,),
            )
        elif corruption == "partial_output":
            connection.execute("DELETE FROM analysis_action_run_terminals WHERE run_ref='run'")
        elif corruption == "missing_output":
            connection.execute("DELETE FROM dataset_artifacts WHERE artifact_ref='artifact'")
        elif corruption == "admission":
            connection.execute(
                "UPDATE analysis_action_runs SET dataset_input_payload='invalid' WHERE run_ref='run'"
            )
        elif corruption == "terminal":
            connection.execute(
                "UPDATE analysis_action_run_terminals SET failure_payload='invalid' WHERE run_ref='run'"
            )
        elif corruption == "artifact":
            connection.execute(
                "UPDATE dataset_artifacts SET descriptor_payload='invalid' WHERE artifact_ref='artifact'"
            )
        elif corruption == "evidence":
            connection.execute("DELETE FROM dataset_evidence WHERE artifact_ref='artifact'")
        elif corruption == "resource":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE action_resource_journal SET safe_locator=''")
        before = tuple(connection.iterdump())

    def forbidden(*args: object, **kwargs: object) -> tuple[ResourceRecord, ...]:
        pytest.fail("invalid recovery metadata must prevent external proof or cleanup")

    monkeypatch.setattr(reconciliation, "discharge_resources", forbidden)
    selected = "missing" if corruption == "missing_session" else "session"
    with (
        session_writer_guard(store.layout.lock_path(selected)),
        pytest.raises(IntegrityError) as caught,
    ):
        reconciliation.reconcile_session(store, selected, event=lambda _: None)
    error = caught.value
    assert error.stage == "reconciliation"
    assert error.location == "dataset.reconciliation"
    assert error.run_ref == run_ref
    assert error.expected
    assert error.expected != "one supported complete canonical v3 metadata value"
    assert error.received == received
    assert error.repair is not None
    assert "recovery" in error.repair.action
    assert "Expected:" in str(error) and "Received:" in str(error) and "Repair:" in str(error)
    with sqlite3.connect(store.db_path) as connection:
        assert tuple(connection.iterdump()) == before


def test_snapshot_transaction_closes_before_resource_proof_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    staging, _, resources = reserve_output(
        store, run_ref="run", session_ref="session", artifact_ref="artifact_test", nonce="test"
    )
    staging.mkdir(parents=True)
    original_read = store._read
    original_discharge = discharge_resources
    active = 0

    @contextmanager
    def tracked_read() -> Iterator[sqlite3.Connection]:
        nonlocal active
        with original_read() as connection:
            active += 1
            try:
                yield connection
            finally:
                active -= 1

    def checked_discharge(
        selected: SessionStore,
        owned: tuple[ResourceRecord, ...],
        bindings: tuple[S3Access, ...],
    ) -> tuple[ResourceRecord, ...]:
        assert active == 0
        assert set(owned) == set(resources)
        return original_discharge(selected, owned, bindings)

    monkeypatch.setattr(store, "_read", tracked_read)
    monkeypatch.setattr(reconciliation, "discharge_resources", checked_discharge)
    with session_writer_guard(store.layout.lock_path("session")):
        reconciliation.reconcile_session(store, "session", event=lambda _: None)
    assert not staging.exists()
    assert store.resources("session") == ()
    run = store.run("run")
    assert run is not None and run.lifecycle == "failed"


def test_local_execution_proof_retires_only_after_durable_discharge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    resource = backend_reservation("run", "duckdb@v1")
    store.reserve(resource)
    prove_local_termination(resource)
    original = store._write

    @contextmanager
    def rollback() -> Iterator[sqlite3.Connection]:
        with original() as connection:
            yield connection
            raise OSError("injected journal rollback")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", rollback)
        with pytest.raises(OSError):
            store.discharge(resource)
    assert store.resources("session") == (resource,)
    assert confirm_execution_termination(resource)
    store.discharge(resource)
    assert store.resources("session") == ()
    assert not confirm_execution_termination(resource)


@pytest.mark.parametrize("selection", ("missing", "foreign", "succeeded", "failed"))
def test_exact_run_selection_is_checked_by_reconciliation_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    store = _store(tmp_path)
    if selection == "foreign":
        store.create_session("foreign", session_ref="foreign")
        store.admit("foreign", "f" * 64, _input(), run_ref="selected")
    elif selection == "succeeded":
        store.publish("run", "artifact", descriptor())
        store.admit("session", "f" * 64, _input(), run_ref="pending")
    elif selection == "failed":
        store.fail("run", _failure())
        store.admit("session", "f" * 64, _input(), run_ref="pending")
    run_ref = (
        "missing" if selection == "missing" else "selected" if selection == "foreign" else "run"
    )
    with sqlite3.connect(store.db_path) as connection:
        before = tuple(connection.iterdump())

    def forbidden(*args: object, **kwargs: object) -> tuple[ResourceRecord, ...]:
        pytest.fail("invalid or completed selection must not touch external resources")

    monkeypatch.setattr(reconciliation, "discharge_resources", forbidden)
    with session_writer_guard(store.layout.lock_path("session")):
        if selection == "failed":
            reconciliation.reconcile_session(
                store, "session", event=lambda _: None, run_ref=run_ref
            )
        else:
            with pytest.raises(MaterializationError) as caught:
                reconciliation.reconcile_session(
                    store, "session", event=lambda _: None, run_ref=run_ref
                )
            assert caught.value.stage == "reconciliation"
            assert caught.value.run_ref == run_ref
            assert caught.value.expected and caught.value.received
            assert caught.value.repair is not None
    with sqlite3.connect(store.db_path) as connection:
        assert tuple(connection.iterdump()) == before
