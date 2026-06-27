---
name: marivo-analysis
description: Use for any Marivo metric-centered analysis task: observe, compare, attribute, discover, correlate, test, forecast, quality assessment, evidence-aware investigation, or continuing an analysis session over semantic metrics.
---

# marivo-analysis

Use this skill when writing or running metric-centered workflows with
`marivo.analysis` (imported as `mv`).

The `mv` top level is the core agent surface: constructor refs and policies,
sessions, frames, frame metadata, lineage, and namespace entrypoints. Use
submodules for domain DTOs and errors: `mv.evidence.*`, `md.*`,
and `mv.errors.*`.

Use `marivo-semantic` instead when the task is authoring semantic models.

## Python Environment

Marivo is a pip-installed Python library in the current project environment;
the current workspace is not expected to contain the Marivo package source. Do
not rely on repo fixtures, `make`, or a fixed `.venv` path.

Do not use bare `python`, `python3`, `pip`, or `pip3` commands. First identify
the project virtualenv path, then use `<venv>/bin/python` and `<venv>/bin/pip`
consistently for every install, check, and script run. If the project has no
virtualenv yet, create or activate one before using this skill.

## How to start

1. For a real project, start from
   `references/examples/00_real_project_template.py`; it shows
   `ms.load()` -> `mv.session.get_or_create(...)`
   with `default_calendar`.
2. For a specific intent pattern, adapt the closest runnable
   `references/examples/NN_*.py`; those examples use a tiny fixture so they
   can run in CI.
3. Confirm metric ids: `import marivo.semantic as ms; catalog = ms.load(); catalog.list(kind="metric").show()`.
4. Use runtime help as the authoritative per-object contract. For the intent,
   frame, policy, or topic you are about to use, inspect
   `mv.help('<name>')`; examples:
   `mv.help('observe')`, `mv.help('discover')`,
   `mv.help('alignment')`, and
   `mv.help('MetricFrame')`. The descriptor exposes `signature`,
   `doc`, bounded `constraints`, runnable `examples`, `methods`,
   `affordances`, and drill-down ids. Consult it per object when the contract
   matters; do not turn help into a blanket ritual for each call.
5. On errors, read the structured output — it includes a fix snippet and the
   available ids when applicable.

## 30-second overview

Default agent-facing operators:

- `session.observe(...)` -> `MetricFrame`
- `session.compare(current_frame, baseline_frame, ...)` -> `DeltaFrame`
- `session.attribute(delta_frame, axes=[...], mode="flat")` -> `AttributionFrame`
- `session.discover.<objective>(...)` -> `CandidateSet`
- `session.correlate(a_frame, b_frame, ...)` -> `AssociationResult`
- `session.hypothesis_test(a_frame, b_frame, ...)` -> `HypothesisTestResult`
- `session.forecast(history_frame, ...)` -> `ForecastFrame`
- `session.derive_metric_frame(...)` -> `MetricFrame`
- `session.assess_quality(artifact)` -> `QualityReport`

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="investigation")
axis = session.catalog.get("model.entity.time_dimension")

session.observe(session.catalog.get("model.metric"), timescope={"start": "...", "end": "..."})  # -> MetricFrame  (end is exclusive: [start, end))
session.compare(cur, base, alignment=mv.window_bucket())      # -> DeltaFrame
session.attribute(delta, axes=[axis], mode="flat")            # -> AttributionFrame
session.discover.point_anomalies(series, threshold=1.0)                               # -> CandidateSet
session.correlate(a, b, alignment=mv.window_bucket())         # -> AssociationResult
session.hypothesis_test(cur, base)                                                    # -> HypothesisTestResult
session.forecast(series, horizon=7)                                                   # -> ForecastFrame
session.assess_quality(series)                                                        # -> QualityReport

