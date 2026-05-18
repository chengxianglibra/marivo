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
- reading session state and proposition context
- terminating the session after final writes

## Choose The First Analysis Step

- metric and window are already known: `marivo-observe`
- abnormal window is still unknown: `marivo-detect` or `marivo-diagnose`
- current and baseline windows are already known: `marivo-attribute`
- two `observe` artifacts already exist: `marivo-compare`
- one compare artifact needs segment drivers: `marivo-decompose`
- you need the session-level picture: `marivo-get_session_state`
- you need one proposition's local evidence closure: `marivo-get_proposition_context`

## Default Operating Loop

1. Create a new session for a new write flow with `marivo-create_session`.
2. Run semantic preflight: approved metric definition, approved time semantics, and ready semantic
   model.
3. Choose the first analysis intent.
4. Plan and execute a bounded multi-step investigation using returned artifact IDs and session state.
5. Read state after each meaningful branch point.
6. Read proposition context only for the proposition that matters.
7. If the investigation exposes a semantic gap or contract conflict, bounce back to
   `marivo-semantic-layer`.
8. Terminate the session explicitly when no more writes are needed.

## High-Value Guardrails

- `time_scope.end` is exclusive.
- Formal analysis starts only after semantic preflight passes.
- If the user needs holiday alignment or holiday information, call `marivo-list_calendar_data` to
  read known holiday rows; when trusted rows are missing, use `marivo-update_calendar_data` to
  upsert sparse `holiday` or `adjusted_workday` rows before the formal comparison.
- For `marivo-observe`, choose **either** `granularity` **or** `dimensions`.
- `marivo-correlate` and `marivo-forecast` require committed `observe(time_series)` artifact IDs
  produced by `marivo-observe(granularity=...)`; scalar or segmented observe artifacts are invalid.
- Read state first, then proposition context only when a specific claim needs explanation.
- Use returned artifact IDs for downstream tools such as `marivo-compare` or `marivo-decompose`;
  do not invent ad hoc IDs.
- Do not rewrite approved metric meaning, join logic, or exclusions inside the investigation loop.
- Terminate the session explicitly with `marivo-terminate_session` after the final write step.

## Common Mistakes

- starting a fresh session for every tiny follow-up instead of continuing the active one
- entering formal analysis before the metric contract or time semantics were approved
- mixing grouped and time-series output in one `marivo-observe` call
- running holiday-aware comparison without checking `marivo-list_calendar_data` for the relevant rows
- passing scalar or segmented observe artifacts into `marivo-correlate` or `marivo-forecast`
- using the investigation loop to improvise reusable metric definitions
- treating session lists or runtime-ish status as a substitute for state or proposition context
- finishing the reasoning mentally but forgetting to terminate the active session

## Read Next

- `references/workflow.md` for planner-led investigation flows and step chaining
- `marivo-semantic-layer` if the investigation exposes missing reusable semantic objects
