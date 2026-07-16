# Marivo Semantic Boundary State-Router Design

Status: implemented; retained as a historical design record

Date: 2026-07-13

## Summary

> **2026-07-16 simplification amendment:** Semantic readiness now exposes
> `ReadinessReport.analysis_ready_refs` directly. Missing semantic objects use
> `AnalysisRepair(kind="semantic_authoring")`. No directional payload,
> validation result, or analysis-side readiness boundary exists. Any older
> transfer/result language below is historical and non-normative.

Redesign the packaged `marivo-semantic` skill as a minimal boundary and
state-routing policy for datasource setup and semantic-layer authoring.

The target skill retains semantic authoring's real partial order and its
human-judgment boundary. It tells a general-purpose coding agent how to resume
from current live state, preserve datasource evidence, author one explicit
object, validate that object through every required stage, stop for one
unresolved business decision, and hand only ready refs to analysis.

The target skill does not repeat installed API signatures, backend catalogs,
constructor contracts, result fields, error catalogs, exact repair calls, or
code examples. Those facts move to the environment-bound datasource and
semantic surface specified by
[`2026-07-13-marivo-semantic-live-interface-surface-design.md`](2026-07-13-marivo-semantic-live-interface-surface-design.md).

The packaged target is one `SKILL.md` with no references, runnable examples,
or attachment runner. This is an atomic replacement: attachments are deleted
only after their retained facts and mechanics are live-owned and the candidate
package passes the deterministic surface, protocol, structure, and drift
checks.

## Relationship To The Live Surface

The live surface and skill divide authoring responsibility at the line between
mechanical truth and policy:

```text
live md/ms surface
    what capabilities exist
    what inputs, outputs, prerequisites, and effects they have
    what state the current object is in
    what calls are mechanically available from current typed state
    what typed repair can resolve a mechanical blocker

marivo-semantic skill
    why and when to activate semantic authoring
    which ordering and safety boundaries cannot be crossed
    how to preserve evidence and limit authoring scope
    when to stop for business judgment
    when and how to hand off to analysis

agent
    interprets evidence, drafts explicit Python, and chooses mechanical continuations allowed by policy

user or business owner
    decides unresolved business meaning and accepts semantic caliber
```

The skill may name stable conceptual phases and live entry points. It must not
reconstruct the current package's mechanical input/effect contract from prose.
The live surface does not persist verify-before-preview as a runtime gate; that
edge is intentionally skill policy. A disagreement exists only when runtime
facts contradict registered mechanical requirements or effects, not merely
because a call is mechanically available before the skill permits choosing it.

This semantic-authoring cutover follows the analysis live-interface cutover,
which supplies the shared introspection and environment-authority foundation.
It is not folded into the base analysis release. Its own atomic candidate also
adds the analysis-side routing schemas, validator/result, and analysis-skill
routing clause together with the semantic producer; none of those requirements
is published early. The later business semantic object-model cutover extends
this state router without adding object-family manuals back into the skill.

## Problem

The current skill already states the intended ownership model, but it still
contains enough version-sensitive detail to act as a second API manual.

### The canonical route repeats exact calls

The skill names current inspection, scope, snapshot projection, catalog,
verification, preview, and readiness calls. `md.help("authoring")` and
`ms.help("authoring")` repeat overlapping routes. Result objects and errors
also provide next-call strings.

This creates multiple owners for one transition. The current successful verify
result can suggest readiness before required preview, while the skill says to
preview first. An agent should not need the skill to correct the installed
runtime's transition facts.

### Hard boundaries mix policy with API facts

Some current hard boundaries are durable policy:

- physical evidence is not business meaning;
- one explicit scope must precede a user-data read;
- one snapshot is reused within an active batch;
- one unresolved semantic decision is asked at a time;
- readiness precedes analysis routing.

Other lines are version-sensitive product facts:

- exact source-builder names;
- exact scope and persistence parameters;
- exact diagnostic call names;
- exact typed-ref construction syntax.

The former belong in the skill. The latter belong in live help, effect
metadata, results, and structured errors.

### Pre-cutover references were API attachments

