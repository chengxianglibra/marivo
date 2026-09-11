# Lazy Analysis Public Cutover Plan

Date: 2026-09-01

Revised: 2026-09-10

Status: Slices 0-4, 5a, 5c, 5d, 6a-6e and 7a-7b complete; Slice 5b implemented, technical acceptance pending

Slice 5b implementation and the supplied review follow-up are recorded under its
[private execution record](../plans/2026-09-08-lazy-analysis-slice-5b-execution.md).
The owner authorized a scoped commit using the revised daily-development
workflow. The later acceptance records below close Slice 4d and 5a; no complete
5b technical acceptance is claimed until its independent gate completes.

Slice 5c's private implementation and technical gate are complete under its
[execution record](../plans/2026-09-09-lazy-analysis-slice-5c-execution.md).
This does not close 5b or parent Slice 5, or activate a public surface.

## Outcome

Replace the eager `marivo.analysis` Frame, Result, detached-selection, and
Session-owned downstream-operation surface with the accepted immutable, typed,
lazy Dataset algebra in one breaking public cutover.

Implementation may land as independently reviewed internal slices before the
public switch. During that work, the current eager surface remains the only
public surface. New Dataset implementations must remain private and must not be
exported, registered in public Help, documented as available, or selected by a
public feature flag until the atomic public-cutover slice.

The released cutover has exactly one product state:

```text
Session source
  -> Logical Dataset transformations
  -> LogicalDataset.execute()
  -> paired Materialized Dataset
  -> show() | to_pandas() | downstream Dataset transformation
```

It leaves no public eager/lazy overload, Frame/Dataset adapter, deprecated
Session alias, migration decoder, dual-read Store, or fallback to an obsolete
Artifact generation.

This document is an implementation and disclosure plan. It does not redefine
Dataset semantics, Population meaning, operator algorithms, physical planning,
materialization authority, Event/Lifecycle behavior, or Evidence meaning. When
implementation discovers an unresolved product contract, the owning design
must be amended and accepted before this plan or a code slice proceeds.

### Observation-model amendment consumed by this plan

The 2026-09-05 amendment changes design targets only. Population is the sole
membership family; Event/Lifecycle selections produce it under domain-owned
completeness checks. Metric observation owns its own time window, supports
explicit analysis-Entity mapping across safe component roots, and consumes
registered unique identity-bearing shapes even with dependent coordinates.
Row filtering preserves exact contribution keys and selected denominators.
Basic ratio/mean/weighted-mean parts are required; time removal is an exact
registered rollup fold. Slices 2, 3, 7, and 8 below include those contracts,
acceptance fixtures, and the reduced 100-symbol target export manifest.

### Execution amendment consumed by this plan

The 2026-09-07 amendment changes design targets only. Before data work, the
compiler retains contiguous eligible Ibis source operators using tested
method/adapter support facts. At the first absent or ineligible source lowerer,
an exact registered pandas method starts a local continuation; all dependent
successors remain local. Source-required semantic or identity work without a
legal local method fails. Independent source branches may feed explicitly
admitted local input roles. Compilation or execution errors never select or
retry a different implementation. There is no internal DuckDB executor;
DuckDB remains an ordinary datasource. Local/object Parquet uses authorized
PyArrow readers under complete local-input guards. Arrow crosses source/storage
boundaries; private DataFrames pass directly between local steps. Slices 4-7 and
the acceptance matrix below consume this contract without changing the public
Dataset action, ownership, reuse or atomic-publication boundaries.

## Authority Boundary

### Semantic and analytical-correctness amendment consumed by this plan

The accepted 2026-09-07 amendment changes design targets only. Entity
`primary_key` identifies the stable instance; snapshot/validity fields identify
historical representations. Version-row and temporally resolved identity
uniqueness are distinct. The Observation Model requires explicit scoped
membership for a versioned Population and preserves independent downstream time
anchors. No `business_key`, physical-key author parameter, implicit latest
selection, or legacy key-subtraction path is added.

The same amendment closes per-axis contribution/fold admission, comparison and
screening scope keys, component-mix partition requirements, per-model forecast
uncertainty, and canonical Lifecycle legal-transition/subject-coverage/violation
parts. Selected-step time-to-event status and completed clipped dwell duration
remain descriptive contracts. The owning designs specify exact schemas,
algorithms and repairs; this plan maps their changes to Slices 1-8 and terminal
acceptance rather than repeating those definitions.

The recorded Slice 0 inventory remains historical evidence of the eager
baseline. The amended scopes below must be rechecked against the working tree
when their code slices begin. Target semantic normalizers and fixtures may be
implemented privately in Slices 2-3; the public semantic authoring/validation
switch occurs with Slice 8. No public loader accepts both identity-key and
legacy version-inclusive-key interpretations during or after the cutover.

This plan owns:

- the exact current-to-target public-surface replacement and removal ledger;
- code ownership for implementation slices;
- the order in which private implementation slices land;
- the one atomic public export, Help, documentation, skill, and persistence
  switch;
- deletion of eager implementations, obsolete tests, aliases, decoders, and
  migration paths;
- the validation matrix for each slice;
- end-to-end real-Agent acceptance and final release gates.

This plan does not own:

- common Dataset types, row and row-set contracts, state, selectors, or actions;
- Population inference, predicates, coordinate algebra, or aggregation meaning;
- semantic graph nodes, Ibis lowering, stage formation, or boundary capability
  meaning;
- Run, storage, Artifact, Evidence, binding, writer-lock, cleanup, or recovery
  semantics;
- typed Metric operator inputs, outputs, numerical definitions, or statuses;
- subject selection into Population, Event, Lifecycle, identity, completeness, or privacy semantics.

## Frozen Contract Inputs

The implementation consumes these accepted designs without copying their
detailed signatures, schemas, state machines, or algorithms:

| Contract area | Sole authority | Cutover use |
| --- | --- | --- |
| Product-wide invariants and clean replacement | `2026-09-01-lazy-analysis-dataset-dsl-design.md` | final product shape and global acceptance |
| Entity identity/versioning and intrinsic Metric/path/time definitions | `docs/specs/semantic/semantic-object-model.md` | private semantic fixtures and atomic authoring/validation cutover |
| semantic static validation, source integrity and Analysis handoff | `docs/specs/semantic/loading-validation-introspection.md` | phase-correct checks without claiming runtime readiness |
| Dataset value, family, state, actions, row and row-set contracts, and selectors | `2026-09-01-lazy-analysis-dataset-core-design.md` | common implementation and exports |
| Population, filtering, observation, coordinates, and aggregation | `2026-09-01-lazy-analysis-observation-model-design.md` | source and Metric vertical slices |
| Semantic compilation, Ibis lowering, placement, and bounded exchange | `2026-09-01-lazy-analysis-planner-and-pushdown-design.md` | compiler and execution-boundary slices |
| Run, storage, Artifact, Evidence, execution-key Artifact lookup, and recovery | `2026-09-01-lazy-analysis-materialization-runtime-design.md` | runtime and persistence slices |
| closed Artifact/Run/Finding read records and Finding coordinates | [Session Runtime Read design](2026-08-30-session-runtime-read-surface-and-graph-design.md) | scoped Finding publication, serialization, and cold-read validation |
| `correlate`, `rank`, `limit`, `rollup`, `compare`, `attribute`, `forecast`, and discovery | `2026-09-01-lazy-analysis-typed-operators-design.md` | typed operator slices |
| subject selection into Population, Event, Lifecycle, and cross-domain population loops | `2026-09-01-lazy-analysis-subject-event-lifecycle-design.md` | domain slices |
| Repository-wide public-surface rules | `AGENTS.md` | tests, Help, typing, docs, and release gates |

The current implemented analysis specs remain sources for the eager inventory
only. The cutover must update them to the accepted lazy contract in the public
switch; they cannot preserve an eager behavior that the accepted lazy designs
remove.

## Entry Gate Audit

### Satisfied design dependencies

The north-star and all six module designs exist and are marked accepted. They
agree on these load-bearing seams:

1. every public row-bearing analysis value is a Dataset;
2. every downstream operator accepts registered Logical and Materialized input
   states and returns a Logical Dataset;
3. only `execute()` can cross from logical definition to committed materialized
   authority;
4. a materialized input is an immutable scan leaf and its origin is not
   executable lineage;
5. source construction remains Session-owned and downstream analysis becomes
   Dataset-owned;
6. complete row and row-set contracts exist before execution;
7. one successful exact execution binds once per Session;
8. private Arrow transfers, pandas DataFrames, numerical buffers and Runtime
   staging never become public Dataset authority;
9. the public cutover removes compatibility paths instead of maintaining a
   second algebra.

### Resolved owner clarifications

On 2026-09-04 the owner resolved all three entry-gate questions and the owning
designs were amended before this plan:

| Capability | Accepted decision | Owning contract |
| --- | --- | --- |
| Parameterized source bindings | Retain `Session.source_bindings(...)` as an authoring-time scope. Every logical source captures its exact non-secret bindings; their digest enters definition/execution identity, `execute()` performs no ambient lookup, and raw values never persist or render. | Observation Model, Compiler, Materialization Runtime |
| Event occurrence range inspection | Remove `session.events.occurrence_bounds(...)`, `EventOccurrenceBounds`, and their Help/test/doc surface with no first-cutover replacement. Event/Lifecycle windows stay explicit and completeness remains separately governed. | Subject/Event/Lifecycle |
| Materialized time-grain rollup | Replace `frame.transform.rollup(...)` with registered `MetricDataset.rollup(drop_dimensions=..., grain=..., drop_time=...)`. It folds current rows or exact retained sufficient state on Entity-reduced Metric shapes and never recomputes an origin graph. | Observation Model, Typed Operators |

The original owner-design clarification gate is satisfied. Implementation
consumes these decisions exactly; it must not infer alternate behavior from
current eager code. Slice 0 later surfaced persistence-layout, target-export,
terminal-read, and completeness-construction gaps. The owner accepted their
recommended contracts on 2026-09-04, and the owning designs were amended before
Slice 0 closeout.

## Global Cutover Invariants

The following rules apply to every slice:

1. New implementation may coexist with eager code only as private, unexported,
   unreleased infrastructure.
2. No public `lazy=`, `eager=`, compatibility mode, environment switch, feature
   flag, or alternate import path is introduced.
3. No type is simultaneously a Frame and a Dataset, and no adapter admits a
   Frame, pandas value, Ibis expression, SQL value, or old Artifact into the
   Dataset algebra.
4. No Session-owned downstream operator remains after the public switch.
5. Every public Dataset method has one capability registration and one exact
   focused Help target.
6. Every public type has a concrete annotation, one export, and one focused Help
   leaf; private graph, receipt, and staging types never appear in public
   annotations.
7. Every producing family registers its row contract, row-set contract, validation, quality,
   Evidence, Finding, retained-state, and materialization contracts before an
   execution Run can be admitted.
8. Every source or operator constructs its complete logical row and row-set
   contracts before datasource access.
9. No row-dependent fact is promoted from preview, local process state, or
   generated SQL into durable authority.
10. Registered support determines source work and exact local continuations
    before data work. Compilation or execution errors never trigger local retry,
    unregistered calculation, implicit sampling, truncation or semantic fallback.
11. Old Session Store and Artifact generations fail closed; they are not
    migrated, imported, adapted, or read through a compatibility decoder.
12. Each slice owns a non-overlapping code area or names the exact shared seam it
    changes. It must preserve unrelated work.
13. A slice is complete only when its static contracts, behavioral tests,
    failure paths, Help/disclosure obligations, and terminal runtime evidence
    pass.
14. Green unit tests, generated SQL, a successful backend query, process health,
    or a transcript alone are never runtime acceptance.
15. Parameterized source values are captured at logical source construction,
    participate in exact execution identity, and never become action-time
    mutable Session state or persisted cleartext.
16. Once a computation becomes local, all dependent successors remain local;
    source upload and an internal DuckDB executor are absent. Complete local
    inputs, retained parts, intermediate memory and deadlines are guarded
    independently of storage streaming capacity.

## Success Definition

The cutover is successful when an agent can:

1. construct a deep logical analysis chain without opening a datasource,
   creating a Run, or materializing rows;
2. inspect the complete row meaning and mechanically valid continuations before
   execution;
3. execute an eligible same-domain relational chain as one Ibis/backend query,
   or retain its eligible source prefix and execute its admitted suffix in pandas;
4. receive one same-family Materialized Dataset only after storage, validation,
   quality, Evidence, Findings, Artifact metadata, and Run success are committed;
5. reconstruct the same logical definition in a fresh process and recover the
   same Session-bound Artifact without a new Run or datasource statement;
6. use an explicit materialized checkpoint in several downstream chains without
   replaying its origin;
7. connect Metric, Event, and Lifecycle work through governed identity-bearing
   Dataset inputs without collecting identities into Python;
8. receive a typed terminal failure with no partial public authority whenever
   semantic admission, compilation, execution, cleanup, validation, Evidence,
   or publication fails;
9. resolve every advertised public path through live Help and run every current
   English and Chinese example against the installed package;
10. find no eager alias, old Frame/Result type, obsolete Help route, old-schema
    decoder, or dual-read path in the released package.

## Current Surface Inventory

The inventory baseline is the `0.5.3.dev0` local surface at
`d3af482c02d4ff1f5a986c7e23c505a6bfbd093d`, inspected on 2026-09-04. The
worktree was already dirty with design-document work, so the commit id records
the code baseline while the inventory-document changes remain uncommitted.

The existing scenario-owned suites freeze the baseline without a Slice-specific
test module: `tests/test_public_surface.py` owns ordered exports,
`tests/test_analysis_capability_registry.py` owns the complete kind-specific
capability projection, and `tests/test_analysis_help.py` owns ordered
Help-target topology. The capability projection includes id, kind, public
entrypoint, callable path, Help target, additional examples, accepted or
receiver input families, and every kind-specific output contract. It is
normalized structurally, including same-as-input output families; it does not
use a dataclass `repr` as authority.

| Surface | Count | Canonical SHA-256 |
| --- | ---: | --- |
| ordered `marivo.analysis.__all__` | 82 | `8acf27f434f7b02056e5ebe7af986b7908af2433687651f8e2d2b2584f7e8af1` |
| ordered capability contracts | 128 | `53f9ec9933c4a477489db76e78b9e6d67052c825136044953f241f8b30ca4ac9` |
| ordered Help targets | 169 | `781fd9757300b03f7e1c3d480a49d4080b0db7fe1ce76743b2412d238a8dcf38` |

The registry contains 34 operators, 51 reads, 31 constructors, 11 recovery
capabilities, and one boundary capability. The 169 Help targets consist of the
128 capability leaves plus 41 type or navigation targets.

### Exact current export classification

Every one of the 82 current exports is classified below. `Retain` means retain
the public spelling; it does not permit reuse of an eager implementation or old
persistence schema.

| Action | Count | Exact current exports |
| --- | ---: | --- |
| retain spelling, rebind where required | 36 | `ArtifactDigest`, `ArtifactRef`, `ArtifactRevalidation`, `ArtifactSummary`, `DroppedBefore`, `EventPattern`, `EveryStart`, `EvidenceIntegrityError`, `FailedRun`, `Finding`, `FindingPage`, `FirstPerSubject`, `FromInception`, `FunnelLossRate`, `Grain`, `InState`, `IncompleteRun`, `PatternStep`, `RunPage`, `Session`, `SessionGraph`, `SucceededRun`, `TimeScope`, `dropped_before`, `every_start`, `first_per_subject`, `from_inception`, `funnel_loss_rate`, `grain`, `in_state`, `runtime_metric`, `sequence`, `session`, `step`, `time_scope`, `window_bucket` |
| replace with named target | 10 | `AlignmentPolicy`, `AssociationResult`, `AttributionFrame`, `CandidateSet`, `DeltaFrame`, `EventFrame`, `ForecastFrame`, `LifecycleFrame`, `MetricFrame`, `SubjectSet` |
| remove without compatibility spelling | 36 | `AbsoluteWindow`, `AnalysisScope`, `AnomalyCandidate`, `ArtifactIssue`, `AssociationFact`, `CandidateOrigin`, `CandidateResolutionIssue`, `CandidateSelection`, `ChangeFact`, `ComparabilityIssue`, `CompletenessDeclaration`, `ContributionFact`, `CrossSectionalOutlierSelection`, `DataQualityIssue`, `DriverAxisSelection`, `EventOccurrenceBounds`, `EventWatermarkReceipt`, `EventWatermarkRequest`, `EvidenceAvailabilityIssue`, `EvidenceRuleIssue`, `ForecastOutput`, `HypothesisTestResult`, `ObservationFact`, `OntologyMetricCandidate`, `PeriodShiftSelection`, `PointAnomalySelection`, `QualityCheckResult`, `SliceSelection`, `TestDecision`, `WindowSelection`, `day_of_week`, `declared_complete_through`, `occurrence_progress`, `period_correspondence`, `period_progress`, `working_day_progress` |

The ten replacements are exact: `AlignmentPolicy` becomes
`WindowBucketAlignment`; the nine row-bearing names become their paired
`Logical*Dataset` and `Materialized*Dataset` classes, including
`LogicalPopulationDataset` and `MaterializedPopulationDataset` for the former
`SubjectSet` selection output; no separate SubjectSet family remains. The 36 removals have no
accepted target consumer. Retaining any removed Evidence item or Candidate
selection type requires an owner-design amendment; existing export alone is not
authority.

### Exact current capability classification

The following groups are exhaustive and non-overlapping; their counts total
128:

| Target action | Count | Exact current capability ids |
| --- | ---: | --- |
| same path, contract rewrite | 4 | `Session.source_bindings`, `events.match`, `lifecycle.replay`, `observe` |
| move from Session namespace to Dataset receiver | 15 | `attribute`, `compare`, `correlate`, `forecast`, `select_subjects`, `events.funnel`, `events.time_to_event`, `lifecycle.distribution`, `lifecycle.dwell`, `lifecycle.transitions`, `lifecycle.violations`, `discover.point_anomalies`, `discover.period_shifts`, `discover.driver_axes`, `discover.interesting_windows` |
| rename and move | 1 | `discover.cross_sectional_outliers` -> `dataset.discover.entity_outliers` |
| replace transform or projection | 7 | `MetricFrame.metric`, `transform.filter`, `transform.slice`, `transform.rollup`, `transform.topk`, `transform.bottomk`, `transform.rank` |
| replace common Frame action/boundary | 3 | `BaseFrame.show`, `BaseFrame.contract`, `boundary.to_pandas` |
| retain Session display spelling | 2 | `Session.render`, `Session.show` |
| retain constructor spelling | 16 | `grain`, `funnel_loss_rate`, `step`, `sequence`, `first_per_subject`, `every_start`, `dropped_before`, `from_inception`, `in_state`, `window_bucket`, `time_scope`, `runtime_metric.aggregate`, `runtime_metric.slice`, `runtime_metric.weighted_mean`, `runtime_metric.ratio`, `runtime_metric.linear` |
| replace constructor | 2 | `SamplingPolicy`, `declared_complete_through` |
| remove without replacement | 44 | `AbsoluteWindow`, `AttributionMode`, `SemanticShape`, `PointAnomalyStrategy`, `RankMethod`, `NormalizeKind`, `NormalizeBaseline`, `hypothesis_test`, `discover.interesting_slices`, `discover.semantic_hypotheses`, `transform.window`, `transform.normalize`, `MetricFrame.components`, `MetricFrame.coverage`, `DeltaFrame.components`, `CandidateSet.select`, `AttributionFrame.at_resolution`, `MetricFrame.as_scalar`, `MetricFrame.as_time_series`, `MetricFrame.as_segmented`, `MetricFrame.as_panel`, `DeltaFrame.as_scalar`, `DeltaFrame.as_time_series`, `DeltaFrame.as_segmented`, `DeltaFrame.as_panel`, `AttributionFrame.as_sum`, `AttributionFrame.as_ratio_mix`, `AttributionFrame.as_weighted_mix`, `CandidateSet.as_point_anomaly`, `CandidateSet.as_period_shift`, `CandidateSet.as_driver_axis`, `CandidateSet.as_slice`, `CandidateSet.as_window`, `CandidateSet.as_cross_sectional_outlier`, `CandidateSet.as_semantic_hypothesis`, `DeltaFrame.predicted_attribution_shape`, `events.watermark`, `events.occurrence_bounds`, `day_of_week`, `period_progress`, `period_correspondence`, `occurrence_progress`, `working_day_progress`, `session.delete` |
| retain runtime/read spelling and rebind persistence | 13 | `session.abandon_run`, `session.get_or_create`, `session.current`, `session.resume`, `session.recent`, `session.inspect`, `session.runs`, `session.get_run`, `session.artifact`, `session.graph`, `session.revalidate`, `artifact.findings`, `artifact.finding` |
| retain catalog read | 21 | `catalog.domains`, `catalog.datasources`, `catalog.entities`, `catalog.dimensions`, `catalog.time_dimensions`, `catalog.measures`, `catalog.metrics`, `catalog.relationships`, `catalog.events`, `catalog.state_models`, `catalog.period_calendars`, `catalog.temporal_sets`, `catalog.work_schedules`, `catalog.require`, `catalog.readiness`, `catalog.period_calendars.grain`, `catalog.period_calendars.period`, `catalog.period_calendars.period_on`, `catalog.period_calendars.periods`, `catalog.temporal_sets.occurrence`, `catalog.temporal_sets.occurrences` |

The 41 non-capability Help targets are also classified exactly:

- replace the 12 current object leaves: the nine target Dataset families replace
  `AssociationResult`, `AttributionFrame`, `CandidateSet`, `DeltaFrame`,
  `EventFrame`, `ForecastFrame`, `LifecycleFrame`, `MetricFrame`, and
  `SubjectSet`; remove `ComponentFrame`, `CoverageFrame`, and
  `HypothesisTestResult`;
- retain and rebuild the nine navigation ids `catalog`, `catalog.temporal`,
  `events`, `evidence`, `lifecycle`, `runtime`, `runtime.runs`,
  `runtime.sessions`, and `runtime_metric`;
- replace the 19 navigation ids `alignment`, `artifacts`,
  `artifacts.discovery_inference`, `artifacts.event_lifecycle`,
  `artifacts.metric_change`, `artifacts.quality_projection`,
  `artifacts.reading`, `discover`, `entry`, `entry.event_observations`,
  `inputs`, `inputs.events`, `inputs.operator_options`, `inputs.scope`,
  `inputs.subject_selection`, `inputs.transform_options`, `methods`,
  `methods.change`, and `methods.relationship_testing` from target owner
  registrations;
- remove the `transform` navigation id.

### Current row-bearing value families

| Current family | Target family | Cutover action |
| --- | --- | --- |
| `BaseFrame` | `Dataset`, `LogicalDataset`, and `MaterializedDataset` common contracts | Replace; remove Frame base and DataFrame-like common protocol. |
| `MetricFrame` | `LogicalMetricDataset` / `MaterializedMetricDataset` | Replace. |
| `DeltaFrame` | `LogicalDeltaDataset` / `MaterializedDeltaDataset` | Replace. |
| `AttributionFrame` | `LogicalAttributionDataset` / `MaterializedAttributionDataset` | Replace. |
| `AssociationResult` | `LogicalAssociationDataset` / `MaterializedAssociationDataset` | Replace detached Result with Dataset. |
| `ForecastFrame` | `LogicalForecastDataset` / `MaterializedForecastDataset` | Replace. |
| `CandidateSet` | `LogicalCandidateDataset` / `MaterializedCandidateDataset` | Replace. |
| `EventFrame` | `LogicalEventDataset` / `MaterializedEventDataset` | Replace. |
| `LifecycleFrame` | `LogicalLifecycleDataset` / `MaterializedLifecycleDataset` | Replace. |
| `SubjectSet` | `LogicalPopulationDataset` / `MaterializedPopulationDataset` | Remove the separate membership family; domain selections produce the common Population contract. |
| `ComponentFrame` | no first-cutover Dataset family | Remove unless an owning design is amended before plan acceptance. Component semantics remain internal family contracts. |
| `CoverageFrame` | no first-cutover Dataset family | Remove. Coverage becomes family/runtime authority and Evidence, not a detached row-bearing side result. |
| `HypothesisTestResult` | no first-cutover family | Remove; no generic statistical test exists in the first cutover. |

### Current source and downstream operation replacement

| Current canonical path | Target canonical path | Action |
| --- | --- | --- |
| `Session.source_bindings(...)` dynamic execution scope | `Session.source_bindings(...)` logical-source authoring scope | Retain the public spelling; replace semantics with immutable per-source capture, exact identity digest, and no action-time ambient lookup. |
| `session.observe(...)` | `session.observe(...) -> LogicalMetricDataset` | Replace eager execution with lazy source construction. |
| no public Population source | `session.population(...)` | Add at public switch. |
| `session.events.match(...)` | `session.events.match(...) -> LogicalEventDataset` | Retain source owner; replace eager behavior and signature. |
| `session.lifecycle.replay(...)` | `session.lifecycle.replay(...) -> LogicalLifecycleDataset` | Retain source owner; replace eager behavior and signature. |
| `session.compare(current, baseline, ...)` | `current.compare(baseline, ...)` | Move to Dataset; remove Session path. |
| `session.attribute(frame, ...)` | `delta.attribute(...)` | Move to Dataset; remove Session path. |
| `session.correlate(a, b, ...)` | `metrics.correlate(...)` | Replace with the accepted multi-Metric Dataset receiver contract. |
| `session.forecast(history, ...)` | `history.forecast(...)` | Move to Dataset; remove Session path. |
| `session.select_subjects(artifact, selection=...)` | Event journey or Lifecycle history `.select_subjects(selection)` | Move to exact Dataset receivers; remove Session path. |
| `session.events.funnel(journeys, ...)` | `journeys.funnel(...)` | Move reducer to Dataset. |
| `session.events.time_to_event(journeys, ...)` | `journeys.time_to_event(...)` | Move reducer to Dataset. |
| `session.lifecycle.distribution(history, ...)` | `history.distribution(...)` | Move reducer to Dataset. |
| `session.lifecycle.transitions(history)` | `history.transitions()` | Move reducer to Dataset. |
| `session.lifecycle.dwell(history)` | `history.dwell()` | Move reducer to Dataset. |
| `session.lifecycle.violations(history)` | `history.violations()` | Move reducer to Dataset. |
| `session.events.occurrence_bounds(...)` and `EventOccurrenceBounds` | none | Remove without replacement; observed row bounds are neither window nor completeness authority. |
| `frame.transform.filter(...)` | `dataset.where(...)` | Replace with shared typed predicate algebra. |
| `frame.transform.slice(...)` | `dataset.where(...)` | Replace keyword/mapping slice shape with typed predicates; no alias. |
| `frame.transform.topk(...)` | `dataset.rank(...).limit(...)` | Replace as two explicit semantics; no alias. |
| `frame.transform.bottomk(...)` | descending/ascending `dataset.rank(...).limit(...)` as admitted by the exact operator contract | Replace; no alias. |
| `frame.transform.rank(...)` | `dataset.rank(...)` | Replace namespace path and selector contract. |
| `frame.transform.window(...)` | no first-cutover generic transform | Remove; time scope and Event/Lifecycle windows remain on their natural sources/contracts. |
| `frame.transform.normalize(...)` | no first-cutover generic transform | Remove. Governed reusable calculations remain Metrics; no generic replacement is added. |
| `frame.transform.rollup(...)` | `metric_dataset.rollup(drop_dimensions=..., grain=..., drop_time=...)` | Replace with the registered current-row/retained-state fold; no `frame.transform` or `drop_axes` alias. |
| `frame.metric(...)` | `metric_dataset.metric(metric_input)` | Replace string selection with exact Metric identity/field contract. |
| `frame.components()` | no detached first-cutover read | Remove; component authority remains in Metric/operator contracts. |
| `frame.coverage()` | no detached first-cutover read | Remove; expose exact coverage through Dataset contract, committed Evidence, and family status. |

