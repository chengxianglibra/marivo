# Semantic Expressions

Numerical and conditional authoring uses controlled native Ibis Deferred recipes.
They capture definitions without source reads. Physical types and backend support
are bound when an execution actually needs them. The same closed expression is
used locally and in a named declaration; names do not add execution authority.

## Symbol capture and loader binding

Entity, physical field and Relationship constructors return native typed Ibis
Deferred symbols. Compose them directly, including exact forward references
created with `ms.ref.<kind>(path)`:

```python
import marivo.datasource as md
import marivo.semantic as ms

orders_ref = ms.entity(
    name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"), primary_key=["order_id"],
)
amount_ref = ms.measure_column(
    name="amount", entity=orders_ref, column="amount",
    additivity=ms.additive_all(), unit="CNY",
)
status_ref = ms.dimension_column(name="status", entity=orders_ref, column="status")
paid_at_ref = ms.time_dimension_column(
    name="paid_at", entity=orders_ref, column="paid_at",
)
orders = orders_ref
amount = amount_ref
status = status_ref
paid_at = paid_at_ref

@ms.metric()
def revenue():
    return amount.sum(time=paid_at).where(status == "paid")
```

This fragment requires a declared warehouse and a domain when collected into a
model. The loader binds the complete physical and named dependency graph before
publishing closed Catalog values. It validates default time, permissions, units,
paths and cycles without reading a source or replaying a module. Physical fields
and dependencies may be declared later in the same complete load scope.

`ms.ref.*` also produces exact native lookup selectors for Catalog description,
readiness, preview and source checks. Metadata `.ref` and `.refs` return these
selectors. Retrieve closed analysis inputs through the appropriate Catalog
collection's `.get(path)`; ordinary Analysis never binds a lookup symbol implicitly.
Equality constructs a recipe. Do not use symbols for boolean identity, hash keys,
sets, dictionary keys or container membership. Inspect identities through Catalog
metadata rather than `.key`, `.path`, `.kind` or `.version` on a Deferred.
Unsupported attributes and methods fail when a declaration or parameter captures
the recipe. Ordinary Python attribute access itself does not promise immediate
failure; unconsumed recipes and already formatted strings cannot be traced back.

## Authored bodies and naming

Entity/Source and Dimension/TimeDimension bodies retain restricted Ibis
contracts. They may derive one relation or field from their permitted table
inputs; they cannot execute a query or create a foreign source. ms.bind is
available only in supported restricted Ibis field bodies.

Entity bodies also accept module-level, typed scalar builtin UDFs declared with
`ibis.udf.scalar.builtin`, with at least one argument and concrete argument/return
types. Zero-argument builtin UDFs are not admitted. These build a backend function expression without
executing the Python function body. The captured function name, namespace and
signature participate in definition identity; execution still requires backend
support. Arbitrary Python helpers and Python/Pandas/Arrow UDFs are not Entity
body symbols.

Measure/Metric decorators take zero-argument functions. The validator captures a
leading docstring, fresh local expressions and one final return without invoking
the authored function. Imports, arbitrary helpers, control flow, reassignment,
Ibis tables and ms.bind in these bodies are rejected.
Tuple and list literals are accepted as captured expression arguments, including
`status.isin(["paid", "shipped"])`; lists are frozen as tuples. This does not admit
comprehensions, mutation, subscripting or arbitrary function calls.

The function's lowercase snake_case name supplies declaration metadata. A Python
alias does not rename it; arithmetic produces an anonymous expression. Model
loading collects exported declaration roots and resolves their domain and
qualified identity. Local named expressions can be composed without a loader or
Session. Session-bound Population dependencies cannot be published as reusable
model closures; independently resolvable literal domains can be collected.

Published expressions are selected with canonical `catalog.get("kind:path")`;
`entry.show()` and `entry.definition` explain captured meaning, and `entry.value`
delivers native closed expressions after kind narrowing. Deferred itself has no Marivo show, contract or
public manual schema-resolution step. Do not call the decorated value as a
function after capture.

## Row quantities and Metric equations

| Stage | Meaning | Examples |
| --- | --- | --- |
| Row | Quantity on one exact contribution root | amount - cost; quantity * unit_price |
| Source reduction | Complete contributions under one filter/time policy | amount.sum(time=paid_at); orders.count(time=paid_at) |
| Metric | Completed reductions composed on checked coordinates | revenue / order_count |
| Internal samples | Complete child Metric in each sample, then reduce | revenue.per(customer_id).mean() |
| Window Metric | Reevaluate the original equation on explicit contribution windows | revenue.window(mv.to_date(grain=mv.grain("month"))) |

