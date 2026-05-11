---
name: marivo
description: "Use Marivo for local MCP stdio analytics work: datasource setup and browsing, semantic-layer construction, typed analysis sessions, state/context reads, readiness troubleshooting, and governed investigation loops. Trigger when the user mentions Marivo, datasource configuration, semantic models, entities/datasets/metrics/mappings, typed analysis intents, session state/context, or wants structured evidence instead of ad-hoc SQL."
---

# Marivo Skill

Marivo is an agentic analytics system. This skill is for the **local MCP stdio workflow** only. Treat it as the decision layer for how to use the Marivo tools you already have in the current workspace.

Do not mix this skill with remote transport guidance. If the task is not about the local stdio workflow, leave this skill out.

## What This Skill Owns

This file owns only the minimum routing layer:

- whether Marivo is the right system for the task
- whether the task is about datasource setup, semantic-layer construction, or analysis
- which surface to use first
- the default investigation loop
- the highest-value guardrails and anti-patterns
- which reference file to read next

Do not turn this file into a tool inventory or a field-by-field schema manual.

## When To Use Marivo

Use this skill when the task involves any of these:

- configuring or inspecting a datasource
- browsing live schemas, tables, columns, or previews
- building or repairing semantic objects and their grounding
- creating reusable metrics, dimensions, predicates, relationships, or mappings
- starting or continuing a typed analysis session
- reading session state or proposition context
- troubleshooting readiness, routing, grounding, or execution auth
- preferring structured Marivo evidence over ad hoc SQL or free-form summaries

## Choose The Surface First

Start by choosing the correct Marivo surface:

- **Datasource surface**: register, inspect, or browse live source metadata
- **Semantic surface**: create or inspect reusable governed objects
- **Action surface**: submit typed intents to advance an investigation
- **State surface**: read the session-level decision picture
- **Context surface**: read one proposition's canonical closure
- **Infrastructure surface**: inspect health, sync, routing, mapping, engines, jobs, or auth

Use these routing rules:

- choose the datasource surface when the question is what data exists and how it is shaped
- choose the semantic surface when the same business concept should be reused across investigations
- choose the action surface when you need to measure, compare, detect, attribute, validate, or diagnose
- choose state, not context, for the session-level picture
- choose context, not state, for one proposition's local evidence closure
- choose infrastructure when the problem is reachability, sync, routing, grounding, or job progress rather than evidence interpretation

Keep these boundaries explicit:

- datasource browse inspects live metadata; it is not analysis evidence
- actions create evidence artifacts; they do not explain themselves
- state and context are canonical evidence reads
- runtime-status or jobs are operator surfaces, not canonical evidence
- semantic objects define reusable meaning; they are not session evidence
- mappings govern source-to-engine routing and catalog projection; they are not semantic physical grounding
- entity fields are the only physical grounding owner; downstream objects reference `entity.<entity>.field.<field>` rather than declaring physical columns

## Default Operating Loop

Use this loop for most work:

1. Confirm health or datasource reachability if the path is unclear.
2. Browse live datasource metadata.
3. Build or repair the semantic object graph from datasource grounding.
4. Create a session.
5. Start with a bounded typed intent, usually `observe` or `detect`.
6. Read session state.
7. If one proposition matters, read proposition context.
8. Submit one bounded follow-up intent or stop.
9. When no further writes are needed, explicitly terminate the session.

Practical heuristics:

- start with `detect` when anomaly discovery is the first task
- start with `observe` when you already know the metric and window
- use `diagnose(mode="explicit_compare")` when the current and baseline windows are already known
- use `diagnose(mode="auto_detect")` when the abnormal window is not known yet
- move to the semantic surface when the same business concept, grouping axis, or time contract should be reused
- author semantic objects entity-first: datasource browse -> dataset -> entity/fields -> time/dimension/predicate -> metric/relationship/mapping
- use `predicate.*` refs when you need governed, reusable filter semantics for metrics or request scopes
- use `relationship.*` and compatibility profiles for cross-entity blockers; do not add metric-owned grounding
- use the shortest valid structured time window and remember that range end is exclusive
- for `observe`, choose exactly one output shape per step: `granularity` for time series or `dimensions` for grouped comparisons
- for `detect` and `diagnose(mode="auto_detect")`, send structured range windows plus top-level `granularity`
- use atomic intents when you need tighter control over branching
- use derived intents only when the task already fits that bounded pattern
- typed intent `metric` parameters must use canonical refs such as `metric.watch_time`, not bare names
- stop investigations by closing the lifecycle explicitly, not by just ceasing tool calls
- after termination, treat the session as read-only and continue only with canonical read surfaces

## Datasource And Semantic Workflow

When the task is about datasource setup or semantic construction, keep the order tight:

1. register or select the datasource
2. browse schemas, tables, columns, and optionally preview rows
3. decide which fields become stable entity fields
4. create or repair the dataset and its grounding
5. add semantic fields, time objects, dimensions, predicates, and metrics
6. add relationships or mappings when the business concept crosses boundaries
7. check readiness before treating the graph as reusable
8. run one representative typed intent or preview-backed workflow

Rules of thumb:

- live browse first
- grounding second
- dependent metrics and relationships after fields exist
- readiness check before using the model for repeated analysis
- keep physical locators in entity fields, not in downstream semantic objects
- use mappings for source-to-engine routing, not for semantic grounding
- domain objects are discovery metadata only
- `lifecycle_status=active` does not imply `readiness_status=ready`

## Common Mistakes

Avoid these high-frequency errors:

- treating datasource browse or MCP summaries as analysis evidence
- treating runtime/status or jobs as canonical evidence state
- treating `get_session_state` as a list of executed steps or artifacts
- treating derived intents as an open-ended planning engine
- guessing request payloads when the tool guidance already gives the shape
- sending shorthand time windows where a structured range is required
- reading `range.end` as inclusive
- finishing the analysis mentally but leaving the session open
- omitting `execution_identity.session_user` when the target engine requires authenticated access
- confusing mappings with dataset grounding

## Read Next

Use the smallest next document that matches the task:

- [`references/steps.md`](references/steps.md): investigation execution, typed intent guardrails, typed ref chaining, and state/context usage
- [`references/semantic-layer.md`](references/semantic-layer.md): semantic object families, dependency order, activation order, and modeling heuristics
- [`references/semantic-readiness.md`](references/semantic-readiness.md): lifecycle versus readiness and why-not-ready troubleshooting
- [`references/http-contracts.md`](references/http-contracts.md): cross-surface HTTP and session invariants that still matter for local execution
- [`references/planning.md`](references/planning.md): client-side orchestration patterns when a task spans multiple dependent intents
- [`references/infrastructure.md`](references/infrastructure.md): datasource setup, sync, mappings, engines, execution auth, and observability
- [`references/payload-cheatsheet.md`](references/payload-cheatsheet.md): minimum useful payloads for common semantic-layer writes and typed intents
- [`references/osi-mcp-modeling.md`](references/osi-mcp-modeling.md): step-by-step semantic modeling via MCP tools when building OSI-aligned datasets and metrics

For exact request fields, examples, and validation guidance, use the matching tool or server-supplied guidance links before reading deeper references.
