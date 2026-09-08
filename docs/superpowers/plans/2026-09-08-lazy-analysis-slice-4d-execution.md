# Slice 4d: private runtime reads and composed Slice 4 acceptance

Status: Slice 4d and parent Slice 4 implemented and accepted. The 4a-4d composed
gate passes under the owner-approved allocation of multi-input consumption and
combined-budget acceptance to Slice 5a.

## Baseline and authorization

The owner authorized the implementation plan on 2026-09-08. Planning verified
the accepted Slice 4c 723-file candidate digest
`d2d8fcc4ed6c978fb7bd32e13b51f79b640c01bc07e628bc3c00eb66df1dbf39`
and passed 60 Store, codec and receipt tests. Existing work is preserved.
Before implementation, the 4c review was committed as `22284a02`. Its accepted
723-file candidate is
`383a28ecf6367b378c813b736cbdac4d549829acf87ff5dc3b598208e0d4a251`,
verified against the clean checkout and the updated 4c review record. That
review's 6,449-test gate supersedes the earlier planning candidate.
No commit, push, release, public facade, export, Help or site cutover is authorized.

## Ownership and implementation sequence

- Session read models, history and graph: new private `session/_lazy_*` modules,
  plus the Run input/failure codec section of `materialization/contracts.py`.
- Evidence terminal values, Finding codecs and selected reads: new private
  `evidence/_dataset_*` modules. Future producer registration and extraction
  remain owned by Slices 5-7; current production Finding sets remain empty.
- Coordinator: strict existing-v3 Store opening, scoped Store projections,
  Dataset action-port integration, immutable receipt audit and three-axis
  inspection in `materialization/`, plus shared contract integration.
- Parent-gate coverage: real independent DuckDB source connections and actual
  Arrow integer-SUM widening/overflow, and fresh composed read journeys.
- Tests: focused `test_lazy_*` modules and helpers under the existing fixture
  policy; each contributor owns its new modules and preserves other changes.
- Documentation: this record, owning lazy read/runtime amendments, and the
  public-cutover plan's ownership, acceptance and capability matrix records.

Private Session additions are `session/_lazy_read_model.py`,
`session/_lazy_runtime_reads.py`, and focused private history/graph modules as
needed by those owners. Evidence additions use `evidence/_dataset_*`.
These are target-owner extensions; no old persistence module is repurposed.

## Frozen read and inspection contracts

Every read selects one SQLite snapshot without initialization, activation,
writer guards, reconciliation or origin access. Existing Runtime handles may
still execute later; no new read-only permission mode is introduced.
Artifact opening validates metadata and the Evidence envelope only. Finding
pages validate visible records only. Full inspection alone scans every payload,
retained part and Finding, using bounded batches and a deadline rather than
whole-result collection limits.

Storage summary priority is `mutated`, `missing`, `unauthorized`, `unknown`;
all failures remain in `issues`. `readable` requires every storage check to
succeed. Artifact and Evidence axes are independent; unavailable dependent
checks are unverifiable. Inspection never compares current sources or repairs
state. The owner explicitly selected this summary policy.

## Acceptance

Run focused behavioral checks, touched-module typing/lint, and a final
`make check-agent` with a separate pinned versioned MinIO service and no S3
skips. Retain fresh 4a-4d and relevant 3b Runtime evidence under a new isolated
evidence directory, together with complete logs, before/after source manifests
and a hash index. All records must bind one unchanged final candidate.

The parent gate additionally proves real equal-argument DuckDB connections with
conflicting same-named tables, actual backend Arrow SUM widening/overflow,
cross-Session consumption, cold read assembly, exact binding recovery, and
closed successful/failed/incomplete outcomes. Final public real-Agent acceptance
remains Slice 9. Acceptance results and reproduction are recorded below.

## Implemented reads and verification boundaries

`DatasetRuntime.recent(project_root, ...)`, `inspect(project_root, name, ...)`,
and `open(project_root, session_ref)` use the existing-v3 factory. Opening does
not activate or reconcile a Session. Run, Artifact, Finding, history, recap and
Graph reads each select one SQLite snapshot and decode only selected bodies.
Run output relationships are checked independently of unselected output bodies.
Current production still registers zero Findings; test-owned exact registrations
exercise all five Finding variants, coordinates, derivations and actual Store
selection without introducing future family producers.

