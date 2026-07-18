---
name: marivo-analysis
description: Use for any Marivo metric-centered analysis task: observe, compare, attribute, discover, correlate, hypothesis_test, forecast, quality assessment, evidence-aware investigation, or continuing an analysis session over semantic metrics.
---

# marivo-analysis

## Trigger

Use this skill when the task involves:

- analysis over Marivo semantic metrics;
- continuation of an existing Marivo analysis session;
- review of conclusions backed by Marivo artifacts;
- decisions about staying in typed flow, using a terminal custom-analysis
  exit, or returning to semantic authoring.

Do not trigger solely for ordinary SQL, pandas, generic reporting, or
general data-analysis questions that do not use Marivo.

## Mission and authority

This skill is a **boundary protocol**, not a manual, tutorial, or analysis
planner. It protects Marivo-specific semantic, evidence, state, and routing
boundaries. It does not describe how to perform an analysis, which operators
to call, or how to interpret results.

An environment-verified live Marivo surface outranks any cached knowledge in
this file. Unverified `PATH` output does not. If the installed Marivo
package, its Python interpreter, or its help surface cannot be confirmed
before analysis begins, the skill stops and requests environment repair
rather than guessing.

## Live-contract rule

Use the same project interpreter for discovery and execution. Start with
`<analysis-python> -m marivo help analysis` or the corresponding
`<venv>/bin/marivo help analysis`, verify the rendered Marivo version,
resolved Python executable, and package path, then follow focused live help
topics for every API contract.

A bare `marivo` resolved from `PATH` is not authoritative unless its
rendered fingerprint matches the interpreter and package used by the
analysis process. If the intended analysis interpreter cannot be
identified, stop and request environment repair rather than selecting a
likely executable.

After entry:

- live help owns APIs, operators, constraints, examples, and recovery;
- semantic object details own the business-object contract;
- artifact reads own current-state facts and mechanical compatibility;
- structured errors own repair guidance.

Focused help is responsible for being self-contained for one correct
minimal invocation. Complex legal investigations may consult as many focused
topics as their actual branches require; this skill neither counts nor
forbids those calls. Help-call limits are interface evaluation thresholds,
not runtime permissions enforced by the skill.

The skill does not enumerate any API details, signatures, or examples.

## Script workspace

Use a rerunnable Python script as the default execution unit after analysis
entry. Store it under the current session's project-local
`<project_root>/.marivo/analysis/sessions/<session.id>/scripts/` directory and
run it with the verified `<analysis-python>`. On failure, repair and rerun the
same script instead of replacing it with disposable snippets.

Before authoring equivalent code, the agent may read scripts from this
project's current or prior sessions as reference material for imports, session
recovery, and operator composition. A prior script is an eligible reference
only when its originating session records a `succeeded` job and the output
artifact remains recoverable. Reference-only means never executing it directly,
copying it wholesale, or treating it as trusted current code.

For every new analysis, re-resolve semantic refs, time scopes, and parameters
against live help and current state. The script is not evidence: material
claims remain traceable to current semantic objects, artifacts/jobs, and
analysis scope.

## Historical session reference

Historical sessions are external reference memory, not prompt context that is
loaded by default. Inspect them only when resuming work, when the current
question clearly repeats earlier work, or when the same failure recurs. Use the
bounded historical-session surface from live recovery help and inspect no more
than three candidate sessions before returning to current live state.

Historical session metadata and scripts may suggest recovery or operator
composition, but they do not support current material claims. Never inherit a
prior conclusion, semantic assumption, or parameter without resolving it
against the current semantic catalog, runtime fingerprint, and analysis scope.

## Hard boundaries

### Semantic authority

Business metrics, dimensions, time dimensions, relationships, and caliber
come from the semantic catalog. Analysis code must not infer or redefine
business objects inside the semantic layer. A missing or disputed semantic
object stops the affected typed branch. During analysis the agent must not add,
edit, or remove semantic definitions; durable authoring is deferred until the
user approves the closeout proposal.

### Live-state authority

The agent acts on the installed runtime, current semantic state, current
artifacts, and current structured errors. Skill text and historical
examples must never override live state.

### Judgment separation

Artifacts, typed findings, bounded digests, candidates, scores, quality
results, issues, and affordances are computed facts or mechanical
compatibility information. They are not business conclusions,
recommendations, priorities, or stop conditions. The agent owns
cross-artifact synthesis and every next-step judgment.

### Evidence integrity

