# R9.6 extension locations and controlled boundaries

Date: 2026-10-06

This record implements section 7.3 of the
[R9 plan](2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md).
The audit range is `1189a3da48..9c5d267f0d`: R9.1 freezes the physical
denominator, R9.2-R9.5 implement and retire the recorded extension locations.
The new R9.6 boundary tests are separate from the cost harness and from real
backend qualification. A controlled declaration is neither a seventh backend
nor an execution witness for an unqualified physical key.

## Commit dependencies

| Commit | Owning change |
| --- | --- |
| `1189a3da48` | R9.1: `scripts/r9_qualification_requirements.py` freezes the 19 physical profiles below. |
| `77c6fdd1f8` | R9.2: exact source decoding, close-failure accounting, selected-driver diagnostics and real source-profile tests. |
| `e931d458e0` | R9.3: exact method registrations, portable expression preparation, source capture authority, owned cancellation and business completeness. |
| `93498a7290` | R9.4: producer recovery, file registrations, Decimal row statistics, native interruption and resource cleanup. |
| `9c5d267f0d` | R9.5: exact provider purposes, native SQL ownership audit, source preview ownership and physical legacy retirement. |

The committed implementation is inspected with `git show <commit> -- <path>`.
The acceptance candidates and original evidence remain owned by the
[qualification ledger](2026-10-04-marivo-full-algebra-dsl-r9-qualification-ledger.md)
and [evidence index](2026-10-04-marivo-r9-evidence-index.md).
This file records change locations; it does not relabel previous execution
candidates or turn source smoke results into method acceptance.

## Shared Location Sets

Every profile row below inherits A/I/L/D/R/T/P. These sets list exact paths so
an unchanged provider module does not conceal a shared product modification.
Additional backend-specific paths and reasons follow in the next section.

