# Operators and Frames

Status: current method contracts, 2026-10-08. This document owns the analysis
algebra's method equations, Cell policies, sufficient state, numerical rules and
conditional continuations. [Analysis Design](python-analysis-design.md) owns the
public model and compiler path; [Runtime](session-state-and-runtime.md) owns
execution, publication and Store 9 recovery.

## Results and continuations

The current families are AnalysisDomain; Category/Boolean/Temporal/Numeric/Ratio/
Difference/Statistic/Coefficient relations; and Ranking, Attribution, Journey,
History, Funnel, Anchor, Retention, Deviation, TimeRun, Association and Forecast
results. Each admitted shape has concrete Logical/Materialized variants.
Unsuffixed family names here are prose, not additional exported constructors.

`session.members(...)` constructs the member domain. Domain/relation/result
methods construct Logical work; `execute()` returns its Materialized pair.
Materialized receivers construct fixed continuations from their saved graph and
parts. The old Population/Metric Dataset construction and axis-mutation methods
are absent.

Every result has immutable identity, owned fields and a bounded repr. Results
that enter typed analysis expose `contract()` with exact actions and Help targets.
Materialized inspection uses bounded `show()` and isolated `to_pandas()` copies.
Terminal tables have export/read operations, without analysis continuation.

A continuation depends on the exact domain, quantity, input binding and required
parts. Missing Subject maps, coverage, original components or temporal authority
cannot be reconstructed from values or inferred from a family name. New successor
construction and execution enforce their own method contracts; recovery trusts
locally committed results under Store 9.

K denotes the continuations admitted by those bound facts and parts. It is not a
family-wide method list or a permission to reconstruct lost state.

## Reusable algebra

BindProject, MapCorrespond, CellDerive, RowState, OriginalReduce and PartsTransport
are the shared meta-rule families. Concrete methods have their own versioned
identity and are derived through the single MethodRegistry. Specialized matching,
replay, allocation and statistical rules retain their own algorithmic owners.

Current-row `summarize` constructs a new RowStatistic quantity over represented
rows. Original `rollup` merges the retained Metric components and finishes under
the same governed equation. Empty state can be mergeable even when its finished
mean or ratio is Undefined. An absent/unknown component is never that empty state.

Selection transports the same primary/part key mapping. It can preserve some
capabilities and revoke others, including complete grids or full partitions.
Projection/display cannot manufacture a contribution partition, Subject binding
or original aggregation state.

Each physical implementation must qualify the exact ordered input types, domains,
source/fixed shape, time authority, checks, retained/output parts and numerical
policy. The semantic tables below do not claim every type/backend combination
is executable. Unsupported shapes reject explicitly before work; they do not
retry a different numerical or execution route.

## Evidence and field matching

Entity identity/version grain and source parsing are trusted declarations.
Constructor guarantees, call assumptions and completed invocation checks remain
distinct. Selection preserves unique keys; grouping constructs unique targets.
To-one structure proves single-valuedness, not total matching.
SQLite, MySQL and ClickHouse observations reject nested contribution-coordinate state;
relationship inference does not change backend admission or substitute a path.

`LogicalAnalysisDomain.read(field, *, at=None, via=None,
match_verification="check")` and its governed variants use declared field owner,
complete relationship keys and captured version facts. Both scalar reads and
ordinary member observations infer unique directed keyed to-one paths when via
is omitted or None. Explicit routes bind roles by root identity, independent of
argument order; partial observation overrides leave other roots automatic.
Contribution attribution and complete-key classification are independent bindings.
Versioned fields still require their own explicit attribute instant; the observation
window does not choose their version. Same-owner/same-version
reads need no matching query. Unknown path/version totality creates an exact
`mapping_total` obligation. `match_verification="assume"` removes only that
call's matching query and retains the assumption, without a new Null policy.

`ExactKeys(verification="check")` and `ExactKeys(verification="assume")`
similarly distinguish checked pairing from a call assumption. Both policies
enter definitions, execution keys and frozen calls. Materialized contracts
report assumptions as not checked. Actual method input, finite-value,
missing-operand, denominator and coverage rules remain mandatory.

## State and Cell contracts

