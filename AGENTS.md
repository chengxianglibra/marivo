# AGENTS.md

Marivo is a Python library for datasource access, semantic authoring, and analysis,
consumed primarily by agents through a write-run-read loop. This file owns stable
repository-wide decisions; API details and procedures belong in the linked docs.
`CLAUDE.md` must remain a relative symlink to this file.

## Repository Conventions

- Keep code, comments, tests, fixtures, generated code and user-facing strings in
  English. Localized documentation may use its target language.
- Editing this file or the packaged `marivo-semantic` and `marivo-analysis` skills
  requires explicit user authorization.
- Use [marivo-review](.agents/skills/marivo-review/SKILL.md) for the project review
  contract, including formal reviews in the team development workflow.
- Route library access to private state through typed operations owned by the
  defining module. Document individual private accesses at the owning boundary
  when a protocol or third-party interface requires them. Respect the complexity
  limits and use explicit `@override` on inherited methods.

## Contract Navigation

Use [architecture navigation](docs/architecture/README.md) for owning contracts
and `marivo.help("<module>.<target>")` for the current installed syntax. The public
modules are `datasource` (`md`), `semantic` (`ms`), `analysis` (`mv`) and
`ontology` (`mo`).

| Scope | Owning documentation |
| --- | --- |
| Datasource declarations, discovery and credentials | [Datasource](docs/architecture/datasource/README.md) |
| Semantic design and authoring | [Semantic overview](docs/architecture/semantic/overview.md), [authoring](docs/architecture/semantic/authoring-workflow.md) |
| Ontology declarations and lookup | `marivo.help("ontology")`, [exports](marivo/ontology/__init__.py) |
| Analysis and time | [Analysis](docs/architecture/analysis/README.md), [temporal semantics](docs/architecture/temporal-semantics.md) |
| Help, result display and errors | [Agent-facing public surface](docs/architecture/agent-friendly-public-surface.md) |
| Dependencies, engine boundaries and static gates | [Architecture boundaries](docs/architecture/architecture-boundaries.md) |
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

Use [Make targets](Makefile) or the project virtual environment's explicit tool
paths. See [development setup](README.md#development) and install the
[`docs` extra](CONTRIBUTING.md) for Sphinx checks.

| Purpose | Command |
| --- | --- |
| Focused tests | `make test TESTS='tests/test_file.py'` |
| Focused typing | `make typecheck TYPECHECK_TARGETS='marivo/path'` |
| Focused lint | `make lint LINT_TARGETS='marivo/path tests/test_file.py'` |
| Routine local CI during development | `make check-fast` |
| Full pre-push gate | `make check` |
| Relevant Runtime tests | `make runtime-test TESTS='tests/test_file.py'` |
| Release acceptance | `make release-check` |

- Modified Python modules must pass typing. Public parameters and returns require
  concrete types, not `Any`; implicit `Any`, broad casts and type ignores require
  strict local justification. Test current contracts, not retired compatibility shapes.
- Keep runtime and type-only dependencies acyclic. Resolve cycles at the owning
  boundary. Function-local imports must never hide a cycle.
- Use public interfaces or the declared SPI across domains. Keep engine-specific
  decisions in Datasource. Never add Import Linter exemptions or raise the
  architecture ratchet.
- Use `make check-fast` as the default local CI check during day-to-day development.
  Start with focused checks and run `make test` for shared behavior changes. Run
  explicit tests for subprocess, SQL or data changes. For docs-only edits, validate
  affected content, links and diffs.
- Pass `make check` without narrowing variables before pushing. Its default suite
  excludes Runtime and release tests; run relevant Runtime tests separately.
  In the team development workflow, run the same full gate before committing
  shared behavior changes, after integrating review repairs and acceptance:
  `env -u TESTS -u LINT_TARGETS -u TYPECHECK_TARGETS -u PYTEST_ADDOPTS make check`.
- Reserve full release acceptance for release work. Do not start MinIO for ordinary
  commit preparation. For relevant Runtime validation, use Podman and the
  `marivo-multisource` Compose project. Follow the
  [environment instructions](tests/datasource/environment/README.md) and reuse running services.
- Distinguish static, local, provider Runtime, installed-package and release evidence.

## Library Decisions

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
- Domain exceptions follow the owning hierarchy, including `OntologyError` under
  `SemanticError`. Cross-surface Help exceptions may extend `MarivoError`. Use structured
  fields and shared rendering. State expected/received input and a concrete repair
  based on real state; never invent candidates or silently fall back.
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
