# Lazy Analysis Public Cutover Plan

Date: 2026-09-01

Revised: 2026-09-04

Status: Slice 0 complete; Slices 1-9 require separate authorization

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

## Authority Boundary

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

- common Dataset types, row contracts, state, selectors, or actions;
- Population inference, predicates, coordinate algebra, or aggregation meaning;
- semantic graph nodes, Ibis lowering, stage formation, or boundary capability
  meaning;
- Run, storage, Artifact, Evidence, binding, claim, cleanup, or recovery
  semantics;
- typed Metric operator inputs, outputs, numerical definitions, or statuses;
- SubjectSet, Event, Lifecycle, identity, completeness, or privacy semantics.

## Frozen Contract Inputs

The implementation consumes these accepted designs without copying their
detailed signatures, schemas, state machines, or algorithms:

| Contract area | Sole authority | Cutover use |
| --- | --- | --- |
| Product-wide invariants and clean replacement | `2026-09-01-lazy-analysis-dataset-dsl-design.md` | final product shape and global acceptance |
| Dataset value, family, state, actions, row contracts, and selectors | `2026-09-01-lazy-analysis-dataset-core-design.md` | common implementation and exports |
| Population, filtering, observation, coordinates, and aggregation | `2026-09-01-lazy-analysis-observation-model-design.md` | source and Metric vertical slices |
| Semantic compilation, Ibis lowering, placement, and bounded exchange | `2026-09-01-lazy-analysis-planner-and-pushdown-design.md` | compiler and execution-boundary slices |
| Run, storage, Artifact, Evidence, execution binding, and recovery | `2026-09-01-lazy-analysis-materialization-runtime-design.md` | runtime and persistence slices |
| `correlate`, `rank`, `limit`, `rollup`, `compare`, `attribute`, `forecast`, and discovery | `2026-09-01-lazy-analysis-typed-operators-design.md` | typed operator slices |
| SubjectSet, Event, Lifecycle, and cross-domain population loops | `2026-09-01-lazy-analysis-subject-event-lifecycle-design.md` | domain slices |
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
6. a complete row contract exists before execution;
7. one successful exact execution binds once per Session;
8. private Arrow, Parquet, DuckDB, and Python-kernel exchanges never become
   public Dataset authority;
9. the public cutover removes compatibility paths instead of maintaining a
   second algebra.

### Resolved owner clarifications

On 2026-09-04 the owner resolved all three entry-gate questions and the owning
designs were amended before this plan:

| Capability | Accepted decision | Owning contract |
| --- | --- | --- |
| Parameterized source bindings | Retain `Session.source_bindings(...)` as an authoring-time scope. Every logical source captures its exact non-secret bindings; their digest enters definition/execution identity, `execute()` performs no ambient lookup, and raw values never persist or render. | Observation Model, Compiler, Materialization Runtime |
| Event occurrence range inspection | Remove `session.events.occurrence_bounds(...)`, `EventOccurrenceBounds`, and their Help/test/doc surface with no first-cutover replacement. Event/Lifecycle windows stay explicit and completeness remains separately governed. | Subject/Event/Lifecycle |
| Materialized time-grain rollup | Replace `frame.transform.rollup(...)` with registered `MetricDataset.rollup(drop_dimensions=..., grain=...)`. It folds current rows or exact retained sufficient state on Entity-reduced Metric shapes and never recomputes an origin graph. | Observation Model, Typed Operators |

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
7. Every producing family registers its row contract, validation, quality,
   Evidence, Finding, retained-state, and materialization contracts before an
   execution Run can be admitted.
8. Every source or operator constructs its complete logical row contract before
   datasource access.
9. No row-dependent fact is promoted from preview, local process state, or
   generated SQL into durable authority.
10. No unsupported engine operation triggers an unregistered local calculation,
    implicit collection, sampling, truncation, or semantic fallback.
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

## Success Definition

The cutover is successful when an agent can:

1. construct a deep logical analysis chain without opening a datasource,
   creating a Run, or materializing rows;
2. inspect the complete row meaning and mechanically valid continuations before
   execution;
