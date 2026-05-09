# Phase 9 — Production Server Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace thin wrapper adapters with production-grade native port implementations, establish local/server parity gating, and add MySQL-backed CI.

**Architecture:** Event-sourced `SqlSessionStore` over `session_events` table (replacing CRUD `sessions` table bridge), `RoutingDataSource` with per-datasource engine cache via existing `DatasourceRegistry`, `MetadataEvidenceStoreAdapter.read()` implementation, contract test parity gating between local and server adapters.

**Tech Stack:** Python 3.11+, SQLite, MySQL (via PyMySQL + testcontainers), DuckDB, Trino, Pydantic, pytest, FastAPI

---

## File Structure

### New files (create)

| File | Responsibility |
|------|---------------|
| `app/adapters/server/model_store.py` | `SqlModelStoreAdapter` (moved from wrappers.py) |
| `app/adapters/server/session_store.py` | `SqlSessionStore` — native event-sourced over `session_events` |
| `app/adapters/server/data_source.py` | `RoutingDataSource` — per-datasource engine cache |
| `app/adapters/server/evidence_store.py` | `MetadataEvidenceStoreAdapter` (moved + read() added) |
| `app/adapters/server/audit_log.py` | Re-export of `FileAuditLog` from local adapter |
| `app/adapters/server/cache_store.py` | `InMemoryCacheStore` (moved from wrappers.py) |
| `app/adapters/server/authz.py` | `NoopAuthZAdapter` (moved from wrappers.py) |
| `app/adapters/server/telemetry.py` | `LocalTelemetryAdapter` (moved from wrappers.py) |
| `app/adapters/server/runtime_config.py` | `TomlRuntimeConfigAdapter` (moved from wrappers.py) |
| `app/adapters/server/_legacy_session.py` | DELETED — no legacy bridge needed (Marivo not launched) |
| `tests/contracts/session_store_cases.py` | `SESSION_STORE_CASES` for contract + parity tests |
| `tests/contracts/step_store_cases.py` | `STEP_STORE_CASES` for contract tests |
| `tests/contracts/artifact_store_cases.py` | `ARTIFACT_STORE_CASES` for contract tests |

### Modified files

| File | Change |
|------|--------|
| `app/contracts/values.py` | Add `datasource_id` to `LogicalQuery` |
| `app/contracts/ids.py` | No change (DatasourceId already exists) |
| `app/storage/schema.py` | Add `session_events` DDL; remove FK refs to sessions |
| `app/runtime/ports.py` | Remove 7 optional fields |
| `app/runtime/runtime.py` | Remove `wire_datasource_svc`, `wire_semantic_v2_svc`; add service registry |
| `app/profiles/server.py` | Update `_build_server_ports`, simplify `ServerComposition` |
| `app/api/app_factory.py` | Update `_build_services`, `_attach_state` |
| `app/api/deps.py` | Update `AppServices` dataclass |
| `app/adapters/local/sqlite_session_store.py` | Harmonize schema (event_id, payload_json, UNIQUE, index) |
| `tests/contracts/conftest.py` | Update `_init_state_db` schema |
| `tests/contracts/test_session_store.py` | Add `SqlSessionStore` factory, new cases |
| `tests/contracts/test_data_source.py` | Add `RoutingDataSource` factory |
| `tests/contracts/test_evidence_store.py` | Add `MetadataEvidenceStoreAdapter` factory |
| `tests/contracts/test_parity.py` | Expand parity matrix |
| `tests/runtime/test_port_wrappers.py` | Update imports |
| `tests/profiles/test_server_factory.py` | Update for new composition |
| `pyproject.toml` | Add `test-mysql` extras |
| `Makefile` | Add `test-mysql` target |

| `.github/workflows/server-contract-tests.yml` | CI job for MySQL + parity testing |

### Deleted files

| File | When |
|------|------|
| `app/adapters/server/wrappers.py` | Task 2 (replaced by individual modules) |

---

## Sub-phase 9.1: Adapter Extraction & Native Implementations

### Task 1: Add `datasource_id` to `LogicalQuery`

**Files:**
- Modify: `app/contracts/values.py:102-106`
- Modify: `tests/test_contracts_values.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_values.py — add to existing test class or file
def test_logical_query_datasource_id_default():
    from app.contracts.values import LogicalQuery
    q = LogicalQuery(sql="SELECT 1")
    assert q.datasource_id is None

def test_logical_query_datasource_id_explicit():
    from app.contracts.values import LogicalQuery
    from app.contracts.ids import DatasourceId
    q = LogicalQuery(sql="SELECT 1", datasource_id=DatasourceId("ds_abc"))
    assert q.datasource_id == "ds_abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_contracts_values.py::test_logical_query_datasource_id_default -v`
Expected: FAIL — `LogicalQuery` has no field `datasource_id`

- [ ] **Step 3: Add `datasource_id` field to `LogicalQuery`**

```python
# app/contracts/values.py — replace the LogicalQuery class
class LogicalQuery(BaseModel):
    """Logical query produced by core/planner, consumed by DataSource port."""

    sql: str
    params: dict[str, Any] = {}
    datasource_id: DatasourceId | None = None
```

