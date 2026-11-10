# Agent-Facing Public Surface

Marivo is consumed through a write-run-read loop. The library owns deterministic
meaning, typed computation, bounded disclosure and concrete repair. The agent
owns the business question, evidence interpretation and conclusions.

## One public Help coordinator

Python marivo.help(target) and CLI marivo help [target ...] use the same native
registry. Root Help introduces concepts and routes only to authoring or analysis.
Targets are exact lowercase snake_case semantic paths, independent of Python
symbols. Named pages disclose Target and Kind: topic, object, callable,
attribute, expression or error.

The CLI accepts zero or more complete canonical string targets. With no targets
it renders root Help. Otherwise it prints each existing page to stdout in input
order, including repeated targets, preserving its Target and Kind headers and
per-page budget. The batch output is the concatenation of the individual CLI
pages. Each argument is resolved independently; separate arguments are never
joined into a target. Python Help continues to accept one target per call.

Every CLI target is attempted. Target errors and rendering failures print their
existing diagnostics and repairs to stderr without suppressing other pages.
The command exits 0 if all targets succeed, 2 if any target is rejected, or 1 if
any page fails to render; rendering failure takes precedence over target errors.
One CLI telemetry operation encloses the individual Help operations and retains
a failed outcome when any target fails. Help does not execute examples, create
Sessions or read sources; enabled telemetry may record Help failures locally.

Python Help also accepts public callables and public types. Type pages
own acquisition and member discovery; exporting a type permits annotation,
inspection and Help, without authorizing direct construction or extension.
Object pages explain meaning and link exact operations: semantic.metric is the
object, semantic.metric.define describes ms.metric. Returned semantic types use explicit `semantic.metric_object`,
`semantic.entity_object` and corresponding `*_object` targets, avoiding constructor
collisions. Shared expression routes are explicitly owned by semantic role, rather than inferred from Ibis reflection.

Public fields/properties have independent attribute targets, queried by string
without evaluating a getter, such as analysis.analysis_frame.output_names.
Read actual values in their original object context. Variant-only members state
the availability condition, for example the clocked contribution's time field.
Private error subclasses reuse their nearest registered public error contract.

Reflection supplies installed signatures/members. Owning docstrings and domain
rules supply constraints, effects and examples. Native registries assemble these
facts and task navigation; renderers maintain no shadow capability inventory.
Unknown-target repairs use real registered candidates and preserve the requested
receiver. No executable compatibility alias supplies a second canonical entry.

## Task navigation and exact reference

Task topics answer the current information gap; they are not a required call
sequence. The analysis root starts with one minimal executable example from
Session and Catalog discovery through an explicit time range and output Grain
to compute/show. It assumes an authored project, requires selecting a Metric by
business meaning and uses an illustrative exact identity. It then offers inputs,
operators, sessions, execution and results, with a direct get_or_create startup
link. Inputs selects Catalog
objects, question-scoped expressions, member domains, time, exact Frame handles
or comparison inputs. Operators gives a compact intent/input/output map and
links directly to exact operations. Sessions owns context and recovery;
execution fixes or saves values; results inspects data and audit. Known APIs
and objects can enter their exact reference directly.

Each exact callable parameter discloses its role, accepted input and omit
meaning, with acquisition links next to that parameter. Links go directly to
known producers or to a bounded choice page when alternatives matter. Native
parameter targets own these edges; the renderer does not infer constructors
from Python annotations. Type Help retains complete producer/consumer discovery.

Exact callable pages use one short example with declared inputs. Examples may
compose operations to show exact-input handles, lazy derivation from fixed data,
statistic consumption or export access. They remain static text: rendering Help
does not execute examples, create Sessions or read project/source data. Task
pages link to the owning callable instead of maintaining another example set,
apart from the single analysis-root quick start. The root's example counts toward
its explicit render budget; other task pages retain their navigation-only budgets.
The project HTTP service also exposes bounded service reference pages under
`analysis.results.service`. The HTTP reference owns connection, fixed-result
reads, current Catalog browsing and explicit Catalog reload facts; OpenAPI owns
the machine-readable wire schema.

