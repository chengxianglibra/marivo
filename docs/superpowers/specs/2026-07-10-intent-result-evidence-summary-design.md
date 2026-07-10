# Intent result evidence summary

Date: 2026-07-10
Status: Design approved, pending implementation plan

## Problem

Analysis intents synchronously run the evidence pipeline before returning their
typed frame or result. `commit_result()` extracts findings, seeds propositions,
computes assessments, persists the canonical evidence, and then returns the
same frame. The returned artifact exposes only lightweight status fields such
as `evidence_status`, `quality_summary`, and `blocking_issues`.

This leaves a gap in the default agent workflow. `artifact.show()` displays the
computed result and a status such as `evidence=complete`, but it does not show
the bounded observation, fact, or open item produced by the evidence rules. An
agent must switch to the session-wide `session.knowledge()` snapshot or query
`session.evidence` by artifact id to discover what the immediately preceding
intent concluded. That extra read is easy to omit and mixes two distinct
responsibilities:

- an intent result should expose the evidence needed to understand that step;
- session state should aggregate multiple steps for recovery, synthesis, and
  audit.

The raw evidence volume cannot be assumed to be small. Time-series observation,
segmented comparison, attribution, anomaly discovery, and forecasting can emit
one finding per bucket, segment, contribution row, candidate, or horizon. The
agent-facing projection can and should be deliberately small even when the
canonical evidence is large.

## Goals

- Make one `artifact.show()` call sufficient to inspect both the typed result
  and the bounded evidence produced by that intent.
- Preserve the existing intent signatures and typed return families.
- Persist an immutable evidence summary as it existed when the intent was
  committed, so a recovered frame renders the same conclusion later.
- Keep `judgment.db` as the only source of full findings, propositions,
  assessments, and traces.
- Bound the inline evidence independently of raw finding count and make every
  omission explicit.
- Distinguish complete, empty, partial, unavailable, and deliberately
  non-emitting paths without inventing evidence.
- Keep rendering free of hidden database I/O.

## Non-goals

- No `IntentResult[T]` wrapper or change to an intent return type.
- No `artifact.evidence()` method in this version.
- No new session API or evidence-store schema.
- No change to finding extraction, proposition seeding, assessment, or
  followup business rules.
- No change to `SessionKnowledge` or the raw `session.evidence` audit surface.
- No evidence emission from frame-local transforms that currently call
  `commit_result(..., emit_evidence=False)`.
- No migration or rewriting of historical frame metadata.
- No recommendation or next-step judgment in the summary.

## Alternatives considered

### Persist a bounded summary on the existing result (selected)

Generate a small snapshot during `commit_result()`, persist it in frame meta,
and render it from `artifact.show()`. This keeps the existing typed result flow,
preserves commit-time semantics, and needs no read from `judgment.db` during
rendering.

### Query `judgment.db` from `artifact.show()`

This avoids a new metadata field but gives `show()` hidden I/O and makes an old
result display the latest session assessment rather than the assessment that
existed when the result was returned. It conflicts with the selected snapshot
semantics.

### Add a result wrapper or require `artifact.evidence().show()`

A wrapper provides a clean container but changes every intent call shape. A
second `show()` preserves return types but recreates the omission risk this
design is meant to remove. Both add more public surface than the problem needs.

## Data model

`BaseFrameMeta` gains one optional field:

```python
evidence_summary: ArtifactEvidenceSummary | None = None
```

The summary is a frozen, extra-forbid evidence DTO:

```python
class ArtifactEvidenceSummary(BaseModel):
    finding_count: int
    items: tuple[ArtifactEvidenceItem, ...] = ()
    omitted_count: int = 0
```

Each item is a deterministic, agent-readable projection of one observation,
fact, or open item:

```python
class ArtifactEvidenceItem(BaseModel):
    kind: ArtifactEvidenceItemKind
    statement: str
    status: AssessmentStatus | None = None
    confidence: float | None = None
```