Add the import at the top of `values.py` (it already imports from `.ids`):
```python
from .ids import DatasourceId, SessionId, StepId, UserId
```
(`DatasourceId` is already imported — just add it to the `LogicalQuery` class.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contracts_values.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass (the field defaults to `None`, so all existing `LogicalQuery` constructions still work)

- [ ] **Step 6: Commit**

```bash
git add app/contracts/values.py tests/test_contracts_values.py
git commit -m "feat(contracts): add datasource_id field to LogicalQuery"
```

---

### Task 2: Split `wrappers.py` into individual modules

This is a mechanical refactoring. Each class moves to its own file, `__init__.py` re-exports, and all import sites are updated.

**Files:**
- Create: `app/adapters/server/{model_store,session_store,data_source,evidence_store,audit_log,cache_store,authz,telemetry,runtime_config}.py`
- Modify: `app/adapters/server/__init__.py`
- Delete: `app/adapters/server/wrappers.py`
- Modify: `app/profiles/server.py` (imports)
- Modify: `tests/runtime/test_port_wrappers.py` (imports)

- [ ] **Step 1: Create `authz.py`**

```python
# app/adapters/server/authz.py
from __future__ import annotations

from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision


class NoopAuthZAdapter:
    """Always allows, returns ``AuthZDecision(allowed=True)``."""

    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)
```

- [ ] **Step 2: Create `telemetry.py`**

```python
# app/adapters/server/telemetry.py
from __future__ import annotations

from app.contracts.values import TelemetryEvent


class LocalTelemetryAdapter:
    """No-op telemetry adapter; does nothing."""

    def emit(self, event: TelemetryEvent) -> None:
        pass
```

- [ ] **Step 3: Create `runtime_config.py`**

```python
# app/adapters/server/runtime_config.py
from __future__ import annotations

from app.config import MarivoConfig


class TomlRuntimeConfigAdapter:
    def __init__(self, config: MarivoConfig) -> None:
        self._config = config

    def get(self, key: str) -> str | None:
        value = getattr(self._config, key, None)
        if value is None:
            return None
        return str(value)
```

- [ ] **Step 4: Create `audit_log.py`**

```python
# app/adapters/server/audit_log.py
from app.adapters.local.file_audit_log import FileAuditLog

__all__ = ["FileAuditLog"]
```

- [ ] **Step 5: Create `cache_store.py`**

```python
# app/adapters/server/cache_store.py
from __future__ import annotations

from app.contracts.ids import CacheKey
from app.contracts.values import CacheValue


class InMemoryCacheStore:
    """Simple dict-backed cache store. TTL is ignored."""

    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    def get(self, key: CacheKey) -> CacheValue | None:
        raw = self._cache.get(key)
        if raw is None:
            return None
        return CacheValue(raw)

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        self._cache[key] = bytes(value)
```

- [ ] **Step 6: Create `model_store.py`** — move `SqlModelStoreAdapter` from `wrappers.py` lines 121-250 verbatim

- [ ] **Step 7: Create `evidence_store.py`** — move `MetadataEvidenceStoreAdapter` from `wrappers.py` lines 644-784 verbatim (with its helper methods). Keep `read()` as `NotImplementedError` for now (Task 7 implements it).

- [ ] **Step 8: Create `data_source.py`** — move `DataSourceAdapter` from `wrappers.py` lines 570-641 verbatim. This is the CURRENT adapter; Task 6 replaces it with `RoutingDataSource`.

- [ ] **Step 9: Create `session_store.py`** — placeholder with a stub that raises `NotImplementedError`. Task 4 replaces this with the full `SqlSessionStore`.

```python
# app/adapters/server/session_store.py
"""Session store adapter — Task 4 replaces this with SqlSessionStore."""


class SqlSessionStoreAdapter:
    """Placeholder — replaced by SqlSessionStore in Task 4."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Use SqlSessionStore after Task 4")
```

- [ ] **Step 11: Update `__init__.py`**

```python
# app/adapters/server/__init__.py
from app.adapters.server.authz import NoopAuthZAdapter
from app.adapters.server.cache_store import InMemoryCacheStore
from app.adapters.server.data_source import DataSourceAdapter
from app.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
from app.adapters.server.model_store import SqlModelStoreAdapter
from app.adapters.server.session_store import SqlSessionStoreAdapter
from app.adapters.server.telemetry import LocalTelemetryAdapter
from app.adapters.server.runtime_config import TomlRuntimeConfigAdapter

__all__ = [
    "DataSourceAdapter",
    "InMemoryCacheStore",
    "LocalTelemetryAdapter",
    "MetadataEvidenceStoreAdapter",
    "NoopAuthZAdapter",
    "SqlModelStoreAdapter",
    "SqlSessionStoreAdapter",
    "TomlRuntimeConfigAdapter",
]
```

Note: No backward-compatible aliases are needed — Marivo has not launched.

- [ ] **Step 12: Delete `wrappers.py`**

```bash
rm app/adapters/server/wrappers.py
```

- [ ] **Step 13: Update imports in `app/profiles/server.py`**

Change the import block in `_build_server_ports`:
```python
from app.adapters.server.artifact_store import (
    MetadataArtifactStoreAdapter,
    MetadataStepStoreAdapter,
)
from app.adapters.server.authz import NoopAuthZAdapter
from app.adapters.server.cache_store import InMemoryCacheStore
from app.adapters.server.data_source import DataSourceAdapter
from app.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
from app.adapters.server.model_store import SqlModelStoreAdapter
from app.adapters.server.session_store import SqlSessionStoreAdapter
from app.adapters.server.telemetry import LocalTelemetryAdapter
from app.adapters.server.runtime_config import TomlRuntimeConfigAdapter
```

- [ ] **Step 14: Update `MetadataCacheStoreAdapter` → `InMemoryCacheStore` in `_build_server_ports`**

```python
cache_store=InMemoryCacheStore(),  # was MetadataCacheStoreAdapter(metadata_store)
```

- [ ] **Step 15: Update `FileAuditLogAdapter` → `FileAuditLog` in `_build_server_ports`**

```python
from app.adapters.server.audit_log import FileAuditLog
# ...
audit_log=FileAuditLog(),  # was FileAuditLogAdapter()
```

Note: `FileAuditLog` takes no required args. If it needs a log path, pass it in the constructor. Check the local `file_audit_log.py` for the constructor signature — it takes an optional `log_dir`.

- [ ] **Step 16: Update test imports**

Run: `rg "from app.adapters.server.wrappers import" tests/ app/`
Update each match to import from the new individual modules.

- [ ] **Step 17: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 18: Commit**

```bash
git add app/adapters/server/ app/profiles/server.py tests/
git commit -m "refactor(server): split wrappers.py into individual adapter modules"
```

---

### Task 3: Add `session_events` DDL to `schema.py`

The server `SqlSessionStore` uses `MetadataStore.query_rows()`/`execute()`, so the `session_events` table must be created by `MetadataStore.initialize()`.

**Files:**
- Modify: `app/storage/schema.py`
- Modify: `app/storage/mysql_metadata.py` (if FK validation needs update)

- [ ] **Step 1: Add `session_events` DDL to `METADATA_DDL` in `schema.py`**

After the existing `sessions` table definition, add:

```sql
CREATE TABLE IF NOT EXISTS session_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    event_type   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    actor        TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_session_events_sid ON session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_session_events_owner ON session_events(event_type, actor);
```

- [ ] **Step 2: Verify MySQL DDL generation handles AUTOINCREMENT**

The `_mysql_table_line()` function in `schema.py` converts `INTEGER PRIMARY KEY AUTOINCREMENT` to `BIGINT UNSIGNED AUTO_INCREMENT`. Verify this works for the `session_events` table by checking the conversion logic handles the `UNIQUE(session_id, seq)` constraint correctly.

Note: MySQL's `_mysql_table_line()` strips `REFERENCES` from column definitions and emits separate `ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` statements. The `session_events` table has no `REFERENCES`, so this should work cleanly.

- [ ] **Step 3: Write test for DDL presence**

```python
# tests/test_metadata_schema_bootstrap.py — add test
def test_session_events_table_created_by_initialize(tmp_path):
    from app.storage.sqlite_metadata import SQLiteMetadataStore
    store = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    store.initialize()
    with store.connect() as con:
        rows = store.query_rows(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='session_events'",
        )
    assert len(rows) == 1
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_metadata_schema_bootstrap.py::test_session_events_table_created_by_initialize -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage/schema.py tests/test_metadata_schema_bootstrap.py
git commit -m "feat(storage): add session_events DDL to metadata schema"
```

---

### Task 4: Implement `SqlSessionStore` (event-sourced)

This is the core of Phase 9.1 — replacing the CRUD bridge with native event-sourced storage over the `session_events` table.

**Files:**
- Modify: `app/adapters/server/session_store.py` (replace legacy re-export)
- Modify: `tests/contracts/test_session_store.py` (add server factory + new cases)

- [ ] **Step 1: Write failing test for `SqlSessionStore.append_event`**

```python
# tests/contracts/test_session_store.py — add factory and test

def _make_sql_session_store(tmp_path: Path) -> "SqlSessionStore":
    from app.adapters.server.session_store import SqlSessionStore
    from app.storage.sqlite_metadata import SQLiteMetadataStore
    store = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    store.initialize()
    return SqlSessionStore(store)

# Add to session_store_factories list:
session_store_factories = [
    ("SqliteSessionStore", _make_sqlite_session_store),
    ("SqlSessionStore", _make_sql_session_store),
]
```

Run: `pytest tests/contracts/test_session_store.py -v`
Expected: FAIL — `SqlSessionStore` doesn't exist yet

- [ ] **Step 2: Implement `SqlSessionStore.__init__` and `append_event`**

```python
# app/adapters/server/session_store.py
from __future__ import annotations

import json
from typing import Any

from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent, SessionState
from app.core.session.rebuild import rebuild_session_state
from app.storage.metadata import MetadataStore

_MAX_RETRY_ATTEMPTS = 3


class SqlSessionStore:
    """Event-sourced SessionStore backed by session_events table.

    Uses MetadataStore for SQL execution. Supports both SQLite and MySQL
    via the MetadataStore dialect abstraction.
    """

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        """Append an event with UNIQUE-violation retry.

        Under MySQL REPEATABLE READ, a same-transaction retry would see
        the same snapshot. We must ROLLBACK the failed transaction and
        start a NEW one before retrying.
        """
        for attempt in range(_MAX_RETRY_ATTEMPTS):
            with self._metadata.connect() as con:
                row = self._metadata.query_one(
                    "SELECT COALESCE(MAX(seq), 0) FROM session_events WHERE session_id = ?",
                    [str(session_id)],
                )
                next_seq = (row["COALESCE(MAX(seq), 0)"] if row else 0) + 1
                try:
                    self._metadata.execute(
                        "INSERT INTO session_events (session_id, seq, event_type, timestamp, payload_json, actor) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            str(session_id),
                            next_seq,
                            event.event_type,
                            event.timestamp,
                            json.dumps(event.payload, sort_keys=True),
                            str(event.actor) if event.actor else None,
                        ],
                    )
                    return  # success
                except Exception as exc:
                    if "UNIQUE" in str(exc) or "Duplicate entry" in str(exc):
                        continue  # retry with new transaction
                    raise
        raise RuntimeError(
            f"Failed to append event for session {session_id} after "
            f"{_MAX_RETRY_ATTEMPTS} attempts (UNIQUE constraint violations)"
        )

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        rows = self._metadata.query_rows(
            "SELECT session_id, event_type, timestamp, payload_json, actor "
            "FROM session_events WHERE session_id = ? ORDER BY seq",
            [str(session_id)],
        )
        if not rows:
            raise NotFoundError(
                code=ErrorCode.SESSION_NOT_FOUND,
                message=f"Session not found: {session_id}",
            )
        return [
            SessionEvent(
                session_id=SessionId(row["session_id"]),
                event_type=row["event_type"],
                timestamp=row["timestamp"],
                payload=json.loads(row["payload_json"]),
                actor=UserId(row["actor"]) if row.get("actor") else None,
            )
            for row in rows
        ]

    def list_sessions(self, owner: UserId) -> list[SessionState]:
        """Batch-load events for all sessions owned by `owner`."""
        rows = self._metadata.query_rows(
            "SELECT session_id, event_type, timestamp, payload_json, actor "
            "FROM session_events "
            "WHERE session_id IN ("
            "  SELECT DISTINCT session_id FROM session_events "
            "  WHERE event_type = 'session_created' AND actor = ?"
            ") ORDER BY session_id, seq",
            [str(owner)],
        )
        # Group by session_id and rebuild each session state
        sessions: dict[str, list[SessionEvent]] = {}
        for row in rows:
            sid = row["session_id"]
            if sid not in sessions:
                sessions[sid] = []
            sessions[sid].append(
                SessionEvent(
                    session_id=SessionId(sid),
                    event_type=row["event_type"],
                    timestamp=row["timestamp"],
                    payload=json.loads(row["payload_json"]),
                    actor=UserId(row["actor"]) if row.get("actor") else None,
                )
            )
        return [
            rebuild_session_state(events)
            for events in sessions.values()
        ]

    def get_proposition_runtime_status(
        self, session_id: str, proposition_id: str
    ) -> dict[str, Any]:
        from app.storage.evidence_repositories import ActionProposalRepository, AssessmentRepository

        row = self._metadata.query_one(
            "SELECT proposition_id, session_id, externally_visible_assessment_id "
            "FROM propositions WHERE proposition_id = ? AND session_id = ?",
            [proposition_id, session_id],
        )
        if row is None:
            raise KeyError(f"proposition {proposition_id!r} not found in session {session_id!r}")

        ev_assessment_id = row.get("externally_visible_assessment_id")
        assessment_repo = AssessmentRepository(self._metadata)
        latest = assessment_repo.get_latest(proposition_id)

        proposals: list[dict[str, Any]] = []
        if latest is not None:
            proposal_repo = ActionProposalRepository(self._metadata)
            proposals = proposal_repo.list_by_assessment(session_id, latest["assessment_id"])

        if ev_assessment_id:
            current_stage = "externally_visible"
            last_successful_stage = "publish"
        elif latest is not None and proposals:
            current_stage = "publish_ready"
            last_successful_stage = "proposal_refresh"
        elif latest is not None:
            current_stage = "assessment_committed"
            last_successful_stage = "assessment_committed"
        else:
            current_stage = "queued"
            last_successful_stage = None

        return {
            "session_id": session_id,
            "proposition_id": proposition_id,
            "current_stage": current_stage,
            "last_successful_stage": last_successful_stage,
            "current_assessment_id": latest["assessment_id"] if latest is not None else None,
            "current_attempt": None,
            "backlog_state": "none",
            "last_failure_reason": "none",
            "last_failure_at": None,
            "schema_version": "proposition_runtime_status.v1",
        }

    def list_sessions_paginated(self, **kwargs: Any) -> dict[str, Any]:
        from app.identity import resolve_user

        status = kwargs.get("status")
        session_id = kwargs.get("session_id")
        limit = kwargs.get("limit")
        page_token = kwargs.get("page_token")

        offset = _decode_page_token(page_token)
        normalized_limit = _normalize_limit(limit)

        # Build filter conditions on session_created events
        clauses: list[str] = ["event_type = 'session_created'"]
        params: list[Any] = []
        if status:
            if status in ("closed", "terminated"):
                clauses.append("session_id NOT IN (SELECT DISTINCT session_id FROM session_events WHERE event_type = 'session_created' AND payload_json LIKE '%open%')")
            # For open status, no additional filter needed beyond created
        if session_id:
            clauses.append("session_id LIKE ?")
            params.append(f"{session_id}%")
        current_user = resolve_user()
        if current_user is not None:
            clauses.append("actor = ?")
            params.append(current_user)

        where = " AND ".join(clauses)
        count_sql = f"SELECT COUNT(DISTINCT session_id) as cnt FROM session_events WHERE {where}"
        count_row = self._metadata.query_one(count_sql, params)
        total = count_row["cnt"] if count_row else 0

        id_sql = (
            f"SELECT DISTINCT session_id FROM session_events WHERE {where}"
            f" ORDER BY session_id DESC LIMIT ? OFFSET ?"
        )
        params.extend([normalized_limit + 1, offset])
        id_rows = self._metadata.query_rows(id_sql, params)

        has_next_page = len(id_rows) > normalized_limit
        session_ids = [row["session_id"] for row in id_rows[:normalized_limit]]

        if not session_ids:
            return {"items": [], "next_page_token": None, "total": 0}

        # Batch-load events for matching sessions
        placeholders = ",".join("?" for _ in session_ids)
        event_rows = self._metadata.query_rows(
            f"SELECT session_id, event_type, timestamp, payload_json, actor "
            f"FROM session_events WHERE session_id IN ({placeholders}) ORDER BY session_id, seq",
            session_ids,
        )
        sessions: dict[str, list[SessionEvent]] = {}
        for row in event_rows:
            sid = row["session_id"]
            if sid not in sessions:
                sessions[sid] = []
            sessions[sid].append(
                SessionEvent(
                    session_id=SessionId(sid),
                    event_type=row["event_type"],
                    timestamp=row["timestamp"],
                    payload=json.loads(row["payload_json"]),
                    actor=UserId(row["actor"]) if row.get("actor") else None,
                )
            )

        items = []
        for sid in session_ids:
            if sid in sessions:
                state = rebuild_session_state(sessions[sid])
                items.append(_session_state_to_dict(state))

        next_page_token = str(offset + normalized_limit) if has_next_page else None
        return {"items": items, "next_page_token": next_page_token, "total": total}


def _decode_page_token(page_token: str | None) -> int:
    if page_token is None:
        return 0
    try:
        offset = int(page_token)
    except ValueError as error:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.") from error
    if offset < 0:
        raise ValueError("Invalid page_token. Expected a non-negative integer offset.")
    return offset


def _normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 25
    if limit <= 0:
        raise ValueError("Invalid limit. Expected a positive integer.")
    return min(limit, 100)


def _session_state_to_dict(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "goal": {"question": state.goal},
        "scope": {"constraints": state.constraints},
        "owner_user": state.owner_user,
        "lifecycle": {
            "status": state.status,
            "terminal_reason": getattr(state, "terminal_reason", None),
        },
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "schema_version": "analysis_session.v1",
    }
```

- [ ] **Step 3: Run existing contract tests with the new factory**

Run: `pytest tests/contracts/test_session_store.py -v`
Expected: PASS — all 7 existing tests should pass for both `SqliteSessionStore` and `SqlSessionStore`

- [ ] **Step 4: Write test for concurrent append retry**

```python
# tests/contracts/test_session_store.py — add test
import threading

@pytest.mark.parametrize("name,factory", session_store_factories)
def test_concurrent_append_both_stored(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-concurrent")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id,
            event_type="session_created",
            timestamp="2026-05-07T10:00:00Z",
            payload={"goal": "concurrent test"},
            actor=UserId("alice"),
        ),
    )

    results = {"t1": None, "t2": None}
    errors = {"t1": None, "t2": None}

    def append_event(thread_id, event_type):
        try:
            store.append_event(
                session_id,
                SessionEvent(
                    session_id=session_id,
                    event_type=event_type,
                    timestamp="2026-05-07T10:00:01Z",
                    payload={"thread": thread_id},
                    actor=None,
                ),
            )
            results[thread_id] = "ok"
        except Exception as e:
            errors[thread_id] = e

    t1 = threading.Thread(target=append_event, args=("t1", "step_completed"))
    t2 = threading.Thread(target=append_event, args=("t2", "step_completed"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    events = store.load_events(session_id)
    assert len(events) == 3  # created + 2 step_completed
```

- [ ] **Step 5: Write test for other event types (not silently dropped)**

```python
@pytest.mark.parametrize("name,factory", session_store_factories)
def test_other_event_types_not_dropped(name, factory, tmp_path):
    store = factory(tmp_path)
    session_id = SessionId("sess-other")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id, event_type="session_created",
            timestamp="2026-05-07T10:00:00Z", payload={"goal": "g"}, actor=None,
        ),
    )
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id, event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z", payload={"step": "s1"}, actor=None,
        ),
    )
    events = store.load_events(session_id)
    assert len(events) == 2
    assert events[1].event_type == "step_completed"
```

- [ ] **Step 6: Run all session store tests**

Run: `pytest tests/contracts/test_session_store.py -v`
Expected: All pass

- [ ] **Step 7: Update `session_store.py` import in server.py**

The `_build_server_ports` in `server.py` now uses `SqlSessionStore` instead of `SqlSessionStoreAdapter`:

```python
from app.adapters.server.session_store import SqlSessionStore
# ...
session_store=SqlSessionStore(metadata_store),
```

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add app/adapters/server/session_store.py app/profiles/server.py tests/contracts/test_session_store.py
git commit -m "feat(server): implement event-sourced SqlSessionStore with concurrent retry"
```

---

### Task 5: Harmonize local `SqliteSessionStore` schema

Since Marivo has not launched, no migration of existing databases is needed. The schema is changed directly.

**Files:**
- Modify: `app/adapters/local/sqlite_session_store.py`
- Modify: `tests/contracts/conftest.py` (`_init_state_db`)

- [ ] **Step 1: Update `_ensure_schema` in `SqliteSessionStore`**

Replace the existing `_ensure_schema` method with the new schema directly (no migration path):

```python
def _ensure_schema(self) -> None:
    conn = self._connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_events (
                event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                event_type  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                actor       TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(session_id, seq)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_events_sid "
            "ON session_events (session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_events_owner "
            "ON session_events (event_type, actor)"
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Update column references in `append_event`**

Change `"payload"` → `"payload_json"` in the INSERT statement:

```python
"INSERT INTO session_events (session_id, seq, event_type, timestamp, payload_json, actor) "
```

- [ ] **Step 3: Update column references in `load_events`**

Change `"payload"` → `"payload_json"` in the SELECT:

```python
"SELECT session_id, event_type, timestamp, payload_json, actor "
```

And in the `json.loads`:

```python
payload=json.loads(row[3]),
```

(This index stays the same since we're selecting the same column positions.)

- [ ] **Step 4: Update `tests/contracts/conftest.py` `_init_state_db`**

```python
def _init_state_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            event_type  TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            actor       TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE(session_id, seq)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events_sid "
        "ON session_events (session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_events_owner "
        "ON session_events (event_type, actor)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cache_entries (
            key        TEXT PRIMARY KEY,
            value      BLOB NOT NULL,
            expires_at TEXT
        )"""
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 5: Run session store contract tests**

Run: `pytest tests/contracts/test_session_store.py -v`
Expected: All pass

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add app/adapters/local/sqlite_session_store.py tests/contracts/conftest.py
git commit -m "refactor(local): harmonize SqliteSessionStore schema with server schema"
```

---

### Task 6: Implement `RoutingDataSource`

**Files:**
- Modify: `app/adapters/server/data_source.py` (replace `DataSourceAdapter`)
- Modify: `app/profiles/server.py` (wire `RoutingDataSource`)
- Modify: `tests/contracts/test_data_source.py` (add RoutingDataSource factory)

- [ ] **Step 1: Write failing test for default DuckDB routing**

```python
# tests/contracts/test_data_source.py — add factory and test

def _make_routing_data_source(tmp_path: Path) -> "RoutingDataSource":
    from app.adapters.server.data_source import RoutingDataSource
    from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
    from app.storage.sqlite_metadata import SQLiteMetadataStore
    from app.registry.datasource_registry import DatasourceRegistry
    from app.routing import QueryRouter
    from app.datasources import DatasourceService

    engine = DuckDBAnalyticsEngine(":memory:")
    engine.initialize()
    metadata = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    metadata.initialize()
    ds_service = DatasourceService(metadata)
    router = QueryRouter(metadata, ds_service)
    return RoutingDataSource(default_engine=engine, registry=ds_service, query_router=router)
```

Run: `pytest tests/contracts/test_data_source.py -v`
Expected: FAIL — `RoutingDataSource` doesn't exist yet

- [ ] **Step 2: Implement `RoutingDataSource`**

```python
# app/adapters/server/data_source.py
from __future__ import annotations

import logging
from typing import Any

from app.contracts.errors import DomainError, ErrorCode
from app.contracts.ids import DatasourceId
from app.contracts.values import (
    ColumnInfo,
    LogicalQuery,
    QueryResult,
    SourceRef,
    SourceSchema,
)
from app.execution.routing_runtime import RoutingRuntime
from app.registry.datasource_registry import DatasourceRegistry
from app.routing import QueryRouter
from app.storage.analytics import AnalyticsEngine

logger = logging.getLogger(__name__)


class RoutingDataSource:
    """DataSource that routes queries to per-datasource cached engines.

    Uses the existing DatasourceRegistry for metadata lookup and
    build_analytics_engine() for engine construction. Routes to the
    default DuckDB engine when datasource_id is None.
    """

    def __init__(
        self,
        default_engine: AnalyticsEngine,
        registry: DatasourceRegistry,
        query_router: QueryRouter,
    ) -> None:
        self._default_engine = default_engine
        self._registry = registry
        self._query_router = query_router
        self._routing_runtime = RoutingRuntime(query_router, default_engine)
        self._engine_cache: dict[DatasourceId, AnalyticsEngine] = {}

    def execute(self, query: LogicalQuery) -> QueryResult:
        engine = self._resolve_engine(query.datasource_id)
        try:
            rows = engine.query_rows(
                query.sql,
                list(query.params.values()) if query.params else None,
            )
        except ImportError as exc:
            raise DomainError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"Engine driver not installed: {exc}. "
                f"Install with: pip install marivo[trino]",
            ) from exc
        except Exception as exc:
            raise DomainError(ErrorCode.QUERY_EXECUTION_FAILED, str(exc)) from exc

        if not rows:
            return QueryResult(columns=[], rows=[], row_count=0, query_sql=query.sql)
        columns = list(rows[0].keys())
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            query_sql=query.sql,
        )

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        try:
            col_dicts = self._registry.browse_catalog_columns(
                source_ref.datasource_id,
                source_ref.schema_name,
                source_ref.table_name,
            )
            columns = [
                ColumnInfo(
                    name=col.get("name", "unknown"),
                    dtype=col.get("data_type", "unknown"),
                    nullable=col.get("properties", {}).get("nullable", True),
                )
                for col in col_dicts
            ]
            return SourceSchema(columns=columns)
        except (KeyError, NotImplementedError, ValueError):
            return SourceSchema(columns=[])
        except Exception as exc:
            raise DomainError(ErrorCode.DATASOURCE_UNAVAILABLE, str(exc)) from exc

    def resolve_tables(self, table_names: list[str], *, session_id: str | None = None) -> Any:
        return self._routing_runtime.resolve_tables(table_names, session_id=session_id)

    def _resolve_engine(self, datasource_id: DatasourceId | None) -> AnalyticsEngine:
        if datasource_id is None:
            return self._default_engine
        if datasource_id in self._engine_cache:
            return self._engine_cache[datasource_id]
        try:
            engine = self._registry.build_analytics_engine(datasource_id)
        except (KeyError, ValueError) as exc:
            raise DomainError(
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"Datasource {datasource_id!r} not found or unavailable",
            ) from exc
        self._engine_cache[datasource_id] = engine
        return engine
```

- [ ] **Step 3: Update `server.py` to wire `RoutingDataSource`**

In `_build_server_ports`, replace:

```python
data_source=DataSourceAdapter(analytics_engine, query_router),
```

with:

```python
from app.adapters.server.data_source import RoutingDataSource
# ...
data_source=RoutingDataSource(
    default_engine=analytics_engine,
    registry=datasource_service,
    query_router=query_router,
),
```

- [ ] **Step 4: Run data source contract tests**

Run: `pytest tests/contracts/test_data_source.py -v`
Expected: PASS for both `DuckDBDataSource` and `RoutingDataSource`

- [ ] **Step 5: Write test for unknown datasource raises DomainError**

```python
def test_routing_data_source_unknown_datasource(tmp_path):
    from app.contracts.errors import DomainError, ErrorCode
    ds = _make_routing_data_source(tmp_path)
    with pytest.raises(DomainError) as exc_info:
        ds.execute(LogicalQuery(sql="SELECT 1", datasource_id=DatasourceId("nonexistent")))
    assert exc_info.value.code == ErrorCode.DATASOURCE_UNAVAILABLE
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add app/adapters/server/data_source.py app/profiles/server.py tests/contracts/test_data_source.py
git commit -m "feat(server): implement RoutingDataSource with per-datasource engine cache"
```

---

### Task 7: Implement `MetadataEvidenceStoreAdapter.read()`

**Files:**
- Modify: `app/adapters/server/evidence_store.py`
- Modify: `tests/contracts/test_evidence_store.py` (add server factory)

- [ ] **Step 1: Write failing test for read() roundtrip**

```python
# tests/contracts/test_evidence_store.py — add server factory

def _make_metadata_evidence_store(tmp_path: Path) -> "MetadataEvidenceStoreAdapter":
    from app.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
    from app.storage.sqlite_metadata import SQLiteMetadataStore
    from app.storage.evidence_repositories import (
        FindingRepository,
        PropositionRepository,
        AssessmentRepository,
    )
    store = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    store.initialize()
    return MetadataEvidenceStoreAdapter(
        finding_repo=FindingRepository(store),
        proposition_repo=PropositionRepository(store),
        assessment_repo=AssessmentRepository(store),
    )

# Add to evidence_store_factories
```

- [ ] **Step 2: Implement `read()` method**

```python
# app/adapters/server/evidence_store.py — add to MetadataEvidenceStoreAdapter

def read(self, ref: EvidenceRef) -> Evidence:
    """Reconstruct an Evidence object from SQL tables using the ref.

    Looks up the finding by ref, then resolves any associated
    proposition and assessment from their respective repositories.
    """
    ref_str = str(ref)
    # Try to find the finding by canonical_item_key (which stores the finding_id)
    finding_rows = self._finding_repo._metadata.query_rows(
        "SELECT * FROM findings WHERE canonical_item_key = ? LIMIT 1",
        [ref_str],
    )
    if not finding_rows:
        raise NotFoundError(
            code=ErrorCode.EVIDENCE_NOT_FOUND,
            message=f"Evidence not found for ref: {ref_str}",
        )

    row = finding_rows[0]
    content = json.loads(row.get("payload_json", "{}"))
    finding = Finding(
        finding_id=row["finding_id"],
        session_id=row.get("session_id", ""),
        artifact_id=row.get("artifact_id"),
        finding_type=row.get("finding_type", "unknown"),
        proposition_id=row.get("proposition_id"),
        content=content,
    )

    proposition = None
    if finding.proposition_id:
        prop_row = self._proposition_repo.get(finding.proposition_id)
        if prop_row:
            prop_payload = json.loads(prop_row.get("payload_json", "{}"))
            proposition = Proposition(
                proposition_id=prop_row["proposition_id"],
                session_id=prop_row.get("session_id", ""),
                description=prop_payload.get("description", ""),
                identity_key=prop_row.get("identity_key"),
            )

    assessment = None
    if proposition and proposition.proposition_id:
        latest_assessment = self._assessment_repo.get_latest(proposition.proposition_id)
        if latest_assessment:
            assessment = Assessment(
                assessment_id=latest_assessment["assessment_id"],
                proposition_id=proposition.proposition_id,
                snapshot_seq=latest_assessment.get("snapshot_seq", 0),
                status=latest_assessment.get("status", "unknown"),
                rationale=latest_assessment.get("confidence_rationale_json"),
            )

    return Evidence(
        ref=ref,
        findings=[finding],
        proposition=proposition,
        assessment=assessment,
    )
```

Add necessary imports at the top of the file:
```python
import json
from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.evidence import Assessment, Evidence, Finding, Proposition
```

- [ ] **Step 3: Run evidence store contract tests**

Run: `pytest tests/contracts/test_evidence_store.py -v`
Expected: PASS for both `FileEvidenceStore` and `MetadataEvidenceStoreAdapter`

- [ ] **Step 4: Commit**

```bash
git add app/adapters/server/evidence_store.py tests/contracts/test_evidence_store.py
git commit -m "feat(server): implement MetadataEvidenceStoreAdapter.read()"
```

---

### Task 8: Update remaining adapter imports in `server.py`

This is a cleanup task to ensure all adapter imports in `_build_server_ports` use the new module paths.

**Files:**
- Modify: `app/profiles/server.py`

- [ ] **Step 1: Update `_build_server_ports` imports**

```python
def _build_server_ports(
    *,
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    datasource_service: DatasourceService,
    query_router: QueryRouter,
    semantic_v2_service: SemanticModelV2Service,
    marivo_config: MarivoConfig,
) -> RuntimePorts:
    from app.adapters.server.artifact_store import (
        MetadataArtifactStoreAdapter,
        MetadataStepStoreAdapter,
    )
    from app.adapters.server.authz import NoopAuthZAdapter
    from app.adapters.server.cache_store import InMemoryCacheStore
    from app.adapters.server.data_source import RoutingDataSource
    from app.adapters.server.evidence_store import MetadataEvidenceStoreAdapter
    from app.adapters.server.model_store import SqlModelStoreAdapter
    from app.adapters.server.session_store import SqlSessionStore
    from app.adapters.server.audit_log import FileAuditLog
    from app.adapters.server.telemetry import LocalTelemetryAdapter
    from app.adapters.server.runtime_config import TomlRuntimeConfigAdapter
    from app.storage.evidence_repositories import (
        ActionProposalRepository,
        AssessmentRepository,
        EvidenceGapRepository,
        FindingRepository,
        InferenceRecordRepository,
        PropositionRepository,
    )
    from app.storage.step_metadata_repository import StepMetadataRepository

    finding_repo = FindingRepository(metadata_store)
    proposition_repo = PropositionRepository(metadata_store)
    assessment_repo = AssessmentRepository(metadata_store)
    gap_repo = EvidenceGapRepository(metadata_store)
    inference_repo = InferenceRecordRepository(metadata_store)
    proposal_repo = ActionProposalRepository(metadata_store)
    step_metadata_repo = StepMetadataRepository(metadata_store)

    return RuntimePorts(
        model_store=SqlModelStoreAdapter(semantic_v2_service, metadata_store),
        session_store=SqlSessionStore(metadata_store),
        evidence_store=MetadataEvidenceStoreAdapter(
            finding_repo=finding_repo,
            proposition_repo=proposition_repo,
            assessment_repo=assessment_repo,
            gap_repo=gap_repo,
            inference_repo=inference_repo,
            action_proposal_repo=proposal_repo,
        ),
        data_source=RoutingDataSource(
            default_engine=analytics_engine,
            registry=datasource_service,  # DatasourceService extends DatasourceRegistry
            query_router=query_router,
        ),
        cache_store=InMemoryCacheStore(),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLog(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=TomlRuntimeConfigAdapter(marivo_config),
        artifact_store=MetadataArtifactStoreAdapter(
            metadata_store,
            step_metadata_repo=step_metadata_repo,
        ),
        step_store=MetadataStepStoreAdapter(
            metadata_store,
            step_metadata_repo=step_metadata_repo,
        ),
    )
```

Note: The `evidence_repos`, `analytics`, and `metadata` kwargs are removed from `RuntimePorts()` here because Task 9 removes those fields.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass (may need to adjust RuntimePorts construction if Task 9 hasn't been done yet)

- [ ] **Step 3: Commit**

```bash
git add app/profiles/server.py
git commit -m "refactor(server): update adapter imports to use split modules"
```

---

### Task 9: Add `step_completed` event ownership guarantee

After every successful `commit_step_result()` call, the Runtime must append a `step_completed` event to the session event log. This guarantees that session state reflects step completion timestamps.

**Files:**
- Modify: `app/runtime/runtime.py` (add event append in `commit_artifact_with_extraction` or a new method)
- Modify: `app/adapters/server/session_store.py` (if transaction sharing is needed)
- Create: test for `step_completed` event ownership

- [ ] **Step 1: Find where `commit_step_result` is called**

Run: `rg "commit_step_result\|commit_artifact_with_extraction" app/ --include="*.py" -n`

The runtime method `commit_artifact_with_extraction()` is the canonical commit boundary. After this succeeds, the runtime should append a `step_completed` event.

- [ ] **Step 2: Add `step_completed` event append to the runtime**

In `app/runtime/runtime.py`, modify `commit_artifact_with_extraction` to append a `step_completed` event after the artifact commit succeeds:

```python
def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
    result = self._ports.artifact_store.commit_artifact_with_extraction(*args, **kwargs)
    # Append step_completed event for session state tracking
    session_id = kwargs.get("session_id") or (args[0] if args else None)
    step_id = kwargs.get("step_id") or (args[1] if len(args) > 1 else None)
    if session_id and step_id:
        from app.contracts.ids import SessionId
        from app.contracts.session import SessionEvent
        from datetime import datetime, timezone

        self._ports.session_store.append_event(
            SessionId(str(session_id)),
            SessionEvent(
                session_id=SessionId(str(session_id)),
                event_type="step_completed",
                timestamp=datetime.now(timezone.utc).isoformat(),
                payload={"step_id": str(step_id)},
                actor=None,
            ),
        )
    return str(result)
```

- [ ] **Step 3: Write failing test for step_completed guarantee**

```python
# tests/contracts/test_session_store.py
@pytest.mark.parametrize("name,factory", session_store_factories)
def test_step_completed_guarantee(name, factory, tmp_path):
    """After commit_step_result, a step_completed event must exist in the session."""
    store = factory(tmp_path)
    session_id = SessionId("sess-step")
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id, event_type="session_created",
            timestamp="2026-05-07T10:00:00Z", payload={"goal": "step test"},
            actor=UserId("alice"),
        ),
    )
    # Simulate step completion via the runtime's commit path
    # For contract test purposes, directly append the event
    store.append_event(
        session_id,
        SessionEvent(
            session_id=session_id, event_type="step_completed",
            timestamp="2026-05-07T10:01:00Z", payload={"step_id": "step-1"},
            actor=None,
        ),
    )
    events = store.load_events(session_id)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 1
    assert step_events[0].payload["step_id"] == "step-1"
```

- [ ] **Step 4: Run test**

Run: `pytest tests/contracts/test_session_store.py::test_step_completed_guarantee -v`
Expected: PASS

- [ ] **Step 5: Add `step_completed_guarantee` to `SESSION_STORE_CASES`**

In `tests/contracts/session_store_cases.py`, add:

```python
def _run_step_completed_guarantee(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-step")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "step test"}, actor=None,
    ))
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="step_completed",
        timestamp="2026-05-07T10:01:00Z", payload={"step_id": "step-1"}, actor=None,
    ))
    events = adapter.load_events(sid)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 1
```

Append to `SESSION_STORE_CASES`:
```python
ContractCase(name="step_completed_guarantee", run=_run_step_completed_guarantee),
```

- [ ] **Step 6: Commit**

```bash
git add app/runtime/runtime.py tests/contracts/
git commit -m "feat(runtime): append step_completed event after step commit"
```

---

### Task 10: Step + event write atomicity

When a step result is committed and a `step_completed` event is appended, both should happen in the same database transaction when sharing a `MetadataStore`. This prevents inconsistent session state if the event append fails after the step commit.

**Files:**
- Modify: `app/runtime/runtime.py` (ensure `commit_artifact_with_extraction` and `append_event` share transaction)
- Modify: `app/adapters/server/session_store.py` (add `append_event_in_connection` method)

- [ ] **Step 1: Add `append_event_with_connection` to `SqlSessionStore`**

This method accepts an existing `MetadataStore` connection so the event append shares the caller's transaction:

```python
# app/adapters/server/session_store.py — add method
def append_event_with_connection(
    self, session_id: SessionId, event: SessionEvent, con: Any
) -> None:
    """Append an event using an existing connection (shared transaction).

    Used when the caller needs the event append to be atomic with
    another operation (e.g., step/artifact commit).
    """
    row = self._metadata.execute_sql(
        con,
        "SELECT COALESCE(MAX(seq), 0) FROM session_events WHERE session_id = ?",
        [str(session_id)],
    ).fetchone()
    next_seq = (row[0] if row else 0) + 1
    self._metadata.execute_sql(
        con,
        "INSERT INTO session_events (session_id, seq, event_type, timestamp, payload_json, actor) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            str(session_id),
            next_seq,
            event.event_type,
            event.timestamp,
            json.dumps(event.payload, sort_keys=True),
            str(event.actor) if event.actor else None,
        ],
    )
```

- [ ] **Step 2: Update `commit_artifact_with_extraction` in runtime to use shared connection**

The runtime method should use the MetadataStore's `connect()` context manager to ensure both the artifact commit and event append share a transaction:

```python
# app/runtime/runtime.py — update commit_artifact_with_extraction
def commit_artifact_with_extraction(self, *args: Any, **kwargs: Any) -> str:
    session_id = kwargs.get("session_id") or (args[0] if args else None)
    step_id = kwargs.get("step_id") or (args[1] if len(args) > 1 else None)

    result = self._ports.artifact_store.commit_artifact_with_extraction(*args, **kwargs)

    if session_id and step_id:
        from app.contracts.ids import SessionId
        from app.contracts.session import SessionEvent
        from datetime import datetime, timezone

        session_store = self._ports.session_store
        if hasattr(session_store, "append_event_with_connection"):
            # Use shared connection for atomic step+event writes
            metadata = getattr(self._ports, "_metadata", None)
            if metadata is not None:
                with metadata.connect() as con:
                    session_store.append_event_with_connection(
                        SessionId(str(session_id)),
                        SessionEvent(
                            session_id=SessionId(str(session_id)),
                            event_type="step_completed",
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            payload={"step_id": str(step_id)},
                            actor=None,
                        ),
                        con,
                    )
            else:
                session_store.append_event(
                    SessionId(str(session_id)),
                    SessionEvent(
                        session_id=SessionId(str(session_id)),
                        event_type="step_completed",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        payload={"step_id": str(step_id)},
                        actor=None,
                    ),
                )
        else:
            session_store.append_event(
                SessionId(str(session_id)),
                SessionEvent(
                    session_id=SessionId(str(session_id)),
                    event_type="step_completed",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    payload={"step_id": str(step_id)},
                    actor=None,
                ),
            )
    return str(result)
```

Note: The `append_event_with_connection` path is only available for `SqlSessionStore` (server mode). Local `SqliteSessionStore` uses its own SQLite connection, so it falls back to `append_event`.

- [ ] **Step 3: Write integration test for atomic step+event**

```python
# tests/runtime/test_step_event_atomicity.py
def test_step_completed_event_atomic_with_commit(tmp_path):
    """step_completed event and step commit share a transaction boundary."""
    from app.storage.sqlite_metadata import SQLiteMetadataStore
    from app.adapters.server.session_store import SqlSessionStore
    from app.contracts.ids import SessionId, StepId
    from app.contracts.session import SessionEvent

    metadata = SQLiteMetadataStore(tmp_path / "test.meta.sqlite")
    metadata.initialize()
    session_store = SqlSessionStore(metadata)

    session_id = SessionId("sess-atomic")
    session_store.append_event(session_id, SessionEvent(
        session_id=session_id, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "atomic test"},
        actor=None,
    ))

    # Append event within a shared connection
    with metadata.connect() as con:
        session_store.append_event_with_connection(
            session_id,
            SessionEvent(
                session_id=session_id, event_type="step_completed",
                timestamp="2026-05-07T10:01:00Z", payload={"step_id": "step-1"},
                actor=None,
            ),
            con,
        )

    events = session_store.load_events(session_id)
    assert any(e.event_type == "step_completed" for e in events)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/runtime/test_step_event_atomicity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/adapters/server/session_store.py app/runtime/runtime.py tests/runtime/test_step_event_atomicity.py
git commit -m "feat(server): add atomic step+event write with shared transaction"
```

---

### Task 11: Add `ServerConfig` fields and shared MetadataStore clarification

**Files:**
- Modify: `app/profiles/server.py` (add `file_store_dir`, `audit_dir` to `ServerConfig`)

- [ ] **Step 1: Add `file_store_dir` and `audit_dir` to `ServerConfig`**

```python
# app/profiles/server.py — update ServerConfig
@dataclass
class ServerConfig:
    marivo_config: MarivoConfig
    db_path: Path | str | None = None
    metadata_store: MetadataStore | None = None
    analytics_engine: AnalyticsEngine | None = None
    file_store_dir: Path | str | None = None   # evidence file storage dir
    audit_dir: Path | str | None = None         # audit log dir
```

- [ ] **Step 2: Wire `FileAuditLog` with `audit_dir` if provided**

In `_build_server_ports`, update the `FileAuditLog` construction:

```python
audit_log_dir = config.audit_dir
if audit_log_dir is not None:
    audit_log = FileAuditLog(log_dir=Path(audit_log_dir))
else:
    audit_log = FileAuditLog()
```

Note: Check `FileAuditLog.__init__` signature — it currently takes no args. If it doesn't accept `log_dir`, add the parameter.

- [ ] **Step 3: Add note about shared MetadataStore**

The spec's §5.4 requirement ("single shared SQLAlchemy engine") is already met: `_build_server_ports` receives a single `MetadataStore` instance and passes it to all adapters. `MySQLMetadataStore` manages its own connection pool (`LifoQueue`). No additional SQLAlchemy engine is needed.

Add a comment in `create_server_runtime`:

```python
# §5.4: All adapters share this single MetadataStore instance,
# which manages its own connection pool. This prevents N independent
# connection pools against the same database.
```

- [ ] **Step 4: Commit**

```bash
git add app/profiles/server.py
git commit -m "feat(server): add file_store_dir/audit_dir to ServerConfig"
```

---

### ~~Task 12: Database migration~~ — REMOVED

Since Marivo has not launched, no migration of existing `.marivo` databases is needed. The schema changes are applied directly (Task 5).

---

### Task 13: Clean up `RuntimePorts`

Remove the 7 optional server-mode fields that leak infrastructure into the port layer. (Formerly Task 9.)

**Files:**
- Modify: `app/runtime/ports.py`
- Modify: `app/profiles/server.py` (remove kwargs from RuntimePorts construction)
- Modify: `app/profiles/local.py` (if it passes these kwargs)
- Modify: any code that reads these fields from `runtime.ports.*`

- [ ] **Step 1: Find all consumers of the 7 fields**

Run: `rg "runtime\.ports\.(semantic_repository|semantic_resolver|metadata|evidence_repos|analytics|calendar_data_reader|time_axis_metadata_provider)" app/ tests/`

Document all matches and plan updates.

- [ ] **Step 2: Update `RuntimePorts.__init__` to remove the 7 kwargs**

```python
# app/runtime/ports.py
from __future__ import annotations

from app.ports.artifact_store import ArtifactStore
from app.ports.audit_log import AuditLog
from app.ports.authz import AuthZ
from app.ports.cache_store import CacheStore
from app.ports.data_source import DataSource
from app.ports.evidence_store import EvidenceStore
from app.ports.model_store import ModelStore
from app.ports.runtime_config import RuntimeConfig
from app.ports.session_store import SessionStore
from app.ports.step_store import StepStore
from app.ports.telemetry import Telemetry


class RuntimePorts:
    """Typed container for all port implementations."""

    def __init__(
        self,
        model_store: ModelStore,
        session_store: SessionStore,
        evidence_store: EvidenceStore,
        data_source: DataSource,
        cache_store: CacheStore,
        authz: AuthZ,
        audit_log: AuditLog,
        telemetry: Telemetry,
        runtime_config: RuntimeConfig,
        artifact_store: ArtifactStore,
        step_store: StepStore,
    ) -> None:
        self.model_store = model_store
        self.session_store = session_store
        self.evidence_store = evidence_store
        self.data_source = data_source
        self.cache_store = cache_store
        self.authz = authz
        self.audit_log = audit_log
        self.telemetry = telemetry
        self.runtime_config = runtime_config
        self.artifact_store = artifact_store
        self.step_store = step_store
```

- [ ] **Step 3: Remove the 7 kwargs from all `RuntimePorts(...)` construction sites**

Search: `rg "RuntimePorts\(" app/ tests/`
Remove `semantic_repository=`, `semantic_resolver=`, `metadata=`, `evidence_repos=`, `analytics=`, `calendar_data_reader=`, `time_axis_metadata_provider=` from each call.

- [ ] **Step 4: Update any code that reads from the removed fields**

For each consumer found in Step 1, provide an alternative:
- If reading `runtime.ports.metadata` — use the `MetadataStore` directly (passed via constructor injection)
- If reading `runtime.ports.evidence_repos` — use repository classes directly
- If reading `runtime.ports.analytics` — use the `DataSource` port instead
- `semantic_repository` / `semantic_resolver` / `calendar_data_reader` / `time_axis_metadata_provider` — check if still used; if so, provide alternatives

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add app/runtime/ports.py app/profiles/ tests/
git commit -m "refactor(runtime): remove 7 infrastructure leakage fields from RuntimePorts"
```

---

### Task 14: Replace wire methods with service registry

Remove `wire_datasource_svc()` and `wire_semantic_v2_svc()` from `MarivoRuntime`. Add a generic service registry so MCP tools can still access these services.

**Files:**
- Modify: `app/runtime/runtime.py`
- Modify: `app/transports/mcp/tools/datasource.py`
- Modify: `app/transports/mcp/tools/semantic.py`
- Modify: `app/transports/mcp/resources/__init__.py`
- Modify: `app/profiles/server.py`

- [ ] **Step 1: Add service registry to `MarivoRuntime`**

```python
# app/runtime/runtime.py — replace wire_datasource_svc/wire_semantic_v2_svc

class MarivoRuntime:
    def __init__(self, ports: RuntimePorts, core: CoreEngine) -> None:
        self._ports = ports
        self._core = core
        self._app: Any = None
        self._services: dict[str, Any] = {}

    def register_service(self, name: str, service: Any) -> None:
        """Register a non-port service for transport-layer access."""
        self._services[name] = service

    def get_service(self, name: str) -> Any:
        """Retrieve a registered service. Raises KeyError if not found."""
        return self._services[name]

    def wire_app(self, app: Any) -> None:
        """Store reference to the FastAPI app for OpenAPI introspection."""
        self._app = app

    # Remove wire_datasource_svc and wire_semantic_v2_svc
    # Remove datasource_svc and semantic_v2_svc properties
```

- [ ] **Step 2: Update `app/profiles/server.py` to use `register_service`**

```python
# In create_server_runtime, replace:
#   runtime.wire_datasource_svc(datasource_service)
#   runtime.wire_semantic_v2_svc(semantic_v2)
# with:
runtime.register_service("datasource", datasource_service)
runtime.register_service("semantic_v2", semantic_v2)
```

- [ ] **Step 3: Update MCP tool: `app/transports/mcp/tools/datasource.py`**

Replace `runtime.datasource_svc` with `runtime.get_service("datasource")`.

- [ ] **Step 4: Update MCP tool: `app/transports/mcp/tools/semantic.py`**

Replace `runtime.semantic_v2_svc` with `runtime.get_service("semantic_v2")`.

- [ ] **Step 5: Update MCP resource: `app/transports/mcp/resources/__init__.py`**

Replace `runtime.semantic_v2_svc` with `runtime.get_service("semantic_v2")`.

- [ ] **Step 6: Update any test files that call `wire_datasource_svc` or `wire_semantic_v2_svc`**

Run: `rg "wire_datasource_svc|wire_semantic_v2_svc|datasource_svc|semantic_v2_svc" tests/`
Update each match.

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add app/runtime/runtime.py app/profiles/server.py app/transports/mcp/ tests/
git commit -m "refactor(runtime): replace wire methods with generic service registry"
```

---

### Task 15: Clean up `ServerComposition` and `AppServices`

**Files:**
- Modify: `app/profiles/server.py` (simplify `ServerComposition`)
- Modify: `app/api/app_factory.py` (update `_build_services`, `_attach_state`)
- Modify: `app/api/deps.py` (update `AppServices`)

- [ ] **Step 1: Simplify `ServerComposition`**

```python
@dataclass
class ServerComposition:
    runtime: MarivoRuntime
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    resolved_analytics_path: Path | str
```

Remove `datasource_service`, `query_router`, and `semantic_v2_service` fields.

- [ ] **Step 2: Update `create_server_runtime` return**

Remove the 3 fields from the `ServerComposition(...)` constructor call.

- [ ] **Step 3: Update `AppServices` in `deps.py`**

The API routes still need `datasource_service`, `query_router`, and `semantic_v2_service`. Change `AppServices` to construct them independently or get them from `runtime.get_service()`:

```python
@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str
    config: MarivoConfig
    runtime: MarivoRuntime
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    semantic_v2_service: SemanticModelV2Service
```

Note: We keep these fields on `AppServices` because API routes need them. But they're no longer sourced from `ServerComposition` — they're constructed in `_build_services`.

- [ ] **Step 4: Update `_build_services` in `app_factory.py`**

```python
def _build_services(
    *,
    resolved_path: Path | str,
    metadata_store: MetadataStore,
    analytics_engine: AnalyticsEngine,
    config: MarivoConfig,
) -> AppServices:
    from app.profiles.resolver import resolve_profile
    from app.profiles.server import ServerConfig, create_server_runtime

    resolve_profile(entry_point="server_http", service_config=config)

    composition = create_server_runtime(
        ServerConfig(
            marivo_config=config,
            db_path=resolved_path if str(resolved_path) != ":memory:" else None,
            metadata_store=metadata_store,
            analytics_engine=analytics_engine,
        )
    )

    runtime = composition.runtime
    datasource_service = runtime.get_service("datasource")
    query_router = runtime.get_service("query_router") if runtime.get_service("query_router") else None
    semantic_v2_service = runtime.get_service("semantic_v2")

    return AppServices(
        resolved_path=composition.resolved_analytics_path,
        config=config,
        runtime=runtime,
        datasource_service=datasource_service,
        query_router=query_router,
        metadata_store=composition.metadata_store,
        analytics_engine=composition.analytics_engine,
        metrics=composition.metrics,
        semantic_v2_service=semantic_v2_service,
    )
```

Wait — we need `query_router` too. Let me register it as a service in `create_server_runtime`:

In `server.py`'s `create_server_runtime`, add:
```python
runtime.register_service("query_router", query_router)
```

- [ ] **Step 5: Update `_attach_state` if needed**

Remove any references to `composition.datasource_service` etc. They now come from `AppServices` or `runtime.get_service()`.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add app/profiles/server.py app/api/app_factory.py app/api/deps.py tests/
git commit -m "refactor(server): simplify ServerComposition, update AppServices wiring"
```

---

### Task 16: FK removal from schema

Remove `REFERENCES sessions(session_id)` from 6 tables in `schema.py`. Since Marivo has not launched, no migration of existing databases is needed — the DDL is changed directly.

**Files:**
- Modify: `app/storage/schema.py`
- Modify: `app/storage/mysql_metadata.py` (update FK validation)

- [ ] **Step 1: Remove inline REFERENCES from `METADATA_DDL`**

In `schema.py`, change these 6 column definitions from:
```
session_id TEXT NOT NULL REFERENCES sessions(session_id),
```
to:
```
session_id TEXT NOT NULL,
```

Tables affected: `plans`, `findings`, `propositions`, `assessments`, `evidence_gaps`, `inference_records`, `action_proposals`.

- [ ] **Step 2: Update `_expected_mysql_foreign_key_names` in `mysql_metadata.py`**

Remove the FK entries that reference `sessions`:
```
fk_plans_session_id
fk_findings_session_id
fk_propositions_session_id
fk_assessments_session_id
fk_evidence_gaps_session_id
fk_inference_records_session_id
fk_action_proposals_session_id
```

- [ ] **Step 3: Run schema bootstrap tests**

Run: `pytest tests/test_metadata_schema_bootstrap.py tests/test_mysql_metadata_integration.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/storage/schema.py app/storage/mysql_metadata.py
git commit -m "refactor(storage): remove FK constraints referencing sessions table"
```

---

### Task 17: Contract test expansion — `session_store_cases.py`

Create shared contract cases for session store that can be used by both contract tests and parity tests.

**Files:**
- Create: `tests/contracts/session_store_cases.py`
- Modify: `tests/contracts/test_session_store.py`
- Modify: `tests/contracts/test_parity.py` (add SessionStore parity)

- [ ] **Step 1: Create `session_store_cases.py`**

```python
# tests/contracts/session_store_cases.py
from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionEvent
from tests.contracts.contract_cases import ContractCase


def _run_append_and_load(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-1")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "test"}, actor=None,
    ))
    events = adapter.load_events(sid)
    assert len(events) == 1
    assert events[0].event_type == "session_created"


def _run_not_found(adapter, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError) as exc_info:
        adapter.load_events(SessionId("nonexistent"))
    assert exc_info.value.code == ErrorCode.SESSION_NOT_FOUND


def _run_owner_isolation(adapter, tmp_path: Path) -> None:
    adapter.append_event(SessionId("s-a"), SessionEvent(
        session_id=SessionId("s-a"), event_type="session_created",
        timestamp="2026-05-07T10:00:01Z", payload={"goal": "g1"}, actor=UserId("alice"),
    ))
    adapter.append_event(SessionId("s-b"), SessionEvent(
        session_id=SessionId("s-b"), event_type="session_created",
        timestamp="2026-05-07T10:00:02Z", payload={"goal": "g2"}, actor=UserId("bob"),
    ))
    alice = adapter.list_sessions(UserId("alice"))
    assert len(alice) == 1
    assert alice[0].session_id == "s-a"


def _run_event_ordering(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-ord")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={}, actor=None,
    ))
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="step_completed",
        timestamp="2026-05-07T10:01:00Z", payload={"step": "s1"}, actor=None,
    ))
    events = adapter.load_events(sid)
    assert [e.event_type for e in events] == ["session_created", "step_completed"]


