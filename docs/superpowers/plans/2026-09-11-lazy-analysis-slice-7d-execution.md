# Slice 7d: private Lifecycle replay and canonical retention

Status: private Slice 7d complete; frozen candidate acceptance passed on 2026-09-11.

## Baseline and scope

The implementation starts from `lazy-dataset` HEAD `2addfce9`, which contains the
committed Slice 7c implementation. Existing untracked evidence is preserved.
The accepted Slice 7a identity and occurrence-source seams and Slice 4d retained
read protocols are prerequisites, not newly claimed acceptance units.

This slice owns private source-free Lifecycle construction, native replay,
positive clipped history intervals, all three mandatory canonical retained
roles, atomic publication, bounded Evidence and source-offline recovery.
Lifecycle reducers and `InState` selection remain Slice 7e. Public exports,
Help and site activation, integrated public journeys, commit, push and release
are outside this slice.

## Implementation and retained authority

`LazySources.lifecycle.replay(model, *, window, seed, population=None,
completeness=())` returns a paired private Lifecycle Dataset. The exact current
StateModel, trigger roles, stable subject identity, explicit inception seed,
source definitions and independent membership authority are frozen at
construction. Versioned subjects require explicit membership. Structural
history has no row-filter or reducer entry point in this slice.

Native DuckDB evaluates each distinct trigger source once, resolves participants
at occurrence time, and replays source-origin lookback strictly before the
window end. Recursive SQL is compiler-owned and stays inside source execution;
no raw source rows are collected into pandas. Homogeneous governed occurrence
identity types and the existing microsecond/UTC temporal admission are inherited
from Slice 7a. Simultaneous cross-Event occurrences require a native confluence
proof over state and per-occurrence violation outcomes before deterministic
emission. Governed identity order constrains occurrences within each Event only.
The shared execution deadline covers coverage-provider reads, replay and proofs.

The primary occupancy projection emits only positive, non-overlapping intervals.
The same replay retains the ordered `lifecycle_legal_transition_trace`, complete
`lifecycle_subject_coverage` ledger and `lifecycle_violation_trace`, each at
version 1. Their exact schemas, keys, roles and fixed record-and-continue
behavior are bound into logical identity. Same-time cycles and self-transitions
remain separate trace rows. The coverage ledger preserves subjects with no
positive interval and the common source-origin-complete prefix, without turning
bounded coverage or observed timestamps into inception proof.

Local Parquet streams, engine payloads and version-pinned objects publish the
primary and all mandatory parts as one bundle. Empty roles remain present.
Combined storage budgets, reservations, failure, cancellation and cleanup use
the existing Runtime owners. Descriptor recovery is metadata-only. Explicit
`revalidate` checks all receipts and native cross-part integrity without reading
current Event or membership sources; primary previews retain Slice 4d's
selective-read behavior. Only aggregate counts and coverage facts enter Evidence.

## Acceptance gates

Final logs, command records and executable candidate manifests are retained under
`evidence/slice-7d/`. Acceptance requires matching before/after candidate hashes
for every gate:

- focused Lifecycle construction and independent numerical reference checks;
- explicit typing of the Lifecycle test modules and workers;
- `make check-agent`, including import boundaries, all default tests, typing and API documentation;
- Lifecycle Runtime, object SDK, per-role failure/cancellation, deadline and membership tests;
- Event Runtime regressions and shared membership/materialization Runtime checks.

The frozen executable candidate contains 968 files and has SHA-256
`489ac9e0573cb30fe7a0a10ed6ee25b9a0ba4b9d50d69a765f3d1086ede2624a`.
The manifest hashes sorted repository-relative paths, a NUL separator, exact
file bytes and another NUL separator for all Python files under `marivo/` and
`tests/`, plus `pyproject.toml`, `Makefile` and `.importlinter`. Documentation,
generated API pages and gate evidence are outside this executable fingerprint.
Each gate records its command, timestamps, exit code, before/after fingerprint,
log checksum and runner checksum. The initial full-check attempt rejected only
the evidence runner's formatting; its failed record is preserved under
`check-format-attempt/`. Formatting that runner did not change the executable
candidate. The final combined Runtime gate follows the successful full check.

The independent-process test records production, source-offline history/part
inspection and exact cold binding in three interpreters, for local and engine
storage. It compares primary and per-part hashes, descriptors, bounded Evidence,
Store snapshots and process identities. Runtime concurrency is at most two
workers. Object acceptance uses native SDK stubs and real local SessionStore
files; no object service is started.

## Final outcome

All seven gate records in `evidence/slice-7d/gates.json` passed with matching
before/after fingerprints and verified log checksums:

