# Marivo Agent Analysis Surface Design

Date: 2026-07-08
Status: approved after design review; implementation not started

## Goal

Make Marivo's public analysis surface guide an unguided agent from
`marivo --help` to a complete metric analysis workflow without guesswork,
while removing non-default objects from the default agent-facing surface.

The target user is an agent using Marivo through a write-run-read loop. The
surface should lead that agent through:

1. finding the Python analysis entry point,
2. creating or reusing a session,
3. browsing semantic-layer objects,
4. choosing a supported analysis operator,
5. inspecting typed artifacts,
6. recovering session state across scripts,
7. producing a final analysis answer or terminal custom report.

This is a breaking public-surface cleanup. Compatibility with old top-level
imports is not a goal.

## Non-Goals

- Do not add a `marivo query`, `marivo analyze`, or report-generating CLI.
  The CLI remains project tooling and should route analysis work to the Python
  API.
- Do not repeat the recent doctor/datasource runtime fixes. The workflow may
  mention `marivo doctor` as a diagnostic step, but datasource resolution
  behavior is out of scope.
- Do not author or repair semantic-layer objects in the analysis surface.
  Semantic authoring remains owned by `marivo.semantic` and the
  `marivo-semantic` skill.
- Do not make `contract().affordances` a recommendation system. It remains
  mechanical compatibility data.

## Current Problems

The current package has most of the right pieces, but the discovery route is
not continuous:

- `marivo --help` lists only project tooling and does not point agents to
  `marivo.analysis`.
- `import marivo as mv` exposes no useful top-level direction.
- `help(marivo.analysis)` is too broad and can hang or push agents into
  internal implementation details.
- `mv.help()` is still closer to a symbol index than a default workflow guide.
- `dir(marivo.analysis)` exposes default analysis entry points alongside DTOs,
  internal modules, and expert-only references.
- The correct agent route, currently `mv.help("agent_surface")`, is useful but
  not discoverable enough and does not read like an end-to-end runbook.
- Error messages for semantic inputs can teach the type rule without giving a
  copyable recovery snippet for the current operation.

## Design Overview

Use a small default workflow surface, with expert details moved behind an
explicit advanced help topic.

The intended path is:

```text
marivo --help
  -> .venv/bin/python -c "import marivo.analysis as mv; mv.help('workflow')"
  -> mv.session.get_or_create(...)
  -> session.catalog.list(...).show()
  -> session.catalog.get(...)
  -> session.observe(...) or another default operator
  -> artifact.show()
  -> artifact.contract()
  -> session.frame_summaries() / recent_jobs() / get_frame(...)
  -> artifact.to_pandas() only for terminal custom reporting
```

## Library And Skill Responsibility Boundary

This design intentionally moves the basic analysis loop into the Marivo
library surface. That is not a duplication of the `marivo-analysis` skill; it
is the stable product contract that both skilled and unskilled agents should
be able to discover.

The Marivo library owns the minimum complete analysis loop:

- which Python module to import,
- how to create or reuse a session,
- how to browse semantic objects,
- which default operators exist and what they return,
- how to inspect artifacts with `show()` and `contract()`,
- how to recover session state,
- when `to_pandas()` is the terminal escape hatch,
- how typed errors should repair the next call.

The `marivo-analysis` skill owns agent operating discipline and analytical
methodology:

- how to map a user's question to the right intent path,
- when to stop and inspect `show()` / `contract()` before composing another
  call,
- how to handle quality, evidence, caveats, and blocking issues,
- when to use `derive_metric_frame` versus terminal `to_pandas()` analysis,
- how to write the final report,
- when a missing semantic object should be routed to `marivo-semantic`.

The skill may route agents to `mv.help("workflow")` and the specific help
topic for the next call, but it should not duplicate signatures, parameter
tables, return-type descriptions, or static API contracts. If skill guidance
and `mv.help(...)` describe the same callable detail, `mv.help(...)` is the
source of truth and the skill should be simplified.

## CLI Entry Point

`marivo --help` should keep the existing command set:

- `init`
- `publish`
- `doctor`

It should add an epilog that routes analysis to the Python API:

```text
Analysis workflow:
  .venv/bin/python -c "import marivo.analysis as mv; mv.help('workflow')"

Common diagnostics before live analysis:
  marivo doctor --semantic
  marivo doctor --datasource <name> --connect
```

This tells agents that the absence of a query subcommand is intentional and
that analysis is Python-native.

## Analysis Help Information Architecture

`mv.help()` becomes a workflow index rather than a broad API index.

Default topics:

- `workflow`
- `session`
- `catalog`
- `observe`
- `compare`
- `attribute`
- `discover`
- `correlate`
- `hypothesis_test`
- `forecast`
- `derive_metric_frame`
- `assess_quality`
- `alignment`
- `calendar`
- `artifacts`
- `recovery`
- `advanced`

`agent_surface` is retired from the documented surface. Because this is a
breaking cleanup, the implementation should teach `workflow` everywhere rather
than preserving an alias.

### `mv.help("workflow")`

