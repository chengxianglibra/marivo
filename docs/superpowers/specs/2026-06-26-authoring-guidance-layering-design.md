# Authoring Guidance Layering Design

Date: 2026-06-26
Status: Approved design, pending written-spec review
Related: `docs/superpowers/specs/2026-06-25-authoring-discover-design.md`,
`docs/superpowers/specs/2026-06-21-semantic-column-authoring-design.md`,
`marivo/skills/marivo-semantic/SKILL.md`

> Historical note: this spec predates removal of the public semantic
> `prepare_*` authoring stage. Current agents must use
> `help -> discover -> settle/grill -> author -> verify`; remaining
> `prepare_*` text below is historical context only.

## Problem

The current semantic authoring guidance is split across three places without a
clean contract:

- `ms.help(...)` explains some authoring functions and parse constructors, but
  it does not consistently answer "what parameters must I settle before I can
  author this object?"
- `md.discover_*` returns datasource evidence, but its `candidates` and
  `judgment_targets` names imply more semantic selection than discovery can
  safely perform.
- The packaged `marivo-semantic` skill teaches workflow and also repeats API
  details that should belong to help.

This makes agents work harder than necessary and creates drift. A time
dimension is the clearest example: the agent should learn the static
`ms.time_dimension_column(...)` parameter contract from help, learn observed
column facts from `md.discover_time_dimensions(...)`, then ask the user only
for policy or project-context values that evidence cannot decide.

This is a breaking cleanup. No compatibility shims, migration path, or old
output preservation is required.

## Goals

- Make `ms.help(...)` the only static authoring contract surface.
- Make `md.discover_*` return runtime datasource evidence only.
- Make `marivo-semantic` teach the sequence for combining help and discovery,
  without duplicating parameter tables.
- Remove names and result fields that imply Marivo has selected semantic
  candidates or authored-object judgments.
- Keep the design small: no generic parameter framework beyond the help JSON
  needed by agents.

## Non-Goals

- No auto-authoring.
- No semantic recommendations such as `should_author=True`.
- No confidence scores.
- No compatibility for old help JSON shape, old help topics, `candidates`, or
  `judgment_targets`.
- No new public "authoring requirements" DTO in datasource discovery.
- No copy of the same constructor parameter table in skill docs.

## Layer Contract

### Help: Static Authoring Contract

`ms.help(...)` owns the static authoring contract. For each agent-facing
constructor or object kind, help must answer:

- which constructor to call;
- which parameters are required;
- which parameters are optional;
- parameter types, allowed values, defaults, and omit rules;
- static cross-parameter constraints;
- which nested parameter shapes exist.

The JSON shape may change. The new descriptor may add a compact
`authoring_contract` object under `content`; callers should treat that as the
canonical machine-readable contract for API facts and static constraints.

For `ms.time_dimension_column`, help must inline the parse decision. Agents
should not need separate parse-constructor help pages to know how to author a
time dimension.

Required time-dimension contract:

```text
constructor: ms.time_dimension_column
required: name, entity, column, granularity
optional: parse, is_default, domain, ai_context

parse:
- native date column: omit parse unless project policy needs explicit metadata
- native datetime column: parse=ms.datetime(timezone=?, sample_interval=?)
- native timestamp column: parse=ms.timestamp(timezone=?, sample_interval=?)
- string/integer date-like column: parse=ms.strptime(format, timezone=?, sample_interval=?)
- hour-only column: parse=ms.hour_prefix(prefix, sample_interval=?)
```

The contract must describe what each parameter means and when each parse shape
is syntactically valid. It must not declare a fixed source for parameter values.
An agent can use discovery evidence, registry facts, project docs, prior
decisions, or user answers to choose values in the current context.

The standalone help topics below are deleted as agent-facing help entries:

```text
ms.help("datetime")
ms.help("timestamp")
ms.help("strptime")
ms.help("hour_prefix")
```

