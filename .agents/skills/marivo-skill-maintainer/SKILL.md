---
name: marivo-skill-maintainer
description: Use when updating the project-local Marivo agent skill under /Users/lichengxiang/source/oss/marivo/marivo-skill/marivo from the repository baseline in plan/marivo-skill.md, including SKILL.md, references, guardrails, and skill boundary decisions.
---

# Marivo Skill Maintainer

Use this skill when the task is to update the project-local Marivo skill at
`/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo`.

The source design baseline lives in `plan/marivo-skill.md`. Treat that plan as the repository-owned
maintenance contract for the external skill.

## Core Boundary

Marivo remains HTTP-only. The external `marivo` skill is an agent-side usage strategy layer, not a
protocol layer, runtime manager, or MCP inventory.

Keep the responsibility split explicit:

- `marivo-mcp` answers "can the agent call Marivo correctly?"
- `marivo` skill answers "should the agent use Marivo this way now?"
- HTTP Marivo remains the canonical execution and evidence boundary.

Do not add skill text that assumes Marivo requires MCP. MCP may be the usual agent adapter, but the
skill must keep HTTP as the product boundary.

## Update Workflow

1. Read `plan/marivo-skill.md` first.
2. Inspect only the external skill files needed for the requested change.
3. Update the smallest matching file under `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo`.
4. Keep `SKILL.md` short and routing-focused.
5. Put detailed topic guidance in one focused `references/*.md` file.
6. Avoid duplicating the same guardrail across references. Choose one owner and link or mention it
   briefly elsewhere.
7. Verify the result with a quick structure and wording pass.

If the user says `maviro-skill` or `maviro`, verify whether they mean the existing path
`/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo` before creating a new misspelled directory.

## File Ownership

- `SKILL.md`: minimum decision entry for when to use Marivo, which surface to choose, the default
  investigation loop, high-value guardrails, common mistakes, and read-next routing.
- `references/steps.md`: session investigation, typed intents, typed ref chaining, state/context
  reads, and close-out sequencing.
- `references/semantic-layer.md`: reusable semantic modeling, object families, dependency order,
  activation order, and modeling heuristics.
- `references/semantic-readiness.md`: lifecycle versus readiness, blocker inspection, and why an
  object is not usable yet.
- `references/http-contracts.md`: shared HTTP/session invariants, structured time windows, session
  ownership, validation recovery, and execution auth rules.
- `references/planning.md`: client-side orchestration over typed intents when no public plan API
  exists.
- `references/infrastructure.md`: health, source sync, engines, mappings, bindings, jobs,
  execution auth, and operational troubleshooting.
- `references/governance.md`: policy, predicate governance, quality gates, and approvals.
- `references/payload-cheatsheet.md`: minimum useful payload shapes only, not a schema manual.

## What Belongs In The Skill

Prefer content that changes agent behavior:

- when to use Marivo versus another tool
- action/state/context/semantic/infrastructure surface routing
- `detect` versus `observe` starting heuristics
- state versus proposition context decisions
- lifecycle and readiness guardrails
- session termination after final writes
- structured time-window and exclusive-end reminders
- canonical evidence boundaries
- semantic modeling dependency order
- infrastructure troubleshooting routing

## What Does Not Belong

Do not put these in the skill body:

- full HTTP path and field documentation
- full MCP tool inventory
- runtime daemon, port, or `runtime.json` implementation details
- ad hoc SQL or text-to-SQL as a public analysis contract
- complete error-code tables
- all semantic object schemas
- examples that conflict with the canonical HTTP contract or executable tool guidance

## Editing Rules

- Update HTTP API docs and `marivo-mcp` first when the executable contract changed; update the
  external skill only when the change affects agent routing, sequence, or guardrails.
- If a change is only a field rename or extra response field, keep it out of `SKILL.md` unless it
  affects agent decisions.
- If a guardrail applies across surfaces, put the full rule in `references/http-contracts.md`.
- If a rule is intent-specific, put it in `references/steps.md`.
- If a rule is about semantic object usability, put it in `references/semantic-readiness.md`.
- Keep examples minimal and valid. Prefer server/tool guidance for exact fields.

## Acceptance Check

Before finishing, confirm:

- the external skill still lets an agent decide when to use Marivo
- `SKILL.md` still distinguishes action, state, context, semantic, and infrastructure surfaces
- session close-out is discoverable
- MCP summaries, runtime status, and jobs are not described as canonical evidence
- the changed reference owns one topic and does not become a second protocol manual
- no new `maviro` path or naming drift was introduced
