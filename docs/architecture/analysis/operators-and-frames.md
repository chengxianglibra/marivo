# Operators and Frames

Status: current AnalysisFrame value contract, clarified 2026-10-21 without changing DSL behavior.
The current data surface is defined by [AnalysisFrame](query-result-reuse.md) and focused Live Help.
Native expressions and existing source/local numerical kernels are implemented;
AnalysisFrame supplies one data contract across expression, memory and stored carriers. Focused local checks do
not by themselves establish full independent acceptance or remote Runtime qualification.
[Analysis Architecture](README.md) owns objects and compilation;
[Runtime](session-state-and-runtime.md) owns invocation and publication.

## Immutable coordinates and values

Population describes finite complete coordinate tuples. AnalysisFrame binds named values
to those coordinates; Column locates an expression on that exact input. Output
axes remain ordered. A semantic Entity identity includes every primary-key component in
declared order; roles, paths and version anchors remain part of its semantic binding.
Frame data axes retain complete physical key types, roles and captured time interpretation,
without the original Entity/Field nominal binding. A display name derived from a semantic
path does not restore that binding. This holds for expression, memory and stored carriers.
A Field Null is a valid category and must not be lost by default grouping behavior.

Complete Frame data owns final typed values, actual coordinates and presence.
Frame has data operations, bounded preview and complete isolated pandas export.
Its persisted `.artifact` exposes submission/Findings/Evidence audit only. Neither
carrier retains a producer graph, hidden aggregate components or source authority.
Use returned compute/persist roots to fix inputs; exact handles are not fingerprints.

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
| Frame Column sum | Current non-NA represented cells | NA |
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
strict policy. Nonfinite values, invalid arithmetic and overflow are errors,
not additional missing states or a reason to retry another algorithm.

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

A membership descriptor in the explicit `memberships` parameter of observe or
Metric.per preserves correlated fields within one tuple. The coordinates are
declared separately in by/samples; Population contains keys, not allocation rules.
Outer and inner rules are independent. Separate
descriptors form independent factors. Repeated `(contribution identity, bucket)`
pairs are set occurrences; equal-valued distinct contributions remain separate.
unmatched is explicit exclude or Null. A Population in by selects exactly its
complete output keys, including keys without facts. Selection preserves original
contribution identities and allocation denominators; selecting one half-allocated
bucket still yields its half, without renormalizing over the selected output.

`Metric.per(samples, memberships=())` completes the entire child Metric within each external and
internal key pair before applying the sample reducer. An average of per-customer
ratios is not an overall ratio. A complete explicit internal domain retains empty
samples under the child's empty policy; it may deliberately select a subset.

`P.count()` captures a Population and counts its complete distinct tuples as a
lazy integer Metric, including Null categories and identities without source
facts. Empty P counts 0; the zero-axis unit domain counts 1. Grouping matches
coordinates already in P, without implicit attribute reads: to count identities
by a new field, explicitly classify while retaining the identity axis first.
Projecting away identity counts distinct resulting categories instead. Missing
groups in an explicit output domain count 0. P/Q count independently. Time axes
broadcast a timeless count or match P's existing frozen time coordinate.
Fact conditions and windows do not reselect the captured denominator.
Only-count observations reject conditions/windows with no fact meaning.

## Current cells, original Metrics and windows

Frame.reduce computes statistics over represented rows using independent valid
masks. Empty/all-NA sum is NA; row_count counts all current rows. It neither
reconstructs original contributions nor changes a calendar's grain. Row mean 55
can differ from original overall 1000/91; distinct counts 2+2=4 do not prove a
union distinct count of 4 when the actual union has 3 members. Request original
overall formulas with an explicit new observe and their governed source inputs.
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
scale fail before clipping a finite coefficient to [-1, 1]. The numeric and
empty-domain contract is owned by [Correlation Statistics](../../design/analysis/correlation-statistics-design.md).
Group coefficients and counts cannot reconstruct pooled correlation.

Source Metric/per/window execution may retain internal sufficient state during
one evaluation. Completed Frame data never persists it for later original
rollup/drill/share. Changing source grain, attributes, window or source scope
requires explicit construction; a fixed ordinary-axis Population bridge can
constrain selected keys but cannot restore old unobserved facts or history.
A new source read may see changes after a selection was fixed.

## Complete correspondence and comparison

