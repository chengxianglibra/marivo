# AGENTS.md

Stable repository-wide rules. Change this file only when the user explicitly
requests or approves it. Task-specific procedures belong in the owning skill
or domain documentation.

## Scope and authorization

- For non-trivial work, state the intended outcome, assumptions, tradeoffs, and
  verifiable success criteria before editing; then complete the authorized work.
- Use context to resolve routine implementation choices. Ask only when missing
  information materially affects business meaning, scope, correctness, or
  authorization. Continue independent work while that question is unresolved.
- Explicit user instructions take precedence over skill guidelines. Reuse
  authorization already given for the same action and scope. A skill invocation
  does not by itself authorize unrelated external writes, publication, messages,
  destructive cleanup, or changes to shared infrastructure. Prepare a reviewable
  result before requesting any still-missing authorization.
- If an instruction blocks completion, cite its file and relevant rule, explain
  the concrete blocker, and distinguish a requirement from your interpretation.
- Make the smallest coherent change, match existing style, and preserve unrelated
  work, including staged changes. Do not add speculative abstractions or cleanup.
- Keep source, comments, tests, fixtures, generated code, and code-owned user
  strings in English; localized documentation is the explicit exception.
- Report the outcome, relevant validation, and material limitations concisely.
  Distinguish proposed design from implemented and observed behavior.

## Python and verification

- Never use bare `python`, `pytest`, `mypy`, or `ruff`. Use repository Make targets
  or explicit `.venv/bin/...` paths (`.venv/Scripts/...` on Windows).
- During iteration prefer `make test-agent TESTS='tests/test_file.py'`,
  `make typecheck-agent TYPECHECK_TARGETS='...'`, and
  `make lint-agent LINT_TARGETS='...'`. Use verbose output only for failures that
  need it. The compact targets preserve failures and exit status.
- Touched Python modules must pass typing. Public parameters and returns use
  concrete types; do not add implicit `Any`, broad casts, or `# type: ignore`
  without a necessary, local justification.
- Reuse fixtures in `tests/conftest.py` and pure builders in
  `tests/shared_fixtures.py`. Preserve worker and mutable-state isolation.
- Verify the changed contract with the narrowest useful checks. Test observable
  behavior and durable invariants; do not add tests that only mirror incidental
  wording or a reversible formatting edit.
- Broaden to `make test` for shared behavior. Use `make check-agent` for the full
  lint, typecheck, default-test, and API-docs gate; it covers the same scope as
  `make check`. Release-specific gates remain owned by the release workflow.
- After applicable checks pass, broaden or repeat only for new changes, failures,
  or unresolved risk. Report checks that could not run and their impact.

## Library contracts

The public surfaces are `marivo.help`, `marivo.datasource`, `marivo.semantic`,
and `marivo.analysis`. Use `import marivo` for focused help and `md`, `ms`, and
`mv` for the respective execution modules. Execution aliases such as
`marivo.session` are unavailable at the top level.

- Python-track expression bodies return Ibis expressions only and remain within
  `marivo/semantic/validator.py`. SQL text belongs in provenance value objects
  such as `ms.from_sql(sql=..., dialect=...)`, not executable expression bodies.
- Semantic definitions own reusable business meaning; analysis consumes governed
  inputs and owns question-specific computation. Physical evidence cannot decide
  business meaning. Follow the owning packaged skill for authoring and handoff.
- New exceptions subclass `DatasourceError`, `SemanticError`, or `AnalysisError`
  for their surface, carry structured fields, and use shared rendering templates.
  State expected input, received input, and a concrete repair from real state;
  never invent candidates or silently fall back.
- Top-level Frame APIs are immutable; only `frame.to_pandas()` returns an isolated
  copy. Helpers consuming frames enforce cross-session ownership.
- Author credentials as `*_env` references, never secrets in project state.
  After a validated datasource round-trip, Marivo may cache resolved secrets in
  plaintext user-global `~/.marivo/secrets.toml`. Persistent semantic and analysis
  state belongs under `<project_root>/.marivo/`.
- Public functions need docstrings with purpose, parameters, return value, a
  usage example, and constraints. Every public symbol resolves through
  `marivo.help("datasource.<target>")`, `marivo.help("semantic.<target>")`, or
  `marivo.help("analysis.<target>")`.

## Agent-facing disclosure

- Keep one owner per fact: live Help owns static API/navigation; `.show()` owns
  bounded current state; analysis artifact `.contract()` owns mechanically valid
  continuations; structured errors own repair; packaged skills own workflow and
  judgment. Datasource and semantic authoring expose operations, effects, input
  facts, errors, and repairs without a shared lifecycle graph. Terminal results
  such as `RawSqlResult` remain contract-free and cannot enter typed analysis.
- Every public result has a bounded single-line repr with kind, identity, and a
  `.show()` pointer; default dataclass reprs are insufficient. Terminal cards
  order content deterministically and show only state-dependent continuations,
  not a repeated capability matrix.
- Keep one canonical public path per capability. Pin `__all__` with snapshot
  tests; exclude internal handoffs, type aliases, and "internal, use X" symbols
  from top-level discovery. New result types join an existing family or justify
  a new one. Prefer closed kind-dispatched variants over optional-field unions.
- Prior-result reuse is a weak dependency: retain one current contract and remove
  legacy artifacts, aliases, migrations, and dual reads unless the owning
  contract explicitly requires compatibility.
- `marivo.help()` introduces core concepts and routes only to `authoring` or
  `analysis`. Discover qualified targets beneath those roots; each focused
  symbol has a minimal runnable example. Keep navigation bounded, progressive,
  independently resolvable, and owned by native registries, without renderer
  shadow inventories.
- Any public export, callable/type, Help target, result/error guidance, or dynamic
  continuation change is one disclosure-contract change. Align the affected
  implementation, registry and budgets, guidance, independent drift/reachability/
  budget tests, examples, skills, CLI, and current docs in the same change.

## Documentation routing

Read the owning docs before changing behavior. Committed specs define intent;
if code and docs disagree, establish the current contract before editing.

| Task | Read first |
| --- | --- |
| Datasource or semantic design | `docs/specs/semantic/overview.md` and focused public Help |
| Datasource declarations and discovery | `docs/specs/semantic/datasource-layer.md` |
| Semantic object model | `docs/specs/semantic/semantic-object-model.md` |
| Semantic authoring | `docs/specs/semantic/authoring-workflow.md` |
| Loading, validation, catalog, analysis handoff | `docs/specs/semantic/loading-validation-introspection.md` |
| Analysis design | `docs/specs/analysis/python-analysis-design.md` |
| Public result and guidance protocol | `docs/specs/agent-friendly-public-surface.md` |
| Agent workflow | `marivo/skills/marivo-semantic/SKILL.md` or `marivo/skills/marivo-analysis/SKILL.md` |

Update affected specs and user documentation with behavior changes. Public API
changes also update examples in both English and Chinese latest site docs under
`site/src/content/docs/`. Load only the relevant skill references. Packaged
skills stay self-contained; code and tests must not depend on local `.agents/`.
