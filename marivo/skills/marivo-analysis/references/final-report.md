# Final analysis report

Use this reference before closing a non-trivial Marivo analysis task. The final
answer is a user-facing synthesis, not a dump of intermediate frame previews.

## Purpose

The report should make the conclusion easy to read, show the evidence that
supports it, and preserve the limits of the analysis. Write Markdown by default.
Only build an HTML, MCP, PDF, slide, or notebook deliverable when the user
explicitly asks for that delivery surface. Marivo does not generate reports;
the agent authors the deliverable from persisted Marivo evidence.

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
   - For anomaly, discover, attribute, or ranking work, include exact identifying
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
7. Agent-authored Next Steps / Agent 撰写的建议动作
   - Prioritize actions by expected value or urgency when the evidence supports
     that judgment.
   - Marivo affordances are mechanical compatibility facts, not recommendations from Marivo.
     Use `artifact.contract()` for mechanical compatibility metadata, session evidence, and analyst judgment;
     distinguish data-quality remediation from business investigation.
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
- Use `frame.show()` or bounded `to_pandas()`
  output to inspect evidence, but turn that evidence into a report narrative.

## Agent-authored deliverables

When the user asks for an interactive analysis report or any durable
deliverable, the agent builds it directly. Marivo provides persisted analysis
state; it does not provide report schemas, adapters, renderers, validators, or
publish targets.

Use these Marivo surfaces as the evidence source:

- `session.jobs()` and `session.job(id)` for executed steps and parameters.
- `session.frame_summaries()` to discover persisted frames worth citing.
- `session.get_frame(ref)` to reload a frame for `show()`, `contract()`, or a
  bounded `to_pandas()` extraction.
- `artifact.meta` for evidence status, blocking issues, confidence scope,
  lineage, quality summary, metric ids, semantic refs, and row counts.
- `session.knowledge()` and `session.evidence` when explicit evidence objects,
  findings, open issues, or cross-step audit facts matter.

For HTML, MCP, PDF, slides, or notebook deliverables, create the file or hosted
artifact with the available agent/runtime tools. Keep the output self-contained
when that is the requested format, and perform delivery-surface QA in that
tooling. Do not look for Marivo report package APIs; they are intentionally not
part of the library contract.

When choosing formats and value suffixes, consult the semantic metric's `unit`
through runtime help or catalog details. `%` means values are percentage points;
a bare ISO 4217 code is a currency suffix; `1` means dimensionless fractions.
Never rescale values just to match display formatting.

Charts must use coherent data grain. Never feed a decomposition extract that
mixes dimensions (for example `query_type` and `source` rows sharing one
timestamp) into a single x/y chart; filter to one dimension, split into
separate charts, or visibly encode the grouping dimension.

Agent-generated analysis scripts and deliverables belong under the session
directory or another user-approved output location, not as new Marivo library
state. After the requested deliverable has been created and QA-checked, use
`marivo publish <path>` to upload a file or directory when the project has S3
publish configuration. Verify the resulting artifact in the delivery surface.
When publish prints both a reader-facing HTTP URL and an S3 URI, cite the
reader-facing HTTP URL as the report link and keep the S3 URI only as object
location metadata.

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
- Use `artifact.contract()` for mechanical compatibility metadata.
  Final report conclusions, exclusions, and next actions are agent-authored, not
  generated by Marivo artifacts.
- For custom Ibis calculations that re-enter the typed analysis flow, cite the
  `derive_metric_frame` artifact ref and its lineage. Do not describe pandas
  scratch work as a canonical MetricFrame source.

## Anti-patterns

- Reporting only a leaderboard, row preview, or raw summary.
- Treating broad candidate counts as actionable incidents without review.
- Omitting the time window, grain, metric definition, filters, or source.
- Hiding caveats in vague language such as "data may vary".
- Claiming causality from compare, correlate, discover, or attribute output
  without external cause evidence.
- Mixing periodic changes, missing-data gaps, low-volume noise, and true sudden
  changes into one undifferentiated anomaly bucket.

## Sampled semi-additive metric reporting

When a MetricFrame has `coverage_ref`, inspect `frame.coverage()` before
reporting sampled folded metrics. If a folded frame is `reaggregatable=False`,
do not roll it up manually; re-run `session.observe(...)` at the required grain
or dimensions.
