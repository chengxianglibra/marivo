# Slice 5a: private Metric comparison and multi-input execution

Status: the private Slice 5a technical gate passed after review follow-up and
pre-commit formatting on 2026-09-08. Parent Slice 5 remains open.

## Parent milestone and prerequisite units

Parent Slice 5 remains open. Slices 3b and 4d, including the composed parent
Slice 4 gate, are the accepted prerequisites recorded in the public-cutover
plan. The owner approved this implementation plan and its two clarifications
on 2026-09-08. Implementation starts from clean `lazy-dataset` commit
`e78e8ec155c70646833cc8a800549ce7e84bfa38`. The planning baseline passed 106
focused identity, placement, registry and codec tests. That is not a 5a gate.

## User-visible or runtime outcome

Private Metric comparison produces, publishes and cold-recovers Delta, with
real `where`, `rank` and `limit` continuations. It proves the first registered
multi-input local consumer and the combined-budget obligation transferred by
the accepted Slice 4d allocation. Construction performs no I/O or Run creation.

## Frozen contract owners consumed

Consume Dataset Core, Observation Model, Typed Operators, Planner/Pushdown,
Materialization Runtime, and Session Runtime Read designs. The approved owner
clarifications admit `delta_unavailable` as an undefined relative Finding
reason, and allow differing Metric observation windows only: Population
membership selection remains identical. Comparison compatibility metadata is
definition/Artifact authority, separate from row meaning and display lineage.

## Exact method, backend, storage, and fixture scope

Register `compare/metric@v1`, only ordinal equal-length `window_bucket()`, and
Delta entity/scalar/dimension/time/dimension-time. Signed integer inputs promote
to int64, floats to float64; Decimal arithmetic preserves exact precision/scale
and fails on unrepresentable output. Entity-preserving work is source-required.
Non-Entity rows admit exact pandas continuation. Scalar keeps singleton meaning
and rejects row filtering/ranking/limiting. DuckDB/Ibis is the tested source
adapter; local Parquet, immutable DuckDB engine and versioned S3-compatible
object receipts retain their existing authority and size limits.

Reuse isolated lazy execution/adapter fixtures and the repository's runtime
marker. Actual multi-input tests must consume independent equal-argument
DuckDB connections with conflicting same-named tables. Runtime acceptance uses
new project roots outside the checkout and a separate pinned MinIO service.

## Exact files owned

- Comparison owner: `operators/compare.py`, `contracts.py`, `delta.py`,
  `errors.py`, `row_values.py`, `registry.py`, `row.py`;
  `observation/metric.py`, `contracts.py`, `ordering.py`, `predicates.py`;
  `tests/test_lazy_compare_contracts.py`, `test_lazy_compare_numeric.py`,
  `test_lazy_observation_runtime_no_io.py`, `test_lazy_source_construction_no_io.py`.
- Runtime owner: `compiler/comparison.py`, `lowering.py`, `nodes.py`,
  `normalize.py`, `placement.py`; `materialization/admission.py`,
  `local_worker.py`, `retained.py`; `tests/test_lazy_compare_compiler.py`
  `test_lazy_local_graph_lifetime.py`, `test_lazy_local_placement.py`
  and `lazy_compare_fixtures.py`; review follow-up adds
  `test_lazy_compare_time_runtime.py`.
- Publication owner: `materialization/contracts.py`, `publication.py`,
  `store.py`, `recovery.py`, `comparison_codec.py`, `comparison_publication.py`;
  `evidence/_dataset_types.py`, `_dataset_reads.py`;
  `tests/test_lazy_delta_publication.py`, `test_lazy_finding_types.py`,
  `test_lazy_adapter_failures.py`; the final source-transaction cleanup repair
  in `materialization/admission.py` was coordinated with the Runtime owner.