### Exact current object and member inventory

The current row-bearing base is `BaseFrame`. Its complete public member set is
`columns`, `contract`, `evidence_digest`, `evidence_status`, `finding`,
`finding_count`, `findings`, `kind`, `lineage`, `quality_summary`, `ref`,
`render`, `row_count`, `shape`, `show`, `state`, and `to_pandas`. Every current
row-bearing family inherits that set. The family-owned additions are:

| Current family | Exact additional public members |
| --- | --- |
| `MetricFrame` | `VALUE_COLUMN`, `semantic_shape`, `measures_meta`, `metrics`, `value_columns`, `time_dimension_columns`, `arity`, `as_scalar`, `as_time_series`, `as_segmented`, `as_panel`, `components`, `coverage`, `transform`, `metric` |
| `DeltaFrame` | `semantic_shape`, `as_scalar`, `as_time_series`, `as_segmented`, `as_panel`, `predicted_attribution_shape`, `transform`, `components` |
| `AttributionFrame` | `attribution_shape`, `attribution_mode`, `as_sum`, `as_ratio_mix`, `as_weighted_mix`, `at_resolution` |
| `CandidateSet` | `as_point_anomaly`, `as_period_shift`, `as_driver_axis`, `as_slice`, `as_window`, `as_cross_sectional_outlier`, `as_semantic_hypothesis`, `select` |
| `EventFrame` | `semantic_shape` |
| `LifecycleFrame` | `semantic_shape` |
| `AssociationResult`, `ForecastFrame`, `SubjectSet`, `ComponentFrame`, `CoverageFrame`, `HypothesisTestResult` | no family-owned public member beyond their inherited result/base contract |

`MetricFrame.transform` and `DeltaFrame.transform` expose `filter`, `slice`,
`rollup`, `topk`, `bottomk`, `rank`, and `window`; the Metric variant also
exposes `normalize`. The current Candidate selection union contains exactly
`PointAnomalySelection`, `PeriodShiftSelection`, `DriverAxisSelection`,
`SliceSelection`, `WindowSelection`, and `CrossSectionalOutlierSelection`.

The current `Session` public property/method set is exactly `artifact`,
`attribute`, `catalog`, `close`, `compare`, `correlate`, `created_at`, `cwd`,
`discover`, `events`, `forecast`, `get_run`, `graph`, `hypothesis_test`, `id`,
`is_read_only`, `lifecycle`, `name`, `observe`, `project_root`, `question`,
`render`, `report_tz`, `report_tz_name`, `report_tz_resolution`,
`report_tz_warning`, `revalidate`, `runs`, `select_subjects`, `show`,
`source_bindings`, `tz`, and `updated_at`. Its namespaces expose:

| Current namespace | Exact public operations |
| --- | --- |
| `session.events` | `match`, `funnel`, `time_to_event`, `watermark`, `occurrence_bounds`, `render`, `show` |
| `session.lifecycle` | `replay`, `distribution`, `transitions`, `dwell`, `violations`, `render`, `show` |
| `session.discover` | `point_anomalies`, `period_shifts`, `driver_axes`, `interesting_slices`, `interesting_windows`, `cross_sectional_outliers`, `semantic_hypotheses` |
| `mv.session` module | `abandon_run`, `current`, `delete`, `get_or_create`, `inspect`, `recent`, `resume` |

The capability and replacement tables above classify every callable in this
member inventory. The internal `SessionRuntimeReads` facade backs the direct
`Session.artifact`, `get_run`, `graph`, `revalidate`, and `runs` methods plus
the private recap used by Session rendering; it is not a public namespace.
Properties without a capability descriptor are still frozen here so Slice 8
cannot accidentally preserve a Frame-era convenience merely because it was
absent from the registry.

### Current discovery replacement

| Current path | Target path | Action |
| --- | --- | --- |
| `session.discover.point_anomalies(...)` | `dataset.discover.point_anomalies(...)` | Move to Dataset. |
| `session.discover.period_shifts(...)` | `dataset.discover.period_shifts(...)` | Move to Dataset. |
| `session.discover.driver_axes(...)` | `dataset.discover.driver_axes(...)` | Move to Dataset. |
| `session.discover.interesting_windows(...)` | `dataset.discover.interesting_windows(...)` | Move to Dataset. |
| `session.discover.cross_sectional_outliers(...)` | `dataset.discover.entity_outliers(...)` | Rename and move; no alias. |
| `session.discover.interesting_slices(...)` | none | Remove. |
| `session.discover.semantic_hypotheses(...)` | none | Remove from analysis. Semantic/ontology discovery remains outside the typed statistical output surface. |

### Current detached read and cast replacement

| Current path or value | Target | Action |
| --- | --- | --- |
| `CandidateSet.select(...)` and `CandidateSelection` variants | filtered or limited one-row `CandidateDataset` | Remove detached selection protocol. |
| `MetricFrame.as_scalar/as_time_series/as_segmented/as_panel` | exact `dataset.row_contract.shape_id` | Remove cast-like reads. |
| `DeltaFrame.as_scalar/as_time_series/as_segmented/as_panel` | exact `dataset.row_contract.shape_id` | Remove cast-like reads. |
| `AttributionFrame.as_sum/as_ratio_mix/as_weighted_mix` | exact Attribution family contract/method identity | Remove cast-like reads. |
| `AttributionFrame.at_resolution(...)` | typed fields plus `where(...)` over the exact registered resolution representation | Remove detached projection read. |
| `CandidateSet.as_*` methods | qualified Candidate Dataset shape | Remove cast-like reads. |
| `DeltaFrame.predicted_attribution_shape` | `dataset.contract()` admission/output facts | Remove standalone predictor. |
| DataFrame-like `__getitem__`, iteration, `len`, realized `.shape`, and arithmetic blockers | `dataset.fields`, Dataset operators, and terminal `to_pandas()` | Remove from common Dataset contract. |

### Statistical-test removal

The cutover removes all of the following together:

- `Session.hypothesis_test(...)`;
- `HypothesisTestResult`;
- `TestDecision` when it has no non-hypothesis public producer;
- `analysis.hypothesis_test` Help;
- relationship-testing Help text that advertises a generic test;
- operator registry entries, examples, docs, tests, Evidence types, and
  persistence decoders used only by the generic hypothesis path.

A future exact statistical test requires a separate accepted Delta-affordance
design. This plan reserves no namespace, placeholder class, empty registry, or
compatibility error for it.

### Event completeness replacement

The cutover removes the flat watermark/declaration surface:

- `session.events.watermark(...)`;
- `EventWatermarkRequest`;
- `EventWatermarkReceipt`;
- `declared_complete_through(...)` and its flat declaration representation.

It adds the accepted closed completeness union and exact focused Help for:

- `BoundedCompletenessDeclarationV1`;
- `SourceOriginCompletenessDeclarationV1`.

The internal `CompletenessDeclaration` union remains annotation shorthand only;
public signatures render the two concrete variants and the alias is not
exported or independently discoverable.

The Module 6 owner must supply the exact public construction path. The cutover
must not retain a watermark operation as an alias for a declaration or observed
coverage receipt.

The cutover also removes `session.events.occurrence_bounds(...)` and
`EventOccurrenceBounds`. This is not a completeness replacement: explicit
Event/Lifecycle windows remain authored inputs, while only the accepted
bounded/source-origin declarations may establish completeness. No Dataset
wrapper, immediate inspection query, compatibility error, or reserved Help leaf
survives.

### Runtime and audit reads retained conceptually

These are non-operator reads or Session management capabilities rather than
eager analysis alternatives. They remain only after their implementations and
return types are rebound to the new Store and Dataset generation:

- `mv.session.get_or_create`, `current`, `resume`, `recent`, and `inspect`;
- `session.runs(...)` and `session.get_run(...)`;
- `session.artifact(ref)` returning the exact recovered Materialized Dataset;
- `session.graph(...)` over committed Run/Artifact edges;
- `session.revalidate(...)` with exactly three independent axes: Artifact
  integrity, storage authority, and Evidence integrity; no current semantic or
  source comparison and no freshness/reuse verdict;
- `mv.session.abandon_run(...)` only through the same Session writer guard and
  recovery protocol, requiring registered backend terminal/fencing proof; caller
  confirmation alone cannot bypass recovery or rewrite committed success;
- Materialized Dataset Finding reads if the Evidence access design retains
  their exact `findings(...)` and `finding(...)` contracts.

The cutover replaces old Frame-oriented return annotations, metadata fields,
schema names, examples, and Help. It does not keep a read that requires decoding
an old Frame Artifact.

### Current persistence-generation inventory

Current Analysis persistence is not one Store. It is two SQLite generations,
Artifact files, an unversioned Session sidecar, and a dormant Job JSON helper:

| Current authority | Exact locator or schema | Tables or files | Writer, decoder, and recovery owners | Target action |
| --- | --- | --- | --- | --- |
| project Session Store | `.marivo/analysis/session_store.db`, `PRAGMA user_version = 2` | `sessions`, `runtime_state`, `artifacts`, `runs`, `run_inputs`; indexes `idx_sessions_updated_id`, `idx_runs_session_started`, `idx_run_inputs_artifact` | `session/_store.py`, `session/__init__.py`, `session/_runtime.py`, `session/_runs.py`, `session/_load.py`, `session/_runtime_reads.py`, `session/history.py`, `session/_resolve.py`, and the direct read in `frames/_component_contract.py` | Replace as a whole; never upgrade or dual-read. |
| per-Session Evidence Store | `.marivo/analysis/sessions/<session_id>/judgment.db`, `PRAGMA user_version = 4` | `artifacts`, `findings`, `artifact_digests`, `artifact_issues`; indexes `idx_artifacts_session_commit`, `idx_findings_session_commit`, `idx_findings_artifact`, `idx_digests_session_commit`, `idx_artifact_issues_artifact` | `evidence/store.py`, `evidence/pipeline.py`, `evidence/audit.py`, `evidence/artifact_reads.py`, `_artifact_integrity.py`, `_artifact_revalidation.py`, `session/_load.py`, `session/_runs.py`, `session/_runtime_reads.py`, `session/core.py` | Remove the independent database. Target Evidence and Findings share the Session Store publication transaction; no mutable/upsert Artifact row or commit marker survives. |
| Frame Artifact | `sessions/<id>/frames/<ref>/data.parquet`, `meta.json`, auxiliary Parquet; `analysis-artifact/v13` | Frame metadata and bytes | `session/_layout.py`, `session/_runtime.py`, `session/_load.py`, `session/_artifact_meta.py`, `evidence/pipeline.py`, all `frames/*Meta` | Remove writer, decoder, reuse, and fallback. Preserve old bytes without reading them as Dataset authority. |
| Run payload | `marivo.analysis_run/v2` | `runs.payload_schema` | `session/_store.py` and runtime readers | Replace with `marivo.analysis_action_run/v2`. |
| Job-to-Run adapter | `marivo.analysis_job/v2` | in-memory compatibility record; no authoritative Job Store generation | `session/_runtime.py:persist_job_record` and legacy success adapter | Remove the adapter, schema, names, and intent cleanup hooks. |
| Session sidecar | `sessions/<id>/meta.json`, unversioned | duplicated Session identity, report timezone, known datasources | `session/__init__.py:_activate_session`, `session/_runtime.py` | Remove the shadow file; move retained report-timezone facts into the target Session Store. |
| dormant Job JSON helper | `sessions/<id>/jobs/*.json`, unversioned | Job dicts | `session/_layout.py` | Remove helpers and directory. Production code no longer writes them. |
| dormant script directory | `sessions/<id>/scripts/` | none | `session/_layout.py` | Remove from the target layout. |
| Semantic temporal snapshots | `.marivo/temporal/period-calendars`, `.marivo/temporal/temporal-sets` | Semantic-owned snapshot payloads | `marivo/_temporal.py`; Analysis only consumes tokens | Retain outside the Analysis Store cutover; bind their exact tokens into Dataset authority. |

The old Run schema is `marivo.analysis_run/v2`, not v1. A v1 string appears
only as a corruption-test sample. Nested Metric, Delta, cumulative,
Attribution, Event, Lifecycle, Subject, semantic-ref, and lineage version
strings are payload contracts, not Store generations; their family owners must
map or delete them without using them for Store activation.

Current parameterized-source values also reach three durable locations through
old lineage projection: Frame `meta.json`, Evidence
`artifacts.lineage_payload`, and Session Store `runs.arguments_json`. The target
negative audit therefore scans every target SQLite text/blob value, Artifact
metadata, Evidence payload, Run envelope, graph, error, card, contract, and
telemetry projection for raw binding values.

### Supporting values and constructors

Every current exported non-row-bearing type or helper must be classified in the
Slice 0 machine-readable export snapshot. The default policy is:

- retain a value only when an accepted lazy signature, Dataset contract,
  runtime read, or Evidence contract consumes or produces it;
- replace it when an accepted design names a new closed value;
- remove it when its only producer or consumer is a removed eager capability;
- never retain a symbol solely because it is already exported.

At minimum the public switch must add the Dataset Core descriptor/export set,
the Observation predicate builders, Population and Metric families, Typed
Operator policy values, and Module 6 families/declarations frozen by their
owners. It must remove old selections, Frame metadata facades, and helper values
with no target consumer.

### Exact target public export manifest

The owner accepted the following ordered 100-symbol `marivo.analysis.__all__`
for the atomic Slice 8 switch. The ordering is part of the public-surface
snapshot: Dataset Core, paired family states, authoring values, terminal reads,
then constructors and namespaces.

```text
Dataset
LogicalDataset
MaterializedDataset
DatasetShapeId
DatasetFieldId
DatasetFieldIdentity
DatasetPhysicalTypeState
DatasetField
DatasetRowBound
DatasetCardinality
DatasetOrderTerm
DatasetOrdering
DatasetByteCount
DatasetFamilyRowSemantics
DatasetRowContract
DatasetRowSetContract
DatasetSchema
LogicalDatasetState
MaterializedDatasetState
DatasetContract
DatasetFields
DatasetFieldRef

LogicalPopulationDataset
MaterializedPopulationDataset
LogicalMetricDataset
MaterializedMetricDataset
LogicalDeltaDataset
MaterializedDeltaDataset
LogicalAttributionDataset
MaterializedAttributionDataset
LogicalAssociationDataset
MaterializedAssociationDataset
LogicalForecastDataset
MaterializedForecastDataset
LogicalCandidateDataset
MaterializedCandidateDataset
LogicalEventDataset
MaterializedEventDataset
LogicalLifecycleDataset
MaterializedLifecycleDataset

AnalysisPredicate
EntitySamplingPolicy
ForecastHorizon
ForecastModel
WindowBucketAlignment
BoundedCompletenessDeclarationV1
SourceOriginCompletenessDeclarationV1
DroppedBefore
EventPattern
EveryStart
FirstPerSubject
FromInception
FunnelLossRate
Grain
InState
PatternStep
TimeScope

ArtifactDigest
ArtifactRef
ArtifactRevalidation
ArtifactSummary
EvidenceIntegrityError
FailedRun
Finding
FindingPage
IncompleteRun
RunPage
SessionGraph
SucceededRun
Session

eq
not_eq
lt
lte
gt
gte
is_in
is_null
is_not_null
all_of
any_of
not_
engine_sample
grain
time_scope
window_bucket
step
sequence
first_per_subject
every_start
dropped_before
in_state
funnel_loss_rate
from_inception
periods
naive
drift
seasonal_naive
runtime_metric
session
```

There are no exported nominal aliases named `PopulationDataset`,
`MetricDataset`, `DeltaDataset`, `AttributionDataset`, `AssociationDataset`,
`ForecastDataset`, `CandidateDataset`, `EventDataset`, or `LifecycleDataset`. Those phrases are family shorthand only; public annotations use
the concrete Logical/Materialized classes directly. `ForecastModel` is the
sealed helper-produced public base for the three private model variants.
`CompletenessDeclaration` and every other annotation alias stay internal and
uncallable. Private descriptor variants, payload schema classes, and family
contracts likewise stay unexported.

Every symbol above has one canonical focused Help owner. In particular,
`EntitySamplingPolicy`, `engine_sample`, `ForecastModel`, and `periods` receive
the missing focused leaves frozen by their owning designs. `Grain` and
`TimeScope` resolve identically from their public type objects and canonical
strings; neither is shadowed by a second temporal type name. Slice 8 replaces
the current export snapshot in `tests/test_public_surface.py` with this exact
ordered manifest and removes all other current exports atomically.

## Target Code Ownership

The implementation plan uses capability ownership rather than preserving the
current `frames`/`intents` split. Slice 0 freezes the following exact target
file manifest. A listed path has exactly one owner; a path that does not yet
exist is created by that owner. Moving responsibility later requires the
Cross-Slice Change Protocol before either slice edits the path.

| Area | Exact owned implementation files | Must not own |
| --- | --- | --- |
| Semantic definition and validation cutover | `marivo/semantic/ir.py`<br>`marivo/semantic/_authoring_decorators.py`<br>`marivo/semantic/_authoring_declarations.py`<br>`marivo/semantic/_authoring_values.py`<br>`marivo/semantic/validator.py`<br>`marivo/semantic/constraints.py`<br>`marivo/semantic/metric_graph.py`<br>`marivo/semantic/metric_graph_lowering.py`<br>`marivo/semantic/runtime_metric_lowering.py`<br>`marivo/semantic/readiness.py`<br>`marivo/semantic/materializer.py` | Population selection policy, Artifact state/receipts, statistical inference |
| Dataset Core | `marivo/analysis/datasets/__init__.py`<br>`marivo/analysis/datasets/base.py`<br>`marivo/analysis/datasets/descriptors.py`<br>`marivo/analysis/datasets/fields.py`<br>`marivo/analysis/datasets/state.py`<br>`marivo/analysis/datasets/contract.py`<br>`marivo/analysis/datasets/registry.py`<br>`marivo/analysis/datasets/handles.py`<br>`marivo/analysis/datasets/actions.py`<br>`marivo/analysis/datasets/errors.py` | Population meaning, physical plan, storage receipts |
| Observation | `marivo/analysis/observation/__init__.py`<br>`marivo/analysis/observation/population.py`<br>`marivo/analysis/observation/predicates.py`<br>`marivo/analysis/observation/source_bindings.py`<br>`marivo/analysis/observation/metric.py`<br>`marivo/analysis/observation/coordinates.py`<br>`marivo/analysis/observation/aggregation.py`<br>`marivo/analysis/observation/rollup.py`<br>`marivo/analysis/observation/contracts.py`<br>`marivo/analysis/observation/errors.py` | Ibis placement, commit ordering |
| Compiler | `marivo/analysis/compiler/__init__.py`<br>`marivo/analysis/compiler/nodes.py`<br>`marivo/analysis/compiler/normalize.py`<br>`marivo/analysis/compiler/manifest.py`<br>`marivo/analysis/compiler/lowering.py`<br>`marivo/analysis/compiler/placement.py`<br>`marivo/analysis/compiler/stages.py`<br>`marivo/analysis/compiler/exchange.py`<br>`marivo/analysis/compiler/errors.py` | public Dataset semantics, Run publication |
| Materialization runtime | `marivo/analysis/materialization/__init__.py`<br>`marivo/analysis/materialization/contracts.py`<br>`marivo/analysis/materialization/execution_key.py`<br>`marivo/analysis/materialization/admission.py`<br>`marivo/analysis/materialization/writer_guard.py`<br>`marivo/analysis/materialization/resources.py`<br>`marivo/analysis/materialization/storage.py`<br>`marivo/analysis/materialization/publication.py`<br>`marivo/analysis/materialization/recovery.py`<br>`marivo/analysis/materialization/reconciliation.py`<br>`marivo/analysis/materialization/inspection.py`<br>`marivo/analysis/materialization/store.py`<br>`marivo/analysis/materialization/layout.py`<br>`marivo/analysis/materialization/errors.py` | operator algorithms, public row meaning |
| Typed operators | `marivo/analysis/operators/__init__.py`<br>`marivo/analysis/operators/registry.py`<br>`marivo/analysis/operators/row.py`<br>`marivo/analysis/operators/rollup.py`<br>`marivo/analysis/operators/compare.py`<br>`marivo/analysis/operators/attribute.py`<br>`marivo/analysis/operators/correlate.py`<br>`marivo/analysis/operators/forecast.py`<br>`marivo/analysis/operators/discovery.py`<br>`marivo/analysis/operators/contracts.py`<br>`marivo/analysis/operators/errors.py` | Session management, generic fallback execution |
| Subject/Event/Lifecycle | `marivo/analysis/domains/__init__.py`<br>`marivo/analysis/domains/subject.py`<br>`marivo/analysis/domains/event.py`<br>`marivo/analysis/domains/lifecycle.py`<br>`marivo/analysis/domains/completeness.py`<br>`marivo/analysis/domains/contracts.py`<br>`marivo/analysis/domains/errors.py` | common Dataset state, generic planner/runtime state |
| Session facade and reads | `marivo/analysis/session/__init__.py`<br>`marivo/analysis/session/core.py`<br>`marivo/analysis/session/history.py`<br>`marivo/analysis/session/_read_model.py`<br>`marivo/analysis/session/_runtime_reads.py`<br>`marivo/analysis/session/_resolve.py`<br>`marivo/analysis/session/_connections.py`<br>`marivo/analysis/session/_lazy_sources.py`<br>`marivo/analysis/session/_lazy_read_model.py`<br>`marivo/analysis/session/_lazy_runtime_reads.py`<br>`marivo/analysis/session/_lazy_history.py`<br>`marivo/analysis/session/_lazy_graph.py` | Dataset-owned downstream operators or captured-value execution lookup |
| Capability and public-surface integration | `marivo/analysis/__init__.py`<br>`marivo/analysis/errors.py`<br>`marivo/analysis/constraints.py`<br>`marivo/analysis/_contract_budget.py`<br>`marivo/analysis/_capabilities/__init__.py`<br>`marivo/analysis/_capabilities/model.py`<br>`marivo/analysis/_capabilities/registry.py`<br>`marivo/analysis/_capabilities/render.py`<br>`marivo/analysis/_capabilities/surface.py`<br>`marivo/analysis/_capabilities/validation.py` | family algorithms, copied continuation inventories |
| Evidence records and terminal reads | `marivo/analysis/evidence/__init__.py`<br>`marivo/analysis/evidence/types.py`<br>`marivo/analysis/evidence/_dataset_types.py`<br>`marivo/analysis/evidence/_dataset_codec.py`<br>`marivo/analysis/evidence/_dataset_reads.py`<br>`marivo/analysis/evidence/identity.py`<br>`marivo/analysis/evidence/digest.py`<br>`marivo/analysis/evidence/store.py`<br>`marivo/analysis/evidence/pipeline.py`<br>`marivo/analysis/evidence/audit.py`<br>`marivo/analysis/evidence/artifact_reads.py`<br>`marivo/analysis/evidence/summary.py`<br>`marivo/analysis/evidence/finding_render.py`<br>`marivo/analysis/evidence/extraction/__init__.py`<br>`marivo/analysis/evidence/extraction/_coordinates.py`<br>`marivo/analysis/evidence/extraction/observation.py`<br>`marivo/analysis/evidence/extraction/delta.py`<br>`marivo/analysis/evidence/extraction/composition.py`<br>`marivo/analysis/evidence/extraction/correlation.py`<br>`marivo/analysis/evidence/extraction/forecast.py`<br>`marivo/analysis/evidence/extraction/anomaly.py`<br>`marivo/analysis/evidence/extraction/event.py`<br>`marivo/analysis/evidence/extraction/funnel.py`<br>`marivo/analysis/evidence/extraction/lifecycle.py`<br>`marivo/analysis/evidence/extraction/subject.py` | executable Dataset origin graphs |

The current `marivo/analysis/frames/`, `marivo/analysis/intents/`, and
`marivo/analysis/executor/` files are eager implementation sources, not target
owners. Slice 8 owns their deletion after every retained behavior has moved to
one path above. It must also delete or replace the current persistence-only
files `marivo/analysis/session/_artifact_meta.py`, `_layout.py`, `_load.py`,
`_runs.py`, `_runtime.py`, `_source_bindings.py`, and `_store.py`; no earlier
slice may repurpose one of those files as a target owner.

Shared registry seams must be assembled from owner registrations. A renderer,
Session facade, compiler, or persistence decoder may not copy a second family,
operator, continuation, or field inventory.

The semantic owner implements intrinsic identity/version and aggregation rules;
Observation implements their context-specific admission. Slice 8 removes the
old version-column-in-primary-key constraints and repairs, and the eager
`_effective_key` subtraction behavior disappears with the old planner. Earlier
private slices use target-contract fixtures without publishing a second authoring
API. The target reader never decodes an old identity interpretation as the new
one. Semantic Help registry changes follow that same owner's native registry
and existing bounded disclosure paths.

## Current Disclosure and Test Inventory

### Current disclosure files

The atomic Slice 8 disclosure owner must update these current files:

- `README.md`, `README.zh-CN.md`, and `docs/api/analysis.rst`;
- `docs/specs/agent-friendly-public-surface.md`;
- `docs/specs/analysis/python-analysis-design.md`;
- `docs/specs/analysis/operators-and-frames.md`, which must be renamed or
  replaced rather than left as a current Frame spec;
- `docs/specs/analysis/session-state-and-runtime.md`;
- `docs/specs/analysis/evidence-access-surface.md`;
- `docs/specs/analysis/timezone-and-calendar-design.md` and
  `docs/specs/temporal-semantics.md`;
- `marivo/skills/marivo-analysis/SKILL.md` and
  `marivo/skills/marivo-semantic/SKILL.md`;
- the English/Chinese pairs beneath `site/src/content/docs/{docs,zh-cn/docs}/latest/`
  for `index.mdx`, `first-analysis.mdx`, `installation.mdx`, `quick-start.mdx`,
  `concepts/index.mdx`, `concepts/analysis-workflow.mdx`,
  `concepts/evidence.mdx`, `concepts/readiness.mdx`,
  `concepts/semantic-layer.mdx`, `guides/business-question.mdx`,
  `reference/deployment.mdx`, `reference/project-configuration.mdx`, and
  `reference/telemetry.mdx`;
- cross-layer review of `docs/README.md` and
  `docs/specs/semantic/{overview,semantic-object-model,authoring-workflow,datasource-layer,loading-validation-introspection}.md`;
- current semantic authoring examples and their English/Chinese site pairs
  containing snapshot/validity `primary_key` declarations or default temporal
  selection guidance; these adopt the identity-key contract in Slice 8, not
  while this design-only amendment is being reviewed.

Historical version directories, historical release notes, blog posts, and
unrelated `docs/superpowers/**` designs remain historical. They may name eager
paths as history but may not be linked or parsed as current guidance. In
particular, old `marivo.help("targets")` references in release notes are not
rewritten. The same stale call in current `README.zh-CN.md` is a current defect
and must be repaired by the owning non-lazy documentation change or Slice 8.

There is no Analysis CLI command. `pyproject.toml` exposes the single `marivo`
entrypoint, and `marivo/cli.py` registers only `init`, `doctor`, and `help`.
Slice 8 retains Python plus Help as the Analysis entry and adds no
`marivo analysis` command.

### Non-overlapping test ownership

