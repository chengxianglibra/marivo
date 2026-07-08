# Semantic Authoring Public Guidance Design

Date: 2026-07-08
Status: approved design, pending written-spec review
Related:
`agent-guide.md`,
`marivo/skills/marivo-semantic/SKILL.md`,
`docs/superpowers/specs/2026-06-25-authoring-discover-design.md`,
`docs/superpowers/specs/2026-06-27-semantic-skill-layering-simplification-design.md`

## Problem

A recent unguided semantic-layer build over a ClickHouse CDN table exposed a
gap in Marivo's public agent-facing surface. The agent was explicitly told not
to use the packaged `marivo-semantic` skill. Starting from `marivo init` and the
installed Python package, it eventually produced a loadable semantic layer, but
the route was not library-led:

- `marivo --help` did not point to datasource or semantic authoring.
- The agent read `marivo/semantic/authoring.py`, `marivo/datasource/backends.py`,
  `__init__.py`, tests, and fixtures to infer public APIs.
- It used direct `clickhouse_connect` SQL to locate tables and inspect schema
  before using Marivo discovery.
- It guessed nonexistent or wrong public APIs such as `md.ai_context(...)`,
  `SecretStore`, and `catalog.query(...)`.
- It hit load and readiness errors whose fixes were discoverable only by
  reading source or retrying.
- It authored many semantic objects before using readiness as the first serious
  validation gate.

The library already has many of the required primitives: `md.help(...)`,
`ms.help(...)`, `md.inspect_*`, `md.discover_*`, `ms.verify_object(...)`,
`ms.readiness(...)`, `catalog.preview(...)`, and structured result rendering.
The missing piece is a continuous, progressively disclosed public route from
the CLI and top-level help surfaces through datasource authoring, evidence
collection, semantic object authoring, verification, readiness, and analysis
handoff.

## Goal

Make Marivo's public interface guide an agent from `marivo --help` to a
complete semantic authoring workflow without relying on packaged skills,
source-code spelunking, or private APIs.

The desired route is:

```text
marivo --help
  -> md.help("authoring")
  -> datasource declaration / registration / test
  -> md.inspect_* and md.discover_* evidence
  -> ms.help("authoring") and ms.help("<semantic-object>")
  -> author one object
  -> ms.verify_object(ref)
  -> ms.readiness(refs=...)
  -> catalog.preview(...) or marivo.analysis handoff
```

This design is intentionally incremental. It adds public guidance, help topics,
examples, next-step affordances, and fix hints around existing APIs. It does
not introduce an authoring wizard or a planner.

## Library And Skill Boundary

Marivo owns stable, testable facts and contracts:

- CLI routing to the Python authoring surfaces;
- static signatures, required parameters, defaults, omit rules, constraints,
  examples, and common mistakes in `md.help(...)` and `ms.help(...)`;
- datasource evidence from `md.inspect_*`, `md.discover_*`, and
  `md.raw_sql(..., reason=...)`;
- current catalog state from `ms.load()`, `catalog.list(...)`,
  `catalog.get(...).details()`, and `catalog.preview(...)`;
- structured validation, blockers, warnings, fix hints, and next-step
  affordances from load errors, `ms.verify_object(...)`, and
  `ms.readiness(...)`.

The `marivo-semantic` skill owns operating discipline:

- mapping a user's request into a narrow active batch;
- inspecting evidence before asking the user questions;
- asking exactly one unresolved semantic decision per grill turn;
- deciding when user intent, business policy, project docs, or prior answers
  settle a value;
- editing model files and rerunning verification;
- choosing when to hand ready refs to `marivo-analysis`.

The library should make the correct route discoverable for unskilled agents.
The skill should make a skilled agent follow that route with better judgment.
The skill must not duplicate constructor parameter tables, discovery schemas,
backend catalogs, or public error catalogs.

## Disclosure Model

The public guidance should use four levels of progressive disclosure.

### Index Level

Entrypoints:

- `marivo --help`
- `md.help()`
- `ms.help()`

This level answers "which door should I open?" It should list the authoring
workflow topics without embedding a full tutorial:

- `md.help("authoring")`
- `md.help("<backend>")`, such as `md.help("clickhouse")`
- `ms.help("authoring")`
- `ms.help("<semantic-object>")`, such as `ms.help("measure_column")`
- `ms.help("readiness")`

### Workflow Level

Entrypoints:

- `md.help("authoring")`
- `ms.help("authoring")`

This level answers "what order should I follow?" It should be short,
stage-based, and copyable. It should point at the next public API rather than
transcribing every parameter.

Datasource authoring stages:

1. Choose or declare a datasource with `md.help("<backend>")`.
2. Persist it with `md.register(spec)` or a file under `models/datasources/`.
3. Provide secrets through `*_env` references, not plaintext literals.
4. Run `md.test(ref)` before semantic authoring.
5. Inspect physical facts with `md.inspect_table(...)` and
   `md.inspect_partitions(...)`.
6. Run the matching `md.discover_*` call for semantic evidence.
7. Use `md.raw_sql(..., reason=...)` only for bounded diagnostics that
   inspection/discovery cannot express.

Semantic authoring stages:

1. Load current state with `ms.load()` and browse with `catalog.list(...)`.
2. Read `ms.help("<object>")` before authoring each object.
3. Use matching `md.discover_*` evidence and project/user context to settle
   constructor values.
4. Author exactly one semantic object.
5. Run `ms.verify_object(ref)` and fix failures before advancing.
6. Close out with `ms.readiness(refs=...)`.
7. Use `catalog.preview(...)` for runtime smoke checks.
8. Hand ready refs to `marivo.analysis`; do not guess a `catalog.query(...)`
   API.

### Contract Level

Entrypoints:

- `md.help("clickhouse")`, `md.help("trino")`, etc.
- `md.help("register")`, `md.help("test")`, `md.help("raw_sql")`
- `ms.help("entity")`, `ms.help("measure_column")`,
  `ms.help("time_dimension_column")`, `ms.help("aggregate")`, etc.

This level answers "how do I call this exact public API?" It should contain:

- signature;
- required and optional parameters;
- defaults and omit rules;
- constraints;
- matching discovery call;
- minimal runnable example;
- common mistakes and recovery hints.

Constructor help remains the source of truth for static authoring contracts.
Skill docs and docs-site guides should point to it rather than copying the
same tables.

### State Level

Entrypoints:

- result `.show()` / `.render()`;
- load errors;
- `VerifyResult.show()`;
- `ReadinessReport.show()`.

This level answers "what should I do next from the state I have?" It should
derive next steps from the current result without becoming a planner.

Examples:

- `md.inspect_table(...).show()` detects partition metadata and points to
  `md.inspect_partitions(...)` and `md.partition({...})` when relevant.
- `md.discover_measures(...).show()` points to `ms.help("measure_column")`,
  lists unresolved decisions such as measure name, unit, additivity, and
  `ai_context.business_definition`, then says to author one measure and run
  `ms.verify_object(ref)`.
- `ms.verify_object(ref).show()` distinguishes "repair this object" from
  "continue the batch or run readiness".
- `ms.readiness(...).show()` groups blockers and warnings, includes fix hints,
  and makes `ready_with_warnings` explicit as a handoff decision rather than a
  silent success.

State-level guidance may say "decide additivity" or "ask the user if evidence
is insufficient." It must not recommend business values with confidence scores.

## Public Surface Changes

### CLI Help

`marivo --help` keeps the existing command set:

- `init`
- `publish`
- `doctor`

It should add a semantic-authoring routing block:

```text
Semantic authoring workflow:
  .venv/bin/python -c "import marivo.datasource as md; md.help('authoring')"
  .venv/bin/python -c "import marivo.semantic as ms; ms.help('authoring')"

Common diagnostics:
  marivo doctor
  marivo doctor --semantic
  marivo doctor --datasource <name> --connect
```

This makes the absence of a semantic-authoring CLI command intentional: Marivo
authoring remains Python-native, and the CLI routes agents to the Python API.

### `md.help("authoring")`

Add a datasource authoring workflow topic. It should cover:

- import shape: `import marivo.datasource as md`;
- how to choose backend help with `md.help("<backend>")`;
- declaring a typed spec;
- persisting it with `md.register(spec)` or a model file;
- `*_env` secret references and the environment/secret-cache resolution model;
- `md.test(ref)` as the explicit live datasource round trip;
- `md.inspect_table(...)` and `md.inspect_partitions(...)` as physical fact
  helpers;
- `md.discover_entity`, `md.discover_dimensions`,
  `md.discover_time_dimensions`, `md.discover_measures`,
  `md.discover_relationship`, and `md.discover_dimension_values`;
- `md.raw_sql(..., reason=...)` as a bounded diagnostic escape hatch;
- the handoff to `ms.help("authoring")`.

It should explicitly say not to import internal secret classes or backend
builders.

### Backend Help Examples

Backend topics such as `md.help("clickhouse")` should keep their signature and
field descriptions, but their example should show the whole common path:

