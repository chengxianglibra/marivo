"""session.correlate for MetricFrames."""

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis._capabilities.model import LiveHelpTarget
from marivo.analysis.errors import (
    AlignmentFailedError,
    AnalysisError,
    CrossSessionFrameError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.policies import AlignmentPolicy
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, metric_id, semantic_model="sales", semantic_kind="time_series"):
    return make_metric_frame(
        df,
        metric_id=metric_id,
        axes={},
        measure={"name": metric_id.rsplit(".", 1)[-1]},
        semantic_kind=semantic_kind,
        semantic_model=semantic_model,
        session=session,
    )


def test_correlate_sample_alignment_same_model_cross_metric():
    session = session_attach.get_or_create(name="demo")
    revenue = _metric(
        session,
        pd.DataFrame({"value": [10.0, 20.0, 30.0, 40.0]}),
        metric_id="sales.revenue",
    )
    orders = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0]}),
        metric_id="sales.orders",
    )

    out = session.correlate(revenue, orders)

    assert isinstance(out, AssociationResult)
    assert out.meta.kind == "association_result"
    assert out.meta.metric_ids == ["sales.revenue", "sales.orders"]
    assert out.meta.semantic_models == ["sales", "sales"]
    assert out.meta.semantic_kinds == ["time_series", "time_series"]
    assert out.meta.correlation == pytest.approx(1.0)
    assert out.meta.alignment == {
        "kind": "window_bucket",
        "calendar": None,
        "period": "month",
        "fallback": "drop",
        "mode": "ordinal_bucket",
        "strict_lengths": False,
    }
    assert out.meta.lag_policy == {"mode": "single", "offset": 0}
    df = out.to_pandas()
    assert df.iloc[0]["metric_id_a"] == "sales.revenue"
    assert df.iloc[0]["metric_id_b"] == "sales.orders"
    assert df.iloc[0]["semantic_model_a"] == "sales"
    assert df.iloc[0]["semantic_model_b"] == "sales"
    assert df.iloc[0]["semantic_kind"] == "time_series"
    assert df.iloc[0]["method"] == "pearson"
    assert df.iloc[0]["alignment_kind"] == "window_bucket"
    assert df.iloc[0]["lag_mode"] == "single"
    assert df.iloc[0]["lag_offset"] == 0
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)
    assert df.iloc[0]["aligned_row_count"] == 4
    assert df.iloc[0]["dropped_row_count"] == 0


def test_correlate_common_key_alignment():
    session = session_attach.get_or_create(name="demo")
    a = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [10.0, 20.0]}),
        metric_id="sales.revenue",
    )
    b = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [5.0, 10.0]}),
        metric_id="sales.orders",
    )

    out = session.correlate(
        a,
        b,
        alignment=AlignmentPolicy(kind="window_bucket"),
    )

    df = out.to_pandas()
    assert df.iloc[0]["driver_field"] == "bucket"
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_common_key_alignment_uses_all_common_non_numeric_columns():
    session = session_attach.get_or_create(name="demo")
    a = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-02", "2026-07-01"],
                "value": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        semantic_kind="panel",
    )
    b = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "business", "business"],
                "bucket": ["2026-07-01", "2026-07-01", "2026-07-02"],
                "value": [5.0, 15.0, 25.0],
            }
        ),
        metric_id="sales.orders",
        semantic_kind="panel",
    )

    out = session.correlate(
        a,
        b,
        alignment=AlignmentPolicy(kind="window_bucket"),
    )

    df = out.to_pandas()
    assert df.iloc[0]["driver_field"] == "segment,bucket"
    assert df.iloc[0]["aligned_row_count"] == 2
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_rejects_duplicate_composite_keys_without_persisting():
    session = session_attach.get_or_create(name="demo")
    a = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-01", "2026-07-01"],
                "value": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        semantic_kind="panel",
    )
    b = _metric(
        session,
        pd.DataFrame(
            {
                "segment": ["consumer", "business"],
                "bucket": ["2026-07-01", "2026-07-01"],
                "value": [5.0, 15.0],
            }
        ),
        metric_id="sales.orders",
        semantic_kind="panel",
    )

    with pytest.raises(AlignmentFailedError):
        session.correlate(
            a,
            b,
            alignment=AlignmentPolicy(kind="window_bucket"),
        )

    assert [job for job in session.jobs() if job.intent == "correlate"] == []