mv.session.current()     # safe probe — returns Session or None; check and continue work
mv.help("discover")      # bounded typed objective helpers and compatibility dispatcher
frame.show()             # bounded result card; repr hints to .show()
```

Every intent returns a typed, immutable frame. Stay in frame world until you
call `frame.to_pandas()`. Use `frame.show()` for bounded inspection.

Read artifacts in this order: `repr(artifact)`, `artifact.summary()`,
`artifact.schema()`, `artifact.contract()`, and only then `artifact.preview(...)`
or `artifact.to_pandas()` for tabular data. Use
`artifact.contract().affordances` for mechanical compatibility. The library does
not rank, recommend, or choose next analysis steps.

`mv.window_bucket()` compares time-series and panel windows
by ordinal bucket position by default. Use
`mv.window_bucket(mode="calendar_bucket")` only when the
same absolute bucket key should be treated as the same row. Use
`strict_lengths=True` only when unequal window bucket counts must fail.

## Where filter

`observe(where=...)` accepts Python-style structured predicates:
`==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `between`. Scalar shorthand uses `==`.
SQL-style ops (`eq`, `ne`, `gte`, …) are **not** supported.
`transform.slice(where=...)` uses shorthand forms only (scalar for `==`, list for
`in`, tuple for `between`). For full value-shape details see `references/cheatsheet.md`.

## When to call show()

Call `show()` at deliberate observation points — not after every API call.
Multi-step scripts are quiet until you explicitly inspect:

```python
session = mv.session.get_or_create(name="revenue_drop")
cur = session.observe(
    session.catalog.get("sales.revenue"),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
)
base = session.observe(
    session.catalog.get("sales.revenue"),
    timescope={"start": "2026-06-11", "end": "2026-06-18"},
)
delta = session.compare(cur, base)
delta.show()            # deliberate inspection point
```

Bounded `show()` output is a working observation, not the final user answer.
Final reports must still be answer-first and source-backed.

## Derived ratio and weighted-average components

Derived ratio and weighted-average observations keep parent frames clean:
`frame.to_pandas()` shows only axis columns plus the final metric value. Use
`frame.components()` when you need the underlying component metric values
(e.g., `failed_count`, `total_count` for a ratio).

```python
rate = session.observe(session.catalog.get("sales.failure_rate"))
components = rate.components()
components.show()
```

When two compatible component-aware metric frames are compared, the returned
DeltaFrame also supports `delta.components()`. For segmented, time-series, or
panel ratio/weighted-average deltas, `session.attribute(delta, axes=[...])` emits
value and mix effects with method `ratio_mix` or `weighted_mix`. Time-series
deltas attribute by `bucket_start`; panel deltas attribute by the requested
dimension within each bucket. Ordinal window-bucket deltas include
`bucket_start_b` for the baseline bucket paired to each current bucket.

## Evidence surfaces

Every result exposes evidence fields on `frame.meta`:

```python
result.meta.artifact_id
result.meta.evidence_status         # "complete" | "partial" | "unavailable"
result.meta.blocking_issues
result.meta.confidence_scope
result.meta.quality_summary         # lightweight summary, not assess_quality output
```

There is no `result.evidence.*` wrapper. Read `result.meta` after each step to
decide whether to continue, remediate quality, or inspect session knowledge.

Use session knowledge when you need cross-step reasoning or recovery:

```python
session = mv.session.get_or_create(name="investigation")
knowledge = session.knowledge()
knowledge.facts(kind="change")
knowledge.facts(kind="driver")
knowledge.open_items(kind="anomaly")
knowledge.next_steps(top=5)
knowledge.blocked_followups()
```

Use Surface 3 audit calls only when you need raw evidence objects, all under
the evidence namespace: `session.evidence.findings(...)`,
`session.evidence.propositions(...)`, `session.evidence.assessments(...)`, and
`session.evidence.trace(...)`.

`result.meta.quality_summary` is a lightweight summary attached automatically.
`session.assess_quality(result)` is an explicit auditable operator that creates a
`QualityReport` and participates in lineage.

## Final analysis report

