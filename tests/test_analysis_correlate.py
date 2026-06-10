"""session.correlate for MetricFrames."""

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import (
    AlignmentFailedError,
    CrossSessionFrameError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.association import AssociationResult
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.policies import AlignmentPolicy, LagPolicy


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, metric_id, semantic_model="sales", semantic_kind="time_series"):
    return MetricFrame.from_dataframe(
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
        lag_policy=LagPolicy(mode="single", offset=0),
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
        lag_policy=LagPolicy(mode="single", offset=0),
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
            lag_policy=LagPolicy(mode="single", offset=0),
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
    assert exc_info.value.details["alignment"]["mode"] == "calendar_bucket"

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
    assert (session.layout.frames_dir / out.ref / "data.parquet").is_file()
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
    assert params["lag_policy"] == {"mode": "single", "offset": 0}
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
    loaded = mv.load_frame(out.ref, session=session)

    assert isinstance(loaded, AssociationResult)
    assert loaded.meta.correlation == pytest.approx(1.0)
    assert loaded.to_pandas().iloc[0]["driver_field"] == "bucket"


def test_correlate_sample_output_round_trips_with_null_driver_field():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    out = session.correlate(a, b)
    loaded = mv.load_frame(out.ref, session=session)

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

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.correlate(a, b, alignment="window_bucket")  # type: ignore[arg-type]

    assert exc.value.details == {
        "expected_kind": "AlignmentPolicy",
        "got_kind": "str",
    }


def test_correlate_rejects_calendar_backed_alignment_for_now():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")
    alignment = AlignmentPolicy(kind="dow_aligned", calendar=mv.CalendarRef("cn_holidays"))

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.correlate(a, b, alignment=alignment)

    assert exc.value.details == {"alignment": alignment.model_dump(mode="json")}


def test_correlate_rejects_non_lag_policy():
    session = session_attach.get_or_create(name="demo")
    a = _metric(session, pd.DataFrame({"value": [1.0, 2.0]}), metric_id="sales.revenue")
    b = _metric(session, pd.DataFrame({"value": [2.0, 4.0]}), metric_id="sales.orders")

    with pytest.raises(SemanticKindMismatchError) as exc:
        session.correlate(a, b, lag_policy={"mode": "single", "offset": 0})  # type: ignore[arg-type]

    assert exc.value.details == {
        "expected_kind": "LagPolicy",
        "got_kind": "dict",
    }


def test_correlate_rejects_non_zero_lag_policy_construction():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LagPolicy(mode="single", offset=1)
