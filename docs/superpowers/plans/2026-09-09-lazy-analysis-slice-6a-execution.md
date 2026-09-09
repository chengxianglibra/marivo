# Slice 6a: private correlation and Association

Status: private Slice 6a and review follow-up complete; current candidate gates passed.

## Baseline and ownership

The owner accepted the implementation plan and coordinate-based lag amendment.
Slices 3b and 4d are accepted prerequisites. At implementation entry, Slice 5d
had been committed as `39e3ac7e` and the working tree was clean. Its complete
implementation is preserved. `evidence/slice-6a/baseline.json` freezes the input.

Operators owns new association contracts, paired states, construction and
numerical routines. Compiler owns correlation lowering and exact preparation
placement. Materialization owns Association codecs, quality, Evidence, Finding
production and cold recovery. Shared seams are observation Metric methods and
family/producer registries; compiler nodes, lowering and placement; Runtime
admission, worker inputs, validation, descriptor and publication registration;
and the existing Finding read contract. Only correlation-specific dispatch is
added to these seams. Tests use focused correlation modules and existing shared
fixtures. This record and the owning Typed Operators design track the work.

## Frozen behavior

The private method accepts two through sixteen quantitative Metrics and Pearson,
Spearman or Kendall tau-b. Four exact Association shapes retain authored pair
order. Time lag follows bound bucket coordinates, including calendar authority;
missing buckets are never collapsed or imputed. Positive lag pairs A(t) with
B(t+k). Per-series input minus matched counts is lag boundary loss; matched
counts equal null plus complete pairs. Candidate count is bounded at 4,096.
Every pair/series requires a valid candidate before publication. Invalid lag
rows remain auditable. Selection maximizes absolute coefficient, then minimizes
absolute lag and signed lag. Selections never recompute search authority.

Pearson/Spearman use tested DuckDB/Ibis lowering when eligible; exact local
continuations use the selected method. Kendall uses complete bounded numeric
pairs. Entity preparation is source-required and projects away identities.
Local raw Entity Artifacts are rejected. There is no exception-driven fallback,
sampling, source upload or internal DuckDB execution. Local successors receive
private DataFrames directly. Existing receipts, budgets and atomic publication
remain authoritative. Numerical preparation is not persisted as a Dataset.

## Acceptance

Use DuckDB 1.5.3, Ibis 12.0.0, pandas 2.3.3, PyArrow 25.0.1 and scipy 1.17.1.
Cover logical and every legal checkpoint input, source engine/local/object
outputs and local numerical local/object outputs. Compare independent numerical
references, including ties, nulls, constants, empty and nonfinite input, missing
buckets, signed lags, calendar boundaries, counts and candidate limits. Verify
zero-I/O construction, identity-free preparation, exact complete-input budgets,
failure rollback, generated-field continuations and descriptive-only Findings.
Three-process journeys prove origin-free continuation and exact cold binding
reuse with unchanged Artifact, Evidence, Findings and Session state.

Run focused tests and touched typing/lint, `make check-agent`, then necessary
focused Runtime tests. Object acceptance uses an isolated versioned MinIO.
Do not run the full release Runtime gate. Bind final evidence and logs under
`evidence/slice-6a/` to one unchanged candidate before closing only Slice 6a.
No public export, Help, site cutover, commit, push or release is authorized.

## Implementation inventory and numerical decisions

| Owner | Files and shared integration |
| --- | --- |
| Operator invocation and states | `operators/association_contracts.py`, `association.py`, `correlate.py`, `association_values.py`; Metric methods, observation registration, predicates and ordering |
| Source eligibility and lowering | `compiler/correlation.py`; placement, normalization, compiled proof and retained lowering dispatch |
| Complete local numerical inputs | `materialization/local_worker.py`; closed `PairInput`, certified stream count, complete-observation method budget and direct DataFrame successors |
| Publication and recovery | `materialization/association_codec.py`, `association_publication.py`; descriptor/Evidence registration, Runtime action port, storage ordering and Finding reader validation |
| Regression coverage | `tests/lazy_correlation_fixtures.py`, `lazy_correlation_runtime_worker.py`, six `test_lazy_correlation_*.py` modules; existing zero-I/O and graph lifetime tests |

