# Dataset-Scoped Field IDs and Kind-Scoped Uniqueness

Date: 2026-06-02

Status: approved design, pending implementation plan.

## Context

Every semantic object in `marivo.semantic` (dataset, field, time_field, metric,
relationship) is identified by `semantic_id`, which is always built as
`f"{model}.{name}"` — a single flat namespace per model with no separation by
owning dataset or object kind. The construction is hard-coded in the authoring
decorators (`marivo/semantic/authoring.py:366`, `:439`, `:526`, `:624`, `:700`,
`:806`), and the duplicate check `_check_duplicate`
(`marivo/semantic/authoring.py:113`) compares `semantic_id` across **all**
pending objects regardless of kind.

This breaks down as soon as a model spans multiple datasets that share column
names. Three concrete failures observed while building a semantic layer over
seven datasets:

1. **Same column name across datasets (same kind).** Seven datasets each expose
   a `log_date` column, so seven `time_field` declarations all resolve to
   `<model>.log_date` and collide. Today they must be hand-suffixed
   (`log_date_7d_summary`, `log_date_7d_portrait`, …). The same happens to 24
   portrait/intent dimension fields shared across three datasets.
2. **Dataset name vs metric name (cross kind).** A dataset `dau_7d_portrait` and
   a metric `dau_7d_portrait` (sum of a `dau` column) both resolve to
   `<model>.dau_7d_portrait` and the project fails to load.
3. The workarounds (manual suffixes, renames) are noise the author should not
   have to carry.

### Root cause

Two independent defects share one symptom:

- **No dataset scope for fields.** `field` / `time_field` ids are
  `<model>.<name>`, not `<model>.<dataset>.<name>`. Fields are stored in a single
  `Registry.fields` dict (`marivo/semantic/validator.py:59`), so identical column
  names in different datasets are a true same-dict collision.
- **Kind-blind uniqueness.** `_check_duplicate` flags any matching `semantic_id`
  even though the registry already separates objects into per-kind dicts
  (`datasets`, `fields`, `metrics`, `relationships` at
  `marivo/semantic/validator.py:56`). A dataset and a metric can never collide in
  the registry; they only collide in the author-time check.

### The fix is already specified

`docs/specs/semantic/python-semantic-layer.md` already prescribes the target ID
contract; the implementation simply never adopted it:

- `python-semantic-layer.md:652` — `ms.ref(...)` is fixed as
  `"<kind>.<fully-qualified-id>"`, kind in {datasource, dataset, field,
  time_field, metric, relationship}.
- `python-semantic-layer.md:655` — fields are dataset-scoped:
  `ms.ref("field.sales.orders.user_id")` (i.e. `<model>.<dataset>.<field>`).
- `python-semantic-layer.md:654`, `:656` — datasets and metrics stay
  `<model>.<name>`; kind is a separate disambiguator.
- `python-semantic-layer.md:495` — relationship endpoints use field refs, not
  bare physical column strings.

So this change is **spec conformance**, not a new scheme. Per the agent guide
("committed specs are sources of truth"), we adopt the documented contract.

## Decisions

Three forks were resolved during design:

1. **Scope: align to the spec ID scheme.** Adopt dataset-scoped field ids plus
   kind-scoped uniqueness. Touch only the ID / namespace layer. Defer the broader
   spec target-state (`ms.ref("<kind>.<fqn>")` prefix parsing/validation,
   `project.refactor.rename`, `potentially_fragile_reference` check, cross-model
   ref checks).
2. **Migration: greenfield hard cut.** No compatibility layer and no migration
   tooling. The semantic layer is still under construction; there is no
   production persisted state to preserve. Existing hand-added suffixes revert to
   bare names (`log_date_7d_summary` → `log_date`); evidence is re-derived on
   demand.
3. **Namespace model: two-tier (Approach A).** Fields/time_fields are scoped to
   their dataset; datasets/metrics/relationships are unique within their own
   kind, so a dataset and a metric may share an FQN. The only companion change is
   making unqualified by-id lookup (`describe` / `_find_ir`) kind-aware.

