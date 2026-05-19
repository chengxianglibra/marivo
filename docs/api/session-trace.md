# Session Trace

Session trace is the agent-facing execution chronology for one analysis session.
It answers "what ran?" and deliberately does not answer "what should I conclude?"

Use `GET /sessions/{session_id}/trace` or the MCP `get_session_trace` tool to inspect steps, stable artifact handles, lightweight deterministic summaries, provenance, semantic metadata, and per-step trace warnings.

## HTTP

```http
GET /sessions/{session_id}/trace
```

The HTTP route requires caller identity. Requests without identity return `401`.
Requests for another user's private session return `403`.

Response schema:

```json
{
  "session_id": "sess_123",
  "goal": "Explain revenue change",
  "lifecycle_status": "active",
  "created_at": "2026-05-18T00:00:00+00:00",
  "updated_at": "2026-05-18T00:01:00+00:00",
  "steps": [
    {
      "step_id": "step_1",
      "step_type": "observe",
      "created_at": "2026-05-18T00:01:00+00:00",
      "summary": "Observed revenue",
      "artifact_id": "art_1",
      "output_summary": {
        "intent_type": "observe",
        "status": "success",
        "artifact_type": "observation",
        "row_count": 10
      },
      "provenance": { "runner": "observe" },
      "semantic_metadata": { "metric": "revenue" },
      "warnings": []
    }
  ],
  "artifact_ids": ["art_1"],
  "schema_version": "session_trace.v1"
}
```

`steps` are sorted by `created_at ASC, step_id ASC`. `artifact_ids` is a deduplicated list in first-seen trace order.

## Output Summary

Trace summaries are deterministic and shallow. Only these scalar fields may appear in `output_summary`:

- `intent_type`
- `step_type`
- `artifact_id`
- `status`
- `result_type`
- `artifact_type`
- `artifact_schema_version`
- `row_count`
- `candidate_count`
- `finding_count`
- `driver_count`

Artifact rows, AOI artifacts, driver rows, backing findings, assessments, proposition contexts, and large nested result payloads are not inlined.

## Warning Codes

- `artifact_id_unresolved`: the step has no stable artifact id in its result, and the artifact store fallback did not resolve one.
- `output_summary_unavailable`: the step result contains no whitelisted scalar summary fields.
- `provenance_missing`: `Step.provenance` is absent.
- `semantic_metadata_unavailable`: `Step.semantic_metadata` is absent.

Warnings are step-local. A warning on one step does not make the entire trace fail.

## Agent Workflow Contract

Before producing a final evidence-based answer, an agent must read:

1. `get_session_trace(session_id)` to understand which steps ran and which artifacts exist.
2. `get_session_state(session_id, ...)` to read active propositions, backing findings, blocking gaps, and artifact references.
3. `get_proposition_context(session_id, proposition_id)` for every proposition cited as evidence.

The trace explains execution. Session state and proposition context support conclusions. If trace warnings affect cited evidence, mention the relevant caveat instead of presenting the conclusion as fully verified.
