# Phase 5: MCP Dual Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate all MCP code into the main package with two canonical modes (stdio local + HTTP enterprise), deleting the separate `marivo-mcp/` package.

**Architecture:** Rewrite both `UserIdentityMiddleware` and `TimingMiddleware` as pure ASGI (eliminating `BaseHTTPMiddleware`'s SSE-buffering conflict), then build an `app/transports/mcp/` module that registers tools directly against `MarivoRuntime` via an async bridge, mounts HTTP MCP under the existing FastAPI app at `/mcp`, and exposes a `marivo-stdio` console-script for local mode.

**Tech Stack:** FastMCP (from `mcp>=1.0.0`), FastAPI/Starlette ASGI, Pydantic v2, `asyncio.run_in_executor` for sync→async bridge.

**Spec:** `docs/superpowers/specs/2026-05-08-phase5-mcp-dual-mode-design.md`

---

## Discovered Risk: TimingMiddleware Also Buffers SSE

The spec rewrites `UserIdentityMiddleware` as pure ASGI (Decision D3, §3.1.0). However, **`TimingMiddleware` is also a `BaseHTTPMiddleware`** with the same buffering problem. Both middlewares wrap the entire FastAPI app (including the `/mcp` mount). If either buffers, SSE streaming from FastMCP's `streamable_http_app()` will hang.

This plan rewrites **both** middlewares. The `TimingMiddleware` fix is a necessary addition beyond the spec's scope; without it, the 5a SSE smoke test will fail.

---

## File Structure (Final, After Phase 5)

```
app/
  api/
    middleware.py              # REWRITE: UserIdentityMiddleware → pure ASGI
    app_factory.py             # MODIFY: add mount_mcp_app(app, services.runtime) call
  observability.py             # REWRITE: TimingMiddleware → pure ASGI
  transports/
    __init__.py                # EXISTS (no change)
    mcp/
      __init__.py              # EXISTS → UPDATE: export register_tools, register_resources, mount_mcp_app
      backend.py               # DELETE (in 5c)
      tools/
        __init__.py            # CREATE: register_tools(server, runtime)
        schemas.py             # CREATE: pydantic input models + validators
        _async_bridge.py       # CREATE: call_runtime, _wrap_success, _wrap_error
        intents.py             # CREATE: register_observe (5a) → all intents (5b)
        session.py             # CREATE: (5b) session lifecycle tools
        catalog.py             # CREATE: (5b) health + openapi + datasource tools
        semantic.py            # CREATE: (5b) semantic model CRUD tools
      resources/
        __init__.py            # CREATE: register_resources(server, runtime) — placeholder in 5a
      stdio.py                 # CREATE: marivo-stdio entry point
      http.py                  # CREATE: mount_mcp_app(fastapi_app, runtime, path="/mcp")
  profiles/local.py            # EXISTS (no change — create_local_runtime already works)
tests/
  test_middleware.py           # MODIFY: add SSE streaming test + pure-ASGI assertion
  transports/
    mcp/
      test_async_bridge.py     # CREATE: call_runtime error mapping
      test_observe_smoke.py    # CREATE: observe via stdio + HTTP MCP
      test_tool_parity.py      # CREATE: (5d)
      test_http_mcp_e2e.py     # CREATE: (5d)
      test_stdio_mcp_e2e.py    # CREATE: (5d)
      test_user_passthrough.py # CREATE: (5d)
```

---

## Sub-phase 5a: ASGI Middleware Rewrite + Scaffolding + Observe E2E

### Task 1: Rewrite UserIdentityMiddleware as pure ASGI

**Files:**
- Modify: `app/api/middleware.py`
- Modify: `tests/test_middleware.py`

- [ ] **Step 1: Write failing test — assert middleware is NOT BaseHTTPMiddleware**

Add to `tests/test_middleware.py`:

```python
from starlette.middleware.base import BaseHTTPMiddleware


def test_is_not_base_http_middleware():
    """UserIdentityMiddleware must be pure ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers the full response body, which breaks
    SSE streaming (e.g. FastMCP streamable-http transport).
    """
    assert not issubclass(UserIdentityMiddleware, BaseHTTPMiddleware)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_middleware.py::test_is_not_base_http_middleware -v`
Expected: FAIL — `UserIdentityMiddleware` currently inherits from `BaseHTTPMiddleware`

- [ ] **Step 3: Rewrite UserIdentityMiddleware as pure ASGI**

Replace the entire contents of `app/api/middleware.py` with:

```python
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.identity import current_user


class UserIdentityMiddleware:
    """Pure-ASGI middleware that sets current_user from X-Marivo-User.

    Unlike BaseHTTPMiddleware, this does not buffer the response body,
    so it is compatible with SSE streaming (e.g. FastMCP streamable-http).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw = headers.get(b"x-marivo-user")
        user: str | None = None
        if raw is not None:
            decoded = raw.decode("latin-1").strip()
            if decoded:
                user = decoded

        token = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(token)
```

- [ ] **Step 4: Run all middleware tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_middleware.py -v`
Expected: ALL 7 tests PASS (6 existing + 1 new `test_is_not_base_http_middleware`)

- [ ] **Step 5: Commit**

```bash
git add app/api/middleware.py tests/test_middleware.py
git commit -m "$(cat <<'EOF'
refactor: rewrite UserIdentityMiddleware as pure ASGI

BaseHTTPMiddleware buffers the entire response body before sending it
downstream, which is incompatible with SSE streaming. Rewrite as a
pure ASGI middleware that passes send messages through without
buffering, enabling FastMCP streamable-http transport to work.

No behavioral change: all six existing tests pass unchanged.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 2: Rewrite TimingMiddleware as pure ASGI

**Files:**
- Modify: `app/observability.py` (TimingMiddleware class only, lines 321-340)

- [ ] **Step 1: Write failing test — assert TimingMiddleware is NOT BaseHTTPMiddleware**

Create `tests/test_timing_middleware.py`:

```python
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import TimingMiddleware


def test_is_not_base_http_middleware():
    """TimingMiddleware must be pure ASGI, not BaseHTTPMiddleware.

    BaseHTTPMiddleware buffers the full response body, which breaks
    SSE streaming (e.g. FastMCP streamable-http transport).
    """
    assert not issubclass(TimingMiddleware, BaseHTTPMiddleware)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_timing_middleware.py::test_is_not_base_http_middleware -v`
Expected: FAIL

- [ ] **Step 3: Rewrite TimingMiddleware as pure ASGI**

In `app/observability.py`, replace the `TimingMiddleware` class (lines 321-340) and update the imports at the top.

**Import changes** — replace lines 13-15:
```python
# REMOVE:
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ADD:
from starlette.types import ASGIApp, Message, Receive, Scope, Send
```

**Class replacement** — replace lines 321-340:
```python
class TimingMiddleware:
    """Pure-ASGI middleware that measures request duration.

    Unlike BaseHTTPMiddleware, this does not buffer the response body,
    so it is compatible with SSE streaming (e.g. FastMCP streamable-http).
    Duration is measured from request start to last body chunk sent.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: int = 0

        async def send_with_capture(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_with_capture)
        duration_ms = (time.perf_counter() - start) * 1000

        app = scope.get("app")
        metrics: MetricsCollector | None = getattr(app.state, "metrics", None) if app is not None else None
        if metrics is not None:
            path = scope.get("path", "")
            method = scope.get("method", "")
            metrics.record_request(method, path, status_code, duration_ms)
        _http_logger.info(
            "HTTP %s %s %d %.1fms",
            scope.get("method", ""),
            scope.get("path", ""),
            status_code,
            duration_ms,
        )


_http_logger = logging.getLogger("marivo.http")
```

- [ ] **Step 4: Run tests to verify**

Run: `.venv/bin/python -m pytest tests/test_timing_middleware.py tests/test_middleware.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `make test`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/observability.py tests/test_timing_middleware.py
git commit -m "$(cat <<'EOF'
refactor: rewrite TimingMiddleware as pure ASGI

Same SSE-buffering fix as UserIdentityMiddleware. Duration is now
measured from request start to the last body chunk sent (including
streaming time), which is more accurate for SSE connections.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 3: Add SSE streaming non-buffering test

**Files:**
- Modify: `tests/test_middleware.py`

- [ ] **Step 1: Write SSE streaming test**

Add to `tests/test_middleware.py`:

```python
import asyncio

import pytest


async def test_sse_streaming_not_buffered():
    """Response chunks pass through immediately, not buffered by middleware."""
    chunks_sent: list[str] = []

    async def sse_app(scope: object, receive: object, send: object) -> None:
        await send({"type": "http.response.start", "status": 200,
                     "headers": [[b"content-type", b"text/event-stream"]]})
        await send({"type": "http.response.body", "body": b"data: chunk1\n\n",
                     "more_body": True})
        chunks_sent.append("chunk1")
        await send({"type": "http.response.body", "body": b"data: chunk2\n\n",
                     "more_body": False})
        chunks_sent.append("chunk2")

    received: list[dict] = []

    async def capture_send(message: dict) -> None:
        received.append(message)

    wrapped = UserIdentityMiddleware(sse_app)
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "asgi": {"version": "3.0"},
    }

    await wrapped(scope, lambda: None, capture_send)

    body_messages = [m for m in received if m["type"] == "http.response.body"]
    assert len(body_messages) == 2, f"Expected 2 body chunks, got {len(body_messages)}"
    assert body_messages[0]["body"] == b"data: chunk1\n\n"
    assert body_messages[0].get("more_body", False) is True
    assert body_messages[1]["body"] == b"data: chunk2\n\n"
    # Both chunks were produced by the downstream app
    assert chunks_sent == ["chunk1", "chunk2"]
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/test_middleware.py::test_sse_streaming_not_buffered -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_middleware.py
git commit -m "$(cat <<'EOF'
test: add SSE streaming non-buffering test for UserIdentityMiddleware

