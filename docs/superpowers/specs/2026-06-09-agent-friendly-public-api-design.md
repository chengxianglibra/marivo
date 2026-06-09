# Agent-Friendly Public API Result Design

## Purpose

Marivo targets coding agents such as Claude Code and Codex. Its public Python API
should therefore make results easy for agents to inspect without requiring them
to learn many result-specific data shapes. At the same time, Marivo must avoid
uncontrolled stdout because agents often run multi-step scripts and large or
unexpected terminal output pollutes the model context.

The target contract is:

- Result-producing public API calls return typed result objects and do not write
  stdout.
- Help APIs are explicit inspection commands: they print bounded help text and
  return `None`.
- Every public result has a one-line `repr()` discovery hint that points to
  `show()`.
- Every public result has one explicit, bounded inspection method: `show()`.
- `render()` is pure and returns the same bounded text that `show()` prints.
- Structured consumers use result-specific typed helpers such as `summary()`,
  `preview()`, `.items`, `.ids()`, or frame-specific escape hatches such as
  `to_pandas()`.

This is a breaking target-state design. It replaces implicit display behavior
with explicit bounded inspection and does not preserve old stdout defaults,
deprecated argument shapes, or compatibility shims.

## Goals

- Result-producing public APIs should not write to stdout as a side effect.
- Agents should have one obvious way to inspect any public result:
  `result.show()`.
- Help APIs should not return result objects; calling `help(...)` is itself the
  inspection action.
- Inspection output should be bounded by the result object itself, not by agent
  discipline or skill instructions.
- Result-producing public APIs should still return typed objects, not strings or
  generic envelopes.
- Marivo skills and docs should teach the same no-side-effect result contract as
  the runtime API.

## Non-Goals

- Do not introduce an `AgentResult[T]` or other envelope around existing frame
  families.
- Do not support multiple render formats for ordinary result objects.
- Do not add `display=True` or `display=False` to public APIs.
- Do not add `format` or any other output-shape selector to help APIs.
- Do not provide a legacy mode that keeps semantic discovery printing by
  default.
- Do not add global stdout configuration in the first implementation.
- Do not add generic `to_dict()` or `to_json()` methods in the first
  implementation; add structured export later only when a concrete agent
  workflow needs it.
- Do not change persisted frame data formats unless implementation uncovers a
  direct result-object schema requirement.

## Core Contract

Every agent-facing public result type must implement the common inspection
surface:

```python
repr(result)
result.render()
result.show()
```

Tabular results also implement:

```python
result.summary()
result.preview(limit=10)
```

Analysis frames keep frame-specific escape hatches:

```python
frame.to_pandas()
```

Method responsibilities:

- `__repr__()` returns a one-line discovery hint and does not write stdout.
- `render()` returns canonical agent-readable plain text, does not write
  stdout, and does not end with a trailing newline.
- `show()` prints `render()` followed by a trailing newline and returns `None`.
- `summary()` and `preview()` return typed bounded projections and do not write
  stdout.
- Rendered text includes a bounded `available:` section that names safe
  inspection and consumption methods for the current result type.

`summary()` is the structured source for the core facts shown by `render()` and
`show()`. `show()` must include a human-readable projection of every summary
field that affects agent interpretation. If a summary field is only machine
bookkeeping and is omitted from `show()`, that omission must be documented on the
result type. Each result family defines its own concrete summary return type in
the class docstring or help text; `summary()` must not return `Any` or an
untyped raw dict.

JSON is not a render format. Markdown is not a render format. Ordinary result
objects have exactly one rendered representation: bounded plain text for agent
terminal inspection.

`available:` is not a planning API. It does not recommend what the agent should
do next for the user task. It only reminds the agent which bounded methods exist
on the result it is already holding.

## Help APIs

`help(...)` is not a result-producing API. Its meaning is "print help for this
symbol or surface." It should print bounded help text directly and return
`None`:

```python
mv.help()                         # prints top-level Marivo help, returns None
mv.help("observe")                # prints API/topic help, returns None
mv.help("semantic.metric")        # prints authoring topic help, returns None
mv.help(revenue)                  # prints semantic-object help, returns None
mv.help(revenue, project=project) # explicit project fallback, returns None
```

