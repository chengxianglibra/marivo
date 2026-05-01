# Semantic Layer Dual-Path Update Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce strict separation between import (official) and CRUD (private) update paths, with per-model versioning (no global version table), visibility-gated writes, same-name validation, and session-level semantic snapshots.

**Architecture:** Drop `semantic_versions` table. Add `revision` column to `semantic_models`. Import does per-model upsert (increment revision, replace children in place). Official models can coexist with same-name private models (official takes priority in resolution). No history table — official model definitions are in Git. Session snapshots record `(model_name, revision)` for audit. All schema changes are destructive (no backward compatibility).

**Tech Stack:** Python, FastAPI, SQLite, Pydantic

**Spec:** `docs/superpowers/specs/2026-05-01-semantic-layer-update-modes-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/storage/schema.py` | Modify | Destructive DDL: drop semantic_versions, rewrite semantic_models with revision, add session tables |
| `app/semantic_service_v2/service.py` | Modify | Remove all semantic_version_id references, add per-model import upsert, visibility guards, same-name check |
| `app/api/semantic_v2.py` | Modify | Pass `requesting_user` to sub-entity routes |
| `app/semantic_service_v2/session.py` | Create | Session snapshot service |
| `app/api/session.py` | Create | Session API routes |
| `app/app_factory.py` | Modify | Wire session service and routes |
| `tests/test_semantic_v2_api.py` | Modify | Update tests for new versioning model, add visibility guard and same-name tests |
| `tests/test_session_api.py` | Create | Session snapshot tests |

---

### Task 1: Destructive DDL — drop semantic_versions, rewrite semantic_models with revision, add session tables

**Files:**
- Modify: `app/storage/schema.py`

- [ ] **Step 1: Rewrite `semantic_models` DDL and drop `semantic_versions`**

In `app/storage/schema.py`, find the `semantic_versions` CREATE TABLE and the `semantic_models` CREATE TABLE. Replace both with:

```sql
DROP TABLE IF EXISTS semantic_versions;

CREATE TABLE IF NOT EXISTS semantic_models (
    model_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    ai_context  TEXT,
    visibility  TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
    owner_user  TEXT,
    revision    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_semantic_models_visibility_owner
    ON semantic_models(visibility, owner_user);
```

Note: `semantic_version_id` column is removed. The old `idx_semantic_models_version_visibility` index is also removed since there's no version column anymore.

- [ ] **Step 2: Remove `evaluated_semantic_version_id` from `semantic_readiness_status`**

Find the `semantic_readiness_status` CREATE TABLE and rewrite:

```sql
CREATE TABLE IF NOT EXISTS semantic_readiness_status (
    model_id    INTEGER PRIMARY KEY REFERENCES semantic_models(model_id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('ready', 'not_ready')),
    blockers    TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 3: Add session and snapshot tables**

Add after `semantic_readiness_status`:

```sql
CREATE TABLE IF NOT EXISTS analysis_sessions (
    session_id          TEXT PRIMARY KEY,
    requesting_user     TEXT NOT NULL,
    snapshot_frozen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at            TEXT
);

