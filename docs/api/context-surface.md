# Context Surface

This document defines the target-state external HTTP contract for Marivo's proposition context surface.

It binds the canonical schema from [`spec/analysis/evidence-engine/schemas/context-surface-schema.md`](../spec/analysis/evidence-engine/schemas/context-surface-schema.md) to a stable HTTP resource. This is a target-state wire specification and does not describe or depend on the current implementation.

## Purpose

Use this endpoint when a client needs:

- the proposition-level minimal canonical closure for explanation and follow-up decisions
- the latest assessment basis for a single proposition
- the direct support, oppose, gap, inference, and provenance handles needed to decide whether to continue validation

Do not use this endpoint as:

- a compact summary or reflection feed
- an assessment history browser
- a generic projection container with `include_*`, `profile`, or token-budget knobs

## Canonical Resource

| Surface | Endpoint | Canonical payload |
|---------|----------|-------------------|
| Proposition context surface | `GET /sessions/{session_id}/propositions/{proposition_id}/context` | `PropositionContextView` |

The canonical target identity is fixed by path only:

- `session_id`
- `proposition_id`

Together they materialize the wire form of `PropositionContextQuery.proposition_ref`. The endpoint defines no request body, no projection profile, no paging controls, and no structured query variant.

This resource exposes externally visible canonical context only. It is not a runtime attempt or operator backlog endpoint.

## Endpoint

### `GET /sessions/{session_id}/propositions/{proposition_id}/context`

Returns the proposition-level minimal canonical closure as `PropositionContextView`.

Example:

```bash
curl -s "http://localhost:8000/sessions/sess_123/propositions/prop_456/context" | jq .
```

## Response Shape

The wire response is exactly the canonical `PropositionContextView` payload. It does not add transport fields.

```json
{
  "proposition": {
    "proposition_id": "prop_456"
  },
  "seed_entries": [],
  "relevant_findings": [],
  "latest_assessment": null,
  "blocking_gaps": null,
  "non_blocking_gaps": null,
  "applied_inference_records": null,
  "assessment_dependencies": null,
  "artifact_refs": [],
  "schema_version": "proposition_context_view.v1"
}
```

Wire invariants fixed by this contract:

- `proposition` always corresponds to the requested `session_id` and `proposition_id`
- authored and system-seeded propositions use the same endpoint and the same payload shape
- `schema_version` identifies the canonical context surface contract, not transport behavior
- the response contains no paging metadata, truncation metadata, or projection metadata
- the response must not expose a half-refreshed closure; `latest_assessment` and its proposal/explanation basis must come from the same externally visible proposition-local bundle

## Field Semantics

### Proposition And Minimal Closure Boundary

`proposition` is the only target object of the resource.

The returned closure must be sufficient to:

- explain what the proposition is about
- explain why the latest assessment is in its current state
- audit the direct support, oppose, gap, and inference inputs
- decide whether more validation work is warranted

The payload must not mix in:

- session findings unrelated to the target proposition
- superseded assessment members
- compact reflection summaries or narrative-only fragments

### `seed_entries` And `relevant_findings`

`seed_entries` and `relevant_findings` are intentionally separate fields.

- `seed_entries` is creation-time seed hydration in the canonical order of `proposition.seed_finding_refs`
- each `seed_entries[*].seed_ref` must be preserved verbatim, including role semantics such as `primary`, `secondary`, or `context`
- `seed_entries[*].finding = null` is allowed and exposes an unresolved seed ref without invalidating the proposition
- `relevant_findings` is the live finding set needed to explain `latest_assessment` and the direct finding inputs of `applied_inference_records`
- overlap between `seed_entries[*].finding` and `relevant_findings` is allowed and does not collapse the two fields
- `relevant_findings` is the committed latest-assessment closure, not the recompute candidate finding set used by the inference engine
- if `latest_assessment = null`, `relevant_findings` must be `[]`

### `latest_assessment` Closure

The endpoint always returns at most one assessment closure: the latest assessment for the proposition.

Fixed rules:

- `latest_assessment = null` means the proposition has not entered assessment yet
- if `latest_assessment = null`, then `blocking_gaps`, `non_blocking_gaps`, `applied_inference_records`, and `assessment_dependencies` must all be `null`
- if `latest_assessment` exists and there are no blocking gaps, `blocking_gaps` is `[]`
- if `latest_assessment` exists and there are no non-blocking gaps, `non_blocking_gaps` is `[]`
- if `latest_assessment` exists and there are no applied inference records, `applied_inference_records` is `[]`
- if `latest_assessment` exists and current inference does not depend on prior assessments, `assessment_dependencies` is `[]`
- `latest_assessment = null` is a canonical read result only; it does not by itself distinguish between untriggered, failed, or migration-blocked runtime states

Inclusion boundaries are fixed:

- `blocking_gaps`, `non_blocking_gaps`, and `applied_inference_records` may include members from `latest_assessment` only
- `assessment_dependencies` may include only the direct assessment inputs referenced by `applied_inference_records.input_assessment_ids`
- `assessment_dependencies` is a stable de-duplicated direct closure and must not recurse into older history

### `artifact_refs`

`artifact_refs` is the minimal provenance handle set for the returned evidence.

- members come only from the source artifacts of returned `seed_entries[*].finding` and `relevant_findings`
- the set is de-duplicated
- the field carries lookup handles only, not expanded provenance payloads
- assessment dependencies do not expand `artifact_refs` by themselves
- this surface must not expose semantic-ref-specific fields such as `metric_ref`, `process_ref`, `dimension_ref`, `time_ref`, `binding_ref`, or `semantic_ref`
- embedded canonical subject payloads may still carry typed semantic identifiers such as `metric.*` and `dimension.*` inside ordinary business fields like `subject_json.metric` or `subject_json.slice`
- if a caller needs semantic meaning, it must derive that from canonical findings / propositions, step lineage metadata, and the semantic contracts, not by adding semantic refs to this payload

## Ordering

Response ordering is stable and fixed by canonical object ordering:

- `seed_entries` follows the canonical order of `proposition.seed_finding_refs`
- `relevant_findings` follows the canonical `Finding` stable order
- `blocking_gaps` and `non_blocking_gaps` follow the canonical `EvidenceGap` stable order
- `applied_inference_records` follows the canonical `InferenceRecord` stable order
- `assessment_dependencies` follows the canonical `Assessment` stable order after stable de-duplication
- `artifact_refs` follow source finding order with stable de-duplication

## Unsupported Query Controls

This endpoint does not support:

- `profile`
- `mode`
- `include_*`
- `limit`
- `page_token`
- compact, audit, or token-budget variants

Clients must not treat unsupported query parameters as future-compatible projection knobs.

## Errors

This endpoint uses the standard error envelope from [`errors.md`](errors.md).

Common cases:

| Status | Scenario |
|--------|----------|
| `400` | malformed path parameter, unsupported query parameter, invalid request shape at the transport layer |
| `404` | session not found, proposition not found, proposition does not belong to the requested session |
| `500` | unexpected server-side failure while materializing the context view |

Error behavior is fixed as follows:

- a proposition that does not belong to the requested session returns `404`, not a cross-session redirect or fallback lookup
- unsupported projection-style query parameters such as `include_findings`, `profile`, `limit`, or `page_token` return `400`
- unresolved seed hydration does not return `404`; it is represented as `seed_entries[*].finding = null`

## Relationship To Session State

This document defines the proposition-level canonical read surface only.

For the session-level decision surface, use [`session-state.md`](session-state.md):

- `GET /sessions/{session_id}/state`
- `POST /sessions/{session_id}/state/query`

If a caller needs runtime progress or failure detail for the proposition publish path, that belongs to a separate operator-facing runtime status surface rather than this endpoint.

## Non-goals

This contract does not define:

- assessment history browsing
- compact or audit context projections
- paging, truncation, or continuation tokens
- runtime attempt or queue status
- cache headers or conditional request semantics