| Contract | Exact current or planned test owner |
| --- | --- |
| semantic identity/versioning, source uniqueness and ordered Metric graphs | `tests/test_semantic_validator.py`, `tests/test_semantic_authoring_snapshot_e2e.py`, `tests/test_semantic_phase2_validity.py`, `tests/test_semantic_readiness.py`, `tests/test_semantic_metric_graph_lowering.py`; replace old physical-key assertions at Slice 8 |
| ordered public export fingerprint | `tests/test_public_surface.py` |
| normalized capability-contract fingerprint | `tests/test_analysis_capability_registry.py` |
| ordered Help-target fingerprint | `tests/test_analysis_help.py` |
| exact public export set | `tests/test_public_surface.py` |
| module/export negative boundary | `tests/test_analysis_imports.py`, `tests/test_analysis_grain_public_exports.py` |
| registry structure and topology | `tests/test_analysis_capability_registry.py` |
| type algebra | `tests/test_analysis_type_algebra.py` |
| Help rendering and budgets | `tests/test_analysis_help.py` |
| Help resolution, aliases, and errors | `tests/test_analysis_help_resolution.py`, `tests/test_unified_help.py` |
| packaged skill and documentation drift | `tests/test_packaged_skill_shape.py` |
| CLI/bootstrap boundary | `tests/test_cli.py` |
| dynamic Artifact disclosure | `tests/test_agent_result_protocol.py`, `tests/test_analysis_artifact_protocol.py`, `tests/test_analysis_result_surface_identity.py` |
| authority, family, and admission | `tests/test_analysis_authority_inventory.py`, `tests/test_analysis_family_gate.py`, `tests/test_analysis_operator_admission.py` |
| retained Session/Run/Artifact reads, graph scope, and selected Finding reads | `tests/test_analysis_runtime_reads.py`, `tests/test_analysis_session_graph.py`, `tests/test_analysis_artifact_evidence_reads.py`; private v3: `tests/test_lazy_runtime_reads.py`, `tests/test_lazy_session_history.py`, `tests/test_lazy_session_graph.py`, `tests/test_lazy_finding_types.py`, `tests/test_lazy_finding_reads.py`, `tests/test_lazy_integrity_inspection.py`, `tests/test_lazy_inspection_boundaries.py`, `tests/test_lazy_runtime_read_acceptance.py`; Slice 8 switches public assertions |
| executable Analysis Help examples | planned `tests/test_analysis_help_examples_execute.py` |
| executable current English/Chinese examples | planned `tests/test_lazy_analysis_current_docs_examples.py` |

`tests/test_agent_api_drift.py` may retain behavioral drift assertions but must
stop duplicating the complete `mv.__all__` set; `tests/test_public_surface.py`
is the sole set owner, while the Slice 0 fingerprint additionally freezes
ordering. The existing `tests/test_help_examples_execute.py` exercises
Datasource and Semantic examples only and is not Analysis example evidence.
`site` content verification checks files, sidebar, and metadata rather than
Python example execution.

## Delivery Strategy

### Private-first, public-once

Slices 1 through 7 may merge as private implementation only when all of these
conditions hold:

- no new Dataset class or callable joins `marivo.analysis.__all__`;
- no new public Help target advertises the private implementation;
- no current public eager call dispatches to a selectable lazy alternative;
- no released Session Store is upgraded to v3;
- no current English or Chinese user documentation presents lazy syntax as
  available;
- private tests invoke the implementation through test-only factories or
  internal modules, never a temporary public alias.

The public surface and persistence generation switch only in Slice 8. A release
must not be cut from an intermediate commit after the switch begins and before
Slice 9 acceptance completes.

### Milestones, implementation units, and dependency order

Slice numbers 0-9 identify delivery milestones, not a mandatory numeric execution
order. The sub-slices below are the independently reviewed implementation units.
A parent slice closes only after all of its units and its combined exit gate
pass. These subdivisions do not authorize implementation or change the existing
private-first/public-once boundary.

The recommended execution order for the shared foundation is:

```text
1 -> 2a -> 2b -> 3a -> 4a -> 4b -> 3b -> 4c -> 4d
```

The non-numeric order is intentional: Slice 3b consumes the guarded local reader
and executor from 4a and the engine Artifact writer/reader from 4b. A local
Parquet checkpoint alone cannot prove a source-required identity-membership
continuation. Slice 3a can close before those capabilities exist; Slice 3 as a
whole cannot. No unit may silently implement another unit's prerequisite to
claim its own exit gate.

After that foundation, Slices 5-7 use the prerequisites in their own sub-slice
tables. Independent units may run concurrently only after assigning their
shared files and registration seams. Complete all units in Slices 1-7 before
the atomic Slice 8 switch; Slice 9 validates the resulting exact candidate.

Every sub-slice has one bounded contract or runtime outcome. An executing unit
includes its invocation, row and row-set contracts, algorithm, source/local
registration, materialization, Evidence, repairs, positive/negative tests, and
cold-read evidence together. Do not split that work into separate API,
compiler, and persistence deliveries that cannot execute independently.

Before coding a unit, its implementation document must freeze:

- exact predecessor units and their completed evidence;
- the supported backend, method variants, storage target, and fixture scope;
- exact files owned and the consumer/registration seams changed;
- independent positive, negative, failure, and runtime acceptance criteria;
- the row in the capability-to-acceptance matrix that it completes.

The area manifest above assigns permanent contract ownership; the unit document
assigns the precise edit scope within that area. A later unit extends the same
owner registration rather than duplicating it. An unresolved dependency or
acceptance assignment must be corrected here before the affected unit starts.

### Slice completion rule

Every slice contains:

```text
one user-observable or runtime outcome
  + exact owning contracts
  + non-overlapping code ownership
  + positive and adjacent-negative tests
  + failure injection where state changes
  + runtime evidence against a supported backend
  + explicit removal/defer list
```

No slice is accepted because its signatures compile or its mocks are green.
Pure value/definition units such as Slice 1 and 2a prove their no-I/O boundary
directly; they do not create a backend journey merely to satisfy this template.
Every executing unit requires fresh terminal runtime evidence for its bounded
route. Parent acceptance includes the interactions between its completed units.

## Slice 0: Freeze Inventory, File Ownership, and Baseline

### Outcome

Produce the exact implementation manifest and prove that every current public,
persisted, Help, documentation, skill, and test surface is classified before
new code begins.

### Work

1. Record the exact head SHA and current `mv.__all__` snapshot.
2. Export every capability id, public entrypoint, Help target, capability kind,
   accepted input family, and output family from the current registry.
3. Inventory all current Frame/Result/Selection classes and public methods.
4. Inventory Session methods and namespaces, including source, downstream,
   runtime, Event, Lifecycle, and discovery paths.
5. Inventory every current persistence schema/version string, SQL table,
   decoder, writer, recovery path, and corruption test.
6. Inventory all current analysis Help targets, examples, packaged-skill facts,
   latest English/Chinese site pages, CLI routes, and release-note references.
7. Inventory tests by owning contract rather than filename alone.
8. Replace the proposed package rows in `Target Code Ownership` with exact file
   manifests and identify every shared seam.
9. Record the three resolved entry-gate decisions in the implementation
   manifest and pin their owner-design references.
10. Capture current broad-gate results and distinguish pre-existing failures.

### Required artifacts

- a checked-in current-export snapshot test;
- a checked-in current-capability snapshot test;
- a replacement/removal table with no unclassified row;
- an exact code-ownership table with no overlapping implementation owner;
- a persistence-generation inventory;
- a documentation and Help route inventory;
- baseline output from `make check-agent`, site verification/build, examples,
  and package build/check.

### Exit gate

- every current public symbol and capability is classified as `retain`,
  `replace`, or `remove`;
- every accepted target symbol has one planned owner, test suite, Help leaf, and
  disclosure update;
- every Store table, schema, and decoder has one replacement or deletion row;
- the resolved entry-gate decision table is reflected by the implementation
  manifest with no open owner question;
- no implementation slice needs to decide a public or persisted product
  contract.

### Slice 0 execution record: 2026-09-04

Completed against code baseline
`d3af482c02d4ff1f5a986c7e23c505a6bfbd093d`:

- froze all 82 ordered exports in `tests/test_public_surface.py`, all 128
  normalized capability contracts in
  `tests/test_analysis_capability_registry.py`, and all 169 ordered Help
  targets in `tests/test_analysis_help.py`;
- classified every current export and capability as retain, replace, or remove;
- recorded the exact current row-bearing, selection, Session, namespace,
  persistence, disclosure, Help, CLI, skill, and test surfaces;
- replaced area-level ownership proposals with non-overlapping exact file
  manifests and explicit deletion owners;
- verified `make check-agent`: lint/import contracts passed, mypy passed for
  308 source files, 5,482 tests passed, and API documentation built;
- verified `.venv/bin/pytest -q -n 0 tests/test_help_examples_execute.py`:
  106 examples passed, while confirming that this suite covers Datasource and
  Semantic rather than Analysis;
- verified `cd site && npm run verify:content && npm run build`: 343 required
  content files were present and the 321-page site build passed;
- verified `make pypi-build pypi-check`: sdist, wheel, Twine metadata, and the
  wheel-content contract passed.

The worktree already contained unrelated and uncommitted design-document work.
The baseline SHA therefore freezes executable code, while this plan and the
scenario-owned snapshot assertions remain the Slice 0 changes. No pre-existing
file was normalized or reverted.

The owner accepted the three surfaced contracts on 2026-09-04, and their owning
designs now freeze:

1. the generation-scoped v3 Session Store and fail-closed old-generation
   boundary in Materialization Runtime, amended on 2026-09-05 to colocate all
   metadata and Evidence, commit the publication bundle once, and guard writes
   per Session; exact relations, descriptors, indexes, and transaction owners
   follow that revised owner;
2. the exact ordered 100-symbol target export manifest above, the paired-family
   no-alias rule in Dataset Core, and the exact Artifact, Run, Finding,
   revalidation, graph, nested-value, and Help contracts in the accepted lazy
   v3 Session Runtime Read amendment and Module 5/6 Finding tables;
3. direct construction through
   `BoundedCompletenessDeclarationV1(...)` and
   `SourceOriginCompletenessDeclarationV1(...)`, with the unexported
   `CompletenessDeclaration` retained only as internal annotation shorthand.

The repository still has no current runner for Analysis Help examples or
current bilingual Analysis snippets. This is not a Slice 0 blocker: Slice 8
owns the scenario-classified planned tests
`tests/test_analysis_help_examples_execute.py` and
`tests/test_lazy_analysis_current_docs_examples.py`, and Slice 9 runs both
explicit entrypoints. The current Datasource/Semantic example suite and site
metadata/build checks cannot substitute for those future gates.

All Slice 0 exit conditions are satisfied. Slice 0 is complete; Slices 1
through 9 remain separately unauthorized until the owner starts the next named
slice.

## Slice 1: Dataset Core and Private Family Registry

### Outcome

Implement the private common Dataset value model, paired state types, exact row
and row-set contracts, selectors, and family registration without datasource
execution or public exposure.

### Owned implementation

- `Dataset`, `LogicalDataset`, and `MaterializedDataset` internal bases;
- closed paired-family registry;
- `DatasetShapeId`, `DatasetFieldId`, field identity, physical-type state,
  field, row-bound, cardinality, ordering, byte-count, family-row-semantics,
  row-contract, row-set-contract, and schema descriptors;
- `LogicalDatasetState` and `MaterializedDatasetState`;
- `DatasetFields` and Session-owned `DatasetFieldRef`;
- immutable Dataset construction, equality, hashing, definition fingerprint,
  and bounded lineage;
- common bounded `repr` and `DatasetContract` terminal protocol;
- private `LogicalRootHandle` and `MaterializedScanLeafHandle` pairing checks;
- common operator-construction protocol returning only Logical state.

### Explicit exclusions

- no datasource binding or Ibis expression;
- no Run/Artifact allocation, receipt publication, or Evidence/row read;
- no public export or Help route;
- no family-specific Metric/Event/statistical meaning;
- no pickle or logical-Artifact ref.

Core still owns the state-specific abstract action signatures and trusted
Materialized authority descriptors defined by the Dataset Core design. Declaring
those contracts does not execute a read, publish an Artifact, or recover persisted
state. The required family decoder registration slot is not a decoder implementation.

### Required tests

- family registry duplicate/missing/invalid-pair tests;
- immutable value, equality, hashing, and deterministic fingerprint tests;
- Core realization-sharing fixtures distinguish ordered first-use labels
  `[0, 0]` for two uses of one significant realization from `[0, 1]` for
  independent producers with equal definitions; reconstructed equivalent sharing
  has the same fingerprint despite different graph-local handles and object
  addresses;
- realization traversal stops at exact Materialized Artifact leaves and never
  hashes origin topology or creates a second planner fingerprint;
- complete logical row-contract and row-set-contract construction without
  execution, with `dataset.schema` identical to `row_contract.schema`;
- one canonical schema field inventory, id-only coordinate/key/ordering/family
  references, and derived value bindings;
- field-id uniqueness and exact selector resolution;
- missing, ambiguous, wrong-role, stale, and foreign-Session selector failures;
- logical/materialized private-root mismatch corruption tests;
- no DataFrame-like protocol tests;
- `repr` and `contract()` no-I/O/no-Run tests;
- Dataset absence from the terminal `AgentResult` protocol and
  `DatasetContract` conformance;
- construction of arbitrarily deep test DAGs with zero datasource calls, Runs,
  Artifacts, or storage writes.

### Exit gate

Every later family can register one paired state type and construct complete
logical row and row-set contracts without importing compiler, runtime, pandas,
or storage implementation.

### Slice 1 execution record: 2026-09-07

Slice 1 is implemented privately and accepted. The owner separately authorized
this slice on 2026-09-07; Slices 2-9 remain outside this authorization.

Candidate: branch `lazy-dataset`, base HEAD
`5b1fa9aca9b0ddc53755c527d9716720b7e72253`, plus the uncommitted owned changes.
The SHA-256 of the 21 ordered production/test/import-boundary files (UTF-8 path,
NUL, file bytes, NUL for each lexically ordered path) is
`6036925b7c3abd8eecac5a9ac437e87e662c9330c5cd7c9f32df5a64133f7247`.
Documentation and generated build artifacts are excluded from this content digest.
The digest includes pre-commit formatter line wrapping in the row-contract test;
its Python AST is unchanged from the fully validated candidate.

Implemented:

- all ten private Dataset Core modules, sealed descriptor/state variants and
  paired-family registration; the production registry has no family registrations;
- canonical schema and separate jointly validated row/row-set contracts,
  exact field selectors and registered physical-type refinement;
- immutable values, identity equality, unhashability, bounded lineage, and the
  sole definition fingerprint including canonical significant-realization sharing;
- factory-issued logical roots with inductive child-integrity checks, exact
  Materialized scan leaves, state-specific abstract actions and pure construction;
- registry-derived DatasetContract terminal reads, structured error repairs,
  dependency guards and early-public-exposure negatives.

Review disposition (2026-09-07):

- Adopted one shared tagged canonical scalar/tuple encoder and one stable-ID
  lexical predicate. Descriptor and definition owners still select their own
  complete payloads and expose their own typed errors. Independent byte vectors
  cover exact types, float.hex, negative zero, Unicode escaping and tuple order.
- Removed the unused lineage-limit parameter and shared the repeated ordered
  key/coordinate validation. Kept each closed selector/contract sealing block
  local: its construction and repair rules have a distinct owner, and a generic
  sealing abstraction would add no contract benefit.
- Moved registry freezing to explicit assembly. Logical, Materialized and raw
  construction require a finalized registry and do not mutate it on success or
  failure. Failed assembly remains open for repair.
- Replaced the blanket indirect-backend exception with three exact legacy edges:
  datasets.errors -> analysis.errors, and datasets.fields -> semantic.catalog /
  semantic.runtime_metric. Those edges retain their existing transitive backend
  dependencies; every other direct or indirect Core entry remains forbidden.
  Nine injected import-graph violations verify the boundary. The eager/execution
  import contract retains full transitive enforcement.
- Retained the materialized-state decoder registration slot and abstract
  Materialized reads: the Core design explicitly owns them. The fixture identity
  callback proves registration completeness, not persisted decoding or recovery;
  abstract methods and trusted authority descriptors perform no reads/publication.
- Additional independent review found a pre-existing family-fact ambiguity:
  a DatasetFieldId and a literal tuple containing its encoded tag could collide.
  Family tuple facts now have their own tag. Both direct and nested regressions
  failed on the old encoding and pass after the correction.

The shared encoding changes private fingerprints. No durable identity, published
Dataset consumer or compatibility path exists in this slice. The refreshed
candidate digest and no-I/O fingerprints below identify the reviewed revision.

Fresh verification:

- Baseline: `make test-agent TESTS='tests/test_public_surface.py tests/test_analysis_capability_registry.py tests/test_analysis_help.py tests/test_analysis_imports.py tests/test_agent_result_protocol.py'`
  passed 424 tests before implementation.
- `make test-agent TESTS='tests/test_lazy_dataset_descriptors.py tests/test_lazy_dataset_row_contract.py tests/test_lazy_dataset_registry.py tests/test_lazy_dataset_values.py tests/test_lazy_dataset_fields.py tests/test_lazy_dataset_contract.py tests/test_lazy_dataset_runtime_no_io.py tests/test_analysis_imports.py'`
  passed 195 tests in 11.16 seconds. Descriptor, row-contract, registry, value, selector, terminal-contract,
  static-type and no-I/O tests cover all Slice 1 acceptance rows. Independent
  review probes and regressions reject changed top-level/nested root definitions,
  mutable registration containers, foreign ID vocabularies and wrong-state methods.
- `make typecheck-agent TYPECHECK_TARGETS='marivo/analysis/datasets tests/typing'`
  passed for 13 files.
- `make lint-agent LINT_TARGETS='marivo/analysis/datasets tests/lazy_dataset_fixtures.py tests/test_lazy_dataset_descriptors.py tests/test_lazy_dataset_row_contract.py tests/test_lazy_dataset_registry.py tests/test_lazy_dataset_values.py tests/test_lazy_dataset_fields.py tests/test_lazy_dataset_contract.py tests/test_lazy_dataset_runtime_no_io.py tests/typing/lazy_dataset_core_contract.py tests/test_analysis_imports.py'`
  passed, including both Core import contracts.
- `.venv/bin/pytest -n 0 -q -s tests/test_lazy_dataset_runtime_no_io.py::test_deep_private_dataset_dag_is_pure`
  passed in a fresh child process: 2,001 logical nodes, 16 retained lineage facts,
  1,985 omitted facts, 27 guarded entrypoints and three typed negative cases.
  Datasource, connection, query, Run, Artifact, Store, Evidence, binding,
  filesystem and network attempt counts were all zero.
- `make check-agent` passed: lint/import contracts; mypy for 319 files;
  5,659 tests in 118.57 seconds; API documentation build.
- `git diff --check` and the exact owned-file scope check passed.

The no-I/O journey's source fingerprint was
`ds_0b610ada9b21cc97a0c0b3726082bb43e08d3d5bbe4250e80a651a4748312352`;
its final fingerprint was
`ds_5cd26067078c0eedac8169d9cb1c842c6063736ef093503e5b3cc83161156e15`.
The test Session identity is `session-test`; no real Session, backend, Store or
Artifact was created. These are pure Core acceptance facts, not backend or
real-Agent acceptance for later slices.

The current 82-export, 128-capability and 169-Help-target snapshots remain
unchanged. No public API/Help, eager implementation, Session Store, current site
content or packaged skill was switched. No commit, push or release was made.
The local implementation document at
`../plans/2026-09-04-lazy-analysis-slice-1-dataset-core.md` was synchronized with the
September 7 owner contract; that plans directory remains ignored by the existing
repository rule.

## Slice 2: First Complete Metric Vertical

### Outcome

Privately execute and recover this complete same-datasource journey:

```python
logical = (
    session.observe(metrics=[revenue], time_scope=window)
    .with_dimensions(region)
    .with_time_axis(order_time, grain=mv.grain("day"))
    .aggregate()
)
materialized = logical.execute()
materialized.show()
materialized.to_pandas()
```

The syntax above represents the target contract; tests use the internal entry
until Slice 8.

### Required sub-slices

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 2a: private semantic and Metric source construction | 1 | Implement target semantic normalization and Population/Metric source contracts behind private factories. Identity/version selection, captured bindings, observation scope, and the initial coordinate chain have complete contracts with no data work. Exercise the actual target normalizers; fixtures must not substitute for their implementation. |
| 2b: first committed Metric execution | 2a | Execute one declared datasource route into local immutable Parquet, publish the final v3 transaction bundle, recover it in a fresh process, and perform bounded/guarded terminal reads. Prove admission ordering, exact-key recovery, and precommit/postcommit failure behavior on this route. |

The owner authorized Slice 2a on 2026-09-07 against Slice 1 commit
`277b51e937b99e087b32bdcf87960bdecd59c71a`. The accepted Core amendment adds a
closed Entity-identity field variant containing the exact Entity ref and ordered
typed identity signature, without adding a top-level export. Slice 2a also owns
the minimal Core node-payload, construction, registration and disclosure seams
needed by private Population/Metric families. The implementation document is
`../plans/2026-09-07-lazy-analysis-slice-2a-source-construction.md`.

Slice 2 is intentionally a narrow cross-layer vertical. Its implementation
document names the exact reference backend, Metric variants, and controlled
parameterized-source fixture. General operator support and additional storage
adapters belong to later units; temporary publication semantics do not.

### Owned implementation

- Population and Metric family contracts;
- `Session.source_bindings(...)` validation plus immutable per-source
  construction-time capture;
- exact default Population inference and explicit analysis-Entity component mapping;
- stable identity `K`, source version-row validation and the exact versioned
  Population endpoint binding, using private target semantic fixtures;
- independent membership selection and Metric observation scopes;
- Entity-grained shared Population spine;
- initial `where`, `with_dimensions`, `with_time_axis`, `aggregate`, and
  `metric` definitions needed by the journey;
- private semantic graph and direct Ibis lowering for the journey;
- one supported same-datasource execution domain;
- one local immutable Parquet Dataset storage receipt;
- the final v3 Store layout and one producing Run lifecycle, including the
  Session writer guard, exact execution-key lookup, admission before live work,
  resource reservations, atomic publication, and basic cold reconciliation;
- output schema/key/count validation;
- family materialization registration;
- canonical zero-Finding or exact Metric Evidence publication;
- one normalized Artifact/Evidence/Finding/Run-success transaction and
  Materialized Dataset recovery, without a separate marker or sidecar;
- bounded deterministic `show()` and guarded complete `to_pandas()` reads.

### Required evidence

- construction performs zero datasource and storage operations;
- output family, shape, field order, row key, logical types, nullability,
  ordering, cardinality, and fingerprint are complete before execution;
- one complete Ibis expression compiles and executes as one backend query stage;
- Population membership occurs before Metric evaluation and one shared spine
  retains null Metric branches;
- snapshot/validity fixtures accept `K` without version fields, reject duplicate
  version rows/overlapping intervals, and validate identity uniqueness only
  after exact temporal selection; unscoped versioned inference fails locally;
- ratio/component and additive Metric fixtures recompute at the requested
  coordinates rather than folding projected values incorrectly;
- on an admitted execution-key miss, persist the incomplete Run before live
  profile resolution, compilation, datasource statements, transfers, or resource
  creation; injected failures at those boundaries remain attributable to that
  Run rather than disappearing before admission;
- same-Session writer contention creates no contender Run, and an exact-key
  hit recovers the committed Artifact without live profile resolution, source
  work, storage copy, or a new Run;
- validated immutable storage precedes one transaction publishing the Artifact
  descriptor, Evidence, complete Finding set, and Run success; readers see none
  of that publication bundle before commit and all of it after commit;
- `show()` and `to_pandas()` perform no origin query and create no new analysis
  Run;
- cold recovery returns the exact same materialized family, row contract, and
  row-set contract;
- precommit failure exposes no partial Dataset authority; after authoritative
  rollback and termination/fencing proof, the same Run records terminal failure;
- a crash after commit or lost commit acknowledgement recovers the existing
  succeeded Run and Artifact without terminal rewrite, output deletion, or a
  second execution; unavailable readback never guesses failure;
- unresolved commit outcome or external termination retains the owner-defined
  incomplete/recovery-blocked state until authoritative reconciliation is
  possible; basic cold-process tests cover the reference route;
- a parameterized JSON source can construct inside a binding scope and execute
  after scope exit; missing/extra/secret-like inputs fail before a Logical
  Dataset is returned.

### Exit gate

One real supported backend produces a recoverable same-family Materialized
Metric Dataset with complete committed authority. A compiled query, pandas
preview, staged file, or isolated Evidence row is not enough. Acceptance proves
the normalized committed bundle and exact cold recovery. Slice 4 extends the
adapter, concurrency, and failure matrix without changing this protocol.

### Slice 2a execution record: 2026-09-07

Slice 2a is implemented privately and accepted. At that gate, only semantic/source
construction was complete; Slice 2 still required the separately authorized
2b execution, publication and recovery gate recorded below.

Candidate: branch `lazy-dataset`, base HEAD
`277b51e937b99e087b32bdcf87960bdecd59c71a`, plus the uncommitted owned changes.
The SHA-256 of the 35 ordered production/test/import-boundary files (UTF-8 path,
NUL, file bytes, NUL for each lexically ordered path) is
`d2f0cda1ea376dd346758c81e041639cb819dcec2ee0cc6b4542501217145e4a`.
Documentation and generated build artifacts are excluded from this digest.

Implemented and verified:

- Private production target normalizers separate stable typed Entity identity
  from version-row keys, preserve snapshot/validity endpoint meaning, and declare
  uniqueness, overlap, availability and post-selection checks as execution
  obligations. Existing public loading behavior remains unchanged.
- The canonical Metric graph supplies calculation roots, component roles,
  null/empty rules, required sum/count/weight/ratio state and space-before-time
  evaluation constraints. Safe functional mappings support different-root ratios
  over an explicit customer Population.
- Required injected action/read ports support complete paired Population/Metric
  registration without a default executor. The private Session source facade
  constructs membership, observation, filters, dimensions, day axes, aggregation
  and projection with all eight Metric shapes and complete row contracts.
- Entity identity descriptors bind ordered typed tuples, including unary keys.
  Core owns the immutable node-payload seam and sole safe definition fingerprint.
  Raw source bindings and predicate values remain private; Materialized inputs
  retain exact scan-leaf authority without replayable origin payloads.
- Source bindings use exact scalar types, declaration order, immutable sequence
  copies, complete nested-scope replacement/restoration and per-source reachable
  capture. Scope exit, input mutation and unrelated bindings cannot change an
  existing definition; changed values or types change its fingerprint.
- Independent review regressions reject mismatched row shapes, incomplete action
  ports, stale retained identity signatures, wrong join-key types, unresolved
  versioned intermediate paths and ambiguous scopes. Entity-owned default time
  axes take precedence; retained Population origins are not recaptured. Source
  and relationship-key changes enter the exact node dependency digest.

Fresh verification after the September 7 review cleanup:

Timings below are observations from individual local runs, not performance
acceptance thresholds or values that must reproduce. The combined and full
Make gates use the repository's default `-n auto --dist=loadscope`; the explicit
no-I/O command uses `-n 0`. Counts, outcomes, fingerprints and guarded operation
attempts are the acceptance facts. The local implementation document records the
exact focused command.

- The combined Core/import, semantic normalization/Metric graph, and Observation
  construction/binding/predicate/no-I/O gate passed 322 tests in 14.96 seconds.
  Tests use typed authored in-memory definitions and the production normalizers,
  including scalar/duplicate-coordinate/repeated-aggregation rejection, exact
  temporal endpoints, independent windows, filtering and projection placement.
- `make typecheck-agent` over Core, Observation, the private source facade, four
  touched semantic modules and `tests/typing` passed for 28 files. Scoped lint
  and import contracts passed, including the new Observation isolation boundary.
- `.venv/bin/pytest -n 0 -q -s tests/test_lazy_observation_runtime_no_io.py::test_actual_private_observation_chain_is_pure`
  passed in 8.82 seconds. A fresh child process constructed eight checked
  definitions, an 80-filter chain and five typed negative cases while guarding
  27 runtime entrypoints with telemetry enabled. Datasource, connection, query,
  Run, Artifact, Store, Evidence, binding, filesystem and network attempt counts
  were all zero.
- `make check-agent` passed: lint/import contracts, mypy for 330 source files,
  5,759 tests in 96.48 seconds, and API documentation build.
