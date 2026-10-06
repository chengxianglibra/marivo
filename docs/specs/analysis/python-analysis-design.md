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
resolution follows graph edges to the originating logical input binding rather than
matching equal definition fingerprints. Each originating check retains its ordered
input tuple. Shared paths deduplicate that tuple, while independent realizations
with equal symbolic obligations remain separate checks; a repeated input within
one pairing still occupies both ordered positions. A checker returning no rows is the
success condition, not an already-established fact in the lowered graph.
Every required check must succeed at its registered consume/publish deadline.
A source check proves only the query that performed that check; later native
calculation may read a different source version and is not certified by the earlier
read. Local and fixed consumers still validate their actual captured or receipt-bound
inputs before consumption. Publication requires all inherited publication checks
and the final Artifact's internal key, Cell, required-part and arithmetic validation.
R4 owns evaluating checks, rendering concrete failures, and recording each query's
evidence in one Run.

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
binding fingerprint)`. It is captured before the business-row source read; the Run ref
is the fresh evaluation identity, so the same Lazy value cannot hit an earlier
source result. The semantic dependency digest is an independent frozen input,
not an alias for the source definition fingerprint; the opened R1 source proves
the selected physical binding. Each fixed input occurrence is `(session_ref, artifact_ref,
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

Pure classification and declaration-level capability checks precede the writer
guard, schema preflight, Artifact row read and Run allocation. Mixed live/fixed roots, foreign
Session inputs, unmatched ordered comparison/member bindings and unavailable
backend/source forms reject there. For source-only graphs whose Entity key or
field type is unknown in Semantic metadata, R4.5 may open the selected R1
source for schema only before allocating a Run. This preflight submits no
business-row query, fixes exact physical qualifications, and must be checked
against the bindings opened for execution; changed schemas reject without
fallback. The guard then reconciles the exact unfinished Run. Source-only
allocates a Run and new key before first business-row read; fixed-only
validates a hit before allocating a Run. R4 consumes the
unchanged emitted Ibis expression with its recorded `source_ids`, fulfills
each bound check before its consume/publish deadline, and records completed
evidence from this invocation. No failed check, type mismatch, cancellation
or implementation error changes the selected route or starts a new identity.
R4.3 owns the common source/Parquet/pandas Arrow exchange; R4.4 owns the v7
Store/descriptor/receipt switch; R4.5 owns exact source-free recovery. Their
frozen metadata and failure boundaries are in
[Session State and Runtime](session-state-and-runtime.md#r41-frozen-runtime-and-store-target).

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
The R4.5 qualifications cover string member reads and grouping,
windowed direct sum (explicit Null or zero empty policy) and Entity count,
one- or two-hop to-one contribution paths, ordered absolute Difference, and
same-member Spearman. Original ratios merge independent sum-zero and count
components before division. A zero denominator yields Undefined, including
when both original components are zero. Whole-domain and retained-coordinate
rollups preserve these components. DuckDB table and local Parquet paths have
separate evidence; checked fixed Artifacts use the registered local consumers.
Fixed selection, original rollup and current-row sum/mean/count/count-defined
can share a multi-node schedule with one Run and one final publication.
Public J1–J4 evidence is recorded separately in the R4.5 acceptance ledger. A fixed
leaf uses the frozen signature with pending obligations discharged only after
the Store has validated the exact completed-check evidence and receipts.
An exact fixed predicate on a singleton coefficient may yield zero or one row;
the closed `optional_singleton` row-set contract is recorded in the descriptor
and enforced again on receipt reads.

The fixed route verifies its selected primary and required part receipts before
the pandas method reads rows. It cannot attach an Artifact to DuckDB or a
remote backend. A private result is transient and grants no Run, Store,
publication, cache-hit or recovery authority. R4.4 owns its durable encoding;
R4.5 owns the public cutover.

R9.4 additionally qualifies the existing local CSV and unparameterized GET JSON
R1 schema bindings for `parts_transport` (string Entity identity, NoTime or UTC/us)
and `metric.sum_zero` (string Entity identity, UTC/us, ordinary int64 measure).
The lowering verifies the exact source descriptor kind and uses its existing
Ibis relation. Local schema inference is permitted metadata, not business-result
proof. HTTP/parameterized JSON remains refused before source open; admission
never fetches remote JSON merely to infer a schema.

R9.6 connects the existing `state_rollup.sum_zero@v1` local consumer for
DuckDB native-table int64 Entity/group observations with UTC microsecond time. A
local producer such as `values.deviation(method="zscore").observed` can reduce
its original sum state with `rollup()`.

The R9.6 file-cost closure additionally connects original int64 sum-zero
Entity rollup for local CSV/JSON on `ibis`, and local CSV/JSON/Parquet on
`ibis_python`, with UTC microsecond observation time. Local CSV/JSON zscore
fits an int64 Entity NoTime input; its existing field reader consumes the
float64 fit carrier while the observed view preserves the original int64
state. These exact file keys cannot specialize to
other scalar types, MAD, mean, group domains, local difference or attribution.
Existing Parquet statistical qualifications retain their separate scope. No
new arithmetic, file reader, source snapshot, public symbol or HTTP admission
is introduced; source preparation remains complete before local consumption.

The verified private source prefix is DuckDB native table or Parquet, `NoTime`,
complete int64 identity, and exact registered `bind_project`,
`parts_transport`, `map_correspond`, `row.count`, `row.count_defined`,
`row.sum`, and `row.mean` variants. The source Spearman owner additionally
qualifies ordered int64/float64 Endpoint pairs through both numerical Ibis and
Ibis preparation followed by Python. Fixed receipt execution qualifies
`row.count`, `row.count_defined`, int64 `row.sum`/`row.mean`, and ordered int64/float64 Spearman
pairs. Only fixed `row.count` retains the R3.4 100,000-row limit; other
complete-input algorithms have no implicit row cap or sample. Mean requires
exactly representable int64 operands. R4.5 adds the exact qualifications listed
above and in the public execution section. Other reduction, comparison/ratio
variants, Decimal, unqualified temporal shapes and other backends remain
unavailable before business reads; legacy execution results do not qualify
them. Private results do not publish a new protocol Artifact or authorize
product continuation K.

### Relative Anchor observation and retention (C18)

The single public entry is `session.anchors`, with the exact source/fixed
overloads in [the R7.1 API target](#r71-frozen-domain-api-target).
Event-role source construction takes an explicit `AnalysisDomain` population,
`during: TimeScope`, and `business_order: Ref[BusinessOrderKind] | None = None`.
Journey construction inherits its retained order and has no override parameter.
The source is a closed union of an exact `ParticipantRoleHandle` on an Event and
an exact Logical or Materialized Journey start view. The former uses the Event occurrence key;
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

The named-role `mv.statistical_weight` and dependent current-row weighted mean
are withdrawn proposals, not R5 implementation requirements. The user withdrew
their Semantic declaration in R2.2 and reaffirmed that boundary during R5.1.
A private `StatisticalWeightPart` or `row.weighted_mean` registration does not
authorize a public surface. Reconsideration needs a separate scope decision;
there is no automatic R6 reactivation. Metric-component weighted mean is
unaffected. The withdrawn constructor signature is not retained as an active
or deferred implementation contract here.

The independent reference-weight/standardization target remains owned by R6:
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
business value. The bounded R9.3 fixed Journey ratio consumer accepts homogeneous
microsecond Duration operands without a time axis. It retains the complete
Journey key, endpoint values and zero-denominator status, including cold
recovery. Other Duration units and source Journey ratio keys remain unqualified.

R9.3 also connects native SQLite main-database table preparation for int64 Event
and Subject identities with UTC microsecond timestamps. The driver backup API
copies the complete main database to a private temporary file before business
reads; prepared reads use that same frozen connection. Copy checkpoints and
native query interruption share the execution deadline. Success and failure
restore the original connection and delete the temporary backup. This copy is
not a bounded-window scan and may include unrelated main-database pages. Attached
databases, other key/time shapes and other source backends remain unqualified.
Only occurrence preparation and Journey matching gain this source declaration;
other capability families do not inherit it. Fixed Journey views and ratios
continue through their existing retained consumers.

Endpoint identity, pairing, units, status and coverage remain
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
R9.4 additionally qualifies fixed `NoTime` Entity `Decimal(18,2)` current-row
sum and mean: sum and its state are `Decimal(38,2)`, while mean finishes once
to `Decimal(38,6)` with HALF_EVEN from exact sum/int64 count. The resulting
singleton statistics can roll up their captured state; mean keeps the original
sum scale 2 even though its displayed scale is 6. Empty sum is defined zero;
empty mean is Undefined(empty_mean). This adds neither source row reducers nor
Decimal group/time keys. The original quantile remains nonadditive.
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
before creating a Run or opening the source. Retired R5 routes point to
`session.members(...).observe(...)`; remaining domain routes identify their
R6–R8 migration stage. The R1.1 acceptance record tracks the remaining concrete legacy text
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
Dataset path described above. Existing Event, Attribution, mean,
time-scoped and other older source methods still reject at `source_admission`;
their prior J1–J4 or Group A evidence does not qualify them under this route.
Fixed Artifact continuation retains its separately admitted path. The R1
acceptance record names remaining old text consumers and their R5–R9 owners.
Datasource inspection and connectivity establish physical facts only, not
method admission.

## R4.5 public graph execution and recovery

The former S1–S3 scenario builders, executors and J1 exchange codecs have been
replaced. `session.members(...)` and every existing public relation hold a
`core.graph` definition. Their `execute()` methods enter the same
`DatasetRuntime._execute_graph` scheduler. Public Session creation, resume,
current/history reads and Artifact recovery select Store 7 only; old-generation
projects remain untouched and are rejected rather than migrated or dual-read.

R1 schema-only preflight may open a qualified source before Run allocation to
resolve unknown Entity keys and exact value types. It submits no business rows.
Business source opening and reads follow physical qualification and Run admission;
the Runtime verifies the preflight schema again. Unsupported backends, physical
types, mixed source/fixed graphs and R5–R9 Dataset families reject before business
reads and Run allocation. Existing signatures remain; those families have no
Store 7 execution qualification yet.

The qualified J1–J4 routes include DuckDB native tables and local Parquet sources,
string/int64 Entity identity, direct string member reads/grouping, UTC microsecond
event windows, direct int64/float64 sums, Entity count, one- or two-hop to-one
paths, ordered absolute comparison (homogeneous int64/float64, same-scale
Decimal, or same-unit Duration), original int64 sum-zero/count ratios, and
same-member Spearman. The widened comparison types are bounded R6.2 incremental
evidence; they do not enlarge the historical R4.5 J1–J4 acceptance record.
Original ratios keep independent component roots. With
contribution coordinates, the complete row key is member plus the ordered string
coordinate tuple (at most two). Missing numerator contributions are zero; a zero
denominator is Undefined. Whole and coordinate rollup merge original components;
current-row statistics operate on the actual rows, so their mean can differ.

Two independently published endpoints may combine only when their frozen
observation definitions retain the same explicit member node. Equal values or
equal-looking independently constructed selections do not establish this binding.
Comparison also requires matching Metric, path and coordinates and distinct
windows; Spearman requires matching windows and one observation per member.
Ordered endpoint occurrences are retained in exact keys and parts. Shared nodes
are evaluated once in one top-level Run with only the final result published.

`session.artifact(ref)` validates the exact v7 descriptor, continuation snapshot,
method version, completed-check evidence, primary receipt and all required parts.
It restores the existing public result class. Its repr, show and contract inspect
verified state; fixed continuations use the checked Parquet/Arrow/pandas route
without current Semantic loading or a source/DuckDB connection. Corrupt, missing
or rebound metadata/parts cannot provide dynamic continuation K or an exact hit.
No legacy source definition-only cache remains on an executable source path.
The private generic v6 Dataset harness remains isolated for later-family tests;
it is not selected or recovered by any public Session entry.

## S4 P1 public admission

The first public slice admits only the J1–J4 shapes in the MVP validation
plan. `session.members(Ref[EntityKind])` returns a logical AnalysisDomain;
`read(Ref[DimensionKind])` returns a CategoryRelation; and `observe` accepts
one `Ref[MetricKind]`, an explicit fixed `TimeScope`, one relationship or the
closed two-root `routes(route(...), route(...))` value, and optional declared
contribution coordinates. Domain and relation methods return concrete logical
variants. `execute()` exists only on logical values; `show()` and
`to_pandas()` exist only on materialized values. Both expose `contract()`.
`session.artifact(reference)` returns an exact materialized variant, with its existing public return annotation retained; unqualified families are not recovered through v6.
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
retain their signatures but reject execution until their Store 7 qualification. The public cutover must keep live Help, API docstrings, export
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
| `members.observe(metric, during=window, via=relationship)` | `LogicalNumericRelation` | `MaterializedNumericRelation`; `group_by` returns `GroupedNumericRelation`, `rollup` returns `LogicalRolledNumericRelation` / `MaterializedRolledNumericRelation` | original sum/non-null-count, coverage and optional coordinate state |
| `members.observe(metric, during=window, via=mv.routes(...), coordinates=(...))` | `LogicalRatioRelation` | `MaterializedRatioRelation`; `group_by` returns `GroupedRatioRelation`, `rollup` returns `LogicalRolledRatioRelation` / `MaterializedRolledRatioRelation` | original numerator sum/non-null-count, denominator count, coverage and optional coordinate state |
| `observed.compare(baseline)`, then `where(diff.value.lt(threshold))` | `LogicalDifferenceRelation`, `LogicalSelectedDifferenceRelation` | matching Difference variants; selected `members()` projects identity | exact ordered current and baseline endpoints |
| `relation.summarize(mv.sum/count/mean())` | `LogicalStatisticRelation` | terminal `MaterializedStatisticRelation` | current-row method and Cell checks; no original-state rollup |
| `observed.correlate(other, method="spearman")` | `LogicalAssociationResult` | `MaterializedAssociationResult`, then fixed `MaterializedCoefficientRelation` | paired observation state, pair counts and exact member binding |
| `coefficient.where(coefficient.value.lt(threshold))` | `LogicalCoefficientSelectionRelation` | `MaterializedCoefficientSelectionRelation` | retained pair counts and coefficient policy |

`mv.route(root, *, through=(...))` and `mv.routes(*items)` are
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
| `session.population(...)` and its Dataset family methods | Retained for Population, Event and other shapes outside the admitted Entity-domain chain; it is not a J1–J4 synonym. |
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

The source graph's ordinary native-table UTC-microsecond temporal fold route
accepts string and int64 Subject identities. Int64 Subject transport preserves
adjacent identities above `2**53`; native mean recovery retains each Subject's
ordered samples and spatial support before applying the time fold. This carrier
qualification does not add a fold kind, view/Distributed shape, timestamp unit,
or implicit source-after-local route. Fixed recovery consumes those original
samples rather than averaging the displayed Subject values.

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
Statistical graph methods consume complete source-prepared
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
and remote `duckdb_tdigest@v1` remain unqualified. The legacy remote Association Dataset routes are retired. The current statistical
graph routes retain separate R9 qualification obligations. R7's public Journey,
funnel, History, Anchor and retention producers use the unified graph/method
registry on DuckDB native tables and local Parquet. The old PostgreSQL,
ClickHouse and Trino Event packet/CTE routes and their dedicated reducers,
coverage/codec/publication consumers are retired in R7.9. Their historical
acceptance does not qualify the current graph. Remote Event/History methods
remain R9 requirements. Fixed continuations consume only their retained parts;
starts-only Anchors cannot acquire missing return-event or Metric inputs.
The former Entity Candidate and driver-screening routes are retired; sampling
remains unsupported on these backends. Remote
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
The bounded DuckDB native-table microsecond/string-Subject route also qualifies
`metric.observe` and int64 `state_rollup` under Asia/Tokyo report authority,
including native DATE and explicitly parsed native timestamp inputs. Source
and fixed reductions retain the same grid/window facts. Comparing report zones
uses the same aware scope endpoints; civil scope literals intentionally change
their instant boundaries with report authority. Other method/type/shape keys
retain their own qualification.

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
JSON and retained-stream execution retain their shared owners. Journey and History
use Store 7; the legacy Event and Candidate chains are physically retired.


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

Remaining legacy Dataset families are Population and Metric. Deviation, runs,
association and forecast use their typed graph Result families. R7 Journey, funnel, History, Anchor and retention use the typed graph.
R6 uses typed Relation/Difference/AttributionResult variants. Each admitted shape has paired Logical
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

R9.3 adds a bounded SQLite ordinary-table association preparation consumer for
two int64 entity inputs on a microsecond UTC event axis: Pearson, Spearman and
Kendall, plus coefficient/selected reads of their grouped output. Complete
composite string/int64 identities are preserved, including length-qualified
Ibis strings. This registration does not qualify float/Decimal associations,
multi-input associations, other source shapes, forecast or runs on SQLite.

PostgreSQL ordinary tables also admit this exact two-int64 entity-pair route.
The source prefix binds/projections and observations use governed Ibis reads.
PostgreSQL integral Decimal integer-result carriers are decoded only when finite,
integral and within the Arrow integer range. Float conversion is never used.

MySQL ordinary tables admit the same two-int64 entity-pair consumers. The
provider records successful Ibis UTC reader initialization and fails explicitly
if that initialization warns. UTC-to-UTC rendering uses an identity timestamp
cast with microsecond precision rather than a foreign timezone function.
The exact adapter accepts integral Decimal integer results within range, Boolean
0/1 carriers and naive/text UTC carriers only under explicit UTC Arrow schema.

Trino ordinary Iceberg tables and ClickHouse local MergeTree tables also admit
the same two-int64 pair route. ClickHouse physical UTC timestamp axes use the
identity render; no server timezone is inferred from the driver's UTC fallback.
An axis requiring implicit reader timezone remains rejected when that fact is
unavailable. These bounded consumers do not qualify other numeric types,
distributed profiles, cancellation, or complete R9.3 scenarios.

All six ordinary-table backends also admit zscore and MAD over the complete int64 Entity observation
prepared by this source prefix. The fit preserves original full-width integers
and retains `fit_inputs`/`fit_state`; score views use the same retained fit.
This exact registration does not admit float/Decimal source fits, additional
input shapes.

All six ordinary-table backends also admit complete-grid runs over int64 Entity
observations and count reads, plus naive/drift/seasonal-naive forecasts over
complete int64 time-group training and prediction reads on microsecond UTC grids.
Explicit empty-contribution zero policy supplies Defined empty observations;
null/unavailable history is not imputed. These exact consumers do not qualify
other source numeric types, grid zones/precision, or resource cancellation.

Every graph execution creates one 600-second monotonic budget at entry, including
ordinary source reads, current-row statistics and fixed-input continuation.
An enclosing execution budget is inherited without restarting its clock. Expiry
before native submission or publication refuses the result, preserves previous
artifacts and releases owned resources. Durable publication keeps its existing
commit/acknowledgement reconciliation boundary.

Ordinary statistical source execution uses the shared execute deadline during
native submission and batch consumption. DuckDB/SQLite native interruption does
not require a domain capture. The timer requests interruption; the execution
thread closes cursors and releases the connection. Unconfirmed connection release
prevents publication. Expiry reports a structured timeout; remote termination
remains unknown without independent server proof.
PostgreSQL uses its connection's native cancellation request at expiry. The
Runtime still reports remote termination as unknown; server cancellation status
and session release require an independent observer for qualification.
Trino registers its native cursor before `execute()` and cancels it at expiry,
including while waiting for the initial response or fetching batches. The timer
retries cancellation until the owner releases the cursor because the driver's
first cancellation can precede the initial HTTP response. Independent server
query status must confirm cancellation; client close alone grants no proof.
During fetching, a batch checkpoint can detect expiry before the timer callback
runs. Owner-thread cursor cleanup then invokes native cancellation; evidence must
record this as `owner_checkpoint`, including the observed native cancel call and
independent server termination, rather than claiming timer-thread interruption.

ClickHouse statistical reads pass the current execute deadline's remaining
seconds through native HTTP `max_execution_time`, with
`timeout_before_checking_execution_speed=0` and throwing timeout overflow. Each
read uses the original monotonic start; no client-global settings are mutated.
The read-only account must permit the two timeout settings and retain or permit
`timeout_overflow_mode='throw'`. Missing or locked settings reject before the
business read, with a structured datasource repair. The provider does not issue
control SQL or reuse authoring timeout wrappers. Native query-log timeout status
and absence from active queries must be independently observed for qualification;
client cleanup alone grants no remote termination proof. Local MergeTree proof
does not qualify Distributed shards or unrelated profiles. Native timeout
may precede Python monotonic expiry. Verification accepts only native timeout
code 159 or the structured execute timeout, and still requires the exact native
settings, query identity/SQL, server log and resource release. Client elapsed time
is observed separately from the server's clock; it is not a synchronized lower
bound for native timeout.

ClickHouse keeps the physical UTC timestamp type during UTC identity rendering,
so Ibis labels boundary literals UTC even under non-UTC session settings. A naive
timestamp cast would adopt the session timezone and is not used for this route.

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
occurred. Artifact validations still apply; Event may re-evaluate
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
Deviation scores and run intervals do not confirm a cause or prescribe action. Forecasts are
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


### R4.4 private graph publication boundary

`DatasetRuntime._execute_graph` consumes the existing private graph and method
registry in an explicitly selected v7 Store. It performs pure admission before
writer exclusion, source opening, fixed-row reads or Run allocation. Publication
uses generic descriptor/receipt/snapshot codecs and the existing Store's atomic
transaction and resource journal. It never calls `execute_j1` or a family writer.

Persistent qualification is limited to the R4.3 consumers: stateless qualified
source binding/transport/mapping outputs with no retained parts, registered
source row statistics and Spearman, and one registered fixed row-statistic or
Spearman method over exact Entity inputs. A stateless output with parts, a nested
fixed execution, an unsupported state kind, or an unqualified physical input
rejects; serialization does not qualify a new numerical method. In particular,
singleton row-statistic outputs do not acquire Entity-input continuation rights.
Distinct captured Artifacts have no qualified shared-member witness in this
slice: fixed Spearman rejects them before Artifact metadata or row reads. A
shared exact Artifact may occupy both ordered operand slots and is read once.
The fixed path uses verified Arrow/Parquet data followed by the existing local
pandas consumer, without DuckDB or sources.

Cold protocol reads validate the frozen graph, method implementations, completed
check identities, exact Run/key binding and every required receipt. This does
not expose public construction or K. R4.5 owns public receiver migration,
`session.artifact`, disclosure and deletion of the still-used v6 scenario chain.

## R5.1 frozen public target

Status: target contract frozen, implementation and new physical qualifications
unverified. This section owns C03-C06/C10 consuming signatures and supersedes
conflicting historical Population/Metric Dataset shapes for the R5 cutover.
It does not change currently importable APIs or R4's measured qualifications.
The [R5 migration ledger](../../superpowers/specs/2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md)
owns migration work and evidence, not another API registry.

### R5 public variants and input types

The names below denote concrete target variants, with Logical/Materialized
pairs and existing family protocols. Documentation unions are closed type
aliases, not top-level exports or Help entries. NumericRelation includes the
numeric, ratio and row-statistic families; CategoryRelation, TemporalRelation
and BooleanRelation remain separate families. Materialized input operations
construct Logical fixed continuations; they never execute eagerly.

| Receiver and exact target call | Result and binding |
| --- | --- |
| `Session.members(entity: Ref[EntityKind], *, at: datetime \| BeforeEndBoundary \| None = None)` | `LogicalAnalysisDomain`; no-version requires None, versioned requires one explicit anchor |
| `AnalysisDomain.read(field: Ref[MeasureKind], *, at: datetime \| BeforeEndBoundary \| None = None, via: Ref[RelationshipKind] \| RootRoutes \| None = None)` | `LogicalNumericRelation` |
| `AnalysisDomain.read(field: Ref[DimensionKind], *, at: datetime \| BeforeEndBoundary \| None = None, via: Ref[RelationshipKind] \| RootRoutes \| None = None)` | `LogicalCategoryRelation \| LogicalBooleanRelation`, dispatched by resolved categorical/boolean kind |
| `AnalysisDomain.read(field: Ref[TimeDimensionKind], *, at: datetime \| BeforeEndBoundary \| None = None, via: Ref[RelationshipKind] \| RootRoutes \| None = None)` | `LogicalTemporalRelation`, preserving civil date versus instant and physical precision |
| `AnalysisDomain.each(grid: TimeGrid)` | `LogicalTimeAnalysisDomain`, the bounded member/time product |
| `AnalysisDomain.group_by(*keys: Ref[DimensionKind] \| CategoryRelation, groups: AnalysisDomain \| GroupedAnalysisDomain \| None = None)` | `GroupedAnalysisDomain`; a no-key group denotes Singleton |
| `NumericRelation.group_by(*keys: Ref[EntityKind] \| Ref[DimensionKind] \| CategoryRelation \| TimeGrid \| Grain, groups: AnalysisDomain \| GroupedAnalysisDomain \| TimeAnalysisDomain \| None = None)` | `GroupedNumericRelation` or `GroupedRatioRelation`, according to quantity; no eager rows |
| `NumericRelation.rollup()` or the corresponding grouped call | Logical relation preserving the original quantity family, only with admitted original state |
| `NumericRelation.summarize(method: RowMethod)` or the corresponding grouped call | `LogicalStatisticRelation`; a new current-row quantity |
| `CategoryRelation/TemporalRelation/BooleanRelation.summarize(method: CountMethod)` | `LogicalStatisticRelation`; CountMethod is the closed count/count_defined subset, without numeric conversion |
| `Relation.where(predicate: ValuePredicate)` | The selected variant of that relation; a typed predicate and its explicit relation dependencies, no string/callback |
| `Relation.members()` | `LogicalAnalysisDomain` for source dependencies or `LogicalFixedAnalysisDomain` for fixed dependencies; requires an existing Subject mapping |

A Temporal/Boolean read is a new member of the existing Relation protocol, not
an alternative authoring kind or general expression API. A Dimension Ref alone
cannot statically distinguish its resolved boolean kind, so read has an explicit
closed return union; neither typing nor runtime may pretend it always returns
CategoryRelation. Wrong Session, incompatible phase, ambiguous kind/path or a
multivalued read rejects at the earliest known boundary. Scalar read requires
one value per complete target identity, including checked coverage of that
consumer's domain. Historical read requires explicit `at`; no implicit latest.
Current-row summaries of a versioned read retain that exact version selection
in their output domain, including after selection and fixed recovery.
The shorthand member-domain `group_by(OwnDimension)` may inherit the uniquely
bound member version as its explicit property dependency. It cannot search for
cross-Entity attributes or inherit an observation window.

The observe input alias is exactly
`MetricInputValue = Ref[MetricKind] | RuntimeMetricExpr`;
`public_dsl.py` owns this union. Private legacy observation consumers do not expand
the public input contract. Each member, time-member and grouped-domain receiver offers these
mutually exclusive forms:

- `observe(metric: MetricInput, *, during: TimeScope | GridWindow | None = None,
  via: Ref[RelationshipKind] | RootRoutes | None = None,
  coordinates: tuple[Ref[DimensionKind], ...] = (),
  time_dimension: Ref[TimeDimensionKind] | None = None)`.
- `observe(metric: MetricInput, *, at: datetime | BeforeEndBoundary | GridEndpoint,
  via: Ref[RelationshipKind] | RootRoutes | None = None,
  coordinates: tuple[Ref[DimensionKind], ...] = (),
  time_dimension: Ref[TimeDimensionKind] | None = None)`.

There is no overload accepting both at and during. Result dispatch follows the
resolved quantity, not the Python type of via: ratio returns LogicalRatioRelation;
other numeric quantities return LogicalNumericRelation; pre-grouped receivers
return the matching Grouped relation. A grouped observe binds its member mapping
but defers reduction to rollup. Omitted during means no added time restriction,
not all-history completeness. Endpoint/status/cumulative methods reject a missing
required time argument. A fixed member list does not fix external Metric or
attribute sources; such mixed dependency construction rejects before business I/O
or Run creation. A new source observation starts from a Logical source member
binding; fixed continuation only consumes already retained values and parts.

`mv.route(root: Ref[EntityKind], *, through: tuple[Ref[RelationshipKind], ...])
-> RootRoute` and `mv.routes(*items: RootRoute) -> RootRoutes` remain the only
explicit route shape; `RootRoutes` accepts one to sixteen ordered routes.
Identity or a unique definition-bound route permits None; reachability/shortest-path
guesses do not. A route binds one **distinct contribution root**, not one
component occurrence: every distinct computation root of the resolved input has
exactly one route, and several occurrences that share a root share that one
route while keeping their own branch filters. Extra, duplicate and missing roots
reject; routes carry distinct roots in the order the roots are first observed. A
single route suffices for a single-root input. Same-root occurrences requiring
incompatible roles reject with their occurrence identities: R5 does not add an
occurrence-browser API. Coordinate paths must be uniquely authorized by that
bound graph; no fanout switch or generic join is introduced.

Each occurrence reduces independently within its own branch filter, route and
time range before any combination. Occurrences are combined on the complete
target key; a shared contribution table is never multiplied or summed across
branches first. Combination never falls back to per-column projection products,
root intersections, or row-order alignment.

R9.3 additionally connects exact native-table UTC-us, two-root entity float64
`metric.linear` keys on SQLite, PostgreSQL, MySQL, Trino Iceberg and ClickHouse. Each contribution
root reduces independently before complete target-key combination. PostgreSQL,
MySQL, Trino Iceberg and ClickHouse also connect Decimal(38,6) components reduced from native
Decimal(18,6) facts, preserving Decimal values as DuckDB does. Other Decimal
precision/scale keys are not inferred. SQLite non-finite source values reject and
leave no published result; its Decimal linear composition remains unqualified.
No float coercion or alternate route is used. These bounded vectors do not grant
complete C04 qualification.

R9.3 also connects native-table UTC-us Runtime ratio observations with int64
components and weighted means over int64 facts for string-key Entity targets on
SQLite, PostgreSQL, MySQL, Trino Iceberg and ClickHouse. Ratio components reduce
on their own contribution roots; zero-denominator targets remain Undefined and
zero numerators remain Defined zero when the denominator contributes. Weighted
means retain empty-contribution targets as Null. Pair support is counted through
integer CASE expressions, including PostgreSQL where Boolean-to-BIGINT casts are
unsupported. These bounded keys do not infer additional ratio numeric families
or complete C04 qualification.

The exact native-table UTC-us, two-root Entity float64 ratio key is also
connected on all six backends. Dyadic component facts preserve original sums
3.75 and 4.75 for fixed rollup (15/19), rather than averaging target ratios.
DuckDB, PostgreSQL, MySQL and ClickHouse additionally retain Decimal(38,6) component sums from Decimal(18,6)
facts and finishes the merged 10/3 ratio once as Decimal(38,6), 3.333333, using
HALF_EVEN. PostgreSQL uses native numeric div/mod and unbounded numeric scaling
in Ibis, avoiding per-digit nested expressions and preserving coefficients
beyond 38 digits before finishing. Independent positive/negative ties and extreme
coefficient vectors verify Decimal output. MySQL uses native Decimal quotient
arithmetic after subtracting the exact remainder, with scaling bounded by its
65-digit capacity. Final coefficient overflow becomes an invalid defined/null
Cell and is refused by the existing pre-publication Cell check, rather than
publishing a clamped Decimal cast. Original sum overflow is independently refused
by exact Arrow decoding on MySQL. ClickHouse uses native Decimal256 division at
scale zero and exact scaled coefficients before the single HALF_EVEN finish.
Its Decimal sum/mean observations aggregate in Decimal256 and check the declared
component bounds before publication, preventing Decimal128 sum wraparound.
Original-component and final-ratio overflow both refuse without publication.
SQLite and Trino
Decimal ratio keys remain unqualified and reject
without publication; refusal controls do not prove required Decimal execution.
The float keys cannot specialize into Decimal keys.

For the bounded int64 ratio vector, fixed rollup merges the original 30/9
components to 10/3, rather than averaging target ratios 6 and 0 to 3. All six
source producers preserve these components for continuation with source batch
reads forbidden and no additional native submission. The empty target's
Undefined zero-denominator Cell does not replace its retained zero components.

Native-table UTC-us int64 Runtime aggregate and dimension-slice observations also
have bounded source vectors on all six backends. Runtime sum retains an empty
target as Null(empty_contribution); slicing a static sum with an explicit zero
policy retains excluded targets as Defined zero. The slice is applied before
reduction. These vectors verify complete string-key Entity targets, actual native
submissions, retained parts and connection release; they do not grant other
aggregate functions, slice compositions or complete C04 qualification.

### Runtime expression signatures

The five existing factories remain under `mv.runtime_metric`; the semantic
module remains their implementation owner. The following aliases are local
notation only: `MetricExpr = Ref[MetricKind] | RuntimeMetricExpr` and
`MetricTerms = list[MetricExpr] | tuple[MetricExpr, ...]`.

```python
aggregate(measure: Ref[MeasureKind], *, agg: AggKind, label: str,
          fold: AggregateFoldInput = None,
          slice_by: Mapping[Ref[FieldKind], SliceValue] | None = None) -> RuntimeAggregateExpr
weighted_mean(value: Ref[MeasureKind], weight: Ref[MeasureKind], *, label: str,
              slice_by: Mapping[Ref[FieldKind], SliceValue] | None = None) -> RuntimeWeightedMeanExpr
slice(metric: MetricExpr, *, by: Mapping[Ref[FieldKind], SliceValue],
      label: str) -> RuntimeSliceExpr
ratio(numerator: MetricExpr, denominator: MetricExpr, *, label: str,
      zero_division: Literal["null", "error"] = "null") -> RuntimeRatioExpr
linear(*, add: MetricTerms, subtract: MetricTerms = (), label: str) -> RuntimeLinearExpr
```

AggKind stays `sum/count/count_distinct/min/max/mean/median` or
`("percentile", q)`; AggregateFoldInput stays None, `mean/min/max/first/last`
or `("percentile", q)`. Percentile q is finite, strictly between zero and one,
and not bool. Fold requires the Measure's declared status-time semantics.
Linear retains ordered +1 and -1 occurrences, at least two in total; it is not a
period Difference. Labels are presentation, never business identity.

SliceScalar remains `str | int | float | bool | None`; SliceValue remains a
scalar, list/tuple/set of those scalars, or the existing `{op, value}` predicate.
Replace the current predicate Any with closed, op-dispatched TypedDict variants:
`==/!=` accepts one scalar; ordered comparisons accept one non-null str/int/float;
`in` accepts a scalar collection; `between` accepts an ordered two-element tuple
of non-null str/int/float endpoints, inclusive at both ends. Ordered numeric
operands exclude bool and nonfinite float; resolved field type validates scalar
compatibility (no string-to-number conversion). None means an explicit null
match only for equality/inequality or membership. An empty membership set is
false. Collections freeze as immutable values with deterministic set ordering;
list/tuple order remains part of authored input. No new predicate factory, SQL,
callback or bare business-name string is introduced. Slice limits the selected
contribution branch; where limits output rows and cannot replace it.

### Complete domains, groups and continuation

Each occurrence computes its complete coordinate image within its own branch
filter, path and time range. The result domain is the union of complete typed
tuples, including the target Subject key and bound time coordinate. It is never
a product of separate projections, an intersection, the numerator alone, or a
filter on nonzero results. Missing components receive empty state only with
sufficient bound coverage; unknown mapping/coverage or failed execution is not
zero. Adding contribution coordinates revokes an automatic complete Entity x Time
claim; restoring that target uses group_by with explicit groups and empty-state
admission. Multiple independent one-to-many paths do not authorize a product.

A predicate from another explicitly supplied relation adds that relation as a
value dependency. Its complete instance domain must correspond exactly to the
receiver, or by an already retained checked containment map; it cannot use row
position or retrieve a missing fine domain. Thus fixed `mean.where(revenue.value.gt(10))`
on corresponding retained member relations filters by revenue, not by mean.
Predicate source/fixed and Session checks apply to the whole dependency graph.

Category inputs align by full keys or a retained checked containment map, never
row position. Every consumed classification Cell must be Defined and in its
value domain. Null/Undefined/Unknown keys reject; checks apply after the actual
branch/member selection, not unrelated source rows. Ref keys on relations refer
only to uniquely retained coordinates; new property reads require an explicit
member binding. A Grain coarsens the unique retained time axis; each input cell
must fit wholly within one target cell. A crossing week cannot split across
months. Duplicate keys, multiple time axes and ambiguous Ref bindings reject.
Explicit groups retain empty groups but cannot repair missing member mappings.

Root members project K without distinct. Selection of an Entity relation preserves
its injective Subject mapping; extracting Subjects from multi-instance rows
uses the full-key set image, with Ibis distinct on source or equivalent fixed
Python semantics. That operation cannot repair malformed input keys/versions.

Current-row methods are the closed descriptors from `mv.sum()`, `mv.min()`,
`mv.max()`, `mv.mean()`, `mv.count()` and `mv.count_defined()`, each returning its
specific RowMethod variant. Named statistical-weight binding and dependent
current-row weighted mean are withdrawn from R5 scope; Metric weighted mean is
still required. Original reduction and RowStatistic continuation are separately
registered under the [method/state contract](operators-and-frames.md#r51-method-and-state-contracts).
A missing part removes K and rejects an attempted continuation; unchanged numeric
values alone never establish state or semantic equivalence.

No target here becomes a public export, live Help target or dynamic action until
its implementation package aligns native signatures, export snapshots, independent
Help reachability/budgets, structured repairs, CLI, and both latest site editions.


## R5.2 connected member and scalar-read slice

R5.2 connects the frozen member/read signatures above to the existing graph and
Store 7 route. Entity identities retain every ordered string/int64 primary-key
column. `members(at=...)` requires an aware datetime or `TimeScope.before_end`
for versioned Entities and rejects an anchor for unversioned Entities. Native
date/timestamp snapshot and validity axes use exact declared version selection.
The member version is never an implicit attribute version: historical `read`
requires its own `at`. The direct-own-Dimension grouping shorthand inherits the
already selected member version without discovering another property owner.

R9.3 additionally connects native PostgreSQL/MySQL/Trino/ClickHouse table
member transmission, int64-key time products and direct int64/date scalar reads for the corresponding
entity/NoTime physical keys. Native Boolean reads additionally connect on
PostgreSQL/Trino/ClickHouse; MySQL BOOLEAN is observed as int8 and rejects before
business reads rather than becoming a Boolean or silently coercing integers.
A loaded Boolean Dimension row expression (for example `rows.enabled == 1`)
now uses the existing frozen Semantic/Ibis binder and supports complete member
reads and filtering on MySQL. It does not reinterpret direct integer Dimensions. Exact UTC DATE snapshots and closed-open validity with NULL open
end retain complete int64/string composite identity; selected duplicate identities
reject before publication. A single explicit directed to-one native-table path
with complete int64/string keys additionally consumes direct int64 attributes.
Missing and multiple scoped matches reject; matched NULL remains a Null Cell and
unrelated target duplicates do not become global uniqueness obligations. Other
scalar types and longer paths retain separate qualification requirements.

The qualified DuckDB table/Parquet scalar reads are direct-column or bound
row-expression Measures (int64/float64), Dimensions (string/int64 or native boolean), and
TimeDimensions (native date or aware timestamp). An integer 0/1 Dimension stays
categorical. Computed Boolean Dimensions and numeric Measures retain their ordered expression and bound-field
fingerprints; source execution evaluates them on the scoped owner rows through
the existing Semantic/Ibis binder, while fixed continuation uses retained values.
Unqualified parsing, naive attribute
timestamp conversion, Decimal and Duration do not acquire qualification from
this slice. Broader temporal conversion and numeric qualification remain with
R5.5/R5.6; there is no coercion or fallback. Source physical schema and the exact
realized Arrow schema remain part of Runtime authority.

A scalar read accepts a direct owner, one directed to-one Relationship Ref, or
one explicit member-rooted `mv.routes(mv.route(...))` path. Every relationship
key is used; static multiplicity, role and version errors reject before source
binding. Missing mappings and multiple actual matching rows reject before
publication, including for a declared to-one path. Checks concern the current
consumer domain, not unrelated owner identities. A represented null attribute
is a Null Cell, distinct from missing owner coverage.

Boolean/Temporal logical, materialized and selected variants join the existing
Relation protocol. Numeric read selection uses SelectedNumeric variants rather
than Difference labels. `members()` on these relations consumes their Subject
mapping and returns a logical source or logical fixed domain according to the
actual dependency binding. Source members project complete keys directly;
non-injective multi-instance mappings take the complete-tuple set image. Fixed
continuations cannot introduce external attributes or reload current Semantic.

Public-type Help remains independently resolvable and progressive; its outgoing
route budget is 12 to include the member projection and selection operations.
This does not enlarge root or capability-page budgets. The R5 migration ledger
and R5.2 evidence index own measured execution status, not this contract text.

## R5.4 connected coordinates and reductions

`group_by(*keys, groups=...)` retains complete tuples. Member domains accept
own Dimension reads and explicit corresponding CategoryRelations; observed
relations accept retained Entity/Dimension axes and explicit categories. An
Entity Ref retains its entire composite identity. Classifications join by the
complete receiver or retained Subject key, never by row order. A missing,
ambiguous, non-Defined or out-of-target classification rejects on the consumed
domain. Contribution branches reduce independently before their tuple union;
denominator-only and cancelled-zero coordinates remain present. There is no
per-column Cartesian completion. Empty groups require an explicit target and
complete input coverage. No-key member grouping creates Singleton.

Original `group_by(...).rollup()` eliminates the other axes; `rollup()` eliminates
all axes. Sum, count, mean, ratio, signed linear and Metric weighted mean merge
original components before finishing. Grouping numeric reads grants current-row
`summarize`, not original Metric rollup. `mv.count()` and `mv.count_defined()`
return CountMethod descriptors and also work on categorical, boolean and temporal
rows; `mv.sum/min/max/mean()` require finite Defined numeric Cells. Category
`group_by()` groups its own Defined values before a count. The corresponding
RowStatistic has its own contribution identity. Its `group_by(...).rollup()` and
`rollup()` merge retained row state without treating subgroup Cells as new rows.

LogicalStatisticRelation, MaterializedStatisticRelation and
GroupedStatisticRelation are one statistic family. Grouped original and scalar
receivers disclose only their mechanically valid continuations. Fixed grouping
requires fixed classifications and targets; it cannot reload Semantic or switch
to DuckDB. Store 7 checks exact parts, versions, complete keys and numerical
consistency before continuation. Missing or damaged parts revoke usable K.

This slice retains the existing string contribution coordinates, string/int64 classifications,
complete string/int64 Entity keys and qualified numeric/time inputs. Time grids
and coarsening remain R5.5, the complete numeric matrix remains R5.6, and installed
candidate qualification remains R5.7. The migration ledger and `evidence/r54/`
record measured acceptance; this section is not installed-package evidence.

R9.3 adds bounded ordinary-table routes on all six backends for int64/float64 observations, string
classifications, explicit empty targets, count/count_defined/sum/mean row statistics and original
sum/mean-state rollup with UTC microsecond source time. The added classification,
completion, row-statistic and member-image keys remain exact; they do not
specialize to Decimal inputs. The initial R9.3 shared-stage implementation used
DuckDB/SQLite temporary relations and complete remote exchanges represented as
Ibis-compiled typed literal relations. Under the R9.6 weak-consistency amendment,
pure-native graph dependencies remain Ibis expressions: checks, required parts and
the terminal primary are queried directly, without an intermediate Arrow-to-source
round trip. This creates no remote tables and does not rewrite compiled SQL.
Preparation for registered local consumers still owns complete captured exchanges;
capture views have distinct ownership even for identical values, and released
captures cannot be submitted again.
Empty schemas, multiplicity, full-width keys and UTC microseconds are retained.
Captured exchanges for hybrid/local plans use unique row indices and typed
conditional projection; the cardinality barrier equals the complete captured row
count and is not an admission quota. For these captured exchanges, ClickHouse uses
one typed struct-array expansion to avoid a many-branch query plan. Original mean
retains its sum/count components: 100 amount-1 facts
and one amount-100 fact produce grouped values 1 and 100, current mean 50.5 and
original mean 200/101. Use `grouped.group_by().summarize(mv.mean())` to remove
the grouped axes for the current-row reduction. Fixed reuse reads retained parts
only. Null observations remain in count, count_defined includes only Defined
Cells, and sum/mean reject non-Defined current Cells before consumption.
These bounded numeric/string executions do not qualify the complete C05 numeric,
domain, data-check and cancellation matrix.

R9.3 also connects exact ordinary-table UTC-microsecond int64 share and rank
consumers, and string-identity/int64 cohort predicates, on SQLite, PostgreSQL,
MySQL, Trino and ClickHouse. Shares retain the explicit original singleton denominator; ranking and
cohort preserve the full composite identity. Fixed continuations consume retained
parts without source reads. Cohort truth counts map Boolean conditions to integer
1/0 before reduction, including PostgreSQL where Boolean-to-BIGINT casts are
unsupported. These bounded routes do not qualify the complete C08 reference,
opportunity, numeric-state or cancellation matrix.
Static string-identity Entity targets also have exact ordinary-table NoTime
capture routes on the remote backends. Full two-day opportunity controls retain
all six instances for three composite identities and distinguish `any_instance()`
from `at_least(2)` across source and fixed execution. ClickHouse Boolean decoding
accepts only exact integer 0/1 for declared Boolean output; other scalars reject.

R5.4 review repairs keep explicit targets in the executable group-domain graph:
`group_by(..., groups=target).execute()` validates consumed-key containment and
publishes the complete target, including empty tuples. Restoring that Artifact
returns an AnalysisDomain without Cell or reduction-state parts. Selected
CategoryRelations retain their Dimension identity through selection and Store 7;
their unkeyed grouping still groups the selected category values. Cross-relation
numeric predicates transport the dependency's source bindings as well as its
node, so independently rooted source observations and their fixed counterparts
use the same exact-key correspondence checks.

A MaterializedGroupedNumericRelation retains its complete group axes during
`summarize`. The receiver's current rows are the already materialized group
rows, not the earlier members. Each existing group therefore contributes one
row to count; a strict numeric reducer still rejects a non-Defined group Cell.
Original Metric state is consumed only by original `rollup`, while a newly
created RowStatistic merges only its own row state. The breaking frozen layouts
are accepted explicitly by the R5.4 section of `session-state-and-runtime.md`.

## R6.1 frozen relation-composition target

Status (2026-09-30): accepted implementation target for R6.2–R6.7, **not new
public execution qualification**. The [R6 migration ledger](../../superpowers/specs/2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md)
records the actual starting surface and test owners. This section owns the
concrete API target for C07–C09 and supersedes historical Dataset/Delta field
selection shapes for these capabilities. Existing R4/R5 qualifications remain
bounded to their evidence. No names below are exported merely by this freeze.

### Closed inputs and result families

In the signatures below, NumericRelation means the closed union of Logical and
Materialized numeric families (observed, original ratio, rolled, Difference,
RowStatistic and named numeric views); CategoryRelation, BooleanRelation,
TemporalRelation and AnalysisDomain similarly mean their existing concrete
paired variants. These are documentation/type-checking aliases, not public
constructors or Help targets. A capability is admitted from the exact quantity,
domain and retained parts, never just membership in this union. Selected
variants retain the same family and may repeat where, compare or rank when
their contracts permit it. Every transformation returns a Logical variant;
execute returns its paired Materialized variant. Fixed inputs remain fixed
through any Logical continuation; Logical does not mean source-backed.

```text
ExactKeys()
UnionKeys(*, missing: Literal["keep", "metric_empty"])
TimeChange(*, pairing: ExactKeys | UnionKeys = ExactKeys())
CohortContrast(*, pairing: ExactKeys | UnionKeys = ExactKeys())
PeriodChange(*, alignment: WindowBucketAlignment,
             pairing: ExactKeys | UnionKeys = ExactKeys())
window_bucket() -> WindowBucketAlignment

NumericRelation.compare(baseline: NumericRelation, *,
    design: TimeChange | CohortContrast | PeriodChange = TimeChange(),
    value: Literal["difference", "relative_change"] = "difference")
    -> LogicalDifferenceRelation
NumericRelation.ratio(other: NumericRelation, *,
    pairing: ExactKeys | OneToOneCorrespondence = ExactKeys())
    -> LogicalNumericRelation
one_to_one(*, left: NumericRelation, right: NumericRelation,
    via: Ref[RelationshipKind], time: PeriodChange | None = None)
    -> OneToOneCorrespondence

Relation.where(predicate: BoundPredicate) -> Logical variant of receiver
Relation.members(*, through: SubjectBinding | None = None)
    -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain
all_of(*predicates: BoundPredicate) -> BoundPredicate
any_of(*predicates: BoundPredicate) -> BoundPredicate
not_(predicate: BoundPredicate) -> BoundPredicate
Relation.value.is_defined() -> StatePredicate

AnalysisDomain.cohort(predicate: BoundPredicate, *,
    rule: AnyInstance | AtLeast | AllInstances,
    through: SubjectBinding | None = None)
    -> LogicalAnalysisDomain | LogicalFixedAnalysisDomain
any_instance() -> AnyInstance
at_least(count: int) -> AtLeast
all_instances(*, empty: EmptyOpportunityPolicy) -> AllInstances
empty_opportunity.true() -> EmptyOpportunityPolicy
empty_opportunity.false() -> EmptyOpportunityPolicy
empty_opportunity.undefined() -> EmptyOpportunityPolicy

NumericRelation.share_of(reference: NumericRelation) -> LogicalNumericRelation
AnalysisDomain.penetration_in(reference: AnalysisDomain) -> LogicalNumericRelation
reference_weights(values: NumericRelation, *,
    strata: tuple[CategoryRelation, ...], unit: Ref[EntityKind]) -> ReferenceWeights
NumericRelation.standardize(*, reference: ReferenceWeights) -> LogicalNumericRelation
NumericRelation.rank(*, order: Literal["ascending", "descending"],
    ties: Literal["ordinal", "dense", "min", "max"],
    partition_by: tuple[CategoryRelation, ...] = ()) -> LogicalRankingResult
RankingResult.where(predicate: BoundPredicate) -> LogicalRankingResult
RankingResult.limit(count: int) -> LogicalRankingResult
DifferenceRelation.attribute(*, axes: tuple[Ref[DimensionKind], ...],
    mode: Literal["joint", "hierarchy"] = "joint", top_k: int | None = None)
    -> LogicalAttributionResult
AttributionResult.where(predicate: BoundPredicate) -> LogicalAttributionResult

table(**columns: NumericRelation | CategoryRelation | BooleanRelation |
    TemporalRelation) -> LogicalTable
LogicalTable.execute() -> MaterializedTable
MaterializedTable.show(*, max_output_bytes: int | None = None) -> None
MaterializedTable.to_pandas() -> pandas.DataFrame
```

There is no alternative relative_change method, generic join, formula builder,
free-form pairing callback or current-row statistical_weight surface. Comparison
and ratio accept the same source/fixed mode and Session only, even when the
static input union can express an invalid pair. Static typing must reject wrong
kinds, strings and untyped callables; ownership/parts checks refine those types.

BoundPredicate is a closed immutable union of NumericPredicate,
CategoryPredicate, BooleanPredicate, TemporalPredicate, StatePredicate and
composite AllOf/AnyOf/Not variants. It is not a callback, SQL expression or
implicit Python truth value. StatePredicate contains the exact field input and
an is_defined tag operation; it is not itself a BooleanRelation and does not
read business rows. all_of/any_of require at least two operands; not_ exactly
one. Fields and literals carry concrete types: numeric methods eq/lt/lte/gt/gte
accept a compatible NumericField or int/float/Decimal literal under the numeric
matrix; bool is excluded. Duration comparisons require a compatible Duration
field on both sides; no untyped tick literal or new duration constructor is
introduced in R6. Negated equality uses not_(field.eq(...)), not a second
spelling. Category eq accepts a compatible CategoryField or str/int literal;
Boolean eq accepts a compatible BooleanField or bool literal. Temporal uses
eq/lt/lte/gt/gte with a compatible TemporalField or date/datetime
of the same temporal kind and authority. No implicit timezone or unit coercion.
Scalar literals inherit the field's unit; two fields must prove compatible
units. Composite predicates retain all actually referenced inputs in authored
order, including inputs from named views. Runtime dependencies are explicit.
Selected logical and materialized relations disclose their further `where`
continuation through `.contract()`, including the tag-first selection path.

SubjectBinding is a producer-owned immutable value with exact instance domain,
Subject Entity, complete typed Subject key and single-valued mapping. It is
returned by the owning domain producer, never a user-authored arbitrary map.
Entity identity and Entity×Time's retained projection need no through argument;
an explicit through, if supplied, must match that retained mapping. Other
instance domains require through and retained coverage. R7 owns their producers.
Groups without a retained Subject map reject members; projection forms a set
image without changing the original instance multiplicity. cohort additionally
requires the complete opportunity contract, not merely a Subject map. at_least
requires a positive integer excluding bool; empty-opportunity and Metric
empty-contribution policies remain different closed types.

### Comparison compatibility and quantity templates

| Design | What may change | What must remain equal / proved |
| --- | --- | --- |
| TimeChange | Explicit observation-window or endpoint roles | Same quantity template, exact shared target realization, contribution roles/routes, group axes, units and policies; distinct time bindings |
| CohortContrast | Explicit target membership/selection | Same quantity template and observation time, contribution roles, group axes, units and policies; Group or Singleton output with explicit complete coordinate correspondence |
| PeriodChange(window_bucket()) | Bound observation periods and their time coordinates | Same target realization and quantity template, non-time coordinates, roles, units and policies; complete ordered equal-length bucket bindings from the temporal owner |

ExactKeys/UnionKeys operate only after design compatibility. Union is a chosen
method, never a failed-Exact fallback. CohortContrast cannot pair unrelated Entity
identities by row order. PeriodChange's non-time Union policy does not waive
complete bucket correspondence. An equal target definition or equal returned
keys is not proof of the same target realization. Source target sharing uses
one explicit node; fixed sharing uses the retained exact target realization and
receipt-bound identity, not just equal current/baseline Artifact schemas.

A quantity template is a typed expression tree. Observed leaves retain Metric
or runtime-expression definition, occurrence order, roles, filters, units and
policies; only the time slots selected by the design may be substituted.
Original ratios/linear/weighted means retain their component templates and
finish policies. Difference nodes retain inner design, pairing, absolute versus
relative choice and ordered child templates; both children must match
recursively under the outer design. RowStatistic templates retain method and
input template; ordinary ratio and standardized quantities retain pairing and
reference bindings. Different operators are never interchangeable on units
alone. A comparison cannot rewrite, reorder or cancel this tree. In particular,
(Aug−Jul1)−(Jul2−Jun) can retain two different July realizations; it neither
identifies them nor grants attribution or a linear rewrite. Reference identity
is not a replaceable time slot. Unsupported template combinations reject before
reading rows; extending them requires a registered rule with independent tests.

The ordinary ratio correspondence binds the exact ordered left/right nodes and
a declared one-to-one relationship. It validates both full key images and time
roles. A different node with the same definition cannot reuse that object.
No many-to-one, UnionKeys, metric_empty or guessed relationship path is admitted.

### Reference, named-view and terminal protocols

ReferenceWeights is an input value in the existing reference family, with a
bounded one-line repr and show; it has no standalone execute or arbitrary
constructor. It freezes exact values/strata dependencies, nonempty ordered
unique strata axes, dimensionless weights, complete stratum tuples and the
statistical-unit Entity. The standardized receiver must be grouped by those
same axes and carry that same statistical-unit identity. Output is one
Singleton standardized quantity, preserving the receiver's measurement unit;
there is no inferred per-group reference join. Numerical/zero-weight policy
belongs to [the method owner](operators-and-frames.md#r61-method-rules-and-qualification-target).
share_of consumes only a same-measure Singleton reference; penetration_in
returns a Singleton over the complete fixed reference member domain. Logical
references are fixed for that execution and shared by explicit dependency.

RankingResult has values and ranks; AttributionResult has contribution, current
and baseline. All are typed numeric views bound to the result's complete key,
selection and fixed realization. They use the existing Logical/Materialized
numeric family, not dynamic attributes from column names. Results have bounded
repr/show and contract, with only state-valid continuations. where/limit on the
ranking result restrict both views; where on attribution restricts all three.
An attribution's current/baseline labels mean allocated side terms, especially
for component_mix. View selection preserves the original ranking/reference/
reconciliation scope while separately recording the current selected domain.
Attribution numeric views disclose their valid where, current-row summarize,
rank and explicit ratio continuations; they acquire no original Metric rollup
or time-comparison template. Ranking consumes their typed Other mask and retains
the original allocation evidence on its values view.

Table requires at least one column with a nonempty string display label;
keyword insertion order fixes output-column order. All columns must have the
same typed complete keys, compatible time meaning, source/fixed mode and Session.
Key fields are carried once; display labels colliding with exported key names
reject rather than rename silently. Duplicate/missing keys fail before output,
including equal-length misaligned inputs; double-empty same-key inputs are legal.
Table uses the common graph scheduler, checks and atomic publication protocol,
with a terminal descriptor containing ordered labels, exact input/view bindings
and key schema. Recovery reads those verified inputs/parts without sources.
MaterializedTable has bounded one-line repr, deterministic bounded show and an
isolated to_pandas copy. It has no contract/K, dynamic column attributes,
where/group/arithmetic, or path back into analysis. Continue from the original
Relation variables. LogicalTable exposes execute and bounded repr only.

Errors use existing structured AnalysisError subclasses with expected, received,
repair, constraint identity and stage. Construction/admission rejects kind,
Session, mixed mode and known binding errors; execute discharges actual key,
coverage, value, weight and partition obligations before publication. Help owns
static signatures, result contract owns current valid actions and structured
errors own repairs. R6.2–R6.6 must update native Help, export snapshots, dynamic
guidance, CLI and latest English/Chinese examples together with each executable
surface; this target text alone adds none of those promises.


### R6.3 connected predicates and Subject cohorts

The closed bound predicate API above is connected through the unified graph,
Runtime and Store 7. All actually referenced relations have ordered data edges;
there is no discovery by column name. Selected Numeric, Category, Boolean,
Temporal and Difference relations expose their own typed `value` and `where`,
allowing an explicit tag selection before scalar consumption. L1 does not fuse
a tag selection with ordinary comparison consumption.

Entity targets expose `cohort`; source results can feed new governed observations,
and fixed results expose retained continuations only. Entity identity and the
Entity/time projection expose the producer-owned `subject_binding`, accepted by
`members(through=...)` and the opportunity consumer's `cohort(through=...)`.
Groups without such a map reject projection. `domain.cohort@v1` retains complete
target decisions, including false qualifications, and the exact opportunity grid.
For a direct fixed cohort, a static target Artifact can be combined with fixed
Entity/time opportunity Artifacts sharing one exact time shape. Artifact reads
preserve each input's physical time metadata. The cohort consumer uses the
retained opportunity grid and therefore selects its non-temporal local key;
it does not cast or reinterpret timestamps. Other mixed shapes remain rejected.
Missing opportunities never become Unknown. Existing Unknown consumption is
qualified independently; no new public Unknown-producing method is introduced.
The qualification matrix and remaining R7–R10 boundaries are recorded in the
[R6 ledger](../../superpowers/specs/2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md#r63-predicates-and-full-opportunity-cohorts-2026-09-30).

### R6.4 public reference qualification

The four reference entry points above are now connected for DuckDB table/Parquet
and fixed Artifact execution. `ReferenceWeights` is factory-only and immutable;
its identity freezes ordered values/classification dependencies and the statistical
Entity. Classification bindings must already be retained through grouping or
inclusion; matching display column names is not a correspondence. The receiver
must retain the same ordered axes, frozen time scope and proven statistical Entity.
The factory has bounded `repr`/`show` and no standalone `execute`.

R9.3 connects bounded ordinary-table UTC-microsecond int64 group shares and
int64 stratum values with float64 reference weights on the six backends. Static
string classifications have exact NoTime capture keys on the remote backends.
Missing strata and negative weights reject without normalization or publication;
fixed continuations read retained parts only. Reference proof comparisons use the
complete stratum key image, preserving duplicate rows and every value while
allowing different source row orders. These controls do not qualify the complete
C08 type/domain/state/cancellation matrix.

The bounded ordinary-table NoTime string-identity penetration route preserves
all composite member columns on the six backends. Independent fixed producer
member maps retain their original Artifact input identities and receipts while
historical definition closures remain separate. Key field nullability may differ
between source projections; names and physical types must match, and actual
null coordinates and duplicate identities reject. Intersection proof comparison
uses every typed key rather than source row order. Empty references remain
Undefined/empty_reference. Rank/limit/table controls retain original ranks and
canonical terminal-table key order.

Original Metric/runtime Metric sum/count/linear join mean/weighted_mean/ratio in
standardization admission. Mean/weighted mean use their sample/paired contribution
Entity; ratio uses its denominator component; sum/count use their contribution;
linear requires a common Entity across every term. Unsupported or unproved methods
reject before business reads and Run allocation. Standardization preserves the
measurement unit and describes a weighted stratum value, including weighted
stratum totals, without an actual-population claim or original-state rollup.

Start reference discovery at `marivo.help("analysis.methods.metric.reference")`,
then use the exact callable/type targets and current result `.contract()`.
`where` retains original reference identity, denominator, support/intersection
proof and all weights/values, including zero-weight non-Defined strata. Fixed
recovery executes those continuations without current Semantic or DuckDB.
Selected numeric results disclose `members()` only when they retain a total
Subject map. Complete numeric keys and reference parts do not grant Subject
authority; without that map, projection rejects before execution.
R6.5 ranking/Top-K invariance and R6.7 installed-wheel acceptance are not qualified
by this source-checkout implementation.

### R6.5 connected ranking and terminal tables

`NumericRelation.rank(order=..., ties=..., partition_by=())` now returns
`LogicalRankingResult`; `execute()` returns `MaterializedRankingResult`.
The fixed properties `values` and `ranks` return the existing numeric family.
`values` preserves the original quantity and actual sufficient parts; `ranks`
is a new int64 quantity bound to the original ranking domain. Both views follow
the retained ranking order. Result `where` and `limit` return Logical rankings,
restrict both views together and preserve original ranks and fixed references.
`limit(1..100000)` excludes bool and selects a global prefix. Per-partition Top-K
uses two steps, first `is_defined()`, then `lte(k)` on the selected ranks.

`mv.table(**columns)` returns `LogicalTable`, and execution returns
`MaterializedTable`. These terminal types provide no contract, field predicates,
column attributes or analysis continuations. Only the materialized type exposes
`artifact_ref`, bounded `show()` and isolated `to_pandas()`.
Export contains keys once followed by authored value labels in insertion order,
in deterministic canonical key order. Arrow-backed pandas dtypes preserve int64,
Decimal and Duration precision. Non-Defined Cells export as missing values: this
compact export loses the distinction between Null, Undefined and Unknown and
their reasons. The saved Artifact and `show()` retain those facts. No status
columns or export options are added. The qualified source routes are DuckDB
native tables and local Parquet; fixed continuations consume verified Store 7
parts. R6.6 attribution, R6.7 same-wheel closure and remote qualifications remain
separate acceptance work.

R9.3 connects bounded ordinary-table UTC-microsecond int64 direct-column
native distributions for string-keyed Entity members. Exact distinct is supported
on DuckDB, SQLite, PostgreSQL, MySQL and Trino; explicitly approximate distinct
also runs on ClickHouse. Exact quantile is supported on DuckDB and PostgreSQL;
explicit approximate quantile also runs on Trino and ClickHouse. SQLite/MySQL
quantiles and ClickHouse exact distinct remain pre-read refusals under the native
accuracy contract. Ibis may implement an explicitly approximate declaration with
an exact native operation; the submitted operation and authored intent remain
separate evidence. Original distribution quantities have no original rollup or
attribution authority. Fixed recovery uses retained values only. These registrations
do not qualify other input types, unbounded windows or complete C10.

### R6.6 connected allocation

Absolute Difference.attribute(axes=..., mode="joint", top_k=None) returns
LogicalAttributionResult; execute() returns MaterializedAttributionResult.
Original sum/count/linear select additive_difference; mean/weighted_mean/ratio
select component_mix. The factory does not accept a method override. Both
endpoints must retain complete disjoint original components, valid policies and
Defined overall values. Direct-only aggregates, extrema, folds, relative or
nested changes and selected Differences do not inherit allocation authority.

Logical missing axes rebuild the frozen observation graph as an explicit second
input and validate its endpoints against the original target. Existing source
captures share their nodes; fixed inputs require the requested ordered axes in
checked parts and never re-open sources through lineage. Original reduction
retains an allocation_state partition for this purpose without restoring removed
axes as public group_by authority. Qualified axes remain direct string Dimensions
on the contribution root, under the existing non-null coordinate-state contract.

Both bases use one typed Top-K mapping before allocation. Hierarchy reuses that
mapping for each authored prefix. Original comparison scope, resolution, axes
and Other mask form complete output keys. Only attribution axis components may
be null, denoting mapped Other or inactive prefix positions; the mask and
resolution distinguish them. Source key checks and joins, fixed exchange and
receipts retain that exact identity. A real "Other" string has a zero Other bit.

contribution/current/baseline are same-key NumericRelation views; component_mix
side values are allocated numerator/overall-denominator terms. where retains
the original target, complete basis, allocation rule and reconciliation scope,
selects all three views together and unconditionally revokes current completeness.
Views use existing predicate, rank and table consumers and do not gain original
Metric merge state. Native Help owns static facts and contract() owns current
continuations; existing packaged workflow guidance remains applicable unchanged.

R9.3 connects a bounded ordinary-table UTC-microsecond int64 sum-zero and original-mean path
with explicit string contribution coordinates and bounded observation windows
on the six backends. Non-DuckDB inputs prepare flat native candidate rows before
registered local original reduction, absolute comparison and additive/component-mix attribution.
Single/joint axes, retained hierarchy, common Top-K/Other and selection consume
checked original partitions; missing classifications reject atomically. Fixed
continuations use retained receipts only. Ordinary source reads keep their normal
read guarantees and do not claim a shared snapshot. Historical selected-population
and occurrence preparation still require their existing capture authority. This
subset does not qualify other original methods, source numeric types or complete C09.

The connected fixed allocation route also retains UTC microsecond time shape
for original period buckets and day-to-month coarsened partitions. Source and
fixed allocation consume the same complete retained partition; this exact
registration does not qualify other time precisions or report timezones.

A logical observation over a locally selected population exposes execution as its
continuation boundary. Execute it before grouping, comparison or other derived
operations; its materialized result owns those continuations. Ordinary prepared
observations with identical selected and original populations retain their normal
logical continuations.

### R6.7 canonical recovered variants

LogicalRolledNumericRelation.execute returns MaterializedGroupedNumericRelation
when the reduction retains group keys, otherwise MaterializedRolledNumericRelation.
The return annotation is that closed union. Execution and Store 7 recovery now
agree on the public variant and its actual continuation contract; original
state, definitions, DAG identities and state encoding are unchanged. Numeric
tables over attribution views preserve typed Other axis nulls under the declared
complete key, without permitting null Entity identities.

## R7.1 frozen domain API target

Status: accepted target, 2026-10-01; documentation/static freeze only. None of
the new symbols below is made importable by R7.1. Existing Event/Lifecycle
Dataset APIs remain migration consumers, not implementations of this target.
The [R7 migration ledger](../../superpowers/specs/2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md)
owns status and test responsibility. This section owns F01/F02 and public
handles; operators own domain truth/arithmetic, Runtime owns retained schemas
and placement, and timezone owns instant/window conversion.

R7.3 implementation amendment: the public `session.events.match` entry now accepts
only a same-Session logical AnalysisDomain and returns LogicalJourneyResult.
JourneyResult, EventDurationResult and CompletedJourneys have Logical/Materialized
pairs in the unified graph/Store 7 path. Canonical assignment and reach feed
registered duration/dropout projections, strict selection, Subject image and
complete-opportunity cohort. Duration current-row mean is qualified; original
Journey multiplicity is retained when selecting Subjects. A new Metric observation
after local selection prepares all source dependencies before that selection in
the same Run. Source member projections are consumed by their complete identity,
not the scalar type of the preceding selection. A prepared Metric observation or
Duration row statistic ends this local logical stage: execute it before further
operations such as rollup. Dynamic contracts expose that materialization boundary;
the fixed result exposes its qualified retained-state continuations.
Fixed continuation uses retained parts without rematching; a fixed
population is not an input to a source match or observation. This amendment covers
local DuckDB table/Parquet routes only. It does not qualify R7.4–R7.9 or remote
backends. The [R7.3 evidence index](../../superpowers/specs/2026-10-01-marivo-r73-evidence-index.md)
records executable scope and final gates. Result cards disclose captured time
precision and conversion loss; R7 execution shares a 600-second deadline.

### Domain identities and concrete result families

Every key includes its exact definition and this realization's binding. Subject
K is the Entity's complete primary key; version rows never become Subject K.
Occurrence K is `(Event definition/version, complete occurrence Entity K)`.
Different Events with the same physical ID have different occurrence identity.

| Domain kind | Complete key beyond the realization binding | Public result stems |
| --- | --- | --- |
| entity | complete Subject K | existing AnalysisDomain and BooleanRelation |
| occurrence | exact Event binding and complete occurrence K | ViolationResult; trigger/time/state/kind relations |
| journey | Subject K, start occurrence K, exact pattern/matching binding | JourneyResult, EventDurationResult, CompletedJourneys |
| interval | Subject K, canonical interval ordinal bound to full History trace | StateIntervalResult |
| checkpoint_state | exact checkpoint instant, ModelState K, complete axis tuple | StateDistributionResult |
| model_state | exact StateModel definition/version and declared state key | DwellSummary |
| transition_pair | exact StateModel binding and declared from/to state keys | TransitionSummary |
| anchor | Subject K, source Event binding and occurrence K; Journey source also binds its assignment | AnchorDomain, RetentionResult |
| group | exact pattern step and complete retained axis tuple | FunnelResult, FunnelComparisonResult |

Each new stem has exactly `Logical<stem>` and `Materialized<stem>` public
variants. `LogicalSubjectRetentionResult` / `MaterializedSubjectRetentionResult`
use the Entity domain. `JourneyResult`, `HistoryResult` and similar unsuffixed
names in prose denote these families, not additional public aliases. History
has `LogicalHistoryResult` / `MaterializedHistoryResult`; its primary domain is
the complete input Subject domain, including Subjects with no intervals.
Attribution reuses existing LogicalAttributionResult/MaterializedAttributionResult.
Scalar reads use the existing concrete Numeric/Boolean/Category/TemporalRelation
families; a Duration-valued NumericRelation is not another exported alias.
No terminal table or summary can recreate an instance domain or Subject map.

### Construction and fixed overloads

The exact source-only signatures are:

```text
session.events.match(pattern: EventPattern, *, population: LogicalAnalysisDomain,
    cohort_window: TimeScope, completion_through: datetime,
    matching: FirstPerSubject | EveryStart,
    business_order: Ref[BusinessOrderKind] | None = None,
    completeness: tuple[CompletenessDeclaration, ...] = ()) -> LogicalJourneyResult
session.lifecycle.replay(model: Ref[StateModelKind], *,
    population: LogicalAnalysisDomain, window: TimeScope, seed: FromInception,
    completeness: tuple[CompletenessDeclaration, ...] = ()) -> LogicalHistoryResult
session.anchors(source: ParticipantRoleHandle, *, population: LogicalAnalysisDomain,
    during: TimeScope, business_order: Ref[BusinessOrderKind] | None = None)
    -> LogicalAnchorDomain
session.anchors(source: LogicalJourneyResult, *, population: LogicalAnalysisDomain,
    during: TimeScope) -> LogicalAnchorDomain
```

AnalysisDomain includes its existing governed temporal/version variants, with
one exact Subject image and compatible membership authority. An explicit
Materialized/fixed domain plus live Event/model refs is mixed, rejected before
business reads and Run admission; there is no fixed replay or rematching entry.
Model entries, old PopulationInput, omitted population and implicit root
inference are not alternate accepted inputs. `seed` is required and only
`from_inception()` is accepted. Replay consumes the StateModel's business_order;
it has no call-level order override. Match and Event-role anchors have the sole
explicit order parameter above. Journey anchors inherit their original order
and reject a separately supplied order parameter.

The fixed Journey overload accepts MaterializedJourneyResult and a compatible
MaterializedAnalysisDomain or LogicalFixedAnalysisDomain, returning a
LogicalAnchorDomain with fixed leaves. A Logical Journey backed entirely by
fixed leaves obeys the same mode rule. A logical receiver's mode comes from
reachable DAG inputs, not its class name. `execute()` returns the matching
Materialized variant. Every materialized reducer below similarly returns the
paired Logical result over verified fixed leaves, never an already executed
object or a live source leaf.

### Owned operations and fields

`journeys.funnel(axes: tuple[Ref[DimensionKind], ...] = ()) -> LogicalFunnelResult`
is first_per_subject only. `time_to_event(from_step: PatternStep,
to_step: PatternStep) -> LogicalEventDurationResult` requires exact retained
steps with strictly increasing indexes. `completed() -> LogicalCompletedJourneys`
selects known completed rows without changing their Journey unit.
EventDurationResult owns status, started_at, completed_at, duration,
observed_duration and followup_until relations. CompletedJourneys.duration is
the existing NumericRelation with exact Duration physical type and step-pair
quantity. `subjects(role: ParticipantRoleHandle) -> SubjectBinding` binds the
exact Journey domain; where transports that map and members takes its set image.
`read(mv.dropped_before(step=...))` returns BooleanRelation and is
first_per_subject only. Unknown is not silently removed by ordinary where.

Retained microsecond Journey Duration ratios preserve Unknown(coverage_censored)
or Unknown(entry_unknown) as scalar float64 Unknown with complete exact keys and
both original endpoint Cells. Self-division does not cancel uncertainty. When
both operands are Defined, zero denominators remain Undefined(zero_denominator).
Other non-Defined operands reject. This does not qualify History comparison
endpoints or create a complete time grid for forecasting/runs.
The fixed NoTime float64 Journey quotient has connected zscore/MAD and
Pearson/Spearman/Kendall consumers. Deviation preserves unavailable Cells while
fitting only Defined samples; association refuses Unknown without deleting or
imputing it. These declarations do not qualify other Journey numeric types,
source statistical routes, forecast or runs.

FunnelResult owns handles named cohort_count, resolved_cohort_count, entry_count,
resolved_entry_count, reached_count, lost_count, coverage_censored_count,
conversion_from_first, conversion_from_previous and loss_rate_from_previous.
Its `read(handle)` returns NumericRelation bound to that result and exact step
domain. `read(mv.funnel_loss_rate(step=...))` returns the same loss quantity for
one noninitial exact step. A same-named handle from another result is foreign.
FunnelComparisonResult owns current/baseline handles for all seven counts,
current_loss_rate_from_previous, baseline_loss_rate_from_previous and
loss_rate_delta; the endpoint components survive reads. `compare(baseline)`
returns LogicalFunnelComparisonResult; `attribute(target=..., axes=...,
mode="joint" | "hierarchy", top_k: int | None = None)` returns the existing
LogicalAttributionResult, with scope and numeric view protocols retained.
No counts()/values()/loss_rate() aliases or string column lookup are added.

History exposes `read(mv.in_state(state, at=...)) -> LogicalBooleanRelation`,
`distribution(at: tuple[datetime, ...], axes: tuple[Ref[DimensionKind], ...] = ())
-> LogicalStateDistributionResult`, `transitions() -> LogicalTransitionSummary`,
`violations() -> LogicalViolationResult`, `intervals() -> LogicalStateIntervalResult`
and `dwell() -> LogicalDwellSummary`. Distribution owns known_state_count,
seeded_subject_count, coverage_censored_count and share_among_seeded. Transitions
owns count and share_of_modeled_transitions. Dwell owns interval_count,
completed_count, right_censored_count, coverage_censored_count,
left_clipped_completed_count, mean_duration, median_duration and p90_duration.
All are concrete bound relations, never dynamic columns. Interval/violation
subjects() binds the sole exact model Subject; selected members require through
that retained binding. Subject-level in_state needs no through. Summary state
and transition-pair rows have no default Subject map.

### Anchor and retention consumption

Anchor.observe takes the existing Metric Ref/RuntimeMetricExpr and RootRoutes
contract, a required ElapsedWindow/CalendarWindow and returns LogicalNumericRelation
on the full Anchor domain. Anchor.retention takes an exact returning
ParticipantRoleHandle, required relative window and completeness tuple, returning
LogicalRetentionResult. Fixed versions use only matching captured Metric/return
input parts and frozen definitions. Missing parts reject; a new live input is
mixed, even when passed as a Ref to a Session-named constructor.

Retention owns `status: BooleanRelation` on its original full Omega: Defined true,
Defined false or Unknown(insufficient_followup). Its `known_true()`,
`known_false()` and `unknown()` are views retaining the original Omega, counts and
bounds; they do not establish a new population. Only a selected known-true
relation can yield members, using the retained Anchor SubjectBinding. Subject
retention status has Entity keys and does not require through.
`by_subject(rule: AnyAnchor | EveryAnchor)` explicitly builds the Subject image
as new Omega, with no default rule. No additional bound relation arithmetic or
scalar rollup is introduced. Relative-window/quantifier constructors retain the
single C18 names elapsed, calendar_days, any_anchor and every_anchor.

All these targets require bounded repr/show, actual conditional K, exact public
typing, one native Help route per symbol, structured expected/received/repair
errors and coordinated English/Chinese executable examples in their connecting
package. R7.1 changes neither exports nor live Help/examples; M15 records that
later responsibility and the separate approval requirement for packaged skills.

Domain failures use AnalysisError's structured `constraint_id`, `stage`,
`expected`, `received` and `repair` fields. The frozen constraint groups are
`r7.input_binding` (foreign Session/definition/role), `r7.input_mode` (mixed or
implicit population), `r7.required_parts`, `r7.physical_qualification`,
`r7.occurrence_identity`, `r7.business_order`, `r7.coverage_binding`,
`r7.inception`, `r7.duration_overflow`, `r7.calendar_deadline`,
`r7.preparation_bounds`, `r7.execute_timeout` and `r7.finding_binding`.
Construction/admission handles statically known failures; execution handles
actual captured values; recovery validates before exposing K or returning a hit.
Repairs name the exact offending binding/part/order/window or qualified route.
Insufficient coverage remains method-owned Unknown/censoring, not these errors.


## R7.2 private preparation status

The private occurrence preparation and F13 execution foundation are implemented
in the single typed graph/Runtime/Store 7 path. No public exports, Help targets,
matching/replay constructors or new domain result families connect in R7.2.
Source, fixed and cold P01 preparation evidence grants only captured occurrence
inputs, full Subject maps and preparation/order/coverage checks. Its ns source
profiles pass under the explicit 2026-10-01 possibly-lossy precision amendment,
not the earlier lossless-source claim. Future public result `.show()` must expose
the retained unit/conversion disclosure. The
[Runtime owner](session-state-and-runtime.md#r72-occurrence-preparation-and-f13-foundation)
owns actual placement, schemas and qualification limits; the
[time owner](timezone-and-calendar-design.md#r71-frozen-occurrence-and-relative-window-time)
owns conversion policy. Packaged skills stay unchanged in this private phase.


### R7.4 implementation boundary (2026-10-02)

The public first-per-subject funnel, exact owned reads, compatible period compare,
and funnel_ratio_mix allocation now use the unified graph, registry, Runtime and
Store 7. The Ibis prefix captures entry-time axes before local consumers; fixed
continuations use retained state. Nonempty frozen Findings publish atomically and
share full collection validation across first read, recovery and exact hit.
Private funnel Delta/Attribution registrations, dispatch, extractor consumers and
exclusive codecs are physically removed. Remaining Event/Lifecycle shared code
awaits its owning phase. Detailed validation and physical requirement statuses are
in the R7.4 evidence index; later phases, same-wheel and remote qualification remain separate.

R9.3 additionally admits SQLite native main-table int64/UTC-us preparation for
one direct, unversioned string/int64 Subject entry axis or an ordered
int64/string pair, or one string axis through a single to-one UTC daily
native-DATE snapshot path or UTC native-DATE closed-open validity path with
NULL open end. Occurrences and entry axes
are captured before local Journey matching and funnel reduction. Fixed compatible
comparisons and loss/denominator-mix allocation consume only retained parts;
recovery can construct a new allocation without source or semantic execution.
Historical path keys use bound R1 schema types. Snapshot selection requires the
exact entry period; a missing period rejects and never selects a later or earlier
row. Validity selection includes the start and excludes the end; a missing or
overlapping interval rejects the complete historical path before publication.
SQLite uses the existing governed temporal lowering and registers its
existing temporal functions on the backup connection before submission.
A repeated execution of the same continuation retains its exact artifact hit;
a separately reconstructed continuation does not promise the prior artifact ID.
Other axis counts/orderings, longer paths, other version/time profiles and complete C12
qualification are not added by this bounded declaration.

## R7.6 local History API amendment

R9.3 additionally connects SQLite native main-table int64/UTC-us replay and
unclassified History views through the registered SQLite backup preparation.
The frozen prefix feeds the existing canonical replay and view consumers.
Complete, prefix and unknown source-origin coverage preserve their distinct
traces and censoring. Source/fixed views and retained scalar reads use the same
typed contracts; fixed recovery does not read sources or replay events.
Other identity/time shapes, checkpoint axes and other source backends remain
independently unqualified. This bounded extension does not grant complete C13.

All six History operations and the five Logical/Materialized view pairs above are
implemented on governed local DuckDB tables and Parquet. Both History variants
return Logical views; fixed receivers use their verified frozen graph. Named
fields return the existing precise scalar Relation families. Intervals expose
state/start/end/observed_duration/left_clipped/status; violations expose
trigger/occurred_at/state_at_event/kind. MaterializedStateIntervalResult.state is
the state CategoryRelation. Obtain the committed identity with
`intervals.evidence_digest().artifact_ref` and recover it with `session.artifact(ref)`.

Summary views have no Subject mapping. Interval/violation subjects() retains the
exact model Subject binding and members(through=...) projects its set image.
State-level read has original complete Subject keys and needs no through.
Dynamic contracts disclose qualified field reads and continuations after verifying
required parts; Duration summary fields do not disclose pooling, and History
scalar reads do not disclose unsupported comparison or ratio methods.

Logical selected Subjects can observe a new count/sum Metric through F13 in the
same Run. Execute prepared observations and Duration row statistics before further
continuation. Materialized/fixed selected members cannot introduce a live Metric.
The existing InState/in_state signature is retained with its sole definition in
analysis.lifecycle; no Dataset reducer dependency or string field entry is added.
R7.7-R7.9, remote execution and same-wheel qualification remain separate.

### R7.7 implementation amendment (2026-10-02)

The unified Anchor producer and relative numeric consumer now use `anchor.bind@v1`
and `anchor.observe@v1` in the existing graph, method registry, Runtime and Store 7.
Event-origin elapsed windows lower through Ibis. Calendar windows and the current
local Journey origin follow F13: all finite source candidates are prepared before
local Anchor restriction. P48's frozen native Journey route is still unverified;
local execution does not substitute for that original qualification.

Each retained Anchor includes the full ordered Subject tuple, Event/occurrence
identity, captured start/order and source definition. Journey starts retain the
canonical assignment, with no rematch. Relative NumericRelations retain exact
windows, coverage, per-component original state and every `(Anchor, component
occurrence)` use. Shared contributions in overlapping windows are intentional;
deleting the Anchor coordinate cannot recover original-state rollup.

The closed qualified numeric shapes are count, int64/float64/Decimal(38,6)/Duration
additive sums, their original-component ratios, multi-root float64 linear and int64
ratio. `via` accepts the governed relationship or closed RootRoutes for multiple
roots. Results reuse the existing NumericRelation and numeric owners.

Event construction requires logical population. Fixed Journey construction requires
compatible fixed population; foreign Session, mixed inputs, wrong Subject and
Journey order overrides reject before business reads and Run allocation. A retained
Anchor cannot acquire a new live Metric dependency. Materialized numeric results
continue using verified retained parts, with their exact available operations in
`contract()`. Receipt failures reject rather than recover from source or Semantic.
The execute deadline remains one shared 600-second budget across all stages.

The R7.7 evidence index records tested routes and original mandatory cells separately.
Retention, Subject any/every, complete A13, same-wheel and remote qualification remain
at their existing R7.8/R7.9/R9 owners. AGENTS.md and packaged skills are unchanged.

### R7.8 implementation amendment (2026-10-03)

R9.3 adds bounded SQLite native main-table int64/UTC-us Event-origin anchors
and retention. Both elapsed and calendar windows select the registered
ibis_python preparation/consumption route before submission; SQLite's unsupported
microsecond interval arithmetic is not emitted or rewritten. Native backup
authority freezes the source prefix. The original opportunity set, Unknown
follow-up, deterministic bounds and any/every Subject fibers remain retained
through selection and cold recovery. R9.4 additionally qualifies string Subject capture for this
same native main-table UTC-us Event-origin retention path. Int64 and string
occurrence identities preserve their complete Subject/Event/occurrence tuples;
the preparation key describes the Subject carrier, not the occurrence carrier.
This capture qualification does not add Journey/History consumers or new source
forms and timestamp units. Starts-only fixed anchors still reject
new return inputs. A separately registered SQLite Event-origin Anchor Metric
observation supports one direct int64 sum-zero Metric without coordinates or
filters, using elapsed or calendar windows. Its preparation reads flat candidates and packs
them into typed Arrow lists before local consumption, preserving overlapping
window contribution multiplicity and business order. Own anchors and exact
deadlines are excluded; fixed and cold reads retain the contribution evidence.
SQLite also connects Journey-origin Anchor binding for this same native
int64/UTC-us shape through the existing local binding consumer. Original Journey
assignments and start instants are retained; binding recovered fixed Journeys
does not access a source or repeat matching. These Journey origins also support
the same direct Metric observations and elapsed/calendar retention, preserving
original assignments and opportunities. Source candidates are prepared before
local binding or consumption. Calendar deadlines use wall-time arithmetic; a
return exactly at the spring-DST 23-hour deadline is excluded, while an elapsed
24-hour observation includes it. Compositions, other input shapes and complete
C18 qualification remain separate obligations.

`AnchorDomain.retention` now binds a returning ParticipantRoleHandle on the same
Subject and captures its finite occurrence envelope before local consumption.
The return capture reuses the Anchor preparation's exact population, including
logical member filters, rather than an earlier Entity ancestor.
Event-origin elapsed witness selection uses public Ibis joins. Calendar and
Journey origins consume fully captured inputs through `ibis_python`. Qualifying
returns make an instance true even with partial follow-up. Absence is false only
when exact Event/source/version coverage includes the entire half-open window;
otherwise its Cell is `Unknown(insufficient_followup)`.
Exact-bound declarations and observed receipts are evaluated per Anchor window.
Overlapping or adjacent intervals can establish continuous coverage; a weaker
declaration cannot discard an observed proof, and a gap remains unknown.

`LogicalRetentionResult` / `MaterializedRetentionResult` retain the original
instance Omega, complete truth partition, exact starts/deadlines, return witnesses,
coverage and Subject map. Their `status`, `known_true()`, `known_false()` and
`unknown()` views retain the original denominator and deterministic bounds.
An external status predicate filters the receiver without certifying that the
receiver's own Cells are unknown; only its own status predicate grants that proof.
`by_subject(rule=mv.any_anchor())` or `by_subject(rule=mv.every_anchor())` fixes the
nonempty-fiber Subject image as a new Omega and returns the paired
SubjectRetentionResult family.
A true selected instance status requires its exact SubjectBinding for `members()`;
a true selected Subject status requires no mapping argument. Other selections
reject member projection. Bounds have no arithmetic or rollup operations.

The current MaterializedAnchorDomain retains starts and cannot acquire unseen
return occurrences. Calling retention on it rejects before source access or Run
allocation, with guidance to construct retention before executing Anchors.
Materialized retention results support source-free quantification and status
continuations from their verified complete ledger. Missing or corrupt parts reject
before continuation. This amendment adds no capture API or compatibility path.

The R7.8 evidence index separates actual kernels, transport/read checks and the
original mandatory qualifications. Starts-only fixed retention, inherited R7.7
unfinished targets, same-wheel, remote and full R7/A13 acceptance remain unverified.

## R8.2 connected deviation surface

The seventeen concrete numeric receivers listed in the historical freeze below
now expose `deviation(*, method: Literal["zscore", "mad"], partition_by=())` and return
LogicalDeviationResult. Logical/MaterializedDeviationResult own their four
numeric projections and synchronized result selection. Construction checks the
complete Session/mode/category closure without reading business rows or allocating
a Run. Execution, fixed continuation and restoration use the governed graph and
Store 7. The [R8.2 evidence index](../../superpowers/specs/2026-10-04-marivo-r82-evidence-index.md)
records exact qualified profiles, open requirements and the incomplete phase
status. The remaining statistical families retain their current contracts until
their own implementation stages.

```python
scored = change.deviation(method="mad")
defined = scored.where(scored.score.value.is_defined())
positive = defined.where(defined.score.value.gt(0))
result = positive.execute()
result.show()
result.contract().show()
```

The observed projection preserves only actual original components and Subject
mapping. Reference/deviation/score have fitted quantities and ordinary current-row
statistics; original Metric rollup/attribute requires its original authority.
Selecting a fitted result preserves the original fit. Selecting its numeric
input before constructing another deviation creates a new fitted scope. Use the
result's current contract for mechanically valid continuations.
Tables preserve each fitted column's authority when mixed with categories,
original values, another fit or derived ranks. Rank projections expose the same
retained parts in logical execution and materialized disclosure, while their
current values remain ranks and their original fit scope remains unchanged.
When the new input is an owned fitted field, recovery also verifies the retained
prior fit and its original grid mapping. It never refits that prior authority;
the new fit consumes only its current selected rows, including an empty selection.
Nullable int64 observed fields use Arrow-backed pandas columns so that a Null
Cell does not force nearby large integers through float64 in `to_pandas()`.

## R8.1 frozen statistical Relation API target

The 2026-10-03 R8.1 freeze is a documentation/static contract, not an importable
API or an execution qualification. It supersedes the old statistical Dataset
receiver/container contracts. The [R8 migration ledger](../../superpowers/specs/2026-10-03-marivo-full-algebra-dsl-r8-migration-ledger.md)
owns migration status; operators own formulas, numeric/Cell rules and parts;
Runtime owns execution, publication and recovery; timezone owns grid authority.
The five old wrappers MetricDiscovery.point_anomalies/interesting_windows/
entity_outliers and DeltaDiscovery.period_shifts/driver_axes are actively
withdrawn at R8.5 after the replacement gates close. They have no compatibility
redirect. Deviation/runs and explicit selection/attribute do not inherit their
absolute-score, rolling-window, threshold or axis-screening heuristics.

### Concrete receivers, overloads and views

For this section only, LNumeric is the closed union of LogicalNumericRelation,
LogicalRatioRelation, LogicalRolledNumericRelation, LogicalRolledRatioRelation,
LogicalDifferenceRelation, LogicalSelectedNumericRelation,
LogicalSelectedDifferenceRelation and LogicalStatisticRelation. MNumeric is
their Materialized counterparts, including MaterializedGroupedNumericRelation.
LCategory/MCategory are Logical/MaterializedCategoryRelation and their Selected
variants. These notation aliases are not new Python exports or Help targets.
Numeric admission excludes bool, Duration, date and timestamp for deviation,
correlation and forecast; runs accepts an admitted int64/float64/Decimal receiver
and a unit-compatible predicate. A numerical class carrying Duration does not
silently acquire statistical-method admission.

Each concrete receiver in LNumeric/MNumeric owns the following signatures. All
construction returns a Logical result, including construction from fixed inputs:

```text
LNumeric.deviation(*, method: Literal["zscore", "mad"],
    partition_by: tuple[LCategory | MCategory, ...] = ()) -> LogicalDeviationResult
MNumeric.deviation(*, method: Literal["zscore", "mad"],
    partition_by: tuple[LCategory | MCategory, ...] = ()) -> LogicalDeviationResult
LNumeric.runs(*, where: BoundPredicate) -> LogicalTimeRunResult
MNumeric.runs(*, where: BoundPredicate) -> LogicalTimeRunResult
LNumeric.correlate(*others: LNumeric | MNumeric,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | None = None) -> LogicalAssociationResult
MNumeric.correlate(*others: LNumeric | MNumeric,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | None = None) -> LogicalAssociationResult
LNumeric.forecast(*, horizon: ForecastHorizon, model: ForecastModel = naive(),
    interval_level: float = 0.95) -> LogicalForecastResult
MNumeric.forecast(*, horizon: ForecastHorizon, model: ForecastModel = naive(),
    interval_level: float = 0.95) -> LogicalForecastResult
```

A Logical receiver whose closure is fixed-only may consume corresponding fixed
categories, predicates and other fixed-only Logical numeric inputs. Source
closures may consume source Logical dependencies only. Annotation variants do
not establish mode: classification visits every dependency, including category
and predicate leaves, and rejects mixed/foreign-Session inputs before reads or
Run allocation. No Dataset input overload, column-name lookup or callback exists.

The new result types are Logical/MaterializedDeviationResult,
Logical/MaterializedTimeRunResult and Logical/MaterializedForecastResult.
Logical/MaterializedAssociationResult extend their existing family. The exact
field table below applies to both full and selected results. Every field is an
owned projection, never an independently reconstructed observation:

| Result | Logical field types | Materialized field types | Quantity/authority |
| --- | --- | --- | --- |
| Deviation | observed/reference/deviation/score: LogicalNumericRelation | same fields: MaterializedNumericRelation | observed retains input quantity and actual K; reference/deviation are new fitted quantities in the input unit; score is dimensionless |
| TimeRun | start/end: LogicalTemporalRelation; count/duration: LogicalNumericRelation | corresponding MaterializedTemporalRelation/MaterializedNumericRelation | count is int64; duration carries DurationType("us") from the current bound grid; start/end are frozen instants |
| Association | coefficient: LogicalCoefficientRelation; selected: LogicalBooleanRelation | coefficient: existing MaterializedCoefficientRelation; selected: MaterializedBooleanRelation | coefficient is a descriptive row statistic; selected is the frozen winning-lag flag |
| Forecast | prediction/lower/upper: LogicalNumericRelation | same fields: MaterializedNumericRelation | ModelPrediction and PredictionIntervalBound, never Observed quantity |

LogicalCoefficientRelation is the necessary Logical counterpart of the existing
coefficient family, not another Association family. Numeric view classes do not
grant original Metric rollup/attribute: derivation and contract() require the
actual quantity and retained parts. TimeRun.duration reuses the current Duration
physical/predicate contracts; it is not a Journey duration result and has no
Journey denominator or new Duration ranking/statistics directory.

For each of the four result families R:
`LogicalR.where(predicate: BoundPredicate) -> LogicalR`,
`MaterializedR.where(predicate: BoundPredicate) -> LogicalR`, and
`LogicalR.execute() -> MaterializedR`. Predicates consume owned or exactly
corresponding fields. Selection restricts all result views together while
retaining original fit, grid/condition, search or training scope. Logical
projections can compose before the single public execute boundary; Materialized
projections bind the same immutable Artifact and receipts. Coefficient selection
keeps the existing coefficient-selection family and its new current-row
summarize/rank/table obligations, without granting members or original rollup.

ForecastHorizon, ForecastModel, periods(1..1000), naive(), drift(), and
seasonal_naive(periods=s>1) retain their factory-only values and exact names.
The correlate default changes from Spearman to Pearson: existing J4/A04 examples
and regression inputs must explicitly request method="spearman" when migrated.
No alias preserves the old default or the old multi-Metric Dataset receiver.

### Type examples and disclosure obligations

These are future type/execution requirements, not runnable R8.1 tests:

```python
# Given same-Session, same-mode, exactly corresponding numeric inputs:
scored = change.deviation(method="mad")  # LogicalDeviationResult
defined = scored.where(scored.score.value.is_defined())
selected = defined.where(defined.score.value.gt(3))
next_members = selected.observed.members()  # Requires actual Subject parts.
segments = daily.runs(where=daily.value.lt(-0.20))  # LogicalTimeRunResult
associations = a.correlate(b, c, method="spearman")
chosen = associations.where(associations.selected.value.eq(True))
future = daily.forecast(horizon=mv.periods(4), model=mv.drift())
fixed = future.execute()  # MaterializedForecastResult
```

Positive examples cover each concrete receiver and Logical/Materialized result
projection. Negative examples reject omitted deviation method, invalid Literal,
list partition_by, foreign categories/predicates, bool interval_level, raw int
horizon, model strings, Dataset/column inputs, 1/17 correlation quantities,
duplicate quantity identity, mixed closures and foreign Sessions. Shape/parts,
not Python numeric inheritance, reject scalar correlation, Entity runs, explicit
Entity/category lag, incomplete grid and forecast history/future continuation.
Static negative typing targets cover required/Literal/tuple/horizon/model/input
types. Session identity, closure mode, arity, parts and bool interval_level are
runtime rejection targets: Python typing admits bool as float and cannot prove
those state-dependent conditions. The two proof classes must stay separate.

Each concrete public symbol resolves beneath analysis.dsl.<ConcreteType> through
the existing native Help registry; factory targets retain analysis.forecast_models
and their focused callable leaves. No statistical namespace, alias index or
renderer inventory is introduced. The new methods/types become exports/Help
only in the package that connects their full public execute/Store/recovery path.
F14 budgets and state-aware cards/errors are owned by the Runtime disclosure
section; English/Chinese latest examples and API/CLI align at that same boundary.

### R8.3 connected complete-grid runs

`NumericRelation.runs(where=...)` now builds `LogicalTimeRunResult`; execution
returns `MaterializedTimeRunResult`. The four owned fields are `start`, `end`,
`count` and `duration`. Conditions classify every original cell before maximal
segmentation. Missing grid coordinates and partial cells reject; unavailable
Cells break segments. Selection retains the original condition scope.
An input also needs actual retained coverage facts. A derived ratio whose
endpoints lack those facts rejects with a structured analysis error; the domain's
grid declaration alone does not establish coverage. Continue from a covered
original observation or an owned fitted field retaining its grid mapping.
The same requirement applies to time association and forecast consumption:
missing endpoint coverage raises a structured analysis error before fitting,
including when every current ratio Cell is Defined.

For the native-table int64 UTC/us route, an original complete daily observation
may be grouped by the same grid and rolled up before runs. R9.4 connects this
Group key on SQLite, PostgreSQL, MySQL, Trino Iceberg and local ClickHouse
MergeTree through the existing prepared graph and classification owner. It adds
no float/Decimal, other time-shape or file Group declarations. Retained numeric
inputs continue through the existing fixed route without current Semantic or
source access; exact hits do not execute another segmentation kernel.

```python
segments = daily.runs(where=daily.value.gt(20))
fixed = segments.execute()
selected = fixed.where(fixed.count.value.gt(1)).execute()
mv.table(start=selected.start, end=selected.end, count=selected.count,
         duration=selected.duration).execute().show()
```

The implementation transports grid_cells, condition_cells, run_cells and a zero
Finding policy. Actual Subject mappings alone grant members. Duration uses
exact UTC microseconds and supports unitized predicates; it gains no ranking
capability. Connected execution does not establish the full frozen R8.3 matrix.


### R8.5 public statistical cutover

NumericRelation.deviation/runs/correlate/forecast and their typed Result families
are the canonical statistical entry points. Metric Dataset discover/correlate/forecast,
the Candidate/Association/Forecast Dataset families, their exclusive compiler,
execution, publication and codecs are physically retired. Old descriptors and
family registrations reject; no forwarding aliases or dual-read migration exist.
Forecast factories, observation, distribution reconciliation, scalar/input bindings
and common graph Findings remain under their existing owners. R4 Spearman keeps
its existing implementation identity, pair_counts part and recovery boundary while
using the common exact association arithmetic kernel.

The R8.1 immutable inventory and all prior qualification records remain historical
evidence. Current R8.5 requirements and symbol/test dispositions are reported in
the R8.5 evidence index; this cutover does not qualify R8.2–R8.4, R8.6, R9 or R10.


### R9.3 remote input-read consistency (2026-10-06)

PostgreSQL, MySQL, Trino and ClickHouse domain preparation reads already bound
inputs independently. Source changes between those reads are permitted. No
repeatable-read transaction, database snapshot or cross-read revision guarantee
is required or created. The retained `independent_reads` authority binds the
actual acquired inputs and definitions; it does not claim that their database
versions coincide. All source inputs still precede local evaluation, and fixed
continuations consume the retained inputs without rereading the source.
Business completeness declarations and actual read coverage keep their separate
meaning; an exhaustive read does not prove business completeness. Exact key,
multiplicity, deadline, cancellation and cleanup checks remain mandatory.

### R9.6 source-check consistency amendment (2026-10-06)

The user additionally permits source checks and subsequent native calculations to
read different source versions. Check evidence records its own actual query and
does not certify facts acquired by a later query. One logical node/Run/result
identity is not a promise of one physical scan or one source revision; native
subexpressions may be evaluated by independent statements. Failed checks still
refuse the dependent operation, and no failure changes the selected route.

Qualified pure-native plans query their closed Ibis expressions directly rather
than capturing an intermediate Arrow table solely to stage it back into a source.
Terminal primary and required-part data are retained as Artifact payloads in
Store 7. Independent checks may still produce temporary Arrow validation results
and retain their own query evidence; this strategy removes intermediate stage
capture/restaging, not check-result reads. The final primary/parts' actual schema,
complete keys, Cells, bindings and internal arithmetic remain validated before
atomic publication and on recovery. A mismatch remains a
failure even when source changes are permitted. Registered preparation/local
consumers still capture all source dependencies before local selection; fixed
continuations remain source-free. Read-only permissions, unchanged Ibis SQL
submission, the shared 600-second deadline, cancellation and resource ownership
are unchanged. This amendment grants no new backend/method key or qualification.


### Explicit business completeness on original observations

`LogicalAnalysisDomain.observe(..., complete_during: tuple[TimeScope, ...] | None = None)`
extends the existing observation entry. The declaration belongs to the exact
Metric graph, contribution identity, receiver and original TimeGrid captured by
this call. It requires `during=grid.window`, one original sum or sum-zero Metric,
no `at` or contribution coordinates, and non-partial original grid cells. Native
production is admitted for existing DuckDB table/Parquet observations; it does
not introduce another source route or any domain snapshot.

Each supplied scope must be absolute with aware datetime bounds and lie within
the original grid. At most 64 scopes are accepted. Their UTC-normalized union is
canonicalized, including overlapping and adjacent intervals. A bucket is complete
only if its entire original half-open interval lies in that union. `None` retains
the existing observation policy; `()` explicitly declares no complete buckets.

The native source read remains physically complete. `coverage__complete` retains
that actual acquisition fact. The same quantity-bound CoveragePart owns the
canonical business windows, `coverage__business_complete`, and the original
partial value/tag/reason. Original additive components stay bound and unchanged.
Uncovered buckets expose `Unknown(insufficient_business_coverage)` with no value;
covered buckets retain their original Defined/Null policy. Unknown is not Source
Null, an empty bucket, zero, or a failed acquisition. The producer does not infer
business completeness from a successful query.

Publication, fixed reads and cold recovery independently compare declared
windows against the original bucket boundaries, verify physical completeness,
reconstruct the partial Cell for original additive-state integrity, and verify
the public Cell against the business policy. The coverage declaration contributes
to quantity and graph identity. Changed declarations cannot reuse another result.

Business-covered observations cannot use original-state rollup or coarsening,
even when all currently requested buckets happen to be declared complete; their
current contract omits those continuation hints. A fresh original observation is
required for a different domain or completeness declaration. Fixed selection
retains the declaration and partial support; selection cannot restore an original
full-grid statistical input.

Ordinary numeric sums supply actual full-grid scalar Unknown directly. Three
forecasts reject such training Cells with `r8.cell_policy` and publish no partial
result. Runs treats them as unavailable and splits maximal segments at their
original grid boundaries. Duration sums retain their fixed unit; their retained
Duration quotient propagates this Unknown reason into float64. Duration itself
still does not acquire scalar statistical admission.

```python
from datetime import datetime, timezone
complete = mv.time_scope(
    start=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end=datetime(2026, 8, 3, tzinfo=timezone.utc),
)
daily = members.each(grid).observe(
    sum_metric, during=grid.window, complete_during=(complete,),
)
```
