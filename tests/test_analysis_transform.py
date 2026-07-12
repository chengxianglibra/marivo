from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from time import monotonic

import ibis
import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis import (
    AlignmentPolicy,
    AttributionFrame,
    DeltaFrame,
    MetricFrame,
)
from marivo.analysis.frames.attribution import AttributionFrameMeta
from marivo.analysis.frames.delta import DeltaFrameMeta
from marivo.analysis.session._layout import read_frame_from_disk, read_job_record
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.shared_fixtures import make_metric_frame


def _active_transform(frame: MetricFrame | DeltaFrame, **kwargs):
    op = kwargs.pop("op")
    return getattr(frame.transform, op)(**kwargs)


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
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    country_field = (
        "@ms.dimension(entity=orders)\ndef country(orders):\n    return orders.country\n\n"
        if with_country
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=md.ref('datasource.warehouse'), source=md.table('orders'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.order_date.cast('date')\n"
        "\n"
        f"{country_field}"
        "@ms.metric(entities=[orders], additivity='additive', )\n"
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
    )


def _make_panel(tmp_path) -> MetricFrame:
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    return session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
        dimensions=[make_ref("country", SemanticKind.DIMENSION)],
    )


def _make_segmented(tmp_path) -> MetricFrame:
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    return session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        dimensions=[make_ref("country", SemanticKind.DIMENSION)],
    )


def _make_delta_time_series(tmp_path) -> DeltaFrame:
    _bootstrap_sales(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    current = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
    )
    baseline = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2025-07-01", "end": "2025-07-03"},
        grain="day",
    )
    return session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))


def _make_attribution_frame(tmp_path) -> AttributionFrame:
    source = _make_topk_delta_time_series()
    session = session_attach.current()
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
            "time_dimension": "order_date",
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
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-04"},
        grain="day",
        dimensions=[make_ref("country", SemanticKind.DIMENSION)],
    )
    baseline = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2025-07-01", "end": "2025-07-04"},
        grain="day",
        dimensions=[make_ref("country", SemanticKind.DIMENSION)],
    )
    return session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))


def test_frame_transform_exposes_typed_method_signatures(tmp_path):
    metric = _make_time_series(tmp_path)
    delta = _make_topk_delta_time_series()

    assert callable(metric.transform.topk)
    assert callable(delta.transform.topk)
    assert callable(metric.transform.normalize)
    assert not hasattr(delta.transform, "normalize")

    topk_signature = inspect.signature(metric.transform.topk)
    assert "op" not in topk_signature.parameters
    assert "session" not in topk_signature.parameters
    assert "frame" not in topk_signature.parameters
    assert topk_signature.parameters["by"].default is inspect.Parameter.empty
    assert topk_signature.parameters["limit"].default is inspect.Parameter.empty

    rollup_signature = inspect.signature(metric.transform.rollup)
    assert "op" not in rollup_signature.parameters
    assert rollup_signature.parameters["drop_axes"].default is None
    assert rollup_signature.parameters["grain"].default is None

    normalize_signature = inspect.signature(metric.transform.normalize)
    assert "baseline" in normalize_signature.parameters
    assert normalize_signature.return_annotation in (MetricFrame, "MetricFrame")


def test_frame_transform_methods_preserve_family(tmp_path):
    metric = _make_time_series(tmp_path)
    delta = _make_topk_delta_time_series()

    metric_out = metric.transform.topk(by="value", limit=1)
    delta_out = delta.transform.bottomk(by="delta", limit=1)

    assert isinstance(metric_out, MetricFrame)
    assert isinstance(delta_out, DeltaFrame)


