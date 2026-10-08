# Python Analysis Design

Status: current architecture, 2026-10-08. This document owns the implemented
Analysis DSL's public model and the path from algebraic definition to execution.
Method equations and numerical contracts belong to
[Operators and Frames](operators-and-frames.md); persistence and recovery belong
to [Session State and Runtime](session-state-and-runtime.md).

## Layer ownership

The public Python surfaces are `marivo.datasource`, `marivo.semantic` and
`marivo.analysis`. Import them as `md`, `ms` and `mv`, and import `marivo` for
the shared Help coordinator.

| Owner | Responsibility |
| --- | --- |
| Datasource | Connections, physical sources/schema, Ibis compilation, batch transport, cancellation and source-health evidence |
| Semantic | Entity identity/versioning, field and relationship meaning, Metric equations, source-time roles, units, intrinsic aggregation and business order |
| Analysis DSL | Explicit membership, observation scope, coordinates, comparisons, predicates, references and requested result views |
| Analysis algebra | Bound domains/quantities, Cell policies, sufficient parts, premises, transported facts and pending obligations |
| Method registry | One semantic method owner and exact qualified physical implementations |
| Compiler | Actual graph dependencies, source/fixed classification, implementation selection, stage/check schedule and typed lowering |
| Runtime | Invocation checks, execution, retained state, Evidence/Findings and atomic publication |
| Agent | Business question, interpretation, choice of next question and conclusions |

Analysis consumes reusable declared meaning. It cannot infer a Metric equation,
business order or historical version rule from column names, physical row order,
sample values or natural-language labels.

## Construct, execute, inspect

A Session owns one investigation and project-local Run/Artifact history.
`mv.session.get_or_create(...)`, `current()` and `resume(...)` retain their
native identity and timezone contracts. The member-domain entry is
`session.members(entity_ref)`; Session-level `population` and `observe` and
their former Dataset families are removed without forwarding aliases.

```python
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("revenue-review", report_timezone="UTC")
members = session.members(ms.ref.entity("sales.orders"))
revenue = ms.ref.metric("sales.revenue")
current = members.observe(
    revenue, during=mv.time_scope(start="2026-07-01", end="2026-08-01")
)
baseline = members.observe(
    revenue, during=mv.time_scope(start="2026-06-01", end="2026-07-01")
)
change = current.compare(baseline).execute()
change.show()
```

The example requires a loaded, authored `sales.orders` Entity and
`sales.revenue` Metric with admitted source/time contracts. It describes an
absolute change; business or causal interpretation remains the caller's work.

Constructors and transformations return concrete Logical values silently.
Logical values expose bounded identity and `contract()`; `execute()` admits
work and returns the matching Materialized type. Materialized values expose
bounded `show()`, isolated `to_pandas()` copies and committed evidence reads.
Methods on either state return Logical continuations. No implicit iteration,
truth conversion, indexing or arithmetic substitutes for a registered method.

Construction and planning do not query business rows or allocate a Run.
Schema-only preflight may connect before Run admission to resolve unknown
Entity key or value types. This is separate from business execution, and the
schema is checked again at execution. Repr and contract inspection do not
perform source or retained-row reads.

## Domains, quantities and Cells

An analysis Signature binds a domain, optional quantity, retained parts,
evidence and obligations to exact Session/owner/input/scope identities.

A domain has complete typed instance keys. Entity identity K is all ordered
primary-key components; snapshot and validity coordinates identify historical
representations and do not replace K. Group, singleton, time, Journey, interval
and Anchor domains retain their own complete keys and realization bindings.

A quantity owns its unit, computation/contribution identity and state policy.
Observed Metric, derived quantity, current-row statistic and rolled quantity
are distinct kinds. Equal displayed values or units do not make quantities
interchangeable. Labels are presentation, not identity.

A Cell is Defined, Null, Undefined or Unknown, with an owned reason where
required. A missing coordinate is a separate correspondence fact, not a fifth
Cell or a physical NULL. Each method declares which states it can consume.
Empty original state, absent state and incomplete coverage are distinct.

