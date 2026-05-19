# AOI v0.3 Artifact Algebra Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the AOI v0.3 breaking target from `docs/specs/analysis/aoi-v0.3-design.md`: public artifact algebra, machine-readable registry, generated contracts, artifact store guard metadata, transform guard, deterministic derived manifests, transport projection, and conformance coverage.

**Architecture:** `aoi-spec` is the canonical source. The implementation flows through gates: public spec and registry, generated contracts, artifact storage metadata, runtime lowering and transform guard, derived manifests, transport projection, and E2E conformance. Runtime code must consume `ArtifactEnvelope` values and registry lookups instead of v0.2 `Artifact1` / `Artifact2` result wrappers.

**Tech Stack:** Python 3.11+, Pydantic v2, JSON Schema, YAML, FastAPI, FastMCP, Marivo runtime ports, `.venv/bin/python`, `.venv/bin/pytest`, `make test`, `make typecheck`, `make lint`.

---

## Execution Rules

- This is a breaking implementation. Do not keep v0.2 wire-shape fallback paths unless a test in this plan explicitly names a temporary private helper.
- Do not manually edit `marivo/contracts/generated/aoi.py`; update `aoi-spec/schema/aoi.schema.yaml` and `aoi-spec/schema/aoi.schema.json`, then regenerate with `.venv/bin/python scripts/generate_contract_models.py`.
- Do not use bare `python`, `pytest`, `mypy`, or `ruff`. Use `.venv/bin/python`, `.venv/bin/pytest`, `make test`, `make typecheck`, `make lint`, or `make format`.
- Commit steps are included for execution sessions where commits are allowed. If the user says not to commit, leave the same files unstaged and record the exact verification command outputs instead.
- Lane A is canonical. Rebase every later lane onto the latest `aoi-spec/schema/aoi.schema.json` and `aoi-spec/registry/artifact-registry.yaml` before merging.

## File Structure

Create:

- `aoi-spec/registry/artifact-registry.yaml`: canonical family / shape / measure / capability / transform / consumer registry.
- `aoi-spec/conformance/valid/observe-time-series.json`: valid v0.3 success envelope fixture.
- `aoi-spec/conformance/invalid/old-artifact-result-wrapper.json`: invalid v0.2 wrapper fixture.
- `aoi-spec/conformance/dags/diagnose-candidate-dag.json`: derived diagnose logical DAG fixture.
- `marivo/contracts/aoi_registry.py`: immutable loader and lookup helper for artifact registry.
- `marivo/runtime/aoi_transform_guard.py`: fail-closed transform validation boundary.
- `marivo/runtime/aoi_envelopes.py`: runtime helpers for success, resolved failure, unresolved failure, and typed failure code creation.
- `marivo/runtime/aoi_manifest.py`: `ExecutionManifest` and `ExecutionNode` helpers for derived operations.
- `tests/contracts/test_aoi_registry.py`: registry loader, compatibility, and failure-family tests.
- `tests/contracts/test_generated_models.py`: public schema and conformance example tests.
- `tests/runtime/test_aoi_transform_guard.py`: transform guard negative and positive cases.
- `tests/runtime/test_aoi_manifest.py`: manifest primary artifact, partial branch failure, and no-bundle tests.

Modify:

- `aoi-spec/spec.md`: rewrite public AOI v0.3 spec from the design document.
- `aoi-spec/README.md`: document registry and conformance package.
- `aoi-spec/CHANGELOG.md`: describe v0.3 breaking change.
- `aoi-spec/VERSION`: bump to `0.3.0`.
- `aoi-spec/schema/aoi.schema.yaml`: source schema for v0.3 requests, transforms, envelopes, manifests, and payloads.
- `aoi-spec/schema/aoi.schema.json`: JSON schema generated or synchronized from YAML.
- `scripts/generate_contract_models.py`: keep codegen strict and update invariant patches only where schema cannot express them.
- `marivo/contracts/aoi_runtime.py`: replace v0.2 artifact aliases with v0.3 envelope aliases and validation.
- `marivo/contracts/aoi_projection.py`: project only read/presentation views, never transform DSL inputs.
- `marivo/ports/artifact_store.py`: expose AOI guard metadata at insert/commit boundaries.
- `marivo/adapters/local/file_artifact_store.py`: persist AOI guard metadata with `content`.
- `marivo/adapters/server/artifact_store.py`: persist AOI guard metadata in metadata-backed artifact rows.
- `marivo/adapters/server/mysql_metadata.py` and SQLite metadata templates if the artifacts table needs new columns.
- `marivo/runtime/aoi_lowering.py`: lower source-style requests and artifact-input requests into logical AOI operation DAGs.
- `marivo/runtime/intent_execution.py`: run transform guard before operation execution and use envelope helpers.
- `marivo/runtime/intents/*.py`: emit v0.3 envelopes and consume effective artifact inputs.
- `marivo/runtime/intents/derived_envelopes.py`: replace bundle construction with primary artifact + manifest helpers, or delete after callers move.
- `marivo/transports/http/models/intent_response_models.py`: response models use v0.3 envelopes and manifests.
- `marivo/transports/http/sessions.py`: return v0.3 AOI responses; reject projection refs as downstream inputs.
- `marivo/transports/mcp/tools/intents.py`: expose v0.3 request / response shapes and compact derived manifest output.
- `docs/api/intent-steps.md` and MCP/user AOI docs if present: describe v0.3 artifact references and transform usage.
- `docs/specs/analysis/aoi-v0.3-design.md`: update status notes only after implementation decisions differ from the plan.
- `TODOS.md`: remove or update the AOI v0.3 implementation tracker after all gates land.

Test:

- `tests/contracts/test_aoi_runtime_contract.py`
- `tests/contracts/test_aoi_registry.py`
- `tests/contracts/test_generated_models.py`
- `tests/contracts/artifact_store_cases.py`
- `tests/adapters/test_file_artifact_store.py`
- `tests/runtime/test_aoi_lowering.py`
- `tests/runtime/test_aoi_transform_guard.py`
- `tests/runtime/test_aoi_manifest.py`
- `tests/runtime/test_derived_aoi_envelopes.py`
- `tests/transports/http/test_http_aoi_intents.py`
- `tests/transports/mcp/test_mcp_aoi_adapter.py`
- `tests/integration/test_e2e_osi_aoi.py`

## Task 1: Public Registry Source

**Files:**
- Create: `aoi-spec/registry/artifact-registry.yaml`
- Create: `tests/contracts/test_aoi_registry.py`

- [ ] **Step 1: Write failing registry tests**

Create `tests/contracts/test_aoi_registry.py` with this content:

```python
from __future__ import annotations

import pytest

from marivo.contracts.aoi_registry import (
    AoiRegistryError,
    load_aoi_artifact_registry,
)


def test_registry_loads_expected_version_and_shapes() -> None:
    registry = load_aoi_artifact_registry()

    assert registry.version == "0.3.0"
    assert registry.content_hash
    assert registry.shape("metric_frame", "time_series").required_axes == ("time",)
    assert registry.shape("metric_frame", "panel").required_axes == ("time", "dimension")
    assert registry.shape("metric_frame", "segmented").required_axes == ("dimension",)
    assert registry.shape("diagnosis_result", "candidate_diagnoses").allowed_transforms == (
        "slice",
    )


def test_registry_distinguishes_delta_and_contribution_measures() -> None:
    registry = load_aoi_artifact_registry()

    delta_abs = registry.measure("delta_frame", "delta_abs")
    contribution_abs = registry.measure("attribution_frame", "contribution_abs")

    assert delta_abs.description == "Overall absolute difference between two metric frames."
    assert contribution_abs.description == "Single dimension member absolute contribution to an overall difference."


def test_registry_rejects_unknown_transform_consumer_and_shape() -> None:
    registry = load_aoi_artifact_registry()

    assert registry.allows_transform("metric_frame", "time_series", "rollup") is True
    assert registry.allows_transform("association_result", "pairwise_association", "filter") is False
    assert registry.allows_consumer("association_result", "pairwise_association", "detect") is False

    with pytest.raises(AoiRegistryError, match="unknown artifact shape"):
        registry.shape("metric_frame", "cube")
```

- [ ] **Step 2: Run test to verify missing loader fails**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_registry.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'marivo.contracts.aoi_registry'`.

- [ ] **Step 3: Add canonical registry YAML**

Create `aoi-spec/registry/artifact-registry.yaml`:

```yaml
version: "0.3.0"
failure_code_families:
  - envelope
  - artifact_ref
  - transform
  - capability
  - operation
  - manifest
