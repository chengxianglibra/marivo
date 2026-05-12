# Semantic Layer MCP Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement private working-copy semantic import/export, field-level CRUD, bounded datasource auto-binding, and HTTP/MCP semantic surface parity.

**Architecture:** Use the approved focused application-service extraction. Keep transport layers thin, and move import/export mechanics into focused runtime semantic helpers: datasource binding, merge planning, transactional merge execution, and OSI document export. This is a breaking target-state change: do not preserve the old public import behavior or migrate old public import data.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, FastMCP, SQLite metadata store, generated OSI models under `marivo/contracts/generated/`, repository entrypoints `make test`, `make typecheck`, `make lint`.

---

## Scope Boundaries

In scope:

- Enforce explicit transport-provided identity: stdio MCP requires `MARIVO_USER`; HTTP API and HTTP MCP require `X-Marivo-User`.
- Remove fallback/default-user behavior from HTTP/runtime-backed semantic paths. Missing or blank identity must fail closed before user-scoped service work.
- Replace current public-layer `/semantic-models/import` behavior with private working-copy `import_osi_document`.
- Make HTTP and MCP import return `ImportOsiDocumentReport`.
- Add private-only `export_osi_document`.
- Add field-level CRUD in service, HTTP, and MCP.
- Remove `requesting_user` from MCP semantic tool schemas; identity comes from context.
- Add bounded datasource auto-binding for import.
- Add structured import logs.
- Add focused tests for merge semantics, binding, export, field CRUD, HTTP/MCP schema parity, and transaction atomicity.

Out of scope:

- Official/public publish/admin workflow.
- Data migration or compatibility for old public import data.
- Authentication or authorization of `MARIVO_USER` / `X-Marivo-User`; these are trusted propagation values supplied by the agent or calling environment.
- Expression safety validation beyond existing OSI/schema/runtime readiness behavior.
- UI work.
- Runtime changes unrelated to semantic management.

## Existing State To Respect

- Current `SemanticModelV2Service.import_osi_document()` starts at `marivo/runtime/semantic/semantic_service.py:1022` and imports into public models by replacing children. This behavior must be replaced, not preserved.
- HTTP route `POST /semantic-models/import` currently returns `OSIDocument` from `marivo/transports/http/semantic_v2.py:110`. It must return an import report.
- MCP semantic tools live in `marivo/transports/mcp/tools/semantic.py` and currently expose `requesting_user` on read tools. Remove those MCP parameters.
- Stdio MCP entrypoint `marivo/transports/cli/cmd_mcp.py` currently sets `current_user` from `getpass.getuser()`. Replace that with required `MARIVO_USER`; do not add any local-user or workspace fallback.
- HTTP identity context comes from `X-Marivo-User` via `marivo/transports/http/middleware.py`. Missing or blank headers must fail closed for user-scoped API/MCP requests; do not synthesize a default user in middleware, runtime, or service code.
- Service/runtime code that needs a user-owned private working copy must use `require_user()` or an equivalent fail-closed helper. Do not use `resolve_user()` for owner assignment or write paths.
- Generated OSI models live in `marivo/contracts/generated/`.
- OSI/storage mapping lives in `marivo/runtime/semantic/osi_storage.py`. Reuse it.
- Existing datasource service is `marivo.datasources.DatasourceService`; do not create a second registry.
- There are unrelated unstaged runtime changes in the working tree. Do not stage or revert them unless the user explicitly asks.

## Target File Structure

Create:

- `marivo/runtime/semantic/import_export.py`
  Owns `ImportOsiDocumentReport`, `DatasourceBinder`, `SemanticMergePlanner`, `SemanticMergeExecutor`, `OsiDocumentExporter`, and small support dataclasses. It may call storage mapping helpers from `osi_storage.py`, but it must not import HTTP or MCP modules.

- `tests/runtime/semantic/test_import_export.py`
  Focused service/helper tests for import report shape, duplicate validation, empty document validation, merge semantics, datasource binding, export, and atomic rollback.

Modify:

- `marivo/transports/cli/cmd_mcp.py`
  Read `MARIVO_USER`, trim it, reject missing/blank values, and set `current_user` only from that explicit agent-provided value.

- `marivo/transports/http/middleware.py`
  Keep `X-Marivo-User` as the trusted propagation header. Do not add env/default fallback. For API/MCP paths that require user scope, missing/blank values must be converted into the existing structured auth/domain error path instead of reaching runtime as an implicit user.

- `marivo/contracts/errors.py`
  Add semantic-specific error codes needed by the surface: `NOT_FOUND_SEMANTIC_MODEL`, `NOT_FOUND_DATASET`, `NOT_FOUND_FIELD`, `NOT_FOUND_METRIC`, `NOT_FOUND_RELATIONSHIP`, `DATASOURCE_BINDING_FAILED`, `DATASET_ACCESS_DENIED`.

- `marivo/transports/http/errors.py`
  Ensure new domain errors map to structured HTTP status codes.

- `marivo/runtime/semantic/semantic_service.py`
  Replace the old public import body with calls into `import_export.py`; add `export_osi_document`; add field CRUD methods; keep existing create/get/list/update/delete model/dataset/metric/relationship behavior aligned with private working copy rules.

- `marivo/adapters/server/semantic_service_adapter.py`
  Expose `export_osi_document` and field CRUD through the adapter.

- `marivo/transports/http/semantic_v2.py`
  Add HTTP models for import report if not imported from runtime; update `/semantic-models/import` response model; add export route; add field CRUD routes.

- `marivo/transports/mcp/tools/schemas.py`
  Add MCP field payload/update payload types and import/export report schemas if useful for tool annotations.

- `marivo/transports/mcp/tools/semantic.py`
  Add `import_osi_document`, `export_osi_document`, field CRUD; remove `requesting_user` arguments from MCP read tools.

- `tests/test_semantic_v2_api.py`
  Rewrite old import tests that assert public/latest import behavior; add HTTP export and field CRUD route tests.

- `tests/transports/mcp/test_tool_parity.py`
  Update fake service and assertions for import/export/field tools and absence of `requesting_user`.

- `tests/transports/mcp/test_stdio_mcp_e2e.py`
  Update expected tool inventory and add stdio identity tests: missing `MARIVO_USER` fails closed; explicit `MARIVO_USER=alice` is propagated to runtime.

- `tests/transports/mcp/test_http_mcp_e2e.py`
  Add HTTP MCP identity tests: missing or blank `X-Marivo-User` fails closed; explicit header is propagated.

- `tests/test_middleware.py`
  Update middleware expectations so absence/blankness remains visible as missing identity and no default user is produced.

Verification commands:

- `make test`
- `make typecheck`
- `make lint`

Use only repository entrypoints or explicit `.venv/bin/...`; never use bare `python`, `pytest`, `mypy`, or `ruff`.

---

### Task 0: Enforce Explicit Transport Identity

**Files:**
- Modify: `marivo/transports/cli/cmd_mcp.py`
- Modify: `marivo/transports/http/middleware.py`
- Test: `tests/test_middleware.py`
- Test: `tests/transports/mcp/test_stdio_mcp_e2e.py`
- Test: `tests/transports/mcp/test_http_mcp_e2e.py`

- [ ] **Step 1: Add stdio identity tests**

In `tests/transports/mcp/test_stdio_mcp_e2e.py`, add tests around the stdio startup helper or command handler. If the current test file shells out to the console script, use that harness; otherwise test the smallest extracted helper from Step 3:

```python
import os

import pytest

from marivo.transports.cli.cmd_mcp import _require_stdio_user


def test_stdio_mcp_requires_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARIVO_USER", raising=False)

    with pytest.raises(RuntimeError, match="MARIVO_USER"):
        _require_stdio_user()


def test_stdio_mcp_rejects_blank_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIVO_USER", "   ")

    with pytest.raises(RuntimeError, match="MARIVO_USER"):
        _require_stdio_user()


def test_stdio_mcp_uses_explicit_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARIVO_USER", "  alice  ")

    assert _require_stdio_user() == "alice"
```

