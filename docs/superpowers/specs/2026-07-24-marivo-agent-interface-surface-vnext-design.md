# Marivo Agent Execution Continuity Interface Design

Status: implemented through Phase 5; unified-help breaking cutover complete

Date: 2026-07-24

Phase 4 candidate: `797f81a762d65b388e0ab6607f1692f4c5df80a8`

The deterministic gates, installed-wheel smoke, and complete frozen DAComp-DA
comparison pass for this exact candidate. Across three trials for each of ten
tasks, the candidate completed 29/30 trials versus 26/30 for the frozen
baseline, raised the non-visual rubric median from 91.15 to 94.38, and reduced
median help and failed calls without increasing median turns or tool calls.
Transcript review found remaining signature, help-routing, datasource
connection/ref, and task-specific composition friction, but no repeatable
contract-level regression that blocks cutover. The external execution record,
adjudication rules, per-task dispersion, and follow-up list live in
`../../../../marivo-agent-evals/suites/dacomp_da/mvp_v1/PHASE4_AGENT_SURFACE_REPORT.md`.

The implemented post-cutover follow-up keeps these boundaries narrow:

- `Session.observe(metrics=...)` names its scalar-or-sequence input consistently
  while retaining positional single-metric calls and rejecting the old
  `metric=` spelling;
- analysis help rejects foreign callables with an exact owning-surface
  continuation instead of copying help across surfaces;
- `md.inspect(...)` remains a strict datasource-ref entry point and now teaches
  direct inspection plus the complete bounded inspection actions.

The Phase 5 amendment in this document supersedes only the earlier public help
ownership and environment-entry contract. It does not invalidate the Phase 4
candidate, evaluation evidence, typed semantic input work, result protocols,
or operator behavior. Where the completed Phase 1 help entry conflicts with
Public Contract 1 or Phase 5 below, the Phase 5 contract is normative.

## Summary

Redesign the public Marivo agent interface around execution continuity:

```text
environment-bound entry
  -> loaded semantic object
  -> valid typed operator invocation
  -> bounded artifact read
  -> mechanically valid continuation or executable repair
```

The current capability kernel, typed artifacts, evidence boundary, and
non-prescriptive ownership model remain valid. The missing property is that a
coding agent cannot always move from the object currently in hand to the next
correct call without repeated help queries, reflection, failed guesses, or a
terminal escape.

This design closes that gap by:

- replacing the three public `md.help(...)`, `ms.help(...)`, and `mv.help(...)`
  surfaces with one canonical `marivo.help(...)` entry while preserving
  surface-native descriptor ownership behind private render adapters;
- making `python -m marivo help` a bootstrap-only environment check that
  teaches the verified Python import and never competes with focused Python
  help;
- accepting an exact loaded `CatalogEntry[K]` at catalog-bound runtime inputs
  that currently accept its corresponding `Ref[K]`, while normalizing
  immediately to `Ref[K]` and keeping persistence ref-only;
- allowing typed catalog collections to resolve either a local name, a full
  semantic path, or an exact same-kind ref;
- routing callables, runtime objects, semantic refs/entries, and errors to their
  canonical help owner without pretending that they are all capability
  descriptors;
- including a small set of runnable focused-help examples where supported
  invocation forms differ enough that one example is misleading;
- making every public contract object a bounded agent result with executable
  continuation or repair information;
- adding the missing basic tabular reads to `RawSqlResult` while keeping it
  explicitly terminal;
- distinguishing missing temporal semantics from an invalid analysis call
  before backend execution;
- changing the packaged analysis skill from proactive per-API help lookup to
  environment verification plus object-near, error-driven recovery.

This is an interface and guidance redesign. It does not add a typed regression
capability, change operator calculations, infer business semantics, introduce
an analysis planner, or allow terminal results to re-enter typed analysis.

## Evidence And Design Trigger

The design is informed by a governed ten-task DAComp-DA validation run using
Marivo revision `9908a5a074c7c10958ee024f058a02713b2ce5b7` and a paired raw
SQLite arm under the same model configuration.

The aggregate result was:

| Metric | Marivo | Raw SQLite | Difference |
| --- | ---: | ---: | ---: |
| Agent turns | 469 | 252 | +217 |
| Tool calls | 460 | 241 | +219 |
| Bash calls | 386 | 221 | +165 |
| Help-or-failed Bash calls | 114 | 14 | +100 |
| Failed tool calls | 59 | 14 | +45 |
| Completed reports | 9 | 10 | -1 |

The near equality between extra agent turns and extra tool calls shows that the
gap is not a harness turn-accounting artifact. The Marivo arm performed more
real discovery, inspection, failure, and recovery work.

The observed friction clustered into five interface classes:

1. **Cold entry and help discovery**
   - agents tried `help(marivo.*)`, `dir(...)`, non-canonical CLI spellings, and
     multiple help targets before reaching the live analysis surface;
   - the packaged skill's requirement to follow focused help for API contracts
     encouraged proactive help fan-out.
2. **Catalog and identity navigation**
   - agents passed full semantic paths to local-name-only collection lookups;
   - agents passed `CatalogEntry` values to analysis, then learned through a
     failure that `.ref` was required;
   - agents confused `path`, `key`, entry properties, and kind-specific
     factories while reconstructing identity already carried by the entry.
3. **Operator composition**
   - multi-metric frames required projection before downstream operators, but
     the current dynamic contract did not provide a direct executable
     projection-and-call repair;
   - a task discovered correlation help but never invoked `correlate`, instead
     replacing the required analysis with repeated grouped observations;
   - one minimal focused-help example did not teach both temporal and
     cross-sectional correlation shapes.
4. **Result protocol gaps**
   - agents treated `RawSqlResult` as a `DataFrame` and probed `.shape`,
     `.dtypes`, and other familiar attributes;
   - `ArtifactContract` is structured but does not itself implement the bounded
     result protocol, so `contract()` is less usable than the design intends.
5. **Semantic suitability and capability gaps**
   - ordinary dimensions named Date or Year were not time dimensions, so
     windowed operations failed only after the agent attempted them;
   - tasks requiring analysis not represented by a typed operator escaped to
     terminal custom code and accumulated environment-specific failures.

The last category contains both interface and product-capability concerns. This
design addresses semantic suitability diagnostics and terminal boundary truth.
It explicitly defers new statistical operators, including typed regression.

## Relationship To Existing Designs

This document refines, rather than replaces wholesale:

- `docs/specs/agent-friendly-public-surface.md`;
- `2026-07-13-marivo-analysis-interface-surface-design.md`;
- `2026-07-13-marivo-analysis-boundary-kernel-design.md`;
- `2026-06-12-semantic-analysis-interface-unification-design.md`;
- `2026-06-17-analysis-result-surface-consistency-design.md`.

The following existing decisions remain authoritative:

- `marivo.datasource -> marivo.semantic -> marivo.analysis` is one-directional;
- the live package owns API contracts and runtime facts;
- the skill owns hard boundaries, routing triggers, evidence continuity, and
  closeout obligations;
