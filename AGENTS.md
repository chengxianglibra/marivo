# AGENTS.md

Repository guidance lives in [`agent-guide.md`](agent-guide.md).

Key local rules:

- For Python-related commands, never use bare `python`, `pytest`, `mypy`, or `ruff`.
- Use repository entrypoints only: `make test`, `make typecheck`, `make lint`, `make format`, or the explicit `.venv/bin/...` paths they wrap.
- After behavior changes, update affected API/UI/docs files; update the shared guide only for repository-wide coding/testing rules.
- Treat every public agent-facing surface change as one disclosure-contract change; keep its owning API, Help, dynamic guidance, validation, skills, and current English/Chinese docs aligned as applicable.
- For analysis, treat reuse of prior results as a weak dependency: prefer one clean current contract and remove legacy artifacts, aliases, migrations, and dual-read compatibility unless explicitly required.
