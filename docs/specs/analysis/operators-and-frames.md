# Operators and Frames

Status: current nullable-value and terminal-output contracts, 2026-10-17.
The complete target surface is defined by [Lazy DSL](lazy-analysis-dsl-redesign.md).
Native expressions and existing source/local numerical kernels are implemented;
R3–R8 integrates the corresponding public Query routes. Focused local checks do
not by themselves establish full independent acceptance or remote Runtime qualification.
[Analysis Design](python-analysis-design.md) owns objects and compilation;
[Runtime](session-state-and-runtime.md) owns invocation and publication.

## Immutable coordinates and values

Population describes finite complete coordinate tuples. Query binds named values
to those coordinates; Column locates an expression on that exact input. Output
axes remain ordered. An Entity identity includes every primary-key component in
declared order; roles, paths and version anchors remain part of its binding.
A Field Null is a valid category and must not be lost by default grouping behavior.

Artifact contains final typed nullable values and actual output coordinates.
It is terminal and offers bounded display, complete isolated pandas export and
saved fact reads. It contains no executable graph, sufficient-state continuation
or authority to recompute a Metric. Only Query owns DSL transformations.

## Null, empty and arithmetic policies

Values use normal nullable carriers. There are no per-cell Defined/Null/Undefined/
Unknown tags or reason columns. Missing correspondence endpoints are represented
by independent nonnullable boolean presence, never inferred from value NA.

| Operation | Effective samples | Empty/all-NA result |
| --- | --- | --- |
| Source or internal sum | Non-NA child values, subject to nulls policy | NA by default; 0 for explicit `empty="zero"` |
| count / count_distinct | Method's declared complete occurrences or non-NA values | 0 |
| min/max/mean/median/quantile | Effective non-NA samples | NA |
| weighted_mean | One common non-NA value/weight pair mask for both sums | The operator's denominator policy |
| Query Column sum | Current non-NA represented cells | NA |
| Query Column mean | Current non-NA represented cells | NA |
| row_count | All current coordinate rows, including NA values | 0 |

Strict `nulls="error"` belongs to the source/internal reduction that captures it.
Checks cover its demanded sample/contribution domain. A later where, select, zero
multiplier or empty output cannot suppress a required error.

Division uses `zero_denominator="null"` by default and masks zero before dividing.
Explicit `"error"` fails only the demanded non-NA division. The parameter belongs
to that new operation; a parent comparison or formula cannot overwrite a child's
strict policy. Nonfinite values, invalid arithmetic and overflow are errors,
not additional missing states or a reason to retry another algorithm.

## Physical numeric and temporal carriers

Admitted physical columns use signed int64, finite float64, Decimal(p,s) with
1 <= p <= 38 and 0 <= s <= p, boolean, string, date, exact timezone-aware timestamp
and fixed-duration s/ms/us/ns ticks. Boolean is not a numeric quantity. Every
physical route fixes its actual input and output types before business execution.
Keys are never passed through float, including integers above 2**53.
`Query.coord(axis)` can supply a scalar Column only for a single-component axis.
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

Source arithmetic uses admitted native Ibis operations. Local arithmetic preserves
exact integer/Decimal intermediates until its declared output conversion and
checks finite results and signed overflow. Duration keeps its exact tick unit;
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

## Contribution occurrences and internal samples

Independent fact roots reduce independently under their own filters and time
roles, then combine on complete target keys. One root's facts cannot be multiplied
by another root's rows. Existence selection, overlapping membership allocation
and modeled association events retain different contribution meanings.

A membership descriptor preserves correlated fields within one tuple. Separate
descriptors form independent factors. Repeated `(contribution identity, bucket)`
pairs are set occurrences; equal-valued distinct contributions remain separate.
unmatched is explicit exclude or Null. Complete requested output K is independent
of allocation and must cover the contributions it claims to represent.

`Metric.per(samples)` completes the entire child Metric within each external and
internal key pair before applying the sample reducer. An average of per-customer
ratios is not an overall ratio. A complete explicit internal domain retains empty
samples under the child's empty policy; it may deliberately select a subset.

## Current cells, original Metrics and windows

`Query.reduce` computes new statistics from current represented cells. `rollup`
recomputes the original Metric at a coarser coordinate domain only where its
business additivity and sufficient information permit. Finished means, ratios,
quantiles and approximate estimates cannot be summed to manufacture an original
coarser value. A source route may reevaluate qualified original contributions;
execution-only components never become Artifact continuation state.

