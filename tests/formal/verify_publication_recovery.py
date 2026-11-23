"""Finite publication protocol checks against real SQLite and Parquet state."""

import json
import select
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, NoReturn

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization import terminal_publication, terminal_store
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.model.artifact_data import ArtifactManifest
from marivo.analysis.model.execution_errors import MaterializationError, RecoveryPendingError
from marivo.analysis.session.core import Session
from marivo.datasource.runtime.adapters import SourceSession
from tests.analysis.materialization.terminal_recovery_worker import method_result
from tests.formal._recovery_reader import snapshot


@dataclass(frozen=True)
class _Run:
    id: str
    session: str
    invocation: str
    outcome: str | None
    artifact: str | None


@dataclass(frozen=True)
class _Artifact:
    id: str
    session: str
    invocation: str
    descriptor: str


@dataclass(frozen=True)
class _State:
    runs: frozenset[_Run]
    artifacts: frozenset[_Artifact]
    resources: frozenset[tuple[str, str]]
    files: frozenset[Path]


def _observe(session: Session) -> _State:
    """An independent reader observes durable state, including hidden orphan rows."""
    store = session._runtime.store
    with closing(sqlite3.connect(store.db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        runs = frozenset(
            _Run(*row)
            for row in conn.execute(
                """SELECT r.run_ref, r.session_ref, r.invocation_id, t.outcome, t.output_artifact_ref
                FROM analysis_action_runs r LEFT JOIN analysis_action_run_terminals t
                ON r.run_ref=t.run_ref AND r.session_ref=t.session_ref"""
            )
        )
        artifacts = frozenset(
            _Artifact(*row)
            for row in conn.execute(
                "SELECT artifact_ref, session_ref, invocation_id, descriptor_payload FROM dataset_artifacts"
            )
        )
        resources = frozenset(
            (run, path)
            for run, path in conn.execute(
                "SELECT run_ref,safe_locator FROM action_resource_journal"
            )
        )
    files = frozenset(
        path.relative_to(store.project_root)
        for path in (store.layout.generation_dir / "sessions").rglob("*.parquet")
    )
    return _State(runs, artifacts, resources, files)


_Location = Literal["none", "staging", "final"]


@dataclass(frozen=True)
class _Cut:
    event: str
    files: int
    location: _Location
    journal: int
    committed: bool = False


# Reader-side specification. SQL inserts and journal deletion do not become
# observable one at a time. The small completed fixture owns two Parquet files.
_CUTS = (
    _Cut("publication_admitted", 0, "none", 0),
    _Cut("terminal_primary_written", 1, "staging", 2),
    _Cut("terminal_method_written", 2, "staging", 2),
    _Cut("terminal_files_published", 2, "final", 2),
    _Cut("insert_artifact", 2, "final", 2),
    _Cut("insert_terminal", 2, "final", 2),
    _Cut("before_commit", 2, "final", 2),
    _Cut("after_commit", 2, "final", 0, committed=True),
)
_ATTACHMENT = _Cut("terminal_attachment_written", 2, "staging", 2)
_ROLLED_BACK = _Cut("reconciled rollback", 0, "none", 0)


def _check_delta(
    actual: _State, baseline: _State, session: Session, cut: _Cut, *, failed: bool = False
) -> _Run:
    context = cut.event
    assert baseline.runs <= actual.runs and baseline.artifacts <= actual.artifacts, context
    assert baseline.files <= actual.files and baseline.resources <= actual.resources, context
    new_runs = actual.runs - baseline.runs
    assert len(new_runs) == 1, (context, new_runs)
    (run,) = new_runs
    assert run.session == session.id, (context, run)
    records = actual.artifacts - baseline.artifacts
    assert len(records) == int(cut.committed), (context, records)
    if cut.committed:
        (artifact,) = records
        assert (run.outcome, run.artifact, run.session, run.invocation) == (
            "succeeded",
            artifact.id,
            artifact.session,
            artifact.invocation,
        ), (context, run, artifact)
    else:
        assert (run.outcome, run.artifact) == ("failed" if failed else None, None), (context, run)
    journal = actual.resources - baseline.resources
    assert len(journal) == cut.journal and all(owner == run.id for owner, _ in journal), (
        context,
        journal,
    )
    files = actual.files - baseline.files
    assert len(files) == cut.files, (context, files)
    store = session._runtime.store
    if cut.location == "staging":
        parent = store.layout.run_dir(session.id, run.id).relative_to(store.project_root)
        assert all(path.is_relative_to(parent) for path in files), (context, files)
    if cut.location == "final":
        parent = store.layout.session_dir(session.id).relative_to(store.project_root) / "artifacts"
        targets = {path.relative_to(parent).parts[0] for path in files}
        assert len(targets) == 1, (context, files)
        if cut.committed:
            assert targets == {run.artifact}, (context, targets, run)
        else:
            assert (run.id, str(parent / next(iter(targets)))) in journal, (context, journal)
    return run


@dataclass(frozen=True)
class _Publication:
    session: Session
    prior: mv.AnalysisFrame
    peer: mv.AnalysisFrame
    initial: _State


def _literal(session: Session, value: int) -> mv.AnalysisFrame:
    return session.values(columns={"answer": None}, rows=(((), {"answer": value}),))


@pytest.fixture
def publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Publication:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("formal-publication", timezone="UTC")
    peer = mv.session.get_or_create("formal-unrelated", timezone="UTC")
    prior = _literal(session, 7).persist()
    other = _literal(peer, 17).persist()
    return _Publication(session, prior, other, _observe(session))


def _memory(session: Session, *, calendar: bool = False) -> mv.AnalysisFrame:
    if calendar:
        days = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
        return session.frame(mv.product(days, mv.coordinates(by=(), keys=((),)))).compute()
    return mv.AnalysisFrame._from_memory(session._runtime.local, method_result())


def _prior_readable(case: _Publication) -> None:
    assert case.prior.to_pandas()["answer"].tolist() == [7]
    assert case.peer.to_pandas()["answer"].tolist() == [17]


@pytest.mark.parametrize("cut", (*_CUTS, _ATTACHMENT), ids=lambda cut: cut.event)
def test_publication_cutpoints_are_atomic_to_an_independent_reader(
    publication: _Publication, monkeypatch: pytest.MonkeyPatch, cut: _Cut
) -> None:
    case = publication
    session = case.session
    memory = _memory(session, calendar=cut == _ATTACHMENT)
    table = memory.to_pandas()
    observed: list[_State] = []

    def fault(event: str) -> None:
        if event == cut.event:
            observed.append(_observe(session))
            raise OSError(event)

    monkeypatch.setattr(terminal_publication, "_event", fault)
    stored = None
    failure = None
    if cut.committed:
        stored = memory.persist(description="formal candidate")
        assert stored.description == "formal candidate"
        pd.testing.assert_frame_equal(stored.to_pandas(), table)
    else:
        with pytest.raises(MaterializationError) as error:
            memory.persist()
        failure = error.value
    (at_cut,) = observed
    original = _check_delta(at_cut, case.initial, session, cut)
    if cut.committed:
        assert stored is not None
        assert stored.artifact_id == original.artifact
        final = _check_delta(_observe(session), case.initial, session, cut)
    else:
        assert failure is not None
        assert not isinstance(failure, RecoveryPendingError)
        final = _check_delta(_observe(session), case.initial, session, _ROLLED_BACK, failed=True)
    assert original.id == final.id
    pd.testing.assert_frame_equal(memory.to_pandas(), table)
    _prior_readable(case)


def _forbid(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Recovery must not bind a source, evaluate, or publish")


def _reopen(case: _Publication, monkeypatch: pytest.MonkeyPatch, outcome: _Run) -> mv.Session:
    with monkeypatch.context() as patch:
        patch.setattr(Session, "catalog", property(_forbid))
        patch.setattr(SourceSession, "bind", _forbid)
        patch.setattr(SourceSession, "execute", _forbid)
        patch.setattr(mv.AnalysisFrame, "compute", _forbid)
        patch.setattr(terminal_publication, "publish_completed", _forbid)
        resumed = mv.session.resume(case.session.id)
        summaries = resumed.persisted_frames().items
        assert {item.artifact_id for item in summaries} == {
            case.prior.artifact_id,
            *((outcome.artifact,) if outcome.artifact is not None else ()),
        }
        if outcome.artifact is not None:
            stored = resumed.persisted_frame(outcome.artifact)
            assert stored.artifact_id == outcome.artifact
            assert not stored.to_pandas().empty
        return resumed


def _cold_reopen(case: _Publication, expected: dict[str, str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.formal._recovery_reader",
            str(case.session.project_root),
            case.session.id,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize(
    "cut,readback",
    ((_CUTS[-2], "unavailable"), (_CUTS[-1], "unavailable"), (_CUTS[-1], "contradictory")),
    ids=("rollback-unknown", "commit-unknown", "descriptor-mismatch"),
)
def test_unknown_ack_and_failed_readback_reconcile_the_same_run(
    publication: _Publication,
    monkeypatch: pytest.MonkeyPatch,
    cut: _Cut,
    readback: Literal["unavailable", "contradictory"],
) -> None:
    case = publication
    session = case.session
    memory = _memory(session, calendar=True)
    data = memory.to_pandas()
    observed: list[_State] = []

    def unavailable() -> NoReturn:
        raise OSError("Read-back unavailable")

    manifest = terminal_store.manifest

    def contradictory(
        store: SessionStore, conn: sqlite3.Connection, ref: str
    ) -> ArtifactManifest | None:
        value = manifest(store, conn, ref)
        assert value is not None
        return replace(value, description="Different complete descriptor")

    with monkeypatch.context() as patch:

        def fault(event: str) -> None:
            if event == cut.event:
                observed.append(_observe(session))
                if readback == "unavailable":
                    patch.setattr(session._runtime.store, "read_transaction", unavailable)
                else:
                    patch.setattr(terminal_store, "manifest", contradictory)
                raise OSError("Commit acknowledgement lost")

        patch.setattr(terminal_publication, "_event", fault)
        with pytest.raises(RecoveryPendingError) as error:
            memory.persist(description="unknown acknowledgement")
        assert error.value.stage == "terminal_commit_unknown"

    (at_cut,) = observed
    original = _check_delta(at_cut, case.initial, session, cut)
    unresolved = _observe(session)
    assert _check_delta(unresolved, case.initial, session, cut) == original
    expected = {case.prior.artifact_id: snapshot(case.prior)}
    if original.artifact is not None:
        expected[original.artifact] = snapshot(memory)
    # The first reconciliation must happen without any parent-process buffers.
    _cold_reopen(case, expected)
    resumed = _reopen(case, monkeypatch, original)
    settled = _observe(resumed)
    terminal = _check_delta(
        settled,
        case.initial,
        resumed,
        cut if cut.committed else _ROLLED_BACK,
        failed=not cut.committed,
    )
    assert terminal.id == original.id and terminal.invocation == original.invocation
    if original.artifact is not None:
        assert settled == unresolved
        persisted = resumed.persisted_frame(original.artifact)
        assert persisted.description == "unknown acknowledgement"
        pd.testing.assert_frame_equal(persisted.to_pandas(), data)
    _reopen(case, monkeypatch, terminal)
    _cold_reopen(case, expected)
    assert _observe(resumed) == settled
    _prior_readable(case)


@pytest.mark.parametrize("cut", _CUTS[-2:], ids=lambda cut: cut.event)
def test_process_death_at_commit_boundary_is_reconciled(
    publication: _Publication, monkeypatch: pytest.MonkeyPatch, cut: _Cut
) -> None:
    case = publication
    session = case.session
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.analysis.materialization.terminal_recovery_worker",
            str(session.project_root),
            session.id,
            cut.event,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 37, (result.stdout, result.stderr)
    cut_state = _observe(session)
    original = _check_delta(cut_state, case.initial, session, cut)
    expected = {case.prior.artifact_id: snapshot(case.prior)}
    if original.artifact is not None:
        expected[original.artifact] = snapshot(_memory(session))
    _cold_reopen(case, expected)
    resumed = _reopen(case, monkeypatch, original)
    resolved = _observe(resumed)
    terminal = _check_delta(
        resolved,
        case.initial,
        resumed,
        cut if cut.committed else _ROLLED_BACK,
        failed=not cut.committed,
    )
    assert terminal.id == original.id and terminal.artifact == original.artifact
    if terminal.artifact is not None:
        assert resolved == cut_state
        saved = resumed.persisted_frame(terminal.artifact)
        assert saved.to_pandas()["detail.absolute.q"].tolist() == [60]
        assert len(saved.method_facts) == 1
    _cold_reopen(case, expected)
    assert _observe(session) == resolved
    _prior_readable(case)


def test_failed_cleanup_retains_its_obligation_until_a_later_cold_resume(
    publication: _Publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = publication
    memory = _memory(case.session)
    attempted: list[Path] = []

    def fault(event: str) -> None:
        if event == "before_commit":
            raise OSError("Commit acknowledgement lost before commit")

    def unavailable(path: Path) -> NoReturn:
        attempted.append(path)
        raise OSError("Owned path temporarily cannot be removed")

    with monkeypatch.context() as patch:
        patch.setattr(terminal_publication, "_event", fault)
        patch.setattr(shutil, "rmtree", unavailable)
        with pytest.raises(MaterializationError):
            memory.persist()
        pending = _observe(case.session)
        run = _check_delta(
            pending,
            case.initial,
            case.session,
            _Cut("cleanup pending", 2, "final", 1),
            failed=True,
        )
        assert attempted
        assert {str(path.relative_to(case.session.project_root)) for path in attempted} == {
            path for _, path in pending.resources - case.initial.resources
        }
        _reopen(case, monkeypatch, run)
        assert _observe(case.session) == pending
    _cold_reopen(case, {case.prior.artifact_id: snapshot(case.prior)})
    settled = _observe(case.session)
    assert _check_delta(settled, case.initial, case.session, _ROLLED_BACK, failed=True) == run
    _cold_reopen(case, {case.prior.artifact_id: snapshot(case.prior)})
    assert _observe(case.session) == settled
    _prior_readable(case)


def _process_command(session: Session, role: str, *args: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tests.formal._publication_process",
        str(session.project_root),
        session.id,
        role,
        *args,
    ]


@contextmanager
def _publication_process(
    session: Session, role: str, log: Path, *args: str
) -> Iterator[subprocess.Popen[str]]:
    with (
        log.open("w") as errors,
        subprocess.Popen(
            _process_command(session, role, *args),
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
        ) as process,
    ):
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=45)


def _reply(process: subprocess.Popen[str], log: Path, request: str | None = None) -> object:
    assert process.stdin is not None and process.stdout is not None
    if request is not None:
        process.stdin.write(request + "\n")
        process.stdin.flush()
    ready, _, _ = select.select([process.stdout], [], [], 120)
    assert ready, ("subprocess did not reply", log.read_text())
    line = process.stdout.readline()
    assert line, ("subprocess exited before reply", process.poll(), log.read_text())
    return json.loads(line)


def _recover_process(session: Session) -> object:
    result = subprocess.run(
        _process_command(session, "recover"),
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "committed,crash",
    ((False, True), (True, True), (False, False)),
    ids=("rollback", "commit-before-crash", "commit-during-read"),
)
def test_live_writer_reader_and_recovery_interleave(
    publication: _Publication, tmp_path: Path, committed: bool, crash: bool
) -> None:
    case = publication
    session = case.session
    event = "after_commit" if committed else "insert_terminal"
    cut = _Cut(event, 1, "final", 0 if committed else 2, committed)
    reader_log, writer_log = tmp_path / "reader.log", tmp_path / "writer.log"
    old = {case.prior.artifact_id: [7]}
    with _publication_process(session, "reader", reader_log) as reader:
        assert _reply(reader, reader_log) == "ready"
        with _publication_process(session, "publish", writer_log, event) as writer:
            assert _reply(writer, writer_log) == event
            paused = _observe(session)
            original = _check_delta(paused, case.initial, session, cut)
            visible = {**old, original.artifact: [31]} if original.artifact is not None else old
            assert _reply(reader, reader_log, "read") == visible
            # Keep a real read transaction open from directory selection
            # through publisher exit and the first cold recovery.
            assert _reply(reader, reader_log, "read-paused") == "snapshot"
            reader_snapshot = visible
            assert _recover_process(session) == "busy"
            assert _observe(session) == paused
            if crash:
                writer.kill()
                assert writer.wait(timeout=45) < 0
            else:
                artifact = _reply(writer, writer_log, "continue")
                assert isinstance(artifact, str) and artifact not in old
                visible = {**old, artifact: [31]}
                assert writer.wait(timeout=45) == 0
        assert _recover_process(session) == visible
        assert _reply(reader, reader_log, "continue") == reader_snapshot
        settled = _observe(session)
        rolled_back = crash and not committed
        terminal = _check_delta(
            settled,
            case.initial,
            session,
            _ROLLED_BACK if rolled_back else _Cut("settled", 1, "final", 0, committed=True),
            failed=rolled_back,
        )
        assert (terminal.id, terminal.invocation) == (original.id, original.invocation)
        if committed:
            assert settled == paused
        assert _recover_process(session) == visible
        assert _reply(reader, reader_log, "read") == visible
        assert _observe(session) == settled
    _prior_readable(case)