measures:
  metric_frame:
    value:
      description: "Metric value after applying metric aggregation semantics."
  delta_frame:
    delta_abs:
      description: "Overall absolute difference between two metric frames."
    delta_pct:
      description: "Overall percentage difference between two metric frames."
  attribution_frame:
    contribution_abs:
      description: "Single dimension member absolute contribution to an overall difference."
    contribution_pct:
      description: "Single dimension member percentage contribution to an overall difference."
  candidate_set:
    score:
      description: "Candidate ranking score."
    value:
      description: "Observed value for the candidate window or slice."
  association_result:
    coefficient:
      description: "Pairwise association coefficient."
    p_value:
      description: "Statistical p-value for the association."
  forecast_frame:
    forecast_value:
      description: "Forecasted metric value."
    ci_low:
      description: "Lower confidence interval bound."
    ci_high:
      description: "Upper confidence interval bound."
  hypothesis_test_result:
    statistic:
      description: "Test statistic."
    p_value:
      description: "Test p-value."
families:
  metric_frame:
    shapes:
      scalar:
        required_axes: []
        typical_measures: ["value"]
        capabilities: ["comparable", "filterable"]
        allowed_transforms: ["filter", "summarize_samples"]
        consumers: ["compare", "attribute", "validate"]
      time_series:
        required_axes: ["time"]
        typical_measures: ["value"]
        capabilities: ["sliceable", "filterable", "rollupable", "comparable", "forecastable", "testable"]
        allowed_transforms: ["slice", "filter", "rollup", "summarize_samples"]
        consumers: ["compare", "correlate", "detect", "forecast", "test"]
      segmented:
        required_axes: ["dimension"]
        typical_measures: ["value"]
        capabilities: ["sliceable", "filterable", "rollupable", "comparable"]
        allowed_transforms: ["slice", "filter", "rollup"]
        consumers: ["compare", "attribute"]
      panel:
        required_axes: ["time", "dimension"]
        typical_measures: ["value"]
        capabilities: ["sliceable", "filterable", "rollupable", "comparable"]
        allowed_transforms: ["slice", "filter", "rollup", "summarize_samples"]
        consumers: ["compare", "detect"]
      sample_summary:
        required_axes: ["sample_group"]
        typical_measures: ["n", "mean", "stddev"]
        capabilities: ["testable"]
        allowed_transforms: []
        consumers: ["test", "validate"]
  delta_frame:
    shapes:
      scalar_delta:
        required_axes: []
        typical_measures: ["delta_abs", "delta_pct"]
        capabilities: ["decomposable"]
        allowed_transforms: []
        consumers: ["decompose", "attribute", "diagnose"]
      time_series_delta:
        required_axes: ["time"]
        typical_measures: ["delta_abs", "delta_pct"]
        capabilities: ["sliceable", "filterable", "decomposable"]
        allowed_transforms: ["slice", "filter"]
        consumers: ["decompose", "attribute", "diagnose"]
      segmented_delta:
        required_axes: ["dimension"]
        typical_measures: ["delta_abs", "delta_pct"]
        capabilities: ["sliceable", "filterable", "decomposable"]
        allowed_transforms: ["slice", "filter"]
        consumers: ["decompose", "attribute", "diagnose"]
      panel_delta:
        required_axes: ["time", "dimension"]
        typical_measures: ["delta_abs", "delta_pct"]
        capabilities: ["sliceable", "filterable", "decomposable"]
        allowed_transforms: ["slice", "filter"]
        consumers: ["decompose", "attribute", "diagnose"]
  candidate_set:
    shapes:
      ranked_candidates:
        required_axes: ["candidate"]
        typical_measures: ["score", "value"]
        capabilities: ["sliceable"]
        allowed_transforms: ["slice"]
        consumers: ["diagnose"]
  attribution_frame:
    shapes:
      ranked_contributions:
        required_axes: ["dimension"]
        typical_measures: ["contribution_abs", "contribution_pct"]
        capabilities: ["filterable"]
        allowed_transforms: ["filter"]
        consumers: []
  association_result:
    shapes:
      pairwise_association:
        required_axes: []
        typical_measures: ["coefficient", "p_value"]
        capabilities: []
        allowed_transforms: []
        consumers: []
  forecast_frame:
    shapes:
      forecast_series:
        required_axes: ["time"]
        typical_measures: ["forecast_value", "ci_low", "ci_high"]
        capabilities: ["sliceable"]
        allowed_transforms: ["slice"]
        consumers: []
  hypothesis_test_result:
    shapes:
      two_sample_mean:
        required_axes: []
        typical_measures: ["statistic", "p_value"]
        capabilities: []
        allowed_transforms: []
        consumers: []
  diagnosis_result:
    shapes:
      candidate_diagnoses:
        required_axes: ["candidate"]
        typical_measures: []
        capabilities: ["sliceable"]
        allowed_transforms: ["slice"]
        consumers: []
transforms:
  slice:
    failure_family: transform
  filter:
    failure_family: transform
  rollup:
    failure_family: transform
  summarize_samples:
    failure_family: transform
```

- [ ] **Step 4: Implement registry loader**

Create `marivo/contracts/aoi_registry.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "aoi-spec" / "registry" / "artifact-registry.yaml"


class AoiRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class AoiShapeSpec:
    family: str
    shape: str
    required_axes: tuple[str, ...]
    typical_measures: tuple[str, ...]
    capabilities: tuple[str, ...]
    allowed_transforms: tuple[str, ...]
    consumers: tuple[str, ...]


@dataclass(frozen=True)
class AoiMeasureSpec:
    family: str
    measure: str
    description: str


@dataclass(frozen=True)
class AoiArtifactRegistry:
    version: str
    content_hash: str
    shapes: dict[tuple[str, str], AoiShapeSpec]
    measures: dict[tuple[str, str], AoiMeasureSpec]
    failure_code_families: tuple[str, ...]

    def shape(self, family: str, shape: str) -> AoiShapeSpec:
        try:
            return self.shapes[(family, shape)]
        except KeyError as exc:
            raise AoiRegistryError(f"unknown artifact shape: {family}({shape})") from exc

    def measure(self, family: str, measure: str) -> AoiMeasureSpec:
        try:
            return self.measures[(family, measure)]
        except KeyError as exc:
            raise AoiRegistryError(f"unknown artifact measure: {family}.{measure}") from exc

    def allows_transform(self, family: str, shape: str, transform: str) -> bool:
        return transform in self.shape(family, shape).allowed_transforms

    def allows_consumer(self, family: str, shape: str, operation: str) -> bool:
        return operation in self.shape(family, shape).consumers


@cache
def load_aoi_artifact_registry(path: Path = REGISTRY_PATH) -> AoiArtifactRegistry:
    raw_text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text)
    if not isinstance(raw, dict):
        raise AoiRegistryError("registry root must be an object")
    version = str(raw.get("version") or "")
    if not version:
        raise AoiRegistryError("registry version is required")
    families = raw.get("families")
    if not isinstance(families, dict):
        raise AoiRegistryError("registry families must be an object")

    shapes: dict[tuple[str, str], AoiShapeSpec] = {}
    for family, family_spec in families.items():
        shape_specs = (family_spec or {}).get("shapes")
        if not isinstance(shape_specs, dict):
            raise AoiRegistryError(f"family {family} must define shapes")
        for shape, spec in shape_specs.items():
            shapes[(str(family), str(shape))] = AoiShapeSpec(
                family=str(family),
                shape=str(shape),
                required_axes=tuple(str(v) for v in spec.get("required_axes", [])),
                typical_measures=tuple(str(v) for v in spec.get("typical_measures", [])),
                capabilities=tuple(str(v) for v in spec.get("capabilities", [])),
                allowed_transforms=tuple(str(v) for v in spec.get("allowed_transforms", [])),
                consumers=tuple(str(v) for v in spec.get("consumers", [])),
            )

    measures: dict[tuple[str, str], AoiMeasureSpec] = {}
    raw_measures = raw.get("measures") or {}
    if not isinstance(raw_measures, dict):
        raise AoiRegistryError("registry measures must be an object")
    for family, family_measures in raw_measures.items():
        for measure, spec in (family_measures or {}).items():
            measures[(str(family), str(measure))] = AoiMeasureSpec(
                family=str(family),
                measure=str(measure),
                description=str((spec or {}).get("description") or ""),
            )

    return AoiArtifactRegistry(
        version=version,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        shapes=shapes,
        measures=measures,
        failure_code_families=tuple(str(v) for v in raw.get("failure_code_families", [])),
    )
