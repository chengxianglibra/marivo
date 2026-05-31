from __future__ import annotations

import importlib
import inspect
import json
from datetime import UTC, datetime
from time import monotonic

import ibis
import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis import (
    AlignmentPolicy,
    AttributionFrame,
    AttributionFrameMeta,
    DeltaFrame,
    DeltaFrameMeta,
    DimensionRef,
    MetricFrame,
    MetricRef,
)
from marivo.analysis.session.persistence import read_frame_from_disk, read_job_record


def _active_transform(frame: object, **kwargs):
    return session_attach.active().transform(frame, **kwargs)


def _positive_delta_predicate(row):
    return row["delta"] > 0


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    session_attach._reset_process_state()
    yield
    session_attach._reset_process_state()


def _bootstrap_sales(tmp_path, *, with_country=False):
    semantic_dir = tmp_path / ".marivo" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasource"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\n"
        "md.datasource(name='warehouse', backend_type='duckdb', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_model.py").write_text(
        "import marivo.semantic as ms\nms.model(name='sales')\n"
    )
    country_field = (
        "@ms.field(dataset=orders)\ndef country(orders):\n    return orders.country\n\n"
        if with_country
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.semantic as ms\n"
        "\n"
        "@ms.dataset(name='orders', datasource='warehouse')\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n"
        "\n"
        "@ms.time_field(dataset=orders, data_type='date', granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        f"{country_field}"
        "@ms.metric(datasets=[orders], additivity='additive', decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.revenue.sum()\n"
    )


def _seed(con, *, with_country=False):
    country_column = ", country VARCHAR" if with_country else ""
    con.raw_sql(
        f"CREATE TABLE orders (order_id INTEGER, order_date DATE, revenue DOUBLE{country_column})"
    )
    if with_country:
        con.raw_sql(
            "INSERT INTO orders VALUES "
            "(1, DATE '2025-07-01', 8.0, 'US'),"
            "(2, DATE '2025-07-02', 18.0, 'US'),"
            "(3, DATE '2025-07-01', 28.0, 'CA'),"
            "(4, DATE '2025-07-02', 38.0, 'CA'),"
            "(5, DATE '2026-07-01', 10.0, 'US'),"
            "(6, DATE '2026-07-02', 20.0, 'US'),"
            "(7, DATE '2026-07-01', 30.0, 'CA'),"
            "(8, DATE '2026-07-02', 40.0, 'CA')"
        )
        return
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2025-07-01', 8.0),"
        "(2, DATE '2025-07-02', 18.0),"
        "(3, DATE '2026-07-01', 10.0),"
        "(4, DATE '2026-07-02', 20.0)"
    )


def _make_time_series(tmp_path) -> MetricFrame:
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    return session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
    )


def _make_panel(tmp_path) -> MetricFrame:
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    return session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        dimensions=[DimensionRef("country")],
    )


def _make_segmented(tmp_path) -> MetricFrame:
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    return session.observe(
        MetricRef("sales.revenue"),
        dimensions=[DimensionRef("country")],
    )


def _make_delta_time_series(tmp_path) -> DeltaFrame:
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    current = session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
    )
    baseline = session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2025-07-01", "end": "2025-07-03"},
        grain="day",
    )
    return session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))


def _make_attribution_frame(tmp_path) -> AttributionFrame:
    source = _make_topk_delta_time_series()
    session = session_attach.active()
    source_df = source.to_pandas()
    df = pd.DataFrame(
        {
            "bucket_start": source_df["bucket_start"],
            "driver": ["baseline_gap"] * len(source_df),
            "contribution": source_df["delta"],
        }
    )
    return AttributionFrame(
        _df=df,
        meta=AttributionFrameMeta(
            ref="frame_attribution",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            metric_ids=[source.meta.metric_id],
            source_refs=[source.ref],
            attribution_kind="decomposition",
            driver_field="driver",
            value_column=None,
            contribution_column="contribution",
            method="unit-test",
            params={"source": source.ref},
            semantic_kind=source.meta.semantic_kind,
            semantic_model=source.meta.semantic_model,
        ),
    )


