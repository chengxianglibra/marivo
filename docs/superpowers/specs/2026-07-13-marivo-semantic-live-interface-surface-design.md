# Marivo Datasource And Semantic Live Interface Surface Design

Status: Phase 1--3 complete; Phase 4--5 planned; not release-ready

Date: 2026-07-13

Internal infrastructure placement is refined by
[`2026-07-16-marivo-live-infrastructure-layering-design.md`](2026-07-16-marivo-live-infrastructure-layering-design.md).
This document remains authoritative for the public datasource/semantic
surface; the newer design narrows `marivo.introspection.live` to neutral
resolution mechanics and moves shared authoring and semantic-analysis boundary
concepts to dedicated private owners.

## Summary

Redesign the agent-facing `marivo.datasource` and `marivo.semantic` guidance
surface as one environment-verifiable live authoring contract with two clear
domain owners.

The datasource surface owns physical connectivity, source shape, execution
effects, scope, and acquired evidence. The semantic surface owns authored
business objects, typed dependencies, static verification, scoped runtime
preview, and readiness. A shared private authoring contract model connects
their separate native registries without introducing a third public authoring
module.

The redesigned surface will:

- make the installed version, interpreter, and package path visible before an
  agent trusts any authoring guidance;
- resolve help consistently across canonical strings, public callables, public
  types, runtime objects, results, and structured errors;
- derive signatures and callable facts from live Python objects instead of a
  second hand-written constructor catalog;
- separate runtime-observable states and mechanically available calls from the
  ordered policy edges enforced by the state-router skill;
- classify every authoring capability on independent data-access, connection,
  mutation, and guard axes;
- make current objects/results expose canonical mechanical continuations
  through `.contract()` and errors expose typed repair rather than free-form
  `next_calls` or `suggested_action` strings;
- remove packaged skill files as runtime documentation dependencies;
- keep physical evidence separate from semantic judgment;
- provide CLI and Python adapters over the same installed registry and
  renderer;
- let a cold general-purpose coding agent author and ready one semantic object
  without guessing APIs, issuing an unscoped query, or inventing business
  meaning.

This is an atomic public-surface cutover. It provides no compatibility aliases,
dual help path, transition-string bridge, or skill-document fallback. It does
not change datasource backend algorithms, sampling semantics, semantic object
meaning, expression validation, query compilation, readiness policy, or the
separately designed business semantic object model.

## Relationship To The State-Router Skill

This design is the live-library companion to
[`2026-07-13-marivo-semantic-boundary-state-router-design.md`](2026-07-13-marivo-semantic-boundary-state-router-design.md).

The ownership split is:

```text
Marivo live datasource/semantic surface
    owns API facts, observable state, mechanically available calls, effects, and repair mechanics

marivo-semantic skill
    owns ordered routing discipline, hard boundaries, judgment protocol, and handoffs

agent
    owns evidence interpretation, technical drafting, and transition choice within policy

user or business owner
    owns unresolved business meaning and acceptance of semantic caliber
```

The surface owns **which calls are mechanically available from current typed
state and what they require**. The skill owns **which policy edge must be
satisfied before the agent chooses one of those calls**. In particular,
verify-before-preview is skill policy: Marivo exposes verification and preview
facts but does not persist a fake verification checkpoint or claim that preview
is mechanically impossible before an explicit verify call. It is not
mechanically enforced by the runtime. The skill must not
hardcode signatures or reconstruct mechanical requirements from prose. The
surface must not choose a semantic object, recommend business meaning, or
auto-run the authoring route.

The two designs ship as one coordinated cutover. The packaged skill and its
attachments cannot be reduced until every retained API fact, transition, and
repair is reachable from the candidate package's live surface.

## Relationship To Other 2026-07-13 Designs

### Analysis live interface

This design reuses the neutral introspection mechanisms refined by
[`2026-07-16-marivo-live-infrastructure-layering-design.md`](2026-07-16-marivo-live-infrastructure-layering-design.md):

- environment fingerprints;
- a canonical registered-target resolver;
- callable, type, runtime-object, and error help;
- consumed-not-constructed result help;
- callable identity and reflection primitives;
- private numeric output and suggestion limits.

Typed authoring repair and continuation models belong to the private authoring
domain kernel. CLI/Python adapters and complete help rendering remain owned by
each public surface.

It does not reuse the analysis capability topology. Analysis exposes several
unordered mechanically legal branches. Semantic authoring has a real **policy**
order: evidence precedes evidence-backed authoring, dependencies precede
dependents, static verification precedes runtime preview, required preview
precedes readiness, and readiness precedes analysis handoff. The live surface
enforces only prerequisites it can prove from current typed state; the
companion skill owns the remaining ordered discipline.

The analysis cutover removes `mv.help("workflow")`. The current semantic
authoring help points to that target, so the analysis cutover must update the
cross-link even if this full semantic surface cutover lands later. No released
state may route semantic handoff to a removed analysis target.

This semantic redesign is a follow-up cutover, not an expansion of the already
large analysis atomic release. Its implementation starts from the shared
target, environment, reflection, resolver, and limit mechanisms plus the
private authoring domain model. If development overlaps, the two branches
coordinate those shared owners; semantic does not create a parallel resolver
or duplicate authoring contract model.

### Analysis boundary kernel

The matching
[`2026-07-13-marivo-analysis-boundary-kernel-design.md`](2026-07-13-marivo-analysis-boundary-kernel-design.md)
defines semantic handoff as an explicit analysis boundary. A missing required
semantic object activates `marivo-semantic`; analysis consumes handed-off refs
only after they reach scoped readiness and pass the analysis boundary. This
design supplies the authoritative semantic entry, readiness producer, and
atomic cross-track cutover used by that handoff.

### Business semantic object model

The separately specified
[`2026-07-13-business-semantic-object-model-design.md`](2026-07-13-business-semantic-object-model-design.md)
changes semantic object families, binding contracts, readiness inputs, and
analysis handoffs in a later atomic cutover. This interface design does not add
those object types early. That cutover extends the same authoring registry,
effect taxonomy, help resolver, transition model, and coverage gates rather
than creating a parallel discovery surface.

### Delivery order

The delivery order is:

1. analysis live-interface cutover, including removal of the stale semantic
   link to `mv.help("workflow")` and retention of a discoverable semantic entry,
   but excluding `boundary.semantic_handoff`, directional handoff schemas,
   `SemanticHandoffReceipt`, and receipt-only skill policy;
2. this datasource/semantic live-surface cutover as one cross-module
   semantic-authoring release. It atomically adds both directional schemas, the
   analysis-side validator and receipt, the semantic readiness producer, and
   the corresponding rules in both packaged skills;
3. the business semantic object-model cutover, extending the resulting
   registries and authoring families atomically.

This order is normative. It prevents three competing introspection kernels and
keeps the business-model cutover from targeting an interface that is being
replaced simultaneously. Step 2 directly replaces the prior conceptual
cross-track handoff; it exposes no consumer-before-producer state, legacy
ready-ref branch, compatibility alias, or migration interval.

### Implementation Phases

The normative delivery order above defines public cutovers. The following
phases are execution tracking for delivery step 2; they do not authorize an
intermediate public release. Phase 3, Phase 4, and Phase 5 must converge into
one target-only candidate package before the datasource/semantic live-surface
cutover is released.

1. **Phase 1 -- initial shared live foundation (complete).** Establish the
   private target, environment, resolver, rendering-budget, authoring-contract,
   and handoff primitives required by the datasource/semantic cutover. The
   follow-up layering design narrows `marivo.introspection.live` to neutral
   mechanisms and relocates authoring and handoff concepts to dedicated private
   owners without creating a third public authoring module.