3. execute one same-domain relational chain as one complete Ibis/backend query;
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
`LogicalSubjectSet` and `MaterializedSubjectSet`. The 35 removals have no
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
| remove without replacement | 43 | `AbsoluteWindow`, `AttributionMode`, `SemanticShape`, `PointAnomalyStrategy`, `RankMethod`, `NormalizeKind`, `NormalizeBaseline`, `hypothesis_test`, `discover.interesting_slices`, `discover.semantic_hypotheses`, `transform.window`, `transform.normalize`, `MetricFrame.components`, `MetricFrame.coverage`, `DeltaFrame.components`, `CandidateSet.select`, `AttributionFrame.at_resolution`, `MetricFrame.as_scalar`, `MetricFrame.as_time_series`, `MetricFrame.as_segmented`, `MetricFrame.as_panel`, `DeltaFrame.as_scalar`, `DeltaFrame.as_time_series`, `DeltaFrame.as_segmented`, `DeltaFrame.as_panel`, `AttributionFrame.as_sum`, `AttributionFrame.as_ratio_mix`, `AttributionFrame.as_weighted_mix`, `CandidateSet.as_point_anomaly`, `CandidateSet.as_period_shift`, `CandidateSet.as_driver_axis`, `CandidateSet.as_slice`, `CandidateSet.as_window`, `CandidateSet.as_cross_sectional_outlier`, `CandidateSet.as_semantic_hypothesis`, `DeltaFrame.predicted_attribution_shape`, `events.watermark`, `events.occurrence_bounds`, `day_of_week`, `period_progress`, `period_correspondence`, `occurrence_progress`, `working_day_progress` |
| retain runtime/read spelling and rebind persistence | 14 | `session.abandon_run`, `session.get_or_create`, `session.current`, `session.resume`, `session.recent`, `session.inspect`, `session.delete`, `session.runs`, `session.get_run`, `session.artifact`, `session.graph`, `session.revalidate`, `artifact.findings`, `artifact.finding` |
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
| `SubjectSet` | `LogicalSubjectSet` / `MaterializedSubjectSet` | Replace eager shape while retaining the accepted domain family name. |
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
| `frame.transform.rollup(...)` | `metric_dataset.rollup(drop_dimensions=..., grain=...)` | Replace with the registered current-row/retained-state fold; no `frame.transform` or `drop_axes` alias. |
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
| `MetricFrame.as_scalar/as_time_series/as_segmented/as_panel` | qualified `dataset.row_contract.shape` | Remove cast-like reads. |
| `DeltaFrame.as_scalar/as_time_series/as_segmented/as_panel` | qualified `dataset.row_contract.shape` | Remove cast-like reads. |
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

- `mv.session.get_or_create`, `current`, `resume`, `recent`, `inspect`, and
  `delete`;
- `session.runs(...)` and `session.get_run(...)`;
- `session.artifact(ref)` returning the exact recovered Materialized Dataset;
- `session.graph(...)` over committed Run/Artifact edges;
- `session.revalidate(...)` with independent integrity, storage, Evidence,
  semantic, and datasource axes;
- `mv.session.abandon_run(...)` only under the accepted owner-lock safety rule;
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
| per-Session Evidence Store | `.marivo/analysis/sessions/<session_id>/judgment.db`, `PRAGMA user_version = 4` | `artifacts`, `findings`, `artifact_digests`, `artifact_issues`; indexes `idx_artifacts_session_commit`, `idx_findings_session_commit`, `idx_findings_artifact`, `idx_digests_session_commit`, `idx_artifact_issues_artifact` | `evidence/store.py`, `evidence/pipeline.py`, `evidence/audit.py`, `evidence/artifact_reads.py`, `_artifact_integrity.py`, `_artifact_revalidation.py`, `session/_load.py`, `session/_runs.py`, `session/_runtime_reads.py`, `session/core.py` | Replace as a whole. The mutable/upsert `artifacts` Evidence row is not a target commit marker. |
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

The owner accepted the following ordered 99-symbol `marivo.analysis.__all__`
for the atomic Slice 8 switch. The ordering is part of the public-surface
snapshot: Dataset Core, paired family states, authoring values, terminal reads,
then constructors and namespaces.

