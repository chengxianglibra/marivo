---
name: marivo-semantic
description: Use for Marivo datasource declaration, physical source inspection, evidence acquisition, new or changed semantic objects, semantic verification/preview/readiness repair, or an analysis handoff that reports a genuinely missing business object.
---

# marivo-semantic

## Trigger

Use this skill when the task involves:

- datasource declaration or validation for semantic authoring;
- physical source inspection or evidence acquisition;
- new or changed semantic objects;
- semantic verification, preview, or readiness repair;
- an analysis handoff that reports a genuinely missing business object.

Metric-centered investigation over already-ready refs remains owned by
`marivo-analysis`. Do not keep this skill active merely because an analysis
uses semantic objects.

## Ownership and authority

This skill is a **boundary state-router**, not a manual, tutorial, constructor
catalog, or authoring planner. It preserves the semantic track's real partial
order and its human-judgment boundary. It tells a general-purpose coding agent
how to resume from current live state, preserve datasource evidence, author one
explicit object, validate that object through every required stage, stop for one
unresolved business decision, and hand only ready refs to analysis.

An environment-verified live Marivo surface outranks any cached knowledge in
this file. Unverified `PATH` output does not. If the installed Marivo package,
its Python interpreter, or its help surface cannot be confirmed before
authoring begins, the skill stops and requests environment repair rather than
guessing.

Authority is divided explicitly:

- live Marivo owns mechanical contracts and current state;
- the skill owns ordering, safety, evidence, and handoff policy;
- the agent owns technical interpretation and explicit Python drafting;
- the user or business owner owns unresolved business meaning.

## Live-contract rule

Use the same project interpreter for discovery and execution. Start with
`<selected-python> -m marivo help semantic` (or the corresponding
`<venv>/bin/marivo help semantic`), verify the rendered Marivo version,
resolved Python executable, and package path, then follow focused live help
topics for every API contract.

A bare `marivo` resolved from `PATH` is not authoritative unless its rendered
fingerprint matches the interpreter and package used for execution. If
authoritative discovery and execution fingerprints differ, repair or stop
before opening a datasource connection, reading user data, mutating project or
user state, authoring semantic files, or handing refs to analysis.

After entry:

- live `md.help(...)` owns datasource contracts, inspection, scope, snapshot,
  and diagnostic mechanics;
- live `ms.help(...)` owns semantic constructor parameters, constraints, and
  examples;
- result `.show()` output, `.contract()`, and structured errors own
  state-specific next calls and typed repair;
- the agent owns evidence-based drafting and technical handling, including
  uncommon physical formats;
- the user or business owner owns unresolved business-semantic decisions and
  approves metric meaning before analysis handoff.

The skill does not enumerate any API details, signatures, parameter tables,
backend catalogs, result fields, error kinds, or exact repair calls.

## Canonical route

The routing loop is conceptual, resume-first, and stable. An agent enters at
the current state and continues from the earliest unsatisfied required
boundary for one object. It does not restart the whole authoring tutorial or
repeat safe work.

```text
help/browse -> inspect -> explicit scope -> sample once -> project evidence -> settle/grill -> author one Python object -> load typed object -> static verify -> scoped preview -> readiness -> analysis
```

Each step names the live help target that owns its exact mechanics — never a
signature, transition call, or parameter table from this file:

1. **help/browse** — open `md.help("authoring")` and `ms.help("authoring")`,
   then browse existing objects through the live catalog's typed collections.
2. **inspect** — inspect the source via the live datasource inspection
   capability; read physical extent, partition state, schema, and execution
   capabilities before any user-data query.
3. **explicit scope** — choose the live-declared scope capability; every
   user-data read requires positive row and timeout guards.
4. **sample once** — acquire one snapshot for the active object/batch.
5. **project evidence** — reuse query-free snapshot projections (entity,
   dimensions, values, time dimensions, measures, relationships) without
   reacquiring data.
6. **settle/grill** — settle mechanically discoverable facts first; if exactly
   one business decision remains, ask one evidence-grounded question and stop.
7. **author one Python object** — write exactly one explicit object.
8. **load typed object** — reload the catalog and navigate to that exact typed
   object.
9. **static verify** — run the registered static verification capability.
10. **scoped preview** — run the registered scoped preview capability, reusing
    the active snapshot.
11. **readiness** — run the registered readiness capability; follow typed
    repair for any blocker.
12. **analysis** — hand off only live-marked ready refs through the analysis
    boundary.

The router prefers object-near live guidance in this order: current structured
error repair; current object/result `.contract()`; focused help for the target
capability; root help only when no canonical target is known. It never
substitutes memorized API syntax when the live surface is available.
Mechanical availability is not policy permission: the agent must complete and
read static verification before choosing preview even when focused help shows
preview is callable from a loaded object.