def _make_topk_delta_time_series() -> DeltaFrame:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "bucket_start": pd.to_datetime(
                ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]
            ),
            "current": [10.0, 12.0, 25.0, 8.0],
            "baseline": [7.0, 13.0, 17.0, np.nan],
            "delta": [3.0, -1.0, 8.0, np.nan],
            "pct_change": [3.0 / 7.0, -1.0 / 13.0, 8.0 / 17.0, np.nan],
            "pct_change_status": ["computed", "computed", "computed", "not_computable"],
        }
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_field": "order_date",
        },
    }
    return DeltaFrame(
        _df=df,
        meta=DeltaFrameMeta(
            ref="frame_topk_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket", "axes": axes},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )


def _make_delta_panel(tmp_path) -> DeltaFrame:
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    current = session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        dimensions=[DimensionRef("country")],
    )
    baseline = session.observe(
        MetricRef("sales.revenue"),
        timescope={"start": "2025-07-01", "end": "2025-07-03"},
        grain="day",
        dimensions=[DimensionRef("country")],
    )
    return session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))


def test_transform_api_exposes_typed_method_signatures():
    session = session_attach.get_or_create(name="demo")
    assert callable(session.transform)

    topk_signature = inspect.signature(session.transform.topk)
    assert "op" not in topk_signature.parameters
    assert topk_signature.parameters["by"].default is inspect.Parameter.empty
    assert topk_signature.parameters["limit"].default is inspect.Parameter.empty

    rollup_signature = inspect.signature(session.transform.rollup)
    assert "op" not in rollup_signature.parameters
    assert rollup_signature.parameters["drop_axes"].default is inspect.Parameter.empty


def test_transform_api_methods_cover_supported_ops(tmp_path):
    series = _make_time_series(tmp_path)
    session = session_attach.active()
    filtered = session.transform.filter(series, predicate=lambda d: d["revenue"] > 10)
    assert filtered.to_pandas()["revenue"].tolist() == [20.0]

    windowed = session.transform.window(series, window={"start": "2026-07-02", "end": "2026-07-03"})
    assert windowed.to_pandas()["revenue"].tolist() == [20.0]

    top = session.transform.topk(series, by="revenue", limit=1)
    assert top.to_pandas()["revenue"].tolist() == [20.0]

    bottom = session.transform.bottomk(series, by="revenue", limit=1)
    assert bottom.to_pandas()["revenue"].tolist() == [10.0]

    ranked = session.transform.rank(series, by="revenue", method="dense", rank_column="r")
    assert ranked.to_pandas()["r"].tolist() == [2, 1]

    segmented = MetricFrame.from_dataframe(
        pd.DataFrame({"country": ["US", "CA"], "revenue": [30.0, 40.0]}),
        metric_id="sales.revenue",
        axes={"country": {"role": "dimension", "column": "country"}},
        measure={"column": "revenue"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    share = session.transform.normalize(segmented, mode="share")
    assert share.to_pandas()["revenue"].round(6).tolist() == [0.428571, 0.571429]

    panel = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-01"]),
                "country": ["US", "CA"],
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {"role": "time", "column": "bucket_start", "grain": "day"},
            "country": {"role": "dimension", "column": "country"},
        },
        measure={"column": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    rolled = session.transform.rollup(panel, drop_axes=[DimensionRef("country")])
    assert rolled.meta.semantic_kind == "time_series"
    assert "country" not in rolled.to_pandas().columns

    sliced = session.transform.slice(panel, where={DimensionRef("country"): "US"})
    assert sliced.meta.semantic_kind == "time_series"
    assert "country" not in sliced.to_pandas().columns


def _make_one_sided_delta_panel() -> DeltaFrame:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "bucket_start": [pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-01")],
            "country": ["US", "CA"],
            "current": [10.0, np.nan],
            "baseline": [np.nan, 5.0],
            "delta": [np.nan, np.nan],
            "pct_change": [np.nan, np.nan],
            "pct_change_status": ["not_computable", "not_computable"],
        }
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_field": "order_date",
        },
        "country": {"role": "dimension", "column": "country"},
    }
    return DeltaFrame(
        _df=df,
        meta=DeltaFrameMeta(
            ref="frame_one_sided_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket", "axes": axes},
            semantic_kind="panel",
            semantic_model="sales",
        ),
    )


def _make_current_only_delta_panel() -> DeltaFrame:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "bucket_start": [pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-01")],
            "country": ["US", "CA"],
            "current": [10.0, 7.0],
            "baseline": [np.nan, np.nan],
            "delta": [np.nan, np.nan],
            "pct_change": [np.nan, np.nan],
        }
    )
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_field": "order_date",
        },
        "country": {"role": "dimension", "column": "country"},
    }
    return DeltaFrame(
        _df=df,
        meta=DeltaFrameMeta(
            ref="frame_current_only_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=len(df),
            byte_size=0,
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket", "axes": axes},
            semantic_kind="panel",
            semantic_model="sales",
        ),
    )


