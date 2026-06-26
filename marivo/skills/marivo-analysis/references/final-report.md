# Final analysis report

Use this reference before closing a non-trivial Marivo analysis task. The final
answer is a user-facing synthesis, not a dump of intermediate frame previews.

## Purpose

The report should make the conclusion easy to read, show the evidence that
supports it, and preserve the limits of the analysis. Write Markdown by default.
Only build an HTML or MCP artifact when the user explicitly asks for that
delivery surface.

## Required structure

Use the shape below unless the user's requested format is more specific.

1. Title
   - State the subject, metric, and analysis type plainly.
2. Executive Summary / 结论摘要
   - Lead with the answer in 2 to 4 bullets.
   - Each bullet should include a concrete result, direction, magnitude, segment,
     or time window when available.
   - If comparing two analyses or agents, state which conclusion is more
     reasonable and where each one is too strict, too broad, or unsupported.
3. Scope and metric basis / 分析范围与口径
   - Include the metric id or name, time window, grain, filters, dimensions,
     comparison baseline, and any threshold or classification rule.
   - Mention the relevant frame or artifact references when useful for recovery.
4. Key Findings / 核心发现
   - Give each finding a takeaway heading.
   - For each finding, include the judgment, the supporting evidence, the
     interpretation, and the operational or business implication.
   - Use charts or compact tables when they make the finding easier to verify.
5. Candidate, driver, or segment review / 候选项明细
   - For anomaly, discover, decompose, or ranking work, include exact identifying
     keys such as segment id, resource id, bucket, timestamp, axis, or selector.
   - Show current value, baseline or previous value, absolute change, relative
     change, score, status, and the reason for accepting or rejecting each
     important candidate when those fields are available.
6. Caveats and Assumptions / 限制与假设
   - State missing source context, unknown units, quality issues, incomplete
     coverage, unresolved blocking issues, and assumptions that affect the
     conclusion.
   - Do not claim root cause when only metric correlation or ranking evidence was
     checked.
7. Recommended Next Steps / 建议动作
   - Prioritize actions by expected value or urgency.
   - Use `artifact.contract().affordances` to inspect mechanical compatibility, session knowledge, and analyst
     judgment; distinguish data-quality remediation from business investigation.
8. Source details / 来源与可复现信息
   - List source tables, metric formulas, time windows, filters, frame/artifact
     ids, and scratch query summaries when safe.
   - Do not expose secrets, credentials, private tokens, or unrelated full raw
     datasets.

## Evidence and visuals

- Put the interpretation next to each chart or table; do not make the reader
  infer the conclusion from a screenshot or preview.
- Prefer compact evidence tables for exact identifiers and decisions.
- Prefer charts for trends, window comparisons, rule comparisons,
  classification mix, and before/after patterns.
- Every visual or table should have enough context to answer: what metric,
  what scope, what grouping, and why it matters.
- Use `frame.summary()`, `frame.preview(limit=...)`, or bounded `to_pandas()`
  output to inspect evidence, but turn that evidence into a report narrative.

## Interactive report artifact close-out

When the user asks for an interactive analysis report or a durable report
package, assemble a `MarivoReportArtifact` instead of treating Markdown as the
only output. The report remains narrative-first and artifact-backed:

- The narrative layer owns the title, executive summary, finding takeaways,
  caveats, and recommendations.
- The evidence layer owns KPI strips, charts, compact tables, candidate reviews,
  and driver views.
- The audit layer owns `grounding.json`, flow steps, source provenance, semantic
  refs, SQL or intent details, scripts, replay metadata, and evidence status.

Keep readable interpretation adjacent to each important chart or table. Do not
make the user infer the conclusion from raw rows or a chart alone.

Every reader-facing number in a claim, KPI, chart label, or numeric callout
should resolve through `value_refs` to a bounded dataset cell or to an
artifact/evidence field. Do not restate the same number as a second source of
truth in prose or adapter manifests.

