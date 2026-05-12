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
| Run a bounded diagnosis | `marivo-diagnose` |
| Compare two observe results | `marivo-compare` |
| Break down a compare result | `marivo-decompose` |
| Attribute or validate between slices | `marivo-attribute`, `marivo-validate` |
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

## Ref-Chaining Examples

Compare two earlier observe steps:

```text
marivo-compare(
  session_id="sess_123",
  left_ref={"step_id": "step_obs_current", "step_type": "observe"},
  right_ref={"step_id": "step_obs_baseline", "step_type": "observe"},
  mode="scalar"
)
```

Decompose the resulting comparison:

```text
marivo-decompose(
  session_id="sess_123",
  compare_ref={"step_id": "step_compare_1", "step_type": "compare"},
  dimension="platform"
)
```

Use the actual returned step ids from the previous tool result. Do not invent them.

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
- trying to use datasource browse output as evidence for an investigation conclusion
- skipping `marivo-get_session_state` and jumping straight to proposition context without knowing
  which proposition matters
