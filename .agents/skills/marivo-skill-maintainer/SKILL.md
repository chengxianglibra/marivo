---
name: marivo-skill-maintainer
description: Use when updating the project-local Marivo agent skills under /Users/lichengxiang/source/oss/marivo/marivo-skill, especially marivo-datasource, marivo-semantic-layer, and marivo-analysis, plus their references and eval boundaries.
---

# Marivo Skill Maintainer

Use this skill when the task is to update the project-local Marivo skills at:

- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo-datasource`
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo-semantic-layer`
- `/Users/lichengxiang/source/oss/marivo/marivo-skill/marivo-analysis`

The source design baseline lives in `docs/specs/service/marivo-skill.md`. Treat that spec plus the
current skill directories as the maintenance contract for the external skills.

## Core Boundary

These external skills are **stdio MCP usage guides**. They are agent-side routing layers, not HTTP
protocol manuals, runtime managers, or full MCP inventories.

Keep the responsibility split explicit:

- `marivo-datasource` answers "how do I set up and inspect source metadata now?"
- `marivo-semantic-layer` answers "how do I build reusable semantic objects now?"
- `marivo-analysis` answers "how do I run or continue an investigation now?"

Do not collapse the three skills back into one broad router unless the product requirement changes.

## Update Workflow

1. Read `docs/specs/service/marivo-skill.md` first.
2. Inspect only the skill files needed for the requested change.
3. Update the smallest matching file under the affected skill directory.
4. Keep each `SKILL.md` short and routing-focused.
5. Put detailed topic guidance in the owning `references/*.md` file for that skill.
6. Keep examples limited to the current stdio MCP tool names and argument shapes.
7. Avoid duplicating the same guardrail across skills unless it is necessary to keep a skill
   executable on its own.
8. Verify the result with a quick structure and wording pass.

If the user says `maviro-skill` or `maviro`, verify whether they mean the existing path
`/Users/lichengxiang/source/oss/marivo/marivo-skill` before creating a new misspelled directory.

## File Ownership

- `marivo-datasource/SKILL.md`: datasource-only routing, handoff rules, and anti-patterns.
- `marivo-datasource/references/workflow.md`: datasource create/read/update/delete, browse,
  preview, and stop conditions.
- `marivo-semantic-layer/SKILL.md`: reusable semantic authoring routing and handoff rules.
- `marivo-semantic-layer/references/modeling.md`: semantic model, dataset, metric, and relationship
  authoring order.
- `marivo-semantic-layer/references/readiness.md`: readiness checks, blocker inspection, and repair
  order.
- `marivo-analysis/SKILL.md`: session-scoped investigation routing and close-out rules.
- `marivo-analysis/references/workflow.md`: session creation, analysis intents, state/context reads,
  ref chaining, and terminate flow.
- `evals/evals.json` under each skill: scenario coverage for that skill's owned boundary.

## What Belongs In The Skill

Prefer content that changes agent behavior:

- when to use that skill versus another Marivo skill
- which stdio MCP tool to call first
- the default bounded loop for that skill
- the handoff boundary to the next skill
- high-value guardrails that prevent cross-surface confusion

## What Does Not Belong

Do not put these in the skill body:

- full HTTP path or schema documentation
- full MCP tool inventory
- runtime daemon, port, or `runtime.json` implementation details
- ad hoc SQL or text-to-SQL as a public analysis contract
- complete error-code tables
- all semantic object schemas
- examples that do not match the current stdio MCP tool surface

## Editing Rules

- Update the external skills only when the change affects agent routing, sequence, examples, or
  guardrails.
- If a change is only a field rename or extra response field, keep it out of `SKILL.md` unless it
  affects agent decisions.
- If a rule is datasource-only, keep it in `marivo-datasource`.
- If a rule is semantic-authoring-only, keep it in `marivo-semantic-layer`.
- If a rule is session-analysis-only, keep it in `marivo-analysis`.
- Keep examples minimal and valid. Prefer live tool guidance for exact fields.

## Acceptance Check

Before finishing, confirm:

- the split between datasource, semantic-layer, and analysis is still explicit
- each `SKILL.md` still fits on one screen and routes to the right reference
- examples use current stdio MCP tool names and argument shapes
- no old `marivo` router skill remains as a visible entry point
- each changed reference owns one topic and does not become a second protocol manual
- no new `maviro` path or naming drift was introduced