def test_transform_api_methods_cover_supported_ops(tmp_path):
    _bootstrap_sales(tmp_path, with_country=True)
    con = ibis.duckdb.connect(":memory:")
    _seed(con, with_country=True)
    session = session_attach.get_or_create(name="demo", backends={"warehouse": lambda: con})
    series = session.observe(
        make_ref("sales.revenue", SemanticKind.METRIC),
        time_scope={"start": "2026-07-01", "end": "2026-07-03"},
        grain="day",
    )
    filtered = series.transform.filter(predicate=lambda d: d["value"] > 10)
    assert filtered.to_pandas()["value"].tolist() == [40.0, 60.0]

    windowed = series.transform.window(window={"start": "2026-07-02", "end": "2026-07-03"})
    assert windowed.to_pandas()["value"].tolist() == [60.0]

    top = series.transform.topk(by="value", limit=1)
    assert top.to_pandas()["value"].tolist() == [60.0]

    bottom = series.transform.bottomk(by="value", limit=1)
    assert bottom.to_pandas()["value"].tolist() == [40.0]

    ranked = series.transform.rank(by="value", method="dense", rank_column="r")
    assert ranked.to_pandas()["r"].tolist() == [2, 1]

    segmented = make_metric_frame(
        pd.DataFrame({"country": ["US", "CA"], "revenue": [30.0, 40.0]}),
        metric_id="sales.revenue",
        axes={"country": {"role": "dimension", "column": "country"}},
        measure={"column": "revenue"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    share = segmented.transform.normalize(mode="share")
    assert share.to_pandas()["value"].round(6).tolist() == [0.428571, 0.571429]

    panel = make_metric_frame(
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
    rolled = panel.transform.rollup(drop_axes=[make_ref("country", SemanticKind.DIMENSION)])
    assert rolled.meta.semantic_kind == "time_series"
    assert "country" not in rolled.to_pandas().columns

    sliced = panel.transform.slice(slice_by={make_ref("country", SemanticKind.DIMENSION): "US"})
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
            "time_dimension": "order_date",
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
            "time_dimension": "order_date",
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
    session = session_attach.current()
    stored_df, stored_meta = read_frame_from_disk(session._layout, frame.ref)
    assert isinstance(stored_df, pd.DataFrame)
    assert stored_meta["ref"] == frame.ref
    assert frame.meta.produced_by_job is not None
    job_record = read_job_record(session._layout, frame.meta.produced_by_job)
    assert job_record["output_frame_ref"] == frame.ref


def test_frame_transform_unknown_op_is_plain_attribute_error(tmp_path):
    frame = _make_time_series(tmp_path)
    _assert_persisted_metric_frame(frame)

    with pytest.raises(AttributeError) as excinfo:
        frame.transform.explode  # noqa: B018

    assert "explode" in str(excinfo.value)


def test_attribution_frame_has_no_transform_receiver(tmp_path):
    attribution = _make_attribution_frame(tmp_path)

    assert not hasattr(attribution, "transform")


def test_transform_cross_session_rejected(tmp_path):
    from marivo.analysis.errors import CrossSessionFrameError

    frame_a = _make_time_series(tmp_path)
    session_attach._reset_process_state()
    con = ibis.duckdb.connect(":memory:")
    _seed(con)
    session_b = session_attach.get_or_create(name="other", backends={"warehouse": lambda: con})

    with pytest.raises(CrossSessionFrameError):
        _active_transform(frame_a, op="filter", predicate=lambda d: d["value"] > 0)


def test_transform_lineage_and_job_record_persist(tmp_path):
    frame = _make_time_series(tmp_path)
    session = session_attach.current()

    out = _active_transform(frame, op="filter", predicate=lambda d: d["value"] > 10)

    assert out.lineage.steps[-1].intent == "transform"
    assert out.lineage.steps[-1].inputs == [frame.ref]
    assert out.meta.produced_by_job is not None
    _, meta_dict = read_frame_from_disk(session._layout, out.ref)
    assert meta_dict["lineage"]["steps"][-1]["intent"] == "transform"
    assert meta_dict["lineage"]["steps"][-1]["inputs"] == [frame.ref]
    job_record = read_job_record(session._layout, out.meta.produced_by_job)
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
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    assert df["value"].tolist() == [20.0]
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
    session = session_attach.current()
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
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
    _, stored_meta = read_frame_from_disk(session._layout, out.ref)
    assert stored_meta["alignment"]["axes"] == axes


def test_persist_transform_frame_stores_json_safe_params(tmp_path):
    from marivo.analysis.intents.transform import _persist_transform_frame

    parent = _make_delta_panel(tmp_path)
    session = session_attach.current()

    out = _persist_transform_frame(
        session=session,
        parent=parent,
        df=parent.to_pandas(),
        params={
            "op": "filter",
            "drop_axes": [make_ref("country", SemanticKind.DIMENSION)],
            "predicate": _positive_delta_predicate,
        },
        started_at=datetime.now(UTC),
        started_monotonic=monotonic(),
    )

    assert out.meta.produced_by_job is not None
    job_record = read_job_record(session._layout, out.meta.produced_by_job)
    json.dumps(job_record["params"])
    assert job_record["params"]["drop_axes"] == [{"ref": "country", "kind": "dimension"}]
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
    assert df["value"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization == {
        "mode": "index",
        "baseline": None,
        "columns_affected": ["value"],
    }


def test_transform_normalize_prefers_declared_metric_measure_column():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    assert df["value"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization["columns_affected"] == ["value"]


def test_transform_normalize_prefers_metric_measure_name_when_column_absent():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    assert df["value"].tolist() == [100.0, 200.0]
    assert normalized.meta.normalization["columns_affected"] == ["value"]


def test_transform_normalize_share_on_segmented(tmp_path):
    frame = _make_segmented(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="share")

    df = normalized.to_pandas()
    assert normalized.meta.normalization["mode"] == "share"
    assert df["value"].sum() == pytest.approx(1.0)


def test_transform_normalize_pct_change_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="pct_change")

    values = normalized.to_pandas()["value"]
    assert pd.isna(values.iloc[0])
    assert values.iloc[1] == pytest.approx(1.0)


def test_transform_normalize_pct_change_on_unsorted_panel_uses_time_order_per_dimension():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
            },
            "country": {"role": "dimension", "column": "country"},
        },
        measure={"column": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )

    normalized = _active_transform(frame, op="normalize", mode="pct_change")

    values = normalized.to_pandas()["value"]
    assert values.iloc[0] == pytest.approx(1.0)
    assert values.iloc[1] == pytest.approx(1.0 / 3.0)
    assert pd.isna(values.iloc[2])
    assert pd.isna(values.iloc[3])


def test_transform_normalize_share_on_panel_normalizes_within_each_time_bucket(tmp_path):
    frame = _make_panel(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="share")

    df = normalized.to_pandas()
    shares_by_bucket = df.groupby("bucket_start", dropna=False)["value"].sum()
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
    frame = make_metric_frame(
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
                "time_dimension": "order_date",
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
    assert err.details["column"] == "value"


def test_transform_normalize_per_unit_requires_base(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="normalize", mode="per_unit")


def test_transform_normalize_z_score(tmp_path):
    frame = _make_time_series(tmp_path)

    normalized = _active_transform(frame, op="normalize", mode="z_score")

    values = normalized.to_pandas()["value"]
    assert values.mean() == pytest.approx(0.0)
    assert values.std(ddof=0) == pytest.approx(1.0)


def test_transform_normalize_share_rejected_on_delta():
    frame = _make_topk_delta_time_series()
    with pytest.raises(AttributeError):
        _active_transform(frame, op="normalize", mode="share")


def test_transform_normalize_index_rejected_on_delta():
    frame = _make_topk_delta_time_series()
    with pytest.raises(AttributeError):
        _active_transform(frame, op="normalize", mode="index")


def test_transform_slice_persists_numpy_datetime64_param(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "event_date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"event_date": {"role": "dimension", "column": "event_date"}},
        measure={"column": "revenue"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    sliced = _active_transform(
        frame,
        op="slice",
        slice_by={make_ref("event_date", SemanticKind.DIMENSION): np.datetime64("2026-07-01")},
    )

    assert sliced.meta.row_count == 1
    assert sliced.meta.produced_by_job is not None
    job_record = read_job_record(session_attach.current()._layout, sliced.meta.produced_by_job)
    json.dumps(job_record["params"])
    assert job_record["params"]["where"]["event_date"] == "2026-07-01"


def test_transform_filter_preserves_metric_frame(tmp_path):
    frame = _make_time_series(tmp_path)
    df = frame.to_pandas()
    original_len = len(df)

    filtered = _active_transform(frame, op="filter", predicate=lambda d: d["value"] > 15)

    assert isinstance(filtered, MetricFrame)
    assert filtered.meta.kind == "metric_frame"
    assert filtered.meta.semantic_kind == frame.meta.semantic_kind
    assert filtered.meta.row_count < original_len
    assert filtered.meta.row_count == int((df["value"] > 15).sum())
    assert filtered.ref != frame.ref
    assert filtered.lineage.steps[-1].intent == "transform"
    assert filtered.lineage.steps[-1].inputs == [frame.ref]


def test_transform_filter_requires_predicate(tmp_path):
    frame = _make_time_series(tmp_path)
    with pytest.raises(TypeError) as excinfo:
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
    frame = _make_time_series(tmp_path)
    with pytest.raises(TypeError) as excinfo:
        _active_transform(
            frame,
            op="filter",
            slice_by={"value": 10},
            predicate=lambda d: d["value"] > 0,
        )
    message = str(excinfo.value)
    assert "slice_by" in message


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"method": "dense"}, "method"),
        ({"rank_column": "r"}, "rank_column"),
    ],
)
def test_transform_filter_rejects_non_default_rank_kwargs(tmp_path, kwargs, name):
    frame = _make_time_series(tmp_path)
    with pytest.raises(TypeError) as excinfo:
        _active_transform(frame, op="filter", predicate=lambda d: d["revenue"] > 0, **kwargs)
    assert name in str(excinfo.value)


def test_transform_topk_by_measure_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)
    top = _active_transform(frame, op="topk", by="value", limit=1)
    assert top.meta.row_count == 1
    assert top.to_pandas()["value"].tolist() == [20.0]


