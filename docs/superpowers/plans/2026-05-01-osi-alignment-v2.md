---
status: completed
created: 2026-05-01
---

# OSI Alignment V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Marivo's semantic layer with OSI-aligned objects (SemanticModel, Dataset, Field, Relationship, Metric) with MARIVO extensions in custom_extensions, deleting all legacy objects and tables.

**Architecture:** Three-layer boundary: OSI wire format (API I/O) → MARIVO extension parsing → Internal SQLite/MySQL storage. Destructive update — no migration, no backwards compatibility. Old tables dropped, old code deleted.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic v2, SQLite (primary metadata), MySQL (alternate metadata), DuckDB/Trino (analytics)

---

## File Structure

### New Files (Create)

| File | Responsibility |
|---|---|
| `app/api/models/osi.py` | OSI core Pydantic models: Expression, AIContext, CustomExtension, Field, Dataset, Relationship, Metric, SemanticModel, OSIDocument |
| `app/api/models/marivo_extensions.py` | MARIVO extension Pydantic models: visibility/owner_user, datasource_id, data_type, cardinality, additivity/filters |
| `app/semantic_service_v2/__init__.py` | Package init |
| `app/semantic_service_v2/extensions.py` | custom_extensions parsing/serialization: extract_marivo_extension(), build_custom_extensions() |
| `app/semantic_service_v2/validation.py` | Write-time validation: all checks from spec Section 7.1 |
| `app/semantic_service_v2/service.py` | SemanticModelV2Service: CRUD for models, datasets, relationships, metrics, import, readiness |
| `app/semantic_service_v2/storage.py` | OSI↔storage row mapping functions |
| `app/api/semantic_v2.py` | New FastAPI router with OSI-aligned endpoints |
| `tests/test_osi_models.py` | Tests for OSI Pydantic models and MARIVO extension parsing |
| `tests/test_semantic_v2_service.py` | Tests for new service CRUD + validation |
| `tests/test_semantic_v2_api.py` | Tests for new API endpoints |
| `tests/test_semantic_v2_readiness.py` | Tests for readiness endpoint |
| `tests/test_semantic_v2_import.py` | Tests for import endpoint |

### Modified Files

| File | Change |
|---|---|
| `app/storage/schema.py` | Replace old semantic DDL with new OSI-aligned tables, drop old tables |
| `app/api/router.py` | Replace `semantic.router` with `semantic_v2.router` |
| `app/api/app_factory.py` | Wire new service |
| `app/api/deps.py` | New dependency injection for SemanticModelV2Service |
| `app/service.py` | Update imports from new semantic_service_v2 and semantic_runtime_v2 |

### Deleted Files (after new code is working)

| File/Directories | Reason |
|---|---|
| `app/api/models/entity.py` | Replaced by osi.py |
| `app/api/models/metric.py` | Replaced by osi.py |
| `app/api/models/dimension.py` | Collapsed into Field |
| `app/api/models/time.py` | Collapsed into Field |
| `app/api/models/binding.py` | Inlined into Dataset |
| `app/api/models/predicate.py` | Replaced by metric filters |
| `app/api/models/process_object.py` | Deleted |
| `app/api/models/enum_set.py` | Deleted |
| `app/api/models/compatibility_profile.py` | Deleted |
| `app/api/models/domain.py` | Replaced by SemanticModel |
| `app/api/models/semantic_batch.py` | Deleted |
| `app/api/models/catalog.py` | Deleted |
| `app/api/semantic.py` | Replaced by semantic_v2.py |
| `app/semantic_service/` (entire directory) | Replaced by semantic_service_v2/ |
| `app/semantic_revision/` (entire directory) | Revision removed |
| `app/semantic_readiness/` (entire directory) | Replaced by readiness in semantic_service_v2 |
| `app/semantic_runtime/` (entire directory) | Replaced by semantic_runtime_v2/ |
| `app/analysis_core/capability_profiles.py` | Deleted |
| `app/analysis_core/predicate_validator.py` | Deleted |
| `app/analysis_core/predicate_lowering_boundary.py` | Deleted |
| All `tests/test_semantic_*.py` (old) | Replaced by new test files |
| All `tests/test_api_models_*.py` (old semantic) | Replaced by new test files |

---

## Task 1: OSI Core Pydantic Models

**Files:**
- Create: `app/api/models/osi.py`
- Test: `tests/test_osi_models.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for OSI core Pydantic models and MARIVO extension parsing."""
from __future__ import annotations

import json
import pytest
from pydantic import ValidationError


def test_expression_single_dialect():
    from app.api.models.osi import Expression, DialectExpression

    expr = Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_date_sk")])
    assert len(expr.dialects) == 1
    assert expr.dialects[0].dialect == "ANSI_SQL"
    assert expr.dialects[0].expression == "ss_sold_date_sk"


def test_expression_requires_at_least_one_dialect():
    from app.api.models.osi import Expression

    with pytest.raises(ValidationError):
        Expression(dialects=[])


def test_ai_context_string_form():
    from app.api.models.osi import AIContext

    ctx = AIContext(root="Use this model for retail analytics")
    assert ctx.root == "Use this model for retail analytics"


def test_ai_context_object_form():
    from app.api.models.osi import AIContext

    ctx = AIContext(root={
        "instructions": "Use this for retail",
        "synonyms": ["retail", "store sales"],
        "examples": ["Show me sales by region"],
    })
    assert isinstance(ctx.root, dict)


def test_field_minimal():
    from app.api.models.osi import Field, Expression, DialectExpression

    field = Field(
        name="ss_sold_date_sk",
        expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_date_sk")]),
    )
    assert field.name == "ss_sold_date_sk"
    assert field.dimension is None
    assert field.custom_extensions is None


def test_field_with_dimension_is_time():
    from app.api.models.osi import Field, Expression, DialectExpression, Dimension

    field = Field(
        name="ss_sold_time",
        expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="ss_sold_time_sk")]),
        dimension=Dimension(is_time=True),
    )
    assert field.dimension is not None
    assert field.dimension.is_time is True


def test_dataset_minimal():
    from app.api.models.osi import Dataset

    ds = Dataset(name="store_sales", source="tpcds.public.store_sales")
    assert ds.name == "store_sales"
    assert ds.fields is None


def test_relationship_requires_columns():
    from app.api.models.osi import Relationship

    rel = Relationship(
        name="store_sales_to_date",
        from_="store_sales",
        to="date_dim",
        from_columns=["ss_sold_date_sk"],
        to_columns=["d_date_sk"],
    )
    assert rel.from_ == "store_sales"


def test_metric_minimal():
    from app.api.models.osi import Metric, Expression, DialectExpression

    metric = Metric(
        name="total_sales",
        expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(ss_ext_sales_price)")]),
    )
    assert metric.name == "total_sales"


def test_semantic_model_requires_datasets():
    from app.api.models.osi import SemanticModel

    with pytest.raises(ValidationError):
        SemanticModel(name="retail", datasets=[])


def test_osi_document_structure():
    from app.api.models.osi import OSIDocument

    doc = OSIDocument(version="0.1.1", semantic_model=[])
    assert doc.version == "0.1.1"


def test_osi_document_version_must_be_011():
    from app.api.models.osi import OSIDocument

    with pytest.raises(ValidationError):
        OSIDocument(version="0.2.0", semantic_model=[])


def test_custom_extension_structure():
    from app.api.models.osi import CustomExtension

    ext = CustomExtension(vendor_name="MARIVO", data='{"visibility": "public"}')
    assert ext.vendor_name == "MARIVO"
    assert ext.data == '{"visibility": "public"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_osi_models.py -v --no-header -q`
Expected: FAIL — module `app.api.models.osi` not found

- [ ] **Step 3: Write OSI core Pydantic models**

```python
"""OSI Core Metadata Specification v0.1.1 — Pydantic models.

Layer 1: OSI external contract models. These models represent the wire format
for API input/output. All MARIVO-specific data lives in custom_extensions.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, RootModel


class DialectExpression(BaseModel):
    """Expression in a specific dialect."""

    dialect: Literal["ANSI_SQL", "SNOWFLAKE", "MDX", "TABLEAU", "DATABRICKS"]
    expression: str

    model_config = {"extra": "forbid"}


class Expression(BaseModel):
    """Multi-dialect expression definition."""

    dialects: list[DialectExpression] = Field(..., min_length=1)

    model_config = {"extra": "forbid"}


class AIContext(RootModel):
    """AI context — either a string or an object with instructions/synonyms/examples."""

    root: str | dict

    model_config = {"extra": "forbid"}


class CustomExtension(BaseModel):
    """Vendor-specific extension container."""

    vendor_name: Literal["MARIVO"]
    data: str  # JSON string containing vendor-specific data

    model_config = {"extra": "forbid"}


class Dimension(BaseModel):
    """Dimension metadata for a Field."""

    is_time: bool = False

    model_config = {"extra": "forbid"}


class Field(BaseModel):
    """Row-level attribute for grouping, filtering, and metric expressions."""

    name: str
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class Dataset(BaseModel):
    """Logical dataset representing a business entity."""

    name: str
    source: str
    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    description: str | None = None
    ai_context: AIContext | None = None
    fields: list[Field] | None = None
    custom_extensions: list[CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class Relationship(BaseModel):
    """Foreign key relationship between datasets."""

    model_config = {"extra": "forbid"}

    name: str
    from_: str = Field(alias="from")
    to: str
    from_columns: list[str] = Field(..., min_length=1)
    to_columns: list[str] = Field(..., min_length=1)
    ai_context: AIContext | None = None
    custom_extensions: list[CustomExtension] | None = None


class Metric(BaseModel):
    """Quantitative measure defined on business data."""

    name: str
    expression: Expression
    description: str | None = None
    ai_context: AIContext | None = None
    custom_extensions: list[CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class SemanticModel(BaseModel):
    """Top-level container representing a complete semantic model."""

    name: str
    datasets: list[Dataset] = Field(..., min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    relationships: list[Relationship] | None = None
    metrics: list[Metric] | None = None
    custom_extensions: list[CustomExtension] | None = None

    model_config = {"extra": "forbid"}


class OSIDocument(BaseModel):
    """Top-level OSI document structure."""

    version: Literal["0.1.1"]
    semantic_model: list[SemanticModel]

    model_config = {"extra": "forbid"}


OSI_SPEC_VERSION = "0.1.1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_osi_models.py -v --no-header -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/models/osi.py tests/test_osi_models.py
git commit -m "feat: add OSI core Pydantic models (v0.1.1)"
```

