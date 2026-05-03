# Superpowers Documentation

This directory holds **ephemeral** design specs and implementation plans
produced during active development. Documents here are working artifacts, not
canonical design records.

## Lifecycle

```
draft spec (specs/)
    ↓  design stabilizes, reviewed
canonical spec (spec/)               ← authoritative, long-lived
    ↓
implementation plan (plans/)
    ↓  code merged, tests pass
archive (docs/archive/superpowers/)  or delete (code is the record)
```

### Rules

1. **`specs/` is a draft zone.** When a design stabilizes, merge its decisions
   into the canonical location (`spec/`, `docs/api/`, etc.) and delete or
   archive the draft.
2. **`plans/` are temporary.** Once the implementation is complete, move the
   plan to `docs/archive/superpowers/` or delete it.
3. **No duplication.** A design should not exist in both `specs/` and `spec/`
   at the same time. The canonical location is the authority.
4. **Same spec + plan pair.** Each implementation cycle typically produces one
   spec (the "what") and one plan (the "how"). They share the same date prefix.

## Frontmatter Convention

All documents in this directory should include YAML frontmatter:

```yaml
---
status: draft | canonical | completed | archived
canonical-path: spec/semantic/entity-schema-contract.zh.md  # if merged
created: 2026-05-02
---
```

| Status | Meaning |
|--------|---------|
| `draft` | Actively being designed; not yet stable |
| `canonical` | Decisions have been merged into the canonical location |
| `completed` | Implementation is done; pending archive |
| `archived` | Moved to `docs/archive/superpowers/` |

## Current Status

| Spec | Status | Canonical Path |
|------|--------|---------------|
| `2026-04-29-calendar-data-policy-redesign-design.md` | completed | spec/semantic/ (pending update) |
| `2026-04-30-datasource-merge-design.md` | completed | spec/service/data-plane/ (pending update) |
| `2026-04-30-osi-alignment-design.md` | archived | superseded by v2 |
| `2026-04-30-osi-alignment-v2-design.md` | completed | spec/semantic/ (pending update) |
| `2026-05-01-openapi-schema-contract-hardening-design.md` | canonical | docs/api/openapi.md |
| `2026-05-01-semantic-layer-update-modes-design.md` | canonical | docs/api/semantic.md |
| `2026-05-02-api-schema-hardening-design.md` | canonical | docs/api/ |
| `2026-05-02-dataset-native-grounding-design.md` | canonical | docs/api/semantic.md |

### Canonical Gaps

The following `spec/semantic/` documents still describe the pre-v2 binding model
and need updating to reflect dataset-native, OSI-aligned grounding:

- `spec/semantic/overview.md` — references typed binding layer and `source_objects`
- `spec/semantic/typed-binding-contract.zh.md` — describes carrier/field/time bindings (removed)
- `spec/semantic/entity-centric-object-model.zh.md` — describes entity binding (replaced by dataset-native grounding)
- `spec/service/data-plane/source-engine-mapping-golden-cases.zh.md` — describes old source/engine/mapping model

These gaps should be resolved before the corresponding superpowers specs can be
fully archived.
