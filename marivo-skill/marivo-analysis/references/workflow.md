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
| Read holiday or adjusted-workday rows | `marivo-list_calendar_data` |
| Add or correct trusted holiday rows | `marivo-update_calendar_data` |
| Read execution trace and artifact handles | `marivo-get_session_trace` |
| Read session-level evidence state | `marivo-get_session_state` |
| Read one proposition closure | `marivo-get_proposition_context` |
| Generate HTML audit report | `marivo-export_report` |
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

## Holiday-Aware Calendar Preflight

When the user asks for holiday-aligned, holiday-aware, festival-window, named-holiday, or similar
calendar-aware comparison, or when they simply need known holiday information, check MCP calendar
data before running the formal comparison or answering from memory.

Use this sequence:

1. Identify the current and baseline windows that will be compared.
2. Call `marivo-list_calendar_data` for the combined half-open date range covering both windows.
3. If required holiday rows are missing, call `marivo-update_calendar_data` with trusted sparse
   `holiday` or `adjusted_workday` rows.
4. Call `marivo-list_calendar_data` again to confirm the rows exist.
5. Continue with `marivo-observe` and then `marivo-compare` using `compare_type="holiday_aligned"`
   or `compare_type="holiday_and_weekday_aligned"`.

Do not invent holiday data. If the missing rows are not available from the user, project materials,
or another clearly traceable source, stop the holiday-aware comparison and report the blocker.

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
  strategy="point_anomaly",
  sensitivity="balanced"
)
```

Use `detect` when the abnormal window is still unknown.

## Minimal `diagnose` Example

Use `diagnose` when you want auto-detect plus follow-up decomposition in one call:

```text
marivo-diagnose(
  session_id="sess_123",
  metric="watch_time_seconds",
  dimensions=["country", "platform"],
  strategy="point_anomaly",
  time_scope={
    "field": "event_time",
    "start": "2026-04-15",
    "end": "2026-05-15"
  },
  granularity="day"
)
```

`diagnose` combines detect, compare, and decompose. Use it when the abnormal window is still unknown
and you want the service to drive the follow-up attribution automatically.

## Minimal `attribute` Example

Use `attribute` when both windows are already known:

```text
marivo-attribute(
  session_id="sess_123",
  metric="watch_time_seconds",
  dimensions=["country", "platform"],
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
  current_artifact_id="art_obs_current",
  baseline_artifact_id="art_obs_baseline",
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
  horizon=7
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

Read `marivo-get_session_state` after meaningful branch points. Before an evidence-based final
answer, read `marivo-get_session_trace` to verify the executed step timeline and artifact handles,
then read `marivo-get_session_state`, then read `marivo-get_proposition_context` only for cited
propositions. If the evidence points to a reusable semantic gap instead of an analytical branch,
pause the session work and repair the semantic layer first.

## Trace, State, Context, And Close-Out

Read the execution trace before final evidence synthesis:

```text
marivo-get_session_trace(session_id="sess_123")
```

Trace tells you what ran, which artifact IDs exist, and whether any step has trace warnings. It is
not proof that a conclusion is valid.

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
marivo-export_report(
  session_id="sess_123",
  output_path=".marivo/reports/sess_123.html"
)

marivo-terminate_session(
  session_id="sess_123",
  terminal_reason="answered"
)
```

After reading trace, state, and proposition context, complete the Evidence-Linked Report Checklist
(see SKILL.md) before delivering the final answer.

## Evidence-Linked Final Report Template

After completing the Evidence-Linked Report Checklist, structure the final answer so every
evidence-backed claim carries at least one artifact ID, proposition ID, or trace warning reference.

### Required structure

```text
## Findings

1. [Finding text] (art_obs_1, prop_3)
2. [Finding text] (art_compare_2)
3. [Hypothesis — no artifact reference] UNCONFIRMED

## Evidence Chain

- art_obs_1: daily watch_time_seconds for 2026-05-05..2026-05-12
- art_compare_2: current vs baseline comparison
- prop_3: "watch_time dropped 18% for US mobile" — confirmed by art_compare_2, art_decompose_4
- [Trace warning: step 3 had partial data; prop_3 may underestimate the drop]

## Session Close

Session sess_123 terminated with terminal_reason="answered".
```

### Rules

- Every numbered finding under "Findings" MUST include at least one parenthesized reference.
- If a finding has no artifact or proposition reference, label it "UNCONFIRMED" and keep it separate
  from evidence-backed findings.
- "Evidence Chain" maps each referenced artifact/proposition to what it produced.
- Trace warnings that affect conclusions MUST appear under "Evidence Chain".

## Common Mistakes

- continuing to write after the session should already be closed
- using the analysis session to settle reusable metric definitions that should have been approved in
  the semantic layer
- trying to use datasource browse output as evidence for an investigation conclusion
- skipping `marivo-get_session_trace` before final synthesis, so the answer cannot explain which
  steps and artifact handles support the cited state/context
- passing scalar or grouped observe artifacts into `marivo-correlate` or `marivo-forecast`
- skipping `marivo-get_session_state` and jumping straight to proposition context without knowing
  which proposition matters
- delivering a final answer with zero artifact IDs or proposition references — every evidence-backed
  claim must link to at least one artifact ID, proposition ID, or trace warning
