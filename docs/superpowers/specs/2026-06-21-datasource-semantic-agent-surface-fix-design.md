# Datasource / Semantic Agent-Surface Fix Design

Date: 2026-06-21
Status: Approved design, pre-implementation
Related: `agent-guide.md` ("Agent-Facing Surface Principles"),
`docs/superpowers/specs/2026-06-13-agent-result-surface-design.md`,
`docs/superpowers/specs/2026-06-16-semantic-authoring-surface-redesign-design.md`

> Historical note: this spec predates removal of the public semantic
> `prepare_*` authoring stage. Current agents must use
> `help -> discover -> settle/grill -> author -> verify`; remaining
> `prepare_*` text below is historical context only.

## Problem

From an agent's write-run-read perspective, the public surfaces
`marivo.semantic` (118 names in `__all__`) and `marivo.datasource`
(50 names) violate three of the Agent-Facing Surface Principles. Ground
truth was captured by rendering `ms.help()` / `md.help()` exactly as an
agent sees them.

### Concern 3 — discovery is neither bounded nor complete (most severe)

`help()` is the agent's one-line scan index. Today **30 of the 118
semantic public names render with a blank summary**, including the single
most-recommended constructor:

```
ms.aggregate          [callable]      <- the default tier-1 metric constructor, blank
ms.prepare_measure    [callable]      <- a core ladder step, blank
ms.semi_additive      [callable]
ms.make_ref / ms.linear / ms.richness / ms.parity_check / ms.record_decision  ...
```

Root causes:

- The index summary is read from a hand-maintained `_SUMMARIES` dict in
  `marivo/semantic/help.py` and `marivo/datasource/help.py`. It is a
  second source of truth that has drifted: 30 names missing, and **5 dead
  keys** (`ParitySummary`, `PreviewSummary`, `RichnessSummary`,
  `derived_metric`, `sum`) pointing at symbols that do not exist in
  `__all__`.
- Per-symbol drill-down (`ms.help("aggregate")`) *does* render full docs
  from the docstring, so the information exists — it is simply absent from
  the scan layer where an agent decides what to use.
- No test guards summary coverage. `tests/test_public_surface.py` pins the
  `__all__` *set* only. `marivo.analysis` happens to be complete (0 blanks)
  by manual discipline, not by enforcement.

### Concern 2 — unnecessary types exposed

- `help()` folds 36 semantic + 17 datasource names into an unstructured
  "Other types" bucket, plus a family literally labelled **"Internal IR
  types"** (`MeasureIR`; datasource `AiContextIR`, `CsvSourceIR`,
  `DatasourceAiContextIR`, `DatasourceIR`, `ParquetSourceIR`).
- `MeasureIR` is pure leakage: it appears in `__all__` yet in **no public
  callable signature** (only internal `_build_measure_object` / `loader` /
  `validator`).
- Type aliases `SemanticKindInput` / `SemanticRefInput` sit in the index,
  contradicting the principle "type aliases and module-internal handoff
  types stay out of the top-level help index."
- A large share of the "Other types" bucket are read-only evidence facts an
  agent never constructs and never names (`ComponentFact`,
  `DimensionValueFact`, `JoinPathFact`, `PrimaryKeyCandidate`,
  `FormatCandidate`, `RegisteredMatch`, `VersioningHints`, `DemandSignal`),
  and parse-variant result dataclasses (`DateParse`, `DatetimeParse`,
  `TimestampParse`, `StrptimeParse`, `HourPrefixParse`) that an agent only
  ever obtains from a constructor (`ms.timestamp(...)`) and passes straight
  into `parse=`.

### Concern 1 — redundancy (narrow)

The per-kind `*Ref` / `*Brief` / `*Details` triples are justified
kind-dispatched precision, not redundancy. The one genuine issue is the
`ref` / `make_ref` pair:

- `ref(id: str) -> str` returns a **string** for in-body / forward
  references — used in authoring
  (`marivo/skills/marivo-semantic/references/authoring-patterns.md:120`).
- `make_ref(id, kind) -> SemanticRef` constructs a typed ref. Site docs
  describe it as "catalog 内部使用 / construct the per-kind subclass"; **no
  skill or example calls it**. It is a documented-but-internal constructor.

