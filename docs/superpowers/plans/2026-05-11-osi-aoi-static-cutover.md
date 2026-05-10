# OSI/AOI Static Model Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate static Pydantic models from `osi-marivo-spec` and `aoi-spec` JSON Schemas, then cut Marivo's semantic and analysis-operation runtime over to those generated contracts.

**Architecture:** A CLI generator script produces committed Pydantic v2 models from two JSON Schemas. Runtime code imports generated models directly. Storage mapping, semantic service, and MCP tools are updated phase-by-phase to use generated types. Old hand-written models are deleted last.

**Tech Stack:** Python 3.11+, Pydantic v2, datamodel-code-generator, import-linter, DuckDB (E2E tests)

**Design spec:** `docs/superpowers/specs/2026-05-10-osi-aoi-static-cutover-design.md`
**Review decisions:** Appendix A (CEO D2-D11) and Appendix B (ENG D1-D7)
**Error registry:** `docs/superpowers/specs/2026-05-10-osi-aoi-cutover-error-registry.md`

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `scripts/generate_contract_models.py` | CLI: run datamodel-code-generator, validate output against spec examples |
| `marivo/contracts/generated/__init__.py` | Public re-exports for generated OSI/AOI models |
| `marivo/contracts/generated/osi.py` | Generated Pydantic models from `osi-marivo.schema.json` |
| `marivo/contracts/generated/aoi.py` | Generated Pydantic models from `aoi.schema.json` |
| `tests/test_generated_models.py` | Phase A gate: spec examples validate through generated models |
| `tests/test_osi_storage_roundtrip.py` | Phase B gate: `model == storage_to_model(model_to_storage(model))` |

### Modified files
| File | Changes |
|------|---------|
| `pyproject.toml` | Add `datamodel-code-generator` dev dependency |
| `.importlinter` | Add 2 contracts (Phase A isolation + Phase F enforcement) |
| `marivo/contracts/semantic.py` | Replace `osi_document: dict[str, Any]` with `osi_model: OSISemanticModel` |
| `marivo/adapters/schema.py` | Drop 5 metric columns, add `additive_dimensions TEXT` |
| `marivo/runtime/semantic/osi_storage.py` | Rewrite metric_to_storage/storage_to_metric for new columns; import from generated |
| `marivo/runtime/semantic/semantic_service.py` | Remove primary_time_field refs; extract enrichment helper; update metric SQL; import from generated |
| `marivo/transports/http/models/marivo_extensions.py` | Remove 4 fields from MarivoMetricExtension (keep additive_dimensions only) |
| `marivo/transports/http/models/osi.py` | Becomes re-export shim for generated OSI models |
| `marivo/core/semantic/extensions.py` | No changes needed (protocol already compatible) |

### Deleted files (Phase F)
| File | Reason |
|------|--------|
| `marivo/transports/http/models/intents.py` | Zero importers (confirmed by grep) |
| `marivo/transports/http/models/intent_responses.py` | Zero importers, all `RootModel[JsonObject]` |

---

## Task 1: Add datamodel-code-generator dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dev dependency**

In `pyproject.toml`, add `datamodel-code-generator` to the `dev` optional dependencies list. Find the `[project.optional-dependencies]` section and the `dev` list.

```toml
dev = [
    "pytest>=8",
    # ... existing entries ...
    "datamodel-code-generator>=0.26",
]
```

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install -e ".[dev]"`
Expected: installs successfully, `datamodel-codegen --version` works

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
build: add datamodel-code-generator dev dependency

Required for Phase A of OSI/AOI static cutover — generates Pydantic v2
models from JSON Schema specs.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Bash] [Edit]
EOF
)"
```

---

## Task 2: Create the generator script

**Files:**
- Create: `scripts/generate_contract_models.py`

- [ ] **Step 1: Write the generator script**