CREATE TABLE IF NOT EXISTS session_semantic_snapshots (
    snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES analysis_sessions(session_id),
    model_name          TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    visibility          TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    owner_user          TEXT,
    frozen_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_snapshots_session
    ON session_semantic_snapshots(session_id);
```

- [ ] **Step 4: Remove all references to `semantic_versions` and `semantic_version_id` from schema.py**

Search for any remaining references to `semantic_versions` or `semantic_version_id` in the file (indexes, comments, MySQL conversion logic) and remove them.

- [ ] **Step 5: Verify DDL loads**

Run: `.venv/bin/pytest tests/test_semantic_v2_api.py -v -k "test_list_empty"`
Expected: Likely FAIL because service code still references `semantic_version_id`. This will be fixed in Task 2.

- [ ] **Step 6: Commit**

```bash
git add app/storage/schema.py
git commit -m "feat!: drop semantic_versions, add revision to semantic_models, add session tables

Breaking change: global versioning removed, per-model revision replaces it."
```

---

### Task 2: Remove all `semantic_version_id` references from service code and rewrite import as per-model upsert

**Files:**
- Modify: `app/semantic_service_v2/service.py`

This is the biggest change. All `semantic_version_id` / `_ensure_version` / `_get_latest_version_id` references must be removed, and `import_osi_document` must be rewritten.

- [ ] **Step 1: Remove version helper methods**

Delete `_get_latest_version_id` and `_ensure_version` methods from `SemanticModelV2Service`.

- [ ] **Step 2: Remove `semantic_version_id` from `create_semantic_model`**

In `create_semantic_model`, remove the version association logic:

```python
        # REMOVE these lines:
        # visibility = enriched.get("visibility", "public")
        # version_id = self._ensure_version() if visibility == "public" else None
```

And remove `semantic_version_id` from the INSERT statement:

```python
        self.store.execute(
            """
            INSERT INTO semantic_models
                (name, description, ai_context, visibility, owner_user)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                storage_data["name"],
                storage_data["description"],
                storage_data["ai_context"],
                storage_data["visibility"],
                storage_data["owner_user"],
            ],
        )
```

- [ ] **Step 3: Remove version filter from `list_semantic_models`**

Replace `list_semantic_models` with:

```python
    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        """List semantic models with visibility filtering.

        Returns all official models + private models owned by requesting_user.
        Same-name models can appear twice (one official, one private).
        No version-based filtering — semantic_models always contains the current state.
        """
        results: list[dict[str, Any]] = []

        # All public models
        public_rows = self.store.query_rows(
            "SELECT * FROM semantic_models WHERE visibility = 'public' ORDER BY name"
        )
        for row in public_rows:
            results.append(self._assemble_model(row))

        # Private models owned by requesting_user
        if requesting_user:
            private_rows = self.store.query_rows(
                "SELECT * FROM semantic_models WHERE visibility = 'private' AND owner_user = ? ORDER BY name",
                [requesting_user],
            )
            for row in private_rows:
                # Include private model even if official model with same name exists
                results.append(self._assemble_model(row))

        return results
```

- [ ] **Step 4: Rewrite `import_osi_document` for per-model upsert**

Replace the entire `import_osi_document` method:

```python
    def import_osi_document(self, doc_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Import an OSI document with per-model upsert.

        Each model in the document is independently imported:
        - If an official model with the same name exists, UPDATE in place
          (increment revision, replace all child entities).
        - If no model with that name exists, INSERT with revision=1.
        - Models NOT in the document are left untouched.
        """
        doc = OSIDocument.model_validate(doc_data)

        # Reject private models in imported documents
        for sm in doc.semantic_model:
            marivo_ext = extract_marivo_extension(
                sm.custom_extensions, MarivoSemanticModelExtension
            )
            if marivo_ext and marivo_ext.visibility == "private":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot import private model '{sm.name}' via OSI document",
                )

        results: list[dict[str, Any]] = []
        for sm in doc.semantic_model:
            model_dict = sm.model_dump(by_alias=True, exclude_none=True)

            # Ensure MARIVO extension has visibility=public
            custom_exts = model_dict.get("custom_extensions") or []
            has_marivo_ext = False
            for ext in custom_exts:
                if ext.get("vendor_name") == "MARIVO":
                    import json
                    data = ext.get("data")
                    parsed = json.loads(data) if isinstance(data, str) else data
                    parsed["visibility"] = "public"
                    parsed.pop("owner_user", None)
                    ext["data"] = json.dumps(parsed)
                    has_marivo_ext = True
                    break
            if not has_marivo_ext:
                import json
                custom_exts.append(
                    {"vendor_name": "MARIVO", "data": json.dumps({"visibility": "public"})}
                )
                model_dict["custom_extensions"] = custom_exts

            # Enrich and validate
            enriched = self._enrich_model_dict_with_marivo(model_dict)
            validate_semantic_model(enriched)

            # Check if model with same name already exists
            existing_row = self._get_model_row_by_name(sm.name)
            model = SemanticModel.model_validate(model_dict)
            storage_data = model_to_storage(model)

            if existing_row is not None and existing_row["visibility"] == "public":
                # Update existing official model — increment revision, replace children
                model_id = existing_row["model_id"]
                new_revision = existing_row["revision"] + 1

                # Delete children (will re-insert below)
                self.store.execute("DELETE FROM semantic_metrics WHERE model_id = ?", [model_id])
                self.store.execute("DELETE FROM semantic_relationships WHERE model_id = ?", [model_id])
                self.store.execute(
                    "DELETE FROM semantic_fields WHERE dataset_id IN (SELECT dataset_id FROM semantic_datasets WHERE model_id = ?)",
                    [model_id],
                )
                self.store.execute("DELETE FROM semantic_datasets WHERE model_id = ?", [model_id])

                # Update model row
                self.store.execute(
                    """
                    UPDATE semantic_models
                    SET description = ?, ai_context = ?, revision = ?, updated_at = datetime('now')
                    WHERE model_id = ?
                    """,
                    [
                        storage_data["description"],
                        storage_data["ai_context"],
                        new_revision,
                        model_id,
                    ],
                )
            elif existing_row is not None and existing_row["visibility"] == "private":
                # Private model with same name exists — insert official model alongside it
                # Both will exist; official takes priority in resolution
                self.store.execute(
                    """
                    INSERT INTO semantic_models
                        (name, description, ai_context, visibility, owner_user, revision)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    [
                        storage_data["name"],
                        storage_data["description"],
                        storage_data["ai_context"],
                        "public",
                        None,
                    ],
                )
                # Lookup by name + visibility since both rows now exist
                official_row = self.store.query_one(
                    "SELECT model_id FROM semantic_models WHERE name = ? AND visibility = 'public'",
                    [sm.name],
                )
                assert official_row is not None
                model_id = official_row["model_id"]
            else:
                # New model — insert with revision=1
                self.store.execute(
                    """
                    INSERT INTO semantic_models
                        (name, description, ai_context, visibility, owner_user, revision)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    [
                        storage_data["name"],
                        storage_data["description"],
                        storage_data["ai_context"],
                        "public",
                        None,
                    ],
                )
                model_id = self._require_model_row(sm.name)["model_id"]

            # Insert datasets + fields
            for ds in model.datasets:
                ds_storage = dataset_to_storage(ds, model_id)
                self.store.execute(
                    """
                    INSERT INTO semantic_datasets
                        (model_id, name, source, primary_key, unique_keys, description,
                         ai_context, datasource_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ds_storage["model_id"],
                        ds_storage["name"],
                        ds_storage["source"],
                        ds_storage["primary_key"],
                        ds_storage["unique_keys"],
                        ds_storage["description"],
                        ds_storage["ai_context"],
                        ds_storage["datasource_id"],
                    ],
                )
                ds_row = self.store.query_one(
                    "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
                    [model_id, ds.name],
                )
                assert ds_row is not None
                dataset_id = ds_row["dataset_id"]
                for pos, field in enumerate(ds.fields or []):
                    f_storage = field_to_storage(field, dataset_id, pos)
                    self.store.execute(
                        """
                        INSERT INTO semantic_fields
                            (dataset_id, name, expression, is_time, label, description,
                             ai_context, data_type, position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            f_storage["dataset_id"],
                            f_storage["name"],
                            f_storage["expression"],
                            f_storage["is_time"],
                            f_storage["label"],
                            f_storage["description"],
                            f_storage["ai_context"],
                            f_storage["data_type"],
                            f_storage["position"],
                        ],
                    )

            for rel in model.relationships or []:
                rel_storage = relationship_to_storage(rel, model_id)
                self.store.execute(
                    """
                    INSERT INTO semantic_relationships
                        (model_id, name, from_dataset, to_dataset, from_columns,
                         to_columns, ai_context, cardinality)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        rel_storage["model_id"],
                        rel_storage["name"],
                        rel_storage["from_dataset"],
                        rel_storage["to_dataset"],
                        rel_storage["from_columns"],
                        rel_storage["to_columns"],
                        rel_storage["ai_context"],
                        rel_storage["cardinality"],
                    ],
                )

            for metric in model.metrics or []:
                metric_storage = metric_to_storage(metric, model_id)
                self.store.execute(
                    """
                    INSERT INTO semantic_metrics
                        (model_id, name, expression, description, ai_context,
                         observed_dataset, observation_grain, primary_time_field,
                         additivity, filters)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        metric_storage["model_id"],
                        metric_storage["name"],
                        metric_storage["expression"],
                        metric_storage["description"],
                        metric_storage["ai_context"],
                        metric_storage["observed_dataset"],
                        metric_storage["observation_grain"],
                        metric_storage["primary_time_field"],
                        metric_storage["additivity"],
                        metric_storage["filters"],
                    ],
                )

            # Upsert readiness status
            existing_readiness = self.store.query_one(
                "SELECT 1 FROM semantic_readiness_status WHERE model_id = ?", [model_id]
            )
            if not existing_readiness:
                self.store.execute(
                    "INSERT INTO semantic_readiness_status (model_id, status, blockers) VALUES (?, 'not_ready', '[]')",
                    [model_id],
                )

            # Re-fetch the official model row (may coexist with private same-name model)
            model_row = self.store.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'public'",
                [sm.name],
            )
            results.append(self._assemble_model(model_row))

        return results
