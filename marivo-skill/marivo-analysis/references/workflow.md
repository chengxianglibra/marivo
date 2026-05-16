# Marivo Analysis Workflow Reference

Use this file when the task is about **running or continuing a Marivo investigation** through the
current stdio MCP tools.

Skip this file if the real task is still datasource setup or semantic modeling.

## Tool Routing

| Need | Tool |
| --- | --- |
| Start a new investigation | `marivo-create_session` |
| Inspect session metadata | `marivo-get_session`, `marivo-list_sessions` |
| Quick intent sanity check | `marivo-test_intent` |
| Measure one metric | `marivo-observe` |
| Scan for anomalies | `marivo-detect` |
| Run a diagnosis on the current hypothesis | `marivo-diagnose` |
| Compare two observe results | `marivo-compare` |
| Break down a compare result | `marivo-decompose` |
| Attribute between slices | `marivo-attribute` |
| Run correlation or forecasting | `marivo-correlate`, `marivo-forecast` |
| Read session-level evidence state | `marivo-get_session_state`, `marivo-query_session_state` |
| Read one proposition closure | `marivo-get_proposition_context` |
| Close the write flow | `marivo-terminate_session` |

## Minimal Session Start

```text
marivo-create_session(
  goal="Understand why watch time dropped for US mobile users last week"
)
```

Use the returned `session_id` for every follow-up write or read in the same investigation.

## Semantic Preflight

Before formal analysis starts, confirm all of the following:

- the metric or slice you want to study is backed by an approved semantic contract
- the time field and window semantics are already agreed
- the semantic model is ready for reuse

If any of these checks fail, stop the investigation and return to `marivo-semantic-layer` instead
of compensating with ad hoc filters, joins, or one-off explanations.

## Minimal `observe` Example

```text
marivo-observe(
  session_id="sess_123",
  metric="watch_time_seconds",
  time_scope={
    "field": "event_time",
    "start": "2026-05-05",
    "end": "2026-05-12"
  },
  granularity="day"
)
```

Grouped follow-up:

```text
marivo-observe(
  session_id="sess_123",
  metric="watch_time_seconds",
  time_scope={
    "field": "event_time",
    "start": "2026-05-05",
    "end": "2026-05-12"
  },
  dimensions=["country", "platform"]
)
```

Do not send `granularity` and `dimensions` together.

`marivo-correlate` and `marivo-forecast` only accept committed `observe(time_series)` artifact IDs.
Produce those by calling `marivo-observe` with `granularity` and without `dimensions`; scalar and
grouped observe artifacts are invalid for correlation or forecasting.

## Minimal `detect` Example

```text
marivo-detect(
  session_id="sess_123",
  metric="watch_time_seconds",
  time_scope={
    "field": "event_time",
    "start": "2026-04-15",
    "end": "2026-05-15"
  },
  granularity="day",
  sensitivity="balanced"
)
```

Use `detect` when the abnormal window is still unknown.

## Minimal `diagnose` Example

Use explicit compare when both windows are already known:

```text
marivo-diagnose(
  session_id="sess_123",
  metric="watch_time_seconds",
  candidate_dimensions=["country", "platform"],
  mode="explicit_compare",
  current={
    "time_scope": {
      "field": "event_time",
      "start": "2026-05-05",
      "end": "2026-05-12"
    }
  },
  baseline={
    "time_scope": {
      "field": "event_time",
      "start": "2026-04-28",
      "end": "2026-05-05"
    }
  }
)
```

## Artifact-Chaining Examples

Compare two earlier observe artifacts:

```text
marivo-compare(
  session_id="sess_123",
  left_artifact_id="art_obs_current",
  right_artifact_id="art_obs_baseline",
  compare_type="normal"
)
```

Decompose the resulting comparison:

```text
marivo-decompose(
  session_id="sess_123",
  compare_artifact_id="art_compare_1",
  dimension="platform"
)
```

Correlate two earlier time-series observe artifacts:

```text
marivo-correlate(
  session_id="sess_123",
  left_artifact_id="art_obs_watch_time_daily",
  right_artifact_id="art_obs_errors_daily",
  method="spearman"
)
```

Forecast from one earlier time-series observe artifact:

```text
marivo-forecast(
  session_id="sess_123",
  source_artifact_id="art_obs_watch_time_daily",
  horizon=7,
  profile="auto"
)
```

Use the actual returned artifact IDs from previous tool results. Do not invent them.

## Planner-Led Investigation Loop

Once semantic preflight passes, the agent may plan and execute a multi-step investigation inside the
same session. A typical flow can chain:

1. `marivo-observe` or `marivo-detect` to establish the current shape of the problem
2. `marivo-diagnose`, `marivo-compare`, or `marivo-decompose` to test the leading hypothesis
3. `marivo-attribute`, `marivo-test_intent`, `marivo-correlate`, or `marivo-forecast` when the current
   evidence needs a narrower check

Read `marivo-get_session_state` after meaningful branch points. Read
`marivo-get_proposition_context` only for the proposition that now matters. If the evidence points
to a reusable semantic gap instead of an analytical branch, pause the session work and repair the
semantic layer first.

## State, Context, And Close-Out

Read the session-level picture first:

```text
marivo-get_session_state(session_id="sess_123")
```

Then inspect one proposition only when it matters:

```text
marivo-get_proposition_context(
  session_id="sess_123",
  proposition_id="prop_456"
)
```

Close the active write flow explicitly:

```text
marivo-terminate_session(
  session_id="sess_123",
  terminal_reason="answered"
)
```

## Common Mistakes

- continuing to write after the session should already be closed
- using the analysis session to settle reusable metric definitions that should have been approved in
  the semantic layer
- trying to use datasource browse output as evidence for an investigation conclusion
- passing scalar or grouped observe artifacts into `marivo-correlate` or `marivo-forecast`
- skipping `marivo-get_session_state` and jumping straight to proposition context without knowing
  which proposition matters
