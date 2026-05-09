# Marivo Architecture Invariants

These invariants must hold across all phases and future development. Each entry
documents the rule, rationale, enforcement mechanism, and what a violation looks
like.

Source: [Platform Architecture Design §2](superpowers/specs/2026-05-06-marivo-platform-architecture-design.md)

---

## 1. Core Isolation

**Rule:** `core/` must not import any adapter, transport, or storage library.

**Rationale:** Core Engine contains pure domain logic. I/O dependencies would
make it untestable without infrastructure and create coupling between business
rules and deployment choices.

**Enforcement:** `importlinter:contract:core-no-io` — forbids `marivo.core`
from importing `marivo.api`, `marivo.storage`, `marivo.analysis_core`,
`marivo.evidence_engine`, `marivo.semantic_runtime`, `marivo.execution`,
`marivo.registry`, `marivo.adapters`, `marivo.cli`, and `marivo.ports`.

**Violation example:** Adding `import marivo.storage.metadata` inside a
`marivo/core/` module to read model definitions directly from SQLite.

---

## 2. Domain-Only Ports

**Rule:** Ports return domain objects and domain IDs — not ORM rows or SQL
cursors.

**Rationale:** Port interfaces define the boundary between core logic and
infrastructure. Leaking storage representations (rows, cursors, result sets)
forces core code to understand storage internals.

**Enforcement:** Code review. No automated linter rule exists for return type
shapes.

**Violation example:** A `ModelStore.get_model()` method returning a SQLAlchemy
`Row` object instead of a `SemanticModel` domain type.

---

## 3. Surface → Runtime Only

**Rule:** Surfaces (API, CLI, MCP) call Runtime only — not Core Engine
directly.

**Rationale:** Runtime orchestrates core + ports. Surfaces that bypass Runtime
duplicate orchestration logic and break the single-responsibility boundary.

**Enforcement:** `importlinter:contract:surfaces-must-use-runtime` — forbids
`marivo.api` and `marivo.cli` from importing `marivo.analysis_core`,
`marivo.evidence_engine`, or `marivo.semantic_runtime`.

**Violation example:** An API endpoint importing `marivo.analysis_core.compiler`
to run analysis directly instead of calling `runtime.execute_intent()`.

---

## 4. Profile Factory Exclusivity

**Rule:** Profile Factory is the only place that knows which adapter wires to
which port.

**Rationale:** Adapter wiring in multiple places creates hidden coupling and
makes it impossible to reason about which implementation is active. A single
factory per profile (local, server) is the composition root.

**Enforcement:** Convention and code review.
`importlinter:contract:profiles-do-not-import-runtime-factory` prevents
profiles from importing the old runtime factory.

**Violation example:** An API endpoint constructing a `DuckDBModelStore`
directly instead of receiving a `ModelStore` port from the profile factory.

---

## 5. Mode Separation

**Rule:** Local profile does not start HTTP service; enterprise profile does
not maintain independent business logic.

**Rationale:** Local mode is a library consumed by agents via MCP stdio.
Enterprise mode reuses the same Runtime exposed via HTTP. If either mode
maintains its own business logic, the codebase forks.

**Enforcement:** Architecture tests and code review.

**Violation example:** Adding a `local_analyze()` function in the local profile
that reimplements intent execution logic already in Runtime.
