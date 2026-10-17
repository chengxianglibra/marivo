# Analysis Architecture

Status: current contracts and locally completed Lazy DSL implementation, 2026-10-17.
At `405bd006f8`, native Deferred expressions, S0–S6 and R3–R8 Population/Query
integration have passed the agreed local implementation gate and independent
business acceptance. The new numerical Query source path admits only DuckDB;
other backends require code-route extensions followed by Runtime qualification.
The complete slow suite, remote Runtime and release acceptance remain incomplete.
A design document is not a release or backend qualification certificate.

## Read the owning contract

| Document | Owns |
| --- | --- |
| [Python Analysis Design](python-analysis-design.md) | Population, Query, Column, terminal Artifact and the binding/compiler boundaries |
| [Operators and Frames](operators-and-frames.md) | Nullable numerical policies, complete keys, contribution occurrences and operator meaning |
| [Session State and Runtime](session-state-and-runtime.md) | Fresh Run identity, stage scheduling, resource ownership, atomic Store 10 publication and reads |
| [Timezones and Calendars](timezone-and-calendar-design.md) | Exact boundaries, source/calendar/report authority, windows, periods and time output |
| [Analysis Evidence Access](evidence-access-surface.md) | Self-contained saved Evidence/Findings and their interpretation limits |

Datasource and semantic work starts at [the semantic overview](../semantic/overview.md).
Cross-layer time follows [Temporal Semantics](../temporal-semantics.md).
Static facts, dynamic contracts and errors follow
[Agent-Facing Public Surface](../agent-friendly-public-surface.md).

## Complete design and implementation sources

| Document | Owns |
| --- | --- |
| [Lazy Analysis DSL](lazy-analysis-dsl-redesign.md) | Complete target signatures and evaluation semantics, including session.read(population, fields) |
| [Semantic Expressions](../semantic/expression-model-redesign.md) | Controlled native Deferred inputs, declarations, per-sample policies and supersession |
| [Execution and Handoff](lazy-analysis-dsl-execution.md) | Check timing, pandas stages, terminal saving and output layout |
| [Implementation Architecture](analysis-implementation-architecture.md) | Module ownership and implementation layers |
| [Examples](lazy-analysis-dsl-examples.md) | Multi-round scripts and focused recipes |
| [Scenario Validation](lazy-analysis-dsl-scenario-validation.md) | Independent numerical witnesses and actual acceptance criteria |
| [Algebra Theory](algebra-theory.md) | Mathematical definitions, laws and information requirements |
| [Algebra Methods](algebra-methods.md) | Separate model-method proposals beyond the current minimal DSL |

Complete target documents state desired contracts. Their signatures and numerical
witnesses alone do not prove every public integration or physical route is ready.
Historical acceptance records remain evidence for their own exact scope.

## Execution and output boundary

```text
datasource: qualified Ibis execution returning pandas
    -> semantic: immutable native Deferred definitions and explicit domain dependencies
    -> analysis: Population / Query / Column and exact observation bindings
    -> compiler: physical methods, full keys, demanded slots, schedule and checks
    -> Runtime: pandas stage outputs, resource cleanup and atomic publication
    -> Store 10: committed values, coordinates, presence and actual output records
    -> Artifact: bounded reads, complete export and saved facts
    -> pandas: ordinary post-execution analysis
```

`session.observe` binds Metrics, `session.query` binds keys,
`session.values` binds literal references, and `session.read(population, fields)`
explicitly binds attribute reads to a Session. Population stays lazy; Query alone
has execute. Artifact has no contract or DSL continuation. Definitions never
recover from a saved Artifact; a new execute reevaluates its sources.

Static/type checks, local datasource acceptance, remote Runtime checks and release
gates remain separate. Use native Help and structured errors for the exact
admitted route; Query/Population contracts disclose valid next operations without
reading unknown business keys or manufacturing capability.
