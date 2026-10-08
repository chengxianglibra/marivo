# Marivo Documentation

The maintained documentation describes the Python-native Marivo library only.

## Current Specs

- [`specs/agent-friendly-public-surface.md`](specs/agent-friendly-public-surface.md) -
  design philosophy of the public surface: why it targets coding agents, how the
  three execution modules layer, how the optional ontology extension joins them,
  and how each core API progressively discloses itself.
  Read this first for the cross-cutting picture, then the per-surface specs below.
- [`specs/semantic/overview.md`](specs/semantic/overview.md) -
  datasource + semantic layer overview: design goals, layered architecture, and
  a map to the focused specs in the same directory — the datasource layer, the
  semantic object model, the authoring workflow, and loading, validation, and
  introspection.
- [`specs/analysis/README.md`](specs/analysis/README.md) -
  current Analysis architecture and reading order: algebra, method registration,
  typed definition graph, compiler, Runtime and Store 8. The focused
  [Python Analysis design](specs/analysis/python-analysis-design.md) owns the DSL
  model and execution path; the index routes to method, temporal and recovery contracts.
- [`specs/temporal-semantics.md`](specs/temporal-semantics.md) -
  current cross-layer contract for built-in and fiscal periods, certified
  calendar authority, named scopes, event intervals, work schedules, and
  alignment policy.

## Agent Guidance

Packaged agent guidance lives under:

- `../marivo/skills/marivo-semantic`
- `../marivo/skills/marivo-analysis`

Run `make test TESTS='tests/packaging/test_skills.py'` after changing either
packaged skill.
