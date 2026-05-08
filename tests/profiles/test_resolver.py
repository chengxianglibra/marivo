from __future__ import annotations

import pytest

from app.contracts.errors import ErrorCode
from app.profiles.resolver import (
    ProfileResolutionError,
    resolve_profile,
)

# --- Defaults ---


def test_local_stdio_default_is_local() -> None:
    assert resolve_profile(entry_point="local_stdio", env={}) == "local"


def test_server_http_default_is_server() -> None:
    assert resolve_profile(entry_point="server_http", env={}) == "server"


# --- ProfileResolutionError ---


def test_profile_resolution_error_inherits_validation_code() -> None:
    err = ProfileResolutionError(ErrorCode.VALIDATION, "test")
    assert err.code == ErrorCode.VALIDATION


# --- Explicit + env precedence (C2) ---


def test_explicit_overrides_env() -> None:
    result = resolve_profile(
        entry_point="local_stdio",
        explicit="local",
        env={"MARIVO_PROFILE": "server"},
    )
    assert result == "local"


def test_env_used_when_no_explicit() -> None:
    result = resolve_profile(
        entry_point="server_http",
        env={"MARIVO_PROFILE": "server"},
    )
    assert result == "server"


def test_empty_explicit_treated_as_missing() -> None:
    result = resolve_profile(entry_point="local_stdio", explicit="", env={})
    assert result == "local"


def test_empty_env_treated_as_missing() -> None:
    result = resolve_profile(
        entry_point="local_stdio",
        env={"MARIVO_PROFILE": ""},
    )
    assert result == "local"


def test_unknown_value_raises() -> None:
    with pytest.raises(ProfileResolutionError) as exc_info:
        resolve_profile(entry_point="local_stdio", explicit="nope", env={})
    assert "expected 'local' or 'server'" in str(exc_info.value)


def test_incompatible_value_raises() -> None:
    with pytest.raises(ProfileResolutionError) as exc_info:
        resolve_profile(entry_point="local_stdio", explicit="server", env={})
    assert "not allowed at entry point 'local_stdio'" in str(exc_info.value)


# --- Structured logging (C2) ---


def test_unknown_profile_logs_structured_context(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR"), pytest.raises(ProfileResolutionError):
        resolve_profile(entry_point="local_stdio", explicit="nope", env={})
    assert any(
        "profile.unknown" in record.message
        and "local_stdio" in record.message
        and "explicit" in record.message
        for record in caplog.records
    )


def test_incompatible_profile_logs_structured_context(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR"), pytest.raises(ProfileResolutionError):
        resolve_profile(entry_point="local_stdio", explicit="server", env={})
    assert any(
        "profile.incompatible" in record.message and "local_stdio" in record.message
        for record in caplog.records
    )


# --- Service config + workspace TOML (C3) ---


def test_server_entry_reads_marivo_config_profile() -> None:
    from app.config import MarivoConfig

    cfg = MarivoConfig(profile="server")
    result = resolve_profile(entry_point="server_http", env={}, service_config=cfg)
    assert result == "server"


def test_server_entry_service_config_below_explicit() -> None:
    from app.config import MarivoConfig

    cfg = MarivoConfig(profile="local")  # incompatible at server entry
    # explicit beats service_config; "server" wins.
    result = resolve_profile(
        entry_point="server_http",
        explicit="server",
        env={},
        service_config=cfg,
    )
    assert result == "server"


def test_local_entry_reads_profile_from_toml(tmp_path) -> None:
    toml = tmp_path / "marivo.toml"
    toml.write_text('profile = "local"\n')
    result = resolve_profile(entry_point="local_stdio", env={}, workspace_config_path=toml)
    assert result == "local"


def test_local_entry_toml_below_env(tmp_path) -> None:
    toml = tmp_path / "marivo.toml"
    toml.write_text('profile = "server"\n')  # incompatible at local entry
    # env beats toml; "local" wins.
    result = resolve_profile(
        entry_point="local_stdio",
        env={"MARIVO_PROFILE": "local"},
        workspace_config_path=toml,
    )
    assert result == "local"


def test_local_entry_missing_toml_falls_through(tmp_path) -> None:
    toml = tmp_path / "missing.toml"
    result = resolve_profile(entry_point="local_stdio", env={}, workspace_config_path=toml)
    assert result == "local"  # default


def test_local_entry_toml_without_profile_field_falls_through(tmp_path) -> None:
    toml = tmp_path / "marivo.toml"
    toml.write_text("# no profile field\n")
    result = resolve_profile(entry_point="local_stdio", env={}, workspace_config_path=toml)
    assert result == "local"


def test_local_entry_malformed_toml_raises_profile_error(tmp_path) -> None:
    toml = tmp_path / "marivo.toml"
    toml.write_text("profile = [unterminated\n")
    with pytest.raises(ProfileResolutionError) as exc_info:
        resolve_profile(entry_point="local_stdio", env={}, workspace_config_path=toml)
    assert "Malformed TOML" in str(exc_info.value)


def test_server_entry_ignores_workspace_toml(tmp_path) -> None:
    # Even if a workspace_config_path is somehow passed at server entry,
    # the resolver does NOT consult it.
    toml = tmp_path / "marivo.toml"
    toml.write_text('profile = "local"\n')
    result = resolve_profile(
        entry_point="server_http",
        env={},
        workspace_config_path=toml,  # ignored
    )
    assert result == "server"  # default for server_http
