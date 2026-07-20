"""session.compare against two MetricFrames."""

import json

import ibis
import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyValidationError,
    AttributionAdditivityError,
    ComponentFrameUnavailableError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.observe import observe
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.session._layout import read_frame_from_disk
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import make_metric_frame


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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, alignment=AlignmentPolicy(kind="window_bucket"), session=s)
    assert isinstance(d, DeltaFrame)
    assert d.meta.alignment["kind"] == "window_bucket"
    assert d.meta.source_current_ref == q3.ref
    assert d.meta.source_baseline_ref == q2.ref
    assert d.meta.metric_identity == q3.meta.metric_identity
    assert d.meta.baseline_metric_identity == q2.meta.metric_identity
    assert d.meta.comparison_identity is not None
    assert d.meta.comparison_identity.current_artifact_id == q3.ref
    assert d.meta.comparison_identity.baseline_artifact_id == q2.ref
    df = d.to_pandas()
    assert set(df.columns) >= {"current", "baseline", "delta", "pct_change"}
    assert df.iloc[0]["current"] == pytest.approx(30.0)
    assert df.iloc[0]["baseline"] == pytest.approx(20.0)
    assert df.iloc[0]["delta"] == pytest.approx(10.0)

    reversed_delta = compare(
        q2,
        q3,
        alignment=AlignmentPolicy(kind="window_bucket"),
        session=s,
    )
    assert reversed_delta.ref != d.ref
    assert reversed_delta.meta.comparison_identity is not None
    assert reversed_delta.meta.comparison_identity.current_artifact_id == q2.ref
    assert reversed_delta.meta.comparison_identity.baseline_artifact_id == q3.ref
    assert reversed_delta.to_pandas().iloc[0]["delta"] == pytest.approx(-10.0)

    store = s._evidence_store()
    assert store is not None
    row = (
        store.read()
        .execute("SELECT subject_payload FROM artifacts WHERE artifact_id = ?", (d.ref,))
        .fetchone()
    )
    assert row is not None
    subject = json.loads(row["subject_payload"])["typed_metric_subject"]
    assert subject["kind"] == "delta_metric"
    assert subject["session_id"] == s.id
    assert subject["comparison"]["current_artifact_id"] == q3.ref
    assert subject["comparison"]["baseline_artifact_id"] == q2.ref


def test_compare_default_bucket_handles_scalar_window_outputs(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.to_pandas().iloc[0]["delta"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("baseline_additivity", "baseline_aggregation", "baseline_status_time_dimension"),
    [
        (None, "sum", None),
        ("additive", "mean", None),
        ("additive", "sum", "sales.orders.snapshot_at"),
    ],
)
def test_compare_fails_attribution_closed_when_metric_semantics_differ(
    tmp_path,
    baseline_additivity,
    baseline_aggregation,
    baseline_status_time_dimension,
):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    axes = {
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        }
    }
    current = make_metric_frame(
        pd.DataFrame({"region": ["NORTH"], "value": [30.0]}),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        additivity="additive",
        aggregation="sum",
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame({"region": ["NORTH"], "value": [20.0]}),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        additivity=baseline_additivity,
        aggregation=baseline_aggregation,
        status_time_dimension=baseline_status_time_dimension,
        session=session,
    )

    delta = compare(current, baseline, session=session)

    assert delta.meta.additivity is None
    assert delta.meta.aggregation is None
    assert delta.meta.status_time_dimension is None
    with pytest.raises(AttributionAdditivityError) as exc_info:
        session.attribute(
            delta,
            axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        )
    assert exc_info.value._context["reason"] == "missing_additivity_metadata"


