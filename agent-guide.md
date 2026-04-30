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
- Commit attribution rules live in
  [`.agents/skills/commit-attribution/SKILL.md`](.agents/skills/commit-attribution/SKILL.md).

## Documentation Updates

- After behavior changes, update affected docs in the same change.
- Update this guide only for stable repository-wide coding and testing rules.
- Do not add product usage guidance, API workflows, semantic modeling recipes,
  runtime operation details, MCP/client instructions, migration history, or
  one-off workaround notes here.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.

## Semantic Modeling Guardrail

- Marivo is HTTP-only; do not design repo guidance around MCP-first behavior.
- Keep semantic physical grounding entity-first and entity-only unless the
  contract docs explicitly change.
- Put detailed modeling usage in `marivo-skill/` or domain reference docs, not
  in this guide.