| Set | Exact files modified in the audit range | Reason and commits |
| --- | --- | --- |
| A: adapter and binding | `marivo/datasource/adapters.py`, `marivo/datasource/backends.py`, `marivo/datasource/engines/base.py`, `marivo/datasource/errors.py`, `marivo/datasource/runtime.py`, `marivo/datasource/snapshot.py` | R9.2 selected connection failure and owned-stream state; R9.3 deadline-aware opens, qualified bindings and schema authority; R9.4 owned interruption. These are shared datasource changes, with explicit backend branches where native transports differ. |
| I: registration | `marivo/analysis/methods/builtin.py`, `marivo/analysis/methods/registry.py`, `marivo/analysis/methods/semantics.py`, `marivo/analysis/methods/domain_preparation.py`, `marivo/analysis/methods/anchor_physical.py`, `marivo/analysis/methods/deviation_physical.py`, `marivo/analysis/methods/funnel_physical.py`, `marivo/analysis/methods/history_physical.py`, `marivo/analysis/methods/history_view_physical.py`, `marivo/analysis/methods/journey_physical.py`, `marivo/analysis/methods/retention_physical.py`, `marivo/analysis/methods/runs_physical.py`, `marivo/analysis/methods/statistical_physical.py` | R9.3 exact backend/type/time/route declarations and closed parameter admission; R9.4 precise file/domain/Decimal extensions. A declaration for one table shape does not grant every profile of that backend. |
| L: expression and preparation | `marivo/analysis/compiler/anchors.py`, `marivo/analysis/compiler/domain_preparation.py`, `marivo/analysis/compiler/graph_lowering.py`, `marivo/analysis/compiler/graph_plan.py`, `marivo/analysis/compiler/numeric_sql.py`, `marivo/analysis/compiler/source_time.py`, `marivo/analysis/materialization/graph_axes.py`, `marivo/analysis/materialization/graph_composition.py`, `marivo/analysis/materialization/graph_members.py`, `marivo/analysis/materialization/graph_observation.py`, `marivo/analysis/materialization/graph_preparation.py`, `marivo/analysis/materialization/graph_preflight.py`, `marivo/analysis/materialization/graph_reference.py`, `marivo/analysis/materialization/graph_relation.py`, `marivo/analysis/materialization/graph_source_execution.py`, `marivo/analysis/materialization/temporal_sql.py` | R9.3 portable typed Ibis lowering, complete preparation and exact numeric/time expressions; R9.4 graph sharing and original-state repairs. The current typed lowering retains provider-specific physical branches. These are real compiler changes, not adapter-only extensions. |
| D: decode and validation | `marivo/datasource/adapters.py`, `marivo/analysis/materialization/graph_exchange.py`, `marivo/analysis/methods/state_validation.py`, `marivo/analysis/materialization/statistical_execution.py`, `marivo/analysis/materialization/runs_execution.py` | R9.2/R9.3 full-width scalar and UTC authority; R9.3/R9.4 complete primary/parts, unavailable state and statistical inputs. Arrow schema rejection and complete-part validation remain common boundaries. |
| R: resources and recovery | `marivo/datasource/domain_snapshot.py`, `marivo/datasource/adapters.py`, `marivo/datasource/interrupts.py`, `marivo/analysis/materialization/execute_deadline.py`, `marivo/analysis/materialization/resources.py`, `marivo/analysis/materialization/graph_local_execution.py`, `marivo/analysis/materialization/graph_publication.py` | R9.3 distinct transaction/backup/independent-read authority and deadlines; R9.4 signal wakeup, owned query cancellation, local checkpoints and producer publication. Native server termination and local release remain different facts. |
| T: shared tests and native audit | `tests/r9_source_cases.py`, `tests/test_r92_source_profiles.py`, `tests/test_datasource_adapter_contract.py`, `tests/test_full_algebra_backend_matrix.py`, `tests/r93_source_trace.py`, `tests/test_r93_capability_consumers.py`, `tests/test_r93_method_consumers.py`, `tests/test_r93_execution_consumers.py`, `tests/test_r94_producer_recovery.py`, `tests/test_r94_graph_transport_phases.py`, `tests/test_r94_native_graph_deadline.py`, `tests/r95_driver_audit.py`, `tests/test_r95_sql_runtime.py`, `tests/test_datasource_provider_capabilities.py` | R9.2 real per-profile source schema/parameter/fetch/close tests; R9.3 consumer/oracle and submission checks; R9.4 recovery/resource boundaries; R9.5 independent native owner/purpose audit. These test paths identify coverage owners, not a claim that they all ran again in R9.6. |
| P: disclosure | `docs/specs/semantic/datasource-layer.md`, `docs/specs/semantic/loading-validation-introspection.md`, `docs/specs/analysis/python-analysis-design.md`, `docs/specs/analysis/operators-and-frames.md`, `docs/specs/analysis/session-state-and-runtime.md`, `docs/specs/analysis/timezone-and-calendar-design.md`, `docs/testing/runtime-coverage.md`, `site/src/content/docs/docs/latest/installation.mdx`, `site/src/content/docs/zh-cn/docs/latest/installation.mdx`, `site/src/content/docs/docs/latest/concepts/analysis-workflow.mdx`, `site/src/content/docs/zh-cn/docs/latest/concepts/analysis-workflow.mdx`, `site/src/content/docs/docs/latest/concepts/semantic-layer.mdx`, `site/src/content/docs/zh-cn/docs/latest/concepts/semantic-layer.mdx` | R9.2 optional dependency boundaries; R9.3 independent source authority and business completeness; R9.4 recovery/Decimal/control disclosures; R9.5 one source submission owner. English and Chinese user documentation share the same behavioral boundary. |

Provider statement registration is separate from Analysis implementation
registration: `marivo/datasource/capabilities.py` changes in R9.3-R9.5 and all
six `marivo/datasource/engines/<backend>.py` files change in R9.5 to pin exact
metadata purposes. Provider SQL is the approved fixed channel, not business
expression bodies or a runtime fallback.

## Backend-Specific Locations

