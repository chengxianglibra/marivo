# Operators and Frames

This page owns nullable values, numeric carriers and current-row statistics.
[AnalysisFrame](analysis-frame.md) owns structure, exact handles, correspondence
and row order; [Observation](populations-and-observation.md) owns source contribution
and sample binding; [Runtime](session-state-and-runtime.md) owns execution and reads.

## Null, empty and arithmetic policies

Values use normal nullable carriers. There are no per-cell Defined/Null/Undefined/
Unknown tags or reason columns. Missing correspondence endpoints are represented
by independent nonnullable boolean presence, never inferred from value NA.

| Operation | Effective samples | Empty / nonempty all-NA result |
| --- | --- | --- |
| Source or internal sum | Non-NA child values, subject to nulls policy | 0 / NA |
| count / count_distinct | Method's declared complete occurrences or non-NA values | 0 |
| min/max/median/quantile | Effective non-NA samples | NA |
| Source or internal mean | Effective non-NA inputs, using the qualified AVG algorithm | NA |
| weighted_mean | One common non-NA value/weight pair mask for both sums | The operator's denominator policy |
| Frame Column sum | Current non-NA represented cells | 0 / NA |
| Frame Column mean | Current non-NA represented cells | NA |
| Frame corr / correlate coefficient | Common non-NA pairs within each current group | NA for fewer than two pairs or an exactly constant endpoint |
| Frame pair_count / correlate n_pairs | The same common non-NA pairs, including zero and duplicates | 0; retain groups even without pairs |
| row_count | All current coordinate rows, including NA values | 0 |

Each operator has one fixed NULL computation rule. Existing `nulls="ignore"`
(the default) adds no non-NULL assertion; `nulls="error"` asserts that the capturing
source/internal reduction has no NULLs on its demanded sample/contribution domain,
including native AVG. Successful values use the same calculation in both cases.
Empty input contains no NULL, so this assertion passes: sum is zero and mean is NA.
It does not prove sample presence or source completeness. A later where, select,
zero multiplier or empty output cannot suppress a required check.

Business imputation or sample selection belongs in an explicit supported input
expression or preprocessing step. For [10, NULL, 30], mean is 20; explicit input
zero-fill gives 40/3, while strict mean fails. No automatic fill, propagation mode
or per-reducer cleaning strategy is implied. Existing first/last include_null
selects endpoint candidates and does not cancel strict checks.

Division uses `/` and masks zero before dividing; both 0/0 and nonzero/0
return NA. Numeric source operands are converted to float64 before division,
including integer operands, so backends cannot truncate an integer quotient
before conversion. Its explicit float64 result cast survives native compilation,
even when Ibis already infers float64. This also prevents ClickHouse aggregate
arithmetic optimization from extracting a NULL divisor from `AVG(x / NULL)`.
Weighted means return NA when valid paired weights sum to zero,
including signed cancellation. All backend-native numeric operations delegate
overflow to the backend: its errors, wrapped values, Infinity and NaN are accepted
without an overflow precheck, result rejection or local retry. Native field reads
likewise retain backend floating values. Local computation also delegates numeric behavior to its actual pandas/Arrow or
Decimal scalar kernel: no extra overflow precheck, finite-value scan, protective
widening, high-precision emulation or retry is added. Native errors propagate;
wrapped values, Infinity and valid NaN remain values. Existing NULL, zero-division,
identity, Duration and storage contracts still apply. Direct projection, is_null
and persistence preserve validity without a numeric scan.
At a bound Float64 arithmetic boundary, local operands are converted to Float64
with ordinary rounding, including integers above 2**53. This applies to mixed
integer/floating addition, subtraction and multiplication, and to division and
Metric rescale, including an authored numeric cast to Float64. It does not change
integer-only arithmetic, exact comparisons, identity conversion or safe final
storage conversion. Failed local Metric formulas
identify the output name, failing expression operation and captured input dtypes.
An unevaluated source subexpression need not
trigger overflow, including under empty output; explicit NULL/domain checks retain
their demanded domains. Arrow-valid NaN is a floating value distinct from NULL.

