# AGENTS.md

Marivo is a Python library for datasource access, semantic authoring, and
analysis, consumed primarily by agents through a write-run-read loop.
Keep this file focused on stable repository-wide rules; put task procedures
in project-local skills, README files, or owning domain documentation.

## Working Rules

- Make the smallest change that satisfies the request. Avoid speculative
  abstractions, future placeholders, and over-engineered defensive design,
  implementation, or acceptance validation.
- Inspect the working tree before editing; preserve unrelated local changes
  and match the surrounding style. Review requests are read-only unless the
  user authorizes edits.
- For non-trivial work, state material assumptions, tradeoffs, and verifiable
  success criteria. Resolve routine choices independently; ask when ambiguity would
  make the change risky.
- Treat committed specs and docs as sources of truth. If implementation and
  documentation disagree, establish the intended current contract before
  changing behavior. Proposals and plans alone do not prove implementation.
- Keep source code, comments, tests, fixtures, generated code, and user-facing
  strings in English. Localized documentation may use its target language.
- Editing this file or either packaged skill (`marivo-semantic` and
  `marivo-analysis`) requires explicit user approval. A request to edit the
  named file grants that approval; general implementation or documentation
  work does not implicitly authorize packaged-skill edits.

## Read the Owning Contract

Read only the routes relevant to the task, then follow focused references.
For datasource or semantic work, start with the overview and focused live Help.

| Task | Read first |
| --- | --- |
| Datasource or semantic design | `docs/architecture/semantic/overview.md`; `marivo.help("datasource.<target>")` or `marivo.help("semantic.<target>")` |
| Datasource declarations and discovery | `docs/architecture/datasource/README.md` |
| Semantic object model | `docs/architecture/semantic/semantic-object-model.md` |
| Semantic authoring | `docs/architecture/semantic/authoring-workflow.md` |
| Loading, validation, runtime, and analysis handoff | `docs/architecture/semantic/loading-validation-introspection.md` |
| Python analysis | `docs/architecture/analysis/README.md` |
| Agent workflow and boundaries | `marivo/skills/marivo-semantic/SKILL.md` or `marivo/skills/marivo-analysis/SKILL.md` |

## Documentation Placement

Choose the destination by the document's purpose:

| Content | Target directory |
| --- | --- |
| Current architecture, module responsibilities, data flow, and long-term contracts | `docs/architecture/`, under the owning domain where applicable |
| Public Python API reference sources and Sphinx configuration/templates | `docs/api/` |
| Design proposals, alternatives, design reviews, and historical proposals | `docs/design/` |
| Implementation plans, task/progress ledgers, and implementation records | `docs/implementation/` |
| Test strategies, scenario validation, acceptance records, and test evidence | `docs/testing/`; raw logs and evidence files belong in `docs/testing/evidence/` |

- `docs/design/`, `docs/implementation/`, and `docs/testing/` are local-only,
  Git-ignored directories. Keep temporary task documents and evidence there,
  rather than under `docs/architecture/`.
- Update the owning architecture document when an implemented change alters
  a current contract. Architecture documents must not depend on local-only
  plans, progress records, or evidence as normative references.
- User-facing guides and examples belong in `site/src/content/docs/*/latest/`;
  keep English and Chinese editions aligned. Generated API pages belong in
  `site/public/api/` and are produced from `docs/api/`.

## Python and Validation

Use repository entrypoints or explicit `.venv/bin/...` paths. Never invoke
bare `python`, `pytest`, `mypy`, or `ruff` in this repository.

| Purpose | Command |
| --- | --- |
| Focused tests | `make test TESTS='tests/test_file.py'` |
| Focused typing | `make typecheck TYPECHECK_TARGETS='marivo/path'` |
| Focused lint | `make lint LINT_TARGETS='marivo/path tests/test_file.py'` |
| Default tests | `make test` |
| Local inner loop | `make check-fast` |
| Broad regular gate | `make check` |
| Relevant Runtime tests | `make runtime-test TESTS='tests/test_file.py'` |
| Full release acceptance | `make release-check` |