Value formats: the `percent` format expects values already expressed in
percentage points (store `89.8` for `89.8%`, not `0.898`). Use `number` or
`compact` for raw counts and `currency` for monetary values.

When choosing `format` and value suffixes for report metrics, consult the
semantic metric's `unit` (via `mv.help(ref)` or catalog details): `%` means
values are percentage points (use percent format); a bare ISO 4217 code is a
currency suffix; `1` means dimensionless fractions. Never rescale values to
match a unit.

Charts must be single-series: a chart block's dataset needs one value per x
position, or it must declare a `series` channel
(`fields={"x": ..., "y": ..., "series": ...}`) so a bar chart can group bars by
series. Never feed a decomposition dataset that mixes dimensions (for example
`query_type` and `source` rows sharing one timestamp) into a single x/y chart;
filter to one dimension or split into separate charts.

Output locations: agent-generated analysis scripts and rendered reports
belong under the session directory, not at the project root. Use
`session.save_report(artifact)` to persist and register a
report package in one call. The package bytes live under the session directory
at `<project_root>/.marivo/analysis/sessions/<session_id>/reports/<report_id>/`.
To publish a registered report outside the workspace, call
`session.publish_report(report_id, target=...)`.

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="investigation")

# Persist and register the report package in the session store.
registration = session.save_report(artifact)
# registration.report_id  -> e.g. "rpt_abc123"
# registration.package_dir -> absolute path to the on-disk package

