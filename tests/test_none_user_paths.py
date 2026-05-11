"""Tests for code paths when resolve_user() returns None.

Verifies that operations raise clear errors instead of silently
defaulting to "local" identity, ensuring the transport layer contract
is enforced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marivo.identity import current_user, require_user
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


# ── require_user() raising when no user set ──────────────────────────


def test_require_user_raises_when_no_user():
    token = current_user.set(None)
    try:
        with pytest.raises(RuntimeError, match="User identity not set"):
            require_user()
    finally:
        current_user.reset(token)


# ── create_session with no user ────────────────────────────────────────


def test_create_session_raises_without_user(tmp_path: Path):
    _init_marivo_dir(tmp_path)
    config = LocalConfig(workspace_root=tmp_path)
    runtime = create_local_runtime(config)

    token = current_user.set(None)
    try:
        with pytest.raises(RuntimeError, match="User identity not set"):
            runtime.create_session(goal="test goal")
    finally:
        current_user.reset(token)


# ── register_datasource with no user ───────────────────────────────────


def test_register_datasource_raises_without_user(tmp_path: Path):
    from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
    from marivo.adapters.server.datasource_registry import DatasourceRegistry

    db_path = tmp_path / "metadata.db"
    metadata = SQLiteMetadataStore(db_path)
    metadata.initialize()

    registry = DatasourceRegistry(metadata=metadata)

    token = current_user.set(None)
    try:
        with pytest.raises(RuntimeError, match="User identity not set"):
            registry.register_datasource(
                datasource_type="duckdb",
                display_name="Test DS",
                connection={"path": ":memory:"},
            )
    finally:
        current_user.reset(token)


# ── terminate_session via HTTP with no user ─────────────────────────────


def test_terminate_session_via_http_raises_without_user(tmp_path: Path):
    from fastapi.testclient import TestClient

    from marivo.main import create_app

    db_path = tmp_path / "analytics.duckdb"
    from tests.shared_fixtures import get_seeded_duckdb_path

    get_seeded_duckdb_path(db_path)
    # No default X-Marivo-User header — tests that missing identity is rejected
    client = TestClient(create_app(db_path), raise_server_exceptions=False)
    resp = client.post(
        "/sessions/sess_nonexistent/terminate",
        json={"terminal_reason": "user_closed"},
    )
    # Service requires user identity; without X-Marivo-User it fails with 500
    assert resp.status_code == 500
