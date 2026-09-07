# Slice 4a: guarded local execution foundation

Status: Slice 4a accepted on 2026-09-08; parent Slice 4 remains open.

## Prerequisites and authority

Slice 2b and Slice 3a are the predecessors. Entry HEAD is `5e63a7f1` and the
entry working tree was clean. The planning baseline passed 90 compiler, storage and
ordering tests. The accepted planner, materialization runtime, Observation and
typed-operator designs own behavior; the cutover plan owns slice boundaries.

## Frozen scope and shared seams

Use the existing tested DuckDB source adapter and local Parquet writer. Add
private exact placement and pandas implementations of `metric.where`,
`metric.metric`, `metric.rank` and `metric.limit`. Local admission requires
primary-only Metric semantics without sufficient-component or sampling parts.
Required-part reading and combined input accounting receive isolated contract
tests; full row/part transformations remain Slice 3b. Engine/object adapters,
identity checkpoints, folds, Compare and Forecast remain with later slices.

The Core retains exact immutable input values without adding identity facts.
Compiler recipes remain ephemeral. Source binding uses exact owner identity.
Artifact continuations inherit committed authority and never consult origins.
Runtime owns the worker, limits, resources, final writer and v3 publication.

Owned implementation: `datasets/base.py`; `compiler/normalize.py`, `placement.py`; `operators/__init__.py`, `registry.py`, `row.py`;
`materialization/admission.py`, `publication.py`, `storage.py`, `local.py`,
`local_worker.py`, `resources.py`. Paths are under `marivo/analysis/`.
The existing Core registry reconstruction test passes the retained immutable inputs
to its private constructor. No semantic definition or v3 wire schema is changed.

Owned tests: `tests/test_lazy_local_placement.py`, `test_lazy_local_rows.py`,
`test_lazy_local_guards.py`, `test_lazy_local_execution.py`,
`test_lazy_local_runtime_acceptance.py`, `lazy_local_runtime_worker.py` and
`lazy_local_fixtures.py`, plus `tests/test_lazy_materialization_storage.py`.
Existing Core tests change only for exact input binding. The cutover plan and
this exact execution record own documentation; `.gitignore` includes only this
record as an exception to ignored working plans.

## Implementation and acceptance order

1. Freeze baseline, input contracts, method registration and placement.
2. Implement exact pandas row semantics and complete Arrow/part validation.
3. Supervise a single suffix worker, checking allocations, RSS and deadline.
4. Integrate source/local and Artifact/local routes with the existing v3 commit.
5. Prove independent semantic references, boundary/overflow/failure cases,
   no-fallback placement and no-source cold continuation in fresh interpreters.
6. Run scoped typing and tests, then `make check-agent`; bind terminal evidence
   and no-I/O evidence to matching candidate manifests.

Runtime defaults: 100000 rows per input/output, 64 MiB combined decoded input
and output, 8 MiB decoded batches, 60 seconds for transfer and computation.
Intermediate live-allocation allowance is 256 MiB; worker RSS allowance is
512 MiB. Limits are private operational settings, never definition identity.

Failure injections cover late stream validation, missing/corrupt required parts,
combined inputs, conversion, method size, intermediate/output/RSS overflow,
blocking worker/partial-response timeout, worker exit and publication. No private output may
survive a failed resolved Run; unproved termination uses recovery-blocked.

The runtime journey creates an Artifact, disables the source in a new process,
executes `where -> rank -> limit -> metric`, publishes once and cold-recovers
with an exact binding hit. Inspect actual statements, input/output counts,
direct DataFrame handoffs, worker termination, resource journal and v3 bundles.
Storage-stream admission is tested independently of local collection admission.

## Disclosure and completion

No public exports, Help, facade, eager path, public docs or Store generation
switch. No commit, push or release. Update only the 4a acceptance record after
all gates pass; parent Slice 4 and Slice 3b remain open. Evidence belongs to the
cutover matrix's local execution and transfer guards row, and Journey F.

## Implementation record

- Source support is deliberately pinned to the integration-tested DuckDB 1.5.3 /
  Ibis 12.0.0 pair. Unknown adapter versions are ineligible before data work.
  Source binding equality requires the same owning object and datasource identity.
- Local row methods retain registered shapes, all rank directions/ties and
  partitions, typed SQL null predicates and the admitted source NaN comparison
  order. Median/distinct Metric fixtures exercise primary-only continuations;
  contribution/sampling-part row transformations remain explicitly rejected.
