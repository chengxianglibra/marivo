---
name: marivo-analysis
description: Use when the task is to create or continue a Marivo investigation session, run analysis intents, read session state or proposition context, and close the session through the current stdio MCP tools.
---

# Marivo Analysis Skill

Use this skill for **current Marivo stdio MCP investigation work** only.

It owns session creation, bounded analysis intents, state/context reads, and explicit session
close-out. It does not own datasource discovery or reusable semantic authoring.

## What This Skill Owns

- creating or continuing a session
- choosing the first bounded analysis intent
- chaining follow-up intents with returned refs
- reading session state and proposition context
- terminating the session after final writes

## Choose The First Bounded Step

- metric and window are already known: `marivo-observe`
- abnormal window is still unknown: `marivo-detect` or `marivo-diagnose(mode="auto_detect")`
- current and baseline windows are already known: `marivo-diagnose(mode="explicit_compare")`
- two `observe` artifacts already exist: `marivo-compare`
- one compare artifact needs segment drivers: `marivo-decompose`
- you need the session-level picture: `marivo-get_session_state` or `marivo-query_session_state`
- you need one proposition's local evidence closure: `marivo-get_proposition_context`

## Default Operating Loop

1. Create a new session for a new write flow with `marivo-create_session`.
2. Run one bounded intent.
3. Read state after each meaningful branch point.
4. Read proposition context only for the proposition that matters.
5. Offer one bounded next action instead of auto-running the whole investigation.
6. Terminate the session explicitly when no more writes are needed.

## High-Value Guardrails

- `time_scope.end` is exclusive.
- For `marivo-observe`, choose **either** `granularity` **or** `dimensions`.
- Read state first, then proposition context only when a specific claim needs explanation.
- Use returned refs for downstream tools such as `marivo-compare` or `marivo-decompose`; do not
  invent ad hoc step ids.
- Terminate the session explicitly with `marivo-terminate_session` after the final write step.

## Common Mistakes

- starting a fresh session for every tiny follow-up instead of continuing the active one
- mixing grouped and time-series output in one `marivo-observe` call
- treating session lists or runtime-ish status as a substitute for state or proposition context
- finishing the reasoning mentally but forgetting to terminate the active session

## Read Next

- `references/workflow.md` for bounded analysis flows and step chaining
- `marivo-semantic-layer` if the investigation exposes missing reusable semantic objects
