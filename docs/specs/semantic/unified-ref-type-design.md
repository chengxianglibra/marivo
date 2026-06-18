# Unified Semantic Ref Type Design

Status: draft design.

This document defines the target-state type model that collapses Marivo's three
semantic-reference families — spanning the datasource and semantic layers — into
a single cross-layer family. It is the concrete,
type-level realization of the **Unified Reference Model** intent already stated
in `docs/specs/semantic/stepwise-authoring-design.md`, and it brings the code
into alignment with that intent. `docs/specs/semantic/python-semantic-layer.md`
continues to own the decorator and ref contract and will be updated to match
the model below.

This is a **breaking change**. There is no back-compatibility layer, no
accessor aliases, and no migration path; all call sites move to the new shape
in one pass.

## Problem Statement

Semantic references exist as **three distinct type families** today:

1. **Authoring family** — `_BaseRef` subclasses `EntityRef`, `DimensionRef`,
   `TimeDimensionRef`, `MeasureRef`, `MetricRef`, `RelationshipRef`,
   `DomainRef` (in `marivo/semantic/ir.py`). Returned by the `ms.*`
   decorators/constructors. Kind is encoded by the **subclass**. Accessor is
   `.semantic_id`. The field kinds are **callable** inside metric bodies.
2. **Analysis/read family** — `SemanticRef`, a frozen dataclass `{ref, kind}`
   (in `marivo/semantic/catalog.py`). Returned by `catalog.get(...).ref` and
   `.children`. Kind is a **field**. Accessor is `.ref`. Inert (not callable).
3. **Datasource family** — `DatasourceRef` (in
   `marivo/datasource/authoring.py`), a standalone class returned by
   `md.ref(...)` and consumed by `ms.entity(datasource=...)`. Already public
   (re-exported in analysis `__all__`). Lives in the **datasource layer, which
   sits below semantic** — `marivo.semantic` imports from `marivo.datasource`.

This split causes concrete harm:

- **Analysis rejects authoring refs.** `MetricInput = SemanticObject |
  SemanticRef` does not accept an `ms.*`-issued `MetricRef`; the two families
  are unrelated types.
- **Kind silently dropped.** `resolver._ref_and_kind` handles `SemanticRef |
  SemanticObject | str` but not `_BaseRef`, so an authoring ref falls through
  the `str(value)` branch and loses its kind.
- **Duplicated dispatch.** `marivo/analysis/help.py` branches on both
  `SemanticRef` and `_BaseRef` for the same concept.
- **Mismatched accessors** (`.semantic_id` vs `.ref`, plus the awkward
  `obj.ref.ref` chain) raise cognitive load on an agent-facing surface.
- **Doc↔code mismatch.** The committed *Unified Reference Model* names
  `EntityRef` / `DimensionRef` / `MetricRef` (the authoring family) as the
  typed shape under `SemanticRefInput`, but the code builds `SemanticRefInput`
  on the analysis dataclass instead.

## Goals

- Exactly one ref family, used identically at authoring time and in the
  analysis loop.
- One id accessor, one normalizer, one dispatch path.
- Analysis intent inputs accept the unified refs without per-signature churn.

## Non-Goals

- **Bare-string acceptance policy is out of scope.** Whether analysis *intents*
  should also accept bare `str` (the "object-first vs uniform-accept" question)
  is orthogonal to the type merge and is decided separately. This change does
  not alter which **public intent** parameters accept bare strings, and it keeps
  the **internal resolver** string-tolerant (see Normalizers) — internal callers
  that pass raw ids are unchanged.
- **IR `semantic_id` fields are not touched.** `MetricIR.semantic_id`,
  `EntityIR.semantic_id`, etc. are an internal IR concern, distinct from the
  ref family's accessor. Only the *ref* accessor changes.

## Target Design

### Type model

- `SemanticRef` becomes the **shared public base** of the one family. It is a
  regular class (deliberately **not** a frozen dataclass — see field-ref
  binding below) with an **immutable identity**: `.kind` and `.id` are fixed at
  construction and never change, so `__eq__` / `__hash__` are stable. It
  carries:
  - `.kind: SemanticKind`
  - `.id: str` (the qualified semantic id)
  - `__str__` -> `.id`, `__eq__` / `__hash__` by `(type, id)`
  - `__get_pydantic_core_schema__` (str-or-ref coercion), preserved from the
    current `_BaseRef` so Pydantic anchor models keep validating.