The naming is a trap (the function called `ref` does not return a `Ref`).

## Goals

- Every public name in `marivo.semantic.__all__` and
  `marivo.datasource.__all__` renders a non-blank, scan-friendly one-line
  summary in `help()`, sourced from a single place (the definition itself).
- The summary index cannot silently drift again: a test fails on any blank
  summary.
- The public surfaces carry only what an agent must construct, pass by
  name, or stop to read. Internal IR, type aliases, and nested read-only
  facts leave `__all__`.
- `help()` has no "Internal IR types" family and a near-empty "Other types"
  bucket.

## Non-goals

- `marivo.analysis` is **not** refactored. It already meets the summary
  bar; its `_SUMMARIES` dict stays. The only analysis change is dropping the
  one shared symbol `make_ref` from `ma.__all__`.
- No public renames beyond removing `make_ref`. `ref` keeps its name.
- No change to `marivo/introspection/surface.py` rendering logic beyond what
  is required to populate summaries from docstrings; the existing
  `summaries: Mapping[str, str]` contract is reused.

## Design

The work splits into two independently shippable phases.

### Phase 1 — Summary single-sourcing + docstring polish (Concern 3)

Low-risk, mechanical, does not touch `__all__`.

1. **Delete** `_SUMMARIES` from `marivo/semantic/help.py` and
   `marivo/datasource/help.py`.
2. **Add a derivation helper** (in `marivo/introspection/`, reusing
   `describe.own_doc` / `describe.method_summary`) that maps a surface name
   to a one-line summary:
   - if the name is a registered topic → the topic Descriptor's `summary`;
   - otherwise → the first non-empty docstring line of the resolved object.
   `_surface()` in each help module builds its `summaries` map from this
   helper instead of the hand dict. `surface.py` is unchanged — it already
   reads `surface.summaries.get(name, "")`.
3. **Polish the first docstring line** of every retained public symbol in
   `marivo.semantic` and `marivo.datasource` so it is a crisp, imperative,
   agent-scannable one-liner (what it declares / returns), ≤ ~80 chars.
4. **New test** (extend `tests/test_public_surface.py`): for `ms` and `md`,
   assert every top-level `help()` entry has a non-blank summary, and that
   no topic key references a non-existent symbol. Apply the same non-blank
   assertion to `ma` to lock in its current completeness.

`marivo.analysis` keeps its `_SUMMARIES` (out of scope) and stays green.

### Phase 2 — `__all__` slimming (Concern 2 + `make_ref`)

Breaking surface reduction, per-symbol verified.

**Removal rule (decision contract).** A name is removed from `__all__` only
if **all** hold:

1. it is not a parameter type an agent must construct or pass by name;
2. it is not a return type an agent must name (annotate or `isinstance`) —
   it is only ever reached nested inside another result and consumed via
   `.show()` / attribute access;
3. it is not a terminal result an agent stops to read as a top-level return
   (`*Brief`, `*Details`, and the readiness/verify/parity reports stay);
4. it has no consumer in `marivo/skills/**`, `site/src/**`, `docs/**`
   (excluding generated `site/.astro`), `tests/**`, or example files.

**Verification method.** For each candidate, grep the four consumer roots
and inspect public callable signatures (`inspect.signature`) for the type
name. A candidate that survives all four rule clauses is removed.

**Preliminary classification** (final list produced and signed off during
spec review before any mass edit):

- _Remove — confirmed internal:_ `MeasureIR`; `SemanticKindInput`,
  `SemanticRefInput`; datasource `AiContextIR`, `DatasourceIR`,
  `DatasourceAiContextIR`; `make_ref` (from both `ms` and `ma`).
- _Remove — pending nested-only confirmation:_ parse results `DateParse`,
  `DatetimeParse`, `TimestampParse`, `StrptimeParse`, `HourPrefixParse`;
  evidence facts `ComponentFact`, `DimensionValueFact`, `JoinPathFact`,
  `PrimaryKeyCandidate`, `FormatCandidate`, `RegisteredMatch`,
  `VersioningHints`, `DemandSignal`; datasource result-nested
  `*Warning` / `*Metadata` / `ColumnProfile` and similar.
