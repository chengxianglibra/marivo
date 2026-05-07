# Phase 5: MCP Dual Mode — Design Spec

**Date:** 2026-05-08
**Status:** Draft
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`
**Phase:** 5 of 7 (execution order 6)
**Companion spec:** `docs/superpowers/specs/2026-05-07-phase6-profile-system-design.md` (independent; see §9)

---

## 1. Overview

Phase 5 collapses the MCP surface to two canonical modes and consolidates all MCP code into the main package:

- **Local mode**: stdio MCP transport with embedded `create_local_runtime()` (single user, in-process).
- **Enterprise mode**: HTTP MCP transport mounted on the same FastAPI app that already serves HTTP API, sharing one in-process `MarivoRuntime`.

Both modes share an identical tool surface registered by one function. The `marivo-mcp/` package is deleted entirely; entry-points are consolidated into the main `marivo` package. All client-side proxy abstractions (`MarivoBackend` Protocol, `EmbeddedBackend` / `HttpBackend`, `MarivoHttpClient`, `target_resolution.py`) are removed: the simplified architecture has no client runtime, no client profile, no `RemoteRuntimeClient` facade. Remote use of marivo from agents is HTTP MCP; remote use from SDK is direct HTTP API. User authentication is **out of scope** for this phase set; `X-Marivo-User` is treated as a propagation header injected by the deploying environment.

### Acceptance Criteria

1. `marivo serve` starts a FastAPI process exposing both `/api/...` (HTTP API) and `/mcp` (HTTP MCP), backed by one shared `MarivoRuntime` instance.
2. `marivo-stdio` (new console-script entry) starts a stdio MCP server backed by `create_local_runtime()` and exposes a tool set structurally identical to HTTP MCP.
3. Tool schema parity test passes: both transports register the same tool names with structurally equal `inputSchema`.
4. HTTP MCP end-to-end integration test passes: an MCP client connects to `/mcp`, executes observe → compare → decompose, results match a direct `runtime.observe()` call.
5. `marivo-mcp/` directory is removed; `app/transports/mcp/backend.py` (`MarivoBackend` / `EmbeddedBackend` / `HttpBackend`) is removed; tools call the runtime directly.
6. Parent spec amendments listed in §2 are merged in the same PR as sub-phase 5c.

### Scope

| In scope | Out of scope |
|----------|-------------|
| `app/transports/mcp/` module: tools, resources, stdio entry, HTTP mount | User authentication / Bearer validation / OIDC integration |
| `mount_mcp_app(fastapi_app, runtime)` helper, called from `app_factory.create_app` | `create_client_runtime()` factory or `RemoteRuntimeClient` adapter |
| Tool migration from `marivo-mcp/src/marivo_mcp/tools/` to `app/transports/mcp/tools/` | New MCP tools or wire-shape changes — Phase 5 preserves existing schemas |
| Resource migration from `marivo-mcp/src/marivo_mcp/resources/` to `app/transports/mcp/resources/` | Real OIDC / RBAC enforcement — `OidcRbacAuthZ` remains a Phase 6 stub |
| Deletion of `marivo-mcp/`, `MarivoBackend` Protocol, `EmbeddedBackend`, `HttpBackend`, `MarivoHttpClient`, `target_resolution.py` | MySQL `SqlSessionStore` (Phase 6e) |
| `marivo-stdio` console-script entry; agents reconfigure to point at it | `app/` → `marivo/` namespace cutover (Phase 7) |
| Tool schema parity test, HTTP MCP and stdio E2E tests | Multi-session multi-tenant identity isolation (depends on auth, deferred) |

### Non-Goals (echoed for clarity)

- Local mode does not start an HTTP service.
- HTTP MCP and HTTP API on the same FastAPI process share one runtime instance — they do not run as independent services.
- The MCP wire contract is not the canonical business contract; HTTP API remains canonical for SDK callers (parent spec §2 principle #7).

---

## 2. Parent Spec Amendments

Phase 5 implements simplifications that retire several invariants in the parent spec. These edits are merged in the same PR as sub-phase 5c (§3.3) so the documentation never points at deleted code.

| Parent spec location | Change |
|---|---|
| §4 package structure tree | Remove the `marivo-mcp/` line from the structure listing. |
| §4 paragraph after the tree | Delete the sentence "`marivo-mcp/` remains the installable compatibility distribution during the migration window and delegates into `marivo/transports/mcp/` once the shared implementation is cut over." Replace with: "All MCP entry-points live in `marivo/transports/mcp/` from Phase 5 onward; no separate compatibility distribution exists." |
| §4 "Compatibility window" paragraph | Delete entirely (no compatibility window remains because no separate package remains). |
| §7 "Client profile" paragraph | Replace with: "There is no client profile. Remote agents connect to the enterprise server via HTTP MCP transport (mounted on the same FastAPI app at `/mcp`). The MCP tool schema is identical between local stdio and enterprise HTTP MCP because both surfaces target the same `MarivoRuntime` semantics. Authorization differences (e.g. enterprise `AuthZ` rejecting an action) appear as standard error responses." |
| §9 "Identity trust boundary" paragraph | Replace with: "User authentication is out of scope for the current phase set. `X-Marivo-User` is a propagation header trusted from the calling environment; marivo passes it through to `current_user` ContextVar without validation. A trusted-edge design (Bearer + token introspection + strip-and-reinject) is deferred to a dedicated future phase. Deploying environments that need real authentication MUST gate marivo behind their own authenticated proxy." |
| §9 "stdio client-mode identity binding" paragraph | Delete entirely. |
| §10 Surfaces — MCP transport / runtime mode table (4 rows) | Reduce to 2 rows: `stdio | embedded | Local single-user`, `HTTP MCP | server | Enterprise managed MCP gateway`. |
| §10 Surfaces — paragraph after the table starting "For `stdio + client` mode..." | Delete entirely. |
| §10 SDK section "Remote client: `create_client_runtime()`" line | Replace with: "Remote use: connect to enterprise `marivo serve` via HTTP API or HTTP MCP transport. There is no `create_client_runtime()` factory." |
| §11 Testing Strategy "Client-mode identity tests" bullet | Delete. |
| §12 Migration table — Phase 5 row | Replace **Deliverable** with "HTTP MCP transport mounted on enterprise FastAPI app; stdio + HTTP MCP share an identical tool registration; legacy `marivo-mcp/` package and all client/proxy abstractions removed." Replace **Acceptance Criteria** with "HTTP MCP end-to-end integration test passes; tool schema parity test passes between stdio and HTTP MCP." |
| §12 Migration table — Phase 7 row | Remove "`marivo-mcp` compatibility wrapper validated in CI"; replace with "no `marivo-mcp` distribution remains (removed in Phase 5); only `app/` → `marivo/` mechanical rename and import boundary enforcement". |
| §12 Migration principles — `marivo-mcp` bullet at the bottom | Delete the bullet "`marivo-mcp` stays installable and backward-compatible through the migration window..." entirely. |
| §13 Non-Goals | Append new bullet: "User authentication and authorization in marivo are out of scope for the current phase set. `X-Marivo-User` is a trusted propagation header injected by the deploying environment; marivo does not validate it. A trusted-edge auth design is deferred to a dedicated future phase." |

**Phase 4 spec amendment** (`docs/superpowers/specs/2026-05-07-phase4-local-embedded-runtime-design.md`):

| Phase 4 spec location | Change |
|---|---|
| §6.7 "Session Lifecycle" — bullets 1 and 2 about implicit `_default_session_id` injection | Replace with: "stdio and HTTP MCP both require explicit `session_id` on every tool call that operates on a session. There is no implicit default. The MCP client's first call must be `create_session`, after which the returned `session_id` is passed to subsequent intent calls. This makes stdio and HTTP MCP wire-identical." |
| §6.7 closing paragraph | Delete the "implicit `session_id` is stored on the `EmbeddedBackend` instance" sentence. |

---

## 3. Sub-phase Sequence

Bottom-up: build new code first, validate end-to-end with a single tool, migrate the rest, then delete legacy in one shot. Each sub-phase ends with CI green; no half-broken intermediate state.

| Sub-phase | Name | Deliverable | Gate |
|-----------|------|-------------|------|
| 5a | Scaffolding + observe E2E | `app/transports/mcp/` module skeleton; `register_tools` / `register_resources` / `mount_mcp_app` callable; `marivo-stdio` console-script; `observe` tool registered on both transports | observe works end-to-end on both stdio and HTTP MCP; legacy `marivo-mcp` stdio still runs unchanged; `make test` / `make typecheck` / `make lint` green |
| 5b | Tool & resource migration | All remaining tools (9 intent + 4 session + ≥2 catalog) and 4 resources registered through the new path | All tools callable on both transports with results matching direct runtime calls; existing Phase 4 stdio E2E tests pass against the new entry point |
| 5c | Delete legacy | `marivo-mcp/` directory removed; `app/transports/mcp/backend.py` removed; parent spec + Phase 4 spec amendments committed in same PR | `grep -rn "marivo_mcp\|MarivoBackend\|HttpBackend\|MarivoHttpClient" app/ tests/ docs/` returns nothing; CI green |
| 5d | Integration tests + parity + closure | Tool schema parity test, HTTP MCP E2E test, stdio E2E test, X-Marivo-User passthrough test, import-linter rule | All four new tests green; full `make test` / `make typecheck` / `make lint` / `make test-contracts` green |

### 3.1 Sub-phase 5a — Scaffolding

**New files:**

```
app/transports/mcp/
  __init__.py
  tools/
    __init__.py        # register_tools(server, runtime)
    schemas.py         # pydantic schemas + validators
    intents.py         # register_observe (only one in 5a)
    _async_bridge.py   # call_runtime helper + DomainError → ToolEnvelope mapping
  resources/
    __init__.py        # register_resources(server, runtime) — empty placeholder
  stdio.py             # main()
  http.py              # mount_mcp_app(fastapi_app, runtime, *, path="/mcp")