def test_compare_rejects_delta_frame_as_second_argument(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-30"},
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
    assert "Repair:" in rendered
    assert "delta = session.compare(cur, base, alignment=mv.window_bucket())" in rendered
    assert exc_info.value._context["expected_kind"] == "metric_frame"
    assert exc_info.value._context["got_kind"] == "delta_frame"


def test_compare_semantic_kind_mismatch_raises(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
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
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        compare(a, b, alignment="window_bucket", session=s)  # type: ignore[arg-type]

    assert exc_info.value._context["expected_kind"] == "AlignmentPolicy"
    assert exc_info.value._context["got_kind"] == "str"


def test_compare_rejects_loose_align_parameter(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-30"},
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-03"},
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
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
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

    assert exc_info.value._context["kind"] == "WindowBucketGrainMismatch"
    assert exc_info.value._context["current_grain"] == "day"
    assert exc_info.value._context["baseline_grain"] == "hour"


def test_window_bucket_no_overlap_different_expected_counts_uses_outer_ordinal_union(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    cur = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-02"},
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-02"},
        grain="day",
        session=s,
    )
    base = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-01"},
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
    assert exc_info.value._context["kind"] == "WindowBucketExpectedCountMismatch"


def test_window_bucket_overlapping_windows_use_ordinal_mode_by_default(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
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
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-03"], "revenue": [10.0, 30.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
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
    cur = make_metric_frame(
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
    base = make_metric_frame(
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
    cur = make_metric_frame(
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
    base = make_metric_frame(
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

    assert exc_info.value._context["case"] == "window_bucket_mode_not_applicable"


def test_alignment_policy_calendar_backed_rejects_strict_lengths():
    with pytest.raises(AlignmentPolicyValidationError) as exc_info:
        AlignmentPolicy(
            kind="dow_aligned",
            calendar=None,
            strict_lengths=True,
        )

    assert exc_info.value._context["case"] == "window_bucket_strict_lengths_not_applicable"


def test_window_bucket_no_overlap_uses_window_spine_for_sparse_time_series(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
        pd.DataFrame({"bucket_start": ["2026-07-01", "2026-07-02"], "revenue": [10.0, 20.0]}),
        metric_id="sales.revenue",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "day"}},
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=s,
    )
    base = make_metric_frame(
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
        (10.0, 0.0, np.nan, "from_zero_growth"),
        (-5.0, 0.0, np.nan, "from_zero_decline"),
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
    cur = make_metric_frame(
        pd.DataFrame({"value": [current]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
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
    cur = make_metric_frame(
        pd.DataFrame({"value": [10.0, 11.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "value"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    base = make_metric_frame(
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

    assert exc_info.value._context["kind"] == "ScalarCompareRequiresSingleRow"
    assert exc_info.value._context["current_rows"] == 2
    assert exc_info.value._context["baseline_rows"] == 1


def test_window_bucket_no_overlap_supports_quarter_grain(tmp_path):
    bootstrap_sales_project(tmp_path)
    s = session_attach.get_or_create(
        name="demo", backends={"warehouse": lambda: ibis.duckdb.connect(":memory:")}
    )
    cur = make_metric_frame(
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
    base = make_metric_frame(
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
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    d = compare(a, b, alignment=AlignmentPolicy(kind="window_bucket"), session=s)
    compare_jobs = [j for j in s.jobs() if j.intent == "compare"]
    assert len(compare_jobs) == 1
    assert compare_jobs[0].output_frame_ref == d.ref
    assert (s._layout.frames_dir / d.ref / "data.parquet").is_file()
    job_record = s.job(compare_jobs[0].id)
    assert job_record["params"]["alignment"]["kind"] == "window_bucket"
    assert job_record["schema"] == "marivo.analysis_job/v1"
    assert job_record["subject"]["kind"] == "delta_metric"
    assert "semantic_model" not in job_record

    persisted_meta = json.loads((s._layout.frames_dir / d.ref / "meta.json").read_text())
    assert {"metric_id", "semantic_model", "status_time_dimension"}.isdisjoint(persisted_meta)
    assert persisted_meta["comparison_identity"]["current"]["metric_ref"]["path"] == (
        "sales.revenue"
    )
    assert persisted_meta["catalog_definition_fingerprint"]
    loaded = s.get_frame(d.ref)
    assert loaded.meta.metric_id == "sales.revenue"
    assert loaded.meta.semantic_model == "sales"


def test_compare_works_in_read_only_session(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s_write = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s_write)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s_write)
    s_write.close()
    session_attach._reset_process_state()
    s_read = session_attach.get_or_create(name="demo", use_datasources=False)
    assert s_read.is_read_only
    df_a, meta_a = read_frame_from_disk(s_read._layout, a.ref)
    df_b, meta_b = read_frame_from_disk(s_read._layout, b.ref)
    d = compare(
        MetricFrame(_df=df_a, meta=MetricFrameMeta(**meta_a)),
        MetricFrame(_df=df_b, meta=MetricFrameMeta(**meta_b)),
        alignment=AlignmentPolicy(kind="window_bucket"),
        session=s_read,
    )
    assert isinstance(d, DeltaFrame)


def test_compare_works_in_read_only_session_no_backend(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    # Re-open session without backend -> read-only, but compare still works.
    session_attach._reset_process_state()
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    d = compare(a, b, session=s_ro)
    assert isinstance(d, DeltaFrame)


def test_compare_works_after_archive_reopen(tmp_path):
    bootstrap_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    a = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    b = observe(make_ref("sales.revenue", SemanticKind.METRIC), session=s)
    session_attach._reset_process_state()
    # Re-open without backends; compare still works since it only needs
    # persisted frame data, not a live backend connection.
    s_ro = session_attach.get_or_create(name="demo", use_datasources=False)
    d = compare(a, b, session=s_ro)
    assert isinstance(d, DeltaFrame)


def test_compare_component_aware_scalar_missing_component_ref_fails_closed(tmp_path):
    s = session_attach.get_or_create(name="demo")
    current = make_metric_frame(
        pd.DataFrame({"failure_rate": [0.25]}),
        metric_id="sales.failure_rate",
        axes={},
        measure={"name": "failure_rate"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=s,
    )
    baseline = make_metric_frame(
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
            "composition": {
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


def _bootstrap_unit_sales_project(tmp_path) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.Ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', "
        "name='revenue',  unit='CNY')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def test_compare_propagates_metric_unit_to_delta_meta(tmp_path):
    _bootstrap_unit_sales_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    s = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    q3 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-31"},
        session=s,
    )
    assert q3.meta.unit == "CNY"
    q2 = observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-04-01", "end": "2026-04-30"},
        session=s,
    )
    d = compare(q3, q2, session=s)
    assert d.meta.unit == "CNY"
    assert "unit=CNY" in d._repr_identity()


def _bootstrap_compare_axis_project(tmp_path) -> None:
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n\n"
        "orders = ms.entity(name='orders', datasource=ms.Ref.datasource('warehouse'), "
        "source=md.table('orders'))\n\n"
        "@ms.time_dimension(entity=orders, granularity='day', is_default=True)\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def shipped_date(orders):\n"
        "    return orders.shipped_at.cast('date')\n\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region\n\n"
        "@ms.metric(entities=[orders], additivity='additive')\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )


def _compare_axis_session(tmp_path):
    _bootstrap_compare_axis_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, shipped_at DATE, "
        "amount DOUBLE, region VARCHAR)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', DATE '2026-07-03', 10.0, 'NORTH'),"
        "(2, DATE '2026-08-01', DATE '2026-08-03', 20.0, NULL)"
    )
    return session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})


def test_compare_key_schema_is_stable_across_observed_null_distribution(tmp_path):
    session = _compare_axis_session(tmp_path)
    metric = make_ref("sales.revenue", SemanticKind.METRIC)
    region = make_ref("sales.orders.region", SemanticKind.DIMENSION)
    july = observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-08-01"},
        dimensions=[region],
        session=session,
    )
    august = observe(
        metric,
        time_scope={"start": "2026-08-01", "end": "2026-09-01"},
        dimensions=[region],
        session=session,
    )

    assert july.meta.key_schema is not None
    assert august.meta.key_schema is not None
    assert july.meta.key_schema.fingerprint == august.meta.key_schema.fingerprint
    delta = compare(august, july, session=session)
    assert len(delta.to_pandas()) == 2


def test_compare_rejects_different_explicit_time_dimension_identities(tmp_path):
    session = _compare_axis_session(tmp_path)
    metric = make_ref("sales.revenue", SemanticKind.METRIC)
    time_scope = {"start": "2026-07-01", "end": "2026-09-01"}
    ordered = observe(
        metric,
        time_scope=time_scope,
        grain="day",
        time_dimension=make_ref("sales.orders.order_date", SemanticKind.TIME_DIMENSION),
        session=session,
    )
    shipped = observe(
        metric,
        time_scope=time_scope,
        grain="day",
        time_dimension=make_ref("sales.orders.shipped_date", SemanticKind.TIME_DIMENSION),
        session=session,
    )

    with pytest.raises(AlignmentFailedError) as exc_info:
        compare(ordered, shipped, session=session)
    assert exc_info.value._context["kind"] == "TimeDimensionIdentityMismatch"
    assert exc_info.value._context["current_time_dimension"] == "sales.orders.order_date"
    assert exc_info.value._context["baseline_time_dimension"] == "sales.orders.shipped_date"
