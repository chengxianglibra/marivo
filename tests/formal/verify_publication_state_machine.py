"""Explore publication interleavings and replay observable cuts against the Store."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from subprocess import Popen
from typing import Literal, TypeVar

import pytest

from marivo.analysis.materialization import terminal_publication
from marivo.analysis.model.execution_errors import MaterializationError, SessionBusyError
from tests.formal import verify_publication_recovery as harness
from tests.formal.verify_publication_recovery import (
    _observe,
    _prior_readable,
    _Publication,
    _publication_process,
    _reply,
)

publication = harness.publication

_RunState = Literal["absent", "incomplete", "failed", "succeeded"]
_Mutation = Literal["journal", "failed"]
_Path = Literal["staging", "final"]
_Paths = frozenset[_Path]
_OWNER_RECOVERY = 2
_LOCKED = -2
_T = TypeVar("_T")
_EMPTY: _Paths = frozenset()
_STAGING: _Paths = frozenset({"staging"})
_FINAL: _Paths = frozenset({"final"})
_EVENTS = (
    "publication_admitted",
    "reserve_staging",
    "reserve_final",
    "terminal_primary_directory_created",
    "terminal_primary_written",
    "terminal_method_written",
    "terminal_files_published",
    "insert_artifact",
    "insert_terminal",
    "before_commit",
    "after_commit",
    "writer_release",
)


@dataclass(frozen=True)
class _Run:
    terminal: _RunState = "absent"
    manifest: bool = False
    journal: _Paths = _EMPTY
    directories: _Paths = _EMPTY
    staging: int = 0
    final: int = 0


@dataclass(frozen=True)
class _Recovery:
    todo: tuple[int, ...]
    tried: _Paths = _EMPTY
    resolved: _Paths = _EMPTY


@dataclass(frozen=True)
class _State:
    runs: tuple[_Run, ...] = (_Run(), _Run())
    writer_steps: tuple[int, ...] = (-1, -1)
    lock: int | None = None
    order: tuple[int, ...] = ()
    recovery: _Recovery | None = None
    reader: frozenset[int] | None = None
    # Separate existing commits for the selected Session and an unrelated peer.
    prior: tuple[int, int] = (7, 17)


@dataclass(frozen=True)
class _Edge:
    action: str
    after: _State
    actor: int | None = None


_Trace = tuple[_Edge, ...]
_INITIAL = _State()


def _put(items: tuple[_T, ...], index: int, item: _T) -> tuple[_T, ...]:
    return (*items[:index], item, *items[index + 1 :])


def _durable(state: _State) -> frozenset[int]:
    return frozenset(index for index, run in enumerate(state.runs) if run.manifest)


def _pending(state: _State) -> tuple[int, ...]:
    return tuple(
        index
        for index in state.order
        if state.runs[index].terminal == "incomplete" or state.runs[index].journal
    )


def _update_run(state: _State, actor: int, run: _Run) -> _State:
    return replace(state, runs=_put(state.runs, actor, run))


def _advance_writer(state: _State, actor: int) -> _Edge:
    step = max(0, state.writer_steps[actor] + 1)
    event = _EVENTS[step]
    run = state.runs[actor]
    match event:
        case "publication_admitted":
            run = replace(run, terminal="incomplete")
            state = replace(state, order=(*state.order, actor))
        case "reserve_staging":
            run = replace(run, journal=_STAGING)
        case "reserve_final":
            run = replace(run, journal=_STAGING | _FINAL)
        case "terminal_primary_directory_created":
            run = replace(run, directories=_STAGING)
        case "terminal_primary_written":
            run = replace(run, staging=1)
        case "terminal_method_written":
            run = replace(run, staging=2)
        case "terminal_files_published":
            run = replace(run, directories=_FINAL, staging=0, final=run.staging)
        case "after_commit":
            run = replace(run, terminal="succeeded", manifest=True, journal=_EMPTY)
        case "writer_release":
            state = replace(state, lock=None)
    state = _update_run(state, actor, run)
    return _Edge(event, replace(state, writer_steps=_put(state.writer_steps, actor, step)), actor)


def _cleanup(state: _State, actor: int, path: _Path, *, failed: bool) -> _Edge:
    assert state.recovery is not None
    recovery = state.recovery
    if not failed:
        run = state.runs[actor]
        cleared = replace(run, staging=0) if path == "staging" else replace(run, final=0)
        state = _update_run(state, actor, replace(cleared, directories=run.directories - {path}))
    recovery = replace(
        recovery,
        tried=recovery.tried | {path},
        resolved=recovery.resolved | (set() if failed else {path}),
    )
    label = "cleanup_blocked" if failed else "cleanup_resolved"
    return _Edge(f"{label}_{path}", replace(state, recovery=recovery), actor)


def _finish_run(state: _State, actor: int, mutation: _Mutation | None) -> _Edge:
    assert state.recovery is not None
    run = state.runs[actor]
    if run.terminal == "incomplete" and mutation != "failed":
        run = replace(run, terminal="failed")
    recovery = state.recovery
    resolved = run.journal if mutation == "journal" else recovery.resolved
    run = replace(run, journal=run.journal - resolved)
    state = _update_run(state, actor, run)
    return _Edge("recover_run", replace(state, recovery=_Recovery(recovery.todo[1:])), actor)


def _recovery_steps(
    state: _State, mutation: _Mutation | None, *, allow_failure: bool = True
) -> Iterator[_Edge]:
    recovery = state.recovery
    assert recovery is not None
    if not recovery.todo:
        cold = state.lock == _OWNER_RECOVERY
        event = "recovery_complete" if cold else "writer_reconciliation_complete"
        yield _Edge(event, replace(state, lock=None if cold else state.lock, recovery=None))
        return
    actor = recovery.todo[0]
    run = state.runs[actor]
    paths = run.journal - recovery.tried
    if not paths:
        yield _finish_run(state, actor, mutation)
        return
    # The persisted journal is ordered by safe locator (artifacts before runs).
    path = min(paths)
    yield _cleanup(state, actor, path, failed=False)
    if allow_failure and path in run.directories:
        yield _cleanup(state, actor, path, failed=True)


def _edges(state: _State, mutation: _Mutation | None = None) -> Iterator[_Edge]:
    if state.reader is None:
        yield _Edge("reader_begin", replace(state, reader=_durable(state)))
    else:
        yield _Edge("reader_finish", replace(state, reader=None))
    if state.lock is None:
        yield _Edge(
            "recovery_start",
            replace(state, lock=_OWNER_RECOVERY, recovery=_Recovery(_pending(state))),
        )
        for actor, step in enumerate(state.writer_steps):
            if step == -1:
                pending = _pending(state)
                yield _Edge(
                    "writer_start",
                    replace(
                        state,
                        lock=actor,
                        writer_steps=_put(state.writer_steps, actor, _LOCKED),
                        recovery=_Recovery(pending) if pending else None,
                    ),
                    actor,
                )
        return
    yield _Edge("contender_busy", state)
    if state.recovery is not None:
        lock = state.lock
        cold = lock == _OWNER_RECOVERY
        yield _Edge(
            "recovery_crash" if cold else "writer_reconciliation_crash",
            replace(
                state,
                lock=None,
                recovery=None,
                writer_steps=state.writer_steps if cold else _put(state.writer_steps, lock, -1),
            ),
        )
        yield from _recovery_steps(state, mutation)
        return
    actor = state.lock
    yield _advance_writer(state, actor)
    # A crash before admission has no durable identity or uncommitted artifacts.
    step = -1 if state.runs[actor].terminal == "absent" else len(_EVENTS) - 1
    yield _Edge(
        "writer_crash",
        replace(state, lock=None, writer_steps=_put(state.writer_steps, actor, step)),
        actor,
    )


def _violations(before: _State, edge: _Edge) -> tuple[str, ...]:
    after = edge.after
    errors = []
    if any(
        run.manifest != (run.terminal == "succeeded")
        or (
            run.manifest
            and (run.directories != _FINAL or run.final != 2 or run.staging != 0 or run.journal)
        )
        for run in after.runs
    ) or not _snapshot_unchanged(before, edge):
        errors.append("I1 atomic visibility")
    if before.prior != after.prior or any(
        previous.manifest and previous != current
        for previous, current in zip(before.runs, after.runs, strict=True)
    ):
        errors.append("I2 existing commits immutable")
    if any(
        (not run.manifest and not run.directories <= run.journal)
        or (run.staging > 0 and "staging" not in run.directories)
        or (run.final > 0 and "final" not in run.directories)
        for run in after.runs
    ):
        errors.append("I3 cleanup ownership")
    if (
        edge.action == "recover_run"
        and edge.actor is not None
        and (after.runs[edge.actor].terminal == "incomplete")
    ):
        errors.append("I4 original Run must settle")
    if not _exclusive(before, edge):
        errors.append("I5 writer exclusion")
    return tuple(errors)


def _snapshot_unchanged(before: _State, edge: _Edge) -> bool:
    if edge.action == "reader_begin":
        return edge.after.reader == _durable(before)
    if edge.action == "reader_finish":
        return edge.after.reader is None
    return edge.after.reader == before.reader


def _exclusive(before: _State, edge: _Edge) -> bool:
    after = edge.after
    active = tuple(
        index
        for index, step in enumerate(after.writer_steps)
        if step != -1 and step < len(_EVENTS) - 1
    )
    writer = (after.lock,) if after.lock in (0, 1) else ()
    if active != writer:
        return False
    if after.lock == _OWNER_RECOVERY and after.recovery is None:
        return False
    if after.recovery is not None and after.lock is None:
        return False
    return edge.action != "contender_busy" or (before.lock is not None and before == after)


def _healthy_recovery(state: _State) -> _State:
    assert state.lock is None
    state = replace(state, lock=_OWNER_RECOVERY, recovery=_Recovery(_pending(state)))
    # Each run needs at most two path resolutions and one Run terminal update.
    for _ in range(7):
        (edge,) = _recovery_steps(state, None, allow_failure=False)
        state = edge.after
        if state.lock is None:
            return state
    raise AssertionError("I4 healthy recovery failed to stabilize")


def _stable(state: _State) -> None:
    result = _healthy_recovery(state)
    expected = tuple(
        _Run(terminal="failed") if run.terminal in ("incomplete", "failed") else run
        for run in state.runs
    )
    assert result == replace(state, runs=expected), ("I4 stabilization", state, result)
    assert _healthy_recovery(result) == result, ("I4 repeated recovery differs", result)


@dataclass(frozen=True)
class _Counterexample:
    invariant: str
    trace: _Trace


@dataclass
class _Exploration:
    paths: dict[_State, _Trace]
    edges: int = 0
    violation: _Counterexample | None = None


def _explore(mutation: _Mutation | None = None) -> _Exploration:
    search = _Exploration({_INITIAL: ()})
    pending = deque([_INITIAL])
    while pending:
        state = pending.popleft()
        trace = search.paths[state]
        if mutation is None and state.lock is None:
            _stable(state)
        for edge in _edges(state, mutation):
            search.edges += 1
            violations = _violations(state, edge)
            if violations:
                search.violation = _Counterexample(violations[0], (*trace, edge))
                return search
            if edge.after not in search.paths:
                search.paths[edge.after] = (*trace, edge)
                pending.append(edge.after)
    return search


def test_all_bounded_publication_states_satisfy_invariants() -> None:
    result = _explore()
    assert result.violation is None, result.violation
    assert any(all(run.manifest for run in state.runs) for state in result.paths)
    assert any(state.lock in (0, 1) and state.recovery is not None for state in result.paths)
    assert any(state.reader == frozenset() and _durable(state) for state in result.paths)
    print(f"Reachability: {len(result.paths)} states, {result.edges} edges")


@pytest.mark.parametrize(
    ("mutation", "invariant"),
    [
        ("journal", "I3 cleanup ownership"),
        ("failed", "I4 original Run must settle"),
    ],
)
def test_protocol_mutations_find_shortest_counterexample(
    mutation: _Mutation, invariant: str
) -> None:
    result = _explore(mutation).violation
    assert result is not None and result.invariant == invariant, result
    print(f"{invariant}: {' -> '.join(edge.action for edge in result.trace)}")


def _writer_prefix(cut: str, *, initial: _State = _INITIAL, actor: int = 0) -> _Trace:
    # Select an observable checkpoint, then extract its shortest legal prefix.
    pending: deque[tuple[_State, _Trace]] = deque([(initial, ())])
    seen = {initial}
    while pending:
        state, trace = pending.popleft()
        for edge in _edges(state):
            if edge.actor != actor or edge.action in ("writer_crash", "recover_run"):
                continue
            path = (*trace, edge)
            if edge.action == cut:
                return path
            if edge.after not in seen:
                seen.add(edge.after)
                pending.append((edge.after, path))
    raise AssertionError(f"Unreachable publication cut: {cut}")


@dataclass
class _Replay:
    case: _Publication
    state: _State = _INITIAL
    history: list[str] = field(default_factory=list)
    owners: dict[int, str] = field(default_factory=dict)
    owned_paths: dict[tuple[int, _Path], Path] = field(default_factory=dict)
    invocations: dict[int, str] = field(default_factory=dict)

    def step(self, action: str, actor: int | None = None) -> None:
        (edge,) = tuple(
            edge for edge in _edges(self.state) if edge.action == action and edge.actor == actor
        )
        self.history.append(action)
        self.state = edge.after

    def compare(self) -> None:
        actual = _observe(self.case.session)
        initial = self.case.initial
        context = f"Observed-model disagreement after {' -> '.join(self.history)}"
        assert initial.runs <= actual.runs and initial.artifacts <= actual.artifacts, context
        assert initial.files <= actual.files and initial.resources <= actual.resources, context
        runs = {run.id: run for run in actual.runs - initial.runs}
        assert len(runs) == len(self.state.order), (context, self.state.runs, runs)
        assert {owner for owner, _ in actual.resources - initial.resources} <= runs.keys(), context
        fresh = runs.keys() - self.owners.values()
        if fresh:
            (run_id,) = fresh
            self.owners[self.state.order[-1]] = run_id
        artifacts = {item.id: item for item in actual.artifacts - initial.artifacts}
        classified: set[Path] = set()
        layout = self.case.session._runtime.store.layout
        for actor, run_id in self.owners.items():
            run = runs[run_id]
            assert run.session == self.case.session.id, (context, run)
            assert run.invocation == self.invocations.setdefault(actor, run.invocation), (
                context,
                run,
            )
            expected = self.state.runs[actor]
            staging = layout.run_dir(run.session, run.id).relative_to(layout.project_root)
            final_root = (
                layout.session_dir(run.session).relative_to(layout.project_root) / "artifacts"
            )
            resources = {Path(path) for owner, path in actual.resources if owner == run.id}
            observed_journal: set[_Path] = set()
            for path in resources:
                role: _Path
                if path.is_relative_to(staging):
                    role = "staging"
                else:
                    assert path.parent == final_root, (context, path)
                    role = "final"
                observed_journal.add(role)
                previous = self.owned_paths.setdefault((actor, role), path)
                assert path == previous, (context, previous, path)
            assert len(resources) == len(observed_journal), (context, resources)
            if run.artifact is not None:
                artifact = artifacts.pop(run.artifact)
                assert (artifact.invocation, artifact.session) == (run.invocation, run.session)
                destination = final_root / artifact.id
                assert self.owned_paths.setdefault((actor, "final"), destination) == destination
            final_path = self.owned_paths.get((actor, "final"))
            staged_files = {path for path in actual.files if path.is_relative_to(staging)}
            final_files = {
                path
                for path in actual.files
                if final_path is not None and path.is_relative_to(final_path)
            }
            classified.update(staged_files | final_files)
            observed = _Run(
                terminal="incomplete" if run.outcome is None else _terminal_outcome(run.outcome),
                manifest=run.artifact is not None,
                journal=frozenset(observed_journal),
                directories=frozenset(
                    role
                    for (owner, role), path in self.owned_paths.items()
                    if owner == actor and (layout.project_root / path).is_dir()
                ),
                staging=len(staged_files),
                final=len(final_files),
            )
            assert observed == expected, (
                context,
                f"Run {run.id}",
                f"expected={expected}",
                f"observed={observed}",
            )
        assert not artifacts and classified == actual.files - initial.files, context

    def visible(self, snapshot: frozenset[int] | None = None) -> dict[str, list[int]]:
        observed = _observe(self.case.session)
        owners = _durable(self.state) if snapshot is None else snapshot
        new = {
            run.artifact: [60]
            for run in observed.runs
            if run.id in {self.owners[actor] for actor in owners} and run.artifact is not None
        }
        assert len(new) == len(owners)
        return {self.case.prior.artifact_id: [7], **new}


def _terminal_outcome(value: str) -> Literal["succeeded", "failed"]:
    if value == "succeeded":
        return "succeeded"
    assert value == "failed", value
    return "failed"


def _writer_cuts(replay: _Replay, writer: Popen[str], log: Path, prefix: _Trace) -> None:
    replay.step("writer_start", prefix[0].actor)
    for index, edge in enumerate(prefix[1:]):
        command = None if index == 0 else "continue"
        assert _reply(writer, log, command) == edge.action
        replay.step(edge.action, edge.actor)
        replay.compare()


def _crash(replay: _Replay, writer: Popen[str], actor: int) -> None:
    writer.kill()
    assert writer.wait(timeout=5) < 0
    replay.step("writer_crash", actor)
    replay.compare()


def _cold_steps(replay: _Replay, *, blocked: bool = False) -> None:
    replay.step("recovery_start")
    while replay.state.recovery is not None:
        options = tuple(_recovery_steps(replay.state, None))
        edge = options[-1] if blocked else options[0]
        replay.step(edge.action, edge.actor)


def test_durable_publication_boundaries_replay_the_model(
    publication: _Publication,
    tmp_path: Path,
) -> None:
    replay = _Replay(publication)
    session = publication.session
    prefix = _writer_prefix("after_commit")
    cuts = tuple(edge.action for edge in prefix[1:])
    reader_log = tmp_path / "reader.log"
    recovery_log = tmp_path / "recovery.log"
    writer_log = tmp_path / "writer.log"
    with (
        _publication_process(session, "reader", reader_log) as reader,
        _publication_process(session, "recover-trace", recovery_log) as recovery,
    ):
        assert _reply(reader, reader_log) == "ready"
        assert _reply(recovery, recovery_log) == "ready"
        with _publication_process(session, "publish-trace", writer_log, *cuts) as writer:
            _writer_cuts(replay, writer, writer_log, prefix[:-1])
            assert _reply(reader, reader_log, "read-paused") == "snapshot"
            replay.step("reader_begin")
            assert _reply(recovery, recovery_log, "healthy") == "busy"
            replay.step("contender_busy")
            with pytest.raises(SessionBusyError):
                harness._literal(session, 42).persist()
            replay.step("contender_busy")
            replay.compare()
            assert _reply(writer, writer_log, "continue") == cuts[-1]
            replay.step("after_commit", 0)
            replay.compare()
            _crash(replay, writer, 0)
        _cold_steps(replay)
        assert _reply(recovery, recovery_log, "healthy") == replay.visible()
        replay.compare()
        assert replay.state.reader is not None
        assert _reply(reader, reader_log, "continue") == replay.visible(replay.state.reader)
        replay.step("reader_finish")
        assert _reply(reader, reader_log, "read") == replay.visible()
        # A new writer's pre-commit crash must preserve the just-published commit.
        next_prefix = _writer_prefix("before_commit", initial=replay.state, actor=1)
        with _publication_process(
            session, "publish-trace", writer_log, *(edge.action for edge in next_prefix[1:])
        ) as writer:
            _writer_cuts(replay, writer, writer_log, next_prefix)
            _crash(replay, writer, 1)
        # Interrupt recovery after the owned files have been removed and
        # before the original Run and cleanup receipts are recorded.
        assert _reply(recovery, recovery_log, "pause") == "recovery_before_record"
        replay.step("recovery_start")
        while replay.state.recovery is not None:
            edge = next(_recovery_steps(replay.state, None))
            if edge.action == "recover_run":
                break
            replay.step(edge.action, edge.actor)
        replay.compare()
        recovery.kill()
        assert recovery.wait(timeout=5) < 0
        replay.step("recovery_crash")
        replay.compare()
        _cold_steps(replay)
        assert _reply(reader, reader_log, "recover") == replay.visible()
        replay.compare()
        _prior_readable(publication)
        print("Durable boundary trace:", " -> ".join(replay.history))


@pytest.mark.parametrize("mutation", ["journal", "failed"])
def test_recovery_model_agrees_with_durable_observation(
    publication: _Publication,
    tmp_path: Path,
    mutation: _Mutation,
) -> None:
    replay = _Replay(publication)
    session = publication.session
    witness = _explore(mutation).violation
    assert witness is not None
    cut = next(index for index, edge in enumerate(witness.trace) if edge.action == "writer_crash")
    prefix = witness.trace[:cut]
    writer_log = tmp_path / "writer.log"
    recovery_log = tmp_path / "recovery.log"
    with (
        _publication_process(session, "recover-trace", recovery_log) as recovery,
        _publication_process(
            session, "publish-trace", writer_log, *(edge.action for edge in prefix[1:])
        ) as writer,
    ):
        assert _reply(recovery, recovery_log) == "ready"
        _writer_cuts(replay, writer, writer_log, prefix)
        _crash(replay, writer, 0)
        command = "blocked" if mutation == "journal" else "healthy"
        _cold_steps(replay, blocked=command == "blocked")
        assert _reply(recovery, recovery_log, command) == replay.visible()
        replay.compare()
        if mutation == "journal":
            run = replay.state.runs[0]
            assert run.directories == run.journal == _STAGING and run.staging == 0
        for _ in range(2):
            _cold_steps(replay)
            assert _reply(recovery, recovery_log, "healthy") == replay.visible()
            replay.compare()
        _prior_readable(publication)
        print(f"Recovery trace ({mutation}):", " -> ".join(replay.history))


def test_already_open_session_reconciles_before_admitting_a_new_writer(
    publication: _Publication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = _Replay(publication)
    session = publication.session
    memory = harness._memory(session)
    writer_log = tmp_path / "writer.log"
    prefix = _writer_prefix("terminal_primary_written")
    with _publication_process(
        session, "publish-trace", writer_log, *(edge.action for edge in prefix[1:])
    ) as writer:
        _writer_cuts(replay, writer, writer_log, prefix)
        _crash(replay, writer, 0)

    replay.step("writer_start", 1)
    while replay.state.recovery is not None:
        edge = next(_recovery_steps(replay.state, None, allow_failure=False))
        replay.step(edge.action, edge.actor)

    def observe_admission(event: str) -> None:
        if event == "publication_admitted":
            replay.step(event, 1)
            replay.compare()

    monkeypatch.setattr(terminal_publication, "_event", observe_admission)
    try:
        saved = memory.persist()
    except MaterializationError:
        replay.compare()
        raise
    for edge in _writer_prefix("writer_release", initial=replay.state, actor=1):
        replay.step(edge.action, edge.actor)
    replay.compare()
    assert replay.visible() == {publication.prior.artifact_id: [7], saved.artifact_id: [60]}
    assert saved.to_pandas()["detail.absolute.q"].tolist() == [60]
    assert len(saved.method_facts) == 1
    _prior_readable(publication)
    print("Already-open writer trace:", " -> ".join(replay.history))
