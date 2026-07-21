from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import AxisNotInPanelDimensionsError
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.policies import AlignmentPolicy
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    yield
    session_attach._reset_process_state()


def _panel_delta(tmp_path):
    _ = tmp_path
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        },
        "region": {"role": "dimension", "column": "region"},
    }
    current = make_metric_frame(
        pd.DataFrame(
            [
                {"bucket_start": "2026-07-01", "region": "NORTH", "revenue": 10.0},
                {"bucket_start": "2026-07-01", "region": "SOUTH", "revenue": 30.0},
                {"bucket_start": "2026-07-02", "region": "NORTH", "revenue": 20.0},
                {"bucket_start": "2026-07-02", "region": "SOUTH", "revenue": 44.0},
            ]
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame(
            [
                {"bucket_start": "2026-07-01", "region": "NORTH", "revenue": 8.0},
                {"bucket_start": "2026-07-01", "region": "SOUTH", "revenue": 28.0},
                {"bucket_start": "2026-07-02", "region": "NORTH", "revenue": 18.0},
                {"bucket_start": "2026-07-02", "region": "SOUTH", "revenue": 38.0},
            ]
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "revenue"},
        semantic_kind="panel",
        semantic_model="sales",
        session=session,
    )
    delta = compare(
        current,
        baseline,
        alignment=AlignmentPolicy(kind="window_bucket", mode="calendar_bucket"),
        session=session,
    )
    return session, delta


def test_decompose_panel_per_bucket(tmp_path):
    session, delta = _panel_delta(tmp_path)

    out = decompose(
        delta, axis=make_ref("sales.orders.region", SemanticKind.DIMENSION), session=session
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.semantic_kind == "panel"
    assert out.meta.driver_field == "region"
    assert out.meta.method == "sum"
    assert out.meta.params["bucket_column"] == "bucket_start"
    assert out.meta.params["value_column"] == "delta"
    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "contribution",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
        "rank",
    ]

    for _, bucket_df in df.groupby("bucket_start", sort=False):
        assert list(bucket_df["rank"]) == list(range(1, len(bucket_df) + 1))
        finite_share = bucket_df["share_of_total_delta"].replace([np.inf, -np.inf], np.nan).dropna()
        assert finite_share.sum() == pytest.approx(1.0)


def test_decompose_panel_accepts_model_prefixed_axis_ref(tmp_path):
    session, delta = _panel_delta(tmp_path)

    out = decompose(
        delta, axis=make_ref("sales.orders.region", SemanticKind.DIMENSION), session=session
    )

    assert out.meta.driver_field == "region"
    assert "region" in out.to_pandas().columns


def test_decompose_panel_axis_not_in_dimensions(tmp_path):
    session, delta = _panel_delta(tmp_path)
    delta._df = delta.to_pandas().assign(channel="WEB")

    with pytest.raises(AxisNotInPanelDimensionsError) as exc_info:
        decompose(
            delta, axis=make_ref("sales.orders.channel", SemanticKind.DIMENSION), session=session
        )

    assert exc_info.value._context["axis"] == "channel"
    assert exc_info.value._context["available_dimensions"] == ["region"]


def test_decompose_panel_axes_single_axis_preserves_bucket_scope(tmp_path):
    session, delta = _panel_delta(tmp_path)

    out = decompose(
        delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)], session=session
    )

    assert out.meta.semantic_kind == "panel"
    assert out.meta.driver_field == "region"
    assert out.meta.params["bucket_column"] == "bucket_start"
    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "contribution",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
        "rank",
    ]
    for _, bucket_df in df.groupby("bucket_start", sort=False):
        assert list(bucket_df["rank"]) == list(range(1, len(bucket_df) + 1))
        finite_share = bucket_df["share_of_total_delta"].replace([np.inf, -np.inf], np.nan).dropna()
        assert finite_share.sum() == pytest.approx(1.0)