### Durable partial order

The skill preserves these invariant policy edges:

```text
matching environment before any trusted operation
current catalog/project browse before mutation
physical evidence before evidence-backed semantic drafting
explicit scope before any user-data read
dependency before dependent
one authored object before its validation cycle
static verification before required runtime preview
required runtime preview before readiness
readiness before analysis handoff
```

The live surface declares which object families require static-only,
single-snapshot, snapshot-mapping, or future preview modes. The skill does not
hardcode object-family exceptions. Verify-before-preview is always a skill-owned
policy edge for preview-required families; the runtime does not mechanically
enforce that ordering and no persisted verification token is expected.

### One-object loop

The unit of mutation and validation is one explicit semantic object. For each
object, the agent:

- identifies the exact existing dependency and evidence context;
- settles all mechanically discoverable facts first;
- stops for one unresolved business decision when required;
- edits one explicit Python object;
- reloads and locates that exact typed object;
- follows every registered verify, preview, and readiness prerequisite;
- repairs and revalidates the same object before advancing.

The skill forbids authoring a domain-sized batch followed by deferred
validation. Relationship or other inherently multi-entity objects may consume
multiple evidence inputs, but the authored unit remains one object.

## Evidence and judgment protocol

### Evidence collection order

Before asking a user or drafting business meaning, the agent checks every
relevant source already available in scope:

1. current live constructor and constraint contract for mechanical legality;
2. prior explicit user or accountable business-owner decisions;
3. existing approved catalog and project definitions;
4. source comments, provenance, and project documentation;
5. current datasource inspection and exact snapshot observations.

This is a collection order, not an API sequence and not a rule that later
physical evidence overrides earlier business authority. The agent exhausts
relevant available evidence before asking a question so it can detect conflicts
and avoid asking for observable facts.

### Authority precedence

Different sources answer different questions:

1. the live contract is authoritative for mechanical validity only;
2. an explicit accountable business-owner decision is authoritative for
   business meaning;
3. an existing approved project definition is adopted semantic context unless a
   newer accountable decision changes it;
4. source comments, provenance, and project documentation are supporting
   business evidence whose authority must be identifiable;
5. inspection and snapshot results are authoritative only for the physical
   observations they actually measured.

When sources conflict, the agent does not silently select the highest-looking
technical source or overwrite an existing business decision. It names the
conflict in the one-question grill stop and requests accountable resolution.

### Business decision boundary

The user or accountable business owner decides unresolved questions such as
whether observed uniqueness is an authoritative identity or key; metric
meaning, numerator, denominator, failure handling, and scope; aggregation,
unit, additivity, and time attribution; relationship business meaning and
cardinality promise; uncommon date/epoch/timezone interpretation not established
by source facts; and which lifecycle, event, state, or business concept a
future object represents. The live surface may name the unresolved judgment
target and show evidence. It must not supply a guessed value.

### One-question grill stop

If exactly one business decision remains after the evidence-collection and
authority passes, the agent:

1. names one object and one unresolved judgment target;
2. summarizes the directly relevant evidence and provenance;
3. explains why that evidence cannot establish business authority;
4. asks exactly one question;
5. stops mutation and validation work for that object until answered.

If several decisions remain, the agent asks only the earliest dependency whose
answer can change later questions. It does not bundle a questionnaire. Options
are allowed only when each option is grounded in supplied evidence, existing
project conventions, or an explicit live closed enum. Plausible but unsupported
options are forbidden.

## Hard boundaries

### Physical and semantic ownership

- Datasource objects describe connectivity and physical sources; semantic
  objects describe governed business meaning.
- The agent never recreates a physical source through an invented semantic
  builder.
- Semantic links use the live surface's typed refs; bare ids are not
  substituted when the current contract requires typed identity.

### Scope and query effects

- Metadata inspection precedes any user-data query.
- Every user-data read has an explicit live-declared scope and required positive
  guards.
- A returned-row limit is not treated as a backend scan bound.
- A potentially unbounded diagnostic remains outside the canonical route and
  requires explicit necessity and effect review.
- The agent never follows a retry string without inspecting the registered
  effect of the target transition.
- `md.raw_sql(...)` is the sole terminal raw SQL execution path — bounded,
  timeout-enforced, and terminal; results cannot re-enter typed analysis.

### Snapshot continuity

- One acquired snapshot supports all query-free projections for an active
  object/batch.
- The agent does not reacquire data merely to obtain another semantic-shaped
  view.
- Snapshot age and expiry remain visible reference metadata; age alone never
  requires reacquisition or invalidates matching preview/readiness evidence.
