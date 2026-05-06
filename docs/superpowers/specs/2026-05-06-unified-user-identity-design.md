# Unified User Identity Design

## Problem

Marivo has two disconnected user-identity systems and one gap:

- **Datasources**: No user concept. All datasources are global; any client can CRUD any datasource.
- **Semantic models**: `requesting_user` / `owner_user` with public/private visibility. Passed as query params or via `MARIVO_DEFAULT_USER` env fallback.
- **Sessions**: `execution_identity` (`session_user`, `actor_ref`) stored but never used during intent execution.

These systems never interact. A session's `session_user` does not flow into semantic model visibility checks or datasource access control. There is no single source of truth for "who is making this request."

## Design

### 1. Core Identity Infrastructure

**New module**: `app/identity.py`

```python
from contextvars import ContextVar

current_user: ContextVar[str | None] = ContextVar("current_user", default=None)

def resolve_user() -> str | None:
    """Return the current user, falling back to MARIVO_DEFAULT_USER env var.

    Normalizes empty/whitespace-only strings to None so downstream code
    only needs to check for None, not None + empty string.
    """
    user = current_user.get()
    if user is not None:
        user = user.strip()
        if user:
            return user
    import os
    env_user = os.environ.get("MARIVO_DEFAULT_USER")
    if env_user:
        env_user = env_user.strip()
        if env_user:
            return env_user
    return None
```

**New middleware**: `app/api/middleware.py`

```python
from starlette.middleware.base import BaseHTTPMiddleware
from app.identity import current_user

class UserIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        user = request.headers.get("x-marivo-user")
        if user is not None:
            user = user.strip()
            if not user:
                user = None
        token = current_user.set(user)
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)
```

Registered in `app_factory.py` before `TimingMiddleware`.

**Resolution order**:
1. `X-Marivo-User` HTTP header — primary identity source (stripped, empty normalized to None)
2. `MARIVO_DEFAULT_USER` env var — fallback when header absent (stripped, empty normalized to None)
3. `None` — no user (public semantic models visible; all datasources invisible — cannot run queries; sessions inaccessible)

**Nil user behavior**: When `resolve_user()` returns None, the user sees only public semantic models. They cannot see any datasources (all private), which means they cannot execute any queries — even on public semantic models, which require a datasource. This is intentional: any client that needs to run queries must provide a valid user identity.

### 2. Datasource User-Scoping

Breaking change — no backward compatibility. **All datasources are private.** There are no public datasources.

**Schema**: Add `owner_user` (required) to `datasources` table:

```sql
CREATE TABLE datasources (
    datasource_id   TEXT PRIMARY KEY,
    datasource_type TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    connection_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    owner_user      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX idx_datasources_owner ON datasources(owner_user);
CREATE INDEX idx_datasources_name_owner ON datasources(display_name, owner_user);
```

No `visibility` column — all datasources are private by definition. Every datasource has an `owner_user` and is only visible/usable by that owner.

**Access control**:

| Operation | Rule |
|-----------|------|
| LIST | Return only datasources where `owner_user = resolve_user()` |
| GET | Only visible to `owner_user`; 404 for all others |
| CREATE | `owner_user = resolve_user()` — returns 400 if resolve_user() is None |
| UPDATE/DELETE | Only `owner_user = resolve_user()` can mutate |

**Service layer**: `DatasourceService` gains access-control helpers:
- `_require_owned_datasource(datasource_id, owner_user)` — 404 if not owned by user

**Pydantic models**: Add `owner_user` field to `DatasourceRegisterRequest` (defaults to `resolve_user()`) and `DatasourceResponse`.

**MCP tools**: `create_datasource` and `list_datasources` no longer need explicit user params — identity flows from `X-Marivo-User` header through the MCP server's HTTP client.

### 3. Session and Analysis

**Remove `execution_identity`**:
- Remove `execution_identity` field from `SessionCreateRequest`
- Remove `execution_identity_json` column from `sessions` table
- Remove `SessionExecutionIdentityPayload` model
- Remove `_normalize_execution_identity_payload` from `SessionManager`

**Add session ownership**:
- Add `owner_user` column to `sessions` table
- On creation, `owner_user = resolve_user()`
- LIST sessions returns only sessions owned by `resolve_user()`
- GET/state/intent operations check that `resolve_user()` matches `owner_user`

**Intent execution**: User identity from `resolve_user()` automatically flows into semantic model visibility resolution and query execution user substitution. No separate identity passing needed.

### 4. Query Execution User Substitution

All datasources are private. When a semantic model references a datasource that is not owned by the current user, the `user` field in the connection config is replaced with the current user's identity.

**Rule**:
- Datasource `owner_user` == `resolve_user()` → use connection as-is (your own datasource, your own credentials)
- Datasource `owner_user` != `resolve_user()` → replace `user` field in connection with `resolve_user()`

This covers the key scenario: a public semantic model references its creator's datasource. When another user queries that model, the datasource connection's user is substituted so the query runs as the requesting user (enabling per-user auditing and RLS in the downstream database).

**Implementation**: In `DatasourceRegistry._resolve_runtime_connection`:

```python
def _resolve_runtime_connection(self, datasource_row) -> dict:
    connection = dict(datasource_row.get("connection") or {})
    current = resolve_user()
    if current and datasource_row.get("owner_user") != current:
        connection["user"] = current
    return connection
```

Replaces the current Trino-specific check. Works for all engine types. The `user` field in the connection config is the only field substituted — host, port, catalog, schema, etc. remain from the datasource definition.

### 5. Audit Logging

