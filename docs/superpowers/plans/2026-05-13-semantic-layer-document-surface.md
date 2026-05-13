# Semantic Layer Document Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Marivo's semantic-layer CRUD-oriented MCP/HTTP surface with an OSI-Marivo document workflow: list, get, validate, import, and export.

**Architecture:** Keep transports thin and move document validation/import/export into runtime semantic helpers. Agents author OSI-Marivo JSON, call validation, and import the whole model graph transactionally; Marivo validates schema, references, Marivo extensions, and datasource grounding. This is a breaking change: remove old semantic CRUD and readiness HTTP/MCP surfaces instead of keeping compatibility aliases.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, FastMCP, SQLite metadata store, generated OSI models under `marivo/contracts/generated/`, repository entrypoints `make test`, `make typecheck`, `make lint`.

---

## Scope Boundaries

In scope:

- Add `validate_osi_semantic_models`, `import_osi_semantic_models`, and `export_osi_semantic_models` to the semantic application service.
- Keep `list_semantic_models` and `get_semantic_model`.
- Remove HTTP and MCP semantic CRUD endpoints/tools for model create/update/delete, dataset CRUD, field CRUD, metric CRUD, relationship CRUD, and readiness.
- Support stdio MCP inline JSON and local JSON file paths for validate/import; support `output_path` for export.
- Make import replace the whole same-name private model graph in one transaction.
- Add structured validation results with `valid`, `schema_version`, `errors`, `warnings`, and `summary`.
- Update docs and `marivo-semantic-layer` skill references to a document-first workflow.

Out of scope:

- UI work.
- Public/admin publish workflow.
- External version management.
- A separate schema-discovery MCP tool.
- Compatibility aliases for removed CRUD tools/routes.

## Existing State To Respect

- Current HTTP routes live in `marivo/transports/http/semantic_v2.py` and expose many CRUD routes.
- Current MCP semantic tools live in `marivo/transports/mcp/tools/semantic.py` and expose many CRUD tools.
- Current MCP input schemas live in `marivo/transports/mcp/tools/schemas.py`.
- Current semantic service lives in `marivo/runtime/semantic/semantic_service.py`.
- Current import/export helpers live in `marivo/runtime/semantic/import_export.py`; import currently behaves like merge and does not delete omitted child objects.
- HTTP adapter lives in `marivo/adapters/server/semantic_service_adapter.py`.
- Use repository entrypoints or explicit `.venv/bin/...` paths only. Do not use bare `python`, `pytest`, `mypy`, or `ruff`.

## Target File Structure

Create:

- `tests/runtime/semantic/test_document_validation.py`
  - Unit tests for document validation response shape, schema failures, duplicate names, reference failures, and datasource grounding failures.

Modify:

- `marivo/runtime/semantic/import_export.py`
  - Add validation result models.
  - Add `OsiSemanticDocumentValidator`.
  - Change import execution to whole-model replacement.

- `marivo/runtime/semantic/semantic_service.py`
  - Keep list/get.
  - Add `validate_osi_semantic_models`, `import_osi_semantic_models`, and `export_osi_semantic_models`.
  - Remove external CRUD methods no longer used by transports.

- `marivo/adapters/server/semantic_service_adapter.py`
  - Keep adapter methods for list/get/validate/import/export only.

- `marivo/transports/http/semantic_v2.py`
  - Keep `GET /semantic-models` and `GET /semantic-models/{model}`.
  - Keep/import route as document import.
  - Keep/export route.
  - Add validate route.
  - Remove CRUD and readiness routes.

- `marivo/transports/mcp/tools/schemas.py`
  - Replace semantic CRUD payload aliases with document-file input DTOs.

- `marivo/transports/mcp/tools/semantic.py`
  - Register only list/get/validate/import/export semantic tools.
  - Add JSON file load/write handling for stdio-compatible tools.

- `tests/test_semantic_v2_api.py`
  - Replace CRUD API tests with document-surface tests.

- `tests/transports/mcp/test_tool_parity.py`
  - Replace CRUD inventory assertions with the new compact semantic inventory.

- `tests/transports/mcp/test_stdio_mcp_e2e.py`
  - Add or update stdio tool schema/file tests if this file already covers semantic tool calls.

- `tests/runtime/semantic/test_import_export.py`
  - Update import tests for whole-model replacement and validation-before-write.

- `docs/api/semantic.md`
  - Replace old endpoint table and examples with list/get/validate/import/export.

- `marivo-skill/marivo-semantic-layer/SKILL.md`
  - Replace CRUD staged-write workflow with document-first validation/import workflow.

- `marivo-skill/marivo-semantic-layer/references/modeling.md`
  - Replace CRUD examples with OSI-Marivo document examples and schema-path guidance.

---

### Task 1: Add Document Validation Models and Unit Tests

**Files:**
- Create: `tests/runtime/semantic/test_document_validation.py`
- Modify: `marivo/runtime/semantic/import_export.py`

- [ ] **Step 1: Write tests for validation response shape and duplicate names**

Create `tests/runtime/semantic/test_document_validation.py` with:

```python
from __future__ import annotations

from marivo.runtime.semantic.import_export import OsiSemanticDocumentValidator


def _valid_doc() -> dict:
    return {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "commerce",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "analytics.orders",
                        "primary_key": ["order_id"],
                        "custom_extensions": [
                            {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}
                        ],
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_id"}
                                    ]
                                },
                            },
                            {
                                "name": "order_time",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_time"}
                                    ]
                                },
                                "dimension": {"is_time": True},
                            },
                            {
                                "name": "amount",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "amount"}
                                    ]
                                },
                            },
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]
                        },
                        "custom_extensions": [
                            {
                                "vendor_name": "MARIVO",
                                "data": {
                                    "observed_dataset": "orders",
                                    "primary_time_field": "order_time",
                                    "additive_dimensions": ["order_id"],
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_validate_valid_document_returns_summary() -> None:
    result = OsiSemanticDocumentValidator().validate(_valid_doc())

    assert result.valid is True
    assert result.schema_version == "0.1.1"
    assert result.errors == []
    assert result.summary == {
        "models": 1,
        "datasets": 1,
        "fields": 3,
        "metrics": 1,
        "relationships": 0,
    }


def test_validate_duplicate_dataset_names_returns_json_pointer() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"].append(dict(doc["semantic_model"][0]["datasets"][0]))

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert result.errors[0].code == "DUPLICATE_NAME"
    assert result.errors[0].json_pointer == "/semantic_model/0/datasets/1/name"
    assert "orders" in result.errors[0].message
    assert result.errors[0].hint
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_document_validation.py -q
```

Expected: FAIL because `OsiSemanticDocumentValidator` and validation response models do not exist.

- [ ] **Step 3: Add validation result models and basic duplicate validation**