- Coordinator: three owning spec amendments, `.gitignore`, this record;
  `tests/test_lazy_compare_runtime.py`, `test_lazy_compare_runtime_acceptance.py`,
  `lazy_compare_runtime_worker.py`, the action-port method in
  `lazy_observation_fixtures.py`, and final evidence. Module directories above
  are relative to `marivo/analysis/`; abbreviated test names are under `tests/`.
  Contributors preserve other work and coordinate APIs before changing seams.

## Shared seams changed

Extend paired-family and producer registration, retained comparison metadata,
source lowering, role-indexed local stages, complete combined input guards,
multi-operand descriptor assembly, and transactional nonzero Findings. Run
inputs preserve repeated Artifact occurrences while read caches may deduplicate
physical reads. Reuse Core realization topology; no second identity digest.

## Public additions

None. Private paired Delta classes and window alignment are not exported or
advertised by public Help. Existing eager calls retain their implementation.

## Public removals

None. The atomic facade and persistence switch belongs to Slice 8.

## Persistence changes

Extend private v3 canonical descriptors/Evidence for exact comparison authority,
paired sampling and Delta projection. Publish ordered Findings in the existing
transaction and relation. Do not add a migration, dual decoder, sidecar authority
or separate publication path. Compare consumes public operand rows and required
validation outputs; unused Metric sufficient-component parts are not copied.

## Implementation order

1. Apply the approved owning-spec clarifications and fix this edit/test scope.
2. Implement family construction, compatibility, row schema and exact arithmetic.
3. Integrate source lowering and role-indexed worker execution under shared bounds.
4. Integrate atomic Evidence/Finding publication and catalog-free cold recovery.
5. Complete row continuations and the independent numerical/failure matrix.
6. Freeze one candidate; run the broad gate and retain fresh Runtime evidence.

## Focused positive tests

Cover five shapes, all four operand state vectors, same-Store foreign Artifacts,
source/local parity, repeated Dimension values in different comparison buckets,
signed/Decimal equations, exact empty behavior, sampling sharing and row
continuations. Codec/Store tests independently validate registered state,
nonzero Finding counters/digests, 1000/1001 cap ordering and selected reads.

## Adjacent negative tests

Reject alternate alignment, unequal bucket counts, incompatible Population,
Metric/coordinate/selection/sampling facts, arity-N, foreign Logical graphs,
cross-Store Artifacts, unsupported Entity local placement, scalar row operations,
non-finite inputs and arithmetic overflow. Wrong/missing/corrupt later operands
must fail before local comparison invocation. No missing part triggers replay.

## Failure injections

Prove combined overflow when both operands fit individually; complete later
schema/key/part validation; deadline and worker loss; atomic rollback during
Evidence/Finding publication; and retained receipt/metadata/Finding corruption.
Reuse existing termination, resource reservation and cleanup authority.

## Real runtime journey

For applicable local/engine/object combinations, run separate producer,
continuation and cold-reader processes. Close the origin before retained
continuation and recovery. Verify rows, keys, paired times, Findings, input
occurrences, graph and exact-key no-op counts. Shared sampled scalar self-compare
must realize once and return zero. Independent sampled branches have a distinct
key and cannot satisfy the shared request, including fresh reconstruction.

## Capability-to-acceptance row and evidence locations

Complete the Metric comparison row and the multi-input extension of the
source-prefix/local-suffix row. The exact test matrix is:

| Contract or failure boundary | Test selectors under `tests/` |
| --- | --- |
| Private families, five shapes, compatibility, no-I/O construction, scalar rejection | `test_lazy_compare_contracts.py`; actual fresh-process filesystem/source/Store/Run guards in `test_lazy_observation_runtime_no_io.py`, `test_lazy_source_construction_no_io.py` |
| Independent numeric reference, presence/null/empty/zero/negative values, time keys, integer and Decimal overflow | `test_lazy_compare_numeric.py`, `test_lazy_compare_compiler.py` |
| Twenty shape/state topologies, duplicate input occurrences, foreign selected Artifact, actual numeric source/local execution, relative-unavailable Findings | `test_lazy_compare_runtime.py` |
| Equal-argument independent real connections, conflicting tables, source-required rejection, complete second input and combined-budget protection | `test_lazy_compare_runtime.py` |
| Source and retained Delta row-operation parity, local frontier and Entity engine continuation | `test_lazy_compare_runtime.py::test_delta_row_operations_preserve_source_and_retained_numerical_results` |
| Repeated Dimension members across exact time buckets, paired times and scoped Findings | `test_lazy_compare_time_runtime.py` |
| Shared realization versus independent branches and exact-key isolation | `test_lazy_compare_runtime.py::test_sampled_self_comparison_and_independent_branches_do_not_share_bindings` |
| 1001-to-1000 extraction, exact typed ordering, selected-page corruption isolation, full inspection, rollback, Entity redaction, canonical codec | `test_lazy_delta_publication.py`, `test_lazy_finding_types.py` |
| Three-process local/engine/versioned-object continuation and cold key hit with source closed | `test_lazy_compare_runtime_acceptance.py` using `lazy_compare_runtime_worker.py` |
| Shared worker deadlines, loss, complete input/part guards and cleanup; sampling/retained boundaries; release of old component frames before the next allocation | `test_lazy_local_guards.py`, `test_lazy_local_graph_lifetime.py`, `test_lazy_local_runtime_acceptance.py`, `test_lazy_slice4_boundaries.py`, `test_lazy_retained_runtime_acceptance.py`, runtime reliability tests in the full gate |

Retained evidence lives under
`docs/superpowers/plans/evidence/slice-5a/`, with candidate source manifests,
complete logs and a hash index. No prior slice record substitutes for 5a proof.

## Disclosure updates

Update only the owning designs, cutover allocation and this private execution
record. Current public Help, skills and latest English/Chinese site docs remain
on the current facade until Slice 8. Private contracts disclose implemented
consumers only and have no attribution/discovery placeholders.

## Explicitly deferred contracts

Attribution and its retained components belong to 5b-5d; discovery to 6c/6e;
Event comparison to 7c; public activation to 8; public Agent acceptance to 9.

## Exit gate

Run narrow new/affected tests, explicit touched-module typing/lint, then
`make check-agent`, including daily and runtime tests with versioned MinIO and
no S3 skips. Bind complete logs and new Runtime records to one unchanged
candidate. Only after every required row passes may Slice 5a be accepted;
parent Slice 5 stays open. No commit, push or release is authorized.

## Initial technical gate and verification

The accepted gate ran from `2026-09-08T12:39:02.728181Z` through
`2026-09-08T12:46:28.439416Z`, on the unchanged uncommitted `lazy-dataset`
candidate based on `e78e8ec155c70646833cc8a800549ce7e84bfa38`. Its manifest
contains 762 Python source/test and execution-configuration files with digest
`b62f9f0709cfa2995dc66ff35821b16718a52ea2d6ad46b3deaa4ab832a6e0d2`.
The before/after file manifests match exactly. The gate separately records
the pytest, Make and project configuration digests. The initial documentation
closeout changed only these records. Review follow-up changes and their fresh
verification are recorded separately below.

`make check-agent` completed with exit code zero in 445.71 seconds:

| Check | Accepted result |
| --- | --- |
| Ruff and import contracts | Passed |
| Full source typing | 387 files passed |
| Default tests | 5,927 passed in 63.78 seconds |
| Runtime tests | 762 passed in 364.18 seconds; zero skips |
| API documentation | Built successfully with warnings treated as errors |

Before this gate, explicit-package typing passed for all 15 modified test/helper
files. Focused tests also reproduced and repaired the shared-component lifetime,
source-domain admission and engine-budget error-propagation regressions. The
two fresh-process no-I/O journeys keep their filesystem/network guards active
while constructing comparison and its row continuations; module dependencies
are imported before the boundary, without constructing a Dataset in advance.

