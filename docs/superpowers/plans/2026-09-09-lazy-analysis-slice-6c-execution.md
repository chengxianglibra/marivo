# Slice 6c: private time discovery and Candidate datasets

Status: complete, including the authorized review follow-up. Private Slice 6c only.

## Baseline and prerequisites

The owner approved the complete implementation plan and the Candidate endpoint
and reason-code amendment. Implementation starts from clean `lazy-dataset` HEAD
`16a56d19898be9fa3424607743f97ef6a88f6262`, which includes the previously
uncommitted Slice 6b implementation. Accepted Slices 3b, 4d and 5a supply the
retained-input, Runtime and period-comparison prerequisites.

## Frozen scope and ownership

Operators owns `operators/candidate_contracts.py`, `candidate_dataset.py`,
`discovery.py` and `candidate_values.py`: three closed Candidate shapes, pure
construction, state pairs, selectors, ordering, identity and independent scorers.
Runtime owns `materialization/candidate_codec.py` and `candidate_publication.py`:
bounded original evaluation facts, current selection counts and zero Findings.
These paths are relative to `marivo/analysis/`.

The integration owner alone extends existing shared seams: Dataset registration;
Observation contracts, Metric methods and predicates; Delta methods; operator
errors, registration and row execution; compiler normalization and placement;
materialization admission, contracts, storage, recovery, local execution and
worker IPC. Existing Forecast behavior and files are preserved.

Focused tests are owned by `tests/test_lazy_candidate_contracts.py`,
`test_lazy_candidate_numeric.py`, `test_lazy_candidate_publication.py`,
`test_lazy_candidate_runtime.py`, `test_lazy_candidate_failures.py`,
`test_lazy_candidate_object.py`, `test_lazy_candidate_runtime_acceptance.py`,
`test_lazy_candidate_worker.py`, `lazy_candidate_fixtures.py` and
`lazy_candidate_runtime_worker.py`. Necessary
shared no-I/O, import and runtime routing fixtures are integration-owned.

## Interface and numerical contract

Metric time/dimension-time supports `discover.point_anomalies` (threshold 3)
and `discover.interesting_windows` (threshold 2). Delta time/dimension-time
supports `discover.period_shifts` (threshold 2). Each requires one Metric,
accepts Logical or retained Materialized input, and returns Logical Candidate.
Discovery limit defaults to 50 and is an integer in [1, 1000].

Common fields are item_id, score and structured reason_codes, followed by the
objective's exact owning schema. Keys are business coordinates; selection is
score descending, typed key ascending and item_id ascending. Complete candidate
key and digest uniqueness is checked before limit. Item ids bind the exact
input authority, objective, version, normalized parameters and typed key.

Direction is high/low. The fixed one-element reason tuples contain respectively
point_zscore_threshold_met, global_zscore_run and delta_window_zscore_run.
Deviation is observed minus baseline. Standard deviations are population SD.
Window scores are peak absolute z and direction follows the earliest peak on
exact ties. All endpoints are inclusive coordinate labels. Interesting-window
baseline bounds span the non-null fit support. Period-shift run endpoints use
the current and paired baseline times at the first and last qualifying trailing
window end ordinals; its global scoring baseline is separately disclosed.

At least one evaluable series with no hits produces valid empty authority.
No evaluable series fails the action. Candidates are descriptive leads and
always publish zero Findings, including after selection or rank.

## Execution and persistence

Each scorer has one exact pandas registration after the existing supported
Ibis source prefix. Complete input, method, memory, output and deadline guards
apply before scoring. Local successors receive private DataFrames directly.
No fallback, source replay, imputation, implicit sampling or engine upload is
introduced. Logical, engine, local and version-pinned object inputs use their
existing admitted authority. Output uses local Parquet or native object storage.

Publication retains original input/objective/evaluation authority and current
row counts with Artifact, Evidence, zero Findings and Run success atomically.
Cold decoding independently validates the closed contracts and summaries.
No Store migration or public activation is performed.

## Acceptance matrix

| Boundary | Evidence owner |
| --- | --- |
| Pure construction, shapes, schema, identity, namespace, selectors and local frontier | candidate contracts and shared no-I/O tests |
| Independent point, run and complete trailing-window references | candidate numeric tests |
| Closed Evidence, zero Findings, corrupted metadata and original/current counts | candidate publication tests |
| Logical/retained inputs, direct local successors, calendars, sampling and empty selection | candidate runtime tests |
| Budget, cancellation, source and publication failures | candidate failure tests |
| Native SDK versioned object receipt and offline continuation | candidate object tests |
| Producer, offline continuation and cold binding in separate processes, all three methods | candidate runtime acceptance and worker |

Final gates are touched-module typing/lint, independent numerical and contract
tests, `make check-agent`, and focused `make runtime-test` including Forecast,
comparison and retained-row regression owners. Runtime uses existing bounded
worker concurrency. Object testing uses native SDK stubs and real Store files;
no MinIO service or full release gate is required. Evidence under
`evidence/slice-6c/` must bind every final gate to one unchanged executable
fingerprint. Only then update the cutover acceptance row.

