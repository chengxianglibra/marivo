# Analysis Architecture

Status: current architecture, 2026-10-08. This directory specifies the Python
Analysis DSL implemented on the typed analysis graph. It describes contracts
and ownership, not a release or backend qualification certificate.

## Reading order

| Document | Owns |
| --- | --- |
| [Python Analysis Design](python-analysis-design.md) | Public entry shapes, algebra-to-graph architecture, method registration, planning, execution routes and composition boundaries |
| [Operators and Frames](operators-and-frames.md) | Cell semantics, sufficient state, numerical policies, method equations, part transport and conditional continuations |
| [Session State and Runtime](session-state-and-runtime.md) | Run identity, source/fixed execution, deadlines, atomic publication, Store 9 and recovery |
| [Timezones and Calendars](timezone-and-calendar-design.md) | Report/read/calendar authority, complete grids, duration precision and temporal continuation |
| [Analysis Evidence Access](evidence-access-surface.md) | Committed Evidence and Findings, bounded reads and interpretation limits |

Read [Semantic and Datasource Overview](../semantic/overview.md) for reusable
business meaning and physical access. The cross-layer temporal contract is
[Temporal Semantics](../temporal-semantics.md). Help and result disclosure follow
[Agent-Facing Public Surface](../agent-friendly-public-surface.md).

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
which state, evidence and input bindings a successor needs. It does not replace
specialized matching, replay or statistical algorithms, and its local laws do
not authorize arbitrary graph rewriting.

## Current and historical material

These documents describe one current path. The former Population/Metric Dataset
APIs, HTTP intent architecture, scenario executors and old Store generations are
not alternative entry points.

The [historical records](../../history/analysis/README.md) retain the bounded
S0/S1 implementation evidence and the former auditability proposal. Their paths,
commands and acceptance claims refer to their recorded revisions. They do not
define the current DSL, continuations or execution qualification.

Exact call signatures, examples, errors and admitted result actions are exposed
by `marivo.help("analysis")` and each receiver's `contract()`. A semantic method,
physical registration, focused test, real backend witness and full release
acceptance remain distinct evidence.
