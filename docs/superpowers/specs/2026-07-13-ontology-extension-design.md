# Marivo Ontology Extension Design

Status: implemented

Date: 2026-07-13

Last revised: 2026-08-04

> This document is one of two specifications split from the original
> `Marivo Executable Semantic and Ontology Extension Design`. It owns the
> optional `marivo.ontology` knowledge extension and its narrow bridge into
> typed analysis. It depends on the executable semantic objects and analysis
> defined in the companion
> [`event semantic and analysis design`](2026-07-13-event-semantic-and-analysis-design.md)
> and on the existing Metric semantics.

## Summary

`marivo.ontology` is an optional knowledge extension layered on top of the
stable executable semantic model. It references typed semantic identities, adds
reviewable contextual relationships, and consumes read-only indexes over
dependencies already declared by the semantic catalog. It cannot define
executable identity, joins, filters, populations, readiness, or SQL. Its only
v1 bridge into typed analysis is `discover.semantic_hypotheses(source)`, which
returns unscored candidates that an agent must explicitly inspect and observe.

The executable semantic core (`Entity`, `Dimension`, `TimeDimension`,
`Measure`, `Metric`, `Relationship`, `Event`, `StateModel`, and
`StateProjection`) and the typed Event/Lifecycle analysis it feeds are
specified in the companion design and remain the sole authority for executable
business meaning. Ontology never alters their contracts.

`Rule`, `Policy`, and `Action` belong to a future decision extension that may
consume ontology context and assessed evidence. They are not semantic objects,
ontology edges, evidence facts, or analysis operators in this design.

## Architectural Decision

The optional ontology extension consumes the semantic and analysis cores from
above and feeds one narrow candidate bridge back into typed analysis:

```text
Executable Semantic Core (`marivo.semantic`)     [companion design]
    Entity / Dimension / TimeDimension / Measure / Metric / Relationship
    + Event / StateModel / StateProjection
        |
        | ready typed semantic handles
        v
Typed Analysis Core (`marivo.analysis`)          [companion design]
    ^
    | explicit candidate selection and observe
    |
Optional Ontology Extension (`marivo.ontology`)
    typed semantic refs + explicit SemanticEdge values
    -> CandidateSet[semantic_hypothesis]

Future Decision Extension
    Rule / Policy / Action
    consumes ontology context + assessed evidence
```

The arrows express allowed consumption, not shared ownership:

- semantic definitions never depend on ontology;
- Metric, Event, StateModel, and StateProjection execution never requires
  ontology;
- ontology references semantic identities but cannot alter their contracts;
- evidence records observations and assessments but does not execute ontology;
- the future decision layer may read both ontology and evidence, but neither
  lower layer contains decision logic.

## Scope

This design adds:

- an optional `marivo.ontology as mo` authoring and browsing surface;
- bounded ontology-guided semantic-hypothesis discovery;
- candidate non-seeding behavior in the existing Evidence Engine;
- lineage from ontology candidate discovery into later statistical analysis.

This design does not add:

- a second executable semantic model;
- a generic knowledge-graph node or runtime instance store;
- a graph query language or arbitrary traversal API;
- automatic candidate execution, ranking, or causal inference;
- automatic inheritance from taxonomy;
- any executable semantic authority (identity, joins, filters, populations,
  readiness, or SQL);
- Rule, Policy, Action, permission, obligation, or write-back behavior;
- migration adapters or aliases for an alternative semantic object system.

Ontology cannot be used to simulate a future Semantic Core v2 (portable
business identity or multiple executable representations).

## Optional Ontology Extension

### Module ownership

Ontology is authored and loaded through a separate public module:

```python
import marivo
import marivo.analysis as mv
import marivo.ontology as mo
import marivo.semantic as ms

session = mv.session.get_or_create(name="order-health")
marivo.help("ontology.authoring")
ontology = mo.load(semantic=session.catalog)
```

`marivo.help(...)` is the sole public Python help entry. Ontology targets live
under `ontology.*`; the analysis bridge lives under
`analysis.discover.semantic_hypotheses`. The ontology module owns its native
descriptors and loading behavior, but exposes no second `help` entrypoint.

This delivery extends the global coordinator's closed native-surface set from
`datasource | semantic | analysis` to
`datasource | semantic | analysis | ontology`. It adds an ontology live surface,
bootstrap import guidance for `import marivo.ontology as mo`, and
an ontology-native
`OntologyHelpTargetError(kind="ontology_help_target_not_found")`. As with the
other native surfaces, the sole public `marivo.help(...)` coordinator adapts an
unknown native target into the existing bounded `MarivoHelpTargetError` with
qualified suggestions. `ontology.authoring` is a qualified ontology target
alongside, not an alias of or collision with, the existing global `authoring`
topic.

`mo.load(...)` validates all endpoints against the supplied semantic catalog
and returns an immutable `OntologyCatalog`. The authored ontology root is
optional. The returned catalog records `configured=False` when no ontology root
is present; ordinary semantic and analysis loading still succeeds.

`OntologyCatalog` is a state-bearing public result, not an internal IR escape
hatch. It exposes bounded deterministic `repr()`, `render()`, `show()`, and an
existing `AuthoringContract` through `contract()`. `show()` renders configuration
state, definition fingerprint, edge count, and role-aware summaries of a
bounded, name-ordered edge prefix, with the exact omitted count and, when a root
exists, its inspection location. `contract()` exposes only ontology
authoring/audit help and that source inspection action; analysis still starts
from a typed artifact through `discover.semantic_hypotheses`. Neither surface
exposes raw `SemanticEdgeIR`, arbitrary traversal, or an executable continuation
from an edge.

Ontology code ships with Marivo; optionality concerns authored ontology
content, not a plugin or alternate package installation.

### Session binding and recovery

The project has at most one authored ontology root. When a Session is created
or recovered, it attempts to load that root through the same
`mo.load(semantic=session.catalog)` validation path and records one private
closed binding state:

```text
absent                         no authored root
ready(OntologyCatalog)         root validated against this Session catalog
unavailable(validation_issues) authored root exists but did not validate
```

Ontology binding is never a Session-creation or recovery prerequisite. An
invalid or stale root produces `unavailable` with the exact endpoint/edge
validation issues; Metric, Event, and Lifecycle analysis remain usable.
Discovery is enabled only for `ready`.

`mo.load(...)` is also the read-only author/auditor route for that same
project-root source and, when called directly, still raises the typed validation
error for an invalid root. Session binding catches that error only to preserve
ontology optionality; it does not silently drop definitions or construct a
partial catalog. No compiled OntologyCatalog is cached as a second project
authority, so there is no separate stale-catalog fingerprint state or
`ontology_catalog_mismatch` error. Thus
`session.discover.semantic_hypotheses(...)` has exactly one ontology authority,
including after cold recovery. Recovered candidate and observation artifacts
retain their historical ontology fingerprint for lineage, but recovery does not
pretend that historical content authorizes a new discovery call.

