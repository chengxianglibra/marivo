# Unified User Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify user identity across datasources, semantic models, and sessions using `X-Marivo-User` header + Python contextvars, replacing three disconnected identity systems with one.

**Architecture:** A `current_user` ContextVar set by `UserIdentityMiddleware` from the `X-Marivo-User` HTTP header (falling back to `MARIVO_DEFAULT_USER` env var). `resolve_user()` is the single source of truth. All datasources become private with required `owner_user`. Sessions replace `execution_identity` with `owner_user`. Query execution substitutes `connection.user` when the datasource owner differs from the current user.

**Tech Stack:** Python 3.12+, FastAPI/Starlette, contextvars, SQLite/MySQL, httpx (MCP client)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `app/identity.py` | NEW — `current_user` ContextVar + `resolve_user()` |
| `app/api/middleware.py` | NEW — `UserIdentityMiddleware` extracts `X-Marivo-User` header |
| `app/api/app_factory.py` | MODIFY — register middleware before `TimingMiddleware` |
| `app/storage/schema.py` | MODIFY — add `owner_user` to `datasources` and `sessions` tables; remove `execution_identity_json`; bump schema version |
| `app/registry/datasource_registry.py` | MODIFY — add access control; replace `_resolve_runtime_connection`; remove `ExecutionAuthLoggingEngine`; add `owner_user` to `_row_to_datasource` |
| `app/routing.py` | MODIFY — remove `session_id` param from `resolve_tables`, `resolve_route`, `resolve_datasource_for_source` |
| `app/execution/routing_runtime.py` | MODIFY — remove `session_id` param from `resolve_tables` |
| `app/service.py` | MODIFY — remove `session_id` param from `_resolve_engine`, `_resolve_engine_for_session`, and all callers |
| `app/datasources.py` | MODIFY (no code changes needed — thin facade inherits from `DatasourceRegistry`) |
| `app/api/datasources.py` | MODIFY — use `resolve_user()` for ownership; add 400 on nil user for CREATE; add 404 for non-owner GET/UPDATE/DELETE; filter LIST by owner |
| `app/api/semantic_v2.py` | MODIFY — remove `requesting_user` params and `_resolve_requesting_user` helper |
| `app/semantic_service_v2/service.py` | MODIFY — replace `requesting_user` params with `resolve_user()` calls |
| `app/api/sessions.py` | MODIFY — remove `execution_identity` from create; use `resolve_user()` for ownership |
| `app/session/session_manager.py` | MODIFY — remove `execution_identity` handling; add `owner_user` |
| `app/api/models/session.py` | MODIFY — remove `SessionExecutionIdentityPayload`; remove `execution_identity` from `SessionCreateRequest` |
| `app/api/models/session_responses.py` | MODIFY — remove `execution_identity` from `AnalysisSession`; add `owner_user` |
| `app/api/models/_infrastructure.py` | MODIFY — add `owner_user` to datasource models |
| `marivo-mcp/src/marivo_mcp/config.py` | MODIFY — add `user` field to `MarivoMcpConfig` |
| `marivo-mcp/src/marivo_mcp/http_client.py` | MODIFY — pass `X-Marivo-User` header from config |
| `marivo-mcp/src/marivo_mcp/tools/__init__.py` | MODIFY — remove `requesting_user` params from all 8 MCP tools |

---

### Task 1: Core Identity Infrastructure

**Files:**
- Create: `app/identity.py`
- Test: `tests/test_identity.py`

- [ ] **Step 1: Write the failing test for `resolve_user()`**

Create `tests/test_identity.py`:

```python
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.identity import current_user, resolve_user


class TestResolveUser:
    def test_returns_contextvar_value_when_set(self):
        token = current_user.set("alice")
        try:
            assert resolve_user() == "alice"
        finally:
            current_user.reset(token)

    def test_falls_back_to_env_var(self):
        token = current_user.set(None)
        try:
            with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "env_user"}):
                assert resolve_user() == "env_user"
        finally:
            current_user.reset(token)

    def test_returns_none_when_both_absent(self):
        token = current_user.set(None)
        try:
            with patch.dict(os.environ, {}, clear=True):
                assert resolve_user() is None
        finally:
            current_user.reset(token)

    def test_normalizes_whitespace_only_to_none(self):
        token = current_user.set("   ")
        try:
            assert resolve_user() is None
        finally:
            current_user.reset(token)

    def test_strips_whitespace_from_contextvar(self):
        token = current_user.set("  alice  ")
        try:
            assert resolve_user() == "alice"
        finally:
            current_user.reset(token)

    def test_strips_whitespace_from_env_var(self):
        token = current_user.set(None)
        try:
            with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "  env_user  "}):
                assert resolve_user() == "env_user"
        finally:
            current_user.reset(token)

    def test_empty_env_var_falls_through_to_none(self):
        token = current_user.set(None)
        try:
            with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": ""}):
                assert resolve_user() is None
        finally:
            current_user.reset(token)

    def test_contextvar_takes_priority_over_env(self):
        token = current_user.set("context_user")
        try:
            with patch.dict(os.environ, {"MARIVO_DEFAULT_USER": "env_user"}):
                assert resolve_user() == "context_user"
        finally:
            current_user.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/test_identity.py'`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.identity'`

- [ ] **Step 3: Write `app/identity.py`**

Create `app/identity.py`:

```python
from __future__ import annotations

import os
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
    env_user = os.environ.get("MARIVO_DEFAULT_USER")
    if env_user:
        env_user = env_user.strip()
        if env_user:
            return env_user
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test TESTS='tests/test_identity.py'`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/identity.py tests/test_identity.py
git commit -m "feat: add resolve_user() with contextvar and env fallback"
```

---

### Task 2: UserIdentityMiddleware

**Files:**
- Create: `app/api/middleware.py`
- Modify: `app/api/app_factory.py:177`
- Test: `tests/test_middleware.py`

- [ ] **Step 1: Write the failing test for middleware**

Create `tests/test_middleware.py`:

```python
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.identity import current_user, resolve_user


def _app_with_middleware():
    from app.api.app_factory import create_app

    return create_app(db_path=":memory:")


class TestUserIdentityMiddleware:
    def test_sets_contextvar_from_header(self):
        app = _app_with_middleware()
        client = TestClient(app)
        # Use any endpoint that reads resolve_user — list datasources is safe
        response = client.get("/datasources", headers={"X-Marivo-User": "alice"})
        # We just need to verify the middleware runs without error
        assert response.status_code == 200

    def test_empty_header_treated_as_none(self):
        app = _app_with_middleware()
        client = TestClient(app)
        response = client.get("/datasources", headers={"X-Marivo-User": ""})
        # Empty header should not crash — resolves to None
        assert response.status_code == 200

    def test_whitespace_header_treated_as_none(self):
        app = _app_with_middleware()
        client = TestClient(app)
        response = client.get("/datasources", headers={"X-Marivo-User": "   "})
        assert response.status_code == 200

    def test_no_header_no_error(self):
        app = _app_with_middleware()
        client = TestClient(app)
        response = client.get("/datasources")
        assert response.status_code == 200

    def test_header_value_stripped(self):
        app = _app_with_middleware()
        client = TestClient(app)
        response = client.get("/datasources", headers={"X-Marivo-User": "  alice  "})
        assert response.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/test_middleware.py'`
Expected: FAIL — middleware not registered yet, but endpoint still works (200). The real failure would be if the middleware is missing and we verify it's loaded. These tests are acceptance tests — they pass once middleware is wired.

- [ ] **Step 3: Create `app/api/middleware.py`**

Create `app/api/middleware.py`:

```python
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.identity import current_user


class UserIdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
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

- [ ] **Step 4: Register middleware in `app/api/app_factory.py`**

In `app/api/app_factory.py`, add the import and middleware registration before `TimingMiddleware`:

Add import at top:
```python
from app.api.middleware import UserIdentityMiddleware
```

Change line 177 from:
```python
app.add_middleware(TimingMiddleware)
```
to:
```python
app.add_middleware(UserIdentityMiddleware)
app.add_middleware(TimingMiddleware)
```

Note: Starlette processes middleware in reverse registration order, so `UserIdentityMiddleware` (registered first) runs before `TimingMiddleware` in the request chain.

- [ ] **Step 5: Run test to verify it passes**

Run: `make test TESTS='tests/test_middleware.py'`
Expected: PASS (5 tests)

- [ ] **Step 6: Run existing tests to check for regressions**

Run: `make test`
Expected: All existing tests pass (middleware does not break anything — it just sets a contextvar that no code reads yet).

- [ ] **Step 7: Commit**

```bash
git add app/api/middleware.py app/api/app_factory.py tests/test_middleware.py
git commit -m "feat: add UserIdentityMiddleware for X-Marivo-User header"
```

---

### Task 3: Schema Changes — Datasources and Sessions

**Files:**
- Modify: `app/storage/schema.py:16-30` (sessions table), `app/storage/schema.py:72-81` (datasources table), indexes
- Test: `tests/test_storage.py` (existing — metadata template will need bumping)

- [ ] **Step 1: Update `sessions` table DDL in `app/storage/schema.py`**

In the `sessions` table DDL (lines 16-30), replace `execution_identity_json TEXT NOT NULL DEFAULT '{}'` with `owner_user TEXT NOT NULL`:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id               TEXT PRIMARY KEY,
    goal                     TEXT NOT NULL,
    constraints_json         TEXT NOT NULL,
    budget_json              TEXT NOT NULL,
    owner_user               TEXT NOT NULL,
    status                   TEXT NOT NULL,
    raw_filter               TEXT,
    terminal_reason          TEXT,
    ended_at                 TEXT,
    rollover_from_session_id TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
)
```

Add index after the sessions indexes (after line 68):
```sql
"CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user)",
```

- [ ] **Step 2: Update `datasources` table DDL in `app/storage/schema.py`**

Replace the `datasources` table DDL (lines 72-81) with:

```sql
CREATE TABLE IF NOT EXISTS datasources (
    datasource_id   TEXT PRIMARY KEY,
    datasource_type TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    connection_json TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'active',
    owner_user      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
```

Add indexes after the datasource table:
```sql
"CREATE INDEX IF NOT EXISTS idx_datasources_owner ON datasources(owner_user)",
"CREATE INDEX IF NOT EXISTS idx_datasources_name_owner ON datasources(display_name, owner_user)",
```

- [ ] **Step 3: Bump schema version**

Change `METADATA_SCHEMA_VERSION` (line 10) from:
```python
METADATA_SCHEMA_VERSION = "metadata.osi_v2_additive.v2"
```
to:
```python
METADATA_SCHEMA_VERSION = "metadata.unified_identity.v1"
```

- [ ] **Step 4: Update test metadata template version**

If `tests/shared_fixtures.py` has a metadata template version constant, bump it so cached templates rebuild.

- [ ] **Step 5: Run tests to identify breakage**