```

- [ ] **Step 5: Run registry tests**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_registry.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit registry source if commits are allowed**

```bash
git add aoi-spec/registry/artifact-registry.yaml marivo/contracts/aoi_registry.py tests/contracts/test_aoi_registry.py
git commit -m "feat(aoi): add v0.3 artifact registry"
```

## Task 2: Public Schema, Examples, and Conformance Fixtures

**Files:**
- Modify: `aoi-spec/schema/aoi.schema.yaml`
- Modify: `aoi-spec/schema/aoi.schema.json`
- Modify: `aoi-spec/spec.md`
- Modify: `aoi-spec/README.md`
- Modify: `aoi-spec/CHANGELOG.md`
- Modify: `aoi-spec/VERSION`
- Create: `aoi-spec/conformance/valid/observe-time-series.json`
- Create: `aoi-spec/conformance/invalid/old-artifact-result-wrapper.json`
- Create: `aoi-spec/conformance/dags/diagnose-candidate-dag.json`
- Create: `tests/contracts/test_generated_models.py`

- [ ] **Step 1: Write failing conformance model tests**

Create `tests/contracts/test_generated_models.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from marivo.contracts.generated import aoi

ROOT = Path(__file__).resolve().parents[2]


def _load(path: str) -> dict[str, object]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_valid_observe_time_series_envelope_matches_generated_model() -> None:
    payload = _load("aoi-spec/conformance/valid/observe-time-series.json")

    artifact = aoi.ArtifactEnvelope.model_validate(payload)

    assert artifact.artifact_family == "metric_frame"
    assert artifact.shape == "time_series"
    assert artifact.failure_stage is None
    assert artifact.payload["points"][0]["value"] == 42.0


def test_old_result_wrapper_is_rejected_by_v0_3_model() -> None:
    payload = _load("aoi-spec/conformance/invalid/old-artifact-result-wrapper.json")

    with pytest.raises(ValidationError):
        aoi.ArtifactEnvelope.model_validate(payload)


def test_diagnose_dag_fixture_names_primary_artifact_and_inline_transform() -> None:
    payload = _load("aoi-spec/conformance/dags/diagnose-candidate-dag.json")

    manifest = aoi.ExecutionManifest.model_validate(payload)

    assert manifest.root_operation == "diagnose"
    assert manifest.primary_artifact_id == "art_diagnosis"
    assert manifest.nodes[1].inline_transforms[0].kind == "slice"
```

- [ ] **Step 2: Run test to verify schema is still v0.2**

Run:

```bash
.venv/bin/pytest tests/contracts/test_generated_models.py -q
```

Expected: FAIL because `aoi.ArtifactEnvelope` and `aoi.ExecutionManifest` do not yet match v0.3.

- [ ] **Step 3: Add conformance fixtures**

Create `aoi-spec/conformance/valid/observe-time-series.json`:

```json
{
  "artifact_id": "art_observe_ts",
  "artifact_family": "metric_frame",
  "shape": "time_series",
  "subject": {
    "kind": "metric",
    "metric_ref": "metric.revenue"
  },
  "axes": [
    {"name": "time"}
  ],
  "measures": [
    {"name": "value"}
  ],
  "capabilities": ["sliceable", "filterable", "rollupable", "comparable", "forecastable", "testable"],
  "lineage": {
    "producing_operation": "observe",
    "source_artifacts": [],
    "applied_transforms": []
  },
  "payload": {
    "points": [
      {
        "bucket_start": "2026-05-01T00:00:00Z",
        "value": 42.0
      }
    ]
  }
}
```

Create `aoi-spec/conformance/invalid/old-artifact-result-wrapper.json`:

```json
{
  "artifact_id": "art_old",
  "result": {
    "value": 42.0
  }
}
```

Create `aoi-spec/conformance/dags/diagnose-candidate-dag.json`:

```json
{
  "manifest_id": "man_diagnose",
  "root_operation": "diagnose",
  "primary_artifact_id": "art_diagnosis",
  "nodes": [
    {
      "node_id": "detect",
      "operation": "detect",
      "status": "succeeded",
      "output_artifact_id": "art_candidates"
    },
    {
      "node_id": "candidate_1_compare",
      "operation": "compare",
      "inline_transforms": [
        {
          "kind": "slice",
          "axis": "candidate",
          "selector": {"candidate_id": "candidate_1"}
        }
      ],
      "status": "succeeded",
      "output_artifact_id": "art_delta_candidate_1"
    },
    {
      "node_id": "candidate_1_decompose",
      "operation": "decompose",
      "status": "succeeded",
      "output_artifact_id": "art_attr_candidate_1"
    }
  ],
  "edges": [
    {"from_node_id": "detect", "to_node_id": "candidate_1_compare", "artifact_id": "art_candidates"},
    {"from_node_id": "candidate_1_compare", "to_node_id": "candidate_1_decompose", "artifact_id": "art_delta_candidate_1"}
  ]
}
```

- [ ] **Step 4: Replace schema artifact definitions**

Modify `aoi-spec/schema/aoi.schema.yaml` and `aoi-spec/schema/aoi.schema.json` so generated models include these definitions:

```yaml
ArtifactFamily:
  type: string
  enum:
    - metric_frame
    - delta_frame
    - candidate_set
    - attribution_frame
    - association_result
    - forecast_frame
    - hypothesis_test_result
    - diagnosis_result
ArtifactSubject:
  oneOf:
    - type: object
      required: [kind, metric_ref]
      additionalProperties: false
      properties:
        kind: {const: metric}
        metric_ref: {type: string, minLength: 1}
        scope: {type: object}
    - type: object
      required: [kind, metric_ref, current, baseline]
      additionalProperties: false
      properties:
        kind: {const: comparison}
        metric_ref: {type: string, minLength: 1}
        current: {type: object}
        baseline: {type: object}
    - type: object
      required: [kind, metric_ref, candidate_selector]
      additionalProperties: false
      properties:
        kind: {const: candidate}
        metric_ref: {type: string, minLength: 1}
        candidate_selector: {type: object}
    - type: object
      required: [kind, metric_ref, groups]
      additionalProperties: false
      properties:
        kind: {const: hypothesis}
        hypothesis_ref: {type: string}
        metric_ref: {type: string, minLength: 1}
        groups:
          type: array
          items: {type: object}
    - type: object
      required: [kind, metric_ref]
      additionalProperties: false
      properties:
        kind: {const: diagnosis}
        metric_ref: {type: string, minLength: 1}
        candidate_selector: {type: object}
ArtifactEnvelope:
  oneOf:
    - $ref: "#/$defs/artifacts/SuccessfulArtifactEnvelope"
    - $ref: "#/$defs/artifacts/ResolvedFailedArtifactEnvelope"
    - $ref: "#/$defs/artifacts/UnresolvedFailedArtifactEnvelope"
```

Use the same names in JSON schema. Keep `additionalProperties: false` on every public object.

- [ ] **Step 5: Regenerate contracts**

Run:

```bash
.venv/bin/python scripts/generate_contract_models.py
```

Expected: regenerated `marivo/contracts/generated/aoi.py` and `marivo/contracts/generated/__init__.py`.

- [ ] **Step 6: Run conformance model tests**

Run:

```bash
.venv/bin/pytest tests/contracts/test_generated_models.py -q
```

Expected: PASS.

- [ ] **Step 7: Update public docs**

Modify `aoi-spec/VERSION` to:

```text
0.3.0
```

Add this section to `aoi-spec/README.md`:

```markdown
## AOI v0.3 Artifact Algebra

AOI v0.3 is a breaking artifact-algebra contract. Public analysis outputs use
`ArtifactEnvelope`, artifact compatibility is defined by
`registry/artifact-registry.yaml`, inline transforms are request modifiers, and
derived operations return a primary artifact plus `ExecutionManifest`.
```

Add this entry to the top of `aoi-spec/CHANGELOG.md`:

```markdown
## 0.3.0

- Replaced v0.2 `Artifact { result | failure }` wrappers with `ArtifactEnvelope`.
- Added artifact families, shapes, capabilities, transform DSL, failure code families, and `ExecutionManifest`.
- Added machine-readable artifact registry and conformance fixtures.
```

- [ ] **Step 8: Commit schema gate if commits are allowed**

```bash
git add aoi-spec marivo/contracts/generated tests/contracts/test_generated_models.py
git commit -m "feat(aoi): define v0.3 public schema"
```

## Task 3: Runtime Contract Helpers

**Files:**
- Modify: `marivo/contracts/aoi_runtime.py`
- Modify: `tests/contracts/test_aoi_runtime_contract.py`

- [ ] **Step 1: Add failing v0.3 runtime contract tests**

Append to `tests/contracts/test_aoi_runtime_contract.py`:

```python
def _v0_3_success_envelope() -> dict[str, object]:
    return {
        "artifact_id": "art_metric",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {"kind": "metric", "metric_ref": "metric.revenue"},
        "axes": [],
        "measures": [{"name": "value"}],
        "capabilities": ["comparable", "filterable"],
        "lineage": {
            "producing_operation": "observe",
            "source_artifacts": [],
            "applied_transforms": [],
        },
        "payload": {"value": 42.0},
    }


def test_validate_aoi_artifact_accepts_v0_3_envelope() -> None:
    artifact = validate_aoi_artifact(_v0_3_success_envelope())

    assert artifact.artifact_id == "art_metric"
    assert artifact.artifact_family == "metric_frame"
    assert artifact.payload == {"value": 42.0}