```text
Dataset
LogicalDataset
MaterializedDataset
DatasetFieldId
DatasetFieldIdentity
DatasetPhysicalTypeState
DatasetField
DatasetRowBound
DatasetCardinality
DatasetOrderTerm
DatasetOrdering
DatasetByteCount
DatasetRowContract
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
LogicalSubjectSet
MaterializedSubjectSet

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
`ForecastDataset`, `CandidateDataset`, `EventDataset`, `LifecycleDataset`, or
`SubjectSet`. Those phrases are family shorthand only; public annotations use
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
| Dataset Core | `marivo/analysis/datasets/__init__.py`<br>`marivo/analysis/datasets/base.py`<br>`marivo/analysis/datasets/descriptors.py`<br>`marivo/analysis/datasets/fields.py`<br>`marivo/analysis/datasets/state.py`<br>`marivo/analysis/datasets/contract.py`<br>`marivo/analysis/datasets/registry.py`<br>`marivo/analysis/datasets/handles.py`<br>`marivo/analysis/datasets/actions.py`<br>`marivo/analysis/datasets/errors.py` | Population meaning, physical plan, storage receipts |
| Observation | `marivo/analysis/observation/__init__.py`<br>`marivo/analysis/observation/population.py`<br>`marivo/analysis/observation/predicates.py`<br>`marivo/analysis/observation/source_bindings.py`<br>`marivo/analysis/observation/metric.py`<br>`marivo/analysis/observation/coordinates.py`<br>`marivo/analysis/observation/aggregation.py`<br>`marivo/analysis/observation/rollup.py`<br>`marivo/analysis/observation/contracts.py`<br>`marivo/analysis/observation/errors.py` | Ibis placement, commit ordering |
| Compiler | `marivo/analysis/compiler/__init__.py`<br>`marivo/analysis/compiler/nodes.py`<br>`marivo/analysis/compiler/normalize.py`<br>`marivo/analysis/compiler/manifest.py`<br>`marivo/analysis/compiler/lowering.py`<br>`marivo/analysis/compiler/placement.py`<br>`marivo/analysis/compiler/stages.py`<br>`marivo/analysis/compiler/exchange.py`<br>`marivo/analysis/compiler/errors.py` | public Dataset semantics, Run publication |
| Materialization runtime | `marivo/analysis/materialization/__init__.py`<br>`marivo/analysis/materialization/contracts.py`<br>`marivo/analysis/materialization/execution_key.py`<br>`marivo/analysis/materialization/admission.py`<br>`marivo/analysis/materialization/claims.py`<br>`marivo/analysis/materialization/leases.py`<br>`marivo/analysis/materialization/resources.py`<br>`marivo/analysis/materialization/storage.py`<br>`marivo/analysis/materialization/publication.py`<br>`marivo/analysis/materialization/recovery.py`<br>`marivo/analysis/materialization/reconciliation.py`<br>`marivo/analysis/materialization/store.py`<br>`marivo/analysis/materialization/layout.py`<br>`marivo/analysis/materialization/errors.py` | operator algorithms, public row meaning |
| Typed operators | `marivo/analysis/operators/__init__.py`<br>`marivo/analysis/operators/registry.py`<br>`marivo/analysis/operators/row.py`<br>`marivo/analysis/operators/rollup.py`<br>`marivo/analysis/operators/compare.py`<br>`marivo/analysis/operators/attribute.py`<br>`marivo/analysis/operators/correlate.py`<br>`marivo/analysis/operators/forecast.py`<br>`marivo/analysis/operators/discovery.py`<br>`marivo/analysis/operators/contracts.py`<br>`marivo/analysis/operators/errors.py` | Session management, generic fallback execution |
| Subject/Event/Lifecycle | `marivo/analysis/domains/__init__.py`<br>`marivo/analysis/domains/subject.py`<br>`marivo/analysis/domains/event.py`<br>`marivo/analysis/domains/lifecycle.py`<br>`marivo/analysis/domains/completeness.py`<br>`marivo/analysis/domains/contracts.py`<br>`marivo/analysis/domains/errors.py` | common Dataset state, generic planner/runtime state |
| Session facade and reads | `marivo/analysis/session/__init__.py`<br>`marivo/analysis/session/core.py`<br>`marivo/analysis/session/history.py`<br>`marivo/analysis/session/_read_model.py`<br>`marivo/analysis/session/_runtime_reads.py`<br>`marivo/analysis/session/_resolve.py`<br>`marivo/analysis/session/_connections.py`<br>`marivo/analysis/session/_lazy_sources.py` | Dataset-owned downstream operators or captured-value execution lookup |
| Capability and public-surface integration | `marivo/analysis/__init__.py`<br>`marivo/analysis/errors.py`<br>`marivo/analysis/constraints.py`<br>`marivo/analysis/_contract_budget.py`<br>`marivo/analysis/_capabilities/__init__.py`<br>`marivo/analysis/_capabilities/model.py`<br>`marivo/analysis/_capabilities/registry.py`<br>`marivo/analysis/_capabilities/render.py`<br>`marivo/analysis/_capabilities/surface.py`<br>`marivo/analysis/_capabilities/validation.py` | family algorithms, copied continuation inventories |
| Evidence records and terminal reads | `marivo/analysis/evidence/__init__.py`<br>`marivo/analysis/evidence/types.py`<br>`marivo/analysis/evidence/identity.py`<br>`marivo/analysis/evidence/digest.py`<br>`marivo/analysis/evidence/store.py`<br>`marivo/analysis/evidence/pipeline.py`<br>`marivo/analysis/evidence/audit.py`<br>`marivo/analysis/evidence/artifact_reads.py`<br>`marivo/analysis/evidence/summary.py`<br>`marivo/analysis/evidence/finding_render.py`<br>`marivo/analysis/evidence/extraction/__init__.py`<br>`marivo/analysis/evidence/extraction/_coordinates.py`<br>`marivo/analysis/evidence/extraction/observation.py`<br>`marivo/analysis/evidence/extraction/delta.py`<br>`marivo/analysis/evidence/extraction/composition.py`<br>`marivo/analysis/evidence/extraction/correlation.py`<br>`marivo/analysis/evidence/extraction/forecast.py`<br>`marivo/analysis/evidence/extraction/anomaly.py`<br>`marivo/analysis/evidence/extraction/event.py`<br>`marivo/analysis/evidence/extraction/funnel.py`<br>`marivo/analysis/evidence/extraction/lifecycle.py`<br>`marivo/analysis/evidence/extraction/subject.py` | executable Dataset origin graphs |

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
- `marivo/skills/marivo-analysis/SKILL.md`;
- the English/Chinese pairs beneath `site/src/content/docs/{docs,zh-cn/docs}/latest/`
  for `index.mdx`, `first-analysis.mdx`, `installation.mdx`, `quick-start.mdx`,
  `concepts/index.mdx`, `concepts/analysis-workflow.mdx`,
  `concepts/evidence.mdx`, `concepts/readiness.mdx`,
  `concepts/semantic-layer.mdx`, `guides/business-question.mdx`,
  `reference/deployment.mdx`, `reference/project-configuration.mdx`, and
  `reference/telemetry.mdx`;
- cross-layer review of `docs/README.md` and
  `docs/specs/semantic/{overview,authoring-workflow,datasource-layer,loading-validation-introspection}.md`.

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

1. the generation-scoped v3 Session Store, independent Evidence Store v1,
   exact relations, keys, indexes, transaction owners, report-timezone
   placement, and fail-closed old-generation boundary in Materialization
   Runtime;
2. the exact ordered 99-symbol target export manifest above, the paired-family
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
contracts, selectors, and family registration without datasource execution or
public exposure.

### Owned implementation

- `Dataset`, `LogicalDataset`, and `MaterializedDataset` internal bases;
- closed paired-family registry;
- `DatasetFieldId`, field identity, physical-type state, field, row-bound,
  cardinality, ordering, byte-count, row-contract, and schema descriptors;
- `LogicalDatasetState` and `MaterializedDatasetState`;
- `DatasetFields` and Session-owned `DatasetFieldRef`;
- immutable Dataset construction, equality, hashing, definition fingerprint,
  and bounded lineage;
- common bounded `repr` and `DatasetContract` terminal protocol;
- private `LogicalRootHandle` and `MaterializedScanLeafHandle` pairing checks;
- common operator-construction protocol returning only Logical state.

### Explicit exclusions

- no datasource binding or Ibis expression;
- no Run, Artifact, receipt, Evidence, or row read;
- no public export or Help route;
- no family-specific Metric/Event/statistical meaning;
- no pickle or logical-Artifact ref.

### Required tests

- family registry duplicate/missing/invalid-pair tests;
- immutable value, equality, hashing, and deterministic fingerprint tests;
- complete logical schema and row-contract construction without execution;
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

Every later family can register one paired state type and construct one complete
logical row contract without importing compiler, runtime, pandas, or storage
implementation.

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

### Owned implementation

- Population and Metric family contracts;
- `Session.source_bindings(...)` validation plus immutable per-source
  construction-time capture;
- exact default Population inference;
- Entity-grained shared Population spine;
- initial `where`, `with_dimensions`, `with_time_axis`, `aggregate`, and
  `metric` definitions needed by the journey;
- private semantic graph and direct Ibis lowering for the journey;
- one supported same-datasource execution domain;
- one local immutable Parquet Dataset storage receipt;
- one producing Run lifecycle;
- output schema/key/count validation;
- family materialization registration;
- canonical zero-Finding or exact Metric Evidence publication;
- one Artifact commit and Materialized Dataset recovery;
- bounded deterministic `show()` and guarded complete `to_pandas()` reads.

### Required evidence

- construction performs zero datasource and storage operations;
- output family, shape, field order, row key, logical types, nullability,
  ordering, cardinality, and fingerprint are complete before execution;
- one complete Ibis expression compiles and executes as one backend query stage;
- Population membership occurs before Metric evaluation and one shared spine
  retains null Metric branches;
- ratio/component and additive Metric fixtures recompute at the requested
  coordinates rather than folding projected values incorrectly;
- Artifact publication occurs only after storage, validation, quality,
  Evidence, Finding set, metadata, and marker succeed;
- `show()` and `to_pandas()` perform no origin query and create no new analysis
  Run;
- cold recovery returns the exact same materialized family and row contract;
- failure at every publication boundary yields one terminal failed Run and no
  partial Dataset authority;
- a parameterized JSON source can construct inside a binding scope and execute
  after scope exit; missing/extra/secret-like inputs fail before a Logical
  Dataset is returned.

### Exit gate

One real supported backend produces a recoverable same-family Materialized
Metric Dataset with complete committed authority. A compiled query, pandas
preview, staged file, or Evidence row without an Artifact marker is not enough.

## Slice 3: Filtering, Coordinates, Ordering, and Explicit Checkpoints

### Outcome

Complete the reusable Dataset algebra needed to construct, bound, materialize,
and extend Entity and aggregate Metric chains.

### Owned implementation

- the complete shared `AnalysisPredicate` builder vocabulary;
- family-specific `where(...)` effects and authority rules;
- exact Population construction and sampling;
- all accepted Metric coordinate shapes and aggregation admission;
- logical versus materialized aggregate/fold matrix;
- registered `MetricDataset.rollup(drop_dimensions=..., grain=...)` over
  Entity-reduced shapes;
- `rank(...)` and `limit(...)`;
- generated field ids and deterministic total ordering;
- materialized scan-leaf compilation;
- materialized Dataset use as direct-membership or identity-projection
  Population input;
- exact explicit checkpoint behavior for multiple downstream definitions.

### Required evidence

- predicate literal/type/null/boolean normalization and adjacent invalid cases;
- authored filter order around sampling and aggregation barriers;
- no unproved predicate reorder or push-through;
- exact, semi-additive, component-aware, cumulative, and materialized fold
  admission matrices;
- logical/materialized rollup parity, strict coarser-grain containment,
  partial-period coverage, retained-state rejection, and no-origin-replay;
- deterministic rank ties and limit prefixes;
- unordered preview canonicalization against a backend with deliberately varied
  natural order;
- materialized leaf predicates do not traverse origin lineage;
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

## Slice 4: Compiler Boundaries and Runtime Reliability

### Outcome

Complete the compiler/runtime infrastructure required by all remaining
operators and storage topologies without creating another public execution
path.

### Owned implementation

- exhaustive semantic-node decoder and immutable lowerer manifest;
- action canonicalization, authored occurrence map, and safe CSE;
- deterministic boundary resolution;
- portable and registered backend-specific Ibis lowerings;
- federation admission;
- direct materialized scan/import;
- bounded Arrow stream and Run-staged Parquet exchange;
- registered DuckDB relational and Python-kernel local stages;
- fixed local execution limits and hard guards;
- local, engine, and object final storage candidates/receipts;
- runtime-ranked sink selection after compiler feasibility;
- execution keys, write-once bindings, producer claims, owner leases, resource
  journals, cleanup, and cold reconciliation;
- captured source-binding validation, key digests, compiler adapter handoff,
  and exhaustive raw-value redaction;
- explicit independent-axis Artifact revalidation;
- committed Session graph projection.

### Required evidence

- exact private graph encode/decode and corruption rejection;
- lowerer-manifest assembly-order fingerprint stability;
- whole-expression one-stage compilation without a second Marivo optimizer;
- portable, backend-specific, unsupported, and compile-rejected outcomes;
- no SQL text postprocessing or execute-and-catch capability probing;
- largest-prefix registered-local cuts selected before data work;
- Arrow/Parquet parity for schema, null, timezone, decimal, dictionary,
  ordering, rows, and decoded-byte guards;
- exact boundary tests at 100,000 rows per local input, 67,108,864 combined
  input bytes, 100,000 output rows, 67,108,864 output bytes, and 60 seconds;
- no partial result for input, total-byte, output, or deadline overflow;
- local, engine, and object receipt round trips and mutation detection;
- deterministic sink feasibility/selection and no retry to another sink after
  selected-sink failure;
- one producer for concurrent same-key execution;
- waiter success, producer failure, timeout, and takeover behavior;
- reservation-before-create and crash injection for every external resource;
- no-marker cleanup/failure and marker-before-Store completion recovery;
- surviving backend work retains its claim until terminal state is proven;
- exact execution-binding recovery with no new Run, SQL, quality extraction,
  storage copy, Evidence, or Artifact;
- source binding execution after authoring-scope exit, changed-value key
  separation, same-value cold reconstruction recovery, and proof of no
  `ContextVar` or mutable Session lookup during execution;
- revalidation reports integrity, storage, Evidence, semantic, and datasource
  axes independently;
- no credential, SQL, raw identity, private plan, locator, or staging path leaks
  into diagnostics.

### Exit gate

Every execution topology ends with exactly one committed recoverable Dataset or
one terminal failed Run with no partial publication. No private exchange is
visible as an Artifact, Dataset, Evidence owner, or graph node.

## Slice 5: Compare and Attribution Vertical

### Outcome

Execute, materialize, recover, and continue the arity-one Metric chain:

```python
delta = current.compare(baseline, alignment=mv.window_bucket())
drivers = delta.attribute(axes=(region, channel), mode="joint")
materialized = drivers.execute()
```

### Owned implementation

- Metric `compare` variant and complete Delta row contracts;
- alignment policies retained by the accepted operator contract;
- additive, component-mix, distinct-membership, and distribution-Shapley
  attribution variants;
- joint and hierarchy row contracts;
- logical missing-axis expansion;
- materialized sufficient-statistic admission and barrier failure;
- Top-K/Other mapping and reconciliation;
- Delta/Attribution family materialization registrations;
- exact quality, Evidence, Finding, and retained-state contracts.

### Required evidence

- all-logical, all-materialized, and role-distinct mixed input topologies;
- exact compatibility across Population, coordinates, Metric identity, scope,
  approximation, and Session;
- null, empty, one-sided, zero-denominator, non-finite, and boundary-time cases;
- numerical differential tests for Delta and every attribution method;
- exact reconciliation at every resolution;
- distribution attribution eight-player boundary;
- materialized missing-axis failure before a Run with a copyable logical repair;
- no materialized-origin replay or current semantic join to recover a missing
  attribution axis;
- cold recovery preserves family, row contract, sufficient statistics, and
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

### Owned implementation

For every exact variant:

- invocation and output registration;
- complete pre-execution row contract;
- action-time requirements;
- exact Ibis, DuckDB, or registered Python-kernel lowerer;
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
- discovery distinguishes no candidate from not evaluated;
- entity-outlier identity remains private but can be consumed explicitly as a
  Population input;
- local kernels receive and emit exact Arrow contracts and never expose pandas
  objects at a stage boundary;
- high-cardinality source work stays in the engine or fails at a hard boundary;
- no statistical-test type, method, Help target, semantic node, or lowerer is
  registered;
- removed discovery objectives and old name have no alias.

### Exit gate

Every accepted Metric operator produces one recoverable Dataset family with
exact status and Evidence authority, and every removed operator is absent from
the private target registry.

## Slice 7: SubjectSet, Event, Lifecycle, and Cross-Domain Loops

### Outcome

Privately execute the full identity-preserving loop:

```text
Metric or entity-outlier Dataset
  -> explicit population= on Event/Lifecycle source
  -> Event journey or Lifecycle history Dataset
  -> typed select_subjects(...)
  -> SubjectSet
  -> explicit population= on Metric/Event/Lifecycle source
