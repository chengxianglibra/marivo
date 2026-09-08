# Slice 4c: concurrency and cold reconciliation

Status: Slice 4c and its review follow-up accepted on 2026-09-08; Slice 4d and parent Slice 4 remain open.

## Prerequisites and frozen boundary

Slice 4b is committed at `431492b2`. Since planning, the accepted Slice 3b
review was committed at `8f922646`; implementation starts from that clean HEAD.
Its 713-file candidate fingerprint is
`34c8cb9458d0e83529092e6279639f68c3ac2a02302b246ef319416efeb6f78b`.
Planning reran 49 guard, Store and failure tests successfully. The accepted
Materialization Runtime and public-cutover designs remain the contract owners.

This unit completes the existing local Parquet, immutable DuckDB engine and
versioned S3 object routes. Existing Population/Metric, retained parts, sampling,
source bindings and registered local methods supply the fixtures. No additional
operator, public export, Help target, facade or Store-generation switch is added.

The owner selected conservative S3 recovery: acknowledged terminal requests are
durably discharged promptly; genuinely unknown requests remain incomplete and
block only their own Session. No automatic S3 fencing markers are introduced.
Terminal responses include acknowledged HTTP errors: these terminate the
request but do not establish a successful write or remove the object's separate
cleanup obligation. Timeouts and other unknown outcomes remain unproved.

## Exact ownership and shared seams

- Session concurrency: `materialization/writer_guard.py`, `errors.py`, and only
  creation/activation plus pre-guard action state in `admission.py`.
- Worker lifetime: `materialization/resources.py`, `local_worker.py`, and a
  focused private lifetime capability module. The coordinator wires its worker
  reservation/supervision call sites in `admission.py`.
- Object requests: `materialization/object_storage.py`, `object_termination.py`.
- Recovery coordinator: `materialization/store.py`, `reconciliation.py`, and
  the worker handoff above. No semantic or numerical contracts change.
- Tests: `test_lazy_runtime_concurrency.py`, `test_lazy_worker_recovery.py`,
  `test_lazy_adapter_crash_acceptance.py`, `test_lazy_reconciliation_snapshot.py`,
  `test_lazy_binding_cold_acceptance.py`, their focused process helpers, and
  directly affected existing guard/adapter/worker regression tests.
- Documentation: this record, its `.gitignore` inclusion, and the Slice 4c
  acceptance/matrix entries in the public-cutover plan.

Parallel contributors preserve other edits. The coordinator owns Store writes
and shared integration; worker/request contributors communicate their exact
handoff before that integration changes. All new Python artifacts remain English.

## Implementation order and invariants

1. Acquire the Session guard before resetting producer diagnostics. Resolve a
   raced named Session by releasing the candidate lock, acquiring the winner,
   rechecking identity and reconciling before activation under that same guard.
   Activation writes Session metadata and the current pointer in one Store
   transaction; external recovery work is outside that transaction. Every
   existing-name activation follows this boundary; explicit-ref `open()` remains
   read-only. Reopening uses the caller's explicit object access bindings:
   absent access retains proven-terminal garbage for later cleanup, while an
   unknown execution still blocks activation.
2. Reserve a nonce-owned worker workspace and execution before file creation or
   spawn. Inherit one already locked lifetime descriptor before calculation;
   retain it through worker/transfer termination. Cold recovery proves release
   through exact nonblocking acquisition, never parent PID or elapsed time.
3. Discharge acknowledged S3 request obligations before subsequent callbacks;
   retain payload obligations until exact cleanup or atomic ownership transfer.
4. Read selected recovery metadata in one Session snapshot, close it before
   external work, and preserve authoritative publication/readback semantics.
   Retire process-local proofs only after durable journal removal. Harmless
   cleanup failure keeps its obligation and permits subsequent work.

Worker lifetime resources do not elect producers or provide another writer
guard. No lease, heartbeat, waiting contender, origin replay or retry fallback
is introduced. Unknown remote termination remains distinct from harmless garbage.

## Acceptance selectors and evidence

