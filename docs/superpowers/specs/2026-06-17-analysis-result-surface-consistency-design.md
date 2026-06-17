# Analysis Result-Surface Consistency Design

- **Date:** 2026-06-17
- **Status:** Approved (pending spec review)
- **Builds on:** `docs/superpowers/specs/2026-06-13-agent-result-surface-design.md`
  (the shared `AgentResult` standard: bounded single-line `__repr__`, bounded
  `render()`/`show()`, snapshot-pinned `__all__`, introspection contract).

## Context

The `marivo.analysis` intent results (`*Frame` / `*Result` / `*Report` / `*Set`)
are the objects an agent stops to read in the write-run-read loop. The shared
agent-facing standard already exists and is reused across `semantic`,
`analysis`, and `datasource`:

- `marivo/render.py` — `AgentResult` protocol, `result_repr()`,
  `format_bounded_card()`.
- `marivo/introspection/surface.py` — the `Surface` help engine (analysis and
  semantic help both build on it).
- `tests/test_public_surface.py` — pins each surface's `__all__`.
- `tests/test_agent_result_protocol.py` — runs authoring, analysis, and
  datasource terminal types through one `assert_conforms`.
- `tests/test_introspection_contract.py` — every `__all__` symbol must resolve
  to a descriptor and render help text.

An audit of the analysis result surface found the foundation strong but four
individual result types not living up to the shared bar. This design closes all
four and adds a regression guard so the same class of drift fails loudly.

## Problem (the four gaps)

1. **`.summary()` partially breaks the result protocol.**
   `BaseFrame.summary() -> FrameSummary` conforms and is contract-tested.
   `QualityReport.summary() -> QualityReportSummary`
   ([marivo/analysis/frames/quality.py](../../../marivo/analysis/frames/quality.py))
   and `AssociationResult.summary() -> AssociationResultSummary`
   ([marivo/analysis/frames/association.py](../../../marivo/analysis/frames/association.py))
   return bare pydantic models with no `render()`/`show()`/bounded `__repr__`,
   and are only `isinstance`-tested. So `report.summary().show()` raises
   `AttributeError` and `repr(...)` is an unbounded multi-line dump.

2. **`_repr_identity()` richness is uneven across frames.**
   `MetricFrame`, `DeltaFrame`, `ForecastFrame`, `CoverageFrame` carry domain
   identity. `AttributionFrame`, `ComponentFrame`, `CandidateSet`,
   `HypothesisTestResult`, `QualityReport`, `AssociationResult`,
   `ExplorationResult` carry only `ref` + `rows`, discarding zero-cost
   identity already present on `meta`. `render()`'s first line uses
   `_repr_identity()`, so the `show()` card is affected too. `ComponentFrame`
   even has `parent_ref` on meta but does not surface it, while its sibling
   `CoverageFrame` does — an internal contradiction.

3. **`CoverageFrame` is reachable but undiscoverable.**
   Returned by `MetricFrame.coverage()`, conforms to the protocol, but is
   absent from `marivo.analysis.__all__`, `help._FRAME_SYMBOLS`,
   `help._SUMMARIES`, and `help._CONSTRUCTED_BY`. `mv.help('CoverageFrame')`
   fails. Its sibling `ComponentFrame` (via `.components()`) is fully exposed.
   Being outside `__all__`, it also escapes the introspection contract test.

4. **`FramePreview` is public but has no bounded repr/show.**
   Returned by `frame.preview(limit=n)`, pinned in `__all__`, listed in help,
   but it is a bare pydantic model — `repr()` can dump up to 100 rows, and
   there is no `show()`. Its sibling structured-return `FrameSummary` conforms.

## Goals / Non-goals

**Goals**

- Every analysis result type an agent stops to read conforms to `AgentResult`:
  bounded single-line `__repr__`, bounded `render()`, `show()`.
- Every frame's repr/show first line carries cheap, high-signal domain identity.
- Every linked/returned frame is discoverable via `__all__` + help.
- A regression guard makes "new result type forgot to conform / be exposed"
  fail in CI rather than ship.

**Non-goals**

- No change to data semantics, computation, lineage, or evidence behavior.
- No promotion of the specialized summary DTOs to top-level `__all__`
  (surface growth stays gated).
- No widening of `BaseFrame.summary()`'s return annotation (see Decisions).
- No new `Any`, broad `cast(...)`, or new `# type: ignore`.

## Design