| Backend | Additional exact locations | Why these locations changed |
| --- | --- | --- |
| DuckDB | `marivo/datasource/engines/duckdb.py`; `tests/test_r93_duckdb_anchor.py`, `tests/test_r93_duckdb_history.py`, `tests/test_r94_file_admission.py` | R9.5 scopes HTTP-secret and metadata purposes. R9.3 uses the existing native transaction/file capture mechanisms; R9.4 adds exact CSV/JSON declarations in `methods/builtin.py` and file capture/admission regressions. No new backend driver or DuckDB business kernel was introduced. |
| SQLite | `marivo/datasource/engines/sqlite.py`; `tests/test_r93_sqlite_snapshot.py`, `tests/test_r93_remote_domain_consumers.py` | R9.3 adds native main-database backup in `datasource/domain_snapshot.py`, exact int64/statistical/domain consumers in I, and full-stream decode in A/D. R9.5 pins metadata purposes and deletes `materialization/sqlite_execution.py`; source exact Decimal remains outside the successful source contract. |
| PostgreSQL | `marivo/datasource/engines/postgres.py`; `tests/multisource_environment/postgres_analysis.py`, `tests/test_r94_postgres_fixture_setup.py` | R9.3 typed integer quotient registration in `compiler/numeric_sql.py` avoids a binary64 intermediate; remote capture uses explicit independent reads. R9.4 isolates native fixture setup. R9.5 pins namespace-aware metadata purposes and deletes `materialization/postgres_execution.py`. |
| MySQL | `marivo/datasource/engines/mysql.py`; `tests/test_r93_mysql_authoring_deadline.py`, `tests/test_r93_mysql_cancellation.py`, `tests/mysql_server_observation.py` | R9.3 verifies UTC initialization, exact integer/Boolean/time decode, isolated certification timeout and owned reader/control cancellation. R9.4 observes native termination separately from local shutdown. R9.5 pins metadata purposes and deletes `materialization/mysql_execution.py`. |
| Trino | `marivo/datasource/engines/trino.py`; `tests/multisource_environment/trino/config.properties`, `tests/multisource_environment/trino_analysis.py` | R9.3 preserves the physically UTC time expression, enables qualified ordinary-table consumers and separately declares Iceberg/non-Iceberg fixtures. Remote authority is independent reads, not a promised shared snapshot. R9.5 pins metadata purposes and deletes `materialization/trino_execution.py`. |
| ClickHouse | `marivo/datasource/engines/clickhouse.py`; `tests/multisource_environment/clickhouse_analysis.py`, `tests/test_datasource_clickhouse_cancellation.py`, `tests/test_r94_clickhouse_graph_phases.py` | R9.2 restores UTC only under exact physical schema authority. R9.3 widens Decimal accumulation in `compiler/graph_lowering.py` and preserves UTC in `compiler/source_time.py`. R9.4 adds query-ID/user-bound cancellation and reader-control cleanup; R9.5 pins metadata purposes and deletes `materialization/clickhouse_execution.py`. Distributed topology remains a separate physical profile. |

## Physical Profile Map

All rows consume the shared sets and their backend row above. `source-profile`
means the exact parameter node in `tests/test_r92_source_profiles.py`; it
does not grant full Analysis coverage. The map preserves the original profile
names from `scripts/r9_qualification_requirements.py::PROFILES`.

