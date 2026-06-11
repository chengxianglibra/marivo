# Unified Datasource Surface Design

- Date: 2026-06-11
- Status: approved for planning
- Scope: collapse `marivo.analysis.datasources` into `marivo.datasource` as the
  single public datasource surface (breaking change, no compatibility facade)

## Problem

Datasource capabilities are split across two packages with the boundary in the
wrong place:

- `marivo/datasource/` holds only declaration concerns: `DatasourceSpec`,
  `md.datasource(...)`, `md.ref(...)`, `DatasourceIR`, the `.marivo/datasource`
  file loader, and `*_env` secret-literal validation.
- `marivo/analysis/datasources/` holds the rest of the domain as
  `mv.datasources`: file store (spec-to-file codegen), backend construction,
  secret resolution and the user-global cache, table metadata inspection,
  preview/test diagnostics, and the semantic cross-check audit.

Observed costs of this split:

1. Four read surfaces with three result shapes: `mv.datasources.all()`,
   `project.list_datasources()`, `catalog.list(kind="datasource")`, and the
   public `md.load_datasources(...)`. Two distinct `DatasourceSummary`
   dataclasses exist with different fields
   (`marivo/analysis/datasources/registry.py` vs `marivo/semantic/reader.py`).
2. The semantic track needs analysis-owned capabilities and works around the
   forbidden `semantic -> analysis` import direction with `TYPE_CHECKING`
   imports of `TableMetadata` (`semantic/inspect.py`, `semantic/ledger.py`,
   `semantic/readiness.py`) and the runtime injection seam
   `SemanticProject.bind_datasource_access(...)` filled in by
   `analysis/session/attach.py`.
3. `analysis/executor/runner.py` calls the private
   `_persist_backend_env_sourced_secrets` across modules.
4. Datasource error types are split between `marivo/datasource/errors.py`
   (declaration errors) and `marivo/analysis/errors.py` (eight runtime
   `Datasource*Error` classes).
5. Connection lifecycles are inconsistent: `test()` disconnects, `preview()`
   leaks its backend, and the session-level `BackendCache` is a separate
   mechanism.
6. Agents must hold two near-identical namespaces (`marivo.datasource` to
   declare, `mv.datasources` to manage).

## Decision

Adopt the single-surface option: `marivo.datasource` (canonical alias `md`)
becomes the only public datasource namespace. `mv.datasources` is deleted with
no facade or deprecation period. On-disk formats (`.marivo/datasource/*.py`,
`~/.marivo/secrets.toml`) do not change; the break is Python-API-only.

Confirmed sub-decisions:

- Rename `build_backend` to `connect` and `all` to `list` on the unified
  surface.
- `audit_project` moves to the semantic side as
  `SemanticProject.audit_datasources()` (it consumes a `SemanticProject`;
  keeping it in the datasource package would invert dependencies).
- `backend_factory` parameters on semantic materialization/preview remain as
  explicit overrides but default to `md.connect`;
  `bind_datasource_access(...)` is deleted.
- The physical source descriptors `TableSourceIR`, `FileSourceIR`,
  `EntitySourceIR`, `source_name`, and `source_to_dict` move from
  `marivo/semantic/ir.py` to `marivo/datasource/ir.py`.

## Target architecture

```
marivo.preview / marivo.introspection      shared kernels (unchanged)
marivo.project (new tiny kernel)           project-root resolution
marivo.datasource                          datasource domain:
                                           declare + store + connect +
                                           secrets + metadata + diagnostics
        ^                  ^
marivo.semantic      marivo.analysis       one-way downward imports only
```

`marivo.semantic` and `marivo.analysis` both import `marivo.datasource`
directly. No datasource capability lives above the kernel; no injection seams
exist solely to dodge import direction.

## Public API (`import marivo.datasource as md`)

| Category | API | Notes |
|---|---|---|
| Declaration | `md.datasource(...)`, `md.DatasourceSpec`, `md.ref(...)` | unchanged |
| Management | `md.register(spec)`, `md.remove(name)`, `md.list()`, `md.describe(name)` | `list()` replaces `all()` |
| Runtime | `md.connect(name)` | replaces `build_backend`; returns a live ibis backend; caller owns disconnect |
| Diagnostics / evidence | `md.test(name)`, `md.preview(name, table=...)`, `md.inspect_table(...)`, `md.inspect_source(...)` | `preview` gains try/finally disconnect (bug fix) |
| Help | `md.help()`, `md.help_text()` | now covers management, runtime, and diagnostics entries (moved from the analysis help registry) |

Surface reductions:

- `load_datasources` leaves `__all__`; it stays importable as loader plumbing
  for `semantic/loader.py` and the store.
- Read surfaces collapse to two with distinct purposes: `md.list/describe`
  (management view over project files) and `catalog.list/get` (semantic browse
  over a loaded project). Both are backed by the same `DatasourceIR`.
- One `DatasourceSummary(name, backend_type, description)` defined in
  `marivo.datasource`; `project.list_datasources()` returns
  `DiscoveryResult[DatasourceSummary]` using this type (per the 2026-06-09
  agent-friendly public API design, the discovery shape is unchanged).
  `DatasourceDescription` and `DatasourceTestResult` move with the management
  module.

All public functions keep the repository docstring contract (purpose, params,
return, example, constraints) and concrete types; `describe`/`md.help` cover
every public symbol.

## Package layout