```

**Edited files:**

- `app/api/app_factory.py`: `create_app()` invokes `mount_mcp_app(app, runtime)` after middleware registration and before the return.
- `pyproject.toml` (top-level): add `marivo-stdio = "app.transports.mcp.stdio:main"` to `[project.scripts]`.

**Async/sync bridge.** Runtime methods are synchronous; FastMCP handlers are async. The bridge lives in `_async_bridge.py`:

```python
# app/transports/mcp/tools/_async_bridge.py
import asyncio
from collections.abc import Callable
from typing import Any

from app.contracts.errors import (
    ConflictError, DomainError, IntegrityError, NotFoundError, ValidationError,
)


async def call_runtime(method: Callable[..., dict[str, Any]], /, **kwargs: Any) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: method(**kwargs))
        return _wrap_success(result)
    except NotFoundError as e:   return _wrap_error("NOT_FOUND", str(e))
    except ConflictError as e:   return _wrap_error("CONFLICT", str(e))
    except ValidationError as e: return _wrap_error("VALIDATION", str(e))
    except IntegrityError as e:  return _wrap_error("INTEGRITY", str(e))
    except DomainError as e:     return _wrap_error("DOMAIN", str(e))
    except Exception as e:       return _wrap_error("INTERNAL", str(e))


def _wrap_success(result: dict[str, Any]) -> dict[str, Any]:
    return {"data": result, "error": None}