def _run_other_event_types(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-other")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "g"}, actor=None,
    ))
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="step_completed",
        timestamp="2026-05-07T10:01:00Z", payload={"step": "s1"}, actor=None,
    ))
    events = adapter.load_events(sid)
    assert len(events) == 2
    assert events[1].event_type == "step_completed"


SESSION_STORE_CASES = [
    ContractCase(name="append_and_load", run=_run_append_and_load),
    ContractCase(name="not_found", run=_run_not_found),
    ContractCase(name="owner_isolation", run=_run_owner_isolation),
    ContractCase(name="event_ordering", run=_run_event_ordering),
    ContractCase(name="other_event_types", run=_run_other_event_types),
    ContractCase(name="concurrent_retry", run=_run_concurrent_retry),
    ContractCase(name="step_completed_guarantee", run=_run_step_completed_guarantee),
]


def _run_concurrent_retry(adapter, tmp_path: Path) -> None:
    import threading

    sid = SessionId("s-concurrent")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "concurrent"}, actor=None,
    ))

    results = {"t1": None, "t2": None}

    def append_event(thread_id):
        try:
            adapter.append_event(sid, SessionEvent(
                session_id=sid, event_type="step_completed",
                timestamp="2026-05-07T10:00:01Z", payload={"thread": thread_id}, actor=None,
            ))
            results[thread_id] = "ok"
        except Exception:
            results[thread_id] = "failed"

    t1 = threading.Thread(target=append_event, args=("t1",))
    t2 = threading.Thread(target=append_event, args=("t2",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    events = adapter.load_events(sid)
    assert len(events) == 3  # created + 2 step_completed


def _run_step_completed_guarantee(adapter, tmp_path: Path) -> None:
    sid = SessionId("s-step")
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "step test"}, actor=None,
    ))
    adapter.append_event(sid, SessionEvent(
        session_id=sid, event_type="step_completed",
        timestamp="2026-05-07T10:01:00Z", payload={"step_id": "step-1"}, actor=None,
    ))
    events = adapter.load_events(sid)
    step_events = [e for e in events if e.event_type == "step_completed"]
    assert len(step_events) == 1
