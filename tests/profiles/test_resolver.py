from __future__ import annotations

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
