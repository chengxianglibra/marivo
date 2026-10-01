# R7.3 Journey matching, duration and Subject image evidence

Baseline: `63875faa8c` (R7.2 occurrence preparation). Work is uncommitted.
Status: R7.3 implementation and bounded validation complete, 2026-10-01.
This is bounded R7.3 evidence, not full R7 or same-wheel acceptance.

## Sole execution and retained-state owners

- `session.events.match` consumes a same-Session logical AnalysisDomain and returns
  LogicalJourneyResult. It binds Event/participant/order definitions with governed
  schema preflight; it does not execute the legacy Population/Dataset matcher.
- JourneyMatch, JourneyDuration, JourneyCompleted and JourneyRead are registered
  methods in the existing rule registry, graph planner, execution-key/snapshot,
  publication, exchange and Store 7 owners. The public result pairs are
  JourneyResult, EventDurationResult and CompletedJourneys (Logical/Materialized).
- Matching retains full Subject/start occurrence identity, exact step bindings,
  canonical assignment and dense reach. First-per-subject, every-start shared and
  final-only exclusive reservation consume proved business order. Starts use a
  half-open cohort and completion uses an exclusive follow-up bound. No start
  creates no synthetic failure. Coverage uses each actual predecessor interval.
- Registered duration projections expose all six relations and distinguish
  complete, incomplete, coverage_censored, not_entered and entry_unknown.
  Only completed pairs provide duration. First-per-subject dropout is a Boolean
  relation; every-start dropout rejects. Ordinary where preserves Unknown and
  exact Subject images; cohort requires complete Journey opportunities.
- Duration mean extends the existing row-method owner with exact int64 tick
  sum/count, Fraction division, one nearest-even output and a retained half-tick
  bound. Journey multiplicity is not replaced by Subject cardinality. Grouped
  means retain original sums/counts for rollup. Duration row-statistic comparisons
  are not newly qualified by this work.
- Prepared observation captures all original members and Metric contribution
  dependencies before any local Journey selection. Subject image, restriction and
  observation run in one DAG/Run; no upload, source-after-local or fallback exists.
- Assignment, reach, coverage, precision, mappings and reduction state persist in
  Store 7. Recovery validates exact identity, bindings, temporal order, occurrence
  reuse and exclusive completion. Fixed reducers/reads/selections consume those
  parts without rematching or consulting current semantic/source definitions.
  R7 execution shares the existing 600-second deadline.

## Placement assessment

The existing `compiler/event.py::compile_event_match` does produce dense source
assignments; it is not merely a summary implementation. Inspection found two
contract mismatches that prevent reusing it unchanged for R7.3:
`_ordered_occurrences` ranks `(occurred_at, event_identity components)` rather
than consuming the R7.2 proved business-order ordinal, and `_dense_rows` receives
one `coverage_complete` Boolean rather than per-Event predecessor-interval evidence
and dense reach truth. The old strict/inclusive ambiguity comparison is not the
new declared order proof. Its exclusive reservation formula and retained legacy
consumers remain useful historical evidence, not R7.3 qualification.

The new Ibis prefix owns membership, Event predicates, participant paths, identity,
version/time checks and bounded occurrence capture. The explicitly registered
`ibis_python` assignment consumer handles canonical assignment and interval truth
from that captured input; `artifact_python` consumes verified retained input.
This is a qualification boundary, not a claim that a new native implementation is
mathematically impossible. No native sequence/funnel function, remote backend or
failure-triggered engine switch is qualified. String and int64 identity carriers
are admitted without changing the complete typed key schema.

## Independent evidence owners

| Gate | Current executable owner and oracle |
| --- | --- |
| V04 | `tests/test_analysis_journey_matching_r73.py`: exhaustive subsequence enumeration independently checks three policies, repeated Event, one/three steps, shuffled capture, same-instant proved order and covered/Unknown reach; explicit missing-middle, no-start and window-boundary counterexamples |
| V05 | Same module: five statuses, exact elapsed ticks and DST-fold instant subtraction; public three Journeys 10/30/100 seconds produce 140/3, rounded to 46.666667 seconds; Subject means 20/100 average to 60, retained rollup returns the Journey mean |
| V06 / A09 | Public governed test: dropout and Duration selection -> Subject image -> new Metric in one Run; nonempty/empty selections, empty contribution groups and rollup, complete sum/support/magnitude/coverage components and exact restriction scope; trace forbids every source read after local matching |
| V13 | R7.2 matrix plus public table/Parquet producer: native and Parquet capture, actual source mutation reevaluation, unchanged fixed assignment, microsecond/captured-unit disclosure; source-native matching remains unqualified |
| V14 | Public fault tests: publication-write failure, cancellation and expired common deadline leave no new Artifact; corrupt assignment file rejects; R7.2 covers atomic deadline boundaries and postcommit durable success |
| V15 | Public Duration/Completed/mean subprocess recovery after source deletion poisons source binding and matching; fixed status/read/selection/reduction/rollup use retained parts. Six private table/Parquet policy cases also compare source and artifact_python assignment |
| Binding | Exact steps/role, cross-Session and fixed+source match reject; a fixed AnalysisDomain has no source observe continuation; no business read is allowed during rejection |
| Disclosure | Public export snapshots, native Help signature/registry budgets, dynamic result contracts, executable identical English/Chinese site example and migration ledger |

## Initial R7.3 validation

- `make check-agent`: passed, including lint/import contracts, typing of 433
  modules, 5395 default tests passed, 5 skipped, and API documentation generation.
- Targeted matcher/disclosure/example checks: 30 passed. The exhaustive matcher
  oracle accounts for six policy/coverage cases, each enumerating 32 event streams
  and four patterns independently of the production scan.