- **Eight per-kind subclasses** extend `SemanticRef`, one per `SymbolKind`
  member: `EntityRef`, `MetricRef`, `DimensionRef`, `TimeDimensionRef`,
  `MeasureRef`, `RelationshipRef`, `DomainRef` (semantic layer), and
  `DatasourceRef` (datasource layer). Kind is fixed by the subclass.
  `DatasourceRef` is the **existing** `marivo/datasource/authoring.py` class
  (returned by `md.ref(...)`, consumed by `ms.entity(datasource=...)`),
  **reshaped to extend the base**: its `.name` / `.semantic_id` collapse to
  `.id` and it gains `kind=DATASOURCE`. That same reshaped class then also
  represents the catalog's datasource refs (`_build_datasource_object`, and the
  `datasource` / parent refs in `_build_entity_object`), so `md.ref(...)` and
  `catalog.get(<datasource>).ref` yield one type. The standalone `_BaseRef`
  class and the `SemanticRef` *dataclass* are both removed; their roles fuse
  into this base.
- **Field subclasses stay callable** in metric bodies (`DimensionRef`,
  `MeasureRef`, `TimeDimensionRef`). Callability uses a **late-bound resolver**
  slot set **exactly once** — this is precisely why the base is not a frozen
  dataclass (the `(id, kind)` identity stays immutable; only the resolver is
  attached). Two wiring paths, neither re-mutating:
  - **Authoring path (unchanged):** the field ref is constructed with no
    resolver; the loader sets it after sidecar assembly (today's `loader.py`
    pass over `ctx.pending_refs`), exactly once.
  - **Catalog path:** the ref is constructed **with the resolver already wired**
    from the loaded project, so a catalog-issued field ref is never in a
    `None`-resolver state.

  Non-field subclasses (`Entity`, `Metric`, `Relationship`, `Domain`,
  `Datasource`) carry no resolver and are fully immutable. An accidental call on
  a non-field ref raises the existing teaching error.
- **Accessor name is `.id`.** Considered `.semantic_id` for parity with the IR
  field; rejected because `obj.ref.semantic_id` is needlessly verbose and the
  ref is a distinct public-surface type from internal IR. (Reviewable point.)
- **Home (three modules, dictated by layering).** Because `marivo.semantic`
  imports from `marivo.datasource`, the shared base cannot live in the semantic
  layer — a datasource subclass extending it would create a
  `datasource -> semantic` import cycle. So:
  - **`marivo/refs.py` (new, root / below datasource):** the `SemanticRef` base
    plus the `SymbolKind` enum, which **moves here** from
    `marivo/semantic/ir.py`. Depends on no other Marivo layer.
  - **`marivo/semantic/refs.py` (new):** the seven semantic subclasses, the
    `make_ref(id, kind)` factory, and the `as_ref_id` / `as_ref` normalizers
    (these accept `SemanticObject`, so they belong in the semantic layer).
  - **`marivo/datasource/authoring.py` (modified):** `DatasourceRef` extends
    `marivo.refs.SemanticRef`.

  `marivo.semantic.ir` re-imports `SymbolKind` from `marivo.refs`; the public
  `SemanticKind` alias is unchanged. This also extracts the ref family out of
  the ~1900-line `catalog.py`.

### What `catalog.get(...).ref` and `.children` return

The **matching subclass** (`MetricRef` for a metric, `DimensionRef` for a
dimension, `DatasourceRef` for a datasource, …), not a generic base. The ~30
`SemanticRef(...)` construction sites in `catalog.py` collapse to a single
`make_ref(id, kind)` factory that dispatches to the right subclass for **all
eight** `SymbolKind` values (including the catalog-only `DatasourceRef`).
`SemanticObject.ref` and `.children` are typed to the family.

### Input unions

- `SemanticRefInput = SemanticRef | str` keeps its shape but now resolves to
  `str | <authoring family>`, matching the committed Unified Reference Model.
- `SemanticInput = SemanticObject | SemanticRef` and the
  `MetricInput` / `DimensionInput` aliases **auto-accept authoring refs**,
  because every subclass is a `SemanticRef`. `observe`, `compare`, `decompose`,
  etc. need no signature changes.