---

## Task 2: MARIVO Extension Models and Parsing

**Files:**
- Create: `app/api/models/marivo_extensions.py`
- Create: `app/semantic_service_v2/__init__.py`
- Create: `app/semantic_service_v2/extensions.py`
- Test: `tests/test_osi_models.py` (extend)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_osi_models.py`:

```python
# -- MARIVO Extension tests --

def test_marivo_semantic_model_extension_public():
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    ext = MarivoSemanticModelExtension(visibility="public")
    assert ext.visibility == "public"
    assert ext.owner_user is None


def test_marivo_semantic_model_extension_private_requires_owner():
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    with pytest.raises(ValidationError):
        MarivoSemanticModelExtension(visibility="private")


def test_marivo_semantic_model_extension_private_with_owner():
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    ext = MarivoSemanticModelExtension(visibility="private", owner_user="alice")
    assert ext.owner_user == "alice"


def test_marivo_dataset_extension():
    from app.api.models.marivo_extensions import MarivoDatasetExtension

    ext = MarivoDatasetExtension(datasource_id="tpcds")
    assert ext.datasource_id == "tpcds"


def test_marivo_field_extension():
    from app.api.models.marivo_extensions import MarivoFieldExtension

    ext = MarivoFieldExtension(data_type="integer")
    assert ext.data_type == "integer"


def test_marivo_relationship_extension():
    from app.api.models.marivo_extensions import MarivoRelationshipExtension

    ext = MarivoRelationshipExtension(cardinality="many_to_one")
    assert ext.cardinality == "many_to_one"


def test_marivo_metric_extension_minimal():
    from app.api.models.marivo_extensions import MarivoMetricExtension

    ext = MarivoMetricExtension()
    assert ext.observed_dataset is None


def test_marivo_additivity_full():
    from app.api.models.marivo_extensions import MarivoAdditivity

    add = MarivoAdditivity(dimension_policy="all", time_axis_policy="additive")
    assert add.additive_dimensions is None


def test_marivo_additivity_subset_requires_dimensions():
    from app.api.models.marivo_extensions import MarivoAdditivity

    with pytest.raises(ValidationError):
        MarivoAdditivity(dimension_policy="subset", time_axis_policy="additive")


def test_marivo_additivity_subset_with_dimensions():
    from app.api.models.marivo_extensions import MarivoAdditivity

    add = MarivoAdditivity(
        dimension_policy="subset",
        time_axis_policy="additive",
        additive_dimensions=["region", "category"],
    )
    assert add.additive_dimensions == ["region", "category"]


def test_marivo_metric_filter():
    from app.api.models.marivo_extensions import MarivoMetricFilter
    from app.api.models.osi import Expression, DialectExpression

    f = MarivoMetricFilter(
        name="active_only",
        expression=Expression(dialects=[DialectExpression(dialect="ANSI_SQL", expression="is_active = 1")]),
    )
    assert f.name == "active_only"


# -- Extension parsing tests --

def test_extract_marivo_extension_from_custom_extensions():
    from app.semantic_service_v2.extensions import extract_marivo_extension
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension
    from app.api.models.osi import CustomExtension

    exts = [CustomExtension(vendor_name="MARIVO", data='{"visibility": "public"}')]
    result = extract_marivo_extension(exts, MarivoSemanticModelExtension)
    assert result is not None
    assert result.visibility == "public"


def test_extract_marivo_extension_returns_none_when_absent():
    from app.semantic_service_v2.extensions import extract_marivo_extension
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension
    result = extract_marivo_extension([], MarivoSemanticModelExtension)
    assert result is None


def test_extract_marivo_extension_returns_none_for_empty():
    from app.semantic_service_v2.extensions import extract_marivo_extension
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    result = extract_marivo_extension(None, MarivoSemanticModelExtension)
    assert result is None


def test_build_custom_extensions_with_marivo():
    from app.semantic_service_v2.extensions import build_custom_extensions
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    exts = build_custom_extensions(MarivoSemanticModelExtension(visibility="public"))
    assert len(exts) == 1
    assert exts[0].vendor_name == "MARIVO"
    parsed = json.loads(exts[0].data)
    assert parsed["visibility"] == "public"


def test_build_custom_extensions_with_marivo_only():
    from app.semantic_service_v2.extensions import build_custom_extensions
    from app.api.models.marivo_extensions import MarivoSemanticModelExtension

    exts = build_custom_extensions(MarivoSemanticModelExtension(visibility="public"))
    assert len(exts) == 1
    assert exts[0].vendor_name == "MARIVO"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_osi_models.py -k "marivo" -v --no-header -q`
Expected: FAIL — modules not found

- [ ] **Step 3: Write MARIVO extension models**

Create `app/api/models/marivo_extensions.py`:

```python
"""MARIVO vendor extension models for OSI objects.

Layer 2: MARIVO extension schema. Defines the structure of
custom_extensions[].data when vendor_name == "MARIVO".
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MarivoSemanticModelExtension(BaseModel):
    visibility: Literal["public", "private"]
    owner_user: str | None = None

    @model_validator(mode="after")
    def _private_requires_owner(self) -> MarivoSemanticModelExtension:
        if self.visibility == "private" and not self.owner_user:
            raise ValueError("owner_user is required when visibility is private")
        return self

    model_config = {"extra": "forbid"}


class MarivoDatasetExtension(BaseModel):
    datasource_id: str | None = None

    model_config = {"extra": "forbid"}


MarivoFieldDataType = Literal["string", "integer", "number", "boolean", "date", "datetime"]


class MarivoFieldExtension(BaseModel):
    data_type: MarivoFieldDataType | None = None

    model_config = {"extra": "forbid"}


class MarivoRelationshipExtension(BaseModel):
    cardinality: Literal["many_to_one", "one_to_one"] | None = None

    model_config = {"extra": "forbid"}


class MarivoAdditivity(BaseModel):
    dimension_policy: Literal["all", "subset", "none"]
    additive_dimensions: list[str] | None = None
    time_axis_policy: Literal["additive", "non_additive"]

    @model_validator(mode="after")
    def _subset_requires_dimensions(self) -> MarivoAdditivity:
        if self.dimension_policy == "subset" and not self.additive_dimensions:
            raise ValueError("additive_dimensions is required when dimension_policy is subset")
        if self.dimension_policy != "subset" and self.additive_dimensions:
            raise ValueError("additive_dimensions must only be set when dimension_policy is subset")
        return self

    model_config = {"extra": "forbid"}


class MarivoMetricFilter(BaseModel):
    from app.api.models.osi import Expression

    name: str = Field(..., min_length=1)
    expression: Expression

    model_config = {"extra": "forbid"}


class MarivoMetricExtension(BaseModel):
    observed_dataset: str | None = None
    observation_grain: list[str] | None = None
    primary_time_field: str | None = None
    additivity: MarivoAdditivity | None = None
    filters: list[MarivoMetricFilter] | None = None

    model_config = {"extra": "forbid"}
```

Fix the MarivoMetricFilter import — move it after the OSI import or use TYPE_CHECKING. Actually, the circular import issue needs to be handled. Let me restructure:

Create `app/api/models/marivo_extensions.py`:

```python
"""MARIVO vendor extension models for OSI objects.

Layer 2: MARIVO extension schema. Defines the structure of
custom_extensions[].data when vendor_name == "MARIVO".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from app.api.models.osi import Expression


class MarivoSemanticModelExtension(BaseModel):
    visibility: Literal["public", "private"]
    owner_user: str | None = None

    @model_validator(mode="after")
    def _private_requires_owner(self) -> MarivoSemanticModelExtension:
        if self.visibility == "private" and not self.owner_user:
            raise ValueError("owner_user is required when visibility is private")
        return self

    model_config = {"extra": "forbid"}


class MarivoDatasetExtension(BaseModel):
    datasource_id: str | None = None

    model_config = {"extra": "forbid"}


MarivoFieldDataType = Literal["string", "integer", "number", "boolean", "date", "datetime"]


class MarivoFieldExtension(BaseModel):
    data_type: MarivoFieldDataType | None = None

    model_config = {"extra": "forbid"}


class MarivoRelationshipExtension(BaseModel):
    cardinality: Literal["many_to_one", "one_to_one"] | None = None

    model_config = {"extra": "forbid"}


class MarivoAdditivity(BaseModel):
    dimension_policy: Literal["all", "subset", "none"]
    additive_dimensions: list[str] | None = None
    time_axis_policy: Literal["additive", "non_additive"]

    @model_validator(mode="after")
    def _subset_requires_dimensions(self) -> MarivoAdditivity:
        if self.dimension_policy == "subset" and not self.additive_dimensions:
            raise ValueError("additive_dimensions is required when dimension_policy is subset")
        if self.dimension_policy != "subset" and self.additive_dimensions:
            raise ValueError("additive_dimensions must only be set when dimension_policy is subset")
        return self

    model_config = {"extra": "forbid"}


class MarivoMetricFilter(BaseModel):
    name: str = Field(..., min_length=1)
    expression: dict  # Expression serialized as dict (avoid circular import)

    model_config = {"extra": "forbid"}


class MarivoMetricExtension(BaseModel):
    observed_dataset: str | None = None
    observation_grain: list[str] | None = None
    primary_time_field: str | None = None
    additivity: MarivoAdditivity | None = None
    filters: list[MarivoMetricFilter] | None = None

    model_config = {"extra": "forbid"}
```

Create `app/semantic_service_v2/__init__.py`:

```python
"""OSI-aligned semantic service layer (v2)."""
```

Create `app/semantic_service_v2/extensions.py`:

```python
"""Parsing and serialization of MARIVO custom_extensions.

Handles the bidirectional mapping between:
  - OSI wire format: custom_extensions[].data (JSON string)
  - Python: typed MARIVO extension Pydantic models
"""
from __future__ import annotations

import json
from typing import TypeVar

from app.api.models.osi import CustomExtension
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

MARIVO_VENDOR = "MARIVO"


def extract_marivo_extension(
    custom_extensions: list[CustomExtension] | None,
    extension_type: type[T],
) -> T | None:
    """Extract and parse the MARIVO vendor extension from a custom_extensions list.

    Returns None if no MARIVO extension is present.
    Raises ValidationError if the data payload is invalid for the given type.
    """
    if custom_extensions is None:
        return None
    for ext in custom_extensions:
        if ext.vendor_name == MARIVO_VENDOR:
            return extension_type.model_validate_json(ext.data)
    return None


