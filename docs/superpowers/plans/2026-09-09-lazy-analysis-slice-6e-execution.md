# Slice 6e: private driver-axis screening

Status: private Slice 6e implementation and technical acceptance complete (2026-09-10).

## Baseline and authorization

The owner approved the complete Slice 6e plan and complete-partition cardinality
on 2026-09-09. Implementation starts on clean `lazy-dataset` HEAD
`930fd5fb7bdf61d035678ed2301bd0188cc6a4f7`, containing committed Slice 6d.
The preceding planning baseline matched Slice 6d's 886-file executable digest;
157 focused Attribution and Candidate dependency tests passed during planning.
Slice 5b retains its independent pending technical acceptance.

## Frozen contracts and ownership

Driver screening remains private. `DeltaDiscovery.driver_axes` consumes an
ordered nonempty duplicate-free governed Dimension search space and an integer
limit in [1, 1000]. Only exact additive Delta partitions qualify. Every axis
uses the same scope: unsearched Dimensions, Entity identity when present and
the original comparison ordinal and paired time values. No implicit temporal
or Entity fold occurs.

Cardinality counts all actual partition members, including permitted null and
zero-contribution members. The minimum descending absolute-contribution prefix
reaching 50% defines k; concentration share is that prefix divided by the whole
absolute pool; score is 1 / (k + cardinality / 1000). Reasons contain only
`axis_concentration`. Complete zero pools are evaluated empty; missing or
invalid partitions fail. No score is inferential or causal evidence.

- Operator owner: closed driver invocation, schema, scope, exact local scorer,
  pure construction and independent numerical tests.
- Compiler owner: source-native frozen input, exact partition/endpoint checks,
  original selection and ordinal anchoring, typed digest and scalar proofs.
- Persistence owner: closed driver Evidence codecs, atomic zero-Finding
  publication, original/current authority and cold validation.
- Integration owner: placement, guarded worker graph, mixed-domain local
  anchoring, Runtime admission, recovery journeys, documentation and final gates.

Workers preserve each other's changes. Shared additive preparation is extracted
only where both callers need it; complete Attribution share calculation is not
part of concentration scoring. Missing-axis expansion stops at every retained
boundary. Entity scopes and their continuations remain source-required and
never gain Population input admission.

## Acceptance matrix

Pure tests cover all Delta shapes, scalar/generated selectors, scope keys,
source-free construction, exact parameter and fold admission, independent
numeric references, ties, zero/null members, signed cancellation and overflow.
Runtime covers retained and logical axes, ordered mixed Metric operands,
cross-source expansion, selected original ordinals, exact component state,
sampling, identity privacy, guards, cancellation and atomic rollback.

Separate producer, source-offline continuation and cold-binding processes must
prove immutable rows, original Evidence, zero Findings and exact reuse without
new Runs or source queries. Local Parquet owns ordinary functional cases;
engine cases prove native identity and immutable scans; native SDK stubs plus
real Store files cover object storage. No MinIO service is required.

Final acceptance requires focused typing/lint/tests, `make check-agent`, focused
`make runtime-test`, independent review and an unchanged executable fingerprint
for all terminal evidence under `evidence/slice-6e/`. Acceptance and the cutover
status are updated only after these gates complete. No public exports, Help,
current site documentation, commit, push or release is included.

## Implementation and numerical decisions

The private `candidate/driver-axis@v1` registration owns a separate driver
definition, spec, payload and evaluation summary. The common Candidate family
still owns identity, selectors, ordering, publication and cold recovery. Driver
Evidence has no threshold or time-series statistics. Rank, where and limit keep
the original definition and evaluation while updating current output facts.

Attribution supplies shared complete-partition preparation, sufficient-state
folding and independent endpoint validation. Driver scoring does not execute
Attribution's net-Delta share calculation. Native driver floating folds, totals
and ordered prefixes sum exact binary64 expansions in DuckDB BIGNUM, then round
once to float64. Two static temporary macros are installed inside the source
transaction. The frozen relation, validation and scalar proof use the same
transaction and the existing 256 MiB memory and deadline guards. BIGNUM never
crosses an Arrow boundary. Attribution's native arithmetic remains unchanged.

The local scorer uses complete `fsum` prefixes and exact represented-number
comparisons at the 50% boundary. A finite exact sum whose `fsum` accumulator
overflows is computed from the exact represented binary values before one final
rounding; a truly overflowing result remains an error. This is arithmetic
within the registered local method, with no execution-method fallback.