This topic is the complete runbook. It should include:

1. import line,
2. optional doctor diagnostics for live data,
3. session creation with `mv.session.get_or_create(...)`,
4. catalog discovery commands,
5. default operator map and return types,
6. artifact read order,
7. session recovery commands,
8. terminal `to_pandas()` guidance,
9. final-report reminder.

The examples should use complete, kind-qualified catalog lookups:

```python
import marivo.analysis as mv

session = mv.session.get_or_create(
    name="revenue_drop",
    question="Why did revenue drop last week?",
    report_timezone="Asia/Shanghai",
)

catalog = session.catalog
catalog.list("domain").show()
catalog.list("metric", scope="domain.sales").show()

revenue = catalog.get("metric.sales.revenue")
created_at = catalog.get("time_dimension.sales.orders.created_at")

frame = session.observe(
    metric=revenue,
    time_scope={"start": "2026-06-29", "end": "2026-07-06"},
    grain="day",
    time_dimension=created_at,
    analysis_purpose="daily revenue trend for last week",
)
frame.show()
contract = frame.contract()
```

The workflow topic must stay bounded. Keep the rendered topic within the
existing help-topic budget unless the implementation explicitly changes the
budget test. Prefer a compact runbook with pointers to `mv.help("catalog")`,
`mv.help("artifacts")`, and `mv.help("recovery")` over embedding every detail.

### `mv.help("catalog")`

This topic owns analysis-side semantic consumption. It should not teach
authoring.

Required guidance:

- `session.catalog.list("domain").show()`
- `session.catalog.list("metric", scope="domain.<domain>").show()`
- `session.catalog.list("dimension", scope="entity.<domain>.<entity>").show()`
- `session.catalog.get("metric.<domain>.<metric>").details().show()`
- `mv.help(metric)` for the object returned by `catalog.get(...)`
- `mv.help(metric.ref)` for consumption briefing

Do not teach `catalog.list()` with no arguments in this work. If no-argument
listing is redesigned later, that should be a separate catalog contract change.

### `mv.help("session")`

This topic should avoid `Session.__init__` and focus on public helpers:

- `mv.session.get_or_create(...)`
- `mv.session.list()`
- `mv.session.current()`
- `session.frame_summaries()`
- `session.recent_jobs(limit=5)`
- `session.get_frame(ref)`

`session.knowledge()` and `session.evidence` should be described as audit and
recovery tools, not default first-pass analysis steps.

### `mv.help("artifacts")`

This topic should teach the two-exit artifact model:

- `artifact.show()` for bounded human/agent inspection,
- `artifact.contract()` before composing a next typed operator,
- `artifact.to_pandas()` only for terminal custom analysis or reporting.

It should not teach removed or deprecated result exits such as `summary()`,
`preview()`, direct schema access, or direct DTO construction.

### `mv.help("recovery")`

This topic should cover cross-script continuation:

```python
session = mv.session.get_or_create(name="revenue_drop")
for entry in session.frame_summaries():
    print(entry)
frame = session.get_frame("<artifact-ref>")
frame.show()
frame.contract()
```

The guidance should tell agents to choose persisted frames by
`analysis_purpose`, metric, time scope, shape, and created time.

### `mv.help("advanced")`

This topic is the escape hatch for maintainers and expert agents. It should
list advanced areas and their module paths without putting them in the default
index:

- `transform`
- `select`
- `evidence`
- `knowledge`
- contract DTOs
- artifact DTOs
- lineage DTOs
- slice predicate types
- frame internals
- derive builder DTOs

The advanced topic should make clear that these are not the default workflow
entry points.

`mv.help("transform")` and `mv.help("select")` should continue to render their
existing matrix topics for agents that explicitly ask for them. They move out
of the default workflow index; they do not disappear.

`mv.help("alignment")` and `mv.help("calendar")` remain default help topics
because `compare` and `correlate` use alignment policies, and the public helper
functions return policy values. Calendar implementation internals belong in
advanced help, but the user-facing alignment/calendar contracts do not.

## Public Export Policy

`marivo.analysis.__all__` should contain only the default workflow objects that
agents reasonably import directly.

Keep:

- `help`, `help_text`
- `session`, `Session`
- `MetricFrame`, `DeltaFrame`, `AttributionFrame`, `CandidateSet`,
  `AssociationResult`, `HypothesisTestResult`, `ForecastFrame`,
  `QualityReport`
- `window_bucket`, `dow_aligned`, `holiday_aligned`,
  `holiday_and_dow_aligned`
- `AlignmentPolicy`
- `ibis_query`, `metric_columns`, `time_column`, `dimension_column`
- `SemanticRef`, `SemanticObject`, `ArtifactRef`, `CalendarRef`
- `TimeScope`, `AbsoluteWindow`

Remove from the top-level export list:

- contract DTOs: `ArtifactAffordance`, `ArtifactColumn`, `ArtifactContract`,
  `ArtifactParamTemplate`, `ArtifactPrecondition`, `ArtifactSchema`,
  `ArtifactState`
