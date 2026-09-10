# Slice 7a: private Event matching and journey authority

Status: private Slice 7a implementation and acceptance complete on 2026-09-10.
The public cutover and later Slice 7 units remain independently gated.

## Baseline and accepted scope

The owner approved the implementation plan, native DuckDB execution, homogeneous
occurrence identity signatures, and derived canonical ordering from retained
journey rows. Work starts from clean `lazy-dataset` HEAD `26bb83cc`, containing
the committed Slice 6e work. The cutover plan records accepted 3b and 4d
prerequisites. Existing 6d membership is consumed through its current contract.

This slice owns private Event source construction, shared subject admission,
three matching policies, completeness, eight-column dense journey output,
atomic publication and cold recovery. Reducers/selection, Lifecycle and public
activation remain later slices. No commit, push or release is requested.

## Ownership and implementation order

1. Domain owner: new `marivo/analysis/domains/` modules for subject admission,
   Event construction/normalization, row semantics, completeness and structured
   errors; the extracted admission in `observation/metric.py`; domain tests.
2. Matching owner: `compiler/event.py`, native matching and scalar proofs,
   independent numerical/reference tests. Matching does not collect source
   identities into Python.
3. Persistence owner: `materialization/event_codec.py`, `event_publication.py`,
   Event descriptor/codec/Evidence integration in `materialization/contracts.py`,
   canonical ordering/storage integration and focused persistence tests.
4. Integration owner: `session/_lazy_sources.py`, `observation/contracts.py`,
   `operators/registry.py`, compiler nodes/normalization/placement/lowering,
   `compiler/event_sources.py`, `compiler/event_time.py`, materialization
   admission/publication, shared fixture ports, Runtime tests,
   owning designs, this record and cutover acceptance row.

Parallel owners preserve one another's work and coordinate shared interfaces
before changing shared files. Domain contracts land before dependent assembly.

## Frozen interfaces and execution

The private source signature is `events.match(pattern, *, cohort_window,
completion_through, matching, population=None, completeness=())`. Construction
is source-free. Subject K is complete and ordered; version coordinates remain
separate. Versioned subjects require explicit admitted membership. Occurrence
participant representations are resolved at each Event instant.

All Pattern Event identities have equal arity and ordered logical types;
component names may differ. The canonical eight columns remain unchanged.
Journey ordering derives anchor keys from the initial step and uses retained
Pattern order. Journey owns no domain retained parts and publishes zero Findings.
Inherited sampling keeps the existing common `population_sampling_state` receipt.

Matching is source-required on native DuckDB. Typed Ibis relations implement
earliest successors and exclusive final assignment. Engine and local Parquet
are the bounded storage acceptance targets. Unsupported membership/source domains
fail before data work; no raw identity upload or pandas matching is admitted.

Coverage uses exact bounded/source-origin declarations or a typed private
provider receipt. Missing coverage remains unknown. The cohort is `[start,end)`;
follow-up occurrences are strictly before `completion_through`. Catalog binding
occurs at source construction, not in standalone declaration value constructors.
When a declaration supplements an insufficient observed watermark, the original
watermark is retained separately and the decisive coverage basis remains declared.

The admitted temporal representation is a native date/timestamp with at most
microsecond precision. A physically aware timestamp preserves its instant;
naive columns require explicit UTC-equivalent metadata. Unsupported string/custom
parsing or non-UTC naive wall clocks fail through the typed compiler contract.
Snapshot/validity source and participant versions must represent each occurrence
at its exact instant, independently of the membership selection scope.
Subject identity consumes the existing core type vocabulary; it does not expand
the core stable-ID grammar to new parameterized type tokens.
Private Event construction enforces the existing retained metadata bound of
4096 UTF-8 bytes per Pattern, matching, and completeness JSON field. Boundary
tests admit 4096 and reject 4097 before source access, with a concrete repair.

## Implementation checks and repaired boundaries

- Independent occurrence-list references cover all three matching policies;
  source queries use native relations, window ranks and ASOF successor joins.
  Missing predecessors now explicitly propagate through later steps.
- Self-subject versioned Events now validate their own source version at the
  occurrence instant, including empty participant paths.
- Native timestamp schema validation recognizes DuckDB's default microsecond
  precision when the semantic declaration leaves precision unspecified.
- Composite Event occurrence tuples retain decimal precision; existing composite
  subject tuples remain recoverable without a current catalog.
- Coverage metadata preserves insufficient observed watermarks supplemented by
  declarations. Cold validation rejects inconsistent authority and density.
- Family validation pins the complete eight-column schema, unknown keyed row
  bound, derived anchor ordering and exact Pattern ordering.
