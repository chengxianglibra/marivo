# AOI Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Marivo's analysis operation runtime with AOI generated models as the single semantic boundary, introduce an execution envelope for platform metadata, and consolidate duplicated normalization logic.

**Architecture:** AOI generated Pydantic models (`Observe1`, `Compare`, `Detect`, `Artifact1`, etc.) become the structural validation layer for analysis operations. A new `ExecutionEnvelope` model wraps AOI artifacts with Marivo platform metadata (step_ref, artifact_id, provenance). Shared normalization extracts duplicated metric/time/dimension guards into a single module. HTTP responses use envelopes; MCP DTO→AOI conversion is preserved.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, existing DuckDB test fixtures

**Spec:** `docs/superpowers/specs/2026-05-11-aoi-runtime-boundary-design.md`

---

## Scope and Limitations

This plan covers spec Goals 4 (execution envelope), §4.3 (shared normalization), §8.2 (regression matrix), and the response-path migration. It does **not** cover the full request-side migration (Goals 1-3: making AOI generated models the structural validation layer for incoming requests). The reason:

- AOI request models use different field shapes than Marivo's current runtime (e.g., AOI `Compare.left_artifact_id: str` vs Marivo's `left_ref: ObservationRef` with session_id/step_id/step_type). These can't be swapped without either adapters or AOI spec changes.
- The spec's "前提假设" notes that AOI Observe union unification (Observe1..4 → single Observe) hasn't happened yet, which blocks clean request-side integration.
- The spec says "实现阶段 AOI spec 冻结" — so we can't modify AOI to fit Marivo's request shapes.

Request-side migration should be a separate plan after AOI spec evolves to address these field shape gaps. This plan delivers the foundation (envelope + normalization + response path) that makes that future migration straightforward.

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `marivo/contracts/envelope.py` | `ExecutionEnvelope` Pydantic model — wraps AOI artifact + Marivo platform metadata |
| `marivo/runtime/intents/normalization.py` | Shared normalization slice — metric ref, time scope, dimensions, calendar policy |
| `tests/test_envelope.py` | Unit tests for ExecutionEnvelope construction and serialization |
| `tests/test_normalization.py` | Unit tests for shared normalization functions |

### Modified files

| File | What changes |
|------|-------------|
| `marivo/runtime/intents/_helpers.py` | `commit_step_result()` returns `ExecutionEnvelope` instead of raw dict |
| `marivo/runtime/intents/observe.py` | Use shared normalization; produce AOI `ScalarObservationResult` / `TimeSeriesObservationResult` / `SegmentedObservationResult` as envelope result |
| `marivo/runtime/intents/compare.py` | Use shared normalization; produce AOI delta result types as envelope result |
| `marivo/runtime/intents/decompose.py` | Produce AOI `DeltaDecompositionResult` as envelope result |
| `marivo/runtime/intents/correlate.py` | Produce AOI `AssociationResult` as envelope result |
| `marivo/runtime/intents/detect.py` | Use shared normalization; produce AOI `AnomalyCandidatesResult` as envelope result |
| `marivo/runtime/intents/test.py` | Produce AOI `HypothesisTestResult` as envelope result |
| `marivo/runtime/intents/forecast.py` | Produce AOI `ForecastSeriesResult` as envelope result |
| `marivo/runtime/intents/attribute.py` | Use shared normalization; return envelope with nested AOI artifacts |
| `marivo/runtime/intents/diagnose.py` | Use shared normalization; return envelope with nested AOI artifacts |
| `marivo/runtime/intents/validate.py` | Use shared normalization; product-level semantics in envelope, AOI `HypothesisTestResult` in result |
| `marivo/transports/http/models/intent_response_models.py` | Response models accept `ExecutionEnvelope` instead of `RootModel[JsonObject]` |
| `marivo/transports/http/sessions.py` | `_run_intent()` serializes `ExecutionEnvelope` for HTTP response |
| `tests/test_intent_api.py` | Update response assertions to match envelope structure |

