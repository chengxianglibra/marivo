# Marivo Documentation

## For Agents

- [`agent-guide.md`](../agent-guide.md) — Coding rules and entry points (AI must-read)
- [`specs/analysis/README.md`](specs/analysis/README.md) — Analysis engine and intent system
- [`specs/semantic/overview.md`](specs/semantic/overview.md) — Semantic layer object model
- [`specs/service/README.md`](specs/service/README.md) — Service runtime and operator design

## For Developers

### Architecture

- [`architecture-invariants.md`](architecture-invariants.md) — Five core architecture invariants (Core Isolation, Domain-Only Ports, Surface→Runtime Only, Profile Factory Exclusivity, Mode Separation)

### Internal Design Specs (`specs/`)

Long-lived, canonical design documents. These are the authoritative source for
system architecture decisions.

| Directory | Scope | Entry Point |
|-----------|-------|-------------|
| `specs/analysis/` | Evidence Engine, typed intents, canonical schemas | [`README.md`](specs/analysis/README.md) |
| `specs/semantic/` | Semantic objects, compiler, IR | [`overview.md`](specs/semantic/overview.md) |
| `specs/service/` | Agent runtime, data plane, MySQL metadata | [`README.md`](specs/service/README.md) |

### HTTP API Reference (`docs/api/`)

External-facing HTTP wire contracts. The public API boundary.

- [`docs/api/README.md`](api/README.md) — API index, conventions, and core concepts
- [`docs/api/quickstart.md`](api/quickstart.md) — End-to-end curl walkthrough

### Frontend Design (`docs/ui/`)

- [`frontend-design.zh.md`](ui/frontend-design.zh.md) — UI product design
- [`frontend-implementation.zh.md`](ui/frontend-implementation.zh.md) — Frontend implementation

### Active Development (`docs/superpowers/`)

Implementation plans and draft design specs from active development cycles.
These are **ephemeral** — stable designs should converge into `specs/` or
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
- `specs/` internal docs: author's choice of language
- `docs/api/`: English (public API contract)
- `docs/user/`: bilingual (parallel `en.md` / `zh.md` files)