- [ ] **Step 2: Add HTTP/MCP missing identity tests**

In `tests/transports/mcp/test_http_mcp_e2e.py`, add one request without `X-Marivo-User` and one request with a blank header against any existing user-scoped MCP tool. Assert both fail closed with a non-2xx response or structured error body, and add one positive case with `headers={"X-Marivo-User": "alice"}` that reaches the runtime:

```python
async def test_http_mcp_requires_x_marivo_user(http_mcp_client) -> None:
    response = await http_mcp_client.post_tool(
        "list_semantic_models",
        {},
        headers={},
    )

    assert response.status_code in {401, 403, 500}
    assert "user" in response.text.lower()


async def test_http_mcp_rejects_blank_x_marivo_user(http_mcp_client) -> None:
    response = await http_mcp_client.post_tool(
        "list_semantic_models",
        {},
        headers={"X-Marivo-User": "   "},
    )

    assert response.status_code in {401, 403, 500}
    assert "user" in response.text.lower()
```

If the existing HTTP MCP harness exposes FastMCP JSON-RPC errors rather than raw status codes, assert the JSON-RPC error contains the same user-required message. Do not change the product behavior to pass the exact status above; use the repository's existing structured error mapping.

- [ ] **Step 3: Replace stdio default-user fallback**

In `marivo/transports/cli/cmd_mcp.py`, remove `getpass.getuser()` usage and add:

```python
def _require_stdio_user() -> str:
    user = os.environ.get("MARIVO_USER", "").strip()
    if not user:
        raise RuntimeError("MARIVO_USER is required for marivo stdio MCP")
    return user
```

Then in `handle()`:

```python
    set_current_user(_require_stdio_user())
```

There must be no fallback to local username, workspace identity, anonymous identity, or `MARIVO_DEFAULT_USER`.

- [ ] **Step 4: Keep HTTP identity header-only and fail closed**

In `marivo/transports/http/middleware.py`, keep `X-Marivo-User` as the only HTTP identity source. Do not read env vars. Ensure blank headers are normalized to missing identity and that downstream user-scoped routes hit `require_user()` before owner-scoped service work.

If current HTTP error handling turns missing `require_user()` into a 500, keep that behavior only if it is already the repository's accepted structured error path. Otherwise map the identity failure to `FORBIDDEN` consistently in `marivo/transports/http/errors.py`.

- [ ] **Step 5: Run identity tests**

Run:

```bash
.venv/bin/pytest tests/test_middleware.py tests/transports/mcp/test_stdio_mcp_e2e.py tests/transports/mcp/test_http_mcp_e2e.py -q
```

Expected: tests pass, and no code path sets `current_user` from a default local user.

- [ ] **Step 6: Commit**

```bash
git add marivo/transports/cli/cmd_mcp.py marivo/transports/http/middleware.py tests/test_middleware.py tests/transports/mcp/test_stdio_mcp_e2e.py tests/transports/mcp/test_http_mcp_e2e.py
git commit -m "fix: require explicit user identity for mcp transports"
```

---

### Task 1: Define Semantic Import/Export Contracts And Error Codes

**Files:**
- Create: `marivo/runtime/semantic/import_export.py`
- Modify: `marivo/contracts/errors.py`
- Modify: `marivo/transports/http/errors.py`
- Test: `tests/runtime/semantic/test_import_export.py`

- [ ] **Step 1: Create failing contract tests**

Create `tests/runtime/semantic/test_import_export.py` with these initial tests:

```python
from __future__ import annotations

import unittest

from marivo.contracts.errors import ErrorCode, ValidationError as DomainValidationError
from marivo.runtime.semantic.import_export import (
    ImportOsiDocumentReport,
    SemanticMergePlanner,
)


class ImportExportContractTests(unittest.TestCase):
    def test_import_report_model_counts_and_bindings(self) -> None:
        report = ImportOsiDocumentReport(
            models=[
                {
                    "name": "sales",
                    "created": True,
                    "updated": False,
                    "datasets": {"created": 1, "updated": 0, "unchanged": 0},
                    "fields": {"created": 2, "updated": 0, "unchanged": 0},
                    "metrics": {"created": 0, "updated": 0, "unchanged": 0},
                    "relationships": {"created": 0, "updated": 0, "unchanged": 0},
                    "datasource_bindings": [
                        {
                            "dataset": "orders",
                            "datasource_id": "ds_001",
                            "selection": "first_accessible_candidate",
                        }
                    ],
                }
            ],
            errors=[],
        )

        dumped = report.model_dump()
        self.assertEqual(dumped["models"][0]["name"], "sales")
        self.assertEqual(dumped["models"][0]["fields"]["created"], 2)
        self.assertEqual(
            dumped["models"][0]["datasource_bindings"][0]["selection"],
            "first_accessible_candidate",
        )

    def test_empty_document_is_validation_error(self) -> None:
        planner = SemanticMergePlanner()

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight({"version": "0.1.1", "semantic_model": []})

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("semantic_model", raised.exception.message)

    def test_duplicate_dataset_names_are_validation_error(self) -> None:
        planner = SemanticMergePlanner()
        doc = {
            "version": "0.1.1",
            "semantic_model": [
                {
                    "name": "sales",
                    "datasets": [
                        {"name": "orders", "source": "analytics.orders"},
                        {"name": "orders", "source": "analytics.orders_v2"},
                    ],
                }
            ],
        }

        with self.assertRaises(DomainValidationError) as raised:
            planner.preflight(doc)

        self.assertEqual(raised.exception.code, ErrorCode.VALIDATION)
        self.assertIn("duplicate dataset name", raised.exception.message)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'marivo.runtime.semantic.import_export'
```

- [ ] **Step 3: Add error codes**

In `marivo/contracts/errors.py`, extend `ErrorCode`:

```python
    # Semantic model
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_REVISION_CONFLICT = "model_revision_conflict"
    NOT_FOUND_SEMANTIC_MODEL = "not_found_semantic_model"
    NOT_FOUND_DATASET = "not_found_dataset"
    NOT_FOUND_FIELD = "not_found_field"
    NOT_FOUND_METRIC = "not_found_metric"
    NOT_FOUND_RELATIONSHIP = "not_found_relationship"
    DATASOURCE_BINDING_FAILED = "datasource_binding_failed"
    DATASET_ACCESS_DENIED = "dataset_access_denied"
```

- [ ] **Step 4: Create the import/export helper module with report models and preflight validation**

Create `marivo/runtime/semantic/import_export.py`:

```python
"""Import/export helpers for OSI semantic documents.

This module owns private working-copy import/export mechanics. Transport
layers call the semantic service facade; they do not implement merge or
binding behavior themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from marivo.contracts.errors import ErrorCode
from marivo.contracts.errors import ValidationError as DomainValidationError


class ImportCounter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int = 0
    updated: int = 0
    unchanged: int = 0


class DatasourceBindingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    datasource_id: str
    selection: str = "first_accessible_candidate"


class ImportModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    created: bool
    updated: bool
    datasets: ImportCounter = Field(default_factory=ImportCounter)
    fields: ImportCounter = Field(default_factory=ImportCounter)
    metrics: ImportCounter = Field(default_factory=ImportCounter)
    relationships: ImportCounter = Field(default_factory=ImportCounter)
    datasource_bindings: list[DatasourceBindingReport] = Field(default_factory=list)


class ImportOsiDocumentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ImportModelReport]
    errors: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(frozen=True)
class DatasetBinding:
    model_name: str
    dataset_name: str
    datasource_id: str
    selection: str = "first_accessible_candidate"


@dataclass(frozen=True)
class SemanticMergePlan:
    document: dict[str, Any]
    bindings: list[DatasetBinding] = field(default_factory=list)


class SemanticMergePlanner:
    """Validate import-level invariants and produce a merge plan."""

    def preflight(self, doc_data: dict[str, Any]) -> SemanticMergePlan:
        models = doc_data.get("semantic_model")
        if not isinstance(models, list) or not models:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message="semantic_model must contain at least one model for import",
                detail={"path": "semantic_model"},
            )
        self._reject_duplicates("semantic model name", [m.get("name") for m in models])
        for model in models:
            model_name = str(model.get("name") or "<unnamed>")
            datasets = model.get("datasets") or []
            self._reject_duplicates(
                "dataset name",
                [dataset.get("name") for dataset in datasets],
                scope=f"semantic_model[{model_name}].datasets",
            )
            for dataset in datasets:
                dataset_name = str(dataset.get("name") or "<unnamed>")
                self._reject_duplicates(
                    "field name",
                    [field.get("name") for field in dataset.get("fields") or []],
                    scope=f"semantic_model[{model_name}].dataset[{dataset_name}].fields",
                )
            self._reject_duplicates(
                "metric name",
                [metric.get("name") for metric in model.get("metrics") or []],
                scope=f"semantic_model[{model_name}].metrics",
            )
            self._reject_duplicates(
                "relationship name",
                [rel.get("name") for rel in model.get("relationships") or []],
                scope=f"semantic_model[{model_name}].relationships",
            )
        return SemanticMergePlan(document=doc_data)

    @staticmethod
    def _reject_duplicates(
        label: str,
        names: list[Any],
        *,
        scope: str = "semantic_model",
    ) -> None:
        seen: set[str] = set()
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name:
                continue
            if name in seen:
                raise DomainValidationError(
                    code=ErrorCode.VALIDATION,
                    message=f"duplicate {label}: {name}",
                    detail={"path": scope, "name": name},
                )
            seen.add(name)
```

- [ ] **Step 5: Map new errors in HTTP error handling**

In `marivo/transports/http/errors.py`, find the domain error mapping function. If it already maps all `DomainError` by class, add tests only. If it switches on `ErrorCode`, add:

```python
    ErrorCode.NOT_FOUND_SEMANTIC_MODEL: 404,
    ErrorCode.NOT_FOUND_DATASET: 404,
    ErrorCode.NOT_FOUND_FIELD: 404,
    ErrorCode.NOT_FOUND_METRIC: 404,
    ErrorCode.NOT_FOUND_RELATIONSHIP: 404,
    ErrorCode.DATASOURCE_BINDING_FAILED: 422,
    ErrorCode.DATASET_ACCESS_DENIED: 403,
```

Use the existing style in the file. Do not introduce a second error adapter.

- [ ] **Step 6: Run contract tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Commit**

```bash
git add marivo/contracts/errors.py marivo/transports/http/errors.py marivo/runtime/semantic/import_export.py tests/runtime/semantic/test_import_export.py
git commit -m "feat: add semantic import export contracts"
```

---

### Task 2: Add Bounded Datasource Binding

**Files:**
- Modify: `marivo/runtime/semantic/import_export.py`
- Test: `tests/runtime/semantic/test_import_export.py`

- [ ] **Step 1: Add failing datasource binding tests**

Append to `tests/runtime/semantic/test_import_export.py`:

```python
from marivo.contracts.errors import ErrorCode
from marivo.runtime.semantic.import_export import DatasourceBinder


class _FakeDatasourceService:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.browse_calls: list[tuple[str, str, str]] = []

    def list_datasources(self) -> list[dict[str, object]]:
        return list(self.rows)

    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict[str, object]]:
        self.browse_calls.append((datasource_id, schema_name, table_name))
        for row in self.rows:
            if row["datasource_id"] == datasource_id and row.get("has_table"):
                return [{"name": "id", "type": "integer"}]
        raise KeyError(table_name)


class DatasourceBinderTests(unittest.TestCase):
    def test_selects_first_accessible_candidate_by_stable_order(self) -> None:
        service = _FakeDatasourceService(
            [
                {"datasource_id": "ds_b", "name": "warehouse_b", "status": "active", "has_table": True},
                {"datasource_id": "ds_a", "name": "warehouse_a", "status": "active", "has_table": True},
            ]
        )
        binder = DatasourceBinder(service)

        binding = binder.bind_dataset(
            model_name="sales",
            dataset={"name": "orders", "source": "analytics.orders"},
        )

        self.assertEqual(binding.datasource_id, "ds_a")
        self.assertEqual(service.browse_calls, [("ds_a", "analytics", "orders")])

    def test_binding_failure_when_no_candidate_matches(self) -> None:
        service = _FakeDatasourceService(
            [{"datasource_id": "ds_a", "name": "warehouse_a", "status": "active", "has_table": False}]
        )
        binder = DatasourceBinder(service)

        with self.assertRaises(DomainValidationError) as raised:
            binder.bind_dataset(
                model_name="sales",
                dataset={"name": "orders", "source": "analytics.orders"},
            )

        self.assertEqual(raised.exception.code, ErrorCode.DATASOURCE_BINDING_FAILED)
        self.assertIn("orders", raised.exception.message)

    def test_binding_cache_avoids_repeated_catalog_checks(self) -> None:
        service = _FakeDatasourceService(
            [{"datasource_id": "ds_a", "name": "warehouse_a", "status": "active", "has_table": True}]
        )
        binder = DatasourceBinder(service)

        binder.bind_dataset(model_name="sales", dataset={"name": "orders", "source": "analytics.orders"})
        binder.bind_dataset(model_name="sales", dataset={"name": "orders", "source": "analytics.orders"})

        self.assertEqual(service.browse_calls, [("ds_a", "analytics", "orders")])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py::DatasourceBinderTests -q
```

Expected:

```text
ImportError: cannot import name 'DatasourceBinder'
```

- [ ] **Step 3: Implement `DatasourceBinder`**

Add to `marivo/runtime/semantic/import_export.py`:

```python
class DatasourceBinder:
    """Bind imported datasets to the first stable accessible datasource."""

    def __init__(self, datasource_service: Any | None) -> None:
        self.datasource_service = datasource_service
        self._catalog_cache: dict[tuple[str, str, str], bool] = {}

    def bind_dataset(self, *, model_name: str, dataset: dict[str, Any]) -> DatasetBinding:
        dataset_name = str(dataset.get("name") or "<unnamed>")
        source = str(dataset.get("source") or "").strip()
        parsed = self._parse_source(source)
        if parsed is None:
            raise DomainValidationError(
                code=ErrorCode.DATASOURCE_BINDING_FAILED,
                message=f"Dataset {dataset_name} source {source!r} is not a schema.table or catalog.schema.table FQN",
                detail={"model": model_name, "dataset": dataset_name, "source": source},
            )
        schema_name, table_name = parsed
        candidates = self._candidate_datasources()
        for candidate in candidates:
            datasource_id = str(candidate.get("datasource_id") or "")
            if not datasource_id:
                continue
            if self._has_table(datasource_id, schema_name, table_name):
                return DatasetBinding(
                    model_name=model_name,
                    dataset_name=dataset_name,
                    datasource_id=datasource_id,
                )
        raise DomainValidationError(
            code=ErrorCode.DATASOURCE_BINDING_FAILED,
            message=f"Dataset {dataset_name} source {source!r} could not be bound to an accessible datasource",
            detail={"model": model_name, "dataset": dataset_name, "source": source},
        )

    def _candidate_datasources(self) -> list[dict[str, Any]]:
        if self.datasource_service is None:
            return []
        rows = [dict(row) for row in self.datasource_service.list_datasources()]
        active = [row for row in rows if str(row.get("status") or "active") == "active"]
        return sorted(active, key=lambda row: (str(row.get("name") or ""), str(row.get("datasource_id") or "")))

    def _has_table(self, datasource_id: str, schema_name: str, table_name: str) -> bool:
        key = (datasource_id, schema_name, table_name)
        if key in self._catalog_cache:
            return self._catalog_cache[key]
        assert self.datasource_service is not None
        try:
            self.datasource_service.browse_catalog_columns(datasource_id, schema_name, table_name)
        except KeyError:
            self._catalog_cache[key] = False
            return False
        except ValueError as exc:
            raise DomainValidationError(
                code=ErrorCode.DATASET_ACCESS_DENIED,
                message=str(exc),
                detail={"datasource_id": datasource_id, "schema": schema_name, "table": table_name},
            ) from exc
        self._catalog_cache[key] = True
        return True

    @staticmethod
    def _parse_source(source: str) -> tuple[str, str] | None:
        parts = [part for part in source.split(".") if part]
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 3:
            return parts[1], parts[2]
        return None
```

