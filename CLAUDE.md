# CLAUDE.md

@agent-guide.md

Key local rules:

- For Python-related commands, never use bare `python`, `pytest`, `mypy`, or `ruff`.
- Use repository entrypoints only: `make test`, `make typecheck`, `make lint`, `make format`, or the explicit `.venv/bin/...` paths they wrap.
- After behavior changes, update affected API/UI/docs files; update the shared guide only for repository-wide coding/testing rules.

## Skill routing

Only the following gstack skills are enabled for this project:
- `/plan-ceo-review` - CEO/founder-mode plan review for strategy and scope decisions
- `/plan-eng-review` - Engineering manager-mode plan review for architecture and implementation

All other gstack skills are disabled. Do not invoke or suggest any other skills.
