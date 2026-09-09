# Slice 6b: private Forecast execution and recovery

Status: private Slice 6b complete; all final gates passed on one unchanged candidate.

## Baseline and ownership

The owner approved the Slice 6b implementation plan. Actual implementation entry
was clean commit `ca9b22b5` on `lazy-dataset`, including the completed Slice 6a
implementation. The earlier planning snapshot at `06166dbf` was superseded by
that existing commit. `evidence/slice-6b/baseline.json` records this baseline.
Slices 3b and 4d are accepted prerequisites; Slice 6b is independent of discovery.

Operators owns closed helper values, Forecast contracts, paired states, history
validation and the three numerical models. Compiler changes admit the exact
local method after supported source work, including catalog-free checkpoints.
Materialization owns worker invocation, fit summaries, codecs, quality, Findings
and cold recovery. Shared seams add only Forecast dispatch and the finite float
family fact required for exact interval-level identity (canonical hexadecimal
encoding). Existing eager and correlation behavior is preserved.

## Frozen behavior

The private Metric method accepts an arity-one time or dimension-time shape,
helper-produced horizon in [1, 1000], a named model and finite interval level in
(0, 1). Seasonal length is explicit. Logical construction performs no data I/O.
Every panel shares one complete consecutive history. Builtin bucket arithmetic
uses the existing exact source-aligned temporal owner; custom periods use the
captured certified calendar, including future coverage. No origin lookup or
history imputation is permitted.

Naive, drift and seasonal-naive implement the accepted innovation, positive
residual degrees-of-freedom and horizon-variance equations. Constant nonzero
innovations never become zero uncertainty. Overflow, underflow that erases a
nonzero variance, and unrepresentable nonzero bounds fail. Exact zero residuals
may produce zero estimated variance, with explicit residual-fit disclosure.
Nominal prediction intervals retain their named model assumptions and make no
empirical coverage calibration claim.

Source operators remain in their tested contiguous Ibis prefix. Forecast starts
one registered pandas method after complete input and budget validation. Its
relational successors remain local and receive direct private DataFrames.
Publication is all-or-nothing, including Evidence and deterministic predicted
Findings. Original training summaries and approximation survive selections and
cold recovery. No raw history or executable origin is persisted in Forecast.

## Validation protocol

Use independent numerical references for all models, minima, calendar boundaries,
panels, nulls, missing periods, nonfinite data and zero/nonzero innovations.
Exercise logical, engine and local Metric inputs, exact local handoffs, sampling
meaning, empty selection, model/future proof corruption, guards, cancellation
and transactional publication faults. Journey J uses three separate processes
for producing Forecast, source-offline continuation and cold exact binding reuse.

Object-specific SDK/version/ownership behavior stays in its existing stub/real
Store gate. Forecast also covers successful object publication, version-pinned reads and
source-offline local continuation with native SDK stub responses, plus native
object-target denial before source/model work.
No MinIO service or full release Runtime gate is used.

Run focused daily tests and touched typing/lint, then `make check-agent` and
necessary focused Runtime cases with existing correlation/retained regressions.
Final evidence records identical before/after executable fingerprints, complete
command exits and fresh-process authority comparisons under `evidence/slice-6b/`.
Only after those checks pass is Slice 6b marked accepted. Public exports, Help,
site activation, commits, pushes and releases are outside this task.

## Acceptance ownership matrix

| Requirement | Evidence owner |
| --- | --- |
| Closed helpers, two Forecast shapes, exact private registration and local frontier | `test_lazy_forecast_contracts.py` |
| Independent model equations, minima, zero/nonzero innovations, panels, calendar boundaries, overflow and representability | `test_lazy_forecast_numeric.py` |
| Complete multi-series validation before exactly one selected model invocation | `test_lazy_forecast_worker.py` |
| Logical, engine and local histories; direct DataFrame successors; empty selections; sampling and percentile approximation; real custom-calendar checkpoint | `test_lazy_forecast_runtime.py` |
| Input/output/memory/method/deadline guards, source/publication faults, cancellation, insufficient history and forged cold proof | `test_lazy_forecast_failures.py` |
| Successful version-pinned native object publication/read and source-offline local continuation through SDK stubs and real Store | `test_lazy_forecast_object.py` |
| Three independent processes for every model, origin-free continuation and unchanged exact binding reuse | `test_lazy_forecast_runtime_acceptance.py`, `lazy_forecast_runtime_worker.py` |
| Existing pure source construction, local graph lifetime and retained/correlation behavior | shared no-I/O/lifetime tests, daily broad gate and focused Runtime regressions |

## Implementation details verified by the gate

Generated floating metadata uses finite hexadecimal canonical identity; no
floating JSON ambiguity enters a definition fingerprint. Forecast buffers use
exact Arrow-backed pandas column types without a table exchange between local
steps. Source-aligned bucket validation is shared with the retained temporal
owner. Original fit evidence stores bounded counts, variance ranges and at most
1,000 future coordinates, with model-specific residual-degree checks at cold
read time. Cold endpoint/count validation is independent of the training row
count's magnitude. Finding extraction retains a bounded candidate prefix using
typed coordinate ordering, and checks model, horizon, training count, symmetry
and variance bounds against the bound Artifact.

