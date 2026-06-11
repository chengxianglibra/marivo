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
   - Use `result.meta.recommended_followups`, session knowledge, and analyst
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
truth in prose, adapter manifests, or HTML.

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
filter to one dimension or split into separate charts. The HTML renderer rejects
a chart whose x values repeat without a `series` channel.

Report language: pass `language="zh-Hans"` (or matching BCP 47 locale code)
to `render_report_html`, `materialize_html_adapter`, or
`materialize_mcp_adapter` to localize the report's chrome and the Audit Trail
headings/labels from built-in catalogs
(`marivo/analysis/publish/locales/`, English fallback) and to emit
`<html lang>` accordingly. Use script-based tags (`zh-Hans`, `zh-Hant`) rather
than bare `zh` or region-only forms — the catalog lookup applies progressive
prefix fallback so `zh-Hans-CN` resolves to the `zh-Hans` catalog, but a bare
`zh` will not auto-pick a script variant. The renderer stamps the value onto
`manifest.language` for the written package; authored titles and narrative
text are not translated. You can also set `manifest.language` directly when
constructing the artifact — the keyword argument overrides it for the render.
To add a language, add a `<lang>.json` catalog — never hardcode non-English
labels in Python.

Output locations: agent-generated analysis scripts and rendered HTML reports
belong under the active session's directory, not at the project root. Use
`session.layout.scripts_dir` and `session.layout.reports_dir` (both resolve
to `<project_root>/.marivo/analysis/sessions/<session_id>/{scripts,reports}/`)
and create the directory lazily before writing:

```python
from pathlib import Path
import marivo.analysis as mv

session = mv.session.get_or_create(name="investigation")

scripts_dir = session.layout.scripts_dir
scripts_dir.mkdir(parents=True, exist_ok=True)
(scripts_dir / "step_observe.py").write_text("# observe ...", encoding="utf-8")

reports_dir = session.layout.reports_dir
reports_dir.mkdir(parents=True, exist_ok=True)
html = mv.publish.render_report_html(artifact, language="zh-Hans")
(reports_dir / "trino_anomaly_weekly.html").write_text(html, encoding="utf-8")
```

For a full report package, call
`materialize_html_adapter(artifact, root=session.layout.reports_dir / artifact.manifest.report_id, script_source_dir=session.layout.session_dir)`
so the package files land under `reports/<report_id>/`. When
`script_source_dir` is provided, every referenced script is copied into the
report package, making `<a href="scripts/...">` links functional after
publishing. Paths in `script_refs` stay relative (e.g.
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
2. Call `to_mcp_artifact_payload(artifact)` for an in-memory MCP manifest,
   snapshot, sources, and package metadata.
3. When the payload should be stored in the package, call
   `materialize_mcp_adapter(artifact, package_root)` and use the returned
   artifact so `manifest.adapter_mcp` records the adapter files.
4. In Codex/Data Analytics environments, call MCP `validate_artifact` before the
   first visible `render_artifact` call. Iterate on validator errors with the
   validator, not by repeatedly rendering visible broken artifacts.

The MCP adapter is a bounded report surface. It should expose the same frozen
datasets, source provenance, visual blocks, caveats, and narrative path as the
Marivo report package. It must not connect to live datasources. It must not
recompute main claims. It must not replace `grounding.json` / `flow.json` as the
audit source of truth.

## HTML adapter handoff

When the selected delivery surface is a standalone HTML report, use the Marivo
HTML adapter after the core report artifact validates:

1. Build and validate the `MarivoReportArtifact`.
2. Call `to_html_report_payload(artifact)` when the agent needs to inspect the
   exact renderer payload before writing files.
3. Call `render_report_html(artifact)` for an in-memory standalone HTML string.
4. Call `materialize_html_adapter(artifact, package_root)` when the report
   package should include `index.html`; use the returned artifact so
   `manifest.entrypoints["html"]` records the file.

The standalone HTML surface opens directly from `index.html` and uses frozen
datasets, grounding, flow steps, evidence objects, and source provenance from
the Marivo report package. It must not connect to live datasources.
It must not recompute executive-summary claims. It should keep the main reading
path answer-first, with source, SQL, script, dataset, and evidence details
available through links or expandable panels instead of crowding out the
narrative.

The standalone HTML surface renders evidence-bearing blocks inline so proof sits
next to the conclusion it supports:

- `claim_evidence` blocks render as expandable proof panels next to a finding.
- `step_trace` blocks render the step -> artifact -> source chain with jump links.
- `source_code` blocks render a SQL or script drawer.

Set `collapsed_by_default` on these blocks to control whether the panel starts
expanded or collapsed. The surface supports bounded interaction only: chart tooltips,
table pagination, and local search over already-packaged content. These
interactions never run live queries, never create new aggregations, and never
recompute executive-summary claims.

## Publishing handoff

When the report package should be shared outside the local workspace, publish the
staged package with the deterministic library helper after the artifact validates
and an adapter has materialized at least one entrypoint (for example
`index.html`):

1. Build and validate the `MarivoReportArtifact`, then materialize the package
   directory with `materialize_html_adapter` (and `materialize_mcp_adapter` if an
   MCP payload should be stored).
2. Call `publish_report_package(package_dir, exported_by=..., target=...)`.
3. The helper loads and re-validates the package, scans packaged text files for
   secrets, computes a deterministic content hash (excluding `manifest.json`),
   and stamps `exported_by`, `exported_at`, and `content_hash` into the published
   manifest.
4. Content files upload first and `manifest.json` is written last, so a partial
   upload is never mistaken for a completed publish.

The publish destination is user-scoped: the resolved path must include the
`exported_by` segment, and existing targets are immutable by default (pass
`overwrite=True` to replace one). The library never publishes secrets,
credentials, or row-level frames that the manifest data policy omits. Publishing
is deterministic and library-owned; it does not author narrative, HTML, or
replay scripts.

For direct S3 upload of an HTML report package (without going through
`publish_report_package`), see `references/upload-html-report.md`.

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
- Inspect `result.meta.quality`; when data quality materially affects the
  conclusion, run `session.assess_quality(...)` or state that quality was not
  independently assessed.
- Review `result.meta.recommended_followups` and session knowledge for open
  items worth reporting.
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