| Frozen backend/profile | Physical distinction and additional responsibility |
| --- | --- |
| `duckdb/table` | Native table binding; transaction capture; source-profile `[duckdb-table]`; common ordinary-table consumer/recovery owners. |
| `duckdb/view` | View identity/metadata; source-profile `[duckdb-view]`; view recovery in `tests/test_r94_producer_recovery.py`. No separate provider module change beyond the DuckDB row. |
| `duckdb/csv` | File schema/preparation; source-profile `[duckdb-csv]`; R9.4 file registration in `methods/builtin.py`, `tests/test_r94_file_admission.py`, `tests/test_r94_producer_recovery.py`. |
| `duckdb/parquet` | Exact Arrow schema and immutable file capture; source-profile `[duckdb-parquet]`; `datasource/domain_snapshot.py`, `tests/test_r93_domain_capture.py`, `tests/test_r94_producer_recovery.py`. |
| `duckdb/local-json` | Local Ibis JSON read; source-profile `[duckdb-local-json]`; R9.4 file registration/admission/recovery owners as CSV. |
| `duckdb/http-json-public` | HTTP read and scope/redirect/resource distinction; source-profile `[duckdb-http-json-public]`; A/R and provider credential ownership. Ordinary local-file registration does not imply HTTP method registration. |
| `duckdb/http-json-auth` | Scoped secret preparation distinct from business reads; source-profile `[duckdb-http-json-auth]`; `engines/duckdb.py`, `capabilities.py`, `tests/test_r95_sql_runtime.py`. |
| `postgres/table` | Native table, exact quotient/decode and independent reads; source-profile `[postgres-table]`; PostgreSQL row. |
| `postgres/view` | View metadata and source shape; source-profile `[postgres-view]`; view recovery in `tests/test_r94_producer_recovery.py`. |
| `postgres/namespace-table` | Explicit namespace resolution; source-profile `[postgres-namespace-table]`; `engines/postgres.py` metadata purposes. No extra namespace-specific compiler file was added. |
| `postgres/namespace-view` | Namespace plus view identity; source-profile `[postgres-namespace-view]`; same exact metadata owner, independently frozen source case. |
| `mysql/innodb-table` | Native table/UTC/owned cancellation; source-profile `[mysql-innodb-table]`; MySQL row. |
| `mysql/view` | View resolution; source-profile `[mysql-view]`; view recovery in `tests/test_r94_producer_recovery.py`, common MySQL decode/control. |
| `sqlite/main-table` | Main-database native backup authority; source-profile `[sqlite-main-table]`; `datasource/domain_snapshot.py`, `tests/test_r93_sqlite_snapshot.py`. |
| `sqlite/main-view` | Main view schema and backup boundary; source-profile `[sqlite-main-view]`; same exact SQLite owners plus view recovery. |
| `trino/iceberg` | Iceberg catalog/partition metadata; source-profile `[trino-iceberg]`; Trino row and ordinary-table consumers. |
| `trino/non-iceberg` | Native non-Iceberg catalog remains separate; source-profile `[trino-non-iceberg]`; same provider with connector-specific fixture and metadata facts. It does not borrow Iceberg partition guarantees. |
| `clickhouse/mergetree` | Local physical table and exact native query control; source-profile `[clickhouse-mergetree]`; ClickHouse row. |
| `clickhouse/distributed` | Distributed table/topology metadata; source-profile `[clickhouse-distributed]`; `tests/multisource_environment/clickhouse_analysis.py`, `tests/test_r94_clickhouse_graph_phases.py`. Native control and resource facts remain separately observed. |

The cost fixture explicitly declares Trino `TIMESTAMP(6)` so both accepted
connectors receive the existing UTC-microsecond workload. The first memory
connector collection used implicit `TIMESTAMP`, which defaulted to milliseconds
and correctly failed the exact Analysis method-key contract. Its failed raw
records are retained in `evidence/r96/formal-efficiency-non-iceberg-08/`.
The fixture-only correction changes no product qualification, original values,
source profile, independent oracle or connector consistency guarantee.

## Universal Core Changes

R9 implementation did modify core contracts. The extension claim is therefore
not "zero core changes". R9.6's controlled provider replacement itself needs
no production change, while the earlier universal fixes remain explicit:

| Exact files | Universal contract and regression owner |
| --- | --- |
| `marivo/analysis/core/domain_captures.py` | R9.3 adds `sqlite_native_backup` and `independent_reads` authority kinds. This represents real acquisition semantics, not a backend business algorithm. `tests/test_r93_domain_capture.py`, `tests/test_r93_sqlite_snapshot.py` and producer-bound recovery own the regressions. |
| `marivo/analysis/core/business_coverage.py`, `marivo/analysis/core/model.py`, `marivo/analysis/core/rules.py`, `marivo/analysis/materialization/business_coverage.py`, `marivo/analysis/public_dsl.py` | R9.3 makes explicit business completeness part of quantity/coverage identity and refuses original-state rollup of partial business contributions. `tests/test_r93_full_grid_unknown.py`, `tests/test_r93_cohort_unknown.py`, `tests/test_r93_unknown_ratio_boundary.py` and `tests/test_r93_numeric_unknown.py` own state/identity/refusal regressions. |
| `marivo/analysis/core/rules.py`, `marivo/semantic/catalog.py`, `marivo/analysis/observation/coordinates.py` | R9.3 freezes valid Boolean Dimension expression identity alongside numeric Measure identity, enabling the qualified field contract rather than an adapter option. `tests/test_r93_members_consumers.py` and `tests/test_r94_public_schema_drift.py` own the bound-field regressions. |
| `marivo/analysis/materialization/graph_local_execution.py`, `marivo/analysis/methods/state_validation.py`, `marivo/analysis/methods/semantics.py`, `marivo/analysis/public_dsl.py` | R9.4 carries Decimal and domain state/parts through fixed consumers and checked continuations. `tests/test_r94_decimal_row_statistics.py`, `tests/test_r94_archived_domain_recovery.py`, `tests/test_r94_statistical_k.py` and `tests/test_r94_public_refusals.py` own these regressions. These are shared consumer changes, not new driver responsibilities. |