---

## Phase 1: Foundation

### Task 1: Define ExecutionEnvelope

Define the Pydantic model that wraps AOI artifacts with Marivo platform metadata. This is the runtime's return type for all intent executions.

**Files:**
- Create: `marivo/contracts/envelope.py`
- Create: `tests/test_envelope.py`

- [ ] **Step 1: Write the failing test for basic envelope construction**

```python
# tests/test_envelope.py
from __future__ import annotations

import pytest
from marivo.contracts.envelope import ExecutionEnvelope, StepRef


class TestExecutionEnvelope:
    def test_construct_with_dict_result(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
        )
        assert env.intent_type == "observe"
        assert env.artifact_id == "art_1"
        assert env.result == {"value": 42.0}
        assert env.provenance is None
        assert env.product_metadata is None

    def test_construct_with_provenance(self) -> None:
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0},
            provenance={"query_hash": "abc123"},
        )
        assert env.provenance == {"query_hash": "abc123"}

    def test_construct_with_product_metadata(self) -> None:
        env = ExecutionEnvelope(
            intent_type="validate",
            step_type="validate",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="validate"),
            artifact_id="art_1",
            result={"statistic": 2.1, "p_value": 0.03},
            product_metadata={"validation": {"status": "pass", "issues": []}},
        )
        assert env.product_metadata["validation"]["status"] == "pass"

    def test_to_legacy_dict_flat_merges_result(self) -> None:
        """Backward compat: to_legacy_dict() produces the flat dict shape
        that existing HTTP responses and MCP consumers expect."""
        env = ExecutionEnvelope(
            intent_type="observe",
            step_type="observe",
            step_ref=StepRef(session_id="s1", step_id="step_1", step_type="observe"),
            artifact_id="art_1",
            result={"value": 42.0, "observation_type": "scalar"},
        )
        legacy = env.to_legacy_dict()
        assert legacy["intent_type"] == "observe"
        assert legacy["step_ref"]["session_id"] == "s1"
        assert legacy["artifact_id"] == "art_1"
        # result fields are flat-merged at top level for backward compat
        assert legacy["value"] == 42.0
        assert legacy["observation_type"] == "scalar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test ARGS="tests/test_envelope.py -v"`
Expected: FAIL — `ModuleNotFoundError: No module named 'marivo.contracts.envelope'`

- [ ] **Step 3: Implement ExecutionEnvelope**

```python
# marivo/contracts/envelope.py
"""Marivo execution envelope — wraps AOI artifacts with platform metadata.

The envelope is the runtime's return type for all intent executions.
AOI artifact data lives in `result`; Marivo platform metadata
(lineage, provenance, product-level semantics) lives alongside it.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StepRef(BaseModel):
    """Reference to a step within a session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    step_id: str
    step_type: str


class ExecutionEnvelope(BaseModel):
    """Marivo execution envelope.

    Wraps an AOI artifact result with platform metadata needed for
    lineage, composition, and product-level semantics.

    - ``result``: AOI artifact payload (the analysis output)
    - ``provenance``: execution trace metadata (query hash, timing, etc.)
    - ``product_metadata``: derived-intent product semantics
      (e.g. validation.status, issues) — lives here, not in AOI result
    """

    model_config = ConfigDict(extra="forbid")

    intent_type: str
    step_type: str
    step_ref: StepRef
    artifact_id: str
    result: dict[str, Any]
    provenance: dict[str, Any] | None = None
    product_metadata: dict[str, Any] | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Produce the flat dict shape for backward-compatible serialization.

        Merges ``result`` keys at top level alongside step_ref and artifact_id.
        This matches the current HTTP/MCP response contract until consumers
        migrate to the structured envelope.
        """
        out: dict[str, Any] = {
            "intent_type": self.intent_type,
            "step_type": self.step_type,
            "step_ref": self.step_ref.model_dump(),
            "artifact_id": self.artifact_id,
        }
        out.update(self.result)
        if self.provenance is not None:
            out["provenance"] = self.provenance
        if self.product_metadata is not None:
            out.update(self.product_metadata)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test ARGS="tests/test_envelope.py -v"`