def _assert_persisted_metric_frame(frame: MetricFrame) -> None:
    session = session_attach.active()
    stored_df, stored_meta = read_frame_from_disk(session.layout, frame.ref)
    assert isinstance(stored_df, pd.DataFrame)
    assert stored_meta["ref"] == frame.ref
    assert frame.meta.produced_by_job is not None
    job_record = read_job_record(session.layout, frame.meta.produced_by_job)
    assert job_record["output_frame_ref"] == frame.ref


def test_transform_unknown_op_raises_op_unsupported(tmp_path):
    from marivo.analysis.errors import TransformOpUnsupportedError

    frame = _make_time_series(tmp_path)
    _assert_persisted_metric_frame(frame)
    with pytest.raises(TransformOpUnsupportedError) as excinfo:
        _active_transform(frame, op="explode")
    assert "explode" in str(excinfo.value)


def test_transform_rejects_attribution_frame(tmp_path):
    from marivo.analysis.errors import TransformOpUnsupportedError

    attribution = _make_attribution_frame(tmp_path)

    with pytest.raises(TransformOpUnsupportedError) as excinfo:
        _active_transform(attribution, op="filter", predicate=lambda d: d.index >= 0)

    assert "AttributionFrame" in str(excinfo.value)


def test_transform_cross_session_rejected(tmp_path):
    from marivo.analysis.errors import CrossSessionFrameError

    frame_a = _make_time_series(tmp_path)
    session_attach._reset_process_state()
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session_b = session_attach.get_or_create(name="other", backends={"warehouse": lambda: con})

    with pytest.raises(CrossSessionFrameError):
        _active_transform(frame_a, op="filter", predicate=lambda d: d["revenue"] > 0)


def test_transform_lineage_and_job_record_persist(tmp_path):
    frame = _make_time_series(tmp_path)
    session = session_attach.active()

    out = _active_transform(frame, op="filter", predicate=lambda d: d["revenue"] > 10)

    assert out.lineage.steps[-1].intent == "transform"
    assert out.lineage.steps[-1].inputs == [frame.ref]
    assert out.meta.produced_by_job is not None
    _, meta_dict = read_frame_from_disk(session.layout, out.ref)
    assert meta_dict["lineage"]["steps"][-1]["intent"] == "transform"
    assert meta_dict["lineage"]["steps"][-1]["inputs"] == [frame.ref]
    job_record = read_job_record(session.layout, out.meta.produced_by_job)
    assert job_record["intent"] == "transform"
    assert job_record["input_frame_refs"] == [frame.ref]
    assert job_record["output_frame_ref"] == out.ref
    assert job_record["status"] == "succeeded"


def test_transform_window_clips_time_series(tmp_path):
    frame = _make_time_series(tmp_path)
    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )
    assert clipped.meta.semantic_kind == "time_series"
    df = clipped.to_pandas()
    assert df["bucket_start"].astype(str).tolist() == ["2026-07-02"]


def test_transform_window_scans_role_based_time_axis_and_excludes_end():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "order_day": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "revenue": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "custom_order_day": {
                "role": "time",
                "column": "order_day",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"column": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )

    assert clipped.to_pandas()["order_day"].astype(str).tolist() == ["2026-07-02"]


def test_transform_window_clips_delta_time_series_without_axes(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = DeltaFrame(
        _df=pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "current": [10.0, 20.0, 30.0],
                "baseline": [8.0, 18.0, 28.0],
                "delta": [2.0, 2.0, 2.0],
                "pct_change": [0.25, 1.0 / 9.0, 1.0 / 14.0],
            }
        ),
        meta=DeltaFrameMeta(
            ref="frame_delta_no_axes",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=None,
            created_at=datetime.now(UTC),
            row_count=3,
            byte_size=0,
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket"},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    assert "axes" not in frame.meta.alignment

    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )

    assert clipped.meta.semantic_kind == "time_series"
    assert clipped.to_pandas()["bucket_start"].astype(str).tolist() == ["2026-07-02"]


