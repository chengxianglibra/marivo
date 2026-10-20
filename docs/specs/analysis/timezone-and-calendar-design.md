# Timezones and Calendars

Status: AnalysisFrame cutover source and frozen-data time contract, 2026-10-19.
Exact physical/backend routes require their own qualification. The complete
window and correspondence targets are owned by
[Lazy DSL time](lazy-analysis-dsl-redesign.md#49-滚动窗口累计与期间比较).

## Three independent authorities

The persisted Session timezone owns builtin observation boundary
interpretation. A semantic parser may declare a source read timezone; a certified
calendar owns its boundary timezone. These remain distinct throughout binding
and execution. Saved output preserves the actual boundary timezone and absolute
UTC instants rather than executable source or calendar objects.

`mv.session.create(..., timezone=None)` resolves the system timezone once and
persists the normalized name and resolution facts. An explicit timezone takes
precedence; an invalid value fails. Exact-ID resume uses the saved timezone even
when TZ or the host changes. `session.timezone` exposes that frozen name. A
per-call timezone on instant/time_scope
chooses that new boundary interpretation without mutating the saved Session.
Fixed offsets keep their fixed meaning and do not invent IANA DST rules.

Source naive timestamps use declared parser authority first, then the qualified
reader default. Reader system fallback is available only where that reader's
contract permits it; a failed engine probe or invalid fact must not silently
select the host zone. Source dates remain civil dates. Report conversion never
shifts a date-only business label as if it were an absolute instant.

## Exact boundary construction

Use `session.instant(value, timezone=None)` and
`session.time_scope(start=..., end=..., timezone=None)`; analysis has no module-level
same-name constructor. Scope boundaries are exact
UTC InstantAnchor values. Inputs accept ISO dates/timestamps, dates, aware
datetimes and resolved anchors. Dates and offset-free strings use the frozen
boundary timezone; offset inputs preserve their instant. Naive Python datetimes
and ambiguous/nonexistent wall-clock values reject. A scope is nonempty and
half-open: start is included and end is excluded. No epsilon subtraction,
implicit end-day expansion or host-clock lookup changes that interval.

Field.at(anchor) chooses an exact attribute version with valid_from <= anchor <
valid_to. Versioned source identities have their own explicit at. Observation
periods do not choose attribute versions, and an exact end boundary is not a left
limit. The current API has no before_end anchor or automatic prior-version fallback.

## Output time domains

`observe(metrics, *, by=(), where=None)` takes time in by. A TimeScope contributes
one interval coordinate with exact start/end and boundary timezone; a CalendarAxis
contributes its finite buckets. One bucket is still an axis. At most one output
time axis is allowed. `by=()` has no endpoints; a hard time predicate does not
supply them. Interval output permits all_history, trailing_duration, since and
endpoint to_date; trailing_periods requires a calendar axis. A count-only Metric
can repeat a captured Population count over a time domain, but conditions/windows
with no fact effect reject.

An interval directory records all declared intervals separately from actual row
keys. Equality unions directories by exact boundaries and boundary timezone; outer
adds only actual keys. where/top/select preserve the full directory, even when
one row remains. Interval ordinal still requires one declared record per side;
filtering a multi-interval directory does not qualify it. Interval and calendar
axes are distinct, cannot bridge back to source, and are never implicitly squeezed
or broadcast. Time plus ordinary axes requires complete explicit on in comparison.

## Grain, nominal buckets and actual intervals

`scope.by(mv.grain("day"))` captures a finite ordered CalendarAxis. Builtin Grain
fixes unit/count; day/week/month/quarter/year use count=1 and subday widths tile a
civil day. Certified grains come from calendar.grain(level). Both use the same
immutable Grain contract. CalendarAxis retains original nominal bucket N(t),
actual intersection A(t), timezone, order and exact Grain/snapshot identity.

Ordinary bucket observation uses A(t). Calendar endpoints and Metric windows use
their separate W(t). A requested partial first/last bucket is not a complete period
for an operator that requires A(t)=N(t). Empty business activity does not remove a
requested calendar bucket or prove that the bucket's source data was complete.

Repeated DST hours remain distinct actual chronological buckets. Civil days may
span 23 or 25 hours. The neutral calendar kernel fixes their exact UTC boundaries;
a later operation cannot compress filtered bucket ordinals or reconstruct order
from displayed labels. Calendar products retain factor and full-key order.

Certified period calendars are finite partitions with exact boundaries, exhaustive
coverage, levels and snapshot digest. Scope timezone must match their boundary
zone. Required predecessor/reset periods outside coverage fail; missing business
activity inside certified coverage is a different fact. Complete authority comes
from the captured certification, not a scan of event minima or equal-looking labels.

## Window binding and evaluation

Metric.window accepts only axis-free all_history, trailing_duration,
trailing_periods, since and to_date rules. observe fixes output coordinates,
endpoints and the window binding once. The explicit source expression transports that binding; changing
window, endpoint or evaluation grain requires a new observe.

| Rule | Contribution range for endpoint e=actual_end(t) |
| --- | --- |
| all_history | All eligible contributions strictly before e, without a lower bound |
| trailing_duration(duration=Δ) | [e-Δ,e), using actual elapsed time |
| trailing_periods(count=n) | Start of the nth complete calendar predecessor through e |
| since(start=a) | [a,e), requiring a before every endpoint |
| to_date(grain=p) | [reset_period(e,p).start,e) |

Time is subtracted in instant space for a fixed duration, then presented in the
boundary zone. Seven civil periods need not equal 168 elapsed hours. Period
lookback requires complete nominal periods and the same certified authority.
Interval to-date selects the unique reset period left < e <= right, using the
just-ended period at an exact boundary. It does not inspect the display start.
With calendar output, every nominal output bucket must also be contained in one
reset period; a cross-month week cannot become a month-to-date day.

Each endpoint evaluates the whole Metric formula over its full W(t). Independent
windows are completed before corresponding the endpoints. Overlap retains the
(output key, component, contribution occurrence); it is not a global deduplication.
A window never expands explicit Population contribution selection or a child intrinsic filter.
Time sampling checks apply to the actual demanded child samples. Completed Frame
operations do not change that source window or retain its internal samples. A
new window/calendar or original target observation requires explicit source
construction; summing represented window values is a different current-row question.

## Period correspondence

Every named compare baseline binds complete axes through alias-keyed on/match;
zero axes or a sole same-kind interval/calendar pair permit unique structural
on inference. Align retains a single explicit on/match. Both reject paired
inputs and validate ownership, complete keys and compatible interpretation.

`offset(*, years=n)` and `offset(*, periods=n)` require exactly one non-boolean
integer keyword. Period distance uses nominal grain, never sparse row position;
year distance uses civil years, with ISO week-year for weeks. Complete builtin
day/quarter matching stays available. Source `CalendarAxis.shift` admits only
unit-step builtin week/month/year full buckets or bucket-start prefixes.

Full buckets map to full targets. Prefixes preserve weekday/day-of-month/month-day
and civil cutoff clock. Missing ISO W53 or civil cutoff date is legal absence,
not clamping or a shorter current range. Nonzero target DST gap/fold rejects;
zero offset copies the exact original instant, including an explicit fold branch.
Shift returns exact selected targets, possibly sparse/empty, with one direct
origin relation; chains never recover ancestors or revive an absent period.

Frozen directories carry stable nominal ordinals, selected keys, exact
nominal/actual UTC and canonical civil bounds, timezone and rule versions.
Direct maps and legal absence reasons are finite typed attachments. Data-side
offset validates saved target progress; partial day/quarter or a different saved
prefix rejects instead of clipping aggregate values. Ordinal still requires
continuous compatible complete nominal/actual partitions, or one declared
interval per side with the same boundary timezone. Filtering never renumbers it.
Named calendars retain only their existing ordinal support.

A named compare can consume an immediate direct relation only when its origin
matches the receiver's whole frozen time domain. Explicit conflicting match or
wrong anchors reject; without a direct relation, omitted match means equality.
Align's match stays explicit. After mapping, exact checks actual complete joint
keys; compare outer retains current rows followed by alias-ordered mapped
baseline-only rows. Any actual baseline time row outside the current declared
domain rejects. Align owns the general two-ended time union and paired axes.
Unused directory buckets do not create rows; presence remains independent of NA.

Compare preserves current ordinary axes and stores named endpoint directories,
consumed maps, exact value/presence bindings. Align may retain a paired axis with
both frozen endpoints and its map. Full reads validate required attachments;
positive comparison/direct previews validate required finite coordinate authority
while bounding primary rows, and n=0 remains metadata-only. No current TZ,
Catalog, source or Metric reconstruction occurs. Each explicit observe evaluates
its original window Metric; matching aggregates never recomputes a baseline.


## Physical precision and saved output

Actual datasource Timestamp/Duration units remain authoritative across projection.
The calendar kernel uses UTC microsecond boundaries; source ns precision does not
grant ns calendar or Duration boundary precision. A coarse provider carrier cannot
round an accepted requested scope. Unsupported precision-losing conversion rejects
before the affected business read.

Parquet may encode timestamp[s] as timestamp[ms]; qualification uses the actual
written schema, not the requested fixture type. Duration keeps exact s/ms/us/ns
integer ticks. Calendar months/years never become fixed-duration seconds.

Complete Frame data saves actual raw keys, UTC nominal/actual bounds, timezone,
original labels/ordinals and paired endpoint maps as typed data. Reopening uses those saved facts without current
Catalog, calendar certification, host timezone or datasource access. It restores
neither a window nor a calendar execution object. Source parser/backend conversion
qualification remains distinct from the lossless saved-output read boundary.
