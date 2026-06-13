# Agent Result Surface Design

Date: 2026-06-13
Status: Approved design, pre-implementation
Related: `agent-guide.md` ("Agent-Facing Surface Principles"),
`docs/superpowers/specs/2026-06-09-semantic-catalog-public-api-design.md`,
`docs/superpowers/specs/2026-06-12-semantic-analysis-interface-unification-design.md`

## Problem

`agent-guide.md` now carries seven Agent-Facing Surface Principles
(committed in `fe697f93`). They are written as review criteria. Three of
them are codeable contracts that nothing currently enforces, so the
codebase drifts from them:

- **Principle 3 (`__repr__` is the floor).** Many public result types still
  use the default dataclass/pydantic repr, which dumps every field. An agent
  that `print()`s one of these — or hits one in a traceback — pays a
  token-dump it never asked for, before it ever reaches a designed `.show()`.
  Audit hits (`def __repr__` / `def show` / `_repr_identity`):
  `PreviewResult`, `DatasourceTestResult`, `ColumnInspection`, `JobSummary`,
  `SessionSummary`, `FrameSummary`, `AuthoringAssessment` have **0**.
- **Principle 4 (terminal results implement a shared protocol).** The
  analysis `BaseFrame` (`marivo/analysis/frames/base.py`) already implements
  the intended protocol — bounded `__repr__`, bounded `render()` via
  `format_bounded_card`, `show()`, and a static `_AVAILABLE_ENTRIES`
  affordance. But that protocol is private to the frames subtree. Semantic
  and datasource result types each reimplement repr/show ad hoc or not at
  all. There is no shared declaration and no cross-module test.
- **Principle 5 (surface growth is gated).** `__all__` is not pinned by any
  test, so a new public symbol can be exported in passing. `ms.find_project`
  is exported in `marivo.semantic.__all__` even though its own help summary
  says "internal — use ms.load() instead" — the exact violation Principle 2
  names.

The infrastructure to fix all three already exists and is under-adopted:

- `marivo/render.py:format_bounded_card` is a top-level, module-agnostic
  bounded-card renderer. Today only `marivo/analysis/frames/` consumes it.
- `marivo/introspection/` is the shared help/describe/suggestion home:
  `errors.py:hint_from_catalog` (state-derived hints) and
  `_fuzzy.py:did_you_mean` already generate suggestions from real state.

So this is an adoption-and-enforcement effort, not new machinery.

## Goals

- Every terminal result type — an object an agent stops to read or
  `print()`s — has a bounded single-line repr, a bounded `.show()`, and an
  affordance footer, all routed through `format_bounded_card`.
- One shared, declared result protocol spanning semantic, analysis, and
  datasource. Conformance is pinned by a contract test, not by inheritance.
- Public `__all__` is pinned by a snapshot test; surface growth becomes a
  deliberate, reviewed decision. `find_project` leaves the public surface.

## Non-Goals

- Changing the **semantics** of any result (frame shapes, computations,
  persistence, evidence). Only its textual presentation and protocol
  conformance change.
- Re-touching the unification refactor's Future Work (`resolver.time_axis`,
  further IR/details merge).
- Introducing any new public result type. This design regularizes the
  existing ones only.
- Changing `agent-guide.md`. The seven principles are already committed;
  this design implements three of them.

## Core Decision

A **structural protocol** (`typing.Protocol`), enforced by a contract test —
not a shared base class. Rationale: `format_bounded_card` is already the
shared rendering primitive, so a conforming implementation is a thin wrapper,
not duplicated logic; the semantic pydantic models and the existing
`BaseFrame` inheritance chain make a common base class costly and invasive,
while a Protocol is zero-inheritance; and a contract test pins the contract
more reliably than a base class, which cannot stop a subclass from writing a
field-dumping `_repr_identity`. (Considered and rejected: a shared ABC base
— inheritance conflicts with pydantic/`BaseFrameMeta`; a mixin — MRO/pydantic
metaclass friction. Both still need the contract test, so neither earns its
inheritance cost.)