def build_custom_extensions(
    marivo_ext: BaseModel | None = None,
    *others: CustomExtension,
) -> list[CustomExtension]:
    """Build a custom_extensions list from a MARIVO extension model and optional other extensions."""
    result: list[CustomExtension] = []
    if marivo_ext is not None:
        result.append(
            CustomExtension(
                vendor_name=MARIVO_VENDOR,
                data=marivo_ext.model_dump_json(exclude_none=True),
            )
        )
    result.extend(others)
    return result
```

- [ ] **Step 4: Fix test to use dict for metric filter expression**

The MarivoMetricFilter uses `dict` for expression to avoid circular imports. Update test:

```python
def test_marivo_metric_filter():
    from app.api.models.marivo_extensions import MarivoMetricFilter

    f = MarivoMetricFilter(
        name="active_only",
        expression={"dialects": [{"dialect": "ANSI_SQL", "expression": "is_active = 1"}]},
    )
    assert f.name == "active_only"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_osi_models.py -v --no-header -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/models/marivo_extensions.py app/semantic_service_v2/ tests/test_osi_models.py
git commit -m "feat: add MARIVO extension models and custom_extensions parsing"
```

---

## Task 3: New Storage Schema

**Files:**
- Modify: `app/storage/schema.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_metadata_schema_bootstrap.py` (or create new):

```python
def test_osi_v2_tables_exist_in_ddl():
    """New OSI-aligned tables must be in the DDL."""
    from app.storage.schema import METADATA_DDL, _table_name

    table_names = set()
    for stmt in METADATA_DDL:
        name = _table_name(stmt)
        if name:
            table_names.add(name)

    required = {
        "semantic_versions",
        "semantic_models",
        "semantic_datasets",
        "semantic_fields",
        "semantic_relationships",
        "semantic_metrics",
        "semantic_readiness_status",
    }
    missing = required - table_names
    assert not missing, f"Missing OSI v2 tables: {missing}"


def test_old_semantic_tables_removed_from_ddl():
    """Old semantic tables must NOT be in the DDL."""
    from app.storage.schema import METADATA_DDL, _table_name

    table_names = set()
    for stmt in METADATA_DDL:
        name = _table_name(stmt)
        if name:
            table_names.add(name)

    removed = {
        "semantic_entity_contracts",
        "semantic_entity_key_refs",
        "semantic_entity_stable_descriptors",
        "semantic_metric_contracts",
        "semantic_process_objects",
        "semantic_process_exported_dimension_refs",
        "semantic_dimension_contracts",
        "semantic_time_objects",
        "semantic_enum_sets",
        "semantic_enum_set_versions",
        "semantic_enum_set_values",
        "semantic_predicate_contracts",
        "semantic_domain_catalog",
        "typed_bindings",
        "binding_imports",
        "carrier_bindings",
        "carrier_field_surfaces",
        "carrier_time_surfaces",
        "field_bindings",
        "time_bindings",
        "join_relations",
        "consumption_policies",
        "semantic_entity_relationships",
        "compiler_compatibility_profiles",
    }
    found = removed & table_names
    assert not found, f"Old tables still in DDL: {found}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metadata_schema_bootstrap.py -k "osi_v2 or old_semantic" -v --no-header -q`
Expected: FAIL — new tables don't exist, old tables still present

- [ ] **Step 3: Replace old semantic DDL with new OSI-aligned tables**

In `app/storage/schema.py`, remove all old semantic layer DDL (from `semantic_entity_contracts` through `compiler_compatibility_profiles` including all related indexes and triggers) and replace with:

```python
    # -------------------------------------------------------------------------
    # OSI-aligned semantic layer tables (v2)
    # -------------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS semantic_versions (
        version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_models (
        model_id             INTEGER PRIMARY KEY AUTOINCREMENT,
        semantic_version_id  INTEGER REFERENCES semantic_versions(version_id),
        name                 TEXT NOT NULL,
        description          TEXT,
        ai_context           TEXT,
        visibility           TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
        owner_user           TEXT,
        created_at           TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(semantic_version_id, name),
        UNIQUE(owner_user, name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_models_version_visibility ON semantic_models(semantic_version_id, visibility)",
    "CREATE INDEX IF NOT EXISTS idx_semantic_models_visibility_owner ON semantic_models(visibility, owner_user)",
    """
    CREATE TABLE IF NOT EXISTS semantic_datasets (
        dataset_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id       INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name           TEXT NOT NULL,
        source         TEXT NOT NULL,
        primary_key    TEXT,
        unique_keys    TEXT,
        description    TEXT,
        ai_context     TEXT,
        datasource_id  TEXT,
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_fields (
        field_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        dataset_id   INTEGER NOT NULL REFERENCES semantic_datasets(dataset_id) ON DELETE CASCADE,
        name         TEXT NOT NULL,
        expression   TEXT NOT NULL,
        is_time      INTEGER NOT NULL DEFAULT 0,
        label        TEXT,
        description  TEXT,
        ai_context   TEXT,
        data_type    TEXT,
        position     INTEGER NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(dataset_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_relationships (
        relationship_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id         INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name             TEXT NOT NULL,
        from_dataset     TEXT NOT NULL,
        to_dataset       TEXT NOT NULL,
        from_columns     TEXT NOT NULL,
        to_columns       TEXT NOT NULL,
        ai_context       TEXT,
        cardinality      TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_metrics (
        metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        model_id            INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        expression          TEXT NOT NULL,
        description         TEXT,
        ai_context          TEXT,
        observed_dataset    TEXT,
        observation_grain   TEXT,
        primary_time_field  TEXT,
        additivity          TEXT,
        filters             TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(model_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS semantic_readiness_status (
        model_id                        INTEGER PRIMARY KEY REFERENCES semantic_models(model_id) ON DELETE CASCADE,
        status                          TEXT NOT NULL CHECK (status IN ('ready', 'not_ready')),
        blockers                        TEXT,
        evaluated_semantic_version_id   INTEGER,
        updated_at                      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
```

Also update `METADATA_SCHEMA_VERSION` to `"metadata.osi_v2.v1"` to force re-initialization.

Remove all old semantic table DDL, indexes, and triggers from `METADATA_DDL`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_metadata_schema_bootstrap.py -k "osi_v2 or old_semantic" -v --no-header -q`
Expected: PASS

- [ ] **Step 5: Run full schema bootstrap test**

Run: `.venv/bin/pytest tests/test_metadata_schema_bootstrap.py -v --no-header -q`
Expected: PASS (after fixing any cascade issues with removed tables)

- [ ] **Step 6: Commit**

```bash
git add app/storage/schema.py
git commit -m "feat: replace legacy semantic DDL with OSI-aligned tables"
```

---

## Task 4: Service Layer — Storage Mapping and CRUD

**Files:**
- Create: `app/semantic_service_v2/storage.py`
- Create: `app/semantic_service_v2/service.py`
- Create: `app/semantic_service_v2/validation.py`
- Test: `tests/test_semantic_v2_service.py`

This is the largest task. The service handles:
1. OSI document → storage row mapping
2. Storage row → OSI document assembly
3. CRUD for SemanticModel, Dataset, Relationship, Metric
4. Write-time validation
5. Semantic version management
6. Visibility filtering

- [ ] **Step 1: Write failing tests for service CRUD**

```python
"""Tests for OSI-aligned semantic service (v2)."""
from __future__ import annotations

import json
import pytest
from app.storage.sqlite_metadata import SqliteMetadataStore


@pytest.fixture
def metadata():
    store = SqliteMetadataStore.in_memory()
    store.initialize_schema()
    return store


@pytest.fixture
def service(metadata):
    from app.semantic_service_v2.service import SemanticModelV2Service
    return SemanticModelV2Service(metadata)


def _make_osi_document(**overrides):
    """Build a minimal valid OSI document for testing."""
    doc = {
        "version": "0.1.1",
        "semantic_model": [{
            "name": "retail",
            "description": "Retail analytics",
            "datasets": [{
                "name": "store_sales",
                "source": "tpcds.public.store_sales",
                "fields": [{
                    "name": "ss_sold_date_sk",
                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}]},
                    "custom_extensions": [{"vendor_name": "MARIVO", "data": '{"data_type": "integer"}'}],
                }],
                "custom_extensions": [{"vendor_name": "MARIVO", "data": '{"datasource_id": "tpcds"}'}],
            }],
            "metrics": [{
                "name": "total_sales",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(ss_ext_sales_price)"}]},
                "custom_extensions": [{"vendor_name": "MARIVO", "data": json.dumps({
                    "observed_dataset": "store_sales",
                    "additivity": {"dimension_policy": "all", "time_axis_policy": "additive"},
                })}],
            }],
            "custom_extensions": [{"vendor_name": "MARIVO", "data": '{"visibility": "public"}'}],
        }],
    }
    doc.update(overrides)
    return doc


def test_create_semantic_model(service, metadata):
    doc = _make_osi_document()
    result = service.create_semantic_model(doc["semantic_model"][0])
    assert result["name"] == "retail"
    assert len(result["datasets"]) == 1
    assert result["datasets"][0]["name"] == "store_sales"


def test_get_semantic_model(service):
    doc = _make_osi_document()
    created = service.create_semantic_model(doc["semantic_model"][0])
    result = service.get_semantic_model("retail")
    assert result["name"] == "retail"


def test_get_semantic_model_not_found(service):
    with pytest.raises(Exception):
        service.get_semantic_model("nonexistent")


def test_list_semantic_models(service):
    doc = _make_osi_document()
    service.create_semantic_model(doc["semantic_model"][0])
    result = service.list_semantic_models()
    assert len(result) >= 1
    assert any(m["name"] == "retail" for m in result)


def test_update_semantic_model(service):
    doc = _make_osi_document()
    service.create_semantic_model(doc["semantic_model"][0])
    updated = service.update_semantic_model("retail", {"description": "Updated description"})
    assert updated["description"] == "Updated description"


def test_delete_semantic_model(service):
    doc = _make_osi_document()
    service.create_semantic_model(doc["semantic_model"][0])
    service.delete_semantic_model("retail")
    with pytest.raises(Exception):
        service.get_semantic_model("retail")


def test_create_dataset(service):
    doc = _make_osi_document()
    service.create_semantic_model(doc["semantic_model"][0])
    ds = service.create_dataset("retail", {
        "name": "date_dim",
        "source": "tpcds.public.date_dim",
    })
    assert ds["name"] == "date_dim"


