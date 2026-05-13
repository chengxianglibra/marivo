"""Async bridge: call synchronous MarivoRuntime methods from async MCP handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import copy_context
from typing import Any

from marivo.contracts.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    IntegrityError,
    NotFoundError,
    ValidationError,
)


async def call_runtime(method: Callable[..., Any], /, **kwargs: Any) -> dict[str, Any]:
    """Call a sync runtime method from an async MCP handler.

    Runs the method in a thread executor to avoid blocking the event loop.
    Copies the current contextvars.Context so that identity (current_user)
    and other context-scoped state propagate into the executor thread.
    Catches DomainError subclasses and wraps them into structured envelopes.
    """
    loop = asyncio.get_running_loop()
    ctx = copy_context()
    try:
        result = await loop.run_in_executor(None, ctx.run, lambda: method(**kwargs))
        return _wrap_success(result)
    except NotFoundError as e:
        return _wrap_error("NOT_FOUND", str(e))
    except ConflictError as e:
        return _wrap_error("CONFLICT", str(e))
    except ForbiddenError as e:
        return _wrap_error("FORBIDDEN", str(e))
    except ValidationError as e:
        return _wrap_error("VALIDATION", str(e))
    except IntegrityError as e:
        return _wrap_error("INTEGRITY", str(e))
    except DomainError as e:
        return _wrap_error("DOMAIN", str(e))
    except RuntimeError as e:
        if "User identity not set" in str(e):
            return _wrap_error("VALIDATION", str(e))
        return _wrap_error("INTERNAL", str(e))
    except Exception as e:
        return _wrap_error("INTERNAL", str(e))


def _wrap_success(result: Any) -> dict[str, Any]:
    if result is None:
        return {"data": None, "error": None}
    if isinstance(result, dict):
        return {"data": result, "error": None}
    # Pydantic BaseModel subclasses (e.g. SessionState)
    if hasattr(result, "model_dump") and callable(result.model_dump):
        return {"data": result.model_dump(), "error": None}
    # SessionId or other non-dict return types
    return {"data": str(result), "error": None}


def _wrap_error(code: str, message: str) -> dict[str, Any]:
    return {"data": None, "error": {"code": code, "message": message}}