def test_transform_window_rejects_relative_window(tmp_path):
    from marivo.analysis.errors import WindowInvalidError

    frame = _make_time_series(tmp_path)

    with pytest.raises(WindowInvalidError) as excinfo:
        _active_transform(
            frame,
            op="window",
            window={"expr": "today", "as_of": "2026-07-02T12:00:00"},
        )

    assert excinfo.value.details["kind"] == "TimeScopeModelInvalid"


def test_transform_window_rejects_relative_window_with_as_of(monkeypatch):
    from marivo.analysis.errors import WindowInvalidError

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"column": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    with pytest.raises(WindowInvalidError) as excinfo:
        _active_transform(
            frame,
            op="window",
            window={"expr": "last 2 days", "as_of": "2026-07-03T01:00:00+00:00"},
        )

    assert excinfo.value.details["kind"] == "TimeScopeModelInvalid"


def test_transform_window_absolute_rejects_tz_field(tmp_path):
    from marivo.analysis.errors import WindowInvalidError

    frame = _make_time_series(tmp_path)

    with pytest.raises(WindowInvalidError) as exc_info:
        _active_transform(
            frame,
            op="window",
            window={"start": "2026-07-01", "end": "2026-07-02", "tz": "UTC"},
        )

    assert exc_info.value.details["kind"] == "TimeScopeModelInvalid"


def test_transform_window_absolute_timezone_clips_tz_aware_axis():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-07-01 00:00", "2026-07-02 00:00", "2026-07-03 00:00"]
                ).tz_localize("America/Los_Angeles"),
                "revenue": [10.0, 20.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"column": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    clipped = _active_transform(
        frame,
        op="window",
        window={
            "start": "2026-07-02T00:00:00",
            "end": "2026-07-03T00:00:00",
        },
    )

    df = clipped.to_pandas()
    assert df["revenue"].tolist() == [20.0]
    assert df["bucket_start"].dt.tz is not None


def test_transform_window_requires_time_axis(tmp_path):
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformShapeUnsupportedError):
        _active_transform(frame, op="window", window={"start": "2026-07-01", "end": "2026-07-31"})


def test_transform_window_updates_meta_window(tmp_path):
    frame = _make_time_series(tmp_path)
    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )
    assert clipped.meta.window is not None
    assert clipped.meta.window["chained_from"] is not None


def test_persist_transform_frame_updates_delta_alignment_axes(tmp_path):
    from marivo.analysis.intents.transform import _persist_transform_frame

    parent = _make_delta_panel(tmp_path)
    session = session_attach.active()
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_field": "order_date",
        }
    }

    out = _persist_transform_frame(
        session=session,
        parent=parent,
        df=parent.to_pandas().drop(columns=["country"]),
        params={"op": "rollup", "drop_axes": ["country"]},
        started_at=datetime.now(UTC),
        started_monotonic=monotonic(),
        axes=axes,
    )

    assert isinstance(out, DeltaFrame)
    assert out.meta.alignment["axes"] == axes
    _, stored_meta = read_frame_from_disk(session.layout, out.ref)
    assert stored_meta["alignment"]["axes"] == axes


def test_persist_transform_frame_stores_json_safe_params(tmp_path):
    from marivo.analysis.intents.transform import _persist_transform_frame

    parent = _make_delta_panel(tmp_path)
    session = session_attach.active()

    out = _persist_transform_frame(
        session=session,
        parent=parent,
        df=parent.to_pandas(),
        params={
            "op": "filter",
            "drop_axes": [DimensionRef("country")],
            "predicate": _positive_delta_predicate,
        },
        started_at=datetime.now(UTC),
        started_monotonic=monotonic(),
    )

    assert out.meta.produced_by_job is not None
    job_record = read_job_record(session.layout, out.meta.produced_by_job)
    json.dumps(job_record["params"])
    assert job_record["params"]["drop_axes"] == [{"type": "DimensionRef", "id": "country"}]
    assert job_record["params"]["predicate"] == {
        "type": "callable",
        "name": f"{__name__}._positive_delta_predicate",
    }


def test_transform_normalize_param_value_converts_numpy_scalar():
    from marivo.analysis.intents.transform import _normalize_param_value

    normalized = _normalize_param_value(np.int64(1))

    assert normalized == 1
    assert type(normalized) is int
    assert json.dumps(normalized) == "1"