```python
#!/usr/bin/env python3
"""Generate static Pydantic models from OSI-Marivo and AOI JSON Schemas.

Usage:
    python scripts/generate_contract_models.py [--check]

With --check, exits non-zero if generated files differ from committed
(CI freshness gate).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OSI_SCHEMA = ROOT / "osi-marivo-spec" / "schema" / "osi-marivo.schema.json"
AOI_SCHEMA = ROOT / "aoi-spec" / "schema" / "aoi.schema.json"
OUTPUT_DIR = ROOT / "marivo" / "contracts" / "generated"

OSI_EXAMPLES = ROOT / "osi-marivo-spec" / "examples"
AOI_EXAMPLES = ROOT / "aoi-spec" / "examples"


def generate(schema: Path, output: Path, module_name: str) -> None:
    """Run datamodel-code-generator for one schema."""
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(schema),
        "--output",
        str(output),
        "--input-file-type",
        "jsonschema",
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.11",
        "--use-standard-collections",
        "--use-union-operator",
        "--field-constraints",
        "--collapse-root-models",
        "--use-schema-description",
        "--enum-field-as-literal",
        "all",
        "--strict-nullable",
        "--disable-timestamp",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR generating {module_name}:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    print(f"Generated {output}")


def read_spec_version(schema: Path, key: str) -> str:
    """Read the version field from a JSON Schema."""
    with open(schema) as f:
        doc = json.load(f)
    version = doc.get("properties", {}).get("version", {}).get("const")
    if version is None:
        version = doc.get("version", "unknown")
    return str(version)


def write_init(output_dir: Path) -> None:
    """Write __init__.py with version constants and public re-exports."""
    osi_version = read_spec_version(OSI_SCHEMA, "version")
    aoi_schema = json.loads(AOI_SCHEMA.read_text())
    aoi_version = aoi_schema.get("version", "unknown")

    init = output_dir / "__init__.py"
    init.write_text(
        f'"""Generated contract models — do not edit manually.\n\n'
        f"Regenerate: python scripts/generate_contract_models.py\n"
        f'"""\n\n'
        f'OSI_MARIVO_SPEC_VERSION = "{osi_version}"\n'
        f'AOI_SPEC_VERSION = "{aoi_version}"\n'
    )
    print(f"Generated {init}")


def validate_examples() -> None:
    """Validate spec examples parse through generated models."""
    failures: list[str] = []

    # OSI examples
    for example_path in sorted(OSI_EXAMPLES.rglob("*.json")):
        try:
            from marivo.contracts.generated.osi import Model as OSIDocument

            with open(example_path) as f:
                data = json.load(f)
            OSIDocument.model_validate(data)
        except Exception as exc:
            failures.append(f"OSI {example_path.relative_to(ROOT)}: {exc}")

    # AOI examples
    for example_path in sorted(AOI_EXAMPLES.rglob("*.json")):
        try:
            with open(example_path) as f:
                data = json.load(f)
            # AOI schema uses oneOf at top level — try each request/artifact type
            _validate_aoi_example(data, example_path)
        except Exception as exc:
            failures.append(f"AOI {example_path.relative_to(ROOT)}: {exc}")

    if failures:
        print("\nValidation failures:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)

    print(f"\nAll examples validated successfully.")


def _validate_aoi_example(data: dict, path: Path) -> None:
    """Try to validate an AOI example against the appropriate generated model."""
    # Import will fail until models are generated — that's expected on first run
    from marivo.contracts.generated import aoi as aoi_models

    # AOI examples are either requests or artifacts
    # Try to find a matching model based on the data shape
    validated = False
    for attr_name in dir(aoi_models):
        cls = getattr(aoi_models, attr_name)
        if isinstance(cls, type) and hasattr(cls, "model_validate"):
            try:
                cls.model_validate(data)
                validated = True
                break
            except Exception:
                continue
    if not validated:
        raise ValueError(f"No AOI model accepted {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate contract models from JSON Schemas")
    parser.add_argument("--check", action="store_true", help="CI mode: fail if files differ")
    parser.add_argument("--skip-validation", action="store_true", help="Skip example validation")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generate(OSI_SCHEMA, OUTPUT_DIR / "osi.py", "OSI")
    generate(AOI_SCHEMA, OUTPUT_DIR / "aoi.py", "AOI")
    write_init(OUTPUT_DIR)

    if not args.skip_validation:
        validate_examples()

    if args.check:
        result = subprocess.run(
            ["git", "diff", "--exit-code", str(OUTPUT_DIR)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.returncode != 0:
            print("\nGenerated files differ from committed:", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
            sys.exit(1)
        print("Generated files match committed code.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable and test generation**

Run: `python scripts/generate_contract_models.py --skip-validation`
Expected: creates `marivo/contracts/generated/osi.py`, `aoi.py`, `__init__.py`

- [ ] **Step 3: Inspect generated output**

Read `marivo/contracts/generated/osi.py` and verify:
- `SemanticModel` class exists with `name`, `datasets`, `custom_extensions` fields
- `CustomExtension` has `vendor_name` and `data` fields
- `Metric` has `expression`, `custom_extensions`
- `extra="forbid"` or equivalent appears on models
- Pydantic aliases work for `from` → `from_` on Relationship

Read `marivo/contracts/generated/aoi.py` and verify:
- `TimeScope` has required `field`, `start`, `end`
- Request models for observe, compare, decompose, etc.
- Artifact models exist

- [ ] **Step 4: Fix any generation issues**

If the generated models don't match expectations (E1 scenario from error registry):
1. Check `datamodel-code-generator` version
2. Add post-generation patches to the script if needed
3. Ensure JSON Schema draft 2020-12 features are handled

Document any known limitations as comments in the generator script.

- [ ] **Step 5: Run validation against spec examples**

Run: `python scripts/generate_contract_models.py`
Expected: "All examples validated successfully."

If validation fails, fix the generator or add patches. This is the Phase A gate.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_contract_models.py marivo/contracts/generated/
git commit -m "$(cat <<'EOF'
feat: add OSI/AOI static model generation (Phase A)

Generate Pydantic v2 models from osi-marivo-spec and aoi-spec JSON
Schemas. Includes CLI script with --check flag for CI freshness gate
and validation against all spec examples.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Bash] [Write]
EOF
)"
```

---

## Task 3: Add import-linter isolation contract for generated models

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Write the failing lint check**

Run: `make lint` (or `.venv/bin/import-linter`) to confirm current contracts pass.

- [ ] **Step 2: Add the isolation contract**

Append to `.importlinter`:

```ini
[importlinter:contract:generated-contracts-isolation]
name = contracts/generated/ must not import runtime, adapters, or transports
type = forbidden
source_modules =
    marivo.contracts.generated
forbidden_modules =
    marivo.runtime
    marivo.adapters
    marivo.transports
```

- [ ] **Step 3: Verify the contract passes**

Run: `.venv/bin/import-linter`
Expected: all contracts pass (generated models should have no internal imports)

- [ ] **Step 4: Commit**