Filtering and Top-N select existing coordinates. Moving a selection across
rollup, reduce, share or a window requires proof that demanded input, policies,
complete keys and checks remain equivalent. A parent selection cannot infer fine
members that were never requested; drill constructs explicit new endpoint work.
Comparison results do not gain direct drill or relative-value rollup authority.

The qualified L8 rollup fusion is limited to adjacent single-consumer original
rollups over identical ordinary exact int64/Decimal direct sum/count components
and direct retained-axis maps. It preserves the original ordered intermediate
finishes and checks: an overflowing middle group still fails even if a later
scalar total would cancel it. Same-input source fusion keeps independent logical
occurrences and complete component/check slots; typed preparation authority owns
that sameness. Float sums, per/window inputs, differing policies, pending checks,
selection/share boundaries and shared intermediates receive no general fusion
grant. These optimizations do not replace the operator's original semantics.

Metric.window attaches one axis-free rule. Each endpoint evaluates its own full
W(t) before a comparison. Display boundaries, nominal buckets, actual clipped
intervals and contribution windows remain distinct. Windowed original rollup may
change spatial grain only while preserving the original calendar and endpoints.
Changing or removing that time binding requires a new observe.

## Complete correspondence and comparison

`align(other, how="exact", match=None)` retains left/right roles. Same-binding
identity correspondence preserves shared axes. exact checks unique complete keys
and equal images; outer retains their union and records real missing sides as NA
plus False presence. It never substitutes zero or claims empty business activity.

Axis-free ordinal uses each endpoint's original full calendar order. Both sides
need equal resolved Grain, boundary timezone and compatible calendar authority,
and every actual bucket must equal its nominal bucket. Filtering does not compress
positions. exact requires equal bucket counts and complete key pairing; outer
retains the longer tail without relaxing completeness or resampling.

Declared corresponding uses exactly one identity axis per side and an explicit
left-to-right one-to-one path. The path must explain all identity components and
required version anchors. Equal field names do not prove identity. Direct full-PK
equalities require no extra data; other mappings are explicit plan dependencies.
One-to-one structure does not prove this invocation's domain coverage.

`compare` requires equal nonempty quantity-name sets and compatible numeric types
and units. It emits each name's current, baseline, absolute and relative values.
Absolute is current-baseline; relative is that difference divided by abs(baseline).
A zero baseline defaults only the new relative to NA, preserving a valid absolute;
explicit error fails the invocation. Input subexpression policies remain in force.
General comparison does not require the same Metric definition at both endpoints;
business comparability remains the caller's responsibility.

Nonidentity correspondence retains its own coordinate and actual endpoint
projections, including missing endpoints. Presence paths preserve nested left/right
or current/baseline roles. A projected current time axis requires an actual current
coordinate on every represented row; a baseline-only row cannot be dropped or given
a fabricated date. Period mappings and leap-day behavior belong to
[Timezones and Calendars](timezone-and-calendar-design.md#period-correspondence).

## Reference transport and share

Broadcast transports any compatible value kind over a complete unique target-key
to reference-key projection. A zero-axis reference must have exactly one row;
a one-row grouped reference remains grouped. Every reference axis must match a
bound target axis, retained projection or legal coarsening; names and row positions
cannot supply a mapping. Reference keys must be unique and cover every target key.
Transport repeats value use, not original facts or contribution authority.

Share is numerical and requires a valid contribution quantity and compatible
unit. Its numerator and denominator consume one actual input under the requested
partition. Share followed by filtering retains the original denominator; filtering
before share changes that denominator. General broadcast division is not a proof
of contribution inclusion. No common-input route means an explicit capability
error, not independent reevaluation disguised as the same input.

## Export and interpretation

Pandas export has RangeIndex and `(kind, name)` columns: coordinate, named value
and presence. Axis metadata preserves kind, stable column token, complete key
components, dtype, timezone/bounds and actual projections. It contains no semantic
Ref, formula, graph or hidden state. Empty and keys-only results keep their schema.
Follow complete business keys when pairing exported tables; preserve Field Null
with `dropna=False` where appropriate. A valid zero and NA remain different.

Ordinary classification, descriptive/paired statistics, cell-level rolling and
forecasting belong in pandas after export. A computed difference is arithmetic
evidence, not a causal explanation. Neither output values nor restored metadata
prove source completeness, freshness or suitability for a new question.