In `marivo/runtime/semantic/import_export.py`, add these imports near the top:

```python
from typing import Literal

from marivo.contracts.generated import OSIDocument
```

Add these models after `ImportOsiDocumentReport`:

```python
class SemanticValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    json_pointer: str
    severity: Literal["error", "warning"] = "error"
    hint: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class OsiSemanticValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    schema_version: str = "0.1.1"
    errors: list[SemanticValidationIssue] = Field(default_factory=list)
    warnings: list[SemanticValidationIssue] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class OsiSemanticDocumentValidator:
    """Validate OSI-Marivo semantic model documents before import."""

    def __init__(self, datasource_service: Any | None = None) -> None:
        self.datasource_service = datasource_service

    def validate(self, document: dict[str, Any]) -> OsiSemanticValidationResult:
        errors: list[SemanticValidationIssue] = []
        try:
            parsed = OSIDocument.model_validate(document)
        except Exception as exc:
            return OsiSemanticValidationResult(
                valid=False,
                schema_version=str(document.get("version") or "0.1.1"),
                errors=[
                    SemanticValidationIssue(
                        code="SCHEMA_VALIDATION_FAILED",
                        message=str(exc),
                        json_pointer="",
                        hint="Update the document to match osi-marivo-spec/schema/osi-marivo.schema.json.",
                    )
                ],
                summary=_summarize_document(document),
            )

        semantic_models = document.get("semantic_model") or []
        errors.extend(_duplicate_name_issues(semantic_models, "semantic model", "/semantic_model"))
        for model_index, model in enumerate(semantic_models):
            if not isinstance(model, dict):
                continue
            model_pointer = f"/semantic_model/{model_index}"
            errors.extend(
                _duplicate_name_issues(
                    model.get("datasets") or [],
                    "dataset",
                    f"{model_pointer}/datasets",
                )
            )
            errors.extend(
                _duplicate_name_issues(
                    model.get("metrics") or [],
                    "metric",
                    f"{model_pointer}/metrics",
                )
            )
            errors.extend(
                _duplicate_name_issues(
                    model.get("relationships") or [],
                    "relationship",
                    f"{model_pointer}/relationships",
                )
            )
            for dataset_index, dataset in enumerate(model.get("datasets") or []):
                if isinstance(dataset, dict):
                    errors.extend(
                        _duplicate_name_issues(
                            dataset.get("fields") or [],
                            "field",
                            f"{model_pointer}/datasets/{dataset_index}/fields",
                        )
                    )

        return OsiSemanticValidationResult(
            valid=not errors,
            schema_version=parsed.version or "0.1.1",
            errors=errors,
            summary=_summarize_document(document),
        )
```

Add helper functions near `_reject_duplicates`:

```python
def _summarize_document(document: dict[str, Any]) -> dict[str, int]:
    models = document.get("semantic_model") if isinstance(document, dict) else []
    if not isinstance(models, list):
        models = []
    dataset_count = 0
    field_count = 0
    metric_count = 0
    relationship_count = 0
    for model in models:
        if not isinstance(model, dict):
            continue
        datasets = model.get("datasets") or []
        metrics = model.get("metrics") or []
        relationships = model.get("relationships") or []
        if isinstance(datasets, list):
            dataset_count += len(datasets)
            for dataset in datasets:
                if isinstance(dataset, dict) and isinstance(dataset.get("fields"), list):
                    field_count += len(dataset["fields"])
        if isinstance(metrics, list):
            metric_count += len(metrics)
        if isinstance(relationships, list):
            relationship_count += len(relationships)
    return {
        "models": len(models),
        "datasets": dataset_count,
        "fields": field_count,
        "metrics": metric_count,
        "relationships": relationship_count,
    }


def _duplicate_name_issues(
    items: object,
    label: str,
    pointer: str,
) -> list[SemanticValidationIssue]:
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    issues: list[SemanticValidationIssue] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized = name.strip()
        if normalized in seen:
            issues.append(
                SemanticValidationIssue(
                    code="DUPLICATE_NAME",
                    message=f"Duplicate {label} name {normalized!r}.",
                    json_pointer=f"{pointer}/{index}/name",
                    hint=f"Rename this {label} or remove the duplicate object.",
                )
            )
        seen.add(normalized)
    return issues
```

- [ ] **Step 4: Run validation tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_document_validation.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add marivo/runtime/semantic/import_export.py tests/runtime/semantic/test_document_validation.py
git commit -m "feat: add semantic document validation models" \
  -m "Introduce structured OSI semantic validation results and duplicate-name checks." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Add Reference, Extension, and Datasource Validation

**Files:**
- Modify: `tests/runtime/semantic/test_document_validation.py`
- Modify: `marivo/runtime/semantic/import_export.py`

- [ ] **Step 1: Add tests for broken references and datasource grounding**

Append to `tests/runtime/semantic/test_document_validation.py`:

```python
class _FakeDatasourceService:
    def __init__(self, *, columns: list[str] | None = None, fail: bool = False) -> None:
        self.columns = columns or ["order_id", "order_time", "amount"]
        self.fail = fail

    def get_datasource(self, datasource_id: str) -> dict:
        if datasource_id != "ds_001" or self.fail:
            raise KeyError(datasource_id)
        return {"datasource_id": datasource_id, "status": "active"}

    def browse_catalog_columns(
        self, datasource_id: str, schema_name: str, table_name: str
    ) -> list[dict]:
        if datasource_id != "ds_001" or schema_name != "analytics" or table_name != "orders":
            raise KeyError((datasource_id, schema_name, table_name))
        return [{"name": column} for column in self.columns]


def test_validate_primary_key_must_reference_dataset_field() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["datasets"][0]["primary_key"] = ["missing_id"]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_FIELD" for issue in result.errors)
    assert any(issue.json_pointer.endswith("/primary_key/0") for issue in result.errors)


def test_validate_relationship_must_reference_known_datasets_and_fields() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["relationships"] = [
        {
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["customer_id"],
        }
    ]

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_DATASET" for issue in result.errors)


def test_validate_metric_extension_references_known_dataset_and_time_field() -> None:
    doc = _valid_doc()
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"][
        "primary_time_field"
    ] = "missing_time"

    result = OsiSemanticDocumentValidator().validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_FIELD" for issue in result.errors)


def test_validate_datasource_grounding_checks_live_columns() -> None:
    doc = _valid_doc()
    service = _FakeDatasourceService(columns=["order_id", "order_time"])

    result = OsiSemanticDocumentValidator(datasource_service=service).validate(doc)

    assert result.valid is False
    assert any(issue.code == "UNKNOWN_PHYSICAL_COLUMN" for issue in result.errors)
    assert any(issue.context.get("column") == "amount" for issue in result.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_document_validation.py -q
```