def test_transform_bottomk_by_measure_on_time_series(tmp_path):
    frame = _make_time_series(tmp_path)
    bottom = _active_transform(frame, op="bottomk", by="value", limit=1)
    assert bottom.meta.row_count == 1
    assert bottom.to_pandas()["value"].tolist() == [10.0]


def test_transform_rank_appends_rank_column(tmp_path):
    frame = _make_time_series(tmp_path)
    ranked = _active_transform(frame, op="rank", by="value")
    df = ranked.to_pandas()
    assert "rank" in df.columns
    expected = df["value"].rank(method="first", ascending=False).astype(int).tolist()
    assert df["rank"].tolist() == expected


def test_transform_rank_custom_column_name(tmp_path):
    frame = _make_time_series(tmp_path)
    ranked = _active_transform(frame, op="rank", by="value", rank_column="r")
    assert "r" in ranked.to_pandas().columns


def test_transform_rank_requires_by(tmp_path):
    frame = _make_time_series(tmp_path)
    with pytest.raises(TypeError):
        _active_transform(frame, op="rank")


def test_transform_rank_rejects_null_by_values():
    from marivo.analysis.errors import TransformArgError

    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"revenue": [10.0, np.nan]}),
        metric_id="sales.revenue",
        axes={},
        measure={"column": "revenue"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )

    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(frame, op="rank", by="value")

    err = excinfo.value
    assert "rank" in str(err)
    assert "by" in str(err)
    assert "null" in str(err) or "non-finite" in str(err)
    assert err.details["op"] == "rank"
    assert err.details["by"] == "value"


def test_transform_rank_dense_method_uses_dense_tie_ranks():
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame({"revenue": [20.0, 20.0, 10.0]}),
        metric_id="sales.revenue",
        axes={},
        measure={"column": "revenue"},
        semantic_kind="scalar",
        semantic_model="sales",
        session=session,
    )

    ranked = _active_transform(frame, op="rank", by="value", method="dense")

    assert ranked.to_pandas()["rank"].tolist() == [1, 1, 2]