## Deferred work

Entity-outlier belongs to Slice 6d; driver-axis to 6e; public exports, Help,
current site documentation and eager removal to Slice 8. No commit, push,
release or parent Slice 6 closeout is authorized by this unit.

## Implementation and review decisions

- The namespace registration records its concrete immutable namespace type and
  checks the property and operation on both paired states. No callable alias or
  new public export is introduced.
- Candidate row semantics own interpretation. Original definition and evaluation
  facts live in Evidence; selection updates only the current output count.
  Cold owners receive the persisted original definition for their contract card.
- Period-shift publication reuses the two-operand Delta descriptor authority
  before projecting it into a Candidate descriptor. Materialized Delta inputs
  retain their already committed comparison and approximation facts.
- Empty retained Parquet input now passes its exact empty schema into the local
  collector. The existing collector requires a schema even with zero batches;
  the source-offline empty Candidate continuation exposed this shared defect.
- Independent review identified fractional constant-series roundoff as a source
  of false discoveries. Exact constant values are classified before population
  moments; all three objectives and a mixed panel have regression coverage.
- Independent review also required typed rejection of unrepresentably large
  metadata numbers and preservation of the original maximum score when a
  producer output is truncated. These checks apply without changing retained
  selection semantics.
- The first broad default gate exposed a pre-6c comparison assertion that Delta
  lacked `discover`. Its replacement checks the non-callable namespace, admits
  only the two temporal shapes and rejects the remaining shapes. The executable
  fingerprint was refreshed and every final gate was rerun.

## Exact file scope

All paths are relative to the repository root. The executable manifest covers
all source and test Python files, not only these changed paths.

- `.gitignore`
- `docs/superpowers/plans/2026-09-09-lazy-analysis-slice-6c-execution.md`
- `docs/superpowers/specs/2026-09-01-lazy-analysis-public-cutover-plan.md`
- `docs/superpowers/specs/2026-09-01-lazy-analysis-typed-operators-design.md`
- `marivo/analysis/compiler/normalize.py`
- `marivo/analysis/compiler/placement.py`
- `marivo/analysis/datasets/registry.py`
- `marivo/analysis/materialization/admission.py`
- `marivo/analysis/materialization/candidate_codec.py`
- `marivo/analysis/materialization/candidate_publication.py`
- `marivo/analysis/materialization/contracts.py`
- `marivo/analysis/materialization/local.py`
- `marivo/analysis/materialization/local_worker.py`
- `marivo/analysis/materialization/publication.py`
- `marivo/analysis/materialization/recovery.py`
- `marivo/analysis/materialization/storage.py`
- `marivo/analysis/observation/contracts.py`
- `marivo/analysis/observation/metric.py`
- `marivo/analysis/observation/ordering.py`
- `marivo/analysis/observation/predicates.py`
- `marivo/analysis/operators/candidate_contracts.py`
- `marivo/analysis/operators/candidate_dataset.py`
- `marivo/analysis/operators/candidate_values.py`
- `marivo/analysis/operators/compare.py`
- `marivo/analysis/operators/delta.py`
- `marivo/analysis/operators/discovery.py`
- `marivo/analysis/operators/errors.py`
- `marivo/analysis/operators/registry.py`
- `marivo/analysis/operators/row.py`
- `tests/lazy_candidate_fixtures.py`
- `tests/lazy_candidate_runtime_worker.py`
- `tests/lazy_observation_fixtures.py`
- `tests/test_lazy_candidate_contracts.py`
- `tests/test_lazy_candidate_failures.py`
- `tests/test_lazy_candidate_numeric.py`
- `tests/test_lazy_candidate_object.py`
- `tests/test_lazy_candidate_publication.py`
- `tests/test_lazy_candidate_runtime.py`
- `tests/test_lazy_candidate_runtime_acceptance.py`
- `tests/test_lazy_candidate_worker.py`
- `tests/test_lazy_compare_contracts.py`
- `tests/test_lazy_observation_runtime_no_io.py`
- `tests/test_lazy_source_construction_no_io.py`

## Initial accepted candidate and gates

The initially accepted executable candidate covers 877 files and has SHA-256
`ff1a7069f3c98f6db113bf490d03c39eeaf9c664a08637e8ee960c529521121e`.
The manifest hashes every `marivo/**/*.py` and `tests/**/*.py` file plus
`pyproject.toml`, `Makefile` and `.importlinter`. Documentation and retained
evidence are outside that executable manifest.

`evidence/slice-6c/final/` owns the final candidate manifest, exact commands,
complete logs, exit codes, timings, before/after fingerprints, read-only review
dispositions and three-process recovery records. The initial broad attempt is
retained separately under `evidence/slice-6c/attempt-1/`; its obsolete comparison
test assertion is repaired and it is not terminal acceptance evidence.

| Gate | Final result |
| --- | --- |
| Focused Candidate, shared no-I/O, registry and comparison contracts | 210 passed |
| Strict Python 3.10 typing for changed test modules and shared helpers | 14 modules passed |
| `make check-agent` | 6,560 default tests passed; lint, import contracts, production typing and API documentation passed |
| Focused Runtime with Forecast, comparison and retained-row regressions | 126 passed |