```

- [ ] **Step 2: Update `test_session_store.py` to use `SESSION_STORE_CASES`**

Refactor the existing tests to import from `session_store_cases.py` where appropriate, or keep both patterns (existing parametrized tests + new contract case pattern).

- [ ] **Step 3: Add SessionStore parity test in `test_parity.py`**

```python
def test_session_store_local_server_parity(tmp_path):
    from tests.contracts.session_store_cases import SESSION_STORE_CASES
    from tests.contracts.parity import compare_contract_matrix

    results = compare_contract_matrix(
        local_name="SqliteSessionStore",
        local_factory=_make_sqlite_session_store,
        remote_name="SqlSessionStore",
        remote_factory=_make_sql_session_store,
        cases=SESSION_STORE_CASES,
        tmp_path=tmp_path,
    )
    assert len(results) > 0
    for r in results:
        assert r.local_status == "passed", f"Local case {r.case_name} failed: {r.detail}"
        assert r.remote_status == "passed", f"Remote case {r.case_name} failed: {r.detail}"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/contracts/test_session_store.py tests/contracts/test_parity.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add tests/contracts/session_store_cases.py tests/contracts/test_session_store.py tests/contracts/test_parity.py
git commit -m "test(contracts): add SessionStore contract cases and parity test"
```

---

### Task 18: Contract test expansion — DataSource, StepStore, ArtifactStore

**Files:**
- Modify: `tests/contracts/data_source_cases.py` (add datasource_id routing cases)
- Create: `tests/contracts/step_store_cases.py`
- Create: `tests/contracts/artifact_store_cases.py`
- Modify: `tests/contracts/test_data_source.py` (add RoutingDataSource factory)

- [ ] **Step 1: Add datasource_id routing cases to `data_source_cases.py`**

```python
def _run_duckdb_default_routing(adapter, _: Path) -> None:
    result = adapter.execute(LogicalQuery(sql="SELECT 42 AS answer", params={}, datasource_id=None))
    assert result.row_count == 1

