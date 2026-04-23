# CLAUDE.md

Repository guidance lives in [`docs/agent-guide.md`](docs/agent-guide.md).

Key local rules:

- Marivo is HTTP-only; do not assume any MCP layer exists.
- Prefer typed analysis steps over exposing raw SQL as the external contract.
- Keep factual extraction deterministic; use models for explanation, not evidence structure.
- After behavior changes, update the shared guide and any affected API/UI/docs files.
