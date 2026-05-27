---
name: marivo-py-analysis
description: Use when the task involves Marivo analysis: observe, compare, decompose, discover, correlate, test, forecast, quality assessment, or evidence-aware investigation over a Marivo semantic model.
---

# marivo-py-analysis

Use this skill when writing or running Python code against
`marivo.analysis_py`. Import it as `mv`:

```python
import marivo.analysis_py as mv
```

Assume the active Python environment already has `marivo` installed. Do not use
this skill for stdio MCP workflows, HTTP transports, or semantic model authoring.

## Start

Create or attach a project-local analysis session, then stay in session methods:

```python
import marivo.analysis_py as mv

mv.session.get_or_create(name="analysis")  # backend resolved from .marivo/datasource
current = mv.observe(
    mv.MetricRef("sales.revenue"),
    window={"expr": "this_week"},
    grain="day",
)
print(current.summary())
```

Attach a live backend explicitly in tests / CI or when you need full control
over the backend. Pair with `use_datasources=False` so a stray project
datasource cannot mask a misconfigured fixture.

```python
import ibis
import marivo.analysis_py as mv

def make_backend(datasource_name: str):
    if datasource_name != "warehouse":
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="<trino_host>", port=80, user="<user>",
        database="<catalog>", source="<source>",
        client_tags=["standby", "routing_group=bsk_wide"],
    )

mv.session.get_or_create(
    name="analysis",
    backend_factory=make_backend,
    use_datasources=False,
)
```

Use typed refs when an argument expects a public reference:
`mv.MetricRef`, `mv.DimensionRef`, `mv.CalendarRef`, `mv.AlignmentPolicy`, and
`mv.LagPolicy`. Some convenience parameters, such as `segment_by="country"`,
accept strings and normalize them internally.

## Operators

- `session.observe(...)`: materialize a metric window as a `MetricFrame`.
- `session.compare(current, baseline, ...)`: compute delta and percent change as a `DeltaFrame`.
- `session.decompose(delta, axis=...)`: attribute a change to dimension values as an `AttributionFrame`.
- `session.discover(frame, objective=...)`: find anomaly, slice, window, or driver candidates as a `CandidateSet`.
- `session.correlate(left, right, ...)`: compute association and lag evidence as an `AssociationResult`.
- `session.test(current, baseline, ...)`: run a statistical hypothesis test as a `HypothesisTestResult`.
- `session.forecast(history, horizon=...)`: produce forecast points or intervals as a `ForecastFrame`.
- `session.evaluate_forecast(forecast, actuals, ...)`: compare forecast output with actual observations.
- `session.assess_quality(frame)`: create a formal `QualityReport` artifact.

Every operator returns an immutable typed result. Prefer `result.summary()` and
`result.preview(limit=...)` before materializing data with `result.to_pandas()`.

## Surface 1: Result Evidence

Every result exposes these flat fields directly:

```python
result.artifact_id
result.subject
result.evidence_status         # "complete" | "partial" | "unavailable"
result.blocking_issues
result.recommended_followups   # C1 + C2 only
result.confidence_scope
result.quality                 # lightweight summary, not assess_quality output
```

There is no `result.evidence.*` wrapper. Read these fields after each step to
decide whether to continue, remediate quality, or inspect session knowledge.

`recommended_followups` is intentionally narrow:

- C1 `dag_continuation`: deterministic next operators from the allowed analysis DAG.
- C2 `quality_remediation`: deterministic repair actions for blocking issues.

Strategic, business, heuristic, and semantic-axis suggestions are agent-owned.
Do not expect `recommended_followups` to suggest every useful next question.

## Surface 2: Session Knowledge

Use session knowledge when you need cross-step reasoning or recovery:

```python
knowledge = session.knowledge()
knowledge.facts(kind="change")
knowledge.facts(kind="driver")
knowledge.facts(kind="tested_hypothesis")
knowledge.facts(kind="forecast")
knowledge.facts(kind="association")
knowledge.open_items(kind="anomaly")
knowledge.open_items(kind="question")
knowledge.next_steps(top=5)
knowledge.blocked_followups()
knowledge.for_subject(subject)
```