Full Subject maps, coordinate tuples, original components and coverage are
retained parts. A scalar cannot recreate any of them. `members()` takes the
set image of an actual Subject mapping; it does not turn instance multiplicity
into Subject counts or infer identities from displayed numbers.

## Algebra and method registration

`analysis/core/model.py` owns signatures, domains, quantities, parts and
evidence. `core/rules.py` and specialized domain rule modules own derivation.
`analysis/methods` is the single registration and physical admission owner.

The six reusable meta-rule families are:

| Rule family | Meaning |
| --- | --- |
| BindProject | Bind governed source/field meaning to an explicit subject domain |
| MapCorrespond | Construct an exact, union, one-to-one or temporal correspondence |
| CellDerive | Derive values from bound endpoint Cells under an explicit policy |
| RowState | Construct a new statistic from current represented rows |
| OriginalReduce | Merge retained original components and finish the governed quantity |
| PartsTransport | Project/select views while preserving or restricting their actual state |

Concrete methods remain distinct versioned identities. Shared rules coexist
with dedicated History, Journey, funnel, Anchor, retention, deviation, runs,
association, forecast, display and allocation rules. Domain algorithms are not
automatically reducible to the six generic rules.

`MethodNode` construction invokes `MethodRegistry.derive()`, which resolves
the sole method semantic owner. `core.rules.derive()` delegates to that same
registry. The resulting `RuleDerivation` carries:

- output Signature and bound preconditions;
- RequiredParts and their PartTransform;
- post facts and transported evidence;
- evaluation identity and pending consume/publish obligations.

Derivation is conditional: a post fact, check ID or successful construction
does not prove a physical implementation exists or that a runtime check passed.
The planner and execution consumers use these contracts; they are not a
documentation-only algebra layer.

Physical selection uses the exact method/version, ordered input value types
(including Decimal precision/scale and Duration unit), ordered domain kinds,
source or fixed shape, time authority and route. It also requires declared
checks, output/retained parts and precision contracts. Selection does not
execute the method or discharge its pending obligations.

Implementation IDs and evidence are provenance, not capability predicates.
Typed consumer rules own numerical specialization, input-arity expansion,
check placement and consumer-specific shape restrictions. No method-ID prefix,
legacy resolver or successful old route grants new admission.

## Definition graph and compiler

`core/graph.py` owns the definition DAG:

| Node | Captured authority |
| --- | --- |
| SourceLeaf | Exact Semantic/source definition, dependency fingerprint, declared Signature and physical value type |
| FixedLeaf | Exact same-Session Artifact, retained Signature, value type and fixed time shape |
| MethodNode | Versioned rule parameters, ordered role-bearing inputs, derivation and explicit additional source/retained references |

Historical Artifact lineage is a reference, not a live data-dependency edge.
An explicit FixedLeaf stops source classification. Additional field, predicate,
comparison, grouping and domain-method dependencies are real graph edges;
classification cannot ignore them merely because the receiver is fixed.

Definition fingerprints identify normalized meaning and explicit bindings.
Node identities control sharing in one invocation: one shared node has one
realization; separately constructed lookalikes are not common-subexpression
merged. One logical realization is not a promise of one physical scan.

`compiler/graph_plan.py` captures dependency order, classifies the root and
selects qualified stages. `compiler/graph_lowering.py` lowers the admitted
schedule to typed Ibis relations, local stages and deadline-bound checks.
Compiler handoff reuses captured static graph/registry facts. Private in-process
compiler objects are trusted; this path does not maintain deep anti-mutation
snapshots or audit local objects as hostile data.

Logical lowered layouts retain complete typed keys, Cell slots and declared
part schemas. Before a source read is issued, `compiler/cell_lowering.py` lowers
those slots to their closed physical carriers. Known Cells carry only their value
and frozen state; Validity Cells carry only their value and one frozen missing
state; Encoded Cells carry their value and a non-null int16 state column.
Carrier selection follows declared policies and construction, never sampled
rows. `core/cell_encoding.py` owns the canonical per-slot reason dictionaries;
`materialization/cell_arrow.py` owns their Arrow operations and public decoding.
Fixed execution and retained storage use the same bindings. Scalar algorithms
may decode individual Cells; public rendering/export restores the existing
string tag/reason columns and their ordering. Each emitted expression has exact
SourceLeaf provenance;
provenance is not reconstructed by comparing Ibis expressions. The compiler
does not submit SQL, consume source rows, allocate a Run or publish an Artifact.

