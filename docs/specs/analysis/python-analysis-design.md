# Python Analysis Design

## R0.3 accepted full-algebra target (inactive)

This section owns the target Analysis contracts for R0.3. It does not describe
currently importable Python. The 2026-09-26 R0 capability ledger owns migration
and implementation status; the Semantic object model owns authored facts, and
the Datasource layer owns physical access. Construction and planning perform no
business-data I/O. Every operation below has a Logical result and a matching
Materialized result after `execute()`; a fixed continuation consumes retained
Artifact data and parts, never silently rereads a source. A failure names the
expected type/identity/fact, the received value, and a focused repair target.

### R3.2 private method registration (connected construction only)

`analysis/methods` is the single registration owner for R3 private core
construction. `core.rules.derive` resolves that registration; it no longer
owns a second method dispatch. The six meta-rule identities are distinct from
concrete `cell.difference`, `cell.ratio`, `row.{sum,mean,count,count_defined,
weighted_mean}` and `state_rollup` method identities, all at version 1.
`bind_project`, `map_correspond` and `parts_transport` retain their versioned
rule names as the connected method identities. These are private contracts,
not new public API or Help targets.

Semantic derivation consumes the existing closed core parameter variants and
exact Signatures. Its RuleDerivation owns bound Pre, quantity and unit, Cell
policy, state, RequiredParts, part transformations, Post and still-pending
obligations. Count uses `count_all`, defined-count uses `defined_only`, and
connected numeric cell/current-row methods use `strict`; an adapter cannot
replace these policies. Original reduction still accepts only `sum@v1` state
version `v1` with `(sum, non_null_count)`, exact contribution binding and
coverage, and a whole-input singleton. Current-row statistics remain a new
quantity and cannot acquire original rollup by retaining their display values.
Statistical weights must belong to the exact input binding and scope.
Conditional private continuation requirements are pruned when required parts
or their bindings are absent; successor derivation must still validate exact
parameters and premises. These requirements are not executable public K.

A physical qualification key includes method/version, ordered exact value
types (including Decimal precision/scale), ordered domain kinds, source form,
backend/table kind, exact time shape and an explicit route. Supported routes
are source Ibis, source Ibis preparation followed by Python, and fixed Artifact
Python. Registration rejects mismatched value-type/domain positions, method
input arity, conflicting keys, missing semantic checks/parts, unknown types,
and types incompatible with the method. Integer counts require checked-int64
precision; other connected numeric methods require exact Decimal, finite
float64, or checked-int64 precision according to their inputs and result.
Selection compares the whole key and additionally requires all bound checks and
retained/output parts.
It returns one declaration and the unchanged pending semantic derivation;
there is no execution, route retry, source inspection, Run allocation or Store
access. Static qualification evidence never discharges an invocation's Pre.
Physical type/time/shape facts and resource limits must be verified and enforced
by the subsequent compiler/execution consumers; this private registry does not
observe them itself.

R3.2 introduced registration without qualified physical implementations. R3.4
now connects the narrowly qualified consumers listed below; all other exact keys
retain an explicit blocked reason and recovery condition for the owning R4-R8
consumer. Synthetic qualification declarations in unit tests prove matching and
rejection only. They do not qualify a backend.
Existing J1 current-row sum/count/mean, original sum rollup/group, and difference
consumers now obtain their contracts through `MethodRegistry.execution`. The
registered MethodSemantics owns Cell policy, units, state components, checks and
empty-result reasons. `methods/execution.py` maps these facts to the existing
consumer's exact part/check encoding and route qualifications; the old overlapping
registrations and resolver are removed from `operators/dsl_j1_contracts.py`.
`methods/j1.py` routes the existing consumers to this owner. Missing canonical
registration rejects without a legacy fallback. Count discloses `count_all`,
matching both the canonical rule and the existing executable behavior.

Execution layouts keep existing receipt method IDs and qualified routes; they do
not grant graph implementations, new backends or new public continuations. Core
construction and graph selection do not consult J1 execution layouts. Unconnected
observation, ratio, selection and Spearman contracts remain with their current
owners until their owning migration; the J1 consumer resolver names those methods
explicitly, never as fallback for a connected method. `md.raw_sql` remains a
datasource terminal and cannot be registered here.

### R3.3 private definition graph and pure planning

`analysis/core/graph.py` owns the private immutable definition DAG consumed by
`analysis/compiler/graph_plan.py`. `SourceLeaf` captures a Semantic Ref,
definition fingerprint, datasource Ref, declared shape, exact value type and
Signature. `FixedLeaf` captures an ArtifactRef, definition fingerprint, retained
Signature and declared fixed shape. These are supplied metadata contracts, not
source inspection or Artifact receipt validation. R4 must verify their actual
physical and persisted bindings before consumption. Historical Artifact lineage
is deliberately absent from the data-dependency graph.
Live `SourceLeaf` signatures admit only declaration and builder evidence. Check,
observation and derived evidence from an earlier realization cannot discharge
this invocation's pending source checks.

`method_node` derives through the single method registry. Its ordered edges
identify subject/quantity inputs and current/baseline comparison endpoints;
`BindProject` additionally requires explicit source leaves covering its field
owner, relationship endpoints and Metric computation roots. Those source edges
participate in classification even when the subject input is fixed. Each required
source occurs once and retains the exact Session, owner and scope. Other
connected methods cannot introduce hidden source edges. Each node has an
explicit identity independent of its definition fingerprint. Equal definitions
constructed separately remain separate; repeated references to the same node
share one stage. Identity collisions, cycles, method/version/role mismatches,
foreign owners and altered derivations fail before planning.
`BindProject` also rejects an output type that contradicts a known bound field
or Metric logical type; unresolved physical details remain R4 obligations.

`classify_inputs` visits only the reachable DAG and returns source, artifact or
mixed inputs. `plan` rejects mixed inputs and multiple live datasources before
implementation selection. The initial private planner also requires one exact
source/time shape; heterogeneous shapes have no implicit coercion or federation.
Every reachable method receives exactly one explicit route choice: `ibis`,
`ibis_python` or `artifact_python`, matched against the full registration key.
There is no automatic route retry. A local output cannot feed another source
stage. A bare source leaf cannot authorize a read without a registered method;
a bare fixed leaf likewise cannot authorize an Artifact read without a
registered local method. A fixed leaf with unfinished publication obligations
is rejected.

The returned GraphPlan contains dependency-ordered source binding, Artifact
read, source method and local method stages. Ibis-to-Python has an explicit
preparation stage feeding its registered local stage. It also retains bound
CheckRequirements and PhysicalRequirements, including declared input and output
types, time/table shape, precision and resources. Source binding stages are
pure descriptors, not independently authorized reads. R3.4/R4 must lower and
validate the admitted prefix before executing it. The R4 consumer must satisfy
`consume` obligations before the owning method uses their facts and `publish`
obligations before publication; inherited obligations keep their original
bindings. Pending Post never becomes established evidence through planning.
Static implementation qualification does not satisfy invocation checks.

These APIs are private and do not expand public Help, exports, or continuation
capabilities. R3.3 itself qualified no production physical implementation;
synthetic registrations test stage selection only. Existing J1/Dataset execution
and the Store-backed Session history graph remain owned by their current
consumers pending R4-R8 migration; the private planner never calls them as a
fallback. The R3.4 handoff below adds lowering. Run allocation, execution,
publication and recovery remain outside this increment.

### R3.4 admitted lowering and local laws

`analysis/compiler/graph_lowering.py` consumes an unchanged GraphPlan and exact
R1 BoundSources associated with its reachable SourceLeaf identities. It
revalidates admission before lowering; missing, duplicate, foreign-owner or
additional bindings fail. CoordinateColumn, CellColumns and PartColumns describe
the complete typed physical layout. The lowerer validates declared column types
and complete component sets and creates a canonical layout without reading data.
A field projection consumes the normalized direct field owner and source column;
it retains the actual subject restriction instead of scanning the owner as the
result. ValuePredicate is a closed, scope-bound int64 comparison with an explicit
`reject` or `drop` policy for non-Defined Cells. Predicates participate in graph
fingerprints. A where stage without a predicate cannot select a production route.

The builtin consumer declarations qualify only int64-valued Entity inputs with
int64 complete identity components and NoTime on DuckDB native tables or Parquet:

- Direct int64 field binding without a relationship path or parsing.
- Projection, view and explicit value filtering, with complete retained parts.
- Exact-key pairing, one-to-one pairing, complete-tuple union and Subject image.
  Every incoming key is checked before union or non-injective Subject deduplication;
  a declared injective Subject map is checked and never repaired with distinct.
- Whole-input `row.count` and `row.count_defined`, retaining their RowStatePart.
  Count includes all four Cell tags; defined-count includes only Defined Cells.
  Empty input yields a Defined zero on the singleton.
- Fixed Artifact `row.count` has a caller-owned local consumer with a 100,000-row
  limit. The handoff includes ArtifactReadStage, exact input/output layouts and
  the registered LocalMethodStage. The local function consumes validated Cells;
  it performs no Artifact read or publication.

Retained physical parts are Subject, original state, row state and coverage.
Their coordinate/state components are int64 and coverage columns are boolean.
Other parts, numeric types, temporal shapes, preparation routes and methods remain
unqualified. Original Metric observation, source sum/mean/rollup, grouped domain
execution and public methods are not implemented by these declarations. R4/R5
must verify that a supplied Metric leaf layout really belongs to its captured
canonical Metric definition; supplying a layout is not observation evidence.

LoweredPlan carries dependency-ordered relations or local stages, the original
physical requirements, and mandatory checks. IntegrityCheck describes invalid
identity, Cell or predicate rows; SemanticCheck retains the exact original
CheckRequirement, including input scope and consume/publish deadline. Check
resolution follows graph edges to the originating realization rather than
matching equal definition fingerprints. Each originating check retains its ordered
input tuple. Shared paths deduplicate that tuple, while independent realizations
with equal symbolic obligations remain separate checks; a repeated input within
one pairing still occupies both ordered positions. A checker returning no rows is the
success condition, not an already-established fact in the lowered graph.
Every input integrity check must succeed before its dependent stage is consumed;
publication also requires all inherited publication checks. R4 owns evaluating
checks, rendering concrete failures, and recording their evidence in one Run.