- `git diff --check` and the owned-file scope check passed. Existing public
  export, capability and Help snapshots remain unchanged; private imports also
  preserve the current public Session methods.

The no-I/O journey's first observed Metric fingerprint was
`ds_c33203300d7711f2687eb4453e87acfabc855c7ead4546ecf126409abb307b73`;
its final `metric/dimension-time@v1` fingerprint was
`ds_f37aebe4e722a5a7879dbaa9334f4c071d7dfcfb58f142f319ec5495a9bbbdfe`.
The test owner identity is `session-observation`; no real Session, backend,
Store, Run or Artifact was created.

Review disposition:

- Removed the unused Observation `fail()` helper, duplicate Population
  normalization branches and their inline cast. Directly deleting the cast is
  insufficient for mypy because the runtime Ref discriminator does not narrow
  its generic marker; an exact membership-Dimension type guard now carries that
  proof without broadening the normalizer's input contract.
- Catch only `ObservationConstructionError` when searching compatible time
  axes. Exact scalar branches now provide static narrowing without redundant
  `isinstance` checks or numeric asserts. Metric identity collection preserves
  the narrowed identity type; a corrupt bound comparison raises its structured
  predicate error. Consumer admission uses full registered IDs, and coordinate
  checks unpack the grain by name.
- Retained the small canonical Metric/coordinate tuples. A frozen nested
  dataclass would require a new projection or expansion of Core's closed
  row-fact encoding. Payload checks have different owners and outcomes
  (admission, capture traversal, disclosure or required authority), and the two
  coordinate constructors enforce different path/time constraints. No generic
  accessor or construction-template abstraction was added for these checks.
- Retained internal `_create_ref` calls: public Ref factories invoke telemetry
  and can access files, so the production normalizer must bypass that wrapper
  to satisfy telemetry-enabled no-I/O acceptance. This is required purity work,
  not an additional public surface change. Direct `RefPayloadV1` construction
  already shares validated path rules and needs no intermediate Ref/helper.
- Retained normalized time-fold/status-axis facts to recognize unsupported
  semantic graphs before rejecting them. The local dependency-digest import
  reuses one existing collector after module initialization; that collector
  reads IR and does not re-enter Entity normalization. No additional module or
  deferred execution support was introduced.

This slice claims no execution backend or storage adapter. Runtime Metric
expressions, semi-additive time folds, general coordinate allocation, complete
predicates, sampling, rank/limit and retained-state folds remain outside this
initial construction envelope and fail explicitly where encountered. No current
public API, Help, site content or packaged workflow skill was switched.
No commit, push or release was made. The local implementation document at
`../plans/2026-09-07-lazy-analysis-slice-2a-source-construction.md` is synchronized;
the plans directory remains ignored by the existing repository rule.

### Slice 2b and Slice 2 acceptance record: 2026-09-07

The owner authorized Slice 2b implementation and the parent Slice 2 acceptance
against committed Slice 2a `21259dfa180b175f5d6c2783cc91e8dff86c5fd9`.
**Slice 2b is implemented and accepted; the composed Slice 2 gate is complete.**
The implementation record is
`../plans/2026-09-07-lazy-analysis-slice-2b-execution.md`; complete machine-readable
runtime evidence is
`../plans/evidence/2026-09-07-slice-2b-runtime.json`.

Initial acceptance candidate (superseded by the review follow-up below): branch
`lazy-dataset`, base HEAD `21259dfa180b175f5d6c2783cc91e8dff86c5fd9`, plus the
uncommitted owned changes at that gate.
The SHA-256 of the 74 explicitly listed source, test and configuration files is
`3f5d0cd87b9b10268da7025e68110699ff49a538c342a6dc04a0d3226b0af8ae`.
The protocol is sorted UTF-8 path, NUL, file bytes, NUL; the evidence contains every
path and individual hash. Before/after manifests match. Documentation and generated
build artifacts are excluded from the candidate digest.

Implemented and verified:

- Private Population and Metric chains lower the actual frozen semantic graph
  into one final Ibis calculation. Independent sum, count, mean, weighted mean,
  same-root ratio and cross-root ratio references verify exact filtering before
  component recomputation, unequal weights/denominators, null and zero branches,
  all eight Metric shapes and a shared Population spine without fanout.
- Declared DuckDB TableSources use one read transaction for physical schema,
  identity/version validation and computation. Controlled GET JSON uses reserved
  reader/fence names in that datasource; captured values survive scope exit,
  separate exact keys, and remain absent from persistence and diagnostics.
  Primary values and sufficient-component parts cross one bounded Arrow stream.
- Observation registers producer, quality, validation, Evidence and retained-state
  versions during pure construction. Core owns the complete definition fingerprint;
  Runtime derives only the execution key. The descriptor's semantic dependency
  projection also comes from the owning frozen Observation facts.
- The final nine-table STRICT v3 Store enforces WAL/FULL publication, exact-key
  uniqueness, complete zero-Finding Evidence and one successful terminal. Guarded
  admission precedes all live work. File and backend resources are reserved before
  creation; immutable Parquet and receipts are durable before the single bundle
  transaction. Independent readers observe no partial bundle.
- Cold reconstruction uses only selected v3 metadata and returns the same family,
  complete row/row-set contracts and a non-executable scan leaf. `show()` reads at
  most 20 rows and renders at most 8192 bytes; collection is capped at 100000 rows,
  64 MiB and 60 seconds using a terminable storage-only subprocess. Primary plus
  all retained parts share the 64 MiB file budget and 8 MiB decoded-batch cap.
- Failure injection proves same-Run attribution, no contender Run, safe cleanup
  after termination proof, committed readback after lost acknowledgement, and
  recovery-pending when readback or termination is unknown. Actual process exits
  cover reservation, final rename, an open publication transaction and commit.
  A second interpreter recovers after the source database is moved offline.

Fresh final gates:

- Combined compiler/materialization, 2a Observation/semantic, Core and import
  regressions: **531 passed in 94.26 seconds**.
- Telemetry-enabled no-I/O subprocess: **1 passed in 3.87 seconds**; eight
  definitions, 80 filter nodes, five negative cases and 27 guarded entrypoints.
  Datasource, query, connection, Run, Artifact, Store, Evidence, binding, filesystem
  and network attempt counts are all zero.
- Explicit `-n 0 -q -s` runtime acceptance: **1 passed in 24.39 seconds**, containing
  four real producer exits with code 73 and four fresh recovery processes. The
  first three recover the original failed Run without publishing or replaying;
  the committed case preserves the exact succeeded Run and Artifact. Every cold
  process records zero profile/credential/backend/compiler/source-factory attempts.
- Scoped typing: **32 source files**, plus owned fixture/test typing; lint and
  transitive import contracts pass. Compiler isolation is independently enforced.
- Final `make check-agent`: **348 source files** pass mypy, **5912 tests pass in
  149.96 seconds**, lint/import contracts pass, and API documentation builds.
- `git diff --check` passes. Public exports, Help, capability snapshots and public
  Session methods retain their current contracts.

The committed terminal case is Session
`session_ab51f699c31b4e1b9f0f55f11f479bff`, Run
`run_37b7cfc129114182621d02e7`, Artifact
`artifact_c9d05e3b355e41d6bfb294b0441cd4e4`. Its one primary stage transfers six rows
and 254 Arrow bytes; eight runtime validation operations are recorded separately.
Primary and retained receipts account for 1107 and 1978 bytes respectively,
including manifests. Evidence has zero Findings and zero failed/warning checks.
The full row contracts, fingerprints, normalized statement inventory, receipts
and reconciliation snapshots are retained in the evidence file.

Observed dependencies are Python 3.12.13, Ibis 12.0.0, DuckDB 1.5.3, PyArrow 25.0.1
and pandas 2.3.3. Timings are observations, not performance thresholds. The
implementation record documents repaired schema/Arrow-width, JSON, credential,
dependency-summary, fork and concurrent-initialization issues and their regressions.

This closes the specified local reference vertical. Retained rollups, generic
pandas continuation, other storage adapters, complete history browsing and the
public cutover remain with their later owners. No commit, push or release was
made. The implementation/evidence directory remains ignored by the existing rule.

### Slice 2b review follow-up: 2026-09-07

The review suggestions were checked against the owning runtime contracts and
the accepted implementation. The bounded corrections are complete:

- Preview rows, rendered guidance and complete collection consume the same selected
  `ReadPolicy`. Source-engine execution retains its independent named deadline;
  the owning executor-specific budget contract does not couple engine cancellation
  to the retained-read timeout.
- Publication callbacks now belong to `publish(event=...)`. Constructing two
  runtimes over one Store cannot replace either runtime's instrumentation or
  failure injection. No Store-lifetime callback remains.
- Runtime assertions are explicit typed checks, including the complete Artifact
  check before commit. Unsupported family payloads and missing Population ancestry
  also use structured errors. Redundant Session-creation branching is removed,
  and the diagnostic docstring no longer claims a separately enforced bound.
- SQLGlot import placement and pure profile registry lookup are retained. The
  pre-existing `show(max_output_bytes=...)` action-port contract is documented as
  a reducing override capped at 8192 bytes. No public surface or persisted codec
  changed, and no extra execution capability was added.

The current candidate retains base HEAD
`21259dfa180b175f5d6c2783cc91e8dff86c5fd9` and the same 74-file manifest. Exactly
six manifest files changed: `materialization/{admission,publication,store}.py`
and their three existing execution/failure/Store test modules. Its combined
SHA-256 is `0f041e7422b284de515d36cf242dc70d0e8283f270092d2c12841a43c69f2e17`.
The initial evidence remains historical and unchanged; current evidence is
`../plans/evidence/2026-09-07-slice-2b-review-runtime.json`.

Fresh validation:

- Affected Store/codec/execution/failure/guard tests: **91 passed in 84.77s**.
  New regressions prove shared-Store callback isolation, policy-aligned preview
  and collection, and `python -O` rollback when a real SQLite trigger removes
  the pending publication bundle.
- All six touched implementation/test modules pass typing; lint and import
  contracts pass.
- Fresh-process runtime evidence: **1 passed in 25.37s**, covering all four
  crash points with identical before/after candidate manifests. Recovery records
  zero source, backend, profile, credential and compiler attempts.
- Final `make check-agent`: **5915 tests passed in 156.52s**, full typing,
  lint/import checks and API documentation build passed. `git diff --check`
  passed; current public exports, Help and capability snapshots remain unchanged.

Slice 2b and the composed Slice 2 acceptance remain complete. No commit, push
or release was performed during this follow-up.

## Slice 3: Filtering, Coordinates, Ordering, and Explicit Checkpoints

### Outcome

Complete the reusable Dataset algebra needed to construct, bound, materialize,
and extend Entity and aggregate Metric chains.

### Required sub-slices

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 3a: source-side Population and Metric algebra | 2b | Complete predicates, Population construction/sampling, source-side coordinates and aggregation admission, and deterministic rank/limit. Execute their fused source chains and reject invalid contribution or temporal combinations before data work. |
| 3b: retained-state folds and checkpoint continuations | 3a, 4a, 4b | Add exact contribution/denominator retention, row/part selection, materialized fold/rollup, and shared checkpoints. Prove local Parquet calculations under 4a's guards and source-required membership continuations through 4b's engine Artifact path, with cold recovery and no origin replay. |

The retained-state protocol itself is final from Slice 2; 3b registers the
additional Metric roles and algorithms. It does not create another writer,
publication protocol, or generic local executor. The evidence below is divided
between these units by source-definition versus materialized-input authority;
logical/materialized parity closes in 3b.

### Owned implementation

- the complete shared `AnalysisPredicate` builder vocabulary;
- family-specific `where(...)` effects and input checks;
- exact Population construction and sampling;
- all accepted Metric coordinate shapes and aggregation admission;
- logical versus materialized aggregate/fold matrix;
- exact row-selection contribution keys and computational denominator semantics;
- required ratio/mean/weighted-mean retained parts with atomic row/part selection;
- registered `MetricDataset.rollup(drop_dimensions=..., grain=..., drop_time=...)` over
  Entity-reduced shapes;
- `rank(...)` and `limit(...)`;
- generated field ids and deterministic total ordering;
- materialized scan-leaf compilation;
- materialized Dataset use as direct-membership or identity-projection
  Population input;
- exact explicit checkpoint behavior for multiple downstream definitions.

Population-input admission uses closed registered Entity-present shapes plus a
proof of Entity-only uniqueness. Functionally dependent region/time coordinates
may remain; true Entity-by-time rows require explicit owned subject selection.

### Required evidence

- predicate literal/type/null/boolean normalization and adjacent invalid cases;
- authored filter order around sampling and aggregation barriers;
- selected-rate fixture A = 8/10, B = 1/10 yields 80% after selecting A;
- customer-day selection never restores other days or an original denominator;
- no unproved predicate reorder or push-through;
- exact, semi-additive, component-aware, cumulative, and materialized fold
  admission matrices;
- the `[10,0]`/`[0,10]` device-peak fixture rejects a projected-peak sum of 20
  when the governed peak of spatial sums is 10; asynchronous endpoints and
  unequal mean coverage require exact state/alignment or rejection;
- one 100-unit order in two overlapping tags does not become a 200-unit
  target total; only a separately governed conserving allocation or exact
  contribution-union state admits the fold;
- logical/materialized rollup parity, strict coarser-grain containment,
  partial/selected-period coverage, `drop_time=True`, retained-state rejection,
  and no-origin-replay;
- combined time/Dimension rollup and the corresponding two-call chain lower to
  the same normalized time-fold-then-Dimension-fold invocations over current
  rows and exact retained state;
- deterministic rank ties and limit prefixes;
- unordered preview canonicalization against a backend with deliberately varied
  natural order;
- materialized leaf predicates do not traverse origin lineage;
- January membership plus February observation works before and after recovery,
  and omitting observation scope never inherits January;
- two orders with two/three lines and revenue 100 produce order count 2 and
  average order value 50 under an explicit governed customer Population;
- logical and materialized population inputs preserve identity, scope,
  sampling, approximation, and Session authority;
- a shared checkpoint feeds two downstream logical definitions without origin
  replay or implicit equivalent-Artifact substitution;
- high-cardinality identities remain in an admitted engine or immutable storage
  path.

### Exit gate

The Metric algebra can express and safely execute both a fused logical chain and
an explicitly checkpointed chain, with deterministic order and exact
materialization-barrier repairs.

### Slice 3a acceptance record: 2026-09-07

This is the initial candidate record. The review follow-up below supersedes its
candidate digest and final gate; its original runtime and no-I/O evidence is
preserved with the `-initial.json` suffix.

**Slice 3a is implemented and accepted. Slice 3b and the parent Slice 3 gate
remain open.** The authorized source algebra was implemented on branch
`lazy-dataset`, against committed Slice 2b
`9e0cda8e5451070d64236446659333a29da62f01`; the checkout was clean at entry.
The execution record is `../plans/2026-09-07-lazy-analysis-slice-3a-execution.md`.

Implemented and verified:

- All twelve predicate builders preserve exact typed bindings, SQL three-valued
  logic, normalized Boolean/set identity and authored occurrence diagnostics.
  Population predicates bind stable member Dimensions; Metric predicates bind
  current retained fields, including generated rank. Literal disclosure stays
  outside errors and persisted graph projections.
- A sealed private Entity policy admits native DuckDB reservoir sampling after
  membership/version validation and filtering. A shared realization is evaluated
  once before every dependent Metric branch; independent handles remain separate.
  Unsupported exact parameters, repeated sampling and post-sample filtering fail
  without coercion or fallback. Sampling receipts and an actual private state part
  join the existing reserved, bounded, atomic v3 publication.
- Intrinsic Metric graphs normalize extrema, distinct, quantile, linear/derived,
  semi-additive and cumulative computation alongside sum/count/mean/weighted mean
  and ratio. Observation records actual grains, per-component contribution paths,
  per-axis partitions and temporal requirements instead of a Boolean admission
  flag. Unsafe fanout, overlapping fold claims and incompatible axes/anchors fail
  during construction. Certified calendar snapshots bind field and definition
  identity; display coverage and cumulative history have separate checks.
- Real source references verify all eight shapes, selected A = 8/10, exact
  customer-day selection, two orders with revenue 100 and AOV 50, device peak 10,
  overlapping tags without doubling a total, independent membership/observation
  versions, all registered status-time folds, and cumulative reset/trailing/history
  endpoints. Weighted and distinct cumulative values recompute raw base windows.
- Rank remains restricted to entity, dimension, time and dimension-time shapes.
  All four tie policies, both directions, partitions, composite identities,
  non-finite/null values and repeated ordered limits have independent references.
  Generated rank never becomes a contribution key. Compiler, writer and reader
  consume the Core total order; row-key uniqueness is independently validated.
- Ordinary source chains use one final Ibis calculation, with validation and
  sampling preparations disclosed separately. New source algorithms receive
  retained parts only when exact registered state requires them. Primary reads
  remain independent of unused private parts; explicit sampling-part audit is
  available after source-free metadata recovery.

The final candidate manifest contains 308 source, test and configuration files.
Its ordered-content SHA-256 is
`2f959c7c8f2d55b7a16c7d72d4429e2f106ca0d2e004276ebd45b8d52a2ebb4f`.
The protocol is sorted UTF-8 path, NUL, file bytes, NUL. Documentation and
generated files are excluded. Matching before/after manifests, individual hashes,
runtime versions, normalized statements, receipts and terminal bundles are in
`../plans/evidence/2026-09-07-slice-3a-runtime-initial.json`.

Fresh final gates:

- Focused predicates, ordering, semantic/Observation and materialization
  regressions: 158 passed; source/calendar/coordinate regressions: 57 passed.
  Sampling's native query, final query, state-write and publication failure
  matrix proves failed Runs publish no partial Artifact/Evidence/Findings and
  leave no resources. The final full gate includes all these regressions.
- Telemetry-enabled fresh-process no-I/O evidence:
  `../plans/evidence/2026-09-07-slice-3a-no-io-initial.json`. All 45 definitions, twelve
  predicates, nine grains, eight aggregation variants, four ties and sixteen
  rejected cases execute under 62 entrypoint guards; all fourteen I/O categories
  record zero attempts. The candidate digest matches runtime acceptance.
- Explicit two-process runtime acceptance: 1 passed in 33.01 seconds. Session
  `session_719a1232a75e4b6e83243819dcdc789c`, Run
  `run_8764f6e492d946acc587ecc3`, Artifact
  `artifact_01efb4c2c1de48a1a01275b33a5046b2` use one sampling fence, one primary
  query/stage and ten validation operations, transferring one row and 81 Arrow
  bytes. A new interpreter with the source database moved offline recovers the
  same rows, order, receipt and display, then validates the sampling-state part.
  All source/profile/credential/backend/compiler attempts are zero.
- Scoped typing passes for 36 touched dependency modules and typing probes.
  Final `make check-agent`: lint and import contracts pass, mypy checks 354
  files, 6085 tests pass in 186.97 seconds, and API documentation builds.
  `git diff --check` passes.

No public export, Help, Session API or site API switches in this unit. Materialized
input continuation, fold/rollup, checkpoints, pandas and engine Artifacts retain
their later slice ownership. No commit, push or release was made. Execution and
machine-readable evidence files remain under the repository's ignored plans rule.

### Slice 3a review follow-up: 2026-09-07

The owner authorized evaluating the review and adopting justified fixes on the
same uncommitted candidate. Full dispositions are recorded in
`../plans/2026-09-07-lazy-analysis-slice-3a-review-followup.md`.

Two source acceptance gaps were confirmed and repaired: natural-order
perturbation and January-membership/February-observation cold recovery. Five
tests now physically reverse insertion order, confirm the unordered backend scan
changes, and execute new Sessions to verify stable unordered previews and all
four ranked-prefix tie policies. The independent scope fixture proves January
members have reference value 30, explicit February observation 300, and omitted
observation scope 3000; two Artifacts recover unchanged in a new process with
the source offline and all source entrypoints forbidden.

The scope fixture uncovered a publication defect missed by source-only numeric
tests: repeated status-time validation names made the v3 descriptor invalid.
Compiler declarations now assign stable occurrence identities, preserving every
validation expression. The cold scope test proves repeated checks survive with
unique identities. Two additional real publication tests cover repeated calendar
coverage checks for additive and cumulative graphs.

Rank's coupled arguments now share one frozen `RankSpec` across logical and
retained payloads, field derivation and Compiler lowering. Coordinate binding and
resolution have distinct names; the duplicated `builtin_grain` imports are
consolidated. Sampling bounds remain deliberate and tested: negative seeds were
already rejected by `_int(minimum=0)`, large-integer repr remains bounded, and the
realization-count cap is named. No materialized-input execution or broader
cross-layer refactor was added.

The old semi-additive test's change from deferred source support to required
source recomputation is intentional Slice 2b-to-3a evolution. Projected retained
folding remains refused. The existing retained predicate construction test ends
at a payload-free scan leaf; executed retained predicates remain a 3b gate.
Updated working-tree specifications are part of the authorized implementation,
not a claim of a new commit. Native reservoir SQL remains inside the private
Runtime adapter and never enters an authored Python-track expression.

The reviewed candidate has 311 source, test and configuration files, with digest
`51034e26bb28fcf8490fa17feeddb348c140fdd4996d5c2e9caab8aa5a553946` under the same
ordered-content protocol. Matching before/after manifests and refreshed terminal
evidence are in `../plans/evidence/2026-09-07-slice-3a-runtime.json`; independent
scope recovery is in `../plans/evidence/2026-09-07-slice-3a-scope-recovery.json`,
and refreshed no-I/O evidence is in
`../plans/evidence/2026-09-07-slice-3a-no-io.json`. All bind this candidate digest.
The explicit runtime acceptance passes in 54.25 seconds: Session
`session_2e1989cd7b254e30932c29d85230b4c1`, Run
`run_0032a54813ef69a72b51f001`, Artifact
`artifact_fcba0b1ed2234a3fa24683fa7f6f619c` still require one sampling fence,
one primary query/stage and ten validation operations, transferring one row and
81 Arrow bytes. Cold-source attempt counts remain zero.

Final follow-up gates: scoped typing passes for 24 implementation/test modules;
`make check-agent` passes lint/import contracts, mypy for 354 files, 6101 tests in
267.60 seconds and API documentation. The 16 additional tests cover the two
confirmed evidence gaps, sampling boundaries and repeated calendar publication
checks. `git diff --check` and the final candidate/evidence comparison pass.
Slice 3a remains accepted; Slice 3b and the parent Slice 3 remain open. No commit,
push or release was made.

During the subsequently authorized commit preparation, the formatting hook
normalized only `tests/lazy_scope_recovery_worker.py`,
`tests/test_lazy_scope_recovery.py`, and `tests/test_lazy_source_natural_order.py`.
Their Python ASTs are unchanged from the reviewed candidate. The recorded
runtime evidence identifies the pre-formatting bytes; production files remain
identical to that accepted candidate.

### Slice 3b acceptance record: 2026-09-08

**Slice 3b is implemented and accepted; the combined Slice 3 gate is complete.**
This supersedes the open Slice 3 status in the earlier 3a/4a/4b records. Work
began from committed Slice 4b `431492b2`, after verifying its accepted candidate
and 64 focused prerequisite tests. The exact ownership, variant matrix and
evidence locations are recorded in the
[Slice 3b execution record](../plans/2026-09-08-lazy-analysis-slice-3b-execution.md).

- Metric row semantics now retain a closed, versioned fold graph, per-axis
  contribution proofs and temporal coverage authority. Cold recovery validates
  those facts without traversing a source definition or querying a catalog.
- `where`, `rank`, `limit` and Metric projection transform the current primary
  rows and selected component parts together using complete Entity, Dimension
  and time contribution keys. Generated rank is excluded from the key; the
  selected dependency closure and original sampling realization are preserved.
  Receipt/schema/key/support/coverage and primary-state reconciliation reject
  inconsistent required parts before computation or publication. Unrelated
  parts remain independent of primary reading and otherwise valid projection.
- Logical `aggregate()` retains governed source recomputation. Materialized
  aggregate and both rollup registrations fold current rows through exact
  Ibis/pandas algorithms: sum/count/extrema, linear, mean/weighted-mean/ratio
  component state, specialized Entity-key distinct and admitted temporal folds.
  Combined rollup normalizes to the same time-then-Dimension nodes as two calls.
  Strict coarser grain, interval containment, partial periods, time removal and
  empty scalar rules have independent coverage.
- Cumulative time folds retain their actual selected endpoint and observed
  coverage. Cumulative Dimension folds require aligned endpoints and a provably
  contiguous observed interval, or canonical empty state. Clipped intervals do
  not claim complete calendar coverage. Different internal gaps, asynchronous
  endpoints, projected device peaks and overlapping tags cannot silently become
  a spatial total. Unproved distinct/distribution and cumulative Entity folds
  reject at construction with an owned repair.
- One guarded worker receives all consumed local/object parts, or one wide
  stream from a legal Ibis prefix, and passes private DataFrames directly through
  the local suffix. Combined input, intermediate/output, RSS and deadline guards
  retain 4a's values. Existing writers, receipts and atomic publication are
  reused. Failure at output finalization or Store publication leaves the input
  checkpoint unchanged and exposes no partial Artifact/Evidence.
- Direct Population and Entity-unique Metric engine checkpoints supply exact
  membership to two independent observations. Scoped, sampled and resolved
  snapshot/validity membership are covered. January membership, February
  observation and omitted observation scope retain independent authority after
  recovery. Consecutive observations preserve the nearest selected membership
  fingerprint at publication. True Entity-by-time multiplicity, foreign Session
  or incompatible domain, and implicit local/object identity import remain
  rejected.

The final `make check-agent`, with the real isolated MinIO service enabled,
passes lint/import contracts, typing for **370 source files**, **6,352 tests in
437.82 seconds**, and API documentation construction. The 23-case Runtime fold
matrix, source/retained construction no-I/O checks and shared adapter regressions
are included in that gate. No S3 acceptance tests are skipped.

All four fresh three-process journeys have matching before/after **711-file**
source/test/configuration candidate SHA-256
`170ed26d8f829192c968509f0c069608130c68915dc54c14decee64ad4e97774`.
The Parquet journey moves the source database offline before two retained folds;
the engine journey removes the sampled membership origin before two independent
observations. Each ends with exactly three succeeded Runs, three Artifacts,
three Evidence records, two input edges to the same checkpoint, and an empty
resource journal. Cold `execute()` reuses the same bindings with no query,
transfer, copy, worker or new Run. Fresh shared engine/object journeys also pass,
including fixed-version S3 reads and the unchanged publication protocol.

The execution record links the final gate log, SQL and process records, part
receipts, Store input-edge audit and immutable adapter evidence. New capabilities
remain private. Slices 4c-4d, the parent Slice 4 gate and Slices 5-9 remain open;
public Help, exports, site documentation and persistence cutover remain Slice 8.
No commit, push or release was performed.

### Slice 3b review follow-up: 2026-09-08

The supplied review was checked against the owning contracts and fresh Runtime
evidence. The execution record now has an explicit `.gitignore` exception, so
the links above and in the acceptance matrix will resolve when this change is
committed.
Shared part-key alignment and builtin grain widths have one implementation;
component schema dispatch is a typed table, and malformed catalog identity
separators follow the existing structured error. The unproducible `allocated`
partition label is removed from decoding/admission. Actual governed allocated
contribution values remain foldable through their proven disjoint partition.

No version-equality guard was added. The Observation Model explicitly permits
January membership to feed independently anchored February observations.
New snapshot/validity tests prove the checkpoint keeps its January members while
fresh versioned Metric facts are read from the correct source; removing that
required source blocks a new action. Entity-only uniqueness already has both
functional-path construction proof and realized-row validation. A filter that
incidentally leaves one row cannot manufacture a missing proof. Existing
sampling realization, retained state and approximation disclosure survive
Population/Metric checkpoints and cold recovery.

