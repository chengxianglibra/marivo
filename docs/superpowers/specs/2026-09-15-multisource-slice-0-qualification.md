# Multi-datasource Slice 0 qualification inventory

Date: 2026-09-15. Baseline: `lazy-dataset`,
`1f9b3d5d585fed7c339bbe7ef008262ce7333b92`.

Status: **Slice 0 qualification complete**, including the pinned local Trino and
ClickHouse live feasibility probes described below. This is not remote Dataset
activation or acceptance of later implementation slices. No production
registrations or public contracts change. Slice 9d is complete, as confirmed by
the user on 2026-09-15; its acceptance is independent of this qualification.

Owner: [multi-datasource design](2026-09-15-lazy-analysis-multi-datasource-design-and-plan.md),
especially sections 3, 4, 7, 9 and 10. Current execution authority remains with
[Python Analysis](../../specs/analysis/python-analysis-design.md),
[planner](2026-09-01-lazy-analysis-planner-and-pushdown-design.md), and
[Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md).

## 1. Complete method and shape inventory

The existing private family assembly in
`marivo/analysis/observation/contracts.py:make_family_registry` and its
family registration callees contains **53 producer IDs, 49 consumer
registrations, and 37 shapes in 9 families**. Four producers are Session roots.
This is a qualification snapshot, not another runtime dispatch registry.
Names below are internal operator IDs, not a list of public callable paths.

| Family | Exact shapes | Consumer operator IDs (family prefix applies unless shown) | Input roles |
| --- | --- | --- | --- |
| Population | entity-membership | where, sample | input |
| Metric | entity, entity-dimension, entity-time, entity-dimension-time, scalar, dimension, time, dimension-time | where, metric, with_dimensions, with_time_axis, aggregate, rollup, rank, limit, expand_axes, forecast, correlate | input |
| Metric | as above | compare | current, baseline |
| Metric | time, dimension-time | discover.point_anomalies, discover.interesting_windows | metric_time |
| Metric | entity | discover.entity_outliers | metric_entity |
| Delta | entity, scalar, dimension, time, dimension-time, funnel | where, rank, limit, attribute, discover.driver_axes | input |
| Delta | time, dimension-time | discover.period_shifts | delta_time |
| Delta | exact receiver and operand shapes checked separately | attribute_expanded, discover.driver_axes_expanded, funnel_attribute | input, current, baseline |
| Attribution | joint, hierarchy, funnel-loss-rate | where, rank, limit (joint/hierarchy only) | input |
| Association | entity, dimension, time-lag, dimension-time-lag | where, rank, limit | input |
| Forecast | time, dimension-time | where, rank, limit | input |
| Candidate | point-anomaly, interesting-window, period-shift, entity-outlier, driver-axis | where, rank, limit | input |
| Event | journey, funnel, time-to-event | funnel, time_to_event, select_subjects (journey); where (funnel/time-to-event) | input |
| Event | funnel | compare | current, baseline |
| Lifecycle | history, distribution, transitions, dwell, violations | distribution, transitions, dwell, violations, select_subjects (history); where (reducers) | input |

The four remaining producer IDs are `session.population`, `session.observe`,
`session.events.match`, and `session.lifecycle.replay`. Observation consumes its
selected Population; Event/Lifecycle roots carry Population input authority.
`session.population` is a semantic source root. Source Entities additionally
come from the semantic graph, not just these Dataset input roles.

Exact consumer restrictions (owner: the family registrations, plus constructors):

- Metric `where`/`limit` exclude scalar. `rank` and `correlate` accept only
  entity/dimension/time/dimension-time. `compare` additionally accepts scalar.
- Metric coordinates and initial aggregation accept the four entity-present
  shapes. `rollup` accepts dimension/time/dimension-time. Forecast and time
  discovery accept time/dimension-time; Entity outliers accept entity only.
- Delta `where` accepts non-scalar ordinary Delta and funnel; `rank`/`limit`
  exclude scalar and funnel. Expanded attribution/driver inputs must be the
  exact paired Metric operands; funnel attribution requires paired Event funnels.
- Attribution funnel-loss-rate has no row consumers. Event journey cannot use
  `where`; Lifecycle history cannot use reducer `where`.