Run: `make test`
Expected: Many test failures — session creation and datasource creation now require `owner_user` and `execution_identity_json` is gone. This is expected. Fix in subsequent tasks.

- [ ] **Step 6: Commit**

```bash
git add app/storage/schema.py
git commit -m "refactor: add owner_user to datasources and sessions, remove execution_identity_json"
```

---

### Task 4: Datasource Registry — Access Control and User Substitution

**Files:**
- Modify: `app/registry/datasource_registry.py`
- Test: `tests/test_datasources.py`

- [ ] **Step 1: Write failing tests for datasource access control**

Add to `tests/test_datasources.py`:

```python
from app.identity import current_user


class TestDatasourceAccessControl:
    def test_create_sets_owner_user(self, metadata_store):
        token = current_user.set("alice")
        try:
            from app.registry.datasource_registry import DatasourceRegistry

            reg = DatasourceRegistry(metadata_store)
            ds = reg.register_datasource("duckdb", "test_ds", {"database": ":memory:"})
            assert ds["owner_user"] == "alice"
        finally:
            current_user.reset(token)

    def test_create_without_user_raises(self, metadata_store):
        token = current_user.set(None)
        try:
            from app.registry.datasource_registry import DatasourceRegistry

            reg = DatasourceRegistry(metadata_store)
            with pytest.raises(ValueError, match="user_required"):
                reg.register_datasource("duckdb", "test_ds", {"database": ":memory:"})
        finally:
            current_user.reset(token)

    def test_list_filters_by_owner(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token_a = current_user.set("alice")
        try:
            reg.register_datasource("duckdb", "alice_ds", {"database": ":memory:"})
        finally:
            current_user.reset(token_a)

        token_b = current_user.set("bob")
        try:
            reg.register_datasource("duckdb", "bob_ds", {"database": ":memory:"})
        finally:
            current_user.reset(token_b)

        token_a2 = current_user.set("alice")
        try:
            ds_list = reg.list_datasources()
            assert len(ds_list) == 1
            assert ds_list[0]["display_name"] == "alice_ds"
        finally:
            current_user.reset(token_a2)

    def test_get_other_owners_datasource_raises(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token_a = current_user.set("alice")
        try:
            ds = reg.register_datasource("duckdb", "alice_ds", {"database": ":memory:"})
            ds_id = ds["datasource_id"]
        finally:
            current_user.reset(token_a)

        token_b = current_user.set("bob")
        try:
            with pytest.raises(KeyError):
                reg.get_datasource(ds_id)
        finally:
            current_user.reset(token_b)

    def test_update_other_owners_datasource_raises(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token_a = current_user.set("alice")
        try:
            ds = reg.register_datasource("duckdb", "alice_ds", {"database": ":memory:"})
            ds_id = ds["datasource_id"]
        finally:
            current_user.reset(token_a)

        token_b = current_user.set("bob")
        try:
            with pytest.raises(KeyError):
                reg.update_datasource(ds_id, display_name="hacked")
        finally:
            current_user.reset(token_b)

    def test_delete_other_owners_datasource_raises(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token_a = current_user.set("alice")
        try:
            ds = reg.register_datasource("duckdb", "alice_ds", {"database": ":memory:"})
            ds_id = ds["datasource_id"]
        finally:
            current_user.reset(token_a)

        token_b = current_user.set("bob")
        try:
            with pytest.raises(KeyError):
                reg.delete_datasource(ds_id)
        finally:
            current_user.reset(token_b)


class TestQueryExecutionUserSubstitution:
    def test_own_datasource_connection_unchanged(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token = current_user.set("alice")
        try:
            ds = reg.register_datasource(
                "trino", "trino_ds", {"host": "localhost", "user": "alice", "port": 8080}
            )
            conn = reg._resolve_runtime_connection(ds)
            assert conn["user"] == "alice"
        finally:
            current_user.reset(token)

    def test_other_owners_datasource_user_replaced(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token_a = current_user.set("alice")
        try:
            ds = reg.register_datasource(
                "trino", "trino_ds", {"host": "localhost", "user": "alice", "port": 8080}
            )
        finally:
            current_user.reset(token_a)

        token_b = current_user.set("bob")
        try:
            conn = reg._resolve_runtime_connection(ds)
            assert conn["user"] == "bob"
        finally:
            current_user.reset(token_b)

    def test_duckdb_no_user_field_no_substitution(self, metadata_store):
        from app.registry.datasource_registry import DatasourceRegistry

        reg = DatasourceRegistry(metadata_store)
        token = current_user.set("alice")
        try:
            ds = reg.register_datasource("duckdb", "duckdb_ds", {"database": ":memory:"})
            conn = reg._resolve_runtime_connection(ds)
            assert "user" not in conn
        finally:
            current_user.reset(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test TESTS='tests/test_datasources.py::TestDatasourceAccessControl'`
Expected: FAIL — `register_datasource` does not check `resolve_user()` or set `owner_user`

- [ ] **Step 3: Implement datasource access control in `app/registry/datasource_registry.py`**

**3a.** Add import at top:
```python
from app.identity import resolve_user
```

**3b.** Replace `register_datasource` (lines 93-125) — add `owner_user` column and nil-user guard:

```python
def register_datasource(
    self,
    datasource_type: str,
    display_name: str,
    connection: dict[str, Any],
) -> dict[str, Any]:
    validate_datasource_type(datasource_type)
    owner_user = resolve_user()
    if owner_user is None:
        raise ValueError("user_required: cannot create datasource without user identity")

    datasource_id = f"ds_{uuid4().hex[:12]}"
    now = now_iso()
    self.metadata.execute(
        """
        INSERT INTO datasources (
            datasource_id,
            datasource_type,
            display_name,
            connection_json,
            status,
            owner_user,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        [
            datasource_id,
            datasource_type,
            display_name,
            json.dumps(connection),
            owner_user,
            now,
            now,
        ],
    )
    return self.get_datasource(datasource_id)
```

**3c.** Replace `get_datasource` (lines 127-133) — add ownership check:

```python
def get_datasource(self, datasource_id: str) -> dict[str, Any]:
    row = self.metadata.query_one(
        "SELECT * FROM datasources WHERE datasource_id = ?", [datasource_id]
    )
    if row is None:
        raise KeyError(f"Unknown datasource: {datasource_id}")
    ds = self._row_to_datasource(row)
    self._require_owned_datasource(ds)
    return ds
```

**3d.** Replace `list_datasources` (lines 135-137) — filter by owner:

```python
def list_datasources(self) -> list[dict[str, Any]]:
    owner_user = resolve_user()
    if owner_user is None:
        return []
    rows = self.metadata.query_rows(
        "SELECT * FROM datasources WHERE owner_user = ? ORDER BY created_at",
        [owner_user],
    )
    return [self._row_to_datasource(row) for row in rows]
```

**3e.** Replace `ensure_datasource` (lines 139-171) — add owner_user to upsert and ownership check:

```python
def ensure_datasource(
    self,
    datasource_type: str,
    display_name: str,
    connection: dict[str, Any],
) -> dict[str, Any]:
    validate_datasource_type(datasource_type)
    owner_user = resolve_user()
    if owner_user is None:
        raise ValueError("user_required: cannot create datasource without user identity")
    existing = self.metadata.query_one(
        "SELECT * FROM datasources WHERE display_name = ? AND owner_user = ?",
        [display_name, owner_user],
    )
    if existing is None:
        return self.register_datasource(
            datasource_type,
            display_name,
            connection,
        )
    self._require_owned_datasource(self._row_to_datasource(existing))

    now = now_iso()
    self.metadata.execute(
        """
        UPDATE datasources
        SET datasource_type = ?, connection_json = ?, updated_at = ?
        WHERE datasource_id = ?
        """,
        [
            datasource_type,
            json.dumps(connection),
            now,
            existing["datasource_id"],
        ],
    )
    return self.get_datasource(str(existing["datasource_id"]))
```

**3f.** Replace `update_datasource` (lines 173-198) — add ownership check:

```python
def update_datasource(
    self,
    datasource_id: str,
    display_name: str | None = None,
    connection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = self.get_datasource(datasource_id)
    updates: list[str] = []
    params: list[Any] = []

    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name)
    if connection is not None:
        updates.append("connection_json = ?")
        params.append(json.dumps(connection))

    if not updates:
        return existing

    params.extend([now_iso(), datasource_id])
    self.metadata.execute(
        f"UPDATE datasources SET {', '.join(updates)}, updated_at = ? WHERE datasource_id = ?",
        params,
    )
    return self.get_datasource(datasource_id)
```

Note: `get_datasource` already does the ownership check, so `update_datasource` inherits it.

**3g.** Replace `delete_datasource` (lines 200-202) — add ownership check:

```python
def delete_datasource(self, datasource_id: str) -> None:
    self.get_datasource(datasource_id)
    self.metadata.execute("DELETE FROM datasources WHERE datasource_id = ?", [datasource_id])
```

Note: `get_datasource` already does the ownership check, so `delete_datasource` inherits it.

**3h.** Replace `_resolve_runtime_connection` (lines 363-378) — universal user substitution:

```python
def _resolve_runtime_connection(self, datasource: dict[str, Any]) -> dict[str, Any]:
    connection = dict(datasource.get("connection") or {})
    current = resolve_user()
    if current and datasource.get("owner_user") != current:
        connection["user"] = current
    return connection
```

**3i.** Remove `session_id` parameter from `build_analytics_engine` (lines 346-361):

```python
def build_analytics_engine(
    self,
    datasource_id: str,
) -> AnalyticsEngine:
    from app.registry.factories import build_analytics_engine as _build_analytics_engine

    datasource = self.get_datasource(datasource_id)
    connection = self._resolve_runtime_connection(datasource)
    return _build_analytics_engine(datasource["datasource_type"], connection)
```

Note: This also removes `ExecutionAuthLoggingEngine` and `_RuntimeConnectionResolution` — they are no longer needed. Audit logging is handled separately.

**3j.** Add `_require_owned_datasource` helper method:

```python
def _require_owned_datasource(self, datasource: dict[str, Any]) -> None:
    owner_user = datasource.get("owner_user")
    current = resolve_user()
    if current is None or owner_user != current:
        raise KeyError(f"Unknown datasource: {datasource['datasource_id']}")
```

**3k.** Update `_row_to_datasource` (lines 384-401) — add `owner_user`:

```python
def _row_to_datasource(self, row: dict[str, Any]) -> dict[str, Any]:
    datasource_type = str(row["datasource_type"])
    raw_connection = _loads_stored_json(row["connection_json"])
    connection = raw_connection if isinstance(raw_connection, dict) else {}

    datasource: dict[str, Any] = {
        "datasource_id": row["datasource_id"],
        "datasource_type": datasource_type,
        "display_name": row["display_name"],
        "connection": connection,
        "status": row["status"],
        "owner_user": row["owner_user"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    validation = self.evaluate_datasource(datasource)
    datasource["readiness_status"] = validation.readiness_status
    datasource["failure_code"] = validation.failure_code
    return datasource
```