`mv.help(target=None, *, project=None)` is the canonical public help entrypoint
for agents. `ms.help(...)` and `project.help(...)` may remain as thin
convenience aliases for human workflows, but agent-facing docs and skills should
teach `mv.help(...)` as the primary path so agents do not need to choose a help
method based on object type.

For semantic objects, the preferred target is the Python ref object returned by
the semantic authoring API, not a string id:

```python
orders = ms.dataset(...)

@ms.metric(datasets=[orders], decomposition=ms.sum(), name="revenue")
def revenue(table):
    return table.amount.sum()

mv.help(revenue)
mv.help(orders)
```

If the ref can be resolved from the current loaded project or current working
directory, `mv.help(ref)` prints semantic-object help. If it cannot be resolved,
Marivo raises a typed, agent-actionable error that explains how to load the
project or pass `project=project`. Passing `(project, "sales.revenue")` is not
the canonical path because it forces agents back to string lookup even when they
already hold a typed semantic ref.

Semantic-object help is a read-only semantic briefing. It exists to help agents
use an object safely, not to replace `_model.py` as the authoring source and not
to provide structured data access. It should summarize bounded consumption
context such as business meaning, guardrails, dependencies, grain, additivity,
decomposition, default time field, readiness or parity warnings, examples, and
the semantic id or Python ref to use in analysis APIs.

Reading `_model.py` remains the right path when the agent needs to modify the
semantic model, inspect implementation expressions, debug authoring behavior, or
audit help output against source. `mv.help(ref)` is the default consumption
surface when the agent needs to understand how to use an already-defined object
without loading the full model file into context.

Help output should use the same size discipline as result rendering, but help is
not a result object. It should not implement `show()`, `render()`, `summary()`,
or `preview()`.

Help output shares the result rendering budget: target under 80 lines. Long help
content should be summarized with explicit pointers to narrower `help(...)`
targets rather than printing full catalogs.

Help APIs have one output shape: bounded plain text. They do not accept
`format`, `json`, or other shape-selection arguments. If callers need structured
semantic data, they should call a result-producing API rather than ask help for
JSON.

This replaces `describe(...)` as an inspection surface. If callers need
structured semantic data, they should use result-producing APIs such as
discovery results, dependency results, readiness results, or future explicit
data-access APIs. They should not call `help(...)` and expect a structured
payload.

## Cold-Start Repr

No-stdout result-producing public APIs need a built-in discovery path for
first-time agents. If a caller captures or inspects a returned object in a REPL,
notebook, debugger, or agent tool output, `repr(result)` should reveal the next
inspection method without dumping data.

Every public result must implement a bounded one-line `__repr__`:

```text
<DiscoveryResult[MetricSummary] items=12; call .show() to inspect>
<MetricFrame ref=frame_ab12 metric=sales.revenue rows=7; call .show() to inspect>
<ReadinessResult status=ready_with_warnings issues=2; call .show() to inspect>
```

Rules:

- `__repr__` is exactly one line.
- It includes result type, key identity, and scale or status.
- It always hints `call .show() to inspect`.
- It never includes preview rows, full schemas, dict payloads, or pandas output.
- It never performs IO.
- If `_repr_html_()` is defined, it must return either `None` (to trigger the
  `__repr__` fallback in Jupyter) or `"<pre>" + html.escape(repr(result)) + "</pre>"`.
  It must not render full preview tables or rich HTML.

This preserves script safety while making silent results discoverable. `repr()`
is not the progressive disclosure layer; it only points to `show()`.

## Result-Producing API Rule

Agent-facing result-producing public APIs compute and return results only:

```python
result = public_api_call(...)
# stdout is unchanged
result
# <MetricFrame ref=frame_ab12 metric=sales.revenue rows=7; call .show() to inspect>
result.show()
```

The absence of implicit stdout is intentional. If an agent forgets `show()`, the
script is quiet and recoverable; if the returned object is inspected, its
one-line `repr()` points to `show()`. If an API prints by default, one missed
opt-out can flood the agent context with intermediate results that are hard to
recover from.

Skills may teach when to call `show()`, but runtime safety must not depend on
every generated script remembering a quiet flag.

## Public API Families

### Semantic Discovery

APIs:

```python
project.list_models() -> DiscoveryResult[ModelSummary]
project.list_datasources() -> DiscoveryResult[DatasourceSummary]
project.list_datasets() -> DiscoveryResult[DatasetSummary]
project.list_fields() -> DiscoveryResult[FieldSummary]
project.list_time_fields() -> DiscoveryResult[FieldSummary]
project.list_metrics() -> DiscoveryResult[MetricSummary]
project.list_relationships() -> DiscoveryResult[RelationshipSummary]
project.search(query) -> DiscoveryResult[SearchHit]
```

Default behavior:

- Return a typed discovery result.
- Do not print.

Inspection:

```python
metrics = project.list_metrics()
metrics
# <DiscoveryResult[MetricSummary] items=12; call .show() to inspect>
metrics.show()
```

Example `show()` output:

```text
DiscoveryResult[MetricSummary] items=12
columns: id | label | datasource | dataset
preview:
sales.revenue | Revenue | sales_db | orders
sales.orders | Orders | sales_db | orders
... 10 more items; call .preview(limit=...) or iterate .items
available:
- .ids(): list of semantic ids
- .first(): first MetricSummary item
- .require_one(): item when exactly one matches
- .preview(limit=...)
- .render()
```

Programmatic consumption:

```python
metric_ids = metrics.ids()
first_metric = metrics.require_one()
```

`DiscoveryResult[T]` should be list-like for ergonomic iteration, length, and
indexing. It should also expose agent-oriented helpers such as `.items`, `.ids()`
where the item type has semantic ids, `.first()`, and `.require_one()`.

`require_one()` returns the only item when the discovery result has exactly one
item. If there are zero items, it raises a typed selection error with an
agent-actionable message that says no results matched and suggests inspecting
the result with `.show()` or broadening the query/filter. If there is more than
one item, it raises the same typed selection error with the result count and
suggests narrowing the query/filter, using `.first()`, or iterating `.items`.
The zero-result message must include the words "no results". The multiple-result
message must include the actual count.

These helpers are not compatibility shims. They are the progressive access path:
bounded text for inspection, small structured helpers for common agent tasks,
and `.items` when the caller needs the full typed list.

### Semantic Single-Object Results

APIs such as these should use the same result contract:

```python
project.dependencies(name) -> DependencyResult
project.dependents(name) -> DependencyResult
project.richness() -> RichnessReport
project.readiness() -> ReadinessResult
```

Default behavior:

- Return a typed result.
- Do not print.

These results are non-tabular. They implement the core inspection surface only:
`repr()`, `render()`, and `show()`. They do not implement `summary()` or
`preview()`.

For user-facing semantic-object help, use `mv.help(ref)` where `ref` is the
Python semantic ref returned by `_model.py`. Use `mv.help(ref, project=project)`
only when project resolution cannot be inferred safely.

### Analysis Operators

Analysis operators return concrete frame/result families and do not print:

```python
session.observe(...) -> MetricFrame
session.compare(...) -> DeltaFrame
session.decompose(...) -> AttributionFrame
session.correlate(...) -> AssociationResult
session.forecast(...) -> ForecastFrame
session.assess_quality(...) -> QualityReport
session.explore_ibis(...) -> ExplorationResult
session.from_pandas(...) -> ExplorationResult
```

The returned object stays the concrete frame/result so typed frame chains remain
natural:

```python
cur = session.observe(mv.MetricRef("sales.revenue"), timescope="last_7d")
base = session.observe(mv.MetricRef("sales.revenue"), timescope="previous_7d")
delta = session.compare(cur, base)
delta
# <DeltaFrame ref=frame_cd34 metric=sales.revenue rows=1; call .show() to inspect>
delta.show()
```

This default makes multi-step scripts safe: intermediate results remain
available as typed objects but do not enter stdout unless the script explicitly
inspects them.

### Frame Projections

Frame projection methods do not print:

```python
frame.summary()
frame.preview(limit=10)
frame.render()
frame.to_pandas()
```

`show()` is the only frame method that writes stdout:

```python
frame.show()
```

## Bounded Plain-Text Rendering

`render()` should produce stable line-oriented plain text optimized for terminal
stdout and agent decision-making. The text is a small result card, not a full
data dump.

Every rendered result should have a fixed section order:

```text
<identity line>
status: <status or none>
columns: <bounded column list, when tabular>
preview:
<bounded rows, when tabular>
available:
<bounded inspection and consumption methods>
```

Rules:

- The first line identifies result type, ref or semantic id, kind/status, and row
  count when applicable.