Every access-controlled operation must log the resolved user identity:

- **Access checks**: Log `resolve_user()` value at each `_require_owned_datasource` and `_require_visible_model` call (INFO level for allowed, WARN for denied).
- **Query execution**: Log `resolve_user()` and the datasource `owner_user` at query start. Replaces the existing `ExecutionAuthLoggingEngine` and `marivo.datasource_auth` logger.
- **Session operations**: Log owner_user on session create, list, and intent execution.

All log lines use structured fields: `user=<resolve_user()>`, `resource=<type:id>`, `action=<operation>`.

### 6. API and MCP Tool Changes

**API endpoints**:
- Remove `requesting_user` query parameter from all semantic model endpoints
- Remove `_resolve_requesting_user` helper from `semantic_v2.py`
- All service calls read user from `resolve_user()` internally
- Add `owner_user` field to datasource response models; remove `visibility` from datasource models

**MCP tools**:
- Remove `requesting_user` parameter from all MCP tool definitions (`list_semantic_models`, `get_semantic_model`, `list_datasets`, etc.)
- MCP server passes `X-Marivo-User` header on all HTTP requests to the Marivo API (configured once in MCP server HTTP client setup)
- User identity is transparent to individual tool calls

### 7. Files Changed

| File | Change |
|------|--------|
| `app/identity.py` | New — `current_user` contextvar + `resolve_user()` |
| `app/api/middleware.py` | New — `UserIdentityMiddleware` |
| `app/api/app_factory.py` | Register middleware |
| `app/storage/schema.py` | Add owner_user (required, no visibility) to datasources; add owner_user to sessions; remove execution_identity_json |
| `app/registry/datasource_registry.py` | Add access-control helpers; read user from `resolve_user()`; replace `ExecutionAuthLoggingEngine`; add `owner_user` to `_row_to_datasource`; remove `session_id` param from `_resolve_runtime_connection` and `build_analytics_engine` |
| `app/routing.py` | Remove `session_id` param from `QueryRouter.resolve_tables`, `resolve_route`, `resolve_datasource_for_source` |
| `app/execution/routing_runtime.py` | Remove `session_id` param passthrough in `RoutingRuntime.resolve_tables` |
| `app/service.py` | Remove `session_id` passthrough in `_resolve_engine_for_session` and `_resolve_engine` |
| `app/datasources.py` | Thin facade inherits new access control |
| `app/api/datasources.py` | Remove explicit user params; use `resolve_user()` |
| `app/api/semantic_v2.py` | Remove `requesting_user` params and `_resolve_requesting_user` |
| `app/semantic_service_v2/service.py` | Replace `_resolve_requesting_user` calls with `resolve_user()` |
| `app/api/sessions.py` | Remove execution_identity; use `resolve_user()` for ownership |
| `app/session/session_manager.py` | Remove execution_identity handling; add owner_user |
| `app/api/models/session.py` | Remove `SessionExecutionIdentityPayload` |
| `app/api/models/session_responses.py` | Remove `execution_identity` from response |
| `app/api/models/_infrastructure.py` | Add owner_user to datasource models (no visibility — all private) |
| `marivo-mcp/src/marivo_mcp/tools/__init__.py` | Remove `requesting_user` params from MCP tools |
| `marivo-mcp/src/marivo_mcp/client.py` | Pass `X-Marivo-User` header on all requests |

### 8. Deployment & Migration Strategy

Breaking change with no backward compatibility. Drop and recreate all affected tables:

```sql
DROP TABLE IF EXISTS datasources;
DROP TABLE IF EXISTS sessions;
-- Then create with new schema (see sections 2 and 3)
```

Existing datasources and sessions are lost. Clients must re-register datasources and re-create sessions after deployment.

### 9. Test Plan

All tests use `make test` (repository entrypoint). Tests set `current_user` contextvar directly via `current_user.set()` — no HTTP client needed for service-layer tests.

**Identity infrastructure** (`app/identity.py` + middleware):
- resolve_user() returns header value when set
- resolve_user() falls back to MARIVO_DEFAULT_USER env var
- resolve_user() returns None when both absent
- resolve_user() normalizes empty/whitespace strings to None
- Middleware sets contextvar from X-Marivo-User header
- Middleware normalizes empty header to None

**Datasource access control** (service-layer tests):
- Create datasource with user → owner_user set to resolve_user()
- Create datasource without user → returns 400
- List datasources → returns only owner's datasources
- Get another user's datasource → returns 404
- Update/delete own datasource → succeeds
- Update/delete another user's datasource → returns 404

**Session access control** (service-layer tests):
- Create session with user → owner_user set to resolve_user()
- Create session without user → returns 400
- List sessions → returns only owner's sessions
- Access another user's session → returns 404

**Query execution user substitution** (integration tests):
- Query using own datasource → connection user unchanged
- Query using another user's datasource → connection user replaced with resolve_user()
- Query with no user in connection config → no substitution, no error

**Semantic model visibility** (existing test updates):
- Public model visible to all users (update existing tests to use resolve_user)
- Private model visible only to owner (update existing tests)

### 10. Out of Scope

- Authentication/authorization of the `X-Marivo-User` header value (caller's responsibility)
- Per-dataset or per-metric visibility (remains model-level)
- Row-level security in downstream databases (the `user` field substitution enables this but is not enforced by Marivo)
- MCP server configuration for passing the header (implementation detail, not a design concern)

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | HOLD_SCOPE mode, 0 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 2 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

UNRESOLVED: 0
VERDICT: CEO + ENG CLEARED — ready to implement