Source declaration trust does not remove type, identity or captured NULL/domain
checks. A missing scalar join produces NULL without a separate required-match
audit; the consuming operator's captured NULL policy still applies, including
strict rejection by native AVG.

## Physical numeric and temporal carriers

Admitted physical columns use signed int64, float64, Decimal(p,s) with
1 <= p <= 38 and 0 <= s <= p, boolean, string, date, exact naive or timezone-aware timestamp
and fixed-duration s/ms/us/ns ticks. Boolean is not a numeric quantity. Every
physical route fixes its actual input and output types before business execution.
Keys are never passed through float, including integers above 2**53.
UInt64 source identities and quantities use Decimal(20,0), preserving the complete
0..18446744073709551615 domain before semantic predicates, arithmetic and reductions.
UInt8/16/32 use the admitted int64 carrier. This source normalization is a
representation contract, not a guarantee that subsequent native arithmetic cannot
overflow. Exact Decimal transport must not pass through float. Explicit floating
operations retain their own precision contract. Entity body arithmetic remains
subject to the authored Ibis expression; cast operands before potentially lossy
arithmetic inside that body.

Naive timestamp identity preserves its wall-clock ticks and exact unit with
`timezone=None`; neither reader/Session timezone nor a TimeDimension parser
rewrites it. Identity matching requires compatible naive/aware interpretation
and lossless unit conversion. TimeDimension projections independently interpret
native naive values using parser or frozen reader authority. A naive clock alone
cannot distinguish repeated DST occurrences; use an aware/UTC source or an
additional identity component when those are distinct business instances.

`frame.coord(axis)` can supply a scalar Column only for a single-component axis.
Construction remains source-free; preparation rejects a composite identity as a
single quantity before Run admission and business compilation/reads. The complete
identity remains available in output keys, Population tuple predicates and export;
select an existing single-field axis when a scalar coordinate value is needed.

Nullable pandas/Arrow carriers preserve integer and Decimal precision. Stage
normalization checks complete column order, types, nullability, timezones and
unique full keys. Safe storage conversion rejects lossy values rather than
casting them back after loss. Datasource schema metadata remains authoritative
when a provider exposes a different Timestamp or Duration carrier. Source
admission and saved-output export are separate guarantees.

Completed immutable Frame data already owns these integrity checks. Internal
stage handoff copies its rows and attachments without repeating normalization;
new stage results and newly read stored data retain their owning checks.

Source arithmetic uses admitted native Ibis operations with bound result types
and provider-specific output bindings. Integer SUM uses native Ibis SUM without an
input-widening cast; its declared result is int64. Decimal SUM returns
Decimal(38,input_scale). Both use one primary accumulation; count remains int64.
A wider backend accumulator must fit the Ibis transport result when it crosses a
source boundary. For example, DuckDB HUGEINT SUM may fail the native Ibis int64
conversion; Marivo does not retry with Decimal. An explicit business cast such as
`amount.cast(ibis.dtype("decimal(38,0)"))` belongs in an Entity declaration. Decimal add/subtract uses
p=max(p1-s1,p2-s2)+max(s1,s2)+1, s=max(s1,s2); multiply uses p=p1+p2, s=s1+s2.
When p exceeds 38, reduce s by the excess (minimum zero) and cap p at 38.
Mixed Decimal/integer uses the bound integer width (int64: 19 decimal digits).
An unspecified Ibis Decimal literal binds p/s from its literal digits and exponent;
an explicitly typed literal keeps that type. Business row values never select types;
ordinary integer arithmetic remains integer. Division/ratio returns float64;
mean retains its method-declared type, with an explicit Decimal output cast.
Local Decimal scalar work uses its native context; no private precision-120 context
or universal rounding policy is imposed. Explicit round follows its kernel.
Dedicated quantile interpolation/output finishing, exact Duration ticks and
Attribute closure/finite saved-method metadata retain their method and storage
contracts; they are not a generic numeric-safety pass over ordinary results.
Local integer SUM retains the native int64 accumulator, including wraparound;
Decimal SUM retains Arrow's native Decimal(38,s) accumulator. Local means use the
native Arrow mean algorithm. Result metadata describes the actual supported
carrier, without casting an overflowing integer sum to Decimal after the fact.
Final carrier conversion
is safe. Float64 admits
binary approximation, integer low-bit loss beyond 2**53 and ordinary underflow;
backend-native nonfinite results retain their floating carrier. Identity, count,
time and Duration remain exact.
Native intermediate precision, AVG algorithms, floating order and overflow may
differ between source and local execution. ClickHouse may silently return a wrapped
value with a different sign/magnitude; an admitted result may be used in formulas,
filters and ordering. Safe conversion cannot detect prior native wrapping.
Saved results preserve committed values, including Infinity and valid NaN, scale,
dtype, NULL and keys without rounding or recomputation on export/cold read. Existing terminal fusion consumes the same
bound result types; its chosen native path determines overflow behavior. Duration keeps its exact tick unit;
a calendar month is not an elapsed duration. DuckDB native INTERVAL is admitted
as microseconds only after a source-native check rejects year, month and day
components. It does not invent nanosecond precision. Arrow Duration metadata and
integer ticks retain their declared unit on admitted file routes. Decimal precision/scale, quantile
interpolation and any native approximation are method contracts, not promises of
universal rational arithmetic or a global error bound.