- Ranking rejects an existing rank column; limit requires admitted ordering.
- `metric.expand_axes`, `delta.attribute_expanded`,
  `discover.driver_axes_expanded`, and `delta.funnel_attribute` are internal
  continuations, not new discovery entry points.
- `observation/contracts.py:make_ids` rejects unsupported parameterized source
  types. Physical driver support alone cannot bypass construction admission.

Registration owners: `observation/contracts.py`, `operators/compare.py`,
`operators/attribute.py`, `operators/correlate.py`, `operators/forecast.py`,
`operators/discovery.py`, `domains/event.py`, and `domains/lifecycle.py`.

### Existing source/local choices

`operators/registry.py:implementation` owns the exact choice; placement and
`admit_local` further restrict it. Every source registration still selects only
DuckDB 1.5.3 / Ibis 12.0.0. All five other datasource engines remain disabled.

| Method/shape group | Current admitted implementation facts |
| --- | --- |
| Population roots/filter/sample; Observation and coordinate expansion | Source required |
| Initial Metric aggregate | Source required; retained aggregate/rollup has local registration only with `RetainedFoldPayload` |
| Metric/Delta/Attribution/Association/Forecast/Candidate row methods | Existing exact local methods where retained admission permits; otherwise source required |
| Ordinary Metric comparison; retained-axis attribution | Local only for admitted non-Entity rows without source-private authority; source also registered |
| Funnel comparison/attribution | Source or local; all operands and result must pass retained-row admission |
| Driver axes | Source or local for non-Entity output, one input or three expanded inputs; Entity output source required |
| Entity outliers | Source required |
| Other Candidate scoring; Forecast | Local only |
| Pearson/Spearman | Source or admitted local; Entity correlation requires private source-pair preparation |
| Kendall | Local calculation with separately declared DuckDB source preparation |
| Distribution Shapley | Source preparation followed by bounded local work; not ordinary retained-row fallback |
| Event/Lifecycle roots, reducers, subject selection | Source required (including admitted native-retained realization) |
| Exact membership/distribution, identity-bearing Entity Delta/Attribution/Candidate | No ordinary pandas fallback |

Closed method variants requiring separate future backend qualification:

- Correlation: pearson, spearman, kendall.
- Forecast: naive@v1, drift@v1, seasonal_naive@v1.
- Attribution: additive_difference@v1, component_mix@v1,
  distinct_membership@v1, distribution_shapley@v1.
- Discovery: point_zscore@v1, global_zscore_runs@v1,
  delta_window_zscore@v1, entity_mad@v1, axis_concentration@v1.
- Distribution/quantiles and exact distinct must additionally satisfy their
  owning `distribution_contracts.py` / `distinct_contracts.py` restrictions;
  notably an exact method never becomes approximate on a new engine.

### Parts, fences and contributing relations

`compiler/nodes.py` distinguishes `RetainedPartSpec` (keyed sufficient-state
projection of the primary transfer) from `RetainedRelationSpec` (independent
source-private relation). `observation/private_parts.py` combines membership
and distribution authorities. Metric/Delta can require sufficient components,
exact membership and distribution; attribution requires reconciliation, sampling
requires its receipt/state, and Event/Lifecycle use specialized coverage and
proof/publication owners. A scalar sum/count has sufficient-state parts too.

Explicit fence inventory:

| Fence purpose | Current construction/execution owner |
| --- | --- |
| JSON source realization | `materialization/admission.py:_source_backend` |
| Entity reservoir sampling | `compiler/lowering.py` sample branches; `materialization/sampling.py` |
| Lifecycle membership, occurrence, replay, reducers | `compiler/lowering.py`; `compiler/lifecycle.py`, `lifecycle_reducers.py` |
| Event journey membership/occurrence and reducers | `compiler/lowering.py`; `compiler/event.py`, `event_reducers.py` |
| Driver input / expanded comparison | `compiler/lowering.py` driver preparation |
| Entity Candidate scoring input | `compiler/lowering.py` candidate preparation |
| Retained-domain equivalents | `compiler/lowering.py:compile_retained_rows`; `materialization/parquet_scan.py` |

These are owned single-evaluation resources, not optional caches. The two types
are `CompiledSampleFence` and `CompiledRelationFence`; unsupported realizations
must reject their requirements before submitting rows.