Authoring offers direct project loading, Catalog reuse, connection setup,
physical inspection, modeling, expressions and validation, with optional ontology
context. Native datasource and semantic roots remain independent entry points.
The authoring root and native roots reveal interpreter and package identity.
Model navigation embeds registered object contracts and links single-mode
objects directly to construction. Field objects choose column or expression
construction; the retired identity/fields/relationships topic layer has no aliases.
Exact authoring parameters own input meaning, omission and acquisition in their
native registry. Literal and business-authority choices need rules rather than
invented producers. Source descriptors and declared symbols remain distinct
from raw names and closed Catalog values.
`semantic.load` owns workspace selection, valid empty projects and model layout.
Physical declarations require their documented loader placement. Naming a local
Measure/Metric does not publish it; model collection during load publishes
eligible top-level declarations from the model's own files. Exact pages explain
both uses and identify example inputs. Authoring effects describe business
operations; enabled usage telemetry may separately write `.marivo/telemetry`.
An existing closed expression can be composed for one question through
semantic.expressions without publishing a new Metric. Missing reusable meaning
or aggregation authority requires authoring; a missing published name alone
does not.

Task names follow their main navigation parent. Exact API and role targets keep
their owning identities: semantic.semantic_catalog.search and analysis.runtime.values.show
remain exact references even though their intermediate prefixes are not task
pages. Native registries own both the task links and exact reference; retiring a
topic removes that entry without redirecting it or renaming its precise leaves.

Source and Frame describe the question, rather than whether evaluation performs
I/O. A literal Population count forms a Metric without a source dependency.
Frame operations on a pending source expression can execute its captured
dependencies when evaluated. Operations on complete or reopened data do not
implicitly recover the original source declarations.

## Disclosure ownership

| Owner | Facts |
| --- | --- |
| Live Help | Static API, expression grammar, exact examples and navigation |
| Logical object show / typed fields | Captured definitions, inputs, scopes and ownership |
| Completed Frame show | Bounded current data and interpretation |
| Completed / persisted Frame | Values, captured source reads and method facts; persisted commit identity |
| Structured error | Expected/received facts and concrete current repair |
| Packaged skill | Workflow boundaries, judgment and handoff |

Read focused Help when syntax is unknown, and object facts when needed for the
next decision. Related routes are navigation, rather than a mandatory checklist.
Errors do not invent names, choose a different business meaning, silently widen
a scope or retry on another execution target. Public result repr is bounded,
single-line and points to show rather than dumping dataclass fields.

Analysis validation call sites explicitly supply the canonical Help identity of
the public operation they own. Shared validators receive the actual consumer,
so a rejected statistic consumed by over links to over, not reduce. Diagnostic
location text is not a routing key. Internal failures without an explicit owner
retain surface guidance; more specific underlying errors keep their own repairs.

## Computation and inspection

Population and Frame source construction are silent and source-I/O free.
Pending Frame.show discloses captured structure, without schema preparation or
parent-row reads. compute returns complete immutable memory data; persist
publishes an existing completed result, computing it first for a pending input.
Only the returned completed object fixes values and keys.

```python
import marivo.analysis as mv

session = mv.session.get_or_create("revenue-review", timezone="UTC")
revenue_entry = session.catalog.get("metric:sales.revenue")
assert revenue_entry.kind == "metric"
revenue = revenue_entry
period = session.time_scope(start="2026-06-01", end="2026-06-08")
saved = session.observe({"revenue": revenue}, where=period).persist()
saved.show()
```

