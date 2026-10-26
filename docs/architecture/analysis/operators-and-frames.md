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
| Native source mean | Backend AVG samples and NULL policy | Backend result |
| Local internal mean | Effective non-NA completed samples | NA |
| weighted_mean | One common non-NA value/weight pair mask for both sums | The operator's denominator policy |
| Frame Column sum | Current non-NA represented cells | 0 / NA |
| Frame Column mean | Current non-NA represented cells | NA |
| Frame corr / correlate coefficient | Common non-NA pairs within each current group | NA for fewer than two pairs or an exactly constant endpoint |
| Frame pair_count / correlate n_pairs | The same common non-NA pairs, including zero and duplicates | 0; retain groups even without pairs |
| row_count | All current coordinate rows, including NA values | 0 |

Strict `nulls="error"` belongs to the source/internal reduction that captures it,
except native AVG. Native AVG delegates NULL and empty-input behavior to the
backend even when authored with `nulls="error"`. Other captured NULL checks cover
their demanded sample/contribution domain. A later where, select, zero multiplier
or empty output cannot suppress a required check.

Division uses `/` and masks zero before dividing; both 0/0 and nonzero/0
return NA. Its explicit float64 result cast survives native compilation, even
when Ibis already infers float64. This also prevents ClickHouse aggregate
arithmetic optimization from extracting a NULL divisor from `AVG(x / NULL)`.
Weighted means return NA when valid paired weights sum to zero,
including signed cancellation. All backend-native numeric operations delegate
overflow to the backend: its errors, wrapped values, Infinity and NaN are accepted
without an overflow precheck, result rejection or local retry. Native field reads
likewise retain backend floating values. Local numeric kernels retain their own
checked arithmetic and domain rules. An unevaluated source subexpression need not
trigger overflow, including under empty output; explicit NULL/domain checks retain
their demanded domains. Arrow-valid NaN is a floating value distinct from NULL.

Source declaration trust does not remove type, identity or captured NULL/domain
checks. A missing scalar join produces NULL without a separate required-match
audit; the consuming operator's captured NULL policy still applies, including
strict rejection outside native AVG.

## Physical numeric and temporal carriers

Admitted physical columns use signed int64, float64, Decimal(p,s) with
1 <= p <= 38 and 0 <= s <= p, boolean, string, date, exact timezone-aware timestamp
and fixed-duration s/ms/us/ns ticks. Boolean is not a numeric quantity. Every
physical route fixes its actual input and output types before business execution.
Keys are never passed through float, including integers above 2**53.
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
and provider-specific casts. Integer SUM returns Decimal(38,0); Decimal SUM returns Decimal(38,input_scale).
Both use one primary accumulation; count remains int64. Decimal add/subtract uses
p=max(p1-s1,p2-s2)+max(s1,s2)+1, s=max(s1,s2); multiply uses p=p1+p2, s=s1+s2.
When p exceeds 38, reduce s by the excess (minimum zero) and cap p at 38.
Mixed Decimal/integer uses the bound integer width (int64: 19 decimal digits).
An unspecified Ibis Decimal literal binds p/s from its literal digits and exponent;
an explicitly typed literal keeps that type. Business row values never select types;
ordinary integer arithmetic remains integer. Division/ratio returns float64;
mean retains its method-declared type, with an explicit Decimal output cast.
Local Decimal work keeps context precision 120 and converges once per bound result
with HALF_UP (ties away from zero), including carry at precision 38. Explicit
round retains its own semantics. Local observation formulas, internal sample
reducers and Frame comparison arithmetic own their Decimal context, including
traps and exponent bounds. Caller context settings do not alter their results or
turn permitted rounding into errors, and evaluation leaves that context unchanged.
Invalid arithmetic and unrepresentable output still fail. Final carrier conversion
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

Source quantile and median execute on DuckDB, PostgreSQL, MySQL, Trino and
ClickHouse. MySQL, Trino and ClickHouse sort and extract the two interpolation
bounds and valid count on the source; only that bounded summary crosses the
source boundary. Decimal paths retain their declared precision/scale through
the shared interpolation finisher. Integer and floating quantiles return
float64. Empty and all-NULL inputs return NA; strict NULL checks remain attached
to the complete demanded input. Bounds must complete before
downstream numeric fusion consumes their values.

Explicit source approximation uses DuckDB `approx_quantile`, Trino
`approx_percentile`, or ClickHouse `quantileTDigest`; approximate median uses
q=0.5 on that same algorithm. Sketches consume floating inputs and Decimal
results explicitly converge to the declared Decimal carrier. PostgreSQL and
MySQL have no admitted approximate source route. Algorithms and results may
differ across backends; no universal error bound is promised. This source
matrix does not expand Duration, original-input attribution or physical join
topology qualification.

Ordered first/last uses a unique declared time order after completing each child
sample. It must not substitute an unordered backend first/last. include_null and
strict sample policy remain attached to the original occurrence. Window overlap,
per-sample keys and ordered sampling checks survive any legal optimization.

Qualified direct min/max of Float64 time-sample means completes each child mean
before the outer source aggregate on DuckDB, PostgreSQL, MySQL, Trino and
ClickHouse. Strict outer NULL and scheduled-clock coverage checks remain
attached to their original domains; only bounded output summaries cross the
boundary. This does not admit arbitrary nested samples, Decimal/Duration or
ordered first/last to the same optimization.

Qualified direct mean of numeric time-sample means also executes on those five
providers. It computes each timestamp's source AVG, then an equal-weight outer
AVG over those completed means. Both AVG operations use backend NULL and overflow
semantics. This route has no scheduled-clock coverage check, numeric safety
windows, sample ranking or local fallback. Missing clocks do not become samples;
off-grid represented timestamps remain samples. Integer and floating AVG has a
declared Float64 output; Decimal AVG retains its declared precision and scale.
Explicit result casts enforce these carriers after native AVG arithmetic.
Duration, windows,
explicit sample populations and unsupported nested or conditional formulas retain
their existing qualified routes, including explicit local sample reduction where
applicable.

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

Correlation tests constants and ties on actual input values, then uses exact
integer/Decimal differences or binary float scaling before two-pass centered
stable sums and residual scaling. Nonfinite intermediates or a lost nonconstant
scale fail before clipping a finite coefficient to [-1, 1]. The numeric implementation is owned by
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