R9.5 retires the six legacy backend execution files and the shared
`dataset_execution.py`, `scalar_sql_execution.py`, `source_preparation.py`,
`source_stage.py`, `local_stage.py`, `validation.py` under
`marivo/analysis/materialization/`. The retained publication/inspection files
are narrowed to typed graph and retained operations; `datasource/table_source.py`
and `datasource/timezone.py` remove obsolete SQL callbacks. The
[SQL ledger](2026-10-06-marivo-r95-sql-ledger.md) owns exact retirement and
submission authority; fewer files alone are not extension evidence.

## Pre-refactor R9.6 Increment

Before the direct-native refactor, the uncommitted R9.6 product diff modified only
`marivo/analysis/methods/builtin.py`: the existing original-sum local consumer
is connected to the exact DuckDB native-table/int64/Entity-or-group/UTC-us keys. The
consumer remains `analysis.state_rollup.local`; no adapter, compiler, decoder,
business kernel or public symbol is added. Its historical implementation-ID
prefix is retained because the owning preparation dispatcher selects that
established consumer family. Actual execution and the unchanged local
difference/attribution refusal are covered by `tests/test_r96_cost_scenarios.py`.

The cost tooling is `devtools/analysis_r9_cost.py`, `r96_cost_scenarios.py`,
`r96_cost_observer.py`, `r96_cost_cold.py` and `scripts/r96_cost_results.py`.
Their fixtures use exact SourceIR declarations and the existing providers.
The physical fixture regression is `tests/test_r96_physical_cost_fixtures.py`.
The owning spec and matching English/Chinese analysis-workflow examples disclose
the new local original-sum key. Packaged skills and `AGENTS.md` are unchanged.

The efficiency-first schedule adds no production extension. Its Distributed
fixture setup aligns the shard reader's changeable deadline controls with the
already qualified single-node account. SELECT-only grants and `readonly=1`
remain unchanged. `evidence/r96/cluster-policy-08/receipt.json` records both
actual shard settings before/after, ten denied write probes and two successful
600-second deadline-setting reads. This is an environment prerequisite, not a
Distributed business-route or cancellation grant. The narrow pure policy test,
lint and touched-owner typing are bound separately in `pure-gates.json`;
original missing-driver-stub diagnostic outcomes are retained.

## Direct Native Refactor

The additional product change is confined to the existing typed graph execution
owner, `marivo/analysis/materialization/graph_source_execution.py`. Qualified
pure-native plans query their closed Ibis checks, required parts and terminal
primary directly instead of capturing intermediate Arrow tables and staging them
back into source relations. This is a shared Runtime execution strategy, not a
backend adapter, business-kernel change, public API, registry key or SQL exception.

The accepted weak-consistency contract permits checks and calculations to read
different source versions; each check certifies its own query only. One logical
Run/result identity does not guarantee one physical scan. Final schemas, complete
key bindings, Cells, RequiredParts and internal arithmetic remain subject to
the existing validation and publication owners. Read-only execution, the
600-second deadline, cancellation, resource release and atomic Store 7 publication
are unchanged. Hybrid/local plans retain their captured source-before-selection
boundary; fixed execution and cold recovery remain source-free. No failing
execution changes route.

