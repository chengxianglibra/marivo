# Metric Unit Measure-Dimension Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the optional `unit` field to measure dimensions and propagate it at load time (tier-1 from measure, derived via composition algebra), mirroring the existing additivity resolution, with a validator error only for incommensurable `linear` units.

**Architecture:** A new pure `unit_algebra` module is the single source of truth for unit combination. The loader fills `MetricIR.unit` after additivity resolution (author declaration always wins). The validator flags `linear` compositions whose terms carry ≥2 distinct known units. `unit` stays optional everywhere — it never affects computed values, only metadata.

**Tech Stack:** Python 3.12+ frozen dataclasses, ibis, pytest. Repo entrypoints: `.venv/bin/pytest`, `make typecheck`, `make test`.

**Spec:** `docs/superpowers/specs/2026-06-16-metric-unit-measure-propagation-design.md`

---

## File Structure

**New files:**
- `marivo/semantic/unit_algebra.py` — pure unit-combination functions (tier-1 transform, ratio/weighted_average/linear algebra, linear conflict detection). No registry/IR dependencies. Shared by loader, validator, prepare.
- `tests/test_metric_unit_algebra.py` — unit tests for the pure functions.
- `tests/test_metric_unit_propagation.py` — end-to-end authoring + loader resolution + validator tests (mirrors `tests/test_metric_split_resolution.py`).

**Modified files:**
- `marivo/semantic/ir.py` — `DimensionIR.unit` field + `__post_init__` measure-only guard.
- `marivo/semantic/authoring.py` — generalize `_validate_unit` label; `dimension(unit=)` kwarg + guard + wire; fix `aggregate()` to store `unit`; docstrings.
- `marivo/semantic/loader.py` — `_resolve_tier1_unit`, `_resolve_derived_unit`, `_resolve_metric_unit` + call site.
- `marivo/semantic/errors.py` — `INCOMMENSURABLE_LINEAR_UNITS` ErrorKind.
- `marivo/semantic/constraints.py` — `LINEAR_UNIT_COMMENSURABLE` ConstraintId + registration.
- `marivo/semantic/validator.py` — linear commensurability check in the composition loop.
- `marivo/semantic/richness.py` — count-specific gap kind + hints.
- `marivo/semantic/prepare.py` — compute `unit_hint` for ratio/weighted_average.
- `tests/test_semantic_imports.py` — add to `_EXPECTED_ASSEMBLY_KINDS`.
- `tests/test_semantic_catalog.py` — add `"unit"` to `allowed_internal[DimensionIR]`.
- `tests/test_semantic_richness.py` — count-specific gap test.
- `tests/test_semantic_prepare.py` — unit_hint preview tests.

---

## Task 1: Pure unit-combination algebra

**Files:**
- Create: `marivo/semantic/unit_algebra.py`
- Test: `tests/test_metric_unit_algebra.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_metric_unit_algebra.py`:

```python
"""Tests for the pure unit-combination algebra."""

from __future__ import annotations

from marivo.semantic.unit_algebra import (
    linear_unit,
    linear_units_conflict,
    ratio_unit,
    tier1_unit,
    weighted_average_unit,
)


def test_tier1_unit_preserves_for_value_aggs() -> None:
    for agg in ("sum", "min", "max", "mean", "median", "percentile"):
        assert tier1_unit(agg, "CNY") == "CNY"
        assert tier1_unit(agg, None) is None


def test_tier1_unit_counts_are_none() -> None:
    assert tier1_unit("count", "CNY") is None
    assert tier1_unit("count_distinct", "CNY") is None


def test_ratio_unit_same_known_cancels_to_one() -> None:
    assert ratio_unit("CNY", "CNY") == "1"
    assert ratio_unit("{order}", "{order}") == "1"


def test_ratio_unit_differing_or_unknown_is_none() -> None:
    assert ratio_unit("CNY", "{user}") is None
    assert ratio_unit("CNY", None) is None
    assert ratio_unit(None, None) is None


def test_weighted_average_unit_keeps_value_unit() -> None:
    assert weighted_average_unit("CNY") == "CNY"
    assert weighted_average_unit(None) is None


def test_linear_unit_all_same_known() -> None:
    assert linear_unit(["CNY", "CNY"]) == "CNY"


def test_linear_unit_any_none_is_none() -> None:
    assert linear_unit(["CNY", None]) is None
    assert linear_unit([None, None]) is None


def test_linear_unit_distinct_known_is_none() -> None:
    assert linear_unit(["CNY", "{order}"]) is None


def test_linear_units_conflict_only_on_two_distinct_known() -> None:
    assert linear_units_conflict(["CNY", "{order}"]) is True
    assert linear_units_conflict(["CNY", "CNY"]) is False
    assert linear_units_conflict(["CNY", None]) is False
    assert linear_units_conflict([None, None]) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metric_unit_algebra.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'marivo.semantic.unit_algebra'`

