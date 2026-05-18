from __future__ import annotations

import json

import pytest

from marivo.contracts.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    IntegrityError,
    NotFoundError,
    ValidationError,
)
from marivo.transports.mcp.tools._async_bridge import _wrap_success, call_runtime


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
async def test_missing_user_identity_runtime_error_maps_to_validation():
    def method():
        raise RuntimeError(
            "User identity not set — transport layer must set user before service calls"
        )

    result = await call_runtime(method)
    assert result["error"]["code"] == "VALIDATION"


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


def test_wrap_success_list_of_dicts_produces_valid_json():
    """list[dict] must survive serialization as valid JSON, not Python repr."""
    items = [{"datasource_id": "ds_abc", "display_name": "My DB"}]
    wrapped = _wrap_success(items)
    # The data field must be the original list, not a str()-ified Python repr
    assert wrapped["data"] == items
    serialized = json.dumps(wrapped)
    # Must be valid JSON (double-quoted), not Python repr (single-quoted)
    assert "'datasource_id'" not in serialized
    assert '"datasource_id"' in serialized


def test_wrap_success_string_passthrough():
    result = _wrap_success("hello")
    assert result["data"] == "hello"


def test_wrap_success_int_passthrough():
    result = _wrap_success(42)
    assert result["data"] == 42


def test_wrap_success_bool_passthrough():
    result = _wrap_success(True)
    assert result["data"] is True


def test_wrap_success_pydantic_model():
    from pydantic import BaseModel

    class Dummy(BaseModel):
        name: str
        value: int = 5

    wrapped = _wrap_success(Dummy(name="test"))
    assert wrapped["data"] == {"name": "test", "value": 5}


def test_wrap_success_tuple_passthrough():
    items = [{"a": 1}, {"b": 2}]
    wrapped = _wrap_success(tuple(items))
    assert wrapped["data"] == tuple(items)
    # Serialization still produces valid JSON
    serialized = json.dumps(wrapped)
    assert "'a'" not in serialized