def test_validate_aoi_artifact_rejects_old_result_wrapper() -> None:
    with pytest.raises(ValidationError):
        validate_aoi_artifact({"artifact_id": "art_old", "result": {"value": 42.0}})


def test_artifact_to_envelope_result_returns_v0_3_shape() -> None:
    payload = artifact_to_envelope_result(validate_aoi_artifact(_v0_3_success_envelope()))

    assert payload["artifact_id"] == "art_metric"
    assert payload["artifact_family"] == "metric_frame"
    assert "result" not in payload
```

- [ ] **Step 2: Run runtime contract tests to verify old helper fails**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_runtime_contract.py -q
```

Expected: FAIL because `validate_aoi_artifact` accepts `result` wrappers and returns v0.2 models.

- [ ] **Step 3: Replace artifact aliases and validator**

In `marivo/contracts/aoi_runtime.py`, replace `AoiArtifact` and `validate_aoi_artifact` with:

```python
AoiArtifact: TypeAlias = aoi.ArtifactEnvelope  # noqa: UP040


def validate_aoi_artifact(value: Any) -> AoiArtifact:
    if isinstance(value, aoi.ArtifactEnvelope):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, Mapping):
        return aoi.ArtifactEnvelope.model_validate(value)
    if "result" in value:
        raise ValidationError.from_exception_data(
            "AoiArtifact",
            [
                {
                    "type": "value_error",
                    "loc": ("result",),
                    "input": value,
                    "ctx": {"error": ValueError("v0.2 result wrappers are invalid in AOI v0.3")},
                }
            ],
        )
    return aoi.ArtifactEnvelope.model_validate(value)


def artifact_to_envelope_result(artifact: AoiArtifact) -> dict[str, Any]:
    return artifact.model_dump(mode="json", exclude_none=True)
```

Remove `_CanonicalSuccessArtifactShape` and `_CanonicalFailureArtifactShape` when no longer referenced.

- [ ] **Step 4: Update request aliases for generated v0.3 request names**

After regeneration, inspect generated request model names:

```bash
rg -n "^class (Observe|Compare|Decompose|Correlate|Detect|Test|Forecast|Validate|Attribute|Diagnose)" marivo/contracts/generated/aoi.py
```

If `Observe1`, `Observe2`, and `Observe3` are still generated, keep the existing observe union. If the v0.3 schema produces a single `Observe`, update the alias to:

```python
AoiAtomicRequest: TypeAlias = (
    aoi.Compare
    | aoi.Decompose
    | aoi.Correlate
    | aoi.Detect
    | aoi.Test
    | aoi.Forecast
    | aoi.Observe
)
```

- [ ] **Step 5: Run contract tests**

Run:

```bash
.venv/bin/pytest tests/contracts/test_aoi_runtime_contract.py tests/contracts/test_aoi_registry.py tests/contracts/test_generated_models.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit runtime contract gate if commits are allowed**

```bash
git add marivo/contracts/aoi_runtime.py tests/contracts/test_aoi_runtime_contract.py
git commit -m "feat(aoi): validate v0.3 artifact envelopes"
```

## Task 4: Artifact Store Guard Metadata

**Files:**
- Modify: `marivo/ports/artifact_store.py`
- Modify: `marivo/adapters/local/file_artifact_store.py`
- Modify: `marivo/adapters/server/artifact_store.py`
- Modify: `marivo/adapters/server/mysql_metadata.py`
- Modify: `tests/contracts/artifact_store_cases.py`
- Modify: `tests/adapters/test_file_artifact_store.py`

- [ ] **Step 1: Add failing artifact store contract case**

Append to `tests/contracts/artifact_store_cases.py`:

```python
def _aoi_envelope() -> dict[str, object]:
    return {
        "artifact_id": "art_metric_store",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {"kind": "metric", "metric_ref": "metric.revenue"},
        "axes": [],
        "measures": [{"name": "value"}],
        "capabilities": ["comparable"],
        "lineage": {
            "producing_operation": "observe",
            "source_artifacts": [],
            "applied_transforms": [],
        },
        "payload": {"value": 42.0},
    }


def _run_insert_aoi_artifact_guard_metadata(adapter, _: Path) -> None:
    session_id = SessionId("sess-aoi-guard")
    step_id = StepId("step-aoi-guard")

    artifact_id = adapter.insert_artifact(
        session_id=session_id,
        step_id=step_id,
        artifact_type="aoi_artifact",
        name="metric scalar",
        content=_aoi_envelope(),
        artifact_schema_version="0.3.0",
    )

    rows = adapter.list_artifacts(session_id)
    row = next(item for item in rows if item["artifact_id"] == artifact_id)
    assert row["artifact_family"] == "metric_frame"
    assert row["shape"] == "scalar"
    assert row["failure_stage"] is None
    assert row["capabilities"] == ["comparable"]
    assert row["axes"] == []
    assert row["measures"] == [{"name": "value"}]
```

Add the case to `ARTIFACT_STORE_CASES`:

```python
ContractCase(
    name="insert_aoi_artifact_guard_metadata",
    run=_run_insert_aoi_artifact_guard_metadata,
),
```

- [ ] **Step 2: Add local adapter negative test**

Append to `tests/adapters/test_file_artifact_store.py`:

```python
def test_aoi_guard_metadata_mismatch_is_not_committed(store: FileArtifactStore) -> None:
    sid = SessionId("s-aoi")
    step = StepId("step-aoi")

    bad_content = {
        "artifact_id": "art_bad",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {"kind": "metric", "metric_ref": "metric.revenue"},
        "axes": [],
        "measures": [{"name": "value"}],
        "capabilities": ["comparable"],
        "lineage": {
            "producing_operation": "observe",
            "source_artifacts": [],
            "applied_transforms": [],
        },
        "payload": {"value": 42.0},
    }

    with pytest.raises(ValueError, match="artifact_family"):
        store.insert_artifact(
            sid,
            step,
            "aoi_artifact",
            "bad",
            bad_content,
            artifact_schema_version="0.3.0",
            artifact_family="delta_frame",
        )

    assert store.resolve_artifact_for_ref(sid, step) is None
```

- [ ] **Step 3: Run adapter tests to verify missing keyword fails**

Run:

```bash
.venv/bin/pytest tests/adapters/test_file_artifact_store.py tests/contracts/test_artifact_store.py -q
```

Expected: FAIL with unexpected keyword `artifact_family`.

- [ ] **Step 4: Add guard metadata arguments to the port**

Add keyword-only parameters to both `insert_artifact` and `commit_artifact_with_extraction` in `marivo/ports/artifact_store.py`:

```python
        artifact_family: str | None = None,
        shape: str | None = None,
        failure_stage: str | None = None,
        capabilities: list[str] | None = None,
        axes: list[dict[str, Any]] | None = None,
        measures: list[dict[str, Any]] | None = None,
        manifest_id: str | None = None,
```

- [ ] **Step 5: Implement local guard metadata extraction**

In `marivo/adapters/local/file_artifact_store.py`, add helper:

```python
def _aoi_guard_metadata(
    content: Any,
    *,
    artifact_family: str | None,
    shape: str | None,
    failure_stage: str | None,
    capabilities: list[str] | None,
    axes: list[dict[str, Any]] | None,
    measures: list[dict[str, Any]] | None,
    manifest_id: str | None,
) -> dict[str, Any]:
    if not isinstance(content, dict) or "artifact_family" not in content:
        return {
            "artifact_family": artifact_family,
            "shape": shape,
            "failure_stage": failure_stage,
            "capabilities": capabilities,
            "axes": axes,
            "measures": measures,
            "manifest_id": manifest_id,
        }
    inferred = {
        "artifact_family": content.get("artifact_family"),
        "shape": content.get("shape"),
        "failure_stage": content.get("failure_stage"),
        "capabilities": content.get("capabilities"),
        "axes": content.get("axes"),
        "measures": content.get("measures"),
        "manifest_id": (content.get("lineage") or {}).get("manifest_id"),
    }
    supplied = {
        "artifact_family": artifact_family,
        "shape": shape,
        "failure_stage": failure_stage,
        "capabilities": capabilities,
        "axes": axes,
        "measures": measures,
        "manifest_id": manifest_id,
    }
    for key, supplied_value in supplied.items():
        if supplied_value is not None and supplied_value != inferred.get(key):
            raise ValueError(f"{key} does not match ArtifactEnvelope content")
    return inferred