- [ ] **Step 3: Write the module**

Create `marivo/semantic/unit_algebra.py`:

```python
"""Pure unit-combination algebra for derived and tier-1 metric units.

Single source of truth shared by the loader unit resolver, the validator
linear-commensurability check, and the authoring brief unit preview. These
functions operate on unit strings only — no registry or IR dependencies —
so they are trivially testable and reusable.

Conventions (see docs/specs/semantic/python-semantic-layer.md):
- ``None`` means "no unit declared/derivable" (honest absence, never a guess).
- ``"1"`` is the UCUM dimensionless unit (a ratio of equal units cancels to it).
"""

from __future__ import annotations

from collections.abc import Sequence


def tier1_unit(agg_name: str, measure_unit: str | None) -> str | None:
    """Unit of a tier-1 metric: preserve the measure unit, except counts.

    ``sum``/``min``/``max``/``mean``/``median``/``percentile`` of values in unit
    U are still in unit U. ``count``/``count_distinct`` change dimension to a
    counted noun (e.g. ``{order}``), which is content-specific and not inferred
    here, so they yield ``None``.
    """
    if agg_name in ("count", "count_distinct"):
        return None
    return measure_unit


def ratio_unit(numerator_unit: str | None, denominator_unit: str | None) -> str | None:
    """Same known unit cancels to dimensionless ``"1"``; otherwise ``None``.

    Differing known units would form a compound (e.g. ``CNY/{user}``);
    constructing compound units is a non-goal, so they resolve to ``None`` and
    the author may declare one explicitly.
    """
    if numerator_unit is not None and numerator_unit == denominator_unit:
        return "1"
    return None


def weighted_average_unit(value_unit: str | None) -> str | None:
    """Weighting preserves the value's unit; the weight's unit is irrelevant."""
    return value_unit


def linear_unit(term_units: Sequence[str | None]) -> str | None:
    """Resolved unit for a linear composition, or ``None`` when unknown/conflicting.

    Any ``None`` term yields ``None`` (honest absence, not a conflict). All terms
    sharing one known unit yield that unit. ≥2 distinct known units yield ``None``
    here — the conflict is reported by the validator, not resolved.
    """
    units = list(term_units)
    if any(u is None for u in units):
        return None
    distinct = set(units)
    return next(iter(distinct)) if len(distinct) == 1 else None


def linear_units_conflict(term_units: Sequence[str | None]) -> bool:
    """True iff ≥2 distinct *known* units (incommensurable addition)."""
    known = {u for u in term_units if u is not None}
    return len(known) >= 2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_metric_unit_algebra.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Typecheck**

Run: `make typecheck`
Expected: no errors in `marivo/semantic/unit_algebra.py`

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic/unit_algebra.py tests/test_metric_unit_algebra.py
git commit -m "feat(semantic): add pure unit-combination algebra module"
```

---

## Task 2: `DimensionIR.unit` field + measure-only guard + catalog allowlist

**Files:**
- Modify: `marivo/semantic/ir.py:309-324` (DimensionIR fields + `__post_init__`)
- Modify: `tests/test_semantic_catalog.py:1483` (allowed_internal[DimensionIR])
- Test: `tests/test_metric_unit_propagation.py`

> The catalog coverage test (`tests/test_semantic_catalog.py:1487`) iterates **every** `DimensionIR` field and requires it to appear in details or `allowed_internal`. Adding `unit` breaks it until the allowlist is updated — both changes ship in this task. `additivity` is already in that allowlist for exactly this reason.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metric_unit_propagation.py`:

```python
"""End-to-end tests for unit on measure dimensions and loader propagation."""

from __future__ import annotations

import dataclasses

import pytest

from marivo.semantic.ir import (
    AiContextIR,
    DimensionIR,
    DimensionKind,
    SourceLocation,
)


def test_dimension_ir_has_unit_field() -> None:
    names = {f.name for f in dataclasses.fields(DimensionIR)}
    assert "unit" in names


def test_dimension_ir_unit_only_on_measure() -> None:
    loc = SourceLocation(file="t.py", line=1)
    base = dict(
        semantic_id="sales.orders.amount",
        domain="sales",
        entity="sales.orders",
        name="amount",
        description=None,
        ai_context=AiContextIR(),
        is_time_dimension=False,
        data_type=None,
        granularity=None,
        required_prefix=None,
        python_symbol="amount",
        location=loc,
    )
    # measure + unit is allowed
    measure = DimensionIR(kind=DimensionKind.MEASURE, unit="CNY", **base)
    assert measure.unit == "CNY"
    # categorical + unit is rejected
    with pytest.raises(ValueError, match="unit is only valid on measure"):
        DimensionIR(kind=DimensionKind.CATEGORICAL, unit="CNY", **base)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'unit'`

- [ ] **Step 3: Add the field and guard**

In `marivo/semantic/ir.py`, add `unit` to `DimensionIR` after the `additivity` field (currently line 313):

```python
    additivity: Additivity | None = None
    unit: str | None = None