- Explicit refresh may replace observations. Definition, datasource, source,
  schema, scope, or evidence-identity mismatches still invalidate dependent
  preview/readiness evidence according to the live contract.
- Multi-entity objects use the exact registered evidence mapping; snapshots are
  not silently substituted across entities or sources.

### Privacy, secrets, and persistence

- Credentials remain references, not authored plaintext.
- Any user-global secret cache or project-local plaintext value cache requires
  the live surface's explicit effect and privacy contract.
- Memory-only evidence remains the default unless persistence is knowingly
  accepted for the data in scope.

### Evidence is not meaning

- Observed uniqueness is not an authoritative primary or business key.
- Observed values do not establish exhaustive enum membership or business
  definitions.
- Physical type and shape do not establish unit, aggregation, additivity,
  cardinality, timezone, or temporal policy.
- Evidence projections report observations and judgment targets; they do not
  recommend semantic objects or values.

### Explicit authoring

- The final source of truth is explicit project Python, not a prompt-only
  decision, generated brief, hidden runtime state, or one-off analysis script.
- The agent writes one object and validates it before advancing.
- Advisory richness is not readiness and cannot create a required object by
  itself.

### Diagnostics and parity

- Potentially unbounded raw diagnostics are escape hatches, not normal evidence
  acquisition.
- Provenance parity is used only when the current object and task require it; it
  is not promoted to a universal readiness step by skill prose.
- A diagnostic result cannot override unresolved business meaning.

## Handoffs

The skill follows object-near contract and help for every handoff. Exact type
and field names remain discoverable from the installed live surface; this file
does not reconstruct them from conversation memory.

| Condition | Handoff |
| --- | --- |
| Analysis reports a required business object genuinely does not exist | Activate `marivo-semantic`; author the smallest dependency-closed set, one validated object at a time, then hand off ready refs |
| Semantic readiness exposes an analysis handoff | Activate `marivo-analysis`; follow the semantic-to-analysis handoff's live target; transfer governed inputs exactly as recorded by readiness |
| An unresolved business decision remains | One-question grill stop; stop mutation and validation for that object until answered |
| An unresolved environment fingerprint mismatch | Environment-repair stop; no datasource, semantic, or analysis work proceeds until one authoritative environment is established |

When semantic readiness exposes a non-`None` analysis handoff, the skill
activates `marivo-analysis` and transfers the ready typed refs, fingerprints,
readiness status, and snapshot/preview evidence identity exactly as recorded by
readiness. Analysis consumes those handed-off refs only after its query-free
boundary returns a `SemanticHandoffReceipt`; fingerprint, ref, readiness, or
preview-evidence rejection routes back through typed repair. A semantic-first
task creates or recovers an analysis session before validation; no prior blocked
analysis branch is required. A `ready_with_warnings` payload reports warnings;
it does not prove that the runtime captured user or agent acceptance. Before
transferring that payload, the skill explicitly discloses the warnings and
applies its proceed-or-stop policy.

## Closeout obligations

Successful semantic closeout states only Marivo-specific facts:

- which explicit project object was added or changed;
- which datasource evidence identity and scope grounded it;
- which business decisions were supplied and by whom when known;
- which registered validation stages passed;
- which refs are analysis-ready;
- which warnings or caveats remain;
- which analysis task or branch receives the handoff.

Closeout does not generate a general modeling tutorial, repeat constructor
syntax, or claim that observed data proved business meaning.

A blocked closeout states:

- the exact current object/state;
- the typed blocker or unresolved judgment target;
- whether data was queried or project state was mutated;
- whether evidence remains reusable;
- the one required user or environment action.

## Boundary-violation behavior

The skill does not provide fallback implementations or memorized workarounds.

| Situation | Required behavior |
| --- | --- |
| Help and execution fingerprints differ | Stop before connection, read, mutation, or authoring |
| Proposed call has an unknown effect | Read focused live help; do not invoke it from memory |
| User-data read has no explicit registered scope | Stop before query |
| A matching snapshot already exists, regardless of age | Reuse it; treat timestamps and cache status as reference information only |
| Evidence suggests but cannot establish business meaning | Ask one grounded question and stop |
| A dependency is missing or not ready | Repair/ready the dependency before authoring the dependent |
| Static verification fails | Repair and reverify the same object |
| Required preview is missing or does not match current definitions/dependencies | Run or repair scoped preview before readiness |
| Readiness is blocked | Follow typed repair; do not hand off the blocked ref |
| Analysis needs an absent object | Author the smallest dependency-closed set for that governed requirement, one validated object at a time, then hand off ready refs |
| Runtime transition contradicts its registered input, state, target-surface, or effect facts | Treat candidate as internally inconsistent; stop and report the contract defect |
