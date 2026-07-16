# Marivo Analysis Boundary Kernel Design

Status: base analysis cutover implemented; semantic-routing follow-up pending written-spec re-review

Date: 2026-07-13

## Summary

> **2026-07-16 simplification amendment:** Semantic readiness now exposes
> `ReadinessReport.analysis_ready_refs` directly. Missing semantic objects use
> `AnalysisRepair(kind="semantic_authoring")`. No directional payload,
> validation result, or analysis-side readiness boundary exists. Any older
> transfer/result language below is historical and non-normative.

Redesign the packaged `marivo-analysis` skill as a minimal boundary kernel
between a general-purpose coding agent and the live Marivo analysis runtime.
The skill will no longer own an analysis workflow, repeat API guidance, teach
general analytical practice, or constrain valid exploration paths.

The target is a single `SKILL.md` with near-zero static product knowledge. It
will activate Marivo's live progressive-disclosure surface, state a small set
of non-negotiable semantic and evidence boundaries, define cross-capability
routings, and require Marivo-specific traceability at closeout.

This is an atomic breaking contract and guidance redesign. It provides no
compatibility package shape, deprecated attachment path, redirect, or user
migration workflow. It does not change analysis operators, artifact models,
session persistence, semantic runtime behavior, or quality algorithms.

## Problem

The current layering says that `marivo-analysis` owns workflow while
`mv.help()` owns static API contracts and artifacts own dynamic guidance. That
boundary was useful before Marivo's public help surface matured, but it now
creates duplicate ownership.

The current CLI routes agents into analysis help, and `mv.help("workflow")`
already exposes the default runbook, question-to-operator routing, artifact
read order, quality gates, operator boundaries, and recovery entry points.
Topic help, semantic object details, artifact `show()` / `contract()`, metadata,
and structured errors progressively disclose the remaining live behavior.

The packaged skill repeats much of that material through its start flow,
intent routing, analysis loop, recovery instructions, quality field list,
examples, pitfalls, backend guidance, cumulative guidance, and report
template. This creates four problems:

- static skill content can drift from the installed Marivo version;
- agents must choose between duplicate sources of truth;
- a suggested workflow can be mistaken for a required analysis plan;
- the skill teaches API usage and general analysis behavior that belongs to
  Marivo or the agent.

## Decision

Adopt a **boundary-kernel** design.

`marivo-analysis` is the minimal policy adapter that protects Marivo-specific
semantic, evidence, state, and routing boundaries. It does not describe how to
perform an analysis. Within the legal boundary, the agent remains free to plan,
branch, inspect, recover, validate, stop, and report according to the task.

The design covers the complete active contract surface:

- the packaged skill and its current references/examples;
- ownership statements in repository guidance and analysis specs;
- current latest-version user documentation in English and Chinese;
- help, constraint, error, and introspection references that point into the
  packaged skill;
- installation, drift, example, and error-template tests affected by removing
  skill-owned content.

Historical versioned documentation and release notes remain historical and are
not rewritten.

## Alternatives Considered

### Activation-only boundary

The skill would only say to enter through an environment-bound Marivo help
command and follow the live analysis route. This is maximally small, but it
does not protect against Marivo-specific category errors such as redefining a
business metric from raw fields, treating artifact affordances as
recommendations, silently discarding blockers, or losing an evidence chain
across routings.

### Boundary kernel — selected

The skill contains one live entry rule, a small set of hard boundaries,
routing conditions, and closeout obligations. It preserves agent autonomy while
protecting the parts of Marivo usage that a general-purpose model cannot infer
from generic analysis knowledge alone.

### Quality-governance gateway

The skill would additionally define risk tiers, methodology checklists,
analysis review gates, claim ledgers, and report templates. This could increase
delivery consistency but would teach general analysis practice, duplicate
other skills, and recreate a prescriptive workflow. Quality governance can be
composed separately when a task requires it.

## Ownership Model