Read-only filesystem tests cover a clean WAL database and a separately live WAL.
SQLite `mode=ro` may create operational WAL/SHM companions in a writable existing
generation. These are snapshot coordination, not new metadata authority. The
nonwritable clean-database path uses immutable reads only when both database and
directory are nonwritable and no WAL exists; it never ignores an existing WAL.
Logical rows, timestamps, activation and pending recovery obligations stay intact.

Full inspection reads raw selected metadata so ordinary Artifact-open failure
does not prevent independent axes. Receipt ownership is checked before any
external read. The shared deadline covers SQLite selection, complete Finding
audit and a killable storage reader. Each declared primary/part is streamed with
8 MiB batches, without the collection row or total-memory cap. Incomplete checks
remain unknown/unverifiable, and safe structured categories preserve all issues.
An independent review supplied regressions for foreign receipts, malformed
producer timestamps, exception-chain redaction and Evidence-phase expiration.

The approved equal-argument connection test uses real independent DuckDB
connections with conflicting `orders` rows (11 and 29). Each source branch and
its registered unary Metric continuation returns its own value; a source-required
cross-owner combination is rejected before admission. The existing parent design
originally named a single multi-input local consumer, while all registered
current Metric continuations are unary. The owner approved assigning that
consumer and its combined-budget acceptance to Slice 5a; unary proof is not
represented as multi-input acceptance.

## Reproduction

