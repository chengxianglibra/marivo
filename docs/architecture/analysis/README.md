# Analysis Architecture

Analysis separates explicit source questions, immutable data operations and
publication. One AnalysisFrame type represents pending expressions, complete
memory data and exact stored references. Artifact is the committed result's
submission/audit view.

## Owning contracts

| Topic | Document |
| --- | --- |
| Population, source observation, membership and endpoint investigation | [Populations and observation](populations-and-observation.md) |
| Frame structure, carriers, names, correspondence and row order | [AnalysisFrame](analysis-frame.md) |
| Nullable values, physical numeric carriers and current-row statistics | [Operators and Frames](operators-and-frames.md) |
| Session ownership, evaluation, Store publication/recovery and reads | [Session and Runtime](session-state-and-runtime.md) |
| Source/evaluation audit, Findings and immutable method explanations | [Evidence access](evidence-access-surface.md) |
| Runnable source/fixed-data workflows | [Examples](examples.md) |
| Conditional mathematical laws and actual optimizer coverage | [Core algebra](algebra-theory.md) |

Semantic definitions start at [the semantic overview](../semantic/overview.md).
[Temporal semantics](../temporal-semantics.md) owns exact time boundaries and
frozen matching. [Public surface](../agent-friendly-public-surface.md) owns Help,
captured state and structured repair disclosure.

## Construction and data flow

```text
Session + resolved definitions / explicit literal domains
    -> Population / source observation / literal Frame
    -> Frame expression with exact Column and FrameAxis bindings
    -> compiler: required keys, values, checks and one stage schedule
    -> source Ibis reads + fixed inputs + pandas local kernels
    -> complete immutable memory data and audit
    -> Store transaction publishing that existing result
    -> stored Frame: source-free supported data operations
    -> frame.artifact: submission / Findings / Evidence
```

mv.* owns source-free descriptions and composition; session.* introduces Catalog,
Store, execution ownership or frozen defaults; receiver methods derive existing
context. Public result types are acquired values, rather than user constructors.
Lifecycle discovery is mv.session.create/resume/recent/inspect. Source captures,
repr and metadata show do not evaluate. A completed Frame show explicitly reads
current data; compute/persist are the evaluation boundaries.

Each source question supplies its actual Metric/field/domain/time dependencies.
Finished data retains complete keys, values, presence and necessary frozen
coordinates without original semantic objects, producer graphs or hidden
aggregate components. New source facts require new explicit inputs. A bridge can
reuse fixed selected ordinary keys without recovering their original definitions.

The compiler shares actual inputs only within an evaluation and releases them
by consumer lifetime. Fixed-data composition does not authorize upload to a
remote source or implicit rereading. Numeric/backend qualification, source
coverage and error checks remain owned by the consuming operation. Local code
alignment and examples do not establish an unrun provider or release matrix.

## Implementation decisions

[Analysis DSL simplification](dsl-simplification-proposal.md) records the inline
membership/coordinate inputs, fixed outer/null policies and external-domain
coverage repair. Current contracts are listed above; implementation and validation
evidence is tracked in [the progress record](dsl-simplification-progress.md).
Attribute refactoring remains outside this scope.