- Modified Python modules must pass typing. Do not introduce implicit `Any`,
  broad `cast(...)`, or `# type: ignore` without strict local justification.
  Public API parameters and return values require concrete, specific types;
  they must not use `Any` or other ambiguous types.
- Reuse `tests/conftest.py` and `tests/shared_fixtures.py` for repeated setup.
  Test the current owning contract, not retired compatibility shapes.
- Start with the narrowest useful checks; broaden to `make test` when shared
  behavior changes. Narrow runs do not replace a required broad final gate.
  For documentation-only edits, validate affected content, paths, and diffs.
- `make test` uses short tracebacks and stops after five failures. If needed,
  rerun only the failing scope with `.venv/bin/pytest` and suitable verbosity.
- `make check` covers lint, typing, default-test, and API-doc stages.
  Run it without narrowing variables for broad evidence.
  Default tests exclude Runtime and release tests.
- `make check-fast` is a local inner loop: lint, typecheck, pytest-testmon
  (only tests affected by edits), and an incremental API docs build, in
  parallel. It does not track subprocess-executed code or SQL and data file
  changes. It does not replace `make check`. The full gate must pass before
  pushing. CI and `make release-check` keep the full suite and the clean docs
  build. `make test` worker count follows logical CPUs (`nproc` on Linux,
  `sysctl -n hw.ncpu` on macOS), capped at 8; override with `PYTEST_WORKERS`.
- Run only relevant Runtime tests during development. Full release acceptance
  belongs to release preparation and publishing CI; do not run it or start
  MinIO for ordinary commit preparation.
- Agents may use Colima to start backend services needed for relevant Runtime
  validation without requesting additional permission. Follow
  `tests/datasource/environment/README.md`, use the dedicated
  `marivo-multisource` environment, and reuse already-running services.
- Repair failures caused by the change and rerun relevant checks. Report what
  actually ran and any blockers; distinguish static, local, Runtime, and release
  evidence.

## Public Library Contracts

The public entry points are `marivo.help`, `marivo.datasource`,
`marivo.semantic`, and `marivo.analysis`. Use `import marivo` for Help and
`import marivo.datasource as md`, `import marivo.semantic as ms`, and
`import marivo.analysis as mv` for execution. There is no top-level
`marivo.session` or equivalent execution alias.

### Semantic Expressions and Resolution

- Entity/Source and Dimension/TimeDimension bodies retain restricted Ibis
  contracts. Measure/Metric declarations take zero arguments and return closed
  typed row/Metric expressions over constructed dependencies; they do not
  execute arbitrary Python, accept Ibis tables, or use `ms.bind`.
  Decorator bodies remain restricted by `marivo/semantic/validator.py`.
- Physical constructors and `ms.ref.*` capture native typed Deferred symbols.
  Compose them directly; the loader binds the complete dependency scope and
  publishes closed Catalog values. Bare lookup symbols are not closed analysis
  inputs. Never mutate resolved values, refresh from live context, or replay
  imports or module effects. Reference-resolving consumers use their completed scope.
- Do not require original definition versions or reject inputs solely because
  same-path definitions changed. Users accept the semantic consequences;
  preserve kind, root, grain, unit, time-role, and null correctness checks.
  Fingerprints and cache keys describe consumed definitions; persisted
  provenance and records retain their owning contracts.
- SQL belongs only in explicitly supported provenance contracts, never in
  executable declaration bodies.

### State, Types, and Errors

- Top-level Frame APIs remain immutable; only `frame.to_pandas()` returns an
  isolated copy. Helpers consuming frames must enforce cross-session ownership.
