"""Tests for code paths when resolve_user() returns None.

Verifies that operations default to "local" identity instead of crashing
or silently degrading, ensuring no workflow is blocked when no user is
provided.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from marivo.contracts.ids import SessionId, UserId
from marivo.contracts.session import SessionState
from marivo.identity import resolve_user
from marivo.profiles.local import LocalConfig, create_local_runtime


def _init_marivo_dir(root: Path) -> None:
    marivo = root / ".marivo"
    marivo.mkdir(exist_ok=True)
    (marivo / "models").mkdir(exist_ok=True)
    (marivo / "evidence").mkdir(exist_ok=True)
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        'profile = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )


# ── resolve_user() returning None ──────────────────────────────────────


def test_resolve_user_none_when_no_contextvar_and_no_env() -> None:
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MARIVO_DEFAULT_USER", None)
        assert resolve_user() is None


# ── create_session with no user ────────────────────────────────────────


def test_create_session_defaults_to_local_actor(tmp_path: Path) -> None:
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MARIVO_DEFAULT_USER", None)
        state = runtime.create_session(goal="test goal")

    assert isinstance(state, SessionState)
    events = runtime.ports.session_store.load_events(state.session_id)
    created_event = next(e for e in events if e.event_type == "session_created")
    assert created_event.actor == UserId("local")


# ── register_datasource with no user ───────────────────────────────────


def test_register_datasource_defaults_to_local(tmp_path: Path) -> None:
    from marivo.adapters.server.datasource_registry import DatasourceRegistry
    from marivo.storage.sqlite_metadata import SQLiteMetadataStore

    db_path = tmp_path / "metadata.db"
    metadata = SQLiteMetadataStore(db_path)
    metadata.initialize()

    registry = DatasourceRegistry(metadata=metadata)

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MARIVO_DEFAULT_USER", None)
        result = registry.register_datasource(
            datasource_type="duckdb",
            display_name="Test DS",
            connection={"path": ":memory:"},
        )

    assert result["owner_user"] == "local"


# ── terminate_session with no user ─────────────────────────────────────


def test_terminate_session_with_local_fallback(tmp_path: Path) -> None:
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MARIVO_DEFAULT_USER", None)
        # Create session (gets actor="local" by default)
        state = runtime.create_session(goal="test goal")
        # Terminate with "local" actor (matching the create actor)
        runtime.terminate_session(
            SessionId(state.session_id),
            actor=UserId("local"),
            terminal_reason="user_closed",
        )

    events = runtime.ports.session_store.load_events(SessionId(state.session_id))
    terminated = [e for e in events if e.event_type == "session_terminated"]
    assert len(terminated) == 1
