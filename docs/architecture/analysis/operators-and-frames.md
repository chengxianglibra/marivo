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
| min/max/mean/median/quantile | Effective non-NA samples | NA |
| weighted_mean | One common non-NA value/weight pair mask for both sums | The operator's denominator policy |
| Frame Column sum | Current non-NA represented cells | 0 / NA |
| Frame Column mean | Current non-NA represented cells | NA |
| Frame corr / correlate coefficient | Common non-NA pairs within each current group | NA for fewer than two pairs or an exactly constant endpoint |
| Frame pair_count / correlate n_pairs | The same common non-NA pairs, including zero and duplicates | 0; retain groups even without pairs |
| row_count | All current coordinate rows, including NA values | 0 |

Strict `nulls="error"` belongs to the source/internal reduction that captures it.
Checks cover its demanded sample/contribution domain. A later where, select, zero
multiplier or empty output cannot suppress a required error.

Division uses `zero_denominator="null"` by default and masks zero before dividing.
Explicit `"error"` fails only the demanded non-NA division. The parameter belongs
to that new operation; a parent comparison or formula cannot overwrite a child's
strict policy. Nonfinite results and invalid domains are errors, never missing states
or a reason to retry another algorithm. Ordinary source integer and Decimal
arithmetic follows backend overflow behavior, including ClickHouse wrapping.
Local integer arithmetic uses checked kernels. An unevaluated source subexpression
need not trigger overflow, including under empty output; explicit null/zero/domain
checks retain their own demanded domains. Source
field reads check actual NaN/Infinity before selection, ordering or a pandas
carrier can hide them. A pandas missing-value encoding remains NULL; an Arrow
valid NaN is a nonfinite value, not NULL.

## Physical numeric and temporal carriers

Admitted physical columns use signed int64, finite float64, Decimal(p,s) with
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

Source arithmetic uses admitted native Ibis operations with explicit bound result
casts. Integer SUM returns Decimal(38,0); Decimal SUM returns Decimal(38,input_scale).
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
round retains its own semantics. Final carrier conversion is safe. Float64 admits
binary approximation, integer low-bit loss beyond 2**53 and ordinary underflow;
new nonfinite results fail. Identity, count, time and Duration remain exact.
Native intermediate precision, AVG algorithms, floating order and overflow may
differ between source and local execution. ClickHouse may silently return a wrapped
value with a different sign/magnitude; an admitted result may be used in formulas,
filters and ordering. Safe conversion cannot detect prior native wrapping.
Saved results preserve committed values, scale, dtype, NA and keys without rounding
or recomputation on export/cold read. Existing terminal fusion consumes the same
bound result types; its chosen native path determines overflow behavior. Duration keeps its exact tick unit;
a calendar month is not an elapsed duration. DuckDB native INTERVAL is admitted
as microseconds only after a source-native check rejects year, month and day
components. It does not invent nanosecond precision. Arrow Duration metadata and
integer ticks retain their declared unit on admitted file routes. Decimal precision/scale, quantile
interpolation and any native approximation are method contracts, not promises of
universal rational arithmetic or a global error bound.

Exact median/quantile uses linear interpolation h=(n-1)q; [0,10] at q=.95 gives
9.5. Native approximate reductions require an explicit approximate definition,
qualified actual type/backend and algorithm identity. An unavailable exact route
never falls back to approximation, and the reverse is also prohibited.

Ordered first/last uses a unique declared time order after completing each child
sample. It must not substitute an unordered backend first/last. include_null and
strict sample policy remain attached to the original occurrence. Window overlap,
per-sample keys and ordered sampling checks survive any legal optimization.

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
named statistic. Both endpoints must be numeric on the exact input; different
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

Source Metric/per/window execution may retain internal sufficient state during
one evaluation. Completed Frame data never persists it for later original
rollup/drill/share. Changing source grain, attributes, window or source scope
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
