# Evidence Access Surface

Status: design. This document specifies the evidence surface that
`marivo.analysis` exposes to general coding agents: how evidence is attached to a
result, how an agent asks "how far have I judged this session," how the underlying
evidence objects are reached for audit/replay, and where judgment state is
persisted. It is a companion to [`operators-and-frames.md`](operators-and-frames.md)
(result families, `FollowupAction`/`BlockingIssue`/`ConfidenceScope` base schema)
and [`session-state-and-runtime.md`](session-state-and-runtime.md) (the session
directory this ledger lives in).

`marivo.analysis.evidence` is the single evidence runtime for the library. It
depends only on analysis frames, meta, lineage, the `AnalysisError` taxonomy, and a
session-local `judgment.db`.

Two independent extensions reuse this same runtime. The semantic execution
extension adds `EventFrame` and `LifecycleFrame` observations. The optional
ontology discovery extension adds `CandidateSet[semantic_hypothesis]` lineage
without adding findings. Neither introduces a second evidence store, a causal
fact type, or a SemanticEdge proposition path, and Event/Lifecycle evidence
does not require ontology.

## Purpose

The surface fixes one answer for each of these questions:

- When an agent holds a result, which fields carry its evidence information.
- When an agent asks "what have I established in this session," what it calls and
  what typed object it gets back.
- When an agent needs audit, replay, or cross-session reference, how the evidence
  chain objects themselves are exposed.
- Whether evidence emission is a side effect of committing a step or an explicit
  trigger.
- Under what rules the runtime generates a follow-up, and how much to trust one.
- Where judgment state lives on disk.

## Non-goals

The surface does not define: business-domain or strategic follow-ups (the agent's
own job — the runtime does not impersonate business judgment); heuristic semantic-
axis enumeration suggestions; an agent-authored proposition write path; any wire
contract; UI projection copy; or causality inferred from catalog edges,
association results, or tested hypotheses.

## Core principles

**P1 — Evidence is a side effect of committing a step.** Submitting
`session.compare(...)` triggers the runtime to extract findings, seed propositions,
recompute assessments, and fill typed fields on the result. An agent completes a
full workflow through operators and read methods alone and never has to learn the
internal evidence layering. An extractor may deliberately emit zero findings for
an artifact shape whose contract is candidate-only, including
`CandidateSet[semantic_hypothesis]`; zero findings is distinct from extraction
failure.

**P2 — Three surfaces serve three situations.** Result-bound (Surface 1, every
step) answers "where do I go next"; session-bound (Surface 2, key decision points)
answers "what do I know / still doubt / do next"; object-bound (Surface 3, audit)
serves replay and UI. An agent on the main path only recognizes Surface 1's four
types plus Surface 2's default entry (`SessionKnowledge` and its five section
types).

**P3 — Names are semantic, not engine-internal.** Surface 2 is organized around
fact shapes an agent understands (`ChangeFact`, `AttributedDriver`,
`TestedHypothesis`, `ForecastSummary`, `AssociationSummary`, `OpenAnomaly`), not
`Proposition[change]` / `Assessment[...]`. Engine names appear only in Surface 3.

**P4 — Evidence failure never blocks analysis.** The artifact and intrinsic
metadata land in the outer transaction. Observation extraction and finding
insertion may use a shape-specific inner savepoint; proposition seeding,
assessment, follow-up, and blocking-issue writes use the existing later
savepoint. Either savepoint can roll back independently while the artifact
still commits with `evidence_status="partial"`. The agent always gets a result
and can proceed. Surface 1 signals degradation via `evidence_status`; Surface 2
via `evidence_completeness`.