```

Store the returned fields in artifact records and index summaries with keys `artifact_family`, `shape`, `failure_stage`, `capabilities`, `axes`, `measures`, and `manifest_id`.

- [ ] **Step 6: Update server adapter and metadata schema**

Mirror the same keyword arguments in `marivo/adapters/server/artifact_store.py`. Insert them into metadata columns:

```python
[
    "artifact_family",
    "shape",
    "failure_stage",
    "capabilities_json",
    "axes_json",
    "measures_json",
    "manifest_id",
]
```

Use `json.dumps(..., sort_keys=True, ensure_ascii=False, default=str)` for JSON columns.

- [ ] **Step 7: Run store tests**

Run:

```bash
.venv/bin/pytest tests/contracts/test_artifact_store.py tests/adapters/test_file_artifact_store.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit store gate if commits are allowed**

```bash
git add marivo/ports/artifact_store.py marivo/adapters/local/file_artifact_store.py marivo/adapters/server/artifact_store.py marivo/adapters/server/mysql_metadata.py tests/contracts/artifact_store_cases.py tests/adapters/test_file_artifact_store.py
git commit -m "feat(aoi): persist artifact guard metadata"
```

## Task 5: Transform Guard

**Files:**
- Create: `marivo/runtime/aoi_transform_guard.py`
- Create: `tests/runtime/test_aoi_transform_guard.py`

- [ ] **Step 1: Write failing transform guard tests**

Create `tests/runtime/test_aoi_transform_guard.py`:

```python
from __future__ import annotations

from marivo.runtime.aoi_transform_guard import (
    TransformGuardContext,
    TransformGuardFailure,
    validate_artifact_input_transforms,
)


def _artifact() -> dict[str, object]:
    return {
        "artifact_id": "art_panel",
        "artifact_family": "metric_frame",
        "shape": "panel",
        "axes": [{"name": "time"}, {"name": "dimension", "dimension": "country"}],
        "measures": [{"name": "value"}],
        "capabilities": ["sliceable", "filterable", "rollupable", "comparable"],
    }


def _context() -> TransformGuardContext:
    return TransformGuardContext(
        session_id="sess_1",
        actor="alice",
        visible_metrics=("metric.revenue",),
        visible_dimensions=("country",),
        visible_time_fields=("event_time",),
        visible_sample_groups=("current", "baseline"),
        visible_candidate_ids=("candidate_1",),
        allowed_expression_dialects=("ANSI_SQL",),
        allowed_expression_fields=("country", "value"),
        allowed_expression_functions=(),
    )


def test_slice_dimension_transform_is_normalized() -> None:
    result = validate_artifact_input_transforms(
        artifact=_artifact(),
        transforms=[
            {
                "kind": "slice",
                "axis": "dimension",
                "selector": {"dimension": "country", "value": "US"},
            }
        ],
        consumer="forecast",
        context=_context(),
    )

    assert result.effective_shape == "time_series"
    assert result.normalized_transforms[0]["kind"] == "slice"
    assert result.normalized_transforms[0]["axis"] == "dimension"


def test_unknown_transform_fails_closed() -> None:
    result = validate_artifact_input_transforms(
        artifact=_artifact(),
        transforms=[{"kind": "project", "columns": ["value"]}],
        consumer="compare",
        context=_context(),
    )

    assert isinstance(result, TransformGuardFailure)
    assert result.failure_code == "transform.unsupported_kind"


def test_unsafe_expression_fails_before_query_compilation() -> None:
    result = validate_artifact_input_transforms(
        artifact=_artifact(),
        transforms=[
            {
                "kind": "filter",
                "predicate": {
                    "dialects": [
                        {"dialect": "ANSI_SQL", "expression": "country = 'US'; drop table orders"}
                    ]
                },
            }
        ],
        consumer="compare",
        context=_context(),
    )

    assert isinstance(result, TransformGuardFailure)
    assert result.failure_code == "transform.expression_invalid"
```

- [ ] **Step 2: Run test to verify missing guard fails**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_transform_guard.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement guard result models and validator**

Create `marivo/runtime/aoi_transform_guard.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marivo.contracts.aoi_registry import load_aoi_artifact_registry


@dataclass(frozen=True)
class TransformGuardContext:
    session_id: str
    actor: str | None
    visible_metrics: tuple[str, ...]
    visible_dimensions: tuple[str, ...]
    visible_time_fields: tuple[str, ...]
    visible_sample_groups: tuple[str, ...]
    visible_candidate_ids: tuple[str, ...]
    allowed_expression_dialects: tuple[str, ...]
    allowed_expression_fields: tuple[str, ...]
    allowed_expression_functions: tuple[str, ...]


@dataclass(frozen=True)
class TransformGuardSuccess:
    effective_family: str
    effective_shape: str
    normalized_transforms: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class TransformGuardFailure:
    failure_code: str
    message: str


TransformGuardResult = TransformGuardSuccess | TransformGuardFailure


def validate_artifact_input_transforms(
    *,
    artifact: dict[str, Any],
    transforms: list[dict[str, Any]] | None,
    consumer: str,
    context: TransformGuardContext,
) -> TransformGuardResult:
    registry = load_aoi_artifact_registry()
    family = str(artifact.get("artifact_family") or "")
    shape = str(artifact.get("shape") or "")
    normalized: list[dict[str, Any]] = []

    for transform in transforms or []:
        kind = str(transform.get("kind") or "")
        if kind not in {"slice", "filter", "rollup", "summarize_samples"}:
            return TransformGuardFailure("transform.unsupported_kind", f"unsupported transform {kind}")
        if not registry.allows_transform(family, shape, kind):
            return TransformGuardFailure("capability.missing", f"{family}({shape}) does not allow {kind}")
        if kind == "slice":
            failure = _validate_slice(transform, artifact, context)
            if failure is not None:
                return failure
            shape = _effective_shape_after_slice(shape, transform)
        elif kind == "filter":
            failure = _validate_expression(transform.get("predicate"), context)
            if failure is not None:
                return failure
        elif kind == "rollup":
            failure = _validate_rollup(transform, artifact)
            if failure is not None:
                return failure
            target_axes = tuple(transform.get("target_axes") or [])
            if target_axes == ("time",):
                shape = "time_series"
            elif target_axes == ("dimension",):
                shape = "segmented"
            elif target_axes == ():
                shape = "scalar"
        elif kind == "summarize_samples":
            shape = "sample_summary"
        normalized.append(dict(transform))

    if not registry.allows_consumer(family, shape, consumer):
        return TransformGuardFailure(
            "operation.unsupported_shape",
            f"{consumer} cannot consume {family}({shape})",
        )
    return TransformGuardSuccess(
        effective_family=family,
        effective_shape=shape,
        normalized_transforms=tuple(normalized),
    )


def _validate_slice(
    transform: dict[str, Any],
    artifact: dict[str, Any],
    context: TransformGuardContext,
) -> TransformGuardFailure | None:
    axis = transform.get("axis")
    selector = transform.get("selector")
    if axis not in {"time", "dimension", "candidate", "sample_group"} or not isinstance(selector, dict):
        return TransformGuardFailure("transform.invalid_selector", "slice selector is invalid")
    if axis == "dimension":
        dimension = selector.get("dimension")
        if dimension not in context.visible_dimensions:
            return TransformGuardFailure("artifact_ref.cross_session", "dimension is not visible")
    if axis == "candidate" and selector.get("candidate_id") not in context.visible_candidate_ids:
        return TransformGuardFailure("artifact_ref.cross_session", "candidate is not visible")
    artifact_axes = {str(item.get("name")) for item in artifact.get("axes", []) if isinstance(item, dict)}
    if axis not in artifact_axes:
        return TransformGuardFailure("capability.axis_missing", f"axis {axis} is missing")
    return None


def _validate_expression(
    predicate: Any,
    context: TransformGuardContext,
) -> TransformGuardFailure | None:
    if not isinstance(predicate, dict):
        return TransformGuardFailure("transform.expression_invalid", "predicate must be an object")
    for dialect_entry in predicate.get("dialects") or []:
        dialect = dialect_entry.get("dialect")
        expression = str(dialect_entry.get("expression") or "")
        if dialect not in context.allowed_expression_dialects:
            return TransformGuardFailure("transform.expression_invalid", "expression dialect is not allowed")
        if ";" in expression or "--" in expression or "/*" in expression or "*/" in expression:
            return TransformGuardFailure("transform.expression_invalid", "expression contains unsafe tokens")
    return None


def _validate_rollup(
    transform: dict[str, Any],
    artifact: dict[str, Any],
) -> TransformGuardFailure | None:
    target_axes = transform.get("target_axes")
    if not isinstance(target_axes, list):
        return TransformGuardFailure("transform.invalid_selector", "rollup target_axes must be a list")
    artifact_axes = {str(item.get("name")) for item in artifact.get("axes", []) if isinstance(item, dict)}
    if not set(str(axis) for axis in target_axes).issubset(artifact_axes):
        return TransformGuardFailure("capability.axis_missing", "rollup target axis is missing")
    return None


def _effective_shape_after_slice(shape: str, transform: dict[str, Any]) -> str:
    if shape == "panel" and transform.get("axis") == "dimension":
        return "time_series"
    if shape == "panel" and transform.get("axis") == "time":
        return "segmented"
    return shape
```

