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
  Each public API symbol resolves through its owning help adapter:
  `md.help(...)` for datasource symbols and `ms.help(...)` for semantic
  symbols. `md.describe(name)` remains a datasource-domain read for one
  registered datasource; it is not a generic symbol-introspection API and
  this cutover adds no `ms.describe(...)` or cross-surface
  `describe(symbol)` alias.
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
  result protocol: bounded `.show()` output, `.contract()` for mechanically
  valid next actions, deterministic ordering, and closing affordance hints
  generated from real state. State-bearing datasource and semantic
  objects/results expose `.contract()` matching the analysis artifact
  convention, so every agent-facing surface agrees on mechanically valid
  next actions.
- Surface growth is gated: public `__all__` sets are pinned by a snapshot
  test. A new public result type must join an existing family (naming and
  protocol) or justify a new one. Type aliases and module-internal handoff
  types stay out of the top-level help index.
- Discovery is progressive and bounded: `help()` is a short index grouped
  by family; the owning `md.help(...)` / `ms.help(...)` adapter includes a
  minimal runnable example for each symbol.
- Prefer one entry shape with closed, kind-dispatched variants over
  optional-field mega-classes: precise types fail loudly, optional-field
  unions fail silently.

## Authoring Guidance Layering

`ms.help` owns the static authoring contract — constructor, required/optional
parameters, types, defaults, omit rules, and cross-parameter constraints — as
the single source agents consult before authoring; the CLI route
`marivo help semantic` is the matching terminal entry point. `md.help` owns
datasource contracts; `md.inspect(...)`, an explicitly scoped
`inspection.sample(...)`, and query-free snapshot projections own runtime
datasource evidence. They never own semantic-selection judgments. The
`marivo-semantic` skill owns workflow and routing only:

```text
help/browse -> inspect -> explicit scope -> sample once -> project evidence -> settle/grill -> author one Python object -> load typed object -> static verify -> scoped preview -> readiness -> analysis
```

It must not duplicate parameter tables from either help surface. There is no
public prepare stage or automatic authoring planner. The agent owns evidence-based
drafting and technical handling, including uncommon physical formats. The user or
business owner owns unresolved business-semantic decisions and approves metric
meaning before analysis handoff.

Ownership split: the live `ms.help(...)` / `md.help(...)` surfaces own the
static contracts; the registry behind them owns mechanical continuation facts
but is not itself a public API; the `marivo-semantic` skill owns workflow and
routing only; the runtime has no canonical link to packaged skill files, so
skill content is never read or executed by the library.

## Analysis Guidance Layering

Environment-verified live surfaces own capabilities and runtime guidance: the
CLI route `python -m marivo help analysis [target]` and the Python
`mv.help(...)` surface own the static analysis contract — signatures, artifact
families, constraints, return types, errors, and runnable examples. Frames and
results own dynamic guidance — `show()` describes current state, `contract()`
describes mechanically valid next actions, and structured errors own repair
guidance. The `marivo-analysis` skill owns hard boundaries, handoffs, evidence
continuity, and closeout obligations. The agent owns planning and judgment.

The skill is a one-file boundary kernel. It does not duplicate the help
contract, frame/result guidance, or error repair guidance. It does not
prescribe an ordered operator sequence or a report template. Intentional
teaching order is documented in the live help surface and the active specs,
not in the skill.

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
| Datasource + semantic design (start here) | `docs/specs/semantic/overview.md` + live `ms.help(...)` / `md.help(...)` surface |
| Datasource declarations, discovery | `docs/specs/semantic/datasource-layer.md` |
| Python semantic object model | `docs/specs/semantic/semantic-object-model.md` |
| Semantic authoring workflow | `docs/specs/semantic/authoring-workflow.md` |
| Semantic loading, validation, runtime | `docs/specs/semantic/loading-validation-introspection.md` |
| Semantic-to-analysis handoff contract | `docs/specs/semantic/loading-validation-introspection.md` |
| Python analysis design | `docs/specs/analysis/python-analysis-design.md` |
| Agent usage examples | `marivo/skills/marivo-semantic/` |

## Documentation Updates

- After behavior changes, update affected user, spec, or skill files in the
  same change.
- When changing the public API, also update the example code in the `site/`
  documentation (versioned under `site/src/content/docs/*/latest/`). Keep
  both English and Chinese editions in sync.
- Update this guide only for stable repository-wide coding and testing rules.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.