**P5 — `recommended_followups` is strictly C1 + C2.** The runtime emits only two
categories: `dag_continuation` (C1 — a mechanically legal downstream operator from
the [shape-aware DAG](operators-and-frames.md#shape-aware-dag)) and
`quality_remediation` (C2 — a deterministic fix for a specific `BlockingIssue`).
Business/strategic and heuristic suggestions are the agent's to generate. Prefer an
empty list over noise: when no deterministic C1/C2 follow-up applies, the field
stays empty.

**P6 — One evidence runtime.** `marivo.analysis.evidence` is the only evidence
runtime; it fails closed with structured `AnalysisError`s and persists to a
session-local `judgment.db`. It stores no secrets, no free text, and no raw frame
data.

## Surface 1: result-bound

The result an agent gets back (`MetricFrame`, `DeltaFrame`, `AttributionFrame`,
`CandidateSet`, `AssociationResult`, `HypothesisTestResult`, `ForecastFrame`,
`EventFrame`, `LifecycleFrame`, `QualityReport`) carries evidence as flat fields
on `result.meta`, with no nested wrapper and no extra read call:

| Field | Type | Source |
| --- | --- | --- |
| `artifact_id` | `str` | Generated at commit (replay-stable canonical id) |
| `subject` | `EvidenceSubject` | Derived from the artifact's typed semantics |
| `source_refs` | `list[ArtifactRef]` | Upstream step artifact refs |
| `lineage` | `Lineage` | Alignment, definition compatibility, cleaning steps, `triggered_by_followup` |
| `confidence_scope` | `ConfidenceScope` | See [ConfidenceScope](#confidencescope-cross-step-compatibility) |
| `quality_summary` | `QualitySummary \| None` | Lightweight summary computed synchronously from artifact payload + lineage; **not** `assess_quality` output; not a step |
| `blocking_issues` | `list[BlockingIssue]` | Filled synchronously at commit |
| `recommended_followups` | `list[FollowupAction]` | Filled at commit; C1 + C2 only |
| `evidence_status` | `Literal["complete","partial","unavailable"]` | See [fallback](#evidence_status-fallback) |
| `evidence_summary` | `ArtifactEvidenceSummary \| None` | Bounded commit-time display snapshot; rendered by `artifact.show()` when present |

All Surface 1 fields are read via `frame.meta`; there is no `frame.evidence.*`
namespace. `artifact_id` is the replay-stable identity; `frame.ref` is a loading
alias equal to `artifact_id`.

`EvidenceSubject` is a closed tagged union, not one optional-field mega-class:

```text
MetricEvidenceSubject
    metric: MetricRef
    entity: existing normalized metric subject identity
    slice: canonical scalar selector map
    grain: normalized grain or none
    analysis_axis: scalar | time | segment | panel | change |
                   decomposition | correlation | forecast | anomaly

EventEvidenceSubject
    events: ordered tuple[EventRef, ...]
    participant_roles: ordered tuple[ParticipantRoleRef, ...]
    entity: EntityRef
    analysis_shape: sequence | funnel | time_to_event

LifecycleEvidenceSubject
    state_model: StateModelRef
    entity: EntityRef
    analysis_shape: distribution | transitions | dwell | violations
```

`MetricEvidenceSubject` retains its current subject fields and meaning; the
semantic execution extension does not rewrite Metric identity. The tag and
every ordered ref in the two new variants are part of their canonical subject
keys. Time windows, alignment, matching policy, replay policy, analysis scope,
and definition versions remain in their existing lineage and
`ConfidenceScope` owners rather than being duplicated in the subject payload.

`result.meta.evidence_summary` is an optional, immutable commit-time display
snapshot. When present, `artifact.show()` renders at most five observation,
fact, or open-item statements before the result preview. The summary is restored
from frame `meta.json`; rendering never reads `judgment.db`.

An absent summary means evidence emission was suppressed, evidence was
unavailable, summary projection failed with an explicit warning, or the frame
predates this field. Full evidence remains in `session.evidence`; session-wide
synthesis remains in `session.knowledge()`.

A `CandidateSet` distinguishes artifact-level follow-ups from row-level typed
affordances. `result.meta.recommended_followups` contains only C1/C2
`FollowupAction` values generated at commit. A row's `affordances` field contains
`ArtifactAffordance` values read through
`select(..., attribute="affordances")`; those values are candidates for agent
judgment and never enter `SessionKnowledge.next_steps()`.

For `CandidateSet[semantic_hypothesis]`, a ready row additionally exposes
`analysis_target`, an immutable `SemanticMetricCandidate` containing the target
Metric ref, candidate-set and item identities, `SemanticEdgeRef`, inherited
scope/time contract, and readiness fingerprint. It has no public constructor.
`session.observe(analysis_target)` unwraps the governed Metric and persists this
origin in the new MetricFrame's lineage. A blocked row has no
`analysis_target`; selecting it raises the row's typed semantic or readiness
repair rather than returning a Metric ref. This is the only automatic
provenance bridge from semantic candidate discovery into the evidence chain.

### `FollowupAction` category

On the base `FollowupAction` schema (from
[`operators-and-frames.md`](operators-and-frames.md)) the evidence surface adds a
required closed `category` (`dag_continuation` | `quality_remediation`) and a
`source_issue_id` that is required exactly when `category="quality_remediation"`.
Adding a category is a spec change, not a runtime extension.

### `result.meta.quality_summary` vs `session.assess_quality(...)`

| | `result.meta.quality_summary` | `session.assess_quality(result)` |
| --- | --- | --- |
| Trigger | Automatic at commit | Explicit agent call |
| A step in the DAG? | No | Yes (a core operator) |
| Produces an artifact? | No (embedded summary) | Yes (a canonical `QualityReport`) |
| Depth | Coverage, null rate, sample size, definition compatibility | Full `QualityReport[shape]` |
| Recomputed? | No; stored in SQLite | Each explicit call creates a new step |

Sampled folds additionally produce a linked `CoverageFrame` (via
`frame.coverage()`); its time-slot coverage summarizes into
`result.meta.quality_summary`.

### `evidence_status` fallback

| `evidence_status` | Failing stage | Still filled | May be empty |
| --- | --- | --- | --- |
| `complete` | none | all fields | none |
| `partial` | semantic-execution observation extraction/write or savepoint seeding/assessment/follow-up/blocking-issue write | `artifact_id`, `subject`, `source_refs`, `lineage`, `quality_summary`, `confidence_scope`, `blocking_issues` (incl. one `evidence_partial`) | Failed observation digest, `evidence_summary`, and `recommended_followups` (possibly partial C1) |
| `unavailable` | judgment store unavailable at startup | intrinsic fields computed by this step (id/subject/source_refs/lineage/quality/confidence); `blocking_issues` carries one `evidence_store_unavailable` | `recommended_followups` (no store to persist/dedupe); `evidence_summary` is `None` (`evidence_summary_unavailable`) |

Under `unavailable` the result is in-memory only: downstream operators in the same
process can still consume it, but a restart loses it and
`session.knowledge().evidence_completeness` stays `unavailable` until the store
recovers.

Result-bound deliberately does **not** carry judgment state
(`validated`/`refuted`/`inconclusive`), `proposition_id`/`assessment_id`,
cross-step accumulated facts, or support/oppose aggregation — those are Surface
2/3.

### Semantic-execution observation digests

Every successful `EventFrame` and `LifecycleFrame` artifact commit invokes
exactly one shape-dispatched observation extractor. With
`evidence_status="complete"`, it emits exactly one
`finding_type="observation"` digest. Extractor or digest-write failure does not
fabricate an observation or roll back the artifact; it returns the artifact
with `evidence_status="partial"` and the typed `evidence_partial` issue. The
digest payload is a closed tagged variant and is bounded by the same private
evidence-summary limits as metric observations:

| Artifact shape | Bounded digest |
| --- | --- |
| `EventFrame[sequence]` | Journey, completed, partial, and coverage-censored counts; unused-event count; bounded step completion counts. |
| `EventFrame[funnel]` | Cohort, completed-final-stage, partial, coverage-censored, and unused-event counts plus bounded ordered stage counts and conversion rates. |
| `EventFrame[time_to_event]` | Attempt, completed, not-completed, coverage-censored, and unused-end counts plus bounded elapsed-duration summary. |
| `LifecycleFrame[distribution]` | Total subjects and bounded state count/share rows. |
| `LifecycleFrame[transitions]` | Total transitions and bounded from/to count/share rows. |
| `LifecycleFrame[dwell]` | Bounded per-state interval/completed/censored counts and mean/median/p90 duration. |
| `LifecycleFrame[violations]` | Total violations and bounded counts by closed violation kind and current state. |

The digests contain aggregates and semantic refs, never raw subject, journey,
or event identities. They seed no proposition or fact. In particular, a
lifecycle violation is an observed modeled transition failure; it is not
automatically a policy breach, quality blocker, business rule, or causal claim.

`CandidateSet[semantic_hypothesis]` emits no finding, observation, proposition,
fact, open item, or system-level follow-up. Its `SemanticEdgeRef`, provenance,
target, readiness, reason codes, and item-level affordances remain artifact
payload and lineage only. A later correlation or hypothesis-test artifact uses
the existing `association` or `tested_hypothesis` evidence path and adds the
originating candidate-set, candidate-item, and SemanticEdgeRef to its source
lineage. That statistical result is still not causal evidence.

## Follow-up generation rules

`recommended_followups` is generated synchronously at commit and is a first-class
contract: any follow-up not traceable to a C1 or C2 rule below is a spec
violation.

**C1 — `dag_continuation`.** Look up the current `(family, shape)` in the
[shape-aware DAG](operators-and-frames.md#shape-aware-dag) and emit a legal
downstream operator only when (1) it runs on the current artifact with default or
its own closed-enum parameters — the runtime infers no new ref and no policy, (2)
all its input refs already resolve in the session, and (3) it introduces no
un-pruned enumeration. The whitelist:

| Source artifact | C1 follow-ups |
| --- | --- |
| `MetricFrame[time_series]` | `discover.point_anomalies`, `discover.interesting_windows`, `forecast(horizon=default)`, `assess_quality` |
| `MetricFrame[segmented]` | `discover.interesting_slices`, `discover.cross_sectional_outliers`, `assess_quality` |
| `MetricFrame[panel]` | union of the two rows above |
| `MetricFrame[scalar]` / `DeltaFrame[*]` / `AttributionFrame` / `CandidateSet[*]` / `AssociationResult` / `HypothesisTestResult` / `ForecastFrame` / `EventFrame[*]` / `LifecycleFrame[*]` | `assess_quality` (plus, for a delta: `discover.driver_axes`, `discover.period_shifts` for time_series/panel deltas, `discover.interesting_slices` for time_series/segmented/panel deltas) |
| `QualityReport` | none (terminal) |

Operators that need a judgment input are **not** auto-emitted: `attribute`/
`decompose` (needs an axis), `compare` and `correlate` (need the other frame),
`hypothesis_test` (needs hypothesis + `SamplingPolicy`), non-`assess_quality`
`transform` ops (need predicates/policy), and any composite. The agent generates
those calls itself.

`discover.semantic_hypotheses` is also never emitted as C1. Although the input
family may be compatible, choosing ontology-guided hypothesis discovery is an
agent judgment. Likewise, a semantic-hypothesis row's `observe` affordance stays
inside that row and never becomes an artifact-level C1 action.

**C2 — `quality_remediation`.** For each `BlockingIssue`, consult a fixed
`kind → remediation` map and emit a C2 follow-up (with `source_issue_id`) only when
the fix runs from the current artifact plus fields the issue already provides.
Examples: `null_rate_high → transform(impute_nulls | filter)`;
`comparability_incompatible → compare(alignment=<issue.suggested_alignment>)` when
lineage resolved one; `evidence_partial → retry_evidence_pipeline`. Issues that
require the runtime to guess a policy (e.g. `sample_size_low` — "widen by how
much") emit **nothing**; the agent decides.

**Conformance.** Every emitted action must map to exactly one whitelist/remediation
row; `category="dag_continuation" ⇔ source_issue_id IS NULL`; the runtime picks no
axis/pairing/sampling; regenerating on the same result is byte-equal; and
generation reads only shape + lineage + blocking issues + the session artifact
index, never raw frame data.

## Surface 2: session-bound

After several steps, an agent asks across the session: what do I know, what is
still open, what next. The entry is a read method that creates no step and no
lineage:

```python
knowledge = session.knowledge()   # immutable SessionKnowledge snapshot
```

`SessionKnowledge` exposes:

- `facts(kind=None)` — established facts; `kind ∈ {change, driver, tested_hypothesis, forecast, association}`.
- `observations()` — bounded metric, event, and lifecycle observation digests,
  oldest first.
- `open_items(kind=None)` — items awaiting judgment/review; `kind ∈ {anomaly, question}`.
- `blocked_followups()` — follow-ups a `BlockingIssue` prevents executing.
- `next_steps(top=5)` — deduped, commit-ordered, not-yet-executed follow-ups across all results.
- `for_subject(subject)` — a sub-view filtered by subject canonical key.

`evidence_completeness` gates consumption: `complete` (store healthy),
`partial` (≥1 step is `evidence_status=partial`; lists may undercount), or
`unavailable` (store down — **all lists are empty, but "empty" means "unknown," not
"none"**). Check it before reading the lists.

**Typed facts.** `facts(kind=...)` returns closed variants sharing base fields
(`id`, `kind`, `subject`, `window`, `status` ∈
`validated|refuted|inconclusive|pending`, `confidence`, `confidence_basis`,
`source_refs`, `latest_assessment_id`) plus per-kind fields — e.g. `ChangeFact`
adds `direction`/`magnitude`/`comparison_window`; `TestedHypothesis` adds
`method_family`/`alpha`/`p_value`/`reject_null`. Fact fields are a deterministic
projection of proposition + latest assessment + seed finding.

**Observations.** `observations()` projects the `finding_type="observation"` digest
findings directly (no proposition/assessment join), ordered by commit time.
Observations are ground truth, not testable claims, so they carry no
`status`/`confidence`. Each `metric_frame` commit appends exactly one bounded,
shape-dispatched digest (scalar value; time-series first/last/min/max/mean +
direction; segmented total + top segments; panel bucket/segment counts + top
segments). Composition fields (`total_value`, `share`, panel `top_segments`) are
filled only when the metric is `additive`. Each `EventFrame` or
`LifecycleFrame` commit appends the one closed digest defined above. Semantic-
hypothesis CandidateSets append no observation.

**Open items.** Two kinds only: `OpenAnomaly` (an anomaly candidate seeded as a
proposition, not yet validated/refuted) and `OpenQuestion` (`reason ∈
{reopened_gap, persistent_blocking_issue}`).

**`next_steps(top=5)`** dedupes `recommended_followups` across results by
`(operator, canonical(input_refs), canonical(params))`, keeps commit order, filters
executed items, and does **not** semantically rank — ordering is the agent's. There
is no unified dispatcher: the agent reads a `FollowupAction` and calls the matching
typed operator itself, filling parameters from `action.input_refs`/`action.params`.

## Surface 3: object-bound

Engine-object access for audit and replay; the default agent never touches it. It
is reached through the `session.evidence` namespace:

```python
session.evidence.findings(artifact_id=None, finding_type=None, subject=None)   # -> Iterator[Finding]
session.evidence.propositions(proposition_type=None, subject=None, status=None) # -> Iterator[Proposition]
session.evidence.assessments(proposition_id=None, latest_only=True)             # -> Iterator[Assessment]
session.evidence.proposition(proposition_id)          # -> Proposition
session.evidence.latest_assessment(proposition_id)    # -> Assessment | None
session.evidence.trace(proposition_id)                # -> EvidenceTrace
```

An `EvidenceTrace` links a proposition to its `latest_assessment`, `seed_findings`,
`support_findings`, `oppose_findings`, `source_artifacts`, and `source_steps`. All
Surface 3 objects are immutable, reference fields are typed `*Ref`s, and subtype
payloads are `TypedDict` unions. `action_proposals` is not exposed (no policy
engine writes them). Delta finding payloads carry a `unit: str | None` field
(the subject metric's declared UCUM unit, threaded from `MetricIR.unit`).
Semantic-execution observation findings carry the exact
`EventEvidenceSubject` or `LifecycleEvidenceSubject` variant and one closed
digest payload. There is no proposition query result whose origin is a
SemanticEdgeRef or semantic-hypothesis candidate because neither source is
seeded.

## Storage: `judgment.db`

Each session has one `judgment.db` at
`<project_root>/.marivo/analysis/sessions/<session_id>/judgment.db`. SQLite is the
source of truth for artifact metadata, lineage, findings, propositions,
assessments, blocking issues, and follow-ups; the frame `meta.json` only caches
Surface 1 fields for load ergonomics, and parquet holds raw data only.

**Commit pipeline.** A step writes `frames/<artifact_id>/data.parquet` (temp +
fsync + rename), computes its SHA-256, then in one SQLite transaction inserts the
artifact metadata, extracts and inserts findings, and computes `confidence_scope` +
`quality_summary`; a `SAVEPOINT` then seeds propositions, recomputes latest
assessments, and generates C1/C2 follow-ups + blocking issues. If the savepoint
body fails it rolls back to the savepoint, sets `evidence_status="partial"`, adds an
`evidence_partial` blocking issue, and the outer transaction still commits.
Cross-store consistency reduces to single-store atomicity; orphaned frame
directories from a failed commit are GC'd on startup.

The semantic execution extension preserves the outer transaction and the
existing proposition savepoint. For an `EventFrame` or `LifecycleFrame`, one
inner observation savepoint surrounds only the shape-dispatched extractor and
finding insert. Failure rolls back that digest, marks the artifact `partial`,
records `evidence_partial`, skips proposition seeding for the absent finding,
and still commits the outer artifact transaction. Success releases the digest
into the outer transaction before the existing proposition savepoint, so a
later seeding/assessment/follow-up failure keeps the observation. A semantic-
hypothesis CandidateSet validly inserts zero findings and skips proposition
seeding; zero is not converted to `partial`. This ontology discovery behavior
is independent of Event/Lifecycle observation extraction. When `observe`
consumes a `SemanticMetricCandidate`, the artifact row and lineage payload
retain the candidate-set ref, item id, `SemanticEdgeRef`, and readiness
fingerprint so all downstream source-artifact traversal can recover that
origin.

**Schema** (`EXPECTED_SCHEMA_VERSION = 1`, WAL, `foreign_keys = ON`):

| Table | Key columns |
| --- | --- |
| `artifacts` | `artifact_id` PK, `session_id`, `step_type`, `artifact_type`, `subject_payload`, `lineage_payload`, `confidence_scope`, `quality_summary`, `evidence_status`, `frame_path`, `frame_sha`, `triggered_by_followup`, `committed_at_us` |
| `findings` | `finding_id` PK, `artifact_id` FK, `finding_type`, `canonical_item_key`, `subject_payload`, `payload`, `committed_at_us`; `UNIQUE(artifact_id, finding_type, canonical_item_key)` |
| `propositions` | `proposition_id` PK, `proposition_type`, `origin_kind` (`system_seeded`), `subject_key`, `payload`, `seed_finding_refs` |
| `assessment_snapshots` | `snapshot_id` PK, `proposition_id` FK, `status`, `confidence`, `confidence_basis`, `payload`, `is_latest` |
| `assessment_edges` | (`snapshot_id`, `finding_id`, `role`) PK — support/oppose/seed links |
| `blocking_issues` | `issue_id` PK, `artifact_id` FK, `kind`, `severity`, `payload`, `resolved_by_step_id` |
| `followups` | `followup_id` (= `action_id`) PK, `source_artifact_id` FK, `category`, `source_issue_id` FK, `operator`, `payload`, `executed_step_id` |

Time discipline: every time column is an `INTEGER` of microseconds since the Unix
epoch UTC with a `_us` suffix; Python `datetime`s are timezone-aware UTC. Migration:
startup compares `PRAGMA user_version` to `EXPECTED_SCHEMA_VERSION` — lower applies
`migrations/v{n}_to_v{n+1}.sql`, higher raises `SchemaVersionMismatchError`, and a
failed migration raises `MigrationFailedError` leaving the db intact. Concurrency:
WAL allows many readers + one writer; a second writer on the same session raises
`SessionLockedByAnotherProcessError`; there is no multi-writer or cross-session
query.

## Identity and stability

| Id | Generation | Stability |
| --- | --- | --- |
| `session_id` | at session create (`sess_<hex>`, persisted) | stable across processes |
| `artifact_id` | `stable_hash(step_type, normalized_inputs, normalized_params, semantic_anchors)` | same inputs replay to the same id |
| `finding_id` | `stable_hash(artifact_id, finding_type, canonical_item_key)` | follows `artifact_id` |
| `proposition_id` | seeding identity normalization | stable across replay |
| `followup.action_id` | `stable_hash(source_artifact_id, category, operator, canonical(input_refs), canonical(params))` | same result replays to the same id |

Replay normalizes inputs to typed refs, params through RFC 8785 JCS, and semantic
anchors to catalog id + version. `EvidenceSubject` canonicalization dispatches
by variant. `MetricEvidenceSubject` preserves the existing ordered metric,
entity, slice, grain, and analysis-axis JCS payload and resulting `subject_key`.
The `EventEvidenceSubject` and `LifecycleEvidenceSubject` keys add their tagged
variant name to the ordered refs and scalar fields before the same
`canonical_key(subject)` (JCS + SHA-256 prefix) operation.
`MetricEvidenceSubject.grain` stores the normalized token (`"day"`,
`"5minute"`). Event order and participant-role order are semantic and remain in
the key; lifecycle identity includes the state-model ref and shape. A
`SemanticMetricCandidate` origin affects artifact identity and lineage but does
not change the resolved metric's `MetricEvidenceSubject` key, so the same metric
observation remains comparable while its discovery provenance is recoverable.

Follow-up execution lineage is an internal field
(`triggered_by_followup`); the current public `session.*` wrappers do not set it, so
`next_steps()` may re-surface an executed action until an implementation records the
`executed_step_id` marker.

## Exception taxonomy

All evidence exceptions subclass `AnalysisError` and carry `kind`/`message`/
`hint`/`expected`/`received`/`location`/`repair` (stable typed fields):

| Exception | Trigger |
| --- | --- |
| `EvidenceStoreUnavailableError` | `judgment.db` cannot be opened / IO error |
| `FollowupGenerationRuleViolatedError` | runtime attempted a non-C1/C2 follow-up (bug guard) |
| `PropositionNotFoundError` | `session.evidence.proposition(id)` id absent |
| `FindingExtractionFailedError` | extractor error or contract violation |
| `SchemaVersionMismatchError` | db schema newer than the code |
| `MigrationFailedError` | migration SQL failed |
| `SessionLockedByAnotherProcessError` | concurrent writer on one session |
| `EvidencePartialError` | savepoint body failed after artifact+findings committed (surfaces as a `BlockingIssue`) |

## Analysis flow integration

Committing a step runs, in order: build & run the Ibis expression → write the
parquet (temp+fsync+rename) → open the SQLite transaction → insert artifact metadata
→ extract findings → compute confidence/quality → (savepoint) seed propositions,
recompute assessments, generate C1/C2 follow-ups + blocking issues → commit → return
the result with all Surface 1 fields. Failure semantics follow P4: a stage-1–4c
failure aborts the outer transaction and GCs the parquet; a savepoint-body failure
degrades to `partial` and still commits; a store-unavailable startup yields an
in-memory-only `unavailable` result. Whole-session replay keeps every
`artifact_id`/`finding_id`/`proposition_id`/`action_id` and the
`SessionKnowledge.snapshot_id` stable for the same inputs and store state.

## ConfidenceScope cross-step compatibility

`result.meta.confidence_scope` is an exposed field, not an automatic gate. When
reasoning across results an agent **should** call
`confidence_scope.compatible_with(other)`, which returns
`exact | compatible | incompatible | unknown` (aligned with metric-definition
compatibility); the runtime does not auto-reject a step. A `ConfidenceScope` carries
`metrics`, `dimensions`, `time_window`, `alignment`, `assumptions`, and
`definition_versions`.

With the semantic execution extension, `ConfidenceScope` becomes a closed
tagged union rather than adding event/lifecycle optional fields to the Metric
shape:

- `MetricConfidenceScope` retains the fields above and uses
  `MetricEvidenceSubject`;
- `EventConfidenceScope` carries `EventEvidenceSubject`, cohort and follow-up
  bounds, matching-policy fingerprint, analysis scope, assumptions, and
  definition versions;
- `LifecycleConfidenceScope` carries `LifecycleEvidenceSubject`, analysis/as-of
  bounds, replay or state-projection evidence-path fingerprint, analysis scope,
  assumptions, and definition versions.

Compatibility across different scope variants is `incompatible` in v1. Within
one variant it compares the complete typed contract; it never treats shared
time bounds or Entity identity alone as sufficient compatibility.

## Namespace overview

```python
# Public (default agent surface)
session.observe(...); session.compare(...); session.attribute(...)
session.discover.<objective>(...); session.correlate(...)
session.hypothesis_test(...); session.forecast(...); session.assess_quality(...)
session.events.sequence(...); session.events.funnel(...)
session.events.time_to_event(...)
session.lifecycle.distribution(...); session.lifecycle.transitions(...)
session.lifecycle.dwell(...); session.lifecycle.violations(...)
session.knowledge() -> SessionKnowledge

# Result meta fields (auto-filled)
result.meta.artifact_id / lineage / confidence_scope / quality_summary
result.meta.blocking_issues / recommended_followups  # C1 + C2 only
result.meta.evidence_status

# Semi-public (audit / advanced)
session.evidence.findings(...) / propositions(...) / assessments(...)
session.evidence.proposition(id) / latest_assessment(id) / trace(id)
```

## Semantic and ontology extension verification

The atomic semantic execution candidate must add fixtures for every new subject
and artifact shape. The separately atomic ontology discovery candidate adds its
own non-seeding and lineage fixtures:

1. Commit each `EventFrame` shape and assert one bounded
   `EventEvidenceSubject` observation, no proposition/fact, stable replay ids,
   and complete recovery of evidence summary and scope.
2. Commit each `LifecycleFrame` shape under replay and state-projection evidence
   paths and assert the matching `LifecycleEvidenceSubject` observation. A
   violation remains an observation and creates no rule, policy, quality
   blocker, proposition, fact, or causal claim.
3. Commit `CandidateSet[semantic_hypothesis]` with ready, projection-missing,
   and readiness-blocked rows. Assert zero findings/propositions/facts/open
   items, no artifact-level recommended follow-up, deterministic row ordering,
   and the exact item-level affordances.
4. Select a ready row's `SemanticMetricCandidate`, observe it, then correlate or
   hypothesis-test the resulting MetricFrame. Assert that candidate-set, item,
   and `SemanticEdgeRef` survive through source lineage while the evidence kind
   remains `association` or `tested_hypothesis`, never causality.
5. For every new observation shape, inject (a) an observation extractor/write
   failure and assert a usable artifact with `evidence_status="partial"`, a
   typed `evidence_partial` issue, and no fabricated digest, then (b) a later
   proposition-savepoint failure and assert that the artifact and observation
   remain committed while failed seeding/assessment/follow-up writes roll back.
6. Verify that subject and confidence-scope compatibility is exact within each
   tagged variant and incompatible across metric, event, and lifecycle variants.

Surface tests also assert that `SessionKnowledge.observations()` includes event
and lifecycle digests in commit order, `next_steps()` excludes candidate
item-level affordances, Surface 3 exposes no SemanticEdgeRef proposition, and
neither extension creates a second evidence database or namespace. Metric,
Event, and Lifecycle commits remain legal with ontology not configured; the
`ontology_not_configured` versus configured-empty distinction belongs to the
analysis capability precondition and produces no evidence row by itself.
