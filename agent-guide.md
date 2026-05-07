# Agent Guide

Shared coding and testing guidance for agents working in this repository. Keep
this file focused on stable rules that should be loaded for every coding task.

## Core Rules

- Think before coding: state assumptions, surface tradeoffs, and ask only when
  ambiguity would make the change risky.
- Prefer the minimum code that solves the requested problem; do not add
  speculative flexibility, future placeholders, or unrelated abstractions.
- Make surgical changes: touch only the files required, match existing style,
  and never clean up unrelated local changes.
- Define verifiable success criteria for non-trivial work and loop until the
  relevant checks pass or explain why they could not run.

## Python And Typing

- Never use bare `python`, `pytest`, `mypy`, or `ruff` in this repository.
- Use repository entrypoints or explicit `.venv/bin/...` paths only.
- New or modified Python code must satisfy typing for the touched modules.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore`
  unless it is strictly necessary and locally justified.
- When changing schemas, API models, or service contracts, update type
  annotations end-to-end in the same change.

## Repository Entrypoints

Prefer these repository entrypoints:

```bash
make test
make typecheck
make lint
make format
```

- For frontend work, run commands from `frontend/` and use the existing
  `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`, and
  `npm run test:browser` scripts when relevant.
- Tests and shared fixture details live in
  [`.agents/skills/marivo-test-fixtures/SKILL.md`](.agents/skills/marivo-test-fixtures/SKILL.md).
- Claude review instructions live in
  [`.agents/skills/claude-review/SKILL.md`](.agents/skills/claude-review/SKILL.md).
- **Mandatory:** When creating a git commit, always invoke the
  `commit-attribution` skill first. Follow its pre-commit scope check and
  attribution rules on every commit — no exceptions. The skill lives in
  [`.agents/skills/commit-attribution/SKILL.md`](.agents/skills/commit-attribution/SKILL.md).
- **Plan commit steps must include attribution:** When writing implementation
  plans (e.g. via `writing-plans`), every commit step must embed the
  `Co-Authored-By` trailer in the commit command so the executing agent copies
  it verbatim. Example:
  ```bash
  git commit -m "$(cat <<'EOF'
  feat: add specific feature

  Co-Authored-By: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
  EOF
  )"
  ```
  Do NOT write bare `git commit -m "..."` without attribution.

## Documentation Routing

When working on a task, read the right docs first:

| Task Type | Read First | Then |
|-----------|-----------|------|
| Analysis engine / evidence / intents | `specs/analysis/README.md` | Subtopic files |
| Semantic layer / objects / compiler | `specs/semantic/overview.md` | Schema contract files |
| Service runtime / agent runtime / data plane | `specs/service/README.md` | Subtopic files |
| HTTP API endpoint | `docs/api/README.md` | Endpoint-specific doc |
| Frontend UI | `docs/ui/frontend-design.zh.md` | `frontend/README.md` |
| Adding or modifying tests | `.agents/skills/marivo-test-fixtures/SKILL.md` | Spec docs for the domain |
| Product background / motivation | `docs/marivo-proposal.md` | `docs/marivo-for-builders.zh.md` |
| Active development plans | `docs/superpowers/README.md` | Dated plan/spec files |
| Global doc index | `docs/README.md` | — |

## Documentation Updates

- After behavior changes, update affected docs in the same change.
- Update this guide only for stable repository-wide coding and testing rules.
- Do not add product usage guidance, API workflows, semantic modeling recipes,
  runtime operation details, MCP/client instructions, migration history, or
  one-off workaround notes here.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.

## Superpowers + Gstack Workflow

This repo uses two skill suites that complement each other:

- **Superpowers** — development discipline (how to write code: TDD, planning,
  debugging, review workflow)
- **Gstack** — operational/product layer (how to verify and deliver: browser
  QA, deployment, design review, safety gates)

Use `/browse` from gstack for all web browsing. Never use `mcp__claude-in-chrome__*`
tools.

### Feature Development Flow

| Phase | Superpowers | Gstack |
|-------|-------------|--------|
| Ideation | `brainstorming` | `/design-consultation`, `/design-shotgun`, `/browse` for references |
| Planning | `writing-plans` | `/plan-eng-review`, `/plan-design-review` |
| Implementation | `using-git-worktrees` → `test-driven-development` → `executing-plans` | `/browse` for API docs, `/careful` for risky changes |
| Verification | `verification-before-completion` | `/qa` or `/qa-only` for browser-based e2e, `/benchmark` for perf |
| Review & Ship | `requesting-code-review` → `finishing-a-development-branch` | `/review`, `/ship`, `/land-and-deploy`, `/canary` |
| Debugging | `systematic-debugging` | `/investigate` |

### Common Pairing Patterns

| Scenario | First | Then |
|----------|-------|------|
| New feature | `brainstorming` | `/design-consultation` |
| Code complete | `verification-before-completion` | `/qa` |
| Ready to merge | `requesting-code-review` | `/review` + `/ship` |
| Bug or regression | `systematic-debugging` | `/investigate` |
| Risky change | `writing-plans` | `/careful` + `/freeze` |

### Available Gstack Skills

`/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`,
`/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`,
`/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`,
`/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`,
`/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/codex`,
`/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`,
`/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

## GitNexus — Code Intelligence

This project is indexed by GitNexus as **marivo** (24537 symbols, 39348 relationships, 277 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

### Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

### Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

### Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/marivo/context` | Codebase overview, check index freshness |
| `gitnexus://repo/marivo/clusters` | All functional areas |
| `gitnexus://repo/marivo/processes` | All execution flows |
| `gitnexus://repo/marivo/process/{name}` | Step-by-step execution trace |

### CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

## Semantic Modeling Guardrail

- Marivo is HTTP-only; do not design repo guidance around MCP-first behavior.
- Keep semantic physical grounding entity-first and entity-only unless the
  contract docs explicitly change.
- Put detailed modeling usage in `marivo-skill/` or domain reference docs, not
  in this guide.