```

### Owned implementation

- Subject identity and SubjectSet paired family;
- Event pattern, matching, completeness, journey, funnel, time-to-event,
  selection, comparison, and funnel-attribution variants;
- Lifecycle replay, history, distribution, transitions, dwell, violations,
  and selection variants;
- complete continuation matrices;
- family filter registrations;
- identity-safe metadata and persistence;
- domain materialization contracts and retained private trace state.

### Required evidence

- exact Session, Entity, composite-key, Population, sampling, scope, Pattern,
  StateModel, step, state, and axis admission matrices;
- matching differential tests for all accepted policies and occurrence edge
  cases;
- funnel density, zero-denominator, censoring, grouping reconciliation, and
  no-rematch tests;
- time-to-event complete, incomplete, repeated-attempt, and typed-step tests;
- replay inception, source-origin coverage, missing inception, no-trigger,
  illegal/simultaneous transition, clipping, terminal-state, and censoring
  tests;
- reducer numerical and structural parity;
- structural journey/history filtering rejection and generated-field filter
  registration;
- logical same-plan SubjectSet semi-join with one membership evaluation;
- materialized SubjectSet cold recovery with no origin graph;
- empty complete membership succeeds and uncertain membership publishes no
  SubjectSet;
- high-cardinality Event/Lifecycle execution performs no unbounded local
  transfer;
- adversarial identity scan across errors, logs, Runs, cards, Evidence,
  Findings, telemetry, object names, and recovery diagnostics;
- raw identities appear only in authorized Dataset storage and explicit
  `show()`/`to_pandas()` row reads;
- Event/Lifecycle producer failure publishes no partial identity Artifact or
  Evidence.
- no occurrence-bounds source/read node, Run kind, result type, capability, or
  implicit window inference exists in the target registry.

### Exit gate

Metric, Event, and Lifecycle analysis compose through exact governed identity
without a DataFrame/list/file/SQL bridge, and every output is one same-family
recoverable Dataset or one terminal failed Run with no partial authority.

## Slice 8: Atomic Public, Persistence, Help, and Documentation Cutover

### Outcome

Make the completed private Dataset implementation the only public analysis
surface and replace the persistence generation in the same unreleased change
set.

### Public API switch

1. Replace `marivo.analysis.__all__` with the exact accepted Dataset, policy,
   Session, runtime-read, Evidence, and helper inventory.
2. Publish the common and paired concrete Dataset classes and their exact
   focused Help leaves.
3. Change `Session.observe`, `SessionEvents.match`, and
   `SessionLifecycle.replay` to the lazy source contracts.
4. Retain `Session.source_bindings(...)` with construction-time capture only.
5. Add `Session.population` and publish
   `MetricDataset.rollup(drop_dimensions=..., grain=...)`.
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

1. Set the replacement Session Store to exact `PRAGMA user_version = 3`.
2. Write only:
   - `marivo.analysis_action_run/v2`;
   - `marivo.dataset_artifact/v1`;
   - `marivo.dataset_storage_receipt/v1`;
   - `marivo.dataset_evidence/v1`;
   - `marivo.dataset_artifact_commit/v1`;
   - `marivo.dataset_execution_binding/v1`;
   - `marivo.action_resource_journal/v1`.
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

Run every backend affected by the cutover through its registered profile and
supported contract. At minimum the matrix must explicitly classify DuckDB,
SQLite, Trino, MySQL, PostgreSQL, and ClickHouse as:

- portable Ibis supported;
- exact backend-specific Ibis supported;
- registered bounded local supported;
- federated/materialized-boundary supported;
- or typed unsupported.

Do not claim support from compilation alone. Each supported path needs terminal
row, authority, placement, receipt, and audit evidence against the real backend
or the repository's designated live integration environment.

### Performance and execution-economics gates

The acceptance harness must record query count, stage count, transferred rows,
transferred decoded bytes, output rows, output bytes, peak local RSS where
available, elapsed time, Run count, and Artifact count.

Required binary outcomes:

1. one same-domain relational chain containing filter, projection, fanout-safe
   join, aggregation, window, and order compiles and executes as one engine
   query stage;
2. logical construction emits zero datasource calls and zero Runs;
3. exact binding recovery emits zero datasource calls, zero new Runs, zero
   storage copies, and returns the same Artifact ref;
4. a materialized downstream chain emits no origin-source query;
5. a high-cardinality rank-and-limit journey does not collect the unbounded
   Entity relation into the Marivo process;
6. every bounded-local guard fails atomically at one unit above its exact limit;
7. no execution path inserts sampling to satisfy a resource limit;
8. equal parameterized-source bindings recover without a request while one
   changed value produces a distinct execution key;
9. materialized rollup issues no origin query and publishes only its final
   coarsened Dataset.

### Real-Agent journeys

Each journey runs in a fresh terminal process against a supported real backend.
Evidence includes the script, terminal output, exact Session id, Run id,
Artifact ref, row/authority assertions, and post-run runtime reads. A transcript
or dispatch record is supplementary, not acceptance.

#### Journey A: default Population and Metric coordinates

1. Resolve exact Metric and Dimension inputs through the current catalog.
2. Construct one Entity-grained multi-Metric Dataset.
3. Add a Dimension and time coordinate, then aggregate.
4. Inspect `contract()` before execution.
5. Execute, inspect the Materialized Dataset, read Evidence, and verify one
   backend query stage.

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

#### Journey D: Metric to Event to SubjectSet to Metric

1. Select a governed identity-bearing Metric Dataset.
2. Pass it explicitly as Event `population=`.
3. Match a first-per-subject journey and select dropped subjects.
4. Materialize and recover the SubjectSet in a fresh process.
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

#### Journey F: explicit execution-boundary failure and repair

1. Construct a cross-datasource operator graph with no federation or bounded
   local route.
2. Prove compilation fails before data work.
3. Verify the repair names `.execute()` only when a reachable public Dataset
   input is an independently placeable valid boundary.
4. Materialize that exact input and rerun the downstream action.
5. Prove immutable scan/import and no hidden intermediate Artifact.

#### Journey G: failure atomicity and cold reconciliation

1. Crash once before the Artifact commit marker and prove cleanup plus terminal
   failed Run with no Artifact/Evidence authority.
2. Crash once after the marker but before Session Store finalization and prove
   recovery completes the same Run/Artifact without datasource re-execution.
3. Run two concurrent exact executions and prove one producer and one Artifact.

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
2. Roll it to a coarser grain and drop the Dimension in one request.
3. Prove the canonical time-then-Dimension fold, singleton/empty behavior, and
   complete versus partial target-period coverage.
4. Prove the action scans only the immutable Artifact and sends no origin
   query.
5. Repeat with cumulative period-end and blocked non-additive fixtures; verify
   exact `last` behavior and the direct-target-observation repair.

### Negative cutover audit

Search the tracked package, tests, current specs, latest site docs, examples,
and skill for every forbidden path. The audit must prove absence of:

- public `BaseFrame`, `MetricFrame`, `DeltaFrame`, `AttributionFrame`,
  `EventFrame`, `LifecycleFrame`, `ForecastFrame`, `AssociationResult`,
  `CandidateSet`, and `HypothesisTestResult`;
- public `session.compare`, `session.attribute`, `session.correlate`,
  `session.forecast`, `session.select_subjects`, and Session-owned reducers;
- public `session.events.occurrence_bounds`, `EventOccurrenceBounds`, and any
  observed-range window inference;
- public `frame.transform`, `rollup(drop_axes=...)`, and detached
  selection/cast methods;
- `interesting_slices`, `semantic_hypotheses`, and
  `cross_sectional_outliers` aliases;
- `analysis-artifact/v13`, `marivo.analysis_run/v2`,
  `marivo.analysis_job/v2`, old Store decoders,
  migration, backfill, and dual reads in live implementation;
- public plan, SQL, Ibis, task, future, receipt, staging, claim, or lease types;
- execution-time lookup of parameterized source values from a `ContextVar`,
  mutable Session map, or `execute(...)` argument;
- silent pandas/Polars fallback or unbounded local identity transfer.

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
final immutable storage
  -> exact Artifact metadata
  -> Evidence + Findings + Artifact commit marker
  -> Session Store Artifact row + execution binding + Run success
```