def _wrap_error(code: str, message: str) -> dict[str, Any]:
    return {"data": None, "error": {"code": code, "message": message}}
```

The error-mapping table is the same as Phase 4's `EmbeddedBackend._sync_call` (§7.7 of the Phase 4 spec). Tools never call the runtime without going through `call_runtime`.

**Tool registration shape.**

```python
# app/transports/mcp/tools/intents.py
def register_observe(server: FastMcpServer, runtime: MarivoRuntime) -> None:
    @server.tool()
    async def observe(
        session_id: str,
        metric: str,
        time_scope: TimeScope,  # pydantic model from schemas.py
        ...
    ) -> dict[str, Any]:
        return await call_runtime(
            runtime.observe,
            session_id=session_id,
            metric=metric,
            time_scope=time_scope.model_dump(),
            ...
        )
```

`session_id: str` is required by the Python signature → FastMCP advertises it as required in `inputSchema` → MCP clients that omit it receive a structured invalid-params response without entering the runtime.

**Stdio entry.**

```python
# app/transports/mcp/stdio.py
def main() -> None:
    config = _load_local_config()  # reads .marivo/marivo.toml
    runtime = create_local_runtime(config)
    server = FastMCP("marivo-mcp")
    register_tools(server, runtime)
    register_resources(server, runtime)
    server.run()  # stdio is FastMCP default
