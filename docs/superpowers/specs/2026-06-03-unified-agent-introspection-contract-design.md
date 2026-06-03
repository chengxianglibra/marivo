# Unified Agent-Facing Introspection Contract

Date: 2026-06-03

Status: approved design, pending implementation plan.

## Context

Marivo's goal is to expose its Python API definitions, capabilities, and
constraints to coding agents (Claude Code, Codex, etc.) in a friendly, controlled
way, so an agent knows how to define datasources, build semantic objects, and run
analysis **without guessing and iterating on broken code**.

Today the runtime-introspection surface is strong but uneven across the three
public packages:

- `marivo.semantic` (imported as `ms`) is the gold standard: a constraints
  catalog (`marivo/semantic/constraints.py`) is the single source of truth for
  agent-facing rule text; `ms.help(symbol, format="json")` returns structured
  data including constraints; errors pull their hints from the same catalog
  (`_hint_from_catalog`); 3 runnable, CI-checked examples back it.
- `marivo.analysis` (imported as `mv`/`ap`) has `mv.help()` **text only — no
  `format="json"`**, no constraints catalog, and its guidance is scattered across
  SKILL prose, hard-coded ASCII matrices in `marivo/analysis/help.py`, structured
  error `fix_snippet`s, and examples. Frame classes (the return type of every
  intent) render only a class line — their methods (`as_time_series`,
  `components`, `to_pandas`, `next_intents`, …) are invisible. `load_frame` has no
  own docstring and `help("load_frame")` misleadingly prints the *module*
  docstring via a fallback.
- `marivo.datasource` (imported as `md`) has **no `help` at all** and no
  constraints catalog, even though declaring a datasource is often the agent's
  first step.

Concrete measurements that motivated this work:

- Top-level `help()` lists a hand-curated subset: analysis 14 of 81 `__all__`
  symbols, semantic 21 of 43. Symbols outside the list resolve only if the agent
  already knows the name.
- `mv.help("MetricFrame")` shows the internal dataclass signature
  `(_df, meta, _NEXT_INTENTS)` and zero of its 12 public methods.
- `mv.help("load_frame")` prints the module docstring, not the function's.
- No test asserts: every `__all__` symbol is introspectable; the top-level list
  stays a subset of `__all__`; every `Constraint.example`/`docs_ref` path exists;
  every error kind maps to a catalog constraint. Examples are CI-run, but these
  cross-references can rot silently.

The consequence: the "ask the runtime, do not guess" path is complete only for
`semantic`. Agents must learn three different introspection idioms, and for two
surfaces the runtime cannot answer "what are this symbol's capabilities and
constraints?"

## Goal

Make every public surface answer the same machine-readable question with the same
shape:

```
<surface>.help(symbol=None, *, format="text" | "json")
```

returning, for any public symbol or topic, a single canonical descriptor:
`{signature, doc, constraints, examples, methods, ...}`. Structured data is the
single source of truth; the human text view is rendered from that same data, so
the two cannot drift. Bring `analysis` and `datasource` up to `semantic`'s bar,
make frames first-class and discoverable, and lock the whole contract with
drift-protection tests.

When this lands, an agent consults `help('<object>', format='json')` for the
specific object it is about to author or call — per object, not "before every
call" — and gets a complete, uniform, machine-checkable answer.

## Non-Goals

- No static/exported manifest and no MCP tool surface in this spec. The
  descriptor is designed to be trivially serializable so a static export is a
  cheap follow-on, but it is not built here.
- No central plugin registry and no single global `marivo.help()` entry point.
  Each surface keeps its own `help` and ownership of its catalog (federated).
- No new business rules. Analysis and datasource catalogs are *harvested* from
  rules that already exist in validators, structured errors, repair codes, and
  matrices — we relocate truth into the catalog, we do not invent it.
- No change to analysis/semantic/datasource runtime behavior beyond introspection,
  docstrings, and error-hint sourcing.

## Locked Decisions

