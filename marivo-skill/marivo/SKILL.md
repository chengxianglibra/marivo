---
name: marivo
description: "Use Marivo for HTTP-based, evidence-first analytics work: semantic discovery, session creation, typed intent submission, canonical state/context reads, semantic-layer modeling, lifecycle/readiness-aware runtime routing, source-engine mapping, and execution-auth-aware troubleshooting. Trigger when the user mentions Marivo, semantic metrics/entities/mappings/predicates, typed analysis intents, proposition state/context surfaces, or wants structured evidence instead of ad-hoc SQL or MCP."
---

# Marivo Skill

Marivo is an **HTTP-only agentic analytics system**. Treat the HTTP API as the product boundary. Do not assume a hidden planner layer, a text-to-SQL public contract, old `/steps/*` routes, MCP-owned evidence, or any required MCP layer.

Use this file to decide **which Marivo surface to use next**. Do not use it as the full field-level contract reference.

## What This File Owns

This file owns only the minimum routing layer:

- when Marivo is the right system to use
- which surface to use first
- the default investigation loop
- the highest-value guardrails and anti-patterns
- which reference file to read next

Do not turn this file into a transport guide, an MCP tool inventory, or a field-by-field schema manual.

## When To Use This Skill

Use this skill when the task involves any of these:

- investigating a metric change, anomaly, or hypothesis through typed intents
- reading canonical evidence from session state or proposition context
- modeling reusable semantic contracts such as domains, entities, metrics, dimensions, predicates, processes, or time semantics
- troubleshooting lifecycle/readiness, source sync, routing, grounding, or execution auth problems
- preferring structured Marivo evidence over ad hoc SQL or MCP summaries

Skip this skill when the task is unrelated to Marivo's HTTP surfaces.

## Choose The Surface First

Start by choosing the correct Marivo surface:

- **Action surface**: submit typed intents when the task should create or advance analysis
- **State surface**: read session-level decision state when deciding whether to continue, branch, or stop
- **Context surface**: read proposition-level canonical closure when one claim needs deeper explanation
- **Semantic layer**: create or inspect reusable governed business contracts such as `domain.*`, `entity.*`, `metric.*`, `predicate.*`, or `mapping.*`
- **Infrastructure surfaces**: inspect health, sources, sync, mappings, engines, jobs, and operational grounding

Use these routing rules:

- choose **session investigation** for one-off analysis grounded in current evidence
- choose the **semantic layer** when the same business concept should be reusable across investigations
- choose **infrastructure surfaces** when the problem is reachability, sync, routing, grounding, mapping, or job progress rather than evidence interpretation
- choose **state**, not context, for the session-level picture
- choose **context**, not state, for one proposition's local evidence closure
- choose synced **source metadata** only when the question is what Marivo currently knows after sync, not what the external catalog currently says

Keep these boundaries explicit:

- actions create evidence artifacts; they do not explain themselves
- state and context are canonical evidence reads
- runtime-status or jobs are operator surfaces, not canonical evidence
- semantic objects define reusable meaning; they are not session evidence
- `mapping.*` objects govern source-to-engine routing and catalog projection; they are not semantic object physical grounding
- domains are discovery metadata, not permissions, compiler compatibility, or runtime policy
- entity fields are the only physical grounding owner; dimension/time/predicate/metric/process objects reference `entity.<entity>.field.<field>` or other semantic refs instead of declaring physical columns

## Default Operating Loop

Use this default loop for most investigation work:

1. Confirm health or discovery surfaces if service reachability is unclear.
2. Discover or resolve the ready semantic object you want to use.
3. Create a session (include `execution_identity` when engine auth requires a session user).
4. Start with a bounded typed intent, usually `detect` or `observe`.
5. Read session state.
6. If one proposition matters, read proposition context.
7. Either submit a bounded follow-up intent or stop.
8. When no further session writes are needed, explicitly terminate the session.

Practical heuristics:

- start with `detect` when anomaly discovery is the first task
- start with `observe` when you already know the metric and window
- use `detect(patterns=["period_shift"])` or `profile="level_shift"` when the question is about whole-window degradation rather than a single bucket spike
- use `diagnose(mode="explicit_compare")` when the caller already knows current and baseline windows; this skips `detect` and directly expands through scalar observe, compare, and decompose
- move to the semantic layer when the same business concept, grouping axis, or time contract should be reused across investigations
- author semantic objects entity-first: discover/create the `domain.*`, create `entity.*` with thin `fields[]` and entity grounding, then create `time.*` / `dimension.*` / `predicate.*` with fully qualified entity field refs, then metric/process contracts
- when a metric/process crosses entities, fix the blocker through `relationship.*` and `compiler_profile.*`; do not add metric/process-owned grounding
- use `predicate.*` refs when you need governed, reusable filter semantics for metrics or request scopes
- use `POST /semantic/batch` dry-run when authoring multiple semantic objects together; inspect per-item guidance and entity-field coverage before applying or activating
- always send canonical structured time windows such as `{"kind":"range","start":"YYYY-MM-DD","end":"YYYY-MM-DD"}`; do not use shorthand strings such as `"2026-04-01 to 2026-04-19"`
- when the business request gives an inclusive end date, convert it to Marivo's exclusive `range.end` before sending the request; for example, inclusive `2026-04-01` through `2026-04-18` must be sent as `start=2026-04-01, end=2026-04-19`
- for `observe`, choose exactly one output shape per step: use `granularity` for time-series or `dimensions` for grouped comparisons, never both in the same request
- for `detect` and `diagnose(mode="auto_detect")`, send `time_scope={kind,start,end}` plus top-level `granularity`; never send legacy `time_scope.mode`, `time_scope.grain`, or `time_scope.current`
- use derived intents such as `attribute`, `diagnose`, or `validate` only when the problem already fits that bounded pattern
- use atomic intents when you need tighter control over branching
- typed intent `metric` parameters must use canonical refs such as `metric.watch_time`, not bare names such as `watch_time`
- stopping an investigation means closing the lifecycle explicitly, not just ceasing tool calls
- terminate via canonical HTTP `POST /sessions/{session_id}/terminate`
- use `terminal_reason="answered"` when the investigation reached a normal conclusion; use `terminal_reason="user_closed"` when the caller is simply ending the session without a stronger lifecycle reason
- after termination, treat the session as read-only; continue with `GET /sessions/{session_id}`, `GET /sessions/{session_id}/state`, or proposition context reads instead of more intent writes

Read more before going deeper:

- use `references/steps.md` for intent-level guardrails and state/context sequencing
- use `references/semantic-layer.md` for reusable semantic modeling heuristics
- use `references/http-contracts.md` for shared HTTP rules such as structured time windows and session ownership

## Common Mistakes

Avoid these high-frequency errors:

- treating MCP summaries, local notes, or agent narration as canonical evidence
- treating `/jobs` or runtime-status as session evidence state
- treating `get_session_state` as a session step or artifact inventory; it only shows live propositions and their closure, so a successful `observe` can still leave state empty
- treating `lifecycle_status=active` as proof that an object is usable now
- treating derived intents as an open-ended planning engine
- treating old `/steps/*` routes as current public write surfaces
- confusing `mapping.*` (source-to-engine routing) with entity physical grounding; they are separate concepts
- adding `physical_column`, carrier, binding-target, SQL, or table/view fields to dimension/time/predicate/metric/process objects; only entity fields own physical locators
- using unqualified `field.*` refs outside entity-local field definitions; downstream objects should use `entity.<entity>.field.<field>`
- fixing cross-entity blockers by inventing metric/time/process grounding; use entity fields, relationships, and compatibility profiles instead
- guessing request payloads when the tool or guided contract links already provide the exact shape
- sending shorthand time-window strings where the contract expects structured objects such as `time_scope`, `left`, or `right`
- sending legacy detect or diagnose time shapes such as `time_scope.mode`, `time_scope.grain`, or `time_scope.current`; use observe-aligned range plus top-level `granularity`
- reading `range.end` as inclusive; Marivo range windows are `[start, end)`, so a same-day-inclusive business window must be converted before submission
- finishing the analysis mentally but leaving the session lifecycle in `open`
- omitting `execution_identity.session_user` when the target engine requires authenticated access

Readiness rule:

- `lifecycle_status` answers whether an object is in the governed public catalog
- `readiness_status` answers whether runtime or catalog should consume it by default
- `active` does not imply `ready`

If you need more than these top-level rules, leave this file and read the smallest matching reference instead of adding detail here.

## Read Next

Use the smallest next document that matches the task:

- [`references/steps.md`](references/steps.md): investigation execution, typed intent guardrails, typed ref chaining, and state/context usage
- [`references/semantic-layer.md`](references/semantic-layer.md): semantic object families, dependency order, activation order, and modeling heuristics
- [`references/semantic-readiness.md`](references/semantic-readiness.md): lifecycle versus readiness and why-not-ready troubleshooting
- [`references/http-contracts.md`](references/http-contracts.md): cross-surface HTTP, session invariants, and execution auth
- [`references/planning.md`](references/planning.md): client-side orchestration patterns when a task spans multiple dependent intents
- [`references/infrastructure.md`](references/infrastructure.md): sources, sync, mappings, engines, execution auth, and observability
- [`references/payload-cheatsheet.md`](references/payload-cheatsheet.md): minimum useful payloads for common semantic-layer writes

For exact request fields, examples, and validation guidance, use the matching tool or server-supplied guidance links before reading deeper references.