| Layer | Ownership |
| --- | --- |
| Environment-bound CLI help and `mv.help()` | Capability discovery, environment identity, API contracts, operator semantics, constraints, examples, and recovery mechanisms |
| Semantic objects | Business definitions, units, composition, additivity, guardrails, and provenance |
| Artifact `show()`, `contract()`, and metadata | Current facts, mechanical compatibility, lineage, quality, blockers, and confidence scope |
| `marivo-analysis` skill | Hard boundaries, cross-capability routings, evidence continuity, and Marivo-specific closeout obligations |
| Agent | Requirement interpretation, hypotheses, exploration path, method selection, judgment, stop criteria, conclusions, and recommendations |
| Repository maintainer guidance | Repository tests, dogfooding, internal feedback, and development rules |

The active guide and analysis specs must stop saying that the skill owns
workflow. The replacement contract is:

> Environment-verified Marivo live surfaces own capabilities and runtime
> guidance. The skill owns hard boundaries, routings, evidence continuity, and
> Marivo-specific closeout obligations. The agent owns analysis planning and
> judgment.

This analysis decision does not erase the semantic track's real partial order.
Semantic authoring must acquire evidence, satisfy object dependencies, verify,
preview, and reach scoped readiness before analysis routing; the
`marivo-semantic` skill may own that state-transition routing while live
`md.help(...)` / `ms.help(...)` own API facts. Analysis begins after readiness
and exposes a non-linear graph of legal operators, so its skill owns boundaries
rather than a route. Aligning semantic fingerprints, typed repair objects, and
discovery ergonomics is named future work under **semantic live-surface
alignment**, not part of the base breaking analysis cutover. The typed
bidirectional routing, analysis validator, semantic producer, and corresponding
rules in both skills land later as one atomic semantic-authoring cutover; none
of those pieces is published early.

## Design Goals

### Live-contract first

All version-sensitive information comes from the installed Marivo package,
current semantic objects, current artifacts, or current structured errors.
The skill does not treat a package as authoritative until its help fingerprint
matches the interpreter and package that will execute the analysis.

### Version resilience

Ordinary API additions, removals, signature changes, and new artifact families
must not require a skill edit. The skill changes only when a boundary,
ownership rule, or routing contract changes.

### Agent autonomy

The skill rejects invalid boundary crossings but does not prescribe a valid
analysis path. Two agents can take different legal routes and both satisfy the
skill.

### Boundary completeness

The skill identifies when work stays in typed analysis, returns to semantic
authoring, exits for terminal custom analysis, re-enters governed analysis, or
moves to a deliverable/publishing capability.

### Evidence continuity

The task's semantic anchors, artifacts, jobs, scope, blockers, confidence, and
open gaps remain recoverable across scripts, context compaction, and agent
routings.

### Minimal cognitive load

Every skill statement must pass this deletion test:

> If the statement can be recovered from Marivo's live surface, or a capable
> general agent already knows it, remove it from the skill.

## Target Package Shape

The packaged shape is exactly:

```text
marivo/skills/marivo-analysis/
└── SKILL.md
```

There is no `references/` directory and there are no packaged examples. If the
target contract still requires a fact currently present in an attachment, its
new owner must independently expose that fact in the same breaking release.
No attachment, redirect, copy, or tombstone remains in the packaged skill.

## `SKILL.md` Structure

### Trigger

Trigger for:

- analysis over Marivo semantic metrics;
- continuation of an existing Marivo analysis session;
- review of conclusions backed by Marivo artifacts;
- decisions about staying in typed flow, using a terminal custom-analysis
  exit, re-entering governed flow, or returning to semantic authoring.

Do not trigger solely for ordinary SQL, pandas, generic reporting, or general
data-analysis questions that do not use Marivo.

### Mission and authority

State that the skill is a boundary protocol, not a manual or planner. An
environment-verified live Marivo surface outranks cached skill knowledge;
unverified `PATH` output does not.