1. **Unified introspection contract.** One canonical descriptor shape, identical
   across all three surfaces.
2. **Everything in one spec.** Shared core + all three surfaces at full parity
   (including new analysis and datasource constraints catalogs) + frame-method
   discovery + drift tests + skill/docs rebalancing. Sequenced into phases.
3. **Structured data is canonical; text renders from it; keep the `help()` name.**
   `format="json"` returns the descriptor; `format="text"` (default) renders the
   same descriptor to prose. No new `describe()` vocabulary. In-repo tests,
   skills, and examples are updated in the same change; exact byte-stability of
   current text output is not a constraint.
4. **Thin shared core + federated catalogs.** A new internal package owns the
   canonical types and the single renderer; each surface owns its catalog,
   resolver, and top-level summaries and emits through the shared types.

## Architecture

A new **internal** package `marivo/introspection/`. It is not exported in any
`__all__`; agents never import it. They only ever call `<surface>.help`. The
dependency direction is strictly one-way — surfaces depend on `introspection`;
`introspection` imports nothing from `datasource`/`semantic`/`analysis` — so there
are no import cycles. Surfaces pass their catalog, resolver, and summaries in as
data.

```
marivo/introspection/
  schema.py      # Descriptor, MethodInfo, TopLevelEntry, SCHEMA_VERSION, Kind literals
  constraints.py # Constraint, ASTSpec (lifted/generalized from semantic/constraints.py)
  describe.py    # inspect-based builders: callable/class/frame/module -> Descriptor
  render.py      # render_json(Descriptor)->dict ; render_text(Descriptor)->str
  surface.py     # Surface spec + help(surface, symbol, format) orchestration
  errors.py      # hint_from_catalog(catalog, error_kind) shared by every surface's errors.py
```

`render.py` is the **only** place human text is produced, and `render_json` and
`render_text` consume the same `Descriptor`. A renderer bug is fixed once; json
and text cannot diverge.

Each surface keeps a thin, federated `help.py`:

```python
# marivo/<surface>/help.py
_SURFACE = Surface(
    name="marivo.analysis",
    all_names=__all__,        # top-level list is DERIVED from __all__, not hand-curated
    summaries={...},          # one-liners per symbol (the only curated text)
    resolve=_resolve,         # surface-owned symbol -> object
    catalog=CONSTRAINTS,      # surface-owned constraints
    topics={...},             # surface-owned curated topics (alignment, decomposition, ...)
)

def help(symbol=None, *, format="text"):
    return render(_SURFACE, symbol, format)   # delegates to the shared core
```

The agent-visible call is identical on all three: `md.help`, `ms.help`,
`mv.help`, each `(symbol=None, *, format="text"|"json")`.

## The Descriptor Schema

One data structure, returned as a dict by `format="json"` and rendered to prose by
`format="text"`. A `kind` field discriminates the variants.

`kind ∈ {callable, class, frame, module, topic, surface, unknown}`

Common fields (every descriptor):

| field | meaning |
|---|---|
| `schema_version` | `"1"`; bump on breaking shape changes |
| `surface` | `marivo.datasource` / `marivo.semantic` / `marivo.analysis` |
| `kind` | discriminator above |
| `symbol` | name asked for (`null` for the top-level `surface` listing) |
| `summary` | one-line description |
| `doc` | full docstring — **own docstring only** |
| `constraints` | list of `Constraint.to_dict()` dicts |
| `examples` | `list[str]` of runnable example paths |
| `see_also` | `list[str]` of related `help(...)` calls |

Kind-specific additions:

- `callable` → `signature`
- `class` / `frame` → `methods: [{name, signature, summary}]`; `frame` also adds
  `next_intents: [str]` and a curated `constructed_by` string, and suppresses the
  internal dataclass `__init__` signature.
- `topic` → `content: dict` (alignment/decomposition/discover/select/transform/
  calendar matrices as **structured data**, not ASCII).
