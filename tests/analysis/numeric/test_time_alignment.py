"""Independent calendar-key kernels and current public period correspondence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
import pytest
from pydantic import TypeAdapter

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import (
    TimeScope,
    certify_period_calendar,
    certify_temporal_set,
    semantic_grain,
    time_scope,
)
from marivo.analysis._comparison import (
    OrdinalTimeKey,
    _freeze_time,
    _TimeKey,
    holiday,
    holiday_then_weekday,
    weekday,
)
from marivo.analysis.core.time_alignment import (
    HolidayThenWeekdayTimeMapping,
    HolidayTimeMapping,
    TimeKeyMapping,
    evaluate_time_keys,
)
from marivo.analysis.core.time_grid import BoundTimeGrid, bind_grid
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_period_alignment import period
from tests.semantic.preview_helpers import certified_project, default_scope
from tests.shared_fixtures import DslCaseFactory

KeyKind = Literal["ordinal", "weekday", "holiday", "holiday_then_weekday"]


def occurrences(
    *,
    zone: str = "UTC",
    start: date | datetime = date(2026, 8, 2),
    end: date | datetime = date(2026, 8, 4),
    baseline_start: date = date(2026, 7, 2),
    baseline_end: date = date(2026, 7, 4),
) -> tuple[TimeScope, TimeScope]:
    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
        boundary_timezone=zone,
        coverage=(date(2026, 1, 1), date(2027, 1, 1)),
        rows=(
            {"id": "current", "start": start, "end": end},
            {
                "id": "baseline",
                "start": baseline_start if type(start) is date else start,
                "end": baseline_end if type(end) is date else end,
            },
        ),
        occurrence_id="id",
        start="start",
        end="end",
    )
    return snapshot.occurrence_scope("current"), snapshot.occurrence_scope("baseline")


def time_key(kind: KeyKind, *, timestamp: bool = False) -> _TimeKey:
    if kind == "ordinal":
        return OrdinalTimeKey()
    if kind == "weekday":
        return weekday()
    current, baseline = occurrences()
    if timestamp:
        snapshot = certify_temporal_set(
            temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
            boundary_timezone="UTC",
            coverage=(date(2026, 1, 1), date(2027, 1, 1)),
            rows=tuple(
                {
                    "id": name,
                    "start": datetime.fromisoformat(f"2026-{month}-02T00:00:00+00:00"),
                    "end": datetime.fromisoformat(f"2026-{month}-04T00:00:00+00:00"),
                }
                for name, month in (("current", "08"), ("baseline", "07"))
            ),
            occurrence_id="id",
            start="start",
            end="end",
        )
        current, baseline = (
            snapshot.occurrence_scope("current"),
            snapshot.occurrence_scope("baseline"),
        )
    if kind == "holiday":
        return holiday(current=current, baseline=baseline)
    return holiday_then_weekday(current=current, baseline=baseline)


def grid(start: str, end: str, zone: str = "UTC") -> BoundTimeGrid:
    return bind_grid(time_scope(start=start, end=end), mv.grain("day"), report_timezone=zone)


def keys(bound: BoundTimeGrid, key: _TimeKey) -> list[str]:
    return [aligned for _, aligned in evaluate_time_keys(bound, _freeze_time(key), "current")]


def test_weekday_and_ordinal_have_independent_complete_grid_oracles() -> None:
    bound = grid("2026-08-01", "2026-08-10")
    assert keys(bound, weekday()) == [
        "weekday:6:1",
        "weekday:7:1",
        "weekday:1:1",
        "weekday:2:1",
        "weekday:3:1",
        "weekday:4:1",
        "weekday:5:1",
        "weekday:6:2",
        "weekday:7:2",
    ]
    assert keys(bound, OrdinalTimeKey()) == [f"ordinal:{i}" for i in range(1, 10)]


def test_holiday_offsets_and_hybrid_outward_count_survive_window_clipping() -> None:
    current, baseline = occurrences()
    holiday_key = holiday(current=current, baseline=baseline)
    hybrid = holiday_then_weekday(current=current, baseline=baseline)
    bound = grid("2026-08-01", "2026-08-06")
    assert keys(bound, holiday_key) == [
        "holiday:-1",
        "holiday:0",
        "holiday:1",
        "holiday:2",
        "holiday:3",
    ]
    assert keys(bound, hybrid) == [
        "holiday_then_weekday:before:6:1",
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:after:2:1",
        "holiday_then_weekday:after:3:1",
    ]
    assert keys(grid("2026-07-18", "2026-07-19"), hybrid) == ["holiday_then_weekday:before:6:3"]
    assert keys(grid("2026-08-18", "2026-08-19"), hybrid) == ["holiday_then_weekday:after:2:3"]
    partial = grid("2026-08-01T12:00:00Z", "2026-08-03T12:00:00Z")
    assert keys(partial, holiday_key) == ["holiday:-1", "holiday:0", "holiday:1"]


def test_unequal_holidays_use_each_occurrences_own_half_open_boundary() -> None:
    current, baseline = occurrences(baseline_end=date(2026, 7, 5))
    mapping = _freeze_time(holiday_then_weekday(current=current, baseline=baseline))
    assert [
        key for _, key in evaluate_time_keys(grid("2026-08-02", "2026-08-05"), mapping, "current")
    ] == [
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:after:2:1",
    ]
    assert [
        key for _, key in evaluate_time_keys(grid("2026-07-02", "2026-07-05"), mapping, "baseline")
    ] == [
        "holiday_then_weekday:holiday:0",
        "holiday_then_weekday:holiday:1",
        "holiday_then_weekday:holiday:2",
    ]


def test_dst_and_certified_day_use_frozen_civil_authority() -> None:
    current, baseline = occurrences(
        zone="America/New_York", start=date(2026, 3, 8), end=date(2026, 3, 10)
    )
    bound = grid("2026-03-07", "2026-03-10", "America/New_York")
    assert (bound.cells[1].end - bound.cells[1].start).total_seconds() == 23 * 3600
    assert keys(bound, holiday(current=current, baseline=baseline)) == [
        "holiday:-1",
        "holiday:0",
        "holiday:1",
    ]
    snapshot = certify_period_calendar(
        calendar_ref=ms.ref.period_calendar("sales.fiscal"),
        boundary_timezone="UTC",
        coverage=(date(2026, 8, 1), date(2026, 8, 4)),
        levels={"week": "week"},
        rows=[{"date": date(2026, 8, 1) + timedelta(days=i), "week": "w"} for i in range(3)],
    )
    certified = bind_grid(
        time_scope(start="2026-08-01", end="2026-08-04"),
        semantic_grain(calendar=ms.ref.period_calendar("sales.fiscal"), level="day"),
        report_timezone="UTC",
        snapshot=snapshot,
    )
    assert keys(certified, weekday()) == ["weekday:6:1", "weekday:7:1", "weekday:1:1"]


def test_holiday_admission_and_whole_day_timestamp() -> None:
    absolute = time_scope(start="2026-08-01", end="2026-08-02")
    with pytest.raises(AnalysisError, match="certified TemporalSet occurrence"):
        holiday(current=absolute, baseline=absolute)
    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-03-08T05:00:00+00:00"),
        end=datetime.fromisoformat("2026-03-09T04:00:00+00:00"),
        zone="America/New_York",
    )
    key = holiday(current=current, baseline=baseline)
    assert keys(grid("2026-03-08", "2026-03-09", "America/New_York"), key) == ["holiday:0"]
    with pytest.raises(AnalysisError, match="differs from grid"):
        keys(grid("2026-03-08", "2026-03-09"), key)
    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-08-02T12:00:00+00:00"),
        end=datetime.fromisoformat("2026-08-03T12:00:00+00:00"),
    )
    with pytest.raises(AnalysisError, match="whole-day"):
        holiday_then_weekday(current=current, baseline=baseline)
    weekly = bind_grid(absolute, mv.grain("week"), report_timezone="UTC")
    with pytest.raises(AnalysisError, match="single-day"):
        keys(weekly, weekday())


def test_holiday_descriptors_keep_bounded_identity_and_show(
    capsys: pytest.CaptureFixture[str],
) -> None:
    long_key = "occurrence" * 2000
    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.holidays"),
        boundary_timezone="UTC",
        coverage=(date(2026, 1, 1), date(2027, 1, 1)),
        rows=({"id": long_key, "start": date(2026, 8, 1), "end": date(2026, 8, 4)},),
        occurrence_id="id",
        start="start",
        end="end",
    )
    scope = snapshot.occurrence_scope(long_key)
    for factory in (holiday, holiday_then_weekday):
        descriptor = factory(current=scope, baseline=scope)
        assert len(repr(descriptor)) < 100 and "\n" not in repr(descriptor)
        assert "capture=" in repr(descriptor)
        descriptor.show()
        text = capsys.readouterr().out
        assert len(text.encode()) <= 8193
        assert ".current.show()" in text and ".baseline.show()" in text
        descriptor.show(max_output_bytes=None)
        assert long_key in capsys.readouterr().out


def test_catalog_occurrence_round_trip_supplies_alignment_capture(
    tmp_path: Path, semantic_project_factory: Callable[[dict[str, str]], SemanticProject]
) -> None:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    catalog.preview(
        ms.ref.temporal_set("sales.named_campaigns"), scope=default_scope(max_rows=2), limit=1
    )
    campaigns = catalog.temporal_sets.get("sales.named_campaigns")
    current, baseline = campaigns.occurrence("spring"), campaigns.occurrence("incident")
    mapping = _freeze_time(holiday(current=current, baseline=baseline))
    assert isinstance(mapping, HolidayTimeMapping)
    assert mapping.current.snapshot_digest == current.snapshot_digest
    assert keys(grid("2026-03-01", "2026-03-04"), holiday(current=current, baseline=baseline)) == [
        "holiday:0",
        "holiday:1",
        "holiday:2",
    ]


def test_timestamp_capture_round_trip_keeps_dst_civil_dates() -> None:
    from pydantic import TypeAdapter

    from marivo.analysis.core.time_alignment import TimeKeyMapping

    current, baseline = occurrences(
        start=datetime.fromisoformat("2026-03-08T05:00:00+00:00"),
        end=datetime.fromisoformat("2026-03-09T04:00:00+00:00"),
        zone="America/New_York",
    )
    original = _freeze_time(holiday(current=current, baseline=baseline))
    adapter: TypeAdapter[TimeKeyMapping] = TypeAdapter(TimeKeyMapping)
    restored = adapter.validate_json(adapter.dump_json(original))
    assert repr(restored) == repr(original)
    assert isinstance(restored, HolidayTimeMapping)
    assert restored.current.dates == (date(2026, 3, 8), date(2026, 3, 9))
    assert isinstance(restored.current.start, datetime)
    assert isinstance(restored.current.end, datetime)
    assert restored.current.end - restored.current.start == timedelta(hours=23)
    assert evaluate_time_keys(
        grid("2026-03-08", "2026-03-09", "America/New_York"), restored, "current"
    ) == evaluate_time_keys(
        grid("2026-03-08", "2026-03-09", "America/New_York"), original, "current"
    )


@pytest.mark.parametrize("lengths", [(3, 2), (2, 3)])
def test_public_ordinal_retains_original_positions_after_selection(
    analysis_dsl_case_factory: DslCaseFactory,
    lengths: tuple[int, int],
) -> None:
    case = analysis_dsl_case_factory("j2")
    _, current = period(case.session, 8, lengths[0])
    _, baseline = period(case.session, 7, lengths[1])
    selected = current.where(current["value"] > 0)
    comparison = selected.compare(baseline, match=mv.ordinal(), how="outer")
    result = comparison.execute()
    frame = result.to_pandas()
    assert frame[("presence", ("current",))].sum() == 1
    assert frame[("presence", ("baseline",))].sum() == lengths[1] * 4
    assert frame[("value", "value.absolute")].dropna().tolist() == [1]
    projection = next(
        p for p in frame.attrs["marivo"]["axes"][0]["projections"] if p["path"] == ("current",)
    )
    assert [row[0] for row in projection["values"] if row[0] is not None] == [0]
    pd.testing.assert_frame_equal(case.session.artifact(result.id).to_pandas(), frame)


@pytest.mark.parametrize("kind", ["ordinal", "period_offset", "previous_year"])
def test_public_period_correspondence_preserves_independent_source_oracle(
    analysis_dsl_case_factory: DslCaseFactory,
    kind: str,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute('DELETE FROM "order"')
        connection.executemany(
            'INSERT INTO "order" VALUES (?, ?, ?, ?, ?, ?)',
            [
                ("a", "A", "web", "paid", "2026-08-01T12:00:00Z", 30),
                ("b", "B", "web", "paid", "2026-08-02T12:00:00Z", 15),
                ("c", "A", "web", "paid", "2025-08-01T12:00:00Z", 10),
            ],
        )
    metric = session.catalog.metrics.get("sales.revenue")
    members = session.members(ms.ref.entity("sales.customer"))
    current_axis = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
    if kind == "previous_year":
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.previous_year()
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    elif kind == "period_offset":
        baseline_axis = session.time_scope(start="2026-07-31", end="2026-08-02").by(mv.grain("day"))
        match = mv.period_offset(-1)
        expected = [30, 0, 0, 0, -30, 15, 0, 0]
    else:
        baseline_axis = session.time_scope(start="2025-08-01", end="2025-08-03").by(mv.grain("day"))
        match = mv.ordinal()
        expected = [20, 0, 0, 0, 0, 15, 0, 0]
    current = session.observe({"value": metric}, during=current_axis, by=members)
    baseline = session.observe({"value": metric}, during=baseline_axis, by=members)
    result = current.compare(baseline, match=match).execute()
    frame = result.to_pandas()
    assert len(frame) == 8 and frame["presence"].all().all()
    assert frame[("value", "value.absolute")].tolist() == expected
    assert frame.attrs["marivo"]["axes"][0]["kind"] == "correspondence"
    assert {p["path"] for p in frame.attrs["marivo"]["axes"][0]["projections"]} == {
        ("current",),
        ("baseline",),
    }


def test_previous_year_leap_day_is_missing_and_current_projection_is_exact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    metric = session.catalog.metrics.get("sales.revenue")
    current_axis = session.time_scope(start="2024-02-28", end="2024-03-01").by(mv.grain("day"))
    baseline_axis = session.time_scope(start="2023-02-28", end="2023-03-01").by(mv.grain("day"))
    current = session.observe({"value": metric}, during=current_axis)
    baseline = session.observe({"value": metric}, during=baseline_axis)
    with pytest.raises(AnalysisError, match="complete"):
        current.compare(baseline, match=mv.previous_year()).execute()
    result = current.compare(
        baseline, match=mv.previous_year(), how="outer", time_axis="current"
    ).execute()
    frame = result.to_pandas()
    assert frame.iloc[:, 0].tolist() == [
        datetime(2024, 2, 28, tzinfo=timezone.utc),
        datetime(2024, 2, 29, tzinfo=timezone.utc),
    ]
    assert frame[("presence", ("current",))].tolist() == [True, True]
    assert frame[("presence", ("baseline",))].tolist() == [True, False]
    assert frame[("value", "value.absolute")].iloc[0] == 0
    assert pd.isna(frame[("value", "value.absolute")].iloc[1])


@pytest.mark.parametrize("kind", ["holiday", "holiday_then_weekday"])
def test_timestamp_occurrence_capture_is_lossless_and_identity_sensitive(kind: KeyKind) -> None:
    key = time_key(kind, timestamp=True)
    mapping = _freeze_time(key)
    adapter: TypeAdapter[TimeKeyMapping] = TypeAdapter(TimeKeyMapping)
    restored = adapter.validate_json(adapter.dump_json(mapping), strict=True)
    assert isinstance(restored, (HolidayTimeMapping, HolidayThenWeekdayTimeMapping))
    assert isinstance(mapping, (HolidayTimeMapping, HolidayThenWeekdayTimeMapping))
    assert restored == mapping
    assert (
        isinstance(restored, (HolidayTimeMapping,))
        if kind == "holiday"
        else restored.kind == "holiday_then_weekday"
    )
    assert restored.current.snapshot_digest == mapping.current.snapshot_digest
    assert replace(restored.current, key="another") != restored.current
    assert replace(restored.current, snapshot_digest="another-snapshot") != restored.current
    with pytest.raises(AnalysisError):
        replace(restored.current, start=datetime(2026, 8, 2, 12, tzinfo=timezone.utc))