The default agent entry points are `knowledge.facts(...)`,
`knowledge.open_items(...)`, `knowledge.next_steps(...)`,
`knowledge.blocked_followups()`, and `knowledge.for_subject(...)`. Full
typed-field schemas live in `references/typed-facts.md`.

## Surface 3: Audit

Use Surface 3 only when you need to inspect raw evidence objects:

```python
session.findings(artifact=result.artifact_id)
session.findings(finding_type="delta")
session.propositions(type="change", status="validated")
session.assessments(latest_only=True)
session.evidence.proposition("prop_...")
session.evidence.latest_assessment("prop_...")
session.evidence.trace("prop_...")
```

Surface 3 returns `Finding`, `Proposition`, `Assessment`, and `EvidenceTrace`
objects. It is for audit and explanation, not the default path for deciding the
next operator.

## Quality Boundary

`result.quality` is a lightweight summary attached automatically at commit time.
It is useful for quick checks such as coverage, null rate, sample size, and
compatibility hints. It does not create a new step or artifact.

`session.assess_quality(result)` is an explicit operator. It creates a
canonical `QualityReport` artifact, enters the step DAG, and can participate in
the evidence chain.

Use `result.quality` by default. Call `session.assess_quality(...)` when quality
must be auditable, lineage-bearing evidence.

## Followups

Run runtime-generated followups through the session so lineage records the
trigger:

```python
for action in session.knowledge().next_steps(top=3):
    result = session.run_followup(action)
```

An agent may call the corresponding operator directly instead, but that bypasses
`triggered_by_followup` lineage. The same action may remain visible in
`knowledge.next_steps()` because the runtime cannot tell it was satisfied.

## Decision Tree

Need a metric value or time series?
Use `session.observe(...)`.

Need current versus baseline change?
Observe both windows, then `session.compare(current, baseline)`.

Need why a change happened?
Compare first, then call `session.decompose(delta, axis=...)`.

Need unusual points, slices, windows, or driver axes?
Use `session.discover(...)` with the objective that matches the question.

Need whether two metrics move together?
Observe both, then `session.correlate(left, right, ...)`.

Need a formal statistical answer?
Use `session.test(...)`.

Need future values?
Use `session.forecast(...)`, then `session.evaluate_forecast(...)` when actuals
are available.

## Common Pitfalls

- Do not mutate frames. Use `result.to_pandas()` for a defensive copy.
- Do not pass a `DeltaFrame` back into `compare`; compare requires two metric frames.
- Do not expect `recommended_followups` to choose decomposition axes or pair metrics for correlation.
- Do not treat `result.quality` as a replacement for `session.assess_quality(...)` when auditability matters.
- Do not write to or depend on global session state; analysis state is project-local.

## Walkthrough

```python
import marivo.analysis_py as ap

session = ap.session()

# Surface 1: every step returns a result with evidence fields populated
current = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    time="this_week",
    grain="day",
    segment_by="country",
)
baseline = session.observe(
    metric=ap.MetricRef("sales.revenue"),
    time="previous_week",
    grain="day",
    segment_by="country",
)
delta = session.compare(current, baseline)

if delta.blocking_issues:
    for issue in delta.blocking_issues:
        print(issue.kind, issue.message)

for followup in delta.recommended_followups:
    if followup.category == "quality_remediation":
        print(f"remediation for {followup.source_issue_id}: {followup.operator}")
    else:
        print(f"valid next operator: {followup.operator}")

# Surface 2: cross-step reasoning
knowledge = session.knowledge()
if knowledge.evidence_completeness == "unavailable":
    raise RuntimeError("judgment store unavailable")

for change in knowledge.facts(kind="change"):
    print(change.subject, change.direction, change.magnitude, change.status)

# Auto-execute a recommended next step
for action in knowledge.next_steps(top=3):
    result = session.run_followup(action)
```

## Further Reading

- `references/examples/*.py`: executable SDK examples for installed-library usage.
- `references/typed-facts.md`: Surface 2 typed fact and open item fields.
- `references/judgment-db-schema.md`: local SQLite evidence store schema.
- `references/backend-setup.md`: project datasource and explicit backend setup.
- `references/pitfalls.md`: expanded error recovery notes.
