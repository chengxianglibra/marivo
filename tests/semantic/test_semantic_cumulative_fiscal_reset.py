"""Certified calendar tiling rejects invalid boundaries before any source scan."""

from datetime import date

import pytest

from marivo._temporal import (
    PeriodCalendarSnapshotV1,
    PeriodRecord,
    _require_contiguous_periods,
    _snapshot_digest,
)
from marivo.refs import ref as ref_factory


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


def test_contiguous_periods_rejects_declared_level_with_zero_periods() -> None:
    """A declared non-day level must carry at least one certified period."""
    levels = ("day", "fiscal_week", "fiscal_month")
    periods = (
        _period("fiscal_week", "W1", date(2026, 1, 1), date(2026, 1, 8), 0),
        _period("fiscal_week", "W2", date(2026, 1, 8), date(2026, 1, 15), 1),
    )
    with pytest.raises(ValueError, match="zero periods"):
        _require_contiguous_periods(levels, periods, _COVERAGE)


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