The Runtime command selects `test_lazy_candidate_runtime.py`,
`test_lazy_candidate_failures.py`, `test_lazy_candidate_object.py`,
`test_lazy_candidate_runtime_acceptance.py`, `test_lazy_candidate_worker.py`,
`test_lazy_forecast_runtime.py`, `test_lazy_retained_runtime.py`,
`test_lazy_compare_runtime.py` and `test_lazy_compare_time_runtime.py` from
`tests/`, under the standard two-worker Runtime target.

Every terminal gate passed on the same unchanged executable candidate. All
three separate producer, source-offline continuation and cold-binding journeys
preserved exact rows, artifact identity, original search Evidence and zero
Findings. Cold execution reused the exact prior binding while compiler,
backend-construction and worker-entry seams were forbidden; the committed
Store snapshot remained unchanged. Structured reason tuples survived each
guarded read. Native SDK stubs and real SessionStore files also passed object
publication, retained object input and version-pinned offline continuation.

`terminal.json` seals these gate and recovery records. `changed-files.json`
records the exact final 43-file scope, and `candidate.patch` retains the complete
tracked and new-file diff without staging or committing it. Slice 6b remains
preserved at the baseline HEAD. No public activation, MinIO service, release
gate, commit, push or release was performed.

## Authorized review follow-up

The owner requested assessment and adoption of reasonable review suggestions.
This follow-up preserves the initial acceptance evidence and its entire private
scope. The disposition of each suggestion is:

| Suggestion | Disposition and bounded reason |
| --- | --- |
| 1. Duplicate paired Dataset and discovery namespace methods | Retained. Execution already delegates to shared helpers; explicit signatures, return-state types and docstrings describe the distinct contracts. A mixin or generated surface adds complexity without removing duplicated algorithms. |
| 2. Duplicate interpretation guidance | Adopted. The Candidate card renders the owning discovery contract facts, sharing interpretation, parameters and scope with `contract()`. |
| 3. Evaluation dataclass repr in the card | Adopted. The card selects bounded input, evaluated-series/unit and candidate counts. Original qualifying/discovery output counts remain distinct from current output rows. Full structured Evidence remains unchanged. |
| 4. Repeated logical-type branches | Retained. Arrow, scalar validation, DataFrame reads and the stable type registry have distinct boundary responsibilities. A generic codec framework is unnecessary for one closed tuple type. |
| 5. Independently optional definition and evaluation summaries | Adopted. One immutable `CandidateSearchSummary` moves the definition and evaluation together through the worker and publication handoff; Evidence serialization is unchanged. |
| 6. Redundant exact-type and isinstance condition | Adopted. Exact builtin int/float checks provide both runtime exclusion of booleans/subclasses and static narrowing without a redundant condition. |
| 7. Annotation-string namespace registration check | Deferred. This is a limitation for future annotation forms, but the admitted namespaces use the checked closed form and paired-state tests pass. No current failure warrants weakening the existing registration checks or evaluating forward annotations during pure registration. |
| Delta discovery docstring refers to Metric input | Adopted. Both Delta states now require a time-bearing Delta of one Metric. The Metric receiver wording remains correct. |
| Weak mixed-sign exact-peak tie proof | Adopted. Four explicit mirrored cases cover both window objectives with hand-computed scores, literal direction/endpoints and reversed physical row order. The expected values do not reuse the production peak-selection idiom. |
| Allegedly dead range-length check | Retained. Worker dataclasses can reach publication without JSON decoding. Direct malformed one- and three-element ranges are rejected with a typed integrity error by this guard. |

Focused card Runtime checks and the expanded numerical references passed during
iteration. Final follow-up acceptance reran the broad default gate, strict test
typing, the original 126-case focused Runtime selection and all three independent
recovery journeys on one new unchanged executable manifest.

The follow-up executable candidate covers the same 877 files and has SHA-256
`cc1cd9d7a558e7be7d867e515b78d028f6884fa68fa3b7c468943801c6420a6f`.
All updated gate records are retained separately under
`evidence/slice-6c/review-followup/`; initial acceptance artifacts are preserved.
An independent read-only review found no remaining issues in the changes and
confirmed the same executable fingerprint before and after inspection.

| Follow-up gate | Result |
| --- | --- |
| Focused Candidate, no-I/O, registry and comparison tests | 214 passed |
| Strict Python 3.10 test typing | 14 modules passed |
| `make check-agent` | 6,564 default tests and all lint/import/type/API-documentation stages passed |
| Original focused Runtime selection and three independent recovery journeys | 126 passed; all three recovery records retained |

Every follow-up gate finished with exit code zero and matching before/after
fingerprints. `review-followup/terminal.json` seals the superseding acceptance;
the adjacent changed-file manifest and complete patch still cover the original
43-file Slice 6c scope. `followup-files.json` identifies the eight Python files
and two documentation files changed since initial acceptance. The follow-up
preserves all three scoring algorithms, persisted Evidence schemas, original
search authority and private-only scope. No commit, push or release occurred.