def test_transform_topk_on_delta_frame_orders_and_leaves_nan_last():
    delta = _make_topk_delta_time_series()
    top = _active_transform(delta, op="topk", by="delta", limit=2)
    assert isinstance(top, DeltaFrame)
    assert top.meta.row_count == 2
    assert top.to_pandas()["delta"].tolist() == [8.0, 3.0]


def test_transform_bottomk_on_delta_frame_takes_most_negative_delta():
    delta = _make_topk_delta_time_series()
    bottom = _active_transform(delta, op="bottomk", by="delta", limit=1)
    assert isinstance(bottom, DeltaFrame)
    assert bottom.meta.row_count == 1
    assert bottom.to_pandas()["delta"].tolist() == [-1.0]


def test_transform_topk_requires_positive_limit(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="topk", by="value", limit=0)


def test_transform_topk_rejects_unknown_column(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="topk", by="not_a_column", limit=1)


def test_transform_rollup_rejects_time_axis_dimension_ref(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_panel(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError):
        _active_transform(frame, op="rollup", drop_axes=[make_ref("time", SemanticKind.DIMENSION)])


def test_transform_rollup_panel_drops_dim_to_time_series(tmp_path):
    frame = _make_panel(tmp_path)
    rolled = _active_transform(
        frame, op="rollup", drop_axes=[make_ref("country", SemanticKind.DIMENSION)]
    )
    assert rolled.meta.semantic_kind == "time_series"
    assert "country" not in rolled.meta.axes
    df = rolled.to_pandas()
    assert "country" not in df.columns
    assert "bucket_start" in df.columns
    raw = frame.to_pandas()
    expected = raw.groupby("bucket_start", as_index=False)["value"].sum()
    assert df["value"].tolist() == expected["value"].tolist()


def test_transform_rollup_delta_panel_drops_dim_and_recomputes_pct_change(tmp_path):
    frame = _make_delta_panel(tmp_path)
    rolled = _active_transform(
        frame, op="rollup", drop_axes=[make_ref("country", SemanticKind.DIMENSION)]
    )
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
    rolled = _active_transform(
        frame, op="rollup", drop_axes=[make_ref("country", SemanticKind.DIMENSION)]
    )
    df = rolled.to_pandas()

    assert df["current"].tolist() == [10.0]
    assert df["baseline"].tolist() == [5.0]
    assert df["delta"].tolist() == [5.0]
    assert df["pct_change"].tolist() == [1.0]


def test_transform_rollup_delta_preserves_all_missing_baseline(tmp_path):
    frame = _make_current_only_delta_panel()
    rolled = _active_transform(
        frame, op="rollup", drop_axes=[make_ref("country", SemanticKind.DIMENSION)]
    )
    df = rolled.to_pandas()

    assert df["current"].tolist() == [17.0]
    assert df["baseline"].isna().tolist() == [True]
    assert df["delta"].isna().tolist() == [True]
    assert df["pct_change"].isna().tolist() == [True]


def test_transform_rollup_rejects_dropping_time_axis(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError):
        _active_transform(frame, op="rollup", drop_axes=[make_ref("time", SemanticKind.DIMENSION)])


def test_transform_rollup_rejects_unknown_axis(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_panel(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError):
        _active_transform(
            frame, op="rollup", drop_axes=[make_ref("platform", SemanticKind.DIMENSION)]
        )


def test_transform_slice_keeps_segmented_when_multi_value(tmp_path):
    frame = _make_segmented(tmp_path)
    sliced = _active_transform(
        frame,
        op="slice",
        slice_by={make_ref("country", SemanticKind.DIMENSION): ["US", "CA"]},
    )
    assert isinstance(sliced, MetricFrame)
    assert sliced.meta.semantic_kind == "segmented"
    assert sliced.meta.row_count == frame.meta.row_count


def test_transform_slice_demotes_segmented_to_scalar_on_single_value(tmp_path):
    frame = _make_segmented(tmp_path)
    sliced = _active_transform(
        frame,
        op="slice",
        slice_by={make_ref("country", SemanticKind.DIMENSION): "US"},
    )
    assert sliced.meta.semantic_kind == "scalar"
    assert "country" not in sliced.meta.axes
    assert sliced.meta.where["sales.orders.country"] == "US"


def test_transform_slice_accepts_catalog_dimension_ref(tmp_path):
    frame = _make_segmented(tmp_path)
    country = session_attach.current().catalog.get("dimension.sales.orders.country").ref

    sliced = _active_transform(frame, op="slice", slice_by={country: "US"})

    assert sliced.meta.where == {"sales.orders.country": "US"}


def test_transform_slice_requires_dimension_ref_keys(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformArgError):
        _active_transform(frame, op="slice", slice_by={"country": "US"})


def test_transform_slice_delta_dimension_selector_is_recorded_in_alignment(tmp_path):
    frame = _make_delta_panel(tmp_path)
    sliced = _active_transform(
        frame, op="slice", slice_by={make_ref("country", SemanticKind.DIMENSION): "US"}
    )
    assert isinstance(sliced, DeltaFrame)
    assert sliced.meta.alignment["where"]["sales.orders.country"] == "US"
    assert "country" not in sliced.meta.alignment["axes"]
    assert "country" not in sliced.to_pandas().columns


def test_transform_slice_rejects_unknown_dimension(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError) as excinfo:
        _active_transform(
            frame,
            op="slice",
            slice_by={make_ref("platform", SemanticKind.DIMENSION): "mobile"},
        )
    assert "platform" in str(excinfo.value)


def test_transform_slice_supports_range_tuple(tmp_path):
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "event_date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "revenue": [10.0, 20.0],
            }
        ),
        metric_id="sales.revenue",
        axes={"event_date": {"role": "dimension", "column": "event_date"}},
        measure={"column": "revenue"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    values = frame.to_pandas()["event_date"]
    start = values.iloc[0]
    end = values.iloc[-1]
    sliced = _active_transform(
        frame,
        op="slice",
        slice_by={make_ref("event_date", SemanticKind.DIMENSION): (start, end)},
    )
    expected = int(values.between(start, end, inclusive="both").sum())
    assert sliced.meta.row_count == expected


def test_transform_slice_rejects_string_key_that_is_not_axis_column(tmp_path):
    from marivo.analysis.errors import TransformDimensionNotFoundError

    frame = _make_time_series(tmp_path)
    with pytest.raises(TransformDimensionNotFoundError) as excinfo:
        _active_transform(
            frame, op="slice", slice_by={make_ref("revenue", SemanticKind.DIMENSION): (15, 35)}
        )
    assert "revenue" in str(excinfo.value)


def test_transform_slice_rejects_incomparable_range_bounds(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(
            frame, op="slice", slice_by={make_ref("country", SemanticKind.DIMENSION): (1, "z")}
        )
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_slice_rejects_non_range_tuple(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(
            frame,
            op="slice",
            slice_by={make_ref("country", SemanticKind.DIMENSION): ("US", "CA", "MX")},
        )
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_slice_rejects_range_tuple_on_dimension(tmp_path):
    from marivo.analysis.errors import TransformArgError

    frame = _make_segmented(tmp_path)
    with pytest.raises(TransformArgError) as excinfo:
        _active_transform(
            frame,
            op="slice",
            slice_by={make_ref("country", SemanticKind.DIMENSION): ("US", "CA")},
        )
    message = str(excinfo.value)
    assert "tuple" in message or "range" in message


def test_transform_metric_frame_drops_component_contract(tmp_path):
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    frame = make_metric_frame(
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

    out = frame.transform.topk(by="value", limit=1)

    assert out.meta.component_ref is None
    assert out.meta.composition is None


def test_transform_window_preserves_cumulative_marker(tmp_path):
    session = session_attach.get_or_create(name="cum_transform")
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "value": [10.0, 12.0],
            }
        ),
        metric_id="sales.cum_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "1day"}},
        measure={"name": "cum_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=session,
    )
    cumulative_payload = {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.event_time",
        "anchor": "all_history",
        "components": None,
    }
    frame.meta = frame.meta.model_copy(
        update={
            "cumulative": cumulative_payload,
            "component_ref": "frame_component",
            "composition": {"kind": "ratio", "components": {}},
        }
    )

    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )

    assert clipped.meta.cumulative == cumulative_payload
    assert clipped.meta.component_ref is None
    assert clipped.meta.composition is None


def test_transform_window_preserves_grain_to_date_cumulative_anchor(tmp_path):
    """transform.window preserves the cumulative marker for a grain_to_date anchor.

    The v2 cumulative marker carries the anchor tuple (e.g.
    ('grain_to_date', 'month')) so downstream contract()/show() can dispatch
    on the anchor. transform.window must propagate meta.cumulative verbatim
    (model_dump round-trip), preserving the anchor kind and grain.
    """
    session = session_attach.get_or_create(name="cum_gtd_transform")
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "value": [10.0, 22.0],
            }
        ),
        metric_id="sales.mtd_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "1day"}},
        measure={"name": "mtd_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=session,
    )
    cumulative_payload = {
        "kind": "cumulative",
        "base": "sales.gmv",
        "over": "sales.orders.event_time",
        "anchor": ("grain_to_date", "month"),
        "components": None,
    }
    frame.meta = frame.meta.model_copy(
        update={
            "cumulative": cumulative_payload,
            "component_ref": "frame_component",
            "composition": {"kind": "ratio", "components": {}},
        }
    )

    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )

    assert clipped.meta.cumulative is not None
    assert clipped.meta.cumulative["anchor"] == ("grain_to_date", "month")
    assert clipped.meta.cumulative["kind"] == "cumulative"
    # component_ref / composition are stripped on transform (same as all_history).
    assert clipped.meta.component_ref is None
    assert clipped.meta.composition is None