The concurrency suite covers three targets, same/different keys and
process/thread/reentrant contenders, canonical-name/current-pointer races,
committed read availability and actual cross-Session backend overlap. Contenders
must create no Run, query or reservation and must preserve producer diagnostics.

The worker suite covers reserve/create/spawn/terminal crashes, live orphan
blocking, fresh-process recovery after actual exit, resource ownership and
harmless cleanup. Adapter crash acceptance covers native storage boundaries, primary and
retained-part finalization, metadata rollback/commit, lost acknowledgement,
unavailable readback and true uncertain S3 requests against a real service.

Snapshot tests independently inspect Store query scope and integrity before any
external cleanup. Binding acceptance reconstructs captured parameters in fresh
processes, forbids execution-time ambient lookup, separates changed-value keys
and scans Store/errors/diagnostics for raw-value canaries.

Use the existing fixture skill, isolated `tmp_path` projects and barrier/handshake
synchronization under xdist. The supported integration versions remain DuckDB
1.5.3, Ibis 12.0.0, pandas 2.3.3, PyArrow 25.0.1, boto3/botocore 1.43.89 and
MinIO `RELEASE.2025-09-07T16-13-09Z`. Start a separate disposable loopback MinIO
service for 4c; do not stop or reuse another task's service resources.

Run focused `make test-agent`, touched-module typing/lint, then `make check-agent`
with `MARIVO_TEST_S3_ENDPOINT` set and no S3 skips. Retain runtime evidence under
`docs/superpowers/plans/evidence/slice-4c/` via
`MARIVO_SLICE4C_EVIDENCE_DIR`. Each journey records actual process identities,
Run/Artifact/Evidence and journal outcomes, source/request/transfer counters,
and matching before/after candidate manifests. Gate and journey evidence must
refer to the same final candidate.

The owning capability rows are Run admission/publication/concurrency/reconciliation
and parameterized source bindings. Close only 4c after its gates pass; 4d and
parent Slice 4 stay open. No commit, push or release is part of this unit.

## Implemented protocols and focused verification

The worker capability is `pandas_worker_file_lifetime@v1`, paired with a
`pandas_worker_workspace@v1` cleanup obligation. The two reservations precede
filesystem creation. The child verifies the exact inode/nonce and already-owned
open file description before consuming its request, and keeps that descriptor
until OS process exit. Transfer threads borrow protected duplicate descriptors;
a late thread cannot borrow a recycled descriptor after supervision has ended.
An unresolved feeder keeps the Session pending even after its worker exits.
Read-only `DatasetRuntime.open()` remains separate from guarded activation and
recovery. Local lifetime errors do not retain raw filesystem exception contexts.

The S3 writer removes only its acknowledged request obligation immediately.
Payload/manifest reservations still transfer in the existing publication
transaction. A real forwarding proxy demonstrates both killed callers and an
actual SDK read timeout after MinIO has accepted a PUT. Fresh healthy clients,
dead caller PIDs, and an absent object do not invent the missing termination
proof. These selected Runs remain incomplete while their committed reads and
other Sessions remain usable. This unit intentionally provides no automatic
resolution for genuinely uncertain S3 requests.

The recovery snapshot selects only incomplete or still-obligated producers in
the requested Session. It validates all selected metadata before external
cleanup, including committed-output ownership conflicts. Other Sessions and
unobligated historical records are not decoded. All in-process source, planner
and request proofs follow durable journal removal; a failed Store transaction
retains their proofs. Connection-owned planner resources retain an exact derived
proof through their own discharge, independent of discharge ordering.

Focused checks completed before the final gate:

- 28 concurrency/guard cases and 12 final different-key/overlap cases pass with
  real MinIO. Actual DuckDB UDF barriers prove two Sessions overlap inside SQL.
- 67 adapter/failure cases pass; the subsequently added real SDK timeout case
  also passes independently. The frozen suite includes 27 adapter crash cases.
- 16 worker recovery cases pass, including true orphan-process recovery and an
  unresolved feeder. The existing local/worker checks also pass their behavior
  assertions; final candidate checks below supersede intermediate runs made
  while parallel contributors were editing files.
- Five independent snapshot/proof tests and three fresh-process parameterized
  binding journeys pass. Binding journeys cover local, engine and object
  targets with the origin database and HTTP source offline for cold reuse.
