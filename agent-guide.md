# Agent Guide

Shared coding, testing, and documentation guidance for agents working in this
repository. Keep this file focused on stable rules that should be loaded for
every coding task.

## Core Rules

- Think before coding: state assumptions, surface tradeoffs, and ask only when
  ambiguity would make the change risky.
- Prefer the minimum code that solves the requested problem; do not add
  speculative flexibility, future placeholders, or unrelated abstractions.
- Make surgical changes: touch only the files required, match existing style,
  and never clean up unrelated local changes.
- Do not add Chinese text to source code, code comments, tests, fixtures,
  generated code, or user-facing strings in code. Keep code artifacts in
  English unless a task explicitly updates localized documentation.
- Define verifiable success criteria for non-trivial work and loop until the
  relevant checks pass or explain why they could not run.
- Treat committed specs and docs as sources of truth. If code and docs
  disagree, verify the intended current contract before changing behavior.

## Python And Typing

- Never use bare `python`, `pytest`, `mypy`, or `ruff` in this repository.
- Use repository entrypoints or explicit `.venv/bin/...` paths only. For
  targeted Python tests, prefer `make test TESTS='tests/test_file.py'` or
  `.venv/bin/pytest tests/test_file.py`.
- New or modified Python code must satisfy typing for the touched modules.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore`
  unless it is strictly necessary and locally justified.

## Marivo Python Library

The public Marivo surface is the Python library:

- `marivo.datasource`
- `marivo.semantic`
- `marivo.analysis`

Rules for this surface:

- Python-track expressions return ibis expressions only. Do not introduce raw
  SQL strings as executable expression bodies. SQL text only belongs in
  provenance metadata such as `provenance=ms.from_sql(sql=..., dialect=...)`.
- Decorator function bodies stay restricted by
  `marivo/semantic/validator.py`.
- Expression-bearing semantic decorators keep SQL provenance in value objects
  such as `provenance=ms.from_sql(sql=..., dialect=...)`; SQL text is metadata
  only, never an executable expression body.
- New exceptions subclass `SemanticError` or `AnalysisError`, carry structured
  fields, and render through the shared template style.
- Top-level Frame APIs remain immutable. Only `frame.to_pandas()` returns an
  isolated copy.
- Datasource credentials are authored as `*_env` references and must not be
  written into project state. After a validated datasource round-trip, Marivo
  may cache resolved secrets in plaintext user-global state at
  `~/.marivo/secrets.toml`.
- Persistent analysis and semantic state lives project-locally under
  `<project_root>/.marivo/`.
- Cross-session frame ownership is mandatory for helpers that consume frames.
- Public API functions must have a docstring that covers: function purpose,
  parameter descriptions, return value, a usage example, and brief constraints.
  The `describe` function must support each public API symbol.
- Public API functions must not accept or return `Any` or other ambiguous types;
  every parameter and return annotation must be a concrete, specific type.

## Agent-Facing Surface Principles

The library is consumed primarily by agents through a write-run-read loop.
These rules govern every public surface change:

- Errors teach: every typed error states what was expected, what was
  received, and the concrete next step. Suggestions are built from real
  state (e.g. catalog contents), never hardcoded. No silent fallback.
- One path per capability: each task has exactly one public entry point.
  Nothing described as "internal — use X instead" may appear in `__all__`.
- `__repr__` is the floor: every public result type has a bounded,
  single-line repr carrying kind and identity, pointing to `.show()` for
  detail. Default dataclass reprs are not acceptable on public result types.
- Terminal results (objects an agent stops to read) implement the shared
  result protocol: bounded `.show()` output, deterministic ordering, and
  closing affordance hints generated from real state.
- Surface growth is gated: public `__all__` sets are pinned by a snapshot
  test. A new public result type must join an existing family (naming and
  protocol) or justify a new one. Type aliases and module-internal handoff
  types stay out of the top-level help index.
- Discovery is progressive and bounded: `help()` is a short index grouped
  by family; `describe(symbol)` includes a minimal runnable example.
- Prefer one entry shape with closed, kind-dispatched variants over
  optional-field mega-classes: precise types fail loudly, optional-field
  unions fail silently.

## Authoring Guidance Layering

`ms.help` owns the static authoring contract — constructor, required/optional
parameters, types, defaults, omit rules, and cross-parameter constraints — as
the single source agents consult before authoring. `md.discover_*` owns
runtime datasource evidence only — profiles, signals, issues, detected
formats — never parameter tables or semantic-selection judgments. The
`marivo-semantic` skill owns workflow and routing only — help → discover →
settle from evidence → prepare → author → verify — and must not duplicate
parameter tables from either.

## Tests

- Use shared fixtures in `tests/conftest.py` and `tests/shared_fixtures.py`
  for repeated Python-track setup.
- Keep tests aligned to the current contract, not legacy compatibility shapes.
- Run the narrowest useful test first, then broaden to `make test` when the
  change touches shared behavior.

## Repository Entrypoints

Prefer these repository entrypoints:

```bash
make test
make typecheck
make lint
make format
make examples-check
```

## Documentation Routing

When working on a task, read the right docs first:

| Task Type | Read First |
|-----------|------------|
| Python semantic declarations | `docs/specs/semantic/python-semantic-layer.md` |
| Python analysis design | `docs/specs/analysis/python-analysis-design.md` |
| Agent usage examples | `marivo/skills/marivo-semantic/` or `marivo/skills/marivo-analysis/` |

## Documentation Updates

- After behavior changes, update affected user, spec, or skill files in the
  same change.
- When changing the public API, also update the example code in the `site/`
  documentation (versioned under `site/src/content/docs/*/latest/`). Keep
  both English and Chinese editions in sync.
- Update this guide only for stable repository-wide coding and testing rules.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.
