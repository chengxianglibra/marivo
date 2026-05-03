---
status: archived
created: 2026-05-01
---

# OpenAPI Schema Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the scoped semantic-layer and session/intent HTTP APIs so their OpenAPI contracts are typed, JSON-schema-complete, and agent-friendly.

**Architecture:** Keep HTTP `application/json` as the wire format and make Pydantic models the source of truth for scoped request/response schemas. Route handlers may continue calling existing services that return dictionaries, but they must validate and serialize through named Pydantic models at the API boundary. The OpenAPI quality gate is scoped to `/semantic-models/**` and `/sessions/**` in this phase; non-scoped violations are recorded in a follow-up debt document.

**Tech Stack:** FastAPI, Pydantic v2, JSON Schema/OpenAPI 3.1, pytest via `.venv/bin/pytest`, repository entrypoints `make test`, `make typecheck`, and `make lint`.

---

## Scope And File Structure

### In Scope

- `/semantic-models`
- `/semantic-models/import`
- `/semantic-models/{model}`
- `/semantic-models/{model}/datasets/**`
- `/semantic-models/{model}/relationships/**`
- `/semantic-models/{model}/metrics/**`
- `/semantic-models/{model}/readiness`
- `/sessions`
- `/sessions/{session_id}`
- `/sessions/{session_id}/state`
- `/sessions/{session_id}/state/query`
- `/sessions/{session_id}/runtime-status`
- `/sessions/{session_id}/artifacts/{artifact_id}/runtime-status`
- `/sessions/{session_id}/propositions/{proposition_id}/context`
- `/sessions/{session_id}/propositions/{proposition_id}/runtime-status`
- `/sessions/{session_id}/intents/*`
- `/sessions/{session_id}/terminate`

### Out Of Scope For This Implementation

- datasources
- routing
- governance
- jobs
- approvals
- metrics
- calendar
- `/catalog` legacy/stub routes
- `/openapi/*` meta routes

### Files To Create Or Modify

- Create `tests/test_openapi_schema_quality.py`
  - Builds a router-only FastAPI app with `app.api.router.include_api_routers`.
  - Scans only scoped paths.
  - Fails on `additionalProperties: true`, missing/empty `items`, and leaf schemas with no `type`, `$ref`, `oneOf`, `anyOf`, `allOf`, `enum`, or `const`.

- Create `app/api/models/json_contract.py`
  - Holds reusable schema-safe JSON contract types such as scalar values and scalar maps.
  - Avoids `Any`, `object`, and unbounded dictionaries on scoped APIs.

- Modify `app/api/models/osi.py`
  - Align semantic model Pydantic schemas with `docs/api/osi-marivo-schema.json`.
  - Replace `AIContext` free-form object with a named typed object.
  - Add optional top-level `dialects` and `vendors`.
  - Use MARIVO-specific custom-extension models so OpenAPI exposes the fixed vendor schemas.

- Modify `app/api/models/marivo_extensions.py`
  - Replace `MarivoMetricFilter.expression: dict[str, Any]` with the typed OSI `Expression` model or move the filter model into `osi.py` to avoid circular imports.

- Modify `app/api/models/session.py`
  - Add typed request-side models: `SessionBudget`, `SessionPolicyRef`, `SessionGovernancePolicy`, and `SessionStateSlice`.
  - Replace `budget: dict[str, Any]`, `policy: dict[str, Any]`, and `slice: dict[str, Any] | None`.

- Create `app/api/models/session_responses.py`
  - Holds named session response models:
    `AnalysisSession`, `SessionCreateResponse`, `SessionListResponse`, `SessionRuntimeStatusResponse`,
    `ArtifactRuntimeStatusResponse`, `PropositionRuntimeStatusResponse`, `SessionStateView`,
    `PropositionContextView`, and `SessionTerminateResponse`.

- Create `app/api/models/intent_responses.py`
  - Holds named intent response models:
    `ObserveResponse`, `CompareResponse`, `DecomposeResponse`, `CorrelateResponse`,
    `DetectResponse`, `IntentTestResponse`, `ForecastResponse`, `AttributeResponse`,
    `DiagnoseResponse`, and `ValidateResponse`.
  - Uses a typed envelope plus typed JSON-value containers where result details are not yet fully domain-modeled.

- Modify `app/api/models/intents.py`
  - Replace `ObserveScope.constraints: dict[str, Any]` with `ScalarMap`.
  - Replace deprecated `ObserveScope.predicate: dict[str, Any]` with a typed predicate node union or a named scalar-comparison predicate.

- Modify `app/api/models/__init__.py`
  - Export new request/response models used by routes and tests.

- Modify `app/api/semantic_v2.py`
  - Add `response_model=` on scoped semantic routes.
  - Replace request payloads typed as `dict[str, Any]` with OSI semantic object models.
  - Keep service calls using `model_dump(by_alias=True, exclude_none=True)`.

- Modify `app/api/sessions.py`
  - Add `response_model=` on session and intent routes.
  - Serialize existing service dictionaries through Pydantic response models.

- Create `docs/api/openapi-schema-hardening-followups.md`
  - Records non-scoped API schema debt with exact API groups, violation categories, and proposed hardening order.

---

### Task 1: Add Scoped OpenAPI Quality Test

**Files:**
- Create: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Write the failing scoped OpenAPI quality test**

Create `tests/test_openapi_schema_quality.py` with this content:

```python
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import FastAPI

from app.api.router import include_api_routers


SCOPED_PATH_PREFIXES = ("/semantic-models", "/sessions")

SCHEMA_KEYS_THAT_MAKE_A_LEAF_TYPED = {
    "type",
    "$ref",
    "oneOf",
    "anyOf",
    "allOf",
    "enum",
    "const",
}


def _router_only_openapi() -> dict[str, Any]:
    app = FastAPI(title="Marivo Semantic Layer", version="0.1.0")
    include_api_routers(app)
    return app.openapi()


def _is_scoped_path(path: str) -> bool:
    return path.startswith(SCOPED_PATH_PREFIXES)


def _iter_scoped_operation_schemas(openapi: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    paths = openapi.get("paths")
    assert isinstance(paths, dict)
    for path, path_item in paths.items():
        if not _is_scoped_path(str(path)):
            continue
        assert isinstance(path_item, dict)
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            yield f"/paths/{path}/{method}", operation


def _iter_component_schemas(openapi: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    schemas = openapi.get("components", {}).get("schemas", {})
    assert isinstance(schemas, dict)
    for name, schema in schemas.items():
        yield f"/components/schemas/{name}", schema


def _walk_schema(node: Any, pointer: str, violations: list[str]) -> None:
    if isinstance(node, list):
        if not node and pointer.endswith("/items"):
            violations.append(f"{pointer}: array items must not be empty")
        for index, item in enumerate(node):
            _walk_schema(item, f"{pointer}/{index}", violations)
        return

    if not isinstance(node, dict):
        return

    if node.get("additionalProperties") is True:
        violations.append(f"{pointer}/additionalProperties: additionalProperties true is forbidden")

    if node.get("type") == "array":
        items = node.get("items")
        if not isinstance(items, dict) or not items:
            violations.append(f"{pointer}/items: array schema must declare non-empty items")

    child_schema_keys = {
        "properties",
        "items",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "prefixItems",
        "contains",
    }
    has_schema_children = any(key in node for key in child_schema_keys)
    descriptive_only = bool(set(node) - {"title", "description", "default", "deprecated", "examples"})
    if not has_schema_children and descriptive_only:
        if not any(key in node for key in SCHEMA_KEYS_THAT_MAKE_A_LEAF_TYPED):
            violations.append(
                f"{pointer}: schema leaf must declare type/ref/composition/enum/const"
            )

    for key, value in node.items():
        _walk_schema(value, f"{pointer}/{key}", violations)


def test_scoped_openapi_schemas_are_agent_friendly() -> None:
    openapi = _router_only_openapi()
    violations: list[str] = []

    for pointer, schema in _iter_scoped_operation_schemas(openapi):
        _walk_schema(schema, pointer, violations)

    referenced_names: set[str] = set()

    def collect_refs(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                collect_refs(item)
            return
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            referenced_names.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            collect_refs(value)

    for _, operation in _iter_scoped_operation_schemas(openapi):
        collect_refs(operation)

    for pointer, schema in _iter_component_schemas(openapi):
        name = pointer.rsplit("/", 1)[-1]
        if name in referenced_names:
            _walk_schema(schema, pointer, violations)

    assert not violations, "\n".join(sorted(violations))
```

- [ ] **Step 2: Run the test to verify it fails on current OpenAPI**

Run:

```bash
.venv/bin/pytest tests/test_openapi_schema_quality.py::test_scoped_openapi_schemas_are_agent_friendly -q
```

Expected: FAIL with violations under scoped paths, including at least one `additionalProperties true is forbidden` from current `dict[str, Any]` / `dict[str, object]` route schemas.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_openapi_schema_quality.py
git commit -m "test: add scoped OpenAPI schema quality gate"
```

---

### Task 2: Add Shared JSON Contract Types

**Files:**
- Create: `app/api/models/json_contract.py`
- Modify: `app/api/models/__init__.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Add schema-safe JSON contract models**

Create `app/api/models/json_contract.py`:

```python
"""Schema-safe JSON value models for scoped HTTP API contracts."""

from __future__ import annotations

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, RootModel


JsonScalar: TypeAlias = str | int | float | bool | None
ScalarMap: TypeAlias = dict[str, JsonScalar]


class JsonScalarMap(RootModel[ScalarMap]):
    root: ScalarMap


class EmptyObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 2: Export the shared JSON contract names**

Modify `app/api/models/__init__.py` and add these imports near the other model imports:

```python
from .json_contract import (
    EmptyObject,
    JsonScalar,
    JsonScalarMap,
    ScalarMap,
)
```

Add the same names to `__all__`:

```python
    "EmptyObject",
    "JsonScalar",
    "JsonScalarMap",
    "ScalarMap",
```

- [ ] **Step 3: Run the quality test and confirm it still fails for route schemas**

Run:

```bash
.venv/bin/pytest tests/test_openapi_schema_quality.py::test_scoped_openapi_schemas_are_agent_friendly -q
```

Expected: FAIL. This task only adds shared building blocks; scoped routes still reference weak schemas.

- [ ] **Step 4: Commit**

```bash
git add app/api/models/json_contract.py app/api/models/__init__.py
git commit -m "feat: add schema-safe JSON contract types"
```

---

### Task 3: Align OSI And MARIVO Extension Models With The Reference Schema

**Files:**
- Modify: `app/api/models/osi.py`
- Modify: `app/api/models/marivo_extensions.py`
- Modify: `app/api/models/__init__.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Replace free-form OSI AI context with a typed object**