### Gap 1 — `.summary()` protocol conformance

Make `QualityReportSummary` and `AssociationResultSummary` conform to
`AgentResult` by following the existing `FrameSummary` template
([marivo/analysis/frames/base.py](../../../marivo/analysis/frames/base.py),
`FrameSummary` at the `_repr_identity()`/`render()`/`__repr__()`/`show()`
methods). Each gains:

- `_repr_identity() -> str` carrying its distinguishing fields.
- `render() -> str` via `format_bounded_card(...)` (bounded, no trailing
  newline).
- `__repr__() -> str` via `result_repr(self._repr_identity())`.
- `show() -> None` that prints `render()`.

Identity lines:

- `QualityReportSummary ref=<ref> status=<overall_status> blocking=<blocking_issue_count>`
- `AssociationResultSummary ref=<ref> method=<method> r=<correlation:.2f>`

`render()` content (bounded card):

- QualityReportSummary: `status=` line
  `f"{overall_status}; blocking={blocking_issue_count} warning={warning_count}"`,
  `available=(".render()", ".show()")`.
- AssociationResultSummary: `status=` line
  `f"r={correlation:.2f} method={method} aligned={aligned_row_count} dropped={dropped_row_count}"`,
  `available=(".render()", ".show()")`.

**Tier decision:** both remain in `marivo.analysis.frames.__all__` (where they
already live) and are **not** promoted to top-level `marivo.analysis.__all__`.
The bounded `__repr__` already points an agent to `.show()`, which satisfies the
discoverability floor without growing the gated top-level surface.

### Gap 2 — `_repr_identity()` enrichment

Update `_repr_identity()` on the seven bare frames to surface identity already
on `meta`. Target formats (suffix `rows=<row_count>` retained):

| Frame | `_repr_identity()` |
|-------|--------------------|
| `AttributionFrame` | `AttributionFrame ref=<ref> attribution_kind=<attribution_kind> method=<method> rows=<n>` |
| `ComponentFrame` | `ComponentFrame ref=<ref> parent=<parent_ref> metric=<metric_id> rows=<n>` |
| `CandidateSet` | `CandidateSet ref=<ref> objective=<objective> strategy=<strategy> rows=<n>` |
| `HypothesisTestResult` | `HypothesisTestResult ref=<ref> hypothesis=<hypothesis> method=<method> rejected=<rejected_count> rows=<n>` |
| `QualityReport` | `QualityReport ref=<ref> status=<overall_status> blocking=<blocking_issue_count> rows=<n>` |
| `AssociationResult` | `AssociationResult ref=<ref> method=<method> r=<correlation:.2f> rows=<n>` |
| `ExplorationResult` | `ExplorationResult ref=<ref> source=<source_kind> rows=<n>` |

All values are existing required `meta` fields. The bounded-repr length limit
(≤200 chars, enforced by the contract test) is respected.

Tests asserting these reprs are updated in lockstep:
[tests/test_analysis_frames_summary.py](../../../tests/test_analysis_frames_summary.py),
[tests/test_analysis_assess_quality.py](../../../tests/test_analysis_assess_quality.py),
[tests/test_analysis_compare.py](../../../tests/test_analysis_compare.py).

### Gap 3 — expose `CoverageFrame`

Mirror how `ComponentFrame` is exposed (only the frame, not its `*Meta`):

- [marivo/analysis/__init__.py](../../../marivo/analysis/__init__.py): import
  `CoverageFrame`, add `"CoverageFrame"` to `__all__`.
- [marivo/analysis/help.py](../../../marivo/analysis/help.py): add
  `"CoverageFrame"` to `_FRAME_SYMBOLS`, a `_SUMMARIES["CoverageFrame"]` entry,
  and `_CONSTRUCTED_BY["CoverageFrame"] = "MetricFrame.coverage()"`.
- [tests/test_public_surface.py](../../../tests/test_public_surface.py): add
  `"CoverageFrame"` to `ANALYSIS_PUBLIC` (one deliberate snapshot edit).

`CoverageFrameMeta` stays internal (consistent with `ComponentFrameMeta` not
being top-level). After this, the introspection contract test automatically
covers `CoverageFrame` (resolves + renders + has a summary).

### Gap 4 — `FramePreview` conformance

Add to `FramePreview`
([marivo/analysis/frames/base.py](../../../marivo/analysis/frames/base.py)):