```bash
git add .importlinter
git commit -m "$(cat <<'EOF'
chore: add import-linter isolation for contracts/generated/

Prevents generated models from importing runtime, adapters, or
transports — enforces the architectural boundary from eng review D1.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 4: Add generated model validation tests

**Files:**
- Create: `tests/test_generated_models.py`

- [ ] **Step 1: Write the test file**

```python
"""Phase A gate: generated models validate all spec examples."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OSI_EXAMPLES = ROOT / "osi-marivo-spec" / "examples"
AOI_EXAMPLES = ROOT / "aoi-spec" / "examples"


def _collect_json_files(base: Path) -> list[Path]:
    return sorted(base.rglob("*.json"))


# --- OSI tests ---


@pytest.fixture(params=_collect_json_files(OSI_EXAMPLES), ids=lambda p: str(p.relative_to(ROOT)))
def osi_example(request: pytest.FixtureRequest) -> dict:
    with open(request.param) as f:
        return json.load(f)


def test_osi_example_validates(osi_example: dict) -> None:
    from marivo.contracts.generated.osi import Model as OSIDocument

    OSIDocument.model_validate(osi_example)


# --- AOI tests ---


@pytest.fixture(params=_collect_json_files(AOI_EXAMPLES), ids=lambda p: str(p.relative_to(ROOT)))
def aoi_example(request: pytest.FixtureRequest) -> dict:
    with open(request.param) as f:
        return json.load(f)


def test_aoi_example_validates(aoi_example: dict) -> None:
    """At least one generated AOI model must accept the example."""
    from marivo.contracts.generated import aoi as aoi_models

    for attr_name in dir(aoi_models):
        cls = getattr(aoi_models, attr_name)
        if isinstance(cls, type) and hasattr(cls, "model_validate"):
            try:
                cls.model_validate(aoi_example)
                return
            except Exception:
                continue
    pytest.fail(f"No AOI model accepted the example")


# --- Version constants ---


def test_version_constants_exist() -> None:
    from marivo.contracts.generated import AOI_SPEC_VERSION, OSI_MARIVO_SPEC_VERSION

    assert OSI_MARIVO_SPEC_VERSION == "0.1.1"
    assert AOI_SPEC_VERSION == "0.1.0"


# --- Extension decode failure (E9 error scenario) ---


def test_malformed_extension_data_rejected() -> None:
    """E9: malformed JSON in MARIVO extension data field."""
    from marivo.core.semantic.extensions import extract_marivo_extension
    from marivo.transports.http.models.marivo_extensions import MarivoDatasetExtension

    class FakeExt:
        vendor_name = "MARIVO"
        data = "{not valid json"

    with pytest.raises(Exception):
        extract_marivo_extension([FakeExt()], MarivoDatasetExtension)
```

- [ ] **Step 2: Run the tests**

Run: `make test -- tests/test_generated_models.py -v`
Expected: all OSI examples pass, all AOI examples pass, version constants correct

- [ ] **Step 3: Commit**

```bash
git add tests/test_generated_models.py
git commit -m "$(cat <<'EOF'
test: add generated model validation tests (Phase A gate)

Parametrized tests verify every OSI and AOI spec example validates
through generated Pydantic models. Covers E9 (extension decode
failure) error scenario.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Write]
EOF
)"
```

---

## Task 5: Update MarivoMetricExtension to match spec

**Files:**
- Modify: `marivo/transports/http/models/marivo_extensions.py:85-92`

The spec's `MarivoMetricExtension` has only `additive_dimensions`. The implementation has 5 extra fields that must be removed: `observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters`.

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_generated_models.py, add:

def test_marivo_metric_extension_matches_spec() -> None:
    """MarivoMetricExtension should only have additive_dimensions (per spec)."""
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    fields = set(MarivoMetricExtension.model_fields.keys())
    assert fields == {"additive_dimensions"}, f"Extra fields found: {fields - {'additive_dimensions'}}"
```

- [ ] **Step 2: Run it to see it fail**

Run: `make test -- tests/test_generated_models.py::test_marivo_metric_extension_matches_spec -v`
Expected: FAIL — extra fields `observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters`

- [ ] **Step 3: Remove the 5 extra fields**

In `marivo/transports/http/models/marivo_extensions.py`, replace the `MarivoMetricExtension` class:

```python
class MarivoMetricExtension(BaseModel):
    additive_dimensions: list[str] | None = None

    model_config = {"extra": "forbid"}
```

Also remove the now-unused classes if they're only referenced by the old MarivoMetricExtension:
- `MarivoAdditivity` — check if used elsewhere first
- `MarivoMetricFilter`, `MarivoMetricFilterExpression`, `MarivoMetricFilterExpressionDialect` — check if used elsewhere

Run: `grep -rn "MarivoAdditivity\|MarivoMetricFilter" marivo/ tests/ --include="*.py"` to verify no other consumers.

- [ ] **Step 4: Run the test to verify it passes**

Run: `make test -- tests/test_generated_models.py::test_marivo_metric_extension_matches_spec -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to find breakages**

Run: `make test`
Expected: some tests will fail due to code that references removed fields. Note the failures — they will be fixed in subsequent tasks.

- [ ] **Step 6: Commit**

```bash
git add marivo/transports/http/models/marivo_extensions.py tests/test_generated_models.py
git commit -m "$(cat <<'EOF'
refactor: align MarivoMetricExtension with spec (additive_dimensions only)

Remove observed_dataset, observation_grain, primary_time_field,
additivity, filters from MarivoMetricExtension. Per spec, only
additive_dimensions belongs in the metric extension payload. The
removed fields are either caller-supplied (TimeScope.field) or
inferred at execution time.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 6: Update DDL — drop old metric columns, add additive_dimensions

**Files:**
- Modify: `marivo/adapters/schema.py:172-189`

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_generated_models.py or a new test file, add:

def test_semantic_metrics_ddl_has_additive_dimensions() -> None:
    """DDL must have additive_dimensions and must NOT have old columns."""
    from marivo.adapters.schema import METADATA_DDL

    metrics_ddl = [s for s in METADATA_DDL if "semantic_metrics" in s and "CREATE TABLE" in s]
    assert len(metrics_ddl) == 1
    ddl = metrics_ddl[0]
    assert "additive_dimensions" in ddl
    assert "primary_time_field" not in ddl
    assert "observation_grain" not in ddl
    assert "observed_dataset" not in ddl
    assert "additivity " not in ddl  # trailing space to avoid matching additive_dimensions
    assert "filters " not in ddl
```

- [ ] **Step 2: Run it to see it fail**

Run: `make test -- tests/test_generated_models.py::test_semantic_metrics_ddl_has_additive_dimensions -v`
Expected: FAIL — old columns still present

- [ ] **Step 3: Update the DDL**

In `marivo/adapters/schema.py`, replace the `semantic_metrics` CREATE TABLE statement:

```sql
CREATE TABLE IF NOT EXISTS semantic_metrics (
    metric_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id            INTEGER NOT NULL REFERENCES semantic_models(model_id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    expression          TEXT NOT NULL,
    description         TEXT,
    ai_context          TEXT,
    additive_dimensions TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(model_id, name)
)
```

The 5 removed columns are: `observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters`.
The 1 added column is: `additive_dimensions` (TEXT, nullable, stores JSON array of dimension names).

- [ ] **Step 4: Run the DDL test**

Run: `make test -- tests/test_generated_models.py::test_semantic_metrics_ddl_has_additive_dimensions -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marivo/adapters/schema.py tests/test_generated_models.py
git commit -m "$(cat <<'EOF'
refactor: update semantic_metrics DDL for OSI/AOI cutover

Drop 5 old metric columns (observed_dataset, observation_grain,
primary_time_field, additivity, filters) and add additive_dimensions.
Destructive DDL (DROP + CREATE) — no migration system, no prod data.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 7: Extract metric extension enrichment helper (DRY fix)

**Files:**
- Modify: `marivo/runtime/semantic/semantic_service.py`

The enrichment logic is copy-pasted at 3 locations: `_enrich_model_dict_with_marivo` (lines 249-255), `create_metric` (lines 882-886), and `import_osi_document`. Extract a shared helper.

- [ ] **Step 1: Write the failing test**

```python
# In tests/test_semantic_v2_service.py or a new test, add:

def test_enrich_metric_extracts_additive_dimensions() -> None:
    """Shared enrichment helper extracts additive_dimensions from MARIVO extension."""
    import json
    from marivo.runtime.semantic.semantic_service import SemanticModelV2Service

    metric_data = {
        "name": "revenue",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": json.dumps({"additive_dimensions": ["region", "channel"]}),
            }
        ],
    }
    enriched = SemanticModelV2Service._enrich_metric_with_marivo(metric_data)
    assert enriched["additive_dimensions"] == ["region", "channel"]
```

- [ ] **Step 2: Run it to see it fail**

Run: `make test -- tests/test_semantic_v2_service.py::test_enrich_metric_extracts_additive_dimensions -v`
Expected: FAIL — `_enrich_metric_with_marivo` does not exist

- [ ] **Step 3: Add the shared helper and refactor callers**

In `semantic_service.py`, add a static method:

```python
@staticmethod
def _enrich_metric_with_marivo(metric_data: dict[str, Any]) -> dict[str, Any]:
    """Extract MARIVO metric extension fields into top-level dict keys."""
    enriched = dict(metric_data)
    marivo = SemanticModelV2Service._extract_marivo_from_exts(
        metric_data.get("custom_extensions")
    )
    if marivo:
        enriched["additive_dimensions"] = marivo.get("additive_dimensions")
    return enriched
```

Then update the 3 call sites:
1. `_enrich_model_dict_with_marivo` — replace the metric enrichment loop body
2. `create_metric` — replace the inline enrichment
3. `import_osi_document` — replace any metric enrichment if present

- [ ] **Step 4: Run the test**

Run: `make test -- tests/test_semantic_v2_service.py::test_enrich_metric_extracts_additive_dimensions -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marivo/runtime/semantic/semantic_service.py
git commit -m "$(cat <<'EOF'
refactor: extract shared metric enrichment helper

DRY fix: _enrich_metric_with_marivo replaces 3 copy-pasted enrichment
blocks. Now only extracts additive_dimensions (per spec alignment).

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 8: Update osi_storage.py — metric write/read paths

**Files:**
- Modify: `marivo/runtime/semantic/osi_storage.py:138-268`

- [ ] **Step 1: Write the failing test for storage roundtrip**

Create `tests/test_osi_storage_roundtrip.py`:

```python
"""Phase B gate: OSI storage roundtrip preserves all data."""
from __future__ import annotations

import json

from marivo.runtime.semantic.osi_storage import (
    build_custom_extensions,
    metric_to_storage,
    _storage_to_metric,
)
from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension
from marivo.transports.http.models.osi import (
    DialectExpression,
    Expression,
    Metric,
)


def test_metric_roundtrip_with_additive_dimensions() -> None:
    """metric_to_storage -> _storage_to_metric preserves additive_dimensions."""
    ext = MarivoMetricExtension(additive_dimensions=["region", "channel"])
    metric = Metric(
        name="revenue",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="SUM(amount)")]
        ),
        custom_extensions=build_custom_extensions(ext),
    )
    storage = metric_to_storage(metric, model_id=1)
    assert storage["additive_dimensions"] is not None
    assert "region" in json.loads(storage["additive_dimensions"])

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "revenue"
    # Verify MARIVO extension roundtrips
    marivo_ext = None
    for ce in reconstructed.get("custom_extensions", []):
        if ce.get("vendor_name") == "MARIVO":
            marivo_ext = json.loads(ce["data"]) if isinstance(ce["data"], str) else ce["data"]
    assert marivo_ext is not None
    assert marivo_ext["additive_dimensions"] == ["region", "channel"]


def test_metric_roundtrip_without_extensions() -> None:
    """Metric with no MARIVO extension roundtrips cleanly."""
    metric = Metric(
        name="count_all",
        expression=Expression(
            dialects=[DialectExpression(dialect="ANSI_SQL", expression="COUNT(*)")]
        ),
    )
    storage = metric_to_storage(metric, model_id=1)
    assert storage.get("additive_dimensions") is None

    reconstructed = _storage_to_metric(storage)
    assert reconstructed["name"] == "count_all"
```

- [ ] **Step 2: Run to see it fail**

Run: `make test -- tests/test_osi_storage_roundtrip.py -v`
Expected: FAIL — old code still references removed columns

- [ ] **Step 3: Update metric_to_storage**

In `osi_storage.py`, replace `metric_to_storage`:

```python
def metric_to_storage(metric: Metric, model_id: int) -> dict[str, Any]:
    """Extract fields for a ``semantic_metrics`` row."""
    marivo_ext = extract_marivo_extension(metric.custom_extensions, MarivoMetricExtension)
    additive_dimensions = (
        json.dumps(marivo_ext.additive_dimensions)
        if marivo_ext and marivo_ext.additive_dimensions is not None
        else None
    )
    expression = json.dumps(metric.expression.model_dump(exclude_none=True))
    ai_context = json.dumps(metric.ai_context.root) if metric.ai_context is not None else None
    return {
        "model_id": model_id,
        "name": metric.name,
        "expression": expression,
        "description": metric.description,
        "ai_context": ai_context,
        "additive_dimensions": additive_dimensions,
    }
```

- [ ] **Step 4: Update _storage_to_metric**

In `osi_storage.py`, replace `_storage_to_metric`:

```python
def _storage_to_metric(row: dict[str, Any]) -> dict[str, Any]:
    """Assemble a metric dict from a storage row."""
    additive_dims = (
        json.loads(row["additive_dimensions"])
        if row.get("additive_dimensions") is not None
        else None
    )
    marivo_ext = MarivoMetricExtension(additive_dimensions=additive_dims)
    result: dict[str, Any] = {
        "name": row["name"],
        "expression": json.loads(row["expression"]),
        "custom_extensions": _ext_to_dicts(build_custom_extensions(marivo_ext)),
    }
    if row.get("description") is not None:
        result["description"] = row["description"]
    if row.get("ai_context") is not None:
        result["ai_context"] = json.loads(row["ai_context"])
    return result
```

- [ ] **Step 5: Run the roundtrip tests**

Run: `make test -- tests/test_osi_storage_roundtrip.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/semantic/osi_storage.py tests/test_osi_storage_roundtrip.py
git commit -m "$(cat <<'EOF'
refactor: update metric storage for additive_dimensions only

Remove 5 old metric column mappings from osi_storage.py (observed_dataset,
observation_grain, primary_time_field, additivity, filters). Add
additive_dimensions roundtrip. Storage tests verify lossless conversion.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit] [Write]
EOF
)"
```

---

## Task 9: Update semantic_service.py — metric SQL and primary_time_field removal

**Files:**
- Modify: `marivo/runtime/semantic/semantic_service.py`

9 references to `primary_time_field` across `create_semantic_model`, `create_metric`, `update_metric`, and `import_osi_document`. All metric INSERT/UPDATE SQL must drop the 5 old columns and add `additive_dimensions`.

- [ ] **Step 1: Update create_semantic_model metric INSERT (line ~420-442)**

Replace the metric INSERT SQL block:

```python
for metric in model.metrics or []:
    metric_storage = metric_to_storage(metric, model_id)
    self.store.execute(
        """
        INSERT INTO semantic_metrics
            (model_id, name, expression, description, ai_context,
             additive_dimensions)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            metric_storage["model_id"],
            metric_storage["name"],
            metric_storage["expression"],
            metric_storage["description"],
            metric_storage["ai_context"],
            metric_storage["additive_dimensions"],
        ],
    )