Before this redesign was implemented, the packaged skill linked to datasource
setup, closeout, pitfalls, cumulative metrics, cumulative anchors, and runnable
examples. Runtime constraints and errors pointed back into some of those files.

Attachments make the skill package a documentation tree that can drift from
the installed code. They also force a cold agent to decide whether a fact lives
in the root skill, a reference, an example, or runtime help.

### The workflow is not resume-first

The current route is readable from a fresh project, but agents often enter with
an existing datasource, inspection, snapshot, authored object, failed verify,
or blocked readiness report. A fixed start-to-finish checklist encourages
repeating safe work, reacquiring evidence, or ignoring the current object's
typed state.

The target skill must route from current live state and enforce the earliest
unsatisfied required boundary, not restart the whole authoring tutorial.

### Judgment ownership needs a concrete stop protocol

The current skill says to ask one evidence-grounded question when one semantic
decision remains. The target must preserve this behavior while removing
constructor-specific decision lists and examples.

Without a precise protocol, an agent may still:

- ask before reading available evidence;
- present invented answer options;
- bundle multiple business decisions;
- continue authoring while awaiting an answer;
- treat observed uniqueness or value shape as authoritative meaning.

## Decision

Adopt a **boundary state-router** skill.

The skill is a small policy adapter between a general-purpose coding agent and
the environment-bound live authoring surface. It performs no authoring and
contains no private product catalog. It preserves four kinds of responsibility:

1. activation and environment authority;
2. ordered state-routing discipline;
3. evidence, safety, and judgment boundaries;
4. cross-track routing and closeout obligations.

Everything else is either a live Marivo fact, agent judgment, or user decision.

## Alternatives Considered

### Activation-only skill

The skill would only say to verify the environment, open live semantic help,
and follow object-near contracts.

This is maximally small, but it does not protect the semantic track's essential
boundaries: evidence before meaning, dependencies before dependents, one object
at a time, one unresolved question at a time, verification before required
preview, or readiness before analysis routing.

### Boundary state-router — selected

Retain the durable partial order and hard boundaries while moving every
version-sensitive fact and mechanical transition to the live surface.

This gives the agent enough policy to author safely without making the skill a
second API reference or automatic planner.

### Complete authoring playbook

Retain detailed datasource instructions, constructor recipes, cumulative
metric guidance, pitfalls, examples, and exact repair calls in the packaged
skill.

This is convenient when read in isolation but preserves duplicate ownership,
environment drift, and high context cost. It is rejected.

### Runtime authoring wizard

Replace the skill with a runtime wizard that chooses and writes semantic
objects.

This moves business judgment and file mutation into Marivo, revives a prepare
or planner stage, and hides decision provenance. It is rejected.

## Design Goals

### Preserve policy order without prescribing a business model

The skill protects runtime-reported mechanical prerequisites and its own policy
gates. It does not say that every project needs every object family or choose
the next semantic object from a schema.

### Resume from current state

An agent inspects the current environment, project, runtime object, result, or
error and continues from the earliest unsatisfied required boundary. It does
not repeat datasource reads or authoring work merely because the skill starts
with a fresh-project example.

### Keep the installed package authoritative

The skill enters through a matching environment fingerprint and uses live help,
object/result contracts, and typed repair for all mechanical facts. It contains
no fallback API memory.

### Preserve evidence continuity

The active object or batch retains one explicit evidence lineage from
inspection and scope through snapshot projections, preview, and readiness.
Reacquisition is an explicit invalidation event, not an invisible retry.

### Keep judgment explicit

The agent settles technical facts from live contracts and evidence. The user or
business owner settles unresolved business meaning. Marivo and the skill do not
invent semantic caliber.

### Minimize context and navigation cost

One root skill activates one live authoring entry. It does not require reading
attachments, copying examples, or consulting a matrix of backend and object
recipes.

## Non-Goals

The skill does not:

- teach constructor signatures or parameter tables;
- list datasource backends or source-builder APIs;
- list result fields, error kinds, or exact repair calls;
- contain cumulative, lifecycle, event, or other object-family manuals;
- teach general data modeling or analytics methodology;
- infer a primary key, business key, relationship, aggregation, unit,
  additivity, time policy, or business definition;