```

Then extend `DimensionIR.__post_init__` (after the existing additivity guard) with:

```python
        if self.unit is not None and self.kind is not DimensionKind.MEASURE:
            raise ValueError(
                f"DimensionIR {self.semantic_id!r}: unit is only valid on measure dimensions"
            )
```

- [ ] **Step 4: Run test to verify the field/guard pass, then confirm the catalog test now fails**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q`
Expected: PASS (2 passed)

Run: `.venv/bin/pytest tests/test_semantic_catalog.py -q -k coverage`
Expected: FAIL — `DimensionIR fields missing from catalog details: ['unit']`

- [ ] **Step 5: Add `unit` to the DimensionIR internal allowlist**

In `tests/test_semantic_catalog.py`, in the `allowed_internal[DimensionIR]` set (currently ends with `"additivity"` at line 1483), add `"unit"`:

```python
        DimensionIR: {
            "location",
            "ai_context",
            "is_time_dimension",
            "kind",
            "python_symbol",
            "semantic_id",
            "additivity",
            "unit",
        },
```

- [ ] **Step 6: Run catalog + typecheck + broad semantic tests**

Run: `.venv/bin/pytest tests/test_semantic_catalog.py -q`
Expected: PASS

Run: `make typecheck`
Expected: no errors

Run: `.venv/bin/pytest tests/test_semantic_assembly.py tests/test_metric_split_resolution.py -q`
Expected: PASS (confirms the new defaulted field breaks no existing DimensionIR construction)

- [ ] **Step 7: Commit**

```bash
git add marivo/semantic/ir.py tests/test_semantic_catalog.py tests/test_metric_unit_propagation.py
git commit -m "feat(semantic): add optional unit to DimensionIR (measure-only)"
```

---

## Task 3: Authoring — generalize `_validate_unit`, add `dimension(unit=)`, fix `aggregate()` wiring

**Files:**
- Modify: `marivo/semantic/authoring.py:182-193` (`_validate_unit`)
- Modify: `marivo/semantic/authoring.py:649-749` (`dimension`)
- Modify: `marivo/semantic/authoring.py:419-471` (`aggregate`)
- Test: `tests/test_metric_unit_propagation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metric_unit_propagation.py`:

> Test imports for `load_inline_semantic` / `authoring_session` / `ms` are **function-local** in every appended test (matching the repo's `test_semantic_richness.py` style). This keeps each task's commit lint-clean (no `E402` mid-file imports, no `F401` cross-task coupling). Module-level `_INLINE_*` string constants are fine to append.

```python
_DIM_UNIT = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue", unit="USD")
"""


def test_dimension_unit_stored_on_ir() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_DIM_UNIT) as result:
        assert result.registry.fields["test.orders.amount"].unit == "CNY"


def test_aggregate_unit_override_lands_on_metric_ir() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_DIM_UNIT) as result:
        # author override wins over the measure-derived value
        assert result.registry.metrics["test.revenue"].unit == "USD"


def test_dimension_unit_on_categorical_is_rejected() -> None:
    # The measure-only guard fires at the ms.dimension(...) factory call, before
    # entity resolution, so it needs an active loader context (authoring_session)
    # but no real entity. Mirrors the decorator-guard tests in
    # tests/test_metric_split_foundation.py.
    import marivo.semantic as ms
    from marivo.semantic.errors import SemanticDecoratorError
    from tests.shared_fixtures import authoring_session

    with authoring_session(domain="sales"):
        with pytest.raises(
            SemanticDecoratorError, match="unit is only valid on kind='measure'"
        ):
            ms.dimension(entity="sales.orders", unit="CNY")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q -k "dimension_unit or aggregate_unit"`
Expected: FAIL — `dimension() got an unexpected keyword argument 'unit'`

- [ ] **Step 3: Generalize `_validate_unit`**

Replace `marivo/semantic/authoring.py:182-193` with:

```python
def _validate_unit(unit: str | None, semantic_id: str, object_kind: str = "metric") -> None:
    if unit is None:
        return
    if unit == "" or any(not (0x21 <= ord(ch) <= 0x7E) for ch in unit):
        _raise(
            ErrorKind.INVALID_REF,
            f"{object_kind} {semantic_id!r}: unit must be a non-empty token of printable "
            f"ASCII without whitespace (UCUM case-sensitive code such as 'CNY', "
            f"'%', '1', 'ms', '{{order}}'); got {unit!r}.",
            refs=(semantic_id,),
            cls=SemanticDecoratorError,
        )
```

- [ ] **Step 4: Add `unit` to `dimension()`**

In `marivo/semantic/authoring.py`, add the `unit` parameter to `dimension()` (after `additivity`, line 657):

```python
    additivity: Additivity | None = None,
    unit: str | None = None,
```

Add the measure-only guard next to the existing additivity guard (after line 703):

```python
    if unit is not None and kind != "measure":
        _raise(
            ErrorKind.INVALID_REF,
            "unit is only valid on kind='measure' dimensions.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.REF_SHAPE,
        )
```

Inside the `decorator(fn)` body, after `_check_duplicate(ctx, semantic_id, DimensionIR)` (line 719), add:

```python
        _validate_unit(unit, semantic_id, "measure dimension")
```

Pass `unit=unit` into the `DimensionIR(...)` construction (alongside `additivity=...`, line 739):

```python
            unit=unit,
```

- [ ] **Step 5: Fix `aggregate()` to store the unit override**

In `marivo/semantic/authoring.py`, in the `aggregate()` `MetricIR(...)` construction (lines 451-469), add `unit=unit,` (the kwarg and `_validate_unit` call already exist; only the wiring is missing):

```python
            root_entity=entity_id,
            fold_override=fold_ir,
            unit=unit,
        )
```

- [ ] **Step 6: Run tests + typecheck**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q`
Expected: PASS

Run: `make typecheck`
Expected: no errors

- [ ] **Step 7: Update docstrings**

In `aggregate()` docstring (around line 430), add an Args note:

```
        unit: Override the unit derived from ``measure`` at load. Leave None to
            inherit the measure's unit (count/count_distinct derive nothing).
```

In `ratio()`, `weighted_average()`, `linear()` docstrings, change the `unit` description to: "Override the unit derived from the components at load." In `simple_metric()`, note: "tier-2 metrics have no measure to derive from; declare unit directly." In `dimension()` Args, add: "unit: UCUM unit token for a measure dimension (the authoritative declaration site)."

- [ ] **Step 8: Commit**

```bash
git add marivo/semantic/authoring.py tests/test_metric_unit_propagation.py
git commit -m "feat(semantic): author unit on measure dimensions; store aggregate override"
```

---

## Task 4: Loader — tier-1 + derived unit resolution

**Files:**
- Modify: `marivo/semantic/loader.py` (add three functions near `_resolve_metric_additivity`, line 368; add call at line 461)
- Test: `tests/test_metric_unit_propagation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metric_unit_propagation.py`:

```python
_INLINE_UNITS = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="non_additive")
def latency(orders): return orders.latency_ms

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
avg_latency = ms.aggregate(measure=latency, agg="mean", name="avg_latency")
order_count = ms.aggregate(measure=amount, agg="count", name="order_count")
margin = ms.ratio(name="margin", numerator=revenue, denominator=revenue)
arpu = ms.ratio(name="arpu", numerator=revenue, denominator=order_count)
net = ms.linear(name="net", add=[revenue, revenue])
"""


def test_tier1_unit_preserves_measure_unit() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNITS) as result:
        reg = result.registry
        assert reg.metrics["test.revenue"].unit == "CNY"  # sum preserves
        assert reg.metrics["test.avg_latency"].unit is None  # measure unannotated
        assert reg.metrics["test.order_count"].unit is None  # count -> no noun


def test_derived_unit_algebra() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNITS) as result:
        reg = result.registry
        assert reg.metrics["test.margin"].unit == "1"  # CNY / CNY cancels
        assert reg.metrics["test.arpu"].unit is None  # CNY / None -> no compound
        assert reg.metrics["test.net"].unit == "CNY"  # CNY + CNY


_INLINE_UNIT_OVERRIDE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
share = ms.ratio(name="share", numerator=revenue, denominator=revenue, unit="%")
"""


def test_author_override_wins_over_derivation() -> None:
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_UNIT_OVERRIDE) as result:
        # would derive "1", but the author declared "%"
        assert result.registry.metrics["test.share"].unit == "%"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q -k "tier1_unit_preserves or derived_unit or override_wins"`
Expected: FAIL — `revenue` unit is `None` (no resolution yet)

- [ ] **Step 3: Add the resolver functions**

In `marivo/semantic/loader.py`, after `_resolve_metric_additivity` (ends line 388), add:

```python
def _resolve_tier1_unit(metric: MetricIR, registry: Registry) -> str | None:
    from marivo.semantic.unit_algebra import tier1_unit

    measure = registry.fields.get(metric.measure or "")
    if measure is None:
        return None  # validator: UNKNOWN_MEASURE (existing rule)
    agg = metric.aggregation
    agg_name = agg[0] if isinstance(agg, tuple) else agg
    return tier1_unit(agg_name or "", getattr(measure, "unit", None))