The isolated service is container `marivo-slice4d-minio`, label `marivo.slice=4d`,
at `http://127.0.0.1:32768`, using
`minio/minio:RELEASE.2025-09-07T16-13-09Z`, image digest
`sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
Tests create separate disposable versioned buckets through the existing fixture.
No source credentials enter the retained records.

Keep pytest projects outside the checkout so upward project discovery cannot
select an existing user project configuration:

```sh
marivo_test_root="$(mktemp -d)"
MARIVO_TEST_S3_ENDPOINT=http://127.0.0.1:32768 \
MARIVO_SLICE4D_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4d/reads" \
MARIVO_SLICE4C_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4d/reliability" \
MARIVO_SLICE4B_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4d/adapters" \
MARIVO_SLICE3B_EVIDENCE_DIR="$PWD/docs/superpowers/plans/evidence/slice-4d/retained" \
PYTEST_ADDOPTS="--basetemp=$marivo_test_root" \
make check-agent
```

The local `evidence/slice-4d/run_gate.py` wrapper records the complete log and
source manifests before and after this command. `index_evidence.py` copies the
fresh 4a record from pytest storage, validates every embedded candidate manifest
and emission time, and hashes each selected record. Failed attempts remain
separately under `attempt-1/` and `attempt-2/` and are excluded from acceptance.

The first attempt exposed checkout-local pytest project discovery. The second
identified two integration regressions: a crash worker serialized typed
`AnalysisRepair` directly, and the new storage-access error category accidentally
blocked deferred terminal object cleanup. The worker now uses the owning Run
input/failure codecs, and cleanup preserves the existing distinction between
metadata corruption, unresolved execution and unavailable access to terminal
garbage. Their existing real backend regression cases pass after repair.

## Approved parent-gate allocation: 2026-09-08

The owner explicitly approved moving multi-input consumption and combined-budget
acceptance to Slice 5a. Independent source-domain identity, real branch execution,
unary local continuations and their retained-part budgets remain in Slice 4.
Slice 5a's first registered multi-input consumer owns these integrated obligations:

- explicit roles for both independent operands in one local execution;
- complete validation and the combined collection budget across both operands;
- one real local comparison consuming conflicting equal-argument DuckDB branches;
- source-required cross-domain rejection and no return to sources after the
  composed local frontier.

The parent Slice 4 owned-implementation/required-evidence bullets and the
source-prefix/local-suffix acceptance matrix now reflect this allocation.
Slice 5a's outcome, implementation, evidence and matrix row explicitly retain
the multi-input proof, including individually admissible operands whose combined
size exceeds the bound and invalid later operands that must prevent invocation.
Unary tests do not discharge those future obligations.

This follow-up changes owning documentation and acceptance assignments only.
The 742-file source candidate, full-gate/log hashes and all 67 indexed Runtime
record hashes were reverified unchanged. The existing complete gate therefore
closes Slice 4d and parent Slice 4 under the approved scope without another
source change or test run.

## Final private acceptance: 2026-09-08

The final unchanged **742-file** source/test/configuration candidate is
`582e89c929afafd311e988a639841c8cce82d35afe211c2b88bec74148be3673`.
The full gate ran from **10:07:37 to 10:18:29 UTC**. `make check-agent` passed
lint/import contracts, typing for **379 source files**, **6,576 tests in 636.12
seconds**, and API documentation construction. There were no S3 skips. All
**41 touched Python files** also passed explicit-package-bases typing, lint and
formatting checks before the final gate.

The retained **67 fresh Runtime records** and **90 embedded candidate manifests**
match that candidate. Every indexed record was emitted during the final gate;
all record hashes, outer source manifests and the full check log are retained.
Credential and diagnostic canaries are absent from the selected Runtime records.

| Evidence | Records | Proven boundary |
| --- | ---: | --- |
| Guarded local source-offline journey (4a) | 1 | Three processes, complete local continuations and exact cold binding |
| Engine/object adapter journeys (4b) | 2 | Fixed immutable receipts and source-free cold reads |
| Retained-state continuation journeys (3b) | 2 | Local/engine sufficient-state continuation and cold recovery |
| Contention and creation races (4c) | 22 | Same-Session guards, cross-Session SQL overlap and canonical creation |
| Adapter crash and remote uncertainty (4c) | 27 | Exact resources, resolved terminal outcomes and unknown-outcome blocking |
| Worker process loss and orphan proof (4c) | 4 | Actual process-lifetime authority and Session-local recovery |
| Parameterized binding recovery (4c) | 3 | All targets, cold no-op identity/count preservation |
| Composed Dataset/read journeys (4d) | 3 | Local/engine/object producer, terminal failure, continuation, cross-Session consumption and third-process read/reuse |
| Parent numeric/source boundaries | 3 | Independent equal-argument real connections, exact Arrow widening and atomic overflow failure |

The composed journeys use separate producer, continuation and cold-reader PIDs.
The origin is offline for retained continuation and cold reads. Final state has
four terminal Runs, three Artifacts and Evidence envelopes, three input edges,
zero Findings and zero cleanup obligations. Binding recovery preserves exact
rows, identities, Evidence digests, graph and Store counts while forbidding
compilation, workers, writes and object requests. Foreign reads add no consumer
graph node until the local Session actually consumes the Artifact; the boundary
keeps its original owner and never expands the external producing Run.

Independent nonzero-Finding tests cover the five accepted values, coordinates,
derivation and canonical identity through real SQLite selections. Pagination
traverses 105 items at limits 1/20/100 without duplicates, and full audit streams
a Finding set above 1 MiB. These test-only registrations do not authorize a
production family or extractor ahead of Slices 5-7.

Runtime versions were Python **3.12.13**, DuckDB **1.5.3**, Ibis **12.0.0**,
PyArrow **25.0.1**, and pandas **2.3.3**, with the pinned MinIO service above.
Evidence links: [final gate](evidence/slice-4d/gate.json),
[full log](evidence/slice-4d/check.log),
[record hash index](evidence/slice-4d/evidence-index.json),
[before manifest](evidence/slice-4d/source-before.json), and
[after manifest](evidence/slice-4d/source-after.json).

Slice 4d and parent Slice 4 are complete under the approved allocation above.
Multi-input consumption and combined-budget proof remain mandatory at Slice 5a.
This is private library Runtime, real service and real process acceptance.
Public facade/Help/site activation remains Slice 8 and final public real-Agent
acceptance remains Slice 9. No commit, push or release was performed.

The owned MinIO container and its anonymous data volume were removed after
verification. No other containers were running, and Colima, started for this
run, was stopped successfully. The [service cleanup record](evidence/slice-4d/service-cleanup.json)
retains the exact owned identities.

## Review disposition: 2026-09-08

The owner requested assessment and adoption of reasonable review suggestions.
This follow-up preserves the accepted private boundary and the approved Slice
5a allocation. Its source changes require a fresh composed gate; the original
acceptance evidence above remains an immutable record of its earlier candidate.

| Suggestion | Disposition and evidence |
| --- | --- |
| Public docstring contract | These Runtime and Store helpers remain private and are absent from public exports and Help. Full public docstrings and disclosure alignment belong to Slice 8, together with facade activation; Slice 9 owns final public Agent acceptance. No public surface is introduced here. |
| Bare read-argument `ValueError` | Adopted. Limit, cursor and status validation use the existing structured `SessionStateError` contract with expected/received/repair. Malformed cursor bodies and parser failures do not enter diagnostic chains. |
| Missing acceptance record and final gate | Stale against the reviewed worktree: the accepted 6,576-test gate, 67 Runtime records and manifest/hash index are retained above. This follow-up additionally receives its own final candidate and evidence bundle below. |
| Failed Run with an output ref accepted | Disproved through a real corrupted Store with SQLite checks disabled. `SessionStore._run` rejects contradictory failed terminals before either Store or private read projection. A persisted-record regression pins this boundary. |
| Required issue severity changes persisted format | Confirmed and documented in the owning runtime design. The exact private codec rejects old synthetic nonempty issue bodies without severity. The 4c producer emitted only empty issue tuples; zero Findings alone would not establish this fact. No public compatibility or migration contract exists for this pre-cutover format. |
| Writer binding failures use read status | Adopted. The canonical object selector again reports `storage_selection` with writer repair. The read boundary translates only that selection failure to safe `unauthorized`. Writer admission remains unchanged: a failed admitted Run with no partial publication and no source work. |
| Storage summary priority | Existing results were correct. The descending owner policy now has a named tuple and chooses its first matching status; all issues remain independently retained. |
| Shared deadline | Existing behavior is correct: one inspection deadline covers every part. Exhausted later checks remain unknown, rather than receiving a new per-part deadline. |
| Production assertion in history | Adopted. An impossible selected-session lookup now raises the typed metadata invariant error. |
| Adapter exception classifier duplication | No shared classifier added. Local filesystem/Arrow and SDK responses expose different structured facts; each adapter translates its native boundary without parsing raw text. A general classifier would obscure those boundaries. |
| Finding method prefix and Store ownership | No abstraction added for two short selectors. Dataset ownership belongs to the Runtime action port, while the Store owns persistence selection; both methods preserve one snapshot. |
| Recap and Graph count SQL | Kept operation-scoped. Their dependency closures and traversal budgets differ; combining selectors solely to deduplicate count expressions is not required for correctness. |
| Storage status switch | Kept explicit literal branches so worker output is validated and narrowed to the closed status type without casts. |
| `RunRecord` alias and local `_text` names | Kept module-local distinctions between persisted Run envelopes and closed read variants, and between SQL field access and value validation. No observable ambiguity was found. |
| Coordinate special case in `_Value` | Behavior is correct. Moving validation solely to remove a base-class reference is optional cleanup and is excluded from this bounded fix. |
| Parallel eager and lazy type sets | Intentional staged cutover: the new implementation is private, and current public imports retain the old owner. Slice 8 explicitly owns rebinding and removal of the old set; this does not authorize a permanent second public path. |
| Multi-input parent gate | Already resolved by the owner's explicit approval: complete multi-input consumption and combined-budget proof remain mandatory at Slice 5a. |

The Finding cursor audit also covers bounded, deeply nested JSON that exhausts
the parser's recursion depth, using the same safe structured argument failure.
Regression tests use real persisted records and current production admission
where the claim concerns those boundaries.

During this review, a separate worktree change split daily and Runtime tests
into distinct Make targets and moved the Help environment tests to the release
group. Those changes are preserved. The follow-up wrapper runs the current full
`make check-agent` (both daily and Runtime groups), then explicitly runs the
three Help environment tests so the earlier gate's coverage is retained. Each
daily/Runtime invocation uses the same external temporary project root, reset
between groups; emitted Runtime evidence is retained outside that root. The
Help supplement uses a separate external root. The canonical source manifest
retains its existing definition; supplemental hashes bind
`pytest.ini` and the environment entrypoint before and after the gate as well.

The 12 review-related Python files pass explicit-package-bases typing, lint and
formatting. An exploratory expansion to all 66 modified/new Python files passes
lint/format but reports 32 typing diagnostics in four older test/helper modules
outside the normal source-plus-typing-test gate. Each diagnostic maps to an
unchanged line and unchanged relevant type contract at `22284a02`; this is a
static comparison, not a rerun of baseline mypy. No unrelated test typing cleanup
is included, and the expanded check is not represented as green. Its complete
[exploratory log](evidence/slice-4d-review/scoped-checks.log) and the successful
review-scoped checks are retained separately in the follow-up evidence directory.

The first follow-up attempt is retained under `evidence/slice-4d-review/attempt-1/`
and is excluded from acceptance. Its daily group passed 6,303 tests; the Runtime
group stopped with six candidate-fingerprint failures while another task changed
test routing and fixtures in the same checkout. All six failures were the
unchanged-source guard, and the outer before/after manifests also differed.
The three supplemental Help environment tests passed. A stable complete rerun
is required; this attempted gate does not close the review follow-up.

The second attempt, under `attempt-2/`, passed every check: 5,884 daily tests,
721 Runtime tests, and the three supplemental Help environment tests, without
S3 skips. It remains excluded because the other task changed only the
`pytest-xdist` dependency floor in `pyproject.toml` during that gate, changing
the required candidate fingerprint. The final rerun starts after that task
has actually completed; neither mixed candidate is reused as final evidence.

## Final review acceptance: 2026-09-08

The review follow-up is complete on the unchanged **745-file** candidate
`6f154184ace46a2f7f02a4888693c04cc28b1fae34d709a28b09931f1ceb2361`.
This supersedes the initial 742-file candidate for the current reviewed source.
The final gate ran from **11:04:55 to 11:10:39 UTC** after the parallel
performance task completed. Source manifests and supplemental test-configuration
hashes match before and after execution.

`make check-agent` passed lint/import contracts, typing for **379 source files**,
**5,884 daily tests in 51.00 seconds**, **721 Runtime tests in 263.47 seconds**,
and API documentation construction. The supplemental Help environment group
passed **3 tests in 13.43 seconds**. All **6,608 executed tests** passed, with
zero skips, including zero S3 skips. The **12 review-related Python files** also
pass their recorded typing/lint/format checks on this candidate.

The independently collected test inventory proves that the daily and Runtime
groups form the exact **6,605-node non-release set**, with no missing, extra or
overlapping nodes. All **1,123 lazy tests in 67 modules**, including required
3b, 4a-4d and parent Slice 4 acceptance selectors, remain covered. This collection
record is explicitly bound to the final gate and its test configuration.

All **67 fresh Runtime records** and **90 embedded candidate manifests** match
the final candidate. The composed local/engine/object journeys, 4c recovery and
unknown-outcome cases, 3b retained continuations, and real source/numeric parent
boundaries were regenerated in this gate. Every selected record was emitted
within the final gate interval and has a retained SHA-256 entry; previous
attempt records are excluded. Diagnostic and credential canaries are absent
from the selected Runtime records.

A separate read-only audit recomputed the current source fingerprint, all
record and supporting-log hashes, emission intervals and embedded candidates.
Its independent pytest collection reproduced all four node-set hashes and the
18 required acceptance-module selections. No mismatches were found; the live
MinIO container, image digest and endpoint also matched before cleanup.

The isolated service is `marivo-slice4d-review-minio`, label
`marivo.slice=4d-review`, using the same pinned MinIO release and image digest
as the initial acceptance. Its exact container identity and endpoint are
recorded in the gate. Reproduction uses the command above with all evidence
paths changed to a new review directory, followed by
`.venv/bin/pytest -m release tests/test_analysis_help_environment.py` with a
separate external pytest root. The retained `run_gate.py` and
`index_evidence.py` record this run's exact service identity and verification;
update the wrapper's service metadata to the newly inspected identity when
reproducing with a newly created container. Never reuse prior Runtime records.

Evidence: [final gate](evidence/slice-4d-review/gate.json),
[full check log](evidence/slice-4d-review/check.log),
[supplemental log](evidence/slice-4d-review/help-environment.log),
[record hash index](evidence/slice-4d-review/evidence-index.json),
[test selection proof](evidence/slice-4d-review/test-selection.json),
[review checks](evidence/slice-4d-review/review-checks.json),
[before manifest](evidence/slice-4d-review/source-before.json), and
[after manifest](evidence/slice-4d-review/source-after.json).

The accepted review fixes preserve the completed private Slice 4d and parent
Slice 4 gate. Multi-input consumption and combined-budget acceptance remain
mandatory at Slice 5a. Public facade/disclosure cutover remains Slice 8, and
public real-Agent acceptance remains Slice 9. No commit, push or release was
performed. After verification, the owned review MinIO container and anonymous
volume were removed. No other containers were running, and the Colima instance
started for this review was stopped; exact identities are retained in the
[cleanup record](evidence/slice-4d-review/service-cleanup.json).