def test_transform_normalize_index_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="index")

    df = normalized.to_pandas()
    assert df["revenue"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization == {
        "mode": "index",
        "baseline": None,
        "columns_affected": ["revenue"],
    }


def test_transform_normalize_prefers_declared_metric_measure_column():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "rank": [1, 2],
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"column": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    normalized = _active_transform(frame, op="normalize", mode="index")

    df = normalized.to_pandas()
    assert df["rank"].tolist() == [1, 2]
    assert df["revenue"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization["columns_affected"] == ["revenue"]


def test_transform_normalize_prefers_metric_measure_name_when_column_absent():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "rank": [1, 2],
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"name": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    normalized = _active_transform(frame, op="normalize", mode="index")

    df = normalized.to_pandas()
    assert df["rank"].tolist() == [1, 2]
    assert df["revenue"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization["columns_affected"] == ["revenue"]


def test_transform_normalize_share_on_segmented(tmp_path):
    frame = _make_segmented(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="share")

    df = normalized.to_pandas()
    assert normalized.meta.normalization["mode"] == "share"
    assert df["revenue"].sum() == pytest.approx(1.0)


def test_transform_normalize_pct_change_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="pct_change")

    values = normalized.to_pandas()["revenue"]
    assert pd.isna(values.iloc[0])
    assert values.iloc[1] == pytest.approx(1.0)


def test_transform_normalize_pct_change_on_unsorted_panel_uses_time_order_per_dimension():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-07-02", "2026-07-02", "2026-07-01", "2026-07-01"]
                ),
                "country": ["US", "CA", "US", "CA"],
                "revenue": [20.0, 40.0, 10.0, 30.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            },
            "country": {"role": "dimension", "column": "country"},
        },
        measure={"column": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )

    normalized = _active_transform(frame, op="normalize", mode="pct_change")

    values = normalized.to_pandas()["revenue"]
    assert values.iloc[0] == pytest.approx(1.0)
    assert values.iloc[1] == pytest.approx(1.0 / 3.0)
    assert pd.isna(values.iloc[2])
    assert pd.isna(values.iloc[3])


def test_transform_normalize_share_on_panel_normalizes_within_each_time_bucket(tmp_path):
    frame = _make_panel(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="share")

    df = normalized.to_pandas()
    shares_by_bucket = df.groupby("bucket_start", dropna=False)["revenue"].sum()
    assert shares_by_bucket.tolist() == pytest.approx([1.0, 1.0])


def test_transform_normalize_pct_change_requires_time_axis(tmp_path):
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = _make_segmented(tmp_path)

    with pytest.raises(TransformShapeUnsupportedError) as excinfo:
        _active_transform(frame, op="normalize", mode="pct_change")

    err = excinfo.value
    assert err.details["op"] == "normalize"
    assert err.details["mode"] == "pct_change"
    assert err.details["required_axis"] == "time"


def test_transform_normalize_pct_change_rejects_zero_denominator():
    from marivo.analysis.errors import TransformArgError

    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"]),
                "revenue": [10.0, 0.0, 5.0],
            }
        ),
        metric_id="sales.revenue",
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_field": "order_date",
            }
        },
        measure={"column": "revenue"},
        semantic_kind="time_series",
        semantic_model="sales",
        session=session,
    )

    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="normalize", mode="pct_change")

    err = excinfo.value
    assert err.details["op"] == "normalize"
    assert err.details["mode"] == "pct_change"
    assert err.details["column"] == "revenue"


def test_transform_normalize_per_unit_requires_base(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="normalize", mode="per_unit")


def test_transform_normalize_z_score(tmp_path):
    frame = _make_time_series(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="z_score")

    values = normalized.to_pandas()["revenue"]
    assert values.mean() == pytest.approx(0.0)
    assert values.std(ddof=0) == pytest.approx(1.0)


def test_transform_normalize_share_rejected_on_delta():
    from marivo.analysis.errors import TransformArgError

    frame = _make_topk_delta_time_series()
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="normalize", mode="share")


def test_transform_normalize_index_rejected_on_delta():
    from marivo.analysis.errors import TransformArgError

    frame = _make_topk_delta_time_series()
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="normalize", mode="index")
    assert excinfo.value.details["op"] == "normalize"
    assert excinfo.value.details["frame_kind"] == "delta_frame"


def test_transform_slice_persists_numpy_datetime64_param(tmp_path):
    frame = _make_time_series(tmp_path)
    sliced = _active_transform(
        frame,
        op="slice",
        where={"bucket_start": np.datetime64("2026-07-01")},
    )

    assert sliced.meta.row_count == 1
    assert sliced.meta.produced_by_job is not None
    job_record = read_job_record(session_attach.active().layout, sliced.meta.produced_by_job)
    json.dumps(job_record["params"])
    assert job_record["params"]["where"]["bucket_start"] == "2026-07-01"