- plan or rank semantic objects;
- write files or invoke Marivo automatically;
- add a prepare stage, authoring brief, checklist acknowledgement, or wizard;
- require parity or raw diagnostics for readiness unless a future live object
  contract explicitly changes readiness;
- replace current project documentation or business-owner decisions;
- provide compatibility text for retired APIs or deleted attachments.

## Target Package Shape

The target package is:

```text
marivo/skills/marivo-semantic/
  SKILL.md
```

Delete:

```text
references/datasource.md
references/closeout.md
references/pitfalls.md
references/cumulative-metrics.md
references/cumulative-anchors-v2.md
references/examples/01_discover_and_grill.py
references/examples/02_author_one_object.py
```

Delete or repurpose attachment-specific runners and tests. No redirect or
placeholder file remains. The installed package and current latest docs must
not link to deleted paths.

The single file has no fixed line-count target. It is bounded structurally:
one trigger, one mission, one live-entry rule, one routing loop, one hard-
boundary section, one routing section, and one closeout section. API examples,
tables, backend matrices, and error catalogs are forbidden regardless of total
length.

## `SKILL.md` Structure

### Frontmatter and trigger

The description activates the skill when work requires any of:

- datasource declaration or validation for semantic authoring;
- physical source inspection or evidence acquisition;
- new or changed semantic objects;
- semantic verification, preview, or readiness repair;
- an analysis routing that reports a genuinely missing business object.

Metric-centered investigation over already-ready refs remains owned by
`marivo-analysis`. The semantic skill must not remain active merely because an
analysis uses semantic objects.

### Mission and authority

The mission states:

> Establish or repair explicitly authored semantic objects from bounded,
> traceable datasource evidence, obtain human decisions for unresolved
> business meaning, and hand only scoped-ready refs to analysis.

Authority is divided explicitly:

- live Marivo owns mechanical contracts and current state;
- the skill owns ordering, safety, evidence, and routing policy;
- the agent owns technical interpretation and explicit Python drafting;
- the user or business owner owns unresolved business meaning.

### Environment-bound entry

Before trusting help or invoking a datasource/semantic capability, the agent
uses the interpreter intended for execution to open the canonical live semantic
entry and records its version, interpreter, and package path.

The skill may name the stable native entry:

```text
<selected-python> -m marivo help semantic
```

It does not name constructor or transition calls. A bare `marivo` command is
acceptable only after its fingerprint matches the selected execution
environment.

If authoritative discovery and execution fingerprints differ, the agent must
repair or stop before:

- opening a datasource connection;
- reading user data;
- mutating project or user state;
- authoring semantic files;
- handing refs to analysis.

The skill does not prescribe environment-manager-specific activation commands.
The live environment repair supplies current mechanics.

### Resume-first state router

The routing loop is conceptual and stable:

```text
verify environment authority
    -> inspect current project/runtime state
    -> identify the earliest unsatisfied required boundary for one object
    -> read the current object/result contract or structured error repair
    -> use focused live help for the selected capability's exact call
    -> inspect the target effect before invoking it
    -> invoke one explicit capability
    -> read returned content plus its object-near contract, or typed repair
    -> preserve or explicitly invalidate evidence lineage
    -> repeat until one object is ready, one user decision is required, or work is blocked
```

“Earliest unsatisfied boundary” means the first prerequisite required by the
current object's registered path. It is not a recommendation engine and does
not choose which new object a project should have.

The router prefers object-near live guidance:

1. current structured error repair;
2. current object/result `.contract()`;
3. focused help for the target capability;
4. root help only when no canonical target is known.