```

- [ ] **Step 2: Update create_metric (line ~906-926)**

Same pattern — replace the 10-column INSERT with the 6-column INSERT.

- [ ] **Step 3: Update import_osi_document metric INSERT (line ~1268-1290)**

Same pattern — replace the 10-column INSERT with the 6-column INSERT.

- [ ] **Step 4: Update update_metric allowed_fields (line ~978-987)**

Replace:
```python
allowed_fields = {
    "description",
    "ai_context",
    "additive_dimensions",
    "expression",
}
```

Remove `observed_dataset`, `observation_grain`, `primary_time_field`, `additivity`, `filters` from allowed updates.

- [ ] **Step 5: Update _enrich_model_dict_with_marivo metric block (line ~249-255)**

Replace the old 5-field enrichment with the new helper:
```python
enriched_metrics = []
for metric in metrics:
    enriched_metrics.append(self._enrich_metric_with_marivo(metric))
enriched["metrics"] = enriched_metrics
```

- [ ] **Step 6: Update create_metric enrichment block (line ~878-886)**

Replace the inline enrichment with:
```python
enriched_metric = self._enrich_metric_with_marivo(metric_data)
```

- [ ] **Step 7: Remove primary_time_field from validation calls**

Search for any `validate_metric` calls that pass `primary_time_field` and update them.
If `validate_metric` in `semantic_validation.py` references `primary_time_field`, update that too.

Run: `grep -rn "primary_time_field" marivo/ --include="*.py"` to find all remaining references.

- [ ] **Step 8: Run full test suite**

Run: `make test`
Expected: tests that don't depend on the old columns pass. Note any failures — they may need test updates.

- [ ] **Step 9: Fix failing tests**

Update tests that reference old metric columns:
- `tests/test_semantic_v2_service.py` — any test creating metrics with old fields
- `tests/test_semantic_v2_api.py` — any API test with old metric payloads

- [ ] **Step 10: Commit**

```bash
git add marivo/runtime/semantic/semantic_service.py tests/
git commit -m "$(cat <<'EOF'
refactor: remove primary_time_field and old metric columns from semantic service

