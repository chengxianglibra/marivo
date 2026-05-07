from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.contracts.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    IntegrityError,
    NotFoundError,
    ValidationError,
)
from app.transports.mcp.backend import EmbeddedBackend, HttpBackend, MarivoBackend

# --- MarivoBackend protocol check ---


def test_marivo_backend_is_runtime_checkable() -> None:
    """MarivoBackend is a Protocol that supports isinstance checks."""

    class FakeBackend:
        async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            return {}

    assert isinstance(FakeBackend(), MarivoBackend)


# --- EmbeddedBackend: call delegation ---


def test_embedded_call_delegates_to_runtime() -> None:
    runtime = MagicMock()
    runtime.observe.return_value = {"result": "ok"}
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result == {"data": {"result": "ok"}, "error": None}
    runtime.observe.assert_called_once_with(session_id="s1", metric="revenue")


def test_embedded_call_injects_default_session_id() -> None:
    runtime = MagicMock()
    runtime.observe.return_value = {"result": "ok"}
    backend = EmbeddedBackend(runtime)
    backend._default_session_id = "default-sess"

    asyncio.run(backend.call("observe", "/observe", metric="revenue"))
    runtime.observe.assert_called_once_with(session_id="default-sess", metric="revenue")


def test_embedded_call_explicit_session_id_overrides_default() -> None:
    runtime = MagicMock()
    runtime.observe.return_value = {"result": "ok"}
    backend = EmbeddedBackend(runtime)
    backend._default_session_id = "default-sess"

    asyncio.run(backend.call("observe", "/observe", session_id="explicit", metric="revenue"))
    runtime.observe.assert_called_once_with(session_id="explicit", metric="revenue")


# --- EmbeddedBackend: error mapping ---


def test_embedded_call_maps_not_found_error() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = NotFoundError(ErrorCode.NOT_FOUND, "session missing")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "NOT_FOUND"
    assert result["data"] is None


def test_embedded_call_maps_conflict_error() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = ConflictError(ErrorCode.CONFLICT, "already exists")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "CONFLICT"


def test_embedded_call_maps_validation_error() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = ValidationError(ErrorCode.VALIDATION, "bad input")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "VALIDATION"


def test_embedded_call_maps_integrity_error() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = IntegrityError(message="hash mismatch")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "INTEGRITY"


def test_embedded_call_maps_domain_error() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = DomainError(ErrorCode.FORBIDDEN, "forbidden")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "DOMAIN"


def test_embedded_call_maps_unknown_exception() -> None:
    runtime = MagicMock()
    runtime.observe.side_effect = RuntimeError("boom")
    backend = EmbeddedBackend(runtime)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1", metric="revenue"))
    assert result["error"]["code"] == "INTERNAL"
    assert result["data"] is None


# --- HttpBackend ---


def test_http_backend_delegates_to_client() -> None:
    client = MagicMock()

    async def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {"data": {"ok": True}, "error": None}

    client.request_envelope = fake_request
    backend = HttpBackend(client)

    result = asyncio.run(backend.call("observe", "/observe", session_id="s1"))
    assert result == {"data": {"ok": True}, "error": None}