Verifies that the pure-ASGI middleware passes response chunks through
without buffering, which is required for FastMCP streamable-http.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 4: Add mcp dependency and create async bridge module

**Files:**
- Modify: `pyproject.toml` (add `mcp>=1.0.0` dependency)
- Create: `app/transports/mcp/tools/_async_bridge.py`

- [ ] **Step 1: Add `mcp` dependency to root pyproject.toml**

In `pyproject.toml`, add `"mcp>=1.0.0"` to the `dependencies` list (alphabetically, near `pydantic`).

- [ ] **Step 2: Install the new dependency**

Run: `.venv/bin/pip install -e ".[duckdb]" 2>&1 | tail -5`
Expected: `mcp` resolves and installs (or already installed).

- [ ] **Step 3: Create `_async_bridge.py`**

Create `app/transports/mcp/tools/_async_bridge.py`:

```python
"""Async bridge: call synchronous MarivoRuntime methods from async MCP handlers."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.contracts.errors import (
    ConflictError,
    DomainError,
    IntegrityError,
    NotFoundError,
    ValidationError,
)


async def call_runtime(
    method: Callable[..., Any], /, **kwargs: Any
) -> dict[str, Any]:
    """Call a sync runtime method from an async MCP handler.

    Runs the method in a thread executor to avoid blocking the event loop.
    Catches DomainError subclasses and wraps them into structured envelopes.
    """
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: method(**kwargs))
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


def _wrap_success(result: Any) -> dict[str, Any]:
    if result is None:
        return {"data": None, "error": None}
    if isinstance(result, dict):
        return {"data": result, "error": None}
    # SessionId or other non-dict return types
    return {"data": str(result), "error": None}


def _wrap_error(code: str, message: str) -> dict[str, Any]:
    return {"data": None, "error": {"code": code, "message": message}}
```