Expected: FAIL because reference and datasource validation are not implemented.

- [ ] **Step 3: Implement reference validation helpers**

In `marivo/runtime/semantic/import_export.py`, add calls inside `OsiSemanticDocumentValidator.validate()` after duplicate checks for each model:

```python
            errors.extend(_reference_issues(model, model_pointer))
```

Add helpers near the duplicate helpers:

```python
def _reference_issues(model: dict[str, Any], model_pointer: str) -> list[SemanticValidationIssue]:
    issues: list[SemanticValidationIssue] = []
    datasets = model.get("datasets") if isinstance(model.get("datasets"), list) else []
    dataset_fields: dict[str, set[str]] = {}
    for dataset_index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            continue
        dataset_name = str(dataset.get("name") or "")
        fields = dataset.get("fields") if isinstance(dataset.get("fields"), list) else []
        field_names = {
            str(field.get("name"))
            for field in fields
            if isinstance(field, dict) and isinstance(field.get("name"), str)
        }
        dataset_fields[dataset_name] = field_names
        for pk_index, field_name in enumerate(dataset.get("primary_key") or []):
            if field_name not in field_names:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=dataset_name,
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}/primary_key/{pk_index}",
                    )
                )
        for uk_index, unique_key in enumerate(dataset.get("unique_keys") or []):
            for field_index, field_name in enumerate(unique_key):
                if field_name not in field_names:
                    issues.append(
                        _unknown_field_issue(
                            field_name=str(field_name),
                            dataset=dataset_name,
                            json_pointer=(
                                f"{model_pointer}/datasets/{dataset_index}/unique_keys/"
                                f"{uk_index}/{field_index}"
                            ),
                        )
                    )

    relationships = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    for rel_index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            continue
        from_dataset = str(relationship.get("from") or "")
        to_dataset = str(relationship.get("to") or "")
        if from_dataset not in dataset_fields:
            issues.append(
                _unknown_dataset_issue(
                    dataset=from_dataset,
                    json_pointer=f"{model_pointer}/relationships/{rel_index}/from",
                )
            )
        if to_dataset not in dataset_fields:
            issues.append(
                _unknown_dataset_issue(
                    dataset=to_dataset,
                    json_pointer=f"{model_pointer}/relationships/{rel_index}/to",
                )
            )
        for column_index, field_name in enumerate(relationship.get("from_columns") or []):
            if from_dataset in dataset_fields and field_name not in dataset_fields[from_dataset]:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=from_dataset,
                        json_pointer=(
                            f"{model_pointer}/relationships/{rel_index}/from_columns/{column_index}"
                        ),
                    )
                )
        for column_index, field_name in enumerate(relationship.get("to_columns") or []):
            if to_dataset in dataset_fields and field_name not in dataset_fields[to_dataset]:
                issues.append(
                    _unknown_field_issue(
                        field_name=str(field_name),
                        dataset=to_dataset,
                        json_pointer=(
                            f"{model_pointer}/relationships/{rel_index}/to_columns/{column_index}"
                        ),
                    )
                )

    metrics = model.get("metrics") if isinstance(model.get("metrics"), list) else []
    for metric_index, metric in enumerate(metrics):
        if isinstance(metric, dict):
            issues.extend(_metric_extension_issues(metric, metric_index, dataset_fields, model_pointer))
    return issues


def _metric_extension_issues(
    metric: dict[str, Any],
    metric_index: int,
    dataset_fields: dict[str, set[str]],
    model_pointer: str,
) -> list[SemanticValidationIssue]:
    issues: list[SemanticValidationIssue] = []
    marivo = _extract_marivo_datasource_id
    extension_data = _extract_marivo_extension_data(metric)
    if extension_data is None:
        return issues
    observed_dataset = extension_data.get("observed_dataset")
    if isinstance(observed_dataset, str) and observed_dataset not in dataset_fields:
        issues.append(
            _unknown_dataset_issue(
                dataset=observed_dataset,
                json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
            )
        )
        return issues
    primary_time_field = extension_data.get("primary_time_field")
    if (
        isinstance(observed_dataset, str)
        and observed_dataset in dataset_fields
        and isinstance(primary_time_field, str)
        and primary_time_field not in dataset_fields[observed_dataset]
    ):
        issues.append(
            _unknown_field_issue(
                field_name=primary_time_field,
                dataset=observed_dataset,
                json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
            )
        )
    for dimension in extension_data.get("additive_dimensions") or []:
        if (
            isinstance(observed_dataset, str)
            and observed_dataset in dataset_fields
            and isinstance(dimension, str)
            and dimension not in dataset_fields[observed_dataset]
        ):
            issues.append(
                _unknown_field_issue(
                    field_name=dimension,
                    dataset=observed_dataset,
                    json_pointer=f"{model_pointer}/metrics/{metric_index}/custom_extensions",
                )
            )
    return issues


def _unknown_dataset_issue(dataset: str, json_pointer: str) -> SemanticValidationIssue:
    return SemanticValidationIssue(
        code="UNKNOWN_DATASET",
        message=f"Dataset {dataset!r} is not defined in this semantic model.",
        json_pointer=json_pointer,
        hint="Add the dataset to this semantic model or update the reference.",
        context={"dataset": dataset},
    )


def _unknown_field_issue(
    *,
    field_name: str,
    dataset: str,
    json_pointer: str,
) -> SemanticValidationIssue:
    return SemanticValidationIssue(
        code="UNKNOWN_FIELD",
        message=f"Field {field_name!r} is not defined on dataset {dataset!r}.",
        json_pointer=json_pointer,
        hint="Add the field to the dataset or update the reference.",
        context={"dataset": dataset, "field": field_name},
    )


def _extract_marivo_extension_data(obj: dict[str, Any]) -> dict[str, Any] | None:
    for extension in obj.get("custom_extensions") or []:
        if isinstance(extension, dict) and extension.get("vendor_name") == "MARIVO":
            data = extension.get("data")
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
            return data if isinstance(data, dict) else None
    return None
```

- [ ] **Step 4: Implement datasource grounding validation**

In `OsiSemanticDocumentValidator.validate()`, after reference validation, call:

```python
            if self.datasource_service is not None:
                errors.extend(self._datasource_issues(model, model_pointer))
```

Add a method on `OsiSemanticDocumentValidator`:

```python
    def _datasource_issues(
        self,
        model: dict[str, Any],
        model_pointer: str,
    ) -> list[SemanticValidationIssue]:
        assert self.datasource_service is not None
        issues: list[SemanticValidationIssue] = []
        for dataset_index, dataset in enumerate(model.get("datasets") or []):
            if not isinstance(dataset, dict):
                continue
            dataset_name = str(dataset.get("name") or "")
            datasource_id = _extract_marivo_datasource_id(dataset)
            source = str(dataset.get("source") or "")
            parsed_source = DatasourceBinder._parse_source(source)
            if datasource_id is None:
                issues.append(
                    SemanticValidationIssue(
                        code="MISSING_DATASOURCE_ID",
                        message=f"Dataset {dataset_name!r} has no MARIVO datasource_id extension.",
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}/custom_extensions",
                        hint="Add a MARIVO custom extension with data.datasource_id.",
                        context={"dataset": dataset_name},
                    )
                )
                continue
            if parsed_source is None:
                issues.append(
                    SemanticValidationIssue(
                        code="INVALID_DATASET_SOURCE",
                        message=f"Dataset {dataset_name!r} source must be schema.table or catalog.schema.table.",
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}/source",
                        hint="Use a datasource-local relation FQN such as analytics.orders.",
                        context={"dataset": dataset_name, "source": source},
                    )
                )
                continue
            schema_name, table_name = parsed_source
            try:
                self.datasource_service.get_datasource(datasource_id)
                rows = self.datasource_service.browse_catalog_columns(
                    datasource_id,
                    schema_name,
                    table_name,
                )
            except (KeyError, ValueError) as exc:
                issues.append(
                    SemanticValidationIssue(
                        code="DATASOURCE_GROUNDING_FAILED",
                        message=str(exc),
                        json_pointer=f"{model_pointer}/datasets/{dataset_index}",
                        hint="Check datasource_id and dataset.source against live datasource metadata.",
                        context={
                            "dataset": dataset_name,
                            "datasource_id": datasource_id,
                            "schema": schema_name,
                            "table": table_name,
                        },
                    )
                )
                continue
            physical_columns = {
                str(row.get("name") or row.get("column_name") or "")
                for row in rows
                if isinstance(row, dict)
            }
            for field_index, field in enumerate(dataset.get("fields") or []):
                if not isinstance(field, dict):
                    continue
                expression = _first_ansi_expression(field)
                if expression and _looks_like_column_reference(expression):
                    column = expression.strip()
                    if column not in physical_columns:
                        issues.append(
                            SemanticValidationIssue(
                                code="UNKNOWN_PHYSICAL_COLUMN",
                                message=(
                                    f"Field {field.get('name')!r} references physical column "
                                    f"{column!r}, but it is not present on {source!r}."
                                ),
                                json_pointer=(
                                    f"{model_pointer}/datasets/{dataset_index}/fields/"
                                    f"{field_index}/expression"
                                ),
                                hint="Update the field expression or choose an existing datasource column.",
                                context={
                                    "dataset": dataset_name,
                                    "datasource_id": datasource_id,
                                    "schema": schema_name,
                                    "table": table_name,
                                    "column": column,
                                },
                            )
                        )
        return issues
```

Add helpers:

```python
def _first_ansi_expression(field: dict[str, Any]) -> str | None:
    expression = field.get("expression")
    if not isinstance(expression, dict):
        return None
    dialects = expression.get("dialects")
    if not isinstance(dialects, list):
        return None
    for dialect in dialects:
        if not isinstance(dialect, dict):
            continue
        if dialect.get("dialect") == "ANSI_SQL" and isinstance(dialect.get("expression"), str):
            return dialect["expression"]
    return None


def _looks_like_column_reference(expression: str) -> bool:
    stripped = expression.strip()
    return stripped.replace("_", "").isalnum() and "." not in stripped
```

- [ ] **Step 5: Run validation tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_document_validation.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add marivo/runtime/semantic/import_export.py tests/runtime/semantic/test_document_validation.py
git commit -m "feat: validate semantic document references" \
  -m "Validate OSI semantic references, Marivo extensions, and datasource grounding before import." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 3: Make Import Validate First and Replace Whole Model Graphs

**Files:**
- Modify: `tests/runtime/semantic/test_import_export.py`
- Modify: `marivo/runtime/semantic/import_export.py`
- Modify: `marivo/runtime/semantic/semantic_service.py`

- [ ] **Step 1: Add tests for replace semantics and validation-before-write**

In `tests/runtime/semantic/test_import_export.py`, add tests using the existing fixture style. If the file already has helpers for metadata stores and documents, reuse those helper names. Add this test logic:

```python
def test_import_replaces_removed_child_objects(store, datasource_service) -> None:
    service = SemanticModelV2Service(store, datasource_service=datasource_service)
    original = _document_with_orders_model(fields=["order_id", "order_time", "amount"])
    replacement = _document_with_orders_model(fields=["order_id", "order_time"])

    service.import_osi_semantic_models(original)
    service.import_osi_semantic_models(replacement)

    exported = service.export_osi_semantic_models("commerce")
    fields = exported["semantic_model"][0]["datasets"][0]["fields"]
    assert [field["name"] for field in fields] == ["order_id", "order_time"]


def test_import_does_not_write_when_validation_fails(store, datasource_service) -> None:
    service = SemanticModelV2Service(store, datasource_service=datasource_service)
    valid_doc = _document_with_orders_model(fields=["order_id", "order_time", "amount"])
    invalid_doc = _document_with_orders_model(fields=["order_id", "order_time", "amount"])
    invalid_doc["semantic_model"][0]["datasets"][0]["primary_key"] = ["missing"]

    service.import_osi_semantic_models(valid_doc)
    result = service.import_osi_semantic_models(invalid_doc)

    assert result["valid"] is False
    exported = service.export_osi_semantic_models("commerce")
    fields = exported["semantic_model"][0]["datasets"][0]["fields"]
    assert [field["name"] for field in fields] == ["order_id", "order_time", "amount"]
```

If `tests/runtime/semantic/test_import_export.py` does not currently define `store`, `datasource_service`, `_document_with_orders_model`, or `SemanticModelV2Service`, add explicit imports and helper code equivalent to the existing tests in that file. Keep helper names local to that file.

- [ ] **Step 2: Run targeted import/export tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_import_export.py -q
```

Expected: FAIL because whole-model replacement and/or `import_osi_semantic_models` do not exist yet.

- [ ] **Step 3: Change merge executor to replace child graph**

In `marivo/runtime/semantic/import_export.py`, update `SemanticMergeExecutor._merge_model()` after `model_id = int(row["model_id"])` and before iterating datasets:

```python
        self._delete_model_children(txn, model_id)
```

Add this method to `SemanticMergeExecutor`:

```python
    def _delete_model_children(self, txn: MetadataTransaction, model_id: int) -> None:
        dataset_rows = txn.query_rows(
            "SELECT dataset_id FROM semantic_datasets WHERE model_id = ?",
            [model_id],
        )
        for dataset_row in dataset_rows:
            txn.execute(
                "DELETE FROM semantic_fields WHERE dataset_id = ?",
                [dataset_row["dataset_id"]],
            )
        txn.execute("DELETE FROM semantic_metrics WHERE model_id = ?", [model_id])
        txn.execute("DELETE FROM semantic_relationships WHERE model_id = ?", [model_id])
        txn.execute("DELETE FROM semantic_datasets WHERE model_id = ?", [model_id])
