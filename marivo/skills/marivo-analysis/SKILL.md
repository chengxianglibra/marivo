---
name: marivo-analysis
description: Use for any Marivo metric-centered analysis task: observe, compare, attribute, discover, correlate, hypothesis_test, forecast, quality assessment, governed derive_metric_frame, evidence-aware investigation, or continuing an analysis session over semantic metrics.
---

# marivo-analysis

Use this skill when running metric-centered workflows with `marivo.analysis`
imported as `mv`.

Use `marivo-semantic` instead when the task is authoring semantic-layer objects.
If an analysis exposes missing semantic-layer objects, capture that gap in the
recap and route the authoring work to `marivo-semantic`; do not author semantic
objects inside this skill.

## Ownership

This skill owns workflow only: intent routing, session discipline, observation
points, recovery discipline, and final report shape.

`mv.help()` owns the static analysis contract: signatures, artifact families,
constraints, return types, errors, and runnable examples. Start with
`mv.help("workflow")`, then inspect the specific topic before calling it,
such as `mv.help("observe")`, `mv.help("discover")`, `mv.help("alignment")`,
or `mv.help("MetricFrame")`.

Frames and results own dynamic guidance. Use `artifact.show()` to inspect the
current state and `artifact.contract()` to inspect mechanically valid next actions.
The contract is not a recommendation engine; agent judgment decides which valid
action matters for the user's question.

## Python Environment

For installed-project analysis, identify the project virtualenv first and use
`<venv>/bin/python` for scripts. Do not use bare `python`, `python3`, `pip`, or
`pip3`.

Inside the Marivo repository, use repository entrypoints only:

```bash
make test
make typecheck
make lint
make examples-check
```

## Start Flow

1. Verify the installed analysis surface:
   `<venv>/bin/python -c 'import marivo.analysis as mv; mv.help("workflow")'`.
2. Inspect the specific runtime topic before calling it:
   `mv.help("observe")`, `mv.help("catalog")`, or `mv.help("artifacts")`.
3. Create or reuse one stable task session:
   `session = mv.session.get_or_create(name="revenue_drop_investigation")`.
4. Browse the semantic catalog with typed collection properties
   (`session.catalog.domains.show()`, `session.catalog.metrics.show()`),
   then inspect every metric, dimension, and time dimension the task will
   use with `obj.details().show()`. Read the displayed `ai_context` before
   composing analysis intents.
5. Gate the scoped handoff with
   `session.catalog.readiness(refs=[...]).show()` and resolve blockers before
   observing.
6. Stay in typed artifact flow until terminal analysis requires
   `artifact.to_pandas()`.

Examples are smoke tests and copyable starting points, not the analysis methodology.
For exact callable contracts, use `mv.help("<topic>")`.

## Intent Routing

Use this routing map to pick the first operator, then read `mv.help` for the
exact contract:

```text
Value of a metric in one window?           -> observe
Current vs baseline change?                -> observe x2 -> compare
Why the change happened?                   -> compare -> attribute
Spikes, drops, unusual buckets?            -> observe series -> discover.<objective>
Two metrics move together?                 -> observe both -> correlate
Mean changed between paired samples?       -> observe x2 -> hypothesis_test
Need a future projection?                  -> observe series -> forecast
Need auditable quality evidence?           -> assess_quality
Custom Ibis calculation that must re-enter -> derive_metric_frame
Raw pandas from a frame?                   -> artifact.to_pandas()
```

Default operator names are:

- `session.observe(...)`
- `session.compare(current_frame, baseline_frame, ...)`
- `session.attribute(delta_frame, axes=[...])`
- `session.discover.<objective>(...)`
- `session.correlate(a_frame, b_frame, ...)`
- `session.hypothesis_test(a_frame, b_frame, ...)`
- `session.forecast(history_frame, ...)`
- `session.derive_metric_frame(...)`
- `session.assess_quality(artifact)`

Prefer these default operators first. Use `session.derive_metric_frame(...)`
only when a custom Ibis calculation must re-enter the governed metric-frame
flow. Use `artifact.to_pandas()` for terminal custom analysis that does not
need to feed another typed Marivo intent.

## Analysis Loop

Bundle a chain into one script only when the next operator is already known.
Stop and inspect when the next step depends on values you have not seen.

At each deliberate observation point:

```python
artifact.show()
contract = artifact.contract()
```

Read `show()` for bounded current-state evidence. Read `contract()` before
composing the next operator. Do not pre-write speculative downstream steps just
because they are mechanically possible.

Good split points:

- `discover.<objective>` -> choose the candidate worth selecting or drilling.
- `correlate` -> decide which association deserves follow-up.
- `attribute` -> decide which segment or time bucket needs finer inspection.
- Any branch where `artifact.show()` or `artifact.contract()` changes the next
  call.

## Session And Recovery

Default to one session per analysis task. A script split is not a session
split. Reuse the same stable session name for retries, follow-up scripts, and
branch exploration so artifact refs, evidence facts, and job history remain
available.

Use a new session only when the user starts an independent investigation or the
current session is polluted enough that restarting is the clearest recovery.
State that reason in the final response.

Recover prior artifacts from the session instead of re-running datasource work:

```python
session = mv.session.get_or_create(name="revenue_drop_investigation")
summaries = session.frame_summaries()
previous = session.get_frame("frame_ref_from_summaries")
```

On errors, read the structured output. Use fields such as `schema_version`,
`code` or `kind`, `candidates`, and `repair` or fix snippets. Do not guess from
a stale example when the error provides current repair guidance.

## Evidence And Quality

`artifact.show()` is the default first read after an intent. When the intent
emitted evidence, the same bounded card includes the commit-time evidence
summary before its preview, so do not make a routine second evidence call.

Use `session.knowledge()` for cross-step synthesis or recovery and
`session.evidence` for full typed records and audit traces. Continue to inspect
`artifact.meta.evidence_status`, `artifact.meta.blocking_issues`,
`artifact.meta.confidence_scope`, and `artifact.meta.quality_summary` when
partial, unavailable, or quality-limited results affect the answer.

```python
artifact.meta.evidence_status
artifact.meta.blocking_issues
artifact.meta.confidence_scope
artifact.meta.quality_summary
```

`artifact.meta.quality_summary` is a lightweight status attached to the result.
`session.assess_quality(artifact)` is an explicit auditable operator that
creates a `QualityReport`, persists its latest quality blockers on the source
artifact, and participates in lineage. Do not treat a window as a complete
period fact while `artifact.meta.blocking_issues` contains `time_coverage`.

## Closeout And Recap

For any non-trivial closeout, read `references/final-report.md` before the
final user response. Do not end with only `frame.show()`, `artifact.show()`,
`frame.head(n)`, or raw tables.

Final analysis reports should be answer-first and include:

- conclusion and scope
- key evidence from Marivo artifacts
- caveats and assumptions
- source details
- quality or blocking issues
- agent-authored next steps

When the analysis exposes missing semantic-layer objects or metadata, name them
in the recap and tell the user what to add through `marivo-semantic`. Common
gaps include a missing metric, dimension, time dimension, entity relationship,
unit, or business context. Keep the recap concrete: state which analysis step
was blocked or weakened by the missing semantic object.

## Internal Marivo Feedback

After user-facing closeout, record Marivo-only dogfooding issues at
`.marivo/analysis/internal_feedback/<session_id>.md`, using the current
`session.id` as the filename. Keep these notes out of final answers, reports,
semantic objects, artifacts, evidence, and `session.knowledge()`.

Capture only product or agent-usage friction, such as confusing help, weak
repair guidance, inconsistent `show()` or `contract()` output, API friction,
docs drift, unsupported natural workflows, or semantic-analysis handoff gaps.
Use short Markdown fields: workflow phase, Marivo surface, symptom,
expected/actual behavior, workaround, result impact (`none`, `caveat`, or
`blocker`), suggested fix, and reproduction pointer. If the issue affects
result validity, coverage, confidence, or blocks analysis, also disclose it to
the user as a caveat, quality issue, or blocker.

## Further Reading

- `references/final-report.md` — final user-facing report structure and QA.
- `references/pitfalls.md` — structured error recovery and common mistakes.
- `references/cheatsheet.md` — compact routing aid after runtime help.
- `references/backend-setup.md` — datasource and backend wiring.
- `references/cumulative-frames.md` — cumulative frame caveats, anchor-dispatched compare, and rollup re-aggregation.
- `references/examples/` — smoke tests and copyable starting points only.