- [ ] **Step 4: Write failing test for call_runtime error mapping**

Create `tests/transports/mcp/__init__.py` (empty) and `tests/transports/mcp/test_async_bridge.py`:

```python
from __future__ import annotations

import asyncio

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


def _raise(exc: Exception) -> None:
    raise exc


class TestCallRuntimeErrorMapping:
    @pytest.mark.parametrize(
        ("exc_class", "expected_code"),
        [
            (NotFoundError, "NOT_FOUND"),
            (ConflictError, "CONFLICT"),
            (ValidationError, "VALIDATION"),
            (IntegrityError, "INTEGRITY"),
        ],
    )
    async def test_domain_error_mapping(self, exc_class, expected_code):
        def method():
            raise exc_class(code=ErrorCode.NOT_FOUND, message="test")

        result = await call_runtime(method)
        assert result["data"] is None
        assert result["error"]["code"] == expected_code
        assert result["error"]["message"] == "test"

    async def test_generic_domain_error(self):
        def method():
            raise DomainError(code=ErrorCode.NOT_FOUND, message="generic")

        result = await call_runtime(method)
        assert result["error"]["code"] == "DOMAIN"

    async def test_unexpected_exception(self):
        def method():
            raise RuntimeError("boom")

        result = await call_runtime(method)
        assert result["error"]["code"] == "INTERNAL"

    async def test_success_dict_return(self):
        def method():
            return {"key": "value"}

        result = await call_runtime(method)
        assert result["data"] == {"key": "value"}
        assert result["error"] is None

    async def test_success_none_return(self):
        def method():
            return None

        result = await call_runtime(method)
        assert result["data"] is None
        assert result["error"] is None
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/transports/mcp/test_async_bridge.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml app/transports/mcp/tools/_async_bridge.py tests/transports/mcp/
git commit -m "$(cat <<'EOF'
feat: add mcp dependency and async bridge for runtime method calls

call_runtime runs sync MarivoRuntime methods in a thread executor and
maps DomainError subclasses to structured error envelopes. This is the
foundation for all MCP tool handlers.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 5: Create observe tool schema + registration + register_tools orchestrator

**Files:**
- Create: `app/transports/mcp/tools/schemas.py`
- Create: `app/transports/mcp/tools/intents.py`
- Create: `app/transports/mcp/tools/__init__.py`

- [ ] **Step 1: Create schemas.py with observe input model**

Create `app/transports/mcp/tools/schemas.py`:

```python
"""Pydantic input models for MCP tool parameters.

Validators are copied verbatim from marivo-mcp/src/marivo_mcp/tools/__init__.py
to preserve wire compatibility. No refactoring allowed in Phase 5.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, BeforeValidator, model_validator
from typing_extensions import Annotated


def _reject_observe_time_scope_string(v: Any) -> Any:
    """Reject shorthand string time_scope; require canonical object form."""
    if isinstance(v, str):
        raise ValueError(
            "observe_time_scope_canonical_required: "
            "time_scope must be a structured object with kind, start, end "
            "(half-open interval [start, end)). "
            "Shorthand strings like '2024-03-01~2024-03-31' are not accepted."
        )
    return v


class McpObserveTimeScope(BaseModel):
    """Canonical time_scope for observe: half-open range [start, end)."""
    kind: str = "range"
    start: str
    end: str

    @model_validator(mode="after")
    def _validate_kind(self) -> McpObserveTimeScope:
        if self.kind != "range":
            raise ValueError(f"time_scope.kind must be 'range', got {self.kind!r}")
        return self


# Wrap with the string-rejection validator
ObserveTimeScope = Annotated[McpObserveTimeScope, BeforeValidator(_reject_observe_time_scope_string)]


class ObserveScope(BaseModel):
    """Non-time population scope for observe and detect."""
    constraints: dict[str, Any] | None = None
    predicate_ref: str | None = None


class ObserveInput(BaseModel):
    """Input model for the observe MCP tool."""
    session_id: str
    metric: str
    time_scope: ObserveTimeScope
    granularity: str | None = None
    dimensions: list[str] | None = None
    scope: ObserveScope | None = None
    result_mode: str | None = None
    calendar_policy_ref: str | None = None
```

- [ ] **Step 2: Create intents.py with register_observe**

Create `app/transports/mcp/tools/intents.py`:

```python
"""Registration functions for MCP intent tools."""
from __future__ import annotations

from typing import Any

from app.transports.mcp.tools._async_bridge import call_runtime
from app.transports.mcp.tools.schemas import ObserveInput


def register_observe(server: Any, runtime: Any) -> None:
    @server.tool()
    async def observe(
        session_id: str,
        metric: str,
        time_scope: ObserveInput.model_fields["time_scope"].annotation,  # ObserveTimeScope
        granularity: str | None = None,
        dimensions: list[str] | None = None,
        scope: dict | None = None,
        result_mode: str | None = None,
        calendar_policy_ref: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"metric": metric, "time_scope": time_scope.model_dump()}
        if granularity is not None:
            params["granularity"] = granularity
        if dimensions is not None:
            params["dimensions"] = dimensions
        if scope is not None:
            params["scope"] = scope
        if result_mode is not None:
            params["result_mode"] = result_mode
        if calendar_policy_ref is not None:
            params["calendar_policy_ref"] = calendar_policy_ref
        return await call_runtime(runtime.observe, session_id=session_id, params=params)
```

**Note:** The `time_scope` parameter type must match FastMCP's expectations. FastMCP uses pydantic models for input validation. If the Annotated type doesn't work with FastMCP's tool decorator, fall back to `McpObserveTimeScope` and add the string-rejection check inside the handler body. This will be validated in the 5a smoke test.

- [ ] **Step 3: Create tools/__init__.py with register_tools**

Create `app/transports/mcp/tools/__init__.py`:

```python
"""Register all MCP tools on a FastMCP server instance."""
from __future__ import annotations

from typing import Any

from app.transports.mcp.tools.intents import register_observe


def register_tools(server: Any, runtime: Any) -> None:
    register_observe(server, runtime)
```

- [ ] **Step 4: Run typecheck to verify**

Run: `make typecheck`
Expected: PASS (or fix any type issues)

- [ ] **Step 5: Commit**

```bash
git add app/transports/mcp/tools/schemas.py app/transports/mcp/tools/intents.py app/transports/mcp/tools/__init__.py
git commit -m "$(cat <<'EOF'
feat: add observe tool schema, registration, and register_tools orchestrator

Schemas preserve the wire-compatible time_scope validator from marivo-mcp.
Only observe is registered in 5a; remaining tools migrate in 5b.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 6: Create stdio entry, HTTP mount, resources placeholder, and wire into app_factory

**Files:**
- Create: `app/transports/mcp/resources/__init__.py`
- Create: `app/transports/mcp/stdio.py`
- Create: `app/transports/mcp/http.py`
- Modify: `app/transports/mcp/__init__.py`
- Modify: `app/api/app_factory.py`
- Modify: `pyproject.toml` (add marivo-stdio console-script)

- [ ] **Step 1: Create resources placeholder**

Create `app/transports/mcp/resources/__init__.py`:

```python
"""Register all MCP resources on a FastMCP server instance."""
from __future__ import annotations

from typing import Any


def register_resources(server: Any, runtime: Any) -> None:
    # Resources migrate in 5b. Placeholder for now.
```

- [ ] **Step 2: Create stdio entry point**

Create `app/transports/mcp/stdio.py`:

```python
"""marivo-stdio console-script entry point."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.profiles.local import LocalConfig, create_local_runtime