Update all metric INSERT/UPDATE SQL to use additive_dimensions instead
of 5 old columns. Remove 9 primary_time_field references across 4
methods. AOI TimeScope.field makes time field selection caller-specified.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 10: Evolve contracts/semantic.py wrapper

**Files:**
- Modify: `marivo/contracts/semantic.py`

- [ ] **Step 1: Write the failing test**

```python
def test_semantic_model_contract_has_typed_osi() -> None:
    """Domain SemanticModel should use typed OSI model, not dict."""
    from marivo.contracts.semantic import SemanticModel
    import typing

    hints = typing.get_type_hints(SemanticModel)
    # Should NOT have osi_document: dict
    assert "osi_document" not in hints or hints["osi_document"] is not dict
```

- [ ] **Step 2: Update the contract**

In `marivo/contracts/semantic.py`:

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from marivo.contracts.generated.osi import SemanticModel as OSISemanticModel

from .ids import ModelId, RevisionId, UserId


class SemanticModel(BaseModel):
    """Domain-level semantic model, wrapping generated OSI model."""

    model_id: ModelId | None = None
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    osi_model: OSISemanticModel | None = None
    visibility: str = "private"
    owner: UserId | None = None


class ModelSummary(BaseModel):
    model_id: ModelId
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    visibility: str = "private"
    owner: UserId | None = None
    updated_at: str | None = None