- `SemanticAnchorInput = str | SemanticRef | SemanticObject` is unchanged.
  `PromotionSemanticAnchors` / `CommitSemanticAnchors` keep working through the
  base's preserved Pydantic schema.

### Normalizers

`_resolve_ref_string` (authoring), `_to_ref_str` (read), and
`_ref_and_kind` (resolver) collapse into one shared helper in
`marivo/semantic/refs.py`: `as_ref_id(value) -> str`, accepting
`SemanticRef | SemanticObject | str`. It
**remains string-tolerant** — internal callers that pass raw ids keep working
unchanged (`observe_planner` calling `resolver.dimension_on(field_id, table)`,
`resolver.table("sales.orders")` in `tests/test_semantic_resolver.py`), and the
resolver method signatures keep `... | str`. What improves: a unified, now
kind-bearing ref is no longer stringified into a kind-less path — when a typed
ref is passed, its kind flows through (`_ref_and_kind` reads it from the
subclass) instead of being dropped. Only raw strings yield `kind=None`, exactly
as today.

## Change Inventory (areas, not exhaustive)

- **New** `marivo/refs.py` (base `SemanticRef` + `SymbolKind`, moved from
  `ir.py`) and **new** `marivo/semantic/refs.py` (seven semantic subclasses +
  `make_ref` + `as_ref_id` / `as_ref`). **Remove** `_BaseRef` and the
  `SemanticRef` dataclass.
- `marivo/datasource/authoring.py`: `DatasourceRef` extends
  `marivo.refs.SemanticRef`; `.name` / `.semantic_id` -> `.id`,
  `kind=DATASOURCE`; update its construction and `.name` call sites.
- `catalog.py`: construction -> `make_ref` factory; ref string access
  (`_format_ref`, `_format_refs`, `_to_ref_str`, …) `.ref` -> `.id`;
  `SemanticObject.ref` / `.children` typed to the family.
- `ir.py`: re-import `SymbolKind` from `marivo.refs`; ref classes move out;
  `*IR.semantic_id` fields stay.
- `authoring.py`: import refs from `refs.py`; ref-object `.semantic_id` -> `.id`
  (IR access untouched); normalizer -> shared `as_ref_id`.
- `resolver.py`: `_ref_and_kind` handles the family directly (kind from
  subclass), no `str()` fallback.
- `analysis/help.py`: collapse the `SemanticRef` + `_BaseRef` dual dispatch to
  one branch.
- `analysis/intents/` (`observe.py`, `select.py`, `semantic_inputs.py`):
  construct via the factory / subclasses.
- Docs: update `python-semantic-layer.md` (ref/decorator contract) and the
  *Unified Reference Model* section of `stepwise-authoring-design.md` to the
  realized type model.
- `tests/test_public_surface.py` snapshot: `SemanticRef` (base) and the eight
  subclasses are the public ref family. `DatasourceRef` already exists publicly
  but is reshaped (now a `SemanticRef` subclass), so its `__repr__` and identity
  change; update the snapshot accordingly.
- Tests/examples constructing refs directly (e.g.
  `references/examples/08_discover_driver_axes.py`, `select.py`) move to the
  subclass / factory.

## Risks

- **Callable identity token in the analysis loop.** Mitigated by pre-wiring the
  resolver at catalog construction and the existing teaching error on misuse.
- **Hand-constructed wrong-kind/nonexistent ref type-checks.** Pre-existing
  property; convention remains that refs come from `ms.*` or `catalog.get`.
- **`.id` (ref) vs `.semantic_id` (IR) divergence.** Deliberate layer boundary;
  documented so the implementation plan does not over-rename IR fields.
- **`SymbolKind` relocation.** Moving the enum below the datasource layer is
  internal (the public `SemanticKind` alias is unchanged), but every
  `marivo.semantic.ir` importer of `SymbolKind` must resolve to the new home.
- **`DatasourceRef` reshape.** It gains the base's identity / eq / hash / str and
  loses its bespoke `.name`; `md.ref(...)` callers reading `.name` move to `.id`.

## Verification

- `make typecheck`, `make test`, `make examples-check` (examples exercise refs
  heavily), and the public-surface snapshot test.
- **No on-disk migration.** Refs are persisted as strings, not as ref objects
  (verified: the frame / evidence / policy serialization paths contain no
  `SemanticRef`). The merge is a code-level refactor only.