Before the marker, output is unpublished cleanup-eligible staging. After the
marker, recovery must complete the same Artifact publication; it must not turn
the Run into failure or delete the committed output.

### Execution-binding boundary

`DatasetExecutionKeyV1` is Session-local and binds the exact definition,
captured parameterized-source binding digest, semantic dependencies, ordered
logical/materialized input authority, family, row contract, and
implementation/quality/Evidence versions. It does not claim global cache
equivalence or datasource freshness.

The public documentation and skill must state that a same-Session binding hit
recovers the committed snapshot. A user who requires current source rows creates
a new named Session or changes an explicit row-affecting definition input.

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
  contract, authority requirements, blockers, and legal continuations without
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
| Population/Metric row contracts and predicates | 2-3 | compiler and operator admission |
| parameterized source capture, identity, lowering, and redaction | 2 and 4 | compiler plus cold-recovery/runtime audit |
| semantic graph and lowerer manifest | 2-4 | runtime handoff and every operator |
| stage/exchange guards | 4 | runtime failure injection |
| Run/Artifact/binding/publication | 2 and 4 | every producing family |
| Metric operator numerical parity | 5-6 | materialization/Evidence registration |
| Metric rollup fold and coverage parity | 3 | compiler and materialized scan-leaf runtime |
| Subject/Event/Lifecycle correctness and privacy | 7 | compiler, runtime, and redaction tests |
| exports, Help, docs, skill | 8 | independent drift/reachability tests |
| real backend and Agent journeys | 9 | release readiness |