def test_transform_window_preserves_rollup_fold(tmp_path):
    """transform.window preserves meta.rollup_fold across a window clip.

    A rolled cumulative frame (rollup_fold='last') that is subsequently
    window-clipped must keep its rollup_fold marker so downstream consumers
    know the rows are period-end running totals (not reaggregated sums).
    """
    session = session_attach.get_or_create(name="rollup_fold_transform")
    frame = make_metric_frame(
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "value": [10.0, 22.0],
            }
        ),
        metric_id="sales.mtd_gmv",
        axes={"time": {"role": "time", "column": "bucket_start", "grain": "1day"}},
        measure={"name": "mtd_gmv"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        session=session,
    )
    frame.meta = frame.meta.model_copy(
        update={
            "rollup_fold": "last",
            "cumulative": {
                "kind": "cumulative",
                "base": "sales.gmv",
                "over": "sales.orders.event_time",
                "anchor": ("grain_to_date", "month"),
                "components": None,
            },
        }
    )

    clipped = _active_transform(
        frame, op="window", window={"start": "2026-07-02", "end": "2026-07-03"}
    )

    assert clipped.meta.rollup_fold == "last"
    assert clipped.meta.cumulative is not None
    assert clipped.meta.cumulative["anchor"] == ("grain_to_date", "month")