def _resolve_derived_unit(metric: MetricIR, registry: Registry) -> str | None:
    from marivo.semantic.ir import (
        LinearComposition,
        RatioComposition,
        WeightedAverageComposition,
    )
    from marivo.semantic.unit_algebra import (
        linear_unit,
        ratio_unit,
        weighted_average_unit,
    )

    comp = metric.composition
    if isinstance(comp, RatioComposition):
        num = registry.metrics.get(comp.numerator)
        den = registry.metrics.get(comp.denominator)
        if num is None or den is None:
            return None
        return ratio_unit(num.unit, den.unit)
    if isinstance(comp, WeightedAverageComposition):
        value = registry.metrics.get(comp.value)
        return weighted_average_unit(value.unit) if value is not None else None
    assert isinstance(comp, LinearComposition)
    units: list[str | None] = []
    for term in comp.terms:
        dep = registry.metrics.get(term.metric)
        if dep is None:
            return None
        units.append(dep.unit)
    return linear_unit(units)


def _resolve_metric_unit(registry: Registry) -> None:
    import dataclasses

    # Phase A: tier-1 simple metrics resolve from their measure dimension.
    for sid, m in list(registry.metrics.items()):
        if m.metric_type == "simple" and m.aggregation is not None and m.unit is None:
            resolved = _resolve_tier1_unit(m, registry)
            if resolved is not None:
                registry.metrics[sid] = dataclasses.replace(m, unit=resolved)

    # Phase B: derived metrics propagate from components (fixpoint over chains).
    for _ in range(len(registry.metrics) + 1):
        changed = False
        for sid, m in list(registry.metrics.items()):
            if m.metric_type == "derived" and m.unit is None:
                resolved = _resolve_derived_unit(m, registry)
                if resolved is not None:
                    registry.metrics[sid] = dataclasses.replace(m, unit=resolved)
                    changed = True
        if not changed:
            break
```

- [ ] **Step 4: Wire the call**

In `marivo/semantic/loader.py`, after `_resolve_metric_additivity(registry)` (line 461), add:

```python
    _resolve_metric_additivity(registry)
    _resolve_metric_unit(registry)
    return registry, sidecar
```

- [ ] **Step 5: Run tests + typecheck**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q`
Expected: PASS

Run: `make typecheck`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic/loader.py tests/test_metric_unit_propagation.py
git commit -m "feat(semantic): propagate metric unit from measure and composition at load"
```

---

## Task 5: Error taxonomy — `INCOMMENSURABLE_LINEAR_UNITS` + `LINEAR_UNIT_COMMENSURABLE`

**Files:**
- Modify: `marivo/semantic/errors.py:80` (ErrorKind)
- Modify: `marivo/semantic/constraints.py:63` (ConstraintId) and `:453` (registration)
- Modify: `tests/test_semantic_imports.py:476` (`_EXPECTED_ASSEMBLY_KINDS`)
- Test: `tests/test_metric_unit_propagation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_metric_unit_propagation.py`:

```python
def test_linear_unit_error_taxonomy_registered() -> None:
    from marivo.semantic.constraints import ConstraintId, get_constraint
    from marivo.semantic.errors import ErrorKind

    assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS.value == "incommensurable_linear_units"
    assert ConstraintId.LINEAR_UNIT_COMMENSURABLE.value == "linear_unit_commensurable"
    assert get_constraint(ConstraintId.LINEAR_UNIT_COMMENSURABLE) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q -k taxonomy`
Expected: FAIL — `AttributeError: INCOMMENSURABLE_LINEAR_UNITS`

- [ ] **Step 3: Add the ErrorKind**

In `marivo/semantic/errors.py`, in the assembly-time block, after `INVALID_MEASURE_AGGREGATION` (line 80):

```python
    INVALID_MEASURE_AGGREGATION = "invalid_measure_aggregation"
    INCOMMENSURABLE_LINEAR_UNITS = "incommensurable_linear_units"
```

- [ ] **Step 4: Add the ConstraintId and registration**

In `marivo/semantic/constraints.py`, after `MEASURE_AGGREGATION_VALID` (line 63):

```python
    MEASURE_AGGREGATION_VALID = "measure_aggregation_valid"
    LINEAR_UNIT_COMMENSURABLE = "linear_unit_commensurable"
```

In the constraint registry, after the `MEASURE_AGGREGATION_VALID` entry (ends line 461):

```python
    ConstraintId.LINEAR_UNIT_COMMENSURABLE: _constraint(
        ConstraintId.LINEAR_UNIT_COMMENSURABLE,
        "incommensurable_linear_units",
        "assembly",
        ("metric",),
        "Linear metric terms must share one unit; differing units cannot be added.",
        "Addition is only defined on commensurable quantities (CNY + {order} is undefined).",
        "Align the component units, or remodel as a ratio/derived composition.",
    ),