### Live-contract rule

Provide one stable root instruction:

> Use the same project interpreter for discovery and execution. Start with
> `<analysis-python> -m marivo help analysis` or the corresponding
> `<venv>/bin/marivo help analysis`, verify the rendered Marivo version,
> resolved Python executable, and package path, then follow focused live help.

A bare `marivo` resolved from `PATH` is not authoritative unless its rendered
fingerprint matches the interpreter and package used by the analysis process.
If the intended analysis interpreter cannot be identified, the skill stops and
requests environment repair rather than selecting a likely executable.

After entry:

- live help owns APIs, operators, constraints, examples, and recovery;
- semantic details own the business-object contract;
- artifact reads own current-state facts and mechanical compatibility;
- structured errors own repair guidance.

Focused help is responsible for being self-contained for one correct minimal
invocation. Avoidable disclosure hops are an interface defect that an external
Agent UX evaluator may measure; they are not a runtime permission rule in the
skill. Complex legal investigations may consult as many focused topics as
their actual branches require; the skill neither counts nor forbids those
calls.

The skill does not enumerate any of those details.

### Hard boundaries

Keep exactly the categories below. The final wording may be concise, but it
must preserve their meaning.

#### Semantic authority

Business metrics, dimensions, time dimensions, relationships, and caliber come
from the semantic catalog. Analysis code must not infer or redefine business
objects from raw fields. A missing or disputed semantic object returns to
semantic authoring.

#### Live-state authority

The agent acts on the installed runtime, current semantic state, current
artifacts, and current structured errors. Skill text and historical examples
must never override live state.

#### Judgment separation

Artifacts, candidates, scores, quality statuses, and affordances are computed
facts or mechanical compatibility. They are not business conclusions,
recommendations, priorities, or stop conditions. The agent owns those
judgments.

#### Evidence integrity

The agent must not hide blockers that affect validity, coverage, or confidence,
and must not sever the recoverable evidence chain during script, session, or
agent transitions.

#### Governed transition

Leaving typed Marivo analysis, re-entering governed analysis, adding semantic
objects, and producing or publishing deliverables must use the corresponding
public boundary. One-off analysis code must not absorb another layer's
responsibility.

### Routing

The skill keeps cross-track routing explicit without inventing transfer objects:

- genuine semantic absence follows
  `AnalysisRepair(kind="semantic_authoring")` into `marivo-semantic`;
- semantic authoring returns only after explicit readiness and analysis consumes
  exactly `ReadinessReport.analysis_ready_refs`;
- terminal custom analysis and deliverable production keep their independent
  boundaries.

The skill does not reconstruct missing-object requirements from conversation
memory, and readiness warnings never imply captured acceptance.

### Closeout obligations

Do not prescribe a report structure. Require only that:

- material claims remain traceable to the relevant semantic object,
  artifact/job, and analysis scope;
- Marivo facts, agent interpretations, and unverified hypotheses remain
  distinguishable;
- result-impacting blockers, quality limitations, and confidence limits are
  disclosed;
- semantic gaps that weakened or blocked the task are named and handed back to
  semantic authoring;
- absolute interpreter and package paths remain available only to the live
  in-memory validator and explicit environment-authority diagnostics. Ordinary
  routing/result renders mask them, and they do not enter persisted analysis
  state, user-facing reports, or deliverables. Internal diagnostic logs and
  evaluator transcripts may retain them.

## Information Flow

```text
user question
    -> boundary skill activates
    -> environment-bound help proves version/interpreter/package identity
    -> current Marivo help discloses live capabilities
    -> agent chooses and revises its analysis path
    -> Marivo artifacts preserve facts and state
    -> agent makes judgments
    -> traceable closeout or explicit routing
```

This is a state and ownership flow, not a required sequence of analysis
operators.

## Boundary-Violation Behavior

The skill does not provide fallback implementations.

