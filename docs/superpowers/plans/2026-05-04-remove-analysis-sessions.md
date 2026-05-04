# Remove Analysis Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `analysis_sessions` and `session_semantic_snapshots` tables and all related code; rely on `step_metadata.typed_semantic_snapshot` for traceability.

**Architecture:** Pure deletion — no new code. Remove DB tables, service class, API endpoints, and all references. Traceability is already handled by the existing `step_metadata.typed_semantic_snapshot` mechanism.

**Tech Stack:** Python, FastAPI, SQLite

---

### Task 1: Remove DB schema definitions

**Files:**
- Modify: `app/storage/schema.py:1018-1044`

- [ ] **Step 1: Remove the analysis session DDL from schema.py**

Remove lines 1018-1044 (the comment block, DROP statements, CREATE TABLE statements, and INDEX statement):

```python
# DELETE these lines (1018-1044):
    # -------------------------------------------------------------------------
    # Session snapshot tables for dual-path semantic layer
    # -------------------------------------------------------------------------
    "DROP TABLE IF EXISTS session_semantic_snapshots",
    "DROP TABLE IF EXISTS analysis_sessions",
    """
    CREATE TABLE analysis_sessions (
        session_id          TEXT PRIMARY KEY,
        requesting_user     TEXT NOT NULL,
        snapshot_frozen_at  TEXT NOT NULL DEFAULT (datetime('now')),
        status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        ended_at            TEXT
    )
    """,
    """
    CREATE TABLE session_semantic_snapshots (
        snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id          TEXT NOT NULL REFERENCES analysis_sessions(session_id),
        model_name          TEXT NOT NULL,
        revision            INTEGER NOT NULL CHECK (revision >= 1),
        visibility          TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
        owner_user          TEXT,
        frozen_at           TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_session_snapshots_session ON session_semantic_snapshots(session_id)",
```

- [ ] **Step 2: Run tests to verify schema change doesn't break existing tests**

Run: `make test`
Expected: Some tests will fail (shared_fixtures.py expects those tables). That's expected — fixed in Task 5.

- [ ] **Step 3: Commit**

```bash
git add app/storage/schema.py
git commit -m "refactor: remove analysis_sessions and session_semantic_snapshots from DB schema"
```

---

### Task 2: Delete SessionService

**Files:**
- Delete: `app/semantic_service_v2/session.py`

- [ ] **Step 1: Delete the file**

```bash
rm app/semantic_service_v2/session.py
```

- [ ] **Step 2: Commit**

```bash
git add -A app/semantic_service_v2/session.py
git commit -m "refactor: delete SessionService (analysis session service)"
```

---

### Task 3: Delete analysis session API and update router

**Files:**
- Delete: `app/api/analysis_session.py`
- Modify: `app/api/router.py:8,77`

- [ ] **Step 1: Delete the API file**

```bash
rm app/api/analysis_session.py
```

- [ ] **Step 2: Update router.py — remove import**

In `app/api/router.py`, remove `analysis_session,` from the import block (line 8):

```python
# Before:
from app.api import (
    analysis_session,
    approvals,
    ...
)

# After:
from app.api import (
    approvals,
    ...
)
```

- [ ] **Step 3: Update router.py — remove router mount**

In `app/api/router.py`, remove `analysis_session.router,` from the router tuple (line 77):

```python
# Before:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        datasources.router,
        routing.router,
        semantic_v2.router,
        analysis_session.router,
        governance.router,
        ...
    ):

# After:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        datasources.router,
        routing.router,
        semantic_v2.router,
        governance.router,
        ...
    ):
```

- [ ] **Step 4: Commit**

```bash
git add -A app/api/analysis_session.py app/api/router.py
git commit -m "refactor: remove analysis session API endpoints and router mount"
```

---

### Task 4: Clean up app_factory.py and deps.py

**Files:**
- Modify: `app/api/app_factory.py:28,167,189,207`
- Modify: `app/api/deps.py:17,39`

- [ ] **Step 1: Update app_factory.py — remove SessionService import**

In `app/api/app_factory.py`, remove line 28:

```python
# DELETE:
from app.semantic_service_v2.session import SessionService
```

- [ ] **Step 2: Update app_factory.py — remove SessionService instantiation**

In `app/api/app_factory.py`, remove line 167:

```python
# DELETE:
    session_service = SessionService(cast("SQLiteMetadataStore", metadata_store))
```

- [ ] **Step 3: Update app_factory.py — remove session_service from AppServices constructor**

In `app/api/app_factory.py`, remove line 189 from the `AppServices(...)` call:

```python
# DELETE this line from the AppServices() constructor call:
        session_service=session_service,
```

- [ ] **Step 4: Update app_factory.py — remove session_service from _attach_state**

In `app/api/app_factory.py`, remove line 207:

```python
# DELETE:
    app.state.session_service = services.session_service
```

- [ ] **Step 5: Update deps.py — remove SessionService import and field**

