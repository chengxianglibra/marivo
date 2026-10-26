# Agent-Facing Public Surface

Marivo is consumed through a write-run-read loop. The library owns deterministic
meaning, typed computation, bounded disclosure and concrete repair. The agent
owns the business question, evidence interpretation and conclusions.

## One public Help coordinator

Python marivo.help(target) and CLI marivo help <target> use the same native
registry. Root Help introduces concepts and routes only to authoring or analysis.
Targets are exact lowercase snake_case semantic paths, independent of Python
symbols. Named pages disclose Target and Kind: topic, object, callable,
attribute, expression or error.

Qualified strings, public callables and public types are accepted. Type pages
own acquisition and member discovery; exporting a type permits annotation,
inspection and Help, without authorizing direct construction or extension.
Object pages explain meaning and link exact operations: semantic.metric is the
object, semantic.metric.define describes ms.metric. Deferred expression routes
are explicitly owned by semantic role, rather than inferred from Ibis reflection.

Public fields/properties have independent attribute targets, queried by string
without evaluating a getter, such as analysis.analysis_frame.output_names.
Read actual values in their original object context. Variant-only members state
the availability condition, for example a semantic Grain's calendar field.
Private error subclasses reuse their nearest registered public error contract.

Reflection supplies installed signatures/members. Owning docstrings and domain
rules supply constraints, effects and examples. Native registries assemble these
facts and task navigation; renderers maintain no shadow capability inventory.
Unknown-target repairs use real registered candidates and preserve the requested
receiver. No executable compatibility alias supplies a second canonical entry.

## Task navigation and exact reference

Task topics answer the current information gap; they are not a required call
sequence. The analysis root offers inputs, source, frame, execution, results and
sessions. Inputs selects governed meaning, member domains and time; source
forms an explicit observation or asks for new source facts; frame composes data
and calculations over represented rows. Execution fixes or saves values;
results inspects data and audit; sessions establishes or recovers exact context.
Known APIs and objects can enter their exact reference directly.

Exact callable pages use one short example with declared inputs. Examples may
compose operations to show exact-input handles, lazy derivation from fixed data,
statistic consumption or export access. They remain static text: rendering Help
does not execute examples, create Sessions or read project/source data. Task
pages link to the owning callable instead of maintaining another example set.

Authoring routes datasource work through connections, sources, inspection and
the semantic handoff. Semantic work selects model definitions, expression roles,
time meaning, catalog lookup or validation according to the missing decision.
An existing closed expression can be composed for one question through
semantic.expressions without publishing a new Metric. Missing reusable meaning
or aggregation authority requires authoring; a missing published name alone
does not.

Task names follow their main navigation parent. Exact API and role targets keep
their owning identities: analysis.catalog.metrics and analysis.runtime.values.show
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
| Artifact / Finding / Evidence | Exact committed submission and audit facts |
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
revenue = session.catalog.metrics.get("sales.revenue")
period = session.time_scope(start="2026-06-01", end="2026-06-08")
saved = session.observe({"revenue": revenue}, by=(period,)).persist()
saved.show(n=10)
saved.artifact.show()
```

This fragment requires a project with the declared Metric and qualified source
route. Frame owns rows, export and data operations. Artifact owns submission,
Findings and Evidence, without row export or DSL continuation. Source-free cold
recovery and fixed-data operations do not choose current models or datasources.
The full lifecycle and read integrity contract belongs to
[Runtime](analysis/session-state-and-runtime.md).

## Data display contracts

| Receiver | Display controls | Meaning |
| --- | --- | --- |
| AnalysisFrame | show(*, n=20) | Metadata or actual result-order prefix, according to carrier |
| Artifact | show() | Submission and audit only |
| PreviewResult / RawSqlResult | show(*, n=None, max_output_bytes=8192), corresponding render | Bounded source result display |
| Entry/report/page | Owning type's show/render protocol | Definitions, check outcomes or bounded items |

Frame n is a nonnegative non-boolean integer. n=0 reads no row/coordinate payload;
positive preview decodes the prefix and required frozen interpretation. Metadata
success does not certify unread data. Preview is not full export followed by head.
Frame cards have bounded text/column display; explicit complete export is
frame.to_pandas() on a completed root. No common adapter erases these read boundaries.

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
