# Agent Guide

Shared coding, testing, and documentation guidance for agents working in this
repository. Keep this file focused on stable rules that should be loaded for
every coding task. Do not modify this file without explicit user approval.

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

- `marivo.help`
- `marivo.datasource`
- `marivo.semantic`
- `marivo.analysis`

Import `marivo` for focused help and each execution surface under its
conventional alias — `import marivo.datasource as md`, `import marivo.semantic
as ms`, `import marivo.analysis as mv`. `marivo.session` and similar execution
aliases are not available at the top level.

Rules for this surface:

- Python-track expressions return ibis expressions only. SQL text belongs only
  in provenance value objects such as
  `provenance=ms.from_sql(sql=..., dialect=...)`, never in executable expression
  bodies.
- Decorator function bodies stay restricted by
  `marivo/semantic/validator.py`.
- New exceptions subclass `SemanticError` or `AnalysisError`, carry structured
  fields, and render through the shared template style. New datasource
  exceptions subclass `DatasourceError`, parallel to the `SemanticError` and
  `AnalysisError` hierarchy rules, and follow the same structured-fields and
  shared-template-rendering contract.
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
  Each public API symbol resolves through the one public help coordinator:
  `marivo.help("datasource.<target>")` for datasource symbols,
  `marivo.help("semantic.<target>")` for semantic symbols, and
  `marivo.help("analysis.<target>")` for analysis symbols.
- Public API functions must not accept or return `Any` or other ambiguous types;
  every parameter and return annotation must be a concrete, specific type.

## Agent-Facing Surface Principles

The library is consumed primarily by agents through a write-run-read loop.
These rules govern every public surface change:

- Errors teach: every typed error states what was expected, what was
  received, and the concrete next step. Suggestions are built from real
  state (e.g. catalog contents), never hardcoded. No silent fallback.
- Keep guidance with its natural owner: live Help owns static API and navigation
  facts; result `show()` / `contract()` methods own current state and
  mechanically valid continuations; structured errors own concrete repair;
  packaged skills own workflow boundaries and judgment without duplicating
  those contracts.
- One canonical path per capability: discovery and guidance point to exactly one
  public entry point. Compatibility paths exist only when the owning contract
  explicitly requires them. Nothing described as "internal — use X instead"
  may appear in `__all__`.
- `__repr__` is the floor: every public result type has a bounded,
  single-line repr carrying kind and identity, pointing to `.show()` for
  detail. Default dataclass reprs are not acceptable on public result types.
- Terminal results (objects an agent stops to read) implement bounded
  `.show()` output with deterministic ordering. Artifact cards include only
  continuation hints that depend on the artifact's current state; they do not
  repeat the full capability matrix. Analysis artifacts expose `.contract()`
  when they own mechanically valid next actions. Datasource and semantic
  authoring expose callable operations, effects, input facts, structured errors,
  and typed repairs without a shared lifecycle graph. Explicit terminal
  boundaries such as `RawSqlResult` remain contract-free because they cannot
  enter typed analysis.
- Surface growth is gated: public `__all__` sets are pinned by a snapshot
  test. A new public result type must join an existing family (naming and
  protocol) or justify a new one. Type aliases and module-internal handoff
  types stay out of the top-level help index.
- Discovery is progressive and bounded: `marivo.help()` introduces the core
  concepts and routes only to `marivo.help("authoring")` or
  `marivo.help("analysis")`; qualified focused targets are discovered beneath
  those secondary roots and include a minimal runnable example for the owning
  symbol.
- Prefer one entry shape with closed, kind-dispatched variants over
  optional-field mega-classes: precise types fail loudly, optional-field
  unions fail silently.
- Treat a change to any public export, callable or type contract, Help target,
  result/error guidance, or dynamic continuation as one disclosure-contract
  change. Keep the affected implementation/API, native Help registry and
  budgets, dynamic guidance, independent drift/reachability/budget tests,
  examples, skills, CLI, and current English/Chinese docs aligned. Preserve one
  owner per fact, bounded progressive routes, and independently resolvable
  targets. Do not add renderer shadow inventories or unowned compatibility
  aliases; compatibility and migration belong to the owning contract.

## Tests

- Use shared fixtures in `tests/conftest.py` and `tests/shared_fixtures.py`
  for repeated Python-track setup.
- Keep tests aligned to the current owning contract; do not preserve legacy
  compatibility shapes unless explicitly required.
- Run the narrowest useful test first, then broaden to `make test` when the
  change touches shared behavior.

## Documentation Routing

When working on a task, read the right docs first:

| Task Type | Read First |
|-----------|------------|
| Datasource + semantic design (start here) | `docs/specs/semantic/overview.md` + focused `marivo.help("semantic.<target>")` / `marivo.help("datasource.<target>")` |
| Datasource declarations, discovery | `docs/specs/semantic/datasource-layer.md` |
| Python semantic object model | `docs/specs/semantic/semantic-object-model.md` |
| Semantic authoring workflow | `docs/specs/semantic/authoring-workflow.md` |
| Semantic loading, validation, runtime, and analysis handoff | `docs/specs/semantic/loading-validation-introspection.md` |
| Python analysis design | `docs/specs/analysis/python-analysis-design.md` |
| Agent workflow and boundaries | `marivo/skills/marivo-semantic/SKILL.md` or `marivo/skills/marivo-analysis/SKILL.md` |

## Documentation Updates

- After behavior changes, update affected user, spec, or skill files in the
  same change.
- When changing the public API, also update the example code in the `site/`
  documentation (versioned under `site/src/content/docs/*/latest/`). Keep
  both English and Chinese editions in sync.
- Update this guide only for stable repository-wide coding and testing rules.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.