- the agent owns question interpretation, operator choice, hypotheses, stop
  criteria, and conclusions;
- the capability registry is private and is the single static source for help,
  family compatibility, affordances, and repair links;
- `Ref[K]` is the only persisted semantic identity;
- artifacts remain immutable;
- `to_pandas()` and `md.raw_sql(...)` remain terminal boundaries;
- terminal outputs do not re-enter typed Marivo analysis;
- no public natural-language planner or ranked next-action API is introduced.

This document changes or strengthens these prior decisions:

1. **Catalog-bound runtime inputs are no longer ref-only.**
   An exact loaded `CatalogEntry[K]` is accepted as an ephemeral typed handle
   and normalized to `Ref[K]` at the public boundary.
2. **Round-trip efficiency is measured through composition.**
   "Root help plus one focused target reaches the first valid observe" remains a
   useful lower bound but is not sufficient acceptance for the whole surface.
3. **Focused help covers important invocation forms.**
   A capability may carry several runnable examples when its supported forms
   require different argument structures. The registry does not attempt to
   enumerate every legal parameter combination.
4. **Dynamic contracts are agent results.**
   A structured contract that cannot be inspected through bounded
   `repr()`/`render()`/`show()` does not close progressive disclosure.
5. **Help is demand-driven after entry.**
   The skill must not instruct an agent to query focused help proactively for
   every API contract when the current object, contract, or structured error
   already provides the required facts.

These refinements become normative only when the coordinated implementation
cutover is approved and completed.

## Coordination And Cutover Ordering

The in-progress Event/Lifecycle work is the sequencing authority for the active
checkout. This interface redesign does not block an Event/Lifecycle public
vertical phase, and it does not require all five Event/Lifecycle phases to land
as one unit. Instead:

1. active Event/Lifecycle work first reaches either a complete public vertical
   phase boundary or is explicitly parked with no partial public surface;
2. Phase 0 of this design freezes its consumer inventory against that exact
   revision;
3. every qualifying Event/Lifecycle catalog-bound runtime input plus its public
   help/result surface joins this design's atomic cutover;
4. later Event/Lifecycle phases adopt this design's final input, help, result,
   and repair contracts from their first public release.

This means `EventEntry`, `Ref[EventKind]`, Event catalog collections,
Event/Lifecycle analysis constructors, and any Event/Lifecycle reducers present
at the frozen revision are not optional inventory items. The surface cutover
must not be released against a revision containing partially registered Event
objects or capabilities.

The optional ontology extension remains independent and does not block this
design. If its public `discover.semantic_hypotheses` bridge is present in the
candidate revision, its qualifying semantic inputs and help examples join the
frozen inventory. Otherwise it adopts the final contract when it later ships.
Ontology never participates in executable semantic normalization, readiness, or
operator selection.

## Ownership Model

| Owner | Owns | Must not own |
| --- | --- | --- |
| Top-level module | Canonical help routing, global help topics, and object briefing composition | Domain descriptor semantics, analysis workflow, or semantic inference |
| CLI help bootstrap | Environment fingerprint and the verified `import marivo; marivo.help(...)` handoff | Focused target help or object introspection |
| Capability kernel | Canonical ids, public entrypoints, input/output families, help targets, examples, constraint links | Natural-language planning or ranked recommendations |
| Semantic catalog | Loaded business objects, exact identity, graph navigation, available axes, readiness facts | Operator selection |
| Public input normalizer | Entry/ref ownership, kind, membership, and canonical ref extraction | String-to-kind guessing or semantic fallback |
| Focused help | Static invocation contract and representative runnable examples | Current artifact state |
| Artifact `show()` | Bounded facts about the current result | API manuals or recommendations |
| Artifact `contract()` | Mechanical compatibility, bindings, preconditions, terminal ports, repairs | Ranked next steps |
| Structured error | Expected/received/location plus executable repair from current state | Skill-file references or guessed workarounds |
| Boundary skill | Environment verification, hard boundaries, handoffs, evidence continuity, closeout | API inventory, signatures, or proactive help sequence |
| Agent | Investigation plan, method choice, synthesis, interpretation, stop decision | Redefining semantic truth or hiding blockers |

## Goals

### Execution continuity

At every public object boundary, the agent can determine one correct next call
without private reflection or reconstructing identity already carried by the
object.

### Typed convenience without semantic ambiguity

Accepting a loaded catalog entry must not weaken kind safety, catalog
membership, immutability, lineage, or persisted identity.

### Canonical help ownership

`marivo.help(...)` is the only public Python help entry. Equivalent spellings of
one callable resolve to one surface-native capability descriptor through the
shared resolver. Runtime objects, semantic entries/refs, and error instances
resolve to their own canonical type, object, or repair briefing. The top-level
router composes help; it does not flatten datasource, semantic, and analysis
descriptor models or invent an operator identity for a semantic object that
has several legal consumers.

### Focused-help sufficiency

Focused help must state the live signature and invocation-critical constraints,
and include more than one runnable example when the callable has a small number
of materially different calling forms that agents otherwise confuse.

### Executable recovery

Every repair that claims a retry is possible must contain a bounded
copy-pasteable snippet derived from current state. A repair without enough
information to run is an inspection link, not a retry.

### Data-bearing result consistency

`BaseFrame` artifacts and `RawSqlResult` expose the same basic row/column reads
needed for inspection, while terminal and typed continuity remain explicit.

### Agent-visible improvement

The redesign should reduce observable interaction friction. External evaluation
may distinguish:

- cold-entry turns;
- productive analysis turns;
- help turns;
- failed/recovery turns;
- terminal escape rate;
- typed operator hit rate;
- completion and rubric quality.

## Non-Goals

This design does not:

- add typed regression, generalized linear models, model comparison, delta
  R-squared, interaction modeling, or another new statistical operator;
- add a public natural-language planner or question-to-operator router;
- expose the private capability registry through `marivo.describe(...)`, JSON, or a
  public mutable registry;
- accept bare semantic-id strings in analysis operators;
- infer that an ordinary dimension named Date, Year, Month, or Timestamp is a
  time dimension;
- silently fall back from a typed operator to pandas, raw SQL, or another
  terminal path;
- permit `RawSqlResult` or pandas data to become canonical artifact input;
- change analysis calculations, evidence extraction, artifact identity, or
  persistence schemas except where entry values are normalized before those
  layers;
- make help prose or layout a stable machine-readable schema;
- expose a public `help_text`, JSON help projection, or mutable help registry;
- add `marivo.load(kind=...)` or another top-level dispatcher that replaces
  typed datasource and semantic operations;
- add shape selectors, exhaustive help matrices, or other public APIs solely to
  make an evaluation harness easier to score;
- turn catalog cards, readiness reports, evidence summaries, or paginated
  listings into DataFrame-like results;
- modify the DAComp task rubric or use the benchmark as a replacement for
  deterministic repository tests.

The deliberate string asymmetry is kind ownership, not inconsistency. A typed
`CatalogCollection[K]` already fixes one semantic kind and may therefore resolve
a string within that kind and scope. Analysis parameters such as
`dimensions: list[SemanticInput[FieldKind]]` admit several kinds and do not own a
single lookup scope, so accepting a bare string there would require guessing.