- Every result render includes `available:`.
- `available:` always includes at least one safe bounded method. The common
  fallback entry is `.render()`.
- Tabular results include `.preview(limit=...)`.
- Analysis frames include `.to_pandas()`.
- Discovery results prioritize `.ids()`, `.first()`, and `.require_one()` when
  those helpers exist.
- `available:` must never render `none`.
- Each result type defines at most 5 `available:` entries, chosen at type
  definition time as the most useful bounded inspection or consumption methods
  for that result family.
- Implementations do not perform runtime `available:` truncation and do not
  print a "more capabilities" line.
- Tables use ` | ` as the delimiter.
- Preview output is bounded by default, with explicit row and column limits.
- Truncation is explicit and actionable.
- Cell serialization is deterministic for nulls, NaN-like values, datetimes,
  timedeltas, and scalar numpy/pandas values.
- Pandas default `to_string()` output is not a contract because column width,
  index display, and dtype formatting can drift across versions.

Example:

```text
MetricFrame frame_ab12 metric=sales.revenue shape=time_series rows=7
status: evidence=partial quality=unknown
columns: bucket_start | revenue
preview:
bucket_start | revenue
2026-06-01 | 123.4
2026-06-02 | 118.9
... 5 more rows; call .preview(limit=...) or .to_pandas()
available:
- .summary()
- .preview(limit=...)
- .to_pandas()
- .render()
```

## Size Budget

Each result type owns its display budget. A missed `show()` call should not be
dangerous.

Suggested defaults:

- header/status: up to 3 lines,
- columns: up to 8 columns,
- preview: up to 5 rows by default,
- total rendered text: target under 80 lines for any result.

If a result has more data than the budget, it must show a truncation line with a
specific inspection method. It must never silently omit large data without an
explicit hint.

## Display Flow

Display side effects should be centralized on result objects:

```python
class AgentReadableResult:
    def render(self) -> str: ...

    def show(self) -> None:
        print(self.render())
```

Result-producing public APIs should not call `show()`. Internal callers should
not need quiet flags because result-producing APIs are quiet by default. Help
APIs are the explicit exception: they print bounded help and return `None`.

## Errors

Errors remain typed exceptions. Marivo should not return error result objects
from normal result-producing public APIs.

Exception messages should be actionable for agents:

```text
Unsupported grain '5 minute' for base granularity 'day'.
Supported granularities: year, quarter, month, week, day.
Next: choose one supported value or change time_field to a finer-grained field.
```

Failure visibility comes from the raised typed exception and its structured
details. Successful result visibility comes only from `show()` or explicit
printing of `render()`. Help visibility comes from the `help(...)` call itself.

## Skill and Documentation Updates

### marivo-analysis

The skill should stop teaching `print(frame.summary())` as the default
exploration pattern and should not teach quiet flags.

Default multi-step script:

```python
session = mv.session.get_or_create(name="revenue_drop")
cur = session.observe(mv.MetricRef("sales.revenue"), timescope="last_7d")
base = session.observe(mv.MetricRef("sales.revenue"), timescope="previous_7d")
delta = session.compare(cur, base)
delta.show()
```

Programmatic script:

```python
cur = session.observe(...)
base = session.observe(...)
delta = session.compare(cur, base)
df = delta.to_pandas()
```

The skill should teach: call `show()` at deliberate observation points, not
after every API call. Final user-facing reports must still be answer-first and
source-backed. Bounded `show()` output is a working observation, not the final
user answer.

### marivo-semantic

The semantic skill should teach explicit inspection:

```python
mv.help(revenue)

metrics = project.list_metrics()
metrics.show()

metric_ids = metrics.ids()
```

It should not rely on discovery APIs printing by default, and it should teach
`mv.help(ref)` as the semantic-object briefing path before telling agents to
read `_model.py`.

### Docs and Drift Tests

Update:

- `docs/specs/semantic/python-semantic-layer.md`,
- `docs/specs/analysis/python-analysis-operator-design.md`,
- `marivo-skills/marivo-analysis/`,
- `marivo-skills/marivo-semantic/`,
- public API docstrings and `help()` text,
- existing stale-doc or drift tests that scan agent guidance.

Add drift checks that:

- require docs to mention that result-producing public APIs do not write stdout,
- require docs to mention that help APIs print bounded help and return `None`,
- require agent-facing docs to teach `mv.help(...)` as the canonical help
  entrypoint,