```

- [ ] **Step 5: Add `revision` to `_assemble_model` output**

In `_assemble_model`, change the return to include revision:

```python
        result = storage_to_model(dict(model_row), datasets, relationships, metrics)
        result["revision"] = model_row["revision"]
        return result
```

- [ ] **Step 6: Remove `semantic_version_id` from `get_readiness`**

In `get_readiness`, replace:

```python
    def get_readiness(self, model_name: str) -> dict[str, Any]:
        """Return readiness status/blockers for a model."""
        model_row = self._require_model_row(model_name)
        readiness_row = self.store.query_one(
            "SELECT status, blockers FROM semantic_readiness_status WHERE model_id = ?",
            [model_row["model_id"]],
        )
        if readiness_row is None:
            return {
                "status": "not_ready",
                "blockers": [],
            }
        import json
        blockers = readiness_row["blockers"]
        return {
            "status": readiness_row["status"],
            "blockers": json.loads(blockers) if blockers else [],
        }
```

- [ ] **Step 7: Run tests and fix failures**

Run: `.venv/bin/pytest tests/test_semantic_v2_api.py -v`

Expected: Several failures due to removed `semantic_version_id`. Fix each test that references it. The test helper `_TestMetadataStore.initialize()` runs DDL directly so it will create the new schema. Tests that assert on `semantic_version_id` in responses need updating.

Key test fixes:
- `TestReadinessAPI::test_get_readiness` — remove assertions on `semantic_version_id` and `evaluated_semantic_version_id`
- `TestImportOSIDocumentAPI` — should still pass (import still works, just different version semantics)

- [ ] **Step 8: Add per-model import tests**

Add to `tests/test_semantic_v2_api.py`:

```python
class TestPerModelImport(unittest.TestCase):
    def test_import_updates_only_included_models(self) -> None:
        """Import model A should not affect existing model B."""
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                },
                {
                    "name": "growth",
                    "datasets": [
                        {
                            "name": "events",
                            "source": "analytics.events",
                            "primary_key": ["event_id"],
                            "fields": [
                                {"name": "event_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "event_id"}]}},
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc)
        # Second import: only commerce updated
        doc2 = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders_v2",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                                {"name": "amount", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]}},
                            ],
                        }
                    ],
                },
            ],
        }
        resp = client.post("/semantic-models/import", json=doc2)
        self.assertEqual(resp.status_code, 200)
        # commerce updated
        commerce = client.get("/semantic-models/commerce").json()["semantic_model"][0]
        self.assertEqual(commerce["datasets"][0]["source"], "analytics.orders_v2")
        self.assertEqual(len(commerce["datasets"][0]["fields"]), 2)
        # growth unchanged
        growth = client.get("/semantic-models/growth").json()["semantic_model"][0]
        self.assertEqual(growth["datasets"][0]["source"], "analytics.events")

    def test_import_increments_revision(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 1)
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 2)

    def test_import_official_model_with_same_name_as_private_succeeds(self) -> None:
        """Importing official model when private model with same name exists should succeed."""
        client = _make_app()
        # Create private model first
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce", visibility="private", owner_user="alice"),
        )
        # Import official model with same name
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                },
            ],
        }
        resp = client.post("/semantic-models/import", json=doc)
        self.assertEqual(resp.status_code, 200)
        # Both models should exist
        models = client.get("/semantic-models", params={"requesting_user": "alice"}).json()["semantic_model"]
        commerce_models = [m for m in models if m["name"] == "commerce"]
        self.assertEqual(len(commerce_models), 2)  # one official, one private

    def test_import_new_model_revision_is_1(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 1)