- `surface` → `entries: [{name, kind, summary}]`, derived from `__all__`.
- `unknown` → `did_you_mean: [str]` (difflib close-matches over `__all__`).

**`doc` resolution rule (fixes two traps):** resolve `doc` from the object's own
`__doc__` (cleandoc-normalized), never from `inspect.getdoc`'s MRO walk and never
from a module-docstring fallback. This makes `load_frame`'s missing docstring
visibly empty (and we add a real one) and prevents Pydantic models such as
`AlignmentPolicy` from surfacing `BaseModel`'s docstring. (`semantic` already has
a test asserting exception classes do not surface an inherited base docstring; the
rule generalizes that behavior to every kind, in one place.)

Concrete json:

```jsonc
// semantic.help("metric", format="json")
{ "schema_version":"1", "surface":"marivo.semantic", "kind":"callable", "symbol":"metric",
  "signature":"metric(*, name, datasets, decomposition, additivity, ...)",
  "summary":"declare a dataset-backed aggregate metric",
  "doc":"Declare a base metric ...",
  "constraints":[{"id":"metric_datasets_required","error_kind":"missing_datasets",
    "hint":"Base metrics need datasets=[...]","example":".../01_single_model_file.py"}],
  "examples":[".../01_single_model_file.py"],
  "see_also":["help('decomposition')","help('derived_metric')"] }

// analysis.help("MetricFrame", format="json")
{ "schema_version":"1", "surface":"marivo.analysis", "kind":"frame", "symbol":"MetricFrame",
  "summary":"typed metric result frame",
  "constructed_by":"session.observe(...)",
  "methods":[{"name":"as_time_series","signature":"() -> MetricFrame","summary":"..."},
             {"name":"components","signature":"() -> ComponentFrame","summary":"..."},
             {"name":"to_pandas","signature":"() -> pd.DataFrame","summary":"materialize a copy"}],
  "next_intents":["compare","decompose","assess_quality"],
  "constraints":[], "examples":[...] }
```

## Constraint Types

`Constraint` and `ASTSpec` move from `marivo/semantic/constraints.py` into
`marivo/introspection/constraints.py` essentially unchanged. The one
generalization: `Constraint.id` becomes a plain `str` (a `StrEnum` value is a
`str`), so each surface defines and owns its own id enum while sharing the type and
`to_dict()` shape. The fields stay: `id, error_kind, phase, applies_to, title,
why, hint, example?, docs_ref?, ast_spec?`.

Semantic's `constraints.py` keeps its `ConstraintId` enum and `CONSTRAINTS` dict
and simply imports `Constraint`/`ASTSpec` from `introspection` — a near-zero diff.

## Per-Surface Work

### datasource (new)

- New `marivo/datasource/help.py` and `marivo/datasource/constraints.py`; add
  `help` to `marivo/datasource/__init__.py` `__all__` (agents call `md.help(...)`,
  matching `ms`/`mv`).
- Catalog harvested from the real rules in `datasource/authoring.py`,
  `datasource/loader.py`, and `datasource/errors.py`: `*_env` credential refs (no
  plaintext secrets in project state), `md.ref("name")` shape, `backend_type`
  validity, declaration location (`.marivo/datasource/*.py`), and the
  `~/.marivo/secrets.toml` resolved-secret cache behavior.
- `datasource/errors.py` hints pulled from the catalog via the shared
  `hint_from_catalog` (mirrors semantic).
- Top-level entries derived from the 9 public symbols + summaries.

### semantic (refactor — already the bar)

- `constraints.py`: re-point `Constraint`/`ASTSpec` imports to `introspection`;
  keep `ConstraintId` + `CONSTRAINTS` in place. Behavior unchanged.
- `help.py`: replace bespoke `_describe_*`/`_help_json`/`_list_top_level` with
  shared delegation; keep `constraints` and `decomposition` as `topic`
  descriptors with structured `content`. `format="json"` stays, now canonical.
