"""Independent civil-date witnesses for exact finite calendar shifts."""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

import marivo.analysis as mv
from marivo._temporal_grid import CivilShiftAbsent, CivilShiftTarget, TimeShiftAbsent
from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, TimestampDataType
from marivo.analysis.core.frame_calendar import FrozenSourceCalendar, freeze_calendar
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    build_partial_calendar_map,
    calendar_match_intents,
    read_calendar_directory,
    validate_frozen_coordinates,
)
from marivo.analysis.dsl.temporal import exact_scope
from marivo.analysis.errors import AnalysisError


def calendar(start: str, end: str, unit: str = "week", zone: str = "UTC") -> mv.CalendarAxis:
    from marivo._temporal import builtin_grain

    return exact_scope(start, end, zone).by(builtin_grain(unit))


def bounds(axis: mv.CalendarAxis) -> tuple[tuple[str, str], ...]:
    return tuple((cell.start.isoformat(), cell.end.isoformat()) for cell in axis.grid.cells)


def frozen(
    axis: mv.CalendarAxis, token: str = "period"
) -> tuple[FrozenSourceCalendar, FrozenCalendarDirectory]:
    captured = freeze_calendar(
        axis._binding,
        token=token,
        name="period",
        key=DataColumn(f"key.{token}", "period", TimestampDataType("us", "UTC"), False),
    )
    return captured, read_calendar_directory(
        captured.axis, captured.directory.schema, captured.directory.rows
    )


def test_iso_week_year_is_distinct_from_fifty_two_weeks() -> None:
    current = calendar("2021-01-04", "2021-01-11")
    assert bounds(current.shift(mv.offset(years=-1))) == (
        ("2019-12-30T00:00:00+00:00", "2020-01-06T00:00:00+00:00"),
    )
    assert bounds(current.shift(mv.offset(periods=-52))) == (
        ("2020-01-06T00:00:00+00:00", "2020-01-13T00:00:00+00:00"),
    )
    assert current.scope is not None
    assert current.shift(mv.offset(years=-1)).scope is None


def test_sparse_and_empty_selection_do_not_create_enclosing_members() -> None:
    current = calendar("2021-12-27", "2022-01-10")
    baseline = current.shift(mv.offset(years=-1))
    assert bounds(baseline) == (
        ("2020-12-21T00:00:00+00:00", "2020-12-28T00:00:00+00:00"),
        ("2021-01-04T00:00:00+00:00", "2021-01-11T00:00:00+00:00"),
    )
    captured, directory = frozen(baseline)
    assert directory.buckets[1].nominal_ordinal - directory.buckets[0].nominal_ordinal == 2
    validate_frozen_coordinates(
        DataSchema((captured.axis,), ()),
        captured.directory.rows.iloc[:, :1],
        tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in captured.attachments),
    )
    missing = calendar("2020-12-28", "2021-01-04").shift(mv.offset(years=-1))
    assert missing.grid.cells == () and missing.scope is None
    assert missing.grid.origin is not None
    assert isinstance(missing.grid.origin.entries[0], TimeShiftAbsent)
    assert missing.grid.origin.entries[0].reason == "period_absent"
    assert missing.shift(mv.offset(years=1)).grid.cells == ()


@pytest.mark.parametrize(
    ("start", "end", "unit", "rule", "expected"),
    [
        (
            "2024-02-01",
            "2024-03-01",
            "month",
            mv.offset(years=-1),
            (("2023-02-01T00:00:00+00:00", "2023-03-01T00:00:00+00:00"),),
        ),
        (
            "2024-01-01",
            "2024-03-01",
            "year",
            mv.offset(years=-1),
            (("2023-01-01T00:00:00+00:00", "2023-03-01T00:00:00+00:00"),),
        ),
        (
            "2026-03-01",
            "2026-03-20T12:00:00+00:00",
            "month",
            mv.offset(periods=-1),
            (("2026-02-01T00:00:00+00:00", "2026-02-20T12:00:00+00:00"),),
        ),
        (
            "2026-03-16",
            "2026-03-19T12:30:00+00:00",
            "week",
            mv.offset(periods=-1),
            (("2026-03-09T00:00:00+00:00", "2026-03-12T12:30:00+00:00"),),
        ),
    ],
)
def test_full_buckets_and_prefixes_keep_civil_roles(
    start: str,
    end: str,
    unit: str,
    rule: mv.CalendarCorrespondence,
    expected: tuple[tuple[str, str], ...],
) -> None:
    assert bounds(calendar(start, end, unit).shift(rule)) == expected