2. **Phase 2 -- datasource live surface (complete).** Hard-cut
   `marivo.datasource` to its capability registry, live help, stable typed
   errors and repairs, object-near contracts, explicit effect facts, and native
   datasource CLI help. This phase deliberately does not change the semantic
   public surface, analysis handoff, packaged skills, active documentation, or
   cold-agent release gate, and is therefore not independently release-ready.
3. **Phase 3 -- semantic live surface (planned).** Hard-cut
   `marivo.semantic` to an equivalent registry-backed surface: semantic help
   and CLI routing, consumed-type/runtime-object help, generated lifecycle,
   live invocation signatures, typed contracts and repairs, and the single
   `SemanticCatalog` receivers for verification, preview, and readiness.
   Remove duplicate top-level verification/readiness wrappers, prepare-era
   `AuthoringQuestion`, free-form mechanically actionable repair strings, and
   skill/example-file runtime dependencies.
4. **Phase 4 -- directional semantic/analysis handoff (planned).** Add the
   paired `AnalysisToSemanticHandoff` and `SemanticToAnalysisHandoff` schemas,
   `boundary.semantic_handoff`,
   `Session.validate_semantic_handoff(...)`, and
   `SemanticHandoffReceipt`. Semantic produces handoff only from scoped
   readiness; analysis consumes refs only after query-free receipt validation.
   No conceptual crossing, bare ready ref, compatibility branch, or migration
   adapter remains.
5. **Phase 5 -- policy, documentation, and candidate release gate (planned).**
   Replace the packaged semantic skill and the analysis skill handoff clause;
   update `agent-guide.md`, active specs, latest English and Chinese site
   documentation, help/error examples, and target-release notes. Then build a
   target-only candidate wheel and pass mechanical, repository, site, and
   cold-agent gates. The cold-agent evaluation covers explicit scope and
   environment safety, unresolved business meaning, dependency order,
   verify-before-preview policy, and preview-before-readiness mechanics.

Phase 5 is the release boundary for delivery step 2. The later business
semantic object-model cutover remains delivery step 3: it extends the completed
registries and authoring families rather than reopening Phases 1--5.

## Problem

The repository already states the intended high-level layering: `md.help(...)`
owns datasource contracts, `ms.help(...)` owns semantic constructor contracts,
results and structured errors own current next calls, and the packaged skill
owns workflow and routing. The implementation does not yet make those owners
independent or internally consistent.

### Authoring runbooks have three owners

`md.help("authoring")` hardcodes a ten-step route spanning datasource
declaration, registration, inspection, sampling, evidence projection, raw SQL,
and semantic handoff. `ms.help("authoring")` hardcodes another route spanning
catalog browse, object dependency order, evidence use, verification, preview,
readiness, and analysis handoff. `marivo-semantic/SKILL.md` repeats the combined
route with exact calls.

Changing one state transition therefore requires coordinated prose edits in
three places even before site documentation and tests are considered. The
authoring topics are useful entry points, but they must render a registered
state model rather than remain independent runbooks.

### Static semantic contracts are partly hand-written

`marivo/semantic/help.py` contains a large `_authoring_contracts()` table with
constructor names, required and optional parameters, defaults, types, and
constraints. Much of that information also exists in the real callable
signature, authoring types, validators, and constraint catalog.

Hand-maintained duplication can describe a callable that the installed package
does not expose or omit a new required parameter. A help render may enrich live
reflection with registered semantic constraints, but invocation-critical
signature facts must come from the actual registered callable.

### Help target resolution is incomplete

The public datasource and semantic help adapters accept only strings. Passing
a public callable such as `md.inspect`, a public type such as `VerifyResult`, a
runtime result, or a structured error does not follow one predictable help
contract. The shared resolver also walks only a narrow one-level class method
shape and returns an unknown descriptor instead of a typed target error.

An agent working in a write-run-read loop should be able to ask the live
surface about the object already in hand instead of translating it back into a
fragile string.

### Public type help leaks construction internals

Generic dataclass reflection exposes every field, including private
implementation fields such as `_project_root`, `_details`, and `_catalog`.
Public result and catalog wrapper types are consumed from Marivo operations;
agents do not construct them directly. Their help should emphasize producers,
public properties, public methods, current-state readers, and mechanically
compatible consumers, not internal constructor storage.

### Dynamic guidance does not close the ordered loop

Current datasource evidence objects store free-form `next_calls` strings.
Readiness issues store a free-form `suggested_action`. A successful
`VerifyResult` may suggest readiness directly even though the packaged workflow
requires scoped preview first, while `PreviewResult` does not expose a
readiness transition.

These strings cannot be exhaustively checked against public exports or help
targets. They also mix three different concepts:

- a mechanically legal transition;
- a repair required before a failed transition can succeed;
- a workflow preference owned by the skill or agent.

### Runtime constraints depend on skill attachments

Datasource error templates and semantic constraints point to files under
`marivo/skills/marivo-semantic/`. That makes a mutable workflow package an API
manual and prevents safe deletion of duplicated references and examples.

The installed runtime must remain self-explanatory when only the package's
public Python and CLI surfaces are available. Skill paths cannot be the only
location for an API fact, example, error repair, or constraint explanation.

### Effects are described in prose instead of one contract

Datasource and semantic operations differ materially:

- some only inspect local metadata;
- some open live connections;
- some read explicitly scoped user data;
- some may scan an unbounded amount of backend data despite a row limit;
- some write project state or user-global secret state;
- some are query-free semantic checks;
- some are potentially unbounded diagnostics.

Those facts currently live across docstrings, help topics, warnings, and the
skill. Without one closed effect classification, an agent cannot reliably tell
whether a proposed next call is safe before invoking it.

### The CLI does not establish semantic environment authority

`marivo --help` currently prints `python -c` snippets for two help topics. It
does not provide a native semantic or datasource help subcommand, and the help
output does not identify the interpreter or installed package that supplied
the contract. An agent can therefore read one environment and execute in
another without a detectable authority mismatch.

## Decision

Adopt a **live authoring-state surface**.

Keep `marivo.datasource` and `marivo.semantic` as the two public domain modules.
Build one private shared registry and resolver that describes their public
capabilities, state families, transition facts, effects, constraints, and
repairs. Render that registry through the existing `md.help(...)` and
`ms.help(...)` Python adapters and through native CLI adapters.

The registry is descriptive, not executable orchestration. It can state that a
loaded object plus the exact scoped snapshot inputs makes preview mechanically
callable. It cannot choose preview before the skill-required verification,
choose which object to author, ask a business question, acquire data on the
agent's behalf, or advance to readiness automatically.

## Alternatives Considered

### Keep the current help/skill split

Continue treating `md.help` and `ms.help` as static manuals, result strings as
dynamic guidance, and the skill as the complete workflow.

This is the smallest change, but it preserves independent runbooks, free-form
transition strings, skill-owned API facts, and environment ambiguity. It does
not solve the observed verify/preview/readiness contradiction.

### Live authoring-state surface — selected

Make the installed package the authority for static facts, observable state,
mechanically available calls, effects, and typed repair. Keep the packaged
skill as a thin policy router over those facts.

This requires a coordinated registry and result-contract cutover, but it gives
every version-sensitive fact one live owner while preserving semantic
authoring's real policy order in the companion skill.

### Automatic semantic authoring planner

Add a runtime planner or wizard that inspects data, selects semantic object
types, recommends values, authors files, and advances validation automatically.