Each LoweredRelation, IntegrityCheck and SemanticCheck carries explicit ordered
SourceLeaf `source_ids`, recorded while lowering its actual operands. Repeated
references to one leaf are deduplicated; independent leaves remain separate even
when they bind the same physical table. An exact-key correspondence's primary
expression carries only its left operand's sources; its pairing checks carry
both operands. Projection and other unary operations preserve their operand's
recorded sources, and union/field-owner joins combine their actual operands.
`LoweredPlan.sources_for(expression)` resolves this metadata only for the exact
emitted expression object. It never compares Ibis relations to infer provenance;
untracked copies and derived expressions are rejected with a request to use the
emitted expression unchanged or re-lower the graph. R1 still validates physical
relation ancestry independently when compiling the selected bindings.
The handoff declares scan/filter/project/group/count/join/union requirements; R1
admits join/union only for DuckDB in this increment. The consumer qualifies those
bindings and passes the unmodified Ibis expression
and expected schema to SourceSession.compile/batches. The compiler never opens a
connection, compiles/submits SQL, reads an Artifact, allocates a Run, or writes a
Store. R1/R4 must still enforce actual backend/time/shape, receipt, source scope
and resource requirements. Static qualification does not discharge any of them.
Failures do not trigger route reselection. No public execute path is connected.

Local laws are explicitly registered by their semantic method owner:

- L1 returns a fresh graph node for adjacent closed int64 selections with matching
  unknown policy, input scope and parts. Signature, transported evidence,
  obligations, part transforms and conditional private K must agree exactly.
- L7 composes complete fixed coordinate mappings while retaining the role chain
  and input binding. Its result asserts a coordinate function only; repeated
  targets remain non-injective and grant no contribution or rollup permission.
- L8/L9 operate on complete fixed original `sum@v1` state, coverage and disjoint
  contribution identities. An absolute-sum bound excludes intermediate int64
  overflow. L8 preserves full components through intermediate empty groups; L9
  preserves explicit selected empty targets. Their StateEquation comparison is
  **state only**. Neither rewrites the semantic graph, expands K, reads a source,
  changes an observation scope, nor authorizes source pushdown. RowStatistic
  input, missing components, overlapping contributions and scope changes fail.

These private outputs are R4/R5 handoffs, not public terminal result families.
There are no new exports, Help targets, CLI commands or packaged-skill promises.
The connected J1/core semantic-owner overlap is resolved. Whole-stage R3
acceptance is separate from these bounded corrections; public graph migration,
Runtime/Store and broader method/backend qualification retain their phase owners.

### R4.1 frozen graph-to-Runtime handoff (inactive)

R4.1 fixes the target contract for one R4 consumer of `GraphPlan` and
`LoweredPlan`; it does not connect the current public execute path. The root's
normalized definition fingerprint remains stable across top-level executions.
Explicit node identities determine sharing **within** one invocation and are
never used as random cache salt. The graph owner supplies the ordered reachable
dependencies and method derivations; the method registry supplies the selected
`MethodKey`, route, full `QualificationKey` and existing
`Qualified.implementation_id`. R4.2 must add an integer implementation
contract version (initially 1) before constructing a new key; the current R3
`Implementation` has the ID but no version. Store alone allocates the `run_`
and `artifact_` references. Receipt
storage alone certifies file content. No owner infers one identity from another
by matching equal displayed values.

`marivo.analysis.execution_key/v1` has two closed canonical tuple variants.
`H` is the existing typed tuple canonicalization in
`datasets.descriptors._canonical_digest` (SHA-256 of its UTF-8 encoding), not
`repr`, unordered dictionary iteration or a Python class name. Every tuple
below is in the stated order; the ordered plan contains, for each reachable
method in dependency order, its method name/version, route, exact qualification
key, selected implementation ID/version and output signature. It excludes
ephemeral node IDs while retaining graph structure through the definition
fingerprint and ordered bindings.

```text
source key = H(("marivo.analysis.execution_key/v1", "source",
                definition_fingerprint, ordered_plan,
                ordered_source_bindings, run_ref))
fixed key  = H(("marivo.analysis.execution_key/v1", "fixed",
                definition_fingerprint, ordered_plan,
                ordered_fixed_inputs))
```

Each source binding occurrence is `(source definition fingerprint, datasource
Ref, physical source shape, semantic dependency digest, exact selected source
binding fingerprint)`. It is captured before opening the source; the Run ref
is the fresh evaluation identity, so the same Lazy value cannot hit an earlier
source result. Each fixed input occurrence is `(session_ref, artifact_ref,
producing_run_ref, primary_receipt_digest, ordered_parts, input_binding,
method_state_contract_id, method_state_version, snapshot_digest)`, where every
ordered part is `(role, contract_id, contract_version, receipt_digest)`.
Occurrences remain separate and retain operand order, including equal
Artifact references in distinct input slots. A fixed key is constructed only
after exact references, ownership and receipt metadata are verified; a hit
also validates all required files, parts, state and snapshot before returning
the original Artifact without a Run. A miss admits exactly one new Run under
the writer guard. The existing Store uniqueness on `(Session, execution key)`
remains the publication arbiter.