**Cross-relation requirements are not yet fields on current registrations.**
The following qualification obligations are inferred from existing lowering,
not claims of already implemented capability metadata:

- Population includes its subject plus predicate and reference-axis paths.
- Observation includes coordinate spines, each Metric component root, relationship
  paths, reference/status/cumulative axes and component filters.
- Event/Lifecycle include all step sources and participant paths; reducer axes
  can add subject-to-axis relations.
- Comparison/expanded attribution/driver work consume exact current/baseline
  roles. Assertions, main output, private parts and fences must use the required
  common realization of their contributing data.

`compiler/normalize.py:required_entities` owns this closure. Dataset operand
count does not establish physical table count. `placement.py:same_domain`
currently also compares a local version tuple; that is neither remote live
compatibility proof nor a common cross-table snapshot. Separate table snapshot
IDs on Trino must not imply atomic multi-table consistency.

## 2. Physical statement and lifetime inventory

This table records actual source and retained-path seams to extract in Slice 1;
it does not implement an adapter. Search anchors are symbols or distinctive
statement roles so the inventory remains usable as line numbers move.

| Seam | Actual current behavior and owner | Qualification/extraction obligation |
| --- | --- | --- |
| Admission/domain | `operators/registry.py`; `compiler/placement.py:source_binding`, `_source_supported`; `admission.py` binding-hit path | Keep same-Session hit source-free; separate authority, static compatibility and bound realization |
| Open/schema | `admission.py:_source_backend`, `_validate_source_schema`; datasource `build_backend_with_secrets`, engine qualification | Resolve captured credentials only under reserved Run; verify exact physical types/settings, no trial analytical replanning |
| Session controls | `_source_backend`: DuckDB `Backend` checks, `SET threads`, TimeZone, memory_limit=256MiB, max_temp_directory_size=0B, `BEGIN TRANSACTION` | Concrete adapter owns controls and transaction; MySQL authoring timeout's transaction must not be nested |
| Deadline | `_engine_deadline` and worker interrupt path: DuckDB cursor class and `.con.interrupt()` | Bound execution and fetch; remote cancellation needs independent terminal evidence |
| Source preparation | `_source_backend`: `backend.compile`, sample SQL parsed as DuckDB, `create_table(temp=True)`, source-read views and reserved source fences | Reserve before resource effects; compile-to-submit exactness includes preparation, not just primary |
| Source validation | `validation.py:compile_preparations`, `execute_batch`: compile each named integer check separately; `raw_sql`, DuckDB cursor `fetchone` | Preserve order, exactly one ordinal/result, zero violations, no missing/duplicate/malformed checks; keep per-check bounded execution |
| Sampling | `sampling.py:sample_statement`, `execute_sample`: quoted DuckDB identifiers, reservoir SQL, temporary table, identity/count scalar query | Seed is not a cross-statement realization proof; declaration requires an admitted fence |
| Primary batching | `admission.py:_batches`: diagnostic compile then `to_pyarrow_batches` | Ibis DuckDB hooks execute, then compile again; selected SQL is currently not the immutable submission input |
| Pre-execute hooks | installed Ibis DuckDB `_run_pre_execute_hooks`, `to_pyarrow_batches`, `to_pyarrow` | Inventory UDF/in-memory/read-source preparation effects, parameter substitution, default limits, schema conversion and cursor closure; required resource effects become declared preparations |
| Transfer sizing | `_batch_rows`: source string length max query, conservative row width, 1..1024 batch rows; `_batches`: 8MiB check after fetch | Remote page/decompression/nested-width bounds must hold before decoding; reducing batch rows alone is insufficient |
| Proof/scalar queries | `admission.py` event/reducer/selection/candidate/association/driver proofs, pair/support counts, attribution reconciliation | Current paths compile then call `to_pyarrow` or `raw_sql(...).fetchone`; enumerate each statement role and retain typed bounded decoding |
| Private retained relations | `admission.py:_parquet_parts` and retained-part loops: zero-row schema query, count/support queries, primary/part streaming | Preserve source-private authority, exact type/row/coverage reconciliation and statement realization |
| Retained helper validation | `retained.py`: membership schema, DuckDB struct/key/support reconciliation; `lifecycle_publication.py`: part keys, coverage ledger, scalar proof and bounded native incoming-row staging | Include helper-generated statements, not only their admission call sites; no private-state authority expansion |
| Distribution/reducer helpers | `materialization/distribution.py`: compile then raw scalar validation; `lifecycle_reducer_publication.py`: selection/output/summary compile then `to_pyarrow`; `event_publication.py`: typed retained Arrow validation | Keep native helper queries distinct from pure Arrow decoding and preserve their owner-defined order |
| Cold inspection/local journal | `inspection.py`: isolated DuckDB controls and zero-row schema read; `store.py`, `storage.py`, `project_storage.py`: project-local SQLite/persistence operations | These are local retention/journal domains, not statements to route to a remote datasource adapter |
| Native Parquet | `parquet_scan.py`: `.con.register`, temporary table, unregister, count, zero-row schema reads; `admission.py` Parquet read views | Keep native DuckDB domain explicit; do not silently upload retained data into remote sources |
| SQL/dialect lowering | `compiler/lifecycle.py`, `driver_numeric.py`, `distribution.py`; `admission.py` identifier rendering, `DESCRIBE`, reconciliation SQL | Concrete dialect/macro/list/quantile/diagnostic behavior stays scoped; Ibis compilation is not numerical parity |
| Termination/publication | `_source_backend` rollback and exact `TransactionException` matching; success rollback/disconnect before publication; `resources.py` process/nonce proof | Local PID death cannot prove remote query termination; journal submit/ack/unknown and terminal receipts without retry |
| Cleanup/recovery | `resources.py:confirm_execution_termination`; worker and object-request proof families | New remote adapter needs its own exact capability; unknown work remains unresolved, not clean by connection close |