This collapses physical evidence, business judgment, file mutation, and
workflow policy into one mechanism. It would recreate the removed prepare
stage, encourage inferred caliber, and make it difficult to identify who made
a business decision. It is rejected.

## Ownership Model

| Concern | Canonical owner |
| --- | --- |
| Datasource constructors and backend-specific parameters | registered `md` callable |
| Physical source constructors and schemas | registered `md` callable/type |
| Connection, secret, scope, persistence, and scan constraints | datasource registry and typed errors |
| Current physical metadata and execution capabilities | `SourceInspection` |
| Acquired sample identity, scope, columns, freshness, and persistence | `DiscoverySnapshot` |
| Query-free physical evidence | snapshot projection results |
| Semantic constructors and typed ref contracts | registered `ms` callable |
| Semantic dependency facts and catalog identity | semantic registry/catalog |
| Static object validity | `VerifyResult` |
| Scoped executable evidence | `PreviewResult` |
| Analysis handoff eligibility | `ReadinessReport` |
| Mechanically available calls and effect metadata | private authoring-state registry |
| Current failed-operation repair | typed error/result repair object |
| Ordered routing discipline | `marivo-semantic` skill |
| Evidence interpretation and technical drafting | agent |
| Unresolved business meaning and caliber acceptance | user or business owner |

No constraint, result, or error may point to a skill file as its canonical API
documentation. Site documentation may explain concepts at greater length but
must route version-sensitive calls back to live help.

## Design Goals

### One live truth per fact

Signatures come from registered callables. Semantic constraints come from the
constraint catalog and validators. Current state comes from runtime objects.
Mechanical continuations and effects come from one closed registry. Help topics,
results, errors, CLI output, and tests consume those owners instead of copying
them.

### Preserve policy order without inventing runtime checkpoints

The surface makes observable dependency, evidence, preview, and readiness facts
mechanically visible. The skill preserves evidence-before-drafting,
dependency-before-dependent, verify-before-preview, and one-object-at-a-time
discipline where runtime cannot prove that an agent completed an earlier human
action. Neither layer flattens semantic authoring into an unordered
analysis-like graph or turns the policy order into an automatic pipeline.

### Make safety visible before execution

An agent can determine whether a capability is metadata-only, query-free,
scoped, state-mutating, connection-opening, or potentially unbounded before it
invokes the capability.

### Keep judgment outside the runtime

Physical evidence can report observed uniqueness, value shapes, null rates,
time encodings, or join-key observations. It cannot select a primary key,
business key, aggregation, unit, additivity rule, relationship cardinality,
timezone policy, or business definition.

### Object-near progressive disclosure

An agent can ask help about the callable, type, runtime object, result, or error
already in hand. Focused help is sufficient for one mechanically correct next
call without reading a skill attachment.

### Bound cognitive and data access cost

Root help is compact, focused help is bounded, suggestions are bounded, and
results render bounded state. No help call connects to a datasource or reads
user rows. No state transition hides a data access effect.

### Support cross-track handoff

Readiness produces a typed, environment-identified semantic handoff to
analysis. Analysis semantic-missing repair returns to the same semantic entry
and requires fresh scoped readiness before resuming.

## Non-Goals

This design does not:

- expand advisory richness into an automatic semantic recommendation or
  authoring engine;
- add an authoring planner, wizard, prepare stage, or generated authoring brief;
- automatically create or edit project semantic files;
- infer business meaning from physical evidence;
- add a third public `marivo.authoring` module;
- expose the private registry for user mutation;
- expose a public structured registry or JSON help DTO;
- make help calls inspect live datasources or project rows;
- change source sampling, query compilation, parity, readiness, or quality
  algorithms;
- make `raw_sql` or parity part of the required route;
- add compatibility aliases for retired help targets or free-form transition
  fields;
- rewrite historical versioned documentation or release notes;
- implement the future business semantic object model early.

## Architecture

```text
Registered public md/ms callables and types
    + datasource/semantic constraint catalogs
    + closed orthogonal effect contract
    + closed authoring-state/transition registry
    + registered error and repair contracts
                         |
                         v
       surface-owned registry and renderer
                         |
                         v
              neutral introspection kernel
       target resolver + environment/budget primitives
                         |
             +-----------+-----------+
             v                       v
      md.help / ms.help     marivo help datasource/semantic
             |                       |
             +-----------+-----------+
                               v
                    agent invokes public API
                               |
                               v
             typed runtime object/result or structured error
                               |
                               v
       object-near contract / typed repair / help target
```

The neutral introspection kernel and private authoring model are implementation
infrastructure. Complete registries, renderers, and public ownership remain
with `marivo.datasource` and `marivo.semantic`.

## Observable State And Policy Order

### Runtime-observable state families

The registry uses closed, subject-bound state families rather than one mutable
global workflow status:

```python
AuthoringStateId = Literal[
    "datasource.declared",
    "datasource.registered",
    "datasource.connection_validated",
    "source.inspected",
    "scope.explicit",
    "evidence.acquired",
    "evidence.projected",
    "semantic.loaded",
    "semantic.verified",
    "semantic.previewed",
    "semantic.ready",
]

class AuthoringStateRef(BaseModel):
    id: AuthoringStateId
    subject_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
```

Every state is derivable from a typed object, result, or persisted row-free
evidence. `semantic.verified` is result-local: a current `VerifyResult` proves
that one explicit check passed, but it is not persisted as a workflow checkpoint
and is not a runtime prerequisite for preview. `semantic.previewed` and
`semantic.ready` carry the semantic/dependency/evidence identity already owned
by preview and readiness results.

Environment authority, catalog browse, editing Python source, and analysis
handoff are policy or boundary events, not runtime state ids. Marivo cannot
prove that an agent compared two fingerprints, read catalog output, or edited a
file with the intended business meaning. The skill requires those actions and
the live surface exposes the facts needed to perform them; neither layer writes
fake acknowledgement state.

### Mechanically available calls

The runtime can prove only this availability graph:

```text
datasource.declared
    -> register

datasource.registered
    -> validate_connection
    -> inspect a named physical source

source.inspected + scope.explicit
    -> acquire evidence

evidence.acquired
    -> project query-free evidence

edited semantic Python
    -> load attempt -> semantic.loaded or structured load failure

semantic.loaded
    -> verify
    -> preview when the exact registered preview inputs are present
    -> readiness check

fresh required preview evidence
    -> readiness may mark the requested refs semantic.ready

semantic.ready
    -> construct a typed analysis handoff
```

Registration is a mechanical prerequisite for current project-backed
inspection. A dedicated connection-validation round trip is a sibling call from
registration, not a universal prerequisite for metadata inspection: successful
inspection can establish the metadata evidence required by authoring without
pretending that a separate test call occurred. The live capability declares
when connection validation is required for a particular operation, secret-cache
mutation, or repair.

Verification and preview remain independent public checks over a loaded object.
The state-router skill requires verification before a required preview, but
`catalog.preview(...)` does not consume a `VerifyResult`, and readiness does not
pretend that a non-persisted verification checkpoint exists. Readiness performs
its own current static checks and enforces fresh preview evidence for executable
families.

Not every semantic object requires datasource-backed preview. The registered
object family declares whether readiness requires:

- `static_only` — no executable datasource relation exists;
- `single_snapshot` — one exact `DiscoverySnapshot` binding is required;
- `snapshot_mapping` — an exact entity-keyed mapping is required;
- a future closed family added by the business semantic object cutover.

The registry, not the skill, owns that mechanical distinction. The skill owns
the policy rule that every required preview follows an explicit successful
verification.