Two shared edge cases were repaired by Forecast acceptance: empty local output
now carries an explicit schema batch into storage, and sampling-only retained
parts do not enter Metric sufficient-state validation for a result family.
Existing component validation and parent-owned sampling checks remain in place.


## Initial acceptance results

The initial executable candidate was
`c04af431f07f818a834506c20b4c1785fd0cb065388c1613df1b4a6fcb96743f`.
`evidence/slice-6b/candidate-files.json` records all 861 source, test, dependency,
Makefile and import-contract files under the existing journey fingerprint
protocol. Documentation and generated evidence are outside that fingerprint.
Every final command and journey records identical before/after fingerprints.

- `make check-agent` passed full lint/format, import contracts, typing for 425
  production modules, 6,428 default tests and the API documentation build.
  The default tests took 87.62 seconds; the complete gate took 119.30 seconds.
- Explicit-package test typing passed all nine new Forecast test/helper modules.
- Focused `make runtime-test` passed all 60 selected tests in 105.17 seconds,
  without skipped tests. It covers all Forecast Runtime/failure/object/journey
  cases, retained Metric regressions, and correlation regressions. The existing
  correlation nonidentity checkpoint storage matrix is outside this focused
  selection; its storage boundaries have independent baseline acceptance.
- All three independent-process journeys passed: naive with local history,
  drift with engine history, and seasonal-naive with local history. Each
  preserves rows, Artifact, Evidence, Findings and cold exact binding, including
  unchanged Store state on reuse and an unavailable original datasource.
- `check-final`, `test-typing-final` and `runtime-final` JSON/log pairs plus
  `recovery/forecast-*.json` retain the initial terminal evidence.
  `initial-terminal.json` preserves that accepted fingerprint. `candidate.patch`
  preserves the reviewable implementation and documentation diff.

Only private Slice 6b is closed. No service, public cutover, commit, push or
release was performed. The Slice 6a baseline remains preserved at `ca9b22b5`.


## Review follow-up

The review suggestions were checked against the owning design, current private
surface and actual decoder/worker paths. The accepted numerical contract already
specifies drift residual degrees of freedom as `n - 2` in the typed-operators
design; no model equation or public disclosure boundary changes are needed.

Adopted bounded improvements:

- `FamilySummaries` carries the original Attribution, Association and Forecast
  summaries through the worker result. A named graph result replaces the seven
  positional return values. Existing original-summary precedence is preserved.
- Producer version registrations share their common five entries while keeping
  the exact family-specific versions and ordering.
- Forecast contract disclosure uses `_contract_facts`, matching sibling owners;
  the private scalar helper is `_float_value`. The codec uses explicit exact-type
  narrowing without its redundant `isinstance` condition.
- Metric inputs still enter retained-part validation even when their only parts
  are sampling state. This preserves duplicate-role rejection. Result families
  with only sampling state avoid the unrelated Metric validator; numeric parts
  and Delta retain their existing validation paths.

The cold interval-level chain is `SessionStore._artifact_metadata` ->
`decode_descriptor` -> registered family `validate` -> `validate_forecast`.
The family check precedes descriptor fingerprint and publication-proof checks.
The corruption regression now covers finite levels 0, 1, -0.1 and 1.1 and requires
that specific invocation rejection, so a fingerprint mismatch cannot mask a
missing range check. The existing empty-selection Runtime test now names that
case explicitly and still checks zero Findings and empty readable rows.

The closed-family dispatch/traits redesign and consolidation of paired Dataset
methods were not adopted: they are broader architectural changes without a
demonstrated defect. Existing export snapshots and deferred Slice 8 disclosure
remain authoritative. Machine-generated logs remain ignored local evidence;
`review-followup/` preserves fresh gates separately from the original acceptance.


### Final review verification

The superseding executable candidate is
`016e0648cd86c6aac7fdb60dbbbb9085040d30202baf9be58ce4e962cfc63880` (861 files).
All final gates and all three fresh-process journeys have identical before/after
fingerprints. `evidence/slice-6b/review-followup/` owns the new candidate manifest,
complete logs and JSON records; the root `terminal.json` points to this acceptance.

- `make check-agent`: passed full lint/import checks, 425-module production
  typing, 6,429 default tests (85.21 seconds), and API documentation. The complete
  gate took 104.97 seconds.
- Strict explicit-package typing: passed all 11 Forecast and touched local
  worker/row test and helper modules.
- Focused `make runtime-test`: passed 72 tests without skips. This reran the
  initial Forecast/retained/correlation selection and added Attribution operand
  authority and sampled Metric checkpoint regressions for the shared worker
  and retained validation changes. Complete command duration: 126.85 seconds.
- Journey J: all three model/storage cases passed with three distinct process
  IDs, unavailable origins, identical rows/Artifact/Findings, and unchanged cold
  Store snapshots. Exact binding reuse created no Run.
- All 35 changed Python files passed the CJK scan; `git diff --check` passed.

HEAD remains `ca9b22b5`; no public activation, service, commit, push or release
was performed. The earlier acceptance evidence is retained separately.
