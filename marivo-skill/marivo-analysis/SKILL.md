---
name: marivo-analysis
description: Use when the task is to create or continue a Marivo investigation session, run planner-led analysis intents, read session state or proposition context, and close the session through the current stdio MCP tools.
---

# Marivo Analysis Skill

Use this skill for **current Marivo stdio MCP investigation work** only.

It owns session creation, planner-led investigation flows, state/context reads, and explicit session
close-out. It does not own datasource discovery or reusable semantic authoring.

## What This Skill Owns

- creating or continuing a session
- checking semantic preflight before formal analysis starts
- choosing the first analysis intent
- chaining follow-up intents with returned artifact IDs
- planning and executing a multi-step investigation with those artifact IDs
- reading session trace before evidence-based close-out
- reading session state and proposition context
- terminating the session after final writes

## Choose The First Analysis Step

- metric and window are already known: `marivo-observe`
- abnormal window is still unknown: `marivo-detect` or `marivo-diagnose`
- current and baseline windows are already known: `marivo-attribute`
- two `observe` artifacts already exist: `marivo-compare`
- one compare artifact needs segment drivers: `marivo-decompose`
- you need the execution timeline and artifact handles: `marivo-get_session_trace`
- you need the session-level picture: `marivo-get_session_state`
- you need one proposition's local evidence closure: `marivo-get_proposition_context`

## Default Operating Loop

1. Create a new session for a new write flow with `marivo-create_session`.
2. Run semantic preflight: approved metric definition, approved time semantics, and ready semantic
   model.
3. Choose the first analysis intent.
4. Plan and execute a bounded multi-step investigation using returned artifact IDs and session state.
5. Read state after each meaningful branch point.
6. Before an evidence-based final answer, complete the Evidence-Linked Report Checklist.
7. Generate the HTML audit report with `marivo-export_report(session_id=..., output_path=".marivo/reports/<session_id>.html")`.
8. If the investigation exposes a semantic gap or contract conflict, bounce back to
   `marivo-semantic-layer`.
9. Terminate the session explicitly when no more writes are needed.

## High-Value Guardrails

- `time_scope.end` is exclusive.
- Formal analysis starts only after semantic preflight passes.
- If the user needs holiday alignment or holiday information, call `marivo-list_calendar_data` to
  read known holiday rows; when trusted rows are missing, use `marivo-update_calendar_data` to
  upsert sparse `holiday` or `adjusted_workday` rows before the formal comparison.
- For `marivo-observe`, choose **either** `granularity` **or** `dimensions`.
- `marivo-correlate` and `marivo-forecast` require committed `observe(time_series)` artifact IDs
  produced by `marivo-observe(granularity=...)`; scalar or segmented observe artifacts are invalid.
- `marivo-get_session_trace` explains what ran and which artifact handles exist; it is not the
  evidence conclusion surface.
- Read state first, then proposition context only when a specific claim needs explanation.
- For final answers, read trace before state/context and mention trace warnings when they affect
  cited evidence.
- Use returned artifact IDs for downstream tools such as `marivo-compare` or `marivo-decompose`;
  do not invent ad hoc IDs.
- Do not rewrite approved metric meaning, join logic, or exclusions inside the investigation loop.
- Terminate the session explicitly with `marivo-terminate_session` after the final write step.

## Evidence-Linked Report Checklist

You MUST complete these items before delivering an evidence-based final answer:

1. **Read trace** — call `marivo-get_session_trace` and note every artifact ID and any trace warnings.
2. **Read state** — call `marivo-get_session_state` and identify which propositions support the answer.
3. **Read proposition context** — call `marivo-get_proposition_context` for each proposition cited
   in the answer.
4. **Link every conclusion** — every factual claim in the final answer MUST reference at least one of:
   - an artifact ID (e.g., `art_808c90d05292`)
   - a proposition ID (e.g., `prop_456`)
   - a trace warning that affected the conclusion
5. **Flag unsupported claims** — if a conclusion has no artifact or proposition reference, explicitly
   label it as an unsupported hypothesis, not an evidence-backed finding.

## Common Mistakes

- starting a fresh session for every tiny follow-up instead of continuing the active one
- entering formal analysis before the metric contract or time semantics were approved
- mixing grouped and time-series output in one `marivo-observe` call
- running holiday-aware comparison without checking `marivo-list_calendar_data` for the relevant rows
- passing scalar or segmented observe artifacts into `marivo-correlate` or `marivo-forecast`
- using the investigation loop to improvise reusable metric definitions
- answering from memory or state alone without checking `marivo-get_session_trace` for the executed
  step/artifact timeline
- treating session lists or runtime-ish status as a substitute for state or proposition context
- finishing the reasoning mentally but forgetting to terminate the active session
- delivering a final answer with zero artifact IDs or proposition references — every evidence-backed
  claim must link to at least one artifact ID, proposition ID, or trace warning

## Read Next

- `references/workflow.md` for planner-led investigation flows and step chaining
- `marivo-semantic-layer` if the investigation exposes missing reusable semantic objects