def test_transform_dispatcher_persists_handler_result(tmp_path, monkeypatch):
    transform_mod = importlib.import_module("marivo.analysis.intents.transform")
    parent = _make_time_series(tmp_path)
    session = session_attach.active()

    def handler(frame, params):
        assert frame is parent
        assert params.op == "filter"
        return (
            parent.to_pandas().head(1),
            {"semantic_kind": "time_series", "axes": parent.meta.axes},
            {"op": params.op, "predicate": _positive_delta_predicate},
        )

    monkeypatch.setitem(transform_mod._OP_DISPATCH, "filter", handler)

    out = _active_transform(parent, op="filter")

    assert isinstance(out, MetricFrame)
    assert out.ref != parent.ref
    assert out.meta.produced_by_job is not None
    assert out.meta.lineage.steps[-1].intent == "transform"
    job_record = read_job_record(session.layout, out.meta.produced_by_job)
    assert job_record["intent"] == "transform"
    assert job_record["input_frame_refs"] == [parent.ref]
    assert job_record["output_frame_ref"] == out.ref
    assert job_record["params"]["predicate"] == {
        "type": "callable",
        "name": f"{__name__}._positive_delta_predicate",
    }


def test_transform_filter_preserves_metric_frame(tmp_path):
    frame = _make_time_series(tmp_path)
    df = frame.to_pandas()
    original_len = len(df)

    filtered = _active_transform(frame, op="filter", predicate=lambda d: d["revenue"] > 15)

    assert isinstance(filtered, MetricFrame)
    assert filtered.meta.kind == "metric_frame"
    assert filtered.meta.semantic_kind == frame.meta.semantic_kind
    assert filtered.meta.row_count < original_len
    assert filtered.meta.row_count == int((df["revenue"] > 15).sum())
    assert filtered.ref != frame.ref
    assert filtered.lineage.steps[-1].intent == "transform"
    assert filtered.lineage.steps[-1].inputs == [frame.ref]


def test_transform_filter_requires_predicate(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="filter")
    assert "predicate" in str(excinfo.value)


def test_transform_filter_rejects_misaligned_mask_index(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)

    def predicate(d):
        return pd.Series([True] * len(d), index=range(100, 100 + len(d)))

    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="filter", predicate=predicate)
    message = str(excinfo.value)
    assert "index" in message or "alignment" in message


def test_transform_filter_rejects_unsupported_kwargs(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(
            frame,
            op="filter",
            where={"revenue": 10},
            predicate=lambda d: d["revenue"] > 0,
        )
    message = str(excinfo.value)
    assert "where" in message or "unsupported kwargs" in message


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"method": "dense"}, "method"),
        ({"rank_column": "r"}, "rank_column"),
    ],
)
def test_transform_filter_rejects_non_default_rank_kwargs(tmp_path, kwargs, name):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="filter", predicate=lambda d: d["revenue"] > 0, **kwargs)
    assert name in str(excinfo.value)


def test_transform_topk_by_measure_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)
    top = _active_transform(frame, op="topk", by="revenue", limit=1)
    assert top.meta.row_count == 1
    assert top.to_pandas()["revenue"].tolist() == [20.0]


def test_transform_bottomk_by_measure_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)
    bottom = _active_transform(frame, op="bottomk", by="revenue", limit=1)
    assert bottom.meta.row_count == 1
    assert bottom.to_pandas()["revenue"].tolist() == [10.0]


def test_transform_rank_appends_rank_column(tmp_path):
    frame = _make_time_series(tmp_path)
    ranked = _active_transform(frame, op="rank", by="revenue")
    df = ranked.to_pandas()
    assert "rank" in df.columns
    expected = df["revenue"].rank(method="first", ascending=False).astype(int).tolist()
    assert df["rank"].tolist() == expected


def test_transform_rank_custom_column_name(tmp_path):
    frame = _make_time_series(tmp_path)
    ranked = _active_transform(frame, op="rank", by="revenue", rank_column="r")
    assert "r" in ranked.to_pandas().columns


def test_transform_rank_requires_by(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="rank")