- Independent read-only review found two cold authority gaps: a complete
  declaration could coexist with unknown/insufficient effective coverage, and
  unused coverage binding fields could carry non-digest text. Both are now
  rejected, with positive complete observed authority retained.

## Independent acceptance

- Source-free construction, exact identity/Session/shape/sampling admission,
  homogeneous occurrence signatures and occurrence-time temporal resolution.
- Differential all-policy matching, repeated Events, non-final sharing,
  exclusive finals, single-step Patterns, missing propagation, simultaneous
  ambiguity and all temporal/coverage boundaries.
- Source-side density/assignment/status validation, safe scalar Evidence,
  canary scans, storage/commit/cancellation failure atomicity and corruption.
- Separate-process production, origin-offline recovery and exact binding reuse;
  engine membership reuse after its origin source is removed.
- Focused tests and touched-module typing, `make check-agent`, then focused
  `make runtime-test` with bounded workers. No full release gate or object service.

Fresh evidence is recorded under `evidence/slice-7a/` with an unchanged executable
candidate fingerprint, commands, logs and terminal records. Only successful gates
close cutover matrix row Event matching/completeness and journey recovery (7a).

## Initial accepted candidate and gate results

The accepted 928-file executable candidate is
`7b1b600d406fbf90a6177d57d634e3324df7ab2a58c96fab815d1e1cdcafabee`.
It includes all `marivo/**/*.py`, `tests/**/*.py`, `pyproject.toml`, `Makefile`,
and `.importlinter`. `candidate.json` records each file's SHA-256; `gates.json`
records commands, log hashes, exit codes, elapsed time, and an unchanged candidate
check before and after every gate. Documentation-only acceptance updates do not
change that executable fingerprint.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Event contracts, compiler/reference matching, persistence and time normalization | 144 passed | 13.017 s |
| `make check-agent` | 6,892 default tests; format/lint, import contracts, 449-file typing and API docs passed | 96.056 s |
| Event Runtime: matching/failure/privacy, identity membership, temporal versions, provider coverage, three-process recovery | 54 passed | 42.314 s |
| Shared Runtime: entity-Candidate membership, retained membership, sampling and materialization | 80 passed | 55.989 s |
| Explicit new-test typing with package bases | 13 files passed | 0.591 s |

Exact selectors are retained in `gates.json` and the replayable
`verify_candidate.py`. The focused source/reference suite covers all three
native DuckDB matching policies. Local Parquet and engine receipts are exercised
where storage, identity placement and recovery boundaries differ; the suite does
not multiply every numeric case across sinks.

Independent read-only source/domain review reported no additional findings.
The persistence review's two findings are fixed and its seven new regressions
were independently rerun successfully. Oversized retained JSON is rejected at
source construction, preserving the existing persistence capacity boundary.

## Fresh-process and privacy evidence

`slice-7a-local-runtime.json` and `slice-7a-engine-runtime.json` each record three
distinct interpreter PIDs and the same candidate fingerprint. Production writes
selected engine Metric membership, then removes its `orders` origin. Continuation
matches Events using that retained identity relation and removes both occurrence
tables. Cold recovery reopens the exact Artifact and reconstructs the same logical
binding with placement, compilation and source-backend creation forbidden.

For both sinks, continuation and cold recovery retain identical terminal hashes,
Artifact receipts and Event Evidence. Store counts stay at two Runs, two terminals,
two Artifacts, two Evidence rows and one input edge, with no resource journal
entries. The cold phase records only reconciliation and no new source execution.
The Event Artifact has two dense rows for one incomplete journey under declared
complete coverage, zero Findings and no Event-owned retained parts.

The 5,000-subject engine case writes 10,000 journey rows with no Python matcher,
local worker, primary-row batch transfer or transferred bytes. Occurrence and
subject canaries are absent from metadata, Runs, Evidence, Findings, statement
logs and storage names. Explicit terminal reads remain the authorized row surface.
Atomic failure cases cover source validation, staged storage, quality, cancellation
and Store commit; lost commit acknowledgement recovers the original complete bundle.

Runtime versions: Python 3.12.13, DuckDB 1.5.3, Ibis 12.0.0, PyArrow 25.0.1 and
pandas 2.3.3. All Runtime gates use at most two pytest workers per invocation.
No public export, public Help, packaged workflow skill or bilingual site surface
is activated. Changes remain uncommitted on `lazy-dataset`.

## Review fix: bound coverage-provider source queries

The local review identified a source execution deadline bypass: coverage-provider
queries ran before the existing protected source preparation stages. Event coverage
resolution now runs under the same DuckDB interruption deadline, with failures
attributed to source binding. Two real-query Runtime regressions interrupt the
first or second Event coverage query, verify a failed terminal without Artifacts,
Evidence, Parquet files or resource journal entries, and retry the same logical
request successfully in the same Session.

