"""Issue #50: semantic cumulative fiscal reset — evidence, fail-closed, rollup.

These tests lock the three acceptance surfaces that were still open after the
core fiscal reset landed:

1. fiscal ``grain_to_date`` compare produces complete typed cumulative alignment
   evidence (no bare ``ValueError`` on the identity/evidence path);
2. period-calendar boundary integrity is fail-closed (gap / overlap / coverage /
   undeclared level are rejected by ``PeriodCalendarSnapshotV1`` itself before any
   backend scan);
3. fiscal rollup determinism is covered in ``test_analysis_cumulative_observe.py``
   (the ``transform.rollup`` period-end fold); re-running the fold is asserted
   here for determinism against the certified fiscal binding.
"""

from __future__ import annotations

from datetime import date

import pytest

from marivo._temporal import (
    PeriodCalendarSnapshotV1,
    PeriodRecord,
    _require_contiguous_periods,
    _snapshot_digest,
    semantic_grain,
)
from marivo.analysis._cumulative import (
    AuthoredSemanticGrainToDateAnchorV1,
    CumulativeAlignmentV1,
    CumulativePairSummaryV1,
    SemanticGrainToDateAnchorSemanticsV1,
    authored_comparable_period_anchor,
    canonical_comparable_period_anchor,
    cumulative_alignment_evidence,
)
from marivo.refs import ref as ref_factory


def _fiscal_grain(calendar: str = "sales.fiscal", level: str = "fiscal_month"):
    return semantic_grain(calendar=ref_factory.period_calendar(calendar), level=level)


def _anchor(calendar: str = "sales.fiscal", level: str = "fiscal_month"):
    return ("grain_to_date", _fiscal_grain(calendar=calendar, level=level))


def _pairs() -> CumulativePairSummaryV1:
    return CumulativePairSummaryV1(
        schema="cumulative-pair-summary/v1",
        matched_rows=2,
        matched_null_rows=0,
        current_unpaired_rows=0,
        baseline_unpaired_rows=0,
        fallback_rows=0,
        unpaired_action="dropped",
    )


# ---------------------------------------------------------------------------
# 1. Fiscal grain_to_date compare evidence is typed and complete.
# ---------------------------------------------------------------------------


def test_authored_anchor_accepts_semantic_grain_to_date() -> None:
    authored = authored_comparable_period_anchor(_anchor())
    assert isinstance(authored, AuthoredSemanticGrainToDateAnchorV1)
    assert authored.calendar_ref == "sales.fiscal"
    assert authored.level == "fiscal_month"


def test_canonical_anchor_accepts_semantic_grain_to_date() -> None:
    canonical = canonical_comparable_period_anchor(_anchor())
    assert isinstance(canonical, SemanticGrainToDateAnchorSemanticsV1)
    assert canonical.calendar_ref == "sales.fiscal"
    assert canonical.level == "fiscal_month"


def test_semantic_anchor_alignment_evidence_is_complete() -> None:
    evidence = cumulative_alignment_evidence(
        current_anchor=_anchor(),
        baseline_anchor=_anchor(),
        pairs=_pairs(),
    )
    assert isinstance(evidence, CumulativeAlignmentV1)
    assert isinstance(evidence.current_authored_anchor, AuthoredSemanticGrainToDateAnchorV1)
    assert isinstance(evidence.baseline_authored_anchor, AuthoredSemanticGrainToDateAnchorV1)
    assert isinstance(evidence.canonical_anchor, SemanticGrainToDateAnchorSemanticsV1)
    assert evidence.canonical_anchor.calendar_ref == "sales.fiscal"
    assert evidence.canonical_anchor.level == "fiscal_month"


def test_semantic_anchor_alignment_rejects_cross_calendar_mismatch() -> None:
    with pytest.raises(ValueError, match="not canonically equivalent"):
        cumulative_alignment_evidence(
            current_anchor=_anchor(calendar="sales.fiscal"),
            baseline_anchor=_anchor(calendar="sales.retail"),
            pairs=_pairs(),
        )


def test_semantic_anchor_alignment_rejects_level_mismatch() -> None:
    with pytest.raises(ValueError, match="not canonically equivalent"):
        cumulative_alignment_evidence(
            current_anchor=_anchor(level="fiscal_month"),
            baseline_anchor=_anchor(level="fiscal_week"),
            pairs=_pairs(),
        )


# ---------------------------------------------------------------------------
# 2. Period-calendar boundary integrity is fail-closed.
# ---------------------------------------------------------------------------


def _period(level: str, key: str, start: date, end: date, ordinal: int) -> PeriodRecord:
    return PeriodRecord(
        level_name=level,
        key=key,
        start_date=start,
        end_date=end,
        global_ordinal=ordinal,
    )


_COVERAGE = (date(2026, 1, 1), date(2026, 1, 15))
_LEVELS = ("day", "fiscal_week")


def test_contiguous_periods_accepts_valid_tiling() -> None:
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 8), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 15), 1),
    )
    _require_contiguous_periods(_LEVELS, periods, _COVERAGE)  # no raise


def test_contiguous_periods_rejects_gap() -> None:
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 7), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 15), 1),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        _require_contiguous_periods(_LEVELS, periods, _COVERAGE)


def test_contiguous_periods_rejects_overlap() -> None:
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 9), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 15), 1),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        _require_contiguous_periods(_LEVELS, periods, _COVERAGE)


def test_contiguous_periods_rejects_escaped_coverage() -> None:
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 8), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 16), 1),
    )
    with pytest.raises(ValueError, match="escapes certified coverage"):
        _require_contiguous_periods(_LEVELS, periods, _COVERAGE)


def test_contiguous_periods_rejects_undeclared_level() -> None:
    periods = (_period("fiscal_month", "M1", date(2026, 1, 1), date(2026, 1, 15), 0),)
    with pytest.raises(ValueError, match="not declared"):
        _require_contiguous_periods(_LEVELS, periods, _COVERAGE)


def test_snapshot_construction_rejects_gap_even_with_valid_digest() -> None:
    """The digest is only one defense; boundary integrity is enforced by the
    snapshot type itself, so a hand-built (or tampered-but-rehashed) snapshot
    with a gap still fails closed before any backend scan."""
    calendar_ref = ref_factory.period_calendar("sales.fiscal")
    coverage = (date(2026, 1, 1), date(2026, 1, 15))
    levels = ("day", "fiscal_week")
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 7), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 15), 1),
    )
    digest = _snapshot_digest(
        calendar_ref=calendar_ref,
        boundary_timezone="UTC",
        coverage=coverage,
        levels=levels,
        periods=periods,
        containments=(),
        correspondences=(),
    )
    with pytest.raises(ValueError, match="gap or overlap"):
        PeriodCalendarSnapshotV1(
            calendar_ref=calendar_ref,
            boundary_timezone="UTC",
            coverage=coverage,
            levels=levels,
            periods=periods,
            containments=(),
            correspondences=(),
            snapshot_digest=digest,
        )