An artifact digest is a bounded operator-local read model, not a replacement
for the artifact or exact findings. When the question exceeds its retained
items or inference boundaries, the agent must inspect the live fallback
surface. It must not hide issues that affect validity or coverage, upgrade an
epistemic kind, or sever the recoverable evidence chain during script,
session, or agent transitions.

### Terminal boundary

Leaving typed Marivo analysis, adding semantic objects, and producing or
publishing deliverables must use the corresponding public boundary.
`session.observe(...)` is the sole producer of an initial canonical
`MetricFrame`. `frame.to_pandas()` and `md.raw_sql(...)` are the sole
terminal exits; results from either cannot re-enter typed analysis.
Missing business semantics remain unresolved until approved semantic
authoring; runtime capability gaps remain custom terminal work until modeled
explicitly. One-off analysis code must not absorb another layer's responsibility.

Choose a terminal exit deliberately from current artifact state and mechanical
compatibility. Familiarity with local pandas, SQL, or prior scripts is not a
reason to leave typed flow; cross the boundary only when the remaining work is
intentionally custom and terminal.

When a semantic gap blocks typed analysis, `md.raw_sql(...)` is an allowed
terminal escape without prior approval. The agent may use explicit temporary
inferred semantics, but must record every assumption and keep the result
separate from canonical Marivo evidence. A raw-SQL result cannot re-enter typed
analysis, become a semantic object, or erase the underlying semantic gap.

## Routing

| Condition | Route |
| --- | --- |
| A required business object is missing or must change | Stop the affected typed branch; optionally use terminal `md.raw_sql(...)`; defer `marivo-semantic` until closeout approval |
| Semantic authoring returns ready refs | Read the current `ReadinessReport`; after blockers are cleared and warnings are disclosed, consume only `analysis_ready_refs` through the ordinary analysis APIs |
| The task needs terminal custom analysis | `md.raw_sql(...)` or `frame.to_pandas()` (terminal; cannot re-enter typed analysis) |
| The user requests a durable report, notebook, slides, HTML, or publishing | The corresponding independent delivery capability |
| The work is Marivo repository maintenance or dogfooding | Follow repository-local maintainer instructions; do not use the public skill as maintainer guidance |

A missing or changed semantic object produces an
`AnalysisRepair(kind="semantic_authoring")`. This identifies why typed analysis
cannot continue; it is not permission to mutate the semantic layer during the
analysis. The agent records the exact gap, may use terminal raw SQL, and requests
approval for the smallest semantic change at closeout. Only after approval does
it follow the semantic help target through `marivo-semantic`. After that change
passes explicit readiness, analysis resumes with the report's current
`analysis_ready_refs`.

## Boundary-violation behavior

The skill does not provide fallback implementations.

| Situation | Required behavior |
| --- | --- |
| Missing or ambiguous semantic object | Distinguish invalid lookup from genuine absence; stop the affected typed branch, use terminal raw SQL only with disclosed temporary assumptions, and defer authoring until closeout approval |
| Invalid API, shape, parameter, or operator | Follow the current structured error and live help; do not use a skill-cached workaround |
| Result-impacting blocker | Repair it, weaken the conclusion explicitly, or stop; never silently emit a stronger claim |
| Session or artifact cannot be recovered | Use the live recovery surface and disclose evidence-chain loss if recovery still fails |
| Correct Marivo version/help is unavailable or its fingerprint differs from the execution environment | Stop guessing and diagnose the environment; do not continue analysis |
| Task crosses a typed-analysis boundary | Hand off explicitly to the appropriate semantic, terminal-analysis, or delivery capability |
| Skill conflicts with live Marivo | Treat the live surface as authoritative and the skill as drifted |

## Closeout obligations

The skill does not prescribe a report structure. Require only that:

- material claims remain traceable to the relevant semantic object,
  artifact/job, and analysis scope;
- Marivo facts, agent interpretations, and unverified hypotheses remain
  distinguishable;
- result-impacting issues, quality limitations, omissions, and inference
  boundaries are disclosed;
- semantic gaps that weakened or blocked the task are named and handed back
  as a smallest-change proposal that requires explicit user approval before
  semantic authoring;
- every raw-SQL escape names the missing or incorrect semantic object, why the
  typed branch stopped, the datasource and analysis purpose, all temporary
  inferred semantics, and the terminal/bounded/no-lineage/no-evidence-continuity
  limitations; canonical artifact claims and raw-SQL-supported claims remain
  visibly separate;
- absolute interpreter and package paths from the environment fingerprint do
  not enter user-facing reports or deliverables. Internal diagnostic logs
  and evaluator transcripts may retain them.