def test_transform_rank_rejects_null_by_values():
    from marivo.analysis.errors import TransformArgError

    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"revenue": [10.0, np.nan]}),
        metric_id="sales.revenue",
        axes={},
        measure={"column": "revenue"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )

    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="rank", by="revenue")

    err = excinfo.value
    assert "rank" in str(err)
    assert "by" in str(err)
    assert "null" in str(err) or "non-finite" in str(err)
    assert err.details["op"] == "rank"
    assert err.details["by"] == "revenue"


def test_transform_rank_dense_method_uses_dense_tie_ranks():
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"revenue": [20.0, 20.0, 10.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"column": "revenue"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )

    ranked = _active_transform(frame, op="rank", by="revenue", method="dense")

    assert ranked.to_pandas()["rank"].tolist() == [1, 1, 2]


def test_transform_topk_on_delta_frame_orders_and_leaves_nan_last():
    delta = _make_topk_delta_time_series()
    top = _active_transform(delta, op="topk", by="delta", limit=2)
    assert isinstance(top, DeltaFrame)
    assert top.meta.row_count == 2
    assert top.to_pandas()["delta"].tolist() == [8.0, 3.0]


def test_transform_topk_decrease_on_delta_frame_takes_most_negative_delta():
    delta = _make_topk_delta_time_series()
    top = _active_transform(delta, op="topk", by="delta", limit=1, order="decrease")
    assert isinstance(top, DeltaFrame)
    assert top.meta.row_count == 1
    assert top.to_pandas()["delta"].tolist() == [-1.0]


def test_transform_topk_requires_positive_limit(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="topk", by="revenue", limit=0)


def test_transform_topk_rejects_unknown_column(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="topk", by="not_a_column", limit=1)


def test_transform_rollup_panel_drops_time_axis_to_segmented(tmp_path):
    frame = _make_panel(tmp_path)
    rolled = _active_transform(frame, op="rollup", drop_axes=["time"])
    assert rolled.meta.semantic_kind == "segmented"
    assert "time" not in rolled.meta.axes
    df = rolled.to_pandas()
    assert "bucket_start" not in df.columns
    assert {"country", "revenue"} <= set(df.columns)


def test_transform_rollup_panel_drops_dim_to_time_series(tmp_path):
    frame = _make_panel(tmp_path)
    rolled = _active_transform(frame, op="rollup", drop_axes=[DimensionRef(id="country")])
    assert rolled.meta.semantic_kind == "time_series"
    assert "country" not in rolled.meta.axes
    df = rolled.to_pandas()
    assert "country" not in df.columns
    assert "bucket_start" in df.columns
    raw = frame.to_pandas()
    expected = raw.groupby("bucket_start", as_index=False)["revenue"].sum()
    assert df["revenue"].tolist() == expected["revenue"].tolist()


def test_transform_rollup_delta_panel_drops_dim_and_recomputes_pct_change(tmp_path):
    frame = _make_delta_panel(tmp_path)
    rolled = _active_transform(frame, op="rollup", drop_axes=[DimensionRef(id="country")])
    assert isinstance(rolled, DeltaFrame)
    assert rolled.meta.semantic_kind == "time_series"
    assert "country" not in rolled.meta.alignment["axes"]
    df = rolled.to_pandas()
    assert "bucket_start" in df.columns
    assert "country" not in df.columns
    assert "pct_change" in df.columns
    assert "pct_change_status" in df.columns

    raw = frame.to_pandas()
    expected = raw.groupby("bucket_start", as_index=False)[["current", "baseline", "delta"]].sum(
        min_count=1
    )
    expected["delta"] = expected["current"] - expected["baseline"]
    expected["pct_change"] = expected["delta"] / expected["baseline"].abs()
    np.testing.assert_allclose(df["delta"], expected["delta"])
    np.testing.assert_allclose(df["pct_change"], expected["pct_change"])
    assert df["pct_change_status"].tolist() == ["computed", "computed", "not_computable"]


def test_transform_rollup_delta_recomputes_delta_from_current_and_baseline(tmp_path):
    frame = _make_one_sided_delta_panel()
    rolled = _active_transform(frame, op="rollup", drop_axes=[DimensionRef(id="country")])
    df = rolled.to_pandas()

    assert df["current"].tolist() == [10.0]
    assert df["baseline"].tolist() == [5.0]
    assert df["delta"].tolist() == [5.0]
    assert df["pct_change"].tolist() == [1.0]