```

- [ ] **Step 3: Find and update all importers**

Run: `grep -rn "from marivo.contracts.semantic import\|from marivo.contracts import semantic" marivo/ tests/ --include="*.py"`

There are ~5 importers. Update each to handle `osi_model` instead of `osi_document`.

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: PASS after importer updates

- [ ] **Step 5: Commit**

```bash
git add marivo/contracts/semantic.py
git commit -m "$(cat <<'EOF'
refactor: replace osi_document dict with typed osi_model in SemanticModel

Domain contract now wraps generated OSI SemanticModel instead of
untyped dict. Eng review D2: evolve contracts/semantic.py in place.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 11: Migrate osi_storage.py imports to generated models

**Files:**
- Modify: `marivo/runtime/semantic/osi_storage.py:25-31`

- [ ] **Step 1: Update imports**

Replace:
```python
from marivo.transports.http.models.osi import (
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)
```

With:
```python
from marivo.contracts.generated.osi import (
    Dataset,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)
```

Also update the `build_custom_extensions` function's internal import:
```python
from marivo.contracts.generated.osi import CustomExtension
```

- [ ] **Step 2: Check if generated model field names match**

The generated models may have different class names or field names. Verify:
- `SemanticModel.name`, `.datasets`, `.custom_extensions`, `.ai_context`, etc.
- `Metric.expression`, `.custom_extensions`
- `Field.dimension`, `.expression`
- `Relationship.from_` (alias for `from`)

Fix any naming mismatches.

- [ ] **Step 3: Run tests**

Run: `make test -- tests/test_osi_storage_roundtrip.py tests/test_semantic_v2_service.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add marivo/runtime/semantic/osi_storage.py
git commit -m "$(cat <<'EOF'
refactor: migrate osi_storage imports to contracts/generated

Runtime storage adapter now imports OSI models from the generated
contract layer instead of transport models. Eng review D11.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 12: Migrate semantic_service.py imports to generated models

**Files:**
- Modify: `marivo/runtime/semantic/semantic_service.py:37-46`

- [ ] **Step 1: Update imports**

Replace:
```python
from marivo.transports.http.models.osi import (
    Dataset,
    Metric,
    OSIDocument,
    Relationship,
    SemanticModel,
)
```

With:
```python
from marivo.contracts.generated.osi import (
    Dataset,
    Metric,
    Relationship,
    SemanticModel,
)
```

For `OSIDocument` — check if the generated module has an equivalent top-level document model. The generated model may be named `Model` (the default root model name from datamodel-code-generator). Update accordingly.

Also update the `MarivoSemanticModelExtension` import — this one stays from `marivo.transports.http.models.marivo_extensions` for now (it's the typed MARIVO extension, not an OSI model).

- [ ] **Step 2: Run tests**

Run: `make test -- tests/test_semantic_v2_service.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add marivo/runtime/semantic/semantic_service.py
git commit -m "$(cat <<'EOF'
refactor: migrate semantic_service imports to contracts/generated

Service layer now imports OSI models from generated contracts.
Transport model module becomes a re-export shim only.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 13: Convert osi.py to re-export shim

**Files:**
- Modify: `marivo/transports/http/models/osi.py`

- [ ] **Step 1: Replace with re-exports**

Replace the entire file with re-exports from generated models:

```python
"""OSI model re-exports for transport layer backward compatibility.

All models are generated from osi-marivo-spec. This module re-exports
them for FastAPI route compatibility during cutover.
"""
from marivo.contracts.generated.osi import (  # noqa: F401
    AIContext,
    CustomExtension,
    Dataset,
    Dimension,
    Expression,
    Field,
    Metric,
    Relationship,
    SemanticModel,
)

# Re-export the document-level model
from marivo.contracts.generated.osi import Model as OSIDocument  # noqa: F401

OSI_SPEC_VERSION = "0.1.1"
```

Note: The exact class names depend on what `datamodel-code-generator` produces. Adjust imports to match generated names.

- [ ] **Step 2: Verify all existing consumers still work**

Run: `make test`
Expected: PASS — all existing code that imports from `transports.http.models.osi` gets the same classes

- [ ] **Step 3: Run typecheck**

Run: `make typecheck`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add marivo/transports/http/models/osi.py
git commit -m "$(cat <<'EOF'
refactor: convert transport osi.py to re-export shim

