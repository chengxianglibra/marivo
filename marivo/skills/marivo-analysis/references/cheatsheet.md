# marivo-analysis cheatsheet

Use this as a compact routing aid after loading the skill. It is not the static
API contract. For exact signatures, parameter constraints, return types,
errors, and runnable examples, use `mv.help()` and the specific
`mv.help("<topic>")` entry before authoring a call. For live affordances and
mechanically valid next actions from a concrete artifact, use
`artifact.contract()`. Assume `marivo` is imported from the active Python
environment as an installed package.

## Runtime Help

Use the uniform runtime help contract for exact callable, frame, policy, and
topic details. Use `artifact.contract()` on concrete artifacts for live
affordances and mechanically valid next actions:

```python
mv.help('agent_surface')                 # default operator surface and artifact protocol
mv.help('discover')                      # objective compatibility and required kwargs
mv.help('alignment')                     # AlignmentPolicy variants
mv.help('MetricFrame')                   # methods and artifact protocol
mv.help('MetricFrame.components')        # method signature and doc
```

## Intents

| Intent | Reach for it when | Routing reminder |
| --- | --- | --- |
| `session.observe` | You need a metric-backed frame from catalog evidence. | Read `mv.help("observe")` before authoring filters, windows, grain, or time-dimension arguments. |
| `session.compare` | You have comparable observed metric frames and need a delta artifact. | Read `mv.help("compare")` and `mv.help("alignment")`; keep comparing frames, not already-derived deltas. |
| `session.attribute` | You have a delta artifact and need driver attribution by catalog axes. | Use catalog objects at public boundaries; inspect the artifact contract before chaining. |
| `session.discover.<objective>` | You need candidate windows, slices, axes, or anomalies from a frame. | Read `mv.help("discover")` for objective compatibility and required kwargs; inspect `CandidateSet.show()` and `CandidateSet.contract()` before selecting. |
| `candidates.select(...)` | You need to carry one candidate value into the next step. | Use the selector contract from `mv.help("discover")` and the concrete candidate artifact. |
| `session.correlate` | You need association evidence between metric frames. | Read `mv.help("correlate")` and use public alignment helpers. |
| `session.hypothesis_test(a, b)` | You need a typed statistical comparison between frames. | Read `mv.help("hypothesis_test")`; report assumptions and quality evidence. |
| `session.forecast(history, horizon=7)` | You need a bounded projection from a time-aware history frame. | Read `mv.help("forecast")`; treat the output as scenario evidence, not certainty. |
| `session.derive_metric_frame` | Custom Ibis work must re-enter the typed metric flow. | Read the governed derive guidance below, then confirm exact parameters with runtime help and errors. |
| `session.assess_quality(frame)` | You need quality evidence before trusting a frame or result. | Use the quality artifact in final caveats and next-step decisions. |

## Predicate Routing

`where` and transform predicate value shapes are runtime API contracts. Before
authoring them, read `mv.help("observe")` and `mv.help("transform")`. Use
Python-style operators such as `==`, `!=`, `>`, `>=`, `<`, and `<=`; do not
invent SQL-style mnemonic names such as `"eq"`, `"ne"`, or `"gte"`.

## Frame Flow

| Frame | Created by |
| --- | --- |
| `MetricFrame` | `session.observe`, `session.derive_metric_frame` for governed custom Ibis re-entry |
| `DeltaFrame` | `session.compare` |
| `CandidateSet` | `session.discover.<objective>` |
| `AssociationResult` | `session.correlate` |
| `HypothesisTestResult` | `session.hypothesis_test` |
| `ForecastFrame` | `session.forecast` |
| `QualityReport` | `session.assess_quality` |
| `AttributionFrame` | `session.attribute` |

Use `artifact.show()` for bounded inspection. Use `artifact.contract()` before
composing the next operator. Use `artifact.to_pandas()` only for terminal
custom analysis outside the typed artifact flow.

Frames are immutable. Use `frame.show()` for a cheap read and
`frame.to_pandas()` when you need a mutable copy. Use
`frame.to_pandas().head(n)` only when you explicitly want pandas behavior.

Use catalog metric objects from `session.catalog.get("metric.<metric_id>")`, catalog dimension objects from
`session.catalog.get("dimension.<dimension_id>")`, `mv.CalendarRef(...)`, and
`mv.window_bucket()` / calendar alignment helpers at public operator boundaries. Do not pass bare
strings directly to `observe`, `attribute`, `transform`, or calendar-backed
`compare`.

## Minimal Flow Sketch

```python
import marivo.analysis as mv

cur = session.observe(
    session.catalog.get("metric.sales.revenue"),
    timescope={"start": "2026-07-01", "end": "2026-10-01"},
)
base = session.observe(
    session.catalog.get("metric.sales.revenue"),
    timescope={"start": "2025-07-01", "end": "2025-10-01"},
)
delta = session.compare(cur, base, alignment=mv.window_bucket())
created_at = session.catalog.get("time_dimension.sales.orders.created_at")
attribution = session.attribute(delta, axes=[created_at])
attribution.show()
```

