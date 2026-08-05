# Cumulative Comparable-Period Alignment Design

Status: proposed

Date: 2026-08-04

## Summary

Improve comparison of `trailing` and `grain_to_date` cumulative metrics without
making unlike anchors look comparable.

The design keeps one public entry point and reuses existing
`AlignmentPolicy` constructors. It adds three runtime behaviors:

1. fixed-duration trailing anchors are compared by canonical duration, so
   `trailing(7, "day")` and `trailing(1, "week")` are equivalent;
2. existing calendar-backed alignment policies may align equal-anchor
   trailing and grain-to-date time series by day-of-week or holiday position;
3. cumulative compare keeps paired rows only and exposes every dropped or
   fallback row in alignment evidence.

True anchor mismatches remain blocked. Rolling 7-day versus rolling 28-day and
MTD versus QTD are different measures, not two periods of the same measure.

This design does not change all-history comparison semantics or cumulative
attribution. It does replace the shared delta comparison identity so every
delta uses one recovery and evidence contract; the cumulative-specific behavior
remains limited to anchor equivalence and alignment of already-observed
trailing/grain-to-date frames.

## Problem

The current runtime correctly rejects different trailing payloads and different
grain-to-date reset grains. It also restricts grain-to-date comparison to
ordinal `window_bucket` alignment. That boundary avoids wrong answers, but it
leaves two useful cases unsupported or unnecessarily awkward:

- semantically equal fixed durations authored with different units are rejected
  because tuple payloads differ;
- business reporting often compares the same weekday occurrence or holiday
  occurrence within two reporting periods, not merely the same calendar-day
  ordinal.

For example, “MTD revenue at the first Tuesday of this month versus the first
Tuesday of the prior month” is a supported comparable-period question. Pairing
the same absolute day-of-month can select different weekdays. A named holiday
may likewise be paired with the same holiday occurrence in another period.
Conversely, allowing MTD and QTD because both are “to date” would be a category
error.

The missing capability is a canonical anchor-equivalence predicate plus
anchor-aware use of the alignment policies Marivo already owns.

## Goals

- Preserve strict semantic comparability while accepting equivalent fixed
  trailing durations.
- Reuse `mv.window_bucket()`, `mv.dow_aligned()`,
  `mv.holiday_aligned()`, and `mv.holiday_and_dow_aligned()`; add no
  cumulative-only policy constructor.
- Support day-of-week and holiday-position comparison for cumulative time
  series and panels.
- Keep reset-boundary, single-period, query-grain, report-timezone, and value-
  semantics checks fail-closed.
- Drop unpaired cumulative rows rather than fabricate new/churned levels.
- Persist enough alignment evidence for `.show()`, `.contract()`, quality
  assessment, recovery, and audit.
- Keep one delta comparison identity and recovery path across exact and
  cumulative-equivalent comparisons.

## Non-Goals

- Comparing genuinely different trailing spans or reset grains.
- Automatically choosing a holiday calendar or declaring two calendars
  equivalent.
- Adding fiscal reset semantics to `ms.grain_to_date`; fiscal cumulative
  authoring requires its own semantic design.
- Comparing scalar cumulative values by a calendar policy.
- Aligning the Nth workday of one period with the Nth workday of another.
  `workday_aligned` is not part of the public `AlignmentPolicy` contract in this
  version, and `nearest_prior_workday` fallback does not add that semantic.
- Hiding unequal elapsed spans, unmatched holidays, fallback rows, or tails.
- Treating rolling-window autocorrelation as independent observations.
- Preserving cache reuse or direct recovery for delta artifacts persisted with
  `DeltaComparisonIdentityV1`.

## Canonical Anchor Semantics

Authored cumulative anchors keep their existing IR and hash identity. Compare
derives a separate closed semantic projection:

```python
class AuthoredTrailingAnchorV1(BaseModel):
    kind: Literal["trailing"]
    count: int = Field(gt=0)
    unit: Literal["second", "minute", "hour", "day", "week"]

class AuthoredGrainToDateAnchorV1(BaseModel):
    kind: Literal["grain_to_date"]
    reset_grain: Literal["week", "month", "quarter", "year"]

type AuthoredComparablePeriodAnchorV1 = (
    AuthoredTrailingAnchorV1 | AuthoredGrainToDateAnchorV1
)

class TrailingAnchorSemanticsV1(BaseModel):
    kind: Literal["trailing"]
    span_seconds: int = Field(gt=0)

class GrainToDateAnchorSemanticsV1(BaseModel):
    kind: Literal["grain_to_date"]
    reset_grain: Literal["week", "month", "quarter", "year"]
```

