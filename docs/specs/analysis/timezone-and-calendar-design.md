# Timezones and Calendars

Status: AnalysisFrame cutover source and frozen-data time contract, 2026-10-19.
Exact physical/backend routes require their own qualification. The complete
window and correspondence targets are owned by
[Lazy DSL time](lazy-analysis-dsl-redesign.md#49-滚动窗口累计与期间比较).

## Three independent authorities

The persisted Session report timezone owns builtin observation boundary
interpretation. A semantic parser may declare a source read timezone; a certified
calendar owns its boundary timezone. These remain distinct throughout binding
and execution. Saved output preserves the actual boundary timezone and absolute
UTC instants rather than executable source or calendar objects.

A Session's explicit report_timezone is persisted; a conflicting reopen rejects.
Omitted report_timezone resolves the system timezone once and persists it. A
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
Scalar to-date selects the unique reset period left < e <= right, using the
just-ended period at an exact boundary. It does not inspect the display start.
With calendar output, every nominal output bucket must also be contained in one
reset period; a cross-month week cannot become a month-to-date day.

Each endpoint evaluates the whole Metric formula over its full W(t). Independent
windows are completed before corresponding the endpoints. Overlap retains the
(output key, component, contribution occurrence); it is not a global deduplication.
A window never expands explicit member selection or a child intrinsic filter.
Time sampling checks apply to the actual demanded child samples. Completed Frame
operations do not change that source window or retain its internal samples. A
new window/calendar or original target observation requires explicit source
construction; summing represented window values is a different current-row question.

## Period correspondence

Frame align/compare uses full explicit on, except zero axes and exactly one sole
calendar axis on each input. Match is always an explicit choice. It requires
exactly one compatible calendar pair and rejects already paired inputs. All
matching consumes finite frozen directories and versioned pure rules, without
current CalendarAxis, model, certification or timezone-rule reconstruction.

Each directory has exact physical keys, original nominal label/ordinal and
nominal/actual boundaries. Match requires compatible continuous nonoverlapping
complete nominal partitions, with actual bounds equal to nominal bounds. This
structural admission applies even after where/top; removing rows cannot hide
partial buckets. Ordinary reading/equality without match does not gain that
complete-bucket requirement. Corrupt/missing records are integrity errors.

Ordinal uses original full-domain positions and never compresses after selection.
Builtin period_offset supports day/week/month/quarter/year by nominal label
arithmetic followed by lookup in the other saved directory. previous_year supports
builtin day/month/quarter/year and leaves leap days unmatched; it does not clamp
February 29 or subtract 365 days. Named calendars support only ordinal with the
same frozen identity/digest/level/timezone. Named offsets, subday/DST offsets,
multiple calendar matches and arbitrary relation maps are postponed.

First build a stable partial one-to-one directory map, including one-sided buckets;
then apply exact/outer to actual complete row keys. Unused directory buckets do
not create output rows or cause exact failure. Unmatched actual rows fail exact;
outer retains them with independent side presence. A full directory can contain
a leap day that was filtered from actual rows, allowing exact on the remaining
fully paired domain. Missing values and absent endpoints remain different.

A matched result retains a single paired axis with an opaque key, both frozen
endpoint descriptions and a complete map. Frozen interpretation is self-contained;
show resolves labels/bounds/paired endpoints, and full export retains isolated
typed attachments. There is no time_axis="current" projection, endpoint method
or new public visible projection column. Endpoint windows complete before
pairing; mapping daily rows cannot replace the baseline's own window computation.

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