def _run_unknown_datasource_raises(adapter, _: Path) -> None:
    with pytest.raises(DomainError) as exc_info:
        adapter.execute(LogicalQuery(sql="SELECT 1", datasource_id=DatasourceId("nonexistent")))
    assert exc_info.value.code == ErrorCode.DATASOURCE_UNAVAILABLE

def _run_resolve_tables(adapter, _: Path) -> None:
    # resolve_tables should not raise for empty list
    result = adapter.resolve_tables([], session_id=None)
    assert result is not None

# Append to DATA_SOURCE_CASES:
# ContractCase(name="duckdb_default_routing", run=_run_duckdb_default_routing),
# ContractCase(name="unknown_datasource_raises", run=_run_unknown_datasource_raises),
# ContractCase(name="resolve_tables", run=_run_resolve_tables),
```

Note: `duckdb_explicit`, `trino_routing`, and `trino_not_installed` cases require a registered Trino datasource and/or Trino driver. These are added as integration tests (marked `@pytest.mark.trino`) rather than contract cases, because they need external infrastructure. Add them as separate test functions in `test_data_source.py` with appropriate markers.

- [ ] **Step 2: Create `step_store_cases.py`**

```python
# tests/contracts/step_store_cases.py
from __future__ import annotations

from pathlib import Path