Row arithmetic requires one root. Different roots reduce independently before
Metric arithmetic, preserving each component's filters, time role and repeated
occurrences. A many-valued relationship is not an implicit numeric join.
Mixing a row quantity with a completed Metric is invalid.

Measure owns unit and business sum permission. Same-unit linear row arithmetic
intersects permissions; constant scaling preserves compatible facts. Products
and quotients do not acquire sum permission from dtype or algebra. An explicit
Measure declaration may assert compatible units or new authorized permission:

```python
@ms.measure(additivity=ms.additive_all())
def order_amount():
    return quantity * unit_price
```

Here quantity and unit_price must be same-root row inputs with an
established business interpretation. Omitted unit/additivity inherits facts;
unknown units stay unknown. Unit assertions do not convert currency or scale.
An assertion cannot erase fixed status-time constraints or refer to foreign axes.
Metric decorators accept domain/context metadata, without unit, time, fold or
numeric-policy overrides.

One numeric Entity output column can have separate Dimension and Measure
declarations:

```python
amount_axis = ms.dimension_column(name="amount_axis", entity=orders, column="amount")
amount = ms.measure_column(
    name="amount", entity=orders, column="amount", unit="CNY",
    additivity=ms.additive_all(),
)
```

Use the Dimension for grouping and conditions, and the Measure for quantity
arithmetic and source reductions. Give them different semantic names; binding the
same column does not merge their roles or grant a Dimension quantity arithmetic
or sum/mean reductions. Existing Dimension counting reductions remain supported.
For a shared derived numeric value, create the column in the Entity body and
bind both declarations to that output column. `@ms.measure` continues to compose
established quantities; it does not convert a Dimension into a Measure. Actual
numeric types and backend support are checked during preparation.

### Sum permission declarations

| Declaration | Permission across native coordinates |
| --- | --- |
| `ms.additive_all()` | All axes may be merged. Takes no arguments. |
| `ms.additive_except(snapshot_at)` | All axes except the named fixed axes may be merged. |
| `ms.additive_over(region, product)` | Only the named axes may be merged. |
| `ms.non_additive()` | No axes may be merged by summing displayed values. |

`additive_except` and `additive_over` require one or more unique Dimension or
TimeDimension fields as positional arguments. Unpack a collection with `*axes`.
The axes must belong to the native contribution root. Fixed axes preserve their
coordinate; they do not remove rows, select a temporal reducer, or declare a
status-time role. A physical Measure's `status_time_dimension` must also be
included in `ms.additive_except(...)`. Explicit internal sample reduction owns
temporal aggregation; these policies do not establish contribution coverage.
Help: `semantic.additive_all`, `semantic.additive_except`,
`semantic.additive_over`, and `semantic.non_additive`.

### Explicit scaling and target units

`ms.rescale(expr, *, divisor, unit)` divides a numeric Row or completed Metric
expression by an authored constant and explicitly declares its target unit.
The source unit must be known after binding. `divisor` is a finite, strictly
positive `int`, `float` or `Decimal`; booleans and dynamic expressions are invalid.
The target follows the existing unit string validation and canonicalization,
including opaque units. The operation preserves stage, source roots, time
restrictions and sum permissions, and uses existing division and null rules.
Its captured recipe identifies the divisor and target unit; completed results
retain that target unit.

Division and rescale use Float64 operands, with ordinary floating rounding for
integers above 2**53 in both native and local arithmetic. This conversion belongs
to the authored arithmetic boundary; integer-only expressions, identity values
and safe final storage conversion retain their existing rules. A Metric rescale
is not automatically rewritten into row-level rescale before reduction, because
that would change the rounding and overflow path.

For a byte-valued `total_bytes` Metric over a complete 86400-second interval,
and an established P95 of complete hourly byte totals:

```python
@ms.metric()
def daily_bandwidth():
    return ms.rescale(total_bytes, divisor=86400 * 1024**3, unit="GiB/s")


@ms.metric()
def p95_bandwidth():
    return ms.rescale(hourly_p95_bytes, divisor=3600 * 1024**3, unit="GiB/s")
```

The author is responsible for the scale factor and physical/business meaning.
An explicit target may cross dimensions, and `divisor=1` is valid. Fixed duration
factors do not follow an analysis window automatically. Marivo does not infer
conversions or introduce a unit registry. Ordinary scalar division retains the
source unit; a Measure unit assertion remains compatible-only when the inferred
unit is known and may establish a previously unknown unit. Physical Measures
declare source units. Help: `semantic.rescale`.

## Predicates and scalar paths

Field comparisons and missing/membership tests produce Deferred boolean recipes.
Use parenthesized &, | and ~, never Python and/or/not or bool. Field NA follows
three-valued logic; only final True selects. Membership leaves test complete
correlated tuples with null-safe field matching. Both sides retain dependencies,
static checks and captured NULL/domain checks even with boolean constants.

