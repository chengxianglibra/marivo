# Superpowers Documentation

This directory holds **ephemeral** design specs and implementation plans
produced during active development. Documents here are working artifacts, not
canonical design records.

## Lifecycle

```
draft spec (specs/)
    ↓  design stabilizes, reviewed
canonical spec (specs/)              ← authoritative, long-lived
    ↓
implementation plan (plans/)
    ↓  code merged, tests pass
archive (docs/archive/superpowers/)  or delete (code is the record)
```

### Rules

1. **`specs/` is a draft zone.** When a design stabilizes, merge its decisions
   into the canonical location (`specs/`, `docs/api/`, etc.) and delete or
   archive the draft.
2. **`plans/` are temporary.** Once the implementation is complete, move the
   plan to `docs/archive/superpowers/` or delete it.
3. **No duplication.** A design should not exist in both draft `specs/` and canonical `specs/`
   at the same time. The canonical location is the authority.
4. **Same spec + plan pair.** Each implementation cycle typically produces one
   spec (the "what") and one plan (the "how"). They share the same date prefix.

## Frontmatter Convention

All documents in this directory should include YAML frontmatter:

```yaml
---
status: draft | canonical | completed | archived
canonical-path: specs/semantic/entity-schema-contract.zh.md  # if merged
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
| `2026-04-29-calendar-data-policy-redesign-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-04-30-datasource-merge-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-04-30-osi-alignment-design.md` | archived | superseded by v2 |
| `2026-04-30-osi-alignment-v2-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-01-openapi-schema-contract-hardening-design.md` | archived | docs/api/openapi.md |
| `2026-05-01-semantic-layer-update-modes-design.md` | archived | docs/api/semantic.md |
| `2026-05-02-api-schema-hardening-design.md` | archived | docs/api/ |
| `2026-05-02-dataset-native-grounding-design.md` | archived | docs/api/semantic.md |
| `2026-05-04-remove-analysis-sessions-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-06-marivo-platform-architecture-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-06-phase2-contracts-ports-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-06-unified-user-identity-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-07-aoi-v0.1-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-07-phase3-runtime-decoupling-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-07-phase4-local-embedded-runtime-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-07-phase6-profile-system-design.md` | archived | superseded by phase 6.1/6.2 |
| `2026-05-08-phase5-mcp-dual-mode-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-08-phase6.1-runtime-self-sufficiency-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-08-phase6.2-server-profile-boundary-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-09-phase6.3-contract-parity-tests-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-09-phase7-namespace-cutover-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-09-phase9-production-server-adapters-design.md` | archived | docs/archive/superpowers/specs/ |
| `2026-05-13-aoi-generated-model-runtime-cutover-design.md` | archived | docs/archive/superpowers/specs/ |

| Plan | Status | Canonical Path |
|------|--------|---------------|
| `2026-04-29-calendar-data-policy-redesign.md` | archived | docs/archive/superpowers/plans/ |
| `2026-04-30-datasource-merge.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-01-osi-alignment-v2.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-04-remove-analysis-sessions.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-06-phase2-contracts-ports.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-06-unified-user-identity.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-07-phase3-runtime-decoupling.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-07-phase4-local-embedded-runtime.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-08-aoi-schema-layout.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-08-aoi-spec-materialization.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-08-phase5-mcp-dual-mode.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-08-phase6.1-runtime-self-sufficiency.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-08-phase6.2-server-profile-boundary.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-09-legacy-package-drain.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-09-phase6.3-contract-parity-tests.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-09-phase7-namespace-cutover.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-09-phase9-production-server-adapters.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-11-aoi-runtime-boundary.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-11-osi-aoi-static-cutover.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-12-semantic-layer-mcp-surface.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-13-aoi-generated-model-runtime-cutover.md` | archived | docs/archive/superpowers/plans/ |
| `2026-05-13-semantic-layer-document-surface.md` | archived | docs/archive/superpowers/plans/ |

### Canonical Gaps

All previously identified canonical gaps have been resolved. The `specs/semantic/`
documents have been updated to reflect dataset-native, OSI-aligned grounding:

- `specs/semantic/overview.md` — rewritten for 3-layer OSI architecture
- `specs/semantic/typed-binding-contract.zh.md` — marked SUPERSEDED (binding layer deleted)
- `specs/semantic/entity-centric-object-model.zh.md` — marked SUPERSEDED (replaced by dataset-native grounding)
- `specs/semantic/compiler-compatibility-profile.zh.md` — marked SUPERSEDED (compatibility profiles deleted)
- `specs/semantic/process-object-schema.zh.md` — marked SUPERSEDED (process objects deleted)
- `specs/semantic/enum-set-schema-contract.zh.md` — marked SUPERSEDED (enum sets deleted)
- `specs/semantic/predicate-schema-contract.zh.md` — marked SUPERSEDED (predicates deleted)
- `specs/service/data-plane/source-engine-mapping-golden-cases.zh.md` — rewritten for datasource routing
- `specs/semantic/ir-schema-contract.zh.md` — updated for dataset-native grounding
- `specs/semantic/compiler-spec.zh.md` — updated for dataset-native grounding
- Remaining schema contract files — updated with transition notes