Approaches B (scope fields only, keep top-level objects globally unique) and C
(embed kind into `semantic_id`) were rejected: B leaves the dataset-vs-metric
collision requiring a manual rename and diverges from the spec's kind-as-
disambiguator design; C changes every id, double-encodes kind (the registry
already separates by kind), and contradicts the spec's kind-free FQN.

## ID contract

`semantic_id` does **not** carry kind. Kind disambiguation continues to come from
the per-kind registry dicts and `ref.kind`, matching the spec's
`refactor.rename(kind, fqn, ...)` signature.

| kind                     | `semantic_id`                  | uniqueness boundary                       |
| ------------------------ | ------------------------------ | ----------------------------------------- |
| model                    | `<model>`                      | project                                   |
| dataset                  | `<model>.<dataset>`            | `datasets` dict                           |
| field / time_field       | `<model>.<dataset>.<field>`    | `fields` dict (field + time_field shared) |
| metric / derived_metric  | `<model>.<metric>`             | `metrics` dict (metric + derived shared)  |
| relationship             | `<model>.<relationship>`       | `relationships` dict                      |

Rules:

- A field id is derived from its owning dataset:
  `f"{dataset.semantic_id}.{field_name}"`. Consequently the field's model always
  equals the dataset's model. If `@ms.field(model_name=...)` /
  `@ms.time_field(model_name=...)` disagrees with the dataset's model, raise a
  decorator error.
- `FieldIR.name` stays the bare field name (`log_date`). The reader invariant
  `name == semantic_id.split(".")[-1]` (`marivo/semantic/reader.py:218`) remains
  satisfied automatically.
- `_model_of(semantic_id)` = first dotted segment (`marivo/semantic/ledger.py:256`)
  still returns the model for the now-three-segment field ids.

### Uniqueness rule

`_check_duplicate` compares `semantic_id` **only within the same target
collection**, i.e. among pending objects of the same IR type:

- `DatasetIR` → datasets
- `FieldIR` → fields (covers both field and time_field)
- `MetricIR` → metrics (covers both base metric and derived_metric)
- `RelationshipIR` → relationships

`dau.dau_7d_portrait` (dataset) and `dau.dau_7d_portrait` (metric) therefore
coexist. Seven `log_date` time_fields become
`dau.<dataset>.log_date` and never collide.

### Ambiguous lookup

Because identical FQNs may now exist across kinds, unqualified by-id lookup is
ambiguous. `_find_ir(name, reg, kind=None)`:

- `kind` given → look up only the matching collection.
- `kind=None` → collect matches across all collections: 0 → `None`; 1 → the
  unique match (behavior unchanged for the common case); >1 → raise a structured
  `ambiguous_reference` error.

The `ambiguous_reference` error carries the full candidate set
`candidates=[(kind, semantic_id), …]` so the caller knows exactly what to
disambiguate to. `describe(...)` gains an optional `kind=` and forwards it.
`search(kind=...)` is already kind-aware and is unchanged.

## Implementation surface

### A. `marivo/semantic/authoring.py` (core)

- `field()` / `time_field()`: build `semantic_id = f"{ds_ref}.{obj_name}"` where
  `ds_ref` is the resolved dataset semantic_id. Validate that the field's model
  equals the dataset's model; raise on mismatch.
- `dataset()` / `metric()` / `derived_metric()` / `relationship()`: id
  construction unchanged (`<model>.<name>`).
- `_check_duplicate(ctx, semantic_id)` → `_check_duplicate(ctx, semantic_id,
  ir_type)`; compare only against pending objects where `isinstance(ir,
  ir_type)`. Each call site passes its IR type.
- `ms.ref()` stays a passthrough. For this change, string field refs use the
  bare three-segment FQN (`dau.dau_7d_portrait.log_date`). The kind-prefixed
  `ms.ref("field.…")` form remains deferred.

### B. `marivo/semantic/reader.py`

