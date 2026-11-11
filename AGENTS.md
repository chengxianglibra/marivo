# AGENTS.md

Marivo is a Python library for datasource access, semantic authoring, and analysis,
consumed primarily by agents through a write-run-read loop. This file owns stable
repository-wide decisions; API details and procedures belong in the linked docs.
`CLAUDE.md` must remain a relative symlink to this file.

## Working Rules

- Make the smallest change that satisfies the request; avoid speculative abstractions
  and unnecessary defensive implementation or acceptance work.
- Inspect the working tree, preserve unrelated changes, and match surrounding style.
  Reviews are read-only unless edits are authorized.
- Resolve routine choices independently; disclose material assumptions and ask only
  when unresolved ambiguity makes the change risky.
- Use existing code conventions when documentation drifts, and report discrepancies.
  Establish the intended current contract before changing behavior; proposals and
  plans alone do not prove implementation.
- Keep code, comments, tests, fixtures, generated code and user-facing strings in
  English. Localized documentation may use its target language.
- Editing this file or packaged `marivo-semantic` / `marivo-analysis` skills requires
  explicit user authorization. A request to edit the named file grants it; general
  implementation or documentation work does not authorize packaged-skill edits.

## Contract Navigation

Read the routes relevant to the change, following focused references as needed.
Use `marivo.help("datasource.<target>")`, `marivo.help("semantic.<target>")` or
`marivo.help("analysis.<target>")` for current public syntax.

| Scope | Owning documentation |
| --- | --- |
| Datasource declarations, discovery and credentials | [Datasource](docs/architecture/datasource/README.md) |
| Semantic design and expressions | [Overview](docs/architecture/semantic/overview.md), [object model](docs/architecture/semantic/semantic-object-model.md), [expressions](docs/architecture/semantic/expressions.md) |
| Authoring, loading and validation | [Authoring](docs/architecture/semantic/authoring-workflow.md), [loading and validation](docs/architecture/semantic/loading-validation-introspection.md) |
| Analysis and time | [Analysis](docs/architecture/analysis/README.md), [temporal semantics](docs/architecture/temporal-semantics.md) |
| Help, result display and errors | [Agent-facing public surface](docs/architecture/agent-friendly-public-surface.md) |
| Tests and fixtures | [Test fixture skill](.agents/skills/marivo-test-fixtures/SKILL.md) |
| Agent workflow and boundaries | [Semantic skill](marivo/skills/marivo-semantic/SKILL.md), [analysis skill](marivo/skills/marivo-analysis/SKILL.md) |

## Documentation Ownership

- Current contracts belong in `docs/architecture/` under the owning domain. Update
  them when behavior changes; do not use local-only plans or evidence as normative references.
- Proposals belong in `docs/design/`, plans and progress in `docs/implementation/`,
  and validation records in `docs/testing/` (raw evidence in `docs/testing/evidence/`).
  These directories are Git-ignored and local-only.
- Keep user guides and examples aligned in `site/src/content/docs/docs/latest/`
  and `site/src/content/docs/zh-cn/docs/latest/`. API reference sources belong in
  `docs/api/`; generate `site/public/api/` rather than editing its output.

## Python and Validation

Use repository entrypoints or explicit `.venv/bin/...` paths, never bare `python`,
`pytest`, `mypy` or `ruff`. [Makefile](Makefile) owns execution flags and parallelism.

| Purpose | Command |
| --- | --- |
| Focused tests | `make test TESTS='tests/test_file.py'` |
| Focused typing | `make typecheck TYPECHECK_TARGETS='marivo/path'` |
| Focused lint | `make lint LINT_TARGETS='marivo/path tests/test_file.py'` |
| Local iteration | `make check-fast` |
| Broad final gate | `make check` |
| Relevant Runtime tests | `make runtime-test TESTS='tests/test_file.py'` |
| Release acceptance | `make release-check` |

- Modified Python modules must pass typing. Public parameters and returns require
  concrete types, not `Any`; implicit `Any`, broad casts and type ignores require
  strict local justification. Test current contracts, not retired compatibility shapes.
- Start with focused checks; run `make test` for shared behavior changes. For docs-only
  edits, validate affected content, links and diffs. Incremental `make check-fast` does
  not replace the broad gate or explicit tests for subprocess, SQL or data changes.
- Pass `make check` without narrowing variables before pushing. Its default suite
  excludes Runtime and release tests; run relevant Runtime tests separately.
- Reserve full release acceptance for release work. Do not start MinIO for ordinary
  commit preparation. Colima is authorized for relevant Runtime validation: follow
  [environment instructions](tests/datasource/environment/README.md), use the dedicated
  `marivo-multisource` environment, and reuse running services.
- Fix failures caused by the change and rerun relevant checks. Report actual evidence
  and blockers, distinguishing static, local, Runtime and release validation.

## Library Decisions

Public entry points are `marivo.help`, `marivo.datasource`, `marivo.semantic` and
`marivo.analysis`. Use `import marivo` for Help and the aliases `md`, `ms` and `mv`
for their respective submodules; there is no top-level `marivo.session`.

- Semantic values and top-level Frames remain immutable. Consumers use completed
  scopes and enforce session/project ownership, without ambient refresh or import replay.
- Source reductions declare or inherit their contribution clocks. Report scopes,
  grains and windows filter or bucket clocked branches; they never replace a Metric's clock.
- Do not require original definition versions merely because same-path definitions
  changed. Preserve kind, root, grain, unit, time-role and null correctness checks;
  fingerprints describe consumed definitions. Persisted identities retain their contracts.
- Follow native backend/kernel arithmetic; do not add generic overflow/finite scans,
  protective widening, high-precision emulation or retries. Preserve operation-specific
  and storage contracts; see [numeric policies](docs/architecture/analysis/operators-and-frames.md#null-empty-and-arithmetic-policies).
- Trust declared source identity, cardinality and version grain; do not add full-source
  audits. Keep explicit `source_check` and required operation/output integrity checks;
  see [declaration trust](docs/architecture/analysis/populations-and-observation.md#population-identity-and-set-semantics).
- Fixed-data operations and reopening use captured results without implicit source reads
  or producer replay. Cross-process reuse requires explicit `persist()` and exact saved
  identities; do not add automatic persistence or cross-process result caches. See
  [result retention](docs/architecture/analysis/session-state-and-runtime.md#session-and-invocation-ownership).
- Project semantic/analysis state belongs in `<project_root>/.marivo/`. Declare credentials
  with `md.env(...)`; never store secrets in project state. User-global secret caching
  follows the [datasource credential contract](docs/architecture/datasource/README.md#credentials-and-secret-persistence).
- Exceptions belong to `DatasourceError`, `SemanticError` or `AnalysisError`, with
  structured fields and shared rendering. State expected/received input and a concrete
  repair based on real state; never invent candidates or silently fall back.
- Keep one canonical public entry per capability. Within the changed scope, remove
  retired aliases, migrations and dual reads unless the owning contract requires them;
  prior result reuse is a weak dependency.

## Public Disclosure

Help owns static API facts and grammar; result display and typed fields own captured
facts; errors own concrete repairs; packaged skills own workflow and judgment.
Align affected exports, types, Help, result/error guidance, continuations, examples
and current docs as one contract, with independent drift/reachability/budget checks.
[Public surface contracts](docs/architecture/agent-friendly-public-surface.md) own
exact naming, display limits and verification rules; do not duplicate those inventories here.
