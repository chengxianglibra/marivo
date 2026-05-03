# Marivo Documentation

## For Agents

- [`agent-guide.md`](../agent-guide.md) — Coding rules and entry points (AI must-read)
- [`spec/analysis/README.md`](../spec/analysis/README.md) — Analysis engine and intent system
- [`spec/semantic/overview.md`](../spec/semantic/overview.md) — Semantic layer object model
- [`spec/service/README.md`](../spec/service/README.md) — Service runtime and operator design

## For Developers

### Internal Design Specs (`spec/`)

Long-lived, canonical design documents. These are the authoritative source for
system architecture decisions.

| Directory | Scope | Entry Point |
|-----------|-------|-------------|
| `spec/analysis/` | Evidence Engine, typed intents, canonical schemas | [`README.md`](../spec/analysis/README.md) |
| `spec/semantic/` | Semantic objects, binding, compiler, IR | [`overview.md`](../spec/semantic/overview.md) |
| `spec/service/` | Agent runtime, data plane, MySQL metadata | [`README.md`](../spec/service/README.md) |

### HTTP API Reference (`docs/api/`)

External-facing HTTP wire contracts. The public API boundary.

- [`docs/api/README.md`](api/README.md) — API index, conventions, and core concepts
- [`docs/api/quickstart.md`](api/quickstart.md) — End-to-end curl walkthrough

### Frontend Design (`docs/ui/`)

- [`frontend-design.zh.md`](ui/frontend-design.zh.md) — UI product design
- [`frontend-implementation.zh.md`](ui/frontend-implementation.zh.md) — Frontend implementation

### Active Development (`docs/superpowers/`)

Implementation plans and draft design specs from active development cycles.
These are **ephemeral** — stable designs should converge into `spec/` or
`docs/api/`, and completed work should move to `docs/archive/`.

- [`docs/superpowers/README.md`](superpowers/README.md) — Lifecycle rules and status tracking

## For Users (`docs/user/`)

User-facing documentation — how to use Marivo, not how it's built internally.

- [`docs/user/getting-started.md`](user/getting-started.md) — Quick start guide
- [`docs/user/concepts.md`](user/concepts.md) — Core concepts
- [`docs/user/faq.md`](user/faq.md) — Frequently asked questions

## Archive (`docs/archive/`)

Completed implementation plans and superseded designs. Read-only historical
reference; the code and canonical specs are the authority.

- [`docs/archive/plans/`](archive/plans/) — Historical implementation task lists
- [`docs/archive/superpowers/`](archive/superpowers/) — Completed superpowers specs and plans

## Product Background

- [`marivo-proposal.md`](marivo-proposal.md) — Project proposal and motivation
- [`marivo-for-builders.zh.md`](marivo-for-builders.zh.md) — Builder-facing explanation

## Language Convention

- `.zh.md` suffix = Chinese-language document
- `spec/` internal docs: author's choice of language
- `docs/api/`: English (public API contract)
- `docs/user/`: bilingual (parallel `en.md` / `zh.md` files)