```

No implicit session creation. The agent's first MCP call must be `tools/call create_session`.

**HTTP MCP mount.**

```python
# app/transports/mcp/http.py
def mount_mcp_app(
    fastapi_app: FastAPI,
    runtime: MarivoRuntime,
    *,
    path: str = "/mcp",
) -> None:
    server = FastMCP(
        "marivo-mcp",
        stateless_http=True,
        json_response=True,
    )
    register_tools(server, runtime)
    register_resources(server, runtime)
    fastapi_app.mount(path, server.streamable_http_app())
```

The mount call must run **after** `UserIdentityMiddleware` is registered on `fastapi_app`, so the middleware covers `/mcp/...` requests and writes `X-Marivo-User` to the `current_user` ContextVar.

**5a Gate:**

- `marivo serve` runs; an MCP client (e.g. `mcp-inspector` or a unit-test client) connects to `http://127.0.0.1:8000/mcp` and successfully invokes `observe`.
- `marivo-stdio` runs as a subprocess; a JSON-RPC `tools/call observe` over stdin returns the same shape result as the HTTP MCP path.
- Existing `marivo-mcp` stdio command still runs (unchanged) — proves no regression to legacy path during migration.
- `make test` / `make typecheck` / `make lint` green.

### 3.2 Sub-phase 5b — Tool and Resource Migration

**Migration order** (independent items, can be parallelized across PRs):

| Group | Items |
|-------|-------|
| Intents (9) | compare, decompose, detect, correlate, forecast, test, attribute, validate, diagnose |
| Session lifecycle (4) | create_session, get_session, terminate_session, list_sessions |
| Catalog (≥2) | discover_catalog and any other current catalog-style tools in `marivo-mcp/src/marivo_mcp/tools/` |
| Resources (4) | semantic-models, datasets, relationships, metrics (read-only mirrors) |

**Per-item migration template:**

1. Copy the pydantic schema (including all validators) from `marivo-mcp/src/marivo_mcp/tools/__init__.py` into `app/transports/mcp/tools/schemas.py`. Preserve validator behavior verbatim (e.g. `_reject_observe_time_scope_string`, structured-object enforcement for `attribute` left/right, base64-encoded payload handling).
2. Add `register_<tool>(server, runtime)` in the appropriate `tools/<group>.py` (`intents.py`, `session.py`, or `catalog.py`).
3. Append a call to it in `register_tools` (`tools/__init__.py`).
4. Add a smoke test in `tests/transports/mcp/test_<tool>_smoke.py`: invoke the tool via stdio + HTTP MCP, compare against a direct `runtime.<method>()` call.
5. **Forbidden in this sub-phase**: no wire-shape changes, no schema simplification, no validator rewrites. Migration is mechanical.

**Resources** follow the same pattern in `app/transports/mcp/resources/`. Resource handlers also use `call_runtime` for sync→async bridging when they invoke runtime methods (some resources may call `runtime.list_models()` etc. — implement those as needed; the resource migration must not introduce new runtime methods, only consume existing ones).

**5b Gate:** all tools and resources callable on both transports; Phase 4 stdio E2E tests adapted to new entry point and green; per-tool smoke tests green.

### 3.3 Sub-phase 5c — Delete Legacy

**Deletions:**

- `marivo-mcp/` directory in full (source, `pyproject.toml`, `docs/`, etc.).
- `app/transports/mcp/backend.py` (entire file: `MarivoBackend` Protocol, `EmbeddedBackend`, `HttpBackend`, helper wrappers).
- All imports of `marivo_mcp` anywhere in the repo (verified by grep).
- Any reference to `MarivoHttpClient`, `target_resolution.py`, or `mode=remote` in docs and tests.

**Pyproject updates** (top-level `pyproject.toml`):

- Confirm `marivo-stdio = "app.transports.mcp.stdio:main"` is in `[project.scripts]`.
- Remove any `marivo-mcp`-related dev / install dependency or workspace declaration.

**Documentation updates:**

