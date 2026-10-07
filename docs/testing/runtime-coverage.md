# Test goals and execution gates

Tests are organized by the behavior they protect. File names and test names describe
that behavior; development milestones and historical completion inventories are not
test boundaries. Runtime and release markers select execution cost and environment,
independently of the owning goal directory.

## Goal ownership

| Goal | Owning tests | Gate |
| --- | --- | --- |
| Datasource declarations, source IR, metadata, credentials and typed repair | `tests/datasource/test_datasource_*.py`, `test_source_ir.py` | Daily; marked real reads use Runtime |
| SQL submission authority and native driver ownership | `tests/datasource/test_sql_audit.py`, `test_driver_audit.py`, `test_sql_runtime.py` | Daily audit; Runtime native witnesses |
| Native deadlines, interrupts, cancellation and reader cleanup | `tests/datasource/test_*cancellation.py`, `test_source_deadline.py`; `tests/analysis/materialization/test_*interrupts.py`, `test_*deadline.py`, `test_*transport_phases.py` | Daily protocol checks; Runtime real execution |
| Semantic declarations, identity/versioning, metric equations and fanout | `tests/semantic/test_object_authoring_surface.py`, `test_metric_*.py`, `test_semantic_*.py` | Daily; marked source consumers use Runtime |
| Coherent authoring, validation and analysis handoff | `tests/semantic/test_coherent_slice_authoring.py`, `test_semantic_handoff.py`, `test_authoring_consumers.py` | Daily; Runtime source evidence |
| Typed graph rules, capture, lowering, admission and route planning | `tests/analysis/graph/` | Daily; marked public execution uses Runtime |
| Numeric observation, original-state aggregation, comparison, attribution and selection | `tests/analysis/numeric/` | Daily arithmetic/contracts; Runtime public behavior |
| Time grids, timezone parsing, windows and temporal folds | `tests/analysis/temporal/` | Daily kernels; Runtime source/fixed execution |
| Journey matching, Duration, Funnel Findings, anchors and retention | `tests/analysis/journey/` | Daily independent oracles; Runtime public and process boundaries |
| History replay, completeness, views and captured observations | `tests/analysis/lifecycle/` | Daily independent oracles; Runtime source-free continuation |
| Deviation, maximal runs, association and forecast | `tests/analysis/statistics/` | Daily numerical/state oracles; Runtime composed execution |
| Artifact integrity, atomic publication, crash recovery and cold ownership | `tests/analysis/materialization/` | Daily Store/schema checks; Runtime process/resource boundaries |
| Analysis session ownership and persistence | `tests/analysis/session/` | Daily; marked real source integration uses Runtime |
| Exact public exports, Help reachability/budgets, result/error guidance and documentation examples | `tests/surface/` | Daily; executable examples use Runtime |
| Config, refs, CLI, doctor, preview, telemetry and Make routing | `tests/project/` | Daily |
| Concrete callable/type contracts | `tests/typing/`, `tests/surface/test_*typing.py` | Typecheck; daily rejected-call probes |
| Archive integrity, package extras and packaged resource layout | `tests/packaging/test_archives.py`, `test_metadata.py`, `test_skills.py` | Daily; real candidate archive validation also runs in release |
| Installer behavior and installed-package origin, surface and recovery | `tests/packaging/test_installer*.py`, `test_wheel.py` | Release |
| Explicit installed native-source journeys | `tests/packaging/test_installed_sources.py` | Opt-in release; requires selected services |

Fixtures and suite policies remain in `tests/conftest.py`; reusable pure builders remain
in `tests/shared_fixtures.py`. Focused helper modules live with their owning behavior.
Shared root resolution, JSON transport, documentation readers and typing runners live
in `tests/support/`. Tests and workers do not import helpers from test modules.

## Coverage consolidation