## Public Contract 1: Environment-Bound Entry

### Top-level Python API

The top-level package exports one canonical help callable in addition to its
version:

```python
import marivo

marivo.help()
marivo.help("authoring")
marivo.help("analysis.observe")
```

`marivo.help(target)` prints bounded deterministic help and returns `None`.
There is no public `marivo.help_text`, structured help projection, or format
selector. The CLI and tests may call a private string renderer; test
convenience does not justify a second public API.

The public annotation is a closed union of the registered string, callable,
type, `Ref`, `CatalogEntry`, public result, and public error target families
plus `None`. It does not use `Any`, an unrestricted `object`, duck typing, or a
stringly typed object surrogate.

The datasource, semantic, and analysis modules no longer expose `help` or
`help_text`. Removing those names from `__all__` is insufficient: after the
breaking cutover, all of these checks are false:

```python
hasattr(md, "help")
hasattr(ms, "help")
hasattr(mv, "help")
```

The private datasource, semantic, and analysis registries and renderers remain
the authoritative owners of their descriptor semantics. The top-level help
router lazily adapts to those owners; it does not copy or flatten them.

### Bootstrap-only CLI

The canonical environment entry is:

```text
<selected-python> -m marivo help
```

The equivalent console-script command may be used after its interpreter is
known:

```text
marivo help
```

The command accepts no track or target. It prints:

- the Marivo version;
- the selected Python executable;
- the loaded package path;
- the supported execution imports;
- `marivo.help()` as the only focused-help handoff;
- concise examples for a string target and an object target.

The execution imports remain:

```python
import marivo
import marivo.datasource as md
import marivo.semantic as ms
import marivo.analysis as mv
```

`marivo --help` continues to describe CLI syntax. `marivo help` verifies the
installed Python environment and teaches the Python API. These are distinct
roles.

The CLI does not accept any of these forms:

```text
marivo help observe
marivo help analysis observe
marivo help semantic load
marivo help datasource inspect
```

Extra arguments exit with status 2 and state that CLI help is bootstrap-only,
then show `import marivo; marivo.help(...)`. The CLI never implements focused
target routing, so the agent does not choose between two partially overlapping
help systems.

The environment fingerprint remains diagnostic state and must not enter
persisted artifacts, evidence, or user deliverables.

### Global target grammar

`marivo.help()` renders a short global index organized around datasource
evidence, semantic authoring, and analysis. It is not the concatenation of the
three native root pages. It stays inside the shared root-help budget and points
to global composition topics or qualified focused targets for detail.

Qualified string targets use:

```text
datasource.<canonical-id>
semantic.<canonical-id>
analysis.<canonical-id>
```

Only the first surface prefix is consumed by the global router. The remainder
is resolved by the owning native surface, so nested ids such as
`analysis.events.match` retain their full domain identity.

For an unqualified string:

1. one owner routes automatically;
2. no owner raises one global help-target error with bounded suggestions;
3. multiple owners render an explicitly registered global composition topic
   when one exists;
4. otherwise a global ambiguity error lists qualified targets and never picks
   the first surface.

Unknown and ambiguous public targets raise one top-level help-target error
contract. Surface-specific help-target exceptions are implementation details
and do not cross the `marivo.help(...)` boundary.

These targets resolve to the same analysis descriptor:

```python
marivo.help("observe")
marivo.help("analysis.observe")
marivo.help("Session.observe")
marivo.help("session.observe")
marivo.help(mv.Session.observe)
marivo.help(session.observe)
```

This is target normalization, not several public capability aliases. Rendered
help names the native canonical id and public invocation.

### Global composition topics

`marivo.help("authoring")` is one bounded end-to-end workflow topic:

```text
datasource declaration
  -> inspection
  -> explicit scoped sampling
  -> evidence projection
  -> semantic authoring
  -> verify
  -> preview
  -> readiness
  -> analysis handoff
```

Its focused components remain addressable as
`datasource.authoring` and `semantic.authoring`. The page composes two domain
state machines; it does not merge their state or ownership.

`marivo.help("load")` is a comparison topic, not an operation dispatcher. It
distinguishes:

```text
datasource.load -> md.load() -> DatasourceCatalog
semantic.load   -> ms.load() -> SemanticCatalog
```

`md.load()` and `ms.load()` remain separate typed operations.
`marivo.load(kind=...)` does not exist.

The former domain `help` and `help_text` descriptors are removed rather than
becoming global composition topics.

### Target-kind equivalence

| Target | Canonical help result |
| --- | --- |
| Equivalent strings, callables, or bound methods for one public call | One surface-native capability descriptor |
| Public result object or type | Its registered type contract |
| Exact `Ref` or `CatalogEntry` | One canonical semantic-object briefing |
| Error instance with a repair help target | Concrete error briefing plus the qualified canonical target |
| Error instance without a repair help target, or an error class | Generic registered error contract |

An error without a concrete repair target does not guess the capability that
raised it.

### Canonical semantic-object briefing

`marivo.help(ref)` and `marivo.help(entry)` use one object-centered renderer.
The namespace from which the object originated does not change its facts.

A bare `Ref[K]` is pure typed identity. Its help may show:

- kind and path;
- how to load the semantic catalog and require the ref;
- bounded object-inspection calls.

It must not claim that the ref exists in a current project, that it is ready,
or that an analysis operator is currently legal. It must not discover or load a
project implicitly.

A `CatalogEntry[K]` is already owned by one compiled catalog. Its help may
compose:

- identity from `entry.ref`;
- business definition, unit, guardrails, composition, and lineage from
  `entry.details()`;
- current semantic continuations from `entry.contract()`;
- conditional, kind-specific analysis consumers from the analysis registry.

Potential analysis consumers are labeled conditional until readiness proves
the handoff. Datasource connectivity and inspection evidence are never inferred
from a semantic ref or entry. The renderer performs no datasource query.

The object briefing is a composition boundary:

| Fact | Authoritative owner |
| --- | --- |
| Kind and path | `Ref` |
| Definition, unit, guardrails, and lineage | `CatalogEntry.details()` |
| Semantic continuation | `CatalogEntry.contract()` |
| Analysis readiness | `ReadinessReport` |
| Operator contract | Analysis capability registry |
| Physical evidence | Datasource runtime result and contract |

## Public Contract 2: Typed Semantic Inputs

### Input type

Public APIs consuming a loaded semantic object use:

```python
type SemanticInput[K] = Ref[K] | CatalogEntry[K]
```

This is a type-level input contract, not a new top-level public constructor or
help-index entry.

The contract applies to runtime calls that already have one authoritative
compiled catalog against which an entry can be checked:

- `Session.observe.metrics`;
- `Session.observe.dimensions`;
- `Session.observe.time_dimension`;
- `Session.observe.slice_by` keys;
- attribution/decomposition axes;
- discovery search-space axes;
- transform slice keys;
- Event/Lifecycle semantic inputs, participant axes, and reducers present in the
  Phase 0 frozen public inventory;