- `agent-guide.md` and `CLAUDE.md`: remove or update any mention of `marivo-mcp` or `marivo serve-local` HTTP-stdio bridging.
- `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`: apply the §2 patch list verbatim.
- `docs/superpowers/specs/2026-05-07-phase4-local-embedded-runtime-design.md`: apply the §6.7 patch.

**CI updates:**

- Remove `marivo-mcp` jobs from CI workflows.
- Add a `marivo-stdio` smoke-run step (subprocess invocation, single tool call).

**5c Gate:**

- `grep -rn "marivo_mcp\|MarivoBackend\|HttpBackend\|MarivoHttpClient\|target_resolution" app/ tests/ docs/ pyproject.toml` returns nothing.
- All parent + Phase 4 spec amendments committed in the same PR.
- `make test` / `make typecheck` / `make lint` green.

### 3.4 Sub-phase 5d — Integration Tests, Parity, Closure

**New tests** under `tests/transports/mcp/`:

```
tests/transports/mcp/
  test_tool_parity.py         # stdio vs HTTP MCP tool surface equality
  test_http_mcp_e2e.py        # FastAPI TestClient + MCP client → /mcp
  test_stdio_mcp_e2e.py       # subprocess marivo-stdio + JSON-RPC over stdin
  test_user_passthrough.py    # X-Marivo-User propagation
  test_<tool>_smoke.py        # one per tool from 5b (already accumulated)
```

**Parity test logic:**

```python
def test_tool_surface_parity(runtime):
    stdio_server = FastMCP("stdio-test")
    http_server = FastMCP("http-test", stateless_http=True, json_response=True)
    register_tools(stdio_server, runtime)
    register_tools(http_server, runtime)

    stdio_tools = {t.name: t.input_schema for t in stdio_server.list_tools()}
    http_tools = {t.name: t.input_schema for t in http_server.list_tools()}

    assert stdio_tools.keys() == http_tools.keys()
    for name in stdio_tools:
        assert stdio_tools[name] == http_tools[name], f"Schema diverged for {name}"
```