```

- [ ] **Step 9: Run all tests**

Run: `.venv/bin/pytest tests/test_semantic_v2_api.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add app/semantic_service_v2/service.py tests/test_semantic_v2_api.py
git commit -m "feat!: remove global versioning, rewrite import as per-model upsert

Breaking change: semantic_versions table dropped, per-model revision
replaces global version. Import updates only models in the document.
list_semantic_models no longer filters by version."
```

---

### Task 3: Add `_require_private_model` helper and enforce on model-level writes

**Files:**
- Modify: `app/semantic_service_v2/service.py`
- Modify: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_semantic_v2_api.py`:

```python
class TestVisibilityGuardOnModelWrites(unittest.TestCase):
    def test_create_public_model_via_crud_returns_403(self) -> None:
        client = _make_app()
        resp = client.post("/semantic-models", json=_make_model_dict(name="new_public"))
        self.assertEqual(resp.status_code, 403)

    def test_create_private_model_via_crud_succeeds(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="new_private", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 200)

    def test_update_official_model_returns_403(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)
        resp = client.put("/semantic-models/official_model", json={"description": "new"})
        self.assertEqual(resp.status_code, 403)

    def test_update_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.put("/semantic-models/priv_model", json={"description": "new"})
        self.assertEqual(resp.status_code, 200)

    def test_delete_official_model_returns_403(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)
        resp = client.delete("/semantic-models/official_model")
        self.assertEqual(resp.status_code, 403)

    def test_delete_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.delete("/semantic-models/priv_model")
        self.assertEqual(resp.status_code, 204)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add `_require_private_model` helper and enforce guards**

Add after `_require_model_row` in `app/semantic_service_v2/service.py`:

```python
    def _require_private_model(self, name: str) -> dict[str, Any]:
        """Look up a model and raise 403 if it is not private."""
        row = self._require_model_row(name)
        if row["visibility"] != "private":
            raise HTTPException(
                status_code=403,
                detail=f"Cannot modify official semantic model '{name}' via CRUD; use /semantic-models/import",
            )
        return row