```

- [ ] **Step 5: Add the value to the pinned assembly-kinds snapshot**

In `tests/test_semantic_imports.py`, in `_EXPECTED_ASSEMBLY_KINDS` (set starting line 476, containing `"invalid_measure_aggregation"` at line 508), add `"incommensurable_linear_units"`.

- [ ] **Step 6: Run the taxonomy + snapshot tests**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py::test_linear_unit_error_taxonomy_registered tests/test_semantic_imports.py -q`
Expected: PASS (covers `test_error_kind_all_covered`, `test_constraint_ids_all_registered`, `test_error_kind_assembly_kinds`)

- [ ] **Step 7: Commit**

```bash
git add marivo/semantic/errors.py marivo/semantic/constraints.py tests/test_semantic_imports.py tests/test_metric_unit_propagation.py
git commit -m "feat(semantic): register linear-unit incommensurability error taxonomy"
```

---

## Task 6: Validator — linear unit commensurability check

**Files:**
- Modify: `marivo/semantic/validator.py:30-38` (import `LinearComposition`) and `:1008-1033` (composition loop)
- Test: `tests/test_metric_unit_propagation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metric_unit_propagation.py`:

```python
_INLINE_LINEAR_CONFLICT = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="{order}")
def lines(orders): return orders.line_count

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
line_total = ms.aggregate(measure=lines, agg="sum", name="line_total")
bad = ms.linear(name="bad", add=[revenue, line_total])
"""


def test_linear_incommensurable_units_rejected() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_CONFLICT) as result:
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS in {e.kind for e in result.errors}


_INLINE_LINEAR_OK = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

gross = ms.aggregate(measure=amount, agg="sum", name="gross")
refunds = ms.aggregate(measure=amount, agg="sum", name="refunds")
net = ms.linear(name="net", add=[gross], subtract=[refunds])
"""


def test_linear_same_unit_no_error() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_OK) as result:
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS not in {e.kind for e in result.errors}


_INLINE_LINEAR_OVERRIDE = """\
import marivo.semantic as ms
import marivo.datasource as md

wh = md.ref("wh")
orders = ms.entity(name="orders", datasource=wh, source=ms.table("orders"))

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="CNY")
def amount(orders): return orders.amount

@ms.dimension(kind="measure", entity=orders, additivity="additive", unit="{order}")
def lines(orders): return orders.line_count

revenue = ms.aggregate(measure=amount, agg="sum", name="revenue")
line_total = ms.aggregate(measure=lines, agg="sum", name="line_total")
bad = ms.linear(name="bad", add=[revenue, line_total], unit="CNY")
"""


def test_linear_override_does_not_suppress_conflict() -> None:
    from marivo.semantic.errors import ErrorKind
    from tests.shared_fixtures import load_inline_semantic

    with load_inline_semantic(_INLINE_LINEAR_OVERRIDE) as result:
        # author labelled the result CNY, but CNY + {order} is still invalid
        assert ErrorKind.INCOMMENSURABLE_LINEAR_UNITS in {e.kind for e in result.errors}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q -k "incommensurable or same_unit_no_error or override_does_not_suppress"`
Expected: FAIL — `test_linear_incommensurable_units_rejected` and the override test fail (no such error raised yet)

- [ ] **Step 3: Import `LinearComposition` in the validator**

In `marivo/semantic/validator.py`, add `LinearComposition` to the `from marivo.semantic.ir import (...)` block (alphabetical, before `MetricIR`):

```python
    DimensionIR,
    DomainIR,
    EntityIR,
    LinearComposition,
    MetricIR,
```

- [ ] **Step 4: Add the commensurability check in the composition loop**

In `marivo/semantic/validator.py`, inside the `for m_id, m_ir in registry.metrics.items()` composition loop (line 1009), after the inner `for comp_key, comp_ref ...` block ends (after line 1033), still inside the outer `for`, add:

```python
        if isinstance(m_ir.composition, LinearComposition):
            from marivo.semantic.unit_algebra import linear_units_conflict

            term_units = [
                registry.metrics[t.metric].unit
                for t in m_ir.composition.terms
                if t.metric in registry.metrics
            ]
            if linear_units_conflict(term_units):
                errors.append(
                    SemanticLoadError(
                        kind=ErrorKind.INCOMMENSURABLE_LINEAR_UNITS,
                        message=(
                            f"Metric {m_id!r} adds incommensurable units "
                            f"{sorted(u for u in term_units if u is not None)!r}; "
                            "linear terms must share one unit."
                        ),
                        refs=(m_id,),
                        constraint_id=ConstraintId.LINEAR_UNIT_COMMENSURABLE,
                        details={
                            "metric": m_id,
                            "units": {
                                t.metric: registry.metrics[t.metric].unit
                                for t in m_ir.composition.terms
                                if t.metric in registry.metrics
                            },
                        },
                    )
                )
```

- [ ] **Step 5: Run tests + typecheck**

Run: `.venv/bin/pytest tests/test_metric_unit_propagation.py -q`
Expected: PASS