(Adjust for FastMCP's actual tool-listing API; the assertion contract is what matters.)

**HTTP MCP E2E:** start FastAPI via `TestClient`, instantiate an MCP client over the test transport, run `create_session → observe → compare → decompose`, assert each result has the expected `step_type` and matches a direct `runtime.<method>()` invocation against the same fixture data.

**Stdio E2E:** spawn `marivo-stdio` as a subprocess (uses Phase 4's existing subprocess test pattern), pipe MCP JSON-RPC, run the same intent sequence.

**X-Marivo-User passthrough test:** issue an HTTP MCP tool call with `X-Marivo-User: alice` header → assert the runtime saw `current_user.get() == "alice"`. Issue without the header → assert `current_user.get() is None`. Issue with `X-Marivo-User: <whitespace>` → asserts `None` (existing middleware behavior). The test explicitly documents that marivo does not validate the header.

**Import-linter rule** (added to `.importlinter`):

```ini
[importlinter:contract:transports-mcp-no-api-internals]
name = transports/mcp/ must not import app/api/ internals
type = forbidden
source_modules = app.transports.mcp
forbidden_modules = app.api.endpoints
```

This prevents transport code from leaking into HTTP-API business handlers. (The mount helper does take a `FastAPI` app reference, but only to call `app.mount(...)` — it does not import endpoint modules.)

**5d Gate** (Phase 5 closure):

- All four new test files green.
- Tool-level smoke tests from 5b all green.
- `make test` / `make typecheck` / `make lint` / `make test-contracts` green.
- Parent spec and Phase 4 spec amendments visible in `git log` since 5c.
- `TODOS.md` Phase 6 items untouched.

---

## 4. Code Structure (Final)

After Phase 5 closes, the MCP-relevant tree is:

```
app/
  transports/
    mcp/
      __init__.py
      stdio.py             # marivo-stdio entry: create_local_runtime + register + server.run
      http.py              # mount_mcp_app(fastapi_app, runtime)
      tools/
        __init__.py        # register_tools(server, runtime) — imports all register_* below
        schemas.py         # pydantic input/output models + validators
        _async_bridge.py   # call_runtime, _wrap_success, _wrap_error
        intents.py         # register_observe / compare / decompose / ... (10)
        session.py         # register_create_session / get_session / terminate_session / list_sessions
        catalog.py         # register_discover_catalog / ...
      resources/
        __init__.py        # register_resources(server, runtime)
        semantic_models.py # register_semantic_models_resource (and the 3 subordinate resources)
  api/
    app_factory.py         # create_app calls mount_mcp_app(app, runtime) after middleware
  cli/
    cmd_serve.py           # unchanged — uvicorn runs app.main:app which builds via app_factory
```

`app/transports/mcp/backend.py` is **deleted** (no `MarivoBackend` Protocol, no backends).
`marivo-mcp/` is **deleted** (no separate package).

---

## 5. Tool Migration Mechanics

### 5.1 Schema Preservation

The current `marivo-mcp/src/marivo_mcp/tools/__init__.py` contains pydantic models with custom validators that encode **wire compatibility decisions** — e.g. `_reject_observe_time_scope_string` rejects shorthand string time-scopes with a specific error code (`observe_time_scope_canonical_required`) and a specific user-facing message about half-open intervals. These validators are part of the wire contract: agents that have learned the canonical shape rely on the exact rejection behavior.

Migration rule: validators are copied **verbatim** into `tools/schemas.py`. Renames are limited to module path; identifiers, error codes, and user-facing messages do not change. Refactoring schemas is explicitly forbidden in 5b — if a schema looks awkward, file a TODO and refactor in a separate phase.

### 5.2 Stdio / HTTP MCP Wire Identity

After Phase 5, the only differences between stdio and HTTP MCP are:

- **Transport**: stdio (line-delimited JSON-RPC over stdin/stdout) vs. streamable-http (SSE-based JSON-RPC over HTTP).
- **Runtime source**: `create_local_runtime()` vs. the FastAPI process's existing runtime (which is `create_runtime_from_service()` until Phase 6, then `create_server_runtime()` after).

Tool names, `inputSchema`, `outputSchema`, validation behavior, error envelopes, and `session_id` requirements are 100% identical. The parity test in 5d makes this an enforced invariant going forward.

### 5.3 Resource Migration

Resources are read-only; in the current `marivo-mcp` they call HTTP API paths. Post-migration they call `runtime.<method>()` for the same data. This means some resources may need a corresponding runtime method (e.g. `runtime.list_models()`). If a needed runtime method does not exist, **stop and add it in `app/runtime/`** before migrating the resource — do not work around by re-importing storage repos into the resource module. Adding runtime methods is a small, in-scope deviation; importing storage from transports is a hard violation of parent spec §2 principle #3.

---

## 6. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| FastMCP `streamable_http_app()` and FastAPI mounting have ASGI/SSE keep-alive incompatibilities | Medium | High | 5a's HTTP MCP smoke test is the first end-to-end check; if mounting fails, fall back to mounting via Starlette `Mount` directly or run the MCP ASGI app as a sibling sub-app on the same process. Either fallback preserves the "one process, one runtime" invariant. |
| Same-process MCP multi-session concurrency over single MySQL/SQLite connection pool causes contention | Medium | Medium | Runtime methods already run in a thread executor (Phase 4 pattern). Per-call short connections (Phase 4 spec §3.5) eliminate session-bound state. If contention shows up under load, document and address in Phase 7. |
| Parent spec amendments and 5c deletion land in different PRs, leaving docs pointing at deleted code | Medium | Low | Hard rule: 5c PR description must include a checklist of all 12 parent-spec patches and the Phase 4 §6.7 patch; reviewer blocks merge until checked. |
| Schema validators with non-obvious behavior (e.g. `BeforeValidator` ordering, custom error codes) get subtly altered during migration | Low | Medium | 5b template forbids combined "migrate + refactor" PRs. Each tool's smoke test invokes the validator with its known-bad inputs and asserts the same error code surfaces. |
| Middleware ordering: HTTP MCP mounted before `UserIdentityMiddleware` registration → `current_user` not set on `/mcp` requests | Low | Medium | `mount_mcp_app` documents the required ordering; `app_factory.create_app` enforces it; 5d's `test_user_passthrough.py` covers `/mcp` path explicitly. |
| Phase 5 lands before Phase 6, leaving server runtime as `create_runtime_from_service()` (still tied to `SemanticLayerService`) | High | Low | Acceptable: HTTP MCP only requires *some* runtime; switching its source is a one-line change in `app_factory` when Phase 6 ships. |
| Phase 4 stdio E2E tests rely on implicit `_default_session_id` and break when 5b removes it | High | Low | Test updates are part of 5b's session-tool migration: any test that called intents without first calling `create_session` is rewritten to do so. |
| Agents with hard-coded `marivo-mcp` command in their MCP config break on upgrade | Low | None | Service is not launched; no installed agents depend on the legacy entry-point. Documentation update in 5c covers any internal/dev configurations. |

---

## 7. Testing Strategy

### 7.1 Unit Tests

- `tools/schemas.py` validators: parameter-bound tests for each non-trivial validator (especially the time_scope canonical-shape and JSON-string rejection paths). These are the wire contract.
- `_async_bridge.call_runtime`: error-mapping table tests (every `DomainError` subclass → expected envelope code).

### 7.2 Smoke Tests (per-tool, accumulated in 5b)

For each of the ~16 migrated tools and 4 resources: call via stdio, call via HTTP MCP, call runtime directly. Assert all three produce the same data envelope (modulo timestamps and uuids).

### 7.3 Integration Tests (5d)

- `test_tool_parity.py`: structural equality of tool surfaces.
- `test_http_mcp_e2e.py`: full intent chain via FastAPI TestClient.
- `test_stdio_mcp_e2e.py`: full intent chain via subprocess + JSON-RPC.
- `test_user_passthrough.py`: `X-Marivo-User` propagation behavior on `/mcp` endpoints, including the absence and whitespace cases.

### 7.4 Import-Linter

- `transports-mcp-no-api-internals` (added in 5d).
- Existing `core/` boundary contracts unchanged.

### 7.5 CI

- All four integration tests are required (no "best effort" tier).
- `marivo-stdio` smoke-run step replaces any prior `marivo-mcp` jobs.
- `make test` covers the full Phase 5 suite without external dependencies (no MySQL / Docker required for Phase 5 closure; MySQL is Phase 6's CI requirement).

---

## 8. Coordination with Phase 6

Phase 5 and Phase 6 share several touch points but are independently deployable.

| Touch point | Phase 5 assumption | Phase 6 contribution |
|---|---|---|
| Server runtime construction | `mount_mcp_app(runtime)` is runtime-source-agnostic. Phase 5 wires it through whatever the FastAPI app currently uses. | Phase 6c switches `app_factory` to use `create_server_runtime()`; no Phase 5 code changes. |
| `AuthZ` adapter | Not invoked at the MCP layer (no auth in this phase set). | `AlwaysAllowAuthZ` continues to be the server profile's stub; Phase 5 does not depend on it. |
| `SqlSessionStore` | HTTP MCP session events go through whatever the server profile provides (Phase-6-pre: `SemanticLayerService`-backed; Phase-6e+: native event-sourced). | Phase 6e's switch is invisible to Phase 5 because session writes happen through `runtime.create_session` etc. |
| Tool surface | Defined by `register_tools(server, runtime)` and bound to runtime methods. | Phase 6's `_svc` removal does not change runtime method signatures, so Phase 5 tool registration is stable across the Phase 6 cutover. |

If Phase 5 ships before Phase 6: HTTP MCP is fully functional but routes through the legacy `SemanticLayerService` storage path. The MCP wire surface is stable; only the runtime's internals change later. If Phase 6 ships before Phase 5: `create_server_runtime()` exists but no remote MCP transport hosts it — HTTP API is the only remote surface until Phase 5 adds HTTP MCP. Either order is supported.

---

## 9. Open Items (Non-Blocking)

These are Phase-5-adjacent items deliberately deferred:

- **Trusted-edge auth design** (Bearer + token introspection + strip-and-reinject for `X-Marivo-User`): a separate phase, not yet scheduled.
- **Multi-tenant identity isolation** for shared HTTP MCP sessions: depends on auth.
- **Streamable-HTTP transport tuning** (uvicorn keep-alive, worker timeout for SSE): operational concern, captured in deployment docs after 5d.
- **MCP rate limiting and abuse controls**: not in scope; environment proxy responsibility.
- **`mcp-inspector` / agent compatibility matrix testing**: covered by 5d's E2E tests against FastMCP's reference behavior; no separate compatibility CI.
