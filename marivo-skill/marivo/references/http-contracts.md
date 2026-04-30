# Marivo HTTP Contract Reference

Use this file when the question is about **cross-surface HTTP rules**: transport semantics, session ownership, execution auth, request validation recovery, or invariants that apply to more than one Marivo domain.

Skip this file if the real task is choosing an investigation intent, modeling semantic objects, or troubleshooting readiness. Those belong in `steps.md`, `semantic-layer.md`, and `semantic-readiness.md`.

This file owns shared HTTP, session, and execution auth invariants. It does not own intent-specific guardrails or semantic lifecycle guidance.

Boundary reminder:

- keep session ownership, structured time-window rules, execution auth, and validation-recovery lookup here
- let `steps.md` own intent sequencing and state/context usage
- let `semantic-layer.md` and `semantic-readiness.md` own semantic modeling and availability decisions
- if another reference needs one of these shared invariants, link here instead of restating the full rule

## Contract Lookup Order

Use this lookup order:

1. use `SKILL.md` to choose the correct Marivo surface
2. use the matching tool for executable parameters and examples
3. follow server-supplied guidance links when validation or contract discovery requires it
4. use references only for mental-model clarification

## Transport Rules

- Marivo is HTTP-only
- many successful writes return `200 OK`; do not assume `201 Created` is the only success condition
- transport tooling may normalize responses, but canonical meaning still comes from the HTTP contract
- guided remediation fields may appear alongside the legacy `detail` array on validation failures

## Session Ownership Rules

- `{session_id}` in the path is authoritative for ownership
- keep downstream refs in the same session unless the contract explicitly says otherwise
- session root requests carry session context such as goal, budget, and policy
- per-intent execution scope still belongs in typed intent requests, not in the session root
- ending a session is also path-scoped: use `POST /sessions/{session_id}/terminate` against the owning session

## Execution Auth

Marivo supports engine-level authentication through a two-layer model:

### Engine Auth Configuration

Each engine declares its auth requirements:

- `auth.mode`: `none` or `username_only`
- `auth.username_source`: `session_user` or `fixed`
- `auth.fallback_username`: static username when `username_source=fixed`

Rules:

- `mode=none` does not allow `username_source` or `fallback_username`
- `mode=username_only` requires `username_source`
- `username_source=fixed` requires `fallback_username`
- DuckDB engines only support `mode=none`

### Session Execution Identity

Sessions carry execution identity:

- `execution_identity.session_user`: the authenticated user for engine routing
- `execution_identity.actor_ref`: the originating actor reference

Resolution order for Trino:

1. `session_user` from the session (when `username_source=session_user`)
2. `fallback_username` from the engine (when `username_source=fixed`)
3. fail with `session_user_missing` if neither is available

DuckDB ignores all auth fields entirely.

### Auth Failure Taxonomy

- `session_user_missing`: engine requires a session user but none was provided
- `engine_auth_invalid`: engine auth configuration is inconsistent (e.g., `mode=username_only` but no `username_source`)
- `engine_auth_unsupported`: engine type does not support the configured auth mode
- `session_execution_identity_invalid`: session identity fields are blank or malformed

### Audit Contract

- execution auth success events are audited with a deliberate delay to avoid race conditions with routing
- session auth routing boundaries are preserved: one session's identity does not leak to another

## Cross-Surface Invariants

- the path chooses the typed intent; do not invent a generic action discriminator
- canonical evidence reads belong on session state and proposition context surfaces
- runtime-status endpoints and jobs are operator-facing surfaces, not canonical evidence reads
- default runtime resolution should assume semantic objects that are both `lifecycle_status=active` and `readiness_status=ready`
- when the server returns guidance links, prefer those links over guessing
- after session termination, treat the session as read-only for writes and continue only with canonical read surfaces unless you create a new session

## Session Termination

Use explicit termination when the investigation is complete and you do not plan further writes in that session.

Rules:

- terminate through canonical HTTP `POST /sessions/{session_id}/terminate`
- prefer a terminal reason that matches the actual close-out, commonly `answered` or `user_closed`
- do not treat "agent stopped talking" as equivalent to lifecycle closure
- after termination, intent writes against that session should be considered invalid; session root, state, and proposition context reads remain valid