- the optional ontology-to-analysis bridge when it is present in the candidate
  revision;
- `SemanticCatalog.verify`, `preview`, `preview_many`, and `readiness`;
- future runtime operators only when they have the same authoritative catalog
  context and explicitly opt into this boundary.

`SemanticInput[K]` applies only where a parameter already consumes a top-level
catalog-backed `Ref[K]` with a corresponding registered `CatalogEntry[K]`.
Nested typed handles and analysis values such as `PatternStep`,
`ModelStateHandle`, `ProjectionStateHandle`, `SubjectSet`, or an ontology
candidate remain their own exact types and are not widened by this alias.

Semantic authoring decorators and constructors remain ref-only. They run before
there is an authoritative compiled catalog, so accepting a loaded entry there
would introduce the wrong lifecycle. Datasource APIs also retain their existing
name/ref contracts unless a separate datasource design changes them. Phase 0
freezes the exact runtime-consumer list; it does not widen every public function
that happens to mention `Ref`.

### Normalization

One shared internal normalizer performs:

1. separate closed runtime discrimination for refs and entries;
2. catalog ownership validation for `CatalogEntry`;
3. exact kind validation;
4. membership validation in the current compiled catalog;
5. extraction of the canonical `Ref[K]`.

The runtime checks are intentionally different:

- a ref is accepted only when `type(value) is Ref`;
- an entry is accepted when `isinstance(value, CatalogEntry)`, its concrete
  class is one of the registered entry classes in the catalog's closed
  kind-to-entry mapping, and that class agrees with both the entry ref kind and
  the parameter's expected kind;
- arbitrary subclasses outside that closed mapping and duck-typed values with a
  `.ref` attribute are rejected.

The implementation must not use `type(value) is CatalogEntry`; real loaded
objects are concrete subclasses such as `MetricEntry` and `EventEntry`.

After normalization, planners, executors, persistence, evidence, replay, and
lineage see only refs. They do not accept the union and do not carry catalog
entry instances.

### Invalid inputs

The boundary rejects:

- bare strings;
- entries from a different compiled catalog/session;
- stale entries from an earlier compilation of the same project;
- an entry or ref of the wrong kind;
- unknown refs;
- mutable or duck-typed objects that happen to expose `.ref`.

Every rejection includes:

- parameter location;
- expected kind and accepted public input types;
- received type, kind, and ref when available;
- current candidates of the correct kind;
- an exact retry snippet only when the correction is mechanically unique.

Stale same-path reacquisition is retryable. Wrong-kind, missing, or ambiguous
inputs do not select a semantic replacement; they return an inspection or
user-choice repair with bounded candidates.

### Entry lifetime and recompilation

A `CatalogEntry` is an ephemeral handle owned by one compiled catalog instance.
Its validity ends when that catalog is replaced or recompiled, even when a
same-kind object with the same path still exists. The boundary never silently
rebases a stale entry because doing so would hide a compilation change.

When the current catalog still contains the exact same-kind path, the stale-entry
error includes the current entry and a retry that reacquires it, for example:

```python
exam = session.catalog.metrics.get("students.average_exam_score")
frame = session.observe(exam, analysis_purpose="Inspect exam scores")
```

If the path no longer exists or changed kind, the error renders bounded current
same-kind candidates and requires an explicit new choice.

### Canonical example

```python
session = mv.session.get_or_create(name="student_analysis")
exam = session.catalog.metrics.get("students.average_exam_score")
student = session.catalog.dimensions.get("students.students.student_id")

frame = session.observe(
    exam,
    dimensions=[student],
    analysis_purpose="Inspect exam scores by student",
)
```

The equivalent explicit-ref form remains legal:

```python
frame = session.observe(exam.ref, dimensions=[student.ref])
```

Neither form has different execution or persistence semantics.

## Public Contract 3: Catalog Lookup And Object Cards

### Collection lookup grammar

A typed `CatalogCollection[K]` accepts:

```python
collection.get(local_name: str)
collection.get(full_path: str)
collection.get(ref: Ref[K])
```

The collection already owns the expected kind, so accepting a full path does
not guess kind from a string.

Resolution rules:

1. an exact same-kind ref performs exact membership lookup within the
   collection's current scope;
2. a string containing path separators is treated as a full path, but it must
   still resolve to a member of the collection's current scope;
3. a single segment is a local name in the collection's current scope;
4. one match returns the loaded entry;
5. multiple matches fail with exact full-path candidates;
6. no match fails with bounded nearby candidates and concrete inspection calls,
   without selecting a semantic replacement.

`scope_ref` is therefore a hard visibility boundary, not only a local-name
disambiguation hint. A scoped collection must never return an out-of-scope entry
through a full path or ref. An exact same-kind object outside the scope fails
with the current scope and the global `catalog.require(ref)` alternative; it is
not silently widened.

`catalog.require(ref)` remains the strict cross-kind global membership
operation. Collection lookup and global require have different scopes and are
not aliases.

### Entry cards

Every `CatalogEntry.show()` card includes:

- exact kind and full path;
- business definition;
- `.ref` as an available typed identity;
- `.details()`, `.contract()`, `.show()`, and bounded navigation properties;
- current owner/parent identity where applicable.

`MetricEntry.show()` additionally includes bounded exact refs for:

- effective entities;
- candidate dimensions;
- candidate time dimensions;
- required relationships;
- component metrics and additivity/composition facts where applicable.

Counts without recoverable member identities are insufficient. When a bounded
card omits members, it states the omitted count and the concrete full read.

No card recommends an operator.

## Public Contract 4: Focused Help

### Focused-help sufficiency

Every focused page is reached through `marivo.help(...)`; "focused help" names
the rendered contract, not another public callable.

An invokable focused help target includes:

- canonical capability id;
- public entrypoint;
- live signature;
- accepted input families and semantic kinds;
- fixed output family;
- invocation-critical constraints;
- producer/consumer type edges;
- one concise runnable example;
- additional runnable examples only for a small number of supported forms whose
  argument structure is materially different;
- direct links to object-near contract and error recovery surfaces.

Optional details may remain linked, but no further help hop is required to run
the examples shown.

### Additional examples

The capability descriptor may carry a bounded tuple of labeled examples. This
is ordinary help content, not a new shape taxonomy or dispatch API.

The initial implementation needs extra examples only for general ambiguities
observed repeatedly in agent use:

| Capability | Additional examples |
| --- | --- |
| `observe` | segmented or time-series call in addition to the simplest scalar call |
| `correlate` | common-key cross-sectional call in addition to the time-series call |
| `MetricFrame.metric` | one full-metric-id projection call |

Examples must use current public catalog navigation and must not reconstruct
refs unnecessarily. All examples remain on the ordinary focused-help page and
inside the existing shared surface limit. If useful examples do not fit, the
design reduces prose or moves extended teaching to the maintained site docs; it
does not add `shape_id`, pagination, or `--shape` solely to accommodate help
volume.

### No planner

Root and focused help may expose:

- accepted families;
- semantic shape requirements;
- producer/consumer edges;
- synonyms used for lexical help suggestions;
- mechanical preconditions.

