# OpenAPI Schema Hardening Follow-Ups

This document records API schema debt outside the current hardening phase.
The current strict OpenAPI quality gate applies only to `/semantic-models/**`
and the scoped `/sessions/**` lifecycle, state, runtime-status, proposition
context, and intent routes.

## Current Phase Exclusions

The following API groups have been closed in Waves 1–4 and are now fully
covered by the scoped quality gate:

- datasources (`/datasources/**`)
- routing (`/routing/**`)
- governance and approvals (`/policies/**`, `/quality-rules/**`, `/governance/**`, `/approvals/**`)
- jobs (`/jobs/**`)
- calendar (`/calendar/**`)

The following remain outside the quality gate and are deferred to Wave 5:

- `/engines/**` — engine inventory API (router not yet implemented)
- `/mappings/**` — mapping API (router not yet implemented)
- `/catalog` legacy and stub routes
- `/openapi/*` meta routes
- `/sessions/{session_id}/...` routes not listed in the scoped contract,
  such as planner-context surfaces

## Violation Categories To Eliminate

- `additionalProperties: true` from `dict[str, Any]`, `dict[str, object]`, and
  untyped response envelopes.
- Array schemas with missing or empty `items`.
- Schema leaves that only carry metadata such as `title`, `description`, or
  `default` without `type`, `$ref`, `oneOf`, `anyOf`, `allOf`, `enum`, or
  `const`.
- Response bodies typed as plain `dict` instead of named Pydantic response
  models.
- Request fields that expose Python implementation containers rather than
  stable public contract models.

## Proposed Hardening Order

1. Datasources and routing, because they are foundational setup surfaces for
   agents.
2. Governance and approvals, because agents need clear policy and decision
   contracts.
3. Jobs and metrics, because they expose operational state.
4. Calendar, because the data model is already mostly typed and should be
   straightforward to close.
5. `/catalog` legacy/stub routes, after deciding whether each route remains
   public.
6. `/openapi/*` meta routes, after the primary API surface is stable.

## Exit Criteria For Future Global Enforcement

- Every public request and response body uses a named Pydantic model or a typed
  scalar/list schema.
- No public OpenAPI schema contains `additionalProperties: true`.
- No public OpenAPI array schema has missing or empty `items`.
- No public OpenAPI schema leaf lacks a type, ref, composition, enum, or const.
- The scoped path filter in `tests/test_openapi_schema_quality.py` is removed.