### No second executable identity

Ontology v1 contains no standalone term, generic node, runtime instance, or
independent business-object identity. Across all relation constructors, every
endpoint is one of this closed typed-ref union:

```text
EntityRef
MeasureRef
MetricRef
```

Bare semantic-id strings are not accepted by the authoring API. Physical
datasource refs and analysis artifact refs are not ontology endpoints. This
module-wide union does not make its members interchangeable: each public
relation constructor admits a narrower role-specific subset defined below.

Ontology constructors are semantic-ref-only authoring calls. They do not accept
`EntityEntry`, `MeasureEntry`, or `MetricEntry`, because authoring has no
authoritative compiled-catalog lifecycle. An agent starting from a catalog entry
passes its explicit `.ref`:

```python
refund_rate_entry = session.catalog.metrics.get("commerce.refund_rate")
refund_pressure = mo.influences(
    name="refund_pressure_influences_order_health",
    driver=refund_rate_entry.ref,
    outcome=healthy_order_rate_ref,
    ai_context=ms.ai_context(
        business_definition="Refund pressure may degrade order health.",
    ),
)
```

Passing an entry raises `invalid_ontology_ref` with the `.ref` conversion as its
repair. The runtime bridge contributes no `SemanticInput[K]` parameters to the
vNext Phase 0 inventory: discovery consumes an analysis artifact, and
`session.observe(candidate)` consumes the exact `OntologyMetricCandidate` type.

### Read-only semantic index

The semantic catalog owns and this delivery adds or standardizes deterministic
immutable reverse indexes over its authoritative declarations:

- complete explicit Metric dependency and Measure lineage, including composed
  Metric components, plus root Entity;
- Entity-to-Metric root resolution;
- Measure-to-Metric observation relationships (`measures` resolution).

`OntologyCatalog` only delegates read-only views of those semantic-owned
indexes; discovery consults the semantic catalog implementation behind those
views. The reverse directions are part of this delivery rather than assumed to
exist merely because forward dependencies or detail fields exist today. Their
entries are derived indexes, not authored `SemanticEdge` objects. They have no
independent edge identity and cannot be overridden. Ontology may use them for
the final mechanical resolution step of the bounded candidate bridge, but never
as a second semantic authority or new executable facts.

### Explicit SemanticEdge

The only authored ontology object in v1 is `SemanticEdge`. Authors create it
through a relation-specific constructor:

```python
refund_pressure = mo.influences(
    name="refund_pressure_influences_order_health",
    driver=refund_rate,
    outcome=healthy_order_rate,
    ai_context=ms.ai_context(
        business_definition=("Refund pressure may contribute to deterioration in order health."),
    ),
)
```

V1 exposes exactly two constructors. Their signatures, rather than a caller
supplied relation string, define the relation, endpoint roles, and allowed
endpoint kinds:

| Public constructor | Endpoint contract | Directionality | Discovery meaning | Forbidden inference |
| --- | --- | --- | --- | --- |
| `mo.influences(driver, outcome)` | driver: `EntityRef`, `MeasureRef`, or `MetricRef`; outcome: `EntityRef` or `MetricRef` | directed | proposes the driver when an anchor matches the outcome | Causal fact, effect, or confidence |
| `mo.related_to(left, right)` | each side: `EntityRef`, `MeasureRef`, or `MetricRef` | symmetric | proposes the endpoint opposite a matching anchor | Joinability or statistical association |

Every constructor is keyword-only, requires `name`, its two role-specific
endpoints, and `ai_context`, and returns `SemanticEdgeRef`.
`ai_context` is the existing `AiContextValue` built only through
`ms.ai_context(...)`; ontology adds no alias or second context type. Its
`business_definition` must be non-empty and explains the relation's business
meaning. Optional `guardrails` contain only edge-specific cautions; fixed
relation constraints remain constructor/help-owned and are not repeated by
every author.
`mo.related_to(...)` canonicalizes its endpoint identities, so swapping `left`
and `right` cannot create a distinct edge or bypass duplicate detection.

Both constructors lower into one internal `SemanticEdgeIR` containing a
closed relation enum plus normalized `source` and `target` fields. A private
`_edge(...)` factory may centralize that lowering and shared validation, but
neither `_edge(...)` nor a generic `mo.edge(...)` is exported, indexed by
`marivo.help(...)`, or accepted as an authoring path. The author never supplies
relation, predicate, directionality, or internal source/target roles; the
chosen constructor derives them mechanically. V1 has no separate edge-family
taxonomy because it would only duplicate the two public relations.

### SemanticEdgeRef identity

`SemanticEdgeRef` is an ontology-owned reference to one authored assertion. It
is not a semantic business-object identity and is deliberately outside
`SemanticKind`, `Ref[SemanticKindTag]`, the semantic factory registry,
`marivo.semantic_ref/v1`, and `session.catalog.require(...)`.

An edge's required author-supplied `name` is unique within the one project
ontology root, must match
`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`, and is never case-folded or otherwise
normalized. The constructor returns an immutable exact `SemanticEdgeRef` whose
closed fields are `kind="semantic_edge"` and `path=<name>`. Its canonical runtime
key is `semantic_edge:<name>`, and its only public serialized form is:

```json
{
  "schema": "marivo.ontology_ref/v1",
  "kind": "semantic_edge",
  "path": "refund_pressure_influences_order_health"
}
```

Unknown fields are rejected. The persistence layer may reconstruct this value
through a private recovery adapter; there is no public ref constructor. A
`SemanticEdgeRef` identifies the assertion and its historical lineage, not a
second executable object, and is never a legal edge endpoint. Duplicate names
are rejected before endpoint or context comparison. For `related_to`, swapped
endpoints also normalize to the same assertion definition, so a second name
cannot be used to author a duplicate canonical pair.

The omitted relations each fail the v1 public-contract economy test:

- `part_of` would duplicate Entity/field ownership, Metric dependencies, Event
  participation, StateModel membership, or executable composition without a
  non-overlapping ontology-only domain/range;
- `precedes` would duplicate normative StateModel transitions or explicit
  EventPattern ordering while neither executing nor supplying either contract;
- `specializes` has no v1 consumer beyond browsing and risks suggesting
  property, identity, or readiness inheritance that ontology cannot perform;
- `contrasts_with` would produce the same symmetric association candidate as
  `related_to`; an author's intended comparison belongs in
  `ai_context.business_definition` until a distinct typed comparison consumer
  exists;
- `causes` would overstate a hypothesis input as causal proof.

None is exported or retained as a compatibility alias. They may be designed in
a future version only with a non-overlapping business meaning and a distinct
typed consumer. Generic `mo.edge(...)` is likewise absent.