- Source observation, members and a single-source Metric.where can combine field
  and Population membership leaves on their own contribution/identity domain.
- A source reduction's where= accepts field leaves.
- Population.where accepts membership leaves over existing coordinates only.
- AnalysisFrame.where consumes a boolean Column from that exact input.

Single-valued field.via((relationship, ...)) captures an explicit directed scalar
path. field.at(anchor) captures a versioned attribute instant. Neither reads
attributes or proves a matching version exists. Source reductions, sample axes
and sum-permission coordinates still require their native field forms.
TimeField.within(scope) creates a half-open predicate; it does not supply the
observation endpoint needed by a window or time-sample reduction.
Exact grammar lives at semantic.predicate and semantic.field Help targets.

## Internal samples and windows

Metric.per accepts a field, a nonempty field/Grouping tuple or a complete
Population, followed by a sample reducer. The entire child Metric completes for
each (external coordinate, internal sample) before reduction. Field samples use
observed keys. An explicit Population preserves its compatible tuples, including
empty samples under the child's empty policy, and may select a subset.

Internal weighted means consume a completed Metric weight on the same sample
domain. Row weighted means consume same-root row value/weight pairs. Both sums
use one common non-NA pair mask; weights need no independent business sum grant.
Signed weights are admitted under the denominator policy, without a convexity claim.

Ordered first/last preserves the declared unique sample time order and explicit
include_null policy. For a sampled status quantity, spatial reduction precedes
temporal reduction, for example bandwidth.sum(time=sample_at).per(sample_at).quantile(0.95).
Per-device P95 values cannot reconstruct the P95 of the combined time series.

Field `sample_interval` supplies optional source coverage, independently of parse,
for sample reducers that require it. Each time-sample `mean` or `weighted_mean`
uses represented samples without scheduled coverage or implicit clock completion,
regardless of arithmetic, conditionals, Duration, windows or native optimization.
Explicit Population sample domains still retain their declared tuples and empty
sample policy. Existing NULL policies and weighted valid-pair masks are unchanged.
Other sample reducers retain their own coverage checks, including inside an outer
mean. A time mean alone does not require a finite interval; windows and strict
sample layers retain their endpoint and bounded-coverage requirements.
Preparation freezes the sampling clock: naive values use parser timezone or
reader authority; aware values use UTC. Output timezone only clips these points
to the actual contribution intervals. The clock is never inferred from the first
row. Missing expected points fail coverage; present points whose completed Metric
is NULL retain the reducer's null policy. Same-time facts finish spatial
reduction first. Without cadence, only represented samples are evaluated.
Local DST gaps create no imaginary points and repeated hours retain distinct
instants. Source wall clocks follow backend-native gap/fold interpretation; a
native choice does not invent missing sample points. Complete sampling requires a bounded
contribution interval. An aware non-UTC civil sampling plan is not supported.

DuckDB, PostgreSQL, MySQL, Trino and ClickHouse finish a qualified direct numeric
source mean followed by one internal time-sample mean in the source. The exact
sample identities and completed inner means remain the inputs to the outer
equal-weight AVG. Both layers skip NULLs and return NA without valid inputs; each
`nulls="error"` asserts no NULLs in its own demanded input domain. Native AVG
algorithms and overflow remain backend-owned. There is no
scheduled-clock coverage check, safety window, sample ranking or local fallback
on this route. Missing clocks are absent samples; off-grid observations still
participate. `sample_interval` never truncates or buckets those observations.
Integer and floating AVG has a declared Float64 output; Decimal AVG retains its
declared precision and scale. Explicit result casts enforce these carriers after
native AVG arithmetic.
Original-input consumers, Duration, windows, explicit sample populations
and nested or conditional formulas retain their existing execution routes,
including explicit local sample reduction where qualified. These choices do not
change the time-mean coverage policy. Native floating order remains backend-dependent.

A demanded coverage failure retains its Metric output name, sample layer, semantic
time field, actual interval/timezone and complete group coordinates. Local and
native compressed routes report up to six failed groups and three real missing
instants per group, with expected/covered counts and truncation flags. NULL values
at represented clocks are not missing clocks. Native witnesses come from the same
statement, without a diagnostic reread. `sample_interval` declares clock points;
it never truncates timestamps into hourly buckets.

Membership allocation rules appear directly in observe.by or Metric.per inputs; they are independent at inner and outer levels. Correlated
member fields stay in one tuple descriptor. [Observation](../analysis/populations-and-observation.md)
owns allocation and output-domain behavior.

Window rules are source-free immutable values: all_history, since, to_date,
trailing_duration and trailing_periods from mv. They attach to a Metric and
require an observation endpoint; nested windows are rejected. They reevaluate
its formula over each window rather than summing completed output cells.
[Temporal semantics](../temporal-semantics.md) owns the exact bounds.

