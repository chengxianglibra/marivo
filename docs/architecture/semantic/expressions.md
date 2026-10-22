# Semantic Expressions

Numerical and conditional authoring uses controlled native Ibis Deferred recipes.
They capture definitions without source reads. Physical types and backend support
are bound when an execution actually needs them. The same closed expression is
used locally and in a named declaration; names do not add execution authority.

## Capture, finalize and resolve

Entity, physical field and Relationship constructors capture typed Ref lookup
keys. Capture the complete physical dependency scope before finalization,
including forward references used by restricted Ibis field bodies:

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
finalized = ms.finalize()
orders = finalized.resolve(orders_ref)
amount = finalized.resolve(amount_ref)
status = finalized.resolve(status_ref)
paid_at = finalized.resolve(paid_at_ref)

@ms.metric()
def revenue():
    return amount.sum(time=paid_at).where(status == "paid")
```

This fragment requires a declared warehouse and a domain when collected into a
model. Finalization validates the physical closure and freezes default-time,
coordinate and dependency facts. It closes capture; late physical declarations
fail. Failed closure checks leave it open for repair. Finalization does not
publish Metrics, read schemas, connect or replay imported modules.
Bare Refs are lookup inputs rather than row or Metric values.

## Authored bodies and naming

Entity/Source and Dimension/TimeDimension bodies retain restricted Ibis
contracts. They may derive one relation or field from their permitted table
inputs; they cannot execute a query or create a foreign source. ms.bind is
available only in supported restricted Ibis field bodies.

Measure/Metric decorators take zero-argument functions. The validator captures a
leading docstring, fresh local expressions and one final return without invoking
the authored function. Imports, arbitrary helpers, control flow, reassignment,
Ibis tables and ms.bind in these bodies are rejected.

The function's lowercase snake_case name supplies declaration metadata. A Python
alias does not rename it; arithmetic produces an anonymous expression. Model
loading collects exported declaration roots and resolves their domain and
qualified identity. Local named expressions can be composed without a loader or
Session. Session-bound Population dependencies cannot be published as reusable
model closures; independently resolvable literal domains can be collected.

Published expressions are read through catalog.fields/catalog.metrics and
catalog.describe(ref).show(). Deferred itself has no Marivo show, contract or
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

Here quantity and unit_price must be resolved same-root row inputs with an
established business interpretation. Omitted unit/additivity inherits facts;
unknown units stay unknown. Unit assertions do not convert currency or scale.
An assertion cannot erase fixed status-time constraints or refer to foreign axes.
Metric decorators accept domain/context metadata, without unit, time, fold or
numeric-policy overrides.

## Predicates and scalar paths

Field comparisons and missing/membership tests produce Deferred boolean recipes.
Use parenthesized &, | and ~, never Python and/or/not or bool. Field NA follows
three-valued logic; only final True selects. Membership leaves test complete
correlated tuples with null-safe field matching. Both sides retain dependencies,
static checks and demanded numerical checks even with boolean constants.

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

Field `sample_interval` supplies optional source coverage, independently of parse.
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
Other admitted sample reducers return NA without valid samples. Strict
nulls="error" is checked on its demanded source/sample domain. Actual source
NaN and Infinity are rejected before pandas conversion can conflate NaN with
NULL; raw reads and numeric reductions retain the same checks.
Division uses `/` as its only public syntax. Division and weighted mean mask
zero denominators before arithmetic and return NA, including cancelling signed
weights. An outer formula cannot overwrite a strict nulls child.
Demand-aware native ifelse evaluates its selected branch while retaining static
checks on both; multiplication by zero and coalesce do not catch strict failures.

Exact and approximate reductions are distinct authored operations. An unavailable
route fails instead of substituting the other. Exact quantiles use the native
qualified arithmetic and precision; they do not promise arbitrary-precision
interpolation. Trino's source route requires explicit approximate quantiles when
appropriate. Backend translation, load and readiness do not qualify a method.
Detailed current-value and native-carrier policies belong to
[Operators and Frames](../analysis/operators-and-frames.md).

## Implementation map

- [_deferred_semantics.py](../../../marivo/semantic/_deferred_semantics.py) and [_definition_expression.py](../../../marivo/semantic/_definition_expression.py) own controlled expression binding.
- [validator.py](../../../marivo/semantic/validator.py) owns authored syntax restrictions.
- [expressions.py](../../../marivo/semantic/expressions.py) owns finalization; [_authoring_declarations.py](../../../marivo/semantic/_authoring_declarations.py) owns named Metric capture.
- [_definition](../../../marivo/_definition/) owns closed computation graphs; [_numeric_recipe.py](../../../marivo/semantic/_numeric_recipe.py) and [_numeric_lowering.py](../../../marivo/semantic/_numeric_lowering.py) own actual-type numeric preparation.

## Bound numeric results

Integer sums return Decimal(38,0); Decimal sums return Decimal(38,input_scale),
using a single primary accumulation. Counts retain int64. Bound Decimal arithmetic
uses the [numeric carrier rules](../analysis/operators-and-frames.md), with explicit
Ibis result casts and ties-away Decimal convergence. Division is float64; Decimal
mean keeps its declared p/s without promising an exact backend AVG algorithm.
Ordinary source overflow follows the backend, including ClickHouse silent wrapping;
local integer kernels are checked. Explicit null/zero/domain policies, identity and
Duration contracts are unchanged. No extra arithmetic is run to verify overflow.