Align and each named compare baseline bind every complete axis. Ordinary axes
require a full explicit bijection; zero-axis pairs or a sole same-kind time axis
permit only the specified structural inference. Named compare's `on` and `match`
are alias-keyed mappings. FrameAxis/Column belong to the exact receiver; matching
names do not rebind them. Exact checks complete actual joint keys. Compare outer
starts with current rows and appends mapped baseline-only keys by alias order;
actual baseline time rows outside the current declared time domain reject. Align
retains its general two-ended union and paired output.
For ordinary axes, explicit on declares the caller's business correspondence.
Validation checks ownership, roles, complete key types and time interpretation;
it does not check original Entity/Field equality. Compatible Customer/Product
identity keys can be explicitly paired without establishing business identity.

Align preserves left/right values. Compare requires equal nonempty value paths
and compatible numeric types/units. It keeps current axes/unqualified values and
adds `<alias>.baseline.*`, `<alias>.absolute.*`, `<alias>.relative.*` in each alias's
current value order. Absolute is current-baseline and relative divides by the
absolute baseline. Missing rows/NA yield NA differences before zero policy.
Duration baseline/absolute retain ticks; relative is dimensionless. Child strict
policies stay required even after select or Top-0.
Equal value names select comparison pairs; they do not establish Metric identity
or business comparability, even when numeric types and units are compatible.

Compare row flags are nonnullable `current.present` / `<alias>.present`, distinct
from value NA; inherited flags use `current.presence.*` / `<alias>.presence.*`.
Generated paths cannot collide with retained values/presence. Select remaps only
direct saved value bindings; arithmetic does not gain comparison certification.
Where/top retain complete frozen directories. Reduce drops row presence and all
comparison certifications; retained current time axes keep only their own
interval/calendar explanation. Same-domain equality composition may transport
exact bindings; displaced time authority leaves only inert transported presence
facts. No completed result recovers Metric or source evaluation authority.



A TimeScope in by retains one interval coordinate; single-bucket results keep
the axis. Interval equality uses exact bounds and boundary timezone. Explicit
ordinal pairs intervals only when both complete declared directories contain one
record and their boundary timezones agree. where/top/select retain the full
directory, so filtering a multi-interval result to one row does not qualify it
for ordinal. Time plus ordinary coordinates requires complete explicit on.

Calendar match consumes frozen finite directories and versioned pure rules,
without model or timezone database reconstruction. Original ordinals do not
compress after where/top. Structural match admission requires full compatible
nominal partitions and actual boundaries equal to nominal ones. It differs from
actual output-row coverage: unmatched actual rows fail exact or remain in outer;
unused directory buckets do not create rows. Named calendars support ordinal
only; builtin offsets/year follow their stated grain limits. Paired coordinates
retain both frozen endpoints/map. Re-aligning a paired axis, arbitrary identity
correspondence, scalar broadcast, calendar coarsening and partitioned top are
outside the first Frame surface.

## Reference transport and share

A complete-domain reference can be an explicit values Frame aligned on its
entire target domain, followed by Column arithmetic. This is not automatic
broadcast or proof of contribution inclusion. Frame has no share receiver or
implicit original denominator. When a source ratio is required, construct the
explicit governed source formula; when external arithmetic is needed, disclose
its exact fixed input and denominator. Neither is an invented equivalent for
retired contribution-share behavior.

## Export and interpretation

Frame.to_pandas returns an isolated complete table with RangeIndex and
`(kind, name)` MultiIndex columns: physical coordinate keys by axis token, named
values and presence. Axis descriptions and units are isolated attrs;
`attrs["frozen_coordinates"]` contains typed isolated attachment tables for
calendar labels/bounds/ordinals and paired endpoint maps. It contains no semantic
Ref, formula, reader or hidden state and cannot be imported as a typed contract.
The simpler interpretation path is Frame.show, which resolves saved labels and
bounds without new visible projection columns or endpoint methods.

Exact output order survives complete reads and persistence. Empty/keys-only
results retain schema. Pair exports by complete business keys, preserve Field
Null with dropna=False where appropriate, and distinguish zero, NA and absent
endpoint. Unsupported descriptive/paired statistics, forecasting and external
presentation need explicit sample, NA, tie and uncertainty choices. Arithmetic
is not causal evidence or proof of source freshness/completeness.