The constructors remain public authoring functions. They are just no longer
separate help topics. Their usage appears inside `ms.help("time_dimension")`
and `ms.help("time_dimension_column")` where the authoring decision is made.

### Discover: Runtime Evidence

`md.discover_*` owns runtime datasource evidence. It should not expose static
authoring parameter tables and should not imply semantic selection.

Remove `judgment_targets` from discovery result objects and rendering. The
questions about which parameters must be settled belong to `ms.help(...)`.
Discovery may still expose signals and issues, but they must be evidence
statements, not authoring-field checklists.

Remove or rename `candidates` anywhere discovery cannot genuinely select a
semantic candidate. The preferred replacements are specific evidence names:

- entity discovery: source/table evidence fields, primary-key evidence, column
  profiles, time-like columns;
- dimension discovery: `columns`;
- time-dimension discovery: `columns`;
- measure discovery: `columns`;
- relationship discovery: `evidence`;
- dimension-value discovery: `values`.

For example:

```python
time = md.discover_time_dimensions(warehouse, md.table("orders"), columns=("dt",))
time.columns[0].profile
time.columns[0].detected_formats
time.columns[0].issues
```

This says "these are profiled columns and their evidence", not "these are
semantic objects Marivo recommends authoring".

### Skill: Workflow Only

`marivo-semantic` teaches the workflow and routing:

1. Read `ms.help("<constructor-or-object>")` for the static authoring contract.
2. Run the matching `md.discover_*` call for datasource evidence.
3. Use discovery evidence, registry facts, project docs, prior decisions, and
   user answers to settle the constructor parameters.
4. Ask the user only when the needed parameter value cannot be discovered or
   inferred from project context.
5. Call the matching `ms.prepare_*` API for readiness.
6. Author one object.
7. Run `ms.verify_object(...)`.

The skill should not copy constructor parameter tables or parse-constructor
details. It may show short examples, but examples should demonstrate the
sequence, not become another contract source.

### Prepare: Readiness Only

`ms.prepare_*` remains the semantic readiness surface. It can use datasource
inspection internally, but it does not own static help contracts or datasource
discovery evidence vocabulary. Prepare briefs should stay focused on blockers,
matches, registry/project state, and readiness status.

## Expected Surface Changes

- `ms.help(...)` prints text and should expose authoring-contract detail for
  authoring constructors.
- `ms.help("time_dimension_column")` becomes the canonical time-dimension
  authoring page.
- `ms.help("datetime")`, `ms.help("timestamp")`, `ms.help("strptime")`, and
  `ms.help("hour_prefix")` are no longer valid help topics.
- Discovery results no longer expose `.judgment_targets`.
- Discovery results no longer expose `.candidates` when the result only
  contains profiled evidence subjects.
- Skill references no longer mention `judgment_targets` or standalone parse
  help topics.

## Agent Flow Example

For a physical date column:

```python
contract_text = ms.help_text("time_dimension_column")
evidence = md.discover_time_dimensions(
    warehouse,
    md.table("orders"),
    columns=("dt",),
    scope=md.latest_partition(),
)
```

The agent reads the help contract to know that `name`, `entity`, `column`, and
`granularity` must be settled, and that `parse` may be omitted or set through a
parse constructor. It reads discovery evidence to learn that `dt` is a profiled
column and that sampled values match `"%Y%m%d"`. It can propose
`parse=ms.strptime("%Y%m%d")`, then ask the user only for values that remain
unsettled after checking datasource evidence, registry facts, project docs, and
prior decisions.

## Verification

- Tests assert deleted parse help topics are absent.
- Tests assert `time_dimension_column` help contains the full parse decision.
- Tests assert discovery results do not expose `judgment_targets`.
- Tests assert dimension/time/measure discovery use evidence-subject names such
  as `columns`, not `candidates`.
- Skill tests assert the workflow starts with help, then discovery, then
  prepare, author, verify.