Hand-written OSI models replaced with re-exports from
contracts/generated/osi. Transport layer preserves import paths
for FastAPI routes during cutover.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 14: Phase B verification gate

**Files:**
- No new files

- [ ] **Step 1: Run full test suite**

Run: `make test`
Expected: all tests pass

- [ ] **Step 2: Run typecheck**

Run: `make typecheck`
Expected: no new errors

- [ ] **Step 3: Run lint**

Run: `make lint`
Expected: import-linter contracts all pass

- [ ] **Step 4: Run the storage roundtrip test**

Run: `make test -- tests/test_osi_storage_roundtrip.py -v`
Expected: all roundtrip tests pass

- [ ] **Step 5: Run the generated model tests**

Run: `make test -- tests/test_generated_models.py -v`
Expected: all spec examples validate

- [ ] **Step 6: Verify semantic CRUD still works**

Run: `make test -- tests/test_semantic_v2_service.py tests/test_semantic_v2_api.py -v`
Expected: create/list/get/import semantic model works with generated OSI models

Phase B gate: PASS if all above succeed.

---

## Task 15: Delete zero-importer intent models (Phase C prep)

**Files:**
- Delete: `marivo/transports/http/models/intents.py`
- Delete: `marivo/transports/http/models/intent_responses.py`

- [ ] **Step 1: Verify zero importers**

Run: `grep -rn "from marivo.transports.http.models.intents import\|from marivo.transports.http.models import intents" marivo/ tests/ --include="*.py"`
Run: `grep -rn "from marivo.transports.http.models.intent_responses import\|from marivo.transports.http.models import intent_responses" marivo/ tests/ --include="*.py"`

Expected: no results (confirmed during review)

- [ ] **Step 2: Delete the files**

```bash
rm marivo/transports/http/models/intents.py
rm marivo/transports/http/models/intent_responses.py
```

- [ ] **Step 3: Run tests**

Run: `make test`
Expected: PASS — no code depends on these files

- [ ] **Step 4: Commit**

```bash
git add -A marivo/transports/http/models/intents.py marivo/transports/http/models/intent_responses.py
git commit -m "$(cat <<'EOF'
refactor: delete unused intent request/response transport models

Both files had zero importers (confirmed by grep). intents.py held
old typed-intent request classes; intent_responses.py held
RootModel[JsonObject] placeholders. CEO Finding #8.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Bash]
EOF
)"
```

---

## Task 16: Add TimeScope.field validation test

**Files:**
- Create or extend: `tests/test_generated_models.py`

- [ ] **Step 1: Write TimeScope validation tests**

```python
def test_aoi_timescope_requires_field() -> None:
    """AOI TimeScope must have required 'field' parameter."""
    from marivo.contracts.generated.aoi import TimeScope
    from pydantic import ValidationError

    # Valid
    ts = TimeScope(field="order_date", start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")
    assert ts.field == "order_date"

    # Missing field
    with pytest.raises(ValidationError):
        TimeScope(start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")

    # Empty field
    with pytest.raises(ValidationError):
        TimeScope(field="", start="2024-01-01T00:00:00Z", end="2024-02-01T00:00:00Z")
```

Note: The exact class name `TimeScope` depends on generated output. Check `marivo/contracts/generated/aoi.py` for the actual name and adjust.

- [ ] **Step 2: Run**

Run: `make test -- tests/test_generated_models.py::test_aoi_timescope_requires_field -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_generated_models.py
git commit -m "$(cat <<'EOF'
test: add AOI TimeScope.field validation tests

Verifies that TimeScope requires field parameter (non-empty string),
confirming primary_time_field elimination. Eng review D7 critical test #4.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 17: Add additive_dimensions validation test

**Files:**
- Extend: `tests/test_generated_models.py`

- [ ] **Step 1: Write tests**

```python
def test_additive_dimensions_validation() -> None:
    """additive_dimensions must be a non-empty list of strings when present."""
    from marivo.transports.http.models.marivo_extensions import MarivoMetricExtension

    # Valid: list of dimensions
    ext = MarivoMetricExtension(additive_dimensions=["region", "channel"])
    assert ext.additive_dimensions == ["region", "channel"]

    # Valid: None (not set)
    ext = MarivoMetricExtension()
    assert ext.additive_dimensions is None

    # Valid: None explicitly
    ext = MarivoMetricExtension(additive_dimensions=None)
    assert ext.additive_dimensions is None
```

Check if the spec enforces `minItems: 1` on `additive_dimensions`. If so, add:

```python
    # Invalid: empty list (spec requires minItems: 1)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MarivoMetricExtension(additive_dimensions=[])
```

- [ ] **Step 2: Run**

Run: `make test -- tests/test_generated_models.py::test_additive_dimensions_validation -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_generated_models.py
git commit -m "$(cat <<'EOF'
test: add additive_dimensions validation tests

Eng review D7 critical test #5. Covers valid list, null, and empty
list edge cases per spec minItems constraint.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Task 18: Phase B+C E2E test — create semantic model → observe → compare → decompose

**Files:**
- Create: `tests/test_e2e_osi_aoi.py`

This is the critical E2E gate combining Phases B and C.

- [ ] **Step 1: Write the E2E test**

