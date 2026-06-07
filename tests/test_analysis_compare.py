"""session.compare against two MetricFrames."""

import ibis
import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyValidationError,
    ComponentFrameUnavailableError,
    SemanticKindMismatchError,
    SessionStateError,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import MetricRef
from marivo.analysis.session.persistence import read_frame_from_disk
from tests.conftest import bootstrap_sales_project


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _seed(con):
    con.raw_sql("CREATE TABLE orders (order_id INTEGER, created_at DATE, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0),"
        "(2, DATE '2026-07-02', 20.0),"
        "(3, DATE '2026-04-01', 5.0),"
        "(4, DATE '2026-04-02', 15.0)"
    )


def test_compare_returns_delta_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, alignment=AlignmentPolicy(kind="window_bucket"), session=s)
    assert isinstance(d, DeltaFrame)
    assert d.meta.alignment["kind"] == "window_bucket"
    assert d.meta.source_current_ref == q3.ref
    assert d.meta.source_baseline_ref == q2.ref
    df = d.to_pandas()
    assert set(df.columns) >= {"current", "baseline", "delta", "pct_change"}
    assert df.iloc[0]["current"] == pytest.approx(30.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_default_bucket_handles_scalar_window_outputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.to_pandas().iloc[0]["delta"] == pytest.approx(10.0)


def test_compare_rejects_delta_frame_as_second_argument(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    delta = compare(q3, q2, session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(q3, delta, session=s)  # type: ignore[arg-type]

    rendered = str(exc_info.value)
    assert (
        "SemanticKindMismatchError: compare(current, baseline) expected MetricFrame for `baseline`, got DeltaFrame."
        in rendered
    )
    assert "Fix:" in rendered
    assert (
        'delta = session.compare(cur, base, alignment=mv.AlignmentPolicy(kind="window_bucket"))'
        in rendered
    )
    assert exc_info.value.details["expected_kind"] == "metric_frame"
    assert exc_info.value.details["got_kind"] == "delta_frame"


def test_compare_semantic_kind_mismatch_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        grain="day",
        session=s,
    )
    with pytest.raises(SemanticKindMismatchError):
        compare(a, b, session=s)


def test_compare_rejects_non_alignment_policy(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(a, b, alignment="window_bucket", session=s)  # type: ignore[arg-type]

    assert exc_info.value.details["expected_kind"] == "AlignmentPolicy"
    assert exc_info.value.details["got_kind"] == "str"


def test_compare_rejects_loose_align_parameter(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )

    with pytest.raises(TypeError):
        compare(q3, q2, align="sample", session=s)  # type: ignore[call-arg]


def test_window_bucket_aligns_equal_length_time_series_by_ordinal_bucket(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        session=s,
    )
    base = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-03"},
        grain="day",
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert len(df) == 2
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-04-01", "2026-04-02"]
    assert list(df["delta"]) == [pytest.approx(5.0), pytest.approx(5.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"
    assert delta.meta.alignment["strict_lengths"] is False


def test_window_bucket_ordinal_rejects_time_series_grain_mismatch(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-04-01", "2026-04-02"], "revenue": [5.0, 15.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "hour"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    assert exc_info.value.details["kind"] == "WindowBucketGrainMismatch"
    assert exc_info.value.details["current_grain"] == "day"
    assert exc_info.value.details["baseline_grain"] == "hour"


def test_window_bucket_no_overlap_different_expected_counts_uses_outer_ordinal_union(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        session=s,
    )
    base = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-02"},
        grain="day",
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert len(df) == 2
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert str(df.iloc[0]["bucket_start_b"]) == "2026-04-01"
    assert pd.isna(df.iloc[1]["bucket_start_b"])
    assert df.iloc[0]["delta"] == pytest.approx(5.0)
    assert pd.isna(df.iloc[1]["baseline"])
    assert pd.isna(df.iloc[1]["delta"])
    assert delta.meta.alignment["mode"] == "ordinal_bucket"
    assert delta.meta.alignment["coverage"]["paired_buckets"] == 1
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 1
    assert delta.meta.alignment["coverage"]["baseline_unpaired_buckets"] == 0


def test_window_bucket_strict_lengths_rejects_different_expected_counts(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-02"},
        grain="day",
        session=s,
    )
    base = observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-04-01", "end": "2026-04-01"},
        grain="day",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(
            cur,
            base,
            alignment=AlignmentPolicy(kind="window_bucket", strict_lengths=True),
            session=s,
        )

    assert "equal expected bucket counts" in str(exc_info.value)
    assert exc_info.value.details["kind"] == "WindowBucketExpectedCountMismatch"


def test_window_bucket_overlapping_windows_use_ordinal_mode_by_default(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-02", "2026-07-03"], "revenue": [7.0, 9.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-02", "end": "2026-07-04", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-07-02", "2026-07-03"]
    assert list(df["delta"]) == [pytest.approx(3.0), pytest.approx(11.0)]
    assert delta.meta.alignment["mode"] == "ordinal_bucket"


def test_window_bucket_calendar_mode_outer_joins_bucket_keys(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-03"], "revenue": [10.0, 30.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [8.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )

    delta = compare(
        cur,
        base,
        alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
        session=s,
    )

    df = delta.to_pandas()
    assert "bucket_start_b" not in df.columns
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert df.iloc[0]["delta"] == pytest.approx(2.0)
    assert pd.isna(df.iloc[1]["current"])
    assert pd.isna(df.iloc[2]["baseline"])
    assert delta.meta.alignment["mode"] == "calendar_bucket"


def test_window_bucket_february_to_march_daily_uses_outer_ordinal_union(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-02-01", periods=28, freq="D"),
                "revenue": [float(value) for value in range(1, 29)],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-02-01", "end": "2026-03-01", "grain": "day"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2026-03-01", periods=31, freq="D"),
                "revenue": [float(value) for value in range(101, 132)],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-03-01", "end": "2026-04-01", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert len(df) == 31
    assert str(df.iloc[0]["bucket_start"]).startswith("2026-02-01")
    assert str(df.iloc[0]["bucket_start_b"]).startswith("2026-03-01")
    assert pd.isna(df.iloc[28]["bucket_start"])
    assert str(df.iloc[28]["bucket_start_b"]).startswith("2026-03-29")
    assert delta.meta.alignment["coverage"]["paired_buckets"] == 28
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 0
    assert delta.meta.alignment["coverage"]["baseline_unpaired_buckets"] == 3


def test_window_bucket_leap_year_february_returns_rows_by_default(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2024-02-01", periods=29, freq="D"),
                "revenue": [1.0] * 29,
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2024-02-01", "end": "2024-03-01", "grain": "day"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.date_range("2025-02-01", periods=28, freq="D"),
                "revenue": [1.0] * 28,
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2024-02-01", "end": "2024-03-01", "grain": "day"},
        session=s,
    )
    base.meta = base.meta.model_copy(
        update={"window": {"start": "2025-02-01", "end": "2025-03-01", "grain": "day"}}
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert len(df) == 29
    assert pd.isna(df.iloc[28]["bucket_start_b"])
    assert delta.meta.alignment["coverage"]["current_unpaired_buckets"] == 1


def test_alignment_policy_window_bucket_defaults_dump_explicit_mode():
    policy = AlignmentPolicy(kind="window_bucket")

    assert policy.model_dump(mode="json") == {
        "kind": "window_bucket",
        "calendar": None,
        "period": "month",
        "fallback": "drop",
        "mode": "ordinal_bucket",
        "strict_lengths": False,
    }


def test_alignment_policy_calendar_backed_rejects_window_bucket_mode():
    with pytest.raises(AlignmentPolicyValidationError) as exc_info:
        AlignmentPolicy(
            kind="dow_aligned",
            calendar=None,
            mode="calendar_bucket",
        )

    assert exc_info.value.details["case"] == "window_bucket_mode_not_applicable"


def test_alignment_policy_calendar_backed_rejects_strict_lengths():
    with pytest.raises(AlignmentPolicyValidationError) as exc_info:
        AlignmentPolicy(
            kind="dow_aligned",
            calendar=None,
            strict_lengths=True,
        )

    assert exc_info.value.details["case"] == "window_bucket_strict_lengths_not_applicable"


def test_window_bucket_no_overlap_uses_window_spine_for_sparse_time_series(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"bucket_start": ["2026-04-01"], "revenue": [5.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-04-01", "end": "2026-04-03", "grain": "day"},
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-07-01", "2026-07-02"]
    assert list(df["bucket_start_b"].astype(str)) == ["2026-04-01", "2026-04-02"]
    assert df.iloc[0]["delta"] == pytest.approx(5.0)
    assert pd.isna(df.iloc[1]["baseline"])
    assert pd.isna(df.iloc[1]["delta"])
    assert delta.meta.alignment["coverage"]["baseline"]["missing_buckets"] == 1


@pytest.mark.parametrize(
    ("current", "baseline", "expected_pct", "expected_status"),
    [
        (10.0, 0.0, np.inf, "from_zero_growth"),
        (-5.0, 0.0, -np.inf, "from_zero_decline"),
        (0.0, 0.0, np.nan, "zero_baseline_no_change"),
        (-50.0, -100.0, 0.5, "computed"),
        (10.0, np.nan, np.nan, "not_computable"),
    ],
)
def test_compare_pct_change_status_handles_zero_missing_and_negative_baseline(
    tmp_path, current, baseline, expected_pct, expected_status
):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [current]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [baseline]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    delta = compare(cur, base, session=s)

    row = delta.to_pandas().iloc[0]
    if pd.notna(baseline):
        assert row["delta"] == pytest.approx(current - baseline)
    else:
        assert pd.isna(row["delta"])
    if pd.isna(expected_pct):
        assert pd.isna(row["pct_change"])
    else:
        assert row["pct_change"] == expected_pct
    assert row["pct_change_status"] == expected_status


def test_compare_scalar_rejects_multirow_inputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(name="demo")
    cur = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [10.0, 11.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame({"value": [8.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(cur, base, session=s)

    assert exc_info.value.details["kind"] == "ScalarCompareRequiresSingleRow"
    assert exc_info.value.details["current_rows"] == 2
    assert exc_info.value.details["baseline_rows"] == 1


def test_window_bucket_no_overlap_supports_quarter_grain(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": ["2026-04-01", "2026-07-01"],
                "revenue": [100.0, 200.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "quarter"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-04-01", "end": "2026-10-01", "grain": "quarter"},
        session=s,
    )
    base = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": ["2025-04-01", "2025-07-01"],
                "revenue": [80.0, 150.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "quarter"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2025-04-01", "end": "2025-10-01", "grain": "quarter"},
        session=s,
    )

    delta = compare(cur, base, alignment=AlignmentPolicy(kind="window_bucket"), session=s)

    df = delta.to_pandas()
    assert list(df["bucket_start"].astype(str)) == ["2026-04-01", "2026-07-01"]
    assert list(df["bucket_start_b"].astype(str)) == ["2025-04-01", "2025-07-01"]
    assert list(df["delta"]) == [pytest.approx(20.0), pytest.approx(50.0)]


def test_compare_persists_job_and_frame(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    d = compare(a, b, alignment=AlignmentPolicy(kind="window_bucket"), session=s)
    compare_jobs = [j for j in s.jobs() if j.intent == "compare"]
    assert len(compare_jobs) == 1
    assert compare_jobs[0].output_frame_ref == d.ref
    assert (s.layout.frames_dir / d.ref / "data.parquet").is_file()
    job_record = s.job(compare_jobs[0].id)
    assert job_record["params"]["alignment"]["kind"] == "window_bucket"


def test_compare_works_in_read_only_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_write = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s_write)
    b = observe(MetricRef("sales.revenue"), session=s_write)
    s_write.close()
    session_attach._reset_process_state()
    s_read = session_attach.get_or_create(name="demo", use_datasources=False)
    assert s_read.is_read_only
    df_a, meta_a = read_frame_from_disk(s_read.layout, a.ref)
    df_b, meta_b = read_frame_from_disk(s_read.layout, b.ref)
    d = compare(
        MetricFrame(_df=df_a, meta=MetricFrameMeta(**meta_a)),
        MetricFrame(_df=df_b, meta=MetricFrameMeta(**meta_b)),
        alignment=AlignmentPolicy(kind="window_bucket"),
        session=s_read,
    )
    assert isinstance(d, DeltaFrame)


def test_compare_archived_session_raises_for_cached_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    session_attach.archive("demo")
    with pytest.raises(SessionStateError):
        compare(a, b, session=s)


def test_compare_stale_archived_session_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(MetricRef("sales.revenue"), session=s)
    b = observe(MetricRef("sales.revenue"), session=s)
    session_attach._reset_process_state()
    session_attach.archive("demo")
    assert s.state == "active"
    with pytest.raises(SessionStateError):
        compare(a, b, session=s)


def test_compare_component_aware_scalar_missing_component_ref_fails_closed(tmp_path):
    s = session_attach.get_or_create(name="demo")
    current = MetricFrame.from_dataframe(
        pd.DataFrame({"failure_rate": [0.25]}),
        metric_id="sales.failure_rate",
        axes={},
        measure={"name": "failure_rate"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    baseline = MetricFrame.from_dataframe(
        pd.DataFrame({"failure_rate": [0.10]}),
        metric_id="sales.failure_rate",
        axes={},
        measure={"name": "failure_rate"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    current.meta = current.meta.model_copy(
        update={
            "decomposition": {
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            }
        }
    )

    with pytest.raises(ComponentFrameUnavailableError):
        compare(current, baseline, session=s)