Each source-prepared candidate carries all complete pairs and its input,
matched, null and complete counts, including one typed empty sentinel when
there are no complete pairs. Before merging unlike numerical SQL types,
Pearson pairs are centered in their original numeric type, and rank methods
use pairwise average ranks. These coefficient-preserving representations avoid
loss of integer distinctions and do not sample, omit pairs, or change methods.
Local raw non-identity inputs preserve numeric values through pair preparation.
Pearson centers before floating conversion; Spearman ranks each vector
independently; Kendall computes tau-b. Endpoint roundoff within `1e-12` is
normalized to signed one on both paths so perfect-correlation lag ties share
the prescribed selection rule. Non-finite or materially out-of-range results
still fail. Independent references use `statistics.correlation`, explicit
average ranks and pairwise concordance/discordance counts.

The complete-observation method budget includes null observations, not merely
the shorter transferred complete-pair stream. Association outputs retain no
raw pairs or executable origin graph. Original search counts and pair ranges
are carried through filtering, ranking and limits. Cold Finding validation
checks the exact registered pair subject, method, lag scope and count equations.

## Acceptance ownership matrix

| Requirement | Evidence owner |
| --- | --- |
| Four shapes, three methods, arity 2/16/17, candidate 4096/4097, private exports, current selectors and zero I/O | `test_lazy_correlation_contracts.py`, existing `test_lazy_*_no_io.py` |
| Independent numerical references, mixed numeric types and large integers | `test_lazy_correlation_compiler.py`, `test_lazy_correlation_numeric.py` |
| Five statuses, empty/non-finite input, missing candidates, positive/negative lag, missing buckets, month/year boundary, certified calendar, null Dimension series and tie selection | `test_lazy_correlation_numeric.py` |
| Logical/engine inputs, source-private transfer, authored pair order, row-specific Finding subjects, direct local handoffs, local/object non-identity Metric inputs, retained search/selection authority | `test_lazy_correlation_runtime.py` |
| Complete-input budgets, deadlines, source/transfer/publication faults, cancellation, raw local Entity rejection, changed receipt, missing/corrupt pair stream, forged Evidence/Finding facts | `test_lazy_correlation_failures.py` |
| Three independent processes, removed origin, exact cold binding reuse, unchanged rows/contracts/Evidence/Findings/Artifact/Run authority | `test_lazy_correlation_runtime_acceptance.py`; eight engine/local/object terminal records |
| Shared 5d and retained Metric execution remains intact | focused `test_lazy_distribution_runtime.py` and `test_lazy_retained_runtime.py`, plus the complete daily gate |

Object execution is expressly part of the accepted Slice 6a plan. This focused
acceptance therefore uses the existing versioned object fixture with a separate
MinIO process at `127.0.0.1:19066` and per-test disposable buckets. It does not
change normal Runtime routing or run the full release gate. The task owns only
this isolated service; unrelated services and work remain untouched.

## Final evidence protocol

The final executable candidate is
`31524592d2f3280ba73cfaf0eb900cfc04d9e8d669d9a51bd05c48f334243635`.
`evidence/slice-6a/candidate-files.json` freezes SHA-256 for all 845 Python,
Makefile, dependency and import-contract files, in the same ordering/protocol
used by the existing Runtime journey manifest. Documentation and generated
logs are outside this executable fingerprint. `baseline.json` retains the
starting commit and file hashes. Iteration logs are diagnostic only.

Final gate records carry command, elapsed time, exit code and identical
before/after candidate fingerprints. Three-process records additionally carry
independent PIDs, runtime versions, committed identities and Store snapshots.
Final gate completion is recorded below after all commands terminate.

## Final gate results

All final gates passed on the candidate above, with unchanged before/after
fingerprints:

- `make check-agent`: 6,365 daily tests passed; full lint/format and import
  contracts passed; all 419 production modules passed typing; API docs built.
  Evidence: `evidence/slice-6a/check-final.{json,log}`.
- Strict additional typing for all eight correlation test/helper modules passed.
  Evidence: `evidence/slice-6a/test-typing-final.{json,log}`.
- The focused Runtime command covering correlation execution, failures and
  three-process acceptance, plus distribution and retained Metric regressions,
  passed all 77 tests in 228.83 seconds.
  Evidence: `evidence/slice-6a/runtime-final.{json,log}`.
- All eight `evidence/slice-6a/recovery/correlation-*.json` journeys passed:
  Pearson/Spearman engine output; all three methods local and object output.
  Every journey preserves exact cold binding without a new Run or source query,
  and retains matching rows, selected rows, Artifact, Evidence and Findings.