Ontology defines no public `basis` or relation-specific `provenance` value. The
edge's source location, stable identity, definition fingerprint, and containing
catalog fingerprint provide construction and recovery lineage, but do not
claim business authority or turn the edge into evidence.

The internal direction derived from constructor roles has the following closed
discovery meaning:

- discovery first derives a closed anchor set containing the source Metric,
  its root Entity, and the MeasureRefs it explicitly depends on; it does not
  add Dimensions, Relationships, inferred neighbors, or recursively reached
  refs;
- an `influences` edge is considered only when an anchor matches the edge's
  `outcome`; its `driver` is the proposed candidate;
- `mo.related_to(...)` rejects identical endpoint identities at authoring time;
- a `related_to` edge proposes the other endpoint only when exactly one of
  `left` or `right` belongs to the discovery anchor set. If both endpoints are
  anchors, the edge is excluded once as `anchor_overlap`; it never emits two
  proposals or an arbitrary opposite endpoint.

After endpoint-to-Metric resolution, any MetricRef equal to the source
MetricRef is excluded as `source_metric_self_resolution`, regardless of
relation. Candidate tuples produced through repeated anchor/index paths are
de-duplicated before ordering. These rules prevent a symmetric edge or a
non-Metric endpoint's reverse index from manufacturing a self-candidate.

Direction remains an internal admission rule, not a Candidate row field. The
public `edge_relation` plus constructor roles determine how to interpret the
proposed `candidate_semantic_ref`: `influences` proposes only the edge's driver
when an anchor matches its outcome, while symmetric `related_to` proposes the
other endpoint. Storage orientation therefore never becomes a second public
business meaning.

SemanticEdge carries no identity mapping, row predicate, join key, expression,
Metric definition, readiness result, artifact, or runtime truth value. It
cannot make an invalid semantic plan valid.

### Loading states

Ontology state is closed and observable:

- not configured: the semantic and analysis cores remain fully usable;
- configured and valid: ontology browsing and candidate discovery are enabled;
- configured but empty for a source: discovery returns an empty CandidateSet;
- configured but invalid: `mo.load(...)` fails with typed endpoint or edge
  validation evidence rather than silently dropping definitions, while a
  Session records `unavailable` and preserves ordinary analysis.

Calling ontology-guided discovery when the session has no configured ontology
raises `OntologyNotConfiguredError` with
`kind="ontology_not_configured"`. This is distinct from a successful empty
candidate result. Calling it when the Session binding is `unavailable` raises
`OntologyUnavailableError(kind="ontology_unavailable")` with the stored
validation issues and an exact `mo.load(semantic=session.catalog)` inspection
repair. It never collapses an invalid root into the absent state.

## Typed Ontology-to-Analysis Bridge

### Public operator

`session.discover.semantic_hypotheses(source, *, limit=50)` is the sole v1
execution bridge from ontology context into typed analysis. `limit` is a closed
integer range `1..200`; booleans, zero, negative values, `None`, and values over
200 are rejected. It caps persisted rows after deterministic ordering and is a
resource bound, not a score, rank, or recommendation signal.

`source` must be a persisted `MetricFrame` or a Metric-derived
`DeltaFrame[scalar_delta | time_series_delta | segmented_delta | panel_delta]`
with exactly one recoverable source Metric lineage. A Delta comparing two
observations of the same Metric is legal. `DeltaFrame[funnel]` and any later
non-Metric Delta shape are outside this capability rather than admitted with a
doomed lineage precondition. Missing or multiple Metric identities on an
otherwise admitted source raise `missing_metric_lineage` or
`ambiguous_metric_lineage`; the operator never selects one.

The result is the existing `CandidateSet` family with closed shape
`semantic_hypothesis`.

The agent-facing loop is deliberately the ordinary analysis loop:

```python
candidates = session.discover.semantic_hypotheses(source, limit=50)
candidates.show()
candidate = candidates.select(item_id="<copy one item_id from show()>")
observed = session.observe(candidate)
```

`show()` renders a copyable `select(item_id="...")` call for every displayed
candidate, so the agent does not reconstruct an opaque id or read a private
metadata object.

### Resolution algorithm

For the source Metric, discovery performs exactly these steps:

1. Derive the closed anchor set of the source MetricRef, its root EntityRef,
   and its complete explicit MeasureRef lineage through any composed Metric
   components, using the semantic catalog's authoritative dependency index.
2. Read only explicit `influences` and `related_to` SemanticEdges admitted by
   the constructor-role rules above. Their relation and endpoint interpretation
   are derived from the constructor, not caller input.
3. Traverse at most one such explicit edge.
4. If the proposed endpoint is a MetricRef, use that Metric directly.
5. Otherwise consult the semantic catalog's read-only dependency and
   `measures` indexes to resolve every Metric that explicitly observes or
   depends on the proposed endpoint.
6. Normalize the source observation into an immutable
   `InheritedObservationScope`: exact time range, grain, time-dimension ref,
   axis refs, slice predicates, cohort binding, and source catalog fingerprint.
   No ref is remapped by name or by Entity.
7. For every resolved Metric, run normal semantic readiness **and** plan the
   inherited scope against that Metric. Emit a Candidate row only when all
   inherited refs and temporal requirements are executable for that Metric.

There is no recursive graph traversal, path search, popularity ranking,
heuristic target choice, or implicit Metric authoring. Discovery evaluates the
complete bounded one-edge resolution domain, de-duplicates candidate identity,
applies the total public ordering below, and only then retains the first
`limit` rows. The omitted-row count is persisted; omitted rows are not hidden
Candidate values and cannot be selected without rerunning with a larger legal
limit.

If one proposed endpoint resolves to several executable Metrics, every Metric
becomes an independent candidate. An endpoint that resolves to no Metric, a
Metric that fails semantic readiness, or a Metric that cannot execute the exact
inherited scope does not form a Candidate row. Discovery records exact aggregate
exclusion counts plus bounded exclusion coordinates in CandidateSet metadata;
it does not turn rejected resolution attempts into blocked candidates or
duplicate the semantic catalog's typed readiness repairs. Discovery never
broadens, drops, or guesses a time dimension, axis, slice, or cohort to
manufacture a candidate.

### Candidate row contract

Each semantic-hypothesis row carries:

```text
item_id
semantic_edge_ref
edge_relation           influences | related_to
candidate_semantic_ref
metric_ref
```