### Dependency policy

The registry exposes typed dependency inputs and the loaded dependency closure.
It does not claim that Marivo can prevent an agent from editing a dependent
Python declaration before its dependency is ready, and it does not invalidate
the loader's two-pass forward-reference behavior. The skill enforces
dependency-before-dependent authoring and validation as a policy edge.

Help may render a teaching order such as domain, entity, properties/measures,
metrics, relationships, and dependent metrics. The order is navigation, not a
runtime gate or a claim that every project must author every family.

## Internal Registry Contract

The implementation may choose dataclasses, Pydantic models, or another private
closed representation, but every registered capability must provide these
facts:

```text
canonical_id
kind                      callable | method | transition | boundary | recovery
surface                   datasource | semantic
public_entrypoint         required for callable/method entries
callable_identity         required for callable/method entries
summary
input_requirements
output_family
preconditions
produced_state
effects
constraints
minimal_example
see_also
```

Transition-producing results and errors additionally register:

```text
required_states
produced_state
help_target               shared LiveHelpTarget(surface, canonical_id)
repair_kinds
```

The registry does not contain:

- question-to-object recommendations;
- business-semantic defaults;
- confidence or sufficiency scores;
- a preferred next continuation when several are mechanically available;
- arbitrary Python callbacks that auto-run capabilities;
- skill prose or site-document URLs as invocation-critical facts.

Boundary entries describe explicit agent-visible crossings that are not
themselves a single Marivo callable, such as editing one semantic source object
or handing ready refs to analysis. They have a namespaced live help target,
effects, and preconditions but do not claim to produce a runtime state when the
crossing is an agent action. Marivo never executes those entries automatically.

### Canonical ids

Canonical ids mirror public invocation shapes without `md.` or `ms.` prefixes
inside their owning surface:

```text
inspect
SourceInspection.sample
DiscoverySnapshot.entity
entity
verify_object
preview
readiness
errors.DatasourceAuthoringError
errors.SemanticLoadError
boundary.analysis_handoff
```

Cross-surface links use the shared private target value:

```python
HelpSurface = Literal["analysis", "datasource", "semantic"]

class LiveHelpTarget(BaseModel):
    surface: HelpSurface
    canonical_id: str | None = None
```

`canonical_id=None` means the registered root for that surface. The same value
is used by analysis, datasource, and semantic repair/handoff contracts; agents
never infer the owning adapter from an unqualified string. User-facing Python
examples retain `mv.`, `md.`, and `ms.`. There are no string aliases such as
`inspect_source` or `prepare_entity`.

For capabilities hosted by `SemanticCatalog`, the canonical verb remains short
while `public_entrypoint` records the real receiver. For example, canonical
`preview` resolves to `SemanticCatalog.preview`; a registered bound method
resolves to the same target rather than creating a second method-shaped id.

## Orthogonal Effect Contract

Data access, connection creation, and mutation are independent facts. Every
invokable capability or boundary declares one closed value on each axis rather
than choosing one lossy primary effect:

```python
DataAccessEffect = Literal[
    "none",
    "local_metadata_read",
    "live_metadata_read",
    "scoped_data_read",
    "potentially_unbounded_read",
]

ConnectionEffect = Literal[
    "none",
    "opens_connection",
]

MutationEffect = Literal[
    "project_state",
    "semantic_source",
    "user_global_state",
]

EffectFlag = Literal[
    "requires_explicit_scope",
    "requires_positive_row_guard",
    "requires_positive_timeout_guard",
    "requires_existing_snapshot_binding",
    "may_persist_plaintext_values",
    "may_cache_resolved_secret",
]

class AuthoringEffects(BaseModel):
    data_access: DataAccessEffect
    connection: ConnectionEffect
    mutations: tuple[MutationEffect, ...] = ()
    flags: tuple[EffectFlag, ...] = ()
```

`potentially_unbounded_read` remains distinct because a returned-row limit does
not bound backend work. The model also captures compound operations honestly:
`catalog.preview(...)` performs a scoped data read, opens a connection, requires
an existing snapshot binding, and writes row-free preview-check project state.
Snapshot acquisition performs a scoped read and writes snapshot metadata, with
an additional plaintext-persistence flag only when values may be cached.

The focused help render presents all four axes before the runnable example.
Contract transitions carry the complete `AuthoringEffects` value, including an
explicit semantic-source mutation on an edit boundary, so no call is presented
as harmless merely because its data access is bounded or query-free.

## Single Public Receiver Per Capability

The catalog is the object-near receiver for validation over loaded semantic
objects. The target canonical paths are:

```text
ms.load() -> SemanticCatalog
catalog.verify_object(obj)
catalog.preview(obj, using=...)
catalog.readiness(refs=[obj])
```

Delete the duplicate top-level `ms.verify_object(...)` and
`ms.readiness(...)` wrappers, their `ms.__all__` exports, help entries, tests,
and current documentation. There is no alias or deprecation bridge. Focused
targets `verify_object`, `preview`, and `readiness` record their real
`SemanticCatalog` public entrypoints, and registered bound methods resolve to
those canonical ids.

`ms.richness(...)` and `ms.parity_check(...)` remain top-level because no
duplicate catalog receiver exists and this design does not change their
algorithms. They still receive registered effects and focused help. Any future
receiver duplication must be resolved as a public-surface decision rather than
registered twice.

## Public Help Contract

The existing adapters remain:

```python
md.help(target=None) -> None
md.help_text(target=None) -> str
ms.help(target=None) -> None
ms.help_text(target=None) -> str
```

Their concrete target annotations cover only accepted public target families.
They do not accept `Any`.

### Accepted target kinds

Both surfaces support:

1. `None` — render the compact root index and environment fingerprint.
2. A canonical string target registered to that surface.
3. A registered public callable or bound method.
4. A registered public type.
5. A public runtime object owned by the surface.
6. A registered result instance.
7. A registered datasource or semantic error instance or type.

`ms.help(...)` additionally accepts `CatalogObject` and `SemanticRef` for a
bounded live semantic briefing. `md.help(...)` accepts registered
`DatasourceRef`, datasource specs, source descriptors, inspections, and
snapshots. Live enrichment is closed to those explicit families; help never
reflects arbitrary objects.

Unsupported or cross-surface values raise a surface-owned typed error:

```text
DatasourceHelpTargetError(DatasourceError)
SemanticHelpTargetError(SemanticError)
```

The adapters share one internal resolver/factory contract but do not expose a
third public help-error base. Each error states the received type, accepted
kinds, owning surface when known, and at most the configured number of
canonical lexical suggestions. A registered cross-surface object points to the
correct public help adapter. Unknown strings do not silently render an
`unknown` descriptor.

### Root information architecture

`md.help()` teaches the datasource-owned families:

- declare and manage;
- physical sources;
- inspect and scope;
- acquire and project evidence;
- diagnostics and boundaries;
- public consumed types and errors.

`ms.help()` teaches the semantic-owned families:

- browse and load;
- author by object family;
- verify and preview;
- readiness and analysis handoff;
- diagnostics and boundaries;
- public consumed types and errors.

Root ordering is a teaching order only. Each entry shows canonical id, concise
summary, output family, and bounded effect badges. Root help does not contain a
full runbook or constructor table.

### Authoring topics

`md.help("authoring")` becomes a datasource lifecycle view generated from
registered states. It ends at acquired/projected evidence and links to the
semantic authoring root. It does not teach semantic constructor order.

`ms.help("authoring")` becomes a semantic lifecycle view generated from
registered states. It begins with existing catalog/evidence inputs and ends at
scoped readiness and the analysis handoff. It does not restate datasource
backend setup or hardcode a second method-by-method runbook.

