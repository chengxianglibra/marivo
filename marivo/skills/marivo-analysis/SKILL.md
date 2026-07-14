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
planner. It protects Marivo-specific semantic, evidence, state, and handoff
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

## Hard boundaries

### Semantic authority

Business metrics, dimensions, time dimensions, relationships, and caliber
come from the semantic catalog. Analysis code must not infer or redefine
business objects from raw fields. A missing or disputed semantic object
returns to semantic authoring via the `marivo-semantic` skill.

### Live-state authority

The agent acts on the installed runtime, current semantic state, current
artifacts, and current structured errors. Skill text and historical
examples must never override live state.

### Judgment separation

Artifacts, candidates, scores, quality statuses, and affordances are
computed facts or mechanical compatibility information. They are not
business conclusions, recommendations, priorities, or stop conditions.
The agent owns those judgments.

### Evidence integrity

The agent must not hide blockers that affect validity, coverage, or
confidence, and must not sever the recoverable evidence chain during
script, session, or agent transitions.

### Terminal boundary

Leaving typed Marivo analysis, adding semantic objects, and producing or
publishing deliverables must use the corresponding public boundary.
`session.observe(...)` is the sole producer of an initial canonical
`MetricFrame`. `frame.to_pandas()` and `md.raw_sql(...)` are the sole
terminal exits; results from either cannot re-enter typed analysis.
Missing business semantics return to semantic authoring; runtime
capability gaps remain custom terminal work until modeled explicitly.
One-off analysis code must not absorb another layer's responsibility.

## Handoffs

| Condition | Handoff |
| --- | --- |
| A required business object is missing or must change | `marivo-semantic` |
| Semantic authoring returns ready refs | The registered analysis semantic-handoff boundary |
| The task needs terminal custom analysis | `md.raw_sql(...)` or `frame.to_pandas()` (terminal; cannot re-enter typed analysis) |
| The user requests a durable report, notebook, slides, HTML, or publishing | The corresponding independent delivery capability |
| The work is Marivo repository maintenance or dogfooding | Follow repository-local maintainer instructions; do not use the public skill as maintainer guidance |

The skill preserves and transfers the typed handoff payload carried by the
current error or semantic readiness result. It does not reconstruct those
fields from conversation memory or add a broader catalog-cleanup request. The
returning handoff is mechanically validated by the analysis boundary before
the skill resumes analysis routing; the receipt does not select an operator
or record warning acceptance.

## Boundary-violation behavior

The skill does not provide fallback implementations.

| Situation | Required behavior |
| --- | --- |
| Missing or ambiguous semantic object | Distinguish invalid lookup from genuine absence; for genuine absence stop the affected branch and transfer the typed semantic-authoring handoff |
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
- result-impacting blockers, quality limitations, and confidence limits are
  disclosed;
- semantic gaps that weakened or blocked the task are named and handed back
  to semantic authoring;
- absolute interpreter and package paths from the environment fingerprint do
  not enter user-facing reports or deliverables. Internal diagnostic logs
  and evaluator transcripts may retain them.