def test_create_relationship(service):
    doc = _make_osi_document()
    doc["semantic_model"][0]["datasets"].append({
        "name": "date_dim",
        "source": "tpcds.public.date_dim",
    })
    service.create_semantic_model(doc["semantic_model"][0])
    rel = service.create_relationship("retail", {
        "name": "store_sales_to_date",
        "from": "store_sales",
        "to": "date_dim",
        "from_columns": ["ss_sold_date_sk"],
        "to_columns": ["d_date_sk"],
        "custom_extensions": [{"vendor_name": "MARIVO", "data": '{"cardinality": "many_to_one"}'}],
    })
    assert rel["name"] == "store_sales_to_date"


def test_validation_rejects_invalid_dataset_ref_in_metric(service):
    doc = _make_osi_document()
    # Metric references nonexistent dataset
    doc["semantic_model"][0]["metrics"][0]["custom_extensions"][0]["data"] = json.dumps({
        "observed_dataset": "nonexistent_dataset",
        "additivity": {"dimension_policy": "all", "time_axis_policy": "additive"},
    })
    with pytest.raises(Exception, match="observed_dataset"):
        service.create_semantic_model(doc["semantic_model"][0])


def test_validation_rejects_invalid_relationship_ref(service):
    doc = _make_osi_document()
    doc["semantic_model"][0]["relationships"] = [{
        "name": "bad_rel",
        "from": "nonexistent",
        "to": "also_nonexistent",
        "from_columns": ["col"],
        "to_columns": ["col"],
    }]
    with pytest.raises(Exception):
        service.create_semantic_model(doc["semantic_model"][0])


def test_private_model_requires_owner_user(service):
    doc = _make_osi_document()
    doc["semantic_model"][0]["custom_extensions"] = [
        {"vendor_name": "MARIVO", "data": '{"visibility": "private"}'},
    ]
    with pytest.raises(Exception, match="owner_user"):
        service.create_semantic_model(doc["semantic_model"][0])


def test_private_model_with_owner(service):
    doc = _make_osi_document()
    doc["semantic_model"][0]["custom_extensions"] = [
        {"vendor_name": "MARIVO", "data": '{"visibility": "private", "owner_user": "alice"}'},
    ]
    result = service.create_semantic_model(doc["semantic_model"][0])
    assert result["visibility"] == "private"
    assert result["owner_user"] == "alice"


def test_private_model_visible_only_to_owner(service):
    doc = _make_osi_document()
    doc["semantic_model"][0]["custom_extensions"] = [
        {"vendor_name": "MARIVO", "data": '{"visibility": "private", "owner_user": "alice"}'},
    ]
    service.create_semantic_model(doc["semantic_model"][0])
    # Owner can see it
    result = service.list_semantic_models(requesting_user="alice")
    assert any(m["name"] == "retail" for m in result)
    # Other user cannot
    result = service.list_semantic_models(requesting_user="bob")
    assert not any(m["name"] == "retail" for m in result)


def test_public_model_version_association(service):
    doc = _make_osi_document()
    service.create_semantic_model(doc["semantic_model"][0])
    result = service.get_semantic_model("retail")
    assert result.get("semantic_version_id") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_v2_service.py -v --no-header -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write storage mapping**

Create `app/semantic_service_v2/storage.py`:

```python
"""OSI↔storage row mapping functions.

Maps between OSI wire format (Pydantic models / dicts) and normalized storage rows.
"""
from __future__ import annotations

import json
from typing import Any

from app.api.models.marivo_extensions import (
    MarivoAdditivity,
    MarivoDatasetExtension,
    MarivoFieldExtension,
    MarivoMetricExtension,
    MarivoRelationshipExtension,
    MarivoSemanticModelExtension,
)
from app.api.models.osi import (
    CustomExtension,
    Dataset,
    Expression,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)
from app.semantic_service_v2.extensions import (
    build_custom_extensions,
    extract_marivo_extension,
)


def _json_serialize(obj: Any) -> str | None:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _json_deserialize(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


def model_to_storage(model: SemanticModel) -> dict[str, Any]:
    """Extract OSI core + MARIVO extension fields for semantic_models row."""
    marivo = extract_marivo_extension(model.custom_extensions, MarivoSemanticModelExtension)
    return {
        "name": model.name,
        "description": model.description,
        "ai_context": _json_serialize(model.ai_context.root if model.ai_context else None),
        "visibility": marivo.visibility if marivo else "public",
        "owner_user": marivo.owner_user if marivo else None,
    }


def dataset_to_storage(ds: Dataset, model_id: int) -> dict[str, Any]:
    """Extract OSI core + MARIVO extension fields for semantic_datasets row."""
    marivo = extract_marivo_extension(ds.custom_extensions, MarivoDatasetExtension)
    return {
        "model_id": model_id,
        "name": ds.name,
        "source": ds.source,
        "primary_key": _json_serialize(ds.primary_key),
        "unique_keys": _json_serialize(ds.unique_keys),
        "description": ds.description,
        "ai_context": _json_serialize(ds.ai_context.root if ds.ai_context else None),
        "datasource_id": marivo.datasource_id if marivo else None,
    }


def field_to_storage(field: Field, dataset_id: int, position: int) -> dict[str, Any]:
    """Extract OSI core + MARIVO extension fields for semantic_fields row."""
    marivo = extract_marivo_extension(field.custom_extensions, MarivoFieldExtension)
    return {
        "dataset_id": dataset_id,
        "name": field.name,
        "expression": _json_serialize(field.expression.model_dump(mode="json")),
        "is_time": 1 if (field.dimension and field.dimension.is_time) else 0,
        "label": field.label,
        "description": field.description,
        "ai_context": _json_serialize(field.ai_context.root if field.ai_context else None),
        "data_type": marivo.data_type if marivo else None,
        "position": position,
    }


def relationship_to_storage(rel: Relationship, model_id: int) -> dict[str, Any]:
    """Extract OSI core + MARIVO extension fields for semantic_relationships row."""
    marivo = extract_marivo_extension(rel.custom_extensions, MarivoRelationshipExtension)
    return {
        "model_id": model_id,
        "name": rel.name,
        "from_dataset": rel.from_,
        "to_dataset": rel.to,
        "from_columns": _json_serialize(rel.from_columns),
        "to_columns": _json_serialize(rel.to_columns),
        "ai_context": _json_serialize(rel.ai_context.root if rel.ai_context else None),
        "cardinality": marivo.cardinality if marivo else None,
    }


def metric_to_storage(metric: Metric, model_id: int) -> dict[str, Any]:
    """Extract OSI core + MARIVO extension fields for semantic_metrics row."""
    marivo = extract_marivo_extension(metric.custom_extensions, MarivoMetricExtension)
    return {
        "model_id": model_id,
        "name": metric.name,
        "expression": _json_serialize(metric.expression.model_dump(mode="json")),
        "description": metric.description,
        "ai_context": _json_serialize(metric.ai_context.root if metric.ai_context else None),
        "observed_dataset": marivo.observed_dataset if marivo else None,
        "observation_grain": _json_serialize(marivo.observation_grain if marivo else None),
        "primary_time_field": marivo.primary_time_field if marivo else None,
        "additivity": _json_serialize(marivo.additivity.model_dump(mode="json") if marivo and marivo.additivity else None),
        "filters": _json_serialize(
            [f.model_dump(mode="json") for f in marivo.filters] if marivo and marivo.filters else None
        ),
    }


def storage_to_model(row: dict[str, Any], datasets: list[dict], relationships: list[dict], metrics: list[dict]) -> dict[str, Any]:
    """Assemble a full OSI-conformant SemanticModel dict from storage rows."""
    visibility = row.get("visibility", "public")
    owner_user = row.get("owner_user")
    marivo_model_ext = MarivoSemanticModelExtension(visibility=visibility, owner_user=owner_user)
    custom_exts = build_custom_extensions(marivo_model_ext)

    result: dict[str, Any] = {
        "name": row["name"],
        "description": row.get("description"),
        "ai_context": _json_deserialize(row["ai_context"]) if row.get("ai_context") else None,
        "custom_extensions": [e.model_dump(mode="json") for e in custom_exts],
        "datasets": [_storage_to_dataset(ds) for ds in datasets],
        "relationships": [_storage_to_relationship(r) for r in relationships] or None,
        "metrics": [_storage_to_metric(m) for m in metrics] or None,
    }
    # Include internal fields for service response (not in OSI output)
    result["semantic_version_id"] = row.get("semantic_version_id")
    result["visibility"] = visibility
    result["owner_user"] = owner_user
    return {k: v for k, v in result.items() if v is not None or k in ("name", "datasets")}


def _storage_to_dataset(row: dict[str, Any]) -> dict[str, Any]:
    marivo_ext = MarivoDatasetExtension(datasource_id=row.get("datasource_id"))
    custom_exts = build_custom_extensions(marivo_ext)

    result: dict[str, Any] = {
        "name": row["name"],
        "source": row["source"],
        "primary_key": _json_deserialize(row.get("primary_key")),
        "unique_keys": _json_deserialize(row.get("unique_keys")),
        "description": row.get("description"),
        "ai_context": _json_deserialize(row["ai_context"]) if row.get("ai_context") else None,
        "custom_extensions": [e.model_dump(mode="json") for e in custom_exts],
        "fields": row.get("_fields", []),
    }
    return {k: v for k, v in result.items() if v is not None or k in ("name", "source")}


def _storage_to_relationship(row: dict[str, Any]) -> dict[str, Any]:
    marivo_ext = MarivoRelationshipExtension(cardinality=row.get("cardinality"))
    custom_exts = build_custom_extensions(marivo_ext) if row.get("cardinality") else []

    result: dict[str, Any] = {
        "name": row["name"],
        "from": row["from_dataset"],
        "to": row["to_dataset"],
        "from_columns": _json_deserialize(row["from_columns"]),
        "to_columns": _json_deserialize(row["to_columns"]),
        "ai_context": _json_deserialize(row["ai_context"]) if row.get("ai_context") else None,
    }
    if custom_exts:
        result["custom_extensions"] = [e.model_dump(mode="json") for e in custom_exts]
    return {k: v for k, v in result.items() if v is not None or k in ("name", "from", "to", "from_columns", "to_columns")}


def _storage_to_metric(row: dict[str, Any]) -> dict[str, Any]:
    additivity = _json_deserialize(row.get("additivity"))
    filters = _json_deserialize(row.get("filters"))
    marivo_ext = MarivoMetricExtension(
        observed_dataset=row.get("observed_dataset"),
        observation_grain=_json_deserialize(row.get("observation_grain")),
        primary_time_field=row.get("primary_time_field"),
        additivity=MarivoAdditivity.model_validate(additivity) if additivity else None,
        filters=[MarivoMetricFilter.model_validate(f) for f in filters] if filters else None,
    )
    custom_exts = build_custom_extensions(marivo_ext)

    result: dict[str, Any] = {
        "name": row["name"],
        "expression": _json_deserialize(row["expression"]),
        "description": row.get("description"),
        "ai_context": _json_deserialize(row["ai_context"]) if row.get("ai_context") else None,
        "custom_extensions": [e.model_dump(mode="json") for e in custom_exts],
    }
    return {k: v for k, v in result.items() if v is not None or k in ("name", "expression")}
```