| Gate | Observed result |
| --- | --- |
| Focused contracts, numerical oracle and shared regression | 175 passed |
| Explicit test typing | 8 files passed |
| `make check-agent` | Format, lint, import contracts, 468 source files, 7,062 default tests and API documentation passed |
| Lifecycle Runtime and object SDK | 40 passed |
| Event Runtime | 108 passed |
| Shared membership/materialization Runtime | 85 passed |
| Final combined Runtime after `check-agent` | 233 passed, two workers |

The final local process identities are `56079`, `56102` and `56125`; engine
identities are `56154`, `56198` and `56224`. Each triplet retained identical
primary hashes, all three part hashes, descriptors and Evidence after source
removal and exact cold binding. The runtime evidence SHA-256 values are
`ac6edce1a7650df248d35649f6645628f0572a67caf8b921e488914c489779e6`
(local) and
`2d1eef502bb8b97157c48b3c025f6934d8041899dcac1f2e7c219840bee71618`
(engine).

This completes private Lifecycle replay and canonical retention only. No public
activation, Slice 7e completion, commit, push or release is claimed.

## Review fixes and superseding acceptance (2026-09-11)

Both local-review findings are fixed. The native confluence proof groups the
complete simultaneous subject history rather than only equal Event identities.
Within each group it preserves governed identity order for the same Event and
checks every compatible cross-Event interleaving. Different identity values
cannot silently establish cross-Event order. Runtime regression cases prove
atomic ambiguity rejection, cleanup and retry after refining occurrence time;
equivalent terminal violations and same-Event cycles remain admitted.

Retained Lifecycle semantic validation now translates construction errors into
Artifact integrity errors at the codec boundary. Corrupt initial-state and seed
metadata produce an invalid `revalidate` report and typed cold-recovery failure
after source removal, without Store mutation or replay guidance.

The superseding 968-file executable candidate is
`c5f4190f9ad4e5012af836914f85325211850970b1ca6e4fb42e48cc5e3e9b4f`.
`evidence/slice-7d/review-fix/gates.json` records four successful frozen gates:
181 focused tests; explicit typing of the two modified source modules and three
test modules; `make check-agent` including 468 source files, 7,068 default tests
and API documentation; then 237 Lifecycle/Event/shared Runtime tests with two
workers. All before/after hashes and log checksums match. The final Runtime gate
also refreshes both three-process, source-offline recovery proofs and verifies
identical primary, required parts, descriptors and Evidence. Earlier acceptance
records remain preserved as historical evidence. Private Slice 7d remains
complete under this superseding candidate; the original deferred scope remains.

## Additional review triage (2026-09-11)

Accepted findings:

- Remove the accidental `"lifecycle"` marker from Event semantic dependency
  facts. A fixed source-free Event definition now matches the Slice 7c baseline
  digest, pinned by an independent captured-value regression test.
- Restore the Event source boundary in retained-part demand propagation to the
  baseline. The Lifecycle boundary remains necessary to avoid propagating its
  output roles into membership inputs; the Event-only edit was unnecessary.
- Correct the Lifecycle Finding examples and constraints to name `history` and
  Lifecycle history, rather than copied Journey terminology.
- Construct each complete integrity SQL term directly, removing mutations of
  `terms[0]` and `terms[-1]` without introducing another SQL builder.

Deferred judgment suggestions:

- A family strategy, role dataclass migration and generic writer framework
  would broaden this private slice without fixing a demonstrated contract
  failure. The closed role tuples remain bound into the current identity.
- Independent part streams have their own schemas and counts, unlike projected
  `PartWriteSpec` streams. The engine and Parquet fault hooks belong at their
  respective physical writes; sharing an event name does not duplicate the
  retained contract's ownership.
- Internal post-construction type assertions guard Core invariants, not invalid
  user input. Generic helper renaming alone has no observable benefit here.

The supplied review's seven-gate counts and process identities refer to the
original acceptance, before the prior confluence and recovery-error fixes.
That evidence is historical; subsequent frozen candidates remain separately
recorded rather than overwriting it.

Final acceptance for this triage is bound to the 968-file candidate
`732f7d27075a62fad48928bd762ec95c62fffde18c417e3513880112b8621011`.
`evidence/slice-7d/review-triage/gates.json` contains four successful gates:
187 focused tests, explicit typing of five modified modules, full
`make check-agent` with 468 source files and 7,069 default tests plus API docs,
then 237 Runtime regressions with two workers. All before/after hashes and log
checksums match. Both three-process source-offline recovery proofs were refreshed
and retain identical history, three parts, descriptor and Evidence. This is the
current private Slice 7d acceptance; previous candidate records are historical.