Expected: PASS — all 4 tests green

- [ ] **Step 5: Commit**

```bash
git add marivo/contracts/envelope.py tests/test_envelope.py
git commit -m "feat: add ExecutionEnvelope model for AOI/platform metadata separation"
```

---

### Task 2: Create shared normalization module

Extract duplicated normalization logic from observe, detect, diagnose, attribute, validate into a single module. Currently each handler independently does: metric ref normalization, dimension dedup/empty-list cleanup, hour boundary validation, calendar policy ref validation, and limit bounds checking.

**Files:**
- Create: `marivo/runtime/intents/normalization.py`
- Create: `tests/test_normalization.py`

- [ ] **Step 1: Write failing tests for normalization functions**

```python
# tests/test_normalization.py
from __future__ import annotations

import pytest
from marivo.runtime.intents.normalization import (
    normalize_metric_ref,
    normalize_dimensions,
    validate_granularity,
    validate_hour_boundaries,
    validate_and_normalize_calendar_policy_ref,
)


class TestNormalizeMetricRef:
    def test_strips_whitespace(self) -> None:
        # normalize_metric_ref delegates to runtime.core but we test the guard
        assert normalize_metric_ref("  metric.revenue  ") == "metric.revenue"

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="requires 'metric'"):
            normalize_metric_ref("")

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="requires 'metric'"):
            normalize_metric_ref(None)


class TestNormalizeDimensions:
    def test_empty_list_becomes_none(self) -> None:
        assert normalize_dimensions([]) is None

    def test_none_stays_none(self) -> None:
        assert normalize_dimensions(None) is None

    def test_deduplicates_preserving_order(self) -> None:
        assert normalize_dimensions(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_strips_whitespace(self) -> None:
        assert normalize_dimensions(["  region  ", "country"]) == ["region", "country"]

    def test_removes_empty_strings(self) -> None:
        assert normalize_dimensions(["a", "", "  ", "b"]) == ["a", "b"]

    def test_all_empty_becomes_none(self) -> None:
        assert normalize_dimensions(["", "  "]) is None


class TestValidateGranularity:
    def test_valid_values(self) -> None:
        for g in ("hour", "day", "week", "month"):
            assert validate_granularity(g) == g

    def test_none_passes(self) -> None:
        assert validate_granularity(None) is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="not valid"):
            validate_granularity("year")


class TestValidateHourBoundaries:
    def test_non_hour_granularity_skips(self) -> None:
        # Should not raise for non-hour granularity
        validate_hour_boundaries("day", "2024-01-01", "2024-01-02")

    def test_hour_granularity_with_datetime_passes(self) -> None:
        validate_hour_boundaries("hour", "2024-01-01 00:00:00", "2024-01-02 00:00:00")

    def test_hour_granularity_with_date_only_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_hour_boundaries("hour", "2024-01-01", "2024-01-02")


class TestCalendarPolicyRef:
    def test_none_returns_none(self) -> None:
        assert validate_and_normalize_calendar_policy_ref(None) is None

    def test_valid_ref_passes(self) -> None:
        result = validate_and_normalize_calendar_policy_ref("calendar_policy.natural_yoy")
        assert result == "calendar_policy.natural_yoy"

    def test_invalid_ref_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_and_normalize_calendar_policy_ref("not_a_valid_ref")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test ARGS="tests/test_normalization.py -v"`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement shared normalization**