## Time Window Contract

Apply these rules anywhere a Marivo route accepts a time window, including `observe.time_scope`, `detect.time_scope`, `diagnose.time_scope`, `diagnose.current/baseline.time_scope`, and `attribute.left/right.time_scope`:

- use canonical structured objects, not shorthand strings
- for a range window, send `{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`
- Marivo range semantics are `[start, end)`: `start` is inclusive and `end` is exclusive
- if the business request is inclusive on both ends, advance the submitted `end` by one day before sending it
- keep this rule explicit when reporting results so you do not accidentally claim that the exclusive `end` day was observed
- for `detect` and `diagnose(mode="auto_detect")`, `time_scope.kind` must be `range` and the bucket size is top-level `granularity`
- `snapshot_now`, `latest_available`, and `as_of` are observe-only time-scope kinds; do not send them to `detect` or `diagnose`
- do not send removed detect/diagnose fields: `time_scope.mode`, `time_scope.current`, or `time_scope.grain`

Examples:

- correct: `{"kind":"range","start":"2026-04-01","end":"2026-04-19"}`
- meaning: covers `2026-04-01` through `2026-04-18`
- incorrect: `"2026-04-01 to 2026-04-19"`

## Validation Recovery

For request validation failures:

1. start with the matching tool description
2. read the structured guidance envelope when present: `code`, `message`, `category`, `field_path`, `guidance.docs_url`, `guidance.schema_url`, `guidance.contract_url`, `guidance.examples`, and `guidance.remediation`
3. use `guidance.examples` when present
4. use `guidance.schema_url` or equivalent schema lookup for the exact request model
5. use `guidance.contract_url` for route-scoped context
6. use `detail` locations to map the failure to a field path

Do not guess your way past a `422` when the contract already tells you how to repair it.

Common semantic authoring error codes:

- `semantic_ref_conflict`: the submitted semantic ref is already owned by a governed object; inspect
  `guidance.remediation.existing_object_id` before retrying
- `binding_target_kind_not_allowed_for_scope`: choose a target kind allowed by the binding scope
- `metric_input_semantic_ref_prefix_invalid`: legacy metric binding payloads use
  `semantic_ref=metric_input.<slot_or_name>`; new metrics should declare component
  `input_field_ref` instead
- `metric_input_target_key_invalid`: legacy metric binding payloads use the metric family slot
  name as `target.target_key`; new metrics should declare component `input_field_ref` instead
- `binding_primary_time_missing`: repair the entity-owned time field grounding, then point
  `time.*.source_field_ref` at that field or add the required relationship/profile alignment
- `binding_required_metric_input_missing`: legacy binding records are missing required metric input
  slots; new metrics should declare component `input_field_ref` values on the metric contract

For `409 semantic_ref_conflict` on `POST /semantic/metrics`:

1. read `guidance.remediation.existing_object_id`, `existing_lifecycle_status`, and
   `existing_revision`
2. inspect the existing metric before retrying
3. for spelling, description, or unit-label corrections, wait for/use the metric revision path
   instead of creating `metric.*_v2`
4. use a new metric ref only when the requested object is a different business semantic identity

Deprecated semantic objects still own their refs. Do not retry create with the same ref expecting
deprecation to release ownership.

## Common Missteps

- using old `/steps/*` routes instead of `/intents/*`
- assuming a public `/plans` API exists
- treating `/jobs` or runtime-status as canonical evidence state
- collapsing action, state, and context into one surface
- sending shorthand time windows to routes that require nested request objects
- forgetting that `range.end` is exclusive and then misreading either the request scope or the returned coverage
- omitting `execution_identity.session_user` when the engine requires `auth.mode=username_only` with `username_source=session_user`

## Read Next

- Read `steps.md` for intent behavior and state/context usage.
- Read `planning.md` for client-side orchestration over current HTTP primitives.
- Read `semantic-layer.md` or `semantic-readiness.md` for semantic availability questions.
