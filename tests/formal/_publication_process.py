"""Drive public publication and retained reads at controlled process boundaries."""

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pyarrow as pa

import marivo
import marivo.analysis as mv
from marivo.analysis.materialization import terminal_publication, terminal_storage, terminal_store
from marivo.analysis.materialization.contracts import ResourceRecord, RunFailure
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.model.artifact_data import ArtifactManifest
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.execution_errors import SessionBusyError
from marivo.datasource.runtime.adapters import SourceSession
from tests.analysis.materialization.terminal_recovery_worker import method_result


def _reply(value: object) -> None:
    print(json.dumps(value), flush=True)


def _read(session: mv.Session) -> dict[str, list[object]]:
    result: dict[str, list[object]] = {}
    for item in session.persisted_frames().items:
        frame = session.persisted_frame(item.artifact_id)
        rows = frame.to_pandas()
        if "answer" in rows:
            result[item.artifact_id] = rows["answer"].tolist()
        else:
            assert len(frame.method_facts) == 1
            result[item.artifact_id] = rows["detail.absolute.q"].tolist()
    return result


def _read_paused(session: mv.Session) -> dict[str, list[object]]:
    manifest = terminal_store.manifest
    paused = False

    def pause_manifest(
        store: SessionStore, conn: sqlite3.Connection, ref: str
    ) -> ArtifactManifest | None:
        nonlocal paused
        if not paused:
            paused = True
            _reply("snapshot")
            assert sys.stdin.readline().rstrip() == "continue"
        return manifest(store, conn, ref)

    with patch.object(terminal_store, "manifest", pause_manifest):
        visible = _read(session)
    assert paused
    return visible


def _checkpoint(name: str) -> None:
    _reply(name)
    assert sys.stdin.readline().rstrip() == "continue"


def _publish_trace(session: mv.Session, cuts: tuple[str, ...]) -> None:
    memory = mv.AnalysisFrame._from_memory(session._runtime.local, method_result())
    reserve = SessionStore.reserve
    physical_schema = terminal_storage.physical_schema
    primary_started = False

    def observe_reservation(store: SessionStore, record: ResourceRecord) -> None:
        reserve(store, record)
        event("reserve_final" if "/artifacts/" in record.safe_locator else "reserve_staging")

    def event(name: str) -> None:
        if name in cuts:
            _checkpoint(name)

    def observe_primary_directory(schema: DataSchema) -> pa.Schema:
        nonlocal primary_started
        if not primary_started:
            # write_output has created the primary directory and has yet
            # to open its Parquet writer.
            primary_started = True
            event("terminal_primary_directory_created")
        return physical_schema(schema)

    with (
        patch.object(SessionStore, "reserve", observe_reservation),
        patch.object(terminal_storage, "physical_schema", observe_primary_directory),
        patch.object(terminal_publication, "_event", event),
    ):
        _reply(memory.persist(description="state machine candidate").artifact_id)


def _forbid(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Recovery must not bind a source, evaluate, or publish")


def _recover_trace(session_id: str) -> None:
    fail = SessionStore.fail
    remove = shutil.rmtree

    def recover(command: str) -> object:
        def cleanup(path: Path) -> None:
            if command == "blocked":
                raise OSError("Temporary owned-path cleanup failure")
            remove(path)

        def record(
            store: SessionStore,
            run_ref: str,
            failure: RunFailure,
            *,
            resolved_resources: tuple[ResourceRecord, ...] = (),
        ) -> None:
            if command == "pause":
                _checkpoint("recovery_before_record")
            fail(store, run_ref, failure, resolved_resources=resolved_resources)

        with patch.object(shutil, "rmtree", cleanup), patch.object(SessionStore, "fail", record):
            try:
                session = mv.session.resume(session_id)
            except SessionBusyError as error:
                assert error.session_ref == session_id
                return "busy"
            return _read(session)

    with (
        patch.object(mv.Session, "catalog", property(_forbid)),
        patch.object(SourceSession, "bind", _forbid),
        patch.object(SourceSession, "execute", _forbid),
        patch.object(mv.AnalysisFrame, "compute", _forbid),
        patch.object(terminal_publication, "publish_completed", _forbid),
    ):
        _reply("ready")
        for command in sys.stdin:
            action = command.rstrip()
            assert action in ("healthy", "blocked", "pause")
            _reply(recover(action))


def main() -> None:
    assert Path(marivo.__file__).resolve().parents[1] == Path(__file__).resolve().parents[2]
    project, session_id, role, *args = sys.argv[1:]
    os.chdir(project)
    if role == "recover-trace":
        _recover_trace(session_id)
        return
    try:
        session = mv.session.resume(session_id)
    except SessionBusyError as error:
        assert role == "recover" and error.session_ref == session_id
        _reply("busy")
        return
    if role == "publish":
        (cut,) = args
        memory = session.values(columns={"answer": None}, rows=(((), {"answer": 31}),)).compute()

        def checkpoint(name: str) -> None:
            if name == cut:
                _reply(name)
                assert sys.stdin.readline().rstrip() == "continue"

        terminal_publication._event = checkpoint
        _reply(memory.persist(description="candidate").artifact_id)
    elif role == "publish-trace":
        _publish_trace(session, tuple(args))
    elif role == "reader":
        _reply("ready")
        for command in sys.stdin:
            action = command.rstrip()
            assert action in ("read-paused", "read", "recover")
            if action == "recover":
                _reply(_read(mv.session.resume(session_id)))
            else:
                _reply(_read_paused(session) if action == "read-paused" else _read(session))
    else:
        assert role == "recover"
        _reply(_read(session))


if __name__ == "__main__":
    main()