```

Because children are deleted first, `_merge_dataset`, `_replace_metric`, and `_replace_relationship` will count imported children as created. Keep the counters simple; replacement semantics matter more than preserving update counters.

- [ ] **Step 4: Add service methods for validate/import/export semantic models**

In `marivo/runtime/semantic/semantic_service.py`, import validation result helpers:

```python
    OsiSemanticDocumentValidator,
    OsiSemanticValidationResult,
```

Add these methods near existing import/export methods:

```python
    def validate_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        result = OsiSemanticDocumentValidator(
            datasource_service=self.datasource_service
        ).validate(doc_data)
        return result.model_dump()

    def import_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        validation = OsiSemanticDocumentValidator(
            datasource_service=self.datasource_service
        ).validate(doc_data)
        if not validation.valid:
            return validation.model_dump()
        owner_user = require_user()
        planner = SemanticMergePlanner(DatasourceBinder(self.datasource_service))
        plan = planner.preflight(doc_data)
        report = SemanticMergeExecutor(self.store).execute(
            document=plan.document,
            owner_user=owner_user,
            bindings=plan.bindings,
        )
        response = validation.model_dump()
        response["import_report"] = report.model_dump()
        return response

    def export_osi_semantic_models(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        owner_user = require_user()
        return OsiDocumentExporter(self.store).export(
            owner_user=owner_user,
            semantic_model_name=semantic_model_name,
        )
```

Keep the existing `import_osi_document` and `export_osi_document` temporarily by delegating to the new names so intermediate tests remain easier to migrate:

```python
    def import_osi_document(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        return self.import_osi_semantic_models(doc_data)

    def export_osi_document(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        return self.export_osi_semantic_models(semantic_model_name)
```

These aliases are removed from external transports in later tasks.

- [ ] **Step 5: Run targeted import/export tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic/test_document_validation.py tests/runtime/semantic/test_import_export.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add marivo/runtime/semantic/import_export.py marivo/runtime/semantic/semantic_service.py tests/runtime/semantic/test_import_export.py
git commit -m "feat: replace semantic models from documents" \
  -m "Validate semantic documents before import and replace same-name model graphs transactionally." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 4: Reduce HTTP Semantic API to Document Surface

**Files:**
- Modify: `tests/test_semantic_v2_api.py`
- Modify: `marivo/adapters/server/semantic_service_adapter.py`
- Modify: `marivo/transports/http/semantic_v2.py`

- [ ] **Step 1: Replace HTTP API route tests**

In `tests/test_semantic_v2_api.py`, replace CRUD test classes with tests that assert the compact surface. Keep existing app/store helper setup. Add:

```python
def test_http_semantic_routes_expose_document_surface_only() -> None:
    client = _make_app()
    paths = {route.path for route in client.app.routes}

    assert "/semantic-models" in paths
    assert "/semantic-models/import" in paths
    assert "/semantic-models/export" in paths
    assert "/semantic-models/validate" in paths
    assert "/semantic-models/{model}" in paths
    assert "/semantic-models/{model}/datasets" not in paths
    assert "/semantic-models/{model}/datasets/{name}" not in paths
    assert "/semantic-models/{model}/metrics" not in paths
    assert "/semantic-models/{model}/relationships" not in paths
    assert "/semantic-models/{model}/readiness" not in paths


def test_validate_endpoint_returns_structured_result() -> None:
    client = _make_app()
    doc = {"version": OSI_SPEC_VERSION, "semantic_model": []}

    response = client.post(
        "/semantic-models/validate",
        json=doc,
        headers={"X-Marivo-User": "alice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["schema_version"] == OSI_SPEC_VERSION
    assert "errors" in body
    assert "summary" in body


def test_import_endpoint_returns_validation_result_and_writes_valid_document() -> None:
    client = _make_app()
    doc = {"version": OSI_SPEC_VERSION, "semantic_model": [_make_model_dict("commerce")]}

    response = client.post(
        "/semantic-models/import",
        json=doc,
        headers={"X-Marivo-User": "alice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["import_report"]["models"][0]["name"] == "commerce"

    get_response = client.get(
        "/semantic-models/commerce",
        headers={"X-Marivo-User": "alice"},
    )
    assert get_response.status_code == 200
    assert get_response.json()["semantic_model"][0]["name"] == "commerce"


def test_export_endpoint_returns_document() -> None:
    client = _make_app()
    doc = {"version": OSI_SPEC_VERSION, "semantic_model": [_make_model_dict("commerce")]}
    client.post(
        "/semantic-models/import",
        json=doc,
        headers={"X-Marivo-User": "alice"},
    )

    response = client.get(
        "/semantic-models/export",
        params={"semantic_model_name": "commerce"},
        headers={"X-Marivo-User": "alice"},
    )

    assert response.status_code == 200
    assert response.json()["semantic_model"][0]["name"] == "commerce"
```

If `_make_model_dict()` currently creates datasource-bound models that validation cannot ground because the datasource service lacks registered metadata, adjust `_make_app()` or test helper setup to register a minimal datasource fixture before import using the repository's existing datasource test helper pattern.

- [ ] **Step 2: Run HTTP semantic tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py -q
```

Expected: FAIL because old routes still exist and `/validate` is missing.

- [ ] **Step 3: Reduce adapter methods**

In `marivo/adapters/server/semantic_service_adapter.py`, keep `get_semantic_model()` and `list_semantic_models()`. Add:

```python
    def validate_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        return _translate(lambda: self._service.validate_osi_semantic_models(doc_data))

    def import_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        return _translate(lambda: self._service.import_osi_semantic_models(doc_data))

    def export_osi_semantic_models(
        self,
        semantic_model_name: str | None = None,
    ) -> dict[str, Any]:
        return _translate(
            lambda: self._service.export_osi_semantic_models(semantic_model_name)
        )
```

Delete adapter methods for create/update/delete semantic model, dataset CRUD, field CRUD, relationship CRUD, metric CRUD, and readiness. If another non-transport caller breaks during tests, move that caller to document import/export rather than reintroducing adapter CRUD.

- [ ] **Step 4: Reduce HTTP router**

In `marivo/transports/http/semantic_v2.py`, remove imports for CRUD-only models:

```python
AIContextObject, Dataset, Dimension, Expression, Metric, Relationship, SemanticModel, OsiField
```

Keep `OSIDocument` and `OSI_SPEC_VERSION`. Add a response model import if it lives in runtime:

```python
from marivo.runtime.semantic.import_export import OsiSemanticValidationResult
```

Delete `SemanticModelReadinessResponse`, `SemanticModelUpdateRequest`, `DatasetUpdateRequest`, and `FieldUpdateRequest`.

Keep `list_semantic_models()` and `get_semantic_model()`. Replace import/export and add validate:

```python
@router.post("/validate", response_model=OsiSemanticValidationResult)
def validate_osi_semantic_models(request: Request, payload: OSIDocument) -> OsiSemanticValidationResult:
    svc = _get_service(request)
    result = _run(lambda: svc.validate_osi_semantic_models(_dump_model(payload)))
    return OsiSemanticValidationResult.model_validate(result)


@router.post("/import", response_model=OsiSemanticValidationResult)
def import_osi_semantic_models(request: Request, payload: OSIDocument) -> OsiSemanticValidationResult:
    svc = _get_service(request)
    result = _run(lambda: svc.import_osi_semantic_models(_dump_model(payload)))
    return OsiSemanticValidationResult.model_validate(result)


@router.get("/export", response_model=OSIDocument)
def export_osi_semantic_models(
    request: Request,
    semantic_model_name: str | None = None,
) -> OSIDocument:
    svc = _get_service(request)
    result = _run(lambda: svc.export_osi_semantic_models(semantic_model_name))
    return OSIDocument.model_validate(result)
```

If Pydantic rejects `import_report` as an extra field on `OsiSemanticValidationResult`, split response models:

```python
class ImportOsiSemanticModelsResponse(OsiSemanticValidationResult):
    import_report: dict[str, Any] | None = None
```

Then use `response_model=ImportOsiSemanticModelsResponse` for import.

Delete all route functions below `get_semantic_model()` for CRUD/readiness.

- [ ] **Step 5: Run HTTP semantic tests**

Run:

```bash
.venv/bin/pytest tests/test_semantic_v2_api.py tests/runtime/semantic/test_document_validation.py tests/runtime/semantic/test_import_export.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add marivo/adapters/server/semantic_service_adapter.py marivo/transports/http/semantic_v2.py tests/test_semantic_v2_api.py
git commit -m "feat: simplify semantic HTTP surface" \
  -m "Expose semantic list, get, validate, import, and export while removing CRUD routes." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 5: Reduce MCP Semantic Tools and Add File-Based JSON Inputs

**Files:**
- Modify: `tests/transports/mcp/test_tool_parity.py`
- Modify: `marivo/transports/mcp/tools/schemas.py`
- Modify: `marivo/transports/mcp/tools/semantic.py`

- [ ] **Step 1: Replace MCP inventory tests**

In `tests/transports/mcp/test_tool_parity.py`, update `_FakeSvc` to include only semantic methods:

```python
    def validate_osi_semantic_models(self, **kw):
        return {"valid": True, "schema_version": "0.1.1", "errors": [], "warnings": [], "summary": {}}

    def import_osi_semantic_models(self, **kw):
        return {"valid": True, "schema_version": "0.1.1", "errors": [], "warnings": [], "summary": {}}

    def export_osi_semantic_models(self, **kw):
        return {"version": "0.1.1", "semantic_model": []}
```

Replace `test_semantic_tools_include_import_export_and_field_crud()` with:

```python
def test_semantic_tools_expose_document_surface_only() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)

    expected = {
        "list_semantic_models",
        "get_semantic_model",
        "validate_osi_semantic_models",
        "import_osi_semantic_models",
        "export_osi_semantic_models",
    }
    removed = {
        "create_semantic_model",
        "update_semantic_model",
        "delete_semantic_model",
        "get_semantic_model_readiness",
        "create_dataset",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "delete_dataset",
        "create_field",
        "list_fields",
        "get_field",
        "update_field",
        "delete_field",
        "create_metric",
        "list_metrics",
        "get_metric",
        "update_metric",
        "delete_metric",
        "create_relationship",
        "list_relationships",
        "get_relationship",
        "update_relationship",
        "delete_relationship",
    }
    assert expected.issubset(tools)
    assert removed.isdisjoint(tools)
```

Replace `test_mcp_semantic_tools_do_not_expose_requesting_user()` with:

```python
def test_mcp_semantic_document_tools_have_file_or_document_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    validate_props = tools["validate_osi_semantic_models"].parameters["properties"]
    import_props = tools["import_osi_semantic_models"].parameters["properties"]
    export_props = tools["export_osi_semantic_models"].parameters["properties"]

    assert {"document", "input_path"}.issubset(validate_props)
    assert {"document", "input_path"}.issubset(import_props)
    assert "output_path" in export_props
    assert "requesting_user" not in tools["list_semantic_models"].parameters.get("properties", {})
    assert "requesting_user" not in tools["get_semantic_model"].parameters.get("properties", {})
```

- [ ] **Step 2: Run MCP parity tests to verify failure**

Run:

```bash
.venv/bin/pytest tests/transports/mcp/test_tool_parity.py -q
```

Expected: FAIL because old tools are still registered.

- [ ] **Step 3: Add MCP document IO schemas**

In `marivo/transports/mcp/tools/schemas.py`, replace semantic CRUD payload aliases with:

```python
class McpOsiSemanticDocumentInput(BaseModel):
    """MCP input that accepts either an inline OSI document or a local JSON file path."""

    model_config = ConfigDict(extra="forbid")

    document: OSIDocument | None = Field(
        default=None,
        description="Inline OSI-Marivo document. Mutually exclusive with input_path.",
    )
    input_path: str | None = Field(
        default=None,
        description="Local JSON file path containing an OSI-Marivo document.",
    )

    @model_validator(mode="after")
    def _require_one_input(self) -> McpOsiSemanticDocumentInput:
        if (self.document is None) == (self.input_path is None):
            raise ValueError("Provide exactly one of document or input_path.")
        return self


class McpOsiSemanticExportInput(BaseModel):
    """MCP export input for optional model selection and file output."""

    model_config = ConfigDict(extra="forbid")

    semantic_model_name: str | None = Field(default=None)
    output_path: str | None = Field(
        default=None,
        description="Optional local JSON file path to write the exported OSI-Marivo document.",
    )
```

Keep `McpOsiDocumentPayload` only if non-semantic tools still import it. Remove unused CRUD aliases after updating imports.

- [ ] **Step 4: Reduce MCP semantic tool registration**

Replace `marivo/transports/mcp/tools/semantic.py` contents with:

```python
"""Registration functions for MCP semantic model document tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marivo.identity import resolve_user
from marivo.transports.mcp.tools._async_bridge import call_runtime
from marivo.transports.mcp.tools.schemas import (
    McpOsiSemanticDocumentInput,
    McpOsiSemanticExportInput,
)


def _read_document(payload: McpOsiSemanticDocumentInput) -> dict[str, Any]:
    if payload.document is not None:
        return payload.document.model_dump(by_alias=True)
    assert payload.input_path is not None
    with Path(payload.input_path).expanduser().open(encoding="utf-8") as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise ValueError("input_path must contain a JSON object OSI-Marivo document")
    return parsed


def _write_document(path: str, document: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def register_semantic_tools(server: Any, runtime: Any) -> None:
    svc = runtime.get_service("semantic_v2")

    @server.tool()  # type: ignore
    async def list_semantic_models() -> dict[str, Any]:
        """List semantic models via GET /semantic-models."""
        return await call_runtime(svc.list_semantic_models, requesting_user=resolve_user())

    @server.tool()  # type: ignore
    async def get_semantic_model(model: str) -> dict[str, Any]:
        """Get a semantic model as an OSI document via GET /semantic-models/{model}."""
        return await call_runtime(
            svc.get_semantic_model,
            name=model,
            requesting_user=resolve_user(),
        )

    @server.tool()  # type: ignore
    async def validate_osi_semantic_models(payload: McpOsiSemanticDocumentInput) -> dict[str, Any]:
        """Validate an inline or file-based OSI-Marivo semantic model document."""
        return await call_runtime(
            svc.validate_osi_semantic_models,
            doc_data=_read_document(payload),
        )

    @server.tool()  # type: ignore
    async def import_osi_semantic_models(payload: McpOsiSemanticDocumentInput) -> dict[str, Any]:
        """Validate and import an inline or file-based OSI-Marivo semantic model document."""
        return await call_runtime(
            svc.import_osi_semantic_models,
            doc_data=_read_document(payload),
        )

    @server.tool()  # type: ignore
    async def export_osi_semantic_models(payload: McpOsiSemanticExportInput) -> dict[str, Any]:
        """Export OSI-Marivo semantic models, optionally writing the JSON document to a file."""
        document = await call_runtime(
            svc.export_osi_semantic_models,
            semantic_model_name=payload.semantic_model_name,
        )
        if payload.output_path is not None:
            _write_document(payload.output_path, document)
        return document
```

- [ ] **Step 5: Run MCP parity tests**

Run:

```bash
.venv/bin/pytest tests/transports/mcp/test_tool_parity.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add marivo/transports/mcp/tools/schemas.py marivo/transports/mcp/tools/semantic.py tests/transports/mcp/test_tool_parity.py
git commit -m "feat: simplify semantic MCP tools" \
  -m "Expose document validate, import, and export tools with inline and file-based JSON support." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 6: Remove External CRUD Service Methods and Update Callers

**Files:**
- Modify: `marivo/runtime/semantic/semantic_service.py`
- Modify: tests that still reference removed methods

- [ ] **Step 1: Find remaining external CRUD references**

Run:

```bash
rg "create_semantic_model|update_semantic_model|delete_semantic_model|create_dataset|list_datasets|get_dataset|update_dataset|delete_dataset|create_field|list_fields|get_field|update_field|delete_field|create_metric|list_metrics|get_metric|update_metric|delete_metric|create_relationship|list_relationships|get_relationship|update_relationship|delete_relationship|get_readiness" marivo tests -g '*.py'
```

Expected: remaining references are either in `SemanticModelV2Service` definitions or tests that must be migrated.

- [ ] **Step 2: Migrate tests away from removed service methods**

For any test that creates semantic objects through CRUD methods, replace setup with `import_osi_semantic_models()`:

```python
service.import_osi_semantic_models(
    {
        "version": "0.1.1",
        "semantic_model": [
            {
                "name": "commerce",
                "datasets": [
                    {
                        "name": "orders",
                        "source": "analytics.orders",
                        "primary_key": ["order_id"],
                        "custom_extensions": [
                            {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}
                        ],
                        "fields": [
                            {
                                "name": "order_id",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "order_id"}
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
)
```

For tests that assert CRUD behavior itself, delete the test or replace it with document import/export behavior. Do not keep CRUD methods only to satisfy old tests.

- [ ] **Step 3: Delete CRUD methods from service**

In `marivo/runtime/semantic/semantic_service.py`, remove public methods for:

```text
create_semantic_model
update_semantic_model
delete_semantic_model
create_dataset
get_dataset
list_datasets
update_dataset
delete_dataset
create_field
get_field
list_fields
update_field
delete_field
create_relationship
get_relationship
list_relationships
update_relationship
delete_relationship
create_metric
get_metric
list_metrics
update_metric
delete_metric
get_readiness
```

Keep private helpers used by list/get/import/export, especially `_get_model_row_by_name`, `_require_visible_model`, `_assemble_model`, and Marivo extension parsing helpers if validation/import still use them.

- [ ] **Step 4: Run semantic-focused tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic tests/test_semantic_v2_api.py tests/transports/mcp/test_tool_parity.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add marivo/runtime/semantic/semantic_service.py tests
git commit -m "refactor: remove semantic CRUD service surface" \
  -m "Keep semantic service writes document-based and migrate tests away from removed CRUD methods." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 7: Update Documentation and Semantic-Layer Skill

**Files:**
- Modify: `docs/api/semantic.md`
- Modify: `marivo-skill/marivo-semantic-layer/SKILL.md`
- Modify: `marivo-skill/marivo-semantic-layer/references/modeling.md`
- Optional modify: `marivo-skill/marivo-semantic-layer/references/readiness.md` if it points agents to removed readiness tool as a semantic-layer management step.

- [ ] **Step 1: Update API docs**

In `docs/api/semantic.md`, replace the endpoint table with:

```markdown
## OSI Semantic Model Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/semantic-models` | List semantic models visible to the requester |
| `GET` | `/semantic-models/{model}` | Get one semantic model as an OSI document |
| `POST` | `/semantic-models/validate` | Validate an OSI-Marivo semantic model document |
| `POST` | `/semantic-models/import` | Validate and import an OSI-Marivo semantic model document |
| `GET` | `/semantic-models/export` | Export visible semantic models, optionally filtered by `semantic_model_name` |
```

Add a section:

```markdown
## Document-First Authoring Workflow

1. Inspect datasource metadata through datasource browse endpoints.
2. Draft an OSI-Marivo JSON document that contains the complete semantic model graph.
3. Validate the document with `/semantic-models/validate`.
4. Fix validation errors using `json_pointer` and `hint`.
5. Import the validated document with `/semantic-models/import`.
6. Confirm the stored model with `GET /semantic-models/{model}` or `/semantic-models/export`.

The removed dataset, field, metric, relationship, readiness, and model mutation endpoints are not
part of the semantic-layer management surface. The import document is the source of truth.
```

Keep the existing OSI JSON examples, but remove owner/visibility examples from agent-authored documents if they conflict with current identity rules.

- [ ] **Step 2: Rewrite skill routing**

In `marivo-skill/marivo-semantic-layer/SKILL.md`, replace the old tool choice section with:

```markdown
## Choose The Next Tool

- Need physical metadata before authoring: use `marivo-datasource`.
- Need current semantic state: `marivo-list_semantic_models`, `marivo-get_semantic_model`, or
  `marivo-export_osi_semantic_models`.
- Need to check a draft: `marivo-validate_osi_semantic_models`.
- Draft is validated and user approved it: `marivo-import_osi_semantic_models`.
- Reusable graph is imported and now needs a representative run: switch to `marivo-analysis`.
```

Replace the staged CRUD build section with:

```markdown
## Document-First Build With Mandatory User Confirmation

The agent drafts a complete OSI-Marivo JSON document, validates it, fixes validation errors, and only
imports it after explicit user confirmation. Do not create datasets, fields, metrics, or
relationships through separate CRUD tools.

1. Collect business knowledge before technical work.
2. Browse datasource metadata to choose physical grounding.
3. Draft the full OSI-Marivo JSON document in a file for non-trivial models.
4. Run `marivo-validate_osi_semantic_models` with `input_path` or inline `document`.
5. Fix every validation error using `json_pointer`, `message`, and `hint`.
6. Repeat validation until `valid: true`.
7. Present the validated document summary to the user and wait for approval.
8. Run `marivo-import_osi_semantic_models`.
9. Confirm with `marivo-get_semantic_model` or `marivo-export_osi_semantic_models`.
```

Remove references to `marivo-create_dataset`, `marivo-create_metric`, `marivo-create_relationship`, `marivo-update_*`, and `marivo-get_semantic_model_readiness`.

- [ ] **Step 3: Rewrite modeling reference examples**

In `marivo-skill/marivo-semantic-layer/references/modeling.md`, replace the Tool Routing table with document tools and add this section:

```markdown
## OSI-Marivo Schema Reference

The canonical JSON Schema lives at:

osi-marivo-spec/schema/osi-marivo.schema.json

Generated Python models live under:

marivo/contracts/generated/

Use the schema and the examples below before drafting. Use validation feedback, especially
`json_pointer` and `hint`, to repair drafts.
```

Replace CRUD examples with a JSON document example:

```json
{
  "version": "0.1.1",
  "semantic_model": [
    {
      "name": "video_analytics",
      "datasets": [
        {
          "name": "watch_events",
          "source": "main.watch_events",
          "primary_key": ["event_id"],
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {"datasource_id": "ds_local"}
            }
          ],
          "fields": [
            {
              "name": "event_id",
              "expression": {
                "dialects": [
                  {"dialect": "ANSI_SQL", "expression": "event_id"}
                ]
              }
            },
            {
              "name": "event_time",
              "expression": {
                "dialects": [
                  {"dialect": "ANSI_SQL", "expression": "event_time"}
                ]
              },
              "dimension": {"is_time": true}
            },
            {
              "name": "watch_seconds",
              "expression": {
                "dialects": [
                  {"dialect": "ANSI_SQL", "expression": "watch_seconds"}
                ]
              }
            }
          ]
        }
      ],
      "metrics": [
        {
          "name": "watch_time_seconds",
          "expression": {
            "dialects": [
              {"dialect": "ANSI_SQL", "expression": "SUM(watch_seconds)"}
            ]
          },
          "description": "Total watch time in seconds",
          "custom_extensions": [
            {
              "vendor_name": "MARIVO",
              "data": {
                "observed_dataset": "watch_events",
                "primary_time_field": "event_time",
                "additive_dimensions": ["event_id"]
              }
            }
          ]
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Search docs and skill for removed tool names**

Run:

```bash
rg "create_semantic_model|create_dataset|create_field|create_metric|create_relationship|update_semantic_model|update_dataset|update_field|update_metric|update_relationship|get_semantic_model_readiness|readiness" docs/api marivo-skill/marivo-semantic-layer -n
```

Expected: No matches for removed CRUD tools. If `readiness` remains only as background conceptual documentation outside semantic-layer management workflow, keep it only if it does not instruct agents to call a removed tool.

- [ ] **Step 5: Commit**

Run:

```bash
git add docs/api/semantic.md marivo-skill/marivo-semantic-layer/SKILL.md marivo-skill/marivo-semantic-layer/references/modeling.md marivo-skill/marivo-semantic-layer/references/readiness.md
git commit -m "docs: document semantic JSON workflow" \
  -m "Update semantic API and skill guidance for document-first validation and import." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

If `references/readiness.md` is unchanged, omit it from `git add`.

### Task 8: Final Surface and Regression Verification

**Files:**
- Modify only files needed to fix failures found by verification.

- [ ] **Step 1: Run semantic and MCP focused tests**

Run:

```bash
.venv/bin/pytest tests/runtime/semantic tests/test_semantic_v2_api.py tests/transports/mcp/test_tool_parity.py tests/transports/mcp/test_stdio_mcp_e2e.py tests/transports/mcp/test_http_mcp_e2e.py -q
```

Expected: PASS.

- [ ] **Step 2: Run repository checks**

Run:

```bash
make test
make typecheck
make lint
```

Expected: all checks pass.

- [ ] **Step 3: Verify removed route/tool names**

Run:

```bash
rg "create_dataset|create_field|create_metric|create_relationship|get_semantic_model_readiness|/datasets|/metrics|/relationships|/readiness" marivo/transports docs/api/semantic.md marivo-skill/marivo-semantic-layer -n
```

Expected: no removed semantic management routes/tools remain in transport or agent-facing docs. If the command finds unrelated datasource or analysis strings, inspect and confirm they are not semantic CRUD leftovers.

- [ ] **Step 4: Commit any verification fixes**

If verification required code or docs fixes, stage the known implementation areas and run:

```bash
git add marivo tests docs/api marivo-skill
git commit -m "fix: complete semantic document surface migration" \
  -m "Address final verification issues after removing semantic CRUD surfaces." \
  -m "Co-Authored-By: Copilot CLI:gpt-5.5 [Edit] [Bash]" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

If no fixes were needed, do not create an empty commit.

## Self-Review Notes

- Spec coverage: tasks cover compact HTTP/MCP surface, inline/file stdio inputs, full validation, whole-model replacement import, old CRUD removal, docs, skill references, and final verification.
- No compatibility aliases are planned for external CRUD tools/routes.
- The plan uses repository-approved commands and avoids bare `python`, `pytest`, `mypy`, and `ruff`.
- The execution phase should use a fresh isolated worktree before code changes if the current workspace has unrelated user edits.
