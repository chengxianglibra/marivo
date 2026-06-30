# Frame/Result Interface Simplification Design

- **Date:** 2026-06-28
- **Status:** Approved for spec drafting, pending written-spec review
- **Scope:** `marivo.analysis` frame/result agent-facing interfaces
- **Decision:** Breaking update. No compatibility or migration layer.

## Context

Analysis artifacts are the objects an agent reads in Marivo's write-run-read
loop. The current surface exposes too many near-peer information exits:
`repr`, `summary()`, `show()`, `preview()`, `schema()`, `contract()`,
`contract().affordances`, and `next_intents()`. Each method has a defensible
local purpose, but the combined surface forces agents to choose a reading order
before they can do the real work.

The product boundary remains strict: Marivo is a deterministic analysis kernel.
It exposes typed computation, evidence, lineage, contracts, and fail-closed
errors. It does not choose business next steps or act as an agent planner.

## Problem

The current frame/result surface has four agent-ergonomics problems.

1. `summary()` and `show()` both look like observation exits. Agents can waste
   steps reading both, or choose `summary()` and miss the bounded display that
   is intended as the split-point readout.
2. `schema()` and `contract()` split one machine question across two methods:
   "what is this artifact and what can legally consume it next?"
3. `contract().affordances` is documented as its own action target in some
   guidance, which makes affordances feel like recommendations instead of
   neutral mechanical compatibility facts.
4. `preview()` and `next_intents()` are additional near-peer exits for
   observation and continuation planning, so the surface cannot honestly claim
   a two-exit model while they remain public.

## Goals

- Reduce the default agent mental model to two public exits:
  `artifact.show()` after each analysis step and `artifact.contract()` before
  composing the next operator.
- Remove `summary()` from the public frame/result surface.
- Fold schema facts into `contract()` so there is one machine-readable contract
  exit.
- Remove `preview()` and `next_intents()` from the public frame/result surface.
- Keep affordances as neutral contract data, never as recommended next steps.
- Preserve tabular escape hatches such as `to_pandas()` for terminal custom
  analysis.
- Update help, skills, docs, and tests so the simplified reading order is the
  only taught public contract.

## Non-Goals

- No compatibility shim for old public `summary()` or `schema()` calls.
- No new planner object, decision descriptor, ranked next action, or
  recommendation API.
- No semantic or datasource surface redesign in this slice.
- No change to metric computation, evidence generation, lineage semantics, or
  persisted artifact identity beyond fields required by the public contract.

## Public Mental Model

Agents should learn exactly this path:

```python
artifact.show()       # observe the current bounded result
artifact.contract()   # inspect machine-readable compatibility before next call
artifact.to_pandas()  # terminal escape hatch for custom analysis
```

Short rule:

```text
After each analysis step, read show(). Before composing another operator, read
contract(). Use tabular escape hatches only for terminal custom analysis.
```

`repr(artifact)` remains a safe Python fallback that identifies the artifact
and points to `.show()`. It is not a separate agent workflow step.

`preview()` is not a public escape hatch in this design. `show()` owns bounded
inspection; `to_pandas()` owns terminal custom analysis.

## Interface Design

### `show()`

`show()` is the only default observation exit. It prints bounded, stable output
for humans and agents at script split points.

Each frame/result `show()` should include:

- artifact identity: family, ref, and cheap domain identity
- artifact shape: semantic shape, metric, time scope, grain, dimensions, or the
  closest equivalent for non-metric result families
- evidence state: evidence status, quality summary, blocking issues, and
  confidence scope when present
- bounded rows or ranked entries with deterministic ordering
- a short `available:` footer that points to the two public exits and terminal
  data escape hatches, not to every internal helper

`show()` must not choose the next analytical step. It can expose neutral facts
and compatible exits, but the agent remains responsible for judgment.

### `contract()`

`contract()` is the only machine-readable contract exit. Agents read it when
they need to compose the next operator.

`ArtifactContract` should contain the current contract fields plus embedded
schema:

- `kind`
- `ref`
- `is_canonical`
- `schema`
- `affordances`
- `blocking_issues`

`schema` moves into the contract as structured column facts:

```python
class ArtifactSchema(BaseModel):
    semantic_shape: str | None
    columns: list[ArtifactColumn]
```

The embedded schema intentionally drops its own `kind` and `ref`; those remain
on `ArtifactContract` to avoid duplicate identity.

`affordances` remain mechanical compatibility entries:

```python
class ArtifactAffordance(BaseModel):
    operator: str
    required_inputs: list[str]
    preconditions: list[ArtifactPrecondition]
    param_template: ArtifactParamTemplate
    expected_output_family: str | None
```

Affordances are unranked and non-recommending. They say "this can be wired" and
not "this should be done."

### Removed Public Exits

`summary()` is removed from the public frame/result API. If an implementation
needs a structured summary for rendering, tests, or persistence, it should use
private helpers such as `_build_summary()` or internal DTOs that are not taught
in help, skills, examples, or top-level public exports.

`schema()` is removed from the public frame/result API. Its information moves
to `artifact.contract().artifact_schema`.

`preview()` is removed from the public frame/result API. Existing bounded row
projection logic may remain as private render helpers for `show()`.

`next_intents()` is removed from the public frame/result API. Existing
`_NEXT_INTENTS`-style implementation data may remain private input for building
`contract().affordances`.

`contract().affordances` remains a field path, but agent guidance should say
"read `contract()`" instead of teaching `contract().affordances` as a separate
step.