# ---------------------------------------------------------------------------
# Sampled semi-additive rollup gate
# ---------------------------------------------------------------------------


def _bootstrap_bandwidth_for_rollup(tmp_path):
    """Bootstrap a bandwidth semantic project for rollup gate tests."""
    from marivo.analysis.timezone import resolve_system_timezone

    report_tz_name = resolve_system_timezone().name
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = semantic_dir.parent.parent / "datasources"
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
        "\n"
        "bandwidth_samples = ms.entity(\n"
        "    name='bandwidth_samples',\n"
        "    datasource=md.ref('datasource.warehouse'),\n"
        "    primary_key=['sample_id'],\n"
        "    source=md.table('bandwidth_samples'),\n"
        ")\n"
        "\n"
        "@ms.time_dimension(entity=bandwidth_samples, granularity='day')\n"
        "def dt(bandwidth_samples):\n"
        "    return bandwidth_samples.dt.cast('date')\n"
        "\n"
        "@ms.time_dimension(\n"
        "    name='sample_ts',\n"
        "    entity=bandwidth_samples,\n"
        "    granularity='minute',\n"
        f"    parse=ms.datetime(timezone='{report_tz_name}', sample_interval=(5, 'minute')),\n"
        ")\n"
        "def sample_ts(bandwidth_samples):\n"
        "    return bandwidth_samples.sample_ts\n"
        "\n"
        "@ms.dimension(entity=bandwidth_samples)\n"
        "def province(bandwidth_samples):\n"
        "    return bandwidth_samples.province\n"
        "\n"
        "@ms.metric(\n"
        "    name='upstream_bw_p95',\n"
        "    entities=[bandwidth_samples],\n"
        "    additivity=ms.semi_additive(over=sample_ts, fold=('percentile', 0.95)),\n"
        ")\n"
        "def upstream_bw_p95(bandwidth_samples):\n"
        "    return bandwidth_samples.upstream_bw_var.sum()\n"
    )


def _seed_bandwidth_for_rollup(con):
    """Seed bandwidth_samples with two days of data for rollup gate tests."""
    con.raw_sql(
        "CREATE TABLE bandwidth_samples ("
        "sample_id INTEGER, dt DATE, sample_ts TIMESTAMP, "
        "upstream_bw DOUBLE, upstream_bw_var DOUBLE, reserved_bw DOUBLE, province VARCHAR)"
    )
    rows = []
    sid = 1
    for day in ("2026-01-01", "2026-01-02"):
        for i in range(12):
            minute = i * 5
            ts = f"TIMESTAMP '{day} 00:{minute:02d}:00'"
            rows.append(f"({sid}, DATE '{day}', {ts}, 100.0, {(i + 1) * 10.0}, 200.0, 'beijing')")
            sid += 1
            rows.append(f"({sid}, DATE '{day}', {ts}, 200.0, 0.0, 0.0, 'beijing')")
            sid += 1
            rows.append(f"({sid}, DATE '{day}', {ts}, 90.0, 0.0, 0.0, 'shanghai')")
            sid += 1
    con.raw_sql("INSERT INTO bandwidth_samples VALUES " + ",".join(rows))


@pytest.fixture()
def sampled_bandwidth_for_rollup(tmp_path):
    _bootstrap_bandwidth_for_rollup(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_bandwidth_for_rollup(con)
    return session_attach.get_or_create(name="demo_rollup", backends={"warehouse": lambda: con})


def test_rollup_rejects_non_reaggregatable_metric_frame(sampled_bandwidth_for_rollup) -> None:
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = sampled_bandwidth_for_rollup.observe(
        make_ref("sales.upstream_bw_p95", SemanticKind.METRIC),
        time_scope={"start": "2026-01-01", "end": "2026-01-02"},
        grain="hour",
        dimensions=[make_ref("sales.bandwidth_samples.province", SemanticKind.DIMENSION)],
    )

    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        frame.transform.rollup(
            drop_axes=[make_ref("province", SemanticKind.DIMENSION)],
        )

    assert exc_info.value.details["op"] == "rollup"
    assert exc_info.value.details["reason"] == "non_reaggregatable"


# ---------------------------------------------------------------------------
# Task 9: rollup grain re-aggregation (grain param + rollup_fold dispatch)
# ---------------------------------------------------------------------------


def _bootstrap_cumulative_day_project(tmp_path) -> None:
    """Sales project with daily event_time, MTD/QTD cumulative + additive gmv."""
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Data')\n",
        encoding="utf-8",
    )
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n",
        encoding="utf-8",
    )
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    (semantic_dir / "metrics.py").write_text(
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "warehouse = md.ref('datasource.warehouse')\n"
        "events = ms.entity(name='events', datasource=warehouse, source=md.table('events'))\n"
        "event_time = ms.time_dimension_column("
        "name='event_time', entity=events, column='event_time', granularity='hour')\n"
        "amount = ms.measure_column("
        "name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
        "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
        "mtd_gmv = ms.cumulative("
        "name='mtd_gmv', base=gmv, over=event_time,"
        " anchor=ms.grain_to_date(grain='month'))\n"
        "qtd_gmv = ms.cumulative("
        "name='qtd_gmv', base=gmv, over=event_time,"
        " anchor=ms.grain_to_date(grain='quarter'))\n",
        encoding="utf-8",
    )


