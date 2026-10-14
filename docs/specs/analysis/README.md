# Analysis Architecture

Status: current architecture, 2026-10-08. This directory specifies the Python
Analysis DSL implemented on the typed analysis graph. It describes contracts
and ownership, not a release or backend qualification certificate.

## Reading order

| Document | Owns |
| --- | --- |
| [Analysis Algebra Theory](algebra-theory.md) | Proposed core algebra: attributed finite sets, quantity sets, navigation, pointwise and axis operators, and their laws; it is not current API or runtime qualification |
| [Algebra: Model Methods](algebra-methods.md) | Proposed signatures and required declarations for forecasting, resampling, attribution and reconciliation |
| [Python Analysis Design](python-analysis-design.md) | Public entry shapes, algebra-to-graph architecture, method registration, planning, execution routes and composition boundaries |
| [Operators and Frames](operators-and-frames.md) | Cell semantics, sufficient state, numerical policies, method equations, part transport and conditional continuations |
| [Session State and Runtime](session-state-and-runtime.md) | Run identity, source/fixed execution, deadlines, atomic publication, Store 9 and recovery |
| [Timezones and Calendars](timezone-and-calendar-design.md) | Report/read/calendar authority, complete grids, duration precision and temporal continuation |
| [Analysis Evidence Access](evidence-access-surface.md) | Committed Evidence and Findings, bounded reads and interpretation limits |

Read [Semantic and Datasource Overview](../semantic/overview.md) for reusable
business meaning and physical access. The cross-layer temporal contract is
[Temporal Semantics](../temporal-semantics.md). Help and result disclosure follow
[Agent-Facing Public Surface](../agent-friendly-public-surface.md).

### Lazy DSL proposal

The following documents describe proposed designs, not the current implementation.
Each document states its own revision.

| Document | Owns |
| --- | --- |
| [Lazy Analysis DSL Definition](lazy-analysis-dsl-redesign.md) | Target objects, signatures, binding rules and evaluation semantics |
| [Semantic Expression Proposal](../semantic/expression-model-redesign.md) | Reusable source definitions, Metric constructors and restricted authoring |
| [Lazy DSL Execution and Result Handoff](lazy-analysis-dsl-execution.md) | Proposed execution, retention, recovery, agent disclosure and pandas layout |
| [Lazy DSL Examples](lazy-analysis-dsl-examples.md) | Complete multi-round analysis and focused examples |
| [Lazy DSL Scenario Validation](lazy-analysis-dsl-scenario-validation.md) | Operator rationale, business scenarios, numerical witnesses and future acceptance criteria |

## Architecture at a glance

```text
datasource: physical schema, Ibis compilation and controlled reads
    -> semantic: typed identities, business meaning and Metric component graphs
    -> analysis DSL: domain/relation/result definitions
    -> analysis algebra: bound signatures, premises, parts and obligations
    -> method registry: exact physical implementation admission
    -> compiler: classified DAG, schedule and typed lowering
    -> Runtime: execution, evidence and atomic publication
    -> Store 9: immutable local Artifacts and source-free continuations
```

Construction returns Logical values. `execute()` returns their paired
Materialized values. A method on a Materialized receiver constructs new Logical
work whose leaves remain fixed; Logical is a computation state, not a source-mode
label. `session.members(...)` is the canonical member-domain entry.

The algebra has an active production role. It defines what a method means and
which facts, evidence and input bindings a successor needs. It does not replace
specialized statistical algorithms, and its local laws do
not authorize arbitrary graph rewriting.

## Contract and qualification boundaries

The current-architecture documents describe the implemented contracts; the proposal
documents above remain unimplemented. Historical designs and revision-specific
acceptance records do not qualify current execution.

Exact call signatures, examples, errors and admitted result actions are exposed
by `marivo.help("analysis")` and each receiver's `contract()`. A semantic method,
physical registration, focused test, real backend witness and full release
acceptance remain distinct evidence.
