# Semantic Skill Layering Simplification Design

Date: 2026-06-27
Status: Approved design, pending written-spec review
Related: `docs/superpowers/specs/2026-06-26-authoring-guidance-layering-design.md`,
`agent-guide.md`, `marivo/skills/marivo-semantic/SKILL.md`

## Problem

The packaged `marivo-semantic` skill still carries too much authoring
knowledge. It explains API patterns, Brief fields, preview behavior, datasource
discovery details, and multiple example tracks that now belong to `ms.help`,
`md.discover_*`, `ms.prepare_*`, or runnable examples.

This conflicts with the repository-wide authoring guidance layering:

- `ms.help(...)` owns static authoring contracts.
- `md.discover_*` owns runtime datasource evidence.
- `ms.prepare_*` and `ms.verify_object(...)` own readiness and validation.
- `marivo-semantic` owns workflow and routing only.

The current skill also has five examples and several reference files that make
it look like the skill is a second API manual. That increases drift and makes
agents more likely to skip the intended `help -> discover -> settle -> prepare
-> author -> verify` loop.

## Goals

- Strongly simplify the packaged `marivo-semantic` skill content.
- Remove reference files whose responsibility belongs to help, discover,
  prepare, verify, or readiness.
- Keep the skill as a workflow contract: how an agent combines help,
  discovery evidence, user agreement, authoring, verification, and closeout.
- Require a per-object grill-me agreement step whenever discovery and project
  evidence do not clearly settle a semantic decision.
- Merge examples into exactly one datasource example and one complete semantic
  model example.
- Keep tests aligned with the new skill surface so deleted files and old
  examples do not remain implicitly required.

## Non-Goals

- No behavior change to Marivo runtime APIs.
- No new public `ms.help`, `md.discover_*`, `ms.prepare_*`, or
  `ms.verify_object` surface.
- No compatibility shim for deleted skill reference files.
- No broad docs-site rewrite.
- No new semantic object constructors or authoring shortcuts.

## Skill Surface

The simplified skill should keep only workflow and routing material.

Keep and compress:

- `marivo/skills/marivo-semantic/SKILL.md`
- `references/workflow.md`
- `references/datasource.md`
- `references/closeout.md`
- `references/pitfalls.md`
- `references/examples/01_datasource.py`
- `references/examples/02_semantic_model.py`

Delete:

- `references/authoring-patterns.md`
- `references/object-briefs.md`
- `references/preview.md`
- `references/evidence-and-ledger.md`
- the current five example files under `references/examples/`

The deleted content should not be recreated under new names. If the
implementation needs a short reminder, it should point to the owning public
surface instead:

- Static parameters, required fields, defaults, omit rules, parse decisions:
  `ms.help(...)`
- Datasource evidence, profiles, signals, issues, detected formats,
  relationship evidence, and current values: `md.discover_*`
- Brief and verify result field contracts: `ms.help("<Brief-or-result>")`
- Blockers, matches, readiness, and verify status: `ms.prepare_*`,
  `ms.verify_object(...)`, and `ms.readiness(...)`
- Diagnostic SQL: `md.raw_sql(...)` with a required reason

## Workflow Contract

`SKILL.md` and `references/workflow.md` should teach one loop for every
datasource-backed semantic object:

```text
ms.help(...) static contract
  -> md.discover_* datasource evidence
  -> settle from evidence, registry, project docs, and prior decisions
  -> grill the user for unresolved semantic decisions
  -> ms.prepare_* readiness
  -> author exactly one semantic object
  -> ms.verify_object(...)
```

The loop must stay one object at a time. Agents must not batch several object
edits before verification.

The ladder remains:

```text
domain -> entity -> dimension -> time_dimension -> measure -> metric
       -> relationship -> cross-entity metric -> derived metric
```

Datasource registration is a prerequisite, not a semantic ladder rung.

## Grill-Me Gate

Every semantic object has an explicit authoring gate before code is written.

Before authoring, the agent must inspect:

- the relevant `ms.help(...)` static contract;
- the matching `md.discover_*` evidence;
- current registry/catalog state;
- project docs and source SQL/provenance when present;
- prior decision-ledger entries or prior user answers;
- the matching `ms.prepare_*` result.

If all constructor values and semantic meanings are clearly settled by those
sources, the agent may author the object and state the evidence basis.

If any semantic choice remains unresolved, the agent must grill the user before
authoring:

- Ask one unresolved semantic decision at a time.
- Ask only about semantic intent, business policy, or ambiguity that cannot be
  resolved from evidence.
- Do not ask for datasource facts Marivo can discover, such as schema, column
  names, data types, sample values, join-key viability, or existing refs.
- Do not invent multiple-choice options. Each option must be grounded in a
  metadata comment, column profile, sample distribution, existing semantic
  object, source SQL, project doc, or prior decision.
- Put the strongest evidence-backed answer first when an option list is
  justified.
- If evidence does not support a finite option list, ask an open clarification
  instead of fabricating choices.
- Author only after the user confirms the unresolved decision. If the decision
  cannot be resolved, record abandonment instead of writing a speculative
  semantic object.

## Examples

The skill should have exactly two runnable examples.

### `01_datasource.py`

This example creates a small temporary DuckDB datasource and demonstrates the
datasource prerequisite flow:

- declare or register a datasource;
- bind it with `md.ref(...)`;
- run a concrete connectivity or test step;
- call `md.help(...)` and at least one `md.discover_*` function to show where
  datasource evidence comes from.

It must not duplicate datasource parameter tables. It should be a proof that a
datasource is ready for semantic authoring, not a full datasource API manual.

### `02_semantic_model.py`

This example creates a complete semantic model over a small sales-style
DuckDB dataset. It must cover all semantic object categories in one coherent
domain:

- domain;
- entity;
- dimension;
- time dimension;
- measure;
- tier-1 aggregate metric;
- relationship;
- cross-entity metric;
- derived metrics.

The example should verify objects in dependency order and show the final model
loads. It should demonstrate final code shape and the verification sequence,
not explain constructor parameter details that belong to `ms.help(...)`.

## Pitfalls Scope

`references/pitfalls.md` should keep only workflow-level failure modes:

- skipping `ms.help(...)`;
- skipping `md.discover_*`;
- asking users for discoverable datasource facts;
- inventing grill-me options;
- writing multiple objects before verification;
- advancing past a failed `ms.verify_object(...)`;
- using `md.raw_sql(...)` as an authoring body;
- handing off to analysis before `ms.readiness(...)`.

API-specific parse recipes, backend catalogs, Trino casting notes, and Brief
field details should be removed from this skill unless they are expressed as a
short pointer to `ms.help(...)`, `md.help(...)`, or runtime errors.

## Tests

Update the semantic skill tests to assert the new boundary.

`tests/test_semantic_agent_tightening.py` should:

- expect exactly `01_datasource.py` and `02_semantic_model.py`;
- assert the skill teaches the loop through help, discovery, grill/settle,
  prepare, author, verify, and readiness;
- assert deleted reference files are absent;
- assert the skill does not mention removed discovery concepts such as
  `judgment_targets`;
- assert legacy inspect/probe habits do not re-enter the workflow guidance.

`tests/test_run_skill_examples.py` should:

- keep executing the examples through `make examples-check`;
- assert `02_semantic_model.py` covers measure-first aggregate metrics,
  relationships, cross-entity metrics, derived metrics, and `verify_object`.

`tests/test_skill_surface_discipline.py` should keep guarding against skill
markdown that transcribes public dataclass field tables or error catalogs.

## Verification

The implementation should finish with:

```bash
make test TESTS='tests/test_semantic_agent_tightening.py tests/test_run_skill_examples.py tests/test_skill_surface_discipline.py'
make examples-check
make lint
git diff --check
```

If example execution fails because the examples surface a real runtime drift,
fix the example or the test expectation within this simplification scope. Do
not widen the task into unrelated runtime API changes.