```
marivo/datasource/
  __init__.py      re-exports the full public surface
  authoring.py     existing (DatasourceSpec, ref, datasource, validation)
  ir.py            existing + TableSourceIR/FileSourceIR/EntitySourceIR/
                   source_name/source_to_dict moved from semantic/ir.py
  loader.py        existing
  errors.py        existing + eight runtime errors moved from analysis
  help.py          existing + management/diagnostic entries
  constraints.py   existing + constraints for moved errors
  typing.py        existing
  store.py         moved from analysis/datasources (uses marivo.project root)
  backends.py      moved (connect dispatch per backend_type)
  secrets.py       moved (provider chain + persist_backend_env_sourced)
  metadata.py      moved (inspect_table/inspect_source + TableMetadata DTOs)
  manage.py        register/remove/list/describe/connect/test/preview
                   (old registry.py minus duplicated DTOs)
marivo/project.py  resolve_project_root moved from analysis/session/active.py
```

`marivo/analysis/datasources/` is deleted. `audit.py` content moves to the
semantic package as the implementation behind
`SemanticProject.audit_datasources()`.

## Structural moves

1. Source descriptors: `TableSourceIR`, `FileSourceIR`, `EntitySourceIR`,
   `source_name`, `source_to_dict` are pure data/string helpers and move to
   `marivo/datasource/ir.py`. This unties `metadata.inspect_source` from
   `marivo.semantic.ir` so the metadata module can live in the kernel.
   Semantic-internal imports update to the new home; no re-export shim.
2. Project root: `resolve_project_root` moves from
   `analysis/session/active.py` to a new `marivo/project.py`; the store,
   `semantic.catalog.load()` (which currently re-implements env/cwd fallback),
   and `analysis/session/active.py` all use it.
3. Secrets flow: `persist_env_sourced` and the backend attribute stash merge
   into a single public function
   `marivo.datasource.secrets.persist_backend_env_sourced(backend)`;
   `connect()` records env-sourced secrets on the backend; `runner.py` and
   `test()` call the public function.
   No more cross-module private access.
4. Errors: `DatasourceMissingError`, `DatasourceEnvVarMissingError`,
   `DatasourceConnectionError`, `DatasourcePreviewError`,
   `DatasourceMetadataError`, `DatasourceBackendTypeUnsupportedError`,
   `DatasourceSecretStorePermissionsError`, `DatasourceSchemaVersionError`
   move from `marivo/analysis/errors.py` to `marivo/datasource/errors.py`.
   The base `DatasourceConfigError` is renamed `DatasourceError` (template
   rendering machinery unchanged); declaration and runtime errors both
   subclass it. No `except AnalysisError` sites exist in `marivo/`, so the
   rebase is behavior-safe inside the library. Hint and `fix_snippet` strings
   that mention `mv.datasources.*` are rewritten to `md.*`. The agent guide's
   exception rule adds `DatasourceError` as a sanctioned base.
   `NoBackendFactoryError` stays in analysis (session concern).

## Call-site migration

Semantic track:

- `TYPE_CHECKING` imports of `TableMetadata` become real imports from
  `marivo.datasource.metadata`.
- `bind_datasource_access(...)` and its bound-slot plumbing are deleted.
  `_resolve_backend_factory` falls back to `md.connect`;
  the `backend_factory` parameter remains for explicit override (tests inject
  in-memory duckdb as before). The committed contract in
  `docs/specs/semantic/python-semantic-layer.md` ("the semantic layer does not
  construct connections itself") is relaxed to "defaults to project
  datasources via `md.connect`; callers may inject an override".
- `reader.DatasourceSummary` is deleted in favor of the unified type.

Analysis track:

- `attach.py` uses `md.connect` as the datasource-backed factory and drops the
  `bind_datasource_access` call.
- `BackendCache` stays in `analysis/executor` (session-scoped caching is an
  analysis concern); it caches `md.connect` results.
- `analysis/__init__.py` removes the `datasources` lazy export and its
  `__all__` entry; `analysis/help.py` drops the `datasources` entry.
- `constraints.py` and `errors.py` hint texts switch to `md.*` invocations.
- `scripts/upload_html_report.py` imports secrets from
  `marivo.datasource.secrets`.

## Documentation and skill updates (same change)

- `docs/specs/semantic/python-semantic-layer.md`: datasource chapter,
  materializer `backend_factory` contract, `mv.datasources.*` references.
- `marivo-skills/marivo-semantic/`: `SKILL.md` plus `references/datasource.md`,
  `preview.md`, `closeout.md`, `evidence-and-ledger.md`.
- `marivo-skills/marivo-analysis/SKILL.md`.
- `agent-guide.md`: public-surface description for `marivo.datasource` and the
  exception base rule. Archived docs and dated plans are not rewritten.

## Tests

- ~18 test files reference `mv.datasources`/`marivo.analysis.datasources`;
  call sites update mechanically (`register` dominates with ~48 uses, then
  `metadata`/`inspect_table`).
- `tests/test_analysis_imports.py` export assertions update.
- `test_analysis_session_profile_integration.py` audit test switches to
  `project.audit_datasources()`.
- New coverage: `md.preview` closes its backend (regression for the leak);
  unified `DatasourceSummary` shape via both `md.list()` and
  `project.list_datasources()`.

## Non-goals

- No connection pooling or cross-session backend caching in the kernel.
- No change to `.marivo/datasource/*.py` file format or store codegen.
- No change to `~/.marivo/secrets.toml` format or the provider chain order.
- No change to preview/inspect semantics beyond the disconnect fix.
- No federation or new backend types.

## Success criteria

- `grep -r "analysis.datasources\|mv\.datasources" marivo/ tests/ docs/specs/
  marivo-skills/` returns no live references (archives excluded).
- `make test`, `make typecheck`, `make lint`, and `make examples-check` pass.
- `md.help()` lists declaration, management, runtime, and diagnostic entries;
  every public `md` symbol satisfies the docstring/describe contract.
- Exactly one `DatasourceSummary` class exists in the library.
- `marivo/semantic/` contains no `TYPE_CHECKING` imports from analysis modules
  and no `bind_datasource_access` seam.