## Execution routes and qualification

| Classified graph / route | Execution contract |
| --- | --- |
| Source-only / ibis | Governed Ibis expressions compile and execute on the datasource |
| Source-only / ibis_python | Governed Ibis preparation captures the required input, then a registered local algorithm consumes it |
| Fixed-only / artifact_python | Controlled retained Arrow/Parquet data enters local pandas/NumPy/SciPy consumers |
| Mixed live source and fixed Artifact | Reject before Run admission or either business input is read |

A fixed member domain followed by a new live Metric/property/Event dependency
is mixed. A Logical continuation over only FixedLeaves remains fixed-only.
Materialized method calls do not replay origin or reconnect its source.

Backend, table form, exact type, domain, time shape, method and cancellation
authority all participate in admission. DuckDB tables, local Parquet, local
CSV/JSON adapters, SQLite, PostgreSQL, MySQL, Trino and ClickHouse do not inherit
one another's method qualifications. A backend name or successfully compiled
expression is not blanket support.

Native relational operations stay in governed Ibis expressions. Matching,
History replay, exact retained-state reduction and statistical kernels use
their registered consumers. Source preparation may materialize full vectors;
batch transport is not proof of a streaming statistical algorithm. Route choice
is complete before execution; a failure never triggers another implementation.

## Premises, assumptions and checks

Evidence has four distinct sources: semantic declarations, constructor-derived
facts, exact call assumptions and actual completed checks.

Entity identity/version grain and declared source parsing are trusted premises.
Selection preserves key uniqueness; grouping constructs unique output keys.
Field ownership and to-one cardinality establish single-valuedness, but
cardinality alone does not prove every target has a match. Equal Entity refs,
DomainSignatures or row counts do not prove equal realized input key sets.

`ExactKeys(verification="check")` checks unknown pairing equality.
`verification="assume"` records an exact call assumption. Field reads similarly
use `match_verification="check"` or `"assume"` for unknown owner/path/version
matching. Assumptions omit only their corresponding checks; they do not create
an intersection, missing-value policy or completed source proof. Materialized
contracts disclose retained assumptions as not checked.

Unknown field/captured-path pairing and actual method consumption requirements
remain typed obligations. Checks retain the originating ordered input nodes,
scope and consume/publish deadline. A check may bind a subset of a method's
direct inputs; lowering resolves each exact domain, quantity and node identity
in the recorded order, including repeated operands. Local cohort consumption
checks the full opportunity domain and records its own completed coverage
proof only after successful consumption. An originating completed check is
required before an inherited consumer runs. Completing a later local stage
does not establish an earlier input check.

Business completeness, version availability, target-grid authority and retained
coverage have separate owners. `source_health` is an explicit data-audit
operation, not an implicit prerequisite of ordinary analysis. Independent source
queries have no transaction-snapshot guarantee; a check proves its own read.

## Observation and composition

`observe` accepts a Metric Ref or the closed RuntimeMetricExpr union. Runtime
factories live under `mv.runtime_metric` and share Semantic's component-graph
owner. SQL, callbacks, arbitrary formulas, coefficients and unit overrides are
not alternative expression bodies.

Observation supplies either a range or an exact endpoint when required by the
Metric's temporal role. Membership selection, observation scope and output time
coordinates remain separate. Routes bind each distinct computation root;
occurrences sharing a root keep independent filters/state. Identity or a unique
definition-bound route permits omitted `via`, including grouped observation.
A foreign root requires its explicit directed route.

Each component occurrence reduces independently on its complete target key
before combination. Component domains combine as complete tuples, never as a
Cartesian product of projected columns, root intersection or row-order alignment.
Only proven empty contributions receive their method's empty state.

