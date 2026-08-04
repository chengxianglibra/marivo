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
   trailing and grain-to-date time series by business position;
3. cumulative compare keeps paired rows only and exposes every dropped or
   fallback row in alignment evidence.

True anchor mismatches remain blocked. Rolling 7-day versus rolling 28-day and
MTD versus QTD are different measures, not two periods of the same measure.

This design is independent of all-history compare and cumulative attribution.
It changes only anchor equivalence and the alignment of already-observed
trailing/grain-to-date frames.

## Problem

The current runtime correctly rejects different trailing payloads and different
grain-to-date reset grains. It also restricts grain-to-date comparison to
ordinal `window_bucket` alignment. That boundary avoids wrong answers, but it
leaves two useful cases unsupported or unnecessarily awkward:

- semantically equal fixed durations authored with different units are rejected
  because tuple payloads differ;
- business reporting often compares the same elapsed workday or holiday
  position, not merely the same calendar-day ordinal.

For example, “MTD revenue after the third working day versus the prior month's
third working day” is a high-value comparable-period question. Pairing July 3
with June 3 can be wrong when weekends or holidays differ. Conversely, allowing
MTD and QTD because both are “to date” would be a category error.

The missing capability is a canonical anchor-equivalence predicate plus
anchor-aware use of the alignment policies Marivo already owns.

## Goals

- Preserve strict semantic comparability while accepting equivalent fixed
  trailing durations.
- Reuse `mv.window_bucket()`, `mv.dow_aligned()`,
  `mv.holiday_aligned()`, and `mv.holiday_and_dow_aligned()`; add no
  cumulative-only policy constructor.
- Support business-day and holiday-position comparison for cumulative time
  series and panels.
- Keep reset-boundary, single-period, query-grain, report-timezone, and value-
  semantics checks fail-closed.
- Drop unpaired cumulative rows rather than fabricate new/churned levels.
- Persist enough alignment evidence for `.show()`, `.contract()`, quality
  assessment, recovery, and audit.

## Non-Goals

- Comparing genuinely different trailing spans or reset grains.
- Automatically choosing a holiday calendar or declaring two calendars
  equivalent.
- Adding fiscal reset semantics to `ms.grain_to_date`; fiscal cumulative
  authoring requires its own semantic design.
- Comparing scalar cumulative values by a calendar policy.
- Hiding unequal elapsed spans, unmatched holidays, fallback rows, or tails.
- Treating rolling-window autocorrelation as independent observations.

## Canonical Anchor Semantics

Authored cumulative anchors keep their existing IR and hash identity. Compare
derives a separate closed semantic projection:

```python
class TrailingAnchorSemanticsV1(BaseModel):
    kind: Literal["trailing"]
    span_seconds: int

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

The original authored payload remains available in each source frame and its
artifact identity. `DeltaFrameMeta.alignment.cumulative_anchor` records both
payloads and the canonical projection, so equivalence never erases provenance.

The existing `ComparableValueSemanticsV1.expression_fingerprint` includes the
authored cumulative node and therefore differs for `7 day` and `1 week` even
when their duration is equal. Compare must not bypass the rest of that contract
or pretend the raw fingerprints match. It derives a comparison-only
`canonical_cumulative_expression_fingerprint` by canonicalizing only the
trailing anchor payload inside both persisted expression graphs. Admission
requires that canonical fingerprint plus every other comparable-value field
(`evaluator_contracts`, slice, key schema, unit, fold, source domain, and
definition transform) to match exactly. Both original expression fingerprints
remain in the comparison identity.

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
business-position pairs. The existing ordinal mode owns elapsed calendar
position; calendar-backed policies own workday/holiday position.

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

The calendar engine pairs period position under its existing typed contract.
It does not change how the cumulative values were computed: both sources are
still calendar-reset MTD values. Calendar alignment changes only which observed
cutoffs are compared.

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

## Paired-Only Cumulative Rows

For both anchors, only rows with a mechanically matched current and baseline
cutoff enter the `DeltaFrame`. This replaces one-sided outer-union semantics for
cumulative values.

The alignment dump includes:

```python
{
    "cumulative_pairs": {
        "matched_rows": 5,
        "current_unpaired_rows": 0,
        "baseline_unpaired_rows": 2,
        "fallback_rows": 1,
        "unpaired_action": "dropped",
    }
}
```

`strict_lengths=True` still rejects unequal expected ordinal lengths. Under the
default non-strict policy, tails are dropped and counted. Calendar alignment
uses the existing `CalendarInfo` dropped/fallback counts and projects them into
the same cumulative-pair summary.

Dropping affects delta rows and component sidecars identically. A component
artifact may not retain a tail absent from its parent delta.

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
pairing: matched=5 current_unpaired=0 baseline_unpaired=2 fallback=1 action=dropped
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
- Build a comparison-only canonical cumulative expression fingerprint and
  compare it together with every non-expression field from the ordinary
  comparable-value contract; do not weaken general compare admission.
- Extend grain-to-date policy validation to the existing calendar-backed kinds
  with `alignment.period == reset_grain`.
- Generalize the current grain-to-date paired-row filter to all cumulative
  trailing/grain-to-date time-bearing comparisons and component sidecars.
- Normalize ordinal and calendar matching facts into one typed cumulative-pair
  summary while retaining the complete existing alignment dump.
- Synchronize live help, frame guidance, structured errors, active specs,
  EN/CN site docs, and packaged analysis skill routing.

## Acceptance Criteria

- Unit-equivalent trailing anchors compare without changing authored hashes or
  source marker payloads.
- Non-equivalent spans and reset grains fail before backend work with exact
  canonical received/expected values.
- MTD/QTD/YTD time series support the existing calendar policies only when
  `alignment.period` matches the reset grain.
- Workday, holiday, adjusted-workday, fallback, DST/report-timezone, and reset-
  boundary regressions produce deterministic pairs and visible counts.
- Ordinal and calendar cumulative comparisons remove all one-sided parent and
  component rows; recovery preserves the same pair summary.
- Runtime, help, `.show()`, `.contract()`, errors, active specs, site docs, and
  tests never advertise 7d/28d or MTD/QTD as ordinary comparable changes.