**3l.** Remove `ExecutionAuthLoggingEngine` class (lines 49-81) and `_RuntimeConnectionResolution` dataclass (lines 404-407).

- [ ] **Step 4: Run the new tests**

Run: `make test TESTS='tests/test_datasources.py::TestDatasourceAccessControl tests/test_datasources.py::TestQueryExecutionUserSubstitution'`
Expected: PASS

- [ ] **Step 5: Fix existing datasource tests to set user identity**

In `tests/test_datasources.py`, find all test classes that call `register_datasource`, `list_datasources`, etc. and add `current_user.set("test_user")` in `setUp` / `setUpClass` and `current_user.reset(token)` in `tearDown` / `tearDownClass`.

- [ ] **Step 6: Run all datasource tests**

Run: `make test TESTS='tests/test_datasources.py'`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/registry/datasource_registry.py tests/test_datasources.py
git commit -m "feat: add owner_user access control and user substitution to datasource registry"
```

---

### Task 5: Remove `session_id` from Query Execution Call Chain

**Files:**
- Modify: `app/routing.py:86-269`
- Modify: `app/execution/routing_runtime.py:34-64`
- Modify: `app/service.py:1109-1138` and all callers

- [ ] **Step 1: Remove `session_id` from `app/routing.py`**

In `resolve_engine_for_tables` (line 86), remove `session_id: str | None = None` parameter and the `session_id=session_id` argument in the call to `self.resolve_tables`.

In `resolve_tables` (line 105), remove `session_id: str | None = None` parameter and the `session_id=session_id` argument in the call to `self.resolve_route`.

In `resolve_route` (line 126), remove `session_id: str | None = None` parameter. Change line 231 from:
```python
engine = self.datasource_service.build_analytics_engine(datasource_id, session_id=session_id)
```
to:
```python
engine = self.datasource_service.build_analytics_engine(datasource_id)
```

In `resolve_datasource_for_source` (line 246), remove `session_id: str | None = None` parameter. Change line 267 from:
```python
return self.datasource_service.build_analytics_engine(datasource_id, session_id=session_id)
```
to:
```python
return self.datasource_service.build_analytics_engine(datasource_id)
```

- [ ] **Step 2: Remove `session_id` from `app/execution/routing_runtime.py`**

In `RoutingRuntime.resolve_tables` (line 34), remove `session_id: str | None = None` parameter. Change lines 47-50 from:
```python
if session_id is None:
    route = self.query_router.resolve_tables(table_names)
else:
    route = self.query_router.resolve_tables(table_names, session_id=session_id)
```
to:
```python
route = self.query_router.resolve_tables(table_names)
```

- [ ] **Step 3: Remove `session_id` from `app/service.py`**

In `_resolve_engine` (line 1109), remove `session_id: str | None = None` parameter. Change the call from:
```python
return self.routing_runtime.resolve_tables(table_names, session_id=session_id)
```
to:
```python
return self.routing_runtime.resolve_tables(table_names)
```

Remove `_resolve_engine_for_session` (lines 1128-1138) entirely.

Replace all callers of `_resolve_engine_for_session(session_id, table_names)` with `_resolve_engine(table_names)`:
- Line 1804: `self._resolve_engine([resolved.table])`
- Line 1969: `self._resolve_engine([table_name])`
- Line 2155: `self._resolve_engine([table_name])`
- Line 2256: `self._resolve_engine([table_name])`
- Line 2389-2390: `self._resolve_engine([table_name])`

Remove `session_id` parameter from `_resolve_metric_execution_context` (line 546) and its callers.

Remove `session_id` parameter from `_build_scoped_query` and any other method that only passes it through.

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: Tests that relied on session_id passthrough may fail. Fix any remaining references.

- [ ] **Step 5: Commit**

```bash
git add app/routing.py app/execution/routing_runtime.py app/service.py
git commit -m "refactor: remove session_id from query execution call chain"
```

---

### Task 6: Session Manager — Replace `execution_identity` with `owner_user`

**Files:**
- Modify: `app/session/session_manager.py`
- Modify: `app/api/models/session.py`
- Modify: `app/api/models/session_responses.py`
- Modify: `app/api/sessions.py`
- Test: `tests/test_session_manager.py`, `tests/test_sessions.py`

- [ ] **Step 1: Remove `SessionExecutionIdentityPayload` from `app/api/models/session.py`**

Delete the `SessionExecutionIdentityPayload` class (lines 16-30).

In `SessionCreateRequest` (lines 40-54), remove the `execution_identity` field:
```python
class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    budget: SessionBudget = Field(...)
```

- [ ] **Step 2: Update response models in `app/api/models/session_responses.py`**

In `AnalysisSession` (lines 44-55), replace `execution_identity: ScalarMap = Field(default_factory=dict)` with `owner_user: str`:

```python
class AnalysisSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    goal: SessionGoal
    scope: SessionScope
    owner_user: str
    lifecycle: SessionLifecycle
    state_summary: SessionStateSummary
    created_at: str
    updated_at: str
    schema_version: str
```

- [ ] **Step 3: Update `app/session/session_manager.py`**

Remove `execution_identity` parameter from `create_session` (line 23). Add `owner_user`:

```python
def create_session(
    self,
    goal: str,
    constraints: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    raw_filter: str | None = None,
) -> dict[str, Any]:
    from app.identity import resolve_user

    owner_user = resolve_user()
    if owner_user is None:
        raise ValueError("user_required: cannot create session without user identity")
    ...