# Daily sales: Jan 1..31, Feb 1..15 (partial for tail-coverage), Mar 1..31.
# Feb is intentionally partial (ends mid-month) so a month rollup's final Feb
# row is the period-end running total AND the rollup coverage marks that row
# partial. March is full so the day->month->quarter chain has a complete Q1.
_DAY_AMOUNTS_T9: dict[str, dict[str, float]] = {
    "2026-01": {f"2026-01-{d:02d}": float(d) for d in range(1, 32)},
    "2026-02": {f"2026-02-{d:02d}": float(d) for d in range(1, 16)},
    "2026-03": {f"2026-03-{d:02d}": float(d) for d in range(1, 32)},
}


def _seed_cumulative_day(con) -> None:
    rows = []
    for month_days in _DAY_AMOUNTS_T9.values():
        for day_str, amt in month_days.items():
            rows.append((day_str, amt))
    con.create_table(
        "events",
        pd.DataFrame(
            {
                "event_id": list(range(1, len(rows) + 1)),
                "event_time": pd.to_datetime([r[0] for r in rows]),
                "amount": [r[1] for r in rows],
            }
        ),
        overwrite=True,
    )


def _cumulative_day_session(tmp_path):
    _bootstrap_cumulative_day_project(tmp_path)
    con = ibis.duckdb.connect(":memory:")
    _seed_cumulative_day(con)
    return session_attach.get_or_create(name="rollup", backends={"warehouse": lambda: con})


@pytest.fixture
def cumulative_day_session(tmp_path):
    return _cumulative_day_session(tmp_path)


def _observe_cumulative_day(session, *, end: str = "2026-02-16") -> MetricFrame:
    """Observe an MTD (month-reset) cumulative gmv at day grain.

    Default end='2026-02-16' (exclusive) includes Feb 1..15 and ends the
    display window mid-February, so a month rollup's final Feb row is partial.
    """
    return session.observe(
        make_ref("sales.mtd_gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-01-01", "end": end},
        grain="day",
    )


def _observe_additive_day(session) -> MetricFrame:
    """Observe the additive gmv at day grain (reaggregatable)."""
    return session.observe(
        make_ref("sales.gmv", SemanticKind.METRIC),
        time_scope={"start": "2026-01-01", "end": "2026-02-16"},
        grain="day",
    )


def test_rollup_grain_takes_period_ends_for_cumulative(cumulative_day_session):
    """rollup(grain='month') on a cumulative day frame takes the last bucket
    per month per dims (period-end running total), keeping rollup_fold + cumulative."""
    session = cumulative_day_session
    frame = _observe_cumulative_day(session)
    raw = frame.to_pandas().sort_values("bucket_start").reset_index(drop=True)
    feb_last_day_value = float(
        raw.loc[raw["bucket_start"] == pd.Timestamp("2026-02-15"), "value"].iloc[0]
    )
    assert feb_last_day_value == sum(_DAY_AMOUNTS_T9["2026-02"].values())

    rolled = frame.transform.rollup(grain="month")
    df = rolled.to_pandas().sort_values("bucket_start").reset_index(drop=True)

    assert (
        df.loc[df["bucket_start"] == pd.Timestamp("2026-02-01"), "value"].iloc[0]
        == feb_last_day_value
    )
    assert rolled.meta.rollup_fold == "last"
    assert rolled.meta.cumulative is not None
    assert rolled.meta.cumulative.get("anchor") == ("grain_to_date", "month")


def test_rollup_grain_sums_reaggregatable_frame(cumulative_day_session):
    """rollup(grain='month') on an additive (reaggregatable) day frame sums per month."""
    session = cumulative_day_session
    frame = _observe_additive_day(session)
    feb_total = sum(_DAY_AMOUNTS_T9["2026-02"].values())

    rolled = frame.transform.rollup(grain="month")
    df = rolled.to_pandas().sort_values("bucket_start").reset_index(drop=True)

    assert df.loc[df["bucket_start"] == pd.Timestamp("2026-02-01"), "value"].iloc[0] == feb_total
    assert rolled.meta.rollup_fold is None


def test_rollup_requires_at_least_one_of_drop_axes_or_grain(cumulative_day_session):
    """Calling rollup with neither drop_axes nor grain raises TransformArgError."""
    from marivo.analysis.errors import TransformArgError

    session = cumulative_day_session
    frame = _observe_additive_day(session)
    with pytest.raises(TransformArgError) as exc_info:
        frame.transform.rollup()
    msg = str(exc_info.value)
    assert "drop_axes" in msg and "grain" in msg


def test_rollup_rejects_non_reaggregatable_without_fold(sampled_bandwidth_for_rollup):
    """v1 rejection verbatim for non-reaggregatable frames without rollup_fold."""
    from marivo.analysis.errors import TransformShapeUnsupportedError

    frame = sampled_bandwidth_for_rollup.observe(
        make_ref("sales.upstream_bw_p95", SemanticKind.METRIC),
        time_scope={"start": "2026-01-01", "end": "2026-01-02"},
        grain="hour",
        dimensions=[make_ref("sales.bandwidth_samples.province", SemanticKind.DIMENSION)],
    )
    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        frame.transform.rollup(grain="day")
    assert exc_info.value.details["reason"] == "non_reaggregatable"
    assert exc_info.value.details["op"] == "rollup"