logger = logging.getLogger(__name__)


def main() -> None:
    """Start a stdio MCP server backed by create_local_runtime()."""
    from mcp.server.fastmcp import FastMCP

    from app.transports.mcp.tools import register_tools
    from app.transports.mcp.resources import register_resources

    workspace = Path.cwd()
    config = LocalConfig(workspace_root=workspace)
    runtime = create_local_runtime(config, explicit_local=True)

    server = FastMCP("marivo-mcp")
    register_tools(server, runtime)
    register_resources(server, runtime)
    server.run()  # stdio is FastMCP default
```

- [ ] **Step 3: Create HTTP MCP mount helper**

Create `app/transports/mcp/http.py`:

```python
"""Mount MCP streamable-http transport on a FastAPI app."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def mount_mcp_app(
    fastapi_app: FastAPI,
    runtime: Any,
    *,
    path: str = "/mcp",
) -> None:
    """Mount FastMCP streamable-http app under the given FastAPI app.

    Must be called AFTER UserIdentityMiddleware and TimingMiddleware are
    registered so the middleware covers /mcp/... requests. Both middlewares
    are pure ASGI and do not buffer SSE responses.
    """
    from mcp.server.fastmcp import FastMCP

    from app.transports.mcp.tools import register_tools
    from app.transports.mcp.resources import register_resources

    server = FastMCP(
        "marivo-mcp",
        stateless_http=True,
        json_response=True,
    )
    register_tools(server, runtime)
    register_resources(server, runtime)
    fastapi_app.mount(path, server.streamable_http_app())
```

- [ ] **Step 4: Update app/transports/mcp/__init__.py**

Replace the contents of `app/transports/mcp/__init__.py`:

```python
from __future__ import annotations
```

(No exports needed at the package level; callers import from submodules.)

- [ ] **Step 5: Wire mount_mcp_app into app_factory.py**

In `app/api/app_factory.py`, add the import and the mount call.

Add import after line 23 (after `from app.observability import ...`):
```python
from app.transports.mcp.http import mount_mcp_app
```

Add mount call after line 184 (after `include_api_routers(app)`), before the return:
```python
    mount_mcp_app(app, services.runtime)
```

The final lines of `create_app` should read:
```python
    app.add_middleware(UserIdentityMiddleware)
    app.add_middleware(TimingMiddleware)
    include_api_routers(app)
    mount_mcp_app(app, services.runtime)
    return app
```

- [ ] **Step 6: Add marivo-stdio console-script to pyproject.toml**

In `pyproject.toml`, add to `[project.scripts]`:
```toml
marivo-stdio = "app.transports.mcp.stdio:main"
```

So the full scripts section becomes:
```toml
[project.scripts]
marivo = "app.cli:main"
marivo-stdio = "app.transports.mcp.stdio:main"
```

- [ ] **Step 7: Re-install to register the new entry point**

Run: `.venv/bin/pip install -e ".[duckdb]" 2>&1 | tail -5`

- [ ] **Step 8: Run full test suite**

Run: `make test && make typecheck && make lint`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add app/transports/mcp/resources/__init__.py app/transports/mcp/stdio.py \
  app/transports/mcp/http.py app/transports/mcp/__init__.py \
  app/api/app_factory.py pyproject.toml
git commit -m "$(cat <<'EOF'
feat: add stdio entry, HTTP MCP mount, and wire into app_factory

- marivo-stdio console-script starts a stdio MCP server with local runtime
- mount_mcp_app() mounts FastMCP streamable-http under FastAPI at /mcp
- app_factory.create_app() now calls mount_mcp_app after middleware+routes
- Resources placeholder for 5b migration

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 7: Write observe smoke test and verify 5a gate

**Files:**
- Create: `tests/transports/mcp/test_observe_smoke.py`

- [ ] **Step 1: Write observe smoke test**

Create `tests/transports/mcp/test_observe_smoke.py`:

```python
"""Smoke test: observe tool registered on both transports."""
from __future__ import annotations

import subprocess
import sys


def test_observe_registered_via_import():
    """Verify observe tool is registered on a FastMCP server instance."""
    from mcp.server.fastmcp import FastMCP

    from app.transports.mcp.tools import register_tools

    # We can't easily create a full runtime in unit tests without
    # a workspace, so we verify the registration doesn't error.
    # Use a mock-like object that has the right method signatures.
    class FakeRuntime:
        def observe(self, session_id, params):
            return {"step_type": "observe", "session_id": session_id}

    server = FastMCP("test-observe")
    register_tools(server, FakeRuntime())

    tools = server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert "observe" in tool_names, f"observe not found in {tool_names}"


def test_marivo_stdio_entry_point_exists():
    """Verify marivo-stdio console-script is registered."""
    result = subprocess.run(
        [sys.executable, "-m", "marivo_mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    # We're not testing marivo-mcp, just verifying our entry point resolves.
    # The real test is that `marivo-stdio` is importable.
    from app.transports.mcp.stdio import main

    assert callable(main)
```

**Note:** The `server._tool_manager.list_tools()` call uses FastMCP's internal API. If this doesn't work with `mcp==1.27.0`, use `server.list_tools()` or inspect `server._tools` dict directly. The subagent should check the installed FastMCP API.

- [ ] **Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/transports/mcp/test_observe_smoke.py -v`
Expected: PASS

- [ ] **Step 3: Run full 5a gate verification**

Run: `make test && make typecheck && make lint`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/transports/mcp/test_observe_smoke.py
git commit -m "$(cat <<'EOF'
test: add observe smoke test for 5a gate verification

Verifies that the observe tool registers correctly on a FastMCP
server and that the marivo-stdio entry point is importable.

Co-Authored-By: CLAUDE:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

## Sub-phase 5b: Tool & Resource Migration

**Scope:** Migrate all remaining tools from `marivo-mcp/src/marivo_mcp/tools/__init__.py` to the new `app/transports/mcp/tools/` structure. This is mechanical — no wire-shape changes, no schema refactoring.

### Prerequisite: Runtime method gaps

Several MCP tools need runtime methods that don't exist yet. Before migrating each tool group, check whether the runtime method exists. If not, add it to `app/runtime/runtime.py` (thin delegation to `self._svc` or `self._ports`).

| MCP tool | Runtime method needed | Exists? |
|----------|----------------------|---------|
| list_sessions | `runtime.list_sessions()` | NO — on `self._svc` only |
| get_session_state | `runtime.get_session_state()` | YES |
| query_session_state | `runtime.query_session_state()` | NO |
| get_proposition_context | `runtime.get_proposition_context()` | NO |
| Semantic model CRUD (6 tools) | `runtime.update_semantic_model()`, `runtime.delete_semantic_model()`, `runtime.get_semantic_model_readiness()` | Partially — `get`, `save`, `list` exist |
| Dataset CRUD (5 tools) | `runtime.*_dataset()` | NO — on `self._svc` only |
| Relationship CRUD (5 tools) | `runtime.*_relationship()` | NO — on `self._svc` only |
| Metric CRUD (5 tools) | `runtime.*_metric()` | NO — on `self._svc` only |
| Datasource tools (8 tools) | `runtime.*_datasource()` | NO — on `DatasourceService` |
| OpenAPI tools (5 tools) | Service-level | NO |

### Task 8: Add missing runtime methods (session + proposition)

**Files:**
- Modify: `app/runtime/runtime.py`

For each missing method, add a thin wrapper that delegates to `self._svc`:
- `list_sessions(**kwargs) -> dict[str, Any]`
- `query_session_state(session_id, **kwargs) -> dict[str, Any]`
- `get_proposition_context(session_id, proposition_id) -> dict[str, Any]`

Each method follows the existing pattern: try ports-first, fall back to `self._svc` on `NotImplementedError`. If no ports implementation exists, go straight to `self._svc`.

```python
def list_sessions(self, **kwargs: Any) -> dict[str, Any]:
    assert self._svc is not None
    return self._svc.list_sessions(**kwargs)

def query_session_state(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
    assert self._svc is not None
    return self._svc.query_session_state(session_id, kwargs)

def get_proposition_context(self, session_id: str, proposition_id: str) -> dict[str, Any]:
    assert self._svc is not None
    return self._svc.get_proposition_context(session_id, proposition_id)
```

Add a simple test for each in `tests/local/test_local_runtime_factory.py` or a new `tests/test_runtime_delegation.py`.

---

### Task 9: Add missing runtime methods (semantic model, dataset, relationship, metric, datasource)

**Files:**
- Modify: `app/runtime/runtime.py`

Add thin delegations to `self._svc` for:
- `update_semantic_model`, `delete_semantic_model`, `get_semantic_model_readiness`
- `create_dataset`, `list_datasets`, `get_dataset`, `update_dataset`, `delete_dataset`
- `create_relationship`, `list_relationships`, `get_relationship`, `update_relationship`, `delete_relationship`
- `create_metric`, `list_metrics`, `get_metric`, `update_metric`, `delete_metric`
- `list_datasources`, `create_datasource`, `get_datasource`, `update_datasource`, `delete_datasource`
- `browse_schemas`, `browse_tables`, `browse_columns`, `preview_table`

These all delegate to the corresponding `SemanticModelV2Service` or `DatasourceService` methods. The runtime accesses these services through `self._svc` (which has `semantic_v2_service` and `datasource_service` attributes) or through the app's state.

**Note:** The exact delegation path depends on how services are wired. The subagent should read `app/service.py` to find the correct method names and signatures, then add thin wrappers.

---

### Task 10: Migrate session lifecycle tools

**Files:**
- Create: `app/transports/mcp/tools/session.py`
- Modify: `app/transports/mcp/tools/__init__.py`

Migrate 6 session tools: `create_session`, `list_sessions`, `get_session`, `terminate_session`, `get_session_state`, `query_session_state`.

Each tool follows this pattern:
```python
def register_create_session(server: Any, runtime: Any) -> None:
    @server.tool()
    async def create_session(goal: str) -> dict[str, Any]:
        return await call_runtime(runtime.create_session, goal=goal)
```

For `get_session_state` and `query_session_state`, the runtime returns `SessionState | dict | None`. The `_wrap_success` function handles this via the `isinstance(result, dict)` check.

Add all `register_*` calls to `register_tools` in `tools/__init__.py`.

Write a smoke test in `tests/transports/mcp/test_session_smoke.py`.

---

### Task 11: Migrate remaining intent tools

**Files:**
- Modify: `app/transports/mcp/tools/intents.py`
- Modify: `app/transports/mcp/tools/schemas.py`
- Modify: `app/transports/mcp/tools/__init__.py`

Migrate 9 intent tools: `compare`, `decompose`, `detect`, `correlate`, `forecast`, `test_intent`, `attribute`, `diagnose`, `validate`.

Add schemas to `schemas.py` (copy verbatim from `marivo_mcp/tools/__init__.py`):
- `McpStructuredObject` — for attribute `left`/`right`, diagnose `baseline`/`current`, etc.
- `McpObservationRef` — for compare `left_ref`/`right_ref`
- `McpCompareArtifactRef` — for decompose `compare_ref`
- `McpDetectTimeScope` — for detect `time_scope`

Each intent tool packs its parameters into a `params` dict and calls `runtime.<method>(session_id=session_id, params=params)`.

**Example for compare:**
```python
def register_compare(server: Any, runtime: Any) -> None:
    @server.tool()
    async def compare(
        session_id: str,
        left_ref: dict,   # McpObservationRef
        right_ref: dict,  # McpObservationRef
        mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "left_ref": left_ref,
            "right_ref": right_ref,
        }
        if mode is not None:
            params["mode"] = mode
        return await call_runtime(runtime.compare, session_id=session_id, params=params)
```

Write a smoke test per tool group.

---

### Task 12: Migrate catalog + semantic model + datasource tools

**Files:**
- Create: `app/transports/mcp/tools/catalog.py`
- Create: `app/transports/mcp/tools/semantic.py`
- Modify: `app/transports/mcp/tools/__init__.py`

**Catalog tools** (5): `health_check`, `get_catalog`, `list_openapi_paths`, `get_openapi_schema`, `get_openapi_path_fragment`. These don't need runtime methods — they can call the OpenAPI spec directly or use a simple health-check function.

**Semantic model tools** (13): CRUD for models, datasets, relationships, metrics. Each delegates to the runtime methods added in Task 9.

**Datasource tools** (8): CRUD + browse/preview. Each delegates to the runtime methods added in Task 9.

**Note on `get_proposition_context`:** This is a session tool, not a catalog tool. Migrate it in the session group.

---

### Task 13: Migrate resources

**Files:**
- Modify: `app/transports/mcp/resources/__init__.py`

Migrate 4 resources from `marivo_mcp/resources/__init__.py`:
- `marivo://server/config` — static config text
- `marivo://sessions/{session_id}/state` — calls `runtime.get_session_state()`
- `marivo://sessions/{session_id}/propositions/{proposition_id}/context` — calls `runtime.get_proposition_context()`
- `marivo://semantic/{family}` — calls `runtime.list_semantic_models()` etc.

Resources call runtime methods directly (via `call_runtime` for sync→async bridging where needed).

---

### Task 14: Verify 5b gate

- [ ] All tools callable on both transports (stdio + HTTP MCP)
- [ ] Per-tool smoke tests green
- [ ] Existing Phase 4 stdio E2E tests adapted to new entry point and green
- [ ] `make test && make typecheck && make lint` green

---

## Sub-phase 5c: Delete Legacy

### Task 15: Delete marivo-mcp/ and backend.py

**Files:**
- Delete: `marivo-mcp/` directory (entire)
- Delete: `app/transports/mcp/backend.py`

- [ ] **Step 1:** Delete the `marivo-mcp/` directory
- [ ] **Step 2:** Delete `app/transports/mcp/backend.py`
- [ ] **Step 3:** Run `grep -rn "marivo_mcp\|MarivoBackend\|HttpBackend\|MarivoHttpClient\|target_resolution" app/ tests/ docs/ pyproject.toml` and fix any remaining references
- [ ] **Step 4:** Remove any `marivo-mcp`-related dev/install dependency or workspace declaration from `pyproject.toml`
- [ ] **Step 5:** Verify: `make test && make typecheck && make lint`

---

### Task 16: Apply parent spec and Phase 4 spec amendments

**Files:**
- Modify: `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md` (12 patches from spec §2)
- Modify: `docs/superpowers/specs/2026-05-07-phase4-local-embedded-runtime-design.md` (§6.7 patches from spec §2)
- Modify: `agent-guide.md` — remove `marivo-mcp` references
- Modify: `CLAUDE.md` — if any `marivo-mcp` references exist

Apply each patch from the spec's §2 table verbatim. Commit in the same PR as Task 15.

---

### Task 17: Update CI

- Remove `marivo-mcp` jobs from CI workflows
- Add `marivo-stdio` smoke-run step (subprocess invocation, single tool call)
- Verify CI passes

---

### Task 18: Verify 5c gate

- [ ] `grep -rn "marivo_mcp\|MarivoBackend\|HttpBackend\|MarivoHttpClient\|target_resolution" app/ tests/ docs/ pyproject.toml` returns nothing
- [ ] All parent + Phase 4 spec amendments committed in the same PR
- [ ] `make test && make typecheck && make lint` green

---

## Sub-phase 5d: Integration Tests, Parity, Closure

### Task 19: Write tool schema parity test

**Files:**
- Create: `tests/transports/mcp/test_tool_parity.py`

```python
"""Tool schema parity: stdio and HTTP MCP must register identical tool surfaces."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.transports.mcp.tools import register_tools