- [ ] **Step 4: Write validation module**

Create `app/semantic_service_v2/validation.py`:

```python
"""Write-time validation for OSI semantic models.

Performs deterministic validation inline on create/update.
No standalone validate endpoint — validation fails fast.
"""
from __future__ import annotations

from typing import Any


class SemanticValidationError(Exception):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


def validate_semantic_model(model_data: dict[str, Any]) -> None:
    """Validate a semantic model dict for completeness and correctness.

    Raises SemanticValidationError with structured error details.
    """
    errors: list[dict[str, Any]] = []
    datasets = {ds["name"]: ds for ds in model_data.get("datasets", [])}
    fields_by_dataset: dict[str, dict[str, dict]] = {}
    for ds_name, ds in datasets.items():
        fields_by_dataset[ds_name] = {f["name"]: f for f in ds.get("fields", [])}

    # Validate visibility/owner_user
    visibility = model_data.get("visibility", "public")
    if visibility not in ("public", "private"):
        errors.append({"message": f"visibility must be 'public' or 'private', got '{visibility}'", "path": "visibility"})
    if visibility == "private" and not model_data.get("owner_user"):
        errors.append({"message": "owner_user is required when visibility is private", "path": "owner_user"})

    # Validate relationships reference existing datasets
    for rel in model_data.get("relationships", []) or []:
        if rel["from"] not in datasets:
            errors.append({"message": f"Relationship '{rel['name']}' references unknown 'from' dataset '{rel['from']}'", "path": f"relationships.{rel['name']}.from"})
        if rel["to"] not in datasets:
            errors.append({"message": f"Relationship '{rel['name']}' references unknown 'to' dataset '{rel['to']}'", "path": f"relationships.{rel['name']}.to"})

    # Validate metrics
    for metric in model_data.get("metrics", []) or []:
        _validate_metric(metric, datasets, fields_by_dataset, errors)

    if errors:
        raise SemanticValidationError(errors)


def validate_relationship(rel_data: dict[str, Any], datasets: dict[str, dict]) -> None:
    """Validate a standalone relationship against existing datasets."""
    errors: list[dict[str, Any]] = []
    if rel_data["from"] not in datasets:
        errors.append({"message": f"Relationship references unknown 'from' dataset '{rel_data['from']}'", "path": "from"})
    if rel_data["to"] not in datasets:
        errors.append({"message": f"Relationship references unknown 'to' dataset '{rel_data['to']}'", "path": "to"})
    if errors:
        raise SemanticValidationError(errors)


def validate_metric(metric_data: dict[str, Any], datasets: dict[str, dict], fields_by_dataset: dict[str, dict[str, dict]]) -> None:
    """Validate a standalone metric against existing datasets/fields."""
    errors: list[dict[str, Any]] = []
    _validate_metric(metric_data, datasets, fields_by_dataset, errors)
    if errors:
        raise SemanticValidationError(errors)


def _validate_metric(
    metric: dict[str, Any],
    datasets: dict[str, dict],
    fields_by_dataset: dict[str, dict[str, dict]],
    errors: list[dict[str, Any]],
) -> None:
    observed_dataset = metric.get("observed_dataset")
    if observed_dataset and observed_dataset not in datasets:
        errors.append({
            "message": f"Metric '{metric['name']}' references unknown observed_dataset '{observed_dataset}'",
            "path": f"metrics.{metric['name']}.observed_dataset",
        })
    observation_grain = metric.get("observation_grain")
    if observed_dataset and observation_grain and observed_dataset in fields_by_dataset:
        ds_fields = fields_by_dataset[observed_dataset]
        for grain_field in observation_grain:
            if grain_field not in ds_fields:
                errors.append({
                    "message": f"Metric '{metric['name']}' observation_grain field '{grain_field}' not found in dataset '{observed_dataset}'",
                    "path": f"metrics.{metric['name']}.observation_grain",
                })
    primary_time_field = metric.get("primary_time_field")
    if primary_time_field and observed_dataset and observed_dataset in fields_by_dataset:
        ds_fields = fields_by_dataset[observed_dataset]
        if primary_time_field not in ds_fields:
            errors.append({
                "message": f"Metric '{metric['name']}' primary_time_field '{primary_time_field}' not found in dataset '{observed_dataset}'",
                "path": f"metrics.{metric['name']}.primary_time_field",
            })
        else:
            field = ds_fields[primary_time_field]
            if not field.get("is_time"):
                errors.append({
                    "message": f"Metric '{metric['name']}' primary_time_field '{primary_time_field}' is not a time field",
                    "path": f"metrics.{metric['name']}.primary_time_field",
                })
    additivity = metric.get("additivity")
    if additivity and additivity.get("dimension_policy") == "subset":
        additive_dims = additivity.get("additive_dimensions", [])
        if observed_dataset and observed_dataset in fields_by_dataset:
            ds_fields = fields_by_dataset[observed_dataset]
            for dim in additive_dims:
                if dim not in ds_fields:
                    errors.append({
                        "message": f"Metric '{metric['name']}' additive_dimension '{dim}' not found in dataset '{observed_dataset}'",
                        "path": f"metrics.{metric['name']}.additivity.additive_dimensions",
                    })
```

- [ ] **Step 5: Write service**

Create `app/semantic_service_v2/service.py`:

```python
"""OSI-aligned semantic model service (v2).

Provides CRUD for SemanticModel, Dataset, Relationship, Metric,
plus import and readiness endpoints.
"""
from __future__ import annotations

import json
from typing import Any
from datetime import datetime, UTC

from app.api.models.marivo_extensions import (
    MarivoDatasetExtension,
    MarivoFieldExtension,
    MarivoMetricExtension,
    MarivoRelationshipExtension,
    MarivoSemanticModelExtension,
)
from app.api.models.osi import (
    CustomExtension,
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)
from app.semantic_service_v2.extensions import extract_marivo_extension
from app.semantic_service_v2.storage import (
    dataset_to_storage,
    field_to_storage,
    metric_to_storage,
    model_to_storage,
    relationship_to_storage,
    storage_to_model,
)
from app.semantic_service_v2.validation import (
    SemanticValidationError,
    validate_semantic_model,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SemanticModelV2Service:
    """Service for OSI-aligned semantic model CRUD operations."""

    def __init__(self, metadata):
        self.metadata = metadata

    # -- Semantic Model CRUD --

    def create_semantic_model(self, model_data: dict[str, Any]) -> dict[str, Any]:
        """Create a semantic model with all nested datasets, relationships, metrics."""
        # Parse and validate OSI structure
        model = SemanticModel.model_validate(model_data)

        # Extract MARIVO extensions for validation
        marivo = extract_marivo_extension(model.custom_extensions, MarivoSemanticModelExtension)
        model_dict = model.model_dump(mode="json", by_alias=True)
        model_dict["visibility"] = marivo.visibility if marivo else "public"
        model_dict["owner_user"] = marivo.owner_user if marivo else None

        # Extract MARIVO extensions from nested objects for validation
        self._enrich_model_dict_with_marivo(model_dict)

        # Validate
        validate_semantic_model(model_dict)

        # Get or create semantic version for public models
        visibility = model_dict["visibility"]
        semantic_version_id = None
        if visibility == "public":
            semantic_version_id = self._get_or_create_latest_version()

        # Insert model
        now = _now()
        self.metadata.execute(
            """INSERT INTO semantic_models
               (semantic_version_id, name, description, ai_context, visibility, owner_user, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [semantic_version_id, model.name, model.description,
             model_to_storage(model).get("ai_context"),
             visibility, model_dict.get("owner_user"), now, now],
        )
        row = self.metadata.query_one("SELECT * FROM semantic_models WHERE name = ? ORDER BY model_id DESC LIMIT 1", [model.name])
        model_id = row["model_id"]

        # Insert datasets and fields
        for ds in model.datasets or []:
            self._insert_dataset(model_id, ds)

        # Insert relationships
        for rel in model.relationships or []:
            self._insert_relationship(model_id, rel)

        # Insert metrics
        for metric in model.metrics or []:
            self._insert_metric(model_id, metric)

        return self.get_semantic_model(model.name)

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        """Get a semantic model by name, applying visibility filtering."""
        row = self._get_model_row(name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {name}")
        self._check_visibility(row, requesting_user)
        return self._assemble_model(row)

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        """List semantic models with visibility filtering."""
        latest_version = self._latest_version_id()
        rows = self.metadata.query_rows(
            """SELECT * FROM semantic_models
               WHERE (semantic_version_id = ? AND visibility = 'public')
                  OR (visibility = 'private' AND owner_user = ?)
               ORDER BY name""",
            [latest_version, requesting_user],
        )
        return [self._assemble_model(row) for row in rows]

    def update_semantic_model(self, name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update a semantic model's top-level fields."""
        row = self._get_model_row(name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {name}")
        model_id = row["model_id"]

        set_clauses = []
        params = []
        for field in ("description",):
            if field in updates:
                set_clauses.append(f"{field} = ?")
                params.append(updates[field])

        if set_clauses:
            set_clauses.append("updated_at = ?")
            params.append(_now())
            params.append(model_id)
            self.metadata.execute(
                f"UPDATE semantic_models SET {', '.join(set_clauses)} WHERE model_id = ?",
                params,
            )
        return self.get_semantic_model(name)

    def delete_semantic_model(self, name: str) -> None:
        """Delete a semantic model and all nested objects (CASCADE)."""
        row = self._get_model_row(name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {name}")
        self.metadata.execute("DELETE FROM semantic_models WHERE model_id = ?", [row["model_id"]])

    # -- Dataset CRUD --

    def create_dataset(self, model_name: str, ds_data: dict[str, Any]) -> dict[str, Any]:
        """Add a dataset to an existing semantic model."""
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        ds = Dataset.model_validate(ds_data)
        self._insert_dataset(row["model_id"], ds)
        return self.get_dataset(model_name, ds.name)

    def get_dataset(self, model_name: str, dataset_name: str) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        ds_row = self.metadata.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise self._not_found(f"Dataset not found: {dataset_name}")
        ds_row = dict(ds_row)
        ds_row["_fields"] = self._get_fields(ds_row["dataset_id"])
        from app.semantic_service_v2.storage import _storage_to_dataset
        return _storage_to_dataset(ds_row)

    def list_datasets(self, model_name: str) -> list[dict[str, Any]]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rows = self.metadata.query_rows(
            "SELECT * FROM semantic_datasets WHERE model_id = ? ORDER BY name",
            [row["model_id"]],
        )
        result = []
        for r in rows:
            r = dict(r)
            r["_fields"] = self._get_fields(r["dataset_id"])
            from app.semantic_service_v2.storage import _storage_to_dataset
            result.append(_storage_to_dataset(r))
        return result

    def update_dataset(self, model_name: str, dataset_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        ds_row = self.metadata.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise self._not_found(f"Dataset not found: {dataset_name}")
        set_clauses = []
        params = []
        for field in ("source", "description"):
            if field in updates:
                set_clauses.append(f"{field} = ?")
                params.append(updates[field])
        if set_clauses:
            set_clauses.append("updated_at = ?")
            params.append(_now())
            params.append(ds_row["dataset_id"])
            self.metadata.execute(
                f"UPDATE semantic_datasets SET {', '.join(set_clauses)} WHERE dataset_id = ?",
                params,
            )
        return self.get_dataset(model_name, dataset_name)

    def delete_dataset(self, model_name: str, dataset_name: str) -> None:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        ds_row = self.metadata.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [row["model_id"], dataset_name],
        )
        if ds_row is None:
            raise self._not_found(f"Dataset not found: {dataset_name}")
        self.metadata.execute("DELETE FROM semantic_datasets WHERE dataset_id = ?", [ds_row["dataset_id"]])

    # -- Relationship CRUD --

    def create_relationship(self, model_name: str, rel_data: dict[str, Any]) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rel = Relationship.model_validate(rel_data)
        datasets = {r["name"]: dict(r) for r in self.metadata.query_rows(
            "SELECT name FROM semantic_datasets WHERE model_id = ?", [row["model_id"]]
        )}
        from app.semantic_service_v2.validation import validate_relationship
        validate_relationship({"from": rel.from_, "to": rel.to}, datasets)
        self._insert_relationship(row["model_id"], rel)
        return self.get_relationship(model_name, rel.name)

    def get_relationship(self, model_name: str, rel_name: str) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rel_row = self.metadata.query_one(
            "SELECT * FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [row["model_id"], rel_name],
        )
        if rel_row is None:
            raise self._not_found(f"Relationship not found: {rel_name}")
        from app.semantic_service_v2.storage import _storage_to_relationship
        return _storage_to_relationship(dict(rel_row))

    def list_relationships(self, model_name: str) -> list[dict[str, Any]]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rows = self.metadata.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY name",
            [row["model_id"]],
        )
        from app.semantic_service_v2.storage import _storage_to_relationship
        return [_storage_to_relationship(dict(r)) for r in rows]

    def update_relationship(self, model_name: str, rel_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rel_row = self.metadata.query_one(
            "SELECT * FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [row["model_id"], rel_name],
        )
        if rel_row is None:
            raise self._not_found(f"Relationship not found: {rel_name}")
        set_clauses = []
        params = []
        for field in ("cardinality",):
            if field in updates:
                set_clauses.append(f"{field} = ?")
                params.append(updates[field])
        if set_clauses:
            set_clauses.append("updated_at = ?")
            params.append(_now())
            params.append(rel_row["relationship_id"])
            self.metadata.execute(
                f"UPDATE semantic_relationships SET {', '.join(set_clauses)} WHERE relationship_id = ?",
                params,
            )
        return self.get_relationship(model_name, rel_name)

    def delete_relationship(self, model_name: str, rel_name: str) -> None:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        self.metadata.execute(
            "DELETE FROM semantic_relationships WHERE model_id = ? AND name = ?",
            [row["model_id"], rel_name],
        )

    # -- Metric CRUD --

    def create_metric(self, model_name: str, metric_data: dict[str, Any]) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        metric = Metric.model_validate(metric_data)
        self._insert_metric(row["model_id"], metric)
        return self.get_metric(model_name, metric.name)

    def get_metric(self, model_name: str, metric_name: str) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        m_row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [row["model_id"], metric_name],
        )
        if m_row is None:
            raise self._not_found(f"Metric not found: {metric_name}")
        from app.semantic_service_v2.storage import _storage_to_metric
        return _storage_to_metric(dict(m_row))

    def list_metrics(self, model_name: str) -> list[dict[str, Any]]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        rows = self.metadata.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY name",
            [row["model_id"]],
        )
        from app.semantic_service_v2.storage import _storage_to_metric
        return [_storage_to_metric(dict(r)) for r in rows]

    def update_metric(self, model_name: str, metric_name: str, updates: dict[str, Any]) -> dict[str, Any]:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        m_row = self.metadata.query_one(
            "SELECT * FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [row["model_id"], metric_name],
        )
        if m_row is None:
            raise self._not_found(f"Metric not found: {metric_name}")
        set_clauses = []
        params = []
        for field in ("description",):
            if field in updates:
                set_clauses.append(f"{field} = ?")
                params.append(updates[field])
        if set_clauses:
            set_clauses.append("updated_at = ?")
            params.append(_now())
            params.append(m_row["metric_id"])
            self.metadata.execute(
                f"UPDATE semantic_metrics SET {', '.join(set_clauses)} WHERE metric_id = ?",
                params,
            )
        return self.get_metric(model_name, metric_name)

    def delete_metric(self, model_name: str, metric_name: str) -> None:
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")
        self.metadata.execute(
            "DELETE FROM semantic_metrics WHERE model_id = ? AND name = ?",
            [row["model_id"], metric_name],
        )

    # -- Import --

    def import_osi_document(self, doc_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Import an OSI document as the latest public semantic layer."""
        from app.api.models.osi import OSIDocument
        doc = OSIDocument.model_validate(doc_data)

        # Reject private models in import
        for sm in doc.semantic_model:
            marivo = extract_marivo_extension(sm.custom_extensions, MarivoSemanticModelExtension)
            if marivo and marivo.visibility == "private":
                raise SemanticValidationError([{
                    "message": f"Cannot import private model '{sm.name}' — private models are not part of the versioned public semantic layer",
                    "path": f"semantic_model.{sm.name}.visibility",
                }])

        # Create new semantic version
        self.metadata.execute("INSERT INTO semantic_versions (created_at) VALUES (?)", [_now()])
        version_row = self.metadata.query_one("SELECT last_insert_rowid() as version_id")
        new_version_id = version_row["version_id"]

        results = []
        for sm in doc.semantic_model:
            sm_dict = sm.model_dump(mode="json", by_alias=True)
            sm_dict["visibility"] = "public"
            sm_dict["owner_user"] = None
            self._enrich_model_dict_with_marivo(sm_dict)
            validate_semantic_model(sm_dict)

            now = _now()
            self.metadata.execute(
                """INSERT INTO semantic_models
                   (semantic_version_id, name, description, ai_context, visibility, owner_user, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'public', NULL, ?, ?)""",
                [new_version_id, sm.name, sm.description,
                 model_to_storage(sm).get("ai_context"), now, now],
            )
            model_row = self.metadata.query_one(
                "SELECT * FROM semantic_models WHERE name = ? AND semantic_version_id = ?",
                [sm.name, new_version_id],
            )
            model_id = model_row["model_id"]

            for ds in sm.datasets or []:
                self._insert_dataset(model_id, ds)
            for rel in sm.relationships or []:
                self._insert_relationship(model_id, rel)
            for metric in sm.metrics or []:
                self._insert_metric(model_id, metric)

            results.append(self._assemble_model(model_row))
        return results

    # -- Readiness --

    def get_readiness(self, model_name: str) -> dict[str, Any]:
        """Get readiness status for a semantic model."""
        row = self._get_model_row(model_name)
        if row is None:
            raise self._not_found(f"Semantic model not found: {model_name}")

        readiness_row = self.metadata.query_one(
            "SELECT * FROM semantic_readiness_status WHERE model_id = ?",
            [row["model_id"]],
        )
        if readiness_row:
            return {
                "status": readiness_row["status"],
                "semantic_version_id": row.get("semantic_version_id"),
                "blockers": json.loads(readiness_row["blockers"]) if readiness_row.get("blockers") else [],
            }
        # No readiness assessment yet — assume not_ready
        return {
            "status": "not_ready",
            "semantic_version_id": row.get("semantic_version_id"),
            "blockers": [{"code": "readiness_not_evaluated", "message": "Readiness has not been evaluated yet.", "subject_ref": f"model.{model_name}"}],
        }

    # -- Private helpers --

    def _get_model_row(self, name: str) -> dict[str, Any] | None:
        latest_version = self._latest_version_id()
        row = self.metadata.query_one(
            """SELECT * FROM semantic_models
               WHERE name = ? AND (semantic_version_id = ? OR visibility = 'private')
               ORDER BY model_id DESC LIMIT 1""",
            [name, latest_version],
        )
        return dict(row) if row else None

    def _check_visibility(self, row: dict[str, Any], requesting_user: str | None) -> None:
        if row.get("visibility") == "private" and row.get("owner_user") != requesting_user:
            raise self._not_found(f"Semantic model not found: {row['name']}")

    def _assemble_model(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        model_id = row["model_id"]

        # Get datasets with fields
        ds_rows = self.metadata.query_rows(
            "SELECT * FROM semantic_datasets WHERE model_id = ? ORDER BY name",
            [model_id],
        )
        datasets = []
        for ds in ds_rows:
            ds = dict(ds)
            ds["_fields"] = self._get_fields(ds["dataset_id"])
            from app.semantic_service_v2.storage import _storage_to_dataset
            datasets.append(_storage_to_dataset(ds))

        # Get relationships
        rel_rows = self.metadata.query_rows(
            "SELECT * FROM semantic_relationships WHERE model_id = ? ORDER BY name",
            [model_id],
        )
        from app.semantic_service_v2.storage import _storage_to_relationship
        relationships = [_storage_to_relationship(dict(r)) for r in rel_rows]

        # Get metrics
        metric_rows = self.metadata.query_rows(
            "SELECT * FROM semantic_metrics WHERE model_id = ? ORDER BY name",
            [model_id],
        )
        from app.semantic_service_v2.storage import _storage_to_metric
        metrics = [_storage_to_metric(dict(r)) for r in metric_rows]

        return storage_to_model(row, datasets, relationships, metrics)

    def _get_fields(self, dataset_id: int) -> list[dict[str, Any]]:
        rows = self.metadata.query_rows(
            "SELECT * FROM semantic_fields WHERE dataset_id = ? ORDER BY position",
            [dataset_id],
        )
        result = []
        for r in rows:
            r = dict(r)
            field_dict: dict[str, Any] = {
                "name": r["name"],
                "expression": json.loads(r["expression"]),
            }
            if r.get("is_time"):
                field_dict["dimension"] = {"is_time": True}
            if r.get("label"):
                field_dict["label"] = r["label"]
            if r.get("description"):
                field_dict["description"] = r["description"]
            if r.get("ai_context"):
                field_dict["ai_context"] = json.loads(r["ai_context"])
            if r.get("data_type"):
                from app.semantic_service_v2.extensions import build_custom_extensions
                from app.api.models.marivo_extensions import MarivoFieldExtension
                exts = build_custom_extensions(MarivoFieldExtension(data_type=r["data_type"]))
                field_dict["custom_extensions"] = [e.model_dump(mode="json") for e in exts]
            result.append(field_dict)
        return result

    def _insert_dataset(self, model_id: int, ds: Dataset) -> None:
        now = _now()
        ds_storage = dataset_to_storage(ds, model_id)
        self.metadata.execute(
            """INSERT INTO semantic_datasets
               (model_id, name, source, primary_key, unique_keys, description, ai_context, datasource_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [ds_storage["model_id"], ds_storage["name"], ds_storage["source"],
             ds_storage["primary_key"], ds_storage["unique_keys"], ds_storage["description"],
             ds_storage["ai_context"], ds_storage["datasource_id"], now, now],
        )
        ds_row = self.metadata.query_one(
            "SELECT * FROM semantic_datasets WHERE model_id = ? AND name = ?",
            [model_id, ds.name],
        )
        dataset_id = ds_row["dataset_id"]

        for pos, field in enumerate(ds.fields or [], 1):
            self._insert_field(dataset_id, field, pos)

    def _insert_field(self, dataset_id: int, field: Field, position: int) -> None:
        now = _now()
        f_storage = field_to_storage(field, dataset_id, position)
        self.metadata.execute(
            """INSERT INTO semantic_fields
               (dataset_id, name, expression, is_time, label, description, ai_context, data_type, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [f_storage["dataset_id"], f_storage["name"], f_storage["expression"],
             f_storage["is_time"], f_storage["label"], f_storage["description"],
             f_storage["ai_context"], f_storage["data_type"], f_storage["position"],
             now, now],
        )

    def _insert_relationship(self, model_id: int, rel: Relationship) -> None:
        now = _now()
        r_storage = relationship_to_storage(rel, model_id)
        self.metadata.execute(
            """INSERT INTO semantic_relationships
               (model_id, name, from_dataset, to_dataset, from_columns, to_columns, ai_context, cardinality, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [r_storage["model_id"], r_storage["name"], r_storage["from_dataset"],
             r_storage["to_dataset"], r_storage["from_columns"], r_storage["to_columns"],
             r_storage["ai_context"], r_storage["cardinality"], now, now],
        )

    def _insert_metric(self, model_id: int, metric: Metric) -> None:
        now = _now()
        m_storage = metric_to_storage(metric, model_id)
        self.metadata.execute(
            """INSERT INTO semantic_metrics
               (model_id, name, expression, description, ai_context, observed_dataset, observation_grain,
                primary_time_field, additivity, filters, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [m_storage["model_id"], m_storage["name"], m_storage["expression"],
             m_storage["description"], m_storage["ai_context"], m_storage["observed_dataset"],
             m_storage["observation_grain"], m_storage["primary_time_field"],
             m_storage["additivity"], m_storage["filters"], now, now],
        )

    def _get_or_create_latest_version(self) -> int:
        row = self.metadata.query_one(
            "SELECT version_id FROM semantic_versions ORDER BY version_id DESC LIMIT 1"
        )
        if row:
            return row["version_id"]
        self.metadata.execute("INSERT INTO semantic_versions (created_at) VALUES (?)", [_now()])
        row = self.metadata.query_one("SELECT last_insert_rowid() as version_id")
        return row["version_id"]

    def _latest_version_id(self) -> int | None:
        row = self.metadata.query_one(
            "SELECT version_id FROM semantic_versions ORDER BY version_id DESC LIMIT 1"
        )
        return row["version_id"] if row else None

    def _enrich_model_dict_with_marivo(self, model_dict: dict[str, Any]) -> None:
        """Extract MARIVO extensions from nested objects and add as top-level fields for validation."""
        for ds in model_dict.get("datasets", []):
            for ext in ds.get("custom_extensions", []) or []:
                if ext.get("vendor_name") == "MARIVO":
                    data = json.loads(ext["data"]) if isinstance(ext["data"], str) else ext["data"]
                    ds["datasource_id"] = data.get("datasource_id")
            for field in ds.get("fields", []) or []:
                for ext in field.get("custom_extensions", []) or []:
                    if ext.get("vendor_name") == "MARIVO":
                        data = json.loads(ext["data"]) if isinstance(ext["data"], str) else ext["data"]
                        field["data_type"] = data.get("data_type")
                if field.get("dimension", {}).get("is_time"):
                    field["is_time"] = True
        for rel in model_dict.get("relationships", []) or []:
            for ext in rel.get("custom_extensions", []) or []:
                if ext.get("vendor_name") == "MARIVO":
                    data = json.loads(ext["data"]) if isinstance(ext["data"], str) else ext["data"]
                    rel["cardinality"] = data.get("cardinality")
        for metric in model_dict.get("metrics", []) or []:
            for ext in metric.get("custom_extensions", []) or []:
                if ext.get("vendor_name") == "MARIVO":
                    data = json.loads(ext["data"]) if isinstance(ext["data"], str) else ext["data"]
                    metric["observed_dataset"] = data.get("observed_dataset")
                    metric["observation_grain"] = data.get("observation_grain")
                    metric["primary_time_field"] = data.get("primary_time_field")
                    metric["additivity"] = data.get("additivity")

    @staticmethod
    def _not_found(message: str) -> Exception:
        from fastapi import HTTPException
        return HTTPException(status_code=404, detail=message)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_semantic_v2_service.py -v --no-header -q`
