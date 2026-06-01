# marivo-semantic Skill Redesign

Date: 2026-06-01

Status: approved design, pending implementation plan.

## Goal

Redesign `marivo-skills/marivo-semantic` around the new Marivo semantic
discovery capabilities. The skill should guide agents to build semantic layers
from structured evidence, auditable decisions, and explicit readiness gates,
rather than relying on a manual Phase 0 checklist and ad hoc judgment.

The skill is for pip-installed Marivo usage in ordinary projects. It must not
assume the user has the Marivo source checkout, repo-local fixtures, `make`, or a
specific virtualenv name.

## Non-Goals

- Do not create a second semantic definition surface. Python files under
  `.marivo/semantic/<model>/` remain the only semantic source of truth.
- Do not make the ledger executable or authoritative for object definitions. It
  records provenance, confirmations, decisions, and rejected candidates only.
- Do not turn richness into a handoff gate. `project.richness(...)` is advisory;
  `project.readiness(...)` is the gate.
- Do not preserve old Phase 0 references/examples when they duplicate or
  contradict the new flow.
- Do not assume source-checkout commands in user-facing skill guidance. Repo
  entrypoints may appear only in maintainer validation notes.

## Target Skill Pack Structure

```text
marivo-skills/marivo-semantic/
  SKILL.md
  references/
    workflow.md
    authoring-patterns.md
    evidence-and-ledger.md
    closeout.md
    datasource.md
    preview.md
    pitfalls.md
    examples/
      01_single_model_file.py
      02_candidate_to_questions.py
      03_closeout_readiness_richness.py
```

`SKILL.md` is a router and rule surface. It should stay short enough for agents
to read every time the skill is invoked. Detailed API usage moves into the
reference files.

## Main Workflow

The primary agent loop is:

```text
discover existing project
-> inspect datasource/table metadata
-> project.propose_candidates(...)
-> project.open_questions(...)
-> ask only unresolved blocking questions
-> author one _model.py using ref variables
-> project.answer(...) / project.record_decision(...)
-> reload + preview + parity
-> project.audit(...)
-> project.readiness(require_evidence_ledger=True, strict_enrichment=True, ...)
-> project.richness(demand=...)
-> handoff only if readiness passes; report richness as advisory
```

The flow is declarative and state-derived. The skill must not prescribe a server
state machine or a step cursor. Agents can edit Python files in any order, then
reload and re-derive state from the files plus ledger.

## SKILL.md Contract

`SKILL.md` should contain:

- When to use the skill: declaring or modifying Marivo semantic datasets, fields,
  time fields, metrics, or relationships.
- When not to use it: running analysis on an already-ready model belongs to
  `marivo-analysis`.
- Runtime assumptions for pip-installed use:
  - identify the project Python environment first;
  - use `<venv>/bin/python` style examples;
  - do not assume `make` or Marivo repo paths;
  - require a project with `.marivo/semantic/` or direct the agent to initialize
    one.
- Non-negotiable rules:
  - Python semantic files are the source of truth;
  - names are candidate signals only, never sufficient business evidence;
  - use `project.propose_candidates(...)` and `project.open_questions(...)`
    before authoring new objects when datasource metadata is available;
  - ask users only for unresolved blockers or business decisions evidence cannot
    settle;
  - record user confirmations with `project.answer(...)`;
  - record adopted and rejected material decisions with
    `project.record_decision(...)`;
  - do not hand off to `marivo-analysis` while readiness is blocked;
  - run `project.richness(...)` at closeout and report it separately from
    readiness.
- Reference routing:
  - object construction starts with `references/workflow.md`;
  - Python declaration shapes use `references/authoring-patterns.md`;
  - uncertain decisions and conflicts use `references/evidence-and-ledger.md`;
  - validation and handoff use `references/closeout.md`;
  - datasource setup uses `references/datasource.md`;
  - preview failures use `references/preview.md`;
  - known failure modes use `references/pitfalls.md`.

## Authoring Defaults

The skill should make these defaults explicit in `SKILL.md` and expand them in
`references/authoring-patterns.md`:

- Default to a single `.marivo/semantic/<model>/_model.py`.
- Use `md.ref("<datasource>")` for datasource references.
- Use decorated Python ref variables for semantic object wiring inside the model
  file, for example `orders`, `order_date`, `revenue`.
- Use string `ms.ref(...)` only for forward references, cross-model boundaries,
  or generated tooling cases where Python imports are awkward.
- Prefer a datasource time partition field such as `dt`, `log_date`, or
  `event_date` as the dataset `@ms.time_field`.
- Use a non-partition business event time only when knowledge, source SQL,
  comments, or user confirmation establishes that axis. Record that choice in
  `description`, `ai_context.business_definition`, and the ledger when material.
- Include `ai_context.business_definition` and `ai_context.guardrails` for
  analyzable handoff refs: datasets, fields, time fields, and metrics.