- `make runtime-test TESTS='tests/test_analysis_domain_preparation_r72.py tests/test_analysis_journey_matching_r73.py --tb=short'`:
  **154 passed in 364.51 seconds**, with two workers. This clean final invocation
  covers 151 preparation/retention regressions and three public end-to-end cases:
  native table, Parquet, and Parquet with string Subject/occurrence identities.
  Public cases execute the site example, all six Duration relations, mixed-field
  every-start cohort, source/fixed means, empty mean, same-Run dropout observation,
  failure/cancellation/deadline, wrong binding, tamper and cold recovery.
- `npm --prefix site run build`: passed, 321 pages, English/Chinese indexes and
  both install script outputs verified. `git diff --check`: passed.
- After the broad gate, stale legacy Event acquisition/backend Help text was
  corrected to distinguish retained legacy consumers from new JourneyResult.
  Export/Help/drift regression: 57 passed; focused resolution, unified Help,
  disclosure journeys and DSL disclosure checks: 62 passed. Focused lint/import
  checks and final `git diff --check` also passed. No final gate remains failed.

Earlier failures below are retained as debugging history and must not be
substituted for a current passing gate.

## Failures repaired during implementation

- The first public broad gate exposed stale six-type exports, Help parameter order
  and an unmigrated legacy relationship fixture. Exports/signature snapshots were
  aligned; that fixture explicitly tests the retained private legacy producer.
- A later broad gate found the bilingual workflow example count changed from 14
  to 15. The independent example snapshot was updated; both language blocks must
  still be byte-identical and the new example is executed in public Runtime tests.
- R7.2 regression initially had 33 failures (119 passed): raw SourceLeaf envelopes
  were mistakenly passed to the MethodNode result adapter. The adapter now only
  wraps MethodNodes; the original envelope remains prepared tabular input.
- A composed status/Duration cohort initially hit the arithmetic-only homogeneous
  Duration check. Transport now validates its unchanged receiver/target carrier
  before arithmetic rules; Journey cohort qualification covers all bound predicate
  leaves. Public tests select Subject 1 using two distinct fields over every-start
  full opportunities. String population carriers also have explicit preparation,
  observation and cohort qualification and a public Parquet regression.
- An intermediate cold-process check failed while retained implementation owner
  identifiers were being aligned. Final evidence uses a stable tree and regenerated
  Artifacts; intermediate Artifacts are not migration inputs.
- An earlier Runtime invocation had 151 passing R7.2 tests but two collection
  errors from the new policy fixture. The clean 154-pass combined run above
  supersedes that failed invocation; it is not treated as a passing command.
- Intermediate fixture-only failures (keyword-only EveryStart construction,
  Parquet column declarations and registry freezing), formatting failures and
  the attempted fixed-domain observe call were corrected. Fixed domains expose
  no source observe method; explicit mixed matching is checked before reads.

## Excluded and unqualified work

- R7.4 funnel/compare/attribute, Lifecycle, Anchor, R7.5–R7.9 and remote backends
  remain excluded. This change does not grant full R7 or installed same-wheel
  qualification. No release gate, object-storage service or MinIO was run.
- M02/M04 are partially migrated: only R7.3 public consumers moved. Legacy private
  Event producers/codecs and regression fixtures remain for later-phase consumers;
  they are not aliases or alternatives to the canonical new public entry.
- Packaged skills were inspected and remain unchanged. Their general workflow
  boundaries still apply; no skill modification or additional authorization was
  required. AGENTS.md is unchanged. No commit, push or publication was performed.

## Adversarial review repairs

- Fixed the public Metric-selected population counterexample: member projections
  carry no scalar column, so occurrence preparation specializes the inherited
  scalar marker independently of complete Subject identity. The original member
  envelope in prepared observation follows the same rule. Inherited source checks
  remain registered and executed; they are not stripped from the graph.
- Extended the three public Runtime cases with a float Metric projection yielding
  Subjects 1/2/3, Journey matching yielding complete/censored rows for Subjects 1/2,
  and subsequent Duration selection/new Metric observation yielding Subject 1 with
  value 10. Source-read spies still prohibit reads after local matching.
- Corrected guidance for prepared observations and source Duration row statistics:
  their logical contract exposes execute only and explains the materialization
  boundary. Fixed contracts retain rollup; regression cases validate empty Metric
  state, a nonempty Metric total of 100, and the original Journey mean 140/3 after
  grouped Duration state merge. Native Help and both site languages match.
- This is a disclosure-boundary repair, not additional qualification for logical
  rollup/where after prepared observation or source Duration state merge.
- During repair, the first Runtime run exposed inherited-check registration gaps;
  those were fixed. A later test expectation incorrectly assumed revenue in an
  empty half-open window; it now asserts the existing Null empty-contribution
  policy and separately verifies nonempty retained-state rollup.

### Post-review validation

- `make check-agent`: **5399 passed, 5 skipped**, plus lint/import contracts,
  typing of 433 source modules and API documentation generation.
- Targeted matching/disclosure/examples: **83 passed**; targeted typing and lint
  also passed before the broad gate.
- Final public Journey Runtime matrix: **3 passed** (native table, Parquet,
  string-identity Parquet), including the new complete selection/observation chain.
- Final shared R7.2 Runtime regression: **151 passed** after the prepared-member
  envelope repair. Together the two final Runtime commands cover 154 cases.
- `npm --prefix site run build`: **321 pages**, including API generation,
  Astro checks/build and install-script verification. `git diff --check` passed.
- The independently reproduced chained population/observation probe returns the
  expected Subject 1 and revenue 10. No release/same-wheel/remote qualification was
  attempted; packaged skills and AGENTS.md remain untouched. No commit or publish.