In `app/api/models/osi.py`, replace the current `AIContext` definition:

```python
class AIContextObject(BaseModel):
    instructions: str | None = None
    synonyms: list[str] | None = None
    examples: list[str] | None = None

    model_config = {"extra": "forbid"}


class AIContext(RootModel[str | AIContextObject]):
    """AI context — either a string or a typed OSI AI context object."""

    root: str | AIContextObject
```

- [ ] **Step 2: Add typed MARIVO custom-extension wrappers in `osi.py`**

In `app/api/models/osi.py`, import the MARIVO extension payload models:

```python
from app.api.models.marivo_extensions import (
    MarivoDatasetExtension,
    MarivoFieldExtension,
    MarivoMetricExtension,
    MarivoRelationshipExtension,
    MarivoSemanticModelExtension,
)
```

Then add these wrapper models after `CustomExtension`:

```python
class MarivoSemanticModelCustomExtension(BaseModel):
    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/$defs/MarivoSemanticModelExtension"},
        },
    )

    model_config = {"extra": "forbid"}


class MarivoDatasetCustomExtension(BaseModel):
    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/$defs/MarivoDatasetExtension"},
        },
    )

    model_config = {"extra": "forbid"}


class MarivoFieldCustomExtension(BaseModel):
    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/$defs/MarivoFieldExtension"},
        },
    )

    model_config = {"extra": "forbid"}


class MarivoRelationshipCustomExtension(BaseModel):
    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/$defs/MarivoRelationshipExtension"},
        },
    )

    model_config = {"extra": "forbid"}


class MarivoMetricCustomExtension(BaseModel):
    vendor_name: Literal["MARIVO"]
    data: str = PydanticField(
        ...,
        json_schema_extra={
            "contentMediaType": "application/json",
            "contentSchema": {"$ref": "#/$defs/MarivoMetricExtension"},
        },
    )

    model_config = {"extra": "forbid"}
```

- [ ] **Step 3: Use typed extension unions on semantic objects**

In `app/api/models/osi.py`, update semantic object fields:

```python
class Field(BaseModel):
    name: str
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoFieldCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}
```

```python
class Dataset(BaseModel):
    name: str
    source: str
    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    fields: list[Field] | None = None
    custom_extensions: list[MarivoDatasetCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}
```

```python
class Relationship(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    from_: str = PydanticField(alias="from")
    to: str
    from_columns: list[str] = PydanticField(..., min_length=1)
    to_columns: list[str] = PydanticField(..., min_length=1)
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoRelationshipCustomExtension | CustomExtension] | None = None
```

```python
class Metric(BaseModel):
    name: str
    expression: Expression
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[MarivoMetricCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}
```

```python
class SemanticModel(BaseModel):
    name: str
    datasets: list[Dataset] = PydanticField(..., min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    relationships: list[Relationship] | None = None
    metrics: list[Metric] | None = None
    custom_extensions: list[MarivoSemanticModelCustomExtension | CustomExtension] | None = None

    model_config = {"extra": "forbid"}
```

- [ ] **Step 4: Add optional OSI enumeration fields to `OSIDocument`**

In `app/api/models/osi.py`, add these aliases and update `OSIDocument`:

```python
Dialect = Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
Vendor = Literal["COMMON", "SNOWFLAKE", "SALESFORCE", "DBT", "DATABRICKS", "MARIVO"]
```

```python
class OSIDocument(BaseModel):
    """Top-level OSI document structure."""

    version: Literal["0.1.1"]
    semantic_model: list[SemanticModel]
    dialects: list[Dialect] | None = None
    vendors: list[Vendor] | None = None

    model_config = {"extra": "forbid"}
```

- [ ] **Step 5: Remove the `Any` expression from MARIVO metric filter**

In `app/api/models/marivo_extensions.py`, remove `Any` from imports and replace `MarivoMetricFilter.expression` with a local typed expression shape to avoid circular imports:

```python
class MarivoMetricFilterExpressionDialect(BaseModel):
    dialect: Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
    expression: str

    model_config = {"extra": "forbid"}


class MarivoMetricFilterExpression(BaseModel):
    dialects: list[MarivoMetricFilterExpressionDialect] = Field(..., min_length=1)

    model_config = {"extra": "forbid"}


class MarivoMetricFilter(BaseModel):
    name: str = Field(..., min_length=1)
    expression: MarivoMetricFilterExpression

    model_config = {"extra": "forbid"}
```

- [ ] **Step 6: Export the new OSI names**

Modify `app/api/models/__init__.py` to export:

```python
    AIContextObject,
    Dialect,
    MarivoDatasetCustomExtension,
    MarivoFieldCustomExtension,
    MarivoMetricCustomExtension,
    MarivoRelationshipCustomExtension,
    MarivoSemanticModelCustomExtension,
    Vendor,
```

Also add each name to `__all__`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py tests/test_openapi_schema_quality.py -q
```

Expected: `tests/test_semantic_v2_api.py` still passes. The OpenAPI quality test may still fail because routes are not yet wired to typed models.

- [ ] **Step 8: Commit**

```bash
git add app/api/models/osi.py app/api/models/marivo_extensions.py app/api/models/__init__.py
git commit -m "feat: align OSI API models with Marivo extension schema"
```

---

### Task 4: Type Semantic-Model Routes

**Files:**
- Modify: `app/api/semantic_v2.py`
- Test: `tests/test_semantic_v2_api.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Import typed semantic models in the route module**

