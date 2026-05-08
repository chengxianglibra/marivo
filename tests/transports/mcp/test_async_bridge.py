from __future__ import annotations

import pytest

from app.contracts.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    IntegrityError,
    NotFoundError,
    ValidationError,
)
from app.transports.mcp.tools._async_bridge import call_runtime


@pytest.mark.parametrize(
    ("exc_class", "expected_code"),
    [
        (NotFoundError, "NOT_FOUND"),
        (ConflictError, "CONFLICT"),
        (ValidationError, "VALIDATION"),
    ],
)
@pytest.mark.asyncio
async def test_domain_error_mapping(exc_class, expected_code):
    def method():
        raise exc_class(code=ErrorCode.NOT_FOUND, message="test")

    result = await call_runtime(method)
    assert result["data"] is None
    assert result["error"]["code"] == expected_code
    assert result["error"]["message"] == "test"


@pytest.mark.asyncio
async def test_integrity_error_mapping():
    def method():
        raise IntegrityError(message="hash mismatch")

    result = await call_runtime(method)
    assert result["data"] is None
    assert result["error"]["code"] == "INTEGRITY"
    assert result["error"]["message"] == "hash mismatch"


@pytest.mark.asyncio
async def test_generic_domain_error():
    def method():
        raise DomainError(code=ErrorCode.NOT_FOUND, message="generic")

    result = await call_runtime(method)
    assert result["error"]["code"] == "DOMAIN"


@pytest.mark.asyncio
async def test_unexpected_exception():
    def method():
        raise RuntimeError("boom")

    result = await call_runtime(method)
    assert result["error"]["code"] == "INTERNAL"


@pytest.mark.asyncio
async def test_success_dict_return():
    def method():
        return {"key": "value"}

    result = await call_runtime(method)
    assert result["data"] == {"key": "value"}
    assert result["error"] is None


@pytest.mark.asyncio
async def test_success_none_return():
    def method():
        return None

    result = await call_runtime(method)
    assert result["data"] is None
    assert result["error"] is None