The kind alias reuses the existing evidence vocabularies instead of copying
their literals:

```python
ArtifactEvidenceItemKind = Literal["observation"] | FactKind | OpenItemKind
```

`statement` is a deterministic English single-line rendering of values already
present in the committed evidence payload. It must not contain a recommendation
or agent-authored interpretation. Assessment status and confidence stay in
typed fields rather than being encoded only in the text.

The summary is intentionally a display projection, not a second evidence
model. It does not contain raw findings, propositions, assessments, evidence
edges, traces, or followup actions. Full typed evidence remains available from
`session.knowledge()` and `session.evidence`.

`BaseFrame` exposes a convenience property:

```python
result.evidence_summary
```

The public result protocol does not gain `.evidence()`.

The two DTOs are nested evidence metadata, not new terminal result families.
They stay out of `marivo.analysis.__all__`, do not implement `.show()` or a
bounded result repr, and follow the existing `QualitySummary` precedent. The
typed aliases and their accepted literal set are pinned by a focused test so a
future `FactKind` or `OpenItemKind` addition cannot drift silently.

## Snapshot and persistence semantics

The summary represents the evidence state at the successful return boundary of
the intent. Reopening the frame with `session.get_frame(ref)` restores this same
snapshot even if later session steps supersede an assessment. Current
cross-step knowledge remains the responsibility of `session.knowledge()`.

The summary is persisted in the existing frame `meta.json`; no new database
table or sidecar is introduced. `evidence_summary` is added to
`_SESSION_LOCAL_META_FIELDS` so display wording and evidence-store availability
do not alter frame content identity.

An absent summary has three supported meanings in new data:

- the path deliberately did not emit evidence, such as a frame-local transform
  using `emit_evidence=False`;
- evidence was unavailable before a summary could be generated;
- canonical evidence committed but the summary projection failed, reported as
  complete evidence plus an `evidence_summary_unavailable` warning.

Historical metadata without the field also loads as `None`. It is displayed
without an inline evidence section and is not migrated.

## Generation flow

`commit_result()` remains the only integration boundary. The existing
`knowledge.py` projection stays the single place that interprets persisted
finding, proposition, and assessment payloads into typed observations, facts,
and open items. A focused internal module,
`marivo/analysis/evidence/summary.py`, only ranks and renders those typed
projections into bounded display items.

`knowledge.py` gains an artifact-scoped projection helper. It reuses the same
per-kind constructors and payload precedence rules as `SessionKnowledge`, but
filters observations, proposition joins, and blocking issues by `artifact_id`.
It does not build a full session snapshot and then guess ownership by subject.

The flow is:

1. Extract findings with the existing extractor.
2. Insert findings in the main evidence transaction.
3. Seed propositions and compute assessments inside the existing phase-2
   savepoint.
4. Close the transaction, leaving the artifact row, findings, and any successful
   propositions and assessments canonical in `judgment.db`.
5. Read the artifact-scoped typed projection through the shared `knowledge.py`
   helper.
6. Build the bounded summary from those typed observations, facts, and open
   items.
7. Copy the summary into frame meta and persist the existing `meta.json`.
8. Return the unchanged typed frame/result.

The additional database read happens once at commit time, after the write
transaction closes. `artifact.show()` remains a pure meta render and never
opens `judgment.db`.

If phase 2 rolls back, the artifact-scoped projection sees the retained phase-1
findings but no rolled-back propositions or assessments. Observation digests
remain available because they are ground truth. Other finding families do not
become assessed facts, so the summary reports the raw finding count and may have
zero high-level items rather than inventing an unassessed fact.

If summary construction itself fails, the successful analysis result and
canonical evidence remain available. The canonical artifact row and frame meta
retain `evidence_status="complete"`; frame meta receives an
`evidence_summary_unavailable` warning and renders
`evidence=complete summary=unavailable` plus the recovery route through
`session.evidence`. No second transaction updates the artifact row because
canonical completeness did not change. A summary failure must not fabricate a
fallback statement.