- `candidate.patch` preserves a reviewable executable diff from the clean
  baseline. `terminal.json` records the completed gates and service cleanup.

Slice 6a is complete as a private implementation. Slice 8 public activation
and all other unfinished slices retain their original scope and status.
No commit, push or release was performed.


## Review follow-up

The user authorized triage and implementation of reasonable review suggestions.
The current HEAD is `06166dbf`; its Runtime performance work and all preexisting
Slice 6a edits are preserved. The follow-up baseline and new gate evidence live
under `evidence/slice-6a/review-1/`, separate from the original acceptance.

| Review item | Decision and evidence |
| --- | --- |
| Missing selection id and pair approximation Evidence | Accepted. Add closed fields, bind them into the existing Evidence digest and validate exact pair/approximation equality on cold reads. |
| Repeated selection rules | Accepted. One declarative order and id in `association_contracts.py` drives the Python selection key, SQL ordering, publication validation and descriptive text. |
| Candidate and pair-count magic numbers | Accepted. Reuse `MAX_CANDIDATES`, `pair_count()` and `candidate_count()`; use the shared `FINDING_CAP` in Association and generic Finding read validation. |
| Coalition terminology on PairInput errors | Accepted. Errors name the selected numerical input kind; coalition behavior is preserved. |
| Three Association FindingRegistration fields | Deferred. The existing owner already constructs/validates these together; adding another registration type solely for grouping would not fix an observed contract defect. |
| Positional preparation flags | Accepted. Use explicit keyword arguments for distribution and correlation preparation. |
| Redundant lag filtering after integer validation | Accepted. Decode each signed integer once; bool, string and float values fail explicitly. |
| Separate SQL/pandas lag mechanics | Keep backend-specific implementations. Calendar, missing-bucket and numerical differential tests are the cross-backend contract; no backend-neutral execution abstraction is introduced. |
| Unspecified 1000-Finding truncation | Partially accepted. The owning Finding extraction section already mandates a hard 1000 cap for every extractor. Preserve it and add missing eligible/emitted/truncated Evidence and card disclosure. A 1001-candidate Runtime test checks full rows, deterministic capped Findings and cold pagination. |
| Correlate bypasses shared input roles | Not reproduced: the role check precedes correlate dispatch. Add an independent mismatched-role regression; no dispatch change is needed. |
| Method budget should use series rather than rows | Not adopted. The method guard budgets complete pairwise observation work, including null observations; candidate cardinality separately budgets pair/lag/series combinations. Counting only series would undercount numerical work. |

The 1001-candidate Runtime regression exposed a source Dimension-Time bug:
series cardinality was aggregated against the wrong Ibis relation. The count
now aggregates its own distinct-series relation. Independent three-method
coverage includes null Dimension series and the source 4097-candidate guard.

The initial candidate and its evidence above remain historical. The review
follow-up candidate and terminal results below supersede the initial acceptance
for the final working tree. Public activation, commit, push and release remain
outside scope.


### Review follow-up acceptance

The final review candidate is
`3a51186475600b2957c5dbe806756e1d08909e636384f081781d2d2f548ab5de`,
covering 846 executable/test/configuration files on HEAD `06166dbf`.
`evidence/slice-6a/review-1/candidate-files.json` freezes every file hash;
`candidate.patch` preserves the executable diff. The final gate records prove
identical candidate fingerprints before and after each command:

- `check-final.{json,log}`: `make check-agent` passed all 6,381 default tests,
  production typing, lint/format, import contracts and API documentation.
- `test-typing-final.{json,log}`: strict typing passed all nine correlation
  test/helper modules.
- `runtime-final.{json,log}`: all 81 focused Runtime tests passed in 222.32
  seconds, including correlation faults/disclosure/pagination, distribution,
  retained Metric and eight independent three-process recovery journeys.
- `recovery/correlation-*.json`: exact cold binding, rows, Evidence, Finding and
  Artifact identity remain unchanged for all eight engine/local/object routes.
- `terminal.json` and `object-service.json`: final verification and cleanup;
  the task-owned MinIO is stopped and its disposable buckets are removed.

The SQL adapter also widens signed lag values solely for ordering so
`abs(INT64_MIN)` cannot overflow. Three-method regressions retain that unusable
candidate beside a valid zero-lag candidate; published lag fields remain int64.
Only private Slice 6a changed. The original baseline, subsequent Runtime
optimization commit, public cutover boundary and no-commit/push/release scope
remain intact.