Pure classification and capability checks precede the writer guard, source
open, Artifact row read and Run allocation. Mixed live/fixed roots, foreign
Session inputs, unmatched ordered comparison/member bindings and unavailable
physical implementations reject there. The guard then reconciles the exact
unfinished Run. Source-only allocates a Run and new key before first source
read; fixed-only validates a hit before allocating a Run. R4 consumes the
unchanged emitted Ibis expression with its recorded `source_ids`, fulfills
each bound check before its consume/publish deadline, and records completed
evidence from this invocation. No failed check, type mismatch, cancellation
or implementation error changes the selected route or starts a new identity.
R4.3 owns the common source/Parquet/pandas Arrow exchange; R4.4 owns the v7
Store/descriptor/receipt switch; R4.5 owns exact source-free recovery. Their
frozen metadata and failure boundaries are in
[Session State and Runtime](session-state-and-runtime.md#r41-frozen-runtime-and-store-target-inactive).

### R4.3 private exchange and method execution

The private graph consumer executes an admitted and lowered plan through R1
`SourceSession` and the selected method implementation. Every submitted Ibis
expression uses the exact `LoweredPlan.sources_for()` bindings. Source, verified
local Parquet, and pandas-to-Arrow output enter one schema-first exchange with
complete ordered keys, four-state Cells, exact input binding, independent part
schemas, and pending checks. A check becomes completed only after its selected
stream is exhausted and closed successfully. Shared explicit nodes retain one
execution-local result; an equal independently constructed node remains a
separate realization.
Source bindings declare allowed non-Defined Cell reasons. Transport retains
that exact policy; producers of new Cells use their registered method policy.
An undeclared reason rejects the transient result instead of being inferred
from encountered rows.

The fixed route verifies its selected primary and required part receipts before
the pandas method reads rows. It cannot attach an Artifact to DuckDB or a
remote backend. A private result is transient and grants no Run, Store,
publication, cache-hit or recovery authority. R4.4 owns its durable encoding;
R4.5 owns the public cutover.

The verified private source prefix is DuckDB native table or Parquet, `NoTime`,
complete int64 identity, and exact registered `bind_project`,
`parts_transport`, `map_correspond`, `row.count`, `row.count_defined`,
`row.sum`, and `row.mean` variants. The source Spearman owner additionally
qualifies ordered int64/float64 Endpoint pairs through both numerical Ibis and
Ibis preparation followed by Python. Fixed receipt execution qualifies
`row.count`, `row.count_defined`, int64 `row.sum`/`row.mean`, and ordered int64/float64 Spearman
pairs. Only fixed `row.count` retains the R3.4 100,000-row limit; other
complete-input algorithms have no implicit row cap or sample. Mean requires
exactly representable int64 operands. Unregistered J1–J3 reductions,
comparison/ratio variants, Decimal, temporal shapes and other backends remain
unavailable before business reads; legacy execution results do not qualify
them. Private results do not publish a new protocol Artifact or authorize
product continuation K.

### Relative Anchor observation and retention (C18)

The single public entry shape is
`session.anchors(source: ParticipantRoleHandle | JourneyResult, *,
population: AnalysisDomain, during: TimeScope) -> LogicalAnchorDomain`.
The source is a closed union of an exact `ParticipantRoleHandle` on an Event and
an exact `JourneyResult` start view. The former uses the Event occurrence key;
the latter uses the retained journey start occurrence and exact pattern/matching
identity. `population` is an `AnalysisDomain` of the role's exact Subject
Entity; `during` is a `TimeScope` selecting starts, not a follow-up limit.
`AnchorDomain` retains the complete `(Subject key, Anchor occurrence key,
source definition/version)` instance key, even when one Subject has several
starts. Duplicate or unresolved keys and a mismatched participant are errors.
Journey starts retain their existing assignment and coverage rather than being
rematched. Subjects with no selected Anchor are absent from this Anchor domain;
their eligibility is a separate cohort question.

`AnchorDomain.observe(metric: Ref[MetricKind] | RuntimeMetricExpr, *,
within: ElapsedWindow | CalendarWindow,
via: Ref[RelationshipKind] | RootRoutes) -> LogicalNumericRelation` and
`AnchorDomain.retention(returning: ParticipantRoleHandle, *,
within: ElapsedWindow | CalendarWindow,
completeness: tuple[BoundedCompletenessDeclarationV1 |
SourceOriginCompletenessDeclarationV1, ...] = ())
-> LogicalRetentionResult` use the same closed window input:
`mv.elapsed(duration: Duration) -> ElapsedWindow` or
`mv.calendar_days(days: int, timezone: ZoneInfo) -> CalendarWindow`; both
require positive length, and `ZoneInfo` must name an IANA zone. For example,
`mv.elapsed(mv.duration(hours=168))` and
`mv.calendar_days(days=7, timezone=ZoneInfo("America/New_York"))` are
different typed inputs.
The interval is `[anchor instant, deadline)` and excludes the Anchor occurrence
itself. A different occurrence at the same instant counts as later only with an
accepted business ordering fact. Calendar arithmetic follows local calendar
boundaries in the named zone before conversion to instants; seven local days
must not be lowered as 168 hours across DST. `returning` is an exact Event
participant role of the same Subject. A source occurrence may prove the
predicate for several overlapping Anchor windows: shared contribution is the
accepted policy, with each use retaining its `(Anchor, occurrence)` binding.
It does not license summing those overlapping contributions as distinct facts.
No hidden exclusive assignment or overlap-policy parameter is offered.

Retention fixes its target instance domain Ω before reading return events.
For every instance, observed qualifying return is known true even with partial
follow-up; absence is known false only if the exact Event/source/window is
covered through the exclusive deadline; otherwise it is unknown. Coverage is
an observed or exact-bound declared fact, not an inference from a maximum event
time, empty result, or rationale. `K+`, `K-`, and `K?` partition Ω and remain
retained as disjoint status sets with coverage and Anchor-to-Subject parts.
For nonempty Ω the result has exact deterministic bounds
`[|K+|/|Ω|, (|K+|+|K?|)/|Ω|]`; for empty Ω both bounds are
`Undefined(empty_omega)`, with empty sets and no implicit zero rate. An
unknown instance stays in the denominator. The 25 true, 5 false, 70 unknown
case must return `[25%, 95%]`, never a rate after dropping unknowns.

`RetentionResult.by_subject(*, rule: AnyAnchor | EveryAnchor)
-> LogicalSubjectRetentionResult` explicitly projects the fixed Anchor domain
to its Subject image and fixes that image as a new Ω. `any_anchor` is true if
any instance is true, false if all are false, otherwise unknown;
`every_anchor` is false if any instance is false, true if all are true,
otherwise unknown. Every Subject in this image has at least one Anchor.
The only constructors of the closed rule union are `mv.any_anchor()` and
`mv.every_anchor()`; there is no default quantifier.
Projection never silently deduplicates an instance-rate denominator. Both
results expose bounded `.show()` and `.contract()`, the status views and their
exact keys; they permit status-based selection and fixed continuation while
the required Anchor/coverage parts survive. They do not expose scalar-bound
`rollup()`, arithmetic on bounds, or conversion of `K?` to false. `members()`
requires a selected, decidable true Subject status; requesting unknown or
incomplete selection returns a structured error.

All C18 failures use `AnalysisError` with a stable constraint ID, exact
`expected`, `received`, `repair`, and a stage of construction, admission, or
execution. Construction rejects wrong role/Subject, nonpositive window,
unknown timezone and duplicate source identity. Admission rejects an
unqualified time axis, unproved same-instant order, incompatible route or
absent required component; execution rejects actual duplicate keys and
contradicted or malformed coverage claims. Insufficient follow-up alone creates `K?`
for retention and is not an execution failure. The repair names the exact
Event/Anchor/window or completeness binding to change; it never recommends
dropping unknown rows.

### Statistical and reference weights (C08)

`mv.statistical_weight(values: NumericRelation, *, role:
Ref[StatisticalWeightKind]) -> StatisticalWeight` binds an independently
observed relation to the exact authored role from the Semantic object model.
`mv.weighted_mean(weight=StatisticalWeight)` constructs a current-row
statistic; the receiver and weight require the same exact instance domain or an
explicit, checked one-to-one correspondence and the declared statistical unit.
Finite nonnegative weights, a positive total weight for a Defined mean, and
Defined values for positively weighted rows are required. Zero total weight
produces `Undefined(zero_weight)` with retained `(weighted_sum, weight_sum)`;
unknown weight or missing correspondence is not silently zero. The result
retains the role/version, binding, units and components, but this direct
statistic has no original-Metric `rollup()`. Order count, allocation and
sampling weights do not gain statistical authority from numerical similarity.

`mv.reference_weights(values: NumericRelation, *, strata:
tuple[CategoryRelation, ...], unit: Ref[EntityKind]) -> ReferenceWeights`
binds an independent Logical or Materialized weight relation. The complete
unique stratum tuple and statistical-unit identity are part of this typed
object; the reference node is fixed for an execution and is not reselected by
downstream `where`, `rank` or `limit`. `stratum_values.standardize(reference=...)`
requires exact stratum correspondence, finite nonnegative weights summing to
one, and a legal value for every positive-weight stratum. Missing strata,
duplicate strata, zero/incorrect total, or incompatible units fail with a
structured repair; no renormalization, row-count substitution or observed
Top-K denominator is allowed. The output is a new NumericRelation with the
reference identity/parts retained, not the observed population total. Its K
permits current-row selection/statistics and fixed continuation, but no
original-state `rollup()` without a separately registered method and parts.

Weight/reference failures are `AnalysisError(expected, received, repair,
constraint_id, stage)`; Semantic declaration failures use `SemanticError`
with the same structured fields. The error distinguishes role mismatch,
missing/duplicate strata, invalid weight value, zero total and broken domain
correspondence rather than reporting only a failed division.

### Ordinary relation ratio (C07)

`mv.one_to_one(*, left: NumericRelation, right: NumericRelation,
via: Ref[RelationshipKind], time: PeriodChange | None = None)
-> OneToOneCorrespondence` binds a declared one-to-one relationship and, when
time axes differ, an explicit period correspondence. It is tied to these exact
two relation nodes and their full key tuples; cardinality and complete key
images are checked at execution. A many-to-one path is not accepted.
`NumericRelation.ratio(other: NumericRelation, *, pairing:
ExactKeys | OneToOneCorrespondence = ExactKeys())
-> LogicalNumericRelation` is a current-row binary method, distinct from
`ms.ratio` (a governed Metric graph) and `mv.runtime_metric.ratio` (one observe
binding). `ExactKeys` requires identical typed instance keys and complete
key images; for different domains, `OneToOneCorrespondence` is constructed
from the exact declared relationship and bound relation keys, and proves a
complete bijection, including time roles. A paired instance may carry an
explicit `MissingCoordinate` on either side from a prior typed operation;
that row remains present and yields `Undefined(missing_side)`. A missing key
image fails pairing rather than widening the domain through `UnionKeys`.
Ordinary ratio does not offer `metric_empty`. A matched pair requires finite
Defined numeric operands, compatible source/temporal scope and known quotient
units. A zero denominator retains both endpoints and yields
`Undefined(zero_denominator)`, not zero, infinity or a dropped row. Any other
non-Defined matched operand is a structured consumption error, not a guessed
business value. Endpoint identity, pairing, units, status and coverage remain
parts; K permits selection, current-row summarization and fixed continuation,
not original-component `rollup()` or automatic share/penetration semantics.
Ratio failures use structured
`AnalysisError(expected, received, repair, constraint_id, stage)` for mismatched
relation owner, key/time/unit correspondence, duplicate keys and non-Defined
matched operands. Zero denominator and an allowed unmatched side are result
Cells, not exceptions. The error suggests an actual typed pairing or a new
governed observation, not a raw SQL join.

### Remaining R0.3 boundaries

`summarize(mv.count())` counts all current instances regardless of value Cell;
`summarize(mv.count_defined())` counts only Defined current values. Neither
changes the original contribution unit. `summarize(mv.mean())` builds new
current-row `(sum, count)` state, whereas `rollup()` merges retained original
Metric components before finishing and must prove coverage, disjointness,
method/version, time and empty-state premises. A legal empty `(0,0)` state can
merge although its displayed mean or ratio is Undefined; absent or unknown
state cannot be replaced by zero. Exact version selection uses the declared
instant/before-end policy and full Entity identity, never a last-known row.
Direct exact distinct and quantile observations have no original-state rollup
or attribution K from a displayed scalar; a coarser result requires a fresh
observation at that target domain. Source I/O and data checks are built as
Ibis expressions; fixed Artifact continuation is controlled local decoding to
pandas/NumPy/SciPy. `md.raw_sql` remains a terminal, read-only datasource
escape hatch outside typed Analysis; neither its SQL text nor its result can
become an Analysis source, method implementation, Artifact or continuation.
The public SQL-executing parity route and `ms.from_sql` are removed. Historical
SQL can be described in `ai_context`; tests may retain independent SQL oracles.

## R1.1 transition status

A one-table unscoped Population scan/filter or sum/count Metric aggregate on
qualified DuckDB or SQLite uses `SourceSession` for its Ibis compilation and
batch read. Basic Metric aggregation can group by a direct dimension. Other older
Dataset source routes reject with `MaterializationError` at `source_admission`,
before creating a Run or opening the source, and identify their R5–R8 migration
stage. The R1.1 acceptance record tracks the remaining concrete legacy text
methods and separately admitted fixed Artifact continuation.

## R1.2 basic source qualification status

The same one-table basic Population and sum/count Metric source path now attempts
DuckDB, SQLite, PostgreSQL, MySQL, Trino, and ClickHouse through `SourceSession`.
The compiler's full composite-key validation is still required before
publication; duplicate identities are never repaired by `distinct`. This
statement describes a candidate route, not a blanket backend or table-form
qualification. A single-column Entity key is projected as an Ibis scalar only on
the MySQL basic source route and restored to the one-field Arrow identity struct
before publication. Other backends retain the ordered Ibis struct for single-column
and composite keys pending separate qualification. MySQL basic
Population execution is qualified for the tested single-column table and view;
sum/count Metric execution is qualified for the tested single-column table.
MySQL composite keys reject before Run because
Ibis 12 cannot compile their `StructColumn`. The R1.2 acceptance record keeps
backend, form, exact type, permission, and resource evidence separate, and
retains the earlier full-gate failures for older source methods.

## R1.4 consumer and disclosure handoff

The internal `SourceSession` is the admitted route for the basic source-backed
Dataset path described above. Existing Event, Lifecycle, Attribution, mean,
time-scoped and other older source methods still reject at `source_admission`;
their prior J1–J4 or Group A evidence does not qualify them under this route.
Fixed Artifact continuation retains its separately admitted path. The R1
acceptance record names remaining old text consumers and their R5–R9 owners.
Datasource inspection and connectivity establish physical facts only, not
method admission.

## S1 W1 private J1 construction

W1 loads the closed Semantic additivity, event-time, and value-policy declarations
through normal authoring, then builds a private J1 Logical chain for members,
Region read and strict selection, Region grouping, builder-backed Revenue
observation, Channel contribution coordinates, and original-state rollup.
Construction and field handles do not read business sources. The accepted method
semantics require a declared directed Buyer path, a single-valued member Region,
sum retained parts, ignore-Null and empty-Null behavior, and a declared event
time. The method has no registered source or pandas implementation in W1, so
the independent SQL oracle remains an oracle rather than DSL execution evidence.
An opaque Metric body may load but cannot continue through this private path.

## S1 W2 private J1 execution

W2 attaches those exact J1 roots to Dataset row and row-set contracts and a
private placement check. A selected DuckDB Ibis backend lowers the admitted
members, Region read/strict selection, Region grouping, Revenue observation,
Channel contribution grouping, original-state rollup, and current-row
sum/count/mean to Ibis expressions. Ibis compiles the source expressions and
checks; a source failure ends that route. The source adapter validates physical
string/int64 keys, string categories, int64/float64 contribution values,
finite floats, complete keys, Cell tags, and checked sum/count state.

The private pandas route consumes an exact J1 predecessor from an exhausted,
receipt-checked local read or a complete source result. A category read can be
selected or grouped after a fixed receipt; a coordinate-free Revenue
observation retains keyed sum, non-null count, and row-count parts for local
rollup after its fixed receipt. An in-memory source result with a retained
Channel coordinate can group that coordinate by explicit key. Persisted
coordinate parts and successful Artifact publication/cold recovery are described
in the W3 section below. These private calls do not activate public Analysis
DSL signatures or claim Runtime publication.

For J1 builder-backed `ms.aggregate(..., agg="sum")`, the graph fixes
ignore-Null inputs and a Null result for complete empty contributions; the
builder has no separate value-policy parameters. Explicit authored policy
facts, when present on a normalized Metric, must agree with that graph before
J1 admission. Both routes apply the same versioned Cell and numeric admission
policy. DuckDB and pandas accumulate float64 in different orders, so a float64
sum or mean is not promised to be bit-identical across routes. J1 exchange
vectors compare float64 with an explicit tolerance and preserve exact int64
checks; a retained coordinate float64 partition must pass its own bounded
sum-state check before publication.

## S1 W3 private J1 Artifact exchange

W3 uses one closed schema-first Arrow stream contract for J1 member relations
and Cell-valued rows. The DuckDB/Ibis producer and receipt-checked local Parquet
reader complete row, key, Cell and content checks before yielding publication
evidence. An exact admitted Run publishes the main rows and separately receipted
sum/count and optional coordinate parts through the existing Store. Recovery
requires the exact Artifact reference, J1 definition, method version and input
binding; it reads every selected receipt before pandas continuation. A cold
process can continue with saved category rows, current-row statistics and
Channel coordinate rollup without a source connection.

## S1 W4 private J1 Runtime execution

The internal `DatasetRuntime.execute_j1(...)` action now owns J1 admission,
Session writer exclusion, incomplete-Run reconciliation, and publication
outcome read-back. A source call opens its supplied DuckDB/Ibis source factory
only after Run admission and assigns a new v2 key from the stable J1 definition
binding and the allocated Run ref. Repeating the same J1 node reads current
source data and publishes a separate immutable Artifact.

A fixed local continuation selects an exact saved predecessor and binds its
primary and retained-part receipts into the v2 key. A fully validated exact
hit returns its original Artifact without a Run; a miss reads the retained
state through the controlled Parquet reader and executes the admitted pandas
method. Live-source work combined with an explicit saved predecessor rejects
before source opening, Artifact row reads, or Run admission. The J1 semantic
node retains its definition fingerprint; the invocation binding additionally
encodes source or exact Artifact input and method version. Run, publication,
exchange binding, and read-back retain the selected key. Existing non-J1
Dataset execution keeps its v1 key and cache behavior. This is a private
execution chain, not a public DSL or general production `execute()` route.

## S2 P1 private two-predecessor binding

The private `J1Observed.compare(baseline)` constructor admits two Entity-level
observations of the same Metric, component plan, coordinates and non-time
scope facts with distinct time scopes. Both branches must descend from the same explicit member root;
matching definitions or keys alone do not establish that identity. The
ordered current/baseline root computes a strict absolute difference. It has a
Difference quantity with endpoint requirements and does not inherit the
Metric's original-state rollup authority. Selection from Difference and new
observation remain later S2 work.

For a source-only compare, the DuckDB/Ibis adapter first checks the complete
logical shape and method route, then realizes the shared member relation once
per invocation and supplies that realization to both observations. It checks
unique and equal endpoint keys and finite Defined numeric Cells before
publishing the result and independently receipted endpoint parts. A second
top-level execution gets a new Run, member realization and Artifact.

For fixed input, the private Runtime requires two exact ordered observed
Artifacts whose exchange metadata names the same nonempty member realization.
The fixed key binds both Artifact references, receipts, parts and member
binding. A validated hit has no new Run; a miss reads both retained inputs and
uses pandas for the same strict difference. Mixed live and fixed inputs, or
independently captured endpoints, fail before Artifact row reads and Run
admission. This route is limited to the admitted J1 compare shape and is not a
public multi-output capture API.

## S2 P2 private J2 selection and next observation

A private Difference now has a bound numeric value field with finite
`lt/lte/gt/gte/eq` thresholds. Float64 `eq` uses exact binary equality. Strict
`where` keeps the Difference value and
filters both retained endpoint parts by exact member key; `members()` projects
the selected identities without reading a source. The selected Entity domain
binds its parent domain and selector definition, so a later observation names
the actual selected domain. A comparison requires the same immediate member
input node for both observations; sharing only an older ancestor is insufficient.
Difference and its selected relation support current-row sum/count/mean, but
neither inherits the original Metric's state rollup. The selected relation has
no second numeric `where` field handle in P2.

For J2, one source-only graph evaluates July and August under strict complete
key pairing, selects negative changes, observes September for those Logical
members, and computes mean over the selected customer rows. The source stage
realizes the compare member node once in that invocation. The J2 fixture uses
explicit zero-valued orders for the zero Cells; a truly empty Revenue sum still
returns Null and cannot enter strict compare.

The existing Store and exchange codec retain the selected Difference's exact
endpoint parts and selector-bound domain. A fixed pair requires two ordered
Artifacts with one shared member realization; pandas can then filter the
Difference, project members, or summarize its current rows after receipt checks.
A fixed selected member Artifact cannot be used for a new live read or observe.
These are private qualifications, not public DSL methods or a two-endpoint
capture API.

## S4 P1 public admission

The first public slice admits only the J1–J4 shapes in the MVP validation
plan. `session.members(Ref[EntityKind])` returns a logical AnalysisDomain;
`read(Ref[DimensionKind])` returns a CategoryRelation; and `observe` accepts
one `Ref[MetricKind]`, an explicit fixed `TimeScope`, one relationship or the
closed two-root `routes(route(...), route(...))` value, and optional declared
contribution coordinates. Domain and relation methods return concrete logical
variants. `execute()` exists only on logical values; `show()` and
`to_pandas()` exist only on materialized values. Both expose `contract()`.
`session.artifact(reference)` returns an exact materialized variant, with
the existing non-DSL materialized families retained in its closed union.
Date-only and naive fixed TimeScope bounds follow the Session's persisted report
timezone before lowering to the admitted UTC event axis. Aware bounds retain
their absolute instant; normalized bounds join the logical definition identity.
The member DSL reads that axis timezone from the normalized Semantic time
dimension. This first source route requires UTC; a different or unresolved
axis timezone is rejected before source execution.

The admitted operations are categorical equality selection and member
projection; grouping by a member Dimension or retained contribution
coordinate; exact absolute same-member comparison, strict numeric selection
and member projection; current-row `sum/count/mean` through closed method
values; original-state rollup; and same-Entity no-lag Spearman with a fixed
coefficient view. Unsupported target-language methods remain unexported.
Public result types never turn an unretained component or subject map into a
continuation. A logical chain constructs without business-source I/O; a
source-dependent top-level execute creates a fresh evaluation. A fixed-only
continuation uses exact retained receipts and pandas. Mixed fixed/live inputs
reject before a Run or either input is read.

The canonical entry for a migrated first-round shape is the domain/relation
chain. Existing Session population/observation and Dataset family methods
continue to serve shapes outside this admitted slice; they are not aliases for
the new chain. The public cutover must keep live Help, API docstrings, export
snapshots, user examples and the packaged analysis workflow synchronized.
Previous private J1 Artifacts have no public continuation snapshot and are
not upgraded. A newly public Artifact must retain its admitted node shape,
method/semantic policy versions, exact input binding and receipts so a cold
recovery can preserve the same K without loading current semantics.

| Canonical P1 call | Logical return | Materialized return or continuation | Required retained authority |
| --- | --- | --- | --- |
| `session.members(entity)` | `LogicalAnalysisDomain` | `MaterializedAnalysisDomain` | Entity identity and exact member root |
| `members.read(dimension)`, then `where(read.value.eq(category))` | `LogicalCategoryRelation`, `LogicalSelectedCategoryRelation` | matching category variant; selected `members()` projects identity | declared single-valued Dimension and selected member keys |
| `members.group_by(dimension).observe(metric, during=window, via=relationship)` | `GroupedNumericRelation` | `MaterializedGroupedNumericRelation` | group binding and sum/count state |
| `members.observe(metric, during=window, via=relationship)` | `LogicalNumericRelation` | `MaterializedNumericRelation`; `group_by` returns `GroupedNumericRelation`, `rollup` returns `LogicalRolledNumericRelation` / `MaterializedRolledNumericRelation` | sum, non-null count, row count and optional coordinate state |
| `members.observe(metric, during=window, via=mv.routes(...), coordinates=(...))` | `LogicalRatioRelation` | `MaterializedRatioRelation`; `group_by` returns `GroupedRatioRelation`, `rollup` returns `LogicalRolledRatioRelation` / `MaterializedRolledRatioRelation` | numerator sum/count/row count and denominator count/row count |
| `observed.compare(baseline)`, then `where(diff.value.lt(threshold))` | `LogicalDifferenceRelation`, `LogicalSelectedDifferenceRelation` | matching Difference variants; selected `members()` projects identity | exact ordered current and baseline endpoints |
| `relation.summarize(mv.sum/count/mean())` | `LogicalStatisticRelation` | terminal `MaterializedStatisticRelation` | current-row method and Cell checks; no original-state rollup |
| `observed.correlate(other, method="spearman")` | `LogicalAssociationResult` | `MaterializedAssociationResult`, then fixed `MaterializedCoefficientRelation` | paired observation state, pair counts and exact member binding |
| `coefficient.where(coefficient.value.lt(threshold))` | `LogicalCoefficientSelectionRelation` | `MaterializedCoefficientSelectionRelation` | retained pair counts and coefficient policy |

`mv.route(root, *, through=(...))` and `mv.routes(first, second)` are
closed values; `mv.sum()`, `mv.count()`, and `mv.mean()` take no arguments.
`rollup()` merges original retained components, while `summarize(...)`
calculates over current rows. Logical values own `execute()` and
`contract()`; Materialized values own `show()`, `to_pandas()`, and
`contract()`. A fixed selected-member projection is a
`LogicalFixedAnalysisDomain` with only a local `execute()` continuation.
Cold recovery reconstructs the concrete materialized variant from a canonical
v1 public snapshot stored in the v3 J1 exchange. The snapshot contains the
node graph, frozen row facts and policy objects; the Artifact descriptor binds
the definition, method, input execution key, member implementation and all
primary/part receipts. Missing, malformed or mismatched snapshots reject.

| Existing public entry | P1 decision |
| --- | --- |
| `session.population(...)` and its Dataset family methods | Retained for Population, Event, Lifecycle and other shapes outside the admitted Entity-domain chain; it is not a J1–J4 synonym. |
| `session.observe(...)` and its Dataset family methods | Retained for existing Metric, time-series and non-J1–J4 analysis; new member-domain J1–J4 guidance starts at `session.members(...)`. |
| `session.artifact(reference)` | Extended to recover a concrete public J1–J4 Materialized variant when its validated snapshot exists; other family Artifacts retain their prior return shape. |
| Private `DatasetRuntime.execute_j1(...)` and old private J1 Artifacts | No public entry or migration; private Artifacts without a public snapshot reject through the public recovery call. |

### S4 P2 public disclosure

`analysis.entry` routes to `session.members`; the existing Analysis method and
input hubs route to the admitted J1–J4 calls. Each public relation type links
its actual methods to exact native `marivo.help("analysis.<target>")` leaves.
Those leaves derive signatures and constraints from the callable owner and
identify the required governed inputs. CLI Help remains a Python Help bootstrap.

The public `AnalysisContract.actions` is a tuple of `AnalysisAction(call,
help_target)` values. It contains only mechanically admitted calls for the
receiver and its declared retained components; the old bare-name
`next_actions` field is removed. `contract().show()` reports the available
kind, phase, domain, quantity, unit and method facts, current-row weighting,
Cell fields, source assumptions and retained component roles as applicable.
It does not query business sources. A materialized relation's `show()` combines
those facts with one bounded committed preview, redacting Entity member values.
Cold recovery derives the same disclosure from the validated frozen snapshot
and Artifact, without reconnecting to current semantics or sources. Integrity
failure still blocks recovery; a contract is not a storage revalidation result.

Structured public repairs retain distinct missing-source-key, missing-part,
binding-mismatch and unsupported-route diagnoses. Each exposes expected,
received and a native Help destination for the failed operation or recovery.
Help, result cards, errors and logs never reveal complete Entity member keys
or digests calculated solely from those keys.

## Accepted S0 Analysis DSL slice (inactive)

This historical S0 section accepted the first-round semantics in the
[DSL MVP](../../superpowers/specs/2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#2-首轮方法范围与后续扩展)
and the
[architecture](../../superpowers/specs/2026-09-24-marivo-analysis-dsl-architecture-design.md#3-基础能力与规则推导)
as an implementation contract before the S4 P1 public admission above. It
covers non-versioned single-column integer/string Entity identity,
many-to-one paths, categorical attributes, fixed half-open event windows,
sum/count and explicit-component ratio, strict selection, exact-key absolute
time comparison, current-row sum/count/mean, original-state rollup, and
same-Entity no-lag Spearman. DuckDB is the first source adapter; pure
retained-input continuation uses pandas. The full
[target interface](../../superpowers/specs/2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)
remains proposed outside this slice. S0 alone did not activate public
signatures, Help, other backends, mixed-input execution, or production routing.

The acceptance baseline is code `79faee0030685f0690fd2970990fccbe9a385886`
in a clean `panda` checkout. The isolated T1 worktree starts at the same
commit; its ignored plan copy matches the original. No dependency lockfile
was found in this checkout, so `pyproject.toml` records the dependency constraints.
SHA-256 inputs reviewed here:

| Input | SHA-256 |
| --- | --- |
| Copied S0 implementation plan (ignored) | `a254242f8a247b9cb53f07d652d0de7f8d2fcced51eb5c047b753a803d83bb60` |
| Tracked DSL MVP | `46cb6232eb9db9c2fff5698177b78487b50d9d3b73d5bbe7400a33d6478a6d51` |
| Tracked target interface | `36e355655bd9e8f3bee0b492e4071a5988c543034ebca2725e8d610b0ca2c57b` |
| Tracked architecture | `72718b431b2af5552c6cda6a0fa22214953d57decb1944d744fcd476e4452e2d` |
| Tracked algebra theory | `c1901f306952e294fb11c329fbf66f5358e1ec3e605f69d9dcd3e60852acc309` |
| Dependency constraints (`pyproject.toml`) | `b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9` |

These identify the reviewed inputs; later implementation must recheck its
own baseline and installed dependencies. The original checkout's `.venv`
reported Ibis 12.0.0, pandas 2.3.3, PyArrow 25.0.1, DuckDB 1.5.3,
SciPy 1.17.1 and NumPy 2.4.6; this T1 document pass did not use them for
execution evidence.

The ledger below is the T1 owner-gap record. An owner produces the named fact;
consumers may check or transport it but must not redefine it. `S0` in the last
column means a private contract seam, not completed execution. The listed V
items are future validation obligations, not T1 test results.

| ID and accepted requirement | Single owner | Current gap | Next task | MVP validation | Stage |
| --- | --- | --- | --- | --- | --- |
| A01 — declared Entity identity, time role, Metric contribution roots and method value policy | Semantic object model | Current normalized graph retains roots/components and some fixed null/empty rules, but cannot supply the new decorator's full additivity, time-role and value-policy declaration | T2; T5 must use real declarations | V02–V04, V14 | S0; S1 authoring consumer |
| A02 — Entity/Group/Singleton domain, member identity, contribution coordinates and explicit node binding | Dataset Core | Existing descriptors/handles do not represent the accepted DSL domain/quantity/Cell distinction or run-local node binding | T2 | V01, V02, V10, V14 | S0; S1–S2 execution |
| A03 — six closed capability rules, including RequiredParts and local preservation | Dataset method contracts | Existing family methods and observation contracts do not share this accepted rule/output derivation | T2 | V01–V05, V13 | S0; S1–S2 execution |
| A04 — one method/version policy with separately admitted implementations | Operator registry | `ImplementationRegistration` describes execution routes but is not the accepted method-semantics owner | T2 | V05, V11, V13, V15 | S0; S1 qualification |
| A05 — source-only, fixed-Artifact-only and mixed transitive input classification | Compiler normalization | `logical_roots()` stops at a materialized leaf, but no closed classification controls admission | T2 | V08, V10, V11 | S0; S1–S2 execution |
| A06 — Ibis source lowering and pandas retained continuation | Compiler placement | Current registered methods may use a DuckDB `ParquetBinding` for retained input | T2 contract; S1 adapter | V05, V07, V11, V15 | S1 |
| A07 — fixed schema, four Cell branches, state/part binding and completed checks | Materialization contract | Existing primary/parts and receipts lack the accepted Cell and shared-producer schema | T3 | V03, V06, V07, V10 | S0; S1 codec |
| A08 — separate stable definition identity and per-source-evaluation Run identity | Dataset Runtime | Current execution key is definition-only and lookup precedes Run admission | T4 | V08, V10 | S0; S1 dispatch |
| A09 — one-key publication, exact receipt/Run recovery and no automatic replay | Session Store | Store uniqueness is sound, but the new source key/receipt binding is absent in publication | T4 | V08, V10 | S0; S1 integration |
| A10 — authentic declarations and independent J1–J4 oracles | Test fixtures | No isolated DSL fixtures/oracles; fabricated semantic flags would conceal A01 | T5 | V01–V04, V09, V13 | S0 |
| A11 — same-Entity no-lag Spearman's paired-value and method state | Association method owner | Existing method exists, but the new Relation/Cell input and exchange route are not admitted | T2 policy; S3 adapter | V09, V11 | S3 |
| A12 — shared batch schema, completion checks and resource close | Materialization execution | `BatchStream` exists, but the new source/retained producer contract and all-exit close obligations are not fixed | T3 | V06, V10, V12 | S0; S1 producer |

For A01, authored assertions, graph-derived component facts, and checks
completed for this execution have distinct provenance. A queued check is not
evidence; declared Entity identity does not require a hidden source uniqueness
scan. Component role, root, filter, time fold and available state can be read
from the canonical graph. A decorator body cannot yield a trustworthy
contribution partition, additivity permission, missing-value policy or hidden
ratio components; the Semantic owner must provide the minimum declaration and
version before a consumer relying on it is admitted. T5 must block on a
missing real declaration rather than insert a fixture-only capability flag.

For A03, the accepted
[method rule table](operators-and-frames.md#accepted-s0-method-rules-inactive)
is the sole owner of the six capability semantics. Its numeric and Cell
policies come from the declared quantity and registered method, never from an
Ibis default, pandas dtype or a second adapter policy. Valid empty
contributions differ from unknown coverage, missing keys and missing state.
A ratio with retained numerator/denominator components may merge those
components before finishing; equal displayed ratios and a decorator body
containing division do not provide that authority.

For A04 and A06, one versioned method policy may have multiple independently
qualified implementations. A registered source implementation constructs Ibis
expressions and delegates SQL dialect compilation to Ibis; a retained-input
implementation consumes pandas after governed receipt reads. An unsupported
type, backend or input shape rejects without a silent fallback, handwritten
analysis SQL or a placeholder production registration.

For A05, compiler normalization classifies this invocation's transitive data
dependencies, stopping at each explicit materialized leaf. Historical lineage
does not turn a fixed Artifact into a live source dependency. For A08, Dataset
Runtime owns the [new lookup and Run order](session-state-and-runtime.md#accepted-s0-input-and-execution-protocol-inactive);
for A09, the Session Store owns unique publication and exact recovery.
Existing methods keep the current behavior below until individually migrated
and qualified; no second Runtime, registry or Store, old-Artifact migration,
or implicit fallback is accepted.

For A07 and A12, both source and receipt-checked Parquet producers expose a
known schema before iteration, including an empty stream. The exchange carries
typed non-null identity keys, explicit Cell tag/reason/value, quantity and
domain binding, private state roles and method versions. A missing row remains
a domain fact. Batch schema drift, mismatched parts or binding, and malformed
Cell payload reject; Arrow nulls alone do not encode all Cell branches. The
existing stream owner must release its resources on exhaustion, early close
and failure. Incomplete row-count, digest or coverage checks cannot become
successful evidence or a published result.

The inactive T3 seam now binds the existing row/row-set, domain, quantity,
method, evidence and selected receipts in `ExchangeBinding`. Its private
`marivo.analysis_exchange/v1` codec records exact fingerprints, method-owned
Cell reasons and distinct evidence categories; decoding alone grants no
publication or completed-check authority. The current Artifact descriptor v1
remains unchanged. `ValidatedExchangeStream` checks the same Arrow schema and
Cell rules for an in-memory source stream and a governed local Parquet stream.
It reports physical completion only after full exhaustion and owned close;
pending coverage obligations remain pending. The receipt adapter exposes its
schema before iteration and supplies the empty header batch needed by existing
consumers. Full producer qualification and descriptor publication remain S1.

## Unified operator and execution ownership

All backends use one operator contract and implementation-registration mechanism.
The selected backend owns physical preparation, execution,
retained import and resource lifetime. DuckDB's temporary objects, macros and
registration hooks implement the same semantic requirements; they are not
operator-level exceptions. Remote implementations must work with read-only
accounts and prove equivalent semantics, numerical behavior and required
assertions before registration. No implicit alternate route or new cross-engine
private-state transfer is introduced. DuckDB analysis and the PostgreSQL, MySQL, SQLite, Trino and ClickHouse scalar subsets and the individually qualified relational/date methods below are enabled.

### Required source columns

All six backends derive required columns from the complete logical dependency
closure, preserving each Entity, exact source binding and physical relation.
Unused declared columns and unrelated physical columns do not participate in
execution type admission, schema validation or source projection. Hidden Metrics,
identity and relationship keys, version axes, predicates and retained components
remain required even when the displayed result is empty or projected.
Missing required columns, unsupported physical types and type mismatches identify
the Entity, relation, logical/physical column and expected/actual type. This does
not relax semantic loading or enable additional backend types or methods.

Column comments remain datasource inspection evidence. Explicit inspection retains
comments for unused columns and maps physical comments to declared aliases;
execution does not fetch comments or infer business semantics from them.

### Discovering execution boundaries

`marivo.help("analysis.actions.execute")` owns the bounded execution guidance.
Datasource connectivity and semantic readiness do not establish support for a
particular method or input shape. For source-bound Metric and Population graphs,
the logical Dataset contract shows the selected backend's static source admission:
`rejected` includes the qualification reason, while `static_pass` means only that
the pure source check passed. Graphs with retained Artifact inputs show
`not_checked`. Final placement, current Runtime state and source-data validation
remain execution-time decisions. Read the structured rejection for the selected
invocation. Remote sources require read-only
accounts; retained import and uploads remain unsupported. Source relations are not
restricted by table form: views and every engine or connector type enter, and Trino
rejects only `$`-suffixed internal tables. Installed
package acceptance is recorded separately from source-tree Runtime evidence.

`md.register()` persists a declaration without loading analysis execution code.
Execution admission loads only the selected backend implementation; a missing
selected dependency fails with a structured repair before opening the source.
Ordinary DuckDB result batches use Ibis execution and an owned Arrow reader.
Colima qualification of the installed Ibis 12 batch interfaces did not qualify
a remote transport switch. PostgreSQL preserves Decimal, timestamps and row
batching, but its default text cursor fails on the anonymous RECORD used by
Dataset identity. A binary server cursor repairs that value, yet closing an
Ibis reader after a partial read leaves its named server cursor and transaction
open. Trino preserves exact Decimal and composite identity values, one-row
batches and submitted SQL, but early reader close leaves the driver cursor open.
ClickHouse preserves Decimal, UInt64, timestamps and composite identity values,
but a SELECT-only account returned a three-row batch for `chunk_size=1`.
PostgreSQL and Trino batch entries also call Ibis pre-execution hooks;
ClickHouse's batch entry collects external tables. The current remote paths do
not invoke these hooks. MySQL and SQLite retain incremental driver transport
because their Ibis batch APIs materialize a complete pandas result first.
Remote source execution requires no write, table-creation or view-creation
privileges. DuckDB may create action-local temporary tables and views.

### Relational and native-date methods

PostgreSQL, MySQL, SQLite, Trino and ClickHouse use exact per-backend admission
for direct-column mean, weighted mean and ratio in addition to Group A. The
complete dependency graph is checked, including predicates and projected-away
components. Same-source relationship paths retain identity, missing-coordinate
and fanout assertions. Each participating relation must meet its backend's existing
physical type restrictions.

Native civil-date axes support single-unit day, week, month, quarter and year
buckets. Snapshot and validity membership use declared boundaries without
automatic source-data identity or time preflights. All five remote backends admit
both authored validity interval closures and configured open-end sentinels.
Version diagnostics still never gate execution or domain equality.

Mean, weighted mean and ratio preserve sufficient components through atomic
primary/parts publication and immutable retained rollup. Composed Decimal
results decide per published unit through the semantic layer's derived
precision facts instead of one blanket rejection: a decimal linear unit
publishes exact dec(38, s) sum-leaf add/sub results where the resolved unit is
admitted, while mean/div units stay backend-rejected — the live MySQL probe
measured engine AVG rounding at ROUND_HALF_UP (a 5-tie 0.3128125 returned
0.312813, so the declared HALF_EVEN quantization cannot be bit-exact),
PostgreSQL and Trino numeric/AVG scales are not a public contract, ClickHouse
decimal division truncates scale and AVG returns Float64, and DuckDB decimal
division and AVG return DOUBLE. A Decimal input with an already resolved
floating result — ratio and weighted mean over Decimal components — remains
the existing float64 contract and is not part of this per-unit resolution.

Status-time folds were qualified per backend by C6 on live probe evidence:
PostgreSQL (ARRAY_AGG argmin/argmax), SQLite (JSON_EXTRACT over MIN/MAX text
argmax), Trino (MIN_BY/MAX_BY) and ClickHouse (native argMin/argMax) admit
first, last, mean, min and max folds; MySQL admits mean, min and max, while
its first and last folds stay rejected because ibis has no ArgMin/ArgMax
compile rule for MySQL. Percentile/quantile folds remain rejected on every
backend. Admission is probe-then-open per backend and fold kind: the
status-time component and the node-level fold override consult the same
per-backend qualified kind set.

Comparison and attribution use complete non-Entity axis state, including
hidden-axis expansion on PostgreSQL, SQLite, Trino and ClickHouse for
additive difference and component mix. SQLite encodes source masks as
fixed-width bits and strictly restores boolean arrays before publication;
PostgreSQL lowers null-safe full alignment through two one-sided joins.
MySQL's scalar mask lowering is implemented, but expanded attribution stays
rejected after the live composed query exhausted the qualification server's
768 MiB memory limit. Trino's expanded Top-K form remains rejected: the live
query exceeded the certified server's 150-stage limit without a read-only
materialization path.
Forecast, Kendall and time discovery consume complete source-aggregated
inputs in the caller; they do not collect raw semantic Entity rows.

Computed Measures (row expressions) aggregate on these backends. A
`@ms.measure(...)` body returns one row-level ibis expression over the owning
Entity's declared columns — add/subtract/multiply arithmetic, unary negation,
explicit casts and typed literals. Cross-row aggregations, window functions,
division, conditional or null-handling calls, references outside the owning
Entity, undeclared columns and float operands in arithmetic fail at semantic
load with structured errors before execution. All six backends admit these
bodies on their declared table sources.

Exact distinct membership for direct measure and Entity identity keys is
qualified on PostgreSQL, MySQL, SQLite, Trino and ClickHouse. SQLite and MySQL
deduplicate typed Entity identity fields as scalar SQL columns and reconstruct
the unchanged private Arrow struct. Exact linear-interpolation distribution
state is qualified on all five remote backends. Percentile status-time folds
and remote `duckdb_tdigest@v1` remain unqualified. Entity Pearson and Spearman
correlation now reduce complete source-private pairs on all five remote backends;
Kendall remains a complete-input local continuation. PostgreSQL admits exact
Event journeys with two or three steps, and Lifecycle replay with two trigger
Events, over unversioned tables with int64 subject and occurrence identities.
Direct PostgreSQL Event funnel, time-to-event and subject selection, and
Lifecycle distribution, transitions, dwell, violations and subject selection
are also admitted. Complete PostgreSQL funnel inputs can continue through the
existing retained comparison and attribution path. PostgreSQL journey queries
use one read-only materialized CTE bundle; replay and direct reducers execute
source-side assertions and outputs in a read-only repeatable-read transaction.

ClickHouse and Trino admit exact two-step Event journeys with first-per-subject
or every-start shared/exclusive matching over the same identity/table shape.
ClickHouse uses a single packet query with materialized CTEs; the reader must
be allowed to set `enable_materialized_cte=1` while remaining read-only. Trino
uses a read-only repeatable-read transaction and separate source count assertions
to avoid duplicating the complete match beyond its stage budget. Its live
acceptance uses Iceberg. Neither path transfers raw occurrences for local matching.
Trino also admits direct ungrouped funnels, first-per-subject time-to-event and
subject selection, plus complete ungrouped funnel comparison through the existing
local continuation. Its grouped funnel reconciliation exceeds the 150-stage
acceptance limit (306 stages), so grouped funnels and dependent attribution remain
closed. Trino and ClickHouse also admit Lifecycle history with two trigger Events
and one int64 component per subject/occurrence identity, under the same table restrictions (Iceberg and MergeTree,
respectively). Native array folds replay every occurrence without a recursive
query depth cap; source-side interleaving exploration proves equal-time
confluence including per-occurrence violation outcomes. Trino keeps all assertions
and parts inside its read-only snapshot. ClickHouse returns assertions, bounded
Evidence, history and all three retained parts in one ordered packet statement
under a shared storage snapshot, without requiring Lifecycle CTE materialization.
Complete retained histories support the existing local distribution, transition,
dwell, violation and subject-selection continuations, including cold recovery.
Direct Trino/ClickHouse Lifecycle reducers and selection remain unqualified.
ClickHouse direct Event reducers and selection remain closed after planner/memory qualification failures. SQLite
and MySQL C9 methods remain closed. These are exact implementation qualifications, not claims
that other engines cannot implement the underlying algorithms. Other Event and
Lifecycle shapes reject through the method registry before source execution.
Sampling, Entity candidates, and source
driver screening remain unsupported on these backends. Remote
retained import stays disabled. Cumulative Metric graphs and semantic calendar buckets were
activated by C6 on all five remote backends — calendar buckets over native
civil-date axes with a matching certified calendar snapshot, cumulative
Metric graphs through the shared endpoint-window lowering with time_scope
clipping and retained continuation — while any other source requirement
beyond that lowering stays rejected. Every admitted cumulative anchor shape
is admitted on every backend, including the fiscal composite
``ms.cumulative(anchor=ms.grain_to_date(grain=<certified calendar grain>))``;
a DuckDB oracle journey executes that fiscal-grain composite (fiscal-month
endpoints and the axis-less scalar path), while its five remote backends'
execution evidence is scheduled with the C7/C8 calendar work. Linear Metric graphs and computed Measures were activated by C4 on
all six backends; the composed-Decimal unit matrix above names each backend
whose linear decimal cell resolves exactly (PostgreSQL, MySQL, ClickHouse) and
the ones that keep the conservative rejection (Trino's lossy AVG probe, SQLite's
missing Decimal storage). Multi-unit buckets and string-parser time axes were
activated by C3b (see its acceptance record for per-backend and per-format
limits). Trino's strptime and composite hour-prefix axes were qualified later
through [live source execution](../../superpowers/specs/2026-09-23-c3b-trino-parsed-time-acceptance.md).
Trino `date_parse` accepts `%f` with its native millisecond result precision;
the tested malformed cell raised the native parser error before publication. No
private state is uploaded or moved to a different executor to bypass rejection.

### Native timestamp analysis

Qualified native timestamps support typed predicates and `hour`/`day` buckets
with `count=1`, through microsecond precision. PostgreSQL also admits timestamptz;
MySQL TIMESTAMP and ClickHouse DateTime/DateTime64 retain physical instant
semantics when bound with an aware timestamp type. ClickHouse timestamp execution
requires a verified UTC reader timezone; non-UTC column and report timezones remain
supported. Existing explicitly asserted
UTC-labelled civil bindings remain validated as civil values. A physical timezone
must match an aware declaration; a plain declaration cannot relabel a non-UTC
ClickHouse instant. Trino uses the actual Iceberg timestamp(6) type. SQLite keeps
its validated civil-text representation and uses connection-local deterministic
time functions without transferring source rows to another executor.

An authored native parser timezone takes precedence over the reader timezone.
Only engines without a timezone probe use recorded system fallback; failed probes
and invalid names fail when reader authority is needed. Physical instants and
explicit parser authority do not require a reader probe. IANA and explicit fixed
offsets are supported for reader/report authority; native parser declarations
retain their existing IANA validation. Report authority is persisted once.

Scopes compare exact instants before producing report-local civil coordinates.
Naive timestamps in DST gaps or folds fail rather than selecting an implicit
interpretation. Two known instants in a repeated report hour share its civil bucket.
Naive predicate literals compare civil fields; aware literals compare instant
fields. Mixing these kinds fails. Milliseconds/microseconds remain exact through
SQL literals, Arrow, Parquet and source-offline cold recovery. Hour-to-day retained
folds reuse the persisted temporal facts. String parsing and multi-unit buckets are
enabled per backend by C3b: a multi-unit bucket is anchored on the civil midnight of
its own day and only counts whose width divides one civil day are admitted, so a
six-hour bucket spanning a spring-forward gap is five hours and one spanning a
fall-back gap is seven. Semantic calendar buckets and cumulative Metric
extensions are enabled on all five remote backends by C6 — calendar buckets
over native civil-date axes with a matching certified calendar snapshot
(admitting only its published levels), cumulative Metric graphs through the
shared endpoint-window lowering with any other source requirement rejected.
Epoch parsing and new timestamp version selection remain
unsupported on remote backends.

```python
import marivo.analysis as mv
import marivo.semantic as ms

orders_by_hour = (
    session.observe(
        ms.ref.metric("sales.revenue"),
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-03"),
    )
    .with_time_axis(ms.ref.time_dimension("sales.orders.order_time"), grain=mv.grain("hour"))
    .aggregate()
    .execute()
)
orders_by_hour.rollup(grain=mv.grain("day")).execute().show()
```

Actual adapter submissions own SQL diagnostics, with source/local domain, role
and submitted/succeeded/failed status. Compilation alone records no submitted SQL.
Driver-internal connection setup and transaction protocol traffic outside the
observed submission boundary are not claimed as captured queries.

### PostgreSQL Group A

The registry admits one datasource and one unversioned `md.table` Entity with
direct-column scalar `sum`, `count`, `min` and `max` Metrics. Admission examines
the complete logical and semantic dependency closure, including projected-away
Metrics, membership predicates and Metric slices. Supported operations are
Population membership, scoped observation, dimensions, Entity aggregation,
filtering, Metric projection, deterministic ranking and Top-N.

All required source columns must use Boolean, string, signed integer, float32,
float64, date, timestamp/timestamptz (precision 0–6) or Decimal types. PostgreSQL CHAR is rejected
because its trailing-space semantics differ from string; text/varchar remain supported. Explicit Decimal precision is
at most 38 and scale lies between zero and precision; a generic Decimal declaration
still requires compatible physical metadata. Temporal scopes use native date
columns. Parsed string time axes and source-private methods are not admitted by Group A. The relational/date extension above owns additional method admission. PostgreSQL receives
no retained-import capability.

The adapter uses read-only service-side cursor transactions and records actual
metadata, validation and output statements. It creates no remote temporary tables,
uploads or UDFs. Each query may read a different source state; no common snapshot
or implicit retry is added. Transport and cleanup failures preserve the original
error and cannot publish partial output.

### MySQL and SQLite Group A

Both backends admit the same single-source, single-unversioned-table Group A
closure: Population, scoped observations, direct-column sum/count/min/max,
dimensions, aggregation, filtering, projection, deterministic ranking and Top-N.
All dependencies must be admitted, including projected-away Metrics. Neither
backend imports retained Artifacts or enables source-private methods.
The relational/date extension above owns additional method admission.

MySQL admits tables and views with a SELECT-only account. Inputs include signed and unsigned
integers, float32/64, native DATE, `utf8mb4_0900_bin` VARCHAR/TEXT and explicit
Decimal precision up to 38. Explicit Boolean bindings accept TINYINT(1) only after
necessary-column 0/1/NULL checks; integer bindings remain integers. DATETIME(0–6)
and TIMESTAMP(0–6) preserve microseconds; TIMESTAMP requires an observed session time_zone of UTC or +00:00; other aliases remain unqualified.
CHAR, BIT, ENUM/SET, nested values and generic Decimal sources are not admitted.
Zero/invalid dates and timestamps fail before publication. Integer SUM is decoded
exactly from Decimal; the current result is int64, so values beyond its range fail.
Floating SUM overflow raises the original database exception.

SQLite accepts persistent ordinary main-database tables and views. INTEGER/INT/BIGINT,
TINYINT/SMALLINT/MEDIUMINT/INT2/INT8 map to int64; REAL/DOUBLE/DOUBLE PRECISION/FLOAT
to float64; TEXT/CLOB/CHAR/VARCHAR (including declared lengths) to string with
BINARY collation and actual text storage. DATE requires valid canonical YYYY-MM-DD
text. BOOL/BOOLEAN requires integer 0/1/NULL. DATETIME/TIMESTAMP requires fixed
`YYYY-MM-DD HH:MM:SS.ffffff` civil text, valid Gregorian years 0001–9999, without
an offset. Necessary-column storage checks run even when output is empty.
Unsigned, Decimal, native aware SQLite storage, virtual and attached tables remain excluded.
SQLite stores inserted NaN as NULL; that distinction cannot be recovered.
Native integer SUM overflow remains an error.

Ordinary timestamps support exact transport, grouping, sorting and typed predicates.
The native timestamp extension above owns temporal method admission; a declaration
alone does not establish a naive timestamp's read-timezone authority.
UInt64 identities, min/max and transport preserve the full unsigned range through
Arrow, Parquet and cold reads without floating or signed conversion.

Both adapters flatten internal Entity identity structs into typed scalar SQL
columns and rebuild the unchanged Arrow identity schema without string or float
encoding. Transport uses direct cursor fetches, not Ibis's pandas-buffered Arrow
path. MySQL uses an unbuffered SSCursor; early cursor close may drain unread
responses. SQLite uses its native incremental cursor. Batch size is transport
configuration, not a result or memory cap. Driver, database and runner limits
remain external; Marivo sets no execution timeout and promises no hard cancel
latency. Analysis does not reuse authoring timeout/transaction wrappers.

Use a SELECT-only MySQL account without write or object-creation privileges;
SQLite execution enables query-only mode. Cancellation targets only the owned
connection, including SQLite fetch execution. Connection close is not proof of
remote termination. Original failures survive cleanup errors, partial output is
never published, and locally safe recovery remains possible with unknown remote
status. Cold Artifact reads and exact binding hits do not access the source.

### Trino scalar Metrics

Trino Group A supports one datasource and one unversioned ordinary relation —
table or view — with direct-column `sum`, `count`, `min` and `max`, Population
filtering, native-date scopes, same-Entity dimensions, aggregation, projection,
rank and limit. Use the existing catalog/schema/table declaration; both tuple and
dotted catalog/schema overrides are resolved consistently for metadata and
executed SQL. The connector name and relation form are observation receipts, not
gates: any connector's ordinary relations enter, and only `$`-suffixed internal
tables are rejected.

Declared physical inputs are signed integers, float32/64,
VARCHAR, BOOLEAN, timestamp(0–6) without time zone, DATE and explicit Decimal
precision/scale up to 38. The observed Iceberg connector exposes timestamp DDL
with precision 0 or 3 as timestamp(6); bindings must match this observed precision. CHAR, generic Decimal source declarations, timezone-bearing
timestamps, precision above microseconds and nested values
are excluded. Declared floating columns must contain finite values or
NULL; source checks reject NaN/infinity even for empty output, and non-finite
aggregate results fail before publication. Decimal and integer identities retain
exact values. Ranking preserves explicit NULL order and deterministic ties.

Use a read-only identity with access to connector and table metadata. Ordinary
Ibis compilation feeds incremental driver page reads; internal identity structs
are reconstructed from typed scalar columns. Batches are not byte or memory
limits. Driver/server limits remain external. Active cursors are owned before
submission; cancellation and close failures report unknown remote status without
blocking safe local recovery. Validation and output can observe different source
states. Retained imports and source-private advanced methods remain unavailable.
The relational/date extension above owns additional method admission.

The original Slice 1d blanket restriction is superseded. Existing DuckDB
Event/Lifecycle, Candidate, JSON and retained-stream execution remain available.


`marivo.analysis` is the governed Dataset analysis surface. Import it as `mv`,
with `marivo.semantic as ms` for semantic identities and `marivo.datasource as md`
for datasource authoring. Analysis consumes declared meaning; the agent owns
business judgment, causal interpretation, and the choice of the next question.

## Construct, execute, inspect

A Session carries a guiding question and persistent project-local identity.
Source constructors describe work without reading source data or creating a Run.
A Logical Dataset has complete row meaning, owned fields, a bounded identity repr,
and `contract()`. Explicit `execute()` returns the paired Materialized Dataset.
Its `show()` and `to_pandas()` read retained values under Runtime guards.

```python
import marivo.analysis as mv
import marivo.semantic as ms

session = mv.session.get_or_create("revenue-review", report_timezone="UTC")
revenue = ms.ref.metric("sales.revenue")
current = session.observe(
    revenue, time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01")
).aggregate()
baseline = session.observe(
    revenue, time_scope=mv.time_scope(start="2026-06-01", end="2026-07-01")
).aggregate()
change = current.compare(baseline).execute()
change.show()
```

This example requires an authored, typed `sales.revenue` Metric with an admitted
reference time axis. The scopes are literal half-open intervals. An observation
normally retains Entity identity; `aggregate()` explicitly removes that axis.
A time series additionally declares `.with_time_axis(axis, grain=mv.grain("day"))`.

Construction may load the in-memory semantic catalog and certified project
snapshots. It does not resolve credentials, open source connections, query rows,
materialize results, or perform result reads. Mutable ambient source bindings are
captured during construction and are not reread at execution.

## Row meaning and ownership

Dataset families are Population, Metric, Delta, Attribution, Association,
Forecast, Candidate, Event and Lifecycle. Each admitted shape has paired Logical
and Materialized classes. Shape, schema, coordinate/key fields, row cardinality,
ordering, authority and definition identity are explicit immutable contracts.

Entity `primary_key` is stable identity K. Snapshot/validity coordinates describe
historical rows separately. Population selects membership; it does not silently
set an observation window. Cross-Session Dataset operands are rejected. Selectors
from `dataset.fields` belong to that exact Dataset, and cannot be borrowed from
another result with the same column name.

Methods transform definitions and return Logical Datasets. Applying a method to
a Materialized Dataset starts from its retained rows and private contribution
state; it does not replay the origin. Projection, filtering, ranking
and aggregation keep their distinct meanings. Native contract validation checks
whether the requested transition is admitted before source work.

## Exact execution and persistence

Runtime fixes the registered implementation and destination before executing.
Source execution supports DuckDB and admitted PostgreSQL, MySQL, SQLite, Trino and ClickHouse scalar/relational methods. The private method registry
selects one exact backend registration for the typed invocation, with full
source execution and preparation declared separately. Unsupported known inputs
fail before Run admission; exact same-Session binding hits remain source-free.
Retained Parquet can attach only to the existing DuckDB reader, never by an
implicit upload to another datasource. Source and execution ownership are
independent of diagnostic engine versions.
Projects retain results in local Parquet. There is no analysis result storage
setting, database result storage, automatic destination selection, or
failure-triggered executor retry.
Native DuckDB analysis of immutable retained Parquet is admitted by the owning
registered method; temporary execution relations are not persisted Artifacts.

Primary output and required part reads need not observe the same source state.
Each query uses its backend's current observation. Marivo neither opens a shared
consistency transaction nor rejects or retries solely because intervening updates
occurred. Artifact validations still apply; Event/Lifecycle may re-evaluate
deterministic source relations, including across concurrent writes. Independent
output queries may observe different versions; atomic publication does not
certify a common source snapshot.

One admitted execution creates a Run. Publication commits the primary result,
required private parts, descriptor, Evidence and Findings atomically. Cache hits
on the same exact realization do not invent another Run. Failed or interrupted
Runs never masquerade as successful Artifacts. Store generation 6 is required;
existing older generations are rejected without rewriting their files.

Local kernels and complete retained reads run synchronously in the calling Python
process. Marivo imposes no execution row/byte/cell, memory/spill, complexity,
storage or deadline budgets. Batch sizes tune transfer; they never truncate the
result. Database, driver, OS and external runner limits remain independent.
Original exceptions retain their causes and tracebacks, including when cleanup
also fails. Complete inputs, semantic validation and atomic publication remain
mandatory. `show()` keeps its display bounds; `to_pandas()` returns a complete
isolated copy. Exact distinct membership and quantile methods retain all state
required for valid computation and cold recovery.

Ibis expressions may compile during normal execution. Correct parameters,
complete typed results, required validation order and method-owned single
evaluation remain mandatory; repeated pure compilation is not a duplicate query.
Engine, driver and Ibis versions do not gate execution or define source domains.
Remote read termination uncertainty is disclosed without blocking safe local
recovery; unresolved publication ownership and storage integrity still block.

## Disclosure and interpretation

Start at `marivo.help("analysis")`. Its bounded hubs route to source entry,
methods, inputs, artifacts, evidence and runtime. Native Help owns exact callable
signatures, constraints and executable examples. Dataset `contract()` owns
mechanical continuations and static Metric/Population source admission, including
the public call and exact Help target for continuations;
structured errors preserve concrete diagnostics and own repair. Packaged skills
own workflow decisions without duplicating signatures or private implementation
inventories. Entry links to Session bootstrap/recovery and Metric, Event and
Lifecycle sources. Inputs link to the scoped catalog and named input groups;
methods group analytical intents. An agent follows only the selected branch and
its prerequisite/result links, then writes and executes the minimum useful chain.
Type/member leaves remain independently queryable without flooding task discovery.

Algebraic attribution does not establish cause. Association is descriptive;
Candidate scores do not confirm an anomaly or prescribe action. Forecasts are
model outputs under explicit assumptions. Evidence records facts and derivation,
not the agent's narrative conclusion. In the shipped Dataset surface, custom
work through `to_pandas()` or `md.raw_sql(...)` is terminal and cannot re-enter
governed analysis. The R0.3 target retains the latter as the explicit public
SQL escape hatch outside Analysis.

## Owning contracts

- [Dataset methods and states](operators-and-frames.md)
- [Session and Runtime](session-state-and-runtime.md)
- [Evidence reads](evidence-access-surface.md)
- [Timezones and calendars](timezone-and-calendar-design.md)
- [Temporal authoring](../temporal-semantics.md)
- [Detailed observation contract](../../superpowers/specs/2026-09-01-lazy-analysis-observation-model-design.md)
- [Detailed materialization contract](../../superpowers/specs/2026-09-01-lazy-analysis-materialization-runtime-design.md)

## Proposed extensions

- [Multi-datasource lazy execution design and implementation plan](../../superpowers/specs/2026-09-15-lazy-analysis-multi-datasource-design-and-plan.md)
  describes staged backend qualification. It does not enable additional source
  execution backends or change the current contracts above.

### ClickHouse scalar Metrics

ClickHouse Group A admits one datasource and one unversioned ordinary relation —
table or view — under any engine: direct-column sum/count/min/max, Population
filters, native-date scopes, same-Entity dimensions, aggregation, projection,
deterministic rank and limit. Distributed and multi-shard relations are accepted,
as are unstable reads: engines whose reads depend on background merge state (for
example ReplacingMergeTree) can return different rows between runs as merges
progress; replay of a committed snapshot is unaffected. Sampling, retained import
and source-private advanced methods remain unavailable. The relational/native-date
extension owns additional method admission.

Physical inputs include Int8/16/32/64, UInt8/16/32/64, Bool, Float32/64, String,
Date and explicit Decimal precision up to 38, with legal Nullable wrappers.
LowCardinality(String) and LowCardinality(Nullable(String)) retain string semantics.
DateTime('UTC') and DateTime with verified UTC engine timezone preserve seconds.
DateTime64 through microseconds and aware non-UTC bindings are admitted by the
native timestamp extension. FixedString, Date32, Int128/256, UInt128/256, Enum
and nested values remain excluded. Engine form is not restricted by this
scalar-type extension.

Use a SELECT-only account configured with effective `join_use_nulls=1`.
Metadata, remaining required assertions and output run separately; empty output never
bypasses Artifact validation. Source identity and time data are trusted. Exact
integer/Decimal sums widen internally to Decimal256 before checked output
conversion; overflow and non-finite output fail without publication.

Native row streams preserve Decimal and typed Entity identities without pandas
or raw source transfer to another engine. Each response is owned and closed;
driver close can drain unread data, so cancellation has no hard latency promise.
Connection close does not prove remote termination. Unknown remote status does
not prevent safe local recovery; partial output is never published. No shared
snapshot, execution budget, upload, temporary object or implicit retry is added.
Batch size is transport configuration, not a result cap.