Every row is therefore a discovered analysis candidate, not a semantic object
readiness report or a rejected resolution attempt. Its semantic coordinates
within one CandidateSet are the serialized SemanticEdgeRef, candidate semantic
ref, and Metric ref. The artifact-local `item_id` also binds those coordinates
to the exact source artifact: it is `candidate_` plus the full lowercase
SHA-256 hex digest produced by the existing analysis canonical-JSON encoder for
`["semantic_hypothesis/v1", source_artifact_ref, semantic_edge_ref,
candidate_semantic_ref, metric_ref]`. Persistence validation recomputes the
digest and rejects blank, duplicate, malformed, or mismatched ids.

Rows are deterministically ordered by public edge relation, serialized
SemanticEdgeRef identity, candidate typed identity, and Metric typed identity.
That is a total ordering because SemanticEdgeRef names are unique and duplicate
candidate tuples are removed before ordering. They have no status, blocked
reason, repair, score, correlation, effect size, causal confidence,
recommendation, or artifact-level next step.

Rows intentionally remain narrow, but they are not the only persisted item
state. CandidateSet metadata stores one immutable, de-duplicated
`SemanticEdgeContext` per edge referenced by either a Candidate row or a
bounded exclusion:

```text
schema_version             semantic-edge-context/v1
semantic_edge_ref
business_definition
guardrails                 closed ordered tuple
anchor_role                outcome | related_endpoint
candidate_role             driver | related_endpoint
```

This is the exact author-time projection needed to understand the candidate
after cold recovery; it is not fetched from whichever ontology happens to be
live later. It carries no authoring API, provenance claim, evidence status, or
causal confidence. `CandidateSet.show()` joins this metadata into each displayed
candidate and renders the source Metric, relation in role-aware natural
language, business definition, bounded guardrails, candidate semantic ref,
resolved Metric ref, and the copyable selection call. Repeated Metrics resolved
from the same edge reuse one persisted context entry rather than duplicating it
in DataFrame cells.

`CandidateSet` remains the one artifact family, but its metadata and selection
are closed shape-discriminated unions. Existing scored shapes use
`ScoredCandidateSetMeta`, which requires their current `strategy`; the
`semantic_hypothesis` shape uses `SemanticHypothesisCandidateSetMeta`, with
`objective="semantic_hypotheses"` and no strategy or score field. Neither
variant inherits fields required only by the other. Its logical row schema
contains exactly the five scalar/ref fields above. The existing shared physical
CandidateSet union layout also contains the other shapes' columns with neutral
values; those columns carry no semantic-hypothesis meaning and validation rejects
any non-neutral value.
`OntologyMetricCandidate` is never a DataFrame cell or a serialized arbitrary
object. Shared source Metric, source Entity, ontology/semantic catalog
fingerprints, inherited-scope fingerprint, and the closed `resolution_summary`
below live in shape-specific CandidateSet metadata rather than repeating on
every row:

```text
resolution_summary:
  examined_edges
  candidate_count_before_limit
  emitted_candidates
  candidate_limit
  candidates_omitted
  excluded_counts:
    anchor_overlap
    source_metric_self_resolution
    no_observable_metric
    semantic_not_ready
    incompatible_inherited_scope
  exclusions                 SemanticHypothesisExclusion tuple, at most 20
  exclusions_omitted
```

`examined_edges` counts explicit edge identities with the required directional
or symmetric anchor match, including an edge excluded as `anchor_overlap`.
Edges with no qualifying anchor match do not enter bounded resolution and are
not counted;
`candidate_count_before_limit` counts valid de-duplicated rows before the
operator cap, `emitted_candidates` is the persisted row count, and
`candidates_omitted` is their exact difference. `anchor_overlap` counts edge
identities; `source_metric_self_resolution` counts resolved Metric attempts;
`no_observable_metric` counts proposed endpoint identities that resolved to no
Metric; the other exclusion counts count resolved Metric attempts, each assigned
to exactly one first failing gate. Every bounded exclusion stores its reason,
SemanticEdgeRef, optional candidate semantic ref, optional resolved MetricRef,
and `matched_anchor_refs`, and sets
`schema_version="semantic-hypothesis-exclusion/v1"`.
`candidate_semantic_ref` is absent only for `anchor_overlap`, where no unique
opposite endpoint exists and `matched_anchor_refs` contains both canonical edge
endpoints; for every other reason it is required and `matched_anchor_refs` is
empty. The exact counts include omitted exclusions.

The summary is execution diagnostics, not a collection of hidden candidates or
persisted readiness repairs. `CandidateSet.show()` always renders the exact
counts, candidate limit, candidate omission count, and bounded exclusions,
including `exclusions_omitted`. When candidates were omitted, it renders the
copyable rerun `session.discover.semantic_hypotheses(source, limit=<n>)` using
the smallest legal larger bound, or explains that 200 is the hard maximum. The
same exclusions project into `contract().issues` with repairs derived from the
live catalog rather than copied from discovery-time readiness output:

- `anchor_overlap` identifies the edge whose two endpoints were already in the
  source anchor set and recommends inspecting the edge definition;
- `source_metric_self_resolution` identifies the path that resolved back to
  the source Metric and recommends inspecting the endpoint/index mapping;
- `no_observable_metric` points to the exact edge/endpoint and ontology or
  semantic-index inspection help;
- `semantic_not_ready` provides the exact
  `session.catalog.readiness(refs=[metric_ref])` inspection call;
- `incompatible_inherited_scope` names the incompatible ref and points to the
  ordinary explicit-scope `observe` path for that Metric.

This projection extends the closed analysis `ArtifactIssue` union with one
`CandidateResolutionIssue` variant:

```text
issue_id
kind                       no_observable_metric
                         | anchor_overlap
                         | source_metric_self_resolution
                         | semantic_not_ready
                         | incompatible_inherited_scope
severity                   warning
source_refs
semantic_edge_ref
candidate_semantic_ref       optional only for anchor_overlap
metric_ref                 optional
historical                 bool
repair                     AnalysisRepair
```

It is an artifact-consumption issue, not datasource evidence, semantic
readiness state, or a Candidate row. It remains on the CandidateSet, is not
seeded into the Evidence Engine, and is not copied onto a MetricFrame when a
different valid candidate is observed.

If recovery cannot validate an exclusion's historical refs against the live
catalog, the issue is marked historical and offers inspect/environment repair
instead of a stale retry snippet. Existing scored discovery shapes retain their
existing logical score/strategy row contract unchanged. The shared
candidate-column layout and persistence validator must reject a score for the
semantic-hypothesis shape and reject non-neutral ontology-only columns for every
other shape.

### Explicit re-entry into analysis

`CandidateSet.select(item_id=...)` is the one public typed item-selection path
for every CandidateSet shape. This extension atomically replaces the current
numeric `select(rank=...)` coordinate; there is no rank alias and no optional
`rank`/`item_id` mega-signature. Existing scored rows retain score and
deterministic ordering for display, but selection uses their stable `item_id`.
Existing shape-specific selection values remain in the closed
`CandidateSelection` union. Their shared coordinate field is `item_id`;
`candidate_ref` and numeric `rank` are not parallel public identities.

