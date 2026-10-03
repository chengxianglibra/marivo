# R7.8 retention implementation and bounded evidence

Entry was clean `panda@bd51f4dc5c5d327685a84f6b7d1dd9b9183d40a1`. Concurrent
commit `4c6516e626` untracked the existing R7.7 qualification records; those local
files are preserved without edits. This implementation is uncommitted. AGENTS.md
and packaged skills are unchanged; no push, release, MinIO or release-check ran.

## Implemented contract

Logical Anchors construct retention with one exact same-Subject returning Event,
an explicit elapsed/calendar window and optional completeness declarations. Event
elapsed witnesses lower through public Ibis; calendar and Journey starts consume
captured source inputs through registered Python methods. Source preparation
precedes every local stage. There is one graph/Runtime/Store 7 execution path.

The retained ledger owns the original full-key instance Omega, start/deadline,
return witnesses, business order, exact Event/source/window coverage and canonical
Journey assignments. Each instance is true, false or unknown: a witness establishes
true even with partial follow-up; false requires coverage of the entire window;
absent coverage leaves unknown. Deadline and own occurrence are excluded. Distinct
same-instant uses require captured integer, enum or precedence order, never physical
identity ordering. Calendar deadlines reject ambiguous/nonexistent local times.

`by_subject(rule=mv.any_anchor())` and `mv.every_anchor()` quantify complete,
nonempty fibers over the actual full Subject image. Subjects without Anchors are
absent. Original Omega and deterministic bounds survive status/selection views;
empty Omega gives Undefined bounds. Only decidable true selections expose member
images, with exact SubjectBinding required for instance results. Source observation
after a logical true member image prepares its inputs before local selection.

The independent 25/5/70 oracle retains all 100 starts and one return shared across
25 starts: true/false/unknown counts are 25/5/70 and bounds are [0.25, 0.95]. Counts
use exact integer state and bounds convert Fraction once for display. This is a
deterministic identification interval; generic bounds arithmetic is outside scope.

At the user's selected boundary, starts-only MaterializedAnchorDomain has no
captured returning input. Its retention call rejects before I/O or a Run with a
concrete repair to construct retention before executing Anchors. No capture API or
fixed anchor.retention implementation was added. MaterializedRetentionResult
continues status, selection and Subject quantification from its retained ledger.

Exports, native Help, repr/show/contract, typed guidance, API docs and executed
English/Chinese latest examples are aligned. Required retained parts reject missing,
damaged, swapped or semantically inconsistent ledgers before recovery exposes a
result. Publication faults, cancellation and deadlines preserve prior Artifacts and
clean resources. Changed source facts produce a fresh source Artifact.

## Actual execution and original qualification

The [qualification record](2026-10-03-marivo-r78-qualification.json) retains all
**1080 original P19-P22 cells**, plus the **540 original shared P24/P50 cells**,
in a SHA256-checked compressed payload. Method/key/time profiles, IDs, routes,
mandatory flags and original planned status are copied unchanged from R7.1.
Independent tests compare the payload against that frozen inventory.

Eleven three-process worker cases passed: all nine Subject/occurrence key pairs
under table/us/UTC, plus int64/int64 Parquet/us/New York and Parquet/ns/UTC. Each
producer checks four source outputs against a raw full-key oracle, including
int64 identities above 2**53. The combined receipts contain **44 source kernel
outputs, 22 fixed quantifier kernel outputs, 242 fixed continuation outputs,
242 cold exact hits and 44 original cold reads**. Continuation outputs include
status/true/false/unknown views, two Subject quantifiers and four true member images.
These counts overlap intentionally: the two quantifiers are part of the 22 outputs
per case. Cold processes forbid retention execution and confirm exact Artifact
references; cold hits do not execute or qualify new kernels.

Those eleven matrix cases used the earlier integer fixture revision, removed
databases/Parquet and guarded all semantic/source loading. A final strengthened
int64/int64 table/us/UTC case passed on the final implementation and also removed
the semantic model directory before offline continuation and cold recovery.
Offline guards cover semantic loaders, source entry/batches, DuckDB/Ibis connections
and Journey matching. Separate final Runtime tests exercise table/Parquet,
elapsed/calendar snapshot-to-validity participant mappings through the public path.
They do not substitute for version axes in every original key/time worker case.

**All 1080 original R7.8 qualification cells remain unverified.** The bounded workers
do not fulfill every requirement of the original full profiles. Seventy-nine worker
cases were not run; the 180 P19/P20 fixed method targets lack returning inputs.
Status reads, quantifiers, member images and cold hits cannot qualify those fixed
anchor.retention targets. P24/P50 transport evidence remains a retention-specific
responsibility, not qualification of all shared producers or native targets.

The inherited R7.7 ledger remains unchanged: 2856 bounded passed and 3894 unverified
original cells, including 2070 unfinished fixed method targets and 90 P48 source
native targets. R7.8 does not promote them. Full A13, same-wheel recovery, remote
backends, full R7 and release acceptance remain unverified.

## Final checks

The local review found three defects and the authorized repair closed them:
return capture now uses the actual filtered Anchor population; per-instance
coverage considers both declarations and observed receipts without bridging gaps;
and an external is-defined predicate no longer certifies receiver Unknown Cells.
Sixteen focused regressions passed, including positive/empty filtered member
observation, elapsed/calendar coverage with bounded/source-origin declarations,
adjacent intervals and a gap, and source/fixed external predicate transport.
Recovery and fixed Subject quantification retain the repaired coverage truth.

The [acceptance record](2026-10-03-marivo-r78-acceptance.md) lists the final gate
and focused Runtime results. Raw local receipts/logs are in ignored
`evidence/r78-matrix/`, `evidence/r78-final/` and `evidence/r78-validation/`; paths
and digests are included in the qualification record. Candidate implementation,
test and owning-document digests identify the checked files. Failed development
runs were repaired and superseded; they do not count as acceptance evidence.

| Responsibility | Executable evidence |
| --- | --- |
| V06/V12/V13 | Fixed Omega, shared return, 25/5/70, half-open windows, own exclusion, integer/enum/precedence, any/every partial fibers, empty/no-Anchor Subjects, exact large/composite keys, DST 23/25-hour deadlines |
| V14 | Three receipt faults, semantic deadline/status/coverage/truncation/witness mutations, full binding validation, declared/observed interval recovery, fresh-process source-free continuation and exact recovery |
| V15 | Publication insert/before-commit faults, cancellation/deadline, resource cleanup and prior Artifact preservation |
| V16 | Native elapsed guard, source freshness, Journey assignment retention without rematching, fixed-input early rejection, F13 filtered logical member observation and external predicate truth |
| V17 | Independent disclosure/export snapshots, Help reachability/budgets, typing, result guidance, API docs and executable bilingual examples |

The authorized implementation scope is complete. Original full R7.8/A13
qualification remains incomplete at the selected fixed-input boundary.