- [ ] **Step 4: Run transform guard tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_transform_guard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit transform guard if commits are allowed**

```bash
git add marivo/runtime/aoi_transform_guard.py tests/runtime/test_aoi_transform_guard.py
git commit -m "feat(aoi): add transform guard"
```

## Task 6: Runtime Lowering to Logical AOI DAGs

**Files:**
- Modify: `marivo/runtime/aoi_lowering.py`
- Modify: `tests/runtime/test_aoi_lowering.py`

- [ ] **Step 1: Add failing lowering tests for artifact inputs**

Append to `tests/runtime/test_aoi_lowering.py`:

```python
def test_lowers_forecast_artifact_input_with_inline_transform() -> None:
    request = aoi.Forecast.model_validate(
        {
            "source": {
                "artifact_id": "art_panel",
                "transforms": [
                    {
                        "kind": "slice",
                        "axis": "dimension",
                        "selector": {"dimension": "country", "value": "US"},
                    }
                ],
            },
            "horizon": {"buckets": 14, "granularity": "day"},
        }
    )

    lowered = lower_aoi_request("forecast", request)

    assert lowered == {
        "operation": "forecast",
        "inputs": [
            {
                "artifact_id": "art_panel",
                "transforms": [
                    {
                        "kind": "slice",
                        "axis": "dimension",
                        "selector": {"dimension": "country", "value": "US"},
                    }
                ],
            }
        ],
        "params": {"horizon": {"buckets": 14, "granularity": "day"}},
    }


def test_lowers_validate_source_style_to_logical_dag() -> None:
    request = aoi.Validate.model_validate(
        {
            "metric": "metric.revenue",
            "current": {"time_scope": _time_scope().model_dump(mode="json")},
            "baseline": {"time_scope": _time_scope().model_dump(mode="json")},
            "grain": "day",
            "hypothesis": {
                "family": "two_sample_mean",
                "alternative": "greater",
                "significance": "balanced",
            },
        }
    )

    lowered = lower_aoi_derived_request("validate", request)

    assert lowered["root_operation"] == "validate"
    assert [node["operation"] for node in lowered["nodes"]] == ["observe", "observe", "test"]
    assert lowered["nodes"][2]["inputs"][0]["transforms"][0]["kind"] == "summarize_samples"
```