Trailing units are already restricted to fixed-size units. Canonicalization
uses the shared duration helper and checked integer arithmetic. Examples:

```text
trailing(7, day)  == trailing(1, week)
trailing(168, hour) == trailing(7, day)
trailing(28, day) != trailing(1, week)
```

These are absolute fixed durations: `day` is exactly 86,400 seconds and `week`
is exactly 604,800 seconds. They are not report-timezone civil-day or civil-week
units, so a 23-hour or 25-hour local day at a DST transition does not change
anchor equivalence. Compare still requires both frames to share the same query
grain and report timezone; existing bucket-timezone warnings remain visible and
are not repaired or suppressed by duration canonicalization.

The original authored payload remains available in each source frame and its
artifact identity. A dedicated typed `DeltaFrameMeta.cumulative_alignment`
record, defined below, records both payloads and the canonical projection, so
equivalence never erases provenance.

The existing `ComparableValueSemanticsV1.expression_fingerprint` includes the
authored cumulative node and therefore differs for `7 day` and `1 week` even
when their duration is equal. Compare must not bypass the rest of that contract
or pretend the raw fingerprints match. It derives a comparison-only
`canonical_expression_fingerprint` from each persisted expression
graph using this closed `canonical-cumulative-expression/v1` projection:

1. validate the source `MetricExpressionGraphV1` before projecting it;
2. visit reachable nodes bottom-up and replace every trailing anchor
   `(count, unit)` with `("trailing", span_seconds, "second")`, leaving every
   other node field unchanged;
3. re-intern each changed node with the existing canonical node-fingerprint
   function, recursively replace parent child ids, rebuild roots, and remap
   occurrence `node_id` values;
4. validate the rebuilt graph and fingerprint
   `{"schema": "canonical-cumulative-expression/v1", "roots": rebuilt.roots}`.

The projection never mutates or replaces the source graph. A malformed graph,
an unresolved effective anchor, or a mismatch between the persisted cumulative
marker and projected graph fails admission before alignment. Admission requires
the two canonical fingerprints plus every other comparable-value field
(`evaluator_contracts`, slice, key schema, unit, fold, source domain, and
definition transform) to match exactly.

The canonical projection is part of a single replacement delta identity
contract defined below. The runtime does not carry both the old exact-only
identity and a cumulative-only identity: every newly materialized delta uses
`DeltaComparisonIdentity`, with a closed semantics variant stating whether
admission required exact equality or cumulative equivalence.

Grain-to-date equivalence remains exact on `reset_grain`. Calendar month,
quarter, and year resets are not interchangeable.

## Alignment Matrix

| Shape and anchor | `window_bucket(ordinal_bucket)` | calendar-backed policy | Other |
| --- | --- | --- | --- |
| scalar grain-to-date | equal elapsed span only | blocked | blocked |
| segmented grain-to-date | equal elapsed span only | blocked | blocked |
| time-series/panel grain-to-date | supported with reset-period checks | supported with period equal to reset grain | blocked |
| scalar/segmented trailing | ordinary paired comparison | blocked | blocked |
| time-series/panel trailing | supported | supported | blocked |

`window_bucket(mode="calendar_bucket")` remains blocked for grain-to-date.
Joining June MTD and July MTD by absolute date key normally produces no
comparable-position pairs. The existing ordinal mode owns elapsed calendar
position; calendar-backed policies in this version own day-of-week and holiday
position only.

## Grain-to-Date Rules

All current structural checks remain normative:

1. canonical reset grains match;
2. query grains match;
3. report timezones and time-dimension identities match;
4. each window starts at its reset boundary;
5. each window spans no more than one reset period;
6. scalar/segmented comparisons cover equal elapsed duration.

For time-series and panel calendar alignment, two additional checks apply:

- `alignment.period` must equal the cumulative `reset_grain`;
- the referenced calendar must be present in the session and the same one must
  govern both sides of the single alignment operation.

Examples:

```python
mtd_delta = session.compare(
    this_month_mtd,
    prior_month_mtd,
    alignment=mv.holiday_and_dow_aligned(
        calendar=mv.CalendarRef("cn_holidays"),
        period="month",
        fallback="drop",
    ),
)
```

The calendar engine pairs day-of-week or holiday position under its existing
typed contract. It does not change how the cumulative values were computed:
both sources are still calendar-reset MTD values. Calendar alignment changes
only which observed cutoffs are compared. It does not infer Nth-workday
equivalence.

`fallback="nearest_prior_workday"` remains an explicit policy choice. Every
fallback count is surfaced; it is never described as an exact match.

## Trailing Rules

Both sides must share canonical `span_seconds`, query grain, report timezone,
time-dimension identity, and comparable value semantics. The authored
`(count, unit)` payloads may differ only when their normalized duration is
equal.

Calendar-backed alignment pairs the **evaluation cutoffs** of rolling values.
It does not shift or resize the underlying trailing window. A rolling-7d value
aligned to a holiday remains the exact 7-day value observed at that cutoff.

Different canonical durations remain blocked with an error that states both
durations and routes the agent according to intent:

- observe and compare the same trailing metric for a period-over-period change;
- use `correlate` for short-window versus long-window association;
- observe the base flow for a direct period total.

The error never suggests changing one marker in metadata or treating a 7d/28d
level difference as an ordinary delta.

## Typed Comparison Identity and Cumulative Alignment Evidence

Canonical anchor equivalence and row accounting use typed, versioned persistence
owners instead of adding unvalidated keys to the existing `alignment` dump:

```python
@dataclass(frozen=True)
class ExactComparisonSemanticsV1:
    schema: Literal["exact-comparison-semantics/v1"]
    comparable_semantics_fingerprint: str

@dataclass(frozen=True)
class CumulativeEquivalentComparisonSemanticsV1:
    schema: Literal["cumulative-equivalent-comparison-semantics/v1"]
    current_expression_fingerprint: str
    baseline_expression_fingerprint: str
    canonical_expression_fingerprint: str
    current_comparable_semantics_fingerprint: str
    baseline_comparable_semantics_fingerprint: str
    canonical_comparable_semantics_fingerprint: str

type DeltaComparisonSemantics = (
    ExactComparisonSemanticsV1
    | CumulativeEquivalentComparisonSemanticsV1
)

@dataclass(frozen=True)
class DeltaComparisonIdentity:
    schema: Literal["delta-comparison/v2"]
    current: MetricIdentity
    baseline: MetricIdentity
    current_artifact_id: str
    baseline_artifact_id: str
    semantics: DeltaComparisonSemantics
    alignment_policy_fingerprint: str
    attribution_basis_fingerprint: str | None = None

class CumulativePairSummaryV1(BaseModel):
    schema: Literal["cumulative-pair-summary/v1"]
    matched_rows: int = Field(ge=0)
    matched_null_rows: int = Field(ge=0)
    current_unpaired_rows: int = Field(ge=0)
    baseline_unpaired_rows: int = Field(ge=0)
    fallback_rows: int = Field(ge=0)
    unpaired_action: Literal["dropped"] = "dropped"

class CumulativeAlignmentV1(BaseModel):
    schema: Literal["cumulative-alignment/v1"]
    current_authored_anchor: AuthoredComparablePeriodAnchorV1
    baseline_authored_anchor: AuthoredComparablePeriodAnchorV1
    canonical_anchor: (
        TrailingAnchorSemanticsV1 | GrainToDateAnchorSemanticsV1
    )
    pairs: CumulativePairSummaryV1
```

`DeltaFrameMeta.comparison_identity` accepts only
`DeltaComparisonIdentity`. Ordinary and all-history comparisons use
`ExactComparisonSemanticsV1`; trailing and grain-to-date comparisons use
`CumulativeEquivalentComparisonSemanticsV1`, even when their authored anchors
happen to be syntactically equal. The identity's ordered source artifact ids
distinguish the exact observations. The semantics variant then makes the
admission rule explicit without optional cumulative fields on ordinary deltas.