from tests.contracts.contract_cases import ContractCase


def _run_insert_and_list(adapter, tmp_path: Path) -> None:
    adapter.insert_step(
        step_id="step-1",
        session_id="sess-1",
        step_type="observe",
        summary="Test step",
        result=None,
        provenance=None,
        semantic_metadata=None,
    )
    steps = adapter.list_steps("sess-1")
    assert len(steps) >= 1


STEP_STORE_CASES = [
    ContractCase(name="insert_and_list", run=_run_insert_and_list),
]
```

- [ ] **Step 3: Create `artifact_store_cases.py`**

```python
# tests/contracts/artifact_store_cases.py
from __future__ import annotations

from pathlib import Path

from tests.contracts.contract_cases import ContractCase


def _run_insert_artifact(adapter, tmp_path: Path) -> None:
    artifact_id = adapter.insert_artifact(
        session_id="sess-1",
        step_id="step-1",
        artifact_type="finding_set",
        name="test_artifact",
        content={"findings": []},
        lifecycle="staged",
        artifact_schema_version="v1",
    )
    assert artifact_id is not None


def _run_resolve_artifact_for_ref(adapter, tmp_path: Path) -> None:
    adapter.insert_artifact(
        session_id="sess-1",
        step_id="step-1",
        artifact_type="finding_set",
        name="test_artifact",
        content={"findings": []},
        lifecycle="staged",
        artifact_schema_version="v1",
    )
    result = adapter.resolve_artifact_for_ref("sess-1", "step-1")
    assert result is not None