Modify `app/api/semantic_v2.py` imports:

```python
from app.api.models.osi import (
    Dataset,
    Metric,
    OSIDocument,
    OSI_SPEC_VERSION,
    Relationship,
    SemanticModel,
)
```

- [ ] **Step 2: Add typed readiness response models**

In `app/api/semantic_v2.py`, add these local models after `_T = TypeVar("_T")`:

```python
from pydantic import BaseModel, ConfigDict, Field


class ReadinessBlocker(BaseModel):
    code: str
    message: str
    subject_ref: str | None = None

    model_config = ConfigDict(extra="forbid")


class SemanticObjectReadiness(BaseModel):
    object_ref: str
    object_type: str
    readiness_status: str
    blockers: list[ReadinessBlocker] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SemanticModelReadinessResponse(BaseModel):
    model: str
    readiness_status: str
    blockers: list[ReadinessBlocker] = Field(default_factory=list)
    objects: list[SemanticObjectReadiness] = Field(default_factory=list)
    schema_version: str = "semantic_model_readiness.v1"

    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 3: Convert helper wrappers to typed `OSIDocument`**

Replace helper return types:

```python
def _osi_model_wrap(model_data: dict[str, Any]) -> OSIDocument:
    return OSIDocument.model_validate(
        {"version": OSI_SPEC_VERSION, "semantic_model": [model_data]}
    )


def _osi_list_wrap(models: list[dict[str, Any]]) -> OSIDocument:
    return OSIDocument.model_validate({"version": OSI_SPEC_VERSION, "semantic_model": models})
```

- [ ] **Step 4: Add request and response models to semantic model CRUD routes**

Update route signatures:

```python
@router.post("", response_model=OSIDocument)
def create_semantic_model(request: Request, payload: SemanticModel) -> OSIDocument:
    svc = _get_service(request)
    result = _run(lambda: svc.create_semantic_model(payload.model_dump(by_alias=True, exclude_none=True)))
    return _osi_model_wrap(result)
```

```python
@router.get("", response_model=OSIDocument)
def list_semantic_models(request: Request, requesting_user: str | None = None) -> OSIDocument:
    svc = _get_service(request)
    results = svc.list_semantic_models(requesting_user=requesting_user)
    return _osi_list_wrap(results)
```

```python
@router.post("/import", response_model=OSIDocument)
def import_osi_document(request: Request, payload: OSIDocument) -> OSIDocument:
    svc = _get_service(request)
    results = _run(lambda: svc.import_osi_document(payload.model_dump(by_alias=True, exclude_none=True)))
    return _osi_list_wrap(results)
```

```python
@router.get("/{model}", response_model=OSIDocument)
def get_semantic_model(
    model: str, request: Request, requesting_user: str | None = None
) -> OSIDocument:
    svc = _get_service(request)
    result = _run(lambda: svc.get_semantic_model(model, requesting_user=requesting_user))
    return _osi_model_wrap(result)
```

```python
@router.put("/{model}", response_model=OSIDocument)
def update_semantic_model(
    model: str, request: Request, payload: SemanticModel
) -> OSIDocument:
    svc = _get_service(request)
    result = _run(lambda: svc.update_semantic_model(model, payload.model_dump(by_alias=True, exclude_none=True)))
    return _osi_model_wrap(result)
```

- [ ] **Step 5: Add request and response models to dataset, relationship, and metric routes**

Update the dataset routes:

```python
@router.post("/{model}/datasets", response_model=Dataset)
def create_dataset(model: str, request: Request, payload: Dataset) -> Dataset:
    svc = _get_service(request)
    return Dataset.model_validate(
        _run(lambda: svc.create_dataset(model, payload.model_dump(by_alias=True, exclude_none=True)))
    )


@router.get("/{model}/datasets", response_model=list[Dataset])
def list_datasets(model: str, request: Request) -> list[Dataset]:
    svc = _get_service(request)
    return [Dataset.model_validate(item) for item in svc.list_datasets(model)]


@router.get("/{model}/datasets/{name}", response_model=Dataset)
def get_dataset(model: str, name: str, request: Request) -> Dataset:
    svc = _get_service(request)
    return Dataset.model_validate(_run(lambda: svc.get_dataset(model, name)))


@router.put("/{model}/datasets/{name}", response_model=Dataset)
def update_dataset(model: str, name: str, request: Request, payload: Dataset) -> Dataset:
    svc = _get_service(request)
    return Dataset.model_validate(
        _run(lambda: svc.update_dataset(model, name, payload.model_dump(by_alias=True, exclude_none=True)))
    )
```

Update relationship and metric routes with the same pattern using `Relationship` and `Metric`.

- [ ] **Step 6: Add readiness response model**

Update readiness route:

```python
@router.get("/{model}/readiness", response_model=SemanticModelReadinessResponse)
def get_readiness(model: str, request: Request) -> SemanticModelReadinessResponse:
    svc = _get_service(request)
    return SemanticModelReadinessResponse.model_validate(_run(lambda: svc.get_readiness(model)))
```

If existing service payload uses different keys, adapt only at the API boundary:

```python
raw = _run(lambda: svc.get_readiness(model))
payload = {
    "model": raw.get("model") or model,
    "readiness_status": raw.get("readiness_status") or raw.get("status") or "unknown",
    "blockers": raw.get("blockers") or [],
    "objects": raw.get("objects") or raw.get("details") or [],
}
return SemanticModelReadinessResponse.model_validate(payload)
```

- [ ] **Step 7: Run semantic API tests and scoped OpenAPI test**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py tests/test_openapi_schema_quality.py -q
```

