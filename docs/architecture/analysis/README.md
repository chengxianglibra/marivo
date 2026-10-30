# Analysis Architecture

Analysis separates explicit source questions, immutable data operations and
publication. One AnalysisFrame type represents pending expressions, complete
memory data and exact stored references. Persisted Frames expose their commit
identity and captured source-read metadata directly.

## Owning contracts

| Topic | Document |
| --- | --- |
| Population, source observation, membership and endpoint investigation | [Populations and observation](populations-and-observation.md) |
| Frame structure, carriers, names, correspondence and row order | [AnalysisFrame](analysis-frame.md) |
| Nullable values, physical numeric carriers and current-row statistics | [Operators and Frames](operators-and-frames.md) |
| Session ownership, evaluation, Store publication/recovery and reads | [Session and Runtime](session-state-and-runtime.md) |
| Actual source reads, evaluation metadata and immutable method explanations | [Result and method facts](result-and-method-facts.md) |
| Runnable source/fixed-data workflows | [Examples](examples.md) |
| Conditional mathematical laws and actual optimizer coverage | [Core algebra](algebra-theory.md) |

Semantic definitions start at [the semantic overview](../semantic/overview.md).
[Temporal semantics](../temporal-semantics.md) owns exact time boundaries and
frozen matching. [Public surface](../agent-friendly-public-surface.md) owns Help,
captured state and structured repair disclosure.

## Task discovery

`marivo.help("analysis")` routes by the information needed next:

| Need | Task target |
| --- | --- |
| Governed inputs, question-scoped composition, member domain or time | `analysis.inputs` |
| Observation, Frame construction, calculations, comparison or statistics | `analysis.operators` |
| Complete memory values or a persisted result | `analysis.execution` |
| Data inspection, export or saved metadata | `analysis.results` |
| New or recovered Session and exact saved Frame | `analysis.sessions` |

The root also links directly to `analysis.session.get_or_create` when context is
missing. Session supplies ownership and frozen defaults; acquire its Catalog
before searching project definitions. The sessions topic owns exact recovery and bounded committed Frame discovery by
optional caller description and captured schema.

Inputs routes to Catalog discovery, shared semantic expressions, Population,
time, existing Frame handles and comparison inputs. `analysis.inputs.frame`
explains exact-input Columns, axes and statistics; `analysis.inputs.comparison`
prepares baselines and endpoint rules. Operators lists analytical intent, input
and output without expanding signatures. Exact operation parameters link to
owning acquisition or construction targets, including cross-surface semantic
roles. Known targets can be read directly. Source captures explicit governed questions
without business reads; a literal member count need not depend on a source.
The observation Help example computes and shows one total over an explicit
TimeScope using an existing Session and closed Metric. It requires no export or
publication; the time boundaries do not establish source completeness.
Frame operations retain the dependencies of pending inputs, so their evaluation
may still read sources. Completed and reopened Frame operations use fixed data.

Choose the counted domain before selecting a statistic:

| Question | Exact contract | Meaning |
| --- | --- | --- |
| How many selected members or coordinate tuples? | `analysis.population.count` | Complete distinct tuples, including selected members without facts; returns a Metric for observe |
| How many source contributions? | `semantic.entity.count` | Contributions under explicit time and conditions; returns a Metric for observe |
| How many represented rows? | `analysis.row_count` | Current Frame rows, consumed by reduce or over |

`semantic.expressions` owns question-scoped Metric arithmetic, conditions,
internal samples and windows over established closed inputs. It is directly
reachable from inputs and Metric parameter acquisition. Use authoring to establish missing reusable
business meaning or aggregation authority. Question-scoped composition does not
require publication. Current row statistics do not reconstruct an original
Metric aggregation.

## Construction and data flow

```text
Session + resolved definitions / explicit literal domains
    -> Population / source observation / literal Frame
    -> Frame expression with exact Column and FrameAxis bindings
    -> compiler: required keys, values, checks and one stage schedule
    -> source Ibis reads + fixed inputs + pandas local kernels
    -> complete immutable memory data and metadata
    -> Store transaction publishing that existing result
    -> stored Frame: source-free supported data operations
    -> Frame identity / source_reads / method_facts
```

mv.* owns source-free descriptions and composition; session.* introduces Catalog,
Store, execution ownership or frozen defaults; receiver methods derive existing
context. Public result types are acquired values, rather than user constructors.
Lifecycle discovery is mv.session.get_or_create/resume/recent/inspect. Source captures,
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