- Add `ai_context.synonyms` and `ai_context.examples` when demand or richness
  points to them.

## Reference Responsibilities

### `references/workflow.md`

This is the main execution guide. It should include the end-to-end workflow,
minimal code snippets for each phase, and the rule that the agent must rerun
state queries after edits instead of trusting stale in-session assumptions.

It should show:

- discovering the project;
- inspecting existing refs;
- inspecting datasource metadata;
- generating candidates;
- classifying questions;
- authoring semantic Python;
- recording confirmations and decisions;
- closing with audit, readiness, and richness.

### `references/authoring-patterns.md`

This owns Python declaration patterns:

- single `_model.py` layout;
- datasource refs;
- dataset, field, time field, metric, derived metric, and relationship snippets;
- decorated ref variables;
- partition time field priority;
- `ai_context` minimums;
- relationship keys using field/time-field refs;
- derived metrics using `ms.component(...)` only.

### `references/evidence-and-ledger.md`

This owns evidence and decision discipline:

- evidence authority model in compressed form;
- what Marivo can fetch automatically;
- what requires user confirmation;
- how `Candidate`, `Enrichment`, and `OpenQuestion` are used;
- how `project.answer(...)` records user confirmations;
- how `project.record_decision(...)` records adopted/rejected decisions;
- ledger is provenance only.

### `references/closeout.md`

This owns final validation and handoff:

- reload;
- raw and semantic previews;
- parity for source-SQL metrics;
- `project.audit(...)`;
- `project.readiness(require_evidence_ledger=True, strict_enrichment=True, ...)`;
- `project.richness(demand=...)`;
- blocked readiness prevents analysis handoff;
- richness gaps are advisory follow-up work.

### `references/datasource.md`, `references/preview.md`, `references/pitfalls.md`

These stay as tool references but must be compressed and aligned with the new
flow. They should not define a competing main workflow.

## Files to Delete or Merge

Implementation should remove these old references after their useful content is
merged:

- `references/authoring-workflow.md`
- `references/evidence.md`
- `references/readiness.md`
- `references/richness.md`
- `references/cheatsheet.md`

Implementation should replace the old examples with the three new examples. If a
repo validation test depends on an old example path, update the test or migrate
the relevant example content into one of the new examples; do not preserve an old
example with obsolete semantics just to keep the path alive.

## Example Strategy

### `01_single_model_file.py`

Shows a pip-installed project shape with:

- one datasource;
- one `.marivo/semantic/sales/_model.py`;
- dataset declaration;
- partition time field declaration;
- one reusable field;
- one base metric;
- minimal `ai_context`.

### `02_candidate_to_questions.py`

Shows the discovery loop:

- load project;
- inspect table metadata;
- call `project.propose_candidates(...)`;
- call `project.open_questions(...)`;
- record a user answer with `project.answer(...)`;
- record a selected decision with `project.record_decision(...)`.

The example may use illustrative sample values for actual user answers, but it
must not imply that business meaning can be inferred from names alone.

### `03_closeout_readiness_richness.py`

Shows validation:

- build a backend factory;
- run previews and parity where applicable;
- run `project.audit(...)`;
- run readiness with `require_evidence_ledger=True` and
  `strict_enrichment=True`;
- run richness with `DemandSignal`;
- print readiness blockers separately from richness gaps.

## Error Handling

The redesigned skill should route failures by source:

- Load, decorator, or AST errors: fix declaration shape using
  `authoring-patterns.md`.
- Datasource, metadata, or preview errors: use `datasource.md` or `preview.md`;
  do not skip evidence collection silently.
- Open-question blocker: ask the user or collect stronger evidence; do not
  default-guess.
- Readiness blocker: fix before handoff.
- Richness gap: report as advisory unless the user asks to continue enriching.
- Audit stale decision: re-enter the `open_questions` path; do not silently keep
  using the stale ledger decision.

## Validation Checklist

Implementation is complete when:

- `SKILL.md` no longer presents the Phase 0 checklist as the main path.
- User-facing skill docs do not assume Marivo source checkout, `make`, or a fixed
  `.venv` name.
- The main path uses `propose_candidates`, `open_questions`, ledger recording,
  audit, readiness, and richness.
- The authoring defaults explicitly prefer single `_model.py`, ref variables, and
  partition time fields.
- Deleted references/examples have no dangling links from the skill pack.
- Examples are pip-installed-project oriented and do not depend on repo fixtures.
- Maintainer validation passes with the repo's normal documentation/example
  checks, including `make examples-check` when run from this checkout.

## Migration Notes

This is a rewrite, not a small edit. The implementation plan should update the
skill pack in coherent slices:

1. Replace `SKILL.md` and create the new core references.
2. Rewrite or compress retained references.
3. Replace examples and remove obsolete files.
4. Update tests or example manifests that reference deleted paths.
5. Run repo validation.