For any non-trivial close-out, read `references/final-report.md` before the
final user response. Do not end with only `frame.show()`, `frame.head(n)`, or
raw tables. Synthesize the answer, scope, evidence, caveats, source details, and
recommended next steps into a clear Markdown report.

## Decision tree

```text
Value of a metric in one window?           -> observe
Current vs baseline change?                -> observe x2 -> compare
Why the change happened?                   -> compare -> attribute
Spikes, drops, unusual buckets?            -> observe series -> discover.<objective>
Two metrics move together?                 -> observe both -> correlate
Need auditable quality evidence?           -> assess_quality
Reshape without changing frame family?     -> transform.<op> (topk, rollup, slice, ...)
Custom Ibis calculation that must re-enter -> derive_metric_frame
Raw pandas from a frame?                   -> frame.to_pandas()
```

Prefer built-in intents first. When Marivo does not directly support an
analysis step but the result must re-enter the typed metric flow, use
`session.derive_metric_frame(...)` — the governed escape hatch for custom Ibis
queries whose output is validated and persisted as a `MetricFrame`. Use
`frame.to_pandas()` for terminal pandas analysis that does not need to feed
typed intents.

## Session

Default to one session per analysis task. Start the first script with
`mv.session.get_or_create(name="<stable_task_name>")`, then reuse the same
stable name or probe `mv.session.current()` in every follow-up script. Do not
create new sessions for script splits, retries, or branch exploration: artifacts,
knowledge, facts, followups, and job history are session-scoped.

Create a new session only when the user explicitly starts an independent
investigation, or when the existing session is polluted enough that restarting
is the correct recovery. State that reason in the final output. When a session
is beyond recovery, delete it with `mv.session.delete(name)`.

```python
mv.session.get_or_create(name="my_analysis")  # idempotent entry point; auto-loads project datasources
mv.session.current()                          # None-safe probe; returns Session, not SessionSummary
mv.session.list()                             # list sessions
mv.session.delete(name="my_analysis")         # destructive cleanup; removes session and all data
session.recent_jobs(limit=5)                   # recent job history
```

Do not construct `backend_factory` for normal project analysis. Use it only as
an explicit test/CI or runtime override; its signature is
`backend_factory(datasource_name: str) -> ibis backend`.

## Minimal templates

### Observe + compare + attribute

```python
import marivo.analysis as mv

cur = session.observe(session.catalog.get("<metric_id>"), timescope={"start": "2026-07-01", "end": "2026-10-01"}, grain="month")
base = session.observe(session.catalog.get("<metric_id>"), timescope={"start": "2025-07-01", "end": "2025-10-01"}, grain="month")
delta = session.compare(cur, base, alignment=mv.window_bucket())
time_axis = session.catalog.get("<metric_time_dimension_id>")
drivers = session.attribute(delta, axes=[time_axis])
drivers.show()
```

### Discover + select

```python
series = session.observe(session.catalog.get("<metric_id>"), timescope={"start": "2026-07-01", "end": "2026-10-01"}, grain="day")
candidates = session.discover.point_anomalies(series, threshold=1.0)
window = candidates.select(rank=1, attribute="window")
```

### Correlate

```python
a = session.observe(session.catalog.get("<metric_a>"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
b = session.observe(session.catalog.get("<metric_b>"), timescope={"start": "2026-07-01", "end": "2026-09-30"})
result = session.correlate(a, b, alignment=mv.window_bucket())
result.show()
```

### Governed derive

```python
custom = session.derive_metric_frame(
    metric=session.catalog.get("sales.revenue"),
    query=mv.ibis_query(
        datasource="warehouse",
        build=lambda db, ctx: db.table("orders"),
    ),
    columns=mv.metric_columns(
        value="value",
        time=mv.time_column(
            column="order_date",
            ref=session.catalog.get("sales.orders.order_date"),
        ),
        dimensions=[
            mv.dimension_column(
                column="region",
                ref=session.catalog.get("sales.orders.region"),
            ),
        ],
    ),
    timescope={"start": "2026-06-18", "end": "2026-06-25"},
    grain="day",
    label="custom_revenue_by_region",
)
custom.show()
```