This shared projection is a hard consistency boundary: statement generation
may format typed values, but it must not re-read raw payload keys or introduce
different defaults. A regression test compares the artifact summary's typed
values with the corresponding artifact-scoped `SessionKnowledge` projection.

## Projection and ordering rules

The raw `finding_count` is the number of findings persisted for the artifact.
It is independent of the number of projected items. For example, hundreds of
time-series bucket findings plus one digest may render as one observation item.

The builder produces at most five items. `omitted_count` counts additional
high-level items, not `finding_count - len(items)`.

Projection and deterministic ordering are intent-specific:

- `observe` and `derive_metric_frame`: project observation digests only; do not
  render individual metric-value bucket findings. Order multi-metric digests by
  canonical subject key.
- `compare`: order by absolute change magnitude descending, then canonical item
  key.
- `attribute`: order by absolute contribution value descending, then canonical
  item key.
- `discover.point_anomalies`: preserve deterministic candidate score order.
- `correlate` and `hypothesis_test`: project the single assessed result and use
  canonical item key as the stable fallback.
- `forecast`: order by horizon index ascending, then canonical item key.
- Extractor families that intentionally produce no findings still receive an
  empty summary when `emit_evidence=True` so empty is distinguishable from
  unavailable or suppressed.

These are presentation rules derived from evidence payloads. They do not claim
which valid next action the agent should take.

## Rendering

Evidence rendering stays on the frame family, not in the generic
`RenderableResult.render()` path. `BaseFrame` gains two protected helpers: one
returns the evidence status token, and one appends blocking/warning and summary
sections to a supplied `Card`. `BaseFrame._card()` calls both helpers before its
preview table.

Two in-scope families construct a `Card` directly and do not call
`super()._card()`: `AssociationResult` and `QualityReport`. Their `_card()`
methods must explicitly merge the shared evidence status token into their
family-specific status text and append the shared evidence sections before
their own preview tables. `MetricFrame` and `DeltaFrame` already delegate to
`BaseFrame._card()` and require no second injection; frame families without an
override inherit the base path directly. This base-path-plus-two-overrides
change is smaller and safer than teaching every `RenderableResult` about
analysis evidence.

Earlier Card sections are preserved first by the shared byte-bounded renderer,
so placing evidence before preview prevents a wide or long table from consuming
the output budget before the evidence is visible.

Example:

```text
MetricFrame ref=... rows=100
status: evidence=complete quality=compatible
analysis_purpose: inspect revenue trend
evidence: findings=100 items=1 omitted=0
evidence items:
- revenue: 100 buckets, 102.4 -> 118.7, direction=increase
preview:
...
available:
- .show()
- .contract()
- .to_pandas()
```

The summary itself is capped at five items. The existing Card byte budget is a
second independent bound. Passing `max_output_bytes=None` removes the Card byte
limit but does not expand the persisted five-item summary; full evidence belongs
to the session audit surface.

Status rendering follows these rules:

- summary present: show `evidence_status` and the evidence section;
- `partial` or `unavailable`: show the status and blocking/warning issue even if
  no summary exists;
- `evidence_summary_unavailable`: keep `evidence=complete`, add
  `summary=unavailable`, and render the warning and audit recovery route;
- summary absent with `evidence_status="complete"`: do not show an evidence
  status or section.

The last rule prevents `emit_evidence=False` transform results and historical
frames without summaries from advertising a misleading `evidence=complete`.
This intentionally changes the visible `MetricFrame` and `DeltaFrame` transform
output; no return type, persisted artifact family, or transform contract changes.
Existing render snapshots that expected the old status token must be updated.

An empty emitted summary renders `no evidence findings emitted`. An unavailable
store renders its blocking issue and never renders a fabricated zero count.