The same atomic cutover makes `item_id` a validated identity rather than an
arbitrary string for every existing shape. Each uses `candidate_` plus the full
lowercase SHA-256 digest of canonical JSON beginning with its shape/schema tag
and followed by these semantic coordinates:

| Shape | Identity coordinates after the tag |
| --- | --- |
| `point_anomaly` | source artifact ref, keys, observation window |
| `period_shift` | source artifact ref, keys, observation window, baseline window |
| `driver_axis` | source artifact ref, typed axis identity |
| `slice` | source artifact ref, selector, optional window |
| `window` | source artifact ref, keys, window |
| `cross_sectional_outlier` | source artifact ref, keys, peer scope |
| `semantic_hypothesis` | source artifact ref, SemanticEdgeRef, candidate semantic ref, MetricRef |

Direction, score, rank, reason codes, and display position do not participate in
identity. Every CandidateSet recovery validator recomputes the shape-specific
digest, enforces per-set uniqueness, and rejects missing or extra coordinates.
Thus a rerun over the same source artifact and semantic coordinates retains ids
even if display scoring/order changes; a changed source artifact or semantic
coordinate produces a different id.

The selection algebra likewise has no base class that makes scoring universal.
Existing scored variants inherit `ScoredCandidateSelectionBase` and retain
their required `score` and `reason_codes`. `OntologyMetricCandidate` is an
independent `CandidateSelection` variant sharing only artifact coordinates
(`candidate_set_ref`, `item_id`, and `source_artifact_ref`);
`candidate_set_ref` is the exact persisted CandidateSet artifact ref. Score,
rank, strategy, and reason codes are absent and rejected by its persistence
schema.
All union variants set `extra="forbid"` and dispatch by the CandidateSet shape.

For `CandidateSet[semantic_hypothesis]`, `select(...)` returns the immutable
`OntologyMetricCandidate` directly rather than a wrapper selection with an
indirect observation target. `OntologyMetricCandidate` joins the selected row
with its CandidateSet metadata and becomes one additional closed
`CandidateSelection` variant.

`OntologyMetricCandidate` has no public constructor and carries:

- resolved MetricRef;
- candidate-set and item identities;
- SemanticEdgeRef;
- public edge relation, candidate semantic ref, and `SemanticEdgeContext`;
- inherited scope and time contract;
- readiness fingerprint;
- the ordered upstream `CandidateOrigin` tuple retained by the CandidateSet
  lineage.

The agent must explicitly select an item and call
`session.observe(candidate)`. This is a distinct overload, not an expansion of
`SemanticInput[K]`:

```python
MetricObservationInput = SemanticInput[MetricKind] | RuntimeMetricExpr

session.observe(
    metrics: (
        MetricObservationInput
        | list[MetricObservationInput]
        | tuple[MetricObservationInput, ...]
    ),
    *,
    time_scope=...,
    grain=...,
    dimensions=...,
    slice_by=...,
    time_dimension=...,
    cohort=...,
    expect_shape=...,
    analysis_purpose=None,
) -> MetricFrame

session.observe(
    candidate: OntologyMetricCandidate,
    *,
    analysis_purpose: str | None = None,
) -> MetricFrame
```

The candidate overload accepts exactly one selected candidate and only the
optional `analysis_purpose`. Passing a candidate inside a list/tuple, mixing it
with a Metric input, or attempting multi-root observation raises
`candidate_not_observable`. Supplying any explicit scope keyword from the
ordinary overload—including an explicit `None`—raises
`candidate_scope_override_forbidden` before unwrapping. An agent that intends a
different scope must explicitly call `session.observe(candidate.metric_ref,
...)`; that is an ordinary Metric observation and deliberately does not claim
the CandidateOrigin lineage.

The candidate overload rechecks the candidate
identity, bound ontology/catalog fingerprints, readiness fingerprint, and
inherited-scope fingerprint before unwrapping the governed Metric. It records
the upstream origin tuple followed by the newly selected origin in the new
MetricFrame lineage, retaining the first occurrence of each
`(candidate_set_ref, item_id)` pair. It does not alter ordinary MetricRef
behavior.

Candidate creation is a point-in-time validation, not a promise of permanent
readiness. A forged `OntologyMetricCandidate` or one that no longer passes
identity, fingerprint, readiness, or inherited-scope revalidation raises the
existing structured `candidate_not_observable` path with the current cause and
repair.

### No hidden structural edge families

StateModel transitions and EventPattern ordering remain owned by the semantic
and analysis cores and are not projected as ontology edges. Semantic ownership,
dependencies, executable Relationships, and StateModel membership remain
read-only catalog structure. V1 has no authored temporal, taxonomic,
composition, or comparison-only edge family.

No SemanticEdge can generate a join, filter, MetricFrame, EventFrame,
LifecycleFrame, SubjectSet, fact, or proposition.

## Evidence Engine Integration

The Evidence Engine and its closed analysis-subject union, including Metric,
Event, Lifecycle, and SubjectSet variants, are defined in the companion design.
Ontology adds no evidence subject; ontology edges and candidate rows are
lineage, not evidence subjects.

### Candidate non-seeding

`CandidateSet[semantic_hypothesis]` emits no finding, observation,
proposition, fact, open item, or system-level recommended follow-up. Edge,
candidate, and resolved Metric refs plus the bounded edge context stay in the
candidate payload and lineage.

Selecting and observing an item creates one typed `CandidateOrigin` lineage
payload on the resulting MetricFrame. It has
`schema_version="candidate-origin/v1"` and contains the ontology catalog
fingerprint, semantic catalog fingerprint, candidate-set ref, item id,
SemanticEdgeRef, public relation, source/candidate/Metric refs, the immutable
`SemanticEdgeContext` projection, inherited-scope fingerprint, and readiness
fingerprint. It is not an evidence subject or finding. Persisting the bounded
context here lets downstream and recovered artifacts explain the historical
candidate without consulting a changed live ontology.

Every artifact-producing operator that consumes one or more MetricFrames, or a
derived artifact already carrying origins, preserves the complete ordered,
de-duplicated `CandidateOrigin` tuple in its persisted lineage. `correlate` and
`hypothesis_test` additionally copy that exact tuple into their association or
tested-hypothesis evidence derivation. No origin is rewritten, dropped on
recovery, or converted into a causal premise. A recovered artifact renders
historical origins even when its project now binds a different ontology; a new
`observe` call, by contrast, requires the live fingerprint checks above.

For one input, origin order is preserved. For several inputs, operators scan
their public input order and then each input's stored origin order, retaining
the first occurrence of each `(candidate_set_ref, item_id)` pair. Recovery
validates that deterministic order and rejects conflicting payloads carrying
the same pair.

