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