They must not expose:

- a ranked list of operators for a user question;
- a default investigation sequence;
- a stop recommendation;
- a generated analysis plan;
- business conclusions.

## Public Contract 5: Bounded Contract Results

### Shared contract-result protocol

Every public object returned by `.contract()` implements:

```python
repr(contract)  # bounded one line, no I/O
contract.render()  # bounded text, no I/O
contract.show()  # prints render(), returns None
```

Its typed fields remain directly accessible. Adding bounded rendering does not
replace the structured contract.

At minimum this applies across datasource, semantic, and analysis contract
results. Conformance is structural. Existing contract classes keep their current
Pydantic or dataclass ownership and may reuse the existing rendering helpers;
this design does not require a new cross-layer base class or mixin.

### Artifact contract

`ArtifactContract.show()` renders:

1. artifact kind, ref, semantic shape, and public columns;
2. current issues and blockers;
3. typed affordances;
4. preconditions and executable repairs;
5. terminal boundary ports.

Each `ArtifactAffordance` retains:

- `capability_id`;
- `public_entrypoint`;
- `help_target`;
- `input_requirements`;
- `preconditions`;
- `expected_output_family`.

A failed precondition remains visible only when it carries a non-empty
`AnalysisRepair`. A retry repair must contain a runnable `snippet`; otherwise
its repair kind is `inspect`, `semantic_authoring`, or another non-retry kind.

### Multi-metric projection repair

An arity-N `MetricFrame` remains ineligible for operators that require one
metric. The runtime does not guess which root the agent wants.

The contract provides one exact projection call for every carried metric
identity:

```text
precondition: single_metric
status: fail
received: arity=3
valid projections:
  frame.metric("sales.revenue")
  frame.metric("sales.order_count")
  frame.metric("sales.average_order_value")
```

Projection arguments come from `frame.metrics`/metric metadata, never from the
display column name or short name. The surrounding card identifies `frame` as
the current receiver. The subsequent affordance shows how a projected frame
binds to a selected capability without recommending which metric or capability
the agent should choose.

## Public Contract 6: Data-Bearing Result Basics

### Scope

This contract applies only to results whose public purpose is direct row/column
inspection:

- the `BaseFrame` artifact family, including Event/Lifecycle frame subclasses;
- `RawSqlResult`.

It does not apply merely because a bounded card happens to render a table.
Catalog collections, readiness reports, evidence summaries, metadata cards, and
paginated listings keep their existing typed result contracts.

The two data-bearing families expose:

```python
result.shape
result.row_count
result.columns
result.to_pandas()
result.render()
result.show()
```

`shape` is a two-integer tuple and `row_count == shape[0]`. `columns` preserves
the concrete family's existing public collection type; callers may rely on its
ordered string values, not on list-versus-tuple identity. `to_pandas()` returns
an isolated copy.

For `RawSqlResult`, `shape[0]` and `row_count` mean returned bounded rows, not the
unknown total cardinality of the underlying query. Its existing immutable
truncation facts remain adjacent:

```python
result.row_count  # returned bounded rows
result.is_truncated  # whether more rows existed at execution time
result.requested_limit  # requested result bound
```

`RawSqlResult.render()` and `.show()` display `row_count` together with
`is_truncated`; they must not render the returned count as a full-source count.
The current raw SQL execution reads `limit + 1`, so it continues to produce an
exact boolean truncation fact rather than adding a third `unknown` state.

The shared basics do not add pandas-style convenience methods such as
`.head()`, `.dtypes`, `.groupby()`, or `.plot()`.

### Typed artifacts

Typed analysis artifacts additionally expose:

```python
result.contract()
```

Their contracts may contain typed affordances and terminal boundary ports.

### Terminal results

`RawSqlResult` remains terminal and bounded. Its `render()`/`show()` state
includes:

```text
terminal_only: true
typed_reentry: false
row_count_semantics: returned_bounded_rows
returned_row_count: <exact returned count>
requested_limit: <execution limit>
is_truncated: <true|false>
preserves: bounded rows, declared columns/types, datasource, SQL reason
does_not_preserve: semantic identity, canonical lineage, typed affordances
```

Giving `RawSqlResult` `.shape` does not make it a `DataFrame` and does not
permit re-entry into analysis. It does not gain `.contract()` merely to mirror
typed artifacts; its bounded card already owns its terminal state and available
read/export actions.

## Public Contract 7: Temporal Suitability And Repair

### Metric object facts

Metric cards and details expose exact candidate time dimensions. An empty set is
rendered explicitly as `candidate_time_dimensions: none`.

### Pre-execution validation

When `time_scope`, `grain`, a time alignment policy, or a time-only operator
requires a time axis, validation occurs before backend execution.

The error distinguishes:

1. the metric has no candidate time dimension;
2. the supplied ref is an ordinary dimension rather than a time dimension;
3. the metric has several candidate time dimensions and requires an explicit
   choice;
4. the time dimension exists but its encoding/grain is incompatible.

Cases 1 and 2 produce a typed semantic-authoring repair when the requested
analysis cannot remain typed. The repair identifies:

- affected capability;
- metric and supplied axis;
- required semantic kind;
- current candidate time dimensions;
- current catalog/environment fingerprint;
- exact readiness/help target for an approved semantic change.

The runtime may include a mechanically legal non-temporal invocation as an
alternative when one exists. It must not label that invocation equivalent to
the requested temporal analysis or automatically reinterpret an ordinary field.

## Packaged Skill Contract

The packaged `marivo-analysis` skill remains a one-file boundary kernel.

### Entry rule

It requires one environment-bound verification before analysis begins:

```text
<analysis-python> -m marivo help
```

After the fingerprint is verified:

- use `marivo.help(...)` as the only public help entry;
- use the public object already in hand;
- inspect `.show()` for state;
- inspect `.contract()` before an unfamiliar composition;
- query focused help when the object contract is insufficient or before first
  use of an unfamiliar capability;
- follow structured error repair after a failure.

The skill must not require focused help for every API call.

### Prohibited skill content

The skill does not contain:

- signatures;
- parameter tables;
- operator inventories;
- shape matrices;
- runnable API examples;
- error catalogs;
- attachment paths required for ordinary API recovery.

Any conditional closeout rule currently stored only in an attachment must move
to the live owner, the single skill file, or be removed before attachment
deletion is claimed complete.

## Structured Repair Contract

All actionable datasource, semantic, and analysis errors render:

- `expected`;
- `received`;
- `location`;
- `repair.kind`;
- `repair.action`;
- canonical live `help_target`;
- bounded current candidates where relevant;
- `repair.snippet` when `kind="retry"`.

Error-instance help uses the existing repair help target when one is present.
This already carries the canonical surface and capability needed for recovery;
the redesign does not add a second `origin_capability_id` field to every error.
An error instance without a repair help target, and an error class, resolve to
the generic registered error contract and never guess which capability raised
it.

The structured `help_target` remains a surface-qualified internal identity.
Rendered repair guidance always expresses its public continuation as
`marivo.help("<surface>.<canonical-id>")`; it never points back to `md.help`,
`ms.help`, or `mv.help`.