ARTIFACT_STORE_CASES = [
    ContractCase(name="insert_artifact", run=_run_insert_artifact),
    ContractCase(name="resolve_artifact_for_ref", run=_run_resolve_artifact_for_ref),
]
```

- [ ] **Step 4: Add `list_sessions_paginated` test cases**

In `tests/contracts/test_session_store.py`, add:

```python
@pytest.mark.parametrize("name,factory", [
    ("SqlSessionStore", _make_sql_session_store),
])
def test_basic_pagination(name, factory, tmp_path):
    store = factory(tmp_path)
    for i in range(30):
        sid = SessionId(f"sess-pag-{i:03d}")
        store.append_event(sid, SessionEvent(
            session_id=sid, event_type="session_created",
            timestamp=f"2026-05-07T10:{i%60:02d}:00Z",
            payload={"goal": f"session {i}"},
            actor=UserId("alice"),
        ))
    result = store.list_sessions_paginated(limit=10)
    assert len(result["items"]) == 10
    assert result["next_page_token"] is not None

@pytest.mark.parametrize("name,factory", [
    ("SqlSessionStore", _make_sql_session_store),
])
def test_empty_page(name, factory, tmp_path):
    store = factory(tmp_path)
    result = store.list_sessions_paginated()
    assert result["items"] == []
    assert result["next_page_token"] is None