The current `_render_status()` behavior hides `unavailable`, and the base card
does not render `blocking_issues`. Both change as part of this design: evidence
status becomes conditional on the rules above, and warning/blocking issues are
rendered through the shared evidence-section helper. Association and quality
render tests pin their independent status composition so those overrides cannot
silently drop the evidence again.

## Error handling

- Evidence store unavailable: retain the current blocking issue, set no
  summary, and make the unavailable state visible in `show()`.
- Evidence phase 2 partial failure: retain committed findings, clear rolled-back
  propositions and assessments from the artifact-scoped projection, and keep
  the existing canonical partial warning. Observation digests may render;
  unassessed fact families do not become summary items.
- Summary construction failure: keep the result and canonical evidence
  complete, add the frame-local `evidence_summary_unavailable` warning, render
  `summary=unavailable`, and point to `session.evidence` for recovery. Do not
  update the artifact row or session completeness.
- Empty extractor result with `emit_evidence=True`: persist an empty summary and
  render `no evidence findings emitted`.
- `emit_evidence=False`: persist no summary and render no evidence section.
- Historical frame without the optional field: load successfully and render no
  evidence section.

## Documentation and agent guidance

The workflow remains one deliberate result observation:

```python
artifact.show()
contract = artifact.contract()
```

Help, the packaged `marivo-analysis` skill, and the latest English and Chinese
analysis/evidence documentation must state:

- `artifact.show()` includes the bounded commit-time evidence summary when the
  intent emitted evidence;
- `session.knowledge()` is for cross-step synthesis and recovery;
- `session.evidence` is for full typed records and audit traces;
- agents do not need a second evidence `show()` after every intent.

No versioned historical documentation is updated.

## Tests

### Summary builder

- Observation bucket findings collapse to a bounded digest item.
- Change, driver, anomaly, and forecast ordering is deterministic.
- At most five items are retained and `omitted_count` is exact.
- Statements, assessment status, and confidence map from committed evidence.
- Raw finding count remains independent of projected item count.
- `ArtifactEvidenceItemKind` accepts exactly `"observation"`, `FactKind`, and
  `OpenItemKind`, so vocabulary additions cannot drift silently.

### Pipeline and persistence

- Complete evidence populates frame meta and `meta.json`.
- Phase-2 rollback does not leak uncommitted assessment status or confidence.
- Artifact-scoped projection and `SessionKnowledge` use the same typed values,
  payload precedence, and defaults for the same artifact.
- Summary construction failure leaves the result and canonical evidence
  complete while adding the frame-local summary warning.
- Unavailable and `emit_evidence=False` paths do not fabricate summaries.
- A persisted and recovered frame renders the original commit-time snapshot.
- Evidence summary changes do not alter frame content hashes.

### Rendering

- One `artifact.show()` includes both evidence and result preview.
- Evidence appears before preview.
- A small byte budget preserves evidence before table rows.
- Empty, partial, and unavailable states have distinct text.
- `AssociationResult` and `QualityReport` retain their family-specific status
  while rendering the shared evidence status and section.
- Transforms and historical frames do not display a misleading
  `evidence=complete`.
- `available` does not advertise `.evidence()`.

### Contract and documentation drift

- The result public-surface snapshot remains unchanged apart from the explicit
  `evidence_summary` property.
- Help, skill guidance, and latest English/Chinese docs teach the single-show
  workflow and preserve the session layering.

## Verification

Focused verification starts with:

```bash
make test TESTS='tests/test_analysis_evidence_summary.py tests/test_analysis_evidence_pipeline.py tests/test_analysis_frames_base.py'
```

`tests/test_analysis_evidence_summary.py` is a new focused test module created
by the implementation; it is intentionally named here before it exists.

Repository and active-site gates are:

```bash
make test
make typecheck
make lint
cd site && npm run verify:content
```

## Success criteria

The change is complete when every evidence-emitting intent result lets an agent
call `artifact.show()` once and see enough bounded, truthful commit-time
evidence to interpret that step, while cross-step synthesis and full audit
remain session responsibilities.