For discovery, first produce a frame, then choose an objective using
`mv.help("discover")`. Read `CandidateSet.show()` and
`CandidateSet.contract()` before selecting a candidate or composing another
operator.

## Governed Derive

Use `session.derive_metric_frame(...)` when a custom Ibis calculation must
re-enter the typed metric flow — custom joins, raw table scans, feature
engineering, or bespoke aggregations that Marivo does not model directly. The
output is validated and persisted as a `MetricFrame` with full lineage.

Exact parameters, helper constructors, output-column bindings, and validation
errors are owned by `mv.help("derive_metric_frame")` and the runtime error
messages. Use normal semantic observation for standard metrics; reserve
`session.derive_metric_frame(...)` for custom backend work that must still feed
`compare`, `attribute`, `discover`, `correlate`, `hypothesis_test`,
`forecast`, or `assess_quality`.

For terminal pandas analysis that does not need to feed typed intents, export a
frame with `frame.to_pandas()` and work locally.

## Discover Routing

`session.discover.<objective>` routes from an observed, compared, or derived
artifact into a `CandidateSet`. Objective compatibility, required kwargs,
selector attributes, and candidate row details are owned by
`mv.help("discover")` and the concrete `CandidateSet.contract()`. Use
`CandidateSet.show()` for a bounded read, then let agent judgment decide
whether a displayed candidate is worth carrying forward; candidate order is not
a Marivo recommendation.

## Discovery Helpers

| Need | Call |
| --- | --- |
| Check active session without raising; returns Session or None | `mv.session.current()` |
| Read recent jobs | `session.recent_jobs(limit=5)` |
| Create or attach a session (idempotent) | `mv.session.get_or_create(name=...)` |
| List sessions | `mv.session.list()` |
| Attach live project data | `mv.session.get_or_create(name=...)` |
| Override backend resolution for tests/CI | `mv.session.get_or_create(name=..., backend_factory=..., use_datasources=False)` |
| Inspect SDK entrypoints | `mv.help()` or `mv.help("discover")` |
| Inspect calendar file shape | `mv.help("calendar")` |
| Confirm metric ids | `import marivo.semantic as ms; catalog = ms.load(); catalog.list(kind=ms.SemanticKind.METRIC)` |
| Recover a frame across scripts (no re-query) | `session.get_frame(ref)` |
| List persisted frame refs and metadata | `session.frame_summaries()` |
| Find frame ref by metric_id | `session.frame_summaries()` |

Calendar alignment and timestamp bucketing use the Python process system timezone. If a naive warehouse timestamp physically stores UTC, declare it in the semantic layer with `@ms.time_dimension(..., timezone="UTC")`.

Metric inputs come from exact catalog ids such as `session.catalog.get("metric.model.metric")`. Do not guess
ids from metric display names; call `catalog.list(kind=ms.SemanticKind.METRIC)` after loading the
semantic project.

For cross-dataset base metrics (datasets cover multiple datasets with an explicit
`root_dataset`), call `session.observe(...)` with the normal arguments.
`dimensions=` and `where=` may target joined datasets; root predicates push
before widening, joined predicates apply after. Do not pass join policy or
route arguments. If planning fails, the repair error includes
`schema_version`, `code`, `candidates`, and `repair`.

For derived metrics (ratio, weighted-average), each component is planned
independently and enforce comparability across components. If a derived observe
fails, the raised error is authoritative: read `schema_version`, `code`,
`candidates`, and `repair`, then apply the `repair` instruction. Do not
maintain or rely on a transcribed repair-code catalog here.

## Backend Setup

Analysis intents that execute against live semantic datasets need a session
backend. In a real project, register `models/datasources/*.py` definitions and
use the default session entrypoint:

```python
session = mv.session.get_or_create(name="analysis")
```

Use `backends={...}` for a small static mapping or
`backend_factory(datasource_name)` only when tests/CI or deterministic scripts
must override project datasource lookup.

```python
import os

import ibis
import marivo.analysis as mv

def make_backend(datasource_name: str):
    if datasource_name != "warehouse":
        raise KeyError(datasource_name)
    return ibis.trino.connect(
        host="<trino_host>",
        port=80,
        user=os.environ["TRINO_USER"],
        database="<catalog>",
        source="<source>",
        client_tags=["standby", "routing_group=bsk_wide"],
    )

mv.session.get_or_create(
    name="analysis",
    backend_factory=make_backend,
    use_datasources=False,
)
```

For Trino, map prompt `catalog` to Ibis `database`, and map
`client-tags`/`client_tags` to Python `client_tags` as a list. See
`references/backend-setup.md` for the full mapping and guardrails.

Datasource names are global, not model-qualified. Semantic authoring uses typed datasource refs such as `md.ref("datasource.warehouse")`; analysis backend factories still receive the registered short name `"warehouse"`, never `"sales.warehouse"`.

When a dataset has multiple time dimensions, use the catalog object for the
chosen time dimension and confirm the exact `observe` argument shape with
`mv.help("observe")`. Alignment helpers and calendar file shape are owned by
`mv.help("alignment")` and `mv.help("calendar")`; inspect resulting delta rows
with `.show()` or `.to_pandas()` only when you need row-level evidence.
