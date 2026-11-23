"""Drive public publication and retained reads at controlled process boundaries."""

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import marivo
import marivo.analysis as mv
from marivo.analysis.materialization import terminal_publication, terminal_store
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.model.artifact_data import ArtifactManifest
from marivo.analysis.model.execution_errors import SessionBusyError


def _reply(value: object) -> None:
    print(json.dumps(value), flush=True)


def _read(session: mv.Session) -> dict[str, list[object]]:
    return {
        item.artifact_id: session.persisted_frame(item.artifact_id).to_pandas()["answer"].tolist()
        for item in session.persisted_frames().items
    }


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


def main() -> None:
    assert Path(marivo.__file__).resolve().parents[1] == Path(__file__).resolve().parents[2]
    project, session_id, role, *args = sys.argv[1:]
    os.chdir(project)
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
    elif role == "reader":
        _reply("ready")
        for command in sys.stdin:
            action = command.rstrip()
            assert action in ("read-paused", "read")
            _reply(_read_paused(session) if action == "read-paused" else _read(session))
    else:
        assert role == "recover"
        _reply(_read(session))


if __name__ == "__main__":
    main()