MySQL SUM retains its native widened Decimal value instead of a redundant
same-type result CAST, which can clip an out-of-range SELECT result to an endpoint
with only a warning. When that sum crosses a source boundary, safe conversion to
the bound Decimal(38,s) carrier rejects unrepresentable values before publication.
This adds no source scan, overflow precheck or local retry; native arithmetic
inside a fused expression still follows the backend's precision and overflow.

Exact median/quantile uses linear interpolation h=(n-1)q; [0,10] at q=.95 gives
9.5. Native approximate reductions require an explicit approximate definition,
qualified actual type/backend and algorithm identity. An unavailable exact route
never falls back to approximation, and the reverse is also prohibited.

Source quantile and median use only native exact algorithms: DuckDB
`quantile_cont`, PostgreSQL `percentile_cont`, and ClickHouse
`quantileExactInclusive`. MySQL and Trino reject exact source quantiles during
schema-bound compilation; no ranked-bounds or local interpolation substitutes for
an unavailable native operation. ClickHouse admits integer and floating inputs;
Decimal exact quantiles are supported on DuckDB and PostgreSQL. Source Duration
quantiles requiring tick interpolation are unavailable. Integer and floating
results are float64; admitted Decimal routes retain the declared carrier.
Empty and all-NULL inputs return NA; strict NULL checks retain their demanded input.

Explicit source approximation uses DuckDB `approx_quantile`, Trino
`approx_percentile`, or ClickHouse `quantileTDigest`; approximate median uses
q=0.5 on that same algorithm. Sketches consume floating inputs and Decimal
results explicitly converge to the declared Decimal carrier. PostgreSQL and
MySQL reject approximate quantiles and distinct counts during compilation.
Approximate distinct uses DuckDB `approx_count_distinct`, Trino `approx_distinct`,
or ClickHouse `uniqHLL12`. Exact distinct uses `COUNT(DISTINCT ...)`, with
ClickHouse mapped to `uniqExact`. Native aggregate mapping and admission share one
policy across preview and analysis; compilation success alone never qualifies an
algorithm. Algorithms and results may differ across backends; no universal error
bound is promised. These routes do not expand original-input attribution or
physical join topology qualification.

Ordered first/last uses a unique declared time order after completing each child
sample. It must not substitute an unordered backend first/last. include_null and
strict sample policy remain attached to the original occurrence. Window overlap,
per-sample keys and ordered sampling checks survive any legal optimization.

Qualified direct min/max of Float64 time-sample means completes each child mean
before the outer source aggregate on DuckDB, PostgreSQL, MySQL, Trino and
ClickHouse. Strict outer NULL and scheduled-clock coverage checks remain
attached to their original domains; only bounded output summaries cross the
boundary. Failed coverage includes same-statement witnesses for at most six
groups and three missing instants per group, without a source reread. This does not admit arbitrary nested samples, Decimal/Duration or
ordered first/last to the same optimization.