First member observation directly computes the complete Metric at the requested
`by` grain. The default `by=()` produces Singleton, or one overall value per
retained time bucket after `each(grid)`. Membership decides which contributions
participate; `by` selects only spatial keys. It accepts an ordered tuple of the
receiver's member Entity (all primary-key components), categorical member or
contribution-path Dimensions, and same-Session logical classifications, including
explicit version reads. Duplicate or unbound axes and mismatched classifications
reject. `groups` binds an exact same-Session logical target domain and retains
empty groups. Public `coordinates` and grouped-domain observation are removed.
Only retained full Subject identity permits a subsequent `members()`.
Multiple member classifications align on the complete member/time key before
their group axes are attached. Explicit target completion preserves comparison
continuations and initializes empty temporal-fold samples with the retained fold
kind in both source and fixed execution.

Time grain is expressed by `each(time_grid(..., grain=grain("day")))` with
`during=grid.window`. Every bucket remains, including empty buckets; `by` never
removes time. Existing result grouping and original `rollup` express time
coarsening. Relative Anchor observation keeps its per-Anchor window contract.

`group_by` on existing results binds classification; `rollup` merges sufficient original state;
`summarize` creates a new current-row statistic. Means retain sum/count, ratios
retain all original components, and linear expressions retain signed ordered
occurrences. Averaging finished means/ratios is not original rollup. Direct
distinct/quantile values retain no set/sketch or distribution rollup authority.

Comparisons require compatible quantities, temporal design and complete typed
correspondence. Predicates carry every referenced input, evaluate all children
and use method-specific Cell policies. References remain frozen. Ranking and
tables are display operations; attribution is an algebraic allocation with
complete endpoint/partition proof and independent reconciliation.

Domain methods preserve canonical matching/replay state. Journey reducers do
not rematch; History views do not replay; Anchor windows retain every original
contribution use; retention distinguishes positive, negative and unknown
follow-up. Statistical selection preserves original fit/classification/search/
training scope. Their equations and conditional continuations are owned by
[Operators and Frames](operators-and-frames.md).

## Local execution optimization

Source observation lowering projects complete Entity instance keys without
`DISTINCT` when the exact member Signature carries their uniqueness evidence.
Projection to Group keys still constructs a distinct target set. A direct
observation of an unchanged, unversioned complete member domain can aggregate
contributions under their own identity keys without joining them back to the
same SourceLeaf. This rewrite requires the exact shared leaf, complete identity
keys and declared uniqueness; selected members, foreign routes and time products
retain their contribution mapping. The complete target domain, empty-contribution
Cells, retained parts, source provenance and pending checks remain unchanged.
Observation windows and Metric slice predicates restrict contributions only;
these rewrites do not restrict the member domain or guarantee one source scan.

Local laws L1/L7/L8/L9 state conditional equivalences. Registration does not
grant arbitrary semantic rewrites, source pushdown or additional public K.
The explicit fixed sum StateEquation helpers keep their own narrow premises.

The executor applies two invocation-local schedule optimizations:

- A2 groups adjacent qualified fixed int64 ordinary selections using L1's
  existing contract, reusing primary/part key indexes and survivor positions.
- A3 groups adjacent qualified fixed original reductions using L8 contracts,
  merging original components directly under terminal keys before one finish.

Both preserve the logical DAG, definition identity, selected implementations,
plan identity and terminal parts. Shared intermediates, explicit boundaries,
pending checks and unsupported mappings/parts end groups. Once computation
starts, failures propagate without retry. Detailed grouping contracts are in
[Runtime](session-state-and-runtime.md#fixed-execution-groups).

## Disclosure and acceptance

`marivo.help("analysis")` owns static API discovery and exact callable contracts.
Results own state-dependent `contract()` actions and bounded `show()` data.
Structured errors own concrete repair, and packaged skills own workflow judgment.

A continuation is admitted from the actual quantity, domain, state and bindings;
a result family name or saved list of actions cannot authorize it. Public result
types have bounded single-line reprs, immutable fields and deterministic reads.
`RawSqlResult` and MaterializedTable are terminal boundaries without typed
analysis continuation. Analysis Evidence and Findings describe deterministic
retained facts, not causal or business judgments.

Current specs, public signatures and qualification evidence are separate.
Focused tests and native backend witnesses do not establish complete backend,
installed-wheel, real-Agent or release acceptance. Historical phase evidence is
available through [archived records](../../history/analysis/README.md) and Git
history; it does not expand the current route.