@pytest.mark.parametrize(
    ("start", "end", "unit", "rule"),
    [
        ("2026-03-01", "2026-03-30T12:00:00+00:00", "month", mv.offset(periods=-1)),
        ("2024-01-01", "2024-02-29", "year", mv.offset(years=-1)),
    ],
)
def test_absent_civil_cutoff_never_clamps_current(
    start: str, end: str, unit: str, rule: mv.CalendarCorrespondence
) -> None:
    current = calendar(start, end, unit)
    original = current.grid
    baseline = current.shift(rule)
    assert current.grid == original
    assert baseline.grid.cells == ()
    assert baseline.grid.origin is not None
    entry = baseline.grid.origin.entries[0]
    assert isinstance(entry, TimeShiftAbsent) and entry.reason == "cutoff_absent"


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-04-01", "2026-04-08T02:30:00-04:00"),
        ("2026-12-01", "2026-12-01T01:30:00-05:00"),
    ],
)
def test_nonzero_target_gap_or_fold_requires_explicit_intervals(start: str, end: str) -> None:
    with pytest.raises(ValueError, match="instants"):
        calendar(start, end, "month", "America/New_York").shift(mv.offset(periods=-1))


@pytest.mark.parametrize(
    ("end", "utc"),
    [
        ("2026-11-01T01:30:00-04:00", "2026-11-01T05:30:00+00:00"),
        ("2026-11-01T01:30:00-05:00", "2026-11-01T06:30:00+00:00"),
    ],
)
def test_zero_offset_copies_aware_fold_instants_after_admission(
    end: str, utc: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = calendar("2026-11-01", end, "month", "America/New_York")
    unsupported = calendar("2026-11-01", "2026-11-02", "day")

    def refuse(*args: object, **kwargs: object) -> datetime:
        raise AssertionError("zero offset must not resolve a civil instant")

    monkeypatch.setattr("marivo._temporal_grid.instant", refuse)
    for rule in (mv.offset(periods=0), mv.offset(years=0)):
        baseline = current.shift(rule)
        assert baseline.grid.cells == current.grid.cells
        assert baseline.grid.civil_bounds == current.grid.civil_bounds
        assert baseline.grid.cells[0].end.isoformat() == utc
    with pytest.raises(ValueError):
        unsupported.shift(mv.offset(periods=0))


def test_shift_is_immutable_and_records_only_immediate_stripped_origin() -> None:
    current = calendar("2026-01-01", "2026-03-01", "month")
    first = current.shift(mv.offset(years=-1))
    second = first.shift(mv.offset(years=-1))
    assert second.grid.origin is not None and second.grid.origin.grid.origin is None
    assert second.grid.origin.grid.cells == first.grid.cells
    assert current.grid.origin is None
    with pytest.raises(FrozenInstanceError):
        first.scope = current.scope
    equivalent = current.shift(mv.offset(periods=-12))
    assert first.grid.cells == equivalent.grid.cells
    assert first.grid.identity != equivalent.grid.identity


def test_full_week_shift_keeps_natural_boundaries_across_dst() -> None:
    current = calendar("2026-03-09", "2026-03-16", "week", "America/New_York")
    previous = current.shift(mv.offset(periods=-1))
    assert bounds(previous) == (("2026-03-02T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),)
    assert (previous.grid.cells[0].end - previous.grid.cells[0].start).total_seconds() == 167 * 3600
    assert (current.grid.cells[0].end - current.grid.cells[0].start).total_seconds() == 168 * 3600


def test_equivalent_month_rules_have_equal_finite_intentions() -> None:
    _, current = frozen(calendar("2026-03-01", "2026-03-20T12:00:00+00:00", "month"))
    assert calendar_match_intents(current, mv.offset(years=-1)) == calendar_match_intents(
        current, mv.offset(periods=-12)
    )


@pytest.mark.parametrize(
    ("start", "end", "unit"),
    [
        ("2026-01-02", "2026-01-08", "week"),
        ("2026-01-02", "2026-02-01", "month"),
        ("2026-01-01", "2026-01-02", "day"),
        ("2026-01-01", "2026-04-01", "quarter"),
    ],
)
def test_shift_rejects_unsupported_grain_or_mid_bucket_range(
    start: str, end: str, unit: str
) -> None:
    with pytest.raises(ValueError):
        calendar(start, end, unit).shift(mv.offset(years=-1))
    with pytest.raises(ValueError):
        calendar(start, end, unit).shift(mv.ordinal())


def test_source_free_intents_distinguish_absence_from_unselected_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, current = frozen(calendar("2020-12-28", "2021-01-04"), "current")

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("fixed-data matching must not consult timezone rules")

    monkeypatch.setattr("marivo._temporal_grid.parse_timezone", refuse)
    years = calendar_match_intents(current, mv.offset(years=-1))[0].result
    periods = calendar_match_intents(current, mv.offset(periods=-52))[0].result
    assert isinstance(years, CivilShiftAbsent) and years.reason == "period_absent"
    assert isinstance(periods, CivilShiftTarget)
    assert periods.bounds[0] == datetime(2019, 12, 30)


def test_source_calendar_freezing_uses_original_captured_civil_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = calendar("2026-11-01", "2026-11-01T01:30:00-04:00", "month", "America/New_York")

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("freezing must not reconstruct original civil boundaries")

    monkeypatch.setattr("marivo._temporal_grid.parse_timezone", refuse)
    _, directory = frozen(current)
    assert directory.buckets[0].actual_end_civil == "2026-11-01T01:30:00.000000-04:00"
    assert directory.buckets[0].actual_end.isoformat() == "2026-11-01T05:30:00+00:00"


def test_frozen_prefix_matching_requires_exact_saved_progress_and_no_tz_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, current = frozen(calendar("2026-03-01", "2026-03-20T12:00:00+00:00", "month"), "current")
    _, baseline = frozen(calendar("2026-02-01", "2026-02-20T12:00:00+00:00", "month"), "baseline")
    _, full = frozen(calendar("2026-02-01", "2026-03-01", "month"), "full")

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("fixed-data matching must not consult timezone rules")

    monkeypatch.setattr("marivo._temporal_grid.parse_timezone", refuse)
    assert (
        build_partial_calendar_map(current, baseline, mv.offset(periods=-1))[0].right_key
        == baseline.buckets[0].key
    )
    with pytest.raises(AnalysisError, match="progress"):
        build_partial_calendar_map(current, full, mv.offset(periods=-1))
    with pytest.raises(AnalysisError, match="complete actual"):
        build_partial_calendar_map(current, baseline, mv.ordinal())


def test_offset_exclusive_integer_keywords_and_out_of_range_errors() -> None:
    from marivo.analysis.dsl.correspondence import offset

    for kwargs in (
        {},
        {"years": -1, "periods": -12},
        {"years": True},
        {"periods": None},
        {"years": -1, "periods": None},
    ):
        with pytest.raises(AnalysisError):
            offset.__call__(**kwargs)
    with pytest.raises(ValueError, match="representable"):
        calendar("2026-01-01", "2026-02-01", "month").shift(mv.offset(years=-2026))