## Result Protocol

Defined at top level in `marivo/render.py`, next to `format_bounded_card`:

```python
@runtime_checkable
class AgentResult(Protocol):
    def render(self) -> str: ...    # bounded plain-text card, no trailing newline
    def show(self) -> None: ...     # print(render()) + newline; returns None
    def __repr__(self) -> str: ...  # single-line, bounded; "<Kind id=...; call .show() to inspect>"
```

- Structural, not inherited. Each terminal type implements `render()` by
  calling `format_bounded_card(...)`; `show()` and `__repr__` are thin
  wrappers over it. An optional module-level helper in `render.py` may
  collapse the `show()`/`__repr__` boilerplate, but it is a free function,
  not a base class — types opt in by calling it.
- `format_bounded_card` is unchanged. Its current signature
  (`identity`, `status`, `columns`, `rows`, `row_count`,
  `preview_truncation_hint`, `available`) already covers every terminal
  type's needs; this design only widens its caller set.

### What is a terminal result type

In scope (objects an agent stops to read / prints):

- **analysis:** every `*Frame` (already conform via `BaseFrame`),
  `JobSummary`, `SessionSummary`, `FrameSummary`, `Lineage`.
- **semantic:** `SemanticObjectList`, `SemanticObject`, `ReadinessReport`,
  `VerifyResult`, `RichnessSummary` (already conform), `AuthoringAssessment`
  and the `*Brief` family (to add).
- **datasource:** `PreviewResult`, `DatasourceTestResult`,
  `ColumnInspection`, `ScanReport`, `DatasourceSummary`.

Explicitly excluded (carry no protocol):

- `*Ref` identifiers, `*Details` value shapes (reached via
  `SemanticObject.details()`), pure value types (`AiContextView`,
  `SnapshotVersioning`, ...), `*Input` type aliases, `*IR` / `*Metadata`
  internal data.

The authoritative in-scope list is the terminal subset of the `__all__`
allowlist (below); the contract test iterates exactly that subset, so the
two stay in sync.

## Affordance Model

Principle 4's "affordance hints generated from real state" conflates two
distinct mechanisms. They are kept separate and may not impersonate each
other:

### Mechanism 1 — static "next callable" list (repr/show footer)

- Each type carries a fixed list of the methods that are legitimate next
  calls on it — e.g. a frame's `.preview(limit=...)` / `.to_pandas()`,
  `SemanticObjectList`'s `.refs()` / `.objects`.
- It is bound to the **type**, not the data, so it is static. This is exactly
  today's `_AVAILABLE_ENTRIES` on `BaseFrame`; the pattern is generalized via
  the `available=` argument of `format_bounded_card`.
- Rendered as the card footer, one or two lines — not a menu.

### Mechanism 2 — state-derived suggestions (errors / empty / ambiguous)

- "unknown metric → available ids", "did you mean X" — suggestions that
  depend on **real state** — continue to flow through
  `marivo/introspection/errors.py:hint_from_catalog` and
  `marivo/introspection/_fuzzy.py:did_you_mean`.
- They appear on **error objects** and on **empty/ambiguous results** (e.g.
  `catalog.list()` with no hits suggesting nearby available domains), not as
  a fixed footer on every successful result.

### Hard constraints

- Every method name in a Mechanism 1 list must be a real callable attribute
  of the type. The contract test verifies this — a static footer cannot rot
  into pointing at a method that no longer exists.
- Mechanism 2 suggestions must come from a live query, never hardcoded.

## Surface Gating

### `__all__` snapshot test

New `tests/test_public_surface.py`:

- For `marivo.semantic`, `marivo.analysis`, `marivo.datasource`, assert each
  module's `__all__` equals an explicit allowlist (set equality, counts
  included).