def test_rollup_grain_target_must_be_coarser(cumulative_day_session):
    """Target grain finer than the current time-axis grain is rejected."""
    from marivo.analysis.errors import TransformArgError

    session = cumulative_day_session
    frame = _observe_cumulative_day(session)
    with pytest.raises(TransformArgError) as exc_info:
        frame.transform.rollup(grain="hour")  # finer than day
    assert exc_info.value.details["op"] == "rollup"
    assert exc_info.value.details["argument"] == "grain"
    assert exc_info.value.details["target_grain"] == "hour"
    assert exc_info.value.details["current_grain"] == "day"


def test_rollup_grain_compat_rule(cumulative_day_session):
    """Week-rolled cumulative under month reset: target grain week under month
    reset is illegal (week buckets straddle month boundaries)."""
    from marivo.analysis.errors import TransformShapeUnsupportedError

    session = cumulative_day_session
    frame = _observe_cumulative_day(session)
    with pytest.raises(TransformShapeUnsupportedError) as exc_info:
        frame.transform.rollup(grain="week")
    assert exc_info.value.details["op"] == "rollup"
    assert exc_info.value.details["reason"] == "grain_incompatible"
    assert exc_info.value.details["target_grain"] == "week"
    assert exc_info.value.details["reset_grain"] == "month"


def test_rollup_chains_day_month_quarter(cumulative_day_session):
    """day -> month -> quarter chain: quarter row == last month's value in that
    quarter (period ends chain)."""
    session = cumulative_day_session
    frame = _observe_cumulative_day(session, end="2026-04-01")
    day_to_month = frame.transform.rollup(grain="month")
    month_to_quarter = day_to_month.transform.rollup(grain="quarter")

    month_df = day_to_month.to_pandas().sort_values("bucket_start").reset_index(drop=True)
    mar_value = float(
        month_df.loc[month_df["bucket_start"] == pd.Timestamp("2026-03-01"), "value"].iloc[0]
    )
    q_df = month_to_quarter.to_pandas().sort_values("bucket_start").reset_index(drop=True)
    assert (
        q_df.loc[q_df["bucket_start"] == pd.Timestamp("2026-01-01"), "value"].iloc[0] == mar_value
    )
    assert month_to_quarter.meta.rollup_fold == "last"
    assert month_to_quarter.meta.cumulative is not None


def test_rollup_partial_tail_coverage(cumulative_day_session):
    """A trailing display window ending mid-period (Feb 15) marks that final
    period's rollup row partial in coverage — asserted unconditionally."""
    session = cumulative_day_session
    frame = _observe_cumulative_day(session, end="2026-02-15")
    rolled = frame.transform.rollup(grain="month")
    cov = rolled.coverage()
    assert cov is not None
    cov_df = cov.to_pandas().sort_values("bucket_start").reset_index(drop=True)
    # The fixture's display window is known to end mid-Feb, so the Feb rollup
    # row must be partial — assert directly, no guard.
    feb_row = cov_df[cov_df["bucket_start"] == pd.Timestamp("2026-02-01")]
    assert not feb_row.empty
    assert (feb_row["coverage_status"] == "partial").iloc[0]
    assert (cov_df["coverage_status"] == "partial").any()
    # And the complete January row is complete.
    jan_row = cov_df[cov_df["bucket_start"] == pd.Timestamp("2026-01-01")]
    assert not jan_row.empty
    assert (jan_row["coverage_status"] == "complete").iloc[0]


def test_transform_persists_artifact_job_and_lineage_without_observation_findings(tmp_path):
    frame = _make_time_series(tmp_path)
    session = session_attach.current()
    before_observations = session.knowledge().observations()

    out = frame.transform.topk(by="value", limit=1)

    stored_df, stored_meta = read_frame_from_disk(session._layout, out.ref)
    assert isinstance(stored_df, pd.DataFrame)
    assert stored_meta["ref"] == out.ref
    assert out.meta.produced_by_job is not None
    job_record = read_job_record(session._layout, out.meta.produced_by_job)
    assert job_record["intent"] == "transform"
    assert job_record["output_frame_ref"] == out.ref
    assert out.meta.lineage.steps[-1].intent == "transform"

    after_observations = session.knowledge().observations()
    assert [obs.id for obs in after_observations] == [obs.id for obs in before_observations]

    store = session._evidence_store()
    assert store is not None
    conn = store.read()
    artifact = conn.execute(
        "SELECT step_type, artifact_type FROM artifacts WHERE artifact_id = ?",
        (out.meta.artifact_id,),
    ).fetchone()
    finding_count = conn.execute(
        "SELECT count(*) FROM findings WHERE artifact_id = ?",
        (out.meta.artifact_id,),
    ).fetchone()[0]
    followup_count = conn.execute(
        "SELECT count(*) FROM followups WHERE source_artifact_id = ?",
        (out.meta.artifact_id,),
    ).fetchone()[0]

    assert artifact == ("transform", "metric_frame")
    assert finding_count == 0
    assert followup_count == 0