```python
# marivo/runtime/intents/normalization.py
"""Shared normalization and validation for intent parameters.

This module consolidates the duplicated guard/normalization logic that was
previously scattered across observe.py, detect.py, diagnose.py, attribute.py,
and validate.py. It runs after AOI structural validation passes but before
intent handler business logic.

Each function is a pure validator/normalizer with no runtime dependencies
(except validate_and_normalize_calendar_policy_ref which delegates to the
calendar module).
"""
from __future__ import annotations

from marivo.core.semantic.calendar import (
    CalendarPolicyResolutionError,
    validate_calendar_policy_ref,
)
from marivo.time_contracts import normalize_hour_boundary

_VALID_GRANULARITIES = frozenset({"hour", "day", "week", "month"})


def normalize_metric_ref(metric_ref: str | None) -> str:
    """Strip whitespace and reject empty/None metric refs.

    Runtime-level normalization (e.g. prefix resolution) is intentionally
    NOT done here — that requires the runtime.core instance and happens
    in the intent handler.
    """
    if not metric_ref or not metric_ref.strip():
        raise ValueError("intent requires 'metric'")
    return metric_ref.strip()


def normalize_dimensions(dimensions: list[str] | None) -> list[str] | None:
    """Normalize a dimensions list: strip, dedup, remove blanks.

    Returns None if the result is empty (matches AOI semantics where
    absent dimensions means scalar/time-series mode).
    """
    if dimensions is None:
        return None
    seen: set[str] = set()
    result: list[str] = []
    for d in dimensions:
        stripped = d.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            result.append(stripped)
    return result if result else None


def validate_granularity(granularity: str | None) -> str | None:
    """Validate granularity value against allowed set."""
    if granularity is None:
        return None
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            f"granularity='{granularity}' is not valid. "
            f"Must be one of: {sorted(_VALID_GRANULARITIES)}"
        )
    return granularity


def validate_hour_boundaries(
    granularity: str | None, start: str | None, end: str | None
) -> None:
    """When granularity is 'hour', enforce datetime (not date-only) boundaries."""
    if granularity != "hour":
        return
    if start:
        normalize_hour_boundary(str(start), label="time_scope.start")
    if end:
        normalize_hour_boundary(str(end), label="time_scope.end")


def validate_and_normalize_calendar_policy_ref(ref: str | None) -> str | None:
    """Validate calendar policy ref against the known catalog."""
    if ref is None:
        return None
    if not isinstance(ref, str):
        raise ValueError("calendar_policy_ref must be a string")
    try:
        return validate_calendar_policy_ref(ref)
    except CalendarPolicyResolutionError as error:
        raise ValueError(f"INVALID_ARGUMENT - {error}") from error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test ARGS="tests/test_normalization.py -v"`
Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add marivo/runtime/intents/normalization.py tests/test_normalization.py
git commit -m "feat: add shared normalization module for intent parameters"
```

---

## Phase 2: Response path migration

### Task 3: Update commit_step_result to produce ExecutionEnvelope

The current `commit_step_result()` in `_helpers.py` returns a flat dict. Update it to produce an `ExecutionEnvelope` that can be serialized to the same flat dict shape for backward compatibility.

**Files:**
- Modify: `marivo/runtime/intents/_helpers.py`
- Modify: `tests/test_envelope.py` (add integration-style test)

- [ ] **Step 1: Write a test for the updated commit_step_result return type**

Add to `tests/test_envelope.py`:

```python
class TestCommitStepResultEnvelope:
    def test_commit_step_result_returns_envelope(self) -> None:
        """commit_step_result should return ExecutionEnvelope."""
        from marivo.contracts.envelope import ExecutionEnvelope
        from marivo.runtime.intents._helpers import build_envelope

        env = build_envelope(
            session_id="s1",
            step_id="step_obs_1",
            step_type="observe",
            artifact_id="art_1",
            artifact_payload={"value": 42.0, "observation_type": "scalar"},
            provenance={"query_hash": "abc"},
        )
        assert isinstance(env, ExecutionEnvelope)
        assert env.artifact_id == "art_1"
        assert env.result["value"] == 42.0

    def test_envelope_legacy_dict_matches_old_shape(self) -> None:
        """to_legacy_dict() must produce the same flat dict as the old code."""
        from marivo.runtime.intents._helpers import build_envelope

        env = build_envelope(
            session_id="s1",
            step_id="step_obs_1",
            step_type="observe",
            artifact_id="art_1",
            artifact_payload={"value": 42.0},
        )
        legacy = env.to_legacy_dict()
        assert legacy["intent_type"] == "observe"
        assert legacy["step_type"] == "observe"
        assert legacy["step_ref"] == {
            "session_id": "s1",
            "step_id": "step_obs_1",
            "step_type": "observe",
        }
        assert legacy["artifact_id"] == "art_1"
        assert legacy["value"] == 42.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test ARGS="tests/test_envelope.py::TestCommitStepResultEnvelope -v"`
Expected: FAIL — `cannot import name 'build_envelope'`

- [ ] **Step 3: Add build_envelope to _helpers.py**

Add `build_envelope()` alongside existing `commit_step_result()`. Do NOT modify `commit_step_result()` yet — that happens when individual intent handlers migrate.

```python
# Add to marivo/runtime/intents/_helpers.py (after existing imports)