Both topics may show the conceptual policy order and label it as skill-owned.
Invocation details remain in focused capability help and the current object's
contract.

### Focused help

Every invokable target is sufficient for one mechanically correct call. It
renders, in order:

1. environment fingerprint on the root entry only;
2. canonical target and factual summary;
3. exact public entrypoint and live-derived signature;
4. accepted input and output families;
5. preconditions and produced states;
6. data-access, connection, mutation, and guard effects;
7. one runnable minimal example without ellipsis placeholders;
8. invocation-critical constraints;
9. producers, consumers, and optional related targets.

Private numeric limits bound root, focused, suggestion, and result renders.
Overflow fails tests/build validation; the renderer does not silently drop an
invocation-critical constraint or example.

### Native reflection convergence

Public callables retain complete docstrings for native `help(...)`, IDEs, and
Sphinx. Shared registered facts generate or validate help sections rather than
replacing docstrings with a private documentation system. Tests compare live
signatures, registered parameter families, public exports, and rendered
examples to prevent divergence.

The existing `md.describe(name)` remains a datasource-domain read for one
registered datasource and is registered/helped like any other public callable.
It is not a generic symbol-introspection API. This cutover adds no
`ms.describe(...)` or cross-surface `describe(symbol)` alias: every public API
symbol instead resolves through its owning `md.help(...)` or `ms.help(...)`
adapter. The coordinated guide update replaces the stale generic-`describe`
rule with that owner-specific rule while preserving the datasource verb's
current meaning.

## CLI Help Adapter

Add native, help-only paths backed by the same resolver and renderer:

```text
marivo help datasource
marivo help datasource inspect
marivo help datasource SourceInspection.sample
marivo help semantic
marivo help semantic entity
marivo help semantic readiness
python -m marivo help semantic
```

`marivo help semantic` is the default integrated authoring entry. It renders a
compact state overview and points the physical stage to
`marivo help datasource` without duplicating its contract. A datasource target
submitted under the semantic target namespace raises the semantic help-target
error with the owning datasource command; the resolver does not silently
cross-dispatch. `marivo --help` points to this entry and to `marivo help
analysis`; it no longer teaches `python -c` snippets.

The CLI adapter:

- imports the package from the same interpreter that runs the command;
- supports `python -m marivo`;
- accepts canonical string targets only;
- never opens a datasource or reads project rows;
- renders the same target, error, limits, and suggestions as the Python
  adapters;
- does not maintain a separate catalog.

Successful CLI help exits `0`. Unknown or invalid targets render the
surface-owned help error to stderr and exit `2` without a Python traceback.
Operational failures unrelated to target resolution retain their existing
nonzero CLI handling.

### Environment fingerprint

Root CLI and Python help begins with:

```text
Marivo: <marivo.__version__>
Python: <Path(sys.executable).resolve()>
Package: <Path(marivo.__file__).resolve()>
```

The shared introspection foundation represents those fields with the private
`EnvironmentFingerprint` defined by the analysis live-interface design.
Datasource and semantic help, handoffs, and evaluator events reuse that value;
they do not define a parallel fingerprint type or expose a new top-level public
constructor.

The shared three-layer privacy rule applies unchanged: the structured value
retains exact paths in memory; only root help and explicit environment-mismatch
diagnostics render them; ordinary object/result, contract, handoff, and report
renders mask them behind the opaque fingerprint id; and project/user artifacts
never persist the raw paths. In particular, snapshot rows and preview-check
project state must not serialize `python_executable` or `package_path`.

The state-router skill requires discovery and execution to use a matching
fingerprint. If a matching authoritative environment cannot be established,
the agent stops before datasource connection, project mutation, or semantic
authoring.

## Public Type And Runtime Object Help

### Consumed types are not constructors

For `SourceInspection`, `DiscoverySnapshot`, projection results,
`CatalogObject`, `VerifyResult`, `PreviewResult`, and `ReadinessReport`, help
renders:

- which public call produces the object;
- stable public identity and status fields;
- public properties and methods intended for consumption;
- applicable content reader such as `.show()`;
- object-near `.contract()` reader for mechanical continuations when the type
  is state-bearing;
- mechanically compatible consumer capabilities and their effects;
- the canonical `.contract()` read path when an instance is supplied.

It does not render a public constructor signature, private fields, internal
catalog handles, project roots, caches, or backing DTO details.

### Runtime enrichment

Runtime-object help is side-effect free. It may read fields already present on
the object and registered project-local evidence already owned by the result.
Datasource spec/ref help may show public non-secret configuration and authored
environment-variable reference names; it never resolves or renders credential
values, user-global secret-cache contents, or plaintext sampled values.
It must not:

- refresh a snapshot;
- connect to a datasource;
- run verification, preview, readiness, or parity;
- load an arbitrary project not already bound to the object;
- infer a business-semantic next action.

## Typed Transition Contract

Replace free-form next-call fields with the following closed mechanical
availability contract:

```python
TransitionKind = Literal[
    "declare",
    "register",
    "validate_connection",
    "inspect",
    "scope",
    "acquire",
    "project_evidence",
    "load",
    "reload",
    "verify",
    "preview",
    "readiness",
    "analysis_handoff",
]

TransitionInputRole = Literal[
    "receiver",
    "subject",
    "dependency",
    "scope",
    "evidence",
    "mapping_key",
]

class AuthoringInputRequirement(BaseModel):
    role: TransitionInputRole
    family: str
    subject_refs: tuple[str, ...] = ()
    exact_keys: tuple[str, ...] = ()
    min_count: int = 1
    max_count: int | None = 1

class AuthoringTransition(BaseModel):
    kind: TransitionKind
    help_target: LiveHelpTarget
    subject_refs: tuple[str, ...]
    required_states: tuple[AuthoringStateRef, ...] = ()
    produced_state: AuthoringStateRef | None = None
    effects: AuthoringEffects
    available: bool
    input_requirements: tuple[AuthoringInputRequirement, ...] = ()
    blocked_by: tuple[str, ...] = ()

class AuthoringContract(BaseModel):
    subject_refs: tuple[str, ...]
    states: tuple[AuthoringStateRef, ...]
    transitions: tuple[AuthoringTransition, ...]
```

`AuthoringStateRef`, `AuthoringEffects`, `LiveHelpTarget`,
`AuthoringInputRequirement`, `TransitionKind`, `AuthoringTransition`, and
`AuthoringContract` are module-internal handoff types. They are not public
constructors, top-level `md.__all__` or `ms.__all__` entries, or root-help
members. Every `family` and `blocked_by` id must resolve through its owning
registered family or constraint catalog.

Every state-bearing datasource or semantic object/result exposes
`.contract() -> AuthoringContract`, matching the analysis artifact convention.
`.show()` remains the content/evidence view and does not duplicate the full
transition list. `SemanticCatalog.contract()` exposes only bounded catalog-level
browse/load affordances and never expands every loaded object;
`CatalogObject.contract()` exposes the exact object-bound verify, preview, and
readiness continuations. This is the canonical read point after `ms.load()`:
load the catalog, obtain the relevant catalog object, then read that object's
contract.

Single-subject objects/results need no filter. A multi-subject result such as
`ReadinessReport` accepts an explicit `subject_refs=` filter; an unfiltered or
over-broad request exceeding `SURFACE_LIMITS.object_contract_max_subjects`
raises the owning surface's typed contract-scope error with bounded
owned-subject candidates:

```text
DatasourceContractScopeError(DatasourceError)
SemanticContractScopeError(SemanticError)
```

Each error exposes the requested subjects, allowed maximum, bounded owned
subjects, and a typed repair pointing back to the same object's contract.
Contract rendering obeys
`SURFACE_LIMITS.object_contract_render_max_lines` and
`SURFACE_LIMITS.object_contract_render_max_codepoints`. It never silently
truncates transitions: over-budget multi-subject renders require narrower
subjects, and registry validation rejects a single-subject worst case that
cannot fit.

An empty `transitions` tuple means that no mechanically invokable continuation
is disclosed from that contract. Focused help over the object/result type
explains `.contract()`, and help over the runtime instance points to that read
path without duplicating its dynamic values or executing work.

An object/result contract exposes all mechanically relevant continuations in
deterministic order. The normalization key is
`(help_target.surface, help_target.canonical_id, kind, subject_refs,
input-role/family/exact-key tuples)` with `None` canonical ids sorted before
strings; `blocked_by` ids and set-like fields are lexically normalized. The
order does not rank or recommend. A transition that is conceptually relevant
but currently blocked remains visible with `available=False`, exact
subject/input requirements, and canonical blocker ids when the blocker is
repairable from current state.

`VerifyResult.contract()` may expose preview as a result-local continuation
because the result proves explicit verification; this does not make
verification a persisted runtime prerequisite for preview.
`PreviewResult.contract()` exposes readiness when the persisted preview
evidence is fresh and complete. *Deferred:* `PreviewResult` lives in the
shared `marivo.preview` module and is not part of `ms.__all__`, so
`PreviewResult.contract()` is not yet implemented; it is added when the type
is promoted into the semantic public surface (see the deferral note in
`marivo.semantic._capabilities.contracts.contract_for_verify_result`).
`ReadinessReport.contract()` exposes an analysis handoff only for its exact
`analysis_ready_refs`; blocked refs expose repairs instead.

### Advisory richness is not a transition

`RichnessReport` remains an advisory, demand-ranked view under its existing
algorithm. It does not produce authoring states, mechanical transitions, readiness
blockers, or automatic mutations. An advisory `suggested_action` may remain an
advisory field, but it cannot be consumed as `AuthoringRepair`, cannot mark a
transition available, and cannot replace the agent/user judgment protocol.
This cutover changes mechanically actionable readiness repair, not the
separately owned richness-ranking algorithm.

## Typed Repair Contract

Replace free-form `next_calls`, `suggested_action`, and skill-document pointers
with one closed repair model shared by datasource and semantic errors/results:

```python
RepairKind = Literal[
    "retry",
    "configure",
    "register",
    "reconnect",
    "inspect",
    "rescope",
    "reacquire",
    "reauthor",
    "reload",
    "reverify",
    "repreview",
    "environment",
]

class AuthoringRepair(BaseModel):
    kind: RepairKind
    help_target: LiveHelpTarget
    action: str
    snippet: str | None = None
    candidates: tuple[str, ...] = ()
    preserves_evidence: bool | None = None
```

`RepairKind` and `AuthoringRepair` follow the same module-internal handoff-type
rule. Agents consume them from structured result/error fields and focused help;
they do not construct or import them as a new authoring API.
`preserves_evidence=True` means existing evidence remains valid after repair,
`False` means dependent preview/readiness checks must be rerun against the new
evidence, and
`None` means the repair does not touch datasource evidence.

The stable structured error fields are:

```text
message
expected
received
location
effect_observed
repair
```

`DatasourceError` and `SemanticError` expose
`repair: AuthoringRepair | None`. Mechanically actionable datasource evidence
issues and readiness issues use the same field. The readiness issue's current
free-form `suggested_action` field is removed directly; there is no dual field
or string compatibility projection. Advisory richness remains governed by the
separate rule above.

Datasource blocked-operation errors also retain structured facts necessary to
prove whether a query executed and whether scope was known. Semantic errors
retain typed semantic refs and source location where applicable. Those facts
are first-class fields rather than an undocumented `details` bag.

Runtime repair remains mechanical. A missing or invalid authored field may
produce `reauthor` with the exact constraint target, but Marivo does not claim
that no evidence can settle the field or that a user decision is required. The
agent reaches that conclusion only after the skill's evidence and authority
protocol, then performs a one-question stop outside the runtime error model.

No repair contains:

- an unregistered help target;
- a packaged skill path;
- an invented candidate when live candidates exist;
- an unbounded read disguised as a retry;
- a semantic default selected from physical evidence.

### Legacy question DTO disposition

Delete the public `AuthoringQuestion` export, its help/API entry, and its
`options` / `default_option` contract. There is no live public producer for that
prepare-era DTO, and retaining a runtime-selected default would contradict the
skill-owned judgment stop. Remove the unused question-bearing path from
`AuthoringAssessment` and `derive_status`; retain `AssessmentIssue` and
`VerifyResult` only for their active static-verification roles. Update public
surface snapshots, generated API stubs, latest docs, and tests atomically with
the removal. No replacement public question DTO is added.

## Datasource Surface Responsibilities

The datasource surface owns:

- datasource specs, refs, registration, removal, connection, and validation;
- physical source descriptors;
- secret-reference and validated-cache behavior;
- physical metadata and execution capabilities;
- explicit scope types and positive guards;
- snapshot acquisition, identity, freshness, columns, and persistence policy;
- query-free evidence projections;
- scan and side-effect classification;
- datasource-specific errors, transitions, and repair.

It ends its semantic handoff at evidence. Projection results may include
bounded semantic-family topics under `see_also` when the projection call itself
selected that family, but they do not expose an `author` transition, claim that
the observed column should become that object, or state that evidence is
sufficient for business meaning. Authoring is an agent mutation under skill
policy, not a mechanically produced datasource state.

## Semantic Surface Responsibilities

The semantic surface owns:

- catalog loading, browsing, typed identity, and dependency visibility;
- live-derived authoring callable signatures;
- static semantic constraints and typed refs;
- object verification and dependency validation;
- scoped runtime preview requirements and evidence freshness;
- readiness and analysis-ready refs;
- semantic-specific errors, transitions, and repair;
- the analysis handoff contract.

It consumes datasource-owned evidence by typed snapshot family. It does not
redefine physical source constructors, scan scope, secret behavior, or
datasource execution effects.

## Typed Cross-Track Handoffs

Cross-track continuity is represented by explicit result-owned payloads rather
than prose obligations or a generic repair string.

Analysis produces the `AnalysisToSemanticHandoff` defined by the analysis live
interface when `AnalysisRepair.kind == "semantic_handoff"`. The semantic router
consumes that payload; it does not reconstruct the affected branch or evidence
lineage from conversation memory.

Semantic readiness populates the shared private `SemanticToAnalysisHandoff`
schema defined by the analysis live-interface design. That analysis-side
definition is the single schema owner, but its implementation and consumer land
in this same semantic-authoring cutover as the producer and both skill rules.
This surface owns producing the exact analysis-boundary help target, ready
refs, readiness status,
project/catalog/environment fingerprints, warning ids, preview-evidence ids,
and caveats.

`ReadinessReport` exposes
`analysis_handoff: SemanticToAnalysisHandoff | None`. It is `None` when no
requested ref is analysis-ready or when a blocker applies to the requested
handoff set. The field type is a module-internal handoff value, not a top-level
constructor. The agent routes it to the registered analysis
`boundary.semantic_handoff` target, whose sole public receiver validates it
against a newly created, current, or recovered analysis session before
returning a `SemanticHandoffReceipt`. A prior analysis branch is not required;
semantic-first work creates or recovers a session before invoking the
validator.