```

Add to `create_semantic_model`, before validation:

```python
        # Reject creation of official models via CRUD
        enriched_pre = self._enrich_model_dict_with_marivo(model_data)
        if enriched_pre.get("visibility", "public") != "private":
            raise HTTPException(
                status_code=403,
                detail="Cannot create official semantic model via CRUD; use /semantic-models/import",
            )
```

Replace `self._require_model_row(name)` with `self._require_private_model(name)` in:
- `update_semantic_model`
- `delete_semantic_model`

- [ ] **Step 4: Fix existing CRUD tests that create public models**

Update tests that use `_make_model_dict()` without visibility to use `visibility="private", owner_user="alice"`.

- [ ] **Step 5: Run all tests**

- [ ] **Step 6: Commit**

```bash
git add app/semantic_service_v2/service.py tests/test_semantic_v2_api.py
git commit -m "feat: add visibility guard on model-level CRUD writes"
```

---

### Task 4: Enforce visibility guard on sub-entity writes

**Files:**
- Modify: `app/semantic_service_v2/service.py`
- Modify: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Write failing tests for sub-entity visibility guard**

Add to `tests/test_semantic_v2_api.py`:

```python
class TestVisibilityGuardOnSubEntityWrites(unittest.TestCase):
    def _create_official_model(self, client: TestClient) -> None:
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)

    def test_create_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.post("/semantic-models/official_model/datasets", json=_make_dataset_dict())
        self.assertEqual(resp.status_code, 403)

    def test_update_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.put("/semantic-models/official_model/datasets/orders", json={"description": "new"})
        self.assertEqual(resp.status_code, 403)

    def test_delete_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.delete("/semantic-models/official_model/datasets/orders")
        self.assertEqual(resp.status_code, 403)

    def test_create_relationship_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        rel = {"name": "r", "from": "orders", "to": "orders", "from_columns": ["order_id"], "to_columns": ["order_id"]}
        resp = client.post("/semantic-models/official_model/relationships", json=rel)
        self.assertEqual(resp.status_code, 403)

    def test_create_metric_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        metric = {"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(order_id)"}]}}
        resp = client.post("/semantic-models/official_model/metrics", json=metric)
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Add guard to all sub-entity write methods**