# Publish outside the workspace (optional).
result = session.publish_report(registration.report_id, target="/published")
```

Paths in `script_refs` stay relative (e.g.
`"scripts/step_observe.py"`); they resolve against
`session.layout.session_dir` for validation (see below).

Script references: `FlowStep.script_refs` and `SourceProvenance.script_refs`
must list every real script path — one entry per file that exists on disk.
Never invent a range-style filename like `"scripts/step1-7.py"` or
`"scripts/step4-6.py"` to stand in for several files; those files do not
exist, and the Audit Trail will publish broken links that readers cannot open.
Enumerate each path explicitly.

Wrong:

```python
# One fabricated filename pretending to cover steps 1 through 7.
script_refs=("scripts/trino_anomaly_step1-7.py",)
```

Right:

```python
script_refs=(
    "scripts/trino_anomaly_step1.py",
    "scripts/trino_anomaly_step2.py",
    "scripts/trino_anomaly_step3.py",
    "scripts/trino_anomaly_step4.py",
    "scripts/trino_anomaly_step5.py",
    "scripts/trino_anomaly_step6.py",
    "scripts/trino_anomaly_step7.py",
)
```

The Audit Trail renders each entry as its own clickable link, so a real
per-file list is both correct and more useful to readers. The same rule
applies to `FlowStep.script_refs` for individual steps: if a step is
implemented by `step4.py`, `step5.py`, and `step6.py`, list all three — not
the invented aggregate `"scripts/step4-6.py"`.

Before rendering, call
`validate_report_artifact(artifact, script_root=session.layout.session_dir)`
so every `script_refs` entry is checked against the session directory the
relative paths resolve against. The validator emits one `script_ref_missing`
issue per path that does not exist on disk, so a fabricated
`"scripts/step1-7.py"` is caught regardless of filename shape — the check is
existence, not pattern matching.

Before rendering or publishing, validate that `grounding.json` resolves every
executive-summary claim, partial evidence is visible, source provenance matches
the step kind, and source/audit details do not crowd out the main reading path.

## MCP adapter handoff

When the selected delivery surface is a Data Analytics MCP report app, use the
Marivo MCP adapter after the core report artifact validates:

1. Build and validate the `MarivoReportArtifact`.
2. Call `session.save_report(artifact, adapter="mcp")` to persist the package
   with MCP adapter files and register it in the session store.
   Internally this uses `to_mcp_artifact_payload(artifact)` for the MCP manifest
   and writes the adapter files to the session report directory.
3. In Codex/Data Analytics environments, call MCP `validate_artifact` before the
   first visible `render_artifact` call. Iterate on validator errors with the
   validator, not by repeatedly rendering visible broken artifacts.

The MCP adapter is a bounded report surface. It should expose the same frozen
datasets, source provenance, visual blocks, caveats, and narrative path as the
Marivo report package. It must not connect to live datasources. It must not
recompute main claims. It must not replace `grounding.json` / `flow.json` as the
audit source of truth.

## Publishing handoff

When the report package should be shared outside the local workspace, use
session-scoped publish after the artifact validates and has been saved as a
package:

1. Build and validate the `MarivoReportArtifact`.
2. Call `session.save_report(artifact)` to persist the package
   directory and register it in the session store. The returned
   `ReportRegistration` contains `report_id` and `package_dir`.
3. Call `session.publish_report(report_id, exported_by=..., target=...)` to
   publish the registered package. The publish method re-validates, scans for
   secrets, and writes the manifest last.
4. The helper loads and re-validates the package, scans packaged text files for
   secrets, computes a deterministic content hash (excluding `manifest.json`),
   and stamps `exported_by`, `exported_at`, and `content_hash` into the published
   manifest.
5. Content files upload first and `manifest.json` is written last, so a partial
   upload is never mistaken for a completed publish.

The publish destination is user-scoped: the resolved path must include the
`exported_by` segment, and existing targets are immutable by default (pass
`overwrite=True` to replace one). The library never publishes secrets,
credentials, or row-level frames that the manifest data policy omits. Publishing
is deterministic and library-owned; it does not author narrative or
replay scripts.

## Discovery and anomaly reports

When reporting anomalies or discovered candidates, separate signal from noise.
The CDN bandwidth drop review is the model:

- Distinguish broad rule hits from actionable candidates.
- Classify candidates into high confidence, medium confidence, low confidence,
  expected cycle, missing-data gap, low-volume noise, or rejected.
- Explain why a broad count is not necessarily an alert count.
- Call out periodic patterns separately from sudden non-periodic changes.
- Treat low-volume near-zero swings as unstable unless they are operationally
  meaningful.
- For each top candidate, include the exact entity and time point plus previous
  value, current value, absolute change, relative change, score, and rationale
  when available.

## Reliability checklist

Before finalizing, check the evidence surface:

- Inspect `result.meta.evidence_status` and report partial or unavailable
  evidence that affects the answer.
- Inspect `result.meta.blocking_issues` and avoid hiding unresolved blockers.
- Inspect `result.meta.confidence_scope` and do not generalize beyond it.
- Inspect `result.meta.quality_summary`; when data quality materially affects the
  conclusion, run `session.assess_quality(...)` or state that quality was not
  independently assessed.
- Use `artifact.contract().affordances` only as mechanical compatibility metadata.
  Final report conclusions, exclusions, and next actions are agent-authored, not
  generated by Marivo artifacts.
- If using `session.explore_ibis(...)`, pandas scratch work, or promoted
  frames, describe the terminal scratch output or promotion path so provenance
  stays visible.

## Anti-patterns

- Reporting only a leaderboard, row preview, or raw summary.
- Treating broad candidate counts as actionable incidents without review.
- Omitting the time window, grain, metric definition, filters, or source.
- Hiding caveats in vague language such as "data may vary".
- Claiming causality from compare, correlate, discover, or decompose output
  without external cause evidence.
- Mixing periodic changes, missing-data gaps, low-volume noise, and true sudden
  changes into one undifferentiated anomaly bucket.

## Sampled semi-additive metric reporting

When a MetricFrame has `coverage_ref`, inspect `frame.coverage()` before
reporting sampled folded metrics. If a folded frame is `reaggregatable=False`,
do not roll it up manually; re-run `session.observe(...)` at the required grain
or dimensions.