def test_transform_rollup_delta_preserves_all_missing_baseline(tmp_path):
    frame = _make_current_only_delta_panel()
    rolled = _active_transform(frame, op="rollup", drop_axes=[DimensionRef(id="country")])
    df = rolled.to_pandas()

    assert df["current"].tolist() == [17.0]
    assert df["baseline"].isna().tolist() == [True]
    assert df["delta"].isna().tolist() == [True]
    assert df["pct_change"].isna().tolist() == [True]


def test_transform_rollup_rejects_dropping_every_axis(tmp_path):
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformShapeUnsupportedError):
        _active_transform(frame, op="rollup", drop_axes=["time"])


def test_transform_rollup_rejects_unknown_axis(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_panel(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError):
        _active_transform(frame, op="rollup", drop_axes=[DimensionRef(id="platform")])


def test_transform_slice_keeps_segmented_when_multi_value(tmp_path):
    frame = _make_segmented(tmp_path)
    sliced = _active_transform(
        frame,
        op="slice",
        where={DimensionRef(id="country"): ["US", "CA"]},
    )
    assert isinstance(sliced, MetricFrame)
    assert sliced.meta.semantic_kind == "segmented"
    assert sliced.meta.row_count == frame.meta.row_count


def test_transform_slice_demotes_segmented_to_scalar_on_single_value(tmp_path):
    frame = _make_segmented(tmp_path)
    sliced = _active_transform(
        frame,
        op="slice",
        where={DimensionRef(id="country"): "US"},
    )
    assert sliced.meta.semantic_kind == "scalar"
    assert "country" not in sliced.meta.axes
    assert sliced.meta.where["country"] == "US"


def test_transform_slice_string_dimension_key_demotes_on_single_value(tmp_path):
    frame = _make_segmented(tmp_path)
    sliced = _active_transform(frame, op="slice", where={"country": "US"})
    assert sliced.meta.semantic_kind == "scalar"
    assert "country" not in sliced.meta.axes
    assert sliced.meta.where["country"] == "US"
    assert "country" not in sliced.to_pandas().columns


def test_transform_slice_delta_dimension_selector_is_recorded_in_alignment(tmp_path):
    frame = _make_delta_panel(tmp_path)
    sliced = _active_transform(frame, op="slice", where={DimensionRef(id="country"): "US"})
    assert isinstance(sliced, DeltaFrame)
    assert sliced.meta.alignment["where"]["country"] == "US"
    assert "country" not in sliced.meta.alignment["axes"]
    assert "country" not in sliced.to_pandas().columns


def test_transform_slice_rejects_unknown_dimension(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError) as excinfo:
        _active_transform(frame, op="slice", where={DimensionRef(id="platform"): "mobile"})
    assert "platform" in str(excinfo.value)


def test_transform_slice_supports_range_tuple(tmp_path):
    frame = _make_time_series(tmp_path)
    values = frame.to_pandas()["bucket_start"]
    start = values.iloc[0]
    end = values.iloc[-1]
    sliced = _active_transform(frame, op="slice", where={"bucket_start": (start, end)})
    expected = int(values.between(start, end, inclusive="both").sum())
    assert sliced.meta.row_count == expected


def test_transform_slice_rejects_string_key_that_is_not_axis_column(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError) as excinfo:
        _active_transform(frame, op="slice", where={"revenue": (15, 35)})
    assert "revenue" in str(excinfo.value)


def test_transform_slice_rejects_incomparable_range_bounds(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="slice", where={"bucket_start": ("x", "z")})
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_slice_rejects_non_range_tuple(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    values = frame.to_pandas()["bucket_start"]
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(
            frame,
            op="slice",
            where={"bucket_start": (values.iloc[0], values.iloc[-1], values.iloc[-1])},
        )
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_slice_rejects_range_tuple_on_dimension(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="slice", where={DimensionRef(id="country"): ("US", "CA")})
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_metric_frame_drops_component_contract(tmp_path):
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = MetricFrame.from_dataframe(
        pd.DataFrame({"region": ["north", "south"], "failure_rate": [0.25, 0.50]}),
        metric_id="sales.failure_rate",
        axes={"region": {"role": "dimension", "column": "region"}},
        measure={"name": "failure_rate"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    frame.meta = frame.meta.model_copy(
        update={
            "component_ref": "frame_components",
            "decomposition": {
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        }
    )

    out = session.transform.topk(frame, by="failure_rate", limit=1)

    assert out.meta.component_ref is None
    assert out.meta.decomposition is None