- [ ] **Step 2: Run lowering tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_lowering.py -q
```

Expected: FAIL because current lowering returns runner parameter dictionaries and v0.2 field names.

- [ ] **Step 3: Add logical lowering helpers**

In `marivo/runtime/aoi_lowering.py`, add these helper shapes:

```python
def _artifact_input(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        value = value.model_dump(exclude_none=True)
    if not isinstance(value, dict) or not value.get("artifact_id"):
        raise ValueError("artifact input must include artifact_id")
    return {
        "artifact_id": str(value["artifact_id"]),
        "transforms": list(value.get("transforms") or []),
    }


def _operation_node(
    node_id: str,
    operation: str,
    *,
    inputs: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "operation": operation,
        "inputs": inputs or [],
        "params": params or {},
    }
```

Update `Forecast` lowering so v0.3 request `source` becomes `inputs[0]`, while source-style old fields are removed after schema regeneration.

- [ ] **Step 4: Lower derived requests into stable DAG dictionaries**

Update `lower_aoi_derived_request` so `validate` returns:

```python
return {
    "root_operation": "validate",
    "nodes": [
        _operation_node("observe_current", "observe", params={"metric": request.metric, "slice": _dump_slice(request.current)}),
        _operation_node("observe_baseline", "observe", params={"metric": request.metric, "slice": _dump_slice(request.baseline)}),
        _operation_node(
            "test",
            "test",
            inputs=[
                {
                    "artifact_id": "${observe_current}",
                    "transforms": [{"kind": "summarize_samples", "grain": request.grain, "groups": [{"name": "current"}]}],
                },
                {
                    "artifact_id": "${observe_baseline}",
                    "transforms": [{"kind": "summarize_samples", "grain": request.grain, "groups": [{"name": "baseline"}]}],
                },
            ],
            params={"hypothesis": request.hypothesis.model_dump(exclude_none=True)},
        ),
    ],
}
```

Use `${node_id}` sentinel refs only inside this private lowering dictionary; do not expose them in public AOI responses.

- [ ] **Step 5: Run lowering tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_lowering.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit lowering if commits are allowed**

```bash
git add marivo/runtime/aoi_lowering.py tests/runtime/test_aoi_lowering.py
git commit -m "feat(aoi): lower requests into artifact DAGs"
```

## Task 7: Envelope Helpers and Atomic Runner Output

**Files:**
- Create: `marivo/runtime/aoi_envelopes.py`
- Modify: `marivo/runtime/intent_execution.py`
- Modify: `marivo/runtime/intents/observe.py`
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `marivo/runtime/intents/decompose.py`
- Modify: `marivo/runtime/intents/correlate.py`
- Modify: `marivo/runtime/intents/detect.py`
- Modify: `marivo/runtime/intents/forecast.py`
- Modify: `marivo/runtime/intents/test.py`
- Modify: `tests/runtime/test_aoi_intent_execution.py`
- Modify: `tests/runtime/intents/test_observe_runner.py`
- Modify: `tests/runtime/intents/test_compare_runner.py`

- [ ] **Step 1: Add failing envelope helper tests**

Append to `tests/runtime/test_aoi_intent_execution.py`:

```python
from marivo.runtime.aoi_envelopes import build_success_artifact, unresolved_failure_artifact


def test_build_success_artifact_uses_v0_3_envelope() -> None:
    artifact = build_success_artifact(
        artifact_id="art_scalar",
        artifact_family="metric_frame",
        shape="scalar",
        subject={"kind": "metric", "metric_ref": "metric.revenue"},
        axes=[],
        measures=[{"name": "value"}],
        capabilities=["comparable"],
        producing_operation="observe",
        source_artifacts=[],
        applied_transforms=[],
        payload={"value": 42.0},
    )

    assert artifact["artifact_family"] == "metric_frame"
    assert artifact["payload"] == {"value": 42.0}
    assert "result" not in artifact


def test_unresolved_failure_artifact_does_not_forge_family() -> None:
    artifact = unresolved_failure_artifact(
        artifact_id="art_failed",
        operation="forecast",
        code="artifact_ref.not_found",
        message="missing artifact",
    )

    assert artifact["failure_stage"] == "unresolved"
    assert artifact["operation"] == "forecast"
    assert "artifact_family" not in artifact
    assert artifact["failure"]["code"] == "artifact_ref.not_found"
```

- [ ] **Step 2: Run helper tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_intent_execution.py -q
```

Expected: FAIL because `marivo.runtime.aoi_envelopes` does not exist.

- [ ] **Step 3: Implement envelope helpers**

Create `marivo/runtime/aoi_envelopes.py`:

```python
from __future__ import annotations

from typing import Any


def analysis_failure(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


def build_success_artifact(
    *,
    artifact_id: str,
    artifact_family: str,
    shape: str,
    subject: dict[str, Any],
    axes: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    capabilities: list[str],
    producing_operation: str,
    source_artifacts: list[dict[str, Any]],
    applied_transforms: list[dict[str, Any]],
    payload: dict[str, Any],
    manifest_id: str | None = None,
) -> dict[str, Any]:
    lineage: dict[str, Any] = {
        "producing_operation": producing_operation,
        "source_artifacts": source_artifacts,
        "applied_transforms": applied_transforms,
    }
    if manifest_id is not None:
        lineage["manifest_id"] = manifest_id
    return {
        "artifact_id": artifact_id,
        "artifact_family": artifact_family,
        "shape": shape,
        "subject": subject,
        "axes": axes,
        "measures": measures,
        "capabilities": capabilities,
        "lineage": lineage,
        "payload": payload,
    }


def resolved_failure_artifact(
    *,
    artifact_id: str,
    artifact_family: str,
    shape: str,
    subject: dict[str, Any],
    axes: list[dict[str, Any]],
    measures: list[dict[str, Any]],
    producing_operation: str,
    source_artifacts: list[dict[str, Any]],
    applied_transforms: list[dict[str, Any]],
    code: str,
    message: str,
    manifest_id: str | None = None,
) -> dict[str, Any]:
    artifact = build_success_artifact(
        artifact_id=artifact_id,
        artifact_family=artifact_family,
        shape=shape,
        subject=subject,
        axes=axes,
        measures=measures,
        capabilities=[],
        producing_operation=producing_operation,
        source_artifacts=source_artifacts,
        applied_transforms=applied_transforms,
        payload={},
        manifest_id=manifest_id,
    )
    artifact.pop("payload")
    artifact["failure_stage"] = "resolved"
    artifact["failure"] = analysis_failure(code, message)
    return artifact


def unresolved_failure_artifact(
    *,
    artifact_id: str,
    operation: str,
    code: str,
    message: str,
    subject: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_id": artifact_id,
        "failure_stage": "unresolved",
        "operation": operation,
        "lineage": {
            "producing_operation": operation,
            "source_artifacts": [],
            "applied_transforms": [],
        },
        "failure": analysis_failure(code, message),
    }
    if subject is not None:
        artifact["subject"] = subject
    return artifact
```

- [ ] **Step 4: Update atomic runners one at a time**

For each atomic runner, preserve the existing SQL/runtime work and wrap the final result dictionary with `build_success_artifact`. Use these target mappings:

```python
OPERATION_OUTPUTS = {
    "observe": ("metric_frame", "scalar|time_series|segmented|panel"),
    "compare": ("delta_frame", "scalar_delta|time_series_delta|segmented_delta|panel_delta"),
    "decompose": ("attribution_frame", "ranked_contributions"),
    "correlate": ("association_result", "pairwise_association"),
    "detect": ("candidate_set", "ranked_candidates"),
    "forecast": ("forecast_frame", "forecast_series"),
    "test": ("hypothesis_test_result", "two_sample_mean"),
}
```

After each runner change, run its focused test file. Example for observe:

```bash
.venv/bin/pytest tests/runtime/intents/test_observe_runner.py -q
```

Expected for each focused file: PASS after that runner is updated.

- [ ] **Step 5: Ensure artifact store receives guard metadata**

Where runners call `runtime.insert_artifact(...)`, pass:

```python
artifact_family=artifact["artifact_family"],
shape=artifact["shape"],
failure_stage=artifact.get("failure_stage"),
capabilities=list(artifact.get("capabilities") or []),
axes=list(artifact.get("axes") or []),
measures=list(artifact.get("measures") or []),
manifest_id=(artifact.get("lineage") or {}).get("manifest_id"),
```

- [ ] **Step 6: Run runtime intent suite**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_intent_execution.py tests/runtime/intents -q
```

Expected: PASS.

- [ ] **Step 7: Commit atomic envelope gate if commits are allowed**

```bash
git add marivo/runtime/aoi_envelopes.py marivo/runtime/intent_execution.py marivo/runtime/intents tests/runtime
git commit -m "feat(aoi): emit v0.3 atomic artifacts"
```

## Task 8: Derived Primary Artifacts and Execution Manifests

**Files:**
- Create: `marivo/runtime/aoi_manifest.py`
- Modify: `marivo/runtime/intents/derived_envelopes.py`
- Modify: `marivo/runtime/intents/validate.py`
- Modify: `marivo/runtime/intents/attribute.py`
- Modify: `marivo/runtime/intents/diagnose.py`
- Create: `tests/runtime/test_aoi_manifest.py`
- Modify: `tests/runtime/test_derived_aoi_envelopes.py`

- [ ] **Step 1: Write manifest tests**

Create `tests/runtime/test_aoi_manifest.py`:

```python
from __future__ import annotations

from marivo.runtime.aoi_manifest import build_execution_manifest


def test_manifest_requires_primary_artifact_id() -> None:
    manifest = build_execution_manifest(
        manifest_id="man_1",
        root_operation="diagnose",
        primary_artifact_id="art_diagnosis",
        nodes=[
            {
                "node_id": "detect",
                "operation": "detect",
                "status": "succeeded",
                "output_artifact_id": "art_candidates",
            }
        ],
        edges=[],
    )

    assert manifest["primary_artifact_id"] == "art_diagnosis"
    assert manifest["nodes"][0]["status"] == "succeeded"


def test_manifest_records_failed_candidate_branch() -> None:
    manifest = build_execution_manifest(
        manifest_id="man_diag",
        root_operation="diagnose",
        primary_artifact_id="art_diagnosis",
        nodes=[
            {
                "node_id": "candidate_1_compare",
                "operation": "compare",
                "status": "failed",
                "failure_code": "operation.insufficient_data",
            },
            {
                "node_id": "candidate_1_decompose",
                "operation": "decompose",
                "status": "skipped",
                "failure_code": "manifest.node_failed",
            },
        ],
        edges=[],
    )

    assert manifest["nodes"][0]["failure_code"] == "operation.insufficient_data"
    assert manifest["nodes"][1]["status"] == "skipped"
```

Replace the old bundle assertions in `tests/runtime/test_derived_aoi_envelopes.py` with assertions that no `bundle_type`, `aoi_artifacts`, or `product_metadata` appears in derived public responses.

- [ ] **Step 2: Run manifest tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_manifest.py tests/runtime/test_derived_aoi_envelopes.py -q
```

Expected: FAIL because manifest helper does not exist and old bundle tests still expect v0.2 bundles until edited.

- [ ] **Step 3: Implement manifest helper**

Create `marivo/runtime/aoi_manifest.py`:

```python
from __future__ import annotations

from typing import Any


def build_execution_manifest(
    *,
    manifest_id: str,
    root_operation: str,
    primary_artifact_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    if not primary_artifact_id:
        raise ValueError("primary_artifact_id is required")
    return {
        "manifest_id": manifest_id,
        "root_operation": root_operation,
        "primary_artifact_id": primary_artifact_id,
        "nodes": nodes,
        "edges": edges,
    }
```

- [ ] **Step 4: Replace derived bundle builder**

Replace `build_derived_bundle_envelope(...)` call sites with a helper that returns:

```python
{
    "intent_type": step_type,
    "step_type": step_type,
    "step_ref": {
        "session_id": session_id,
        "step_id": step_id,
        "step_type": step_type,
    },
    "artifact_id": primary_artifact_id,
    "result": primary_artifact,
    "manifest": manifest,
}
```

The public `result` value is the primary `ArtifactEnvelope`, not a bundle.

- [ ] **Step 5: Update derived runner semantics**

Use this mapping:

```python
DERIVED_PRIMARY_OUTPUTS = {
    "validate": ("hypothesis_test_result", "two_sample_mean"),
    "attribute": ("attribution_frame", "ranked_contributions"),
    "diagnose": ("diagnosis_result", "candidate_diagnoses"),
}
```

For `diagnose`, record failed candidate branches with:

```python
{
    "node_id": "candidate_1_compare",
    "operation": "compare",
    "status": "failed",
    "failure_code": "operation.insufficient_data",
}
```

and keep the primary `diagnosis_result` successful when at least one candidate branch produced a usable diagnosis payload.

- [ ] **Step 6: Run derived tests**

Run:

```bash
.venv/bin/pytest tests/runtime/test_aoi_manifest.py tests/runtime/test_derived_aoi_envelopes.py tests/runtime/intents/test_validate_runner.py tests/runtime/intents/test_attribute_runner.py tests/runtime/intents/test_diagnose_runner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit derived manifest gate if commits are allowed**

```bash
git add marivo/runtime/aoi_manifest.py marivo/runtime/intents/derived_envelopes.py marivo/runtime/intents/validate.py marivo/runtime/intents/attribute.py marivo/runtime/intents/diagnose.py tests/runtime/test_aoi_manifest.py tests/runtime/test_derived_aoi_envelopes.py tests/runtime/intents
git commit -m "feat(aoi): replace derived bundles with manifests"
```

## Task 9: HTTP and MCP Transport Projection

**Files:**
- Modify: `marivo/contracts/aoi_projection.py`
- Modify: `marivo/transports/http/models/intent_response_models.py`
- Modify: `marivo/transports/http/sessions.py`
- Modify: `marivo/transports/mcp/tools/intents.py`
- Modify: `tests/transports/http/test_http_aoi_intents.py`
- Modify: `tests/transports/mcp/test_mcp_aoi_adapter.py`

- [ ] **Step 1: Add transport tests for v0.3 response shape**

Append to `tests/transports/http/test_http_aoi_intents.py`:

```python
def test_http_observe_response_uses_v0_3_artifact_envelope(client, session_id):
    response = client.post(
        f"/sessions/{session_id}/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-02T00:00:00Z",
            },
        },
    )

    assert response.status_code == 200
    artifact = response.json()["result"]
    assert artifact["artifact_family"] == "metric_frame"
    assert "payload" in artifact
    assert "result" not in artifact


def test_http_rejects_projection_ref_as_operation_input(client, session_id):
    response = client.post(
        f"/sessions/{session_id}/intents/forecast",
        json={
            "source": {"artifact_id": "projection:art_metric:table"},
            "horizon": {"buckets": 7, "granularity": "day"},
        },
    )

    assert response.status_code in {400, 422}