```

In the INSERT SQL, replace `execution_identity_json` with `owner_user`.

Remove `_normalize_execution_identity_payload` method (lines 502-545).
Remove `_normalize_execution_identity` method (lines 499-500).
Remove `get_execution_identity` method (lines 108-117).

Update `_session_from_row` (lines 453-485) — replace `execution_identity_json` parsing with `owner_user`:
```python
owner_user = str(row["owner_user"]) if row.get("owner_user") else ""
```

Add `owner_user` to the returned dict instead of `execution_identity`.

Update `list_sessions` — add ownership filtering. Only return sessions where `owner_user = resolve_user()`.

Add ownership check to `get_session`, `terminate_session`, and all state/intent operations — verify `resolve_user()` matches `owner_user`, raise `KeyError` if not.

- [ ] **Step 4: Update `app/api/sessions.py`**

In `create_session` (lines 53-66), remove `execution_identity` from the call:
```python
result = get_services(request).service.create_session(
    goal=payload.goal,
    budget=payload.budget.model_dump(exclude_none=True),
)
```

- [ ] **Step 5: Fix session tests**

In `tests/test_session_manager.py` and `tests/test_sessions.py`:
- Add `current_user.set("test_user")` in setUp/setUpClass
- Remove all references to `execution_identity`
- Add assertions for `owner_user` in responses

- [ ] **Step 6: Run tests**

Run: `make test TESTS='tests/test_session_manager.py tests/test_sessions.py'`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/session/session_manager.py app/api/models/session.py app/api/models/session_responses.py app/api/sessions.py tests/test_session_manager.py tests/test_sessions.py
git commit -m "feat: replace execution_identity with owner_user in sessions"
```

---

### Task 7: Semantic Model Service — Replace `requesting_user` with `resolve_user()`

**Files:**
- Modify: `app/api/semantic_v2.py`
- Modify: `app/semantic_service_v2/service.py`
- Test: `tests/test_semantic_v2_api.py`, `tests/test_semantic_v2_service.py`

- [ ] **Step 1: Update `app/api/semantic_v2.py`**

Remove `_resolve_requesting_user` helper (lines 90-94).

Remove `requesting_user: str | None = None` parameter from ALL endpoints:
- `list_semantic_models` (line 111)
- `get_semantic_model` (line 128)
- `update_semantic_model` (line 146)
- `delete_semantic_model` (line 155)
- `create_dataset` (line 177)
- `list_datasets` (line 188)
- `get_dataset` (line 201)
- `update_dataset` (line 222)
- `delete_dataset` (line 233)
- `create_relationship` (line 255)
- `list_relationships` (line 266)
- `get_relationship` (line 279)
- `update_relationship` (line 299)
- `delete_relationship` (line 311)
- `create_metric` (line 332)
- `list_metrics` (line 343)
- `get_metric` (line 356)
- `update_metric` (line 371)
- `delete_metric` (line 387)
- `get_readiness` (line 407)

Replace all `_resolve_requesting_user(requesting_user)` calls with no argument (service reads `resolve_user()` internally).

Replace all `owner = _resolve_requesting_user(requesting_user)` with `owner = None` (service reads `resolve_user()` internally for owner determination too).

For example, `list_semantic_models` becomes:
```python
@router.get("", response_model=OSIDocument)
def list_semantic_models(request: Request) -> OSIDocument:
    """List semantic models (summary)."""
    svc = _get_service(request)
    results = svc.list_semantic_models()
    return _osi_list_wrap(results)
```

And `update_semantic_model` becomes:
```python
@router.put("/{model}", response_model=OSIDocument)
def update_semantic_model(
    model: str,
    request: Request,
    payload: SemanticModelUpdateRequest,
) -> OSIDocument:
    svc = _get_service(request)
    result = _run(lambda: svc.update_semantic_model(model, _dump_model(payload)))
    return _osi_model_wrap(result)
```

- [ ] **Step 2: Update `app/semantic_service_v2/service.py`**

Add import:
```python
from app.identity import resolve_user
```

For all methods that accept `requesting_user: str | None = None`, remove the parameter and replace internal uses with `resolve_user()`:

- `_get_model_row_by_name` — replace `requesting_user` param with `resolve_user()` call inside
- `_require_visible_model` — replace `requesting_user` param with `resolve_user()` call inside
- `get_semantic_model` — remove `requesting_user` param
- `list_semantic_models` — remove `requesting_user` param
- `get_dataset` — remove `requesting_user` param
- `list_datasets` — remove `requesting_user` param
- `get_relationship` — remove `requesting_user` param
- `list_relationships` — remove `requesting_user` param
- `get_metric` — remove `requesting_user` param
- `list_metrics` — remove `requesting_user` param
- `get_readiness` — remove `requesting_user` param
- `update_semantic_model` — remove `owner_user` param, use `resolve_user()` internally
- `delete_semantic_model` — remove `owner_user` param, use `resolve_user()` internally
- `create_dataset` — remove `owner_user` param, use `resolve_user()` internally
- `update_dataset` — remove `owner_user` param, use `resolve_user()` internally
- `delete_dataset` — remove `owner_user` param, use `resolve_user()` internally
- `create_relationship` — remove `owner_user` param, use `resolve_user()` internally
- `update_relationship` — remove `owner_user` param, use `resolve_user()` internally
- `delete_relationship` — remove `owner_user` param, use `resolve_user()` internally
- `create_metric` — remove `owner_user` param, use `resolve_user()` internally
- `update_metric` — remove `owner_user` param, use `resolve_user()` internally
- `delete_metric` — remove `owner_user` param, use `resolve_user()` internally