Expected: PASS (may need minor fixes for row mapping)

- [ ] **Step 7: Commit**

```bash
git add app/semantic_service_v2/ tests/test_semantic_v2_service.py
git commit -m "feat: add OSI-aligned semantic service with CRUD and validation"
```

---

## Task 5: API Routes

**Files:**
- Create: `app/api/semantic_v2.py`
- Modify: `app/api/router.py`
- Modify: `app/api/deps.py` (or app_factory)
- Test: `tests/test_semantic_v2_api.py`

- [ ] **Step 1: Write failing API tests**

```python
"""Tests for OSI-aligned semantic API endpoints (v2)."""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.api.app_factory import create_app
    from app.storage.sqlite_metadata import SqliteMetadataStore

    app = create_app()
    # Ensure metadata is initialized
    store = SqliteMetadataStore.in_memory()
    store.initialize_schema()
    # Wire up test service
    from app.semantic_service_v2.service import SemanticModelV2Service
    service = SemanticModelV2Service(store)
    from app.api.deps import set_semantic_v2_service
    set_semantic_v2_service(service)
    return TestClient(app)


def _make_osi_body(**overrides):
    body = {
        "version": "0.1.1",
        "semantic_model": [{
            "name": "retail",
            "description": "Retail analytics",
            "datasets": [{
                "name": "store_sales",
                "source": "tpcds.public.store_sales",
                "fields": [{
                    "name": "ss_sold_date_sk",
                    "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}]},
                }],
            }],
            "custom_extensions": [{"vendor_name": "MARIVO", "data": '{"visibility": "public"}'}],
        }],
    }
    body.update(overrides)
    return body


def test_create_semantic_model(client):
    resp = client.post("/semantic-models", json=_make_osi_body())
    assert resp.status_code == 200 or resp.status_code == 201
    data = resp.json()
    assert "version" in data
    assert data["version"] == "0.1.1"
    assert len(data["semantic_model"]) == 1
    assert data["semantic_model"][0]["name"] == "retail"


def test_list_semantic_models(client):
    client.post("/semantic-models", json=_make_osi_body())
    resp = client.get("/semantic-models")
    assert resp.status_code == 200


def test_get_semantic_model(client):
    client.post("/semantic-models", json=_make_osi_body())
    resp = client.get("/semantic-models/retail")
    assert resp.status_code == 200
    assert resp.json()["semantic_model"][0]["name"] == "retail"


def test_delete_semantic_model(client):
    client.post("/semantic-models", json=_make_osi_body())
    resp = client.delete("/semantic-models/retail")
    assert resp.status_code == 200 or resp.status_code == 204
    resp = client.get("/semantic-models/retail")
    assert resp.status_code == 404


def test_create_dataset(client):
    client.post("/semantic-models", json=_make_osi_body())
    resp = client.post("/semantic-models/retail/datasets", json={
        "name": "date_dim",
        "source": "tpcds.public.date_dim",
    })
    assert resp.status_code == 200 or resp.status_code == 201


def test_get_readiness(client):
    client.post("/semantic-models", json=_make_osi_body())
    resp = client.get("/semantic-models/retail/readiness")
    assert resp.status_code == 200
    assert "status" in resp.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_v2_api.py -v --no-header -q`
Expected: FAIL — routes don't exist

- [ ] **Step 3: Write API routes**

Create `app/api/semantic_v2.py`:

```python
"""OSI-aligned semantic model API routes (v2)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.models.osi import OSIDocument, OSI_SPEC_VERSION

router = APIRouter(prefix="/semantic-models", tags=["semantic-models"])


def _get_service(request: Request):
    return request.app.state.semantic_v2_service


@router.post("")
def create_semantic_model(request: Request, body: dict[str, Any]):
    """Create a semantic model from an OSI document."""
    service = _get_service(request)
    doc = OSIDocument.model_validate(body)
    if len(doc.semantic_model) != 1:
        raise HTTPException(status_code=400, detail="Must provide exactly one semantic_model")
    result = service.create_semantic_model(doc.semantic_model[0].model_dump(mode="json", by_alias=True))
    return {"version": OSI_SPEC_VERSION, "semantic_model": [result]}


@router.get("")
def list_semantic_models(request: Request):
    """List semantic models (summary)."""
    service = _get_service(request)
    models = service.list_semantic_models(requesting_user=None)
    return {"items": [{"name": m["name"], "visibility": m.get("visibility", "public")} for m in models]}


@router.get("/{model}")
def get_semantic_model(request: Request, model: str):
    """Get a semantic model as an OSI document."""
    service = _get_service(request)
    result = service.get_semantic_model(model)
    return {"version": OSI_SPEC_VERSION, "semantic_model": [result]}


@router.put("/{model}")
def update_semantic_model(request: Request, model: str, body: dict[str, Any]):
    """Update a semantic model's top-level fields."""
    service = _get_service(request)
    result = service.update_semantic_model(model, body)
    return {"version": OSI_SPEC_VERSION, "semantic_model": [result]}


@router.delete("/{model}")
def delete_semantic_model(request: Request, model: str):
    """Delete a semantic model."""
    service = _get_service(request)
    service.delete_semantic_model(model)
    return {"status": "deleted"}


@router.post("/{model}/datasets")
def create_dataset(request: Request, model: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.create_dataset(model, body)


@router.get("/{model}/datasets")
def list_datasets(request: Request, model: str):
    service = _get_service(request)
    return service.list_datasets(model)


@router.get("/{model}/datasets/{name}")
def get_dataset(request: Request, model: str, name: str):
    service = _get_service(request)
    return service.get_dataset(model, name)


@router.put("/{model}/datasets/{name}")
def update_dataset(request: Request, model: str, name: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.update_dataset(model, name, body)


@router.delete("/{model}/datasets/{name}")
def delete_dataset(request: Request, model: str, name: str):
    service = _get_service(request)
    service.delete_dataset(model, name)
    return {"status": "deleted"}


@router.post("/{model}/relationships")
def create_relationship(request: Request, model: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.create_relationship(model, body)


@router.get("/{model}/relationships")
def list_relationships(request: Request, model: str):
    service = _get_service(request)
    return service.list_relationships(model)


@router.get("/{model}/relationships/{name}")
def get_relationship(request: Request, model: str, name: str):
    service = _get_service(request)
    return service.get_relationship(model, name)


@router.put("/{model}/relationships/{name}")
def update_relationship(request: Request, model: str, name: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.update_relationship(model, name, body)


@router.delete("/{model}/relationships/{name}")
def delete_relationship(request: Request, model: str, name: str):
    service = _get_service(request)
    service.delete_relationship(model, name)
    return {"status": "deleted"}


@router.post("/{model}/metrics")
def create_metric(request: Request, model: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.create_metric(model, body)


@router.get("/{model}/metrics")
def list_metrics(request: Request, model: str):
    service = _get_service(request)
    return service.list_metrics(model)


@router.get("/{model}/metrics/{name}")
def get_metric(request: Request, model: str, name: str):
    service = _get_service(request)
    return service.get_metric(model, name)


@router.put("/{model}/metrics/{name}")
def update_metric(request: Request, model: str, name: str, body: dict[str, Any]):
    service = _get_service(request)
    return service.update_metric(model, name, body)


@router.delete("/{model}/metrics/{name}")
def delete_metric(request: Request, model: str, name: str):
    service = _get_service(request)
    service.delete_metric(model, name)
    return {"status": "deleted"}


@router.post("/import")
def import_osi_document(request: Request, body: dict[str, Any]):
    """Import public semantic models from an OSI document."""
    service = _get_service(request)
    results = service.import_osi_document(body)
    return {"version": OSI_SPEC_VERSION, "semantic_model": results}


@router.get("/{model}/readiness")
def get_readiness(request: Request, model: str):
    """Get readiness status + blockers."""
    service = _get_service(request)
    return service.get_readiness(model)
```

- [ ] **Step 4: Wire up router and dependency**

In `app/api/router.py`, replace `semantic.router` with `semantic_v2.router`:

```python
from app.api import semantic_v2
# ...
def include_api_routers(app: FastAPI) -> None:
    for router in (
        # ... keep all non-semantic routers ...
        semantic_v2.router,
        # ... rest ...
    ):
        app.include_router(router)
```

Add to `app/api/deps.py` (or equivalent):

```python
_semantic_v2_service = None

def set_semantic_v2_service(service):
    global _semantic_v2_service
    _semantic_v2_service = service
```

Update `app/api/app_factory.py` to initialize the service on `app.state`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/test_semantic_v2_api.py -v --no-header -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/api/semantic_v2.py app/api/router.py app/api/deps.py app/api/app_factory.py tests/test_semantic_v2_api.py
git commit -m "feat: add OSI-aligned semantic API routes (v2)"
```

---

## Task 6: Delete Old Code

**Files:**
- Delete: All files listed in "Deleted Files" section above
- Modify: `app/api/models/__init__.py` (remove old exports)
- Modify: Any imports referencing deleted modules

- [ ] **Step 1: Remove old semantic service and related modules**

```bash
rm -rf app/semantic_service/
rm -rf app/semantic_revision/
rm -rf app/semantic_readiness/
rm -rf app/semantic_runtime/
rm app/api/models/entity.py
rm app/api/models/metric.py
rm app/api/models/dimension.py
rm app/api/models/time.py
rm app/api/models/binding.py
rm app/api/models/predicate.py
rm app/api/models/process_object.py
rm app/api/models/enum_set.py
rm app/api/models/compatibility_profile.py
rm app/api/models/domain.py
rm app/api/models/semantic_batch.py
rm app/api/models/catalog.py
rm app/api/semantic.py
rm app/analysis_core/capability_profiles.py
rm app/analysis_core/predicate_validator.py
rm app/analysis_core/predicate_lowering_boundary.py
```

- [ ] **Step 2: Fix all broken imports**

Update `app/api/models/__init__.py` to only export v2 models.
Update `app/service.py` to use new semantic_service_v2.
Fix any remaining import errors throughout the codebase.

- [ ] **Step 3: Delete old tests**

```bash
rm tests/test_semantic_service.py
rm tests/test_semantic_typed_api.py
rm tests/test_semantic_typed_end_to_end.py
rm tests/test_semantic.py
rm tests/test_semantic_domain_catalog.py
rm tests/test_semantic_readiness.py
rm tests/test_semantic_revision_dependency_plan.py
rm tests/test_semantic_runtime.py
rm tests/test_semantic_schema.py
rm tests/test_typed_bindings.py
rm tests/test_api_models_entity.py
rm tests/test_api_models_metric.py
rm tests/test_api_models_dimension.py
rm tests/test_api_models_time.py
rm tests/test_api_models_binding.py
rm tests/test_api_models_predicate.py
rm tests/test_api_models_process_object.py
rm tests/test_api_models_enum_set.py
rm tests/test_api_models_compatibility_profile.py
rm tests/test_predicate_crud.py
rm tests/test_predicate_contract_validator.py
rm tests/test_predicate_lineage.py
rm tests/test_predicate_lineage_reuse.py
rm tests/test_predicate_conflict.py
rm tests/test_predicate_usage_validation.py
rm tests/test_predicate_lowering_boundary.py
rm tests/test_normalized_predicate_input.py
rm tests/test_metric_revision_classification.py
rm tests/test_metric_dimension_resolution.py
rm tests/test_time_contracts.py
rm tests/test_additivity_constraints.py
rm tests/test_version_policy.py
rm tests/test_publish_switch.py
rm tests/test_compiler_typed_resolution.py
rm tests/test_lowering_precheck.py
rm tests/test_read_surface_boundaries.py
rm tests/test_scope_validation.py
rm tests/test_static_sql_boundaries.py
```

- [ ] **Step 4: Run remaining tests**

Run: `.venv/bin/pytest tests/ -v --no-header -q 2>&1 | head -80`
Expected: Some tests may fail due to broken imports. Fix iteratively.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: delete legacy semantic layer code and tests"
```

---

## Task 7: Fix Downstream Dependencies

**Files:**
- Modify: `app/service.py` — update to use semantic_service_v2
- Modify: `app/analysis_core/compiler.py` — adapt for flat expressions + MARIVO extensions
- Modify: `app/analysis_core/typed_resolution.py` — simplify for new object model
- Modify: Other files that import from deleted modules

- [ ] **Step 1: Identify all broken imports**

Run: `.venv/bin/python -c "import app.service" 2>&1`
Fix each import error iteratively.

- [ ] **Step 2: Update app/service.py**

Replace old semantic runtime/service imports with new semantic_service_v2 imports.
The compiler still needs dataset/metric/field resolution — adapt to query from new tables.

- [ ] **Step 3: Update compiler and analysis_core**

The compiler previously resolved metrics through the old typed resolution system.
Adapt to resolve through the new service:
- Metric → observed_dataset, expression, additivity, filters
- Dataset → source, fields with data_type
- Field → expression, is_time, data_type

- [ ] **Step 4: Run typecheck**

Run: `make typecheck`
Expected: No errors (or minimal errors in unrelated code)

- [ ] **Step 5: Run remaining tests**

Run: `make test`
Expected: Core tests pass (some integration tests may need further updates)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: adapt compiler and downstream code for OSI-aligned semantic layer"
```

---

## Task 8: Final Integration Verification

**Files:**
- All modified files

- [ ] **Step 1: Run full typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 2: Run full test suite**

Run: `make test`
Expected: PASS (or only pre-existing unrelated failures)

- [ ] **Step 3: Run lint**

Run: `make lint`
Expected: PASS

- [ ] **Step 4: Manual smoke test — create a semantic model via API**

```bash
curl -X POST http://localhost:8000/semantic-models \
  -H 'Content-Type: application/json' \
  -d '{
    "version": "0.1.1",
    "semantic_model": [{
      "name": "retail",
      "datasets": [{
        "name": "store_sales",
        "source": "tpcds.public.store_sales",
        "fields": [{
          "name": "ss_sold_date_sk",
          "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ss_sold_date_sk"}]},
          "custom_extensions": [{"vendor_name": "MARIVO", "data": "{\"data_type\": \"integer\"}"}]
        }]
      }],
      "custom_extensions": [{"vendor_name": "MARIVO", "data": "{\"visibility\": \"public\"}"}]
    }]
  }'
```

Expected: 200 with OSI-conformant response including version, semantic_model, and custom_extensions.

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -A
git commit -m "fix: final integration fixes for OSI v2 semantic layer"
```