| Situation | Required behavior |
| --- | --- |
| Missing or ambiguous semantic object | Distinguish invalid lookup from genuine absence; for genuine absence stop the affected branch and transfer the typed semantic-authoring routing |
| Invalid API, shape, parameter, or operator | Follow the current structured error and live help; do not use a skill-cached workaround |
| Result-impacting blocker | Repair it, weaken the conclusion explicitly, or stop; never silently emit a stronger claim |
| Session or artifact cannot be recovered | Use the live recovery surface and disclose evidence-chain loss if recovery still fails |
| Correct Marivo version/help is unavailable or its fingerprint differs from the execution environment | Stop guessing and diagnose the environment; do not continue analysis |
| Task crosses a typed-analysis boundary | Hand off explicitly to the appropriate semantic, terminal-analysis, or delivery capability |
| Skill conflicts with live Marivo | Treat the live surface as authoritative and the skill as drifted |

## Atomic Replacement Scope

The target release deletes every current attachment and all active code,
metadata, test, and documentation links to it. There is no dual package shape
and no release in which the old and new skills are both supported.

Current attachments are audit inputs only. For each still-required fact,
implement the target owner directly; delete duplicated, historical, generic,
or invalid guidance. Do not preserve wording, paths, runners, redirects,
compatibility copies, or tombstones merely to ease the cutover.

The later semantic-authoring release makes one further atomic edit to this
single-file analysis skill: it installs the typed bidirectional routing rule in
the same candidate that adds the analysis validator and semantic producer. It
does not ship that rule before those live owners or keep the earlier conceptual
routing as a fallback afterward.

Expected ownership:

| Current content | Correct owner |
| --- | --- |
| `references/cheatsheet.md` | CLI help, `mv.help()`, artifact contract |
| `references/cumulative-frames.md` | cumulative live help and dynamic artifact caveats |
| `references/pitfalls.md` | structured errors, intent help, and recovery help |
| `references/backend-setup.md` | datasource/session help and `marivo doctor` |
| `references/final-report.md` | general agent or delivery skill; retain only Marivo-specific closeout obligations in `SKILL.md` |
| `references/judgment-db-schema.md` | internal analysis spec or runtime introspection |
| `references/typed-facts.md` | runtime types/help/spec |
| `references/examples/*` | live help, current site documentation, or test fixtures |
| Internal feedback instructions | Remove from the public skill; repository maintainers own any future internal policy |

References from `marivo/analysis/errors.py`, `marivo/analysis/constraints.py`,
introspection metadata, and tests are replaced with canonical live help
targets or deleted in the same coordinated change.

## Active Contract Alignment

Update active guidance that currently says the skill owns workflow, including:

- `agent-guide.md`;
- active analysis design/spec documents;
- latest English and Chinese user documentation;
- the target release's new English and Chinese release-note entries for public
  removals and field renames;
- current help/constraint/error/introspection descriptions where they encode
  the old ownership or point into packaged references;
- installation, drift, example, and error-template tests affected by the
  package shape.

Do not rewrite historical versioned site content or release notes.
The target release creates new bilingual release-note entries for the breaking
analysis surface; those entries are part of the release being designed, not a
rewrite of historical records.

## Acceptance Criteria

### Skill shape

- The packaged skill contains exactly one `SKILL.md`.
- It contains no API signatures, operator inventory, parameter tables, call
  examples, ordered analysis process, generic methodology checklist, report
  template, repository test commands, or internal feedback procedure.
- It contains only the trigger, authority rule, hard boundaries, routings, and
  closeout obligations defined above.
- An ordinary Marivo API change does not require a skill update.

### Ownership consistency

- Active guide, spec, help, and latest site documentation use the new ownership
  contract consistently.
- No active text claims that the skill owns the default workflow, operator
  routing, recovery instructions, or report shape.
- Public help and artifact surfaces expose every target-state contract fact
  assigned to them.

### No capability loss

- `marivo --help` preserves discoverable semantic-authoring entry instructions
  while adding the analysis help route.