```

Add MCP equivalent assertions in `tests/transports/mcp/test_mcp_aoi_adapter.py` for `diagnose` returning `result` and `manifest`, without `bundle_type`.

- [ ] **Step 2: Run transport tests**

Run:

```bash
.venv/bin/pytest tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py -q
```

Expected: FAIL because HTTP response models and projection still normalize v0.2 wrappers.

- [ ] **Step 3: Simplify AOI projection boundary**

In `marivo/contracts/aoi_projection.py`, make `project_aoi_artifact_from_any` return a validated v0.3 envelope when given an envelope and raise `ValidationError` for old wrappers at public boundaries. Keep read-only presentation helpers for artifact display, but do not emit values that can be used as operation inputs.

Core replacement:

```python
def project_aoi_artifact_from_any(value: dict[str, Any]) -> dict[str, Any]:
    return artifact_to_envelope_result(validate_aoi_artifact(value))
```

- [ ] **Step 4: Update HTTP response normalization**

In `marivo/transports/http/sessions.py`, update `_atomic_intent_response`:

```python
def _atomic_intent_response(intent_type: str, result: dict[str, Any]) -> dict[str, Any]:
    artifact = result.get("result")
    if not isinstance(artifact, dict):
        artifact = result
    return {
        "intent_type": result.get("intent_type") or intent_type,
        "step_type": result.get("step_type") or intent_type,
        "step_ref": result.get("step_ref"),
        "artifact_id": artifact.get("artifact_id") or result.get("artifact_id"),
        "result": project_aoi_artifact_from_any(artifact),
    }
```

Update `_derived_intent_response` to return:

```python
{
    "intent_type": result.get("intent_type"),
    "step_type": result.get("step_type"),
    "step_ref": result.get("step_ref"),
    "artifact_id": result.get("artifact_id"),
    "result": project_aoi_artifact_from_any(result["result"]),
    "manifest": result["manifest"],
}
```

- [ ] **Step 5: Update response models**

In `marivo/transports/http/models/intent_response_models.py`, replace subclasses of `aoi.Artifact1` / `aoi.Artifact2` with `aoi.ArtifactEnvelope`. Use:

```python
class AtomicIntentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: str
    step_type: str
    step_ref: StepRef | None = None
    artifact_id: str
    result: aoi.ArtifactEnvelope
```

Derived responses add:

```python
    manifest: aoi.ExecutionManifest
```

- [ ] **Step 6: Run transport tests**

Run:

```bash
.venv/bin/pytest tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit transport gate if commits are allowed**

```bash
git add marivo/contracts/aoi_projection.py marivo/transports/http/models/intent_response_models.py marivo/transports/http/sessions.py marivo/transports/mcp/tools/intents.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py
git commit -m "feat(aoi): expose v0.3 transport responses"
```

## Task 10: E2E Conformance and Documentation

**Files:**
- Modify: `tests/integration/test_e2e_osi_aoi.py`
- Modify: `aoi-spec/spec.md`
- Modify: `docs/api/intent-steps.md`
- Modify: MCP/user tool docs if present
- Modify: `TODOS.md`

- [ ] **Step 1: Add E2E test for observe -> compare -> diagnose readback**

Append to `tests/integration/test_e2e_osi_aoi.py`:

```python
def test_e2e_aoi_v0_3_artifact_algebra_reference_scenario(client, session_id):
    observe_response = client.post(
        f"/sessions/{session_id}/intents/observe",
        json={
            "metric": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-05-01T00:00:00Z",
                "end": "2026-05-08T00:00:00Z",
            },
            "granularity": "day",
        },
    )
    assert observe_response.status_code == 200
    observe_artifact = observe_response.json()["result"]
    assert observe_artifact["artifact_family"] == "metric_frame"
    assert observe_artifact["shape"] == "time_series"

    forecast_response = client.post(
        f"/sessions/{session_id}/intents/forecast",
        json={
            "source": {"artifact_id": observe_artifact["artifact_id"]},
            "horizon": {"buckets": 7, "granularity": "day"},
        },
    )
    assert forecast_response.status_code == 200
    forecast_artifact = forecast_response.json()["result"]
    assert forecast_artifact["artifact_family"] == "forecast_frame"
    assert forecast_artifact["lineage"]["source_artifacts"][0]["artifact_id"] == observe_artifact["artifact_id"]
```

- [ ] **Step 2: Run E2E test**

Run:

```bash
.venv/bin/pytest tests/integration/test_e2e_osi_aoi.py -q
```

Expected: PASS after previous gates have landed. If fixture setup needs current schema fields, update fixture request JSON in the same task and keep the assertions above.

- [ ] **Step 3: Rewrite public spec from design**

Update `aoi-spec/spec.md` with sections matching:

```markdown
# AOI v0.3 Artifact Algebra

## Artifact Envelope

All public analysis outputs are `ArtifactEnvelope` values. Successful artifacts carry
`payload`; failed artifacts carry `failure`. `payload` and `failure` are mutually exclusive.

## Artifact Registry

`registry/artifact-registry.yaml` is canonical for families, shapes, measures,
capabilities, transforms, and consumers.

## Transform DSL

`slice`, `filter`, `rollup`, and `summarize_samples` are inline request transforms.
They are never public operations and never create public artifact ids.

## Derived Operations

`validate`, `attribute`, and `diagnose` return a primary artifact and an
`ExecutionManifest`. The manifest is audit metadata, not an analysis input.
```

- [ ] **Step 4: Update API/MCP docs**

Add this example to `docs/api/intent-steps.md`:

```json
{
  "source": {
    "artifact_id": "art_panel",
    "transforms": [
      {
        "kind": "slice",
        "axis": "dimension",
        "selector": {"dimension": "country", "value": "US"}
      }
    ]
  },
  "horizon": {"buckets": 14, "granularity": "day"}
}
```

Add this rule near the example:

```markdown
Projection refs are display handles. They cannot be used as `artifact_id` inputs for AOI operations.
```

- [ ] **Step 5: Update implementation tracker**

If `TODOS.md` contains an AOI v0.3 tracker, replace the broad tracker item with:

```markdown
AOI v0.3 Artifact Algebra landed through public spec, registry, generated contracts,
artifact store metadata, transform guard, derived manifests, transport projection,
and E2E conformance.
```

- [ ] **Step 6: Run final targeted suite**

Run:

```bash
.venv/bin/pytest tests/contracts/test_generated_models.py tests/contracts/test_aoi_registry.py tests/contracts/test_aoi_runtime_contract.py tests/contracts/test_artifact_store.py tests/adapters/test_file_artifact_store.py tests/runtime/test_aoi_lowering.py tests/runtime/test_aoi_transform_guard.py tests/runtime/test_aoi_manifest.py tests/runtime/test_derived_aoi_envelopes.py tests/transports/http/test_http_aoi_intents.py tests/transports/mcp/test_mcp_aoi_adapter.py tests/integration/test_e2e_osi_aoi.py -q
```

Expected: PASS.

- [ ] **Step 7: Run repository checks**

Run:

```bash
make typecheck
make lint
```

Expected: both commands exit 0.

- [ ] **Step 8: Commit conformance/docs gate if commits are allowed**

```bash
git add aoi-spec docs/api docs/specs/analysis tests/integration TODOS.md
git commit -m "docs(aoi): document v0.3 artifact algebra"
```

## Final Verification

- [ ] **Step 1: Run full test suite**

```bash
make test
```

Expected: exits 0.

- [ ] **Step 2: Run typecheck**

```bash
make typecheck
```

Expected: exits 0.

- [ ] **Step 3: Run lint**

```bash
make lint
```

Expected: exits 0.

- [ ] **Step 4: Check generated contracts are current**

```bash
.venv/bin/python scripts/generate_contract_models.py --check
```

Expected: exits 0 with no generated-file diff.

- [ ] **Step 5: Inspect diff for forbidden legacy public shapes**

```bash
rg -n "Artifact1|Artifact2|bundle_type|aoi_artifacts|product_metadata|result \\| failure" aoi-spec marivo tests docs
```

Expected: no matches in public AOI implementation paths except historical notes in `docs/specs/analysis/aoi-v0.3-design.md` and tests that assert old shapes are rejected.

## Self-Review

**Spec coverage:** The plan maps the design gates to implementation tasks: registry in Task 1, schema/envelope in Task 2, generated contracts in Task 3, artifact store in Task 4, transform guard in Task 5, lowering in Task 6, atomic envelopes in Task 7, derived manifests in Task 8, transport projection in Task 9, conformance and docs in Task 10.

**Placeholder scan:** The plan contains concrete file paths, test code, helper code, commands, and expected outcomes. It avoids deferred implementation language.

**Type consistency:** Runtime helpers consistently use `ArtifactEnvelope`, `ExecutionManifest`, `artifact_family`, `shape`, `axes`, `measures`, `capabilities`, `lineage`, `payload`, and `failure`. The registry loader returns immutable dataclasses consumed by transform guard and contract tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-aoi-v0-3-artifact-algebra.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
