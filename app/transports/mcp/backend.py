from __future__ import annotations

import asyncio
from typing import Any, Protocol, cast, runtime_checkable

from app.contracts.errors import (
    ConflictError,
    DomainError,
    IntegrityError,
    NotFoundError,
    ValidationError,
)


@runtime_checkable
class MarivoBackend(Protocol):
    async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]: ...


class EmbeddedBackend:
    """Calls MarivoRuntime methods directly via thread executor.

    MarivoRuntime methods are synchronous. Running them in a thread
    executor prevents blocking the async MCP event loop.
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._default_session_id: str | None = None

    async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_call, method, path, kwargs)

    def _sync_call(self, method: str, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        runtime_method = getattr(self._runtime, method)
        if "session_id" not in kwargs and self._default_session_id is not None:
            kwargs["session_id"] = self._default_session_id
        try:
            result = runtime_method(**kwargs)
            return _wrap_success(result)
        except NotFoundError as e:
            return _wrap_error("NOT_FOUND", str(e))
        except ConflictError as e:
            return _wrap_error("CONFLICT", str(e))
        except ValidationError as e:
            return _wrap_error("VALIDATION", str(e))
        except IntegrityError as e:
            return _wrap_error("INTEGRITY", str(e))
        except DomainError as e:
            return _wrap_error("DOMAIN", str(e))
        except Exception as e:
            return _wrap_error("INTERNAL", str(e))


class HttpBackend:
    """Proxies to Marivo server via HTTP."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return cast("dict[str, Any]", await self._client.request_envelope(method, path, **kwargs))


def _wrap_success(result: dict[str, Any]) -> dict[str, Any]:
    return {"data": result, "error": None}


def _wrap_error(code: str, message: str) -> dict[str, Any]:
    return {"data": None, "error": {"code": code, "message": message}}