Eleven additional Runtime cases cover these boundaries, exact mean folds with
unequal retained counts/coverage, governed `40+60=100` allocation with selected
value `40`, and a real 20,000-row object checkpoint with two retained parts.
The latter continues after source removal under unchanged local limits and
still rejects implicit object-membership import into a source. Backend-specific
Ibis/pandas interpreters and calendar lowering remain separate; no generic
interpreter, speculative allocation API or new approximation field was added.

Fresh `make check-agent` passes lint/import contracts, typing for **370 modules**,
**6,363 tests in 462.66 seconds**, and API documentation construction. Real MinIO
is enabled; no S3 acceptance test is skipped. All four fresh three-process
journeys match the **713-file** candidate SHA-256
`34c8cb9458d0e83529092e6279639f68c3ac2a02302b246ef319416efeb6f78b`.
The [execution record](../plans/2026-09-08-lazy-analysis-slice-3b-execution.md#review-follow-up-2026-09-08)
contains every review disposition and the refreshed evidence locations. This
follow-up supersedes the initial 3b candidate evidence; Slice 3b and parent
Slice 3 remain complete. No commit, push, release or public cutover occurred.

## Slice 4: Compiler Boundaries and Runtime Reliability

### Outcome

Complete deterministic source-prefix compilation, bounded pandas continuations,
additional Artifact storage/read paths, and runtime/read-surface reliability.
Runtime retains its reliability contracts without a cost planner,
internal DuckDB executor or storage-negotiation subsystem.

### Required sub-slices

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 4a: guarded local execution foundation | 2b, 3a | Authorize PyArrow primary/part reads, select exact pandas continuations before data work, and enforce complete-input, intermediate, output, and deadline guards. Use already registered Metric row operations to prove direct private DataFrame handoffs and no return to a source after the local frontier. This unit unblocks 3b; it does not implement Forecast. |
| 4b: engine and object Artifact adapters | 4a | Add configured engine/object writers and readers to 2b's final receipt/publication protocol. Prove immutable round trips, required-part access, mutation and reservation failure handling, and an engine-backed identity checkpoint that can feed its admitted source domain. This unit also unblocks 3b. |
| 4c: concurrency and cold reconciliation | 4b, 3b | Extend the reference-route guarantees to all admitted adapters, multi-process/thread/reentrant contention, name/current-pointer races, lost acknowledgement, exact cold termination proofs, conservative Session-local blocking for unknown S3 requests, and harmless deferred cleanup. Prove same-Session isolation without blocking unrelated Sessions; no automatic S3 fencing is required. |
| 4d: private Session and runtime reads | 4c | Assemble the retained Session/Run/Artifact/Finding read models against v3 through private factories. Cover history/inspection, `runs`, `get_run`, Artifact opening, Finding pagination, read-only behavior, three-axis inspection, and same-Store foreign Artifact/Graph boundaries without scanning unrelated rows or parts. |

Slice 2 owns the minimal final runtime protocol; 4a-4d extend its exact seams.
The complete Forecast family, models, uncertainty, and local execution proof
belong to 6b. Slice 4 must not introduce an unregistered forecast recipe or a
temporary Forecast output merely to exercise the worker.

The owner-approved 2026-09-08 allocation keeps independent source-domain
identity, real branch execution and registered unary local continuations in
Slice 4. Slice 5a owns the first registered multi-input local consumer, its
explicit operand roles, and combined-input collection validation and budgets.
These obligations remain mandatory at 5a's gate; they are not inferred from
Slice 4's unary evidence.

### Owned implementation

- typed graph validation and deterministic traversal of registered source support;
- closed invocation coverage includes `MetricRollupInvocationV1`; window and
  lag expression preparation stays inside exact owner builders rather than
  adding standalone semantic nodes or public capabilities;
- contiguous eligible same-domain Ibis construction, exact Marivo binding identity
  and immutable engine Artifact scans;
- required shared-sample fences without general CSE or occurrence interning;
- authorized PyArrow local/object Artifact readers and exact pandas
  continuations for already registered Metric operations;
- actual Arrow source/storage validation and complete guarded local inputs,
  including each unary input and all its required retained parts;
- private DataFrame handoffs without per-operator serialization;
- Runtime-owned source/local/storage budgets, intermediate-memory and method-size
  guards, worker cancellation and staging;
- one configured local/engine/object target and exact writer/receipt protocol;
- independent source branches feeding registered unary pandas consumers,
  without federation discovery, local-output upload, failed-query retry or
  sink-ranking handshake;
- execution keys, unique Artifact lookup, Session writer guards, normalized
  Artifact descriptors, one metadata publication transaction, external-resource
  obligations, cleanup, and Session-scoped cold reconciliation;
- captured source-binding validation, key digests, compiler adapter handoff,
  and exhaustive raw-value redaction;
- explicit three-axis full integrity inspection without semantic/source comparison;
- explicit same-Store foreign Artifact reads/inputs with preserved ownership,
  consumer-scoped Run edges, and bounded external Graph nodes;
- private retained Session/Run/Artifact/Finding read assembly and committed
  Session graph projection, ready for facade activation in Slice 8.

### Required evidence

- typed graph/source-support/local-method coverage with Dataset Core definition
  identity, including required realization-sharing distinctions;
- one fully eligible same-domain relation query, counting necessary fences/checks/writes
  separately rather than imposing one SQL statement on every method;
- portable and adapter-specific source support, pre-data local-frontier selection,
  unsupported required methods and compile-rejected outcomes; no compile-and-catch
  split discovery, local retry or SQL postprocessing;
- independent equal-argument DuckDB datasource connections with conflicting
  same-named tables never fuse as one source domain; registered unary local
  continuations preserve each exact branch, and a source-required combination
  fails; the single multi-input consumer proof belongs to Slice 5a;
- actual Arrow integer-SUM widening, overflow, null/time/decimal/dictionary and
  bounded variable-width reader tests;
- pandas complete unary-input/retained-part, output, intermediate-memory,
  method-size and hard-deadline
  tests at and above the bound Runtime defaults/configuration;
- a local/object Parquet input above the local collection cap fails before its
  consuming pandas step; a pure storage stream can independently exceed that cap
  within its storage budget, without promising an admissible local continuation;
- late streaming failure discards private state with no partial publication;
- each selected Metric local step runs only after complete input validation and
  its continuation receives private DataFrames without upload or Arrow round
  trips; the numerical Forecast worker journey is added in 6b;
- a source-supported operator following a local frontier stays local; independent
  source branches still execute and feed registered unary consumer roles;
- local Artifact continuations create no DuckDB connection or origin query;
- local, engine and object receipt round trips and mutation detection;
- one configured target validates or fails without choosing another sink or
  relocating computation;
- immediate same-Session writer rejection for same/different keys, across
  processes/threads and reentrant calls, with no new Run or contender queue;
- overlapping backend work across Sessions and isolation of blocked recovery;
- canonical name creation races and transactional current-pointer updates;
- reservation-before-create and crash injection for every external resource;
- atomic metadata rollback, committed-bundle recovery without repair, and
  lost-acknowledgement readback without guessed cleanup or failure;
- surviving backend work blocks only its Session until terminal/fencing proof;
  harmless leftover cleanup does not block publication or later work; a released local lock is not remote termination;
- pure memory buffers create no durable obligations; spills and external work do;
- exact execution-binding recovery with no new Run, SQL, quality extraction,
  storage copy, Evidence, or Artifact;
- source binding execution after authoring-scope exit, changed-value key
  separation, same-value cold reconstruction recovery, and proof of no
  `ContextVar` or mutable Session lookup during execution;
- full inspection reports Artifact, storage, and Evidence integrity independently,
  without current semantic/source comparison or reuse approval;
- retained Session/history/Run reads reconstruct their exact closed variants
  from v3, preserve deterministic pagination and read-only availability, and
  never decode Frame metadata or consult a separate Evidence database;
- Artifact opening validates metadata only, Finding pages validate selected
  records, and previews/operators read only their required payload/part closure;
  ordinary reads do not silently become full integrity inspections;
- no credential, SQL, raw identity, private plan, locator, or staging path leaks
  into diagnostics.

### Exit gate

Every resolved admitted execution ends with exactly one committed recoverable
Dataset or one terminal failed Run with no partial publication. Unresolved
outcome or termination follows the owning recovery-blocked protocol rather than
guessing failure. All retained runtime reads consume the final private v3 read
model. No private exchange is visible as an Artifact, Dataset, Evidence owner,
or graph node.

### Slice 4a acceptance record: 2026-09-08

**Slice 4a is implemented and accepted. Slices 4b-4d, Slice 3b and the parent
Slice 4 gate remain open.** Work began on 2026-09-07 from clean Slice 3a commit
`5e63a7f1` on `lazy-dataset`. The execution record is
[Slice 4a execution record](../plans/2026-09-07-lazy-analysis-slice-4a-execution.md).

Implemented and verified:

- Private deterministic placement preserves the maximal eligible source prefix
  and binds exact pandas row methods before data work. Support is pinned to the
  tested DuckDB 1.5.3 / Ibis 12.0.0 adapter pair; mismatched versions reject
  source-required roots before Run admission. This is adapter-specific support,
  with no portable source claim. Distinct owning bindings never
  fuse because their arguments or table names compare equal. Unknown support,
  source-required successors and actual compile/query failures never select a
  replacement executor. Exact execution-key hits bypass new placement.
- Core retains immutable input references to expose each existing row/row-set
  contract to the compiler without adding identity facts or persisting graphs.
  Local `where`, `metric`, `rank` and `limit` consume primary-only Metric shapes;
  contribution/sampling-part row transformations remain with Slice 3b. Independent
  tests cover null predicates, admitted NaN comparisons, all rank ties and
  directions, composite identities, time/Dimension partitions and limit prefixes.
- Authorized PyArrow primary/part reads validate complete actual schemas, keys,
  order, nullability and selected backing integrity. Combined inputs and parts,
  dictionary expansion, exact temporal/decimal/integer types, conversion,
  intermediate allocations, method scale, output, peak RSS and deadlines have
  independent guards. A 100001-row storage stream succeeds while its local
  collection is rejected before a pandas consumer runs.
- One terminable worker carries the complete local suffix. Adjacent operations
  exchange their private DataFrames directly. Worker creation is reserved before
  spawn; resolved failures prove worker and feeder termination before cleanup.
  Structured early input failures survive broken upload pipes; a blocked partial
  response remains subject to the supervisor deadline. Sorting and output
  validation share one direction/null comparator. A dead parent is not accepted
  as subprocess termination proof; the broader
  external-termination/fencing matrix remains Slice 4c.
- Artifact inputs inherit committed Population and semantic dependency authority,
  record exact consumer Run input edges and use the existing v3 writer,
  publication and committed-outcome readback. Local failures publish no partial
  Artifact/Evidence and leave no resources after proved termination.

Fresh final gates:

- `make check-agent`: lint and import contracts pass; mypy checks 360 files;
  **6180 tests pass in 233.25 seconds**; API documentation builds.
- Additional scoped typing passes for 16 reviewed implementation/test modules.
  Guard tests explicitly cover equality and overflow of configured method,
  output, allocation, deadline and worker-RSS limits. `git diff --check` passes.
- The three-process runtime acceptance creates a source Artifact, moves the
  source offline, executes `where -> rank -> limit -> metric` in a new interpreter,
  and reconstructs the same logical continuation in a third interpreter. The
  continuation uses no source queries, profile/credential resolution or internal
  DuckDB connection, and both guarded construction/placement paths record zero
  I/O attempts. Four local calls use direct DataFrame handoffs. Cold binding
  recovery adds no Run, Artifact, Evidence, storage copy or worker.
- Session `session_268534de5a244b13a9a14c4522a634e9` has exactly two terminal Runs,
  two Artifacts/Evidence envelopes, one Artifact-input edge, zero Findings and
  zero resource obligations. The local result is
  `artifact_1bcde72751e744f2835188da013d44a8`; its verified revenue/rank rows are
  `(100, 1)`, `(30, 2)` and `(10, 3)`.

The final 685-file ordered-content candidate SHA-256 is
`717ff00a041ecd3ad30c9fcade72017bbb48b6f1c812cbd6f2a049f2e4a07991`. The protocol is sorted UTF-8 path, NUL,
file bytes, NUL across library/test Python files and the selected configuration
files, excluding documentation and generated files. Matching before/after
manifests, runtime versions, source statements, v3 snapshots, worker handoffs,
termination and cold-recovery evidence were generated under the test temporary
directory and explicitly copied to local, ignored raw evidence at
`../plans/evidence/2026-09-08-slice-4a-review-runtime.json`. Default tests do not
write into the checkout. The reviewable execution record includes the compact
evidence and item-by-item review decisions.

No public export, Help, Session facade, public Store generation, or user-facing
API documentation switches in this unit. Engine/object adapters, full row/part
selection, fold/rollup, identity checkpoints and Forecast retain their later
slice ownership. No commit, push or release was made.

### Slice 4b acceptance record: 2026-09-08

**Slice 4b is implemented and accepted. Slice 3b, Slices 4c-4d and the parent
Slice 4 gate remain open.** Implementation began from clean accepted Slice 4a
commit `73e085da` with a clean working tree. That commit already contained
the Slice 4a work that had been uncommitted at planning time. The detailed scope, budgets, backend versions,
acceptance matrix and evidence are in the
[Slice 4b execution record](../plans/2026-09-08-lazy-analysis-slice-4b-execution.md).

Implemented and verified:

- Closed private local/engine/object receipts share the existing v3 reservation,
  finalization, ownership validation and atomic metadata publication. A Runtime
  captures one configured target and current access binding per action; binding
  hits recover the committed Artifact before new placement or configuration.
- DuckDB 1.5.3 / Ibis 12.0.0 writes primary and required parts to independently
  addressable database files from a shared native producer realization. File
  content digests fix exact versions after durable close; reads attach read-only
  and verify size, schema, counts and backing identity. Replacement, mutation,
  write attempts, combined budget overflow and reservation failures are covered.
- Versioned MinIO acceptance uses the pinned RELEASE.2025-09-07T16-13-09Z image
  and boto3/botocore 1.43.89. Bounded Parquet staging never becomes an intermediate
  Artifact. Exact data and manifest keys are reserved before PUT; receipts pin
  bounded manifests and every payload VersionId, size and digest. Readers use
  fixed-version bounded GETs, never latest-version or prefix discovery. New
  versions preserve old results; deleted or corrupted selected versions fail.
- Runtime-admitted engine Population scan leaves stop at committed identity and
  support non-versioned, unsampled same-domain `observe`. Primary-only retained
  Metric row methods can continue natively without origin access. Different
  source bindings and local/object Population inputs cannot implicitly enter
  the source domain; pandas results cannot upload into an engine target.
- Failures at reservation, writes, required parts, manifest finalization and
  publication expose no partial Artifact/Evidence. Exact-key collisions preserve
  foreign objects; lost commit acknowledgement recovers committed success.
  Unproved request termination remains recovery-blocked in its own Session;
  proven-terminal harmless garbage retains its cleanup obligation without
  blocking unrelated work. Access errors and exception chains redact injected
  credentials. Full concurrent and cold-recovery proof belongs to Slice 4c:
  exact termination where supported, and conservative Session-local blocking
  for genuinely unknown S3 requests, as selected in its execution record.

Initial gates: `make check-agent` passes lint/import contracts, typing for 365
modules, **6,251 tests in 169.77 seconds**, and API documentation construction.
Scoped implementation/test typing, Python formatting and `git diff --check`
also pass. The real object endpoint is enabled for this gate.

The engine and object journeys each use three fresh interpreters for production,
continuation and cold binding. Engine membership continues after its origin
table is dropped; object rows continue after the source file is moved offline.
Each journey ends with two terminal Runs, two Artifacts/Evidence envelopes, one
input edge and zero resource obligations. Cold reuse returns the same Artifact
with no new Run, query, object request, storage copy or worker.

The initial 697-file candidate SHA-256 is
`3f7935bf21d6ab8420de43964a2d8cbb33e1552f348e8b7cf76429b777e56012`. Its protocol and matching
before/after SQL, request, process and Store evidence are retained in the linked
execution record. These are library Runtime/service acceptance journeys; final
real-Agent acceptance remains Slice 9. No public export, Help, Session facade or
public persistence generation changes. No commit, push or release was made.

### Slice 4b review follow-up: 2026-09-08

The [execution record's review decisions](../plans/2026-09-08-lazy-analysis-slice-4b-execution.md#adversarial-review-decisions-2026-09-08)
classify every submitted finding. The accepted fixes release exact S3 termination
proofs only after durable journal removal, remove the storage/external-reader
import cycle and duplicate worker decoding, restrict serial Parquet decoding to
the object stream, require actual Runtime adapter versions, and centralize the
object prefix and closed Run-phase projection. Entry-state and service-port
wording are clarified.

The alleged missing native Metric projection is disproved by real two-metric
projection after origin removal: the output schema already drives the compiler's
selection. A real parameterized engine checkpoint also verifies that immutable
membership and a new observation's captured source values retain their separate
authorities. Private dev-only boto3 and content-version receipts remain consistent
with the authorized scope and owning design; no public extra or historical
writer-version reuse gate is added.

Fresh `make check-agent` passes lint/import contracts, typing for 366 modules,
**6,256 tests in 230.57 seconds**, and API documentation construction. Real engine
and object three-process journeys have matching 698-file before/after
candidate SHA-256 `b3770bb32139cc17a38c61fe7d4d06ecc6ac9ad5158cc291c777919947c8c763`. The linked record
retains the final SQL, fixed-version requests, process identities, exact Store
counts and cold binding results. It supersedes the initial 4b candidate evidence.
Slice 4b remains accepted; Slice 3b, Slices 4c-4d and parent Slice 4 remain open.
No commit, push, release or public surface switch was performed.

### Slice 4c acceptance record: 2026-09-08

**Slice 4c is implemented and accepted. Slice 4d and parent Slice 4 remain open.**
Implementation began from the accepted Slice 3b review, committed as `8f922646`
after planning. The [Slice 4c execution record](../plans/2026-09-08-lazy-analysis-slice-4c-execution.md)
freezes ownership, supported adapters, failure boundaries and reproduction.

- Every admitted target now has real same/different-key contention evidence
  across processes, threads and reentrant calls. Rejected contenders preserve
  producer diagnostics and create no Run, source query or resource reservation.
  Canonical-name races release candidate locks before acquiring the winner;
  activation/current-pointer updates remain transactional and cannot redirect
  existing handles. Three real DuckDB SQL barriers prove cross-Session overlap.
- Worker execution and its independent workspace are reserved before creation.
  One inherited locked file description covers child process lifetime and
  transfer threads. A fresh guarded process proves termination through the
  exact nonce-owned resource; an actual surviving orphan blocks only its own
  Session. After actual exit, recovery records `process_lost` and cleans exact
  resources. Harmless cleanup failures keep their obligations and allow work.
- S3 terminal responses durably discharge their request obligations before
  later callbacks or validation. Real forwarded PUTs with withheld responses
  cover SDK timeout and caller death. Unknown requests remain incomplete even
  with a healthy new client or absent key, without blocking other Sessions or
  committed reads. No automatic S3 fencing markers are added.
- One Session-scoped recovery snapshot validates all selected metadata before
  external cleanup. Atomic publication, authoritative lost-acknowledgement
  readback, required-part ownership and immutable committed outcomes are
  preserved. Source/planner/request proofs retire only after durable discharge.
- Fresh parameterized-source journeys cover all three targets: scopes end
  before execution, changed values bind different Artifacts, and same values
  recover with the source database and HTTP service offline. Execution-time
  ambient lookup is forbidden, and raw binding values do not enter diagnostics
  or persisted metadata.

The final `make check-agent`, with the isolated pinned MinIO service enabled,
passes lint/import contracts, typing for **371 source files**, **6,439 tests in
655.59 seconds**, and API documentation construction. No S3 tests are skipped.
All 22 modified/new Python files pass scoped typing and formatting checks.

The gate and **60 fresh Runtime records** bind the unchanged **723-file**
candidate SHA-256
`d2d8fcc4ed6c978fb7bd32e13b51f79b640c01bc07e628bc3c00eb66df1dbf39`.
The evidence includes 22 concurrency records, 27 adapter-crash/uncertainty
records, four worker records, three binding journeys and four refreshed 3b/4b
journeys. All 76 embedded candidate manifests agree. The execution record links
the complete check log and per-record hash index.

Only Slice 4c closes. Private read assembly, parent Slice 4, public exports,
Help, site documentation and persistence cutover remain assigned to their later
slices. No commit, push or release was performed.

### Slice 4c review follow-up: 2026-09-08

The [execution record's review decisions](../plans/2026-09-08-lazy-analysis-slice-4c-execution.md#review-follow-up-2026-09-08)
classify every submitted suggestion. Recovery integrity errors now retain the
reconciliation stage, selected producer identity and relevant repair guidance,
including failures in nested selected metadata decoders. Nine corruption cases
prove that these failures precede all external cleanup and preserve Store state.
The private termination-confirmation name and documentation disclose derived
proof retention; worker tests explicitly cover live-holder cleanup rejection
and retries after exact filesystem cleanup but before durable journal removal.

The follow-up also removes the duplicated candidate manifest and avoidable
test callback initialization dependency. A real-MinIO named-reopening case
proves that missing access bindings preserve terminal garbage and its journal;
restored explicit access removes only owned versions and preserves foreign ones.
Guarded reconciliation followed by atomic Session/current-pointer activation,
terminal HTTP error discharge, and conservative unknown-S3 blocking remain the
authorized boundaries. No automatic fencing or additional supervisor abstraction
is introduced.

Fresh `make check-agent` passes lint/import contracts, typing for **371 source
files**, **6,449 tests in 562.26 seconds**, and API documentation construction.
Real MinIO is enabled with no S3 skips; all 22 changed Python files also pass
scoped typing and formatting. The **60 fresh Runtime records** and **76 embedded
manifests** bind the unchanged **723-file** candidate SHA-256
`383a28ecf6367b378c813b736cbdac4d549829acf87ff5dc3b598208e0d4a251`.
The linked record retains the separate follow-up gate, complete log and evidence
index; these supersede the initial 4c candidate evidence. Slice 4c remains
accepted, while Slice 4d, parent Slice 4 and public cutover remain open.
No commit, push or release occurred.

### Slice 4d acceptance and composed parent gate: 2026-09-08

**Slice 4d and parent Slice 4 are implemented and accepted.** The
[4d execution record](../plans/2026-09-08-lazy-analysis-slice-4d-execution.md)
contains exact ownership, selectors, reproduction, the approved allocation and
the fresh 4a-4d/3b evidence index.

Private `session/_lazy_*` and `evidence/_dataset_*` modules now supply closed
Run/Finding/Artifact/history/Graph read models. Existing-v3 factories do not
initialize, activate or reconcile state. Operation-scoped reads select one
snapshot and decode only selected dependencies; Run-only reads do not decode
an output Artifact body, and ordinary Artifact reads do not scan Findings or
backing parts. Full inspection independently checks metadata, all declared
storage and the complete Finding set under one deadline. Storage priority is
`mutated`, `missing`, `unauthorized`, `unknown`, preserving every issue.

Fresh `make check-agent` passes lint/import contracts, **379-file typing**,
**6,576 tests in 636.12 seconds**, and API documentation construction, with
versioned MinIO enabled and no S3 skips. All **41 touched Python files** pass
scoped typing/lint/formatting. The **67 Runtime records** and **90 embedded
candidate manifests** match the unchanged **742-file** candidate
`582e89c929afafd311e988a639841c8cce82d35afe211c2b88bec74148be3673`.
The real local/engine/object composed journeys include production, terminal
failure without partial publication, retained continuation, cross-Session
consumption, cold reads and exact no-op binding reuse. Prior 4a-4c and relevant
3b paths were rerun on this same candidate.

The two parent evidence additions prove actual Arrow integer-SUM widening and
atomic overflow failure, plus independent equal-argument DuckDB connections
whose conflicting `orders` values remain distinct through registered unary
continuations. On 2026-09-08 the owner approved assigning multi-input consumption
and combined-budget acceptance to Slice 5a's first registered comparison. Its
explicit roles, complete operand validation, combined resource guards and real
independent-branch consumer journey remain required by the 5a gate below.
The approved allocation and the unchanged full-gate candidate close 4d and
parent Slice 4 together; unary evidence is not relabeled as multi-input proof.
The allocation changes documentation only, and the current candidate and all
indexed evidence hashes were reverified without repeating the unchanged suite.
Public facade/exports/Help/site switching remains Slice 8 and final public
real-Agent acceptance remains Slice 9.
No commit, push or release occurred.

### Slice 4d review follow-up acceptance: 2026-09-08

The [review disposition and final evidence](../plans/2026-09-08-lazy-analysis-slice-4d-execution.md#review-disposition-2026-09-08)
close the requested review on the **745-file** candidate
`6f154184ace46a2f7f02a4888693c04cc28b1fae34d709a28b09931f1ceb2361`.
Private read arguments now use structured errors, malformed cursors have safe
diagnostic chains, the history invariant is typed, and object target selection
retains writer repair while read authority remains independently classified.
The required private issue severity shape is documented without legacy decoding;
real corrupted-Store tests disprove the alleged failed-Run output bypass.

Fresh `make check-agent` passes lint/import contracts, **379-file typing**,
**5,884 daily tests**, **721 Runtime tests**, and API documentation construction.
The three Help environment tests moved by the parallel performance change also
pass as a supplement, preserving the earlier scope: **6,608 tests, zero skips**,
with the separate pinned MinIO service enabled. All **67 fresh Runtime records**,
**90 embedded manifests**, source manifests and test-configuration hashes bind
the same unchanged candidate. The exact daily/Runtime union is independently
verified. The record distinguishes the successful 12-file review typing check
from an exploratory broader check's unchanged older test diagnostics.

This gate supersedes the initial 4d candidate for the reviewed source and keeps
4d and parent Slice 4 accepted. The two attempts affected by concurrent edits
remain excluded. Multi-input consumption and combined-budget proof stay assigned
to Slice 5a; public cutover and public Agent acceptance remain Slices 8 and 9.
No commit, push or release occurred.

## Slice 5: Compare and Attribution Vertical

### Outcome

Execute, materialize, recover, and continue the arity-one Metric chain:

```python
delta = current.compare(baseline, alignment=mv.window_bucket())
drivers = delta.attribute(axes=(region, channel), mode="joint")
materialized = drivers.execute()
```

### Required sub-slices

These units consume completed Slices 3-4 and the exact Typed Operators
contracts. Each method owns its numerical proof and failure boundaries; one
successful attribution method cannot stand in for the others.

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 5a: Metric comparison (complete) | 3b, 4d | Produce and cold-recover Delta for each admitted alignment and input-authority topology. Prove realization sharing, scope/key preservation, one-sided and unavailable values, and immutable materialized operands. Own the first registered multi-input local consumer, explicit operand roles, complete combined-input validation/budgets, and the real independent-source comparison journey allocated from parent Slice 4. |
| 5b: additive and component-mix attribution | 5a | Produce reconciled joint/hierarchy Attribution with exact component/partition admission, logical axis expansion, retained-state barriers, Top-K/Other/null masks, and scoped Finding identity. |
| 5c: distinct-membership attribution (complete) | 5b's shared attribution contracts | Execute exact source-private membership preparation/allocation. Prove independent numerical reconciliation, sufficient-state admission, cold recovery, and identity redaction without origin replay or local raw-key transfer. |
| 5d: distribution-Shapley attribution (complete) | 5b's shared attribution contracts | Execute exact source-side distribution/coalition work and its admitted bounded local combination. Prove independent numerical results, mapped-player limits, Top-K/Other semantics, complete-input guards, and fail-closed missing/corrupt-state recovery. |

Slice 5d private implementation and technical acceptance are complete under its
[private execution record](../plans/2026-09-09-lazy-analysis-slice-5d-execution.md).
The owner-approved scope includes exact percentiles and explicit DuckDB T-Digest,
with weighted coalition side values and private semantic method selection.
Slice 8 owns the eventual public quantile declaration, Help, export inventory
and bilingual disclosure switch. The unchanged 830-file review-follow-up candidate
`10826fd69be2be4e4e6e308f9b91c251f3a88f7c7a3e2d86c32c7d5a7bbd8ed4`
passed `make check-agent` (6,321 tests, zero skips), strict touched-test typing
and 64 focused Runtime tests with zero skips. Two exact/T-Digest local recovery
records bind six processes on the same candidate. The follow-up proves explicit
source/worker cancellation, method-preserving projection and authored preview
order, and clarifies private state ownership. Historical object-storage evidence
remains in the execution record; the current test policy owns a separate object
connector smoke gate. This closes only private Slice 5d.

5c and 5d may proceed independently after their common contracts are frozen.
Their implementations extend the same Delta/Attribution registrations; neither
creates a parallel family, continuation table, or publication mechanism.

Slice 5a's private technical gate is recorded in its
[private execution record](../plans/2026-09-08-lazy-analysis-slice-5a-execution.md).
The accepted Typed Operators clarification preserves exact Population membership
selection while permitting different Metric observation windows, and retains
valid Delta Findings with either `baseline_zero` or `delta_unavailable` relative
status. The unchanged candidate
`8477c76aebc13e75f9faef35ea4af64eddbe0d18318b8915816641630413d1bc`
passed `make check-agent` after review follow-up and pre-commit formatting on 2026-09-08:
5,932 default tests, 764 Runtime tests
with versioned MinIO and zero skips, full typing/lint and API documentation.
Its execution record binds 35 new comparison records and 66 adjacent Runtime
records to that candidate. This closes only 5a and its allocated multi-input
extension; parent Slice 5, public cutover and public Agent acceptance remain open.
This records technical completion under the owner's explicit implementation
instruction. Independent review approval and release approval are separate
decisions. Review follow-up fixes and their candidate-bound verification are
recorded in the same execution record.

Slice 5c's independent private gate is recorded in its
[execution record](../plans/2026-09-09-lazy-analysis-slice-5c-execution.md).
The unchanged 811-file review-follow-up candidate
`872f46dcc6bd1e7a3a0ce287acdd648720ca59f0090cc121e6c6626457cdf6e1`
passed `make check-agent` (6,276 default tests), touched-test typing and 54 focused
Runtime tests with zero skips and versioned MinIO. All 58 manifests in 29 Runtime
records match this candidate, including distinct operand orders, shared sampling,
source-private failure boundaries and local/engine/object cold reuse. Membership
checkpoints require a compatible engine target; identity-free final Attribution
uses the existing admitted writers. This closes only Slice 5c.
The review follow-up shares comparison ordinals, private membership metadata and
temporal bounds, repairs receipt-path false positives, and retains safe concrete
authority errors. Per-suggestion dispositions and the superseded initial gate
are recorded in the execution record.

### Owned implementation

- Metric `compare` variant and complete Delta row and row-set contracts;
- registered multi-input local execution with explicit operand roles and
  complete combined-input validation and collection budgets before invocation;
- alignment policies retained by the accepted operator contract;
- additive, component-mix, distinct-membership, and distribution-Shapley
  attribution variants;
- joint and hierarchy row and row-set contracts;
- comparison scope coordinates in every Attribution row key, including
  Entity/time and non-decomposed Dimensions, plus unambiguous Other/null masks;
- aligned Session Runtime Read Finding coordinates for comparison ordinals and
  nullable governed axes, with exact resolution/Other/active-mask discriminators;
- logical missing-axis expansion;
- materialized sufficient-statistic admission and barrier failure;
- Top-K/Other mapping and reconciliation;
- Delta/Attribution family materialization registrations;
- exact quality, Evidence, Finding, and retained-state contracts.

### Required evidence

- all-logical, all-materialized, and role-distinct mixed input topologies;
- one registered local comparison consuming exact independent equal-argument
  DuckDB connections with conflicting same-named tables; neither branch may be
  fused into the other source domain;
- both complete operands and every required retained part pass validation and
  the combined collection guard before the local consumer starts, including
  cases where each operand fits separately but their combined input exceeds
  the bound; an oversized or invalid later operand must prevent invocation;
- source-required cross-domain combinations fail before admission, and no
  source work resumes after the composed local frontier;
- on a fixed source, a finite non-null sampled scalar `d.compare(d)` shares one
  realization and yields zero delta; a comparison of separately constructed equal
  sampled branches has a different Core fingerprint and execution key and
  permits differing realizations without requiring every pair of samples to differ;
- an Artifact previously committed for the independent-branch comparison never
  satisfies the shared-branch request; reconstructing the same sharing topology
  in a fresh process recovers its own bound Artifact without source work;
- exact compatibility across Population, coordinates, Metric identity, scope,
  approximation, and Session;
- null, empty, one-sided, zero-denominator, non-finite, and boundary-time cases;
- numerical differential tests for Delta and every attribution method;
- registered source/local parity and source-prefix boundaries: eligible compare
  and attribution work stays in Ibis; an admitted pandas frontier keeps every
  dependent analysis step local with direct private DataFrame handoffs;
- exact reconciliation at every resolution;
- repeated region/channel values across comparison days remain separate keys
  and independently reconcile; unrequested coordinates never disappear;
- `component_mix` rejects overlapping/nonadditive numerator or denominator
  partitions, including distinct-buyers/order-count ratios, before arithmetic;
- Entity-scoped attribution is source-required and publishes no raw identity
  in Findings or metadata;
- non-Entity scoped Findings survive publication and cold reads with distinct
  comparison ordinals, real null, Other, and inactive hierarchy-axis cases;
  their canonical item keys never collide and invalid coordinate/mask pairs fail;
- distribution attribution eight-player boundary;
- materialized missing-axis failure before a Run with a copyable logical repair;
- no materialized-origin replay or current semantic join to recover a missing
  attribution axis;
- cold recovery preserves family, row contract, row-set contract, sufficient statistics, and
  exact derived continuations.

### Exit gate

One end-to-end current/baseline investigation produces a reconciled committed
Attribution Dataset, and the adjacent invalid materialization-barrier journey
fails locally without datasource work.

## Slice 6: Correlation, Forecast, and Discovery

### Outcome

Complete the remaining first-cutover typed Metric operator families:

- `metrics.correlate(...)`;
- `history.forecast(...)`;
- `discover.point_anomalies(...)`;
- `discover.interesting_windows(...)`;
- `discover.entity_outliers(...)`;
- `discover.period_shifts(...)`;
- `discover.driver_axes(...)`.

### Required sub-slices

Correlation and Forecast are independent result families. Discovery units share
the Candidate protocol but retain separate input shapes and scorer evidence.

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 6a: correlation | 3b, 4d | Produce and cold-recover Association for Pearson, Spearman, Kendall, and admitted lag searches. Prove aligned numeric pairs/counts, numerical references, unusable/no-valid-candidate behavior, source-private Entity preparation, local budgets, and descriptive search disclosure. |
| 6b: forecast | 3b, 4d | Own the complete Forecast registration and all accepted models. Prove certified history/future coordinates, model-specific innovations and horizon variance, insufficient-history rejection, guarded local execution, direct DataFrame continuations, nominal prediction disclosure, and all-or-nothing publication. |
| 6c: time discovery and common Candidate contracts | 3b, 4d; 5a for period shifts | Produce point-anomaly, interesting-window, and period-shift Candidates with independent scorer references, exact keys/order/limits, generated-field filtering, and evaluated-empty versus not-evaluated outcomes. |
| 6d: Entity-outlier membership input | 6c's common Candidate contracts; 3b | Produce/filter Entity-outlier Candidates and consume their exact identity projection through Metric `population=`. Prove selector ownership, source-required privacy, logical and engine-checkpoint consumption, and rejection of other Candidate shapes. |
| 6e: driver-axis screening | 6c's common Candidate contracts; 5a-5b | Produce scoped driver-axis Candidates from admitted additive Delta partitions. Prove screening coordinates, exact folds, deterministic keys, logical axis expansion, materialized barriers, and descriptive disclosure. |

Each unit exercises every input-authority case admitted by its owner, including
cold-recovered inputs where legal. Shared Candidate registration never grants
population-input admission beyond the exact Entity-outlier shape. The 6d Metric
continuation does not require Event/Lifecycle implementation from Slice 7.

### Slice 6a private acceptance (2026-09-09)

Slice 6a is complete on the preserved Slice 5d baseline `39e3ac7e`.
The [execution record](../plans/2026-09-09-lazy-analysis-slice-6a-execution.md)
owns the implementation inventory, numerical decisions and acceptance matrix.
The executable candidate is
`31524592d2f3280ba73cfaf0eb900cfc04d9e8d669d9a51bd05c48f334243635`.

Final evidence in `evidence/slice-6a/` binds the same before/after fingerprint:
`make check-agent` passed 6,365 default tests, full lint/import contracts,
production typing and API documentation; strict correlation test typing passed;
the focused Runtime gate passed 77 tests, including existing distribution and
retained Metric regression owners. Eight separate three-process journeys cover
engine/local/object Association output, origin-free continuation and exact cold
binding reuse with unchanged Run, Artifact, row, Evidence and Finding authority.
Versioned object acceptance used isolated MinIO and disposable buckets. Complete
pair guards, candidate/count equations, unusable lag preservation, coordinate
calendars, source-private Entity preparation, numerical references, atomic
failure/cancellation and retained search disclosure are covered by the linked
matrix and final logs.

Only Slice 6a is closed. This acceptance neither activates public exports,
Help or site documentation nor closes Slices 6b-6e. No commit, push, release or
full release Runtime gate was performed.

The subsequent authorized review follow-up supersedes that executable
candidate with
`3a51186475600b2957c5dbe806756e1d08909e636384f081781d2d2f548ab5de`
on preserved HEAD `06166dbf`. `evidence/slice-6a/review-1/` records all 6,381
default tests, nine-module strict test typing and all 81 focused Runtime tests
passing on that same candidate, including eight three-process recovery routes.
The execution record contains the disposition of every review suggestion.
The follow-up adds explicit selection-rule and pair-approximation Evidence,
shared selection/count rules, disclosed Finding-cap counts, corrected PairInput
errors and source Dimension-series/minimum-signed-lag regressions. It preserves
the owning spec's 1,000-Finding cap and existing input-role and complete-input
budget checks. Scope remains private Slice 6a only.

### Slice 6b private acceptance (2026-09-09)

Slice 6b is complete on preserved Slice 6a commit `ca9b22b5`. Its
[execution record](../plans/2026-09-09-lazy-analysis-slice-6b-execution.md)
owns the implementation, numerical decisions and acceptance matrix. The final
executable candidate is
`016e0648cd86c6aac7fdb60dbbbb9085040d30202baf9be58ce4e962cfc63880`.

`evidence/slice-6b/review-followup/` binds every final gate to that unchanged candidate:
`make check-agent` passed 6,429 default tests, full lint/import contracts,
production typing and API documentation; 11-module test typing passed;
the focused Runtime gate passed all 72 selected tests. Three independent
three-process journeys cover every named model, origin-free Forecast
continuation and exact cold binding reuse with unchanged committed authority.
Coverage includes model-specific variance references, complete panels and
certified custom periods, logical/engine/local history, direct DataFrame
successors, empty selection, sampling/percentile meaning, guards, cancellation,
atomic publication failures and corrupted cold claims. Native object SDK stubs
and real Store files prove successful version-pinned publication/read and
source-offline local continuation, plus pre-execution target denial.

The review follow-up consolidates worker summaries and version metadata,
preserves Metric sampling-only role checks, and pins cold interval rejection.
It also reruns affected Attribution and sampled Metric checkpoint regressions.
The execution record retains both the initial and superseding acceptance logs.

This acceptance closes only private Slice 6b. It does not close discovery,
activate public exports/Help/site documentation, run the full release Runtime
gate, or perform a commit, push or release.

### Slice 6c private acceptance (2026-09-09)

Slice 6c is complete on preserved Slice 6b HEAD
`16a56d19898be9fa3424607743f97ef6a88f6262`. Its
[execution record](../plans/2026-09-09-lazy-analysis-slice-6c-execution.md)
owns the exact file scope, interface and numerical decisions, shared seams,
review fixes and acceptance matrix. The initially accepted 877-file executable candidate is
`ff1a7069f3c98f6db113bf490d03c39eeaf9c664a08637e8ee960c529521121e`.

`evidence/slice-6c/final/` binds every terminal gate to that unchanged candidate:
210 focused tests, 14-module strict test typing, `make check-agent` with 6,560
default tests and all lint/import/type/API-documentation stages, and 126 focused
Runtime tests passed. Runtime includes Forecast, comparison and retained-row
regressions. Three independent three-process journeys prove point-anomaly,
interesting-window and period-shift production, source-offline continuation and
exact cold binding reuse, including structured reason tuples and unchanged
committed authority.

The private implementation includes the non-callable discovery namespaces,
three paired Candidate shapes, typed identities and pre-limit uniqueness,
independent population-score and continuous-window references, generated-scalar
selection, complete-input budgets, local DataFrame successors, original search
Evidence, atomic zero-Finding publication and cold validation. Coverage includes
missing buckets, captured custom calendars, paired Delta endpoints, evaluated
empty versus no evaluable series, fractional constants, overflow, sampling,
cancellation, source failures, transaction faults and corrupted metadata. Native
SDK stubs with real Store files verify version-pinned object publication,
retained object inputs and offline continuation without a MinIO service.

The authorized review follow-up supersedes that executable candidate with
`cc1cd9d7a558e7be7d867e515b78d028f6884fa68fa3b7c468943801c6420a6f`.
`evidence/slice-6c/review-followup/` records all 214 focused tests, 14-module
strict test typing, the complete `make check-agent` gate with 6,564 default
tests, and the same 126 Runtime tests passing on that unchanged candidate.
All three independent recovery journeys were repeated and retained. The changes
share discovery disclosure between contracts and cards, replace default summary
repr with bounded scalar facts, move definition/evaluation as one immutable
worker summary, simplify the exact numeric type check, correct Delta receiver
documentation and add four literal mixed-sign peak-tie regressions. Scorers and
persisted Evidence schemas remain unchanged. The execution record explains each
adopted or retained review suggestion and preserves the initial evidence.

This acceptance closes only private Slice 6c. Entity-outlier, driver-axis and
public activation remain Slices 6d, 6e and 8. No public export, Help entry, current
site documentation, commit, push, release or full release Runtime gate is added.

### Slice 6d private acceptance (2026-09-09)

Slice 6d is complete on preserved Slice 6c HEAD
`e146a279591763c87703290fc1bc6cb70f87275e`. Its
[execution record](../plans/2026-09-09-lazy-analysis-slice-6d-execution.md)
owns the exact numerical amendment, implementation boundaries, review
disposition and acceptance matrix. The initially accepted 886-file executable candidate is
`69835ea9397f8a4f07b8200440f76f0370ef63cfd755908c3431f0a880c9275b`.

`evidence/slice-6d/final/` binds every final gate to that unchanged candidate:
196 focused tests, 18-module strict test typing, `make check-agent` with 6,625
default tests and all lint/import/type/API-documentation stages, and 117 focused
Runtime tests passed. Runtime includes time Candidate, retained membership,
retained Metric and engine adapter regressions.

The private implementation includes exact Entity Candidate registration and
selectors, native MAD/fallback scoring, source-private frozen input and scalar
proofs, exact identity projection into Metric `population=`, independent
observation scope, sampling inheritance, identity-safe atomic publication and
cold descriptor validation. A three-process journey proves source-offline
Candidate selection and observation plus exact cold binding without new Runs or
queries. Separate real execution scores an engine Metric checkpoint after its
origin is removed. Native SDK stubs with real Store files cover version-pinned
object output and terminal reads. Local/object identity continuations and
foreign engine domains reject before a Run is admitted; failures never retry
through local identity computation.

The supplied review follow-up clarifies the pytest versus gate timing and adds
one membership comment. The resulting 886-file byte fingerprint is
`8cd7bc7cd27adc001e4c38d27d73e32f1a5038a553d7819b73fc8c2e3346db74`.
`evidence/slice-6d/review-followup/` proves the sole source change preserves its
Python AST and passes focused lint/import checks and whitespace validation.
The original full gates remain bound to the initial fingerprint; this
documentation/comment follow-up does not claim a repeated Runtime gate.

This acceptance closes only private Slice 6d. Driver-axis screening, integrated
Event/Lifecycle membership and public activation remain Slices 6e, 7 and 8.
No public export, Help entry, current site documentation, commit, push, release,
MinIO service or full release Runtime gate is added.

### Slice 6e private acceptance (2026-09-10)

Slice 6e is complete on preserved Slice 6d HEAD
`930fd5fb7bdf61d035678ed2301bd0188cc6a4f7`. Its
[execution record](../plans/2026-09-09-lazy-analysis-slice-6e-execution.md)
owns the complete-partition cardinality amendment, numerical decisions, review
repairs and acceptance matrix. Every final gate uses the unchanged 904-file
executable candidate
`22b754e3b1f55326a615ff9fda1d8a13906adb9352c35b73235df0948347269f`.

`evidence/slice-6e/final/` records 363 focused regression tests, strict typing for
14 changed/new test modules, `make check-agent` with 6,748 default tests and all
format/lint/import/type/API-documentation stages, and 149 focused Runtime tests.
All gates pass with matching before/after fingerprints. An independent review
reproduced and verified the numerical and cold-metadata fixes; no findings
remain open.

The private implementation produces `candidate/driver-axis@v1` from complete
additive Delta partitions. Actual null and zero-contribution members count in
cardinality; exact per-axis folds and the minimum 50% absolute-contribution
prefix determine descriptive concentration scores. Native and local arithmetic
agree through cancellation, small net Delta, Decimal, large integers,
subnormal values and finite extreme-value sums. Full coordinate and digest
uniqueness is checked before limit. Complete zero partitions evaluate without
emitting candidates; unavailable or incomplete inputs fail.

Logical missing-axis expansion preserves selections, shared sampling, captures,
paired time fields and original ordinals across source branches and admitted
local frontiers. Retained barriers stop expansion. Entity production and row
continuations remain source-required, with compatible engine continuation and
no Driver population-input admission. Atomic publication includes original
search Evidence, zero Findings, binding and successful Run; row operations
preserve that original search authority. SDK stubs and real Stores cover object
receipts and identity barriers. Two three-process journeys prove source-offline
continuation and cold exact binding without new Runs or queries.

Only private Slice 6e is closed here. Its fold/expansion/retained-state coverage
does not close Slice 5b's independent acceptance. Integrated Event/Lifecycle
membership and public activation remain Slices 7 and 8. No public export, Help,
current bilingual site documentation, eager deletion, commit, push, release,
MinIO service or full release Runtime gate is included.

The subsequent Spec-alignment review follow-up clarifies the 361-to-363 test
count, verifies all untracked files for whitespace, independently compares the
saved manifest and documents internal Entity membership deduplication. Its
904-file byte fingerprint is
`e4dc3987a57eaf795d752f66032a448a9f6e150387d885d5d87a08a54cd3f07c`.
`evidence/slice-6e/review-followup/` proves the sole source edit is a comment
with an unchanged Python AST and passing focused lint/import and whitespace
checks. The original full gates remain bound to `22b754e3…7269f`.

The consolidated adversarial review additionally confirms that an unscoped
empty global aggregate needs the positive-cardinality filter and documents the
shared floating reconciliation tolerance. Its comment-only 904-file fingerprint
is `6389902905c4aded33fb5cf5cd1c8b1cf3d47db4e5e4af953d60d069a17dc3dd`.
`evidence/slice-6e/review-followup-2/` records the actual empty-aggregate probe,
unchanged Python ASTs and passing focused lint/import and whitespace checks;
the original full acceptance gates remain bound to their original fingerprint.

### Owned implementation

For every exact variant:

- invocation and output registration;
- complete pre-execution row and row-set contracts;
- action-time requirements;
- tested source-lowering support, exact registered pandas method where admitted,
  and any required source preparation or source-private identity restriction;
- approximation/status semantics;
- filterable generated fields;
- quality, Evidence, Finding, and retained-state materialization registration;
- structured repairs and derived continuation admission.

### Required evidence

- registry snapshots and exact capability/lowerer/materialization links;
- numerical differential tests for Pearson, Spearman, Kendall, lag candidates,
  every forecast model, interval construction, and every discovery scorer;
- null, constant, empty, insufficient, non-finite, tie, partial-period,
  missing-bucket, and approximation cases;
- correlation publishes typed unusable rows but fails when a Metric pair has no
  valid candidate;
- forecast rejects uncertified/incomplete histories and never imputes silently;
- model-specific innovation, degrees-of-freedom and multi-step variance
  references cover naive `[1,2,3]`, nonconstant drift and seasonal horizons on
  both sides of a season boundary; insufficient variance authority never
  becomes a zero-width interval;
- scoped driver-axis candidates preserve screening coordinates and deterministic
  keys; correlation search/pair counts and Candidate scores retain their
  descriptive, non-inferential meaning;
- Kendall consumes complete engine-aligned numeric pairs without raw Entity
  identities and fails its bound local budget without sampling;
- source-support traversal selects the local frontier without compilation
  exceptions; source compile or execution failure never invokes a local retry;
- discovery distinguishes no candidate from not evaluated;
- entity-outlier identity remains private but can be consumed explicitly as a
  Population input;
- source/storage boundaries validate exact Arrow contracts, and local steps
  pass private DataFrames directly under exact schemas without public pandas inputs;
- a selected forecast method executes once after complete input validation;
  its registered relational successors remain local and receive private
  DataFrames without upload, Arrow round trips, or an internal DuckDB connection;
- source-required high-cardinality or identity work stays in the engine or fails;
  every admitted local method validates its full input and required retained state;
- no statistical-test type, method, Help target, semantic node, or lowerer is
  registered;
- removed discovery objectives and old name have no alias.

### Exit gate

Every accepted Metric operator produces one recoverable Dataset family with
exact status and Evidence authority, and every removed operator is absent from
the private target registry.

## Slice 7: Subject Selection, Event, Lifecycle, and Cross-Domain Loops

### Outcome

Privately execute the full identity-preserving loop:

```text
Metric or entity-outlier Dataset
  -> explicit population= on Event/Lifecycle source
  -> Event journey or Lifecycle history Dataset
  -> typed select_subjects(...)
  -> PopulationDataset
  -> explicit population= on Metric/Event/Lifecycle source
```

### Required sub-slices

Event matching and Lifecycle replay are distinct engines with separate
canonical outputs. Their units share only the governed identity and Population
seams; Event-specific Delta/Attribution variants consume Slice 5's common
protocol without acquiring its Metric-only admission rules.

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 7a: Event matching and journey authority — complete | 3b, 4d | Private shared subject-identity admission, Event completeness, all three matching policies, and journey materialization/recovery passed; [exact implementation and gates](../plans/2026-09-10-lazy-analysis-slice-7a-execution.md). |
| 7b: Event reducers and subject selection — complete | 7a | Private logical and recovered engine funnel, time-to-event and complete DroppedBefore selection passed, including no rematching, exact reach propagation, local result filtering and the Metric -> Event -> Population -> Metric loop; [exact implementation and gates](../plans/2026-09-10-lazy-analysis-slice-7b-execution.md). |
| 7c: Event funnel comparison and attribution — complete | 7b; 5a-5b's shared contracts | Private complete-journey comparison and loss-rate attribution passed compatibility, scoped endpoint reconciliation, native/compact-local parity, cold recovery and atomic rejection without rematching; [exact implementation and gates](../plans/2026-09-10-lazy-analysis-slice-7c-execution.md). |
| 7d: Lifecycle replay and canonical retention — complete | 7a's shared identity seam; 4d | Private native replay and source-offline recovery passed inception/coverage, lossless transition and violation traces, same-time confluence, empty histories, exact membership and atomic required-part failure/cancellation; [implementation and frozen gates](../plans/2026-09-11-lazy-analysis-slice-7d-execution.md). |
| 7e: Lifecycle reducers and subject selection | 7d; 7b for the Event continuation | Execute distribution, transitions, dwell, violations, and in-state selection from recovered history. Prove exact part consumption, clipped-duration meaning, coverage-sensitive membership, no trigger replay, and continuation into Metric and Event sources. |

7d can proceed independently of 7b-7c after its named prerequisites pass. The
complete loop starting from an Entity-outlier Candidate additionally consumes
6d; the shared privacy audit and integrated Slice 7 gate cover that input too.

### Slice 7a private acceptance

The [Slice 7a execution record](../plans/2026-09-10-lazy-analysis-slice-7a-execution.md)
closes the private Event matching/completeness and journey recovery unit against
928-file executable candidate
`651abecb217dcbd10dbfcef5b3bc1a39b2c6087f149614999f0f3068840f7728`.
Follow-up review evidence under `evidence/slice-7a/review-suggestions/` records 144 focused default tests,
6,892 default tests through `make check-agent`, 56 Event Runtime tests, 80 shared
membership/materialization Runtime tests, and 13-file explicit test typing.
All gates preserve the same candidate fingerprint. Coverage-provider source queries
now share the DuckDB execution deadline; real interruption regressions verify
atomic failure and same-Session retry. The initial candidate evidence remains
under `evidence/slice-7a/`, with the provider-deadline correction separately
retained under `evidence/slice-7a/review-fix/`. The execution record includes
the follow-up review disposition and publication trust boundary.

The admitted source adapter is native DuckDB. Both local-file and engine journey
receipts have separate-process production, origin-offline continuation and exact
cold binding evidence. Occurrence-time version admission, provider/declaration
coverage, dense assignments, identity privacy and atomic failures are covered.
Independent review findings were repaired and reverified. Reducers/selection,
Lifecycle, public disclosure and the integrated D/L/M journeys retain their
separate downstream gates.

### Slice 7b private acceptance

The [Slice 7b execution record](../plans/2026-09-10-lazy-analysis-slice-7b-execution.md)
closes only private Event reducers and subject selection against the 944-file
executable candidate
`d74bcafbc1e1fccdd0f41ff2121968b22768c3dc4ba32a4c84d55bd4c5fd4c6f`.
Fresh review-fix evidence under `evidence/slice-7b/review-fix/` records 245 focused default tests, 22-file
explicit Event test typing, `make check-agent` with 6,992 default tests and
456-file source typing, 88 Event Runtime tests including 7a regressions, and
80 shared membership/materialization Runtime tests. Every gate preserved the
candidate before and after execution; Runtime used at most two workers.

Three separate processes prove journey production, reducers/selection after
occurrence-source deletion, and exact cold binding reuse. Coverage truth remains
bound to each input node across nested Event/Population chains. Complete empty
selection publishes; unknown selection cannot be hidden by filtering, sampling
or downstream Metric membership. Local Parquet reducer results support exact
retained filtering, including null durations, empty rows and inherited sampling.
The earlier axis-name collision finding and the subsequent selected-Population
Metric Dimension failure are fixed and reverified. New tests also prove that
unknown entry propagates from an earlier missing step even when from_step itself
is covered; the proposed single-step coverage replacement was therefore rejected.
The execution record distinguishes historical independent review, supplemental
implementation-agent review and the current review-resolution gates. The frozen
record includes privacy and atomic failure/cancellation evidence. Historical
candidate `95b98ac1...` remains under `evidence/slice-7b/`. Lifecycle, Slice 5b's independent gate,
the integrated public D/L/M journeys and public disclosure remain separately gated.

### Slice 7c observed acceptance (2026-09-11)

Private Event funnel comparison and `FunnelLossRate` attribution passed on the
956-file executable candidate `74ccc5e85fbf42653f27a77a991b2f3cd9a4cb7ce345078d65414e9ba9e55b07`.
The [execution record](../plans/2026-09-10-lazy-analysis-slice-7c-execution.md)
binds 283 focused default checks, 26-file explicit test typing, 325-file scoped
typing, `make check-agent` (7,029 defaults and 464-file source typing), 108 Event
Runtime checks and 80 shared materialization/membership Runtime checks. All six
gates preserve matching before/after hashes; Runtime uses at most two workers.

Native Ibis and compact pandas agree on complete outputs, status and ordering,
including nonzero shifted cohorts. Complete mapped components independently
reproduce both endpoint rates and each resolution's delta. Three interpreters
prove retained journey continuation after occurrence-source deletion and exact
cold binding with no execution queries. Censoring, removed targets, aggregate
checkpoints, missing/corrupt authority, combined input budgets, failure and
cancellation are covered without partial publication or identity disclosure.

Read-only review of the preceding `bc472773...` candidate exposed filtered
checkpoint censoring bypass and native pool/axis name collisions. Both are
fixed in the preserved six-gate run in `evidence/slice-7c/review-fix/` and remain
covered by the current candidate.
Filtered funnel checkpoints are rejected using exact producer authority;
unfiltered checkpoints remain admitted. The versioned checkpoint-scope rule
prevents pre-fix comparison cache reuse. Native pool expressions bind their own
relation and preserve governed `positive`/`negative` axes with full pandas parity.

The 2026-09-11 adversarial-review follow-up corrects `method` and `causal_claim`
to the closed `method_identity` role, documents both compare baseline parameters,
and adds native per-side duplicate-coordinate proofs matching pandas rejection.
Both arithmetic backends consume one domain-owned component-name tuple. The
initial-step undefined-rate interpretation is explicit without growing the
status enum. The current six gates and three-process evidence live in
`evidence/slice-7c/adversarial-review/`; the execution record lists accepted and
deferred review suggestions, including the unmeasured Finding-ranking concern.

This closes only private Slice 7c. It consumes shared 5a–5b protocols without
closing Slice 5b's independent gate. Public exports, Help, bilingual site
activation, integrated D/L/M journeys and Lifecycle remain separately gated.
No commit, push or release work was performed.

### Slice 7d observed acceptance (2026-09-11)

Private Lifecycle replay and canonical retention are accepted on the 968-file
executable candidate
`489ac9e0573cb30fe7a0a10ed6ee25b9a0ba4b9d50d69a765f3d1086ede2624a`.
The [execution record](../plans/2026-09-11-lazy-analysis-slice-7d-execution.md)
defines the fingerprint and seven independently recorded gates in
`evidence/slice-7d/gates.json`: 175 focused tests, explicit typing of eight test
files, the full `make check-agent` gate (468 source files and 7,062 default
tests), 40 Lifecycle Runtime tests, 108 Event Runtime tests, 85 shared Runtime
tests and the final 233-test combined Runtime gate after the full check.
All gate fingerprints and log checksums match; Runtime concurrency is two.

Local Parquet and engine acceptance each use three independent interpreters.
Production, source-offline inspection and exact cold binding preserve the main
history, every mandatory role, descriptor and bounded Evidence without trigger
execution. Native SDK stubs cover object version ownership and per-role upload
failure/cancellation without starting an object service. Source-origin coverage,
inception lookback, same-time loops and confluence, empty history subjects,
membership, missing/corrupt parts, deadlines and retry cleanup are covered.

This closes only private Slice 7d. Slice 7e reducers and `InState`, public
exports, Help and bilingual site activation, and integrated public journeys
remain separately gated. No commit, push or release was performed.

The 2026-09-11 review-fix acceptance supersedes the original candidate with
`c5f4190f9ad4e5012af836914f85325211850970b1ca6e4fb42e48cc5e3e9b4f`
(968 executable files). Native confluence now checks all simultaneous
cross-Event interleavings while preserving each Event's own identity order;
corrupt retained model metadata now produces Artifact integrity repairs.
`evidence/slice-7d/review-fix/gates.json` records 181 focused tests, explicit
typing of five modified modules, `make check-agent` with 7,068 default tests and
468 source files, and 237 Runtime regressions after that broad gate. Every gate
retains matching fingerprints and verified logs. Both local and engine
three-process recovery proofs were refreshed against this candidate. The linked
execution record describes the fixes and preserved scope.

Additional review triage restores the unchanged Slice 7c Event semantic digest,
removes the unnecessary Event retained-demand boundary edit, corrects Lifecycle
Finding guidance, and removes positional SQL-term mutations. Broader strategy,
role and writer refactors are deferred with reasons in the execution record.
The current 968-file candidate is
`732f7d27075a62fad48928bd762ec95c62fffde18c417e3513880112b8621011`.
`evidence/slice-7d/review-triage/gates.json` verifies 187 focused tests, typing of
five modified modules, `make check-agent` with 7,069 default tests and 468 source
files, and 237 Runtime regressions with two workers. All candidate and log hashes
match; both independent-process recovery proofs were refreshed. Private Slice
7d remains complete, with public activation and Slice 7e still deferred.

### Owned implementation

- Subject identity and domain selection into Slice 2's sole Population family;
- Event pattern, matching, completeness, journey, funnel, time-to-event,
  selection, comparison, and funnel-attribution variants;
- Lifecycle replay, history, distribution, transitions, dwell, violations,
  and selection variants;
- complete continuation matrices;
- family filter registrations;
- identity-safe metadata and persistence;
- domain materialization contracts and all canonical retained parts:
  `lifecycle_legal_transition_trace`, `lifecycle_subject_coverage`, and
  `lifecycle_violation_trace`.

### Required evidence

- exact Session, Entity, composite-key, Population, sampling, scope, Pattern,
  StateModel, step, state, and axis admission matrices;
- matching differential tests for all accepted policies and occurrence edge
  cases;
- funnel density, zero-denominator, censoring, grouping reconciliation, and
  no-rematch tests;
- Event funnel comparison/attribution tests for exact Pattern/step/axis and
  Population compatibility, equal cohort duration and follow-up offsets,
  complete classification, full endpoint reproduction, and joint/hierarchy
  reconciliation under the Event-owned scope;
- independent Ibis/pandas compare/attribution conformance over complete compact
  additive components, without transferring raw journey identities locally;
- positive logical-funnel chains built from logical or explicit materialized
  journey assignments; reject censored comparison input, a materialized funnel
  Delta, and a logical Delta built from materialized funnel summaries, with a
  journey-checkpoint repair and no Event rematching;
- time-to-event complete, incomplete, repeated-attempt, and typed-step tests;
- selected-step eligibility distinguishes not-entered/unknown entry from an
  entered censored attempt; reaching the selected target completes that pair
  even when later Pattern steps are unfinished;
- replay inception, source-origin coverage, missing inception, no-trigger,
  illegal/simultaneous transition, clipping, terminal-state, and censoring
  tests;
- reducer numerical and structural parity;
- same-time legal loops remain countable after positive intervals are
  materialized; subjects with unknown inception/no interval retain the identity
  and coverage facts needed for grouped distribution;
- all history parts commit atomically, reject missing/corrupt required state
  without Event replay, and reproduce reducers after cold recovery;
- dwell reports only completed clipped window fragments, discloses left
  clipping, and never presents that conditional mean as whole-episode or
  all-entrant duration;
- structural journey/history filtering rejection and generated-field filter
  registration;
- logical same-plan Population semi-join with one membership evaluation;
- selected Population region filtering uses Module 2's common membership rule
  and cannot bypass an uncertain upstream selection;
- materialized PopulationDataset cold recovery with no origin graph;
- empty complete membership succeeds and uncertain membership publishes no
  PopulationDataset;
- high-cardinality Event/Lifecycle execution performs no unbounded local
  transfer;
- adversarial identity scan across errors, logs, Runs, cards, Evidence,
  Findings, telemetry, object names, and recovery diagnostics;
- raw identities appear only in authorized Dataset storage and explicit
  `show()`/`to_pandas()` row reads;
- identity selections reused with current sources have a configured same-domain
  engine target/reader; incompatible local/remote combinations fail before data
  work without a misleading materialization repair;
- Event/Lifecycle producer failure publishes no partial identity Artifact or
  Evidence.
- no occurrence-bounds source/read node, Run kind, result type, capability, or
  implicit window inference exists in the target registry.

### Exit gate

Metric, Event, and Lifecycle analysis compose through exact governed identity
without a DataFrame/list/file/SQL bridge, and every output is one same-family
recoverable Dataset. Failed or unresolved executions follow Slice 4's final
publication/recovery protocol without partial authority. Every sub-slice and
the complete cross-domain loops pass before this milestone closes.

## Slice 8: Atomic Public, Persistence, Help, and Documentation Cutover

### Outcome

Make the completed private Dataset implementation the only public analysis
surface and replace the persistence generation in the same unreleased change
set.

### Required sub-slices and entry gate

Slice 8 starts only after every unit in Slices 1-7 has passed. In particular,
target semantic normalizers must be implemented and privately exercised in
2a/3a, retained runtime reads must work through 4d's private v3 facade, and every
family must supply its complete native capability/Help inputs. Test fixtures,
type declarations, and a list of future Help targets are not that evidence.

| Unit | Prerequisite | Bounded outcome and independent gate |
| --- | --- | --- |
| 8a: cutover assembly and disclosure preparation | All units in 1-7 | Refresh the affected Slice 0 inventory against the current tree. Assemble exact public facade/export bindings, semantic activation, native Help, executable examples, current EN/ZH docs, skills, and deletion/test-replacement lists into one reviewable switch set. Identify any missing private implementation before activation. |
| 8b: atomic public and persistence activation | 8a | Apply the public, semantic, Store, Help, documentation, and old-path removal changes together. No intermediate public eager/lazy combination or partial persistence switch is a deliverable. |
| 8c: installed-surface verification | 8b | Verify the built package's exports, signatures, Help resolution/budgets, state protocol, current examples, generation rejection, and forbidden-path absence. Repair the coherent switch set before handing its exact revision to Slice 9. |

These units organize preparation and verification, not separate public releases.
Slice 8 activates already working semantics and reads; it must not absorb a
missing algorithm, first implementation of a v3 read model, or unresolved
publication contract. Return such work to its owning private unit and recheck
dependent evidence before completing the switch. Disclosure text may be
prepared privately earlier, but current user-facing guidance changes with 8b.

### Public API switch

The switch includes the Semantic Object Model amendment: `primary_key` becomes
Entity identity, `versioning` supplies historical row coordinates, and all
authoring validation, typed repairs, graph metadata, native Help, fixtures and
current EN/ZH examples change together. No author `rollup_safe`, `business_key`,
or physical-key alias is introduced. Source-only unkeyed Entities remain
ineligible for Population/subject identity, without becoming synthetic singletons.

1. Replace `marivo.analysis.__all__` with the exact accepted Dataset, policy,
   Session, runtime-read, Evidence, and helper inventory.
2. Publish the common and paired concrete Dataset classes and their exact
   focused Help leaves.
3. Change `Session.observe`, `SessionEvents.match`, and
   `SessionLifecycle.replay` to the lazy source contracts.
4. Retain `Session.source_bindings(...)` with construction-time capture only.
5. Add `Session.population` and publish
   `MetricDataset.rollup(drop_dimensions=..., grain=..., drop_time=...)`.
6. Remove every Session-owned downstream operator and reducer, including
   `session.events.occurrence_bounds(...)`.
7. Remove `EventOccurrenceBounds`, `frame.transform`, old
   Frame/Result/Selection classes, casts,
   DataFrame-like common behavior, and detached side-result families.
8. Remove generic hypothesis testing and the rejected discovery objectives.
9. Remove every compatibility alias, deprecation wrapper, dual signature, and
   old public module path.
10. Ensure all public functions and methods have concrete types and complete
   public docstrings.

### Persistence switch

Evidence modules retain typed extraction and reading responsibilities, but their
Store access receives the caller-owned transaction. They cannot open another
publication database or commit independently. Session writer guards and
SQLite transaction ownership are defined only by Module 4.

1. Set the replacement Session Store to exact `PRAGMA user_version = 3`.
2. Use these exact value contracts; normalized terminal, input, Evidence, and
   resource-obligation rows are versioned by Session Store v3 without redundant
   row schema strings. Full Run, Artifact, and Evidence envelopes are assembled
   from their owning relations; no duplicate full metadata payload is written:
   - `marivo.analysis_action_run/v2`;
   - `marivo.dataset_artifact/v1`;
   - `marivo.dataset_artifact_descriptor/v1`;
   - `marivo.dataset_storage_receipt/v1`;
   - `marivo.dataset_evidence/v1`.
   Store Artifact descriptors, Evidence, and Findings in the project Session
   database. Remove separate Evidence databases, Artifact metadata sidecars,
   commit markers, claims, leases, and independent Evidence commit ownership.
   All writers for one Session share its guard; different Sessions remain
   independently executable.
3. Delete old Frame Artifact and analysis Run writers/readers.
4. Reject v2, v0, and future Session Store generations before Session
   activation with a structured instruction to create a new named Session.
5. Do not modify or delete old Store files automatically.
6. Delete Frame-to-Dataset adapters, Run backfills, Artifact import, Evidence
   migration, dual graph reads, and fallback to `analysis-artifact/v13` or
   `marivo.analysis_run/v2`.

### Help and capability switch

1. Rebuild the capability registry from target family/source/operator/read
   registrations.
2. Add every exact Dataset Core, Observation, Typed Operator, and Module 6 Help
   leaf named by the owning designs.
3. Update the six analysis root hubs without inlining the full target inventory.
4. Replace Frame/Result wording with logical/materialized Dataset wording.
5. Update `analysis.entry`, `analysis.methods`, `analysis.inputs`,
   `analysis.artifacts`, `analysis.evidence`, `analysis.runtime`, and the
   terminal pandas boundary.
6. Ensure every target advertised by a root or group resolves independently.
7. Delete every old capability id and Help target associated only with removed
   paths.
8. Add the retained construction-time `Session.source_bindings` and new
   `analysis.metric_dataset.rollup` leaves; ensure no
   `analysis.events.occurrence_bounds` target remains.
9. Derive current continuations from consumer admission; do not store a second
   producer or renderer continuation inventory.

### AgentResult protocol switch

- Dataset values do not implement `render()` and do not satisfy `AgentResult`.
- Logical Datasets expose no `show()` or `to_pandas()`.
- Materialized Datasets expose `show()` and `to_pandas()` but still do not add a
  Dataset `render()` twin.
- `DatasetContract` implements bounded `repr`, pure `render()`, and printing
  `show()`.
- terminal non-Dataset reads retain the shared result protocol where their
  owners require it.
- update `docs/specs/agent-friendly-public-surface.md` and its contract tests in
  this slice.

### Documentation and skill switch

Update atomically:

- `docs/specs/analysis/python-analysis-design.md`;
- `docs/specs/analysis/operators-and-frames.md`, renaming or replacing it when
  necessary so the current spec does not preserve the Frame algebra;
- `docs/specs/analysis/session-state-and-runtime.md`;
- `docs/specs/analysis/evidence-access-surface.md`;
- `docs/specs/agent-friendly-public-surface.md`;
- all affected focused specs and cross-links;
- `marivo/skills/marivo-analysis/SKILL.md`;
- latest English site analysis pages;
- latest Chinese site analysis pages;
- executable examples, README snippets, API docs, and release notes.

Historical versioned site documentation remains historical unless its build
contract requires a mechanical link repair. Current/latest pages must describe
only the lazy Dataset surface.

### Test replacement

- replace public export and capability snapshots;
- replace Frame result-protocol tests with Dataset state/action tests;
- remove tests whose only purpose is old compatibility or migration;
- retain business-correctness fixtures by porting them to the new owner rather
  than deleting adversarial coverage;
- replace literal old-card snapshots with current Dataset cards/contracts and
  behavioral reachability checks;
- add negative searches for every forbidden symbol, schema, alias, and Help
  target;
- prove both current language tracks and packaged skill use only live paths.

### Exit gate

The installed package exposes one Dataset algebra, one persistence generation,
one Help topology, and one current documentation story. No intermediate commit
after this point is releaseable until Slice 9 passes.

## Slice 9: Full Acceptance and Release Readiness

### Outcome

Prove the released cutover through static contracts, numerical parity,
adversarial failure, supported backends, cold recovery, disclosure drift, and
real-Agent journeys.

### Required acceptance units

| Unit | Prerequisite | Evidence owned |
| --- | --- | --- |
| 9a: deterministic and installed-package gates | 8c | Full repository, public contract, numerical, Help/example, bilingual docs/site, and distribution checks below. |
| 9b: backend and execution-economics matrix | 9a | Terminal evidence for every claimed method/adapter/reader/writer path, source/local parity, query/transfer counts, and exact resource limits. Reuse unit harnesses against the candidate; do not infer support from compilation. |
| 9c: adversarial runtime and read acceptance | 9a | Admission ordering, contention, precommit failure, committed-success recovery, unknown outcomes, required parts, cross-Session boundaries, scoped reads, and forbidden disclosure on the candidate. |
| 9d: real-Agent integration journeys | 9a | Execute every journey below with fresh terminal Run/Artifact/Evidence or exact pre-admission rejection proof. Close only after 9b and 9c also pass for the same candidate. |

9b-9d may collect independent evidence concurrently. Each report identifies the
candidate SHA and fixture/backend configuration. Changes during acceptance
invalidate affected evidence and require the final full gate on the resulting
revision; a successful earlier sub-slice is not proof of the installed switch.

### Static and deterministic gates

Run the repository entrypoints, never bare tools:

```bash
make check-agent
make test TESTS='tests/test_analysis_help_examples_execute.py'
make test TESTS='tests/test_lazy_analysis_current_docs_examples.py'
make docs-api
cd site && npm run verify:content && npm run build
make pypi-build pypi-check
git diff --check
```

`make check-agent` must be fully green. A failure is repaired and the full gate
rerun; it is not waived because narrower tests passed.

### Backend matrix

Report basic relational coverage separately from complex method support. For
DuckDB, SQLite, Trino, MySQL, PostgreSQL and ClickHouse, record each claimed
method's tested Ibis lowering and eligibility on that adapter, any exact
registered pandas implementation, and source-private restrictions. Test the
deterministic source-prefix/local-suffix decision independently from numerical
parity and actual source/storage reader support. Missing source support starts
local work only when that method and its input roles admit it; missing required
source and local support is typed unsupported. A selected source implementation
that fails compilation or execution never retries locally. DuckDB is tested as
an ordinary datasource, with no internal local-executor category or six-backend
all-method requirement.

Start with the repository's declared DuckDB datasource reference path and the
real datasource adapter required by each vertical journey. Expand complex correlation,
attribution and Event/Lifecycle methods individually. Every claimed path needs
terminal rows, authority, reader/writer, receipt and Runtime evidence on the real
backend or designated live integration environment. Compile success is insufficient.
Missing supported-method coverage remains an explicit cutover limitation, not a
license to advertise unverified methods on that backend.

### Performance and execution-economics gates

The acceptance harness must record query count, stage count, transferred rows,
transferred decoded bytes, output rows, output bytes, peak local RSS where
available, elapsed time, Run count, and Artifact count.

Required binary outcomes:

1. one fully source-eligible same-domain chain containing filter, projection,
   fanout-safe join, aggregation, window, and order compiles and executes as one engine
   query stage;
2. logical construction emits zero datasource calls and zero Runs;
3. exact binding recovery emits zero datasource calls, zero new Runs, zero
   storage copies, and returns the same Artifact ref;
4. a materialized downstream chain emits no origin-source query;
5. a high-cardinality rank-and-limit journey does not collect the unbounded
   Entity relation into the Marivo process;
6. each bound source, complete local-input/retained-part, intermediate-memory,
   method-size, deadline and storage guard fails atomically beyond its limit;
   local Artifact computation obeys local collection guards even when the
   Artifact was legally written through a larger storage stream;
7. no execution path inserts sampling to satisfy a resource limit;
8. equal parameterized-source bindings recover without a request while one
   changed value produces a distinct execution key;
9. materialized rollup issues no origin query and publishes only its final
   coarsened Dataset;
10. an absent or ineligible source lowerer selects its admitted pandas method
    before compilation/data work; every dependent successor remains local even
    if it has source support, without an internal DuckDB connection;
11. contiguous local steps share private DataFrames without Arrow/Parquet
    serialization, and independent source branches reach only admitted inputs;
12. a selected source query's compile or runtime error fails the Run without
    executing the local implementation or publishing intermediate results.

### Real-Agent journeys

Each journey runs in a fresh terminal process against a supported real backend.
Evidence includes the script, terminal output, exact Session id, Run id,
Artifact ref, row/authority assertions, and post-run runtime reads. A transcript
or dispatch record is supplementary, not acceptance.
For deliberate pre-admission rejection or exact-key recovery, record the
existing Session/Artifact identity and assert that no new Run was created;
do not invent a Run id to fill the evidence template. Parameterized method
cases may share one harness, but each claimed variant has its own assertions
and terminal outcome in the capability-to-acceptance matrix.

#### Journey A: default Population and Metric coordinates

1. Resolve exact Metric and Dimension inputs through the current catalog.
2. Construct one Entity-grained multi-Metric Dataset.
3. Add a Dimension and time coordinate, then aggregate.
4. Inspect `contract()` before execution.
5. Execute, inspect the Materialized Dataset, read Evidence, and verify one
   ordinary relation query, with required fence/validation/write statements
   accounted separately.
6. Exercise the source-side predicate, sampling, Metric projection, and
   rank/limit cases from 3a; verify selection order, deterministic prefixes, and
   adjacent invalid coordinate/contribution cases.

#### Journey B: checkpoint and cross-process reuse

1. Materialize one shared Entity Metric Dataset.
2. In a second process reconstruct and execute the same definition.
3. Prove binding recovery with no new Run or SQL.
4. Build correlation and entity-outlier branches from the recovered leaf.
5. Prove both scan the immutable checkpoint and do not replay its source.

#### Journey C: compare, attribute, and barrier repair

1. Construct compatible logical current and baseline Metric Datasets.
2. Compare and attribute over an axis not retained as a public row field but
   reachable under logical authority.
3. Execute and verify exact reconciliation.
4. Materialize the Delta before adding a missing axis and prove local
   construction failure with an exact rebuild-before-execute repair.
5. Repeat the registered additive, component-mix, distinct-membership, and
   distribution-Shapley cases with their independent numerical fixtures,
   admitted source/local paths, and joint/hierarchy outputs. Exercise partition,
   retained-state, and mapped-player-limit failures; one method's success does
   not certify the others.

#### Journey D: Metric to Event to PopulationDataset to Metric

1. Select a governed identity-bearing Metric Dataset.
2. Pass it explicitly as Event `population=`.
3. Match a first-per-subject journey and select dropped subjects.
4. Materialize and recover the PopulationDataset in a fresh process.
5. Pass it to a new Metric observation.
6. Prove complete membership, no Event rematch, no local identity collection,
   and no identity leak outside authorized rows/storage.

#### Journey E: Lifecycle recovery and reducers

1. Replay one StateModel with exact inception authority.
2. Materialize and recover history in a fresh process.
3. Build distribution, transitions, dwell, violations, and in-state selection
   from the immutable history.
4. Prove reducers do not query trigger Events and retain exact coverage and
   censoring semantics.

#### Journey F: source prefixes, local continuations and exact input roles

1. Construct independent source branches with different exact Marivo bindings;
   prove they are not fused into one source query by Ibis connection equality.
2. Use an exact pandas method that explicitly admits those input roles. Execute
   both eligible source prefixes, validate complete Arrow inputs and their
   combined bounds, and perform the local suffix within one Run.
3. Append an operator with tested source support and prove it stays local after
   that frontier. No DataFrame is uploaded and no internal DuckDB connection opens.
4. Repeat over local/object Parquet Artifacts through authorized PyArrow readers,
   preserving immutable rows and required parts with no origin replay.
5. Prove only the requested root Artifact and its required parts are published;
   there is no automatic input Artifact, hidden checkpoint or federation discovery.
6. Use source-required identity/semantic work without a legal local method and
   prove its domain conflict fails before data work. Advertise `.execute()` as
   a repair only if the configured writer, reader and method can actually meet
   the required authority and bounds.
7. Inject compile and runtime failures in a selected source prefix and prove
   neither failure invokes pandas as a retry.

#### Journey G: failure atomicity and cold reconciliation

1. On an admitted execution-key miss, inject live profile-resolution and compile
   failures; prove the incomplete Run exists before those steps, each resolved
   failure belongs to that same Run, and no Artifact is published. Contrast
   exact-key recovery and writer contention, which create no new Run.
2. Crash before metadata commit and prove Session-guarded termination/cleanup
   plus failed Run with no partial Artifact/Evidence/Finding authority.
3. Crash after commit before return, and separately lose commit acknowledgement;
   prove readback recovers the existing succeeded Run/Artifact without repair
   or datasource re-execution.
4. Make authoritative commit readback or backend termination proof unavailable;
   prove no guessed failed terminal, output deletion, or re-execution occurs.
   Restore proof and reconcile the existing outcome under the Session guard.
5. Overlap same-Session writes and prove immediate busy rejection with no Run;
   explicitly retry after completion and recover the same-key Artifact.
6. Execute different Sessions concurrently and prove a busy or recovery-blocked
   Session cannot prevent another Session from producing its own result.

#### Journey G2: explicit prior-Session result selection

1. Produce an Artifact in A, record its ref, owner, producer times, and backing.
2. Change origin rows or disable its query endpoint while preserving backing.
3. Read it through B's `artifact(ref)` and prove no source query, age test, copy,
   alias, registration, or approval; original owner/ref/Findings remain unchanged.
4. Execute a downstream B-owned Dataset using the exact input and prove only B
   creates a new Run/Artifact, with an input edge to A's original Artifact.
5. Show B's external boundary and A's unchanged local Run/head membership.
6. Reject a foreign Logical input, cross-Store Artifact, forged owner, corrupt
   backing, or structurally invalid operator input with precise repairs.
7. Check three-axis full integrity inspection and factual timestamp disclosure; no source
   freshness status, equivalence verdict, or reusable flag exists.

#### Journey H: captured parameterized source values

1. Construct a Logical Metric Dataset inside `Session.source_bindings(...)`
   against a controlled parameterized JSON source.
2. Exit the scope, execute, and verify the emitted source request used the
   captured exact typed values.
3. Reconstruct with equal bindings in a fresh process and prove same-Session
   recovery sends no request and creates no Run.
4. Change one binding and prove a distinct execution key and source request.
5. Inspect Runs, Artifacts, Evidence, errors, telemetry, cards, and contracts
   and prove no raw binding value appears.

#### Journey I: materialized rollup without origin replay

1. Materialize one additive daily-by-Dimension Metric Dataset.
2. Roll it to a coarser grain and drop the Dimension in one request; separately
   remove time with `drop_time=True` and verify the registered temporal fold.
3. Prove the canonical time-then-Dimension fold, singleton/empty behavior, and
   complete versus partial target-period coverage.
4. Prove the action scans only the immutable Artifact and sends no origin
   query.
5. Repeat with cumulative period-end, ratio, mean, weighted mean, and blocked
   unregistered non-additive fixtures. Verify exact component merges and `last`
   behavior; blocked folds name the direct-target-observation repair.
6. Filter daily rows before time removal and prove only selected periods
   contribute without claiming complete-window coverage.
7. Corrupt a required retained part and prove dependency validation fails without
   querying an origin or borrowing another visible Metric column.

#### Journey J: certified forecast and uncertainty

1. Construct certified histories and execute every accepted forecast model
   against the owner's independent numerical reference cases.
2. Verify future coordinates, model-specific interval variance, and panel
   coverage on each claimed source/local path. Append an admitted relational
   successor to a local forecast and prove it remains local with direct private
   DataFrame handoffs.
3. Recover each Forecast in a fresh process and verify rows, Evidence, Findings,
   model assumptions, and nominal prediction meaning.
4. Reject incomplete or insufficient histories without a Forecast Artifact;
   the owner's constant non-zero innovation fixtures never become zero-width
   intervals, and failed horizon execution publishes no partial forecast.

#### Journey K: time discovery and driver-axis screening

1. Execute point-anomaly, interesting-window, and period-shift discovery from
   their admitted Metric or Delta inputs, including recovered checkpoints.
2. Verify exact coordinates, keys, ordering, limits, generated-field filtering,
   and independent scorer references. Prove an evaluated empty result succeeds
   while an input with no evaluable series or axis fails as specified.
3. Screen driver axes with repeated members across comparison days and retained
   unrequested Dimensions; verify independently scoped scores and keys.
4. Exercise logical missing-axis expansion and materialized-barrier rejection
   without origin replay. Cold-read each Candidate shape and verify Evidence
   preserves search scope and descriptive meaning.

#### Journey L: Event funnel comparison and attribution

1. Construct compatible complete current/baseline journey assignments using
   logical journeys and explicit recovered journey checkpoints.
2. Build funnels, compare them, and attribute one selected non-initial step;
   verify exact loss-rate endpoint reproduction and reconciliation at every
   resolution without Event rematching.
3. Exercise claimed source lowering and an admitted pandas continuation over
   complete compact additive components; raw journey identity never crosses
   that local boundary.
4. Reject incompatible follow-up, censored comparison input, a materialized
   funnel Delta, and a logical Delta built from materialized funnel summaries.
   Repairs name the valid journey-checkpoint construction.
5. Recover the committed Attribution and verify family, scope, Evidence,
   Findings, and identity-safe metadata.

#### Journey M: Entity-outlier Candidate as membership input

1. Produce an Entity-outlier Candidate, bind its score selector, and filter it.
2. Pass the logical Candidate explicitly as Metric `population=` and verify
   the exact selected identity projection without a Python collection bridge
   or an intermediate public membership artifact.
3. Materialize and cold-recover the Candidate in a compatible engine storage
   domain; repeat the observation without replaying its Metric origin.
4. Verify observation scope remains independent of selection scope, identity
   stays within authorized storage/reads, and other Candidate shapes are
   rejected as population inputs.
5. Use the admitted Candidate as Event and Lifecycle `population=` and complete
   their selected-Population return paths, proving the same identity contract
   holds across the Slice 6d/7 boundary.

#### Journey N: retained Session, Run, Artifact, and Finding reads

1. Open a v3 Session in a fresh process and exercise recent/inspection, Run
   listing/detail, Artifact opening, Finding pages, and Graph reads against
   committed successes and resolved failures from the preceding journeys.
2. Verify exact closed variants, deterministic ordering/pagination, original
   Artifact/Finding ownership, and read-only availability under the read owner's
   contract. Reading creates no new analysis Run and queries no origin source.
3. Instrument dependency reads: Artifact opening reads metadata only, Finding
   pages validate selected records, and previews/operators read only their
   required rows/parts; unused parts and all Findings are not silently scanned.
4. Explicitly inspect corruption in primary data, required parts, and Findings;
   verify exactly three independent integrity axes without current semantic or
   source comparison, repair writes, or a reusable/freshness verdict.
5. Create a new named v3 Session in a project retaining eager-generation data;
   reject old identities and incompatible generations without migration,
   rewriting old files, or reading their payloads as Dataset authority.

### Negative cutover audit

Search the tracked package, tests, current specs, latest site docs, examples,
and skill for every forbidden path. The audit must prove absence of:

- public `BaseFrame`, `MetricFrame`, `DeltaFrame`, `AttributionFrame`,
  `EventFrame`, `LifecycleFrame`, `ForecastFrame`, `AssociationResult`,
  `CandidateSet`, `SubjectSet`, `LogicalSubjectSet`, `MaterializedSubjectSet`,
  and `HypothesisTestResult`;
- public `session.compare`, `session.attribute`, `session.correlate`,
  `session.forecast`, `session.select_subjects`, `session.delete`, and
  Session-owned reducers;
- public `session.events.occurrence_bounds`, `EventOccurrenceBounds`, and any
  observed-range window inference;
- public `frame.transform`, `rollup(drop_axes=...)`, and detached
  selection/cast methods;
- `interesting_slices`, `semantic_hypotheses`, and
  `cross_sectional_outliers` aliases;
- `analysis-artifact/v13`, `marivo.analysis_run/v2`,
  `marivo.analysis_job/v2`, old Store decoders,
  migration, backfill, and dual reads in live implementation;
- public plan, SQL, Ibis, task, future, receipt, staging, or writer-guard types;
- execution-time lookup of parameterized source values from a `ContextVar`,
  mutable Session map, or `execute(...)` argument;
- silent pandas/Polars fallback or unbounded local identity transfer;
- source-authority/replay certificates, datasource freshness/reusability verdicts,
  automatic cross-Session matching, and blanket rejection of explicit same-Store
  Materialized inputs.

Historical release notes and intentionally archived design documents may name
old paths as history. They must not be imported, linked as current guidance, or
parsed as a live registry source.

### Final exit gate

All static, backend, performance, failure-injection, disclosure, and real-Agent
journeys pass against the exact candidate revision. The candidate is then
eligible for the repository's normal breaking-release workflow; this plan does
not authorize publishing, tagging, or announcing a release by itself.

## Persistence Cutover Detail

### Store activation

Session activation reads `PRAGMA user_version` before any recovery, catalog
resolution, datasource connection, or write. Only a new empty Store or exact v3
Store is accepted by the lazy runtime.

An old or future Store returns one structured failure containing:

- expected exact Store generation;
- received generation;
- affected Session identity/path without exposing secrets;
- the instruction to create a new named Session and rerun authoring code;
- no retry that would migrate, delete, reinterpret, or import the Store.

### Old data handling

The cutover does not delete old Session directories or Artifact payloads. They
remain user data but are not valid lazy Dataset authority. Automatic cleanup of
old generations is outside this plan and requires separately authorized,
recoverable deletion behavior.

### Publication boundary

The implementation preserves the accepted order:

```text
final immutable storage + validated receipt
  -> one Session Store transaction:
     Artifact descriptor + Evidence + Findings + Run terminal success
     + removal of resolved output obligations
```

The Session writer guard covers recovery, binding lookup, execution, and
publication. Before the metadata commit, output is unpublished and journaled.
After commit, Artifact and Run success already exist together. A lost
acknowledgement requires authoritative readback before cleanup or retry, never
a second publication decision or a terminal rewrite. Other Sessions may run
backend work concurrently; SQLite transactions remain short.

### Concrete input checks without authority audit

Remove the generic authority requirement record, mode enum, requirement summaries,
and independent successful-check audit from Dataset state, Run admission,
terminal storage, public Run values, Help, exports, and tests. Do not rename or
replace them with an equivalent parallel registry. The operator contract owns
required semantic dependencies, fields, identity, coverage, and alignment; the
compiler binds the corresponding source, scan, join, and calculation nodes.
Runtime executes their concrete checks without substituting inputs on failure.

Successful terminal rows require only the output Artifact ref in addition to
normal Run identity/outcome/timing. No compiler audit is required or persisted. Existing Artifact descriptors
and normalized Run input rows retain dependency digests and exact provenance.
Failures use structured errors; historical storage authorization classes are not
persisted. Compiler diagnostics are opt-in execution-local output and never
gate publication or create a public result type.

Acceptance must cover retained-row work after catalog removal, explicitly admitted
current Dimension enrichment, rejected implicit origin replay or missing fields,
and mixed Logical/Materialized inputs. Persistence and public export tests must
prove the removed audit and requirement fields/types are absent.

### Explicit cross-Session Artifact boundary

Use the existing `target_session.artifact(ref)` read for explicit same-Store
selection. Materialized operands from another Session are legal under their
operator's concrete structural contracts; foreign Logical graphs and cross-Store
objects remain rejected. Preserve original Artifact ownership, backing, producer,
and Findings. Only the consuming Session owns/locks new Runs and outputs.

The Store input relation's Artifact foreign key is project-global, while its
Run foreign key and every output remain scoped to the consuming Session.
Do not create an alias registration, binding, copy, or reuse event merely for a
read. Execution keys bind exact selected input refs; no cross-Session automatic
matching by definition, content, or time exists.

Update Dataset state, Artifact summaries, Finding ownership, Graph scope, and
Help together. Materialized state adds `artifact_session_ref`; Artifact summaries
show the original owner plus producer admission/finish times. Cross-Session graph
inputs are boundary Artifacts; foreign Runs do not enter the local graph.
Only budget truncation sets `truncated`. These are factual lineage/time fields,
not datasource freshness or reuse certification.

Remove generic source-authority variants, source-state digests, snapshot-capture
and replay-comparability protocols, the datasource revalidation axis, and
source-version capabilities used solely to certify future reuse. Preserve actual
operator input checks, single-evaluation fences, immutable backing receipts,
normal executor transactions, and source-origin/coverage inputs owned by Event
and Lifecycle algorithms. These do not grant a general suitability promise.

### Result storage, scoped reads, and harmless maintenance

Artifact descriptors bind the primary receipt and `retained_parts[]` instances,
each with a registered role, private contract/version, and concrete receipt.
Metric components and Lifecycle trace data commit with the primary payload and
transfer all output reservations in the same transaction. Parts have no separate
Artifact identity, Run, graph node, or public columns. Align family registrations,
writer output roles, receipt adapters, cold decoders, and consumer role requests.

Opening an Artifact reads supported metadata only. Preview/collection validate
primary data; operators add only required parts; Finding reads validate selected
records. Full inspection validates all data/parts/Findings, including complete
hashes/counts. No ordinary read silently performs that full audit or compares a
semantic catalog. Remove stale semantic-revalidation examples and Help guidance.

Streaming local Parquet persistence has an 8-MiB decoded-batch guard and a 64-MiB
serialized Artifact budget covering primary, private parts, and manifests. Remove
the local-storage row cap and pre-transfer worst-case string-width requirement.
Keep local analytical calculation and complete DataFrame memory limits separate.
Unknown total size is legal with bounded streaming; actual overflow fails without
partial publication, truncation, or sink switching.

Publication and failed-terminal insertion require execution termination/fencing
and proof that surviving temporary resources are harmless, exactly owned, and
unreferenced by committed results. Try cleanup; retain unresolved existing journal
rows on either succeeded or failed Runs without blocking new work. Unknown remote
termination still blocks its Session. Add no cleanup state table or scheduler.

Acceptance includes cold component/trace recovery, part-publication crash points,
no whole-Finding scan for a preview, no unused-part reads, explicit corruption
inspection, variable-width streaming, and success followed by deferred cleanup.

### Execution-key Artifact boundary

`DatasetExecutionKeyV1` combines only Dataset Core's complete canonical
`definition_fingerprint` with the common materialization protocol version.
Core owns normalization of semantic/source/operand/row/producer dependencies
once, including significant realization-occurrence equivalence; the Store scopes
lookup by Session rather than repeating that scope in the digest. Owner-created
graph-local handles establish sharing for Core normalization but their raw values
never enter identity. Runtime consumes the resulting fingerprint without a
second sharing digest or persisted occurrence table. Materialized input tokens
name exact Artifact refs only and terminate realization traversal. It does not
claim global cache equivalence or datasource freshness.

The unique `(session_ref, execution_key_digest)` key on the committed Artifact
relation is the whole lookup contract. It has no second Store relation or
payload.

The public documentation and skill must state that a same-Session execution-key
hit recovers the committed snapshot. A user who requires current source rows
creates a new named Session or changes an explicit row-affecting definition
input.

## Disclosure Cutover Detail

### Root Help

The analysis root stays bounded and routes through the six existing conceptual
hubs. It introduces Dataset state and action navigation without dumping the
complete family/type/operator registry.

Every root/group member must:

- be registry-owned;
- appear in exactly one discovery group;
- resolve independently;
- use one canonical id and public entrypoint;
- remain inside the root/group budget;
- contain no eager alias or dead link.

### Focused Help

Every focused callable leaf owns its reflected signature, exact inputs, output
family, constraints, effects, failure rules, and one minimal runnable example.
Every public type leaf owns producers, consumers, public fields, and public
methods without exposing private descriptors.

The cutover must include all Help targets explicitly required by the Dataset
Core, Observation Model, Typed Operators, and Subject/Event/Lifecycle designs.
Target strings, public objects, and exact registered instances must resolve
according to the shared public Help rules.

### Dataset state disclosure

- logical `repr` and `contract()` disclose definition, family, shape, row
  contract, concrete input requirements, blockers, and legal continuations without
  rows or execution;
- materialized `repr`, `show()`, and `contract()` disclose committed state,
  bounded rows, Evidence, and legal continuations without origin replay;
- `to_pandas()` remains an explicit terminal exit and its result cannot re-enter
  typed analysis;
- raw Entity identities appear only in authorized Dataset row storage and
  explicit terminal row reads.

## Test and Evidence Ownership Matrix

| Evidence family | Primary slice | Required independent consumer |
| --- | --- | --- |
| common Dataset descriptors and state pairs | 1 | every family registration |
| private semantic normalization, Population/Metric contracts, and predicates | 2a, 3a | compiler/operator admission and 8b public activation |
| parameterized source capture, identity, lowering, and redaction | 2a-2b; extended in 4c | compiler plus cold-recovery/runtime audit |
| typed graph and fixed implementation dispatch | 2b, 3a, 4a | runtime handoff and every operator |
| local execution and transfer guards | 4a | 3b folds, 6a-6b numerical methods, and runtime failure injection |
| Run admission, exact-key lookup, and atomic publication | 2b; extended in 4c | every producing family |
| storage adapters and retained-part receipts | 2b local; 4b engine/object | 3b, 5b-5d, and 7d cold readers |
| retained Session/Run/Artifact/Finding reads and graph | 4d | 8c installed facade and 9c scoped-read acceptance |
| Metric operator numerical parity | 5a-5d, 6a-6e | materialization/Evidence registration |
| Metric rollup fold and coverage parity | 3b | compiler and materialized scan-leaf runtime |
| Event matching, reducers, and Population return | 7a-7b | compiler, runtime, and redaction tests |
| Event funnel compare/attribution specialization | 7c | shared Delta/Attribution protocol and independent compact-component conformance |
| Lifecycle canonical retention, reducers, and Population return | 7d-7e | cold-part readers, runtime failure injection, and redaction tests |
| exports, Help, docs, skill | 8a-8c | independent drift/reachability tests |
| real backend and Agent journeys | 9b-9d | release readiness |

Tests must avoid proving a registry with the renderer that consumes it or a
schema with only the writer that produced it. Decoders, reachability checks,
Help inventories, implementation registries, and publication records each need an
independent invariant.

### Capability-to-acceptance matrix

This matrix assigns the mandatory integrated paths. Before a unit starts, its
implementation document attaches exact test selectors, fixtures, claimed
method/adapter combinations, and evidence locations to its row. Each accepted
variant must be covered; one representative family test is insufficient.
Deterministic/numerical cases may share a parameterized harness, while the
journeys prove the integrated public and recovery boundaries. Removing a matrix
row or replacing its evidence with another family's requires review here.

| Capability or boundary | Implementation unit | Focused positive/negative evidence | Final acceptance |
| --- | --- | --- | --- |
| Core values, selectors, realization identity | 1 | paired registry, no-I/O descriptors, wrong/stale/foreign selectors, shared versus independent occurrences | 9a; A-C |
| Semantic identity/versioning and Metric source construction | 2a, 3a | actual target normalizers, exact temporal selection, independent membership/observation scopes, safe component mapping | 9a; A, D, M |
| First execution, terminal reads, exact-key recovery | 2b | same-family bundle, guarded reads, source-free cold reconstruction | A-B, H; 9c |
| Parameterized source bindings | 2a-2b, 4c complete | scope-exit execution, exact key separation, missing/extra input errors, exhaustive redaction; all-target fresh cold journeys in `test_lazy_binding_cold_acceptance.py` | Private 4c gate passed; H remains public Slice 9 acceptance |
| Predicates, coordinates, sampling, Metric projection, rank/limit | 3a | filter order, contribution admission, deterministic ordering, source fusion | A; 9b economics |
| Retained parts, aggregate/fold/rollup, checkpoint membership | 3b complete | `test_lazy_retained_fold_matrix.py`, `test_lazy_local_fold.py`, `test_lazy_retained_compiler.py`, `test_lazy_retained_membership.py`, `test_lazy_retained_failures.py`; exact variants and fresh-process evidence in the [3b record](../plans/2026-09-08-lazy-analysis-slice-3b-execution.md) | Private 3b gate passed; B, D, I, M remain public Slice 9 gates |
| Source-prefix/local-suffix execution | 4a and parent 4 complete; multi-input extension complete in 5a | `test_lazy_local_guards.py`, `test_lazy_local_runtime_acceptance.py`, `test_lazy_slice4_boundaries.py`, `test_lazy_compare_runtime.py`, `test_lazy_local_graph_lifetime.py`; complete unary/retained and combined input guards, intermediate/output/deadline limits, fixed dispatch, real independent-source comparison, immutable direct DataFrame handoff and no retry | Private parent 4 gate passed under the approved allocation; the [5a gate](../plans/2026-09-08-lazy-analysis-slice-5a-execution.md) closes the allocated multi-input proof. F and 9b remain public gates |
| Local/engine/object storage | 2b, 4b | immutable receipts, reservations, required parts, mutation and overflow | F-G, I; 9b-9c |
| Run admission, publication, concurrency, reconciliation | 2b, 4c complete | `test_lazy_runtime_concurrency.py`, `test_lazy_worker_recovery.py`, `test_lazy_adapter_crash_acceptance.py`, `test_lazy_reconciliation_snapshot.py`; exact variants and evidence in the [4c record](../plans/2026-09-08-lazy-analysis-slice-4c-execution.md) | Private 4c gate passed; G and 9c remain public Slice 9 acceptance |
| Session/Run/Artifact/Finding reads, graph, integrity | 4d and parent 4 complete | `test_lazy_runtime_reads.py`, `test_lazy_session_history.py`, `test_lazy_session_graph.py`, `test_lazy_finding_types.py`, `test_lazy_finding_reads.py`, `test_lazy_integrity_inspection.py`, `test_lazy_inspection_boundaries.py`, `test_lazy_runtime_read_acceptance.py`, `test_lazy_read_integrity_regressions.py`, `test_lazy_object_access_boundaries.py`; [exact evidence](../plans/2026-09-08-lazy-analysis-slice-4d-execution.md) | Private 4d and parent 4 gates passed; G2, N and 9c remain public gates |
| Metric comparison | 5a complete | `test_lazy_compare_contracts.py`, `test_lazy_compare_numeric.py`, `test_lazy_compare_compiler.py`, `test_lazy_compare_runtime.py`, `test_lazy_compare_time_runtime.py`, `test_lazy_compare_runtime_acceptance.py`, `test_lazy_delta_publication.py`; five shapes, all state topologies, exact promotion and Decimal Finding order, sampling sharing, complete combined guards, repeated time-series coordinates, atomic Findings and three-storage cold recovery | Private [5a technical gate](../plans/2026-09-08-lazy-analysis-slice-5a-execution.md) passed after review follow-up; C and F remain integrated public acceptance |
| Additive/component-mix attribution | 5b | endpoint reproduction, disjoint partitions, masks/Findings, barrier failures | C |
| Distinct-membership attribution | 5c | exact membership allocation, source-private identity, reconciliation | C; 9b |
| Distribution-Shapley attribution | 5d complete | independent exact/T-Digest coalition and hierarchy references, player bound, retained-state/receipt failures, local budgets and three-process local/object recovery | Private [5d technical gate](../plans/2026-09-09-lazy-analysis-slice-5d-execution.md) passed; C and 9b remain public integrated acceptance |
| Correlation methods and lag searches | 6a | aligned-pair references, unusable rows, no-valid-candidate error, local bounds | B; 9b |
| Forecast models and uncertainty | 6b | certified history, per-model variance, future coordinates, insufficient input, local successors | J |
| Point anomalies, interesting windows, period shifts | 6c | independent scorers, exact keys, filtering, empty versus not evaluated | K |
| Entity-outlier Candidate membership | 6d | score/selector correctness, exact identity projection, rejected Candidate shapes | M |
| Driver-axis screening | 6e | scoped partitions, keys/scores, logical expansion and materialized barrier | K |
| Event matching/completeness and journey recovery | 7a complete | `test_lazy_event_contracts.py`, `test_lazy_event_compiler.py`, `test_lazy_event_numeric.py`, `test_lazy_event_time.py`, `test_lazy_event_storage.py`, `test_lazy_event_runtime.py`, `test_lazy_event_membership.py`, `test_lazy_event_temporal.py`, `test_lazy_event_coverage_runtime.py`, `test_lazy_event_runtime_acceptance.py`; [exact evidence](../plans/2026-09-10-lazy-analysis-slice-7a-execution.md) | Private 7a gate passed; D, L, M remain integrated public gates |
| Event reducers and subject selection | 7b complete | `test_lazy_event_reducer_contracts.py`, `test_lazy_event_population_continuations.py`, `test_lazy_event_reducer_numeric.py`, `test_lazy_event_reducer_storage.py`, `test_lazy_event_reducer_runtime.py`, `test_lazy_event_reducer_failures.py`, `test_lazy_event_reducer_publication_runtime.py`, `test_lazy_event_reducer_runtime_acceptance.py`; [exact evidence](../plans/2026-09-10-lazy-analysis-slice-7b-execution.md) | Private 7b gate passed; D, L, M remain integrated public gates |
| Event funnel compare/attribute | 7c | follow-up compatibility, compact-component parity, journey versus aggregate checkpoint authority | L |
| Lifecycle replay and canonical retained parts | 7d | inception, transition/coverage traces, atomic part publication, corrupt recovery | E; 9c |
| Lifecycle reducers and subject selection | 7e | reducer references, clipped dwell meaning, exact part reads, no replay | E, M |
| Public/semantic/Store/Help/docs switch and removed paths | 8a-8c | installed exports/signatures, generation boundaries, example execution, disclosure/negative audit | 9a, 9c; N; all Agent journeys |

## Slice Document Template

Every implementation slice derived from this plan uses:

```markdown
## Slice <unit id>: <name>

### Parent milestone and prerequisite units
### User-visible or runtime outcome
### Frozen contract owners consumed
### Exact method, backend, storage, and fixture scope
### Exact files owned
### Shared seams changed
### Public additions
### Public removals
### Persistence changes
### Implementation order
### Focused positive tests
### Adjacent negative tests
### Failure injections
### Real runtime journey
### Capability-to-acceptance row and evidence locations
### Disclosure updates
### Explicitly deferred contracts
### Exit gate
```

`Explicitly deferred contracts` may contain only work assigned to another named
unit that follows this unit in the dependency order. It cannot defer a
prerequisite or contain an unresolved signature, owner, output family, authority
rule, schema, migration choice, or compatibility decision. Parent closeout
records every constituent unit and its integrated gate; it cannot infer
completion from the last unit's test result alone.

## Cross-Slice Change Protocol

When implementation reveals a contract conflict:

1. stop the affected slice before adding public or persisted behavior;
2. identify the one owning module design;
3. amend and review that owner first;
4. update only the consumed seams in dependent designs;
5. update this cutover plan's ledger, ownership, slice, tests, and acceptance
   journey;
6. resume implementation only after the revised contracts are accepted.

Do not patch multiple implementations, tests, renderers, and docs with slightly
different local interpretations. Do not treat current eager behavior as a
compatibility requirement unless an accepted owner explicitly retains it.

## Release and Rollback Boundary

This plan authorizes neither a release nor destructive cleanup.

Because the cutover intentionally has no migration or dual read, rollback is a
package/revision rollback before adoption of a new v3 Session, not an in-product
compatibility feature. A v3 Session is not promised readable by the eager
release, and a v2 Session is not readable by the lazy release.

Release notes must therefore state:

- the cutover is breaking;
- old named analysis Sessions are not migrated;
- users create new named Sessions and rerun analysis authoring code;
- same-Session lazy execution has committed snapshot semantics;
- removed paths have only their canonical replacements, where a replacement
  exists;
- removed capabilities without a first-cutover replacement are named plainly.

Automatic deletion, conversion, export, or import of old analysis state is out
of scope.

## Final Acceptance Checklist

The plan can be marked implemented only when every item is true:

- [x] All entry-gate owner clarifications are accepted and reflected here.
- [ ] Every required sub-slice has completed its named prerequisites, bounded
      evidence, and parent integration gate; 3b follows 4a/4b and Forecast is
      implemented by 6b without a temporary earlier family.
- [ ] Every previous public export is classified and tested as retained,
      replaced, or absent.
- [ ] Every target Dataset family has one paired registration and complete
      pre-execution row and row-set contracts.
- [ ] Every downstream operator is Dataset-owned and returns Logical state.
- [ ] Logical construction performs no datasource work and creates no Run.
- [ ] `execute()` is the only public logical-to-materialized transition.
- [ ] Materialized reads and downstream operations never replay origin graphs.
- [ ] Exact same-Session execution-key Artifact lookups recover without a new
      Run or SQL.
- [ ] Core identity distinguishes shared from independent significant
      realizations, preserves equivalent sharing across process reconstruction,
      and prevents an independent-result Artifact from satisfying a shared request.
- [ ] Parameterized source bindings are captured at construction, separated by
      exact execution identity, redacted everywhere durable/visible, and never
      reread from ambient state by `execute()`.
- [ ] Metric rollup has one current-row fold meaning across Logical and
      Materialized inputs and never replays origin graphs.
- [ ] Every producing family publishes strict complete Evidence or fails with
      no partial Artifact.
- [ ] Run admission precedes live work on an admitted miss; exact-key recovery
      and writer contention create no new Run. Precommit, committed-success,
      and unresolved outcomes follow the same final protocol from Slice 2.
- [ ] Compiler, runtime, storage, and operator failures remain distinct and
      typed.
- [ ] Every local execution and transfer bound is enforced at and above its
      exact boundary.
- [ ] Raw identities satisfy the exhaustive allowed-location contract.
- [ ] Session Store v3 is the only accepted lazy Store generation.
- [ ] No migration, import, backfill, Frame adapter, or dual read exists.
- [ ] No eager Session downstream operator, Frame/Result family, detached
      selection, or compatibility alias remains public.
- [ ] `session.events.occurrence_bounds(...)`, `EventOccurrenceBounds`, and
      their Help/tests/docs are absent with no implicit window replacement.
- [ ] Every public symbol resolves through one focused Help leaf.
- [ ] Root/group Help is complete, canonical, reachable, deterministic, and
      bounded.
- [ ] Current specs, packaged skill, examples, and latest English/Chinese site
      documentation match the installed surface.
- [ ] Every capability-to-acceptance row has exact variant/test/evidence
      mappings, including forecast, all discovery shapes, Event funnel
      compare/attribute, Candidate membership, and retained runtime reads.
- [ ] Full repository, docs, site, examples, package, backend, performance,
      failure-injection, and real-Agent gates pass on the exact candidate SHA.
- [ ] Acceptance evidence contains fresh terminal Runtime outcomes, not only
      mocks, SQL snapshots, process health, or transcripts.

## Final Boundary

```text
Accepted module designs
  = product and runtime meaning

This Public Cutover Plan
  = exact replacement, deletion, ownership, order, and evidence

Implementation sub-slices
  = private code and tests against one bounded outcome

Parent delivery milestones
  = completed prerequisite units plus their integrated exit gate

Atomic public cutover
  = one Dataset API, one Help surface, one persistence generation

Release acceptance
  = real backend and real-Agent proof on the exact candidate revision
```

Slice 0 has frozen the exact current inventory, replacement classification,
file ownership, executable baseline, persistence layout, target export and
terminal-read schemas, and completeness construction path. An implementer can
complete every later slice without deciding what a public value means, which
owner is authoritative, whether an old path survives, or what evidence counts
as success. Slices 1 through 9 remain separately unauthorized until the owner
starts the next named slice.
