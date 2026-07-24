# Marivo Ontology Extension Design

Status: written-review revisions integrated; pending written-spec re-review

Date: 2026-07-13

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
reviewable contextual relationships, and builds a read-only index over
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
import marivo.ontology as mo

mo.help()
ontology = mo.load(semantic=catalog)
```

`mo.help(...)` owns ontology authoring and browsing contracts. `mo.load(...)`
validates all endpoints against the supplied semantic catalog and returns an
immutable `OntologyCatalog`. The authored ontology root is optional. The
returned catalog records `configured=False` when no ontology root is present;
ordinary semantic and analysis loading still succeeds.

Ontology code ships with Marivo; optionality concerns authored ontology
content, not a plugin or alternate package installation.

### No second executable identity

Ontology v1 contains no standalone term, generic node, runtime instance, or
independent business-object identity. Every endpoint is one of this closed
typed-ref union:

```text
EntityRef
DimensionRef
TimeDimensionRef
MeasureRef
MetricRef
RelationshipRef
EventRef
StateModelRef
```

Bare semantic-id strings are not accepted by the authoring API. Physical
datasource refs and analysis artifact refs are not ontology endpoints.

### Read-only semantic index

The `OntologyCatalog` builds deterministic read-only indexes from executable
semantic declarations, including:

- Entity ownership of Dimensions, TimeDimensions, and Measures;
- Metric dependencies and root Entity;
- Measure-to-Metric observation relationships (`measures` resolution);
- Event source Entity and participant roles;
- StateModel subject Entity and transition Events;
- executable Relationship adjacency.

These entries are derived indexes, not authored `SemanticEdge` objects. They
have no independent edge identity and cannot be overridden. The ontology
extension may use them for search, explanation, and the final mechanical
resolution step of a bounded candidate bridge, but never as new executable
facts.

### Explicit SemanticEdge

The only authored ontology object in v1 is `SemanticEdge`:

```python
refund_pressure = mo.edge(
    name="refund_pressure_influences_order_health",
    source=refund_rate,
    target=healthy_order_rate,
    family="causal_hypothesis",
    predicate="influences",
    rationale="Refund pressure may precede deterioration in order health.",
    provenance=mo.business_hypothesis(owner="commerce-analytics"),
)
```

`mo.edge(...)` returns `SemanticEdgeRef`. Every edge has a stable identity,
typed source and target refs, one closed family/predicate combination,
rationale, and provenance.

| Family | Predicates | Allowed use | Forbidden inference |
| --- | --- | --- | --- |
| `temporal` | `precedes`, `follows` | Candidate Event navigation | Actual row order or automatic sequence |
| `causal_hypothesis` | `causes`, `influences` | Driver-hypothesis candidate | Causal fact, effect, or confidence |
| `taxonomic` | `specializes`, `part_of` | Browse and explanation | Property, identity, or readiness inheritance |
| `contextual` | `related_to`, `contrasts_with` | Association-hypothesis candidate | Joinability or statistical association |

All authored edges require provenance. A causal-hypothesis edge additionally
requires either a reviewable external source or an explicitly labeled business
hypothesis owner. The edge itself is never evidence.

SemanticEdge carries no identity mapping, row predicate, join key, expression,
Metric definition, readiness result, artifact, or runtime truth value. It
cannot make an invalid semantic plan valid.

### Loading states

Ontology state is closed and observable:

- not configured: the semantic and analysis cores remain fully usable;
- configured and valid: ontology browsing and candidate discovery are enabled;
- configured but empty for a source: discovery returns an empty CandidateSet;
- configured but invalid: `mo.load(...)` fails with typed endpoint or edge
  validation evidence rather than silently dropping definitions.

Calling ontology-guided discovery when the session has no configured ontology
raises `OntologyNotConfiguredError` with
`kind="ontology_not_configured"`. This is distinct from a successful empty
candidate result.

## Typed Ontology-to-Analysis Bridge

### Public operator

`session.discover.semantic_hypotheses(source)` is the sole v1 execution bridge
from ontology context into typed analysis.

`source` must be a persisted `MetricFrame` or `DeltaFrame` with exactly one
recoverable source Metric lineage. A Delta comparing two observations of the
same Metric is legal. Missing or multiple Metric identities raise
`missing_metric_lineage` or `ambiguous_metric_lineage`; the operator never
selects one.

The result is the existing `CandidateSet` family with closed shape
`semantic_hypothesis`.

### Resolution algorithm

For the source Metric and its root Entity, discovery performs exactly these
steps:

1. Read explicit outgoing or incoming `causal_hypothesis` and `contextual`
   SemanticEdges whose configured direction is relevant to the source.
2. Traverse at most one such explicit edge.
3. If the target is a MetricRef, use that Metric directly.
4. Otherwise consult the semantic catalog's read-only dependency and
   `measures` indexes to resolve every Metric that explicitly observes or
   depends on the target.
5. Evaluate normal semantic readiness for each resolved Metric under the
   inherited analysis scope and time contract.

There is no recursive graph traversal, path search, popularity ranking,
heuristic target choice, or implicit Metric authoring.

If one target resolves to several Metrics, every Metric becomes an independent
candidate. If it resolves to none, one blocked candidate preserves the target
and the reason `no_observable_metric`. If a Metric exists but readiness fails,
the blocked row preserves the Metric and typed readiness repair.

### Candidate row contract

Each semantic-hypothesis row carries:

```text
candidate_kind          driver_hypothesis | association_hypothesis
source_metric_ref
source_entity_ref
semantic_edge_ref
edge_family
target_semantic_ref
resolved_metric_ref     optional
status                  ready | blocked
blocked_reason           optional closed reason
analysis_target          optional SemanticMetricCandidate
affordances              item-level only
```

Candidate identity is the stable tuple of edge ref, target semantic ref, and
resolved Metric ref or blocked sentinel. Rows are deterministically ordered by
edge family, target typed identity, and resolved Metric identity. They have no
score, correlation, effect size, causal confidence, recommendation, or
artifact-level next step.

### Explicit re-entry into analysis

A ready row exposes `analysis_target`, an immutable
`SemanticMetricCandidate`. It has no public constructor and carries:

- resolved MetricRef;
- candidate-set and item identities;
- SemanticEdgeRef;
- inherited scope and time contract;
- readiness fingerprint.

The agent must explicitly select the row and call `session.observe(target)`.
`observe` unwraps the governed Metric and records the candidate origin in the
new MetricFrame lineage. It does not alter ordinary MetricRef behavior.

A blocked row exposes no `analysis_target` or `observe` affordance. Selecting
that attribute returns the row's typed readiness or semantic repair instead of
a MetricRef.

### Other edge families

Temporal edges appear only in Event object details as candidate navigation.
The caller still supplies every Event, participant role, and order to
`events.match`; `events.funnel` consumes the resulting journey artifact.

Taxonomic edges appear only in ontology browsing and explanation. They do not
create analysis populations, expand filters, inherit semantic fields, or
change readiness.

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
target, readiness, reason, and item affordances stay in the candidate payload
and lineage.

If an observed candidate later participates in `correlate` or
`hypothesis_test`, the existing association or tested-hypothesis evidence path
retains the candidate-set id, item id, and SemanticEdgeRef. Statistical
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
- the consumed-not-constructed `SemanticMetricCandidate` value;
- the ontology-configured precondition and candidate item affordances.

The operator remains statically discoverable in focused analysis help even
when no ontology is configured. Its runtime precondition distinguishes that
state from an empty result.

This extension does not change existing Metric operators or family edges.

## Help, Browsing, and Recovery

Public guidance preserves one owner per contract:

- `mo.help(...)` describes ontology loading, SemanticEdge authoring, browsing,
  provenance, and non-executable boundaries;
- `mv.help(...)` describes semantic-hypothesis discovery, family validation,
  and recovery of candidate results;
- `show()` provides bounded current content;
- `contract()` provides mechanically valid continuations;
- structured errors provide typed repair.

Ontology authors and auditors may inspect the OntologyCatalog through `mo`, but
analysis agents use the typed candidate bridge. They do not manipulate raw
SemanticEdge objects.

The `ms.help(...)` and `mv.help(...)` contracts for Event, StateModel,
StateProjection, SubjectSet, and Event/Lifecycle operators are specified in the
companion design.

## Lineage

Ontology candidate lineage records OntologyCatalog fingerprint,
SemanticEdgeRef, source and target refs, resolved Metric or blocked reason,
candidate identity, readiness fingerprint, and deterministic ordering key.

Lineage records provenance. It never turns ontology context into executable
truth or causal evidence.

## Structured Errors

All new public errors follow the existing SemanticError or AnalysisError
templates and include expected, received, location, and typed repair.

| Error kind | Trigger |
| --- | --- |
| `ontology_not_configured` | ontology-guided discovery called without configured ontology content |
| `invalid_ontology_ref` | SemanticEdge endpoint does not resolve to an allowed semantic kind |
| `invalid_semantic_edge` | family, predicate, provenance, or direction violates the closed edge contract |
| `missing_metric_lineage` | discovery source has no recoverable source Metric |
| `ambiguous_metric_lineage` | discovery source resolves to several source Metrics |
| `candidate_not_observable` | selected row has no ready analysis target |

An empty configured ontology result is not an error. A blocked candidate is a
valid CandidateSet row, not a failed operator commit.

## Module Ownership

### `marivo.ontology as mo`

Owns optional SemanticEdge authoring, ontology provenance, typed-ref
validation, the read-only semantic dependency index, ontology browsing, and
OntologyCatalog fingerprinting. It owns no executable expression or analysis
artifact.

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

### Ontology not configured versus empty

With no ontology root, semantic-hypothesis discovery raises
`ontology_not_configured` while all other analysis remains legal. With a valid
ontology catalog containing no eligible edge for the source Metric, discovery
returns a committed empty CandidateSet.

### Causal-hypothesis candidate

A causal-hypothesis edge from the source Metric or root Entity produces a
`driver_hypothesis` candidate. The candidate contains no causal confidence and
creates no fact. Only an explicit observe followed by correlation or hypothesis
test produces existing statistical evidence with retained edge lineage.

### Non-Metric target resolution

One edge target resolves through the semantic dependency index to several
Metrics; all appear as separate deterministically ordered candidates. Another
target resolves to none and remains visible as one blocked row with no observe
affordance.

### Temporal navigation

A temporal edge between Events appears in Event details but neither executes
nor pre-fills EventPattern order. The caller must still pass the full typed
pattern to `events.match`; funnel consumes the resulting journey artifact.

### Cold-agent path

This integration fixture spans both designs; steps 1, 2, and 5 exercise the
companion semantic execution surface. A cold general-purpose coding agent:

1. discovers ready Metrics, Events, StateModels, and StateProjections through
   the semantic catalog;
2. completes Metric, Event, and Lifecycle paths with no ontology configured;
3. discovers ontology hypotheses through the typed CandidateSet surface;
4. inspects ready and blocked candidates and explicitly observes one ready
   target;
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
3. Derived structural indexes are read-only and cannot be authored or
   overridden as SemanticEdge objects.
4. An authored SemanticEdge cannot create identity, join, filter, population,
   readiness, SQL, artifact, proposition, fact, or causal evidence.
5. `discover.semantic_hypotheses` performs at most one explicit edge traversal
   plus one mechanical semantic-index resolution and returns every resolved
   Metric or one blocked target.
6. Candidates are unscored, deterministic, item-affordance-only, and never
   automatically executed or recommended by the system.
7. Missing ontology and configured-empty ontology have distinct typed
   behavior.
8. Candidate commits create no findings at all.
9. Later association/test evidence preserves candidate and SemanticEdgeRef
   lineage without becoming causal evidence.
10. Rule, Policy, and Action remain outside semantic, ontology, analysis, and
    evidence type algebras in this release.
11. A cold agent completes all legal paths, including ontology discovery,
    without raw ontology traversal or source-level authoring objects.

## Implementation Boundary

This document specifies a future public contract only.

SemanticEdge authoring, ontology loading, candidate discovery, candidate
selection, lineage, and non-seeding evidence behavior must ship atomically as
one ontology discovery extension.

The ontology extension may ship after the companion delivery slices that own
the semantic refs it exposes. It is never a prerequisite for current Metric
analysis or any Event/Lifecycle execution slice.

This specification revision does not implement runtime code, skills, or site
documentation.