- _Keep:_ decorators / constructors (`entity`, `dimension`, `measure`,
  `metric`, `aggregate`, `count`, `time_dimension`, `relationship`,
  `domain`, `ratio`, `weighted_average`, `linear`, `semi_additive`,
  `ai_context`, `table`, `csv`, `parquet`, `from_sql`, `join_on`,
  `snapshot`, `validity`, `ref`, and the time-parse constructors
  `datetime`, `timestamp`, `strptime`, `hour_prefix` — their *result*
  dataclasses are removed but the constructors stay); all `*Ref`; all
  `*Brief`; all `*Details`;
  `ReadinessReport`, `RichnessReport`, `ParityResult`, `VerifyResult`;
  `SemanticCatalog`, `SemanticObject`, `SemanticObjectList`,
  `AiContextValue`; `ScanScope`; in-use enums (`SemanticKind`,
  `BriefStatus`); `errors`; `load`, `readiness`, `richness`, `verify_object`,
  `parity_check`, `record_decision`, and the `prepare_*` family.

**Family / index cleanup (Concern 2 discovery).** After removal, add
`family_suffixes` entries to the `Surface` config (e.g. `Report → Reports`,
`Source → Sources`) so residual names fold into named families. The
"Internal IR types" family disappears with the IR types; "Other types"
shrinks toward empty.

**`ref` / `make_ref` (Concern 1).** `make_ref` is removed (above). `ref`
stays; its first docstring line is rewritten to: "Return a qualified-name
string for forward / cross-domain in-body references." Internal callers
import `make_ref` from its defining module, not via package `__all__`, so
removal does not affect internals.

## Affected files

Phase 1:

- `marivo/semantic/help.py`, `marivo/datasource/help.py` — delete
  `_SUMMARIES`, derive summaries.
- `marivo/introspection/` — small summary-derivation helper.
- Docstring first lines across `marivo/semantic/**` and
  `marivo/datasource/**` public symbols.
- `tests/test_public_surface.py` — summary-coverage test.

Phase 2 (per removed name):

- `tests/test_public_surface.py` (and `tests/test_grain_public_exports.py`
  if affected) — update pinned sets.
- `docs/api/*.rst` autodoc stubs — drop entries for removed names.
- `site/src/content/docs/{en,zh-cn}/latest/**` — concept pages referencing
  removed names (notably `make_ref` in `concepts/semantic-layer.mdx`,
  both editions).
- `marivo/skills/marivo-semantic/references/**` — any reference to a
  removed name.
- `marivo/{semantic,datasource}/__init__.py` — `__all__` edits and the
  `Surface` `family_suffixes` / `hidden_names` config.

## Testing

- `make test TESTS='tests/test_public_surface.py'` — pinned sets + new
  summary-coverage assertions.
- `make test TESTS='tests/test_agent_api_drift.py'` — existing drift
  contract still holds.
- `make examples-check` — skill examples still import only retained names.
- `make typecheck`, `make lint` for touched modules.
- Full `make test` before completion (shared help infra is cross-module).

## Success criteria

- `ms.help()` and `md.help()` show zero blank summaries.
- No `_SUMMARIES` dict remains in the semantic / datasource help modules.
- `help()` shows no "Internal IR types" family; "Other types" is empty or a
  small, named residue.
- `from marivo.semantic import make_ref` and `from marivo.datasource import
  AiContextIR` (and every other removed name) fail; pin test reflects the
  new sets.
- All listed checks pass.

## Rollout

Ship Phase 1 first (no `__all__` change, fully mechanical, low blast
radius). Then Phase 2 after the removal list is signed off, because every
removal carries pin-test + autodoc + bilingual-site + skill churn.

## Open items (resolve at spec review)

- Final Phase-2 removal list: confirm the "pending nested-only" candidates
  against the four consumer roots and signatures, and get explicit sign-off
  before mass edits.
- Whether any residual "Other types" names warrant a new family suffix vs
  `hidden_names`.
