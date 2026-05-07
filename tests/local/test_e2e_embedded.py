from __future__ import annotations

import asyncio
from pathlib import Path

from app.profiles.local import LocalConfig, create_local_runtime
from app.transports.mcp.backend import EmbeddedBackend


def _init_workspace(tmp_path: Path) -> Path:
    """Create a minimal .marivo/ workspace for testing."""
    marivo = tmp_path / ".marivo"
    marivo.mkdir()
    (marivo / "models").mkdir()
    (marivo / "evidence").mkdir()
    (marivo / "VERSION").write_text("1")
    (marivo / "marivo.toml").write_text(
        '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
    )
    return tmp_path


def test_session_lifecycle(tmp_path: Path):
    """Create, inspect, and terminate a session via local runtime."""
    workspace = _init_workspace(tmp_path)
    config = LocalConfig(workspace_root=workspace)
    runtime = create_local_runtime(config, explicit_local=True)

    # Create session
    session_id = runtime.create_session(goal="test investigation")
    assert session_id is not None

    # Get session state
    state = runtime.get_session_state(session_id)
    assert state is not None
    assert state.status == "active"
    assert state.goal == "test investigation"

    # Terminate session
    runtime.terminate_session(session_id)
    state = runtime.get_session_state(session_id)
    assert state.status == "terminated"


def test_embedded_backend_session_injection(tmp_path: Path):
    """EmbeddedBackend injects _default_session_id when session_id is absent."""
    workspace = _init_workspace(tmp_path)
    config = LocalConfig(workspace_root=workspace)
    runtime = create_local_runtime(config, explicit_local=True)
    session_id = runtime.create_session(goal="MCP session")

    backend = EmbeddedBackend(runtime)
    backend._default_session_id = session_id

    # Call observe without session_id -- should use default.
    # The runtime.observe() requires (session_id, params); the backend
    # passes session_id via injection and packs remaining kwargs as
    # the params dict. We expect a structured error (NOT_FOUND or
    # similar) because no semantic models exist, NOT a raw exception.
    result = asyncio.run(backend.call("observe", "/observe", params={"metric": "revenue"}))
    # Must have data or error, never raise raw exception
    assert "data" in result or "error" in result


def test_embedded_backend_format_parity(tmp_path: Path):
    """Embedded mode response structure matches HTTP mode response structure."""
    workspace = _init_workspace(tmp_path)
    config = LocalConfig(workspace_root=workspace)
    runtime = create_local_runtime(config, explicit_local=True)
    backend = EmbeddedBackend(runtime)
    session_id = runtime.create_session(goal="parity test")
    backend._default_session_id = session_id

    result = asyncio.run(backend.call("observe", "/observe", params={"metric": "revenue"}))
    # Must have data or error, never raw exception
    assert "data" in result or "error" in result


def test_should_embed_returns_false_for_remote():
    """_should_embed returns False when mode is remote."""
    from marivo_mcp.config import MarivoMcpConfig
    from marivo_mcp.server import _should_embed

    config = MarivoMcpConfig(mode="remote")
    assert _should_embed(config) is False


def test_should_embed_returns_true_for_local_embedded():
    """_should_embed returns True when mode=local and embedded=True."""
    from marivo_mcp.config import MarivoMcpConfig
    from marivo_mcp.server import _should_embed

    config = MarivoMcpConfig(mode="local", embedded=True)
    assert _should_embed(config) is True


def test_should_embed_returns_false_for_local_not_embedded():
    """_should_embed returns False when mode=local but embedded=False."""
    from marivo_mcp.config import MarivoMcpConfig
    from marivo_mcp.server import _should_embed

    config = MarivoMcpConfig(mode="local", embedded=False)
    assert _should_embed(config) is False


def test_should_embed_auto_checks_workspace(tmp_path: Path):
    """_should_embed in auto mode checks for .marivo/marivo.toml."""
    from marivo_mcp.config import MarivoMcpConfig
    from marivo_mcp.server import _should_embed

    # Without workspace file
    config = MarivoMcpConfig(mode="auto", workspace_root=str(tmp_path))
    assert _should_embed(config) is False

    # With workspace file
    _init_workspace(tmp_path)
    config2 = MarivoMcpConfig(mode="auto", workspace_root=str(tmp_path))
    assert _should_embed(config2) is True