Expected: semantic API tests pass. The OpenAPI quality test may still fail on `/sessions/**`.

- [ ] **Step 8: Commit**

```bash
git add app/api/semantic_v2.py tests/test_semantic_v2_api.py
git commit -m "feat: expose typed semantic model API schemas"
```

---

### Task 5: Type Session Request Models

**Files:**
- Modify: `app/api/models/session.py`
- Modify: `app/api/models/__init__.py`
- Test: `tests/test_sessions.py`
- Test: `tests/test_session_state.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Replace free-form budget and policy models**

In `app/api/models/session.py`, remove `Any` from imports and add:

```python
class SessionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_scan_bytes: int = Field(default=500_000_000_000, ge=0)
    max_latency_sec: int = Field(default=120, ge=0)


class SessionPolicyRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_version: str | None = None


class SessionGovernancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregate_only: bool = True
    min_group_size: int = Field(default=100, ge=0)
    policy_refs: list[SessionPolicyRef] | None = None
```

- [ ] **Step 2: Update `SessionCreateRequest` fields**

In `SessionCreateRequest`, replace the existing `budget` and `policy` fields:

```python
    budget: SessionBudget = Field(
        default_factory=SessionBudget,
        description=(
            "Hard resource limits enforced by Marivo. Steps that would exceed "
            "max_scan_bytes or max_latency_sec are blocked before execution."
        ),
    )
    policy: SessionGovernancePolicy = Field(
        default_factory=SessionGovernancePolicy,
        description="Governance rules enforced by Marivo for this analysis session.",
    )
```

- [ ] **Step 3: Add typed session-state slice model**

In `app/api/models/session.py`, add:

```python
class SessionStateSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str | None = None
    entity: str | None = None
    grain: str | None = None
```

Then update `SessionStateQueryRequest`:

```python
    slice: SessionStateSlice | None = None
```

- [ ] **Step 4: Preserve service input shape in the route layer**

In `app/api/sessions.py`, when calling `create_session`, later Task 7 must use:

```python
budget=payload.budget.model_dump(exclude_none=True)
policy=payload.policy.model_dump(exclude_none=True)
```

For `query_session_state`, later Task 7 must use:

```python
query = payload.model_dump(exclude_none=True)
```

- [ ] **Step 5: Export the new models**

Modify `app/api/models/__init__.py`:

```python
from .session import (
    SessionBudget,
    SessionCreateRequest,
    SessionExecutionIdentityPayload,
    SessionGovernancePolicy,
    SessionPolicyRef,
    SessionStateQueryRequest,
    SessionStateSlice,
    SessionTerminateRequest,
)
```

Add the new names to `__all__`.

- [ ] **Step 6: Run session request tests**

Run:

```bash
.venv/bin/pytest tests/test_sessions.py tests/test_session_state.py tests/test_openapi_schema_quality.py -q
```

Expected: existing session tests pass except for any assertions expecting arbitrary `budget` or `policy` keys. The OpenAPI quality test may still fail until session responses are modeled.

- [ ] **Step 7: Commit**

```bash
git add app/api/models/session.py app/api/models/__init__.py tests/test_sessions.py tests/test_session_state.py
git commit -m "feat: type session request contract fields"
```

---

### Task 6: Add Session Response Models

**Files:**
- Create: `app/api/models/session_responses.py`
- Modify: `app/api/models/__init__.py`
- Test: `tests/test_sessions.py`
- Test: `tests/test_session_state.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Create typed session response models**

Create `app/api/models/session_responses.py`:

```python
"""Typed response models for session lifecycle, state, and runtime APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.models.json_contract import ScalarMap
from app.api.models.session import SessionBudget, SessionExecutionIdentityPayload, SessionPolicyRef


class SessionGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


class SessionScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: ScalarMap | None = None


class SessionGovernanceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_refs: list[SessionPolicyRef] | None = None
    budget: SessionBudget | None = None
    warnings: list[str] | None = None


class SessionLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    terminal_reason: str | None = None
    ended_at: str | None = None
    rollover_from_session_id: str | None = None


class SessionStateViewRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    view_type: str


class SessionStateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_view_ref: SessionStateViewRef


class AnalysisSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    goal: SessionGoal
    scope: SessionScope
    governance: SessionGovernanceView
    execution_identity: SessionExecutionIdentityPayload = Field(default_factory=SessionExecutionIdentityPayload)
    lifecycle: SessionLifecycle
    state_summary: SessionStateSummary
    created_at: str
    updated_at: str
    schema_version: str


SessionCreateResponse = AnalysisSession
SessionDetailResponse = AnalysisSession
SessionTerminateResponse = AnalysisSession


class SessionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisSession]
    next_page_token: str | None = None


class RuntimeBacklogSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queued_artifacts: int
    queued_propositions: int
    backpressured_propositions: int
    failed_items: int


class SessionRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    overall_status: str
    current_stage: str | None = None
    last_successful_stage: str | None = None
    blocked_reason: str
    backlog_summary: RuntimeBacklogSummary
    updated_at: str
    schema_version: str


class ArtifactRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    artifact_id: str
    current_stage: str
    last_successful_stage: str | None = None
    current_attempt: int | None = None
    backlog_state: str
    last_failure_reason: str
    last_failure_at: str | None = None
    schema_version: str


class PropositionRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    proposition_id: str
    current_stage: str
    last_successful_stage: str | None = None
    current_assessment_id: str | None = None
    current_attempt: int | None = None
    backlog_state: str
    last_failure_reason: str
    last_failure_at: str | None = None
    schema_version: str


class SessionStateTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_truncated: bool
    returned_count: int
    total_count: int
    sort_key: str
    applies_to: str


class SessionStateView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    active_propositions: list[ScalarMap]
    backing_findings: list[ScalarMap]
    blocking_gaps: list[ScalarMap]
    artifact_refs: list[ScalarMap]
    focus_subjects: list[ScalarMap]
    truncation: SessionStateTruncation
    next_page_token: str | None = None
    schema_version: str


class PropositionContextView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    proposition: ScalarMap
    seed_findings: list[ScalarMap]
    latest_assessment: ScalarMap | None = None
    live_evidence_closure: ScalarMap
    schema_version: str
```