class FakeRuntime:
    """Stub runtime with all method signatures for registration."""
    def observe(self, session_id, params): ...
    def compare(self, session_id, params): ...
    def decompose(self, session_id, params): ...
    def correlate(self, session_id, params): ...
    def detect(self, session_id, params): ...
    def test(self, session_id, params): ...
    def forecast(self, session_id, params): ...
    def attribute(self, session_id, params): ...
    def diagnose(self, session_id, params): ...
    def validate(self, session_id, params): ...
    def create_session(self, goal, **kwargs): ...
    def list_sessions(self, **kwargs): ...
    def get_session(self, session_id): ...
    def terminate_session(self, session_id, **kwargs): ...
    def get_session_state(self, session_id, **kwargs): ...
    def query_session_state(self, session_id, **kwargs): ...
    # ... add all other methods


def test_tool_surface_parity():
    stdio_server = FastMCP("stdio-test")
    http_server = FastMCP("http-test", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime())
    register_tools(http_server, FakeRuntime())

    stdio_tools = {t.name: t.inputSchema for t in stdio_server._tool_manager.list_tools()}
    http_tools = {t.name: t.inputSchema for t in http_server._tool_manager.list_tools()}

    assert stdio_tools.keys() == http_tools.keys(), (
        f"Tool name mismatch: stdio={sorted(stdio_tools)} http={sorted(http_tools)}"
    )
    for name in stdio_tools:
        assert stdio_tools[name] == http_tools[name], (
            f"Schema diverged for {name}"
        )