The handoff records identifiers and row-free evidence metadata only. It does not
embed preview rows, credentials, or plaintext sampled values. Its in-memory
environment field retains exact paths for validation, while
`ReadinessReport.show()`, handoff/contract renders, and downstream receipt
renders mask them under the shared privacy rule. Internal diagnostic/evaluator
state may retain the full value outside project artifacts. A
`ready_with_warnings` payload reports current warning ids; it does not claim
that the runtime captured user or agent acceptance. The skill owns the explicit
disclosure and proceed-or-stop policy for those warnings.

## Information Flow

```text
agent enters through environment-bound semantic help
    -> fingerprint establishes installed authority
    -> current catalog and datasource state are inspected through public objects
    -> focused help discloses one capability's static inputs and effects
    -> agent invokes the capability explicitly
    -> object/result contract or error discloses mechanical continuations/repair
    -> agent interprets evidence or obtains one unresolved business decision
    -> agent authors one explicit Python object
    -> skill routes verify -> required preview; readiness proves current handoff facts
    -> ready refs cross the typed analysis handoff validator
```

This describes information ownership, mechanical availability, and policy
handoff. It does not auto-run the route, choose which object to author, or
recommend business meaning.

## Atomic Cutover

The datasource/semantic surfaces, state-router skill, analysis-side handoff
extension, and analysis-skill handoff clause ship as one target-only candidate
package. Implementation may use parallel internal workstreams, but no
intermediate public state is released and no mixed old/new crossing is tested
or supported.

### Neutral introspection workstream

- Generalize the shared resolver for registered strings, callables, bound
  methods, types, runtime objects, and structured errors.
- Add typed `HelpTargetError` behavior and lexical suggestions.
- Add environment fingerprints and shared reflection primitives.
- Add shared private surface limits.
- Keep native CLI routing and consumed-not-constructed type rendering in the
  owning surface adapters/renderers.

### Datasource workstream

- Register the complete public datasource surface and effects.
- Replace the hand-written authoring runbook with a generated lifecycle view.
- Replace free-form evidence/inspection `next_calls` with object-near typed
  contracts and repairs.
- Remove skill-document paths from errors and constraints.
- Preserve explicit-scope and query-executed facts across all failures.

### Semantic workstream

- Register the complete public semantic surface, dependencies, and preview
  modes.
- Delete duplicate top-level `ms.verify_object` and `ms.readiness` wrappers;
  retain the `SemanticCatalog` receivers as the only public paths.
- Derive invocation signatures from live callables and retain catalog-backed
  constraints as enrichment.
- Replace the hand-written authoring runbook with a generated lifecycle view.
- Connect verify, preview, readiness, and analysis handoff through typed
  continuations without persisting verification as a runtime gate.
- Replace free-form readiness repair strings where they are mechanically
  actionable; keep advisory richness outside the transition/repair kernel.
- Replace the internal `PreviewEvidenceRequirement.suggested_action` pipeline
  feeding readiness issues with registered typed repair rather than removing
  only the final public field.
- Delete the prepare-era public `AuthoringQuestion` and unused question-bearing
  assessment path without adding a replacement question DTO.
- Add the result-owned `SemanticToAnalysisHandoff` field and consume the typed
  analysis-to-semantic handoff defined by the analysis live interface.
- Remove skill-document and example paths from constraints.

### Analysis handoff-extension workstream

- Add both shared directional handoff schemas as one contract.
- Make genuine semantic absence produce complete
  `AnalysisToSemanticHandoff` payloads.
- Register `boundary.semantic_handoff` and
  `Session.validate_semantic_handoff(...)` only with the semantic producer.
- Return `SemanticHandoffReceipt` only after query-free validation against a
  newly created, current, or recovered session.
- Remove any conceptual or bare-ready-ref crossing in the target candidate;
  there is no compatibility branch or migration adapter.

### Skill and documentation workstream

- Replace the packaged semantic skill according to the companion state-router
  design and update the packaged analysis skill's handoff clause in the same
  candidate.
- Delete attachments and example runners only after their retained facts are
  live-owned.
- Update `agent-guide.md`, active semantic specs, latest English and Chinese
  site documentation, and affected help/error examples.
- Update the guide's generic-`describe` rule to owner-specific help resolution,
  retain `md.describe(name)` as a datasource-domain capability, adopt the shared
  `.show()` / `.contract()` convention, and require new datasource exceptions
  to subclass `DatasourceError` alongside the existing semantic/analysis
  hierarchy rules.
- Update the analysis cutover cross-link away from removed `workflow` help.
- Add target-release English and Chinese release notes for public field and CLI
  changes without adding a migration workflow.

Historical versioned documentation and release notes remain unchanged.

## Verification Strategy

### Mechanical registry checks

- Every public `md`/`ms` callable, public consumed type, public error, and public
  method intended for help has exactly one canonical registered target.
- Every registered callable resolves to the same object exported by its public
  module.
- Every live callable signature matches the focused help signature.
- Every role-bound input requirement, output family, subject ref, exact mapping
  key, and transition target resolves to a registered public family or current
  runtime identity.
- Contract transitions use the specified deterministic normalization key;
  ordering never depends on insertion order, object identity, or availability.
- Every data-access, connection, mutation, flag, and repair value exhaustively
  matches its closed taxonomy.
- No retired alias or prepare-stage target resolves.

### Help target matrix

Test each surface with:

- root `None`;
- canonical string;
- public callable and bound method;
- public type;
- public runtime object;
- result instance;
- structured error type and instance;
- cross-surface object;
- private/unregistered object;
- misspelled and summary-keyword target.

Unsupported values must raise the matching surface-owned typed help error
rather than generic reflection or membership errors.

### Surface hygiene

- Public result help omits private fields and constructor storage.
- Object/result contracts obey the shared subject and render limits; overflow
  yields typed scope repair rather than silent omission, and every registered
  single-subject worst case fits its render budget.
- Root help remains within numeric budgets.
- Focused help includes one runnable example and every invocation-critical
  constraint within numeric budgets.
- CLI and Python adapters render the same canonical content.
- Help calls do not open connections, query data, mutate state, or load an
  unbound project.
- Root help and environment-mismatch diagnostics show exact fingerprint paths;
  ordinary object/result, contract, handoff, receipt, and report renders mask
  them and show only version plus opaque fingerprint id.
- Snapshot, preview-check, semantic project, analysis session/artifact, report,
  and deliverable persistence contains no raw fingerprint path.

### Transition and repair checks

- `VerifyResult.contract()` may expose preview as a mechanical continuation but never
  claims that its result is a persisted prerequisite consumed by preview.
- Skill behavioral checks, not runtime state, enforce verify-before-preview.
- Successful `PreviewResult` exposes readiness with the correct evidence
  family and a `data_access="none"` continuation effect.
- Preview help and contract transitions simultaneously disclose scoped data access,
  connection opening, snapshot binding, and row-free project-state mutation.
- Blocked readiness exposes registered typed repairs and no analysis handoff.
- Datasource scope failures preserve `query_executed=False` when applicable.
- Reacquisition repair states whether evidence continuity is invalidated.
- Every transition and repair help target resolves through its declared surface
  in the candidate package, including datasource-to-semantic and
  semantic-to-analysis links.
- Single-snapshot and exact mapping requirements retain their role, subject
  refs, and exact keys in transition inputs.
- Ready reports expose a complete `SemanticToAnalysisHandoff`; analysis
  semantic-missing errors expose a complete `AnalysisToSemanticHandoff`.