Each row is a distinct semantic method family. Method, implementation and state
versions have separate owners; a changed contract is never silently interpreted
as an old version. These component names describe semantics, not a second codec:
physical columns and bound part roles belong to the shared graph protocol.
The four semantic states and method-owned reasons are unchanged by compact
storage: Known/Validity/Encoded carriers preserve Defined zero, present Null,
Undefined and Unknown distinctly. The value's Arrow validity may identify one
statically declared missing state; it cannot infer a reason from row values.
See [Runtime](session-state-and-runtime.md#retained-schemas-and-method-state)
for the exact encoding and recovery boundary.

All state carries exact quantity/contribution identity, input binding, full output
keys, coverage, Cell policy, type and method/state versions. Component state and
primary output agree under finish. A missing state is never a valid empty state.
A semantic allowance below still requires a qualified physical implementation.

| Method family | Input Cell / empty policy | Sufficient state and required parts | Permitted original-state K |
| --- | --- | --- | --- |
| Metric sum | Declared Null/empty policy; ignore Null only when declared; admitted empty zero or Null is explicit | sum, non-null count, row count; original contribution/coverage and component part | Merge sum/counts across proven disjoint contributions, then finish under same policy |
| Metric Entity/Measure count | Entity count counts represented identities; Measure count follows declared non-null policy; empty 0 | checked int64 count, row count and exact counted unit | Merge counts only over disjoint original contributions |
| Metric min/max | Declared non-null support; empty/all-null follows Metric Null policy | typed extremum plus non-null and row counts | Same extremum method over admitted contributions; temporal restrictions still apply |
| Metric mean | Declared non-null support; empty/all-null follows Metric Null policy | sum, non-null count, row count | Merge sum/counts, never means |
| Metric weighted mean | Same non-null value/weight pairs; existing Metric zero/missing-weight-sum policy | weighted sum, paired weight sum, paired and row counts, original value/weight refs and units | Merge original paired components; no substitution of current-row weights |
| Metric ratio | Named numerator/denominator policies; zero denominator Undefined or error as declared | Every named original component with its own state, binding and coverage | Merge components independently then finish; no sum/mean of finished ratios |
| Metric linear | Ordered signed components, compatible units and explicit component policies | Every ordered occurrence's state and sign | Merge components independently then finish; retain branch distinctions |
| Metric occurrence combine | One occurrence per canonical component with its own root, filter, route and time range; no cross-occurrence Cell substitution | Every occurrence's original component state, coverage and complete target key; no retained set/sketch promise | Merge each occurrence independently, then combine on the complete target key; never a per-column projection product, root intersection or row-order alignment |
| Current-row count | Every current instance, including Null/Undefined/Unknown; empty 0 | checked int64 count, current instance unit/domain | RowStatistic count-state merge on disjoint retained row contributions |
| Current-row count_defined | Inspect Cell tag; only Defined counts; empty 0 | checked int64 count, current instance unit/domain and original tag policy | Merge this statistic's counts, never recast as count_all |
| Current-row sum | All consumed values Defined and finite; admitted empty 0 | sum and row count, new RowStatistic identity | Same statistic's sum-state merge, disjoint current-row contributions |
| Current-row min/max | All consumed values Defined and finite; empty Undefined(empty_min/empty_max) | optional extremum and row count | Same statistic's extrema merge; empty state is neutral |
| Current-row mean | All consumed values Defined and finite; empty Undefined(empty_mean) | sum and row count, including valid (0,0) | Same statistic's sum/count merge; Undefined empty Cell is not a zero input to summarize |
| Direct count_distinct | Declared value identity; Null excluded; empty 0 | Final value and input/method evidence only; no retained set/sketch promise | No original rollup or attribution |
| Direct median/percentile | Finite non-null values; Metric empty policy; exact linear interpolation | Final value, q, defined operation and actual algorithm/precision evidence; no distribution/sketch promise | No original rollup or attribution |
| Semi-additive time fold | Per declared spatial-before-time order, sample/time policy and coverage | Exact ordered evaluation keys plus pre-fold components sufficient to restore that order; method identity includes first/last/min/max/mean/percentile | Only qualified fold/reduction with retained state and order/disjointness proof; finished values alone insufficient |
| Cumulative | Each endpoint consumes [anchor(e), e), independent of display start | Endpoint, anchor, base components, interval coverage and ordering | Spatial merge with matching endpoints/base policy; no summing overlapping cumulative endpoints |

Subject/member maps, classification maps, full coordinate tuples and temporal
parts are required whenever the successor consumes them; scalar components alone
cannot reconstruct them. Original contribution overlap or non-commuting folds
reject absent a registered proof/restoration method. A RowStatistic is a new
quantity and cannot acquire the original Metric's units of contribution or K.
Current-row weighted mean and its named StatisticalWeight surface are not public
capabilities; existing private rules do not activate them.

## Numeric carriers and ordinary Metrics

The physical value families are signed int64, finite float64, Decimal(p,s) with
1 <= p <= 38 and 0 <= s <= p, and fixed-duration values in s/ms/us/ns. Boolean
is not numeric. Every qualification records the exact input/output/state type,
not merely the family name. Invalid values and overflow reject before publication;
there is no saturation, wrapping, silent float coercion or route retry.

| Method | int64 | float64 | Decimal | Duration / date / timestamp |
| --- | --- | --- | --- | --- |
| count/count_defined/count_distinct | checked int64 result/count state; count_distinct retains full declared identity | Same count result; nonfinite distinct input rejected | Exact decimal equality for distinct, checked count | Duration distinct preserves unit and exact ticks; date/instant distinct preserves typed identity; count methods may count these relations without numeric coercion |
| sum / linear | checked int64 output/state | finite float64 output/state | native Decimal sum; linear components use Ibis promotion and minimal scale adaptation | Duration sum/linear preserve tick unit with checked int64 ticks; date/timestamp numeric sum rejected |
| min/max/first/last | preserve input type | preserve finite input type | preserve (p,s) | Duration preserves unit; time-valued read/selection preserves physical type, no timestamp-to-float numerical aggregation |
| mean | native Ibis mean, float64; retain sum/count | native Ibis mean, finite float64 | Ibis Decimal result type with an explicit transport cast; retain widened sum/count independently | Duration keeps checked tick state and nearest-even tick finish |
| Metric weighted mean | native product/sums, float64 finish | independent numeric value/weight types; finite float64 | Decimal/int, Decimal/float and different Decimal scales admitted through Ibis and minimal adaptation; numerator and weight sum retain separate types | Duration values still require nonnegative int64 weights and nearest-even tick finish |
| Metric ratio | independent checked component states, Ibis division | finite native result | independent Decimal/int/float components; float64 result, no custom Decimal quotient algorithm | Same-unit Duration inputs retain their unit checks |
| source-native median/percentile | native continuous quantile; float64 output may lose large-integer precision | native continuous quantile, finite float64 output | native continuous quantile with source-owned output precision/scale; precision loss is disclosed | Duration/date/timestamp quantile rejected by the direct-observation methods; no implicit float route |
| semi-additive / cumulative | Base method's matrix plus exact time keys | Base method's matrix plus exact time keys | Base method's matrix plus exact time keys | Temporal keys retain physical precision; cumulative Duration sum follows sum, calendar months cannot become fixed seconds |

Duration is a closed physical type with an explicit s/ms/us/ns tick unit in the
graph, execution key and receipt. Local Parquet sources preserve Arrow Duration
metadata and int64 ticks; DuckDB native INTERVAL is admitted as microseconds only
after a source-native check rejects year/month/day components. Native INTERVAL
does not invent nanosecond precision. No fixed-duration operation casts ticks to
float or turns calendar intervals into elapsed time. Ordinary numeric Metric
sum/mean/weighted mean/ratio/linear use standard Ibis arithmetic. Their result and
retained-state schemas are bound before execution and checked by the existing
provider batch transport. Decimal/int/float operands keep independent types;
different Decimal scales use a common Ibis Decimal only when necessary. Narrow
signed integer and float measures widen once at the graph carrier boundary.
Identity and join keys are never widened through float.

Signed linear finishing promotes components before negation and addition.
Integer and Duration tick intermediates use Decimal(38,0), with the final int64
carrier checked after cancellation. A mixed floating result uses its floating
carrier before arithmetic; integer terms in a Decimal result widen at its scale
before negation, preserving fractional Decimal terms on native engines. These
casts protect intermediate arithmetic without widening saved
component types or converting raw source columns.
Decimal Cell difference operands widen to the comparison method's declared
Decimal(38,s) carrier when native mean produces a narrower Decimal. Source and
fixed difference artifacts therefore retain the same physical schema.

Native rounding, truncation, cancellation and backend-dependent numerical
results are accepted. Ordinary Metric methods do not promise a universal relative
error bound, exact rational finishing, or HALF_EVEN. Mean's native primary may
differ from a sum/count continuation. Ratio and weighted mean still apply their
actual zero-denominator policy; small finite nonzero denominators are not rejected
because an old error interval spans zero. Nonfinite values, transport overflow,
invalid Cells and missing required state remain errors. There is no retry with a
different numerical algorithm.

The bounds of downstream comparison/reference/statistical methods apply to their
represented input Cells. They do not certify native aggregation error against raw
facts. Independently stricter attribution, Duration, temporal-fold and current-row
statistic methods retain their own admission and numerical contracts. Exact and
approximate quantile identities remain distinct: native rounding never authorizes
switching to a sketch. Retained sum/count and paired numerator/weight components
continue to own rollup; averaging projected means or ratios remains invalid.

### Native arithmetic and retained state

Ordinary Metric mean, weighted mean, ratio and linear implementations and their
rollups use contract version 5 and `native_numeric` precision. Source mean uses
Ibis `mean`; ratio uses `/`; weighted mean uses the same non-null pair mask for
both sums. Sum, min/max and direct quantiles already use standard Ibis operations.
A scalar numerator cast works around SQL dialect rewriting of a shared division
operand; it does not rewrite stored components. Explicit output casts reconcile
Ibis's inferred types with the provider's strict native cursor transport. The
provider does not adopt `.execute()` or `.to_pyarrow()` conversion implicitly.

Coordinate state records component types independently. Fixed consumers merge
those actual saved types locally, then project to the captured result type. Cold
reads preserve the saved primary without recomputation. New continuations may
round differently from source execution. Store 9's committed-state trust boundary
is unchanged; there is no compatibility migration or new content audit on reads.
Duration and temporal-fold helpers with independent callers remain available.

Fixed original-state consumers share `numeric_state.merge_components` and
`finish_original`; `merge_original` composes these internal operations. Sum,
sum_zero, count, mean, ratio and weighted mean no longer maintain separate scalar
merge/empty/finish implementations. Linear and extrema keep their existing
method policies in the same kernel. Each component, support count and absolute
magnitude keeps its actual saved Arrow type, independently of the display type.
Coordinate-part transport that needs only totals merges components without
finishing a discarded display value. State layout, quantity, units, contribution
bindings, weighting and empty policies remain owned by the registered method.

Consumption still indexes complete primary/state/coverage keys, rejects duplicate
insertions and missing components, and checks retained coverage and Cell/state
agreement. Numerical failures are structured execution failures. Current-row
`summarize` continues to aggregate represented result rows; it does not replace
original-quantity `rollup`.

ClickHouse native weighted products and retained sums widen exact intermediates
where its ordinary carriers can wrap. Product/state carrier checks remain
mandatory; backend overflow is a structured execution failure, not accepted
rounding. Other routes retain native operations and their necessary output casts.
Downstream error bounds for native Metric Cells start at their represented values;
they do not certify the error of the original raw-fact aggregation.

## Composition and conditional continuations

All methods require exact ordered input bindings, full typed keys, input Cell
policies, units, and actual physical qualification. Every transported part is
restricted by the same key mapping as its primary; a value-only result cannot
claim a continuation that requires a missing part. In this table S means strict
selection and admitted current-row statistics; F means source-free fixed
continuation with all required parts. Current-row statistics require their independently qualified type matrix;
this table does not activate additional reducers or numerical carriers.

| Method identity | RequiredParts and consumption | Output / permitted K |
| --- | --- | --- |
| map_correspond (ExactKeys, UnionKeys, one-to-one, period variants) | Complete ordered typed keys; design and target binding; one-to-one relation/time evidence or complete bucket map as applicable | Exact matched/missing-side map and original endpoints; correspondence is not original-state rollup |
| cell.difference | Correspondence, ordered endpoint Cells and recursively frozen quantity templates; matched values Defined/finite | current−baseline; endpoints, presence and policies retained; S/F, members only with Subject map, attribute only under its separate rule |
| cell.relative_change | Same as difference | (current−baseline)/abs(baseline); dimensionless; zero baseline Undefined(zero_baseline); S/F, no automatic attribution |
| cell.ratio | Exact or bound one-to-one correspondence; ordered endpoints and quotient-unit proof | Zero denominator Undefined(zero_denominator); explicit missing-side yields Undefined(missing_side); S/F, never original rollup/share |
| parts_transport (where/view/limit/member projection) | Every referenced predicate input, exact same-domain or retained inclusion mapping, Subject when requested | Same quantity and precisely restricted parts; membership retains selection basis, no reread/reselection; K derived from retained state |
| domain.cohort | Full target Subject domain, complete opportunity domain and coverage, SubjectBinding, all predicate inputs | Exact selected Subject set plus quantifier/decision evidence; F, source observation only for source members, no unknown qualification silently dropped |
| reference.share | Same-measure Singleton reference, support inclusion and additive/allocation proof; full-partition proof only if claimed | Same-key dimensionless share with frozen reference; S/F, no original rollup |
| reference.penetration | Complete member reference Ω, selected member set B, exact identities and intersection | Singleton intersection-count / reference-count; empty reference Undefined(empty_reference); S/F |
| reference.standardize | ReferenceWeights, exact complete unique strata, compatible statistical Entity proved from original Metric/runtime Metric sum/count/linear/mean/weighted_mean/ratio | Singleton weighted standardized value and frozen strata/reference; S/F, no original rollup |
| display.rank | Numeric input and exact Category partition maps, full original ranking domain | Same-key values/ranks plus deterministic order, partition/tie policy; S/F and global limit, no recomputation on selection |
| display.table | Ordered complete-key Relations, exact input/view bindings and key proof | Terminal table; export/read only, no analysis K |
| attribution.additive_difference | Absolute Difference with complete endpoint states, additive partition/allocation proof, axes, coverage and target reproduction | C_i−B_i, allocated side views and scope/resolution reconciliation; S/F and ranking views; filtered output loses complete-partition K |
| attribution.component_mix | Absolute Difference over original mean/weighted_mean/ratio; per-side additive N/W, complete partition, valid totals and original policies | N_i/W_total side terms, their difference, independent scope/resolution checks; same K restrictions as additive attribution |

Reference-weight construction is a pure binding operation, not a second
arithmetic method. Predicate methods are closed tag/scalar/composite variants
consumed by parts_transport or domain.cohort. Their result is a predicate, not
an independently published Relation. Logical attribution axis expansion adds
explicit observation dependencies; Materialized expansion uses retained parts
only. Relative or nested Difference, ordinary ratio, standardized quantities,
distinct and quantile do not receive attribution merely because endpoints exist.
Additive original linear components may qualify only with complete additive
partition and endpoint reproduction; no general FormulaBasis is introduced.
Attribution axes accept corresponding classifications to select retained roles.
A bare Dimension Ref requires one retained binding on each endpoint, and the
complete ordered axis bindings must agree across endpoints.

`ExactKeys(verification="check")` requires unique full typed keys and equal images;
double empty is valid. Entity identity and constructor guarantees are trusted.
A shared captured key domain supplies equality only through nodes that preserve
that domain; equal DomainSignatures, row counts or Entity refs do not suffice.
An unknown equality defaults to an actual check. `ExactKeys(verification="assume")`
records a call-specific assumption and omits that equality check on both source
and fixed routes. It preserves ExactKeys semantics; it does not select an
intersection or introduce missing-value synthesis. A required operand missing
during local consumption still fails. UnionKeys retains its own missing policy. Keep preserves MissingCoordinate
separately from Present(Null/Undefined/Unknown) and does no arithmetic on a
missing row. metric_empty requires complete original observation/coverage and
the concrete Metric's empty finish; filtered/ranked/limited or missing-version
rows are not empty contributions. It never overwrites a Present non-Defined
Cell. A synthesized Defined endpoint participates normally; a synthesized
Null/Undefined retains that tag and original reason in the result (no arithmetic).
Unknown coverage cannot authorize synthesis. The existing side still must pass
strict numeric consumption. This explicit Null case follows original sum/mean empty
policies; historical prose mentioning only Undefined is not a fill-zero rule.

### Predicate and cohort consumption

Before selection, every actual input must correspond to the receiver's complete
consumption domain; a retained inclusion may restrict an ancestor input but
cannot supply missing rows. All child checks run before truth composition.
where's ordinary numeric predicates require Defined finite values. Category,
Boolean and Temporal comparisons require Defined values of the stated type;
there is no implicit truth conversion. is_defined is total on the four Cell tags
and returns false for Null/Undefined/Unknown. No tag check masks another child's
consumption error. Thus all_of(x.is_defined(), x.gt(0)) fails on Undefined while
an explicit first selection followed by a comparison on the selected view can
succeed. L1 applies only to the common fully defined domain.

cohort uses the same type/finite/unit checks over the full opportunity domain.
Ordinary scalar comparisons may produce unknown for an existing Unknown Cell;
Null/Undefined remain hard errors. State predicates remain total. Missing keys,
opportunities or coverage are errors, not new Unknown Cells. AllOf is false if
any child is false, true if all are true, otherwise unknown; AnyOf is true if
any is true, false if all are false, otherwise unknown; Not preserves unknown.
These rules apply after every child passes its consumption checks.

For each target Subject let t/u/f count all true/unknown/false opportunities.
any_instance is true if t>0, false if t=u=0, otherwise unknown. at_least(k) is
true if t>=k, false if t+u<k, otherwise unknown. Both are false on complete empty
opportunity domains. Nonempty all_instances is false if f>0, true if f=u=0,
otherwise unknown. Empty all_instances follows its explicit true/false/undefined
policy. Every target Subject must have decidable qualification before producing
an exact AnalysisDomain; unknown or undefined rejects the whole result. Retain
targets with zero opportunities to evaluate that policy. These truth rules do
not grant global arithmetic on Unknown Cells or relax where.

### Comparison and reference numerical contracts

I=int64, F=finite float64, D=Decimal(p,s), T=fixed Duration(s/ms/us/ns).
Homogeneous operand families are required unless explicitly stated.
Boolean/date/timestamp are not numeric. These are method-specific contracts,
distinct from ordinary Metric native arithmetic above. Every source/fixed route
needs its own exact qualification. No cross-family coercion, Decimal rescaling
or Duration unit conversion is implicit.

| Method | I | F | D | T |
| --- | --- | --- | --- | --- |
| absolute difference / additive attribution | Checked int64 difference and published state | Finite float64 difference | Same-scale inputs; exact Decimal(38,s) result/state | Same-unit exact checked ticks, unit preserved |
| relative change / ordinary ratio / share | Exact integer numerator/denominator until one float64 finish | Finite float64 finish with denominator stability check | Decimal(38,max(s_left,s_right,6)), one HALF_EVEN finish | Same-unit tick ratio, exact until float64 finish; relative change likewise dimensionless |
| scalar/state predicates | Exact comparisons; bool excluded | Finite exact binary64 comparison | Exact equal-scale comparison | Exact same-unit ticks |
| ranking | Exact ordering, int64 defined ranks | Finite value ordering, int64 ranks | Exact decimal ordering, int64 ranks | Exact tick ordering, int64 ranks |
| standardize | I values with I/F dimensionless weights, float64 finish | F values with I/F weights, float64 finish | D values with same-scale D weights within weight input; Decimal(38,max(s_value,6)) finish | Rejected: no standardized Duration quantity method |
| component_mix | Exact N/W until float64 side finish, float64 contributions | Finite float64 side/contribution | Exact Decimal components; Decimal(38,max(s_N,s_W,6)) side/contribution | Rejected: no tick-rounded component allocation method |
| penetration / cohort counts | Checked exact int64 identity counts; penetration finishes float64 once | Not a numeric-input algorithm | Not a numeric-input algorithm | Not a numeric-input algorithm |
| table / transport | Preserve | Preserve (reject malformed nonfinite Defined payload) | Preserve | Preserve |

These restrictions belong to standardization/allocation and do not change
independently admitted Metric/Duration methods. Current-row reducers likewise
need their exact consumer qualification.

Integer/Decimal/tick intermediates must not pass through float. Mathematical
intermediate numerator/difference/score may use exact widened arithmetic; each
stored int64/tick state or output and every declared Decimal precision is range
checked. In particular abs(-2**63) for a score or denominator must not wrap.
Overflow, nonfinite values, unsupported scale and incompatible units reject
before publication. Negative baseline uses abs(baseline) only for relative
change; ordinary ratio keeps denominator sign. Zero policies precede division.
Float results use the represented-input error model: propagate operand bounds through
subtraction, plus R(r); for division use its denominator-interval bound. Exact
integer operands enter with zero error and finish once. Float comparison/rank
operate on represented values without epsilon ties. Numeric qualification tests
use independent Fraction/Decimal or raw-fact oracles and vary row order/batching.

share requires support inclusion and compatible additive measure or admitted
allocated side terms. It promises [0,1] only with nonnegative terms and positive
reference; signed shares otherwise remain signed and make no such promise.
Zero denominator is Undefined(zero_denominator). Complete partition is an
independent proof, not inferred from a sum close to one. penetration counts
B∩Ω using complete identities, not overlapping category sums.

Standardization freezes the following choices. Every stratum key must exist
exactly once on both sides, including zero-weight strata. Weights must all be
Defined, finite, nonnegative and dimensionless. I/D weights sum to exactly one
using exact arithmetic. F weights use an order-independent exact sum of their
binary64 values for validation, accepted iff abs(sum−1)<=1e-12; the represented
weights are used unchanged, never normalized. The unit is the statistical-unit
Entity, distinct from measurement units. Empty strata and zero total reject.
Every positive-weight value must be Defined and finite. At exactly zero weight,
Null/Undefined/Unknown values remain retained but are not multiplied or consumed
as numbers; malformed/nonfinite Defined values still reject. Positive weights,
however small, get no epsilon exemption. The receiver quantity must explicitly
admit averaging comparable stratum values with one common measurement unit;
it cannot smuggle unlike denominators/units into a mean.

Products/sums for I/D use exact widened arithmetic until the single result
finish; checked stored D state preserves s_value+s_weight (<=38). F products
and sums propagate the method sum/product bounds and the weight-sum acceptance
error is disclosed separately, not corrected. The result retains all weights,
values, zero-weight statuses, keys and fixed reference identity. It is a new
standardized quantity; no original Metric merge or actual-population claim.

### Rank, attribution arithmetic and reconciliation

Ranking partitions by complete Category tuples; empty tuple means one partition.
Within a partition Defined finite values sort by requested order, exact ties by
canonical typed instance key. ordinal assigns 1..n; dense increments by one per
distinct value; min/max use the first/last occupied ordinal of each tied block.
Non-Defined values/ranks retain their tag and reason, sort after Defined values,
and tie-break by instance key (no artificial ordering of missingness severity).
Global output order is canonical partition tuple, Defined before non-Defined,
numeric rank, instance key. This is display order, not business event order.
limit takes its global prefix, integer 1..100000 excluding bool. Each partition's
Top-K requires explicit defined-rank filtering then rank<=k; dense/min/max may
retain more than k rows. Neither operation recomputes ranks or fixed references.
Canonical keys compare typed components in declared order, native exact numeric/
temporal order and Unicode codepoint string order; no repr/hash order. Typed
real-null and Other have separate tags; Other sorts after ordinary coordinates.

Attribution accepts nonempty unique ordered axes; hierarchy requires >=2 axes.
joint emits full tuples; hierarchy emits every authored prefix with resolution
in the key. Each scope/resolution is separately complete and reconciled. Both
endpoint quantities must reproduce from retained complete components before
allocation. Unknown coverage, overlapping contributions, illegal folds or
missing states reject independently of residual. Relative/nested changes do
not inherit these methods automatically.

additive_difference publishes C_i−B_i. component_mix publishes N_i/W_total on
each side, then their difference; it never subtracts unallocated group ratios.
Both overall endpoints must be Defined and totals valid under original policies.
Zero W_total rejects, including 0/0; for float a denominator error interval
spanning zero also rejects. A structural N_i=W_i=0 contributes zero; W_i=0 with
nonzero N_i rejects as contradictory, preserving the accepted typed-operators
rule rather than broadening it from algebraic cancellation. Negative basis is
permitted only where the original component policy admits it. Endpoint/partition
proof is independent of this numerical check.

Numerator and denominator error bounds follow their independent component
carriers. An integer or Decimal component has zero floating roundoff bound;
a floating component requires its own retained finite nonnegative magnitude.
A floating numerator does not require a floating denominator, and an exact
numerator does not suppress a floating denominator's error interval.

top_k is None or integer 1..1000 excluding bool. Before arithmetic, select once
on the union of both sides' full basis using descending abs(C_i)+abs(B_i) for
additive, abs(W_i,current)+abs(W_i,baseline) for component_mix; typed coordinates
break ties. Multi-axis mapping follows authored order within each mapped parent,
including Other. A typed Other tag and mask distinguish a mapped remainder from
a real string "Other" or null category. Hierarchy reuses the same mapping;
never independently select each side or resolution.

For complete scope/resolution let D be the independently computed input target
and S the sum of published contributions. Additive I/D/T must reconcile exactly
in their exact carrier, without float conversion. Float arithmetic retains the
accepted threshold abs(D−S)<=max(1e-12,1e-9*max(abs(D),abs(S),1)); numerical-oracle
bounds above must also pass, so reconciliation is not a substitute for precision
qualification. Decimal component_mix checks exact rational component identities
before finish and applies that same threshold using exact Decimal arithmetic
to rounded published side differences; it does not widen the tolerance or cast
to float. Each side is rounded once at its declared scale, its contribution is
the exact difference of published side terms, and D is the reproduced endpoint
difference at that scale. Excess rounding residual rejects; no balancing row or
residual redistribution is allowed. Tests must include high-precision and
many-small-partition examples at this bound.

Selection transports original target/basis/rule, original reconciliation scope,
complete Other mapping and selected keys. It unconditionally revokes current
subdomain completeness, even if selected contributions happen to sum to D.
No shared helper, legacy registration or residual grants distinct_membership,
distribution_shapley or a second attribution executor public qualification.

## Domain methods

### Ordering, matching and reach

Occurrence preparation binds declared complete identity/version grain, exact
Event/participant definitions, physical time precision and source authority.
Declared key uniqueness, version nonoverlap and source parsing are trusted
premises, not automatically repeated source-health queries. Actual matching,
ordered-event and consumed-input requirements retain their method-owned checks. Time separates different instants. Integer/enum sequence is validated
for all captured occurrences of a Subject, including uniqueness across the
Events covered by that order authority; bool/unknown enum/duplicate sequence or
contradicted precedence rejects. A deterministic typed-key enumeration does not
resolve business order.

The only methods admitting a remaining simultaneous partial order are:

| Closed case | Invariant retained output and independent discriminator |
| --- | --- |
| preparation and Event-role Anchor binding | all exact occurrence/Anchor rows as sets; canonical typed-key display order only; shuffled input preserves every binding |
| one-step every_start matching | one assignment per distinct start, identical reach=true and Subject map under either final-sharing policy; first_per_subject is excluded |
| replay of a tied group strictly after an already known terminal state | every occurrence remains its own transition_from_terminal violation with the same terminal state; no legal transition or interval change; retain complete occurrence identities and compare shuffled traces as canonical sets |

Every other consumed ambiguous group requires sequence/precedence sufficient
for a unique relevant order. Equal final states are insufficient when assignment,
transition, interval, violation, classification or continuation parts differ.
The rules above are local proofs for these closed cases, not permission to
enumerate permutations or use a general confluence callback. Ties across
irrelevant Events not in the request are not read. Actual ambiguity is an
execution error before publication, with an exact business_order repair.

Matching starts in the half-open cohort_window, follows up strictly before
completion_through, and uses the earliest qualified occurrence after the
previous assigned step. first_per_subject selects one earliest qualified start;
every_start creates one Journey per start. Repeated Event refs share one input
capture. One occurrence never fills two distinct steps; missing intermediate
steps cannot be skipped. Intermediate occurrences may be reused across attempts.
Shared final assignment may finish several attempts; exclusive final assignment
reserves each final occurrence for the earliest qualified unfinished attempt.
Final reservation never forbids intermediate reuse. The one-step case assigns
the start once, without creating another final-event consumption. Subjects with
no start produce no synthetic failure Journey.

Reach is a Boolean Cell at every exact step of every Journey. True is an
assignment, False is a proved unreachable/absent step, Unknown is insufficient
coverage. Earlier Unknown cannot become False merely because a later Event has
broader coverage. Proved earlier failure makes later steps unreachable. Absence
uses exact attempt interval and Event/source/version coverage, not whole-input
max time, empty rows or a count. Damaged/contradicted claims are errors rather
than Unknown. All checks consume the same captured input used by matching.

### Duration, dropout and Subject image

The closed duration statuses are complete, incomplete, coverage_censored,
not_entered and entry_unknown. Complete owns both assigned endpoints and elapsed
duration. Incomplete owns a known entered start and proved follow-up through the
exclusive bound. Coverage-censored owns entered start and the known follow-up
prefix. Not-entered has proved absence of the from-step; entry-unknown lacks that
fact. Noncomplete duration is Undefined(not_completed); absent/unknown entry
timestamps retain their corresponding Cell reason, never a shared physical NULL.
observed_duration is the elapsed known follow-up interval for entered rows, capped
at completion if completed; it never uses the last observed Event as follow-up.
No entered-but-unknown negative interval is manufactured.

Duration tick unit is the exact admitted occurrence/boundary unit s/ms/us/ns.
All consumed endpoints must share that unit or be exactly representable in it;
no truncation or implicit cross-unit arithmetic is admitted. Native timestamp us
and each Parquet timestamp unit have separate qualifications. Tick subtraction
and published states are checked int64; exact intermediates never use float.
Mean is exact sum(ticks)/count. For sorted ticks x and quantile p, linear
interpolation uses h=(n-1)*p, floor/ceil neighbors and exact Fraction arithmetic;
median has p=1/2 and p90 p=9/10. Each finish rounds once to nearest-even ticks,
preserving unit and disclosing absolute rounding error <= 1/2 tick. Empty
completed sets yield Undefined(empty_completed_set), not Null/zero. Overflow or
nonfinite/invalid time input rejects. Published sum state must itself fit int64.

The admitted current-row Duration reducer here is row.mean for a bound
Duration NumericRelation, with exact tick sum/count state and the above finish.
Fixed Journey status, timestamp and microsecond Duration fields support current-row
`count` and `count_defined`; grouping by the retained Subject coordinate counts
Journey rows within each complete Subject key.
Journey status and temporal-field selection preserve values and Cell tags/reasons
through repeated `where` and fixed recovery; they do not become key-only domains.
This includes CompletedJourneys.duration and intervals().observed_duration;
it does not activate Decimal reducers, generic Duration sum/min/max/quantile or
weighted mean. Dwell median/p90 belong to dwell's domain method only. The Journey
10/30/100 seconds oracle is 140/3 seconds before tick finish; the mean of the two
Subject means is 60 seconds and is a different quantity.

Dropout before a noninitial exact step is True only for known started and
resolved absence/unreachability before that step, False for known reach, Unknown
for unresolved follow-up. It remains first_per_subject only. SubjectBinding is
total/single-valued on its exact Journey/Interval/Anchor/violation domain;
selection transports it, and members takes a set image without reads. This
does not replace Journey opportunity multiplicity with Subject counts.
Every-start opportunity quantification uses existing full-opportunity cohort
methods with their exact three empty policies; no implicit subject funnel or
select_subjects alias is introduced.

### Funnel, period comparison and ratio-mix allocation

Funnel counts exact int64 components over the same canonical assignment. At each
step cohort_count is all starts, resolved_cohort_count is known current reach,
entry_count is known reach of the previous step (all starts for the initial
step), resolved_entry_count is entered rows with resolved current reach,
reached_count is True, lost_count is entered False and coverage_censored_count
is entered Unknown. For noninitial steps resolved_entry=reached+lost and
entry=resolved_entry+coverage_censored. Conversion-from-first finishes
reached/resolved_cohort; conversion-from-previous finishes reached/resolved_entry;
loss-rate finishes lost/resolved_entry. Initial conversions use the known start
cohort; initial loss is Undefined(initial_step). Zero denominators are
Undefined(zero_denominator), including a legal empty ungrouped dense funnel.
Axes bind historical Dimension values at the first assigned occurrence;
complete actual tuples including real null are retained, without Cartesian
invented groups. Group components must reproduce the ungrouped target exactly.

Funnel-period comparison binds identical pattern/matching/Subject, the same
explicit population realization/definition, exact Event and axis definitions,
compatible time authority, equal start-window elapsed length and equal
post-window follow-up length. Relevant follow-up must be complete. Outer pairing
uses full step/axis keys; an absent side gets count zero only with complete
domain evidence of group absence. Rates retain Undefined at zero denominator.
MissingCoordinate remains distinct from an existing non-Defined rate. Ordinary
NumericRelation.compare gains none of these domain-specific zero rules.

funnel_ratio_mix@v1 binds one noninitial exact step. Let Ec/Eb be positive total
resolved-entry counts, and Li,c/Li,b the per-basis lost counts:

```text
target = Lc/Ec - Lb/Eb
loss contribution_i = (Li,c - Li,b)/Ec
loss side(current, baseline)_i = (Li,c/Ec, Li,b/Ec)
denominator_mix contribution_i = Li,b * (1/Ec - 1/Eb)
denominator_mix side(current, baseline)_i = (0, -Li,b * (1/Ec - 1/Eb))
```

Combined sides reproduce the current/baseline overall rates; each contribution
is its own current-minus-baseline side term. These are allocated terms, not
actual subgroup rates. Exact count/Fraction state persists until one finite
float64 finish; zero denominator, missing partition or contradictory components
reject independently of residual. Each finite finish obeys the rational
precision bound; reconciliation additionally uses the existing threshold
max(1e-12, 1e-9*max(abs(expected), abs(actual), 1)), never as a precision substitute.
Every joint/hierarchy resolution reproduces both sides and the target separately.

Common Top-K uses current+baseline resolved-entry count, checked exactly, within
each authored axis prefix; ties use the full canonical typed coordinate key.
Mapping is shared across sides and loss/mix terms. Real null, real "Other" and
typed Other with active/other masks stay distinct. Hierarchy preserves every
ordered prefix. Contribution rank uses abs(contribution), then resolution/full
typed key/kind; zero overall delta retains valid contributions, with
Undefined(zero_total_delta) total shares and separate empty positive/negative
pool reasons. Views filtered later retain original reconciliation scope and
revoke selected-domain complete-partition claims. Logical missing axes become
explicit same-assignment expansion dependencies; fixed missing axes/components
reject rather than reading lineage/current Semantic.

### Replay, History views and retained truth

`session.lifecycle.replay(model, population=members, window=window,
seed=from_inception(), completeness=())` and the paired LogicalHistoryResult /
MaterializedHistoryResult. Population is a required logical same-Session Subject
domain; the model is an exact Ref and owns business order. Catalog entries, fixed
membership, legacy PopulationInput and call-level order overrides reject before
business reads or Run allocation. The graph explicitly consumes both complete
members and occurrence preparation. Construction and planning read no business rows.

Every distinct modeled Event is captured once. Source preparation completes before
registered local replay consumes the captured inputs under the shared 600-second
deadline. Only a tied group reached after a proved terminal prefix can be retained
as a set, with invariant per-occurrence state and violation identities. Other ties
require unique business order; IDs and physical/declaration order cannot resolve them.

The closed HistoryPart@v1 is keyed by the complete Subject key, including empty
lists for no-event/no-interval Subjects. It retains all trigger evaluations,
pre-inception dispositions, legal/self/zero-duration transition trace, violations,
raw/clipped interval boundaries, exact observed ticks and known-prefix coverage.
Arrow/Parquet publication and recovery reproduce these projections from the saved
trace without replaying origin or connecting to source/current Semantic. Violations
use the existing zero-Findings policy. Precision follows the temporal capture contract
and retained loss disclosure. There are no replay row, memory or tie-width quotas.

History exposes the six views and Duration/Subject observation rules below
on the unified local graph path. Logical and materialized History both return
Logical views; materialized receivers retain fixed leaves. Dwell Duration summary
fields cannot be pooled with summarize or rollup. Current-row mean is admitted on
interval observed_duration and retains exact sum/count for fixed merging.

Replay starts at real inception, which may precede the report window; only
source-origin authority proves its absence. A complete origin history with a
modeled trigger but no required inception fails atomically. Complete origin
with no modeled trigger is NotStarted and creates no initial-state interval;
insufficient origin is Unknown. Pre-inception observed triggers remain captured
with pre_inception disposition and cannot establish/advance state. They are not
legal transitions or post-inception illegal-transition assertions.
Without origin authority, even an observed inception has unknown_origin disposition
with no state, inception, interval or legal/violation assertion. Beyond the proved
prefix, nonterminal follow-up has unknown_followup disposition and preserves the
known prefix without inventing transitions. A terminal state reached inside the
proved prefix remains absorbing, so later invariant terminal violations can still
be retained as a set despite incomplete follow-up. Coverage remains censored.
After known inception, illegal_transition records the exact trigger and keeps
state; transition_from_terminal records its own occurrence and also keeps state.
Legal transitions include self-transitions and zero-duration intermediate states.
Canonical transition ordinal follows business order; canonical physical
enumeration of the terminal-only violation case carries no business ordering fact.

Every input Subject has inception/known-prefix/coverage classification even
without intervals. in_state is True for known requested state, False for known
other state or NotStarted, Unknown for insufficient authority. Checkpoints are
in [window.start, window.end]; end observes the left limit and never consumes
an occurrence at end. Distribution binds historical axes at each checkpoint,
keeps all declared ModelStates for each actual full axis group, and reports
Unknown and NotStarted separately. share_among_seeded conditions on definitely
seeded Subjects and does not claim population coverage.

Distribution row grain is checkpoint × model_state × full axes. The
seeded_subject_count and coverage_censored_count fields describe one Subject pool
per checkpoint × full axes and repeat on each model_state row; they must not be
summed across states. A zero known_state_count cell can represent an alternate
state of an already seeded Subject. Its row complement is therefore not a
NotStarted or censored Subject pool. Help extracts these constraints from the
public distribution docstring; contracts, cards and terminal table columns read
the frozen HistoryViewPart request without opening a source.

Transitions emit the model's complete declared TransitionPair domain with zeros;
count comes from all legal trace entries in the window and share's denominator
is all such modeled transitions, including self/zero-duration entries. Intervals
retain original boundary causes and clipping/censoring status. Dwell owns
completed_window_fragment_duration@v1: clip first; include completed fragments,
including left-clipped completed; exclude right/coverage-censored fragments from
duration statistics while retaining their counts. Exact completed ticks plus
sum/count and sorted order statistics are required; grouped finished mean/p90
cannot roll up. Two Histories cannot be bag-merged to replay. State/pair summaries
cannot reconstruct Subject membership or a transition trace from intervals.

Duration disclosure reads the actual DurationType or retained Arrow column unit.
Logical Dwell parent contracts use the existing History field type owner before
execution; materialized parents use their retained Arrow schema. Both expose
the duration fields' units and conversion without source reads or new Runs.
History Duration uses microsecond ticks: duration_unit=us and
seconds = ticks / 1000000. For example, 86412333333 us is 86412.333333 seconds.
Exported pandas timedelta columns use `.dt.total_seconds()` (or
`total_seconds()` for an individual timedelta). Dwell parent cards, Duration
fields and terminal table columns share this conversion fact; disclosure does
not convert or round stored values.

### Retention truth and conditional K

Anchor retention fixes the full Subject-by-Anchor Omega before return reads.
Observed qualifying return establishes K+ even with partial follow-up. No return
establishes K- only with exact bound coverage through the exclusive deadline;
otherwise it is K?. The three disjoint sets cover Omega. For nonempty Omega,
lower=|K+|/|Omega| and upper=(|K+|+|K?|)/|Omega|, exact counts then one float finish;
empty bounds are Undefined(empty_omega). 25/5/70 yields [0.25,0.95].
any_anchor is True if any true, False if all false, otherwise Unknown;
every_anchor is False if any false, True if all true, otherwise Unknown.
Subject projection explicitly replaces Omega with its nonempty-fiber image.
Status views/selection retain their original Omega and bounds; unknown members
selection refuses. Overlapping windows preserve every (Anchor, occurrence) use,
without disjointness or scalar-bound rollup. All fixed K requires the exact
retained definition, instance domain, mapping and method parts, not display values.

## Statistical methods

The following method/state contracts are version 1 unless the registered
physical implementation states its separate contract version. Statistical
numerical certification is independent of ordinary Metric native arithmetic.

### Deviation fit, Cell table and irreversible scope

`deviation.zscore@v1` fits c=mean(x) and s=sqrt(mean((x-c)^2)) over Defined finite
values in each explicitly authored category tuple. `deviation.mad@v1` fits the
median c and s=(7413/5000)*median(abs(x-c)). If raw MAD is exactly zero, s is the
mean absolute deviation around the same c, with no 1.4826 factor. The scale
branch is closed: population_stddev, scaled_mad, mean_absolute_deviation.
Even-sample medians are exact means of the two central original values.
Empty partition_by means one fit over the entire current receiver domain;
Null categories are real partition coordinates and other coordinates never
implicitly partition the fit. Every row has equal weight.

| Original Cell / fit | observed | reference | deviation | score |
| --- | --- | --- | --- | --- |
| Defined finite, n>=2 and raw scale>0 | original Defined | Defined c | Defined x-c | Defined (x-c)/s |
| Defined finite, n=1 | original Defined | Defined c=x | Defined zero | Undefined(insufficient_samples) |
| Defined finite, n>=2 and raw scale=0 | original Defined | Defined c | Defined x-c | Undefined(zero_scale) |
| Null / Undefined / Unknown, n>0 | original tag/reason | Defined c | original tag/reason | original tag/reason |
| Null / Undefined / Unknown, n=0 | original tag/reason | Undefined(no_valid_samples) | original tag/reason | original tag/reason |

An empty receiver has no synthetic row. Its fit scope records original_count=0,
n=0 and no_valid_samples. Nonfinite Defined, malformed/duplicate identity,
foreign correspondence or false coverage claims are hard failures, not excluded
samples. Per-partition original_count equals defined+null+undefined+unknown;
n equals finite Defined count. original_count and n are separately disclosed.
observed retains original unit, quantity/identity, Subject and actual authorized
parts; reference/deviation retain the unit but have new fitted quantities; score
is dimensionless. The three derived quantities have no original rollup/attribute.

Fit scope is frozen at the node. where on the result selects all four views;
rank/limit uses existing RankingResult and preserves n/c/s/branch/input bindings.
Neither rewrites the original fitted domain. where-before-deviation creates a
new fit. Explicit shared input/fit nodes execute once per DAG/Run; separately
constructed equal definitions are not common-subexpression merged. Subject
projection from selected observed uses its real original mapping, never scores
or display row positions.

### r8_numeric_v1: widened arithmetic and one finish

Required input families are int64, finite float64 and Decimal(p,s),
1<=p<=38 and 0<=s<=p. Each vector retains one exact physical input type; bool,
Duration/date/timestamp are not statistical numerics. Counts and horizon/lag
ordinals are checked int64. Integer sums, differences, squares, products,
medians, means, covariance, innovations and variance use widened integer/Fraction
state before finishing. Decimal inputs are validated at their captured (p,s),
then converted losslessly to scaled-integer/rational intermediates. 1.4826 is the
exact rational 7413/5000, not a binary float literal. No input rounding or ambient
process Decimal context is permitted.

| Input / output | observed | reference/deviation and forecast prediction/lower/upper | score / coefficient |
| --- | --- | --- | --- |
| int64 | int64 | finite float64, one nearest representable finish from widened intermediates | finite float64 |
| float64 | exact captured binary64 | finite float64, stable centered/scaled arithmetic | finite float64 |
| Decimal(p,s) | original Decimal(p,s) | Decimal(38,max(s,6)), one ROUND_HALF_EVEN finish | finite float64, one explicit final projection |

Center and x-c, innovations and point predictions use unrounded internal values;
rounding the displayed center before scoring is forbidden. The raw exact
variance/MAD/fallback value decides scale=0, never its displayed rounded value.
For float64, constant/zero tests compare original finite binary64 values and
exact centered identities. Exact binary64-as-rational raw facts are the independent
oracle; stable floating implementations must prove the final bound below for
extreme/near-cancelling/subnormal inputs, row orders and batching.

Irrational sqrt and inverse-normal finish use an isolated ROUND_HALF_EVEN Decimal
working context with Emin=-999999, Emax=999999 and at least 120 significant digits.
For inverse-normal probability p=(1+level)/2, level is its exact binary64 ratio;
start precision is 120+max(0,-floor(log10(min(level,1-level)))). Do not round p to
0, 0.5 or 1 before inversion. Directed enclosing bounds certify the published
rounding/error. If a final rounding boundary is unresolved, refine this same
numeric algorithm by 40 digits under the existing execute deadline; this is not
an implementation/route retry. No unproved exact irrational claim is allowed.

For independent mathematical result r, float64 acceptance is
abs(actual-r)<=R(r), R(r)=1e-12*(1+abs(r)), evaluated without float overflow.
Decimal acceptance requires the specified HALF_EVEN value at output scale;
the disclosed error is at most half the output quantum plus the certified
transcendental enclosure error. Exact rational finishes settle ties exactly;
transcendental rounding is certified before publication. Numerical bounds are
separate from upstream sampling/semantic approximation and business Unknown.
A finite output that cannot meet its bound remains unqualified. Stored precision,
nonfinite output, count overflow or unrepresentable final unit value reject the
whole action. No saturation, epsilon clamp or implicit Decimal rescale repairs a numerical
failure; unsupported physical keys reject before execution.

Correlation permits all ordered pairs of I/F/D input families, including
unequal Decimal precision/scale across endpoints: each endpoint retains its own
original type. Cross-endpoint products/centerings use lossless rational
intermediates, not a common float input cast. Spearman/Kendall rank/compare each
vector in its original exact order; ranks are exact half-integers. This does not
authorize within-vector mixed values or cross-family predicate/partition coercion.
Unary deviation/forecast have no heterogeneous-vector overload. A source-native
physical route must qualify the same exact ordered types and error contract.
The physical precision variant certified_statistical binds r8_numeric_v1
to the eight deviation/association/forecast methods, exact ordered input types,
widened state and declared rounded outputs. It is distinct from exact,
checked_int64, finite_float64 and native_numeric. time.runs retains exact
classification/tick facts with checked counts. Registration does not establish
full backend/type/shape or release acceptance.

Independent oracles use original integers/Decimals/binary64 ratios, sorted order
statistics, centered rational sums, pair counts and the model equations below.
They do not import product numeric helpers, use absolute scores as a
signed-score oracle, or compare implementations sharing one arithmetic helper.
Root/quantile oracle enclosures use separately authored high-precision definitions.

### Runs classification and maximal segments

`time.runs@v1` consumes the complete original grid and every predicate dependency
with corresponding complete typed keys. Pure Entity/scalar or arbitrary timestamp
columns do not qualify. Each non-time tuple is a separate sequence. Validate
physical missing/duplicate/partial cells and false coverage as hard violations;
only an explicitly retained unavailable original cell is a legal gap. Ordinary
where that removed time cells revokes completeness and blocks subsequent runs,
even if surviving coordinates happen to look consecutive.

Each predicate leaf is evaluated before composition. Numeric comparison of a
non-Defined required Cell is unavailable with that Cell's reason; state predicates
are total according to their existing tag semantics. Type/unit/owner/correspondence
errors remain hard failures. Composition is closed: if any dependency is
unavailable, the composite is unavailable; otherwise evaluate the Boolean tree
normally. There is no short-circuit masking of an unavailable sibling. This
classifier is specific to runs and does not weaken ordinary where's strict rule.

Enumerate all original cells in grid order. false, unavailable or a recorded gap
ends the current true segment. Emit every maximal true segment as
[first_cell.start,last_cell.end), count=number of cells, duration=end-start in
exact grid microsecond ticks. Crossing a reader batch carries the unfinished
segment and last grid identity; a batch boundary is not a terminator. DST and
certified unequal periods use actual UTC boundaries, never count*24h.
Run identity binds series/grid, condition definition/version and all dependency
bindings, plus start/end cell identities. Closed left/right termination kinds
are false, unavailable and scope_boundary, with the terminating cell/reason when
present. scope_boundary means observation ended, not that the business condition
resolved. Two-sided adjacent opposite-sign true cells remain one segment.

Retain true/false/unavailable counts for the whole grid, including zero output;
all-unavailable and evaluated-zero are distinct. Post-run where/rank/table selects
segments and never resegments. count can rank; duration supports existing
unitized predicates, with no new Duration ranking directory. Subject images
exist only through original retained SubjectBinding and run->cell->Subject parts.
Projection deduplicates Subjects under existing set rules without changing run
multiplicity; global Time sequences have no members capability.

### Association pairing, statuses, lag and selection

`association.pearson@v1`, `association.spearman@v1` and
`association.kendall@v1` consume 2..16 distinct single-quantity inputs in request
order. Every unordered index pair a<b is evaluated. Quantity identity, not label,
establishes distinctness. Require the same exact complete observation instance
set/coordinate authority and realized unique composite keys; independent source
captures with merely equal row counts/Entity declarations do not prove pairing.
Entity/category rows are statistical units. Time is paired by point, and each
category*time tuple is a separate series. Scalar is rejected.

The disclosed pairing_key follows the retained PairInputsPart.input_domain
instance_key in original order, naming each component's Entity, field and
coordinate role. Pairing uses the complete original observation tuple. For a
composite Entity key, both order_id and member_seq identify the observation;
neither component alone is its identity. Association output keys identify
evaluated candidates, not the original paired observations. The parent,
coefficient/selected views and terminal table columns share this frozen fact
without disclosing identity values or adding Runs.
Terminal tables share identical interpretation facts across an ordered list of
column labels. Different fact values remain separate, so shared disclosure never
conflates different units or pairing keys. Default display budgets still apply.

None means one zero lag. A nonempty range supplies signed int64 offsets only;
explicit lag is rejected on Entity/category even for range(0,1). +k pairs A(t)
with B(t+k) on original grid ordinals and certified coordinates, without crossing
series or collapsing missing rows. Input request order determines direction.
For each pair/lag/series: input-matched=boundary_drop and null+complete=matched.
Only ordinary Null is pairwise deleted after alignment. Undefined/Unknown,
nonfinite Defined, duplicate keys or malformed domains fail atomically.

For complete pairs x,y, Pearson is Sxy/sqrt(Sxx*Syy), where Sxy is the centered
cross-product sum and Sxx/Syy are centered square sums. Spearman is this Pearson
formula on globally assigned average ranks of each complete paired vector.
Frozen Spearman witnesses are validated by one ordered tie-block scan after
sorting, in O(n log n) comparisons, without calling the rank estimator.
Kendall tau-b is (C-D)/sqrt((C+D+T_x)*(C+D+T_y)); C/D count concordant/discordant
unordered observation pairs, T_x/T_y count ties in only that endpoint, and pairs
tied in both are excluded from both factors. Batch-local ranks/coefficient merges
and quadratic source SQL pair joins are forbidden substitutes.

Status precedence is insufficient_pairs when complete<2, then constant_both,
constant_a, constant_b, otherwise valid. Invalid candidates carry Undefined
coefficient with their closed reason and selected=False, never NaN or zero.
A valid candidate requires finite coefficient in [-1,1]; any rounding enclosure
used to finish an endpoint coefficient must be retained, with no arbitrary
near-one clamp. Every pair/series needs >=1 valid lag or the entire call rejects.
Exactly one valid lag wins by (-abs(coefficient),abs(lag),lag), selection version
association.max_abs_coefficient_min_abs_lag_min_signed_lag@v1. All invalid and
valid candidates remain. Pair*lag*series ceiling is 4096, checked statically when
known and at execution otherwise; it is unrelated to input rows/bytes/memory.
where does not recompute coefficients, selected flags or original search_summary.
Coefficient summarize describes current coefficient rows; it is not pooled
correlation, original rollup, Entity selection, causality or inference.

### Forecast model and interval equations

`forecast.naive@v1`, `forecast.drift@v1` and `forecast.seasonal_naive@v1` consume
one complete consecutive time or category*time quantity. All series share the
same captured training grid and approved future grid. Every required training
Cell is Defined/finite. Reject duplicates, missing/partial cells, all other Cell
tags, unapproved continuation and pooling across series. Horizon is periods(1..1000),
level is a finite float in (0,1), seasonal length is an integer s>1 excluding bool.
Minimum n is 2, 3 and s+1 respectively.

For series y[1..n] and future ordinal h>=1, normal_residual@v1 is normative:

```text
naive:
  prediction[h] = y[n]
  innovation[t] = y[t]-y[t-1], t=2..n
  df=n-1; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*h

drift:
  slope=(y[n]-y[1])/(n-1)
  prediction[h]=y[n]+h*slope
  innovation[t]=y[t]-y[t-1]-slope, t=2..n
  df=n-2; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*h*(1+h/(n-1))

seasonal_naive(s):
  prediction[h]=y[n-s+((h-1) mod s)+1]
  innovation[t]=y[t]-y[t-s], t=s+1..n
  df=n-s; sigma2=sum(innovation[t]^2)/df
  variance[h]=sigma2*(floor((h-1)/s)+1)

z=Phi^-1((1+level)/2)
margin[h]=z*sqrt(variance[h])
lower[h]=prediction[h]-margin[h]; upper[h]=prediction[h]+margin[h]
```

Naive/seasonal innovations are not demeaned, including a constant nonzero vector.
Drift removes only its first/last fitted increment and uses df=n-2, not residuals
of a regression on levels. Exact-zero variance is allowed only when every required
innovation is exactly zero. Unavailable/negative variance or a nonfinite required
point/bound aborts all horizons and series. A rounded interval can coincide at a
coarse Decimal quantum without asserting zero innovation: retain nonzero raw
variance and rounding error, never label it exact_zero. Missing variance never
becomes a zero-width interval.

Prediction is ModelPrediction; bounds are PredictionIntervalBound. Their domain
is the same captured future grid. where synchronizes the three views while
retaining original training authority; prediction rank/table and current-row
descriptive statistics do not authorize summing bounds into a total interval.
Association and forecast rank projections retain their original statistical
parts and Finding policy. Same-scope tables containing those ranks capture each column
and its display witnesses through `table_fits`; rank values are verified by
the display owner, not interpreted as coefficients or predictions. Builtin
future-grid lookahead uses absolute instants and enumerates actual civil
boundaries, including repeated and skipped DST boundaries; unused lookahead
must not reject an otherwise complete requested horizon.
Assumption contract is zero_mean_uncorrelated_homoskedastic_normal_innovations@v1,
with drift mean-increment estimation uncertainty. These are nominal prediction
intervals for future observations, not mean confidence intervals, empirical
calibration, causal effects or a guarantee from numerical admission.

### RequiredParts, transformations and closed failures

Part schemas are frozen under r8.<role>/v1, with typed composite keys, explicit
binding/definition/scope/version and separate schema/row-count/receipt. Rational
facts use reduced signed numerator and positive denominator decimal strings;
bounded-root facts bind the exact radicand and directed Decimal endpoints. These
are closed scalar records, not arbitrary reason dictionaries or public exports.

| Role | Required columns/facts and keys | Producer / transport |
| --- | --- | --- |
| fit_inputs | original key, partition tuple, Cell value/tag/reason, input/grid/Subject binding; full original scope | Deviation captures once; selection retains full authority |
| fit_state | partition key, original/tag counts/n, center, raw scale/variance, scale branch, numeric policy, median/order witnesses and input digest | Deviation; immutable through where/rank/limit |
| grid_cells | grid/cell identity, ordinal, original/actual start/end, partial, precision, coverage; per-series complete coordinate map | shared temporal capture; numeric/difference/score transport only if completeness survives |
| condition_cells | series/cell key, dependency bindings, true/false/unavailable and closed reasons | Runs; full classification scope survives selected outputs |
| run_cells | run identity, ordered cell identities, start/end/count/duration and two termination witnesses | Runs; select by run identity, never resegment |
| subject_map | original complete Subject tuple, input cell/instance and run/observed mapping when available | actual Subject owner; never synthesize from public numeric keys |
| pair_inputs | ordered quantity/pair/series/lag, full correspondence bindings, captured pair values/tags and pairing counts | Association; fixed method requires these inputs, transport-only does not qualify the kernel |
| association_state | every candidate key, counts/status/coefficient/selected, input quantity contracts and original search_summary/selection version | Association; selected current rows retain original search authority |
| training_inputs | series/cell key, full training values, training grid and quantity binding | Forecast; no source/calendar reconstruction |
| forecast_state | series key, model/season/n/innovation count/df/slope/sigma2/zero flag/numeric policy; each horizon variance | Forecast; per-series facts, not only min/max summaries |
| future_cells | approved future grid/cell keys, ordinal/start/end, horizon mapping and continuation authority | temporal owner; immutable through forecast selection |
| finding_policy | exact producer/state/extractor/policy/input/scope and count/digest authority | common Runtime owner, including no-Findings policy |

Selected main rows and selected field tables have the same typed current keys;
full fit/classification/search/training authority tables keep their original keys
and explicit scope. Their PartTransform is select_output_retain_scope@v1.
Original observed parts transport only when their existing rule remains true.
Selection revokes complete-time-grid admission for a new runs/forecast kernel;
output transport cannot manufacture lost full inputs. Production and new consumers enforce their input/state contracts. Store 9
recovery decodes committed types and data without replaying numeric proofs;
it must not refit, rerank, resegment, reselect lag or reforecast to replace
missing data. Actual K is the intersection of these parts,
quantity and requested parameters, not the Result family name.

StatisticalRelationError subclasses AnalysisError with code, operation,
method/version, exact input identity, expected, received and repair. Closed codes
are r8.input_identity, r8.input_mode, r8.cell_policy, r8.grid_incomplete,
r8.correspondence, r8.numeric_unqualified, r8.numeric_overflow,
r8.no_valid_candidate, r8.candidate_ceiling, r8.forecast_history,
r8.future_grid, r8.retained_part and r8.numeric_precision. Unavailable scoring is
Cell state, not an execution error. Each code binds actual missing parts/types,
keys, counts or registered qualification; repair uses current state and names
an exact reconstruction/parameter action. Timeout/cancel/resource errors reuse
the existing Runtime owner. There is no generic ValueError or hardcoded repair
catalog substituted for these structured facts.

## Numerical admission and interpretation

Ordinary Metric native arithmetic, exact Duration/Decimal methods, represented
Cell comparisons/references, allocation reconciliation and r8_numeric_v1 are
different numerical contracts. None certifies source sampling, business
completeness or approximation error outside its own scope.

Current-row int64/float64 consumers, the qualified fixed NoTime Entity
Decimal(18,2) sum/mean consumers and the qualified Duration mean consumers keep
their exact declared input/output/state types. The Decimal sum state widens to
Decimal(38,2); its mean finishes once to Decimal(38,6) with HALF_EVEN while
retaining scale-2 sum state. This is not blanket Decimal group/time admission.

Errors carry the expected contract, received facts and concrete repair.
Nonfinite values, overflow, incompatible ownership/types, missing consumed parts
and contradictory coverage fail through AnalysisError subclasses.
Unknown/censoring remains a result Cell only where its method explicitly owns
that meaning. No silent fallback, truncation, imputation or balancing allocation
repairs the computation.

Attribution is algebraic, association is descriptive, and forecast intervals
depend on their stated innovation assumptions. Deviation scores and runs retain
chosen fitting/condition scope; they do not classify business anomalies or
choose the next analysis step. Evidence reads and Findings follow
[Analysis Evidence Access](evidence-access-surface.md).
