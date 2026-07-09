# Marivo Documentation

The maintained documentation describes the Python-native Marivo library only.

## Current Specs

- [`specs/agent-friendly-public-surface.md`](specs/agent-friendly-public-surface.md) -
  design philosophy of the public surface: why it targets coding agents, how the
  three modules layer, and how each core API progressively discloses itself.
  Read this first for the cross-cutting picture, then the per-surface specs below.
- [`specs/semantic/python-semantic-layer.md`](specs/semantic/python-semantic-layer.md) -
  Python semantic model declarations and loading behavior.
- [`specs/analysis/python-analysis-design.md`](specs/analysis/python-analysis-design.md) -
  analysis layer overview: the design philosophy and a map to the focused specs
  in the same directory — operators and frames, session state and runtime, the
  evidence access surface, and timezone and calendar alignment.

## Agent Guidance

Executable agent examples live under:

- `../marivo/skills/marivo-semantic`
- `../marivo/skills/marivo-analysis`

Run `make examples-check` after public symbol, signature, exception, or example changes.