- `_find_ir(name, reg)` → `_find_ir(name, reg, kind=None)` implementing the
  ambiguity rule above. `_find_ir` is only used by `describe`
  (`marivo/semantic/reader.py:1262`), so the blast radius is contained.
- `describe(name, *, kind: SymbolKind | None = None, ...)`: forward `kind`.
  Reuse `_ir_kind` (`marivo/semantic/reader.py:1771`) to label candidates.

### C. `marivo/semantic/errors.py` and `marivo/semantic/constraints.py`

- Add `ErrorKind.AMBIGUOUS_REFERENCE` with structured fields `name` and
  `candidates`.
- Add `ConstraintId.AMBIGUOUS_REFERENCE` with agent-facing text ("pass `kind=`
  to disambiguate").
- Update `UNIQUE_SEMANTIC_NAME` (`marivo/semantic/constraints.py:226`) `why` /
  `hint`: uniqueness is per-kind, and fields are dataset-scoped.

### D. `marivo/semantic/validator.py` — confirmed correct, no change

- Relationship `from_fields` / `to_fields` resolve by full field id against
  `registry.fields` (`marivo/semantic/validator.py:766`). Authored as `FieldRef`
  objects (the spec's recommended style), the ref's `semantic_id` automatically
  becomes the new scoped id, so relationships keep resolving.
- `required_prefix` already supports a dataset-relative bare-name fallback
  (`marivo/semantic/validator.py:94`), which becomes more ergonomic under
  scoping (`required_prefix="log_date"` resolves within the dataset).
- Versioning `valid_from` / `valid_to` resolve via `registry.fields.get(id)` plus
  `field.dataset == ds_id` (`marivo/semantic/validator.py:477`); `FieldRef`-based
  authoring stays correct.
- There is no assembly-time uniqueness check; uniqueness lives only in the
  decorator layer, so nothing here changes.

### E. `marivo/semantic/ledger.py` — greenfield, no migration

- Evidence files `objects/<semantic_id>.json` (`marivo/semantic/ledger.py:295`)
  are written under the new ids naturally. `_model_of` still returns the model
  (first segment). No migration script; stale evidence is re-derived on demand.

### F. Examples, skills, docs (required by the agent guide)

- Audit `marivo-skills/marivo-*/references/examples/` for any example that prints
  or references a field `semantic_id` or calls `describe`; update to scoped ids /
  pass `kind` where needed.
- Re-check `docs/specs/semantic/python-semantic-layer.md` (already the target
  state) for any residual flat-id counter-example; sync
  `marivo-skills/marivo-semantic/references/authoring-patterns.md`. Remove any
  guidance that tells authors to hand-suffix duplicate field names.

### G. Tests

- Update existing tests that assert flat field ids to the scoped form.
- Add:
  1. Two datasets with the same column name → distinct scoped ids, no collision.
  2. A dataset and a metric with the same name → both load successfully.
  3. `describe` on an ambiguous FQN → raises `ambiguous_reference` carrying all
     candidates.
  4. A relationship across scoped fields authored with `FieldRef` → resolves.
  5. `required_prefix` as a bare name → resolves within the dataset.
- Reuse `tests/conftest.py` / `tests/shared_fixtures.py`.

## Success criteria

- `make test` green; `make typecheck` and `make lint` clean.
- A multi-dataset fixture that mirrors the observed failures (≥2 datasets sharing
  a `log_date` column, plus a dataset and a metric named `dau_7d_portrait`) loads
  with **zero manual suffixes or renames**.
- The `describe` ambiguity path is covered by a test.

## Out of scope (deferred)

These are part of the spec's broader target-state but are not in this change:

- `ms.ref("<kind>.<fqn>")` prefix parsing and validation.
- `project.refactor.rename(kind, old_fqn, new_fqn, write=...)`.
- `check` flagging string refs as `potentially_fragile_reference`.
- Cross-model ref existence / cycle / contract checks beyond what already exists.
- Any migration or dual-id compatibility tooling (greenfield hard cut).