Qualified direct mean of numeric time-sample means also executes on those five
providers. It computes each timestamp's source AVG, then an equal-weight outer
AVG over those completed means. Both layers skip NULLs, return NA without valid
inputs and enforce their captured strict assertions. Native AVG algorithms and
overflow remain backend-owned. This route has no scheduled-clock coverage check, numeric safety
windows, sample ranking or local fallback. Missing clocks do not become samples;
off-grid represented timestamps remain samples. Integer and floating AVG has a
declared Float64 output; Decimal AVG retains its declared precision and scale.
Explicit result casts enforce these carriers after native AVG arithmetic.
Duration, windows,
explicit sample populations and unsupported nested or conditional formulas retain
their existing qualified routes, including explicit local sample reduction where
applicable. Every time-sample mean/weighted_mean uses represented samples without
implicit scheduled-clock completion or coverage requirements. The policy belongs
to each logical sample layer, not the optimizer. Explicit Population domains,
NULL/weight policies and stricter nested reducers remain authoritative.

## Current cells, original Metrics and windows

Frame.reduce computes statistics over represented rows using independent valid
masks. Empty sum is typed zero; a nonempty all-NA sum is NA. row_count counts all current rows. It neither
reconstructs original contributions nor changes a calendar's grain. Row mean 55
can differ from original overall 1000/91; distinct counts 2+2=4 do not prove a
union distinct count of 4 when the actual union has 3 members. Request original
overall formulas with an explicit new observe and their governed source inputs.
An empty member sum may be zero while another member's all-NULL fact sum is NA.
Summing those completed cells [0, NA] returns zero; a new overall source sum over
only the all-NULL facts returns NA. Original contribution summaries retain input
and valid counts during evaluation; completed Frame values cannot reconstruct them.
Sum/mean retain the current value expression's unit. A retained unit describes
physical dimension, not original Metric additivity or permission to roll up removed
axes. Summed daily inventory may still have unit items, and summed ratios may
remain dimensionless; neither certifies monthly inventory or an overall ratio.

AnalysisFrame.correlate is the ordinary Pearson/Spearman entry, with explicit
method and fixed coefficient/n_pairs outputs from one input evaluation.
Column.corr and Column.pair_count compose through reduce, one scalar column per
named statistic, or over, broadcasting that statistic to the original rows.
Both endpoints must be numeric on the exact input; different
units are allowed, Duration is rejected. Each pair uses its own common non-NA
mask after both expression checks. Presence is independent, zero-pair groups
remain, and Spearman ranks within each group's valid pairs using average ties.
The coefficient is nullable float64/unit 1; the count is nonnullable int64 with
no unit. Counts remain when the coefficient is undefined. Self pair_count is
the expression's non-NA count. No source correlation pushdown is qualified.

Correlation retains pair selection and constant/insufficient-pair rules, then uses
pandas' native correlation (average ranks before Pearson for Spearman). It adds no
finite scan or precision-preserving centering conversions. Native NaN coefficients
remain valid NaN. The numeric implementation is owned by
[frame_correlation.py](../../../marivo/analysis/methods/frame_correlation.py).
Group coefficients and counts cannot reconstruct pooled correlation.

Within one current-input evaluation, paired statistics reuse successful endpoint
expression evaluation and typed conversion. A coefficient and its pair count
share each group's common valid samples. Both endpoints still complete their
expression and numeric checks before masking; the memo does not cross Frame
operations or input domains.

## Partition statistics and current-value shares

`frame.over(statistic, by=())` uses the same sum, mean, corr, pair_count and
row_count kernels as reduce, then maps each partition's result back to every
original row. `by` contains distinct complete axes from this exact input;
composite keys remain indivisible and Null keys use the existing null-safe
grouping rules. Ordinary, interval, calendar and paired axes use their frozen
coordinates without rematching. The result is a Column bound to the receiver,
with the same row count and order. Empty input stays empty, including zero axes;
`by=frame.axes` partitions by complete row key.