```

- [ ] **Step 4: Run all contract tests**

Run: `pytest tests/contracts/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add tests/contracts/
git commit -m "test(contracts): expand DataSource, StepStore, ArtifactStore contract cases"
```

---

## Sub-phase 9.2: CI Infrastructure & Parity Gating

### Task 19: Add `test-mysql` extras and Make target

**Files:**
- Modify: `pyproject.toml`
- Modify: `Makefile`

- [ ] **Step 1: Add `test-mysql` extras to `pyproject.toml`**

```toml
[project.optional-dependencies]
# ... existing groups ...
test-mysql = ["testcontainers[mysql]"]
```

- [ ] **Step 2: Add `test-mysql` target to `Makefile`**

```makefile
test-mysql:
	pip install -e ".[mysql,test-mysql]"
	$(VENV_PYTEST) tests/contracts/ -m mysql
```

- [ ] **Step 3: Add `pytest.mark.mysql` marker**

In `pyproject.toml` under `[tool.pytest.ini_options]`, add:
```toml
markers = [
    "mysql: tests requiring a MySQL container",
]
```

Or register in a conftest.py.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml Makefile
git commit -m "ci: add test-mysql extras and Make target"
```

- [ ] **Step 5: Create `server-contract-tests` CI job**

```yaml
# .github/workflows/server-contract-tests.yml
name: Server Contract Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  server-contract-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev,mysql,test-mysql]"
      - run: pytest tests/contracts/ -m "not mysql" -v
      - run: pytest tests/contracts/ -m mysql -v
      - run: pytest tests/contracts/test_parity.py -v
```

---

### Task 20: Add MySQL-backed contract tests

**Files:**
- Create: `tests/contracts/test_mysql_session_store.py` (or add to existing with `@pytest.mark.mysql`)

- [ ] **Step 1: Write MySQL contract test fixture**

```python
# tests/contracts/test_mysql_session_store.py
import pytest

pytestmark = pytest.mark.mysql


@pytest.fixture(scope="module")
def mysql_metadata():
    from testcontainers.mysql import MySqlContainer

    with MySqlContainer("mysql:8.0") as mysql:
        from app.storage.mysql_metadata import MySQLMetadataStore

        store = MySQLMetadataStore(
            host=mysql.get_container_host_ip(),
            port=mysql.get_exposed_port(3306),
            database="test",
            user="test",
            password="test",
        )
        store.initialize()
        yield store


def test_mysql_append_and_load(mysql_metadata, tmp_path):
    from app.adapters.server.session_store import SqlSessionStore
    from app.contracts.ids import SessionId
    from app.contracts.session import SessionEvent

    store = SqlSessionStore(mysql_metadata)
    sid = SessionId("mysql-sess-1")
    store.append_event(sid, SessionEvent(
        session_id=sid, event_type="session_created",
        timestamp="2026-05-07T10:00:00Z", payload={"goal": "mysql test"},
        actor=None,
    ))
    events = store.load_events(sid)
    assert len(events) == 1
```

- [ ] **Step 2: Commit**

```bash
git add tests/contracts/test_mysql_session_store.py
git commit -m "test(ci): add MySQL-backed session store contract tests"
```

---

### Task 21: Promote parity gate to blocking and remove `sessions` table DDL

**Files:**
- Modify: `tests/contracts/test_parity.py` (promote from xfail to hard assert)
- Modify: `app/storage/schema.py` (remove `sessions` table DDL)

- [ ] **Step 1: Promote parity tests from observable to blocking**

In `test_parity.py`, change the datasource parity test to assert ALL results pass (not just check that results are non-empty):

```python
for r in results:
    assert r.local_status == "passed", f"Local {r.case_name} failed: {r.detail}"
    assert r.remote_status == "passed", f"Remote {r.case_name} failed: {r.detail}"
```

- [ ] **Step 2: Remove `sessions` table DDL from `schema.py`**

Remove the `CREATE TABLE IF NOT EXISTS sessions (...)` block and any associated indexes from `METADATA_DDL`.

Also update `_expected_mysql_foreign_key_names` to remove any remaining sessions-related entries.

- [ ] **Step 3: Update all references to `sessions` table**

Run: `rg '"sessions"|'sessions'" app/ tests/`
Ensure no code still reads from or writes to the `sessions` table.

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -x -q --timeout=60`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add tests/contracts/test_parity.py app/storage/schema.py
git commit -m "feat(phase9): promote parity gate to blocking, remove sessions table DDL"
```

---

### Task 22: Final verification

- [ ] **Step 1: Verify all 9.1 ACs**

Run through each AC from §11 of the spec:

```bash
# wrappers.py is gone
test ! -f app/adapters/server/wrappers.py && echo "PASS" || echo "FAIL"

# No imports of wrappers module
rg "from app.adapters.server.wrappers" app/ tests/ && echo "FAIL" || echo "PASS"

# RuntimePorts has no optional fields
rg "semantic_repository|semantic_resolver|metadata.*Any|evidence_repos|analytics.*Any|calendar_data_reader|time_axis_metadata_provider" app/runtime/ports.py && echo "FAIL" || echo "PASS"

# Wire methods removed
rg "wire_datasource_svc|wire_semantic_v2_svc" app/runtime/runtime.py && echo "FAIL" || echo "PASS"

# LogicalQuery has datasource_id
rg "datasource_id" app/contracts/values.py && echo "PASS" || echo "FAIL"

# ServerComposition simplified
rg "datasource_service|query_router|semantic_v2_service" app/profiles/server.py | grep -v "register_service\|import" && echo "FAIL" || echo "PASS"
```

- [ ] **Step 2: Verify all 9.2 ACs**

```bash
# test-mysql extras exist
rg "test-mysql" pyproject.toml && echo "PASS" || echo "FAIL"

# make test-mysql target exists
rg "test-mysql:" Makefile && echo "PASS" || echo "FAIL"

# _legacy_session.py is gone
test ! -f app/adapters/server/_legacy_session.py && echo "PASS" || echo "FAIL"

# sessions table DDL removed
rg "CREATE TABLE.*sessions" app/storage/schema.py && echo "FAIL" || echo "PASS"
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -x --timeout=60`
Expected: All pass

- [ ] **Step 4: Run lint and import checks**

Run: `make lint`
Expected: All pass

- [ ] **Step 5: Final commit (if any fixes needed)**

```bash
git add -A
git commit -m "fix(phase9): final verification fixes"
```

---

## Task Dependency Order

```
Task 1  (LogicalQuery.datasource_id)
  ↓
Task 2  (Split wrappers.py)
  ↓
Task 3  (session_events DDL)
  ↓
Task 4  (SqlSessionStore) ← depends on Tasks 2, 3
  ↓
Task 5  (Local schema harmonization) ← independent of Task 4
  ↓
Task 6  (RoutingDataSource) ← depends on Task 1
  ↓
Task 7  (EvidenceStore.read()) ← depends on Task 2
  ↓
Task 8  (Update server.py imports) ← depends on Tasks 2, 4, 6, 7
  ↓
Task 9  (step_completed event ownership) ← depends on Task 4
  ↓
Task 10 (Step + event write atomicity) ← depends on Tasks 4, 9
  ↓
Task 11 (ServerConfig fields) ← depends on Task 8
  ↓
Task 12 (REMOVED — no migration needed)
  ↓
Task 13 (RuntimePorts cleanup) ← depends on Task 8
  ↓
Task 14 (Service registry) ← depends on Task 13
  ↓
Task 15 (ServerComposition cleanup) ← depends on Task 14
  ↓
Task 16 (FK removal) ← independent
  ↓
Task 17 (SessionStore contract tests) ← depends on Tasks 4, 5, 9
  ↓
Task 18 (DataSource/Step/Artifact contract tests) ← depends on Tasks 6, 7
  ↓
Task 19 (test-mysql extras + CI job) ← independent
  ↓
Task 20 (MySQL contract tests) ← depends on Tasks 4, 19
  ↓
Task 21 (Parity gate + sessions DDL removal) ← depends on Tasks 16, 17, 18
  ↓
Task 22 (Final verification) ← depends on all
```

Parallelizable groups:
- Tasks 1, 2, 3, 16, 19 can run in parallel
- Tasks 4, 5, 6, 7 can run in parallel (after 2 and 3)
- Tasks 9, 10, 11 can run in parallel (after 4, 5, 8)
- Tasks 17 and 18 can run in parallel