### Slice 1a reconciliation

The table above records the pre-extraction baseline, not current transaction
requirements. Slice 1a removes the action-wide consistency BEGIN/ROLLBACK and
rollback-error matching. One action-local execution context retains statement
and resource ownership. Initialization still sets UTC and existing resource
controls; removing budgets and changing termination admission remain 1b/1c.

Sampling, source preparation and native retained-reader fences remain required
for single evaluation. Their connection-owned temporary objects are cleaned by
close, without a consistency transaction. Store publication transactions and
semantic version-row selection retain their separate purposes. The inventory's
snapshot/realization requirements below are historical qualification experiments;
none is a source-consistency eligibility requirement. No remote variant is enabled.
See the [Slice 1a evidence](2026-09-15-multisource-slice-1a-acceptance.md).

### Historical realization qualification matrix

| Variant | Actual required operations | Fences / consistency | Transport / termination |
| --- | --- | --- | --- |
| Transaction (DuckDB baseline; proposed PostgreSQL/MySQL/SQLite) | Verify version/types/settings; open owned read context; compile and submit ordered assertions/preparations/output/parts; close | Fence only where explicitly implemented; contributing relations must satisfy method's snapshot requirement; InnoDB scope for MySQL | Typed bounded cursor, pre-transfer sizing, interrupt through fetch; engine-owned terminal proof |
| Version-pinned (proposed Trino Iceberg) | Verify connector/table/Ibis/driver scope; resolve exact snapshot IDs; qualify every physical scan; submit exact SQL; never replace expired IDs | No generic fence admitted; independent IDs do not prove common multi-table observation | Bound HTTP pages and decode; record query ID/nextUri, cancellation and authoritative terminal state |
| Single-statement (proposed ClickHouse local MergeTree) | Verify table/settings; compile assertions and permitted outputs into one envelope; decode fully before publication | Shared snapshot for each repeated table must be verified; no generic fence or cross-table atomicity; pre-transfer checks still need barrier | Bound response/decode and reserved staging; query ID/kill/terminal evidence; private relations excluded |

No variant is implemented by these test probes. SQL strings remain test-only
execution inputs, not semantic authoring bodies or persistent replay plans.

## 3. Probe scope and validation ordering

`tests/lazy_multisource_qualification.py` uses existing fixture declarations and
the production `compile_dataset` lowerer for one unversioned orders Entity,
sum(amount), and count(non-null amount). The fixture has one physical source.
`NoIoActionPort` prevents Dataset execution; remote compiler instances are never
installed in Ibis or Analysis global registries.