### Executability

A retry snippet:

- uses only public modules and calls;
- uses canonical current names;
- includes values discovered from current state;
- does not contain placeholders such as `<metric>` when an exact candidate is
  already known;
- is paste-ready at the current call site using the receiver named by the
  surrounding error or contract (`session`, `catalog`, or `frame`);
- does not bypass semantic, ownership, evidence, or terminal boundaries.

Repository tests execute declared retry snippets with that receiver bound to a
fixture. The design does not require every repair to bootstrap a standalone
script or reconstruct the current session from scratch.

### Help fallback

The help target supplements a repair; it does not substitute for missing retry
arguments. If current state cannot produce a safe retry, the repair must say
which inspection, authoring, or user judgment is required.

## Capability Kernel Changes

The private capability descriptor keeps its existing identity, input/output,
constraint, and domain ownership. It gains only what the ordinary focused page
needs: a bounded tuple of labeled runnable examples where one example is
insufficient.

The neutral live resolver remains shared infrastructure. Datasource, semantic,
and analysis keep their native descriptor richness and private render
adapters. The top-level router owns global qualification, ambiguity handling,
composition topics, and object briefing composition only.

The domain registries no longer register `help` or `help_text` as ordinary
capabilities. A public help surface describing itself through three duplicated
domain descriptors is not a business capability.

Registry validation proves:

- every declared example executes;
- examples use the public input normalization contract;
- one callable still owns one capability id;
- artifact contracts and family gates use the same descriptor identity.

The example metadata remains private. This design does not add a shape matrix,
shape selector, `marivo.describe(...)`, or another registry projection.

### Help telemetry

Python help records one logical `help` capability with bounded routing
attributes:

- target kind: root, string, callable, type, ref, entry, result, or error;
- resolved owner: global, datasource, semantic, or analysis;
- resolved canonical id when one exists;
- outcome: success, unknown, or ambiguous.

The bootstrap command records a separate `help_bootstrap` CLI operation. It is
not recorded as focused help. Telemetry never records full business
definitions, guardrails, object details, or rendered error bodies.

## Persistence And Recovery

Accepting `CatalogEntry` is a public-boundary convenience only.

An entry's compiled-catalog identity is not persisted. Recompilation invalidates
held entries and requires explicit reacquisition from the current catalog as
defined above; refs recovered from persisted state are independently revalidated
against the current compilation.

Persisted state remains unchanged in principle:

- semantic refs use the existing typed ref envelope;
- job parameters store canonical refs or existing runtime metric payloads;
- artifact metadata stores canonical semantic ids/refs;
- evidence subjects and semantic anchors contain no catalog entry object;
- replay reconstructs refs and revalidates current catalog membership;
- cross-session recovery does not retain a pointer to a previous catalog
  instance.

Tests must prove that entry and ref inputs produce equivalent:

- normalized job parameters;
- artifact identity inputs;
- lineage;
- evidence anchors;
- replay behavior;
- recovered frame metadata.

## Implementation Plan

Phases 0-4 below describe the implemented execution-continuity cutover. Phase 5
is a separate breaking public amendment that narrows help ownership after the
Phase 4 transcript review. Phase 5 may be developed incrementally, but its
Python API, CLI, errors, docs, skills, examples, and tests cut over atomically.
There is no supported release with both the old domain help paths and the new
canonical path.

### Phase 0: Contract freeze

- approve this design after the active Event/Lifecycle implementation reaches a
  complete public vertical phase boundary or is explicitly parked;
- freeze one exact candidate revision and record which Event/Lifecycle and
  optional ontology bridge surfaces are present;
- inventory every catalog-bound runtime semantic input in semantic and analysis,
  including every qualifying Event/Lifecycle consumer present at that revision;
- record the explicit exclusions: semantic authoring, datasource APIs, nested
  typed handles, and runtime metric expressions;
- inventory every `.contract()` result type plus the `BaseFrame` and
  `RawSqlResult` data-bearing families;
- record current public exports, help targets, skill shape, and DAComp
  efficiency baseline;
- identify any current skill attachment references and assign each fact to its
  live owner.

No runtime behavior changes in this phase.

### Phase 1: Entry and help continuity (implemented; help entry superseded by Phase 5)

Owning areas:

- `marivo/__init__.py`;
- `marivo/cli.py`;
- analysis/semantic/datasource help resolvers;
- capability registry/rendering;
- packaged `marivo-analysis` skill;
- latest English and Chinese site documentation.

Historical Phase 1 delivery:

- useful top-level module help without new exports;
- canonical CLI repair;
- canonical target-kind resolution across strings, public paths, callables,
  bound methods, objects, entries/refs, and errors;
- representative additional `observe`, `correlate`, and projection examples
  where their calling forms differ;
- demand-driven skill wording.

Phase 1 examples may use ref inputs while developed in isolation. The canonical
entry-based examples become externally visible only at the coordinated Phase 4
cutover after Phase 2 validation passes.

### Phase 2: Catalog and semantic input boundary

Owning areas:

- `marivo/semantic/catalog.py`;
- shared semantic input normalization;
- all public analysis signatures consuming semantic identity;
- catalog readiness/verify/preview boundaries;
- capability accepted-input metadata;
- persistence and replay equivalence tests.

Deliver:

- collection local-name/full-path/ref resolution;
- `CatalogEntry | Ref` public acceptance;
- immediate ref normalization;
- closed polymorphic entry-class validation;
- cross-catalog, stale-entry, and wrong-kind rejection;
- exact executable repairs only for mechanically unique corrections.

This phase must migrate the frozen catalog-bound runtime consumer inventory
atomically. A partial state where `observe` accepts entries but another frozen
analysis consumer does not is unsupported. Semantic authoring and datasource
APIs are outside that inventory. The same rule includes every qualifying
Event/Lifecycle consumer frozen in Phase 0 and the ontology bridge when present.
Event/Lifecycle phases released after this cutover use the final boundary from
their first public release.

### Phase 3: Contract, result, and repair closure

Owning areas:

- shared render/result protocol;
- datasource and semantic contracts;
- `ArtifactContract` and affordance construction;
- `MetricFrame` multi-root projection;
- `RawSqlResult`;
- affected datasource/semantic/analysis error rendering.

Deliver:

- bounded contract result protocol;
- `BaseFrame.row_count` plus `RawSqlResult.shape`/`row_count`;
- explicit RawSqlResult returned-row/truncation semantics;
- executable multi-metric projection repairs;
- explicit terminal-only rendering without typed re-entry;
- temporal suitability repair before backend execution.

### Phase 4: External evaluation and cutover

- build a candidate package from a clean revision;
- run deterministic repository, docs, package, and site gates;
- optionally use a small targeted smoke subset while iterating;
- run the complete frozen ten-task DAComp validation set after deterministic
  gates pass, with three isolated trials per task for the candidate Marivo
  revision and the frozen Marivo baseline revision;
- include raw SQLite only for periodic absolute-friction calibration, not as a
  required arm of every Marivo version comparison;