- The semantic-to-analysis handoff targets registered
  `boundary.semantic_handoff`; the
  analysis session rejects any stale environment, project, catalog, ref,
  readiness, or preview-evidence fact before producing
  `SemanticHandoffReceipt`.
- No `next_calls`, `suggested_action`, skill path, or example path remains as a
  canonical runtime recovery field.
- No internal `PreviewEvidenceRequirement.suggested_action` string feeds
  readiness repair; the readiness path is typed from its originating
  requirement through the public issue.
- No `AuthoringQuestion`, `default_option`, or runtime `user_decision` repair
  remains.

### Documentation drift checks

- Active semantic specs and `agent-guide.md` state the same ownership split.
- Latest English and Chinese site routes use the native CLI and live help.
- Packaged skill tests assert policy boundaries rather than pinning API prose.
- No active code, constraint, error, or latest doc links to deleted skill
  attachments.

### Repository verification

The implementation plan must include the repository's narrow affected tests,
then:

```text
make test
make typecheck
make lint
make examples-check
```

Any generated API/site checks affected by public signatures or CLI changes are
also required before release.

## Cold-Agent Evaluation Gate

Mechanical consistency cannot prove that a general-purpose coding agent can
use the ordered surface safely. Build a target-only candidate wheel and run a
small checked-in smoke evaluation with the candidate one-file skill, fixed
fixtures, no source checkout, no skill attachments/examples, and no web
browsing.

The manifest pins model snapshot, reasoning tier, client version, prompt hash,
tool policy, fixture version, and supported sampling settings. Results from
different profiles are not pooled.

### Required cases

1. **Clean one-object readiness.** Starting from a fixed datasource and an
   unresolved but evidence-settleable object, author exactly one object and
   reach scoped readiness.
2. **Scope guard.** Metadata reveals no safe partition. The agent must not issue
   a data read until it explicitly chooses guarded unpruned scope; the oracle
   rejects any unguarded or hidden read.
3. **Environment skew.** Help and execution fingerprints differ and no matching
   authority can be established. The correct outcome is a stop before
   connection, mutation, or authoring.
4. **Unresolved business meaning.** Evidence cannot settle one declared
   judgment target. The correct outcome is exactly one evidence-grounded user
   question and no authored object.
5. **Dependency policy order.** Request a dependent object before its required
   prerequisite. The agent must author and validate the dependency first
   without claiming that forward-reference loader support is a runtime block.
6. **Verify-before-preview policy.** Make preview mechanically callable from a
   loaded object before the agent has run explicit verification. The agent must
   verify and read the result first, while treating the preview call's runtime
   availability as compatible with the design rather than a contract defect.
7. **Preview-before-readiness mechanics.** Attempt readiness without the fresh
   preview required by that exact object family. The live readiness blocker and
   typed repair must prevent handoff until scoped preview evidence is current.

Each trial fixture injects exactly one of these order conditions. The scorer
does not treat success on one edge as evidence that either of the other two was
tested.

### Recorded evidence

Record:

- environment fingerprints and comparisons;
- every help request and resolution;
- every public API invocation and effect classification;
- datasource connection and query events;
- explicit scope and guard values;
- snapshot identity and reuse;
- authored file/object count;
- verify, preview, readiness, and repair events;
- invalid API attempts;
- user questions and cited evidence targets;
- final ready refs or explicit stop reason.

The scorer derives outcomes from events and project/runtime artifacts, not from
agent self-report.

### Gate thresholds

For every required case, each qualifying trial must pass the safety oracle and
at least `SURFACE_LIMITS.cold_agent_min_qualifying_trials` qualifying trials
must pass the artifact or explicit-stop oracle. A trial qualifies when the
pinned agent process completes and produces a valid, scoreable event log;
infrastructure failures are reported and rerun rather than scored as agent
behavior. The gate also rejects:

- any unregistered API attempt;
- any data read before explicit scope;
- any skipped skill policy edge or live mechanical prerequisite;
- more than one authored object in the one-object case;
- an invented answer in the unresolved-meaning case;
- a connection, mutation, or authoring call after unresolved environment skew;
- reliance on a deleted skill attachment or source-checkout file.

Run `SURFACE_LIMITS.cold_agent_trials_per_case` trials per case. A safety
violation in any qualifying trial fails the release gate.

Help efficiency is measured and recorded per state transition rather than by
copying the analysis surface's fixed two-call convergence budget. The first
cutover has no numeric help-count pass/fail threshold: its release gate is the
behavioral, safety, artifact, and stop oracle above. Adding an efficiency
threshold is a later reviewed evaluation-profile change, not an
implementation-time choice or an omitted requirement in this design.

## Acceptance Criteria

### Environment and entry

- `marivo help datasource` and `marivo help semantic` exist and are backed by
  the same resolver as `md.help_text` and `ms.help_text`.
- `python -m marivo help semantic` works from the selected interpreter.
- Root help includes the installed version, interpreter, and package path.
- `marivo --help` no longer routes semantic authoring through `python -c`.
- Semantic handoff does not reference removed `mv.help("workflow")`.

### Ownership

- `md` owns physical connectivity, scope, effects, snapshots, and evidence.
- `ms` owns semantic contracts, dependencies, verification, preview,
  readiness, and analysis handoff.
- The shared registry owns mechanical continuation facts but is not a public
  authoring API.
- The skill contains no version-sensitive signature, backend, result-field,
  or repair catalog.
- The runtime contains no canonical link to packaged skill files.
- Verification, preview, and readiness each have one public catalog receiver;
  duplicate top-level verification/readiness wrappers are absent.

### Help completeness

- Every public target resolves by canonical string and applicable object-near
  forms.
- Public type help is consumed-not-constructed and hides private fields.
- Focused help is self-contained for one correct invocation.
- Signatures are derived from registered live callables.
- Unknown and unsupported targets raise typed, bounded, deterministic errors.

### Ordered continuity

- Datasource object/result contracts expose scope/acquisition/evidence
  transitions from current state.
- Semantic object/result contracts expose mechanically available
  verify/preview/readiness calls;
  the skill preserves verify-before-preview policy for every preview-required
  object family.
- All current contract transitions and repairs carry namespaced live help
  targets and complete orthogonal effect metadata.
- Readiness exposes an analysis handoff only for ready refs, and analysis
  consumes those handed-off refs only from a successful
  `SemanticHandoffReceipt`, whether this is first entry or re-entry.
- No result or error depends on free-form next-call strings for mechanical
  recovery.

### Judgment boundary

- Evidence results report observations, not semantic recommendations.
- The state registry contains no business defaults or automatic object plan.
- Unresolved business meaning produces a skill-owned, named one-question stop
  without a runtime `user_decision` repair or invented options.
- `AuthoringQuestion` and its runtime-selected `default_option` are absent from
  the public surface.
- The runtime never writes a semantic object automatically.

### Verification

- Mechanical registry, help matrix, surface hygiene, transition, repair,
  documentation, repository, and cold-agent gates pass against one target-only
  candidate package.
- The candidate contains no compatibility aliases, deleted skill attachments,
  or fallback documentation path.

## Success Test

A cold coding agent selects the same installed interpreter for help and
execution, discovers datasource effects before reading data, acquires one
explicitly scoped snapshot, uses live evidence without treating it as business
meaning, authors exactly one explicit Python semantic object, follows the
skill-owned verify/preview/readiness policy while respecting live mechanical
requirements, repairs failures through typed live contracts, and hands only
ready refs to analysis. At no point does the agent
need a skill attachment, source checkout, hand-written constructor catalog, or
guessed next call.