def test_correlate_rejects_unsupported_window_bucket_sub_modes():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0, 6.0]}), metric_id="sales.orders")

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.correlate(
            a,
            b,
            alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
        )

    assert "only supports default window_bucket alignment" in str(exc_info.value)
    assert exc_info.value._context["alignment"]["mode"] == "calendar_bucket"

    with pytest.raises(SemanticKindMismatchError):
        session.correlate(
            a,
            b,
            alignment=AlignmentPolicy(kind="window_bucket", strict_lengths=True),
        )


def test_correlate_sample_alignment_truncates_and_drops_nulls():
    session = session_attach.get_or_create(name="demo")
    a = _metric(
        session,
        pd.DataFrame({"left": [1.0, None, 3.0, 4.0]}),
        metric_id="sales.revenue",
    )
    b = _metric(
        session,
        pd.DataFrame({"right": [1.0, 2.0, 3.0, 4.0, 999.0]}),
        metric_id="sales.orders",
    )

    out = session.correlate(a, b, measure_a="left", measure_b="right")

    df = out.to_pandas()
    assert df.iloc[0]["aligned_row_count"] == 3
    assert df.iloc[0]["dropped_row_count"] == 1
    assert df.iloc[0]["correlation"] == pytest.approx(1.0)


def test_correlate_writes_job_and_frame():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    out = session.correlate(a, b)

    jobs = [job for job in session.jobs() if job.intent == "correlate"]
    assert len(jobs) == 1
    assert jobs[0].output_frame_ref == out.ref
    assert (session._layout.frames_dir / out.ref / "data.parquet").is_file()
    params = session.job(jobs[0].id)["params"]
    assert params["measure_a"] == "value"
    assert params["measure_b"] == "value"
    assert params["alignment"] == {
        "kind": "window_bucket",
        "calendar": None,
        "period": "month",
        "fallback": "drop",
        "mode": "ordinal_bucket",
        "strict_lengths": False,
    }
    assert "lag_policy" not in params
    assert params["method"] == "pearson"


def test_correlate_output_round_trips_through_load_frame():
    session = session_attach.get_or_create(name="demo")
    a = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [1.0, 2.0]}),
        metric_id="sales.revenue",
    )
    b = _metric(
        session,
        pd.DataFrame({"bucket": ["2026-07-01", "2026-07-02"], "value": [2.0, 4.0]}),
        metric_id="sales.orders",
    )

    out = session.correlate(a, b)
    loaded = session.get_frame(out.ref)

    assert isinstance(loaded, AssociationResult)
    assert loaded.meta.correlation == pytest.approx(1.0)
    assert loaded.to_pandas().iloc[0]["driver_field"] == "bucket"


def test_correlate_sample_output_round_trips_with_null_driver_field():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    out = session.correlate(a, b)
    loaded = session.get_frame(out.ref)

    assert isinstance(loaded, AssociationResult)
    row = loaded.to_pandas().iloc[0]
    assert pd.isna(row["driver_field"])
    assert loaded.meta.correlation == pytest.approx(1.0)


def test_correlate_rejects_constant_input_without_persisting():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [5.0, 5.0, 5.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}), metric_id="sales.orders")

    with pytest.raises(AlignmentFailedError):
        session.correlate(a, b)

    assert [job for job in session.jobs() if job.intent == "correlate"] == []


def test_correlate_allows_cross_model_same_shape_frames():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0]}),
        metric_id="marketing.spend",
        semantic_model="marketing",
    )
    out = session.correlate(a, b)

    assert isinstance(out, AssociationResult)
    assert out.meta.semantic_models == ["sales", "marketing"]
    assert out.meta.correlation == pytest.approx(1.0)
    row = out.to_pandas().iloc[0]
    assert row["semantic_model_a"] == "sales"
    assert row["semantic_model_b"] == "marketing"
    jobs = [job for job in session.jobs() if job.intent == "correlate"]
    assert len(jobs) == 1
    record = session.job(jobs[0].id)
    assert record["semantic_model"] == "sales"
    assert record["semantic_models"] == ["sales", "marketing"]


