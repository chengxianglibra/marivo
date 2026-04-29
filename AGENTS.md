# AGENTS.md

Repository guidance lives in [`agent-guide.md`](agent-guide.md).

Key local rules:

- For Python-related commands, never use bare `python`, `pytest`, `mypy`, or `ruff`.
- Use repository entrypoints only: `make test`, `make typecheck`, `make lint`, `make format`, or the explicit `.venv/bin/...` paths they wrap.
- After behavior changes, update affected API/UI/docs files; update the shared guide only for repository-wide coding/testing rules.