Tests must avoid proving a registry with the renderer that consumes it or a
schema with only the writer that produced it. Decoders, reachability checks,
Help inventories, lowerer manifests, and publication records each need an
independent invariant.

## Slice Document Template

Every implementation slice derived from this plan uses:

```markdown
## Slice N: <name>

### User-visible or runtime outcome
### Frozen contract owners consumed
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
### Disclosure updates
### Explicitly deferred contracts
### Exit gate
```

`Explicitly deferred contracts` may contain only work assigned to a later slice
by this plan. It cannot contain an unresolved signature, owner, output family,
authority rule, schema, migration choice, or compatibility decision.

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
- [ ] Every previous public export is classified and tested as retained,
      replaced, or absent.
- [ ] Every target Dataset family has one paired registration and one complete
      pre-execution row contract.
- [ ] Every downstream operator is Dataset-owned and returns Logical state.
- [ ] Logical construction performs no datasource work and creates no Run.
- [ ] `execute()` is the only public logical-to-materialized transition.
- [ ] Materialized reads and downstream operations never replay origin graphs.
- [ ] Exact same-Session execution bindings recover without a new Run or SQL.
- [ ] Parameterized source bindings are captured at construction, separated by
      exact execution identity, redacted everywhere durable/visible, and never
      reread from ambient state by `execute()`.
- [ ] Metric rollup has one current-row fold meaning across Logical and
      Materialized inputs and never replays origin graphs.
- [ ] Every producing family publishes strict complete Evidence or fails with
      no partial Artifact.
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

Implementation slices
  = private code and tests against one bounded outcome

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