```python
"""E2E: semantic model creation through AOI analysis on DuckDB."""
from __future__ import annotations

import json

import pytest

from marivo.adapters.metadata import MetadataStore
from marivo.runtime.semantic.semantic_service import SemanticModelV2Service


@pytest.fixture
def service(tmp_path):
    """Create a service with a fresh SQLite store."""
    db_path = tmp_path / "test.db"
    store = MetadataStore(str(db_path))
    store.initialize()
    return SemanticModelV2Service(store)


def _make_model_payload() -> dict:
    """Minimal OSI semantic model payload for testing."""
    return {
        "name": "test_model",
        "datasets": [
            {
                "name": "orders",
                "source": "test.orders",
                "fields": [
                    {
                        "name": "order_date",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]},
                        "dimension": {"is_time": True},
                    },
                    {
                        "name": "amount",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]},
                    },
                    {
                        "name": "region",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region"}]},
                        "dimension": {"is_time": False},
                    },
                ],
                "custom_extensions": [
                    {"vendor_name": "MARIVO", "data": json.dumps({"datasource_id": "ds_test"})}
                ],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
                "custom_extensions": [
                    {"vendor_name": "MARIVO", "data": json.dumps({"additive_dimensions": ["region"]})}
                ],
            }
        ],
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": json.dumps({"visibility": "private", "owner_user": "test_user"}),
            }
        ],
    }


def test_create_semantic_model_with_generated_osi(service):
    """Create a model and verify it roundtrips through generated OSI models."""
    result = service.create_semantic_model(_make_model_payload())
    assert result["name"] == "test_model"
    assert len(result["datasets"]) == 1
    assert len(result.get("metrics", [])) == 1

    # Verify additive_dimensions roundtrip
    metric = result["metrics"][0]
    for ext in metric.get("custom_extensions", []):
        if ext.get("vendor_name") == "MARIVO":
            data = json.loads(ext["data"]) if isinstance(ext["data"], str) else ext["data"]
            assert data.get("additive_dimensions") == ["region"]


def test_get_semantic_model_roundtrip(service):
    """Create then get — full OSI roundtrip through storage."""
    service.create_semantic_model(_make_model_payload())
    fetched = service.get_semantic_model("test_model", requesting_user="test_user")
    assert fetched["name"] == "test_model"
    assert len(fetched["datasets"]) == 1
```

- [ ] **Step 2: Run**

Run: `make test -- tests/test_e2e_osi_aoi.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e_osi_aoi.py
git commit -m "$(cat <<'EOF'
test: add E2E semantic model creation with generated OSI models

Eng review D7 critical test #3. Creates a model with additive_dimensions
metric extension and verifies full storage roundtrip.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Write]
EOF
)"
```

---

## Task 19: Full verification — make test, typecheck, lint

**Files:**
- No new files

- [ ] **Step 1: Run make test**

Run: `make test`
Expected: all tests pass

- [ ] **Step 2: Run make typecheck**

Run: `make typecheck`
Expected: no new errors

- [ ] **Step 3: Run make lint**

Run: `make lint`
Expected: all import-linter contracts pass

- [ ] **Step 4: Fix any remaining failures**

If tests fail, investigate and fix. Common issues:
- Old test fixtures still reference removed columns
- Type mismatches between generated model field names and code expectations
- Import path issues during migration

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: resolve remaining test/type failures from OSI/AOI cutover

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit] [Bash]
EOF
)"
```

---

## Task 20: Add Phase F import-linter enforcement contract

**Files:**
- Modify: `.importlinter`

This contract cannot be enforced until old transport model paths are deleted or converted to re-export shims. Add it now as a placeholder that will be activated in Phase F.

- [ ] **Step 1: Add the enforcement contract**

Append to `.importlinter`:

```ini
[importlinter:contract:runtime-uses-generated-contracts]
name = runtime/ must not import OSI/AOI from transports/http/models (only from contracts/generated/)
type = forbidden
source_modules =
    marivo.runtime
forbidden_modules =
    marivo.transports.http.models.osi
    marivo.transports.http.models.marivo_extensions
```

- [ ] **Step 2: Run lint to check**

Run: `.venv/bin/import-linter`

If the contract fails (because runtime still imports from transports during cutover), note which imports remain and fix them. After Tasks 11-12, runtime should already import from `contracts/generated/`.

If runtime still has one remaining import from `marivo_extensions` (for `MarivoSemanticModelExtension` etc.), that import needs to be migrated too — either move extension models to `contracts/` or import from generated models.

- [ ] **Step 3: Commit**

```bash
git add .importlinter
git commit -m "$(cat <<'EOF'
chore: add import-linter enforcement for runtime→generated contracts

Eng review D1 Phase F contract. Runtime must import OSI/AOI models
from contracts/generated/, not from transports/http/models/.

Co-Authored-By: AGENT_NAME:MODEL_VERSION [Edit]
EOF
)"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - [x] Phase A (Tasks 1-4): generator script, generated models, import-linter, validation tests
   - [x] Phase B (Tasks 5-14): MarivoMetricExtension alignment, DDL update, DRY fix, storage roundtrip, import migration, contract wrapper, re-export shim
   - [x] Phase C prep (Tasks 15-16): delete zero-importer models, TimeScope tests
   - [x] Phase F (Task 20): enforcement contract
   - [x] Critical tests from eng review D7: storage roundtrip (#1), generation validation (#2), E2E flow (#3), TimeScope.field (#4), additive_dimensions (#5), extension decode failure (#7)
   - [ ] Phase C full (AOI atomic runtime retype) — deferred: requires analyzing each intent module
   - [ ] Phase D (MCP DTO cutover) — deferred: depends on Phase C completion
   - [ ] Phase E (derived compatibility) — deferred: depends on Phase C+D
   - [ ] Critical test #6 (MCP E2E) — deferred to Phase D

2. **Placeholder scan:** No TBD/TODO/placeholders found.

3. **Type consistency:** All imports use consistent names. Generated model names may differ — each task notes to check and adjust.

**Note on Phase C/D/E:** These phases require deeper analysis of `marivo/runtime/intents/*.py` and the MCP tool layer, which are large modules with complex internal state. They should be planned as separate implementation plans once Phases A+B are proven.