Replace `self._require_model_row(model_name)` with `self._require_private_model(model_name)` in:
- `create_dataset`, `update_dataset`, `delete_dataset`
- `create_relationship`, `update_relationship`, `delete_relationship`
- `create_metric`, `update_metric`, `delete_metric`

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add app/semantic_service_v2/service.py tests/test_semantic_v2_api.py
git commit -m "feat: add visibility guard on sub-entity CRUD writes"
```

---

### Task 5: Same-name validation for private model creation

**Files:**
- Modify: `app/semantic_service_v2/service.py`
- Modify: `tests/test_semantic_v2_api.py`

Same-name rules: within same visibility, no duplicates (private per owner_user). Between visibilities, same name allowed.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_semantic_v2_api.py`:

```python
class TestSameNameValidation(unittest.TestCase):
    def test_duplicate_private_name_same_owner_returns_409(self) -> None:
        """Two private models with same name for same owner is not allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 409)

    def test_duplicate_private_name_different_owner_succeeds(self) -> None:
        """Two private models with same name for different owners is allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="bob"),
        )
        self.assertEqual(resp.status_code, 200)

    def test_private_same_name_as_official_succeeds(self) -> None:
        """Private model with same name as official model is allowed."""
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Add same-name check in `create_semantic_model`**

Add after the visibility=private check:

```python
        # Reject private model name that conflicts with another private model for same owner
        if enriched_pre.get("visibility") == "private" and enriched_pre.get("owner_user"):
            private_conflict = self.store.query_one(
                "SELECT 1 FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ? LIMIT 1",
                [model_data.get("name"), enriched_pre.get("owner_user")],
            )
            if private_conflict:
                raise HTTPException(
                    status_code=409,
                    detail=f"Private model '{model_data.get('name')}' already exists for user '{enriched_pre.get('owner_user')}'",
                )
```

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add app/semantic_service_v2/service.py tests/test_semantic_v2_api.py
git commit -m "feat: reject private model creation when same-name private model exists for same owner"
```

---

### Task 6: Pass `requesting_user` to sub-entity read routes