def test_correlate_rejects_mixed_semantic_kind():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(
        session,
        pd.DataFrame({"value": [1.0, 2.0]}),
        metric_id="sales.orders",
        semantic_kind="scalar",
    )
    with pytest.raises(SemanticKindMismatchError):
        session.correlate(a, b)


def test_correlate_rejects_insufficient_aligned_rows():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0]}), metric_id="sales.orders")
    with pytest.raises(AlignmentFailedError):
        session.correlate(a, b)


def test_correlate_rejects_cross_session_frame():
    session_a = session_attach.get_or_create(name="a")
    a = _metric(session_a, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    session_b = session_attach.get_or_create(name="b")
    b = _metric(session_b, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.orders")
    with pytest.raises(CrossSessionFrameError):
        session_a.correlate(a, b)


def test_correlate_rejects_loose_align_parameter():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    with pytest.raises(TypeError):
        session.correlate(a, b, align="sample")  # type: ignore[call-arg]


def test_correlate_rejects_non_alignment_policy():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    with pytest.raises(AnalysisError) as exc:
        session.correlate(a, b, alignment="window_bucket")  # type: ignore[arg-type]

    assert exc.value.location == "correlate.alignment"
    assert exc.value.repair is not None
    assert exc.value.repair.help_target == LiveHelpTarget(
        surface="analysis", canonical_id="correlate"
    )


def test_correlate_rejects_calendar_backed_alignment_for_now():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")
    alignment = AlignmentPolicy(kind="dow_aligned", calendar=mv.CalendarRef("cn_holidays"))

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.correlate(a, b, alignment=alignment)

    assert exc.value._context == {"alignment": alignment.model_dump(mode="json")}


def test_correlate_does_not_accept_lag_policy_argument():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    with pytest.raises(TypeError):
        session.correlate(a, b, lag_policy={"mode": "single", "offset": 0})  # type: ignore[arg-type]


def test_correlate_normalizes_mismatched_bucket_start_dtypes():
    """Correlate should handle object (date) vs datetime64 bucket_start columns."""
    from datetime import date

    session = session_attach.get_or_create(name="demo")

    # Frame A: bucket_start as object dtype (Python date objects)
    a = _metric(
        session,
        pd.DataFrame(
            {
                "bucket_start": pd.Series(
                    [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)],
                    dtype="object",
                ),
                "value": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
    )
    # Frame B: bucket_start as datetime64
    b = _metric(
        session,
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "value": [1.0, 2.0, 3.0],
            }
        ),
        metric_id="sales.orders",
    )

    result = session.correlate(a, b)
    assert isinstance(result, AssociationResult)
    assert -1.0 <= result.meta.correlation <= 1.0


def test_normalize_key_dtypes_casts_object_date_to_datetime64():
    """Unit test for the _normalize_key_dtypes helper."""
    from datetime import date

    from marivo.analysis.intents.correlate import _normalize_key_dtypes

    left = pd.DataFrame(
        {
            "bucket_start": pd.Series([date(2026, 7, 1), date(2026, 7, 2)], dtype="object"),
            "value_a": [1.0, 2.0],
        }
    )
    right = pd.DataFrame(
        {
            "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
            "value_b": [3.0, 4.0],
        }
    )

    norm_left, norm_right = _normalize_key_dtypes(left, right, ["bucket_start"])
    assert pd.api.types.is_datetime64_any_dtype(norm_left["bucket_start"])
    assert pd.api.types.is_datetime64_any_dtype(norm_right["bucket_start"])

    merged = pd.merge(norm_left, norm_right, on=["bucket_start"], validate="one_to_one")
    assert len(merged) == 2


def test_normalize_key_dtypes_skips_non_datetime_object_keys():
    """Object-dtype columns that don't look like dates should be left untouched."""
    from marivo.analysis.intents.correlate import _normalize_key_dtypes

    left = pd.DataFrame(
        {
            "region": pd.Series(["NORTH", "SOUTH"], dtype="object"),
            "value_a": [1.0, 2.0],
        }
    )
    right = pd.DataFrame(
        {
            "region": pd.Series(["NORTH", "SOUTH"], dtype="object"),
            "value_b": [3.0, 4.0],
        }
    )

    # Both sides are object dtype — no mismatch, so no normalization needed.
    norm_left, norm_right = _normalize_key_dtypes(left, right, ["region"])
    assert norm_left["region"].dtype == object
    assert norm_right["region"].dtype == object