- `<analysis-python> -m marivo help analysis` and the corresponding environment
  console script lead to the live analysis entry and render matching version,
  interpreter, and package fingerprints.
- Intent and API detail remains discoverable through `mv.help(...)`.
- Artifact-specific behavior remains discoverable through artifact reads.
- Structured errors and constraints do not point to deleted files.
- Backend, recovery, cumulative, and other still-required guidance is available
  from its target owner in the breaking release.

### Agent behavior

- A normal metric-analysis task activates live help and allows the agent to
  choose its own path.
- Focused help exposes enough information for an external Agent UX evaluator
  to measure disclosure efficiency; the skill itself imposes no help-call
  quota on real investigations.
- An environment mismatch stops execution before analysis rather than allowing
  one package's help to guide another package's runtime.
- A missing metric causes a semantic-authoring routing rather than raw-field
  redefinition.
- The semantic-authoring routing preserves its typed requirement, affected
  capability, project/semantic context, artifact/evidence lineage, and
  environment fingerprint without skill reconstruction.
- In the atomic semantic-authoring target, analysis consumes analysis-ready ready
  refs only after the registered boundary produces
  `ReadinessReport`; this applies to first entry and re-entry, and the
  skill neither reconstructs nor treats the payload as proof of warning
  acceptance.
- A result-impacting blocker prevents an unqualified strong conclusion.
- An API change is resolved from current help/errors rather than cached skill
  text.
- Terminal analysis and durable deliverables cross explicit public routings.
- User-facing closeout omits absolute interpreter and package paths even when
  internal diagnostics retain the full fingerprint.

## Verification Strategy

Automated checks validate code, package structure, executable discovery, and
link integrity. They must not snapshot or pin prose merely to enforce the
current wording of `SKILL.md`.

The implementation must:

- keep skill installation/link tests passing;
- remove or update tests and runners that require packaged analysis examples;
- verify that no source, metadata, current doc, or test points to deleted
  references/examples;
- keep both the environment-bound console-script and `python -m marivo`
  analysis-help routes executable;
- verify version/interpreter/package fingerprint equivalence for matching
  routes and inequality for deliberately skewed environments;
- keep live help examples aligned with real signatures;
- validate latest site content and links;
- run repository test, type, lint, example, site-content, and diff-integrity
  gates appropriate to the touched files.

Scenario-based review covers the agent-behavior acceptance criteria. It is a
review of outcomes and boundaries, not a prose snapshot of the skill.

Base-analysis package checks assert that no result-only rule or
`catalog.readiness(...)` target is exposed without the semantic producer.
The later semantic-authoring candidate verifies producer, consumer, both
directional schemas, both skill rules, first entry, and re-entry together; no
mixed-version or legacy-ready-ref scenario is supported or scored.

Model-backed Agent UX evaluation is owned by the standalone public
`marivo-agent-evals` project. It may evaluate a target-only candidate against
the installed Marivo package, but its model profiles, prompts, scenarios,
orchestration, scoring thresholds, transcripts, and reports do not live in this
repository and are not a PR gate here. Marivo PR checks retain only the fast,
deterministic package, surface, help, error, result, skill-structure, and drift
contracts described above.

## Non-goals

This redesign does not:

- change analysis computations or algorithms;
- add or remove public analysis operators;
- change artifact schemas or persistence;
- change semantic object behavior;
- define a universal data-analysis methodology;
- create a new report format or publishing system;
- create a planner, reviewer, or recommendation engine inside Marivo;
- provide deprecated skill references, compatibility shims, or a migration
  mode.

## Success Test

The redesign succeeds when a capable Codex or Claude Code agent can discover
and use the current execution environment's Marivo version without learning
API details from the skill or trusting an unverified `PATH` executable,
can explore freely inside valid boundaries, and cannot silently bypass the
semantic source of truth, lose material evidence limitations, or blur computed
facts with agent judgment.