If an observed candidate later participates in `correlate` or
`hypothesis_test`, the existing association or tested-hypothesis evidence path
retains the candidate-set ref, item id, and SemanticEdgeRef. Statistical
association or hypothesis-test evidence does not upgrade the originating edge
to causal evidence.

## Analysis Capability Registration

The analysis capability registry receives an independent ontology discovery
extension. The cumulative semantic execution surface is specified in the
companion design.

### Ontology discovery extension

Registers:

- `discover.semantic_hypotheses`;
- `CandidateSet[semantic_hypothesis]`;
- the consumed-not-constructed `OntologyMetricCandidate` selection value;
- the `CandidateResolutionIssue` analysis-artifact issue variant;
- the uniform `CandidateSet.select(item_id=...)` read for all candidate shapes;
- the ontology-binding precondition and empty-result diagnostics.

The operator descriptor accepts `source: MetricFrame | DeltaFrame`, with Delta
admission restricted to the four Metric-derived shapes named above. It adds one
conditional continuation edge to MetricFrame and only those Delta shapes;
Event funnel and future non-Metric Delta contracts never advertise it. An
admitted concrete artifact's `contract()` renders the affordance and evaluates
these closed preconditions before execution:

```text
persisted_source                 pass | fail
single_metric_lineage            pass | fail
ontology_binding_ready           pass | fail
```

Every failed precondition has visible repair. `ontology_binding_ready` branches
on the private state without exposing that state object: an absent root points
to `marivo.help("ontology.authoring")`, while an unavailable binding renders its
stored validation issues and the direct
`mo.load(semantic=session.catalog)` inspection call. Missing or ambiguous
lineage identifies the source artifact and the ordinary observation/compare
path that produces one recoverable Metric identity. A configured valid ontology
with no eligible edge passes these preconditions and returns a successful empty
set.

The operator remains statically discoverable through
`marivo.help("analysis.discover.semantic_hypotheses")` even when no ontology is
configured. Its runtime precondition distinguishes that state from an empty
result.

This extension does not change any existing Metric or Delta operator's meaning.
It does extend the capability DAG, runtime family gate, and artifact contract
with the conditional discovery continuation above; otherwise a cold agent
holding the source frame could not discover the new capability mechanically.

## Help, Browsing, and Recovery

Public guidance preserves one owner per contract:

- `marivo.help("ontology")` provides the bounded ontology index,
  `marivo.help("ontology.authoring")` describes loading and authoring, and
  `marivo.help("ontology.influences")` and
  `marivo.help("ontology.related_to")` each own the constructor's endpoint
  roles, `ai_context` requirement, constraints, example, and non-executable
  boundaries;
- `marivo.help("analysis.discover.semantic_hypotheses")` describes
  relation admission, scope compatibility, resolution-summary units, uniform
  item-id selection, direct observation, and recovery of candidate results;
- `CandidateSet.show()` renders bounded edge context, exact resolution counts,
  exclusion examples, omitted counts, and copyable item-id selection calls;
- `CandidateSet.contract()` provides mechanically valid continuations and live
  repairs for exclusion issues;
- structured errors provide typed repair.

Ontology authors and auditors may inspect the OntologyCatalog through `mo`, but
analysis agents use the typed candidate bridge. They do not manipulate raw
SemanticEdge objects.

The global help contracts for Event, StateModel, StateProjection, SubjectSet,
and Event/Lifecycle operators are specified in the companion design.

## Lineage

CandidateSet lineage retains its exact source artifact, ontology and semantic
catalog fingerprints, and persisted row/context payload. A selected observation
adds exactly the `CandidateOrigin` payload defined above; no second lineage
shape or derivable ordering payload is introduced. Neither form turns ontology
context into executable truth or causal evidence.

## Structured Errors

All new public errors follow the existing SemanticError or AnalysisError
templates and include expected, received, location, and typed repair.
Ontology authoring, loading, and native-help errors live under `mo.errors` and
subclass `SemanticError`; discovery-source and candidate-observation errors
live under `mv.errors` and subclass `AnalysisError`. Neither module re-exports
the other's errors.
Raw dictionaries and other non-`AiContextValue` inputs retain the existing
`invalid_ai_context` authoring error before edge-contract validation.

| Error kind | Trigger |
| --- | --- |
| `ontology_not_configured` | ontology-guided discovery called without configured ontology content |
| `ontology_unavailable` | a configured project ontology did not validate against the Session's semantic catalog; discovery includes the stored validation issues and direct-load repair |
| `ontology_help_target_not_found` | the ontology-native resolver rejects an unknown target; `marivo.help(...)` adapts it to the existing global help error |
| `invalid_ontology_ref` | SemanticEdge endpoint is not an allowed exact typed ref, or that ref does not resolve to an allowed semantic kind |
| `invalid_semantic_edge` | a constructor receives invalid endpoint kinds or pairing, an empty `ai_context.business_definition`, duplicate identity, or otherwise violates its closed relation contract |
| `missing_metric_lineage` | discovery source has no recoverable source Metric |
| `ambiguous_metric_lineage` | discovery source resolves to several source Metrics |
| `candidate_not_observable` | a forged/stale candidate fails revalidation, or a candidate is supplied in a list, mixed input, or multi-root observation |
| `candidate_scope_override_forbidden` | the candidate overload receives an explicit ordinary-observation scope keyword |

An empty configured ontology result is not an error. Excluded resolution paths
are summarized in CandidateSet metadata, rendered in `show()`, and exposed as
repairable `contract().issues`; they are not Candidate rows or operator errors.

## Module Ownership

### `marivo.ontology as mo`

Owns optional SemanticEdge authoring, typed-ref and context validation,
bounded OntologyCatalog inspection, and catalog fingerprinting. It consumes the
semantic catalog's authoritative read-only dependency indexes and contributes
native descriptors to the top-level help coordinator, but owns no executable
expression or analysis artifact.

The first delivery pins `mo.__all__` with an API snapshot containing exactly
`OntologyCatalog`, `SemanticEdgeRef`, `errors`, `influences`, `related_to`, and
`load`. Ontology-owned typed errors live under `mo.errors`, following the
existing module pattern; analysis-owned discovery and observation errors remain
under `mv.errors`. Internal edge IR/factories, semantic registry types, and
omitted relation names are not exported. The top-level help bootstrap must be
able to load the ontology descriptors without requiring authored ontology
content.

The executable `marivo.semantic as ms` and the typed `marivo.analysis as mv`
modules are specified in the companion design. `mv` owns typed operators,
sessions, artifacts, recovery, Evidence Engine integration, and the narrow
semantic-hypothesis candidate bridge registered by this extension.

### Future decision extension