- [ ] **Step 2: Export the response models**

Modify `app/api/models/__init__.py`:

```python
from .session_responses import (
    AnalysisSession,
    ArtifactRuntimeStatusResponse,
    PropositionContextView,
    PropositionRuntimeStatusResponse,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionRuntimeStatusResponse,
    SessionStateView,
    SessionTerminateResponse,
)
```

Add the names to `__all__`.

- [ ] **Step 3: Run the response model import smoke test**

Run:

```bash
.venv/bin/pytest tests/test_sessions.py::SessionAPITests::test_get_session_after_create -q
```

Expected: PASS. The new model module is importable; routes are not yet using it.

- [ ] **Step 4: Commit**

```bash
git add app/api/models/session_responses.py app/api/models/__init__.py
git commit -m "feat: add typed session response models"
```

---

### Task 7: Wire Session Response Models Into Routes

**Files:**
- Modify: `app/api/sessions.py`
- Test: `tests/test_sessions.py`
- Test: `tests/test_session_state.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Import session response models**

Modify imports in `app/api/sessions.py`:

```python
from app.api.models import (
    ArtifactRef,
    ArtifactRuntimeStatusResponse,
    AttributeRequest,
    CompareRequest,
    CorrelateRequest,
    DecomposeRequest,
    DetectRequest,
    DiagnoseRequest,
    ForecastRequest,
    IntentTestRequest,
    ObservationRef,
    ObserveRequest,
    PropositionContextView,
    PropositionRuntimeStatusResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionRuntimeStatusResponse,
    SessionStateQueryRequest,
    SessionStateView,
    SessionTerminateRequest,
    SessionTerminateResponse,
    ValidateRequest,
)
```

- [ ] **Step 2: Add response models to session lifecycle routes**

Update route decorators and return annotations:

```python
@router.post("/sessions", response_model=SessionCreateResponse)
def create_session(payload: SessionCreateRequest, request: Request) -> SessionCreateResponse:
    try:
        result = get_services(request).service.create_session(
            goal=payload.goal,
            budget=payload.budget.model_dump(exclude_none=True),
            policy=payload.policy.model_dump(exclude_none=True),
            execution_identity=payload.execution_identity.model_dump(exclude_none=True),
        )
        return SessionCreateResponse.model_validate(result)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
```

Apply the same pattern:

```python
@router.get("/sessions", response_model=SessionListResponse)
def list_sessions(...) -> SessionListResponse:
    ...
    return SessionListResponse.model_validate(result)
```

```python
@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session(session_id: str, request: Request) -> SessionDetailResponse:
    ...
    return SessionDetailResponse.model_validate(result)
```

```python
@router.post("/sessions/{session_id}/terminate", response_model=SessionTerminateResponse)
def terminate_session(...) -> SessionTerminateResponse:
    ...
    return SessionTerminateResponse.model_validate(result)
```

- [ ] **Step 3: Add response models to runtime and state routes**

Update these routes:

```python
@router.get("/sessions/{session_id}/runtime-status", response_model=SessionRuntimeStatusResponse)
```

```python
@router.get("/sessions/{session_id}/state", response_model=SessionStateView)
```

```python
@router.post("/sessions/{session_id}/state/query", response_model=SessionStateView)
```

```python
@router.get(
    "/sessions/{session_id}/artifacts/{artifact_id}/runtime-status",
    response_model=ArtifactRuntimeStatusResponse,
)
```

```python
@router.get(
    "/sessions/{session_id}/propositions/{proposition_id}/context",
    response_model=PropositionContextView,
)
```

```python
@router.get(
    "/sessions/{session_id}/propositions/{proposition_id}/runtime-status",
    response_model=PropositionRuntimeStatusResponse,
)
```

Each route should wrap the service result:

```python
return SessionStateView.model_validate(get_services(request).service.get_session_state(session_id, query))
```

- [ ] **Step 4: Run session route tests**

Run:

```bash
.venv/bin/pytest tests/test_sessions.py tests/test_session_state.py tests/test_openapi_schema_quality.py -q
```

Expected: session tests pass. OpenAPI quality may still fail on intent response models.

- [ ] **Step 5: Commit**

```bash
git add app/api/sessions.py tests/test_sessions.py tests/test_session_state.py
git commit -m "feat: expose typed session API response schemas"
```

---

### Task 8: Type Intent Requests And Responses

**Files:**
- Modify: `app/api/models/intents.py`
- Create: `app/api/models/intent_responses.py`
- Modify: `app/api/models/__init__.py`
- Modify: `app/api/sessions.py`
- Test: `tests/test_intent_api.py`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Replace free-form observe scope fields**

In `app/api/models/intents.py`, replace `Any` import with `ScalarMap`:

```python
from app.api.models.json_contract import ScalarMap
```

Add a typed deprecated predicate model:

```python
class PredicateComparison(BaseModel):
    field: str
    operator: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"]
    value: str | int | float | bool | None
    values: list[str | int | float | bool | None] | None = None