The Trino probe overrides the physical-table visitor, building SQLGlot `Version`
nodes. It covers both unbound and disconnected database-bound Ibis tables,
repeated physical references, exact catalog/schema qualification, BIGINT IDs,
separate assertions and rejection of a second source. It does not regex-edit SQL.
The unchanged standard compiler is checked separately.

The ClickHouse probe compiles a typed UNION ALL envelope carrying the existing
source checks, primary columns and sufficient-state projections. DuckDB evaluates
the Ibis algebra offline; the opt-in live runner executes the actual ClickHouse
SQL and decodes its Arrow response. Live testing found that native COUNT returns
UInt64 despite Ibis's int64 type: the uncorrected UNION inferred an Arrow-unsupported
Variant(Int64, UInt64). `clickhouse_envelope_sql` explicitly casts every final
branch projection through typed SQLGlot nodes before UNION inference. This is a
private bounded compiler probe, not a globally installed compiler or production
adapter. The decoder's 1024-row/1MiB cap is a probe boundary. Live read sharing is
proved below; production assertion ordering still requires its owning amendment.

| Check | Existing order | Single-statement qualification status |
| --- | --- | --- |
| `sales.orders.identity_non_null` | `_validate_source` feeds source preparation, before primary transfer | Envelope carries it even for empty output or excluded bad rows; moving consumption later still needs explicit owner amendment |
| `sales.orders.source_row_unique` | Same preflight, on full governed source | Same blocker; grouping/filtering the output must not hide duplicates |
| Identity finiteness | Generated additionally for floating identity columns | Not in this int64-ID fixture; float identity needs separate probe |
| Snapshot/validity/relationship assertions | Generated only for corresponding semantic shapes | Not admitted by this unversioned single-table probe |
| Schema and resource sizing | Source schema before lowering; source-width check before result transfer | Must remain before transfer. No envelope decoder can substitute for a server barrier or pre-decode bound |
| Envelope integrity | New test-only decoder before exposure of primary/parts | Missing/duplicate ordinal, nonzero violations, unknown kinds and malformed payload fail closed |
| Output/retained/evidence validation | Current Runtime before atomic publication | Not implemented by the envelope probe; existing owners remain authoritative |

The test does not admit sample fences, independent private relations, membership
or distribution exports. Empty primary, invalid source rows removed by Metric
filtering, missing/duplicate checks and malformed envelope records are covered.
All source checks remain in their current pre-transfer order in production.

## 4. Completed Slice 0 evidence

Commands use repository `.venv` entrypoints. Exact installed versions, command
outcomes, file hashes and links to full live receipts are in the companion
[evidence record](2026-09-15-multisource-slice-0-evidence.json).
The private environment has a separate
[runbook](../../../tests/multisource_environment/README.md).

| Slice 0 exit gate | Observed evidence |
| --- | --- |
| Scope and coupling inventory | Sections 1–3: all 53 producers / 49 consumers / 37 shapes / 9 families, contributing relations, source/local paths, private parts, fences, statement roles and realization obligations |
| Frozen compatibility scope | Trino 483 / Iceberg 1.11.0 / JDBC V1 / PostgreSQL 17.11 / local warehouse / table format v2; ClickHouse 26.3.33.24 / one local MergeTree; ARM64 image digests and installed Ibis 12.0.0, SQLGlot 30.8.0, Arrow 25.0.1 and driver versions recorded |
| Trino live lowering | Submit exactly the `SnapshotCompiler` primary and both assertion statements. Initial primary/parts are `[30,2,30,2,3,2,3]`; after inserting duplicate and null identities, pinned statements retain the same values and zero violations, while current assertions each report one violation and current source count/sum become 6/185 |
| Trino actual expiry | Expire the previously readable snapshot via Iceberg, verify its removal from `$snapshots`, then resubmit the unchanged primary and both checks. All fail with snapshot-specific server errors and query IDs; no fallback to a newer ID |
| ClickHouse live envelope | Exact Arrow schema and primary plus two sufficient-state projections; null measure counted separately from source rows; empty source with scalar output, empty source/output and nonempty source/empty output; filtered duplicate/null identity and empty-output duplicate violations rejected; a removed live assertion record also rejected |
| ClickHouse shared reads | Controlled snapshot-planning delay on the actual envelope, with `system.processes` observation before and after acknowledged writes. Three shared-snapshot runs preserve sum/count/private-state equality; a fourth run inserting duplicate identities gives a matching uniqueness violation count and fails decoder publication. The disabled-setting negative control observes mismatched sum/count, demonstrating an effective concurrent mutation window |
| ClickHouse physical types | Actual unmatched LEFT JOIN produces NULL; Decimal128(2) decodes to exact Decimal 30.75; Asia/Shanghai DateTime64(3) converts to UTC with milliseconds; NaN/Inf are nonfinite, ordinary 1.5 is finite. These targeted facts do not admit arbitrary floating Metric bodies |
| Bounded probe transfer and terminal receipts | ClickHouse requests uncompressed HTTP and Arrow IPC, caps wire reads at 1MiB+1 before decoding and checks the exact schema, decoded row/byte caps and complete envelope before exposure. `wait_end_of_query=1` plus successful complete responses and query-log QueryFinish receipts for concurrent readers. Trino records actual FINISHED stats and snapshot error receipts; unavailable HTTP wire bytes remain null |
| Existing backend boundary | DuckDB execution economics retains one real success and five pre-admission non-DuckDB rejections. Offline tests cover compiler isolation, invalid snapshots, typed branches, malformed/missing checks and wire-budget rejection |