For write operations that check ownership, replace `owner_user=owner_user` with `resolve_user()`:
```python
def update_semantic_model(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = resolve_user()
    row = self._require_visible_model(name)
    if row["visibility"] == "private" and row["owner_user"] != current:
        raise ValueError(f"Cannot update private model '{name}' owned by another user")
    ...
```

For read operations:
```python
def list_semantic_models(self) -> list[dict[str, Any]]:
    requesting_user = resolve_user()
    ...
```

- [ ] **Step 3: Fix semantic model tests**

In `tests/test_semantic_v2_api.py` and `tests/test_semantic_v2_service.py`:
- Remove `requesting_user` query parameters from API calls
- Set `current_user.set("test_user")` in test setUp
- For tests that test visibility, set different users for different scenarios

- [ ] **Step 4: Run tests**

Run: `make test TESTS='tests/test_semantic_v2_api.py tests/test_semantic_v2_service.py'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/semantic_v2.py app/semantic_service_v2/service.py tests/test_semantic_v2_api.py tests/test_semantic_v2_service.py
git commit -m "feat: replace requesting_user params with resolve_user() in semantic models"
```

---

### Task 8: Datasource API and Pydantic Models

**Files:**
- Modify: `app/api/models/_infrastructure.py`
- Modify: `app/api/datasources.py`
- Test: `tests/test_datasources.py`

- [ ] **Step 1: Add `owner_user` to datasource Pydantic models in `app/api/models/_infrastructure.py`**

In `DatasourceRegisterRequest` (lines 64-78), add `owner_user` as optional (defaults to `resolve_user()` at API layer):
```python
class DatasourceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection
```

No `owner_user` in request — it's set from `resolve_user()` server-side.

In `DatasourceResponse` (lines 91-111), add `owner_user`:
```python
class DatasourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasource_id: str
    datasource_type: Literal["duckdb", "trino"]
    display_name: str
    connection: DatasourceConnection
    status: Literal["active", "inactive", "deprecated"] = "active"
    owner_user: str = ""
    readiness_status: Literal["not_ready", "ready"] = "not_ready"
    failure_code: str | None = None
    created_at: str = ""
    updated_at: str = ""
```

- [ ] **Step 2: Update `app/api/datasources.py`**

Add import:
```python
from app.identity import resolve_user
```

In `register_datasource`, add nil-user check:
```python
@router.post("/datasources", response_model=DatasourceResponse)
def register_datasource(payload: DatasourceRegisterRequest, request: Request) -> DatasourceResponse:
    if resolve_user() is None:
        raise HTTPException(status_code=400, detail="user_required: X-Marivo-User header required")
    services = get_services(request)
    try:
        return DatasourceResponse.model_validate(
            services.datasource_service.register_datasource(
                datasource_type=payload.datasource_type,
                display_name=payload.display_name,
                connection=payload.connection.model_dump(exclude={"datasource_type"}),
            )
        )
    except (ValueError, KeyError) as error:
        raise _http_error(error) from error
```

The ownership filtering for LIST, GET, UPDATE, DELETE is handled in the `DatasourceRegistry` service layer, so the API routes just need to propagate the `KeyError` (404) correctly — which they already do via `_http_error`.

- [ ] **Step 3: Run tests**

Run: `make test TESTS='tests/test_datasources.py'`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/api/models/_infrastructure.py app/api/datasources.py tests/test_datasources.py
git commit -m "feat: add owner_user to datasource models and nil-user guard in API"
```

---

### Task 9: MCP Server — User Identity Propagation

**Files:**
- Modify: `marivo-mcp/src/marivo_mcp/config.py`
- Modify: `marivo-mcp/src/marivo_mcp/http_client.py`
- Modify: `marivo-mcp/src/marivo_mcp/tools/__init__.py`
- Test: `tests/test_marivo_mcp_smoke.py`, `tests/test_marivo_mcp_config.py`

- [ ] **Step 1: Add `user` field to `MarivoMcpConfig` in `marivo-mcp/src/marivo_mcp/config.py`**

Add field to `MarivoMcpConfig` class (after `api_token`):
```python
user: str | None = None
```

Add env var loading in `load_config_from_env()`:
```python
"user": _normalize_optional(os.environ.get("MARIVO_USER")),
```

- [ ] **Step 2: Pass `X-Marivo-User` header in `marivo-mcp/src/marivo_mcp/http_client.py`**

In `MarivoHttpClient.__init__` (lines 42-63), after the api_token header block:
```python
headers = {"Accept": "application/json"}
if config.api_token:
    headers["Authorization"] = f"Bearer {config.api_token}"
if config.user:
    headers["X-Marivo-User"] = config.user