- base/session metadata DTOs: `BaseFrame`, `BaseFrameMeta`,
  `FrameSummaryEntry`, `JobSummary`, `SessionSummary`
- lineage DTOs: `Lineage`, `LineageStep`
- issue/confidence DTOs: `BlockingIssue`, `ConfidenceScope`
- policy/type aliases: `AlignmentKind`, `CalendarPolicy`, `SamplingPolicy`,
  `CandidateObjective`, `DiscoverSensitivity`, `TimeScopeInput`
- slice types: `SlicePredicate`, `SlicePredicateOp`, `SliceScalar`,
  `SliceValue`
- specialized internal frames: `ComponentFrame`, `CoverageFrame`
- module attributes: `errors`, `evidence`, `frames`
- derive builder DTOs: `DeriveContext`, `IbisQuerySpec`,
  `MetricColumnBinding`, `MetricColumns`

The implementation may keep module-level import paths for advanced users, but
they should not appear in `dir(marivo.analysis)`, `mv.__all__`, or default
`mv.help()` output.

Removing names from `__all__` is not sufficient to clean `dir(marivo.analysis)`
because regular imports and side-effect imports populate the module
dictionary. Implement `marivo.analysis.__dir__()` so `dir(mv)` reflects the
default public surface, with deliberate additions only when a lazy attribute is
intended to be discoverable. Do not rely on `__all__` alone.

The import mechanics differ by name: `errors` is currently a regular top-level
import, while `evidence` and `frames` are lazy `__getattr__` paths. Removing
them from the default visible surface may require different code changes.

## Error Guidance

Semantic input errors in analysis operators should teach copyable recovery.

For a metric string passed to `observe`, the error should say that bare strings
are not accepted and show:

```python
metric = session.catalog.get("metric.sales.revenue")
session.observe(metric=metric, ...)
```

For a time dimension string passed to `observe`, the error should say
`time_dimension` expects a catalog time-dimension object or ref and show:

```python
created_at = session.catalog.get("time_dimension.sales.orders.created_at")
session.observe(metric=metric, time_dimension=created_at, ...)
```

Avoid wording that says a time dimension "requires a catalog dimension"; that
misleads agents into looking for the wrong semantic kind.

## Documentation And Skill Updates

Update these surfaces together:

- `marivo/cli.py`
- `marivo/analysis/help.py`
- `marivo/analysis/__init__.py`
- analysis semantic-input error construction
- `marivo/skills/marivo-analysis/SKILL.md`
- relevant `marivo/skills/marivo-analysis/references/*.md`
- latest site docs that teach analysis workflow or quick start
- analysis public-surface and drift tests:
  - `tests/test_analysis_help.py`
  - `tests/test_agent_api_drift.py`
  - `tests/test_cli.py`

`agent-guide.md` does not need a change unless implementation introduces a new
repository-wide coding or testing rule.

While rewriting `marivo/analysis/help.py`, remove existing Chinese
`analysis_purpose` examples from user-facing code strings and replace them with
English examples. This aligns the touched help surface with the repository rule
that source code and user-facing strings in code stay English unless a task is
explicitly localized.

While updating the skill, remove duplicated static API details when those
details are now owned by `mv.help("workflow")` or a specific `mv.help(...)`
topic. Keep routing, observation discipline, recovery practice, evidence
handling, and final-report guidance in the skill.

## Acceptance Criteria

- `marivo --help` routes analysis users to `mv.help("workflow")`.
- `mv.help()` is a short workflow index, not a broad type index.
- `mv.help("workflow")` covers session, catalog, operators, artifacts,
  recovery, and terminal reporting without gaps.
- `mv.help("catalog")`, `mv.help("session")`, `mv.help("artifacts")`,
  `mv.help("recovery")`, and `mv.help("advanced")` all render bounded,
  copyable guidance.
- `dir(marivo.analysis)` and `mv.__all__` no longer expose non-default DTO and
  internal module noise.
- Advanced objects remain reachable through module paths or advanced help, but
  they are not default agent-facing entries.
- `observe` semantic-input errors give concrete `catalog.get(...)` recovery
  snippets and use time-dimension-specific wording.
- The `marivo-analysis` skill references the library workflow contract instead
  of duplicating default operator signatures, parameter tables, and static
  return-type descriptions.
- The implementation diff is scoped to CLI help, analysis help, exports,
  semantic-input error wording, tests, skills, and docs. It should not modify
  `marivo/doctor.py` or `marivo/datasource/` runtime behavior.

## Verification Plan

Run focused checks first:

```bash
.venv/bin/marivo --help
.venv/bin/python -c "import marivo.analysis as mv; mv.help(); mv.help('workflow')"
.venv/bin/python -c "import marivo.analysis as mv; print(mv.__all__)"
make test TESTS='tests/test_agent_api_drift.py tests/test_analysis_help.py tests/test_cli.py'
```

Then run broader gates if focused checks pass:

```bash
make typecheck
make lint
```

Use `make test` if the implementation touches shared semantic catalog,
analysis frame, or datasource behavior beyond the scoped help/export/error
changes.
