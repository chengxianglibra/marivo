# Copilot Instructions

Repository guidance lives in [`agent-guide.md`](/Users/lichengxiang/source/oss/marivo/agent-guide.md).

Key local rules:

- For Python-related commands, never use bare `python`, `pytest`, `mypy`, or `ruff`.
- Use repository entrypoints only. During iteration, prefer compact
  `make test-agent`, `make typecheck-agent`, and `make lint-agent`; use
  `make check-agent` for the broad compact gate. The standard `make test`,
  `make typecheck`, `make lint`, and `make format` entrypoints remain available
  when detailed output is needed.
- After behavior changes, update affected API/UI/docs files; update the shared guide only for repository-wide coding/testing rules.