- Ordinary source and local arithmetic follow their actual backend or kernel.
  Do not add generic overflow checks, finite-value scans, protective widening,
  high-precision emulation or failure retries. Preserve operation-specific and
  representation/storage contracts; see [numeric policies](docs/architecture/analysis/operators-and-frames.md#null-empty-and-arithmetic-policies).
- Ordinary analysis trusts declared source identity, relationship cardinality
  and version grain. Do not add full-source audits to re-prove declarations;
  retain explicit `source_check` and required output/operation integrity checks.
  See [declaration trust](docs/architecture/analysis/populations-and-observation.md#population-identity-and-set-semantics).
- Fixed-data Frame operations and reopening consume captured results without
  implicit source reads or producer replay. Cross-process reuse requires explicit
  `persist()` and exact saved identities; new source dependencies remain explicit.
  Do not introduce automatic persistence or cross-process result caches.
  See [result retention](docs/architecture/analysis/session-state-and-runtime.md#session-and-invocation-ownership).
- Persistent semantic and analysis state belongs in `<project_root>/.marivo/`.
  Author credentials as `*_env` references; never write secrets to project
  state. After a validated datasource round-trip, resolved secrets may be
  cached in plaintext user-global state at `~/.marivo/secrets.toml`.
- New exceptions inherit from the owning `DatasourceError`, `SemanticError`,
  or `AnalysisError`, carry structured fields, and use shared template rendering.
  Typed errors state expected input, received input, and a concrete repair.
  Build suggestions from real state; never hardcode candidates or silently fall
  back.
- Prefer one entry shape with closed, kind-dispatched variants over classes
  with many optional fields. New public result types join an existing naming
  and protocol family or justify a new one.

## Agent-Facing Disclosure

Keep each fact with one owner:

| Owner | Responsibility |
| --- | --- |
| CLI / Python Live Help | Static API facts, expression grammar and navigation |
| Result `.show()` and typed fields | Captured definitions and committed state |
| Structured errors | Concrete repair guidance |
| Packaged skills | Workflow boundaries and judgment |

- Each capability has one canonical public entry point. Within the changed
  scope, remove legacy artifacts, aliases, migrations, and dual reads unless
  the owning contract explicitly requires them; prior result reuse is a weak
  dependency. Internal alternatives must not appear in `__all__`.
- Public function docstrings cover purpose, parameters, return value, a usage
  example, and constraints. Every public API symbol resolves through
  `marivo.help("datasource.<target>")`, `marivo.help("semantic.<target>")`, or
  `marivo.help("analysis.<target>")`.
- Help targets are lowercase snake_case semantic paths, independent of Python
  symbols. CLI `python -m marivo help <target>` and Python use the same native
  registry. Named pages disclose Target and Kind. Attribute/field targets have
  Kind attribute and are queried by string without executing a getter. Native
  Deferred syntax is explicitly owned by its semantic role, not inferred from Ibis.
- `marivo.help()` introduces core concepts and routes only to `"authoring"`
  or `"analysis"`. Discover focused targets beneath those roots, with a minimal
  runnable example for each owning symbol. Keep routes bounded and targets
  independently resolvable; exclude type aliases and internal handoff types
  from the top-level Help index.
- Pin public `__all__` sets with snapshot tests. Public results require a
  bounded single-line repr with kind, identity, and a pointer to `.show()`;
  default dataclass reprs are insufficient.
- Terminal results provide bounded, deterministically ordered `.show()` output.
  Artifact cards include state-dependent continuations, not a capability matrix.
  Query, Population and Column expose captured metadata through `.show()`;
  Query.output_names exposes complete names. Type Help owns method discovery.
  There is no generic analysis action contract. Independent semantic definition
  and time identity contracts retain their owning purposes.
  Datasource and semantic authoring disclose operations, effects, input facts,
  errors, and typed repairs without a shared lifecycle graph. Terminal boundaries
  such as `RawSqlResult` remain contract-free because they cannot enter typed
  analysis.
- Treat exports, callable/type contracts, Help targets, result/error guidance,
  and dynamic continuations as one disclosure contract. Align all affected
  implementation/API, native Help registry and budgets, dynamic guidance,
  independent drift/reachability/budget tests, examples, CLI, and current docs.
  Do not add renderer shadow inventories or unowned compatibility aliases.
- Update affected API/UI documentation and specs with behavior changes. Public
  API changes also update examples in `site/src/content/docs/*/latest/`, keeping
  English and Chinese editions aligned. If alignment requires packaged-skill
  edits, obtain the explicit approval described above before editing those
  files; identify any pending alignment rather than claiming completion.
