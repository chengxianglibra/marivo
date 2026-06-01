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

This redesign must remain consistent with
`docs/specs/semantic/agent-semantic-layer-authoring-design.md`, which is still
the source-of-truth authoring design for evidence, preview, readiness, and agent
handoff. The skill may replace the old Phase 0 execution path, but it must not
contradict that design's evidence-first and readiness-before-analysis contract.

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
-> project.answer(...) for user confirmations
-> project.record_decision(...) only when a complete evidence-backed DecisionRecord is available
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
    `project.record_decision(...)` only when the agent has the full required
    `DecisionRecord` fields from the question/candidate/evidence context;
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
- how `project.record_decision(...)` records adopted/rejected decisions when a
  complete `DecisionRecord` can be built;
- a local helper pattern for deriving a `DecisionRecord` from an `OpenQuestion`,
  chosen value, evidence fingerprint, and cited table. Until the SDK exposes a
  higher-level factory, docs and examples must not fabricate internal-looking
  values such as `blast_radius`, `materiality`, or `agreement_confidence`;
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

`references/datasource.md` must retain Trino-specific guidance that existing
agent tests enforce: `backend_type="trino"`, `catalog`, optional `schema`,
`client_tags`, env-backed credentials such as `user_env`, use of
`database="sales_mart"` when inspecting or reading tables, `backend.list_tables`
over `backend.list_schemas`, and the rule that docs should not use
`catalog.schema.table` as the normal table-access pattern.

`references/authoring-patterns.md` or `references/pitfalls.md` must retain the
Trino VARCHAR datetime guidance: do not cast VARCHAR directly to DATE; parse
through timestamp first.

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

The implementation plan must explicitly update
`tests/test_semantic_agent_tightening.py`. It currently hardcodes old reference
paths (`references/authoring-workflow.md`, `references/evidence.md`) and the old
05-09 example names. Preserve these test invariants under the new file names:

- skill docs point to `mv.datasources.inspect_table(...)`;
- docs distinguish `table.schema()` from metadata/comments;
- Trino datasource and inspection guidance remains present;
- examples cover metadata inspection, readiness missing-preview behavior,
  unverified metric readiness, parity drift, and ambiguous time-axis handling;
- `SKILL.md` stays under the `scripts/run_skill_examples.py` line cap.

The old test expectations for specific reference file names and example numbers
are intentionally dropped.

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

The script must be executable by `scripts/run_skill_examples.py`: it should
scaffold a temporary `.marivo/` project, load or reload it, and print a
non-empty result. It cannot rely on a Marivo source checkout, repo fixtures, or
silent declaration-only behavior.

### `02_candidate_to_questions.py`

Shows the discovery loop:

- load project;
- inspect table metadata;
- call `project.propose_candidates(...)`;
- call `project.open_questions(...)`;
- record a user answer with `project.answer(...)`;
- record a selected decision with `project.record_decision(...)` only through a
  helper that derives the required fields from real `OpenQuestion` and evidence
  values.

The example may use illustrative sample values for actual user answers, but it
must not imply that business meaning can be inferred from names alone.

This example should also cover ambiguous time-axis handling, including the rule
that partition fields are preferred unless business evidence establishes a
different axis.

### `03_closeout_readiness_richness.py`

Shows validation:

- build a backend factory;
- run previews and parity where applicable;
- run `project.audit(...)`;
- run readiness with `require_evidence_ledger=True` and
  `strict_enrichment=True`;
- run richness with `DemandSignal`;
- print readiness blockers separately from richness gaps.

This example should fold in the old readiness-focused coverage:

- missing required preview blocks readiness;
- unverified metrics appear in readiness;
- parity drift blocks readiness;
- richness gaps are advisory and do not change readiness status.

All examples must obey the runner contract in `scripts/run_skill_examples.py`:
every non-underscore `.py` file under `references/examples/` is executed
in-process and must finish within 30 seconds with non-empty stdout. The template
escape hatch is not appropriate for these semantic examples because its required
snippets target analysis-session APIs, not semantic authoring.

## Error Handling

The redesigned skill should route failures by source:

- Load, decorator, or AST errors: fix declaration shape using
  `authoring-patterns.md`.
- Datasource, metadata, or preview errors: use `datasource.md` or `preview.md`;
  do not skip evidence collection silently.
- Open-question blocker: ask the user or collect stronger evidence; do not
  default-guess.
- Incomplete decision record: prefer recording a confirmation with
  `project.answer(...)`, or collect the missing evidence fields before calling
  `project.record_decision(...)`.
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
- `project.propose_candidates(...)` and `project.audit(...)` examples pass
  `inspect_table=mv.datasources.inspect_table`; the docs must not hide this
  injection point.
- The authoring defaults explicitly prefer single `_model.py`, ref variables, and
  partition time fields.
- Deleted references/examples have no dangling links from the skill pack.
- Examples are pip-installed-project oriented and do not depend on repo fixtures.
- Examples are self-contained executable scripts with non-empty stdout.
- `tests/test_semantic_agent_tightening.py` is updated to the new reference and
  example names while preserving the behavior-level coverage listed above.
- Maintainer validation passes with the repo's normal documentation/example
  checks, including `make examples-check` when run from this checkout.

## Migration Notes

This is a rewrite, not a small edit. The implementation plan should update the
skill pack in coherent slices:

1. Replace `SKILL.md` and create the new core references.
2. Rewrite or compress retained references.
3. Replace examples, fold retained behavior coverage into the three new files,
   and remove obsolete files plus the now-unused `references/examples/_fixtures/`
   directory.
4. Update `tests/test_semantic_agent_tightening.py` and any other example
   manifests that reference deleted paths.
5. Run repo validation.