| Removed or merged coverage | Current owner and retained boundary |
| --- | --- |
| Duplicate public export lists and historical removed-symbol inventories | `tests/surface/exports.py` owns independent expectations; `test_exports.py` checks exact current membership, ordering and visibility. Current focused Help and typing checks retain reachability and invalid-call boundaries. |
| Historical module/producer/partial-envelope permutations and removed-file archive lists | `tests/analysis/materialization/test_analysis_graph_publication.py` mutates complete current descriptors, schemas, fields and variants. Current artifact-read tests retain malformed evidence, receipt and authority rejection; archive validation compares every current package file and resource hash. |
| Metric migration wording, enum existence and duplicate constructor smoke tests | `tests/semantic/test_semantic_authoring.py`, metric value/additivity tests and live error tests retain real IR construction, invalid input and structured repair. Datasource-only IR cases moved to `tests/datasource/test_source_ir.py`. |
| Duplicate datasource missing-target and default rendering tests | Metadata tests retain the stronger typed-error, truncation, bounded output and continuation assertions. |
| Retired pre-live introspection descriptors, renderers and summary derivation | Current native Help reachability, budgets and callable bindings remain in `tests/surface/`; `test_core.py` retains the shared Constraint payload and summary checks. Public temporal tests retain fixed-width and calendar-variable behavior without the unused duration helper. |
| Decimal precision × storage × three-process Cartesian replay | Pure numerical laws retain all 779 valid Decimal(p,s) profiles for both scoring algorithms. `tests/analysis/statistics/test_deviation_decimal_recovery.py` retains nine endpoint/widening/rounding carrier profiles through independent producer, offline and cold processes. |
| Repeated key × storage × temporal lifecycle/History/anchor matrices | All nine key pairs retain Parquet UTC-us execution; compound keys retain every original temporal and storage variant. Independent native precision, receipt and cancellation checks remain separate. |
| Repeated statistical storage and fit/continuation combinations | Ordinary shared behavior uses Parquet. Each distinct original-state, fit, key, time, resource and source-free recovery risk retains an owning test. Native engine cases remain where the execution mechanism is itself the assertion. |
| Installed-wheel replay of development test suites | One isolated candidate validates archive hashes, dependencies, noneditable origin, poisoned-source rejection, console/Help and representative public graph, numeric, statistical, Funnel and History continuations in separate processes. Functional matrices run under their own goals. |
| Installed native-source replay of deleted test paths and incidental numerical/query-budget replay | Current `source_probe.py` retains actual source privilege, schema-failure, offline and cold witnesses through qualified scalar relationship routes; native trace audits bind SQL to driver calls. Dedicated native C05/C10 tests own query accounting and distinct qualification. The obsolete cluster-only replay had no current test implementation and was removed. |

Qualification receipt IDs and protocol versions can contain historical identifiers.
These are stable product identities, not executable test paths, and are preserved by
this test reorganization. Local historical evidence and archived design documents are
not recurring test inputs and are not rewritten by cleanup.

## Commands

- `make test`: complete daily selection.
- `make test TESTS='tests/semantic/test_metric_roots.py'`: one behavior target.
- `make runtime-test TESTS='tests/analysis/materialization/test_numeric_recovery.py'`: one independent-process boundary.
- `make check-agent`: full daily tests, lint/import contracts, typing and API docs.
- `make release-test`: candidate archives, installers and installed-package checks.
- `make release-check`: full daily, Runtime and packaging gates during release preparation.
- `MARIVO_INSTALLED_MULTISOURCE_TEST=1 make installed-multisource-test`: explicitly selected existing native services; see [source environment setup](../../tests/datasource/environment/README.md).

The daily selection excludes `runtime` and `release`; Runtime selects `runtime`;
release targets run serially with `release`. These sets do not overlap. Use explicit
`-m runtime` with direct pytest commands: naming a Runtime file does not override the
default marker exclusion. Runtime Make targets default to two workers; a selected node
containing `::` runs serially.

Installed tests normally read candidates from `dist/pypi`. `MARIVO_TEST_WHEEL_DIR` may
point them at an isolated build directory so qualification does not replace existing
local build artifacts. Candidate validation compares the complete package source and
resource hashes against the current checkout before installation.

Unavailable services are reported as skipped or unverified. Collection, pure/static
checks, local Runtime and installed-package checks provide distinct evidence; none
implies that an unrun external backend or full Runtime gate passed.