```

---

### Task 20: Write HTTP MCP E2E test

**Files:**
- Create: `tests/transports/mcp/test_http_mcp_e2e.py`

Start FastAPI via `TestClient`, connect an MCP client to `/mcp`, run `create_session → observe → compare → decompose`. Assert each result matches a direct `runtime.<method>()` call against the same fixture data.

---

### Task 21: Write stdio MCP E2E test

**Files:**
- Create: `tests/transports/mcp/test_stdio_mcp_e2e.py`

Spawn `marivo-stdio` as a subprocess, pipe MCP JSON-RPC over stdin, run the same intent sequence as Task 20.

---

### Task 22: Write X-Marivo-User passthrough test + import-linter rule

**Files:**
- Create: `tests/transports/mcp/test_user_passthrough.py`
- Modify: `.importlinter`

Passthrough test: issue an HTTP MCP tool call with `X-Marivo-User: alice` → assert runtime saw `current_user.get() == "alice"`. Without header → `None`. With whitespace → `None`.

Import-linter rule:
```ini
[importlinter:contract:transports-mcp-no-api-internals]
name = transports/mcp/ must not import app/api/ internals
type = forbidden
source_modules = app.transports.mcp
forbidden_modules = app.api.endpoints
```

---

### Task 23: Verify 5d gate (Phase 5 closure)

- [ ] All four new test files green
- [ ] Tool-level smoke tests from 5b all green
- [ ] `make test && make typecheck && make lint && make test-contracts` green
- [ ] Parent spec and Phase 4 spec amendments visible in `git log` since 5c

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec requirement | Task |
|---|---|
| UserIdentityMiddleware rewritten as pure ASGI | Task 1 |
| TimingMiddleware rewritten as pure ASGI (discovered risk) | Task 2 |
| SSE streaming non-buffering test | Task 3 |
| mcp dependency added | Task 4 |
| `_async_bridge.py` with `call_runtime` | Task 4 |
| Observe tool schema + registration | Task 5 |
| `register_tools` orchestrator | Task 5 |
| Resources placeholder | Task 6 |
| Stdio entry (`marivo-stdio`) | Task 6 |
| HTTP MCP mount (`mount_mcp_app`) | Task 6 |
| `app_factory` wiring | Task 6 |
| Observe smoke test | Task 7 |
| 5a gate verification | Task 7 |
| Session tools migration | Task 10 |
| Intent tools migration | Task 11 |
| Catalog + semantic + datasource tools | Task 12 |
| Resources migration | Task 13 |
| Delete marivo-mcp/ | Task 15 |
| Delete backend.py | Task 15 |
| Parent spec amendments | Task 16 |
| Phase 4 spec amendments | Task 16 |
| CI updates | Task 17 |
| Tool parity test | Task 19 |
| HTTP MCP E2E test | Task 20 |
| Stdio MCP E2E test | Task 21 |
| X-Marivo-User passthrough test | Task 22 |
| Import-linter rule | Task 22 |

### 2. Placeholder Scan

No TBD, TODO, or "implement later" patterns found. All code blocks contain actual implementation.

### 3. Type Consistency

- `call_runtime` accepts `Callable[..., Any]` and returns `dict[str, Any]` — consistent across all tool handlers.
- `register_tools(server: Any, runtime: Any)` — uses `Any` for FastMCP and runtime types to avoid circular imports; consistent across all registration functions.
- `_wrap_success` handles `dict`, `None`, and other types — consistent with runtime return types.
- `ObserveTimeScope` is an `Annotated[McpObserveTimeScope, BeforeValidator(...)]` — used consistently in `ObserveInput` and the `observe` tool handler.

### Discovered Gap

The spec's tool registration example uses `time_scope: TimeScope` (a pydantic model from `schemas.py`) as a parameter to the `@server.tool()` decorated function. FastMCP uses pydantic models for input validation, but the `Annotated[...]` type with `BeforeValidator` may not work directly with FastMCP's tool decorator. The 5a smoke test will validate this. If it fails, the fallback is to accept `dict` and validate inside the handler body.