Association and quality domain facts that currently live only in specialized
`summary()` overrides must move into each family's `show()`/`render()` body:

- `AssociationResult.show()` must surface method, correlation, aligned row
  count, dropped row count, and metric ids.
- `QualityReport.show()` must surface overall status, warning/blocking counts,
  and bounded per-check results.

`render()` may remain as a shared implementation detail of the existing
terminal result protocol, but analysis help and skills should not present it as
an agent-facing workflow step.

Any truncation hint rendered by `show()` or the shared bounded-card formatter
must stop advertising `.preview(limit=...)`. Hints should point to
`.to_pandas()` for terminal custom analysis.

## Affected Surfaces

Code surfaces:

- `marivo/render.py`
- `marivo/analysis/frames/base.py`
- concrete frame/result classes under `marivo/analysis/frames/`
- candidate result surfaces that currently expose affordances as selected row
  fields
- `marivo/analysis/help.py`
- `marivo/analysis/errors.py`
- `marivo/analysis/constraints.py`
- `marivo/analysis/__init__.py` and public surface snapshots where `summary`
  DTOs, schema-only types, preview types, or intent-list helpers are currently
  exposed

Agent-facing documentation:

- `agent-guide.md`
- `marivo/skills/marivo-analysis/SKILL.md`
- `marivo/skills/marivo-analysis/references/cheatsheet.md`
- `marivo/skills/marivo-analysis/references/final-report.md`
- examples under `marivo/skills/marivo-analysis/references/examples/`

Tests:

- public surface snapshot tests
- introspection/help drift tests
- agent result protocol tests
- frame/result unit tests that currently call `.summary()`, `.schema()`,
  `.preview()`, or `.next_intents()`
- skill/example checks that mention `summary()` or `contract().affordances`

## Expected Help Contract

Analysis help should teach this concise pattern:

```text
Use .show() to inspect the current artifact.
Use .contract() before composing another operator.
Use .to_pandas() only when leaving typed artifact flow for terminal custom work.
```

Help for `MetricFrame`, `DeltaFrame`, `AttributionFrame`, `CandidateSet`, and
other terminal analysis artifacts should not list `.summary()` or `.schema()`
as public methods. They should also not list `.preview()` or `.next_intents()`.

## Testing Strategy

Use focused tests first, then broaden only where the changed surface requires
it.

Required focused checks:

- A public surface test fails if analysis exposes frame/result summary DTOs or
  public frame/result `.summary()`, `.schema()`, `.preview()`, or
  `.next_intents()` methods. Session-level summaries such as `SessionSummary`,
  `JobSummary`, and `FrameSummaryEntry` are out of scope for this assertion.
- A protocol test asserts public analysis artifacts expose `show()` and
  `contract()` as the primary agent exits.
- A contract test asserts `artifact.contract().artifact_schema` contains column names,
  dtypes, nullability, and semantic roles.
- A contract test asserts `artifact.contract().affordances` remains unranked
  mechanical metadata and contains no recommendation wording.
- Render tests assert the `available:` footer teaches exactly `.show()`,
  `.contract()`, and `.to_pandas()` for frames where `to_pandas()` is valid.
  The same tests also assert truncation hints do not mention `.preview(...)`.
- Association and quality tests assert the domain facts formerly exposed by
  specialized `summary()` DTOs are visible through `show()`/`render()`.
- Help/introspection tests assert `.summary()`, `.schema()`, `.preview()`, and
  `.next_intents()` are absent from public analysis artifact help.
- Skill/example checks assert guidance says `contract()`, not
  `contract().affordances`, as the next-call composition step.

Likely verification commands:

```bash
make test TESTS='tests/test_agent_api_drift.py tests/test_agent_result_protocol.py tests/test_public_surface.py'
make examples-check
make lint
make typecheck
```

## Acceptance Criteria

- The only taught agent exits for analysis frame/result inspection are
  `show()` and `contract()`.
- `summary()` is not public on analysis frame/result objects and is absent from
  public help, skills, examples, and top-level exports.
- `schema()` is not public on analysis frame/result objects; schema facts are
  available through `contract().artifact_schema`.
- `preview()` and `next_intents()` are not public on analysis frame/result
  objects.
- Error repair text, constraint guidance, and render truncation hints do not
  mention `.preview(limit=...)`.
- `contract().affordances` remains available as neutral machine data, but docs
  and skills do not teach it as a standalone reading step.
- Association correlation/alignment facts and quality per-check facts remain
  visible through `show()` after removing specialized `summary()` methods.
- No recommendation or planner semantics are introduced.
- Existing frame/result objects continue to satisfy bounded display behavior
  through `show()` and safe one-line `repr`.

## Implementation Notes

Because this is a breaking cleanup, update tests to the new contract rather
than preserving legacy assertions. Remove stale public names and stale help
entries instead of adding fallback aliases. Where private rendering code needs
summary-like data, keep it private and local to the renderer or frame family.

The implementation should move in this order:

1. Update public tests to describe the new two-exit contract.
2. Move schema data into `ArtifactContract`, without adding unrelated
   `family`, `methods`, or top-level `preconditions` fields.
3. Remove public `summary()`, `schema()`, `preview()`, and `next_intents()` from
   analysis artifacts.
4. Update render/show content and available footers, including association and
   quality domain facts. Update shared truncation hints so no rendered output
   points to `.preview(limit=...)`.
5. Update help, skills, examples, and docs.
6. Run focused tests, then broaden to repository entrypoints for touched
   surfaces.