May define Rule, Policy, and Action by consuming ontology context and assessed
evidence through explicit read boundaries. It cannot retroactively change the
meaning of a semantic definition or an evidence record. No public decision API
is defined here.

## Behavioral Fixtures

### Constructor constraints

Each public constructor accepts its legal endpoint matrix and rejects adjacent
invalid combinations with `invalid_semantic_edge`. In particular,
`mo.influences(...)` rejects an EventRef driver or outcome, and
`mo.related_to(...)` rejects an EventRef or DimensionRef endpoint. Both reject
an empty business definition with `invalid_semantic_edge` and a raw context
dict with the existing `invalid_ai_context`; public `basis=` and `provenance=`
arguments are absent from their signatures and help. Authoring
`mo.related_to(left=a, right=b, ...)` and then the swapped `(b, a)` form yields
the same canonical pair and a deterministic duplicate-definition error rather
than two catalog edges; identical `left` and `right` are rejected. Duplicate
edge names are rejected before definition comparison. Generic and omitted
constructors are not exported;
`marivo.help("ontology.edge")`, `marivo.help("ontology.part_of")`,
`marivo.help("ontology.precedes")`, `marivo.help("ontology.specializes")`, and
`marivo.help("ontology.contrasts_with")` are unknown targets rather than
compatibility routes.

### Public exports and help surface

An API snapshot asserts the exact `mo.__all__` set above. The argument-free
`<analysis-python> -m marivo help` bootstrap lists `marivo.ontology as mo` and
the top-level coordinator routes `ontology`, `ontology.authoring`,
`ontology.influences`, and `ontology.related_to` with no authored root present.
The ontology-native resolver raises `ontology_help_target_not_found`; the public
coordinator adapts an unknown `ontology.*` target to the existing global error
with bounded `ontology.*` suggestions. Omitted constructors do not fall through
to the global `authoring` topic or another native surface.

### Relation necessity

An `influences` edge proposes only its driver through the directed admission
rule. A `related_to` edge proposes only the endpoint opposite exactly one
matching anchor. The catalog contains no third relation whose endpoint
contract, traversal, validation, or consumer duplicates either one under
another label.

### Symmetric and self-candidate suppression

A `related_to` edge with exactly one endpoint in the source anchor set proposes
the other endpoint once. When both endpoints are anchors, it emits no row,
increments `anchor_overlap` once, and exposes one bounded exclusion. An
EntityRef or MeasureRef endpoint whose reverse index resolves to the source
Metric emits no row and increments `source_metric_self_resolution` for that
Metric attempt. Repeated index paths to the same
edge/candidate-semantic-ref/Metric tuple produce one item.

### Candidate judgment context

Two `influences` edges resolve to different Metrics for the same source. Their
rows remain the five-field Candidate contract, while `show()` renders each
edge's historical business definition, guardrails, role-aware relation text,
and copyable `select(item_id="...")` call from persisted
`SemanticEdgeContext`. After the project ontology changes, recovery renders
the same historical context rather than silently substituting the live edge.

### Ontology not configured versus empty

With no ontology root, semantic-hypothesis discovery raises
`ontology_not_configured` while all other analysis remains legal. With a valid
ontology catalog containing no eligible edge for the source Metric, discovery
returns a committed empty CandidateSet whose `show()` distinguishes zero
admitted edges from admitted-but-excluded resolution paths. A compatible source
frame's `contract()` advertises the discovery affordance in both states; the
unconfigured state carries an ontology-authoring repair, while the
configured-empty state is a successful call result.

With an authored but invalid root, Session creation and recovery still succeed
with an `unavailable` ontology binding and ordinary Metric/Event/Lifecycle
analysis remains legal. Ontology discovery raises `ontology_unavailable` with
the original validation issues. Direct `mo.load(...)` raises the underlying
typed validation error. No stale compiled ontology fingerprint is consulted.

### Causal-hypothesis candidate

For `refund_rate -> healthy_order_rate`, discovery from an observation of
`healthy_order_rate` returns `refund_rate` as a driver hypothesis through the
public `influences` relation. Discovery from an observation of `refund_rate`
does not return `healthy_order_rate` as a driver. The candidate contains no
causal confidence and creates no fact. Only an explicit observe followed by
correlation or hypothesis test produces existing statistical evidence with
retained edge lineage.

### Non-Metric endpoint resolution

One proposed endpoint resolves through the semantic dependency index to several
executable Metrics; all appear as separate deterministically ordered candidates.
Another endpoint resolves to none, emits no row, and increments
`resolution_summary.excluded_counts.no_observable_metric`. Its exact edge and
endpoint appear in the bounded exclusions and in `contract().issues` with an
ontology/semantic-index inspection repair, never as a blocked Candidate.

### Candidate bound and identity

A fixture producing 75 valid tuples with `limit=50` persists 50 rows,
`candidate_count_before_limit=75`, `emitted_candidates=50`, and
`candidates_omitted=25`. Repeating the call over the same definitions yields
the same total order and full-digest item ids; `limit=75` yields the same first
50 followed by 25 more. Blank, duplicate, truncated, or digest-mismatched
`item_id` values fail recovery validation. Limits outside `1..200`, including
`None` and booleans, fail before discovery.

### Scope incompatibility

A source MetricFrame grouped by a source-only Dimension or time dimension
discovers a target Metric rooted in another Entity. Even if that target is
semantically ready, discovery emits no Candidate row when the exact source scope
cannot be planned for it and increments
`resolution_summary.excluded_counts.incompatible_inherited_scope`. Discovery
never drops the offending axis or chooses another time dimension. `show()` names
the Metric and incompatible ref; `contract().issues` points to the ordinary
explicit-scope observation path if the agent chooses to investigate the Metric
outside this bridge.

The cohort component of inherited scope depends on the companion design's
SubjectSet-backed cohort observation slice. A candidate inherits the exact
cohort binding and fingerprint; target planning must accept the same subject
identity and membership artifact. It is excluded as
`incompatible_inherited_scope` if it cannot. This ontology bridge cannot ship
its cohort-aware path before that companion slice exists and never substitutes
an Entity-wide filter for a SubjectSet.

### Candidate observation overload

Observing one selected `OntologyMetricCandidate` with only
`analysis_purpose=` succeeds, preserves upstream origins, and appends the
selected `CandidateOrigin`. Supplying
`time_scope=`, `grain=`, `dimensions=`, `slice_by=`, `time_dimension=`,
`cohort=`, or `expect_shape=`—even as `None`—raises
`candidate_scope_override_forbidden`. A candidate in a collection or mixed
with a MetricRef raises `candidate_not_observable`. Explicitly observing
`candidate.metric_ref` with a new scope remains legal ordinary observation and
does not copy candidate-origin lineage.

### Cold-agent path