Full submitted SQL, query IDs, statement roles, transferred row/byte observations,
independent expectations and server outcomes are retained in the
[Trino live receipt](2026-09-15-multisource-slice-0-live-trino.json) and
[ClickHouse live receipt](2026-09-15-multisource-slice-0-live-clickhouse.json).
Repeated SQL is deduplicated by SHA-256; each statement references its exact SQL.
Unknown transport metrics are null, never zero. Fixtures use unique databases and
are removed in finally blocks; only the dedicated qualification environment is
operated. These receipts exercise compiler/decoder feasibility, not Dataset Run
admission, Artifact publication or backend registration.

The ClickHouse delay is the pinned server's test/debug setting
`merge_tree_storage_snapshot_sleep_ms=200`; the supported production candidate
requires `enable_shared_storage_snapshot_in_query=1` without that artificial delay.
Its behavior and MergeTree-only scope are documented in the
[versioned settings owner](https://github.com/ClickHouse/ClickHouse/blob/v26.3.33.24-lts/src/Core/Settings.cpp).
The negative control is mandatory: lack of an observed inconsistency or an
acknowledged write overlapping a server-observed reader fails the run and requires
rerunning the concurrency experiment, rather than being reported as success.

## 5. Remaining later-slice activation gates

The design's section 9 assigns scope inventory and live feasibility to Slice 0;
section 10's full backend acceptance matrix applies to **enabled** entries.
The following obligations remain explicitly unproved and block activation, not
completion of this qualification investigation:

| Owning enabling work | Required evidence / remaining blocker |
| --- | --- |
| ClickHouse assertion order (Slice 6) | The live envelope proves post-transfer fail-closed decoding, not pre-transfer enforcement. Identity and uniqueness remain pre-transfer checks in current production. An explicit owner amendment is required for staged validation, and any check that must stay before transfer needs a proven server barrier; `wait_end_of_query` does not provide that barrier |
| Remote transport (Slices 3–6) | Multi-page, wide/nested values, source sizing, bounded driver decompression/decoding and slow-fetch budgets. The fixed small Arrow probe and Trino fetchall are not production streaming implementations; Trino wire-byte accounting is still absent |
| Remote lifetime (Slices 3–6) | Submit/ack uncertainty, lost cancellation acknowledgement, process death and authoritative remote terminal receipts integrated with the reserved Run and recovery journal |
| Publication and reuse (enabling slices) | Real Dataset primary/parts/Evidence/Findings atomic publication, source-offline binding hit and cold reads on each newly enabled backend |
| Wider matrix (Slices 3–7) | PostgreSQL/MySQL/SQLite exact versions and controls, other Trino connectors/table combinations, Distributed/Replicated ClickHouse, broader types/methods and cross-relation/fence/private-state proofs |

All non-DuckDB source registrations remain disabled. Slice 0 completion authorizes
no backend activation or later-slice completion. The Trino and ClickHouse probes
must be rerun for any change to their pinned versions, table scope, compiler,
settings, validation envelope or decoder; rerun commands are in the runbook.
