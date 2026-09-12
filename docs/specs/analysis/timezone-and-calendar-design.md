# Timezones and Calendars

The Session's persisted report timezone owns built-in temporal interpretation.
A semantic parser may declare a source read timezone, and a certified calendar
owns its boundary timezone. These authorities remain separate and are preserved
through execution, publication and cold recovery.

## Physical time kinds

| Source kind | Interpretation |
| --- | --- |
| Aware timestamp | Absolute instant; localize display and bucket boundaries under report/calendar authority |
| Naive timestamp or time-bearing partition | Local wall clock; apply declared or resolved reader timezone before instant comparison |
| Date or date-only partition | Civil date; preserve its date meaning without an instant shift |

Typed source schema preserves actual timestamp precision. The normalized logical
date/timestamp family does not discard physical precision or parser identity.
Unsupported exact submicrosecond timezone conversion fails explicitly.

## Authority resolution

Session report timezone defaults to system resolution and is persisted once.
An explicit `report_timezone` overrides that default. A conflicting reopen is
rejected. Fixed-offset resolution remains explicit; it does not invent IANA DST
rules. Changing host timezone does not change a recovered Session.

Logical construction captures the persisted report authority without opening a
source connection. Runtime probes the admitted actual reader before setting its
UTC execution environment. An explicit semantic parser timezone takes precedence
over the reader default; system fallback is recorded when the engine has none.
Conflicting or invalid declarations fail through the owning typed error.

## Scopes and buckets

`mv.time_scope(start=..., end=...)` is literal and half-open. A date-only end is
excluded at that midnight. Explicit timestamp endpoints preserve their precision;
no epsilon subtraction or implicit next-day expansion changes their meaning.

Localizable source values are parsed and localized before scope filtering and
bucketing. Native DuckDB timezone operations preserve report/calendar boundaries
across DST. Civil dates compare directly. Integer, string and composite date/hour
partitions follow their governed parser rather than accidental lexical ordering.
An exact-hour upper bucket is excluded; a bucket beginning before a partial-hour
upper bound remains eligible.

Observation windows and Population membership windows are independent. Historical
snapshot/validity coordinates select rows for stable Entity identity K; they do
not become part of K or authorize a fallback to an older snapshot.

## Calendars and cumulative state

Period calendars are finite certified partitions with exact civil boundaries,
ordered periods and a snapshot digest. Their boundary timezone is independent of
source read and Session report time. Construction captures the selected certified
snapshot from project semantic state; no analysis-local calendar file is consulted.

Grain-to-date resets follow the owning civil period. All-history preserves earlier
contributions. Trailing day/week windows retain fixed 86,400/604,800-second lengths.
Retained cumulative coverage compares instants for timestamp axes, preserving
23/25-hour DST days instead of assuming every civil day is 24 hours.

Artifact descriptors retain report authority and adopted per-axis physical kind,
read timezone and source, boundary timezone and parser facts. Retained folds use
these committed contracts. Source-offline cold recovery neither probes a datasource
nor resolves the new host's timezone.

The supported semantic parser and calendar authoring contracts are described in
[temporal semantics](../temporal-semantics.md). Actual source, Runtime and recovery
checks are owned by the lazy temporal test suites; public Session wiring has its
own Runtime tests. Exact Help routes expose the current admitted method contracts.