- inspect transcripts, not only aggregate scores;
- cut over docs, skill, CLI, help, examples, and runtime atomically.

Tasks that require typed regression remain terminal custom-analysis cases in
this design. Their interface acceptance is limited to truthful boundary
discovery, correct terminal result behavior, and no false claim that a typed
regression capability exists. Completion parity for an unsupported statistical
method is not an interface-only release gate.

### Phase 5: Unified help ownership (implemented)

Owning areas:

- `marivo/__init__.py`;
- a new private `marivo/_help/` coordinator;
- `marivo/cli.py`;
- datasource, semantic, and analysis live surfaces and renderers;
- public exports, structured errors, contracts, and affordance hints;
- packaged skills and latest English and Chinese documentation.

Deliver:

- one public `marivo.help(...)` callable returning `None`;
- no public `help_text`;
- no public `md.help`, `ms.help`, or `mv.help`, including hidden module
  attributes outside `__all__`;
- lazy adaptation to the three private native live surfaces;
- qualified and unique-owner string routing with deterministic global
  ambiguity errors;
- bounded global `authoring` and `load` composition topics;
- one no-I/O `Ref`/`CatalogEntry` briefing;
- bootstrap-only `python -m marivo help`;
- one logical help telemetry capability plus a separate CLI bootstrap event;
- removal of old help invocations from active runtime guidance, errors,
  contracts, skills, examples, and latest docs.

Implementation evidence:

- `marivo.help(...)` is the sole public Python help entry; the three domain
  modules expose neither `help` nor `help_text`, and their former public help
  modules are removed;
- the private `marivo/_help/` coordinator performs deterministic qualified,
  unique-owner, ambiguity, global-topic, callable, result, error, `Ref`, and
  `CatalogEntry` routing while leaving descriptor semantics with the native
  private capability surfaces;
- object briefings for refs and entries are no-I/O, and the CLI help command is
  bootstrap-only with a verified Python handoff;
- active runtime hints, API documentation, packaged skills, README files, and
  latest English and Chinese site content use the canonical entry;
- deterministic tests cover the full routing inventory, output bounds,
  telemetry, bootstrap behavior, old-path absence, no-I/O object handling, and
  real execution of all 77 datasource and semantic focused-help examples;
- `make check`, `make docs-api`, package build/check, isolated installed-wheel
  smoke, site content verification/build, and `git diff --check` pass for the
  cutover.

Suggested private ownership:

```text
marivo/_help/model.py
  global target and error models

marivo/_help/route.py
  qualification, owner resolution, and ambiguity

marivo/_help/topics.py
  root, authoring, and load composition topics

marivo/_help/object_briefing.py
  Ref, CatalogEntry, result, and error composition

marivo/_help/render.py
  bounded rendering and the public print boundary

marivo/_help/bootstrap.py
  CLI environment fingerprint and Python handoff
```

Exact file splitting may be reduced when implementation shows a smaller private
shape is sufficient. The required boundary is one coordinator over private
domain adapters, not this directory layout itself.

## Verification Strategy

### Deterministic surface tests

- `marivo.help` is public, prints bounded help, and returns `None`;
- `md`, `ms`, and `mv` expose neither `help` nor `help_text`, and
  `marivo.help_text` does not exist;
- `python -m marivo help` and `marivo help` print the environment fingerprint
  and canonical Python handoff;
- CLI help rejects every track or target argument as bootstrap-only;
- every qualified registry target resolves through its native descriptor;
- every unique unqualified target routes to its one owner;
- every unregistered multi-owner target raises a bounded global ambiguity
  error instead of selecting by surface order;
- `authoring` and `load` render their explicit global composition topics;
- equivalent callable target forms resolve to one descriptor, while result,
  reference, and error targets resolve to their canonical target kind;
- bare `Ref` help performs no project load or datasource call and makes no
  readiness claim;
- `CatalogEntry` help composes loaded details and current contract without a
  datasource call;
- conditional analysis consumers are kind-specific and do not claim legality
  before readiness;
- an error instance with a repair resolves through its existing repair help
  target, while an instance without one stays on the generic error contract;
- every declared focused-help example executes against a fixture;
- root/focused help remains inside shared surface limits;
- no current help or error points to a skill attachment or an old domain help
  path.

### Semantic input tests

For every parameter in the frozen catalog-bound runtime inventory:

- exact entry succeeds;
- exact ref succeeds;
- entry and ref results are equivalent;
- every registered concrete entry subclass succeeds for its exact kind;
- unregistered subclasses and duck-typed `.ref` objects fail;
- wrong-kind entry/ref fails before backend work;
- cross-catalog entry fails;
- stale same-project entry fails with an executable current-catalog reacquisition
  repair when the exact path still exists;
- bare string fails;
- wrong-kind, unknown, and ambiguous inputs suggest current same-kind candidates
  without choosing one;
- every repair labeled `retry` executes with the documented receiver binding.

### Catalog tests

- scoped local-name lookup;
- global collection local-name lookup when unique;
- ambiguous local-name lookup with exact full-path candidates;
- full-path lookup;
- same-kind ref lookup;
- Event collection lookup follows the same overload, kind, and scope contract
  when Event is present in the frozen inventory;
- scoped full-path and ref lookup cannot return an out-of-scope object;
- wrong-kind ref rejection;
- bounded entry cards expose `.ref` and exact available axes;
- omitted members include a concrete full-read action.

### Contract and result tests

- every public contract type conforms to bounded `repr`/`render`/`show`;
- every `BaseFrame` exposes `row_count == shape[0]`;
- `RawSqlResult` exposes shape, returned row count, existing ordered columns,
  and isolated pandas export;
- `RawSqlResult` keeps returned row count, requested limit, and exact truncation
  state adjacent in `render()` and `show()`;
- typed artifacts expose typed affordances and terminal ports;
- `RawSqlResult` exposes no `.contract()`, typed affordance, or re-entry;
- arity-N MetricFrame contracts expose one executable projection per full metric
  identity;
- every rendered `available:` member exists.

### Temporal repair tests

- no candidate time dimension;
- ordinary dimension supplied as time dimension;
- ambiguous candidate time dimensions;
- incompatible encoding/grain;
- validation occurs before backend work;
- semantic-authoring repair carries current context and no inferred semantic
  mutation;
- a non-temporal alternative, when rendered, is mechanically legal and is not
  presented as equivalent.

### Repository gates

Run the narrowest affected tests first, then:

```text
make check
make docs-api
make pypi-build
make pypi-check
git diff --check
```

Run the current site content verification and build commands for the latest
English and Chinese documentation. If the repository does not provide one of
the named Make targets at implementation time, record the actual supported
replacement rather than adding a compatibility target solely for this plan.

Build and install the wheel into an isolated environment, then smoke:

```text
python -m marivo help
import marivo; marivo.help()
import marivo; marivo.help("analysis.observe")
```

## External Agent Evaluation

The external evaluator remains in `marivo-agent-evals`. That repository owns the
frozen task manifest, task-specific assertions, transcript classification,
metric formulas, and judge adapter. This interface design does not duplicate
those details or add product APIs to make them easier to score.