```

Update `ObserveScope`:

```python
    constraints: ScalarMap | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate: PredicateComparison | None = Field(
        default=None,
        description="DEPRECATED: Use predicate_ref instead. Typed non-time predicate comparison.",
    )
```

- [ ] **Step 2: Create typed intent response envelopes**

Create `app/api/models/intent_responses.py`:

```python
"""Typed response models for intent execution APIs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.models.json_contract import JsonScalar, ScalarMap


class StepRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    step_id: str
    step_type: str


class SourceLineage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_object_ref: str | None = None
    binding_ref: str | None = None
    table_name: str | None = None


class ExecutionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    row_count: int | None = None
    elapsed_ms: int | None = None


class IntentResultTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list)
    rows: list[ScalarMap] = Field(default_factory=list)


class IntentResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    step_ref: StepRef | None = None
    step_id: str | None = None
    artifact_id: str
    artifact_type: str
    source_lineage: SourceLineage | None = None
    analytical_metadata: ScalarMap | None = None
    execution_metadata: ExecutionMetadata | None = None
    result: IntentResultTable | ScalarMap | list[ScalarMap] | JsonScalar | None = None


class ObserveResponse(IntentResponseBase):
    intent_type: Literal["observe"] = "observe"


class CompareResponse(IntentResponseBase):
    intent_type: Literal["compare"] = "compare"


class DecomposeResponse(IntentResponseBase):
    intent_type: Literal["decompose"] = "decompose"


class CorrelateResponse(IntentResponseBase):
    intent_type: Literal["correlate"] = "correlate"


class DetectResponse(IntentResponseBase):
    intent_type: Literal["detect"] = "detect"


class IntentTestResponse(IntentResponseBase):
    intent_type: Literal["test"] = "test"


class ForecastResponse(IntentResponseBase):
    intent_type: Literal["forecast"] = "forecast"


class AttributeResponse(IntentResponseBase):
    intent_type: Literal["attribute"] = "attribute"


class DiagnoseResponse(IntentResponseBase):
    intent_type: Literal["diagnose"] = "diagnose"


class ValidateResponse(IntentResponseBase):
    intent_type: Literal["validate"] = "validate"
```

- [ ] **Step 3: Add an adapter for existing intent dictionaries**

In `app/api/sessions.py`, add:

```python
def _normalize_intent_response(raw: dict[str, Any], intent_type: str) -> dict[str, Any]:
    payload = dict(raw)
    payload.setdefault("intent_type", intent_type)
    payload.setdefault("schema_version", str(payload.get("schema_version") or "1.0"))
    if "artifact_type" not in payload:
        payload["artifact_type"] = f"{intent_type}_artifact"
    if "artifact_id" not in payload:
        raise ValueError("intent response missing artifact_id")
    if "step_ref" not in payload and payload.get("step_id"):
        payload["step_ref"] = {
            "session_id": str(payload.get("session_id") or ""),
            "step_id": str(payload["step_id"]),
            "step_type": intent_type,
        }
    return payload
```

- [ ] **Step 4: Export intent response models**

Modify `app/api/models/__init__.py`:

```python
from .intent_responses import (
    AttributeResponse,
    CompareResponse,
    CorrelateResponse,
    DecomposeResponse,
    DetectResponse,
    DiagnoseResponse,
    ForecastResponse,
    IntentTestResponse,
    ObserveResponse,
    ValidateResponse,
)
```

Add those names to `__all__`.

- [ ] **Step 5: Add response models to intent routes**

In `app/api/sessions.py`, import all response models and update each route:

```python
@router.post("/sessions/{session_id}/intents/observe", response_model=ObserveResponse)
def intent_observe(
    session_id: str,
    payload: ObserveRequest,
    request: Request,
) -> ObserveResponse:
    raw = _run_intent(session_id, "observe", payload.model_dump(exclude_none=True), request)
    return ObserveResponse.model_validate(_normalize_intent_response(raw, "observe"))
```

Apply the same pattern:

```python
CompareResponse.model_validate(_normalize_intent_response(raw, "compare"))
DecomposeResponse.model_validate(_normalize_intent_response(raw, "decompose"))
CorrelateResponse.model_validate(_normalize_intent_response(raw, "correlate"))
DetectResponse.model_validate(_normalize_intent_response(raw, "detect"))
IntentTestResponse.model_validate(_normalize_intent_response(raw, "test"))
ForecastResponse.model_validate(_normalize_intent_response(raw, "forecast"))
AttributeResponse.model_validate(_normalize_intent_response(raw, "attribute"))
DiagnoseResponse.model_validate(_normalize_intent_response(raw, "diagnose"))
ValidateResponse.model_validate(_normalize_intent_response(raw, "validate"))
```

- [ ] **Step 6: Run focused intent tests**

Run:

```bash
.venv/bin/pytest tests/test_intent_api.py tests/test_openapi_schema_quality.py -q
```

Expected: existing intent behavior tests pass. The OpenAPI quality test should now pass or report only specific model leaves that must be tightened in the next step.

- [ ] **Step 7: Commit**

```bash
git add app/api/models/intents.py app/api/models/intent_responses.py app/api/models/__init__.py app/api/sessions.py tests/test_intent_api.py
git commit -m "feat: expose typed intent API schemas"
```

---

### Task 9: Record Non-Scoped API Schema Debt

**Files:**
- Create: `docs/api/openapi-schema-hardening-followups.md`
- Test: `tests/test_openapi_schema_quality.py`

- [ ] **Step 1: Create the follow-up debt document**

Create `docs/api/openapi-schema-hardening-followups.md`:

```markdown
# OpenAPI Schema Hardening Follow-Ups

This document records API schema debt outside the current hardening phase.
The current strict OpenAPI quality gate applies only to `/semantic-models/**`
and `/sessions/**`.

## Current Phase Exclusions

The following API groups remain outside the scoped quality gate:

- datasources
- routing
- governance
- jobs
- approvals
- metrics
- calendar
- `/catalog` legacy and stub routes
- `/openapi/*` meta routes

## Violation Categories To Eliminate

- `additionalProperties: true` from `dict[str, Any]`, `dict[str, object]`, and untyped response envelopes.
- Array schemas with missing or empty `items`.
- Schema leaves that only carry `title`, `description`, or `default` without `type`, `$ref`, `oneOf`, `anyOf`, `allOf`, `enum`, or `const`.
- Response bodies typed as plain `dict` instead of named Pydantic response models.
- Request fields that expose Python implementation containers rather than stable public contract models.

## Proposed Hardening Order

1. Datasources and routing, because they are foundational setup surfaces for agents.
2. Governance and approvals, because agents need clear policy and decision contracts.
3. Jobs and metrics, because they expose operational state.
4. Calendar, because the data model is already mostly typed and should be straightforward to close.
5. `/catalog` legacy/stub routes, after deciding whether each route remains public.
6. `/openapi/*` meta routes, after the primary API surface is stable.

## Exit Criteria For Future Global Enforcement

- Every public request and response body uses a named Pydantic model or a typed scalar/list schema.
- No public OpenAPI schema contains `additionalProperties: true`.
- No public OpenAPI array schema has missing or empty `items`.
- No public OpenAPI schema leaf lacks a type, ref, composition, enum, or const.
- The scoped path filter in `tests/test_openapi_schema_quality.py` is removed.
```

- [ ] **Step 2: Run the scoped quality test**

Run:

```bash
.venv/bin/pytest tests/test_openapi_schema_quality.py::test_scoped_openapi_schemas_are_agent_friendly -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/api/openapi-schema-hardening-followups.md tests/test_openapi_schema_quality.py
git commit -m "docs: record remaining OpenAPI schema debt"
```

---

### Task 10: Final Verification

**Files:**
- No new files.
- Verify all files modified in Tasks 1-9.

- [ ] **Step 1: Run focused API tests**

Run:

```bash
.venv/bin/pytest tests/test_openapi_schema_quality.py tests/test_semantic_v2_api.py tests/test_sessions.py tests/test_session_state.py tests/test_intent_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full repository test entrypoint**

Run:

```bash
make test
```

Expected: PASS.

- [ ] **Step 3: Run typecheck**

Run:

```bash
make typecheck
```

Expected: PASS.

- [ ] **Step 4: Run lint**

Run:

```bash
make lint
```

Expected: PASS.

- [ ] **Step 5: Inspect OpenAPI manually for required references**

Run:

```bash
.venv/bin/python - <<'PY'
from fastapi import FastAPI
from app.api.router import include_api_routers

app = FastAPI(title="Marivo Semantic Layer", version="0.1.0")
include_api_routers(app)
schema = app.openapi()

import_schema = schema["paths"]["/semantic-models/import"]["post"]["requestBody"]["content"]["application/json"]["schema"]
observe_schema = schema["paths"]["/sessions/{session_id}/intents/observe"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]

print(import_schema)
print(observe_schema)
PY
```

Expected output includes:

```text
'$ref': '#/components/schemas/OSIDocument'
'$ref': '#/components/schemas/ObserveResponse'
```

- [ ] **Step 6: Commit final verification adjustments if any**

If final verification required small fixes:

```bash
git add app/api tests docs/api
git commit -m "fix: close scoped OpenAPI schema quality gaps"
```

If no fixes were required, do not create an empty commit.

---

## Self-Review

- Spec coverage:
  - Semantic-model routes are covered by Tasks 3-4.
  - Session lifecycle/state routes are covered by Tasks 5-7.
  - Intent request/response schema work is covered by Task 8.
  - Scoped OpenAPI quality test is covered by Task 1 and verified in Tasks 9-10.
  - Non-scoped API debt document is covered by Task 9.

- Placeholder scan:
  - The implementation plan contains concrete file paths, code blocks, commands, and expected outcomes for every task.

- Type consistency:
  - Shared JSON scalar types originate in `app/api/models/json_contract.py`.
  - Request models remain under `app/api/models/session.py` and `app/api/models/intents.py`.
  - Session response models are imported from `app/api/models/session_responses.py`.
  - Intent response models are imported from `app/api/models/intent_responses.py`.