- The allowlist is written from current real state (semantic after
  `find_project` removal, analysis 50, datasource 41) and lives in the test
  file. Any added or removed public symbol must edit this file — turning
  "should this be public" from an incidental export into a deliberate,
  reviewed decision.
- This test is the enforcement organ for Principle 5 and the single source of
  truth the contract test draws its terminal subset from.

### `find_project` cleanup

`ms.find_project` documents itself as internal yet sits in `ms.__all__`
(Principle 2 violation, and the only known existing one):

- Remove it from `marivo/semantic/__init__.py`'s `__all__`. The function
  stays importable for internal callers (`ms.load()` etc.); it is simply no
  longer a public symbol.
- Update `help.py`'s `_SUMMARIES` (drop the entry or mark internal) and any
  test asserting it appears in `__all__`.
- The snapshot allowlist is written with `find_project` already removed, so
  the two land aligned.

### help top-level folding

Excluded types (`*Ref` / `*Details` / value types / `*Input`) remain in
`__all__` but are **not** enumerated item-by-item in the top-level help
index. The top level folds them by family (e.g. "`*Details` × 8 — see
describe('MetricDetails')"), satisfying Principle 5's "type aliases stay out
of the top-level help index". This is a help-rendering change only; it does
not alter `__all__`.

## Testing

### Contract test — `tests/test_agent_result_protocol.py`

Iterates the terminal subset of the allowlist; for each type constructs a
minimal instance (reusing existing fixtures in `tests/conftest.py` /
`tests/shared_fixtures.py`) and asserts:

- `isinstance(obj, AgentResult)` — structural conformance holds.
- `repr(obj)` is single-line (no `\n`), within a bounded length
  (`<= REPR_MAX_LEN`, a named constant), and carries type name and identity.
- `obj.render()` returns `str`, has no trailing newline, and is bounded
  (line count and character count under named caps).
- `obj.show()` returns `None`.
- Every method name in the type's Mechanism 1 list is a real callable
  attribute of the type.

### repr-floor remediation

The currently-0-hit terminal types (`PreviewResult`, `DatasourceTestResult`,
`ColumnInspection`, `JobSummary`, `SessionSummary`, `FrameSummary`,
`AuthoringAssessment`, and the `*Brief` family) get a `format_bounded_card`
implementation, replacing their default field-dumping repr. The contract
test is the regression net.

### Regression baseline

Existing frame repr/show tests and the semantic catalog `.show()` tests must
pass unchanged — those types already satisfy the protocol; making the
contract explicit must not change their output.

## Rollout

Single branch, three reviewable stages. Each stage ends green
(`make test`, `make typecheck`, `make lint`).

1. **Gating first.** `tests/test_public_surface.py` (`__all__` snapshot),
   `find_project` cleanup, help top-level family folding. No behavior change;
   pure convergence; locks the surface at lowest risk.
2. **Protocol adoption.** Define `AgentResult` in `render.py` plus the
   optional thin-wrapper helper; add `format_bounded_card` implementations to
   every 0-hit terminal type; add the contract test.
3. **Doc alignment.** Update affected `docs/specs/` files and
   `marivo-skills/` examples so repr/show output shapes match the new
   contract. `agent-guide.md` is not touched (its principles are already
   committed).

## Risks

- **pydantic repr override.** Several terminal types are pydantic models;
  overriding `__repr__` must bypass pydantic's generated repr (via
  `model_config` or explicit method override) without breaking
  serialization. Verify per type during stage 2.
- **fixture coverage.** The contract test needs a minimal constructible
  instance of each terminal type. Where a fixture does not exist, add the
  narrowest one rather than weakening the assertion.

## Future Work

- A `mv.results()` / `ms.results()` registry that the contract test and help
  both read, removing the hand-maintained terminal subset. Deferred — the
  allowlist-derived subset is sufficient and avoids a new public surface.
- Extending the protocol to error objects so tracebacks render bounded cards
  too. Errors already render through the shared template style; folding them
  into `AgentResult` is evaluated separately.