- Immutable input references expose each existing Core contract to the compiler.
  They never enter fingerprints, serialization, Store metadata or Artifact state.
- The worker receives only its exact primary input, owned RowCall values and
  runtime limits through private IPC. Logical roots, source plans and Dataset
  objects are not serialized. Arrow is used at ingress and the final writer;
  intermediate DataFrames stay in the same worker.
- Primary/part collection validates complete rows, actual schema, keys, order,
  nullable fields and exact selected backing hashes. Combined Arrow buffers,
  conversion, key-validation workspace, method copies, output, RSS and deadline
  have separate guards. Required-part reads select only the supplied exact role.
- Worker creation is reserved before spawn, and successful supervision proves
  both worker exit and input-feeder termination. A dead parent alone is never
  treated as subprocess termination proof. External-termination/cold fencing
  remains Slice 4c; an unresolved worker obligation blocks only its Session.
- The v3 Run records exact input Artifact refs and inherits committed Population
  and semantic dependency authority for local continuations. All final metadata
  uses the existing publication and lost-acknowledgement readback protocol.

Focused evidence before the final candidate: 102 local/Core tests passed;
114 local/storage/source/no-I/O regressions passed in 107.73 seconds; scoped
mypy passed for 28 implementation and test modules. A three-process runtime
journey passed in 20.64 seconds. These are iteration results; final acceptance
must use the full gate and the matching final runtime manifest below.

## Pre-review acceptance: 2026-09-08 (superseded candidate)

- Final `make check-agent`: lint/import contracts pass, mypy checks 360 files,
  6159 tests pass in 205.62 seconds, and API docs build successfully.
- Scoped typing: 28 implementation/test modules pass with explicit package bases.
- Candidate manifest: 685 files, SHA-256 `ac68ae0744811ca6aba04b03ae8551c7149b8199290ab71cecd0028909dacdc5`.
  The runtime test's before/after manifests match a fresh post-gate manifest.
- Terminal evidence: `evidence/2026-09-07-slice-4a-runtime.json`. Source production,
  local continuation and cold reconstruction use distinct interpreter PIDs.
  The continuation has four direct DataFrame calls, zero source statements and
  no remaining resources. Construction and placement I/O attempts are empty.
- Session `session_d93ec6bc3347493a9edf76c1a9895893` publishes local Artifact
  `artifact_f8ce4467c45842b4bb846355d8b23716` with three ordered rows.
  Worker 91950 records peak RSS
  264306688 bytes and terminates before publication.
  Cold reconstruction starts no worker and leaves the exact v3 snapshot unchanged.
- No public cutover, commit, push or release. Slices 3b and 4b-4d remain open.

## Review follow-up: 2026-09-08

Review corrections passed the new candidate gates below. The preceding
acceptance numbers describe the pre-review candidate only.

| Review item | Decision and evidence |
| --- | --- |
| Duplicate output reservation/writing | Adopted: both routes call Runtime `_write_output`; source streaming stays inside its original engine deadline. |
| `inherited_root` in diagnostics | Adopted: errors use the domain phrase "inherited Population definition". |
| Duplicated local ordering | Adopted: `frame_comparator` owns scalar, direction and null ordering for sorting and validation. |
| Paired row/row-set wrapper | Not adopted: Core already validates this pair; introducing a second wrapper across the existing storage and operator APIs adds no missing invariant. |
| Function/loop imports in local validation | Adopted: storage scalar/type helpers are imported once at module scope. |
| Worker locator duplication | Adopted: creation and verification use one private locator helper. |
| Test evidence writes into the checkout | Adopted: default acceptance writes only under `tmp_path`; durable evidence is copied explicitly after acceptance. |
| Import used only by `assert callable` | Replaced with an actual constructor warm-up before the no-I/O audit; the tested public-style construction remains inside the audit. |
| Version mismatch silently changes all nodes to local | Not reproduced: each source graph reaches a source-required root and fails before Run admission. Runtime regressions cover separate DuckDB and Ibis mismatches with unchanged Store snapshots and zero source/worker calls. |
| Variable-width reader coverage | Adopted: actual Parquet wide ASCII/UTF-8 primary and large-binary required-role reads cover decoded limits and rejection before row-group decoding. Binary remains an isolated part contract, not a new production Metric type. |
| Portable versus adapter-specific support | The 4a matrix admits only the tested DuckDB pair; it makes no portable source claim. Engine/object Artifact adapters belong to 4b; broader source coverage remains an open parent Slice 4 obligation. |
| Missing execution record | Adopted: a narrow ignore exception includes this exact record in the reviewable change. |
| Unused graph primary output | Adopted: Runtime verifies the selected primary is the requested final Dataset before admission. |
| Worker inherits DuckDB termination proof | Not reproduced: `pandas_worker@v1` is dispatched before the DuckDB capability and requires explicit local terminal proof. A regression rejects proof after changing parent identity without probing parent liveness. |
| Early worker failure masks guard diagnostics | Adopted: independent duplex reception drains the structured worker result during streaming and keeps partial responses subject to supervision; the real worker regression overflows its row cap while a later upload exceeds pipe capacity. |
| Projection loses sort fields | Not reproduced for admitted Metric operations: partitions are row keys; rank order consists of partitions, generated rank and row keys. Projection retains those fields, and Core rejects a dangling order field. A real source/local parity regression drops the ranked Metric value and then limits the projection. |
| Runtime budget integration coverage | Adopted: parameterized actual Runtime failures cover input/batch/method/intermediate/output/RSS/deadline settings and unchanged Artifact/Evidence/resource state. Exact boundary arithmetic remains with independent guard tests. |

