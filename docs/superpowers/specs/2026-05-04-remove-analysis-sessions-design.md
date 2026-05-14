# Remove Analysis Sessions: Revision-Based Traceability

**Date**: 2026-05-04
**Status**: Draft

## Problem

Marivo has two independent session systems that don't interact:

- **Investigation sessions** (`sessions` table) — the actual analysis container for intents, evidence, and propositions. Does not reference semantic models.
- **Analysis sessions** (`analysis_sessions` table) — designed to freeze semantic model snapshots at session creation, but the snapshot mechanism is incomplete: it only stores lightweight `(model_name, revision)` references (not deep copies) and does not enforce revision consistency at query execution time.

The analysis session system adds complexity without delivering its intended value. The MCP layer was already removed in commit `0002af4`.

## Decision

Remove `analysis_sessions` and `session_semantic_snapshots` entirely. Rely on the existing `step_metadata.typed_semantic_snapshot` for post-hoc traceability.

## Traceability After Removal

Step-level `typed_semantic_snapshot` (already in place, no changes):

- Recorded after each intent execution in the `step_metadata` table
- Contains `resolved_metric_revision`, `entity_revision` (per entity_field_ref), `relationship_revision` (per relationship_ref)
- Captures the actual revision used during execution — the authoritative audit trail

Semantic model `revision` fields (already in place, no changes):

- Per-model `revision` counter, incremented on each import
- Per-object contract `revision` (metrics, datasets, relationships)
- Combined with step_metadata, answers "what definition was used at step N?"

No session-level snapshot is created at session creation time. This is intentional: if no steps have been executed, there is no analysis output that needs tracing.

## What Gets Deleted

### Database tables (`app/storage/schema.py`)

- `analysis_sessions`
- `session_semantic_snapshots`

### Backend service

- `app/semantic_service_v2/session.py` — entire file (`SessionService` class)

### Backend API

- `app/api/analysis_session.py` — entire file (3 endpoints: create, get, end)
- `app/api/router.py` — remove `analysis_session` import and router mount

### References to clean up

- `app/api/semantic_v2.py` — remove `add_model_to_snapshot` call (the `session_id` query parameter path)
- Any test files covering analysis session endpoints

### MCP layer

Already cleaned in commit `0002af4`. Verify no residual references in `marivo-mcp/`.

## Execution Steps

1. Delete DB schema definitions from `app/storage/schema.py`
2. Delete `app/semantic_service_v2/session.py`
3. Delete `app/api/analysis_session.py`
4. Update `app/api/router.py` — remove import and mount
5. Clean references in `app/api/semantic_v2.py` — remove snapshot-related code path
6. Clean or delete related test files
7. Verify `marivo-mcp/` has no residual references
8. Run `make test` to confirm no regressions

## What Does NOT Change

- `sessions` table (investigation sessions) — untouched
- `step_metadata.typed_semantic_snapshot` — untouched, this is now the sole traceability mechanism
- `semantic_models.revision` field — untouched
- Legacy contract-level revision tracking tables (`semantic_metric_contracts`, etc.) — outside this change; these tables were later removed from the active schema/runtime path
- All intent execution paths (observe, compare, decompose, etc.) — untouched
