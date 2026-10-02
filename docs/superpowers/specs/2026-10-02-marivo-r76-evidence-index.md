# R7.6 History views, Duration and Subject observation evidence

Baseline: clean `panda`, `09c0c488738816e02fdea785df72120aa4269e40` (R7.5).
Date: 2026-10-02. Status: implemented and qualified within the local scope below.
Work is uncommitted. The original R7.1 snapshot and earlier evidence remain historical
records; this amendment does not enlarge their acceptance.

## Public and execution owners

LogicalHistoryResult and MaterializedHistoryResult expose exactly six views:
`read(mv.in_state(state, at=checkpoint))`, `distribution(at=..., axes=...)`,
`transitions()`, `violations()`, `intervals()` and `dwell()`. State reads reuse
BooleanRelation. StateDistributionResult, TransitionSummary, ViolationResult,
StateIntervalResult and DwellSummary each have precise Logical/Materialized pairs
and concrete owned Relation fields. Materialized operations return fixed Logical
graphs. `InState`/`in_state` keep their original signature under the new sole owner;
there is no Dataset reducer dependency, synonym or public string-column accessor.

The unique graph/method registry owns derivation, physical keys, planning,
execution, exchange, publication and recovery. Source History consumers use the
registered `ibis_python` route after all Ibis source preparation; fixed consumers
use `artifact_python` on verified Arrow/Parquet receipts. They consume the same
R7.5 captured canonical History, not another scan or replay. The common deadline
remains 600 seconds per execute invocation, including preparation and publication.

Public state reading retains the full original Subject domain, including Subjects
with no interval. NotStarted is a defined false state predicate; insufficient
authority is Unknown. Checkpoints are confined to the report window; end uses
the left limit. Declared states/pairs retain zero rows, legal self-transitions
and zero-duration transitions come from canonical trace, and transition shares
use modeled transitions rather than Subject counts or interval counts.

Distribution axes are prepared at each checkpoint against the original complete
member envelope before local replay/selection. Retained facts include the exact
checkpoint, Dimension/version authority and full nullable group tuple. Snapshot
and validity histories, silent/no-event Subjects and distinct checkpoint values
are tested. Fixed routes consume retained proofs and reject missing axes; they
cannot obtain current Semantic or source data to repair missing authority.

Intervals retain original ordinal, raw start/end reasons in canonical state,
clipped bounds, left clipping and censor status. Violations retain exact Event
occurrence identities and Subject bindings. Both support `subjects()` and a
filtered `members(through=binding)` image. State reading maps Subjects directly
without through. Distribution, transition and dwell summaries have no Subject
mapping. Interval rows cannot reconstruct the discarded transition trajectory.

## Exact Duration and retained state

Dwell statistics describe `completed_window_fragment_duration@v1`: left-clipped
completed fragments are included, right-censored and coverage-censored fragments
contribute counts but no duration. Every declared state remains, including an
empty completed set. Captured integer microsecond ticks are summed exactly;
quantiles interpolate with Fraction; mean, median and p90 finish with one
HALF_EVEN rounding and checked int64 bounds. Empty Duration cells carry
`empty_completed_set`. Raw one/two/three/four-tick examples independently verify
ties, interpolation and a pooled interval mean. The accepted R7.2 native UTC/us
conversion and possible ns loss remain disclosed; this is not ns-exact source
qualification.

The existing row.mean consumes current completed interval rows and retains exact
sum/count for legal fixed rollup. Dwell mean/median/p90 fields cannot be averaged
as group means or group p90s; their current contracts omit that continuation and
the callable rejects it. Dwell keeps the original sufficient interval statistics,
not scalar summaries masquerading as a recoverable distribution.

Closed HistoryViewPart and singleton retained-state parts bind kind, model,
History/input identity, checkpoints, fields, schema and versions. Recovery validates
the saved canonical trace and recomputes projections without origin replay. Subject
parts preserve complete entity/interval/occurrence keys and the original binding
through filtering. Required primary/view/Subject parts, schema metadata, retained
state, identity and version damage reject before continuation or an exact hit;
failed reads allocate no new Run and preserve sibling Artifacts.

## F13 same-Run observation