- `_repr_identity() -> str`:
  `FramePreview ref=<ref> kind=<kind> returned=<returned_row_count>/<row_count> truncated=<is_truncated>`
- `render() -> str` via `format_bounded_card(...)` using `columns` and a bounded
  projection of `rows` (cap honored by the formatter), with `row_count` for the
  truncation line and `available=(".rows (list[dict])", ".columns")`.
- `__repr__() -> str` via `result_repr(self._repr_identity())`.
- `show() -> None` that prints `render()`.

`FramePreview` stays in `__all__` (confirmed with the user).

### Regression guard (approach C)

Extend [tests/test_agent_result_protocol.py](../../../tests/test_agent_result_protocol.py):

1. **Frame discovery sweep** — collect every concrete `BaseFrame` subclass whose
   `__module__` starts with `marivo.analysis.frames` (excludes test doubles),
   and assert for each:
   - it defines its own `_repr_identity` (`cls._repr_identity is not
     BaseFrame._repr_identity`) — prevents a frame shipping with the bare base
     identity (root cause of Gap 2);
   - it is present in `marivo.analysis.__all__` — prevents an unexposed frame
     (root cause of Gap 3);
   - it is present in `marivo.analysis.help._FRAME_SYMBOLS` — prevents an
     undiscoverable frame (root cause of Gap 3).
2. **Explicit terminal builders** — add `FramePreview`, `QualityReportSummary`,
   and `AssociationResultSummary` to `TERMINAL_BUILDERS` so the existing
   `assert_conforms` runs against them (root cause of Gaps 1 and 4).

The sweep is class-level (no per-frame instance fixtures), keeping it light;
instance-level render/show bounds remain covered by the per-frame unit tests and
`tests/test_analysis_frames_base.py`.

## Decisions and rationale

- **FramePreview kept public** (not removed): it is the only path to bounded
  (≤100) structured rows as `list[dict]` without pandas — `.show()` is a
  human-readable card, `.to_pandas()` is unbounded and pandas-bound. Real,
  distinct agent affordance.
- **Specialized summaries stay in `mv.frames`**, not promoted to top-level: a
  conforming bounded repr already points to `.show()`; promotion would grow the
  gated surface for no added capability.
- **`# type: ignore[override]` on the two summary overrides stays.** Removing it
  would require widening `BaseFrame.summary()` to a protocol/supertype return,
  which weakens field access for the common `FrameSummary` case. Keeping precise
  per-subclass return types (with the locally-justified, pre-existing ignore) is
  the better API. This change is purely additive (new methods on the two DTOs)
  and does not touch the override typing. No new ignores are introduced.

## Affected files

Source:
- `marivo/analysis/frames/base.py` (FramePreview methods)
- `marivo/analysis/frames/quality.py` (QualityReportSummary methods + QualityReport `_repr_identity`)
- `marivo/analysis/frames/association.py` (AssociationResultSummary methods + AssociationResult `_repr_identity`)
- `marivo/analysis/frames/attribution.py`, `component.py`, `candidate.py`, `hypothesis.py`, `exploration.py` (`_repr_identity`)
- `marivo/analysis/__init__.py` (CoverageFrame export)
- `marivo/analysis/help.py` (CoverageFrame help registration)

Tests:
- `tests/test_agent_result_protocol.py` (sweep + builders)
- `tests/test_public_surface.py` (ANALYSIS_PUBLIC pin)
- `tests/test_analysis_frames_summary.py`, `tests/test_analysis_assess_quality.py`, `tests/test_analysis_compare.py` (repr assertions)

## Testing and verification

- TDD per gap: write the failing test first (red commits bypass only the pytest
  pre-commit hook via `SKIP=pytest`), then implement to green.
- Suggested order: Gap 1 → Gap 4 → Gap 2 → Gap 3 → regression guard.
- Close-out: `make test`, `make typecheck`, `make lint` all green.
- Commits follow the repository commit-attribution convention.

## Success criteria

- `report.summary().show()` and `assoc.summary().show()` work and print bounded
  cards; their `repr()` is single-line.
- Each of the seven enriched frames' `repr()`/`show()` first line carries its
  domain identity.
- `mv.help('CoverageFrame')` renders; `CoverageFrame` is in `__all__`.
- `repr(frame.preview())` is single-line and bounded; `frame.preview().show()`
  prints a bounded card.
- The regression sweep passes and would fail if any of the above regressed.
- `make test`, `make typecheck`, `make lint` pass.