Run: `make typecheck`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic/validator.py tests/test_metric_unit_propagation.py
git commit -m "feat(semantic): reject incommensurable linear metric units at load"
```

---

## Task 7: Richness — count-specific gap kind + hints

**Files:**
- Modify: `marivo/semantic/richness.py:105-108` (`_detect_depth`) and `:219-227` (`_SUGGESTED_ACTION`)
- Test: `tests/test_semantic_richness.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_semantic_richness.py`:

```python
def test_detect_depth_count_metric_gets_count_hint(semantic_project_factory):
    from marivo.semantic.richness import _SUGGESTED_ACTION, _detect_depth

    files = {
        "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales')\n",
        "sales/objects.py": (
            "import marivo.semantic as ms\n"
            "orders = ms.entity(name='orders', datasource='warehouse', "
            "source=ms.table('orders'))\n"
            "@ms.dimension(kind='measure', entity=orders, additivity='additive')\n"
            "def amount(orders):\n"
            "    return orders.amount\n"
            "order_count = ms.aggregate(measure=amount, agg='count', name='order_count')\n"
        ),
    }
    project = semantic_project_factory(files)
    gaps = {(kind, refs[0]) for kind, refs in _detect_depth(project._registry)}
    assert ("missing_unit_count", "sales.order_count") in gaps
    assert ("missing_unit", "sales.order_count") not in gaps
    assert "missing_unit_count" in _SUGGESTED_ACTION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_semantic_richness.py::test_detect_depth_count_metric_gets_count_hint -q`
Expected: FAIL — `("missing_unit_count", "sales.order_count")` not in gaps

- [ ] **Step 3: Branch the gap kind in `_detect_depth`**

In `marivo/semantic/richness.py`, replace the metric loop (lines 105-107):

```python
    for metric in reg.metrics.values():
        if metric.unit is None:
            agg = metric.aggregation
            agg_name = agg[0] if isinstance(agg, tuple) else agg
            if agg_name in ("count", "count_distinct"):
                gaps.append(("missing_unit_count", (metric.semantic_id,)))
            else:
                gaps.append(("missing_unit", (metric.semantic_id,)))
```

- [ ] **Step 4: Add the count-specific hint and refine the generic one**

In `marivo/semantic/richness.py`, update `_SUGGESTED_ACTION` (the `missing_unit` entry at line 226):

```python
    "missing_unit": (
        "Add unit (UCUM case-sensitive code, e.g. 'CNY', '%', '{order}') so analysis "
        "payloads and displays carry it. For a tier-1 metric, declare unit= on its "
        "measure dimension so every aggregation over it inherits the unit."
    ),
    "missing_unit_count": (
        "Count metric: declare a counted-noun annotation like '{order}' (UCUM "
        "curly-brace count unit); the entity name is not singularized automatically."
    ),
```

- [ ] **Step 5: Run the richness tests**

Run: `.venv/bin/pytest tests/test_semantic_richness.py -q`
Expected: PASS (existing `test_detect_depth_flags_missing_unit` still passes — its metrics are tier-2 `simple_metric`, untouched by count branching)

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic/richness.py tests/test_semantic_richness.py
git commit -m "feat(semantic): count-specific missing-unit richness hint"
```

---

## Task 8: Prepare — `unit_hint` preview via shared algebra

**Files:**
- Modify: `marivo/semantic/prepare.py:255-265` (`prepare_derived_metric` return)
- Test: `tests/test_semantic_prepare.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_semantic_prepare.py`:

```python
def test_prepare_derived_metric_ratio_unit_hint_is_one(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.simple_metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def revenue(t):\n"
        "    return t.amount.sum()\n"
        "@ms.simple_metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def cost(t):\n"
        "    return t.cost.sum()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.revenue", denominator="sales.cost")

    assert brief.unit_hint == "1"


def test_prepare_derived_metric_weighted_average_unit_hint_keeps_value(
    semantic_project_factory,
) -> None:
    model = (
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales')\n"
        "orders = ms.entity(name='orders', datasource='warehouse', source=ms.table('orders'))\n"
        "@ms.simple_metric(entities=[orders], additivity='additive', unit='CNY')\n"
        "def price(t):\n"
        "    return t.price.mean()\n"
        "@ms.simple_metric(entities=[orders], additivity='additive')\n"
        "def qty(t):\n"
        "    return t.qty.sum()\n"
    )
    project = semantic_project_factory({"sales/_domain.py": model})
    project.load()

    brief = project.prepare_derived_metric(numerator="sales.price", weight="sales.qty")

    assert brief.unit_hint == "CNY"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_semantic_prepare.py -q -k unit_hint`
Expected: FAIL — `brief.unit_hint` is `None`

- [ ] **Step 3: Compute `unit_hint` from the shared algebra**