State, interval and violation selections enter the existing prepared-observation
route for original `count:int64` and `sum:float64` Metrics. The full original member
envelope and all source dependencies are prepared before any local consumer.
Only then does the actual full-key Subject image restrict eligible contributions.
Tests inspect source-before-local execution order, nonempty and empty selection,
scope, components and group state. There is no source-after-local query, local
result upload, implicit Subject deduplication of instance statistics or hidden
replay. Execute the source prepared observation before further continuation; fixed
observations roll up their retained original components. Fixed membership plus a
live Metric still rejects. Further Metric expression/component families remain
outside this finite P36 scope.

## Original qualification cells and A10

The [qualification record](2026-10-02-marivo-r76-qualification.json) preserves
every original ID, route, mandatory flag, profile requirement and phase owner.

| Profiles | Full key/time profiles | Source | Fixed | Cold | Accepted cells |
| --- | --- | --- | --- | --- | --- |
| P11–P16, six History views | 9 × 10 | 540 | 540 | 540 | 1620 |
| P24/P36/P49, History consumers | 9 × 10 | 270 | 270 | 270 | 810 |

The 90 core cases compare all six source and fixed views against independent
raw-event/checkpoint/transition/clipped-interval/Fraction oracles. Each removes
the source database and Parquet files, then launches a separate cold process.
Int64 keys exceed 2**53; composite Subject and occurrence identities are compared
in full. The 90 consumer cases execute five transport shapes, completed interval
mean and all three selection shapes with both Metrics. Their fixed/cold P36
continuation is original Metric component rollup, not a new live observation.
P24's Anchor/retention shapes remain R7.7/R7.8; P36 and P49 Journey responsibilities
remain independently owned by R7.3. Historical axes have four supplementary
table/Parquet × snapshot/validity cases; the P12 core matrix uses the no-axis shape
and does not claim a separate all-key/all-time historical-axis cross product.

A10 runs table and Parquet through three distinct processes: producer, fixed
continuation and cold exact-hit recovery. Producer publishes canonical History,
all six views and six same-Run observations, then removes sources. Offline phases
poison SemanticProject.load, semantic.load, SourceSession entry/batches,
duckdb/ibis.duckdb connect and history_execution.execute. Each actually executes
**109 disclosed fixed K entries**, creates **119 outputs**, and the cold process
recreates the exact same Artifact references. Field projections, qualified scalar
statistics/filter/rank, instance Subject mappings, six History views and prepared
observation components are included. Unrecognized disclosed K fails the worker;
contracts are not treated as successful execution. Current bounded repr/show,
contract, receipt reads and exact hits are checked through the public surface.
Manifest references are obtained through public evidence_digest().artifact_ref,
which also supports the interval result whose state property is a CategoryRelation.

## Owning checks

| Requirement | Executable owner |
| --- | --- |
| V10 | test_analysis_history_r76.py raw checkpoint/coverage/end, empty domain, six-view matrix, zero-duration trajectory, axes versions and no-summary/interval reconstruction |
| V11 | Independent history_r76_oracle.py Fraction/clipped-interval oracle, raw fractional ticks, overflow/empty policy, censored exclusion, interval mean sufficient state and rejection of summary pooling |
| V06/V13 | Three selection × two source forms source-prefix traces, empty-selection scope/components, P24/P36/P49 matrix and A10 observations |
| V14 | Eight exchange corruption variants; six missing/corrupt primary/view/Subject receipt variants before recovery/continuation/exact hit; A10 offline K and exact artifact equality |
| V15 | Artifact/Evidence/Findings/terminal/before-commit, cancellation and timeout failures preserve prior Artifact; affected R7.2–R7.5 tests retain actual stream/SQL and shared publication duties |
| V16 | Legacy module absence tests, registry/family retirement and preserved Event/R8 import/dispatch regressions |
| V17 | Export snapshot, positive/negative precise typing, native Help/reachability/budgets, actual K, API generation and identical executed latest English/Chinese History examples |

## Retirement

R7.5's remaining exclusive private consumers now exit. These production modules
are physically deleted:

- domains/lifecycle.py and domains/lifecycle_reducers.py;
- compiler/lifecycle_reducers.py;
- materialization/lifecycle_codec.py and lifecycle_publication.py;
- materialization/lifecycle_reducer_codec.py and lifecycle_reducer_publication.py.

Their payloads/semantics, descriptor field, admission, lowering, dispatch,
publication/storage/recovery codecs and execute_lifecycle port are removed from
shared owners. Generic rejection of an obsolete family is not a compatibility
reader. Actual shared Event/Forecast/Association consumers and adapters remain;
the Event backend qualifier formerly named lifecycle_dialect is now event_dialect.
R7.3/R7.4 Event retirement and remote physical qualification retain their owners.

Six exclusive reducer test files, the reducer worker and two obsolete Lifecycle
fixtures are removed. Independent raw-event, censoring, clipping, self/zero-duration,
Fraction, empty and Subject-selection counterexamples now execute the public path.
Shared StateModel authoring fixtures remain as declarations without an old producer.
No old Runtime test is counted as new-route qualification. The migration ledger
records replacement execution, unreachability and physical deletion separately.
The qualification JSON maps the retired independent counterexamples to their new
owners. In particular, identical positive interval data with one versus two
same-time self-transitions produces distinct exact transition counts; interval
reconstruction cannot erase that discriminator. Old native transport/performance
acceptance is not transferred to this local route and remains a separate R9 duty.

## Validation receipts

Receipts and current source hashes are bound by the qualification JSON. Failed
attempts below are preserved and never counted as passing cells.

- Core matrix: **87 passed**, 1836.89 seconds; three cold imports failed during
  an in-progress row.count precision declaration. After the declaration was
  corrected, **K33-T07, K22-T04 and K33-T08 passed**, 123.38 seconds. All 90
  distinct producer profiles and **1620 cells** are accepted.
- Consumer matrix: **90 passed**, 4682.99 seconds; all **810 cells** accepted.
  A representative full-shape post-matrix check passed in **93.12 seconds**.
- A10: **2 passed**, 561.97 seconds, with all three processes for each source form.
- Final A10 using public Evidence references: **2 passed**, **630.00 seconds**;
  `/tmp/marivo-r76-a10-exit.xml` is the exit receipt. Both source forms retain
  109 fixed K entries, 119 outputs and exact cold hits.
- Focused Runtime gate before the final public-reference/multiplicity closeout:
  **48 passed**, 133.22 seconds; JUnit
  `/tmp/marivo-r76-targeted.xml`.
- The transferred same-positive-interval/different-transition-multiplicity
  counterexample passed in **10.56 seconds**; the final public recovery addition
  to the identical EN/ZH example passed in **30.15 seconds**. These supplemental
  cases do not add original qualification cells.
- Final focused Runtime on the complete current implementation: **49 passed**,
  **145.29 seconds**; exit receipt `/tmp/marivo-r76-focused-exit.xml`. This includes
  the transferred multiplicity counterexample, public Evidence references,
  damaged receipts, empty selection and the final executed EN/ZH example.
- Affected R7.2–R7.5 regression: **256 passed**, 1046.08 seconds; one cold import
  failed on the same temporary precision declaration. That exact validity-table
  case passed after repair in 51.54 seconds: **257 distinct passing tests**.
- Broad gate before the evidence assertion: **5301 default tests, 5 skips**.
  Final make check-agent passed lint/import contracts, typing of **429 modules**,
  **5302 default tests, 5 skips** in 61.08 seconds, and API documentation
  generation; receipt `/tmp/marivo-r76-exit.log`.
- Final site build passed **321 pages** and both install-script outputs;
  receipt `/tmp/marivo-r76-site-exit.log`.
- git diff --check passed. Branch/HEAD remain the entry baseline, with no staged
  changes; AGENTS.md, packaged skills and the historical snapshot are unchanged.

The original R7.1 snapshot, AGENTS.md and packaged skills are unchanged. No commit,
push, release, release-check, MinIO, installed-wheel or remote execution occurred.
R7.6 V10/V11 and local A10 are complete in this scope. R7.7–R7.9/full R7,
same-wheel, remote backends and R8–R10 retain separate acceptance boundaries.
