from __future__ import annotations

import json
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.calendar.align import _json_key, _local_dates, align_calendar_frames
from marivo.analysis.calendar.model import Calendar, CalendarEntry, CalendarPolicy
from marivo.analysis.errors import (
    AlignmentFailedError,
    CalendarPolicyError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents.compare import compare
from marivo.analysis.lineage import Lineage
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import CalendarRef
from marivo.analysis.session._runtime import persist_frame
from tests.shared_fixtures import make_metric_frame


@pytest.fixture
def calendar_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    session_attach._reset_process_state()
    calendar_dir = tmp_path / ".marivo" / "calendar"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "cn_holidays.json").write_text(
        json.dumps(
            {
                "name": "cn_holidays",
                "holidays": [],
                "adjusted_workdays": [],
            }
        ),
        encoding="utf-8",
    )
    yield tmp_path
    session_attach._reset_process_state()


def _metric(session, rows, semantic_kind="time_series"):
    return make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        measure={"name": "value"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def _metric_frame(
    session,
    rows,
    *,
    axes,
    measure,
    semantic_kind="time_series",
):
    return make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes=axes,
        measure=measure,
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def _now():
    return datetime(2026, 5, 29, 10, 0, 0, tzinfo=UTC)


def _component_time_series_metric(session, *, ref, rows, component_rows):
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    }
    metric = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.failure_rate",
        axes=axes,
        measure={"name": "failure_rate"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )
    metric.meta = metric.meta.model_copy(
        update={
            "ref": ref,
            "composition": {
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        }
    )
    metric.meta = persist_frame(session, metric)
    component = ComponentFrame(
        _df=pd.DataFrame(component_rows),
        meta=ComponentFrameMeta(
            ref=f"{ref}_components",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(component_rows),
            byte_size=0,
            lineage=Lineage(),
            parent_ref=metric.ref,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            composition_kind="ratio",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes=axes,
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    metric.meta = metric.meta.model_copy(update={"component_ref": component.ref})
    metric.meta = persist_frame(session, metric)
    return metric


def _calendar() -> Calendar:
    return Calendar(
        name="cn_holidays",
        holidays=[
            CalendarEntry(date="2025-05-01", holiday_id="labor-day"),
            CalendarEntry(date="2026-05-01", holiday_id="labor-day"),
            CalendarEntry(date="2026-04-30", holiday_id="labor-day"),
        ],
        adjusted_workdays=[
            CalendarEntry(date="2026-05-02"),
        ],
    )


def test_calendar_helper_returns_expected_calendar():
    calendar = _calendar()
    assert calendar.name == "cn_holidays"
    assert len(calendar.holidays) == 3
    assert calendar.holidays[1].date == "2026-05-01"
    assert calendar.holidays[1].holiday_id == "labor-day"
    assert [entry.date for entry in calendar.adjusted_workdays] == ["2026-05-02"]


def test_dow_aligned_month_pair_uses_isoweekday_and_week_offset():
    a = pd.DataFrame({"bucket_start": ["2026-05-05"], "value": [100.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-07"], "value": [80.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 1
    row = aligned.iloc[0]
    assert row["current"] == pytest.approx(100.0)
    assert row["baseline"] == pytest.approx(80.0)
    assert row["delta"] == pytest.approx(20.0)
    assert row["pct_change"] == pytest.approx(0.25)
    assert row["align_key"] == '{"kind":"dow","iso_weekday":2,"period_week_offset":0}'
    assert row["align_quality"] == "exact"
    assert row["bucket_start_a"] == "2026-05-05"
    assert row["bucket_start_b"] == "2026-04-07"
    assert info.matched_rows == 1


def test_public_align_key_rejects_unknown_key_kind():
    with pytest.raises(CalendarPolicyError) as exc_info:
        _json_key(("mystery", 1))

    assert exc_info.value.details == {
        "kind": "CalendarAlignKeyInvalid",
        "align_key_kind": "mystery",
    }


def test_local_dates_handles_tz_aware_naive_string_and_python_date_values():
    tz_aware = pd.Series(pd.to_datetime(["2026-05-01T23:30:00+00:00"]))
    assert _local_dates(tz_aware, session_tz="Asia/Shanghai").tolist() == [date(2026, 5, 2)]

    naive = pd.Series(pd.to_datetime(["2026-05-01T23:30:00"]))
    assert _local_dates(naive, session_tz="Asia/Shanghai").tolist() == [date(2026, 5, 1)]

    mixed = pd.Series(["2026-05-01", date(2026, 5, 2)])
    assert _local_dates(mixed, session_tz="Asia/Shanghai").tolist() == [
        date(2026, 5, 1),
        date(2026, 5, 2),
    ]


def test_workday_aligned_respects_holiday_and_adjusted_workday():
    a = pd.DataFrame({"bucket_start": ["2026-05-02"], "value": [100.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-01"], "value": [80.0]})
    policy = CalendarPolicy(mode="workday_aligned", align_period="month")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 1
    assert aligned.iloc[0]["align_key"] == '{"kind":"workday","workday_ordinal":1}'
    assert aligned.iloc[0]["align_quality"] == "exact"
    assert info.matched_rows == 1
    assert info.dropped_rows_a == 0
    assert info.dropped_rows_b == 0


def test_holiday_aligned_non_holiday_rows_do_not_exact_match_by_date():
    a = pd.DataFrame({"bucket_start": ["2026-05-02", "2026-05-01"], "value": [100.0, 10.0]})
    b = pd.DataFrame({"bucket_start": ["2025-05-02", "2025-05-01"], "value": [80.0, 8.0]})
    policy = CalendarPolicy(mode="holiday_aligned", align_period="month", fallback="drop")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 1
    assert (
        aligned.iloc[0]["align_key"]
        == '{"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}'
    )
    assert aligned.iloc[0]["align_quality"] == "exact"
    assert info.matched_rows == 1
    assert info.fallback_rows == 0
    assert info.dropped_rows_a == 1
    assert info.dropped_rows_b == 1


def test_holiday_and_dow_aligned_uses_holiday_for_holidays_and_dow_for_others():
    a = pd.DataFrame({"bucket_start": ["2026-05-01", "2026-05-05"], "value": [100.0, 10.0]})
    b = pd.DataFrame({"bucket_start": ["2025-05-01", "2025-05-06"], "value": [80.0, 8.0]})
    policy = CalendarPolicy(mode="holiday_and_dow_aligned", align_period="month", fallback="drop")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 2
    assert set(aligned["align_key"].tolist()) == {
        '{"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}',
        '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
    }
    assert set(aligned["align_quality"].tolist()) == {"exact"}
    assert info.matched_rows == 2
    assert info.dropped_rows_a == 0
    assert info.dropped_rows_b == 0


def test_multi_day_holiday_shares_one_id_and_aligns_by_derived_ordinal():
    days = [f"2026-05-0{day}" for day in range(1, 6)] + [f"2025-05-0{day}" for day in range(1, 6)]
    calendar = Calendar(
        name="cn_holidays",
        holidays=[CalendarEntry(date=day, holiday_id="wy") for day in days],
    )
    a = pd.DataFrame(
        {
            "bucket_start": [f"2026-05-0{day}" for day in range(1, 6)],
            "value": [100.0, 101.0, 102.0, 103.0, 104.0],
        }
    )
    b = pd.DataFrame(
        {
            "bucket_start": [f"2025-05-0{day}" for day in range(1, 6)],
            "value": [80.0, 81.0, 82.0, 83.0, 84.0],
        }
    )
    policy = CalendarPolicy(mode="holiday_aligned", align_period="month")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=calendar,
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert info.matched_rows == 5
    assert set(aligned["align_key"].tolist()) == {
        '{"kind":"holiday","holiday_id":"wy","holiday_ordinal":1}',
        '{"kind":"holiday","holiday_id":"wy","holiday_ordinal":2}',
        '{"kind":"holiday","holiday_id":"wy","holiday_ordinal":3}',
        '{"kind":"holiday","holiday_id":"wy","holiday_ordinal":4}',
        '{"kind":"holiday","holiday_id":"wy","holiday_ordinal":5}',
    }
    assert set(aligned["align_quality"].tolist()) == {"exact"}
    assert info.dropped_rows_a == 0
    assert info.dropped_rows_b == 0


def test_nearest_prior_workday_fallback_marks_quality_and_counts_rows():
    a = pd.DataFrame(
        {
            "bucket_start": ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"],
            "value": [100.0, 70.0, 30.0, 40.0],
        }
    )
    b = pd.DataFrame(
        {
            "bucket_start": ["2026-04-30", "2026-04-03", "2026-04-02", "2026-04-04"],
            "value": [80.0, 10.0, 0.0, 50.0],
        }
    )
    policy = CalendarPolicy(
        mode="holiday_aligned", align_period="month", fallback="nearest_prior_workday"
    )
    calendar = Calendar(
        name="cn_holidays",
        holidays=[
            CalendarEntry(date="2025-05-01", holiday_id="labor-day"),
            CalendarEntry(date="2026-05-01", holiday_id="labor-day"),
            CalendarEntry(date="2026-04-30", holiday_id="labor-day"),
            CalendarEntry(date="2026-05-02", holiday_id="other-day"),
        ],
    )

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=calendar,
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 4
    assert info.matched_rows == 4
    assert info.fallback_rows == 3
    assert info.dropped_rows_a == 0
    assert info.dropped_rows_b == 1

    exact = aligned[aligned["align_quality"] == "exact"]
    assert len(exact) == 1
    assert exact.iloc[0]["bucket_start_a"] == "2026-05-01"
    assert exact.iloc[0]["bucket_start_b"] == "2026-04-30"

    fallback = aligned[aligned["align_quality"] == "fallback"]
    assert len(fallback) == 3
    assert set(fallback["bucket_start_b"].tolist()) == {"2026-04-02", "2026-04-03"}
    assert set(fallback["align_key"].tolist()) == {
        '{"kind":"fallback_workday","baseline_date":"2026-04-02"}',
        '{"kind":"fallback_workday","baseline_date":"2026-04-03"}',
    }


def test_pct_change_marks_from_zero_growth():
    a = pd.DataFrame({"bucket_start": ["2026-05-05"], "value": [100.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-07"], "value": [0.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    aligned, _info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 1
    assert aligned.iloc[0]["pct_change"] == np.inf
    assert aligned.iloc[0]["pct_change_status"] == "from_zero_growth"


def test_pct_change_uses_absolute_negative_baseline_for_calendar_alignment():
    a = pd.DataFrame({"bucket_start": ["2026-05-05"], "value": [-50.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-07"], "value": [-100.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    aligned, _info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert len(aligned) == 1
    assert aligned.iloc[0]["delta"] == pytest.approx(50.0)
    assert aligned.iloc[0]["pct_change"] == pytest.approx(0.5)
    assert aligned.iloc[0]["pct_change_status"] == "computed"


def test_dow_aligned_multi_period_pairs_periods_by_ordinal():
    a = pd.DataFrame({"bucket_start": ["2026-05-05", "2026-06-02"], "value": [100.0, 110.0]})
    b = pd.DataFrame({"bucket_start": ["2025-05-06", "2025-06-03"], "value": [80.0, 90.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert info.matched_rows == 2
    assert info.dropped_rows_a == 0
    assert info.dropped_rows_b == 0
    assert list(aligned["bucket_start_a"]) == ["2026-05-05", "2026-06-02"]
    assert list(aligned["bucket_start_b"]) == ["2025-05-06", "2025-06-03"]
    assert list(aligned["align_key"]) == [
        '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
        '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
    ]


def test_holiday_aligned_multi_period_derives_ordinals_per_period():
    calendar = Calendar(
        name="cn_holidays",
        holidays=[
            CalendarEntry(date="2025-05-01", holiday_id="promo"),
            CalendarEntry(date="2025-05-02", holiday_id="promo"),
            CalendarEntry(date="2025-06-01", holiday_id="promo"),
            CalendarEntry(date="2026-05-01", holiday_id="promo"),
            CalendarEntry(date="2026-05-02", holiday_id="promo"),
            CalendarEntry(date="2026-06-01", holiday_id="promo"),
        ],
    )
    a = pd.DataFrame({"bucket_start": ["2026-05-02", "2026-06-01"], "value": [100.0, 110.0]})
    b = pd.DataFrame({"bucket_start": ["2025-05-02", "2025-06-01"], "value": [80.0, 90.0]})
    policy = CalendarPolicy(mode="holiday_aligned", align_period="month")

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=calendar,
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert info.matched_rows == 2
    assert list(aligned["bucket_start_a"]) == ["2026-05-02", "2026-06-01"]
    assert list(aligned["bucket_start_b"]) == ["2025-05-02", "2025-06-01"]
    assert list(aligned["align_key"]) == [
        '{"kind":"holiday","holiday_id":"promo","holiday_ordinal":2}',
        '{"kind":"holiday","holiday_id":"promo","holiday_ordinal":1}',
    ]


def test_nearest_prior_workday_fallback_uses_matching_period_pair():
    a = pd.DataFrame({"bucket_start": ["2026-05-04", "2026-06-04"], "value": [100.0, 110.0]})
    b = pd.DataFrame({"bucket_start": ["2025-05-02", "2025-06-03"], "value": [80.0, 90.0]})
    policy = CalendarPolicy(
        mode="holiday_aligned", align_period="month", fallback="nearest_prior_workday"
    )

    aligned, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=policy,
        session_tz="Asia/Shanghai",
    )

    assert info.matched_rows == 2
    assert info.fallback_rows == 2
    assert list(aligned["bucket_start_a"]) == ["2026-05-04", "2026-06-04"]
    assert list(aligned["bucket_start_b"]) == ["2025-05-02", "2025-06-03"]
    assert list(aligned["align_key"]) == [
        '{"kind":"fallback_workday","baseline_date":"2025-05-02"}',
        '{"kind":"fallback_workday","baseline_date":"2025-06-03"}',
    ]


def test_rejects_mismatched_multi_period_counts():
    a = pd.DataFrame({"bucket_start": ["2026-05-01", "2026-06-01"], "value": [100.0, 110.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-01"], "value": [80.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    with pytest.raises(AlignmentFailedError) as exc_info:
        align_calendar_frames(
            a,
            b,
            time_column="bucket_start",
            value_column="value",
            calendar=_calendar(),
            policy=policy,
            session_tz="Asia/Shanghai",
        )

    assert exc_info.value.details["kind"] == "CalendarAlignPeriodPairMismatch"
    assert exc_info.value.details["current_period_ids"] == ["2026-05", "2026-06"]
    assert exc_info.value.details["baseline_period_ids"] == ["2026-04"]


def test_rejects_duplicate_calendar_keys():
    a = pd.DataFrame({"bucket_start": ["2026-05-05", "2026-05-05"], "value": [100.0, 101.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-07"], "value": [80.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="month")

    with pytest.raises(AlignmentFailedError) as exc_info:
        align_calendar_frames(
            a,
            b,
            time_column="bucket_start",
            value_column="value",
            calendar=_calendar(),
            policy=policy,
            session_tz="Asia/Shanghai",
        )

    assert exc_info.value.details["kind"] == "CalendarAlignKeyNotUnique"


def test_rejects_align_period_day():
    a = pd.DataFrame({"bucket_start": ["2026-05-05"], "value": [100.0]})
    b = pd.DataFrame({"bucket_start": ["2026-04-07"], "value": [80.0]})
    policy = CalendarPolicy(mode="dow_aligned", align_period="day")

    with pytest.raises(CalendarPolicyError) as exc_info:
        align_calendar_frames(
            a,
            b,
            time_column="bucket_start",
            value_column="value",
            calendar=_calendar(),
            policy=policy,
            session_tz="Asia/Shanghai",
        )

    assert exc_info.value.details["kind"] == "CalendarPolicyInvalid"


def test_compare_rejects_loose_calendar_alignment_args(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    with pytest.raises(TypeError):
        compare(current, baseline, align="calendar", session=s)  # type: ignore[call-arg]


def test_compare_calendar_rejects_scalar(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"value": 100.0}], semantic_kind="scalar")
    baseline = _metric(s, [{"value": 80.0}], semantic_kind="scalar")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarAlignRequiresTimeSeries"


def test_compare_calendar_returns_delta_frame(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    compare_jobs = [job for job in s.jobs() if job.intent == "compare"]
    assert len(compare_jobs) == 1
    job_record = s.job(compare_jobs[0].id)
    assert job_record["params"]["alignment"]["kind"] == "dow_aligned"
    assert job_record["params"]["alignment"]["calendar"] == {"id": "cn_holidays"}
    assert job_record["params"]["alignment"]["calendar_info"]["calendar_name"] == "cn_holidays"

    assert isinstance(out, DeltaFrame)
    assert out.meta.alignment["kind"] == "dow_aligned"
    assert out.meta.alignment["calendar_info"]["calendar_name"] == "cn_holidays"
    df = out.to_pandas()
    assert list(df["current"]) == [100.0]
    assert list(df["baseline"]) == [80.0]
    assert list(df["align_quality"]) == ["exact"]


def test_compare_holiday_and_dow_alignment_policy(calendar_project):
    calendar_path = calendar_project / ".marivo" / "calendar" / "cn_holidays.json"
    calendar_path.write_text(_calendar().model_dump_json(), encoding="utf-8")
    s = session_attach.get_or_create(name="demo")
    current = _metric(
        s,
        [
            {"bucket_start": "2026-05-01", "value": 100.0},
            {"bucket_start": "2026-05-05", "value": 10.0},
        ],
    )
    baseline = _metric(
        s,
        [
            {"bucket_start": "2025-05-01", "value": 80.0},
            {"bucket_start": "2025-05-06", "value": 8.0},
        ],
    )

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="holiday_and_dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    df = out.to_pandas()
    assert set(df["align_quality"]) == {"exact"}
    assert set(df["align_key"]) == {
        '{"kind":"holiday","holiday_id":"labor-day","holiday_ordinal":1}',
        '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
    }
    assert out.meta.alignment["kind"] == "holiday_and_dow_aligned"
    assert out.meta.alignment["calendar_info"]["mode"] == "holiday_and_dow_aligned"


def test_compare_calendar_uses_calendar_ref_without_session_default(calendar_project):
    s = session_attach.get_or_create(name="demo")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    assert out.meta.alignment["calendar"]["id"] == "cn_holidays"


def test_compare_calendar_rejects_missing_calendar_ref(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])
    alignment = AlignmentPolicy.model_construct(
        kind="dow_aligned",
        calendar=None,
        period="month",
        fallback="drop",
    )
    before_jobs = len(s.jobs())
    before_frames = len(s.frame_summaries())

    with pytest.raises(CalendarPolicyError) as exc_info:
        compare(
            current,
            baseline,
            alignment=alignment,
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarRefMissing"
    assert len(s.jobs()) == before_jobs
    assert len(s.frame_summaries()) == before_frames


def test_compare_calendar_wraps_policy_validation_error(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    with pytest.raises(CalendarPolicyError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="day",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarPolicyInvalid"


def test_compare_calendar_rejects_missing_time_axis(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric_frame(
        s,
        [{"bucket_start": "2026-05-05", "value": 100.0}],
        axes={},
        measure={"name": "value"},
    )
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "NoTimeAxis"


def test_compare_calendar_rejects_missing_required_columns_on_baseline(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric(s, [{"bucket_start": "2026-05-05", "value": 100.0}])
    baseline = _metric_frame(
        s,
        [{"other_time": "2026-04-07", "other_value": 80.0}],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        measure={"name": "value"},
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarAlignColumnMissing"
    assert exc_info.value.details["frame"] == "baseline"
    assert set(exc_info.value.details["missing_columns"]) == {"bucket_start", "value"}


def test_compare_calendar_rejects_ambiguous_value_column(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric_frame(
        s,
        [{"bucket_start": "2026-05-05", "v1": 100.0, "v2": 120.0}],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        measure={"name": "value"},
    )
    baseline = _metric_frame(
        s,
        [{"bucket_start": "2026-04-07", "v1": 80.0, "v2": 90.0}],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        measure={"name": "value"},
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarAlignValueColumnAmbiguous"


def test_compare_calendar_rejects_missing_value_column(calendar_project):
    s = session_attach.get_or_create(name="demo", default_calendar="cn_holidays")
    current = _metric_frame(
        s,
        [{"bucket_start": "2026-05-05"}],
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        measure={"name": "value"},
    )
    baseline = _metric(s, [{"bucket_start": "2026-04-07", "value": 80.0}])

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            current,
            baseline,
            alignment=AlignmentPolicy(
                kind="dow_aligned",
                calendar=CalendarRef("cn_holidays"),
                period="month",
            ),
            session=s,
        )

    assert exc_info.value.details["kind"] == "CalendarAlignValueColumnMissing"


def test_compare_calendar_time_series_ratio_persists_component_delta(calendar_project):
    calendar_path = calendar_project / ".marivo" / "calendar" / "cn_holidays.json"
    calendar_path.write_text(_calendar().model_dump_json(), encoding="utf-8")
    s = session_attach.get_or_create(name="demo")
    current = _component_time_series_metric(
        s,
        ref="frame_current_ratio",
        rows=[{"bucket_start": "2026-05-05", "failure_rate": 0.25}],
        component_rows=[
            {
                "bucket_start": "2026-05-05",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            }
        ],
    )
    baseline = _component_time_series_metric(
        s,
        ref="frame_baseline_ratio",
        rows=[{"bucket_start": "2026-04-07", "failure_rate": 0.10}],
        component_rows=[
            {
                "bucket_start": "2026-04-07",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            }
        ],
    )

    out = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(
            kind="dow_aligned",
            calendar=CalendarRef("cn_holidays"),
            period="month",
        ),
        session=s,
    )

    component_df = out.components().to_pandas()
    assert list(component_df["align_quality"]) == ["exact"]
    assert component_df.iloc[0]["bucket_start_a"] == "2026-05-05"
    assert component_df.iloc[0]["bucket_start_b"] == "2026-04-07"
    assert component_df.iloc[0]["current_failed_count"] == pytest.approx(25.0)
    assert component_df.iloc[0]["baseline_failed_count"] == pytest.approx(10.0)
    assert component_df.iloc[0]["delta_failure_rate"] == pytest.approx(0.15)


def test_align_calendar_info_has_no_calendar_timezone():
    a = pd.DataFrame({"bucket_start": ["2026-05-01"], "value": [10]})
    b = pd.DataFrame({"bucket_start": ["2025-05-01"], "value": [7]})
    result, info = align_calendar_frames(
        a,
        b,
        time_column="bucket_start",
        value_column="value",
        calendar=_calendar(),
        policy=CalendarPolicy(mode="holiday_aligned", align_period="month"),
        session_tz="Asia/Shanghai",
    )

    assert len(result) == 1
    assert "calendar_timezone" not in info.model_dump()
    assert info.session_timezone == "Asia/Shanghai"