In `marivo/semantic/prepare.py`, add a helper above `prepare_derived_metric` (before line 181):

```python
def _preview_unit_hint(
    reg: object,
    composition_kind: str,
    numerator: str | None,
    denominator: str | None,
    weight: str | None,
    missing: tuple[str, ...],
) -> str | None:
    """Preview the unit the loader will derive, for the brief's representable shapes.

    Only ratio and weighted_average are previewable — the brief API has no linear
    term list, so linear previews as None (the loader still derives it at load).
    """
    from marivo.semantic.unit_algebra import ratio_unit, weighted_average_unit

    if reg is None or missing:
        return None
    metrics = reg.metrics  # type: ignore[attr-defined]
    if composition_kind == "ratio" and numerator is not None and denominator is not None:
        num = metrics.get(numerator)
        den = metrics.get(denominator)
        if num is None or den is None:
            return None
        return ratio_unit(num.unit, den.unit)
    if composition_kind == "weighted_average" and numerator is not None:
        value = metrics.get(numerator)
        return weighted_average_unit(value.unit) if value is not None else None
    return None
```

Then in `prepare_derived_metric`, replace `unit_hint=None,` (line 260) with a computed value. Just before the `return DerivedMetricBrief(...)` (line 255), add:

```python
    unit_hint = _preview_unit_hint(
        reg, composition_kind, numerator, denominator, weight, missing
    )
```

And change the return field to `unit_hint=unit_hint,`.

- [ ] **Step 4: Run tests + typecheck**

Run: `.venv/bin/pytest tests/test_semantic_prepare.py -q`
Expected: PASS

Run: `make typecheck`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add marivo/semantic/prepare.py tests/test_semantic_prepare.py
git commit -m "feat(semantic): preview derived metric unit_hint via shared algebra"
```

---

## Task 9: Full suite + docs

**Files:**
- Modify: `docs/specs/semantic/python-semantic-layer.md` (dimension unit field; metric derive+override; agg→unit table; derived algebra)
- Modify: `marivo/skills/marivo-semantic/references/authoring-patterns.md` (declare on measure, override on metric, count declares `{order}`)
- Modify: semantic help data for `dimension` / `aggregate` / `ratio` / `weighted_average` / `linear` unit kwargs

- [ ] **Step 1: Run the full semantic suite**

Run: `make test`
Expected: PASS (all green). If anything outside this feature breaks, investigate before proceeding.

- [ ] **Step 2: Update the spec doc**

In `docs/specs/semantic/python-semantic-layer.md`: add a `unit` row to the dimension declaration field table; update the "Metric unit (UCUM)" section (line 482) to state tier-1 derives from the measure and derived from composition, with author `unit=` as override; add the agg→unit transform table and the ratio/weighted_average/linear algebra (copy from the spec §2). Reference: incommensurable linear units raise `INCOMMENSURABLE_LINEAR_UNITS`.

- [ ] **Step 3: Update the authoring-patterns reference**

In `marivo/skills/marivo-semantic/references/authoring-patterns.md`: change the unit fill strategy to "declare unit on the measure dimension (authoritative); tier-1/derived metrics inherit it; pass unit= on a metric only to override; count/count_distinct declare an explicit `{noun}` annotation."

- [ ] **Step 4: Update semantic help data**

Locate the help entries for `dimension`, `aggregate`, `ratio`, `weighted_average`, `linear` (run `grep -rn "ms.help" marivo/semantic/help.py` to find the data source) and add/adjust the `unit` kwarg descriptions to match the new derive+override semantics.

Run: `.venv/bin/pytest tests/test_semantic_help.py -q` (if present) and `make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/specs/semantic/python-semantic-layer.md marivo/skills/marivo-semantic/references/authoring-patterns.md marivo/semantic/help.py
git commit -m "docs(semantic): document unit derivation on measures and metrics"
```

---

## Self-Review

- **Spec coverage:** §1 data model → Tasks 2, 3. §2 transform/algebra → Task 1 (+ used in 4, 6). §3 loader → Task 4. §4 validator taxonomy → Tasks 5, 6. §5 authoring → Task 3. §6 consumption (catalog allowlist, richness, unit_hint) → Tasks 2, 7, 8; `mv.help`/`MetricDetails`/evidence/frames are zero-code (covered by `make test` in Task 9). §7 free wins → verified by Task 9 full suite. Test strategy items 1–9 → mapped across Tasks 1–8.
- **Type consistency:** `tier1_unit`, `ratio_unit`, `weighted_average_unit`, `linear_unit`, `linear_units_conflict` are defined in Task 1 and called with matching signatures in Tasks 4, 6, 8. `_validate_unit(unit, semantic_id, object_kind="metric")` defined and called consistently in Task 3. `_resolve_metric_unit` mirrors `_resolve_metric_additivity`.
- **No placeholders:** every code step shows complete code; every command states expected output.
```