## Cross-dataset observe

For cross-dataset base metrics, use the normal `session.observe(...)` surface.
Do not pass join policy or route arguments.

Derived metrics (ratio, weighted-average) plan each component independently and
enforce comparability across components. When planning fails, the raised error
is the contract: read its structured fields (`schema_version`, `code`,
`candidates`, `repair`) and apply the `repair` instruction. The `code`
identifies which component and which check failed; do not rely on a transcribed
list here — the error text is authoritative and current.

## Standard workflow

1. `<venv>/bin/python -c 'import marivo.analysis as mv; mv.help()'` — verify install.
2. Confirm metric ids from the semantic layer.
3. Start or attach the task session with
   `mv.session.get_or_create(name="<stable_task_name>")`.
4. Adapt the nearest `references/examples/NN_*.py` file.
5. Run every follow-up script with the same session; on errors, read the
   structured output and apply the fix.
6. Use `frame.show()` for bounded inspection; `frame.head(n)` / `frame.to_pandas()` when you need full data.

## When to split scripts

Bundle a chain into one script when the path is fixed. Stop and run a new
script when the next intent depends on values you have not seen yet. A split is
only a script boundary; it is not a session boundary. Reuse the same session so
artifacts, knowledge, facts, followups, and job history remain available.

- **Bundle** (one script, end with `frame.show()`):
  observe → compare → attribute with a pre-chosen axis; observe → forecast;
  observe → assess_quality. The shape and the next call are decided before
  you run.
- **Split** (run, read, then write the next script):
  - `discover` → which candidate to `select` and drill into.
  - `correlate` → which of several associations is worth follow-up.
  - `attribute` → which segment from the ranking to observe at finer grain.
  - Any branch where `frame.show()` or `artifact.contract().affordances` is the input to your
    decision.

Rule of thumb: if you cannot write the next `mv.*` call without first reading
the `show()` output, that is a split point. Do not pre-write speculative
downstream steps "in case" — they waste compute and obscure the judgment. After
the split, continue with the original task session instead of starting a fresh
one.

In the follow-up script, recover previously produced frames from disk instead
of re-running observe:

```python
session = mv.session.get_or_create(name="my_analysis")
# Discover available frames by metric_id:
summaries = session.frame_summaries()
# Load a frame by ref — zero datasource queries:
prev = session.get_frame("<ref>")
```

## Walkthrough

```python
import marivo.analysis as ap

session = ap.session.get_or_create(name="sales_weekly_revenue")
region = session.catalog.get("sales.orders.region")

current = session.observe(
    metric=session.catalog.get("sales.revenue"),
    timescope={"start": "2026-05-01", "end": "2026-05-08"}, grain="day",
    dimensions=[region],
)
baseline = session.observe(
    metric=session.catalog.get("sales.revenue"),
    timescope={"start": "2026-04-24", "end": "2026-05-01"}, grain="day",
    dimensions=[region],
)
delta = session.compare(current, baseline, alignment=ap.window_bucket())
delta.show()

for issue in delta.meta.blocking_issues:
    print(issue.kind, issue.message)

delta.meta.quality_summary   # lightweight quality snapshot
```

## Further reading

- `references/examples/*.py` — runnable templates (primary reference)
- `references/final-report.md` — final user-facing report structure and QA
- `references/cheatsheet.md` — intent/frame/discover/transform matrices
- `references/pitfalls.md` — error recovery and common mistakes
- `references/backend-setup.md` — datasource and backend wiring
- `../marivo-semantic/references/datasource.md` — datasource definition

## On error

Errors are structured and teach the fix at raise time: read `schema_version`,
`code`/`kind`, the available ids (`candidates`), and the `repair`/fix snippet,
then apply it. For worked recovery patterns see `references/pitfalls.md`; for the
runnable shape of any intent see the matching `references/examples/NN_*.py`.