This fragment requires a project with the declared Metric and qualified source
route. Completed Frames own row data, export and data operations; persisted
Frames also expose commit identity and captured source-read metadata.
`Session.persisted_frames()` discovers saved Frames, and
`Session.persisted_frame(id)` reopens an exact commit. Source-free cold recovery
and fixed-data operations do not choose current models or datasources. The full
lifecycle and read integrity contract belongs to
[Runtime](analysis/session-state-and-runtime.md).

## Data display contracts

| Receiver | Display controls | Meaning |
| --- | --- | --- |
| AnalysisFrame | show() | Pending metadata or all fixed rows using pandas formatting |
| PersistedFrameSummary / PersistedFrameSummaryPage | show() | Commit metadata, description, outputs and axes |
| PreviewResult / RawSqlResult | show(*, n=None, max_output_bytes=8192), corresponding render | Bounded source result display |
| Entry/report/page | Owning type's show/render protocol | Definitions, check outcomes or bounded items |

Frame show does not evaluate pending expressions. On a completed Frame it displays
all fixed rows with pandas formatting, reading complete saved data and required
coordinates when stored. `frame.to_pandas()` returns an isolated table and also
evaluates pending inputs. No common adapter erases these read boundaries.

Preview/raw SQL controls use nonnegative n or None, and positive max_output_bytes
or None; bool is rejected. The UTF-8 budget includes show's printed newline.
Whole rows fit after required metadata; cells and columns are not silently
shortened. Omitted rows have exact counts/reasons and applicable recovery calls.
If mandatory metadata cannot fit, ValueError gives the minimum budget. Line
breaks/separators are escaped losslessly. Their display controls never change
source scope, stored rows or query limits. Complete SQL results already reside
in memory; unlimited display fetches no more source rows.

Ordinary nullable values retain precision within their admitted carrier. Value
NA, legitimate zero and missing alignment endpoints remain different: presence
is a separate nonnullable boolean. Entity identities remain redacted in cards;
complete typed export retains physical keys. Duration display retains exact ticks
and unit. There is no four-state Cell/reason-column protocol.

## Pages, errors and workflow

Bounded pages expose their owning immutable items and opaque cursor protocol.
Cursor scope, snapshot and filters cannot be silently changed. Unknown authority,
empty results, unavailable records and corrupt Stores remain distinct outcomes.
A display omission never authorizes computational truncation, sampling or
approximation. Source/native timeout limits retain their actual proof boundary.

Authoring reads current definitions and physical evidence, writes a coherent
semantic slice, loads and checks its requested roots. Business meaning comes
from current authority; credentials stay outside project persistent state.
[Authoring workflow](semantic/authoring-workflow.md) owns those choices.

Analysis uses exact Session and input identities. Source observations, selected
keys, output coordinates and attributes remain explicit. [Examples](analysis/examples.md)
show fixed-data continuation and renewed source questions. Correlation is not
intervention evidence; algebraic contribution is not cause. Method explanations
retain their original population, and typed audit never stores an agent's
narrative as established truth. Terminal pandas/raw SQL work must state its own
sample, denominator and interpretation limits.

Result publication and project HTTP facts belong to native Help and the current
service guide. Plugins own project connections, credentials, user authorization,
report publication and refresh execution. The packaged authoring and analysis
skills retain their existing domain judgment; there is no separate service skill.

## Change and verification ownership

Exports, callable/types, Help, result/error guidance and dynamic continuations
form one disclosure contract. A change aligns native registries, independent
export/reachability/budget tests, examples, CLI and latest English/Chinese docs.
Packaged skills retain workflow ownership and their explicit edit-authorization
rule. Character/page budgets are proxies rather than measured model efficiency;
journey correctness and failed lookups/repairs also matter.

[marivo/_help](../../marivo/_help/) coordinates public Help;
[introspection/live](../../marivo/introspection/live/) owns shared reflection,
resolution and rendering. Domain-native registries live in the datasource,
semantic and analysis _capabilities packages. Public export snapshots and
behavior tests are independent of rendered output.