Missing-axis expansion retains the original Delta selection. Source branches
perform their own semantic work; the guarded non-Entity local path anchors exact
original coordinates and preassigned ordinals before comparison. Paired time
fields retain their IDs and values. Shared sampling roots realize once. Entity
scope production and row continuations require compatible source execution;
local and object receipts do not grant identity continuation or `population=`.

## Review and iteration disposition

- Independent P1: ordinary native floating SUM lost small contributions during
  cancellation and changed k/score relative to the local method. Exact native
  sums and once-rounded local prefixes repair it. Differential cases include
  `[1e16, 1, -1e16]`, fractional half boundaries, subnormal values and finite
  extreme-value cancellation in either input order.
- Independent P2: cold Evidence could claim positive candidates without enough
  input rows. Validation now bounds candidates by rows times searched axes,
  requires one unscoped search scope and rejects impossible retained scope
  counts, while preserving valid expanded empty count partitions.
- The first frozen focused run passed 359 tests and failed two existing
  Candidate error-message regressions. Its Evidence remains under
  `evidence/slice-6e/pre-final-1/`. The codec now preserves an existing typed
  IntegrityError before wrapping raw construction errors. Ten narrow error and
  Driver population-rejection regressions passed before the final restart.
  The focused total increased from 361 (359 passed plus two failed) to 363
  because `test_driver_scope_never_grants_population_input` added two
  parametrized cases, for scalar and Entity scope. These cover logical and
  retained Driver Candidates and their rank/limit successors; they are separate
  from the P2 cold-count repair.

- The second frozen run passed all 363 focused tests; strict typing exposed two
  test-only dataclass union-narrowing errors. Explicit replacement branches and
  an evaluation type assertion preserve those cases. This run remains under
  `evidence/slice-6e/pre-final-2/`; every final gate is rerun after the correction.

The final read-only review is bound to executable candidate
`22b754e3b1f55326a615ff9fda1d8a13906adb9352c35b73235df0948347269f`
(904 files). Its independent original P1 reproduction produces identical native
and local region results: k=2, share=0.7, score=0.4992511233150274, with every
native validation equal to zero. Both Evidence and descriptor validation reject
the original P2 corruption. Review also covered exact prefix rounding,
intermediate overflow, transaction macro lifetime, Entity boundaries, expansion
authority and the preserved IntegrityError branch. No findings remain open.

## Final immutable evidence

All terminal evidence is collected under `evidence/slice-6e/final/` against the
904-file candidate above. `candidate.json` pins every file; `gates.json` records
exact commands, return codes, full command wall time and before/after digests.
The fingerprint covers all Python files under `marivo/` and `tests/`, plus
`pyproject.toml`, `Makefile` and `.importlinter`. Documentation-only closeout is
outside that executable fingerprint.

| Gate | Result | Full command wall time |
| --- | --- | --- |
| Focused driver, Attribution and existing Candidate regressions | 363 passed | 15.597 s |
| Strict typing for every changed/new test module | 14 files passed | 0.601 s |
| `make check-agent` | 6,748 default tests; 904-file format/lint, import contracts, 438-module typing and API docs passed | 138.853 s |
| Focused Runtime (`make runtime-test-agent`, two workers) | 149 passed | 257.259 s |
| `git diff --check` | Passed | 0.112 s |

The default pytest stage reports 120.82 s; this is distinct from the 138.853 s
for the full compact gate, which also includes static and documentation stages.
The Runtime command and test selection are preserved in `gates.json`, and its
three-process local/engine-input records are written to `journeys/`.
The Runtime pytest stage reports 256.53 s; every final gate returned zero and
recorded the same before/after executable fingerprint.

Both independent journeys passed on that same fingerprint. Local-input
producer/continuation/cold-reader PIDs are 57365/57401/57436; engine-input PIDs
are 57462/57516/57571. Each producer retains a Delta, publishes four scoped
Candidate rows to local Parquet and removes its origin. The next process
recovers the original Candidate and selects/ranks/limits it to one row. The
cold process reuses both exact bindings with no new Run, source statement,
validation query or transfer. Original Artifact facts, rows, definition,
evaluation and the canonical empty Finding set remain identical across all
three processes.

The local search Artifact is `artifact_ca883dcffb734ab3a9f0e9b3a5813af4`; the
engine-input search Artifact is `artifact_c96e5717376441dc8b033acb10b266a9`.
The JSON records preserve their Session, input and selected Artifact refs,
full original/current Evidence and Store snapshots. Tested versions are Python
3.12.13, DuckDB 1.5.3, Ibis 12.0.0, pandas 2.3.3 and PyArrow 25.0.1.