`frame.over(mv.row_count())` binds its reusable unbound request without mutation.
For [10, NA, 30], row_count broadcasts 3 and self pair_count broadcasts 2.
Nested over evaluates its dependencies first: broadcast totals repeated on
several rows remain repeated samples for an outer sum or mean. In where/top,
each partition is evaluated before the operation selects rows. The selection
cannot shrink an earlier statistic's denominator or bypass required checks.

Repeated identical partition expressions share their successful broadcast result
within that evaluation, including across selected outputs and arithmetic. Cache
identity distinguishes literal carriers such as integer, float and Decimal.
The broadcast reduction does not sort its intermediate groups; public reduce
still returns canonical key order. Local Top(0) completes its score expression
and then returns typed empty rows without sorting.

`column.share(along=axes)` requires a nonempty tuple of
distinct complete axes from that Column's input. All other input axes define
partitions. It expands to `column / frame.over(column.sum(), by=remaining)`,
retaining input rows, order and ordinary NA behavior. Axis order normalizes to
input order. A zero-axis Frame rejects share; use an explicit / expression for
a scalar ratio. Numeric and legal Duration sum/divide
combinations retain their existing type rules; boolean, string and date reject.
Unsupported carriers fail before execution rather than converting silently.

Shares are signed ratios, not percentages, and may lie outside [0,1]. Zero
partition totals, including cancellation of nonzero values, give NA. Known equal units cancel to `1`; unknown units remain
None. Filtering before share changes the denominator; filtering a selected share
afterward preserves the computed denominator. For month × region × category,
along=(region,) sums within each month/category, while along=(region, category)
sums within each month. Remove category explicitly with reduce when the intended
rows are region totals, then acquire fresh handles.

Both operations consume current data and preserve inherited method facts without
creating source decomposition proofs. Normalizing inventory or group means
describes those numbers' sum; it does not establish original Metric additivity,
monthly inventory composition or original overall-mean contributions.

Source Metric/per/window execution may retain internal sufficient state during
one evaluation. Completed Frame data never persists it for later original
rollup/drill/share. A Frame with a retained process-local source declaration can
request `attribute(by=...)` for admitted targets on new business classifications.
It keeps the current complete parent keys and observed values, then evaluates
fresh source parent totals and fine contributions. This declaration is separate
from current-cell statistics and is unavailable after independent reopen.
Changing source grain, attributes, window or source scope
requires explicit construction; a fixed ordinary-axis Population bridge can
constrain selected keys but cannot restore old unobserved facts or history.
A new source read may see changes after a selection was fixed.

## Interpretation and implementation

Field Null is a valid grouping category; absent rows and value NA are different.
Exact output order and schema survive empty/keys-only results and persistence.
Arithmetic change is not causal evidence or proof of source freshness/completeness.

[numeric_state.py](../../../marivo/analysis/methods/numeric_state.py) and
[query_values.py](../../../marivo/analysis/methods/query_values.py) own local numeric
completion and reductions. [frame_correlation.py](../../../marivo/analysis/methods/frame_correlation.py)
owns paired statistics. [physical.py](../../../marivo/analysis/methods/physical.py)
owns method/type/backend qualification; [_ibis_numeric.py](../../../marivo/analysis/methods/_ibis_numeric.py)
owns source-native numerical construction.

## Single-member coordinate restriction

`AnalysisFrame.fix(axis, key)` restricts a complete ordinary axis to one typed key
and removes that axis. This is an injective coordinate projection after restriction,
not a current-row statistic. It preserves cells, types, units, NA, presence and order;
empty selection remains empty even after the final axis is removed. It cannot be
implemented with sum/mean/first, which have different type and empty-input contracts.

Fix and pure direct value projection compose without aggregation. Top, partition
statistics and share generally change meaning when moved across fix. Strict input
errors remain required even when their rows are excluded. No new source predicate
pushdown is implied. Cross-Frame comparison and correlation keep their existing
coordinate correspondence requirements.