```python
import marivo.datasource as md

spec = md.clickhouse(
    name="warehouse",
    host="clickhouse.example",
    database="analytics",
    user_env="WAREHOUSE_USER",
    password_env="WAREHOUSE_PASSWORD",
)
md.register(spec)
md.test(spec.ref).show()
md.inspect_table(spec.ref, md.table("orders", database="analytics")).show()
```

Examples must not include plaintext secrets.

### `md.help("ai_context")`

Because datasource specs accept `ai_context=...` but `ai_context` is defined in
`marivo.semantic`, `md.help("ai_context")` should be an alias-like help topic.
It should say:

- construct values with `ms.ai_context(...)`;
- accepted fields are `business_definition`, `guardrails`, `synonyms`,
  `examples`, `instructions`, and `owner_notes`;
- raw dicts, `summary=`, and `glossary=` are invalid in the current API;
- see `ms.help("ai_context")` for the canonical contract.

This avoids the common mistake of guessing `md.ai_context(...)`.

### `ms.help("authoring")`

Add a semantic authoring workflow topic. It should cover:

- import shape: `import marivo.semantic as ms`;
- current catalog browse commands;
- object authoring order:

```text
domain -> entity -> dimension/time_dimension/measure
       -> metric -> relationship -> cross-entity/derived metric
```

- the rule to read `ms.help("<object>")` before each object;
- the matching datasource discovery call for each object family;
- the one-object-then-verify loop;
- closeout with `ms.readiness(refs=...)`;
- runtime smoke checks with `catalog.preview(...)`;
- analysis handoff through `marivo.analysis` sessions.

This topic should not duplicate constructor parameter tables. It should route
to `ms.help("entity")`, `ms.help("measure_column")`, and related topics for
contract details.

### Semantic Constructor Workflow Lines

Each semantic constructor help topic already has a workflow section. Keep that
section short but make it consistently state:

1. Read this contract.
2. Run the matching `md.discover_*` call when datasource evidence is needed.
3. Settle values from evidence, catalog state, project docs, and user answers.
4. Author one object.
5. Run `ms.verify_object(ref)`.

The workflow line should not mention any removed public `prepare_*` stage.

### Result Next-Step Affordances

Public result renderers should add bounded "Suggested next calls" or
"Available next steps" sections where useful.

Required surfaces:

- `DatasourceResult` from `inspect_*`, `discover_*`, and `raw_sql`;
- `VerifyResult`;
- `ReadinessReport`;
- semantic load errors where the recovery path is common and public;
- `SemanticObjectList` and `SemanticObjectDetails` when they can point to
  drill-down or readiness/preview checks.

The next-step text should be deterministic and state-derived. It should not
rank business choices or infer semantic meaning.

## Gap Mapping

### CLI Entry Gap

Observed problem: after `marivo init`, the agent had no public route into
semantic authoring and moved to source-code inspection.

Design response: add semantic authoring routing to `marivo --help`, and add
`md.help("authoring")` / `ms.help("authoring")`.

### Source-Code Spelunking For Datasource Contracts

Observed problem: the agent read datasource internals to learn ClickHouse
construction, backend support, file layout, and secret handling.

Design response: backend help examples show the full typed spec ->
register/test/inspect chain; `md.help("authoring")` explains file layout and
public inspection/discovery entry points.

### Secret Handling Guesswork

Observed problem: the agent guessed a nonexistent `SecretStore`, then imported
internal `LocalPlaintextCache`.

Design response: `md.help("authoring")`, `md.help("test")`, and doctor fix
snippets explain the public rule: model files contain only `*_env` references;
users provide environment variables; `md.test(...)` can cache env-sourced
secrets after validation. Internal secret classes remain private and should not
be shown as authoring APIs.

### `ai_context` Shape Drift

Observed problem: the agent wrote `md.ai_context(summary=..., glossary=...)`
and `ms.ai_context(summary=..., glossary=...)`, then fixed it only after load
errors and source inspection.

Design response: `md.help("ai_context")` points to `ms.ai_context(...)` and
names the accepted fields. Load errors for unexpected `ai_context` keywords
should include the canonical constructor form.

### Table And Schema Discovery Bypassing Marivo

Observed problem: the agent used direct ClickHouse SQL to find the real table
and inspect columns.

Design response: `md.help("authoring")` routes table inspection through
`md.inspect_table(...)`; failed inspection and raw SQL result text may show a
bounded diagnostic fallback using `md.raw_sql(..., reason=...)`. This
incremental design does not add a new table search API.

### Discovery-To-Authoring Handoff Gap

Observed problem: discovery evidence did not naturally lead to semantic object
authoring, so the agent wrote a large model in one pass.

Design response: `md.discover_*().show()` includes an "Authoring handoff"
section with the matching `ms.help(...)` topic, unresolved semantic decisions,
and the "author one object then verify" rule.