It never substitutes memorized API syntax when the live surface is available.
Mechanical availability is not policy permission. In particular, the agent
must complete and read static verification before choosing preview even when
focused help shows that preview is callable from a loaded object.

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
readiness before analysis routing
```

The live surface declares which object families require static-only,
single-snapshot, snapshot-mapping, or future preview modes. The skill does not
hardcode object-family exceptions.

Verify-before-preview is always a skill-owned policy edge for preview-required
families. No verification acknowledgement or persisted verification token is
expected from runtime, the runtime does not mechanically enforce that ordering,
and readiness performs its own current static checks.

Catalog browse is a behavior rule, not a fake runtime state gate. Marivo does
not require an acknowledgement that an agent read catalog output.

### One-object loop

The unit of mutation and validation is one explicit semantic object.

For each object, the agent:

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

## Evidence And Judgment Protocol

### Evidence collection order

Before asking a user or drafting business meaning, the agent checks every
relevant source already available in scope:

1. current live constructor and constraint contract for mechanical legality;
2. prior explicit user or accountable business-owner decisions;
3. existing approved catalog and project definitions;
4. source comments, provenance, and project documentation;
5. current datasource inspection and exact snapshot observations.

This is a collection order, not an API sequence or a rule that later physical
evidence overrides earlier business authority. The live surface owns how each
source is accessed. The agent still exhausts relevant available evidence before
asking a question so it can detect conflicts and avoid asking for observable
facts.

### Authority precedence

Different sources answer different questions:

1. the live contract is authoritative for mechanical validity only;
2. an explicit accountable business-owner decision is authoritative for
   business meaning;
3. an existing approved project definition is adopted semantic context unless
   a newer accountable decision changes it;
4. source comments, provenance, and project documentation are supporting
   business evidence whose authority must be identifiable;
5. inspection and snapshot results are authoritative only for the physical
   observations they actually measured.

When sources conflict, the agent does not silently select the highest-looking
technical source or overwrite an existing business decision. It names the
conflict in the one-question grill stop and requests accountable resolution.

### Technical handling

The agent owns technical interpretation that does not invent business meaning,
including:

- reading physical schemas and execution capabilities;
- handling uncommon physical formats after obtaining required factual input;
- translating an approved definition into explicit supported Python/Ibis;
- choosing a mechanically legal public capability;
- repairing syntax, typing, loading, or execution failures from typed live
  guidance.

Technical plausibility is not authority for business caliber.

### Business decision boundary

The user or accountable business owner decides unresolved questions such as:

- whether observed uniqueness is an authoritative identity or key;
- metric meaning, numerator, denominator, failure handling, and scope;
- aggregation, unit, additivity, and time attribution;
- relationship business meaning and cardinality promise;
- uncommon date/epoch/timezone interpretation not established by source facts;
- which lifecycle, event, state, or business concept a future object represents.

The live surface may name the unresolved judgment target and show evidence. It
must not supply a guessed value.

### One-question grill stop

If exactly one business decision remains after the evidence-collection and
authority passes, the agent:

1. names one object and one unresolved judgment target;
2. summarizes the directly relevant evidence and provenance;
3. explains why that evidence cannot establish business authority;
4. asks exactly one question;
5. stops mutation and validation work for that object until answered.

If several decisions remain, the agent asks only the earliest dependency whose
answer can change later questions. It does not bundle a questionnaire.

Options are allowed only when each option is grounded in supplied evidence,
existing project conventions, or an explicit live closed enum. Plausible but
unsupported options are forbidden.

## Hard Boundaries

### Physical and semantic ownership

- Datasource objects describe connectivity and physical sources; semantic
  objects describe governed business meaning.
- The agent never recreates a physical source through an invented semantic
  builder.
- Semantic links use the live surface's typed refs; bare ids are not substituted
  when the current contract requires typed identity.

The skill states this category boundary without listing current builder or ref
function names.

### Scope and query effects

- Metadata inspection precedes any user-data query.
- Every user-data read has an explicit live-declared scope and required positive
  guards.
- A returned-row limit is not treated as a backend scan bound.
- A potentially unbounded diagnostic remains outside the canonical route and
  requires explicit necessity and effect review.
- The agent never follows a retry string without inspecting the registered
  effect of the target transition.

### Snapshot continuity

- One acquired snapshot supports all query-free projections for an active
  object/batch.
- The agent does not reacquire data merely to obtain another semantic-shaped
  view.
- Reacquisition or refresh changes evidence identity and invalidates dependent
  preview/readiness evidence according to the live contract.
- Multi-entity objects use the exact registered evidence mapping; snapshots are
  not silently substituted across entities or sources.

### Privacy, secrets, and persistence

- Credentials remain references, not authored plaintext.
- Any user-global secret cache or project-local plaintext value cache requires
  the live surface's explicit effect and privacy contract.
- Memory-only evidence remains the default unless persistence is knowingly
  accepted for the data in scope.
- The skill does not copy current cache paths or parameter names; those are live
  product facts.

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
- It does not add speculative objects to make a catalog appear richer.
- Advisory richness is not readiness and cannot create a required object by
  itself.

### Diagnostics and parity

- Potentially unbounded raw diagnostics are escape hatches, not normal evidence
  acquisition.
- Provenance parity is used only when the current object and task require it;
  it is not promoted to a universal readiness step by skill prose.
- A diagnostic result cannot override unresolved business meaning.

## Cross-Track Routing

### Analysis to semantic

When analysis reports genuine semantic absence, its structured repair uses
`kind="semantic_authoring"`, a semantic help target, and a concrete action.
The router authors only the smallest dependency-closed requirement described by
that error; it does not reconstruct a broader cleanup request from conversation
memory.

### Semantic to analysis

When explicit readiness is not blocked, the router discloses warnings, applies
its proceed-or-stop policy, and activates `marivo-analysis` with exactly
`ReadinessReport.analysis_ready_refs`. There is no second validation token or
analysis-side readiness receiver. Ordinary analysis operations resolve the refs
against the current session catalog when invoked.

### User and environment stops

An unresolved business decision remains a one-question stop. An environment
fingerprint mismatch remains an environment-repair stop before datasource,
semantic, or analysis work proceeds.

## Closeout Obligations

Successful semantic closeout states only Marivo-specific facts:

- which explicit project object was added or changed;
- which datasource evidence identity and scope grounded it;
- which business decisions were supplied and by whom when known;
- which registered validation stages passed;
- which refs are analysis-ready;
- which warnings or caveats remain;
- which analysis task or branch receives the routing.

Closeout does not generate a general modeling tutorial, repeat constructor
syntax, or claim that observed data proved business meaning.

A blocked closeout states:

- the exact current object/state;
- the typed blocker or unresolved judgment target;
- whether data was queried or project state was mutated;
- whether evidence remains reusable;
- the one required user or environment action.

## Boundary-Violation Behavior

| Situation | Required skill behavior |
| --- | --- |
| Help and execution fingerprints differ | Stop before connection, read, mutation, or authoring |
| Proposed call has an unknown effect | Read focused live help; do not invoke it from memory |
| User-data read has no explicit registered scope | Stop before query |
| A fresh matching snapshot already exists | Reuse it; do not reacquire for another projection |
| Evidence suggests but cannot establish business meaning | Ask one grounded question and stop |
| A dependency is missing or not ready | Repair/ready the dependency before authoring the dependent |
| Static verification fails | Repair and reverify the same object |
| Required preview is missing or stale | Run or repair scoped preview before readiness |
| Readiness is blocked | Follow typed repair; do not hand off the blocked ref |
| Analysis needs an absent object | Author the smallest dependency-closed set for that governed requirement, one validated object at a time, then hand off ready refs through the analysis boundary |
| Runtime transition contradicts its registered input, state, target-surface, or effect facts | Treat candidate as internally inconsistent; stop and report the contract defect |

The last row prevents the skill from becoming an invisible compatibility shim.
Mechanical availability before a skill policy edge, including preview before
explicit verification, is not such a contradiction; the skill simply does not
choose the call yet. A real registered-fact contradiction is reported without a
permanent workaround.

## Content Disposition

### Current `SKILL.md`

| Current content | Target owner |
| --- | --- |
| Trigger and semantic-vs-analysis boundary | Retain in skill |
| Ownership statement | Retain, shortened |
| Exact help/browse/inspect/sample calls | Live help and object/result contracts |
| Conceptual partial order | Retain in skill |
| Constructor/object dependency facts | Live registry and semantic help |
| One-object validation discipline | Retain in skill |
| One-question grill behavior | Retain in skill |
| Source builder and typed-ref names | Live datasource/semantic help |
| Scope, scan, persistence, and secret principles | Retain as policy; mechanics move live |
| Exact raw diagnostic/parity calls | Live boundary help/effect registry |
| Analysis routing requirement | Retain in skill; mechanics move live |

### `references/datasource.md`

Move constructor, inspection, scope, snapshot, persistence, and diagnostic facts
to registered datasource help, effects, results, and errors. Retain only the
durable scope/privacy/evidence rules in the root skill. Delete the file.

### `references/closeout.md`

Move readiness fields and exact calls to live semantic results/help. Retain the
ready-only routing and closeout obligations in the root skill. Delete the file.

### `references/pitfalls.md`

Convert mechanically detectable failures into typed errors, transitions,
repair, and tests. Retain only true policy boundaries in the root skill. Delete
the file rather than preserving a second error catalog.

### Cumulative references

Move cumulative constructor semantics, anchors, constraints, and minimal
examples to the live semantic callable/help owners. General modeling prose is
deleted or moved to current site concepts if it remains user-documentation
material. The skill does not retain object-family manuals.

### Runnable examples

Focused live help owns one minimal runnable example per capability. Current site
documentation owns longer walkthroughs. Delete packaged skill examples and
their attachment-specific runner; do not keep examples as a hidden API test
source.

## Atomic Replacement Scope

The skill replacement is released only with the companion live-surface
cutover and the analysis-side routing extension. Implementation may prepare
files earlier on a branch, but no candidate package may delete attachments or
publish either routing half while runtime constraints, errors, skills, or latest
documentation still depend on the earlier conceptual crossing.

The coordinated cutover updates:

- `marivo/skills/marivo-semantic/SKILL.md`;
- the routing clause in `marivo/skills/marivo-analysis/SKILL.md`;
- all files under its current `references/` tree;
- attachment/example runners and tests;
- runtime constraint and error links;
- datasource and semantic help topics;
- result transition and repair fields;
- both directional routing schemas, `catalog.readiness(...)`,
  explicit `catalog.readiness(...)`, and `ReadinessReport`;
- `agent-guide.md` and active semantic specs;
- latest English and Chinese site authoring documentation;
- CLI entry guidance;
- release notes for the target public changes.

The target is a direct breaking replacement. It has no bare-ready-ref fallback,
mixed-version branch, compatibility alias, or migration adapter.

Historical versioned docs and historical release notes remain unchanged.

## Active Contract Alignment

After cutover, active documentation uses the same ownership statement:

> Environment-verified datasource and semantic surfaces own API facts,
> observable state, mechanically available calls, orthogonal effects, and
> repair. The `marivo-semantic` skill
> owns ordered routing discipline, evidence and safety boundaries, the
> one-question business-decision protocol, and ready-only routing. The agent
> owns technical drafting; the user or business owner owns unresolved meaning.

No active guide may say that:

- the skill is the source of constructor or backend truth;
- help topics are complete step-by-step runbooks independent of the state
  registry;
- evidence projections recommend semantic meaning;
- verify success permits skipping a required preview;
- verify-before-preview is a persisted runtime checkpoint rather than skill
  policy;
- readiness can be inferred from skill completion;
- analysis may consume an unready ref;
- cumulative or future business object semantics live in packaged skill
  references.

## Verification Strategy

### Package-shape checks

- The packaged semantic skill contains exactly one `SKILL.md`.
- No `references/`, examples, attachment runner, redirect, or placeholder
  remains.
- Nested example support files, including the current
  `references/examples/_support/` tree, are deleted with the attachment tree.
- Package manifests and installation tests include the root file and exclude
  deleted paths.

### Skill-content checks

Structural tests assert the presence of:

- environment-bound entry;
- resume-first routing;
- durable partial order;
- one-object loop;
- evidence-before-question rule;
- business-authority precedence and conflict stop;
- one-question grill stop;
- scope, privacy, evidence, and judgment boundaries;
- readiness-only analysis routing;
- closeout obligations.

They assert the absence of:

- constructor signatures and parameter tables;
- backend or source-builder catalogs;
- exact result fields or error kinds;
- exact transition and repair call recipes;
- cumulative/object-family manuals;
- generic modeling methodology;
- prepare, planner, wizard, or automatic authoring language;
- links to deleted attachments.

Tests should prefer structural ownership assertions over pinning long prose or
capitalization-sensitive phrases.

### Live-owner checks

Before deleting each attachment, inventory every retained fact and prove its
live target exists in the candidate package. Every runtime constraint, error,
transition, and repair help target resolves through its declared live surface
without a skill path. Cross-track cases carry complete typed routing payloads.
Package checks reject a candidate that contains the semantic producer without
the analysis validator, the validator without the producer, or either target
skill rule without both live owners.

### Behavioral scenarios

Review at least:

1. fresh datasource-to-one-object authoring;
2. resume from an existing snapshot;
3. resume from failed verification;
4. stale/missing preview repair;
5. dependency-not-ready repair;
6. one unresolved business decision;
7. unscoped or potentially unbounded read prevention;
8. environment mismatch stop;
9. analysis missing-semantic routing followed by validated re-entry;
10. semantic-first routing into a newly created or recovered analysis session;
11. future object family whose preview mode differs from current objects.

The skill must route from live state without knowing version-specific mechanics
for any scenario.

### External Agent UX evaluation

The standalone marivo-agent-evals project owns model-backed candidate
evaluation, fixtures, event logging, safety oracles, and trial policy. This
repository exposes deterministic installed-package, help, repair, result,
routing, and skill-shape contracts for that evaluator but does not run a model
or store its evaluation harness.
### Repository checks

The implementation plan runs narrow skill, introspection, datasource,
semantic-result, readiness, CLI, packaging, and site tests first, followed by:

```text
make test
make typecheck
make lint
make examples-check
```

`make examples-check` must be updated to validate only retained example owners;
it must not fail because deleted skill examples no longer exist.

## Acceptance Criteria

### Skill shape

- The packaged skill is one root `SKILL.md` with no attachments or examples.
- Its sections are limited to trigger, authority, live entry, routing,
  boundaries, routings, and closeout.
- It contains no version-sensitive API catalog or fallback mechanics.

### Ownership consistency

- Installed live surfaces own signatures, orthogonal effects, observable states,
  mechanical continuations, and typed repair.
- The skill owns partial-order discipline and hard boundaries without
  duplicating those facts.
- Verify-before-preview is enforced by skill behavior and is not represented as
  a persisted runtime checkpoint.
- The agent and user decision rights are explicit.
- Runtime code and active docs do not use skill files as API documentation.

### Ordered behavior

- The agent can enter at any current state and resume at the earliest required
  unsatisfied boundary.
- Existing fresh evidence is reused.
- One object is authored and fully validated before the next.
- Required preview cannot be skipped between verification and readiness.
- Only ready refs cross to analysis.

### Judgment behavior

- Evidence is exhausted before asking a user.
- Explicit accountable business decisions outrank project documentation and
  physical observations for business meaning; conflicts stop for resolution.
- One unresolved judgment target produces exactly one grounded question.
- Unsupported options and inferred business defaults are forbidden.
- Authoring stops while the answer is unresolved.

### Boundary behavior

- Environment mismatch, unknown effects, unscoped reads, stale evidence,
  dependency blockers, and readiness blockers produce explicit stops or live
  repair.
- Analysis-to-semantic and semantic-to-analysis crossings preserve their typed
  routing payloads without reconstruction from conversation memory.
- Analysis consumes analysis-ready ready refs only from
  `ReadinessReport`, for both first entry and re-entry.
- The skill does not hide a defective live transition behind a memorized
  workaround.

### Verification

- Package-shape, skill-content, live-owner, protocol, and repository checks
  pass against one target-only candidate without invoking a model.
- Deleted attachment names and paths are absent from the built wheel and active
  latest documentation.

## Success Test

A cold general-purpose coding agent receives only the one-file skill and an
installed Marivo environment. It verifies that environment, resumes from the
current typed state, inspects effects before invocation, preserves one bounded
evidence lineage, asks at most one grounded business question, authors and
fully validates exactly one explicit Python object, and hands only live-marked
ready refs to analysis. It never needs a packaged reference, copied example,
memorized constructor, automatic planner, or inferred semantic default.
