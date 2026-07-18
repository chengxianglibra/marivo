# Marivo Documentation

The maintained documentation describes the Python-native Marivo library only.

## Current Specs

- [`specs/agent-friendly-public-surface.md`](specs/agent-friendly-public-surface.md) -
  design philosophy of the public surface: why it targets coding agents, how the
  three modules layer, and how each core API progressively discloses itself.
  Read this first for the cross-cutting picture, then the per-surface specs below.
- [`specs/semantic/overview.md`](specs/semantic/overview.md) -
  datasource + semantic layer overview: design goals, layered architecture, and
  a map to the focused specs in the same directory — the datasource layer, the
  semantic object model, the authoring workflow, and loading, validation, and
  introspection.
- [`specs/analysis/python-analysis-design.md`](specs/analysis/python-analysis-design.md) -
  analysis layer overview: the design philosophy and a map to the focused specs
  in the same directory — operators and frames, session state and runtime, the
  evidence access surface, and timezone and calendar alignment.

## Agent Guidance

Packaged agent guidance lives under:

- `../marivo/skills/marivo-semantic`
- `../marivo/skills/marivo-analysis`

Run `make test TESTS='tests/test_marivo_analysis_skill_contract.py'` after changing either
packaged skill.