The exact variant stores one comparable-semantics fingerprint because both
inputs were required to match it. The cumulative-equivalent variant stores both
raw expression and comparable-semantics fingerprints plus their single
canonical projections. Raw fields preserve the authored contracts; canonical
fields own semantic admission, while the complete ordered identity owns cache
identity and audit.

`CumulativeAlignmentV1` rejects mixed authored kinds. For trailing anchors it
recomputes both durations and requires each to equal
`canonical_anchor.span_seconds`; for grain-to-date it requires both authored
reset grains to equal `canonical_anchor.reset_grain`. Identity construction
likewise validates exact equality for the exact variant, or recomputes both
projected expression fingerprints and both canonical comparable fingerprints
for the cumulative-equivalent variant before accepting their single stored
canonical values. Callers cannot supply these identity or evidence models as
trusted input.

`DeltaFrameMeta.cumulative_alignment: CumulativeAlignmentV1 | None` is the
authoritative persisted field. The compare job params carry the same model dump
and the comparison identity dump so cache reconstruction and cold recovery
validate them through the same models.
The existing `alignment` dump continues to own policy, calendar, coverage, and
segment diagnostics; it does not duplicate the authoritative cumulative
summary. `.show()`, `.contract()`, quality assessment, and evidence extraction
read the typed field and comparison identity rather than reconstructing identity
facts from presentation dictionaries.

This is an intentional cache and recovery cutover. The runtime does not
dual-read `DeltaComparisonIdentityV1`, migrate old delta artifacts, or preserve
their cache keys. A caller that still needs an old analysis result reruns
`session.compare(...)` from its source frames and receives a new-schema
artifact. This is acceptable because reuse of prior analysis results is a weak
dependency;
keeping one identity and recovery path is more valuable than retaining old
delta cache hits.

Recovery validates non-empty fingerprints, `matched_null_rows <= matched_rows`,
`fallback_rows <= matched_rows`, and
`DeltaFrameMeta.row_count == pairs.matched_rows`. Invalid or incomplete
cumulative alignment evidence fails recovery instead of being rendered as a
trusted comparison.

## Paired-Only Cumulative Rows

For both anchors, only rows with a mechanically matched current and baseline
cutoff enter the `DeltaFrame`. This replaces one-sided outer-union semantics for
cumulative values.

The typed cumulative alignment record includes:

```python
{
    "schema": "cumulative-alignment/v1",
    "pairs": {
        "schema": "cumulative-pair-summary/v1",
        "matched_rows": 5,
        "matched_null_rows": 0,
        "current_unpaired_rows": 0,
        "baseline_unpaired_rows": 2,
        "fallback_rows": 1,
        "unpaired_action": "dropped",
    }
}
```

`strict_lengths=True` still rejects unequal expected ordinal lengths. Under the
default non-strict policy, tails are dropped and counted.

Pair accounting is collected before the parent filter and is not derived only
from the aggregate `CalendarInfo`:

- ordinal time-series/panel branches contribute their matched and unpaired row
  counts from window-pair coverage;
- a two-sided calendar segment contributes matched, dropped, and fallback row
  counts from that segment's `CalendarInfo`;
- a current-only or baseline-only panel segment contributes the exact number of
  source rows on that side before its synthetic `new`/`churned` rows are
  removed.

The per-branch counts are summed into `CumulativePairSummaryV1`. This explicitly
covers one-sided panel segments, which are represented by `segment_info` but are
not included in the existing aggregate `CalendarInfo` dropped-row counts.

Dropping affects delta rows and component sidecars identically. Compare first
materializes the matched parent pair-key set, then restricts every component
sidecar to that exact set. A component artifact may not retain a tail or
one-sided segment absent from its parent delta.

## Agent-Visible Behavior

`MetricFrame.contract()` states canonical rather than syntactic preconditions:

```text
compare: conditional: trailing span must equal 604800 seconds; equivalent fixed units are accepted
```

For grain-to-date it states both legal alignment families:

```text
compare: conditional: one boundary-anchored month, same query grain; use ordinal or month-period calendar alignment
```

`DeltaFrame.show()` summarizes anchor equivalence and pairing:

```text
cumulative_alignment: trailing span=604800s authored=current(7 day), baseline(1 week)
pairing: matched=5 matched_null=0 current_unpaired=0 baseline_unpaired=2 fallback=1 action=dropped
caveat: rolling values overlap and are autocorrelated
```

For MTD calendar alignment:

```text
cumulative_alignment: grain_to_date reset=month policy=holiday_and_dow_aligned
calendar: cn_holidays matched=5 fallback=0 dropped_current=0 dropped_baseline=1
```

The same facts appear as typed preconditions/evidence, not only prose.

## Failure Matrix

| Case | Result |
| --- | --- |
| trailing 7 day vs 1 week | supported after duration normalization |
| trailing 7 day vs 28 day | blocked: canonical span mismatch |
| MTD vs MTD, ordinal, one period | supported |
| MTD vs MTD, holiday/DOW, `period="month"` | supported |
| MTD with calendar `period="quarter"` | blocked: alignment/reset mismatch |
| MTD vs QTD | blocked: reset-grain mismatch |
| grain-to-date multi-period window | blocked |
| scalar with calendar policy | blocked |
| paired rows plus tails | paired rows supported; tails dropped and surfaced |
| calendar fallback used | supported with exact fallback count and caveat |

## Implementation Seams

- Add one shared canonical anchor projection in `analysis._cumulative`; do not
  alter semantic IR hashes.
- Rebuild the comparison projection bottom-up with canonical trailing anchors,
  re-intern every changed node and ancestor, remap roots/occurrences, and hash
  the versioned projected roots; do not mutate semantic graphs or reuse stale
  node ids.
- Build a comparison-only comparable-semantics fingerprint from that canonical
  expression fingerprint and every non-expression field from the ordinary
  comparable-value contract; do not weaken general compare admission.
- Replace `DeltaComparisonIdentityV1` with `DeltaComparisonIdentity` across
  delta metadata, typed evidence subjects, job validation, persistence, cache
  identity, recovery, and downstream consumers. Use the exact semantics variant
  for ordinary/all-history comparisons and the cumulative-equivalent variant
  for trailing/grain-to-date comparisons; add no union, dual-read, or migration
  branch for V1 artifacts.
- Extend grain-to-date policy validation to the existing calendar-backed kinds
  with `alignment.period == reset_grain`.
- Generalize the current grain-to-date paired-row filter to all cumulative
  trailing/grain-to-date time-bearing comparisons and component sidecars.
- Accumulate row-level pair facts across ordinal/calendar and all panel segment
  branches before filtering; do not infer panel dropped rows solely from
  aggregate `CalendarInfo`.
- Persist one `CumulativeAlignmentV1` typed field containing authored/canonical
  anchors and `CumulativePairSummaryV1`; keep raw/canonical fingerprints in the
  comparison identity and retain the complete existing policy alignment
  dump.
- Synchronize live help, frame guidance, structured errors, active specs,
  EN/CN site docs, and packaged analysis skill routing.

## Acceptance Criteria

- Unit-equivalent trailing anchors compare without changing authored hashes or
  source marker payloads.
- Non-equivalent spans and reset grains fail before backend work with exact
  canonical received/expected values.
- MTD/QTD/YTD time series support the existing calendar policies only when
  `alignment.period` matches the reset grain.
- DOW, holiday, adjusted-workday fallback, DST/report-timezone, and reset-
  boundary regressions produce deterministic pairs and visible counts; no
  surface advertises Nth-workday alignment.
- Calendar panel tests include current-only and baseline-only segments and prove
  that every removed source row appears in the typed pair summary.
- Ordinal and calendar cumulative comparisons remove all one-sided parent and
  component rows; recovery preserves the same pair summary.
- Recovery rejects malformed or incomplete typed cumulative alignment evidence,
  and canonical fingerprint tests cover nested cumulative nodes and ancestors.
- Identity round-trip tests prove ordinary/all-history comparisons use the
  exact variant, trailing/grain-to-date comparisons use the
  cumulative-equivalent variant, V1 artifacts fail with a concrete rerun repair,
  and matched null rows remain visible after recovery.
- Runtime, help, `.show()`, `.contract()`, errors, active specs, site docs, and
  tests never advertise 7d/28d or MTD/QTD as ordinary comparable changes.