```

- [ ] **Step 3: Remove `requesting_user` params from MCP tools in `marivo-mcp/src/marivo_mcp/tools/__init__.py`**

Remove `requesting_user: str | None = None` parameter from all 8 tools:
- `list_semantic_models` (line 765)
- `get_semantic_model` (line 791)
- `list_datasets` (line 845)
- `get_dataset` (line 859)
- `list_relationships` (line 910)
- `get_relationship` (line 924)
- `list_metrics` (line 976)
- `get_metric` (line 990)

Remove `_compact_params(requesting_user=requesting_user)` from each tool's HTTP call — the `requesting_user` query param is no longer accepted by the API.

- [ ] **Step 4: Fix MCP tests**

In MCP test files, add `MARIVO_USER` env var or config field where needed.

- [ ] **Step 5: Run tests**

Run: `make test TESTS='tests/test_marivo_mcp_smoke.py tests/test_marivo_mcp_config.py'`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add marivo-mcp/src/marivo_mcp/config.py marivo-mcp/src/marivo_mcp/http_client.py marivo-mcp/src/marivo_mcp/tools/__init__.py tests/test_marivo_mcp_smoke.py tests/test_marivo_mcp_config.py
git commit -m "feat: add X-Marivo-User header propagation to MCP client, remove requesting_user params"
```

---

### Task 10: Fix All Remaining Tests and Full Regression

**Files:**
- Various test files in `tests/`
- Any remaining service files that reference removed APIs

- [ ] **Step 1: Run full test suite to identify remaining failures**

Run: `make test`
Expected: Some failures in tests that haven't been updated yet.

- [ ] **Step 2: Fix each failing test**

Common patterns to fix:
- Tests creating datasources without `current_user` set → add `current_user.set("test_user")` in setUp
- Tests creating sessions with `execution_identity` → remove and add `current_user.set("test_user")`
- Tests calling semantic model endpoints with `requesting_user` param → remove param
- Tests calling `build_analytics_engine(session_id=...)` → remove `session_id` argument
- Tests referencing `ExecutionAuthLoggingEngine` → remove
- Tests referencing `_RuntimeConnectionResolution` → remove

Key test files to check:
- `tests/test_primitives.py` — session_id in step execution chain
- `tests/test_intent_api.py` — intent execution chain
- `tests/test_step_registry.py` — step runner chain
- `tests/test_execution_feedback.py` — routing feedback
- `tests/test_observability.py` — middleware tests
- `tests/test_storage.py` — metadata store operations
- `tests/test_session_state.py` — session state queries

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: ALL PASS

- [ ] **Step 4: Run typecheck**

Run: `make typecheck`
Expected: PASS (no new type errors)

- [ ] **Step 5: Run lint**

Run: `make lint`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "fix: update all tests for unified user identity"
```

---

### Task 11: Audit Logging

**Files:**
- Modify: `app/registry/datasource_registry.py` — add structured logging in access checks and query execution
- Modify: `app/session/session_manager.py` — add structured logging in session operations
- Test: `tests/test_datasources.py`, `tests/test_session_manager.py`

- [ ] **Step 1: Add audit logging to datasource access checks**

In `_require_owned_datasource`, add logging:
```python
def _require_owned_datasource(self, datasource: dict[str, Any]) -> None:
    owner_user = datasource.get("owner_user")
    current = resolve_user()
    if current is None or owner_user != current:
        logger.warning(
            "access_denied",
            extra={"user": current, "resource": f"datasource:{datasource['datasource_id']}", "action": "access"},
        )
        raise KeyError(f"Unknown datasource: {datasource['datasource_id']}")
    logger.info(
        "access_allowed",
        extra={"user": current, "resource": f"datasource:{datasource['datasource_id']}", "action": "access"},
    )
```

Change the logger at top of file from:
```python
logger = logging.getLogger("marivo.datasource_auth")
```
to:
```python
logger = logging.getLogger("marivo.audit")
```

- [ ] **Step 2: Add audit logging to query execution user substitution**

In `_resolve_runtime_connection`, add logging when substitution occurs:
```python
def _resolve_runtime_connection(self, datasource: dict[str, Any]) -> dict[str, Any]:
    connection = dict(datasource.get("connection") or {})
    current = resolve_user()
    if current and datasource.get("owner_user") != current:
        logger.info(
            "user_substituted",
            extra={
                "user": current,
                "resource": f"datasource:{datasource['datasource_id']}",
                "action": "query_execution",
                "original_user": datasource.get("owner_user"),
            },
        )
        connection["user"] = current
    return connection
```

- [ ] **Step 3: Add audit logging to session operations**

In `session_manager.py`, add logging for session create, list, and access:
```python
import logging

logger = logging.getLogger("marivo.audit")
```

In `create_session`:
```python
logger.info("session_created", extra={"user": owner_user, "resource": f"session:{session_id}", "action": "create"})
```

In `get_session` (after ownership check):
```python
logger.info("session_accessed", extra={"user": resolve_user(), "resource": f"session:{session_id}", "action": "access"})
```

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add app/registry/datasource_registry.py app/session/session_manager.py
git commit -m "feat: add structured audit logging for user identity access checks"
```

---

### Task 12: Final Cleanup and Verification

**Files:**
- Search entire codebase for stale references

- [ ] **Step 1: Search for stale references**

```bash
grep -rn "execution_identity" app/ tests/ --include="*.py" | grep -v __pycache__
grep -rn "requesting_user" app/ tests/ marivo-mcp/ --include="*.py" | grep -v __pycache__
grep -rn "session_id.*session_id" app/ --include="*.py" | grep -v __pycache__
grep -rn "ExecutionAuthLoggingEngine" app/ tests/ --include="*.py" | grep -v __pycache__
grep -rn "_RuntimeConnectionResolution" app/ tests/ --include="*.py" | grep -v __pycache__
```

Expected: No remaining references.

- [ ] **Step 2: Fix any remaining stale references found**

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: ALL PASS

- [ ] **Step 4: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 5: Run lint**

Run: `make lint`
Expected: PASS

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup for unified user identity"
```
