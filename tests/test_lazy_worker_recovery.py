"""Exact file-description proofs across real parent death and cold recovery."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.materialization import resources
from marivo.analysis.materialization.contracts import RunDatasetInput
from marivo.analysis.materialization.errors import IntegrityError, RecoveryPendingError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.worker_lifetime import (
    acquire_worker_lifetime,
    reserve_worker,
    validate_worker_lifetime,
    worker_is_terminal,
)
from tests.lazy_materialization_fixtures import descriptor
from tests.lazy_worker_recovery_helper import CRASH_EXIT
from tests.test_lazy_adapter_runtime_acceptance import _manifest

_ROOT = Path(__file__).resolve().parents[1]
_HELPER = "tests.lazy_worker_recovery_helper"


def _admitted(project: Path) -> SessionStore:
    store = SessionStore(project)
    store.create_session("test", session_ref="session")
    value = descriptor()
    store.admit(
        "session",
        "e" * 64,
        RunDatasetInput(
            value.definition_fingerprint,
            value.row_contract.shape_id,
            value.row_contract_fingerprint,
            value.row_set_contract_fingerprint,
            ("session.population",),
            ("entity:sales.customers",),
        ),
        run_ref="run",
    )
    return store


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return {str(key): item for key, item in value.items()}


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _arguments(
    mode: str, project: Path, produced: dict[str, object] | None = None, *extra: str
) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        _HELPER,
        mode,
        str(project),
        *(
            ["--session", _text(produced["session"]), "--artifact", _text(produced["artifact"])]
            if produced
            else []
        ),
        *extra,
    ]


def _run(
    mode: str,
    project: Path,
    produced: dict[str, object] | None = None,
    *extra: str,
    exit_code: int = 0,
) -> dict[str, object]:
    process = subprocess.run(
        _arguments(mode, project, produced, *extra),
        cwd=_ROOT,
        env={**os.environ, "MARIVO_TELEMETRY": "off"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert process.returncode == exit_code, process.stdout + process.stderr
    return _object(json.loads(process.stdout))


def _evidence(name: str, payload: dict[str, object]) -> None:
    destination = os.environ.get("MARIVO_SLICE4C_EVIDENCE_DIR")
    if destination is None:
        return
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    (root / f"worker-{name}.json").write_text(
        json.dumps(
            {
                "kind": "real-runtime-worker-recovery",
                **payload,
            },
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def test_exact_lifetime_uses_open_description_and_never_parent_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    assert len(store.resources("session")) == 2 and not reservation.path.parent.exists()
    assert worker_is_terminal(store, reservation.execution)
    fd = acquire_worker_lifetime(reservation)
    duplicate = os.dup(fd)
    try:
        monkeypatch.setattr(
            os, "kill", lambda *args: pytest.fail("worker proof must not inspect a PID")
        )
        validate_worker_lifetime(fd, reservation.path, reservation.execution.ownership_nonce)
        assert not worker_is_terminal(store, reservation.execution)
        os.close(fd)
        fd = -1
        assert not worker_is_terminal(store, reservation.execution)
        with pytest.raises(RecoveryPendingError):
            resources.discharge_resources(store, store.resources("session"))
        assert reservation.path.read_text() == reservation.execution.ownership_nonce
        assert len(store.resources("session")) == 2
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(duplicate)
    assert worker_is_terminal(store, reservation.execution)
    resolved = resources.discharge_resources(store, store.resources("session"))
    assert len(resolved) == 2 and not reservation.path.parent.exists()
    # Filesystem cleanup can finish before the journal's durable discharge.
    # A retry must recognize that exact absent workspace without replaying work.
    assert len(store.resources("session")) == 2
    assert worker_is_terminal(store, reservation.execution)
    assert resources.discharge_resources(store, store.resources("session")) == resolved


def test_worker_rejects_unlocked_or_unrelated_descriptor(tmp_path: Path) -> None:
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    fd = acquire_worker_lifetime(reservation)
    unrelated = os.open(reservation.path, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            validate_worker_lifetime(
                unrelated, reservation.path, reservation.execution.ownership_nonce
            )
        os.close(fd)
        fd = -1
        with pytest.raises(ValueError, match="not locked"):
            validate_worker_lifetime(
                unrelated, reservation.path, reservation.execution.ownership_nonce
            )
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(unrelated)


@pytest.mark.parametrize("damage", ["nonce", "symlink", "locator", "hardlink"])
def test_recovery_rejects_foreign_or_corrupt_lifetime(tmp_path: Path, damage: str) -> None:
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    fd = acquire_worker_lifetime(reservation)
    os.close(fd)
    record = reservation.execution
    if damage == "nonce":
        reservation.path.write_text("foreign")
    elif damage == "symlink":
        reservation.path.unlink()
        reservation.path.symlink_to(tmp_path / "foreign")
    elif damage == "hardlink":
        os.link(reservation.path, tmp_path / "foreign")
    else:
        record = replace(
            record, safe_locator=record.safe_locator.replace(record.ownership_nonce, "0" * 32)
        )
    with pytest.raises(IntegrityError, match="worker lifetime"):
        worker_is_terminal(store, record)


def test_precreate_empty_file_and_harmless_cleanup_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    reservation.path.parent.mkdir(parents=True)
    reservation.path.touch()
    assert worker_is_terminal(store, reservation.execution)
    original = shutil.rmtree

    def denied(path: Path) -> None:
        raise PermissionError("injected harmless cleanup failure")

    monkeypatch.setattr(shutil, "rmtree", denied)
    resolved = resources.discharge_resources(store, store.resources("session"))
    assert resolved == (reservation.execution,)
    for item in resolved:
        store.discharge(item)
    assert store.resources("session") == (reservation.workspace,)
    monkeypatch.setattr(shutil, "rmtree", original)
    assert resources.discharge_resources(store, store.resources("session")) == (
        reservation.workspace,
    )
    assert not reservation.path.parent.exists()


@pytest.mark.parametrize(
    "point", ["local_worker_reserved", "lifetime_created", "local_worker_terminal"]
)
def test_cold_process_loss_at_worker_boundaries(tmp_path: Path, point: str) -> None:
    manifest = _manifest()
    produced = _run("produce", tmp_path)
    crashed = _run("crash", tmp_path, produced, "--point", point, exit_code=CRASH_EXIT)
    recovered = _run("recover", tmp_path, produced)
    assert crashed["pid"] != recovered["pid"]
    assert recovered["pending"] is False
    assert recovered["evidence"] == produced["evidence"]
    counts = _object(_object(recovered["snapshot"])["counts"])
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 2
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 1
    assert counts["action_resource_journal"] == 0
    assert not tuple(tmp_path.rglob("worker-*"))
    after = _manifest()
    assert after == manifest
    _evidence(
        f"cold-{point}",
        {
            "candidate_before": manifest,
            "candidate_after": after,
            "produced": produced,
            "crashed": crashed,
            "recovered": recovered,
        },
    )


def test_real_orphan_holds_only_its_session_until_worker_exits(tmp_path: Path) -> None:
    manifest = _manifest()
    produced = _run("produce", tmp_path)
    # macOS Unix socket paths have a short platform limit; use a private sibling
    # under /tmp, while all persisted Runtime state remains in tmp_path.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="marivo-worker-") as socket_dir:
        socket_path = str(Path(socket_dir) / "barrier")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.settimeout(30)
            listener.bind(socket_path)
            listener.listen(1)
            parent = subprocess.Popen(
                _arguments(
                    "crash", tmp_path, produced, "--point", "orphan", "--socket", socket_path
                ),
                cwd=_ROOT,
                env={**os.environ, "MARIVO_TELEMETRY": "off"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            peer: socket.socket | None = None
            try:
                peer, _ = listener.accept()
                peer.settimeout(30)
                child = _object(json.loads(peer.recv(4096)))
                assert child["pid"] != parent.pid
                parent.kill()
                parent.communicate(timeout=15)
                pending = _run("recover", tmp_path, produced)
                assert pending["pending"] is True
                assert (
                    pending["evidence"] == produced["evidence"]
                    and pending["rows"] == produced["rows"]
                )
                counts = _object(_object(pending["snapshot"])["counts"])
                assert counts["analysis_action_run_terminals"] == 1
                assert counts["action_resource_journal"] == 2
                independent = _run("independent", tmp_path)
                assert independent["session"] != produced["session"]
                assert _object(independent["statistics"])["primary_queries"] == 1
                peer.sendall(b"x")
                assert peer.recv(1) == b""
                peer.close()
                peer = None
                recovered = _run("recover", tmp_path, produced)
                assert recovered["pending"] is False
                assert recovered["evidence"] == produced["evidence"]
                counts = _object(_object(recovered["snapshot"])["counts"])
                assert (
                    counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 3
                )
                assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 2
                assert counts["action_resource_journal"] == 0
                assert not tuple(tmp_path.rglob("worker-*"))
                after = _manifest()
                assert after == manifest
                _evidence(
                    "orphan",
                    {
                        "candidate_before": manifest,
                        "candidate_after": after,
                        "producer_parent_pid": parent.pid,
                        "worker": child,
                        "produced": produced,
                        "pending": pending,
                        "independent": independent,
                        "recovered": recovered,
                    },
                )
            finally:
                if peer is not None:
                    with suppress(OSError):
                        peer.sendall(b"x")
                    peer.close()
                if parent.poll() is None:
                    parent.kill()
                parent.communicate(timeout=15)


def test_connection_relation_proof_is_exact_and_retires_after_its_own_discharge() -> None:
    execution = resources.backend_reservation("run_a", "domain_a")
    relation = replace(
        execution,
        resource_kind="planner_temporary_relation",
        safe_locator=execution.safe_locator + "/relation",
    )
    resources.prove_local_termination(execution)
    assert not resources.confirm_execution_termination(replace(relation, run_ref="run_b"))
    assert not resources.confirm_execution_termination(
        replace(relation, execution_domain_id="domain_b")
    )
    assert resources.confirm_execution_termination(relation)
    resources.forget_local_termination((execution,))
    assert not resources.confirm_execution_termination(execution)
    assert resources.confirm_execution_termination(relation)
    resources.forget_local_termination((relation,))
    assert not resources.confirm_execution_termination(relation)


def test_unresolved_input_thread_retains_description_until_it_really_ends(tmp_path: Path) -> None:
    import time
    from collections.abc import Iterator
    from threading import Event
    from threading import enumerate as threads

    import pyarrow as pa

    from marivo.analysis.materialization.local import LocalPolicy
    from marivo.analysis.materialization.local_worker import LocalRequest, StreamInput, supervise
    from marivo.analysis.observation.predicates import gt
    from tests.lazy_local_fixtures import REVENUE, row_call, setup_local

    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    request = LocalRequest(
        StreamInput(source.row_contract, source.row_set_contract),
        (row_call(source.where(gt(REVENUE, 0))),),
        LocalPolicy(),
        time.monotonic() + 30,
    )
    started, release = Event(), Event()
    terminal: list[bool] = []

    def batches() -> Iterator[pa.RecordBatch]:
        started.set()
        assert release.wait(30)
        yield pa.record_batch([pa.array([1])], names=["unused"])

    worker_code = (
        "import os, sys\nfrom multiprocessing.connection import Connection\n"
        "request = Connection(int(sys.argv[1])).recv()\nos._exit(3)\n"
    )
    try:
        with pytest.raises(RecoveryPendingError, match="transfer termination is unresolved"):
            supervise(
                request,
                batches(),
                cancel_source=lambda: None,
                lifetime=reservation,
                terminal=lambda: terminal.append(True),
                worker_code=worker_code,
            )
        assert started.is_set()
        assert terminal == []
        assert not worker_is_terminal(store, reservation.execution)
    finally:
        release.set()
        for thread in threads():
            if thread.name == "marivo-local-input":
                thread.join(timeout=5)
    assert worker_is_terminal(store, reservation.execution)


@pytest.mark.parametrize("boundary", ["open", "flock", "pread"])
def test_cold_proof_errors_have_no_raw_exception_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    store = _admitted(tmp_path)
    reservation = reserve_worker(store, "run", "session")
    fd = acquire_worker_lifetime(reservation)
    os.close(fd)
    canary = "private-worker-path-canary"

    def denied(*args: object, **kwargs: object) -> None:
        raise OSError(canary)

    if boundary == "flock":
        monkeypatch.setattr(fcntl, "flock", denied)
    else:
        monkeypatch.setattr(os, boundary, denied)
    with pytest.raises(IntegrityError) as caught:
        worker_is_terminal(store, reservation.execution)
    pending: list[BaseException] = [caught.value]
    seen: set[int] = set()
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        assert canary not in str(error)
        for linked in (error.__context__, error.__cause__):
            if linked is not None:
                pending.append(linked)