The runtime gate used an isolated MinIO container
`marivo-slice5a-minio`, image `RELEASE.2025-09-07T16-13-09Z`, digest
`sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
The fixture enabled bucket versioning. All S3-compatible cases ran. Runtime
versions were Python 3.12.13, Ibis 12.0.0, DuckDB 1.5.3, pandas 2.3.3 and
PyArrow 25.0.1.

## Initial fresh Runtime evidence

The accepted bundle is
[`evidence/slice-5a/gate-20260908T123902Z/`](evidence/slice-5a/gate-20260908T123902Z/).
Its `gate.json`, `source-before.json`, `source-after.json`, `check.log` and
`index.json` preserve the command, timestamps, environment, per-file source
digests, complete output and record digests. `check.log` has SHA-256
`9ea61e6192ba0aa440fb4b85f43db5091b11b4874f7f53998e6272c0ac2ba8e7`.
The evidence index contains 97 fresh Runtime records: 31 comparison, 56
reliability, six read-boundary, two adapter and two retained-state records.
Every indexed file digest was checked after the gate.

The 31 comparison records include twenty shape/state combinations, five
complete-input rejection cases, real independent-source consumption,
source-required rejection, shared-versus-independent sampling, and three
storage-specific process journeys. Their candidate digests match the accepted
source, and their timestamps fall inside the successful gate interval.

Each local/engine/object journey publishes both Metric operands, compares them,
closes the origin, and continues the retained Delta in a different process.
A third process verifies the same rows, exact coordinates, paired times,
Findings, ordered input Artifact references and execution-key hits. It also
reconstructs shared and independent sampling topologies while source compilation,
backend creation and local worker execution are forbidden. Hit snapshots show
no additional Run or resource activity. Entity continuations remain engine
work with zero Findings; source and retained row-operation results agree.

The complete gate additionally covers 1,001-to-1,000 Finding extraction and
canonical order, selected-page versus full-inspection corruption, signed and
Decimal numerical references, relative-unavailable Finding eligibility, complete
second-input failures, combined bytes and method size, worker termination,
deadline enforcement and atomic publication rollback.

## Review follow-up

The owner supplied review suggestions on 2026-09-08 and authorized adopting
substantiated changes. The review was evaluated against the current code and
the original implementation instruction to close only 5a after all gates pass.
That instruction authorizes technical completion tracking; a passed automated
gate does not represent an independent reviewer approval or release decision.

| Suggestion or observed issue | Disposition and evidence |
| --- | --- |
| Alternate alignment has no negative test | Adopted: a nonregistered alignment subtype fails with the typed comparison error; the registered helper remains a positive control. |
| Cross-Store Artifact rejection lacks comparison coverage | Adopted: actual committed Artifacts are rejected for LM, ML and MM in both Store directions without changing Store snapshots or query histories. |
| Repeated Dimension members across time buckets only have pure arithmetic coverage | Adopted: real source and retained/local comparisons independently verify four Dimension/ordinal keys, paired times, nonzero deltas and Finding coordinates. |
| Entity row-operation parity uses a self-comparison | Strengthened: customer-level observations in different windows yield -20 and 80; the source and source-offline retained paths both select the 80 difference through filtering, ranking and limiting. |
| Combined byte budget is computed from the measured frame | Strengthened: a fixed 32 KiB policy admits either complete operand individually but rejects their sum before calling compare. |
| `fillna(0)` obscures the raw null distinction | Strengthened: exact null masks and non-null values are checked independently in all four Entity state topologies. |
| Executor and publication duplicate exact numeric operations | Keep independent checks: input promotion and finalized output validation have different obligations; publication also validates Ibis output that bypassed the pandas executor. No numerical drift was found in these three arithmetic helpers. |
| Short helpers, scope stripping, function length and local names | No correctness defect established; leave these style-only changes outside the bounded repair. |
| Completion wording amounts to independent self-approval | Clarified: record private technical gate completion under the owner's explicit implementation authorization; no independent-review approval, merge or release is asserted. |
| Additional concrete Decimal defect found during triage | Fix negative Decimal magnitude with context-free `copy_abs()`: ambient precision can otherwise merge distinct >28-digit magnitudes and select the wrong Finding order or top-1000 cutoff. Add exact precision/order/truncation regression. |

The original candidate and its evidence remain immutable historical records.
The follow-up candidate changes one production module (the Decimal magnitude
fix) and four test modules, including the new time Runtime test. The scope
comparison is retained in the new gate's `closeout.json`.

## Review follow-up verification

The new `make check-agent` gate passed from
`2026-09-08T14:39:00.715903Z` to `2026-09-08T14:48:58.943820Z`, in 598.23
seconds. Its 763-file source/test/configuration manifest has SHA-256
`e4c542a1505065abf218971810fdd34947d5196938221085d89de16cd3176c0e`.
The before and after manifests match, as do the final configuration checks.

| Check | Follow-up result |
| --- | --- |
| Ruff and import contracts | Passed |
| Full source typing | 387 files passed |
| Explicit modified test/helper typing before the gate | 16 files passed |
| Default tests | 5,932 passed in 80.62 seconds |
| Runtime tests | 764 passed in 495.68 seconds; zero skips |
| API documentation | Built successfully with warnings treated as errors |

The new bundle is
[`evidence/slice-5a/gate-20260908T143900Z/`](evidence/slice-5a/gate-20260908T143900Z/).
Its index verifies 101 fresh Runtime records: 35 comparison records and the
same 66 adjacent adapter, retained, read-boundary and reliability journeys.
The four added comparison records bind the nonzero Entity/Dimension row-operation
parity and repeated-Dimension time comparisons to this exact candidate. All
record digests were verified after the gate. The complete log digest is
`4be92f7a1706ce30f5bd721e9a442414bc878f1ca1b7b5f411da7b544b40affa`.

This run used the same pinned MinIO image and isolated versioned buckets. An
earlier follow-up attempt crossed a recorded 55-minute macOS suspension and
failed with S3 clock-skew errors plus a local execution failure. Those five
affected cases passed unchanged after wake; the complete gate above then
passed with idle sleep inhibited. The interrupted attempt is retained as
failed evidence and does not contribute to this successful gate.

## Pre-commit candidate verification

The owner subsequently authorized a local commit. Its normal Ruff formatting
hook wrapped one assertion in `test_lazy_compare_runtime.py` and one tuple in
`test_lazy_compare_runtime_acceptance.py`. Both files have identical Python
syntax trees before and after formatting; no implementation or behavior changed.
The complete gate was rerun against this final formatted candidate rather than
carrying forward the preceding source digest.

The final 763-file source/test/configuration manifest has SHA-256
`8477c76aebc13e75f9faef35ea4af64eddbe0d18318b8915816641630413d1bc`.
`make check-agent` passed from `2026-09-08T14:57:37.213684Z` to
`2026-09-08T15:06:53.168578Z` in 555.95 seconds: 5,932 default tests in 83.73
seconds, 764 Runtime tests in 453.10 seconds, zero skips, full typing/lint and
API documentation. Explicit typing also passed for the two formatted test files.

The final bundle is
[`evidence/slice-5a/gate-20260908T145737Z/`](evidence/slice-5a/gate-20260908T145737Z/).
Before and after manifests match. Its 101 fresh Runtime records include all 35
comparison records and 66 adjacent journeys, using the same pinned MinIO image
with isolated versioned buckets. All record digests were verified; the complete
log digest is
`6ef6e9f9f61189bcdd912d5328f2c288497e02fad687c30384a720cab69d7ee5`.

## Closure

Slice 5a and the allocated multi-input/combined-budget extension of parent Slice
4 are complete at the final formatted candidate's private technical gate. Only this
unit is closed. Attribution
5b-5d, discovery, public cutover and public Agent acceptance remain open under
their existing allocations. The owner's subsequent commit instruction permits
the local commit; no push, facade switch or release is included.