- Add method enumeration for `SemanticProject` (kind=class): `load`,
  `list_metrics`, `propose_candidates`, `open_questions`, `answer`,
  `record_decision`, `audit`, `readiness`, `richness`, `describe`/`search` — so an
  agent can introspect the project object's capabilities.
- Top-level entries derived from `__all__` (reconciles the current 21 vs 43 gap).

### analysis (new json + new catalog + frames + fixes)

- `help.py`: add `format="json"`; refactor to shared delegation.
- New `marivo/analysis/constraints.py`, harvested from existing truth — the
  structured-error template fields (`location`/`cause`/`fix_snippet`/`doc` in
  `analysis/errors.py`) and the cross-dataset repair codes
  (`component-axis-unreachable`, `component-axis-field-mismatch`,
  `component-filter-unreachable`, `component-version-mismatch`,
  `snapshot-partition-missing`, `nested-derived-unsupported`, …). `errors.py`
  hints become catalog-backed.
- The five matrices (discover/select/transform/alignment/calendar) become `topic`
  descriptors with structured `content`; text rendering rebuilds a readable table
  from that data — no more hand-maintained ASCII.
- Frame-method discovery for every frame class (`MetricFrame`, `DeltaFrame`,
  `AttributionFrame`, `ForecastFrame`, `QualityReport`, `CandidateSet`,
  `AssociationResult`, `ComponentFrame`, `ExplorationResult`,
  `HypothesisTestResult`): methods + `next_intents`, internal dataclass signature
  suppressed in favor of a `constructed_by` note.
- Fixes: `load_frame` gets a real docstring; the module-docstring fallback is
  removed (own-doc-only now lives in `describe.py`).

## Frame-Method Discovery