## Numeric policies and physical admission

Policies attach to the actual operation. Sum has no empty parameter: an empty
contribution input returns typed zero; a nonempty all-NULL input returns NULL.
Mixed inputs sum their valid values. Count/distinct count returns zero.
Other admitted sample reducers return NA without valid samples. Existing
nulls="ignore" adds no assertion; nulls="error" asserts non-NULL inputs on the
demanded source/sample domain, including native AVG. When that assertion passes,
the result uses the same fixed computation. Empty input passes the NULL assertion;
this does not require a sample or prove clock/source completeness. Business filling
and sample selection use explicit supported expressions or preprocessing, rather
than another reducer mode. Source-native numeric
overflow also belongs to the backend: an error or a returned wrapped/nonfinite
value is accepted without an overflow precheck, finite-result rejection or local
retry. Source reads retain Infinity and valid NaN; completed Frames preserve these
values separately from NULL through export and persistence. Local numeric kernels
use their actual pandas/Arrow or Decimal scalar behavior without extra
overflow/finite checks or protective widening. Explicit function-domain and NULL
contracts retain their scope.
Division uses `/` as its only public syntax. Division and weighted mean mask
zero denominators before arithmetic and return NA, including cancelling signed
weights. An outer formula cannot overwrite a captured strict NULL check.
Demand-aware native ifelse evaluates its selected branch while retaining static
checks on both; multiplication by zero and coalesce do not catch strict failures.

Exact and approximate reductions are distinct authored operations. An unavailable
route fails instead of substituting the other. Exact quantiles use the native
qualified arithmetic and precision; they do not promise arbitrary-precision
interpolation. DuckDB and PostgreSQL retain their qualified native exact routes;
MySQL, Trino and ClickHouse use source-ranked bounds and typed local interpolation.
Explicit approximate quantiles use DuckDB `approx_quantile`, Trino
`approx_percentile` and ClickHouse TDigest. Approximate median uses the same
algorithm at q=0.5. PostgreSQL and MySQL reject approximation instead of using an
exact replacement. Backend translation, load and readiness do not qualify a method.
Detailed current-value and native-carrier policies belong to
[Operators and Frames](../analysis/operators-and-frames.md).

## Implementation map

- [_deferred_semantics.py](../../../marivo/semantic/_deferred_semantics.py) and [_definition_expression.py](../../../marivo/semantic/_definition_expression.py) own controlled expression binding.
- [validator.py](../../../marivo/semantic/validator.py) owns authored syntax restrictions.
- [expressions.py](../../../marivo/semantic/expressions.py) owns closed Catalog value construction; [_authoring_declarations.py](../../../marivo/semantic/_authoring_declarations.py) owns named Metric capture.
- [_definition](../../../marivo/_definition/) owns closed computation graphs; [_numeric_recipe.py](../../../marivo/semantic/_numeric_recipe.py) and [_numeric_lowering.py](../../../marivo/semantic/_numeric_lowering.py) own actual-type numeric preparation.

## Bound numeric results

Integer sums use native Ibis SUM with an int64 declared output; no input
widening cast is added. Decimal sums return Decimal(38,input_scale), using a
single primary accumulation. Counts retain int64. Bound Decimal arithmetic
uses the [numeric carrier rules](../analysis/operators-and-frames.md), with explicit
Ibis result casts and native kernel rounding. Division is float64; Decimal
mean keeps its declared p/s without promising an exact backend AVG algorithm.
All source-native numeric overflow follows the backend, including native errors,
ClickHouse silent wrapping and floating Infinity/NaN. Local kernels retain their
actual pandas/Arrow or Decimal scalar behavior without extra overflow/finite
checks, protective widening or high-precision emulation. Mean has the same NULL and empty-input completion on all
admitted routes; native time-sample mean does not require scheduled sample
coverage. Other captured
NULL/zero/domain policies, identity and Duration contracts retain their owners.
No extra arithmetic is run to verify overflow.

## Analysis lineage handoff

Resolved named declarations retain inert origin identities when consumed by
Analysis. This includes naming-only Measures whose numeric expression needs no
new unit or additivity assertion. Origin recording does not add numeric nodes or
change computation fingerprints. Analysis captures actual consumed declaration
identities and dependency roles before physical compilation; completed Frames
retain static identities rather than executable semantic objects. See the
[Frame lineage contract](../analysis/analysis-frame.md#静态语义对象血缘).

Named Measure origins stay on their row nodes through expression flattening and
predicate capture. They are excluded from node equality, computation fingerprints
and executable dependency lookup. Predicate usage therefore contributes context
origins, while value usage contributes value origins, including when both occur.