- All 22 changed Python files pass scoped typing, lint and formatting checks;
  the source-wide typing and import/lint gates also pass.

The pinned MinIO image digest is
`sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
This run owns container `marivo-slice4c-minio`, label `marivo.slice=4c`, loopback
endpoint `http://127.0.0.1:32769`, with no mounted data volume. Each test uses a
separate disposable versioned bucket and the existing fixture credentials.

## Reproduction and final candidate

With that isolated service available, retain the new and shared journeys with:

```sh
MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:32769 \
MARIVO_SLICE4C_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4c" \
MARIVO_SLICE4B_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4c/adapters" \
MARIVO_SLICE3B_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4c/retained" \
make check-agent
```

The final gate passes against the 723-file candidate
`d2d8fcc4ed6c978fb7bd32e13b51f79b640c01bc07e628bc3c00eb66df1dbf39`.
Its outer gate record captures source/test/configuration manifests before and
after the complete run. The evidence index binds every emitted journey's file
hash to that same unchanged candidate, including concurrency records. Adapter,
binding and worker journeys also check their own before/after manifests.
Raw logs and records stay ignored under `evidence/slice-4c/`.

## Final acceptance: 2026-09-08

`make check-agent` passes lint/import contracts, typing for **371 source files**,
**6,439 tests in 655.59 seconds**, and API documentation construction. Real
MinIO is enabled throughout and no S3 tests are skipped. All 22 touched Python
files also pass explicit scoped typing and formatting checks. The candidate
manifests before and after the gate are identical.

The final evidence bundle contains **60 fresh Runtime records**:

| Evidence | Records | Observed boundary |
| --- | ---: | --- |
| Contention, backend overlap and canonical-name race | 22 | 18 target/key/contender combinations, three real SQL overlaps, one process creation race |
| Engine/object interruption and remote uncertainty | 27 | Exact resource/part crash points, publication/readback outcomes and two real withheld-response cases |
| Worker process loss and orphan recovery | 4 | Reservation, created lifetime, terminal boundary and live orphan isolation |
| Parameterized source binding recovery | 3 | Local/engine/object same-value cold reuse and distinct changed-value bindings |
| Shared 4b/3b journeys | 4 | Fresh engine/object adapter and local/engine retained-state continuations |

All **76 embedded before/after candidate manifests** match the gate. The index
checks that every Runtime record was emitted within that gate, records its exact
content hash, and binds concurrency records to the enclosing unchanged candidate.
Credential and captured-parameter canaries are absent from the retained records.

Local evidence is linked through [the gate](evidence/slice-4c/gate.json),
[complete check log](evidence/slice-4c/check.log), and
[content-addressed evidence index](evidence/slice-4c/evidence-index.json).
These are real library Runtime/service/process journeys; the public facade and
real-Agent acceptance remain later slices. Unknown S3 outcomes remain explicitly
incomplete, while all resolved outcomes have a complete committed Dataset or a
terminal failed Run without partial publication.

Slice 4c is complete. Slice 4d, parent Slice 4 and public cutover remain open.
No commit, push or release was performed.

## Review follow-up: 2026-09-08

The review is bound to the uncommitted 4c candidate above on `8f922646`.
The following dispositions preserve the approved private scope:

| Suggestion | Decision and resulting boundary |
| --- | --- |
| Reconciliation errors reuse publication diagnostics | Accepted. Recovery integrity failures now describe the selected Session/Run recovery invariant, use `stage="reconciliation"`, and retain the producer ref where available. Selected decoder failures are translated at the snapshot boundary. Corruption tests verify diagnostics and refusal before any external cleanup. |
| Discharge callbacks reference a later fixture assignment | Accepted as a small test improvement. Both callbacks are test-local, not module-level, and their original late binding was valid. Setup now precedes callback definitions and hook installation. |
| Duplicated manifest, snapshots and evidence writers | Reuse the existing candidate-manifest helper. Preserve count snapshots and evidence writers whose selected relations, payload schemas or persistence responsibilities differ; no general evidence framework is added. |
| Hidden mutation in the termination predicate | Rename the private function to `confirm_execution_termination` and document idempotent derived-proof retention. A relation must retain its own exact proof until its own durable discharge even after its connection obligation is removed. |
| Bundle supervisor arguments into another type | Not adopted. `WorkerReservation` and `_LifetimeDescriptors` already own reservation identity and live descriptor references. A further forwarding container would not repair a demonstrated defect. |
| `create()` calls `runtime._event` | Retained. A classmethod accesses its own class's event wrapper so the returned runtime accounts for reconciliation events. |
| Repeated capability strings | Retained under existing private capability conventions; no new enum or registry is needed. |
| Missing automatic external fencing | No missing approval or implementation. The authorized plan explicitly selected exact termination proof and conservative Session-local blocking for unknown S3 requests. The cutover row and earlier 4b wording now name that boundary directly. |
| Reconciliation and activation are separate transactions | Retained and clarified above. One Session guard spans both; Session metadata and current-pointer writes are atomic together. External proof/cleanup does not hold a Store transaction. |
| `SessionBusyError.session_ref` exceeds scope | Rejected: the user plan expressly requests safe Session identity, and the frozen ownership list includes `errors.py`. |
| Terminal HTTP errors are discharged | Retained and documented above: authoritative failure responses end request execution; payload cleanup remains independently journaled. The real conditional-write collision test preserves foreign versions. |
| Receiver join guard and receipt type narrowing exceed scope | Retained. The join guard protects teardown if thread startup fails; `LocalReceipt` narrowing permits subtype-specific access without ambiguous typing. |
| Named reopening depends on caller object bindings | Intentional access boundary. Missing access defers only proven-terminal garbage and preserves its journal; no stored or ambient credential fallback is added. A real-MinIO named-reopening regression covers later cleanup with explicit access. |
| Every name hit reconciles | Retained: every activation uses the same guarded recovery path, including a race loser. Explicit-ref `open()` is the separate read-only path. |
| Absent worker lifetime implies termination | Document the owning module invariant: reservation precedes creation, spawn requires the exact locked file, and cleanup can unlink only after all execution proofs succeed. Tests now explicitly reject cleanup while a duplicate holder lives and retry after filesystem cleanup but before durable journal removal. |

Focused follow-up checks pass: 14 recovery snapshot cases, 12 worker proof and
cleanup cases, and all 42 adapter failure cases (41 existing plus the new
named-reopening case), with the real object endpoint enabled. All 22 changed
Python files pass explicit scoped typing, lint and formatting. An independent
review of these fixes found no remaining concrete defect.

The follow-up gate uses a separate disposable container
`marivo-slice4c-review-minio`, label `marivo.slice=4c-review`, at
`http://127.0.0.1:32768`, with the same pinned image digest and dependency
versions. Its anonymous `/data` volume belongs to this run. Fresh evidence is
retained separately under `evidence/slice-4c-review/`, with its shared journeys
under `adapters/` and `retained/`; the initial acceptance evidence above remains
available for comparison. The gate uses the reproduction command above with
this endpoint and evidence directory substituted.

Fresh `make check-agent` passes lint/import contracts, typing for **371 source
files**, **6,449 tests in 562.26 seconds**, and API documentation construction.
Real MinIO is enabled and no S3 tests are skipped. The gate starts and ends with
the same **723-file** candidate SHA-256
`383a28ecf6367b378c813b736cbdac4d549829acf87ff5dc3b598208e0d4a251`.
All **60 fresh Runtime records** were emitted within this gate; their content
hashes and **76 embedded candidate manifests** match its unchanged candidate.
The original record-category counts remain unchanged. Credential and captured
parameter canaries are absent from these retained records.

The [review gate](evidence/slice-4c-review/gate.json),
[complete check log](evidence/slice-4c-review/check.log), and
[evidence index](evidence/slice-4c-review/evidence-index.json) supersede the initial
4c candidate as completion evidence. Slice 4c remains complete; Slice 4d, parent
Slice 4 and public cutover remain open. No commit, push or release was performed.
The owned MinIO container and its anonymous data volume were removed after
verification. Colima, started for this run with no other running containers,
was stopped successfully.