### 4a support matrix

| Input and operation | Placement and authority |
| --- | --- |
| Existing Observation source algebra, DuckDB 1.5.3 / Ibis 12.0.0 | Exact bound source implementation; maximal eligible prefix. |
| Different source adapter or version pair | No source eligibility; source-required roots reject before Run/data work. |
| Primary-only Metric `where`, `metric`, `rank`, `limit` after local frontier | Registered pandas suffix; dependency successors remain local. |
| Committed local Parquet primary input | Exact committed descriptor and PyArrow read; source versions and source availability do not govern this path. |
| Required parts / multiple input collection | Isolated internal read/budget contracts; no production part-transform or multi-input consumer added. |
| Portable source support, engine/object Artifact adapters, full part transformations | Not admitted by 4a; retained for subsequent owning slices. |


## Final review verification: 2026-09-08

- `make check-agent`: lint and import contracts pass; mypy checks 360 files;
  **6180 tests pass in 233.25 seconds**; API docs build. The review adds 21 tests.
- Explicit scoped mypy checks 16 reviewed implementation/test modules.
  Directed guards/storage tests pass (72); row-ordering tests pass (15).
  `git diff --check` passes.
- The 685-file candidate SHA-256 is `717ff00a041ecd3ad30c9fcade72017bbb48b6f1c812cbd6f2a049f2e4a07991`.
  The saved pre-gate manifest, the three-process test's before/after manifests
  and a fresh post-gate manifest match exactly. The manifest covers sorted
  library/test Python paths plus `pyproject.toml`, `Makefile` and `.importlinter`,
  with path/NUL/content/NUL framing; generated files and docs are excluded.
- Runtime versions: Python 3.12.13, DuckDB 1.5.3, Ibis 12.0.0, pandas 2.3.3,
  PyArrow 25.0.1. Session `session_268534de5a244b13a9a14c4522a634e9`
  publishes local Artifact `artifact_1bcde72751e744f2835188da013d44a8`.
- Source production and local/cold continuation run in separate interpreters.
  The source is offline before the local path. The result has identities
  `(3,)`, `(2,)`, `(1,)`, revenue `100`, `30`, `10` and rank `1`, `2`, `3`.
  Construction/placement I/O attempts and forbidden source attempts are empty;
  primary and validation source query counts are zero on both local/cold paths.
- Four pandas steps hand their exact private DataFrame objects to their next
  consumer. Worker 24101 records peak RSS 251363328 bytes and
  terminates before publication. The final Store has two terminal Runs,
  two Artifacts, two Evidence envelopes, one input edge, zero Findings and zero
  resource obligations. Cold reconstruction starts no worker and changes no
  Store rows.
- Default runtime acceptance writes `slice-4a-runtime.json` only under `tmp_path`.
  After the gate, the matching raw evidence was explicitly copied to the local,
  ignored `evidence/2026-09-08-slice-4a-review-runtime.json`; this record retains
  the reviewable compact proof above.
- Slice 4a remains accepted. Parent Slice 4, Slice 3b and Slices 4b-4d remain open.
  No public cutover, commit, push or release was performed.