### Batch Authoring Before Verification

Observed problem: the agent authored a full domain before serious validation.

Design response: `ms.help("authoring")`, semantic constructor workflow lines,
and `VerifyResult.show()` all reinforce one object followed by
`ms.verify_object(ref)`.

### Readiness Repair Gap

Observed problem: readiness found missing `business_definition` blockers, but
the agent had to infer the batch repair pattern.

Design response: `ReadinessReport.show()` includes fix hints for common
blockers:

- `missing_business_definition`: add
  `ai_context=ms.ai_context(business_definition=...)`;
- `unknown_ref`: browse with `catalog.list(...).show()` or inspect a known ref
  with `catalog.get(...).details().show()`;
- `sql_parity_unverified`: run `ms.parity_check(...)` when parity matters, or
  report the warning as non-blocking when analysis handoff allows it.

### Catalog Query Misunderstanding

Observed problem: the agent guessed `catalog.query(...)` after loading the
semantic catalog.

Design response: `ms.help("authoring")` and relevant errors clarify that the
semantic catalog supports browse, preview, readiness, and verification.
Metric analysis runs through `marivo.analysis` sessions.

### Semantic Risk Without Structural Failure

Observed problem: additive ratio and average metrics can pass structural
readiness while remaining semantically suspicious.

Design response: constructor help and relevant results include decision
prompts, not recommendations:

- ratio and average metrics require explicit additivity review;
- time dimensions require timezone/default-axis policy review;
- capacity, count, and snapshot-like measures require grain-aware additivity
  review.

The library should surface these as warnings or authoring reminders. It should
not choose the business answer.

## Non-Goals

- Do not add `marivo author ...`, `marivo semantic ...`, or another CLI wizard.
- Do not add a planner that writes semantic files.
- Do not make `md.discover_*` choose business semantics, confidence scores, or
  recommendations.
- Do not reintroduce a public `prepare_*` semantic authoring stage.
- Do not create broad optional-field mega-results for authoring state.
- Do not make `md.raw_sql(...)` the primary schema discovery path.
- Do not expose internal secret-store classes as public authoring APIs.
- Do not change analysis operators as part of this work.

## Testing And Acceptance Criteria

### CLI

- `marivo --help` snapshot includes semantic authoring workflow routing.
- The help text still lists only the existing CLI commands unless a separate
  design explicitly adds commands.

### Datasource Help

- `md.help()` lists `authoring`.
- `md.help("authoring")` renders the datasource stages and handoff to
  `ms.help("authoring")`.
- Backend help examples include `md.register(spec)`, `md.test(spec.ref)`, and
  an inspection call.
- `md.help("ai_context")` resolves and points to `ms.ai_context(...)`.

### Semantic Help

- `ms.help()` lists `authoring`.
- `ms.help("authoring")` renders the semantic authoring stages, one-object
  verify loop, readiness closeout, and analysis handoff.
- Constructor help topics continue to show signatures and the short workflow,
  without mentioning a public `prepare_*` stage.

### Result Rendering

- Discovery result render tests assert the presence of an authoring handoff for
  entity, dimension, time dimension, measure, and relationship discovery.
- `VerifyResult.render()` includes a pass/fail next-step section.
- `ReadinessReport.render()` includes fix hints for common blocker and warning
  kinds.
- Common semantic load errors include public recovery hints for `ai_context`
  shape errors and missing domain files.

### Docs And Skill Alignment

- `marivo/skills/marivo-semantic/SKILL.md` remains workflow-only and routes API
  detail to `md.help(...)`, `ms.help(...)`, discovery results, verify results,
  and readiness reports.
- Docs-site quick-start and semantic-layer pages do not show stale raw dict
  `ai_context` examples if the live API requires `ms.ai_context(...)`.
- No docs teach internal secret classes or source inspection as normal
  authoring workflow.

## Rollout Plan

### Phase 1: Entrypoints And Workflow Topics

- Update `marivo --help` epilog.
- Add `md.help("authoring")`.
- Add `ms.help("authoring")`.
- Add or update focused help snapshot tests.

### Phase 2: Contract Examples And Aliases

- Expand backend help examples to show register/test/inspect.
- Add `md.help("ai_context")`.
- Sync docs and skill references with the new authoring topics.

### Phase 3: State-Level Guidance

- Add deterministic next-step affordances to discovery results,
  `VerifyResult`, and `ReadinessReport`.
- Add common load-error fix hints for the observed high-frequency mistakes.
- Add render tests to keep the state-level affordances from regressing.

The phases can be implemented in one branch, but each phase should be
testable independently.