**Files:**
- Modify: `app/api/semantic_v2.py`
- Modify: `app/semantic_service_v2/service.py`
- Modify: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_semantic_v2_api.py`:

```python
class TestSubEntityReadVisibility(unittest.TestCase):
    def test_get_dataset_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.get("/semantic-models/priv_model/datasets/orders", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        resp = client.get("/semantic-models/priv_model/datasets/orders", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)

    def test_list_metrics_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.get("/semantic-models/priv_model/metrics", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 2: Add `_require_visible_model` helper**

In `app/semantic_service_v2/service.py`:

```python
    def _require_visible_model(
        self, name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        """Look up a model and raise 404 if not visible to requesting_user."""
        row = self._require_model_row(name)
        if row["visibility"] == "private" and (
            requesting_user is None or requesting_user != row["owner_user"]
        ):
            raise HTTPException(status_code=404, detail=f"Semantic model '{name}' not found")
        return row
```

Replace `self._require_model_row(model_name)` with `self._require_visible_model(model_name, requesting_user)` in all sub-entity **read** methods:
- `get_dataset`, `list_datasets`
- `get_relationship`, `list_relationships`
- `get_metric`, `list_metrics`

Add `requesting_user: str | None = None` to each method signature.

- [ ] **Step 3: Add `requesting_user` to sub-entity read routes in `app/api/semantic_v2.py`**

Add `requesting_user: str | None = None` parameter to each sub-entity GET route and pass through.

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

```bash
git add app/api/semantic_v2.py app/semantic_service_v2/service.py tests/test_semantic_v2_api.py
git commit -m "feat: add requesting_user visibility check to sub-entity read routes"
```

---

### Task 7: Add session snapshot service and API

**Files:**
- Create: `app/semantic_service_v2/session.py`
- Create: `app/api/session.py`
- Modify: `app/app_factory.py`
- Create: `tests/test_session_api.py`

- [ ] **Step 1: Create `SessionService`**

Create `app/semantic_service_v2/session.py`:

```python
"""SessionService — analysis session and semantic snapshot management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from app.storage.sqlite_metadata import SQLiteMetadataStore


class SessionService:
    """Manage analysis sessions and their frozen semantic snapshots."""

    def __init__(self, store: SQLiteMetadataStore) -> None:
        self.store = store

    def create_session(self, requesting_user: str) -> dict[str, Any]:
        """Create an analysis session and freeze the current semantic snapshot."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        self.store.execute(
            """
            INSERT INTO analysis_sessions (session_id, requesting_user)
            VALUES (?, ?)
            """,
            [session_id, requesting_user],
        )

        # Snapshot official models
        official_models = self.store.query_rows(
            "SELECT name, revision, visibility, owner_user FROM semantic_models WHERE visibility = 'public'"
        )
        for model in official_models:
            self.store.execute(
                """
                INSERT INTO session_semantic_snapshots
                    (session_id, model_name, revision, visibility, owner_user)
                VALUES (?, ?, ?, ?, ?)
                """,
                [session_id, model["name"], model["revision"], model["visibility"], model["owner_user"]],
            )

        # Snapshot private models owned by requesting_user
        if requesting_user:
            private_models = self.store.query_rows(
                "SELECT name, revision, visibility, owner_user FROM semantic_models WHERE visibility = 'private' AND owner_user = ?",
                [requesting_user],
            )
            for model in private_models:
                self.store.execute(
                    """
                    INSERT INTO session_semantic_snapshots
                        (session_id, model_name, revision, visibility, owner_user)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [session_id, model["name"], model["revision"], model["visibility"], model["owner_user"]],
                )

        return {
            "session_id": session_id,
            "requesting_user": requesting_user,
            "status": "active",
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details including resolved models from snapshot."""
        session_row = self.store.query_one(
            "SELECT * FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

        snapshot_rows = self.store.query_rows(
            "SELECT * FROM session_semantic_snapshots WHERE session_id = ?", [session_id]
        )

        resolved_objects = [
            {
                "model_name": r["model_name"],
                "revision": r["revision"],
                "visibility": r["visibility"],
                "owner_user": r["owner_user"],
            }
            for r in snapshot_rows
        ]

        return {
            "session_id": session_row["session_id"],
            "requesting_user": session_row["requesting_user"],
            "snapshot_frozen_at": session_row["snapshot_frozen_at"],
            "status": session_row["status"],
            "resolved_objects": resolved_objects,
        }

    def end_session(self, session_id: str) -> dict[str, Any]:
        """End an active session."""
        session_row = self.store.query_one(
            "SELECT * FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
        if session_row["status"] == "ended":
            raise HTTPException(status_code=400, detail=f"Session '{session_id}' is already ended")

        self.store.execute(
            "UPDATE analysis_sessions SET status = 'ended', ended_at = datetime('now') WHERE session_id = ?",
            [session_id],
        )
        return {"session_id": session_id, "status": "ended"}

    def add_model_to_snapshot(
        self,
        session_id: str,
        model_name: str,
        revision: int,
        visibility: str,
        owner_user: str | None = None,
    ) -> None:
        """Add a newly created private model to the active session's snapshot."""
        session_row = self.store.query_one(
            "SELECT status FROM analysis_sessions WHERE session_id = ?", [session_id]
        )
        if session_row is None or session_row["status"] != "active":
            return
        self.store.execute(
            """
            INSERT INTO session_semantic_snapshots
                (session_id, model_name, revision, visibility, owner_user)
            VALUES (?, ?, ?, ?, ?)
            """,
            [session_id, model_name, revision, visibility, owner_user],
        )
```

- [ ] **Step 2: Create session API routes**

Create `app/api/session.py`:

```python
"""Session API — analysis session and semantic snapshot routes."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from app.semantic_service_v2.session import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _get_session_service(request: Request) -> SessionService:
    return cast("SessionService", request.app.state.session_service)


@router.post("")
def create_session(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    svc = _get_session_service(request)
    requesting_user = payload.get("requesting_user")
    if not requesting_user:
        raise HTTPException(status_code=400, detail="requesting_user is required")
    return svc.create_session(requesting_user)


@router.get("/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _get_session_service(request)
    return svc.get_session(session_id)


@router.post("/{session_id}/end")
def end_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _get_session_service(request)
    return svc.end_session(session_id)
```

- [ ] **Step 3: Wire into app factory**

In `app/app_factory.py`, add imports and wire after semantic_v2_service:

```python
from app.semantic_service_v2.session import SessionService
from app.api.session import router as session_router
```

```python
    session_service = SessionService(cast("SQLiteMetadataStore", metadata_store))
    app.state.session_service = session_service
    app.include_router(session_router)
```

- [ ] **Step 4: Create session tests**

Create `tests/test_session_api.py`:

```python
"""Tests for Session API endpoints."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models.osi import OSI_SPEC_VERSION
from app.api.session import router as session_router
from app.semantic_service_v2.service import SemanticModelV2Service
from app.semantic_service_v2.session import SessionService
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.test_semantic_v2_api import _TestMetadataStore


def _make_app() -> TestClient:
    import uuid
    tmp = tempfile.mkdtemp(prefix=f"marivo_session_{uuid.uuid4().hex[:8]}_")
    db_path = Path(tmp) / "meta.sqlite"
    store = _TestMetadataStore(db_path)
    store.initialize()
    semantic_service = SemanticModelV2Service(store)
    session_service = SessionService(store)

    app = FastAPI()
    app.include_router(session_router)
    app.state.semantic_v2_service = semantic_service
    app.state.session_service = session_service
    return TestClient(app)


class TestCreateSession(unittest.TestCase):
    def test_create_session_returns_session_id(self) -> None:
        client = _make_app()
        resp = client.post("/sessions", json={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("session_id", body)
        self.assertEqual(body["status"], "active")

    def test_create_session_snapshots_official_models(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "fields": [
                                {"name": "order_id", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}},
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc)
        resp = client.post("/sessions", json={"requesting_user": "alice"})
        session_id = resp.json()["session_id"]
        detail = client.get(f"/sessions/{session_id}").json()
        model_names = [o["model_name"] for o in detail["resolved_objects"]]
        self.assertIn("commerce", model_names)


class TestGetSession(unittest.TestCase):
    def test_get_session_returns_snapshot(self) -> None:
        client = _make_app()
        session_id = client.post("/sessions", json={"requesting_user": "alice"}).json()["session_id"]
        resp = client.get(f"/sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("resolved_objects", resp.json())

    def test_get_nonexistent_returns_404(self) -> None:
        client = _make_app()
        self.assertEqual(client.get("/sessions/nonexistent").status_code, 404)


class TestEndSession(unittest.TestCase):
    def test_end_session(self) -> None:
        client = _make_app()
        session_id = client.post("/sessions", json={"requesting_user": "alice"}).json()["session_id"]
        resp = client.post(f"/sessions/{session_id}/end")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ended")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_session_api.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add app/semantic_service_v2/session.py app/api/session.py app/app_factory.py tests/test_session_api.py
git commit -m "feat: add session management with per-model revision snapshots"
```

---

### Task 8: In-session snapshot refresh for private model creation

**Files:**
- Modify: `app/api/semantic_v2.py`
- Modify: `tests/test_session_api.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_session_api.py`:

```python
class TestSessionSnapshotRefresh(unittest.TestCase):
    def test_new_private_model_added_to_active_session(self) -> None:
        client = _make_app()
        session_id = client.post("/sessions", json={"requesting_user": "alice"}).json()["session_id"]
        from tests.test_semantic_v2_api import _make_model_dict
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="alice_explore", visibility="private", owner_user="alice"),
            params={"session_id": session_id},
        )
        resp = client.get(f"/sessions/{session_id}")
        model_names = [o["model_name"] for o in resp.json()["resolved_objects"]]
        self.assertIn("alice_explore", model_names)
```

- [ ] **Step 2: Add session_id param to create route**

In `app/api/semantic_v2.py`, modify `create_semantic_model`:

```python
@router.post("")
def create_semantic_model(
    request: Request, payload: dict[str, Any] = Body(...), session_id: str | None = None
) -> dict[str, Any]:
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(payload))
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

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

```bash
git add app/api/semantic_v2.py tests/test_session_api.py
git commit -m "feat: refresh session snapshot when private models are created"
```

---

## Self-Review

### Spec Coverage

| Spec Section | Task |
|-------------|------|
| Drop semantic_versions, per-model revision | Task 1 (DDL), Task 2 (service) |
| Import = per-model upsert, other models untouched | Task 2 |
| No history table (Git is the source) | Task 2 (no archive step) |
| List no longer filters by version | Task 2 |
| Visibility-gated CRUD writes (403 on official) | Tasks 3, 4 |
| Same-name validation (409) | Task 5 |
| requesting_user on sub-entity reads | Task 6 |
| Session snapshot with per-model revision | Task 7 |
| In-session snapshot refresh | Task 8 |

### Placeholder Scan

No TBD, TODO, or placeholder patterns found.

### Type Consistency

- `revision` is `int` everywhere (DDL, service, session, tests)
- `semantic_version_id` removed from all code paths
- All `requesting_user` params: `str | None = None`