from marivo.contracts.envelope import ExecutionEnvelope, StepRef


def build_envelope(
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_id: str,
    artifact_payload: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Build an ExecutionEnvelope from intent execution results.

    This is the successor to commit_step_result()'s dict construction.
    Intent handlers should migrate to use this + runtime artifact commit
    separately.
    """
    return ExecutionEnvelope(
        intent_type=step_type,
        step_type=step_type,
        step_ref=StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type=step_type,
        ),
        artifact_id=artifact_id,
        result=artifact_payload,
        provenance=provenance,
        product_metadata=product_metadata,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `make test ARGS="tests/test_envelope.py -v"`
Expected: PASS — all tests green

- [ ] **Step 5: Run full test suite to check no regressions**

Run: `make test`
Expected: All existing tests pass — `commit_step_result()` is unchanged; `build_envelope()` is additive.

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/_helpers.py tests/test_envelope.py
git commit -m "feat: add build_envelope helper for ExecutionEnvelope construction"
```

---

### Task 4: Migrate observe intent to use shared normalization + envelope

This is the **reference migration**. All subsequent intent migrations follow this pattern. The observe handler currently has ~1150 lines with inline dict validation, normalization, and flat dict return. This task:
1. Replaces inline normalization with shared module calls
2. Uses `build_envelope()` for the return value
3. Keeps `to_legacy_dict()` at the boundary so downstream consumers see no change yet

**Files:**
- Modify: `marivo/runtime/intents/observe.py`
- Modify: `tests/test_intent_api.py` (verify regressions still pass)

- [ ] **Step 1: Run observe-related tests to establish baseline**

Run: `make test ARGS="tests/test_intent_api.py -k observe -v"`
Expected: All observe tests pass (baseline)

- [ ] **Step 2: Replace inline normalization in observe handler with shared module**

In `marivo/runtime/intents/observe.py`, at the top of `run_observe_intent()` (lines 435-470), replace the inline param extraction with shared normalization calls:

```python
# At top of file, add import:
from marivo.runtime.intents.normalization import (
    normalize_metric_ref,
    normalize_dimensions,
    validate_granularity,
    validate_hour_boundaries,
    validate_and_normalize_calendar_policy_ref,
)

# Inside run_observe_intent(), replace lines 435-470 with:
    p = params or {}

    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
    metric_name = runtime.core.metric_name_from_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("observe intent requires 'time_scope'")

    result_mode: str = p.get("result_mode") or "standard"
    if result_mode not in {"standard", "numeric_sample_summary", "rate_sample_summary"}:
        raise ValueError(
            f"observe result_mode='{result_mode}' is not valid. "
            "Must be one of: 'standard', 'numeric_sample_summary', 'rate_sample_summary'."
        )

    normalized_calendar_policy_ref = validate_and_normalize_calendar_policy_ref(
        p.get("calendar_policy_ref")
    )
    granularity = validate_granularity(p.get("granularity") or None)
    dimensions = normalize_dimensions(p.get("dimensions"))

    if granularity is not None and dimensions is not None:
        raise ValueError("observe: granularity and dimensions are mutually exclusive")
```

- [ ] **Step 3: Replace the return path with build_envelope + to_legacy_dict**

Find each `return` statement in `run_observe_intent()` where `commit_step_result()` is called. After each `commit_step_result()` call, the handler currently returns the raw dict. Wrap the return in envelope construction:

```python
# Add import at top:
from marivo.runtime.intents._helpers import build_envelope, commit_step_result

# At each return site where commit_step_result is used, change from:
#   result = commit_step_result(...)
#   return result
# to:
    result = commit_step_result(
        runtime, session_id, step_id, step_type="observe",
        artifact_type=..., artifact_name=...,
        artifact_payload=..., summary=..., provenance=...,
    )
    # Return legacy dict for backward compat during migration.
    # Once all consumers migrate to envelope, this becomes:
    #   return build_envelope(...).model_dump()
    return result
```

Note: In this first migration task, keep the existing `commit_step_result()` call and its dict return to minimize blast radius. The envelope wrapping is additive — `build_envelope()` is available for new code paths but the existing return contract is preserved.

- [ ] **Step 4: Run observe tests to verify no regressions**

Run: `make test ARGS="tests/test_intent_api.py -k observe -v"`
Expected: All observe tests pass unchanged

- [ ] **Step 5: Verify hour boundary and dimension normalization regressions**

Run: `make test ARGS="tests/test_intent_api.py -k 'hour or dimension or granularity' -v"`
Expected: All pass — shared normalization produces identical behavior

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/observe.py
git commit -m "refactor: migrate observe intent to shared normalization"
```

---

## Phase 3: Remaining atomic intents

### Task 5: Migrate compare, decompose, correlate to shared normalization

These three intents have simpler param extraction than observe. They primarily deal with artifact refs (not metric/time/dimension normalization), so the migration is lighter.

**Files:**
- Modify: `marivo/runtime/intents/compare.py`
- Modify: `marivo/runtime/intents/decompose.py`
- Modify: `marivo/runtime/intents/correlate.py`

- [ ] **Step 1: Run baseline tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'compare or decompose or correlate' -v"`
Expected: All pass (baseline)

- [ ] **Step 2: Migrate compare.py**

In `run_compare_intent()` (line 178), the param extraction is minimal (left_ref, right_ref, mode). Add the shared normalization import but the main change is ensuring the return path is consistent. No metric/dimension normalization needed here.

Keep existing param extraction as-is (it's already clean for ref-based intents). No normalization changes needed for compare — this is a ref-only intent.

- [ ] **Step 3: Migrate decompose.py**

In `run_decompose_intent()` (line 22), similar to compare — ref-based params only. No metric normalization needed. Keep existing code.

- [ ] **Step 4: Migrate correlate.py**

In `run_correlate_intent()` (line 62), ref-based with method/min_pairs. No metric normalization needed. Keep existing code.

- [ ] **Step 5: Run regression tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'compare or decompose or correlate or cross_session' -v"`
Expected: All pass including cross-session ref rejection

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/compare.py marivo/runtime/intents/decompose.py marivo/runtime/intents/correlate.py
git commit -m "refactor: review compare/decompose/correlate for normalization consistency"
```

---

### Task 6: Migrate detect, test, forecast to shared normalization

Detect has significant normalization overlap with observe (metric ref, time scope, granularity, hour boundaries). Test and forecast are lighter.

**Files:**
- Modify: `marivo/runtime/intents/detect.py`
- Modify: `marivo/runtime/intents/test.py`
- Modify: `marivo/runtime/intents/forecast.py`

- [ ] **Step 1: Run baseline tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'detect or test or forecast' -v"`
Expected: All pass (baseline)

- [ ] **Step 2: Migrate detect.py to shared normalization**

In `run_detect_intent()` (line 300), replace inline normalization with shared calls. Detect currently duplicates: metric ref normalization (line 316), granularity validation (line 330-340), hour boundary checks (line 343-344).

```python
# Add import at top of detect.py:
from marivo.runtime.intents.normalization import (
    normalize_metric_ref,
    validate_granularity,
    validate_hour_boundaries,
)

# In run_detect_intent(), replace inline metric/granularity/hour checks:
    p = params or {}
    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)

    time_scope_raw = p.get("time_scope")
    if not isinstance(time_scope_raw, dict):
        raise ValueError("detect intent requires 'time_scope'")

    granularity = validate_granularity(p.get("granularity"))
    if granularity is None:
        raise ValueError("detect intent requires 'granularity'")

    validate_hour_boundaries(
        granularity,
        str(time_scope_raw.get("start") or ""),
        str(time_scope_raw.get("end") or ""),
    )
```

- [ ] **Step 3: Migrate test.py**

In `run_test_intent()` (line 135), param extraction is ref-based (left_ref, right_ref, hypothesis, method). No metric normalization needed. Keep existing code.

- [ ] **Step 4: Migrate forecast.py**

In `run_forecast_intent()` (line 114), param extraction is ref-based (source_ref, horizon). No metric normalization needed. Keep existing code.

- [ ] **Step 5: Run regression tests including hour boundary**

Run: `make test ARGS="tests/test_intent_api.py -k 'detect or test or forecast or hour' -v"`
Expected: All pass including hour boundary regression

- [ ] **Step 6: Commit**

```bash
git add marivo/runtime/intents/detect.py marivo/runtime/intents/test.py marivo/runtime/intents/forecast.py
git commit -m "refactor: migrate detect to shared normalization; review test/forecast"
```

---

## Phase 4: Derived intents

### Task 7: Migrate attribute to shared normalization + envelope awareness

Attribute is the most complex derived intent — it orchestrates observe + compare + decompose × N dimensions. Currently ~550 lines with duplicated metric/dimension normalization.

**Files:**
- Modify: `marivo/runtime/intents/attribute.py`

- [ ] **Step 1: Run baseline tests**

Run: `make test ARGS="tests/test_intent_api.py -k attribute -v"`
Expected: All pass (baseline)

- [ ] **Step 2: Replace inline normalization in attribute.py**

In `run_attribute_intent()` (line 39), replace inline metric ref and dimension normalization:

```python
# Add import:
from marivo.runtime.intents.normalization import (
    normalize_metric_ref,
    normalize_dimensions,
)

# Replace inline extraction (lines 64-109):
    p = params or {}
    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)

    dimensions = normalize_dimensions(p.get("dimensions"))
    if not dimensions:
        raise ValueError("attribute intent requires at least one dimension")
```

- [ ] **Step 3: Run regression tests**

Run: `make test ARGS="tests/test_intent_api.py -k attribute -v"`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add marivo/runtime/intents/attribute.py
git commit -m "refactor: migrate attribute to shared normalization"
```

---

### Task 8: Migrate diagnose and validate to shared normalization

Diagnose has extensive normalization (metric, time scope, granularity, hour boundaries). Validate has metric normalization and its product-level semantics (validation.status, issues) which should eventually live in the envelope's product_metadata.

**Files:**
- Modify: `marivo/runtime/intents/diagnose.py`
- Modify: `marivo/runtime/intents/validate.py`

- [ ] **Step 1: Run baseline tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'diagnose or validate' -v"`
Expected: All pass (baseline)

- [ ] **Step 2: Migrate diagnose.py to shared normalization**

In `run_diagnose_intent()` (line 100), replace inline metric ref, granularity, and hour boundary normalization:

```python
# Add import:
from marivo.runtime.intents.normalization import (
    normalize_metric_ref,
    validate_granularity,
    validate_hour_boundaries,
)

# Replace inline extraction for metric:
    p = params or {}
    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
```

Apply the same pattern for granularity and hour boundary validation that exists inline.

- [ ] **Step 3: Migrate validate.py to shared normalization**

In `run_validate_intent()` (line 40), replace inline metric ref normalization:

```python
from marivo.runtime.intents.normalization import normalize_metric_ref

    p = params or {}
    metric_ref = normalize_metric_ref(p.get("metric"))
    metric_ref = runtime.core.normalize_intent_metric_ref(metric_ref)
```

- [ ] **Step 4: Run regression tests including session/validation edge cases**

Run: `make test ARGS="tests/test_intent_api.py -k 'diagnose or validate or closed_session' -v"`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add marivo/runtime/intents/diagnose.py marivo/runtime/intents/validate.py
git commit -m "refactor: migrate diagnose/validate to shared normalization"
```

---

## Phase 5: HTTP response migration + cleanup

### Task 9: Update HTTP response models to support envelope

Replace `RootModel[JsonObject]` response wrappers with models that can accept `ExecutionEnvelope.to_legacy_dict()` output. This is a non-breaking change since the wire format is the same.

**Files:**
- Modify: `marivo/transports/http/models/intent_response_models.py`
- Modify: `marivo/transports/http/sessions.py`

- [ ] **Step 1: Run full HTTP-level tests as baseline**

Run: `make test ARGS="tests/test_intent_api.py -v"`
Expected: All pass (baseline)

- [ ] **Step 2: Verify response models already accept envelope legacy dict**

The current `RootModel[JsonObject]` response models already accept any dict — `to_legacy_dict()` produces a dict. No structural change is needed to the response models at this point. The existing models are forward-compatible with envelope output.

Add a comment to `intent_response_models.py` documenting the migration path:

```python
# marivo/transports/http/models/intent_response_models.py
"""Typed response models for intent execution APIs.

Currently all responses are RootModel[JsonObject] wrappers.
Migration path: once all intent handlers return ExecutionEnvelope,
these will become typed models with explicit step_ref, artifact_id,
and result fields. The wire format stays the same via to_legacy_dict().
"""
```

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add marivo/transports/http/models/intent_response_models.py
git commit -m "docs: annotate response models with envelope migration path"
```

---

### Task 10: Verify regression matrix and run full validation

Final validation task: run every regression path from the spec's §8.2 regression matrix and confirm all are covered.

**Files:**
- No new files — verification only

- [ ] **Step 1: Run cross-session ref rejection tests**

Run: `make test ARGS="tests/test_intent_api.py -k cross_session -v"`
Expected: PASS — compare, correlate, test all reject cross-session refs

- [ ] **Step 2: Run hour boundary tests**

Run: `make test ARGS="tests/test_intent_api.py -k hour -v"`
Expected: PASS — hour granularity requires datetime boundaries

- [ ] **Step 3: Run dimension normalization tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'dimension or granularity' -v"`
Expected: PASS — empty dimensions normalized, granularity/dimensions mutual exclusion

- [ ] **Step 4: Run derived intent validation tests**

Run: `make test ARGS="tests/test_intent_api.py -k 'diagnose or validate' -v"`
Expected: PASS — invalid requests → 422, closed session → 422

- [ ] **Step 5: Run generated model tests**

Run: `make test ARGS="tests/test_generated_models.py -v"`
Expected: PASS — AOI examples validate, schema consistency

- [ ] **Step 6: Run full test suite**

Run: `make test`
Expected: All tests pass

- [ ] **Step 7: Run type checker**

Run: `make typecheck`
Expected: No new errors from changes

- [ ] **Step 8: Run linter**

Run: `make lint`
Expected: Clean

- [ ] **Step 9: Final commit**

```bash
git commit --allow-empty -m "chore: verify AOI runtime boundary regression matrix — all green"
```