The provider-deadline accepted 928-file executable candidate is
`f14c275168d7e054ec054281b472503fc4560e9973613e0ffb4940be65c4d259`.
The initial acceptance above remains historical. Fresh commands, candidate and
per-file hashes, log hashes and local-file/engine three-process evidence are
recorded separately under `evidence/slice-7a/review-fix/`; all five gates preserve
this new fingerprint before and after execution.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused Event default tests | 144 passed | 9.888 s |
| `make check-agent` | 6,892 default tests; format/lint, import contracts, typing and API docs passed | 102.133 s |
| Event Runtime including provider interruption and retry | 56 passed | 43.238 s |
| Shared membership/materialization Runtime | 80 passed | 54.161 s |
| Explicit Event test typing with package bases | 13 files passed | 1.182 s |

Public activation and downstream gates remain unchanged. This fix is uncommitted.

## Follow-up review disposition

The external review's initial `7b1b600d...` evidence remains valid historical
acceptance; the provider-deadline correction has its separate `review-fix/`
candidate above. Follow-up changes and rerun gates are recorded independently
under `evidence/slice-7a/review-suggestions/`.

| Suggestion | Disposition |
| --- | --- |
| Unused `event_summary_proof` | Removed the unused internal function; it was not a public export. |
| `_timezone_signature` stub | Added an explanation that Ibis consumes the signature to emit DuckDB's builtin; the Python body does not execute. `_localize_utc` is called by `event_instant`. |
| Source schema diagnostic | Corrected the expected-type text to include nullability normalization, generic decimal admission and unspecified timestamp scale accepting physical microseconds with the same timezone. |
| `LazyEvents.match` docstring | Expanded parameter, return and example sections. This remains a private facade, not a newly activated public API. |
| New journey-authority value object | Deferred: two explicit compiler handoffs do not yet justify another authority model. |
| Shared status expression and `_check` helper | Kept generation and validation separately expressed. Small module-local validation builders do not justify another shared module. |
| Repeated Event family dispatch | Retained the existing family-dispatch architecture. |
| Coverage primitives and positional construction | Converted all three `EventCoverageFact` construction branches to named arguments. Kept normalized ISO instants at the retained JSON boundary; requests and source receipts retain typed datetimes. |
| Function-based tests | Retained the current lazy-test family convention and repository fixture guidance. |
| Mid-function datetime import | Moved the standard-library import to module scope. |

### Publication and timezone trust boundaries

`DatasetRuntime` owns source binding, validation, compilation and execution. It
executes `CompiledDataset.event_proof` on the action-owned native relation,
rejects nonzero violations through `summary_from_proof`, and binds the resulting
summary during publication. No public caller supplies that proof through
`LazyEvents.match`. Compiler corruption cases independently exercise density,
identity, status, duration and assignment checks.

`build_event_publication` cross-checks an authorized row stream against an
already trusted summary. Its callers are storage tests; production binds the
native summary directly to preserve private source identity placement. The helper
is not an independent verifier of an adversarial caller supplying both forged
rows and a forged summary. Its docstring now states that boundary explicitly;
introducing a second source matcher at publication would duplicate the owning
execution contract.

`EventRowValidator` consumes an internal stream already normalized by the
compiler, accepting `UTC`, `Etc/UTC` and `+00:00` metadata. It is not the source
timezone parser. Aware source instants are normalized to UTC; supported naive
UTC declarations are handled by `event_instant`. The closed internal metadata
set is retained and explained locally, without adding more source spellings.

`tests/test_lazy_event_contracts.py` constructs through `sources.events.match`
(including `test_source_construction_is_complete_immutable_and_source_free`),
which directly calls `domains.event.make_match`. The reported missing call-graph
coverage is not an uncovered facade path. Existing public Event Help entries do
not establish activation of this private lazy facade.

### Follow-up accepted candidate

The current accepted 928-file executable candidate is
`651abecb217dcbd10dbfcef5b3bc1a39b2c6087f149614999f0f3068840f7728`.
All five fresh gates in `evidence/slice-7a/review-suggestions/gates.json` pass
with the same candidate before and after each run. `final-verification.json`
checks all 928 file hashes, five gate log hashes and both fresh-process sink
fingerprints. Earlier accepted candidates remain historical evidence.

| Gate | Result | Elapsed |
| --- | --- | --- |
| Focused Event default tests | 144 passed | 13.634 s |
| `make check-agent` | 6,892 default tests; lint, typing and API docs passed | 105.274 s |
| Event Runtime | 56 passed | 44.357 s |
| Shared membership/materialization Runtime | 80 passed | 56.739 s |
| Explicit Event test typing | 13 files passed | 1.213 s |

The follow-up is uncommitted and does not activate the public lazy surface.