## Acceptance and boundary

Private Slice 6e is accepted. The final Runtime selection covers logical and
retained axes; all four ordered Metric operand-authority combinations; exact
cross-source local expansion with original time ordinals; shared sampling;
native Entity production and compatible engine continuation; source-required
local/object barriers; input/state/memory/output/deadline guards; cancellation,
worker/source failures, atomic rollback and lost acknowledgement; object SDK
stubs with real Stores; and independent cold recovery. The same selection
includes the related existing Candidate, Entity membership, Attribution,
Compare and retained-state regressions.

Committed Slice 6d is preserved at HEAD `930fd5fb7bdf61d035678ed2301bd0188cc6a4f7`.
The dependency paths exercised here do not close Slice 5b's independent pending
acceptance. Public exports, live Help, current bilingual site documentation and
eager deletion remain Slice 8 work. No commit, push, release, MinIO service or
full release Runtime gate was performed.

## Spec-alignment review follow-up (2026-09-10)

The supplied review's four minor observations were assessed as follows:

1. The disposition now reconciles 361 to 363 focused cases and identifies the
   two added population-input rejection cases. They were not P2 cold-count cases.
2. The original `git diff --check` excluded untracked files. The follow-up adds
   explicit `git diff --no-index --check` checks against `/dev/null` for all 19
   untracked files, including six production Python modules, twelve test Python
   files and this execution record. All passed; no whitespace repair was needed.
3. Independent per-file SHA-256 comparison against the saved 904-file manifest
   found no additions, removals or changed bytes before this follow-up. The
   existing full gate evidence remains intact; no second permanent fingerprint
   mechanism is introduced.
4. A comment at the retained-scan membership projection explains that complete
   row keys are validated separately and Driver axes may repeat an Entity. Only
   the internal source membership spine is deduplicated; primary rows and the
   separate population-input restrictions remain unchanged.

The resulting 904-file byte fingerprint is
`e4dc3987a57eaf795d752f66032a448a9f6e150387d885d5d87a08a54cd3f07c`.
`evidence/slice-6e/review-followup/` preserves the pre-comment source, independent
manifest comparison, replayable verification script and check results. The only
changed executable file is `compiler/lowering.py`; its Python AST is identical.
Focused format/lint/import checks and tracked/untracked whitespace checks pass.
The original complete test and Runtime gates remain bound to `22b754e3…7269f`;
this documentation/comment-only follow-up does not claim those gates were rerun.

## Consolidated adversarial review follow-up (2026-09-10)

Minor observations 1-3 repeat the preceding follow-up and require no additional
repair. The added cases remain population-rejection cases, not P2 cold-count
cases. The original `22b754e3…7269f` fingerprint identifies the full acceptance
snapshot; the intervening comment-only follow-up has its separately recorded
fingerprint above.

Minor 4 is not dead code: `_group` uses a global aggregate when scope keys are
empty. An actual DuckDB/Ibis probe on an empty table returned one row with
`axis_cardinality=0` and null total. The positive-cardinality filter removes that
row before finite-total validation. The filter is retained with an explanatory
comment; the probe is saved in `review-followup-2/empty-global-aggregate.json`.

Minor 5 describes different validation strengths, not an observable score
disagreement. Generated rows have k and cardinality at least one, so their
maximum score is `1 / 1.001`; publication/cold Evidence validation enforces this
tighter bound after native scalar proof decoding. No behavior change is made.

Minor 6 is adopted as a comment: shared floating reconciliation permits rounding
between grouped and whole-scope folds; integer/Decimal comparisons remain exact.
This tolerance does not relax partition completeness or the exact 50% boundary.
The rejected Important observations do not warrant numerical or lifecycle edits.

The resulting 904-file fingerprint is
`6389902905c4aded33fb5cf5cd1c8b1cf3d47db4e5e4af953d60d069a17dc3dd`.
`evidence/slice-6e/review-followup-2/verification.json` confirms that the only
differences from the full acceptance snapshot are comments in `lowering.py`,
`driver_candidate.py` and `driver_values.py`, with identical Python ASTs. Focused
format/lint/import and tracked/untracked whitespace checks pass. The original
full gates retain their original fingerprint; they were not rerun for these
comments. No commit or push is included in this review follow-up.