This integration fixture spans both designs; steps 1, 2, and 5 exercise the
companion semantic execution surface. A cold general-purpose coding agent:

1. discovers ready Metrics, Events, StateModels, and StateProjections through
   the semantic catalog;
2. completes Metric, Event, and Lifecycle paths with no ontology configured;
3. discovers the conditional ontology continuation from the source artifact's
   `contract()` and reads its focused help;
4. calls ontology discovery, reads bounded candidate context and resolution
   diagnostics from `show()`, selects one stable `item_id`, and explicitly
   observes the returned `OntologyMetricCandidate`;
5. recovers EventFrame and LifecycleFrame artifacts and reads their contracts;
6. never manipulates source internals, raw SemanticEdge objects, or a graph
   query language;
7. never describes a candidate, association, test, or violation as causal
   proof or an automatically binding decision.

## Acceptance Criteria

The design is accepted when:

1. `marivo.ontology` owns optional knowledge context and accepts only the
   closed typed semantic-ref union.
2. Ontology v1 has no standalone node, runtime instance, generic traversal, or
   executable semantic authority.
3. Derived structural indexes remain authoritative semantic-catalog state;
   ontology reads them through immutable views and cannot author or override
   them as SemanticEdge objects.
4. Exactly two relation-specific constructors are public and help-resolvable:
   `mo.influences(...)` and `mo.related_to(...)`. Their role-specific
   signatures enforce the endpoint matrix and derive relation, endpoint
   interpretation, and directionality without caller-supplied
   relation/family/predicate fields.
   Both require the existing `AiContextValue` with a non-empty business
   definition and accept no public `basis` or relation-specific `provenance`.
   Generic `mo.edge(...)`, `_edge(...)`, `part_of`, `precedes`, `specializes`,
   `contrasts_with`, and `causes` are absent from exports, help, and accepted
   authoring input, with no compatibility aliases. An authored edge cannot
   create identity, join, filter, population, readiness, SQL, artifact,
   proposition, fact, or causal evidence.
5. Session creation and cold recovery attempt the one project-root load and
   record exactly `absent`, `ready`, or `unavailable` without making ontology a
   prerequisite for ordinary analysis. Discovery raises
   `ontology_not_configured` for `absent`, uses only `ready`, and raises
   `ontology_unavailable` with stored validation issues for `unavailable`;
   there is no persisted compiled-catalog mismatch state.
6. `discover.semantic_hypotheses` derives only the source Metric, root Entity,
   and complete explicit Measure lineage as anchors, then performs at most one
   directionally admitted explicit edge traversal plus one mechanical
   semantic-index resolution and returns every resolved Metric that passes
   semantic readiness and exact inherited-scope planning.
7. Candidates are unscored, deterministically de-duplicated and totally
   ordered, bounded by `limit` in `1..200`, and never automatically executed or
   recommended. Every row has a recomputable full-digest `item_id`, is a valid
   analysis candidate, and retains explainable edge context after recovery.
8. Anchor overlaps, source-Metric self-resolution, unresolved endpoints,
   semantically unready Metrics, and incompatible scopes emit no Candidate
   row. Exact aggregate counts plus bounded affected refs are rendered by
   `show()` and become live repairable
   `CandidateResolutionIssue` entries in `contract().issues`; `observe`
   revalidates selected candidates against current state.
9. Missing ontology and configured-empty ontology have distinct typed
   behavior.
10. Compatible MetricFrame and Metric-derived Delta contracts expose the
    conditional ontology-discovery continuation with visible repairs for
    failed preconditions; non-Metric Delta shapes never advertise it.
11. Every CandidateSet shape uses the one stable
    `select(item_id=...) -> CandidateSelection` contract with no numeric-rank
    alias; semantic-hypothesis selection returns `OntologyMetricCandidate`
    directly. Scored and semantic-hypothesis shapes have separate closed
    metadata/selection bases, and the candidate observe overload forbids scope
    overrides and collection/mixed-input ambiguity.
12. Candidate commits create no findings at all.
13. `CandidateOrigin` persists edge context from explicit observation through
    recovery and association/test evidence without becoming causal evidence.
14. Rule, Policy, and Action remain outside semantic, ontology, analysis, and
    evidence type algebras in this release.
15. `SemanticEdgeRef` has one ontology-owned serialized identity outside the
    semantic kind/ref registry; `OntologyCatalog` has a bounded result contract;
    `mo.__all__` is snapshot-pinned; and the global help coordinator resolves
    the fourth native `ontology` surface plus its qualified targets, adapting
    the ontology-native unknown-target error into the existing bounded global
    error.
16. A cold agent completes all legal paths, including ontology discovery,
    without raw ontology traversal or source-level authoring objects.

## Implementation Boundary

This document specifies the shipped public contract for the ontology discovery
extension. The implementation remains optional at runtime: ordinary semantic,
Metric, Event, and Lifecycle paths do not require an ontology root.

SemanticEdge authoring, session binding/recovery, ontology loading, candidate
discovery, uniform item-id candidate selection, inherited-scope validation,
edge-context and exclusion rendering, capability-DAG integration, lineage
propagation, the closed `ArtifactIssue` union extension, and non-seeding
evidence behavior must ship atomically as one ontology discovery extension.

Lineage propagation is a cross-cutting analysis change, not an obligation local
to `correlate` and `hypothesis_test`: the shared `BaseFrameMeta` schema, every
artifact-producing consumer of an origin-bearing frame, multi-input
de-duplication, cold recovery, derived-evidence serialization, and
help/rendering paths must preserve the complete ordered `CandidateOrigin`
tuple. The focused association/test operators additionally copy it into their
evidence derivation.

The same release performs a clean CandidateSet selection cutover from
`select(rank=...)` to `select(item_id=...)` for every existing and new shape.
There is no compatibility alias. The owning analysis specification, capability
registry, runtime gate, public annotations/exports, focused help and runnable
examples, persistence validators, English and Chinese latest documentation,
packaged analysis skill, and contract/drift tests must move together before the
extension is advertised as available.

The delivery also adds semantic-catalog-owned Entity-to-Metric and
Measure-to-Metric reverse indexes, ontology live-help registration/bootstrap
tests and `mo.__all__` snapshots, item-id recipes and recovery validators for
every existing CandidateSet shape, and the separate scored/unscored selection
bases. The cohort-aware discovery path depends on the companion SubjectSet
cohort-observation slice; if that slice is not delivered, cohort-bound source
artifacts must be rejected as `incompatible_inherited_scope` rather than
silently losing scope.

The ontology extension may ship after the companion delivery slices that own
the semantic refs it exposes. It is never a prerequisite for current Metric
analysis or any Event/Lifecycle execution slice.

This specification revision is implemented together with its runtime, live-help,
packaged-skill, test, and English/Chinese latest-site documentation surfaces.