- [ ] **Step 4: Run datasource binding tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py::DatasourceBinderTests -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Run all import/export helper tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/semantic/import_export.py tests/runtime/semantic/test_import_export.py
git commit -m "feat: add bounded datasource binding"
```

---

### Task 3: Implement Private Import Merge And Export In The Service

**Files:**
- Modify: `marivo/runtime/semantic/import_export.py`
- Modify: `marivo/runtime/semantic/semantic_service.py`
- Test: `tests/runtime/semantic/test_import_export.py`
- Test: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Add service-level import/export tests**

Append to `tests/runtime/semantic/test_import_export.py`:

```python
from marivo.adapters.server.semantic_service_adapter import SemanticServiceAdapter
from marivo.datasources import DatasourceService
from tests.shared_fixtures import make_temp_metadata_store


def _osi_doc(model_name: str = "sales_model") -> dict[str, object]:
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": model_name,
                "description": "initial",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "analytics.orders",
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]},
                            }
                        ],
                    }
                ],
            }
        ],
    }


class SemanticImportExportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = make_temp_metadata_store(prefix="semantic_import_export_")
        self.datasource_service = DatasourceService(self.store)
        self.datasource_service.register_datasource(
            name="warehouse_a",
            source_type="duckdb",
            connection={"database": ":memory:"},
        )
        self.service = SemanticServiceAdapter(self.store, datasource_service=self.datasource_service)

    def tearDown(self) -> None:
        self.store.close()

    def test_import_creates_private_working_copy_for_current_user(self) -> None:
        from marivo.identity import reset_current_user, set_current_user

        token = set_current_user("alice")  # Simulates transport/context provider injection.
        try:
            report = self.service.import_osi_document(_osi_doc())
        finally:
            reset_current_user(token)

        self.assertEqual(report["models"][0]["name"], "sales_model")
        self.assertEqual(report["models"][0]["created"], True)
        row = self.store.query_one(
            "SELECT visibility, owner_user FROM semantic_models WHERE name = ?",
            ["sales_model"],
        )
        self.assertEqual(row["visibility"], "private")
        self.assertEqual(row["owner_user"], "alice")

    def test_export_without_name_returns_only_current_user_private_models(self) -> None:
        from marivo.identity import reset_current_user, set_current_user

        token = set_current_user("alice")  # Simulates transport/context provider injection.
        try:
            self.service.import_osi_document(_osi_doc("alice_model"))
            exported = self.service.export_osi_document()
        finally:
            reset_current_user(token)

        self.assertEqual(exported["version"], "0.1.1")
        self.assertEqual([m["name"] for m in exported["semantic_model"]], ["alice_model"])

    def test_export_named_missing_private_returns_not_found(self) -> None:
        from marivo.identity import reset_current_user, set_current_user

        token = set_current_user("alice")  # Simulates transport/context provider injection.
        try:
            with self.assertRaises(Exception) as raised:
                self.service.export_osi_document("missing")
        finally:
            reset_current_user(token)

        self.assertIn("missing", str(raised.exception))

    def test_import_requires_transport_injected_user(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self.service.import_osi_document(_osi_doc("missing_identity"))

        self.assertIn("User identity not set", str(raised.exception))

    def test_export_requires_transport_injected_user(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self.service.export_osi_document()

        self.assertIn("User identity not set", str(raised.exception))
```

- [ ] **Step 2: Run service tests and verify failure**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py::SemanticImportExportServiceTests -q
```

Expected:

```text
FAIL ... current import creates public model or export_osi_document is missing
```

- [ ] **Step 3: Add merge executor and exporter helpers**

In `marivo/runtime/semantic/import_export.py`, add helpers that use the service facade's store and existing storage mapping. Use this exact implementation as the starting point:

```python
import logging

from pydantic import ValidationError as PydanticValidationError

from marivo.contracts.generated import OSIDocument, SemanticModel
from marivo.runtime.semantic.osi_storage import (
    dataset_to_storage,
    field_to_storage,
    metric_to_storage,
    model_to_storage,
    relationship_to_storage,
)

LOGGER = logging.getLogger(__name__)


class SemanticMergeExecutor:
    def __init__(self, store: Any) -> None:
        self.store = store

    def apply(self, *, plan: SemanticMergePlan, owner_user: str) -> ImportOsiDocumentReport:
        reports: list[ImportModelReport] = []
        LOGGER.info(
            "semantic_import_start",
            extra={"owner_user": owner_user, "model_count": len(plan.document["semantic_model"])},
        )
        try:
            for model_data in plan.document["semantic_model"]:
                reports.append(self._merge_model(model_data=model_data, owner_user=owner_user, bindings=plan.bindings))
        except Exception:
            LOGGER.exception("semantic_import_failure", extra={"owner_user": owner_user, "rollback": "required"})
            raise
        LOGGER.info("semantic_import_success", extra={"owner_user": owner_user, "model_count": len(reports)})
        return ImportOsiDocumentReport(models=reports, errors=[])

    def _merge_model(
        self,
        *,
        model_data: dict[str, Any],
        owner_user: str,
        bindings: list[DatasetBinding],
    ) -> ImportModelReport:
        model = SemanticModel.model_validate(model_data)
        existing = self.store.query_one(
            "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
            [model.name, owner_user],
        )
        created = existing is None
        if created:
            storage = model_to_storage(model, owner_user=owner_user, visibility="private")
            self.store.execute(
                """
                INSERT INTO semantic_models (name, description, ai_context, visibility, owner_user)
                VALUES (?, ?, ?, ?, ?)
                """,
                [storage["name"], storage["description"], storage["ai_context"], "private", owner_user],
            )
            model_row = self.store.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [model.name, owner_user],
            )
        else:
            model_row = existing
            storage = model_to_storage(model, owner_user=owner_user, visibility="private")
            self.store.execute(
                "UPDATE semantic_models SET description = ?, ai_context = ?, updated_at = datetime('now') WHERE model_id = ?",
                [storage["description"], storage["ai_context"], model_row["model_id"]],
            )
        assert model_row is not None
        model_id = model_row["model_id"]
        report = ImportModelReport(name=model.name, created=created, updated=not created)
        binding_by_dataset = {
            binding.dataset_name: binding
            for binding in bindings
            if binding.model_name == model.name
        }
        for dataset in model.datasets:
            binding = binding_by_dataset.get(dataset.name)
            self._merge_dataset(model_id=model_id, dataset=dataset, binding=binding, report=report)
        for relationship in model.relationships or []:
            self._replace_relationship(model_id=model_id, relationship=relationship, report=report)
        for metric in model.metrics or []:
            self._replace_metric(model_id=model_id, metric=metric, report=report)
        return report

    def _merge_dataset(self, *, model_id: int, dataset: Any, binding: DatasetBinding | None, report: ImportModelReport) -> None:
        existing = self.store.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_id, dataset.name],
        )
        ds_storage = dataset_to_storage(dataset, model_id)
        datasource_id = binding.datasource_id if binding is not None else ds_storage["datasource_id"]
        if existing is None:
            self.store.execute(
                """
                INSERT INTO semantic_datasets
                    (model_id, name, source, primary_key, unique_keys, description, ai_context, datasource_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    model_id,
                    ds_storage["name"],
                    ds_storage["source"],
                    ds_storage["primary_key"],
                    ds_storage["unique_keys"],
                    ds_storage["description"],
                    ds_storage["ai_context"],
                    datasource_id,
                ],
            )
            report.datasets.created += 1
            ds_row = self.store.query_one(
                "SELECT dataset_id FROM semantic_datasets WHERE model_id = ? AND name = ?",
                [model_id, dataset.name],
            )
        else:
            self.store.execute(
                """
                UPDATE semantic_datasets
                SET source = ?, primary_key = ?, unique_keys = ?, description = ?, ai_context = ?,
                    datasource_id = ?, updated_at = datetime('now')
                WHERE dataset_id = ?
                """,
                [
                    ds_storage["source"],
                    ds_storage["primary_key"],
                    ds_storage["unique_keys"],
                    ds_storage["description"],
                    ds_storage["ai_context"],
                    datasource_id,
                    existing["dataset_id"],
                ],
            )
            report.datasets.updated += 1
            ds_row = existing
        assert ds_row is not None
        if binding is not None:
            report.datasource_bindings.append(
                DatasourceBindingReport(
                    dataset=dataset.name,
                    datasource_id=binding.datasource_id,
                    selection=binding.selection,
                )
            )
        for position, field_model in enumerate(dataset.fields or []):
            self._replace_field(dataset_id=ds_row["dataset_id"], field_model=field_model, position=position, report=report)

    def _replace_field(self, *, dataset_id: int, field_model: Any, position: int, report: ImportModelReport) -> None:
        existing = self.store.query_one(
            "SELECT field_id FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [dataset_id, field_model.name],
        )
        storage = field_to_storage(field_model, dataset_id, position)
        if existing is not None:
            self.store.execute("DELETE FROM semantic_fields WHERE field_id = ?", [existing["field_id"]])
            report.fields.updated += 1
        else:
            report.fields.created += 1
        self.store.execute(
            """
            INSERT INTO semantic_fields
                (dataset_id, name, expression, is_time, is_dimension, label, description, ai_context, data_type, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                storage["dataset_id"],
                storage["name"],
                storage["expression"],
                storage["is_time"],
                storage["is_dimension"],
                storage["label"],
                storage["description"],
                storage["ai_context"],
                storage["data_type"],
                storage["position"],
            ],
        )

    def _replace_relationship(self, *, model_id: int, relationship: Any, report: ImportModelReport) -> None:
        existing = self.store.query_one(
            "SELECT relationship_id FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [model_id, relationship.name],
        )
        if existing is not None:
            self.store.execute("DELETE FROM semantic_relationships WHERE relationship_id = ?", [existing["relationship_id"]])
            report.relationships.updated += 1
        else:
            report.relationships.created += 1
        storage = relationship_to_storage(relationship, model_id)
        self.store.execute(
            """
            INSERT INTO semantic_relationships
                (model_id, name, from_dataset, to_dataset, from_columns, to_columns, ai_context, cardinality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                storage["model_id"],
                storage["name"],
                storage["from_dataset"],
                storage["to_dataset"],
                storage["from_columns"],
                storage["to_columns"],
                storage["ai_context"],
                storage["cardinality"],
            ],
        )

    def _replace_metric(self, *, model_id: int, metric: Any, report: ImportModelReport) -> None:
        existing = self.store.query_one(
            "SELECT metric_id FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [model_id, metric.name],
        )
        if existing is not None:
            self.store.execute("DELETE FROM semantic_metrics WHERE metric_id = ?", [existing["metric_id"]])
            report.metrics.updated += 1
        else:
            report.metrics.created += 1
        storage = metric_to_storage(metric, model_id)
        self.store.execute(
            """
            INSERT INTO semantic_metrics (model_id, name, expression, description, ai_context, additive_dimensions)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                storage["model_id"],
                storage["name"],
                storage["expression"],
                storage["description"],
                storage["ai_context"],
                storage["additive_dimensions"],
            ],
        )


class OsiDocumentExporter:
    def __init__(self, store: Any, assemble_model: Any) -> None:
        self.store = store
        self.assemble_model = assemble_model

    def export(self, *, owner_user: str, semantic_model_name: str | None = None) -> dict[str, Any]:
        if semantic_model_name is None:
            rows = self.store.query_rows(
                "SELECT * FROM semantic_models WHERE visibility = 'private' AND owner_user = ? ORDER BY name",
                [owner_user],
            )
        else:
            row = self.store.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND visibility = 'private' AND owner_user = ?",
                [semantic_model_name, owner_user],
            )
            if row is None:
                from marivo.contracts.errors import NotFoundError

                raise NotFoundError(
                    ErrorCode.NOT_FOUND_SEMANTIC_MODEL,
                    f"Semantic model '{semantic_model_name}' not found",
                )
            rows = [row]
        models = [self.assemble_model(dict(row)) for row in rows]
        return OSIDocument.model_validate({"version": "0.1.1", "semantic_model": models}).model_dump(
            by_alias=True, exclude_none=True
        )
```

- [ ] **Step 4: Replace service import/export methods**

In `marivo/runtime/semantic/semantic_service.py`:

1. Import the new helpers:

```python
from marivo.runtime.semantic.import_export import (
    DatasourceBinder,
    OsiDocumentExporter,
    SemanticMergeExecutor,
    SemanticMergePlanner,
)
```

2. Replace the body of `import_osi_document` with:

```python
    def import_osi_document(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        """Import an OSI document into the current user's private working copy."""
        owner_user = require_user()
        try:
            doc = OSIDocument.model_validate(doc_data)
        except PydanticValidationError as exc:
            raise DomainValidationError(
                code=ErrorCode.VALIDATION,
                message=str(exc),
                detail={"errors": exc.errors()},
            ) from exc
        doc_dict = doc.model_dump(by_alias=True, exclude_none=True)
        planner = SemanticMergePlanner()
        plan = planner.preflight(doc_dict)
        binder = DatasourceBinder(self.datasource_service)
        bindings = []
        for model in doc_dict["semantic_model"]:
            for dataset in model.get("datasets") or []:
                bindings.append(binder.bind_dataset(model_name=model["name"], dataset=dataset))
        plan = type(plan)(document=plan.document, bindings=bindings)
        report = SemanticMergeExecutor(self.store).apply(plan=plan, owner_user=owner_user)
        return report.model_dump()
```

3. Add `export_osi_document` near import:

```python
    def export_osi_document(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        """Export current user's private working copy as an OSI document."""
        owner_user = require_user()
        return OsiDocumentExporter(self.store, self._assemble_model).export(
            owner_user=owner_user,
            semantic_model_name=semantic_model_name,
        )
```

- [ ] **Step 5: Update adapter signature**

In `marivo/adapters/server/semantic_service_adapter.py`, ensure:

```python
    def import_osi_document(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        return _translate(lambda: self._service.import_osi_document(doc_data))

    def export_osi_document(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        return _translate(lambda: self._service.export_osi_document(semantic_model_name))
```

- [ ] **Step 6: Run service tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py::SemanticImportExportServiceTests -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Run broader semantic tests and fix expected public import failures**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py tests/runtime/semantic/test_import_export.py -q
```

Expected initially:

```text
FAIL tests/test_semantic_v2_api.py::TestImportOSIDocumentAPI::test_import_osi_document
```

Update old HTTP import tests in `tests/test_semantic_v2_api.py` to use `headers={"X-Marivo-User": "alice"}` and assert report shape instead of OSI document:

```python
        resp = client.post(
            "/semantic-models/import",
            json=doc,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["models"][0]["name"], "imported_model")
        self.assertTrue(body["models"][0]["created"])
```

Delete or rewrite tests that expect public import. Do not preserve public import compatibility.

- [ ] **Step 8: Commit**

```bash
git add marivo/runtime/semantic/import_export.py marivo/runtime/semantic/semantic_service.py marivo/adapters/server/semantic_service_adapter.py tests/runtime/semantic/test_import_export.py tests/test_semantic_v2_api.py
git commit -m "feat: import semantic documents into private working copies"
```

---

### Task 4: Add Field-Level CRUD In Service And HTTP API

**Files:**
- Modify: `marivo/runtime/semantic/semantic_service.py`
- Modify: `marivo/adapters/server/semantic_service_adapter.py`
- Modify: `marivo/transports/http/semantic_v2.py`
- Test: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Add failing HTTP field CRUD tests**

Append to `tests/test_semantic_v2_api.py`:

```python
class TestFieldCrudAPI(unittest.TestCase):
    def test_create_list_get_update_delete_field(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="field_model"),
            headers={"X-Marivo-User": "alice"},
        )
        payload = {
            "name": "status",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
            "label": "Order Status",
        }

        create_resp = client.post(
            "/semantic-models/field_model/datasets/orders/fields",
            json=payload,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(create_resp.json()["name"], "status")

        list_resp = client.get(
            "/semantic-models/field_model/datasets/orders/fields",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(list_resp.status_code, 200)
        self.assertIn("status", [field["name"] for field in list_resp.json()])

        get_resp = client.get(
            "/semantic-models/field_model/datasets/orders/fields/status",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["label"], "Order Status")

        update_resp = client.patch(
            "/semantic-models/field_model/datasets/orders/fields/status",
            json={"description": "Lifecycle status"},
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["description"], "Lifecycle status")

        delete_resp = client.delete(
            "/semantic-models/field_model/datasets/orders/fields/status",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(delete_resp.status_code, 204)
```

- [ ] **Step 2: Run the field CRUD test and verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py::TestFieldCrudAPI::test_create_list_get_update_delete_field -q
```

Expected:

```text
404 Not Found for /fields
```

- [ ] **Step 3: Add service field CRUD methods**

In `marivo/runtime/semantic/semantic_service.py`, add methods near dataset CRUD:

```python
    def list_fields(
        self, model_name: str, dataset_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        dataset = self.get_dataset(model_name, dataset_name, requesting_user=requesting_user)
        return list(dataset.get("fields") or [])

    def get_field(
        self, model_name: str, dataset_name: str, field_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        for field in self.list_fields(model_name, dataset_name, requesting_user=requesting_user):
            if field["name"] == field_name:
                return field
        raise NotFoundError(
            ErrorCode.NOT_FOUND_FIELD,
            f"Field '{field_name}' not found in dataset '{dataset_name}'",
        )

    def create_field(
        self,
        model_name: str,
        dataset_name: str,
        field_data: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        dataset_row = self.store.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_row["model_id"], dataset_name],
        )
        if dataset_row is None:
            raise NotFoundError(ErrorCode.NOT_FOUND_DATASET, f"Dataset '{dataset_name}' not found")
        field_model = Field.model_validate(field_data)
        existing = self.store.query_one(
            "SELECT 1 FROM semantic_fields WHERE dataset_id = ? AND name = ?",
            [dataset_row["dataset_id"], field_model.name],
        )
        if existing is not None:
            raise ConflictError(ErrorCode.CONFLICT, f"Field '{field_model.name}' already exists")
        position_row = self.store.query_one(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_position FROM semantic_fields WHERE dataset_id = ?",
            [dataset_row["dataset_id"]],
        )
        position = int(position_row["next_position"])
        storage = field_to_storage(field_model, dataset_row["dataset_id"], position)
        self.store.execute(
            """
            INSERT INTO semantic_fields
                (dataset_id, name, expression, is_time, is_dimension, label, description, ai_context, data_type, position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                storage["dataset_id"],
                storage["name"],
                storage["expression"],
                storage["is_time"],
                storage["is_dimension"],
                storage["label"],
                storage["description"],
                storage["ai_context"],
                storage["data_type"],
                storage["position"],
            ],
        )
        return self.get_field(model_name, dataset_name, field_model.name, requesting_user=owner_user)

    def update_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_field(model_name, dataset_name, field_name, requesting_user=owner_user)
        patched = {**current, **updates, "name": field_name}
        self.delete_field(model_name, dataset_name, field_name, owner_user=owner_user)
        return self.create_field(model_name, dataset_name, patched, owner_user=owner_user)

    def delete_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        owner_user: str | None = None,
    ) -> None:
        model_row = self._require_private_model(model_name, owner_user=owner_user)
        row = self.store.query_one(
            """
            SELECT f.field_id
            FROM semantic_fields f
            JOIN semantic_datasets d ON f.dataset_id = d.dataset_id
            WHERE d.model_id = ? AND d.name = ? AND f.name = ?
            """,
            [model_row["model_id"], dataset_name, field_name],
        )
        if row is None:
            raise NotFoundError(
                ErrorCode.NOT_FOUND_FIELD,
                f"Field '{field_name}' not found in dataset '{dataset_name}'",
            )
        self.store.execute("DELETE FROM semantic_fields WHERE field_id = ?", [row["field_id"]])
```

Also add `Field` to the existing generated model imports.

- [ ] **Step 4: Add adapter methods**

In `marivo/adapters/server/semantic_service_adapter.py`, add:

```python
    def create_field(
        self, model_name: str, dataset_name: str, field_data: dict[str, Any], owner_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.create_field(model_name, dataset_name, field_data, owner_user=owner_user)
        )

    def list_fields(
        self, model_name: str, dataset_name: str, requesting_user: str | None = None
    ) -> list[dict[str, Any]]:
        return _translate(
            lambda: self._service.list_fields(model_name, dataset_name, requesting_user=requesting_user)
        )

    def get_field(
        self, model_name: str, dataset_name: str, field_name: str, requesting_user: str | None = None
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.get_field(model_name, dataset_name, field_name, requesting_user=requesting_user)
        )

    def update_field(
        self,
        model_name: str,
        dataset_name: str,
        field_name: str,
        updates: dict[str, Any],
        owner_user: str | None = None,
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.update_field(
                model_name, dataset_name, field_name, updates, owner_user=owner_user
            )
        )

    def delete_field(
        self, model_name: str, dataset_name: str, field_name: str, owner_user: str | None = None
    ) -> None:
        return _translate(
            lambda: self._service.delete_field(model_name, dataset_name, field_name, owner_user=owner_user)
        )
```

- [ ] **Step 5: Add HTTP field routes**

In `marivo/transports/http/semantic_v2.py`, import generated `Field`:

```python
    Field as OsiField,
```

Add:

```python
class FieldUpdateRequest(BaseModel):
    expression: dict[str, Any] | None = None
    dimension: dict[str, Any] | None = None
    label: str | None = None
    description: str | None = None
    ai_context: Any | None = None

    model_config = ConfigDict(extra="forbid")
```

Add routes after dataset routes:

```python
@router.post("/{model}/datasets/{dataset}/fields", response_model=OsiField)
def create_field(
    model: str,
    dataset: str,
    request: Request,
    payload: OsiField,
    requesting_user: str | None = None,
) -> OsiField:
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    return OsiField.model_validate(
        _run(lambda: svc.create_field(model, dataset, _dump_model(payload), owner_user=owner))
    )


@router.get("/{model}/datasets/{dataset}/fields", response_model=list[OsiField])
def list_fields(
    model: str, dataset: str, request: Request, requesting_user: str | None = None
) -> list[OsiField]:
    svc = _get_service(request)
    return [
        OsiField.model_validate(item)
        for item in svc.list_fields(model, dataset, requesting_user=_resolve_requesting_user(requesting_user))
    ]


@router.get("/{model}/datasets/{dataset}/fields/{name}", response_model=OsiField)
def get_field(
    model: str, dataset: str, name: str, request: Request, requesting_user: str | None = None
) -> OsiField:
    svc = _get_service(request)
    return OsiField.model_validate(
        _run(lambda: svc.get_field(model, dataset, name, requesting_user=_resolve_requesting_user(requesting_user)))
    )


@router.patch("/{model}/datasets/{dataset}/fields/{name}", response_model=OsiField)
def update_field(
    model: str,
    dataset: str,
    name: str,
    request: Request,
    payload: FieldUpdateRequest,
    requesting_user: str | None = None,
) -> OsiField:
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    updates = payload.model_dump(exclude_unset=True)
    return OsiField.model_validate(
        _run(lambda: svc.update_field(model, dataset, name, updates, owner_user=owner))
    )


@router.delete("/{model}/datasets/{dataset}/fields/{name}", status_code=204)
def delete_field(
    model: str,
    dataset: str,
    name: str,
    request: Request,
    requesting_user: str | None = None,
) -> None:
    svc = _get_service(request)
    owner = _resolve_requesting_user(requesting_user)
    _run(lambda: svc.delete_field(model, dataset, name, owner_user=owner))
```

- [ ] **Step 6: Run field CRUD test**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py::TestFieldCrudAPI::test_create_list_get_update_delete_field -q
```

Expected:

```text
1 passed
```

- [ ] **Step 7: Commit**

```bash
git add marivo/runtime/semantic/semantic_service.py marivo/adapters/server/semantic_service_adapter.py marivo/transports/http/semantic_v2.py tests/test_semantic_v2_api.py
git commit -m "feat: add semantic field CRUD"
```

---

### Task 5: Update HTTP Import Response And Add Export Route

**Files:**
- Modify: `marivo/transports/http/semantic_v2.py`
- Modify: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Add failing HTTP import/export response tests**

Append to `tests/test_semantic_v2_api.py`:

```python
class TestImportExportTargetStateAPI(unittest.TestCase):
    def test_import_returns_report_not_osi_document(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models/import",
            json={
                "version": OSI_SPEC_VERSION,
                "semantic_model": [_make_model_dict(name="report_model")],
            },
            headers={"X-Marivo-User": "alice"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("models", body)
        self.assertNotIn("semantic_model", body)
        self.assertEqual(body["models"][0]["name"], "report_model")

    def test_export_returns_private_osi_document(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models/import",
            json={"version": OSI_SPEC_VERSION, "semantic_model": [_make_model_dict(name="export_model")]},
            headers={"X-Marivo-User": "alice"},
        )

        resp = client.get("/semantic-models/export", headers={"X-Marivo-User": "alice"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual([model["name"] for model in body["semantic_model"]], ["export_model"])
```

- [ ] **Step 2: Run tests and verify export route fails**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py::TestImportExportTargetStateAPI -q
```

Expected:

```text
FAIL ... /semantic-models/export returns 404
```

- [ ] **Step 3: Add HTTP import report models and export route**

In `marivo/transports/http/semantic_v2.py`, import the report model:

```python
from marivo.runtime.semantic.import_export import ImportOsiDocumentReport
```

Change import route:

```python
@router.post("/import", response_model=ImportOsiDocumentReport)
def import_osi_document(request: Request, payload: OSIDocument) -> ImportOsiDocumentReport:
    """Import an OSI document into the current user's private working copy."""
    svc = _get_service(request)
    result = _run(lambda: svc.import_osi_document(_dump_model(payload)))
    return ImportOsiDocumentReport.model_validate(result)
```

Add export route before `/{model}` routes so it is not captured as a model name:

```python
@router.get("/export", response_model=OSIDocument)
def export_osi_document(
    request: Request,
    semantic_model_name: str | None = None,
) -> OSIDocument:
    """Export current user's private working copy as an OSI document."""
    svc = _get_service(request)
    result = _run(lambda: svc.export_osi_document(semantic_model_name))
    return OSIDocument.model_validate(result)
```

- [ ] **Step 4: Run target-state HTTP tests**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py::TestImportExportTargetStateAPI -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Run all semantic API tests**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py -q
```

Expected: all tests pass after rewriting old public import expectations.

- [ ] **Step 6: Commit**

```bash
git add marivo/transports/http/semantic_v2.py tests/test_semantic_v2_api.py
git commit -m "feat: expose semantic import report and export api"
```

---

### Task 6: Update MCP Semantic Tools And Schemas

**Files:**
- Modify: `marivo/transports/mcp/tools/schemas.py`
- Modify: `marivo/transports/mcp/tools/semantic.py`
- Modify: `tests/transports/mcp/test_tool_parity.py`
- Modify: `tests/transports/mcp/test_stdio_mcp_e2e.py`

- [ ] **Step 1: Add failing MCP inventory/schema tests**

In `tests/transports/mcp/test_tool_parity.py`, update `_FakeSvc` with methods:

```python
    def export_osi_document(self, **kw):
        return {}

    def create_field(self, **kw):
        return {}

    def list_fields(self, **kw):
        return {}

    def get_field(self, **kw):
        return {}

    def update_field(self, **kw):
        return {}

    def delete_field(self, **kw):
        return {}
```

Add tests near the existing parity assertions:

```python
def test_semantic_tools_include_import_export_and_field_crud() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert "import_osi_document" in tool_names
    assert "export_osi_document" in tool_names
    assert "create_field" in tool_names
    assert "list_fields" in tool_names
    assert "get_field" in tool_names
    assert "update_field" in tool_names
    assert "delete_field" in tool_names


def test_mcp_semantic_read_tools_do_not_expose_requesting_user() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    for name in ["list_semantic_models", "get_semantic_model", "list_datasets", "get_dataset"]:
        schema = tools[name].parameters
        assert "requesting_user" not in schema.get("properties", {})
```

- [ ] **Step 2: Run MCP parity tests and verify failure**

Run:

```bash
.venv/bin/pytest tests/transports/mcp/test_tool_parity.py -q
```

Expected:

```text
FAIL ... missing export_osi_document or field tools
```

- [ ] **Step 3: Add MCP field payload schemas**

In `marivo/transports/mcp/tools/schemas.py`, import generated `Field` as `OsiField` if needed:

```python
from marivo.contracts.generated.osi import FieldModel as OsiField
```

If the generated class is named `FieldModel` in `marivo/contracts/generated/osi.py`, use that exact name. Add:

```python
class McpFieldUpdatePayload(BaseModel):
    """Patch payload for update_field."""

    model_config = ConfigDict(extra="forbid")

    expression: Expression | None = None
    dimension: dict[str, Any] | None = None
    label: str | None = None
    description: str | None = None
    ai_context: str | AIContext1 | None = None


McpFieldPayload = Annotated[OsiField, BeforeValidator(_coerce_json_string_to_dict)]
```

- [ ] **Step 4: Update MCP semantic tools**

In `marivo/transports/mcp/tools/semantic.py`:

1. Remove `_resolve_requesting_user()` and all `requesting_user` parameters from MCP tools.
2. Import `McpFieldPayload` and `McpFieldUpdatePayload`.
3. Import identity helpers:

```python
from marivo.identity import require_user, resolve_user
```

3. Add tools:

```python
    @server.tool()  # type: ignore
    async def import_osi_document(document: McpStructuredObject) -> dict[str, Any]:
        """Import an OSI document into the current user's private working copy."""
        return await call_runtime(svc.import_osi_document, doc_data=document)

    @server.tool()  # type: ignore
    async def export_osi_document(semantic_model_name: str | None = None) -> dict[str, Any]:
        """Export current user's private working copy as an OSI document."""
        return await call_runtime(
            svc.export_osi_document,
            semantic_model_name=semantic_model_name,
        )
```

4. Change read calls to use optional `resolve_user()` internally. These reads may still use public visibility fallback when no private working copy exists, but must not fabricate an owner:

```python
        return await call_runtime(
            svc.list_semantic_models,
            requesting_user=resolve_user(),
        )
```

5. Add field CRUD. Write calls must call `require_user()` before passing `owner_user`; do not pass `resolve_user()` as an owner:

```python
    @server.tool()  # type: ignore
    async def create_field(model: str, dataset: str, payload: McpFieldPayload) -> dict[str, Any]:
        """Create a field in a dataset."""
        return await call_runtime(
            svc.create_field,
            model_name=model,
            dataset_name=dataset,
            field_data=payload.model_dump(by_alias=True),
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def list_fields(model: str, dataset: str) -> dict[str, Any]:
        """List fields in a dataset."""
        return await call_runtime(
            svc.list_fields,
            model_name=model,
            dataset_name=dataset,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def get_field(model: str, dataset: str, name: str) -> dict[str, Any]:
        """Get a field by name."""
        return await call_runtime(
            svc.get_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def update_field(
        model: str,
        dataset: str,
        name: str,
        payload: McpFieldUpdatePayload,
    ) -> dict[str, Any]:
        """Patch a field by name."""
        updates = {
            field_name: getattr(payload, field_name)
            for field_name in payload.model_fields_set
        }
        return await call_runtime(
            svc.update_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            updates=updates,
            owner_user=require_user(),
        )

    @server.tool()  # type: ignore
    async def delete_field(model: str, dataset: str, name: str) -> dict[str, Any]:
        """Delete a field by name."""
        return await call_runtime(
            svc.delete_field,
            model_name=model,
            dataset_name=dataset,
            field_name=name,
            owner_user=require_user(),
        )
```

- [ ] **Step 5: Run MCP tests**

Run:

```bash
.venv/bin/pytest tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py -q
```

Expected: tests pass after updating expected tool inventory.

- [ ] **Step 6: Commit**

```bash
git add marivo/transports/mcp/tools/schemas.py marivo/transports/mcp/tools/semantic.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py
git commit -m "feat: expose semantic import export and fields over mcp"
```

---

### Task 7: Add Atomicity, Duplicate, And Export Regression Tests

**Files:**
- Modify: `tests/runtime/semantic/test_import_export.py`
- Modify: `marivo/runtime/semantic/import_export.py` if needed
- Modify: `marivo/runtime/semantic/semantic_service.py` if needed

- [ ] **Step 1: Add mid-merge rollback test**

Append to `tests/runtime/semantic/test_import_export.py`:

```python
class _FailAfterDatasetStore:
    def __init__(self, wrapped: object) -> None:
        self.wrapped = wrapped
        self.failed = False

    def execute(self, sql: str, params: list[object] | None = None) -> None:
        if "INSERT INTO semantic_fields" in sql and not self.failed:
            self.failed = True
            raise RuntimeError("forced mid-merge failure")
        return self.wrapped.execute(sql, params)

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)


class SemanticImportAtomicityTests(unittest.TestCase):
    def test_mid_merge_failure_preserves_existing_private_model(self) -> None:
        from marivo.identity import reset_current_user, set_current_user

        store = make_temp_metadata_store(prefix="semantic_atomicity_")
        datasource_service = DatasourceService(store)
        datasource_service.register_datasource(
            name="warehouse_a",
            source_type="duckdb",
            connection={"database": ":memory:"},
        )
        service = SemanticServiceAdapter(store, datasource_service=datasource_service)
        token = set_current_user("alice")  # Simulates transport/context provider injection.
        try:
            service.import_osi_document(_osi_doc("atomic_model"))
            before = service.export_osi_document("atomic_model")
            service._service.store = _FailAfterDatasetStore(store)
            with self.assertRaises(RuntimeError):
                service.import_osi_document(_osi_doc("atomic_model"))
            service._service.store = store
            after = service.export_osi_document("atomic_model")
        finally:
            reset_current_user(token)
            store.close()

        self.assertEqual(after, before)
```

- [ ] **Step 2: Run atomicity test and verify failure if transaction support is missing**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py::SemanticImportAtomicityTests::test_mid_merge_failure_preserves_existing_private_model -q
```

Expected before transaction fix:

```text
FAIL ... after != before
```

If it passes because each execute autocommits only after success and no destructive update happened, keep the test. It is still the regression guard.

- [ ] **Step 3: Add transaction boundary if needed**

If the atomicity test fails, update `SemanticMergeExecutor.apply()` to use a real transaction. For SQLite store, prefer adding a transaction method to the store only if one already exists. If no transaction API exists, implement import in a plan-first order that does not delete existing rows until all preflight/binding validation has completed, then wrap write errors by re-raising after logging. Do not add a fake rollback comment.

If a transaction API is added to `MetadataStore`, update every implementation and test double in the same task.

- [ ] **Step 4: Run duplicate/empty/export tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/runtime/semantic/test_import_export.py marivo/runtime/semantic/import_export.py marivo/runtime/semantic/semantic_service.py marivo/adapters/metadata.py marivo/adapters/local/sqlite_metadata.py tests/shared_fixtures.py
git commit -m "test: cover semantic import atomicity"
```

Only include `marivo/adapters/metadata.py`, `marivo/adapters/local/sqlite_metadata.py`, and `tests/shared_fixtures.py` if you actually added a transaction API.

---

### Task 8: Final Contract Cleanup And Full Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-05-12-semantic-layer-mcp-surface-design.md` only if implementation reveals a small naming mismatch
- Modify: any test files with stale old-public-import assertions

- [ ] **Step 1: Search for stale public import wording**

Run:

```bash
rg -n "latest public layer|public import|imported public|requesting_user" marivo tests docs/superpowers/specs/2026-05-12-semantic-layer-mcp-surface-design.md
```

Expected:

```text
No stale public import behavior remains in runtime/tests. The spec may mention old public import only as deleted behavior.
No MCP tool schema exposes requesting_user.
```

- [ ] **Step 2: Search for old destructive import code**

Run:

```bash
rg -n "DELETE FROM semantic_metrics|DELETE FROM semantic_relationships|DELETE FROM semantic_fields WHERE dataset_id IN|visibility = 'public'" marivo/runtime/semantic/semantic_service.py marivo/runtime/semantic/import_export.py
```

Expected:

```text
No old public-import child replacement block remains.
```

- [ ] **Step 3: Run focused semantic and MCP tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py tests/test_semantic_v2_api.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run repository checks**

Run:

```bash
make test
make typecheck
make lint
```

Expected:

```text
All checks pass.
```

- [ ] **Step 5: Commit final cleanup if Step 1-4 changed files**

If Step 1-4 changed only the semantic plan doc, commit that exact file:

```bash
git add docs/superpowers/specs/2026-05-12-semantic-layer-mcp-surface-design.md
git commit -m "chore: finalize semantic surface cutover"
```

If Step 1-4 changed implementation or test files instead, replace the `git add` path above with the exact files from `git diff --name-only`. If no cleanup was needed, do not create an empty commit.

---

## Self-Review Checklist

Spec coverage:

- MCP tools: Tasks 6 and 8.
- HTTP API import/export/field routes: Tasks 4 and 5.
- Private working copy import/export: Task 3.
- Focused application service extraction: Tasks 1-3.
- Bounded datasource binding: Task 2.
- Empty document and duplicate identity validation: Tasks 1 and 7.
- Import report response shape: Tasks 1, 3, 5, 6.
- Patch `null` vs missing: Tasks 4 and 6.
- Field CRUD: Tasks 4 and 6.
- Transaction atomicity: Task 7.
- Structured logging: Task 3.
- No old public import compatibility: Tasks 3, 5, 8.
- Full verification: Task 8.

Known open risk:

- Expression safety validation remains out of scope by user decision. Do not add it during implementation.