The [cost record](2026-10-06-marivo-r96-cost-record.md#direct-native-refactor-boundary)
preserves earlier samples under their original candidates. Neither this file
location nor the earlier controlled tests establish all-profile qualification,
a new cost reuse proof or full R9.6 closure.

## Physical Cost Applicability

The seven extra profiles remain in V17; the frozen 28 requirement IDs are
unchanged. This section records current owning applicability, not a registration
change or a positive cost grant. No formal physical-profile cost batch is bound
here. Static qualified keys, schema checks and small full-row fixtures do not
substitute one warmup and three measured realizations at both fact counts.

| Extra physical profile | Native original-total baseline | Current `ibis_python` baseline | Same-profile fixed kernel/exact hit | Current reason and owner |
| --- | --- | --- | --- | --- |
| `duckdb/csv` | Applicable, `unverified`; exact source rollup key added | Applicable, `unverified`; exact file-shaped local keys added | Applicable, `unverified`; complete original observation capture followed by fixed original rollup | R9.6 adds exact int64 Entity source/local rollup and the local baseline's zscore fit/read carrier keys; A/B/C/D below. |
| `duckdb/parquet` | Applicable, `unverified`; exact source sum-zero rollup key exists | Applicable, `unverified`; exact Parquet local sum-zero key added | Applicable, `unverified`; complete original observation capture followed by fixed original rollup | The existing Parquet fit/read path now connects its exact local original-sum key; B/C/D below. |
| `duckdb/local-json` | Applicable, `unverified`; exact source rollup key added | Applicable, `unverified`; exact file-shaped local keys added | Applicable, `unverified`; complete original observation capture followed by fixed original rollup | Only an existing unparameterized local GET file is admitted; the exact same-question source/local keys are now connected; A/B/C/D below. |
| `duckdb/http-json-public` | Contract-inapplicable: owning source preflight refuses | Contract-inapplicable: owning source preflight refuses | Contract-inapplicable: no legal HTTP-origin Analysis producer | The negative/refusal owner remains separate; its V17 cost/boundary attachment is `unverified`. Missing fixture setup is not an executed refusal; E below. |
| `duckdb/http-json-auth` | Contract-inapplicable: owning source preflight refuses | Contract-inapplicable: owning source preflight refuses | Contract-inapplicable: no legal HTTP-origin Analysis producer | Authentication does not bypass the JsonSourceIR admission predicate. The negative/refusal owner remains separate, with V17 cost/boundary attachment `unverified`; E below. |
| `trino/non-iceberg` | Applicable, `unverified` | Applicable, `unverified` | Applicable, `unverified`; retained baseline producer then fixed sum | This is a distinct native table in the `noniceberg` memory connector, not Iceberg evidence; F below. |
| `clickhouse/distributed` | Applicable, `unverified` | Applicable, `unverified` | Applicable, `unverified`; retained baseline producer then fixed sum | All original roots must retain Distributed declarations and actual shard inputs. MergeTree or fixture row counts do not qualify these costs; G below. |

Owning locations:

- A: [analysis design](../../specs/analysis/python-analysis-design.md) owns local-file admission and the exact R9.6 additions. `tests/test_r94_file_admission.py` pins the closed declaration set; `tests/test_r96_local_file_cost_routes.py` checks its exact keys and five actual missing source/local paths. These bounded prerequisites are not repeated 1k/100k cost grants.
- B: `marivo/analysis/methods/builtin.py::implements` adds only native CSV/JSON int64 Entity UTC-us original sum-zero rollup keys using the existing consumer. Existing table/Parquet and fixed keys remain separately scoped.
- C: The same registration function adds local CSV/JSON/Parquet int64 Entity UTC-us original sum-zero keys. The unchanged local recipe needs complete zscore fitting; CSV/JSON fit input is int64 Entity NoTime and its read field carrier is float64, while `.observed` retains the original int64 state. No raw float/Decimal fit, MAD, mean, grouped variant or HTTP key is added.
- D: `tests/test_r96_physical_cost_fixtures.py:20` captures the complete 1k file observation; line 31 performs `captured.rollup().execute()` and independently checks its original total. This is the available same-question fixed recipe, not a formal cost record. Source capture and fixed kernel/hit measurements must remain separate, with the same facts, window, complete keys and retained state.
- E: `marivo/analysis/materialization/graph_preflight.py:214` admits only local unparameterized GET JSON; line 223 rejects other CSV/JSON descriptors before the source owner opens at line 241. This rejects an Entity dependency at `session.members(...)`, not merely a later sum node. `tests/test_r94_file_admission.py:113` covers HTTP, shadow-HTTP and parameterized descriptors; lines 148-155 require refusal with no source open, Run or admitted resources. Datasource HTTP metadata/auth evidence is a different owner and does not create an Analysis producer.
- F: `tests/r9_source_cases.py:286` selects the distinct `noniceberg` catalog and memory connector and yields its actual TableSourceIR at line 307. The existing baseline's coordinate-preparation/local-sum route and retained scalar `summarize(mv.sum())` recipe apply; ordinary Iceberg samples cannot fill this profile.
- G: `tests/r9_source_cases.py:316` selects the cluster and yields the Distributed TableSourceIR at line 349. `devtools/r96_cost_scenarios.py` owns partitioning all fact, companion and subject roots across actual shards; `tests/test_r96_physical_cost_fixtures.py:38` checks full-root population, not formal cost repetition.

The minimal audit amendment is an explicit per-profile/per-mode applicability
record over these same seven profiles and both fact counts. All applicable modes
still require their original question, raw repetitions and resource/receipt
evidence. Missing exact keys stay `unverified`; HTTP positive source and
producer-bound fixed modes carry the owning contract-inapplicable reason, while
their independent refusal/boundary attachment remains a separate obligation.
No setup failure becomes a Runtime refusal, no inapplicable mode becomes one of
four positive passes, and no profile or requirement ID disappears. This is an
audit boundary, not a positive cost grant. The binder's current-candidate and
cohort proof must finish before V17 can close.

The file-key prerequisites are recorded in
`evidence/r96/file-route-07/gate.json`: three paths passed on the first Runtime
invocation, and only its two failed local paths were rerun after the precise
fit-field carrier repair. All five unique paths passed; two pure/pin nodes,
touched-file narrow typing, lint and whitespace also passed. The initial failed
invocation remains failed; no missing first-run raw log or JUnit was invented.

## Controlled Boundary Evidence

`tests/test_r96_extension_boundaries.py` adds three small checks:

- Two isolated exact backend declarations run the existing core derivation and
  `analysis.methods.local.count` consumer on the same Defined/Null/Unknown
  Cells and produce the same count/state. Only physical registration changes;
  no source engine execution is claimed by this test.
- Missing or blocked exact SQLite declarations refuse when another provider
  or route is present. This tests the negative capability boundary without
  acquiring a fallback or new qualification.
- A wrong declared Arrow schema refuses before backend compilation, issued
  read creation or business submission. The small SQLite connection supplies
  a real Ibis schema; this is not a remote-source acceptance journey.

The existing fresh-process
`tests/test_datasource_adapter_contract.py::test_selected_provider_does_not_import_unselected_modules`
already blocks every unselected optional-driver import and checks provider
selection. It is reused once rather than duplicated. Its guard is a controlled
capability stub, not a local-environment tampering scenario.

Verification in this work:

| Check | Command and result |
| --- | --- |
| Source schema refusal and optional imports | `make test TESTS='tests/test_r96_extension_boundaries.py tests/test_datasource_adapter_contract.py::test_selected_provider_does_not_import_unselected_modules'`: these two nodes passed; the two new registration nodes failed during test construction and were repaired below. The whole initial invocation failed. |
| Missing/unavailable exact capability | `make test TESTS='tests/test_r96_extension_boundaries.py::test_provider_replacement_preserves_business_kernel tests/test_r96_extension_boundaries.py::test_missing_or_unavailable_exact_provider_never_borrows_route'`: refusal node passed after the count policy/resource declaration repair; provider node exposed an invalid test-only datasource Ref. The invocation failed. |
| Replacement and unchanged real count consumer | `make test TESTS='tests/test_r96_extension_boundaries.py::test_provider_replacement_preserves_business_kernel'`: 1 passed after correcting that Ref; 0.07 seconds reported test duration. |
| Typing | `make typecheck TYPECHECK_TARGETS='tests/test_r96_extension_boundaries.py'`: passed, one source file. |
| Format, lint and imports | `make lint-agent LINT_TARGETS='tests/test_r96_extension_boundaries.py'`: passed. |
| Whitespace | `git diff --check`: passed. |

The four distinct boundary obligations have passed; successful nodes were
reused while only changed failing nodes were rerun. Initial construction
failures were in the new test stub and do not constitute product findings.
No broad suite or backend service startup was performed for this extension-only
change; R9.7 owns the final candidate gate. Cost-profile coverage is owned by
the separate R9.6 harness/ledger and is not implied by these four boundaries.
