# CLAUDE.md

Shared guidance for agents. Keep this file focused on stable, repository-wide rules that agents should load every time.

## Core Rules

- Think before coding: state assumptions, surface tradeoffs, ask when the request is ambiguous, and push back when a simpler approach is better.
- Prefer the minimum code that solves the requested problem; do not add speculative flexibility or abstractions.
- Make surgical changes: touch only what the request requires, match existing style, and do not clean up unrelated code.
- Define verifiable success criteria for non-trivial tasks and loop until the relevant checks pass or explain why they could not run.

## Python / Typing

- Never use bare `python`, `pytest`, `mypy`, or `ruff` in this repository. Use `make` targets or explicit `.venv/bin/...` paths only.
- New or modified Python code must satisfy `mypy` for touched modules.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore` unless strictly necessary.
- When changing schemas, API models, or service contracts, update type annotations end-to-end in the same change.
- Use the repository `make` targets for Python type checks, linting, and formatting.

## Repository Entrypoints

Prefer these repository entrypoints:

```bash
make test
make typecheck
make lint
make format
```

- Tests and shared fixture details live in [`.claude/skills/marivo-test-fixtures/SKILL.md`](.claude/skills/marivo-test-fixtures/SKILL.md); use that skill when changing tests, fixtures, DuckDB templates, or metadata templates.
- Commit attribution rules live in [`.claude/skills/commit-attribution/SKILL.md`](.claude/skills/commit-attribution/SKILL.md); use that skill when drafting or editing commit messages.

## Repository Boundaries

- Marivo is HTTP-only; do not assume any MCP layer exists.
- Prefer typed analysis steps over exposing raw SQL as the external contract.
- Keep factual extraction deterministic; use models for explanation, not evidence structure.
- `marivo.yaml` is runtime-only. Do not add `sources`, `engines`, `bindings`, or `mappings` inventory blocks; source, engine, and mapping objects are configured through the HTTP API only.
- Source-to-engine projection is mapping-only. Do not reintroduce legacy `/bindings`, `binding.namespace` style config, tables, API routes, test fixtures, or operator-facing contracts.
- Synced `source_objects.authority_locator` is the primary source-side identity for routing and table lookup; treat `fqn` as a derived display/reference field.
- Typed semantic bindings must anchor on source objects and source-side authority locators; runtime compile resolves them through ready mappings.
- Prefer API/service/registry validation over SQLite triggers for request-level business invariants.
- After behavior changes, update the shared guide only when the rule is repository-wide; update affected API, semantic, service, analysis, or UI docs as appropriate.

## Docs Layout

- `docs/api/`: external HTTP API docs only.
- `docs/analysis/`: intents and evidence engine schemas.
- `docs/semantic/`: entity, dimension, metric, process, and semantic compiler schemas.
- `docs/service/`: service runtime and operator design notes.
- Keep MCP implementation details in `marivo-mcp/README.md` and `marivo_mcp.inventory`, not here.

## What Not To Add Here

- Do not add long command recipes for specific tools; put them in a skill or task-specific doc.
- Do not add detailed workflows that only apply to one class of task.
- Do not add long lint, test, commit, or review format examples.
- Do not add implementation details, historical migration details, or one-off workaround notes.
- Put those details in a skill, README/quickstart, or the relevant API, semantic, service, or analysis docs.