In `app/api/deps.py`, remove line 17:

```python
# DELETE:
from app.semantic_service_v2.session import SessionService
```

Remove line 39 from the `AppServices` dataclass:

```python
# DELETE:
    session_service: SessionService
```

- [ ] **Step 6: Commit**

```bash
git add app/api/app_factory.py app/api/deps.py
git commit -m "refactor: remove SessionService from app wiring and deps"
```

---

### Task 5: Clean up semantic_v2.py

**Files:**
- Modify: `app/api/semantic_v2.py:104,109-121`

- [ ] **Step 1: Remove session_id parameter and snapshot logic from create_semantic_model**

In `app/api/semantic_v2.py`, change the `create_semantic_model` function (lines 102-122) from:

```python
@router.post("", response_model=OSIDocument)
def create_semantic_model(
    request: Request, payload: SemanticModel, session_id: str | None = None
) -> OSIDocument:
    """Create a semantic model from an OSI document fragment."""
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(_dump_model(payload)))
    if session_id and hasattr(request.app.state, "session_service"):
        from app.semantic_service_v2.session import SessionService

        session_svc: SessionService = request.app.state.session_service
        model_row = svc._get_model_row_by_name(result["name"])
        if model_row:
            session_svc.add_model_to_snapshot(
                session_id=session_id,
                model_name=result["name"],
                revision=model_row["revision"],
                visibility=model_row["visibility"],
                owner_user=model_row["owner_user"],
            )
    return _osi_model_wrap(result)
```

To:

```python
@router.post("", response_model=OSIDocument)
def create_semantic_model(
    request: Request, payload: SemanticModel
) -> OSIDocument:
    """Create a semantic model from an OSI document fragment."""
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(_dump_model(payload)))
    return _osi_model_wrap(result)
```

- [ ] **Step 2: Commit**

```bash
git add app/api/semantic_v2.py
git commit -m "refactor: remove session_id param and snapshot logic from create_semantic_model"
```

---

### Task 6: Clean up test files

**Files:**
- Delete: `tests/test_session_api.py`
- Modify: `tests/shared_fixtures.py:578-586,666-674`
- Modify: `tests/test_marivo_mcp_transport.py:386-388`

- [ ] **Step 1: Delete test_session_api.py**

```bash
rm tests/test_session_api.py
```

- [ ] **Step 2: Update shared_fixtures.py — remove analysis_sessions from SQL query**

In `tests/shared_fixtures.py`, change the `osi_v2_tables` SQL query (lines 578-586) from:

```python
        osi_v2_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                "('semantic_models', 'semantic_datasets', "
                "'semantic_fields', 'semantic_relationships', 'semantic_metrics', "
                "'semantic_readiness_status', 'analysis_sessions', "
                "'session_semantic_snapshots')"
            ).fetchall()
        }
```

To:

```python
        osi_v2_tables = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                "('semantic_models', 'semantic_datasets', "
                "'semantic_fields', 'semantic_relationships', 'semantic_metrics', "
                "'semantic_readiness_status')"
            ).fetchall()
        }
```

- [ ] **Step 3: Update shared_fixtures.py — remove analysis_sessions from expected set**

In `tests/shared_fixtures.py`, change the expected set (lines 666-674) from:

```python
        and osi_v2_tables
        == {
            "semantic_models",
            "semantic_datasets",
            "semantic_fields",
            "semantic_relationships",
            "semantic_metrics",
            "semantic_readiness_status",
            "analysis_sessions",
            "session_semantic_snapshots",
        }
```

To:

```python
        and osi_v2_tables
        == {
            "semantic_models",
            "semantic_datasets",
            "semantic_fields",
            "semantic_relationships",
            "semantic_metrics",
            "semantic_readiness_status",
        }
```

- [ ] **Step 4: Update test_marivo_mcp_transport.py — remove analysis session tool assertions**

In `tests/test_marivo_mcp_transport.py`, remove lines 386-388:

```python
# DELETE:
    assert "create_analysis_session" not in server.tools
    assert "get_analysis_session" not in server.tools
    assert "end_analysis_session" not in server.tools
```

- [ ] **Step 5: Commit**

```bash
git add -A tests/test_session_api.py tests/shared_fixtures.py tests/test_marivo_mcp_transport.py
git commit -m "refactor: remove analysis session tests and fixture references"
```

---

### Task 7: Run full test suite

- [ ] **Step 1: Run make test**

Run: `make test`
Expected: All tests pass.

- [ ] **Step 2: Run make typecheck**

Run: `make typecheck`
Expected: No type errors (SessionService references are fully removed).

- [ ] **Step 3: Run make lint**

Run: `make lint`
Expected: No lint errors.

---

### Out of scope (noted for follow-up)

- The investigation session's `schema_version: "analysis_session.v1"` (in `app/session/session_manager.py:501`) is confusingly named but is a separate concern. Renaming it would be a schema change affecting existing data. Track as follow-up if desired.