In `describe.py`, `describe_frame`/`describe_class` enumerate public methods via
`inspect`: callables defined on the class, excluding dunders and private names,
each with its signature and a one-line summary (first line of the method's own
docstring). This generalizes the existing `_namespace_methods` special-casing
(`TransformAPI`, `DiscoverAPI`) to all classes. Frames additionally expose
`next_intents` (read from the frame's existing next-intents mechanism) and a
curated `constructed_by` string, and suppress the auto-generated dataclass
`__init__` signature (which leaks internal fields like `_df`).

## Top-Level Listing

The top-level `surface` descriptor is generated from `__all__` (not hand-curated),
so it is always complete. To stay scannable, `entries` are **grouped by kind**
(intents · frames · refs · policies · errors · topics · modules), one line each,
most-used groups first. The agent sees the whole surface organized, then drills in
with `help('<name>')`. `format="text"` renders the groups as labeled sections;
`format="json"` returns the flat `entries` list with each entry's `kind`.

## Introspection-Layer Error Handling

- Unknown symbol → `kind="unknown"` descriptor with `did_you_mean` (difflib over
  `__all__`) and a "call `help()` to list entries" hint. Never raises.
- `inspect` failures (a symbol whose signature cannot be read) fall back to
  signature `(...)`; never propagate to the agent.
- Invalid `format` → `ValueError("format must be 'text' or 'json'")` (matches
  current semantic behavior).
- The layer is import-safe and side-effect-free: `help()` works before any project
  load or backend wiring.

## Drift-Protection Tests

`tests/test_introspection_contract.py`, parametrized across the three surfaces.
Invariants:

1. Every name in `__all__` resolves to a Descriptor without raising (explicit
   exempt set if ever needed).
2. The top-level listing is generated from `__all__` — no phantom entries, no
   missing public symbols.
3. Every `Constraint.example` and `Constraint.docs_ref` path exists on disk.
4. Every error kind / error class on each surface maps to at least one catalog
   constraint (no silent generic-hint fallback).
5. `format="json"` for every symbol/topic validates against the schema
   (`schema_version` present + required keys per `kind`).
6. `format="text"` renders for every symbol/topic without raising.
7. No descriptor's `doc` equals an inherited base-class or module docstring
   (locks the `load_frame`/Pydantic trap shut).

These run in `make test`, are cheap, and are what make "everything in one spec"
safe: the multi-source contract cannot silently rot. Existing
`tests/test_analysis_help.py` assertions are updated to the new rendered text;
`scripts/run_skill_examples.py` continues to execute every example.

## Skill And Docs Rebalancing

- Both `SKILL.md` files (`marivo-skills/marivo-semantic`, `marivo-skills/
  marivo-analysis`): replace bespoke help instructions with the single uniform
  idiom — `<surface>.help('<name>', format='json')` → `{signature, doc,
  constraints, examples, methods}`. Shrink "Non-Negotiable Rules" that merely
  restate runtime-exposed hints; keep genuinely procedural rules (session reuse,
  readiness-gated handoff) and the error→example routing table.
- `marivo-skills/marivo-semantic/references/datasource.md`: add the now-real
  `md.help` introspection guidance.
- Update any example or reference that prints help output, per the repository rule
  that public symbol/representation changes update matching examples under
  `marivo-skills/marivo-*/references/examples/`.
- Net posture: skills point to `help` as the uniform, *sufficient* idiom —
  consult per object, not "before every call." This directly resolves the earlier
  question about over-mandating help in the skill: once the runtime contract is
  uniform, complete, and machine-readable, the blanket pre-call ritual is
  unnecessary.

## Phasing

Each phase keeps `make test` and `make examples-check` green and keeps `help()`
working throughout.

- **P0 — shared core.** `marivo/introspection/` (schema, constraints types,
  describe, render, surface, errors) + the contract-test scaffold.
- **P1 — migrate semantic onto the core.** Lowest risk; it already matches the
  shape. Proves the core end-to-end.
- **P2 — analysis.** `format="json"`, new constraints catalog, matrices as topic
  data, frame-method discovery, `load_frame` docstring + fallback removal.
- **P3 — datasource.** New `help` + catalog + error-hint wiring.
- **P4 — drift tests.** Full invariant suite across all three surfaces.
- **P5 — skills/docs/examples.** Rebalance prose and update examples.

## Success Criteria

- `md.help`, `ms.help`, `mv.help` all accept `(symbol=None, *,
  format="text"|"json")` and return the same descriptor shape.
- `help(symbol, format="json")` for any `__all__` symbol on any surface returns
  `signature`/`doc`/`constraints`/`examples` (and `methods`/`next_intents` for
  frames) — no symbol is unreachable, no frame hides its methods.
- Analysis and datasource each have a constraints catalog; their errors source
  hints from it.
- `help("load_frame")` shows `load_frame`'s own docstring; no descriptor surfaces
  an inherited or module docstring.
- The top-level listing is generated from `__all__`, grouped by kind, and verified
  by tests to match the public surface.
- `tests/test_introspection_contract.py` enforces every invariant in the
  Drift-Protection section; `make test` and `make examples-check` pass.

## Risks And Mitigations

- **Large blast radius (touches all three packages).** Mitigated by phasing,
  one-way dependency, and migrating the lowest-risk surface (semantic) first to
  validate the core before analysis/datasource.
- **Text-output churn breaks existing assertions.** Expected and accepted;
  `test_analysis_help.py` and any help-printing examples are updated in the same
  change. The drift tests assert structure and rendering success rather than exact
  prose, reducing future brittleness.
- **Frame-method enumeration could be noisy.** Mitigated by excluding dunders and
  private names and summarizing from the method's first docstring line; a curated
  per-frame ordering/allowlist can trim noise if needed.
- **Catalog harvesting could drift from validator behavior.** Mitigated by
  invariant 4 (every error kind maps to a constraint) so a new error without a
  catalog entry fails tests.

## Out Of Scope (Deferred)

- Static/exported descriptor manifest and MCP tool surface (descriptor is designed
  export-ready).
- Any single global `marivo.help()` dispatcher.
- New authoring rules or runtime behavior changes beyond those listed.