- reject `help(..., format=...)` examples,
- reject examples that present `ms.help(...)` or `project.help(...)` as the
  primary agent path rather than convenience aliases,
- require result examples to use `.show()` for terminal inspection,
- reject `display=True` and `display=False` examples in public API docs,
- reject examples that present `print(frame.summary())` as the normal
  exploration path,
- keep final-report guidance separate from bounded result inspection.

## Testing Strategy

### No-Stdout Public API Tests

Use stdout capture for representative result-producing public APIs:

- `project.list_metrics()` is silent.
- `project.dependencies(...)` is silent.
- `session.observe()` is silent.
- `session.compare()` and `session.decompose()` are silent.

### Help API Tests

Use stdout capture for representative help APIs:

- `mv.help()` prints bounded top-level help and returns `None`.
- `mv.help("observe")` prints bounded help and returns `None`.
- `mv.help("semantic.metric")` prints bounded authoring-topic help and returns
  `None`.
- `mv.help(revenue_ref)` prints bounded semantic-object help and returns `None`.
- `mv.help(revenue_ref, project=project)` works as the explicit project fallback.
- `mv.help(revenue_ref)` raises a typed, agent-actionable error when the ref
  cannot be resolved to a loaded semantic project.
- `ms.help(...)` and `project.help(...)`, if retained, delegate to the same
  bounded help renderer and return `None`.
- help APIs reject `format` or other shape-selection arguments.
- help output stays within the shared 80-line rendering budget.
- help APIs do not return objects with `show()`, `render()`, `summary()`, or
  `preview()`.

### Show and Render Tests

For representative result types, verify:

- `repr(result)` is one line, writes no stdout, and hints `.show()`.
- `_repr_html_()` does not render large previews automatically.
- `render()` returns text and writes no stdout.
- `show()` prints exactly `render()` plus a trailing newline.
- rendered text starts with result identity.
- rendered text stays within the result budget.
- preview is bounded.
- truncation text is stable and actionable.
- rendered text always includes `available:` with at least one safe bounded
  method.
- tabular rendered text includes `.preview(limit=...)` in `available:`.
- analysis frame rendered text includes `.to_pandas()` in `available:`.
- discovery result rendered text includes `.ids()`, `.first()`, and
  `.require_one()` in `available:` when those helpers exist.
- rendered text never includes `available: none`.
- no result type defines more than 5 `available:` entries, and no renderer emits
  runtime truncation or "more capabilities" text for that section.
- `summary()` returns the concrete typed summary for the result family.
- `summary()` and `preview()` do not write stdout.
- fields in `summary()` that affect agent interpretation are projected in
  `render()` / `show()`.
- `DiscoveryResult.require_one()` returns the item for exactly one result, raises
  a typed selection error with "no results" for zero results, and raises the same
  typed selection error with the actual count for multiple results.

### Internal Script Tests

Add regressions for agent-script safety:

- multi-step analysis scripts only print at explicit `.show()` calls,
- semantic discovery helper paths are quiet unless `.show()` is called,
- skill example runner output contains only deliberate inspection points.

## Acceptance Criteria

- A normal multi-step analysis script produces no stdout until it calls
  `result.show()`.
- Inspecting a returned public result shows a one-line `repr()` hint that points
  to `.show()` without dumping preview data.
- Calling `show()` on any public result prints bounded text that is enough for an
  agent to inspect the result safely.
- Rendered `available:` sections are always present, never say `none`, and list
  only bounded inspection or consumption methods rather than task-planning
  recommendations.
- `summary()` is the structured source for the core facts shown by
  `render()` / `show()`.
- Semantic discovery, semantic single-object result APIs, and analysis operators
  follow the same no-side-effect result contract.
- Help APIs print bounded help text directly and return `None`.
- `mv.help(...)` is the canonical help entrypoint taught to agents, and
  `mv.help(ref)` is the default semantic-object briefing path for already
  defined Python semantic refs.
- Semantic-object help gives bounded consumption context without replacing
  `_model.py` for authoring, implementation inspection, or debugging.
- Skills teach deliberate `.show()` inspection and no longer teach manual
  summary printing or display flags as the default exploration pattern.
- Final-report guidance still prevents agents from using bounded inspection
  output as the final answer.