### Role

The evaluation is supporting version-over-version evidence, not the source of
the public contract. Deterministic runtime, help, error, persistence, and
protocol tests remain the primary release gates.

The frozen Marivo baseline is the primary comparison for an interface revision.
The raw SQLite arm remains useful periodic context for total interaction cost,
but it is not expected to share Marivo's semantic and evidence obligations and
does not need to be rerun for every candidate. A small task subset may be used as
a smoke run while iterating; it is not the release sample.

### Run control

The release evaluation runs the complete frozen ten-task validation set with
three fresh isolated trials per task for:

- the candidate Marivo package;
- the frozen Marivo baseline revision.

Generation freezes the task prompt, data, turn limit, agent configuration, and
resolved model identity across the compared revisions. Both revisions use
byte-identical semantic assets; if a package format change makes that
impossible, the run is not treated as a clean interface A/B until equivalent
compiled semantic fingerprints are demonstrated. A periodic raw calibration,
when run, uses the same generation configuration and underlying data.

Scoring separately records and freezes the judge model identity, judge turn
limit, DAComp scoring revision, prompt/rubric revision, and enabled channels
including whether visual scoring is disabled. Every compared output is judged
with the same configuration. Revision order is counterbalanced. Cold-start and
warm-session results are separate and are never pooled.

### Interpretation

Report every trial plus per-task medians and dispersion for:

- completion and rubric quality;
- total turns and tool calls;
- help, failed, and recovery calls;
- terminal escapes and typed-operator use;
- cost and wall time as diagnostics.

The interface design does not embed hard targets such as `1.3x` turns or a
fixed help-call percentage. Those values are sensitive to task mix, model, and
harness classification and belong in the eval suite's versioned run policy.

Any protocol violation, runtime-integrity failure, contaminated trial,
model/comparison mismatch, false claim of a typed capability, or terminal
re-entry invalidates the affected trial for every task, including tasks whose
requested statistical capability is intentionally deferred. For tasks supported
by typed Marivo, completion, quality, turns, and recovery friction are compared
with the frozen Marivo baseline. A release concern requires transcript-backed,
repeatable degradation rather than one noisy aggregate threshold.

A task requiring typed regression remains a truthful terminal custom-analysis
case until that capability has its own design. Its missing typed completion is
reported but does not by itself fail this interface cutover. Warm reuse may show
steady-state efficiency but cannot replace cold-entry evidence.

## Migration And Cutover

This is a coordinated public behavior change. Implementation must choose one
target contract and update runtime, annotations, help, errors, docs, skill,
examples, and tests together.

The implementation phases above are mergeable development slices only when
their incomplete public behavior remains unreleased. There is one public
interface cutover. A candidate must not publish entry-based examples from Phase
1 while Phase 2 still rejects those entries, or advertise `RawSqlResult.shape`
before its returned-row and truncation semantics are truthful.

No persisted-state migration is required because persisted identity remains
ref-only. Phase 5 is nevertheless an intentional source-level breaking change:
the six public `md`/`ms`/`mv` `help` and `help_text` symbols are removed and one
top-level `marivo.help` symbol is added.

There are no deprecated aliases. Removing old names only from `__all__` is not
a completed migration. Active runtime hints, structured repairs, contracts,
skills, examples, API docs, and latest English and Chinese site content must
move to `marivo.help(...)` in the same cutover.

Historical versioned documentation and published release notes retain their
historical API text. Superseded design records are not mechanically rewritten;
this document is the normative Phase 5 contract.

The public input widening is additive in accepted values, but help, error, and
contract rendering changes are atomic surface changes. If implementation also
removes or renames a public field, method, or help target beyond this design,
that break requires a separate explicit decision; it must not be hidden inside
the input-normalization work.

The implementation must not add:

- callable-ref compatibility;
- raw-string operator inputs;
- implicit `CatalogEntry.__getattr__` forwarding to `Ref`;
- duplicate old/new help paths;
- a public `help_text` retained for tests or CLI implementation convenience;
- focused target or track arguments on the bootstrap-only CLI help command;
- implicit project loading or datasource access from semantic-object help;
- terminal re-entry shims;
- an undocumented structured help API.

## Acceptance Criteria

The design is implemented when:

- a cold agent verifies the selected environment with
  `<selected-python> -m marivo help` and is handed directly to
  `import marivo; marivo.help(...)`;
- `marivo.help(...)` is the only public Python help entry, and no public
  `help_text`, `md.help`, `ms.help`, or `mv.help` remains;
- CLI help is bootstrap-only and cannot become a competing focused-help path;
- every native capability remains reachable through a qualified global target,
  and unique unqualified targets route without a retry;
- `authoring` and `load` resolve through explicit global composition topics
  without merging domain state machines or typed load operations;
- one `Ref` or `CatalogEntry` produces one canonical object briefing regardless
  of datasource, semantic, or analysis context;
- semantic-object help performs no implicit project load or datasource query
  and does not overstate readiness;
- equivalent callable help forms resolve through one capability identity, while
  objects, entries/refs, and errors resolve through their canonical target kind;
- every catalog-bound runtime consumer in the frozen inventory accepts exact
  current-catalog entries and refs with identical persisted behavior;
- semantic authoring and datasource APIs remain outside that input widening;
- typed collection lookup accepts local names, full paths, and same-kind refs
  with deterministic ambiguity handling;
- metric cards expose exact available axes rather than counts alone;
- focused help includes runnable additional examples for the few supported
  calling forms that differ materially;
- every contract object is bounded and inspectable;
- every retry repair is executable from current state;
- multi-metric gating teaches exact projection calls;
- `BaseFrame` and `RawSqlResult` expose the scoped data-bearing result basics
  while `RawSqlResult` remains terminal;
- missing temporal semantics fail before backend work with a truthful typed
  repair;
- the packaged skill verifies the environment once and then defers to
  object-near contracts and errors instead of requiring per-call help;
- deterministic repository gates pass and the scoped external evaluation is
  completed and reviewed;
- the active Event/Lifecycle work was frozen at a complete vertical public phase
  boundary, and every qualifying catalog-bound runtime consumer in that revision
  follows this contract;
- typed regression and other deferred statistical capabilities remain absent
  and are not implied by help, contracts, or acceptance claims.

## Success Test

The redesign succeeds when a capable coding agent can take the current public
object—catalog collection, catalog entry, session, artifact, contract, terminal
result, or structured error—and reach one mechanically correct next typed call
or terminal read without reconstructing identity, consulting private reflection,
parsing an undocumented registry, or relying on a cached API manual.

At cold start, the agent makes one environment-bound CLI call. After import,
the agent never decides among datasource, semantic, analysis, CLI, or
string-returning help APIs; every focused, object, and error lookup begins at
`marivo.help(...)`.

For a terminal result, success means discovering the bounded read/export and
closeout actions plus the explicit absence of typed continuation. It does not
mean fabricating a next analysis call.

Marivo still does not choose the investigation for the agent. It makes every
legal choice executable and every illegal choice recoverable.
