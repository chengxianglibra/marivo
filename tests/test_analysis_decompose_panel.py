from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import AxisNotInPanelDimensionsError
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.intents.compare import compare
from marivo.analysis.intents.decompose import decompose
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.refs import DimensionRef


@pytest.fixture(autouse=True)
def _session_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
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
            "time_field": "order_date",
        },
        "region": {"role": "dimension", "column": "region"},
    }
    current = MetricFrame.from_dataframe(
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
    baseline = MetricFrame.from_dataframe(
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
        alignment=AlignmentPolicy(kind="window_bucket"),
        session=session,
    )
    return session, delta


def test_decompose_panel_per_bucket(tmp_path):
    session, delta = _panel_delta(tmp_path)

    out = decompose(delta, axis=DimensionRef("region"), session=session)

    assert isinstance(out, AttributionFrame)
    assert out.meta.semantic_kind == "panel"
    assert out.meta.driver_field == "region"
    assert out.meta.params["bucket_column"] == "bucket_start"
    assert out.meta.params["value_column"] == "delta"
    df = out.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "contribution",
        "pct_contribution",
        "rank",
    ]

    for _, bucket_df in df.groupby("bucket_start", sort=False):
        assert list(bucket_df["rank"]) == list(range(1, len(bucket_df) + 1))
        finite_pct = bucket_df["pct_contribution"].replace([np.inf, -np.inf], np.nan).dropna()
        assert finite_pct.sum() == pytest.approx(1.0)


def test_decompose_panel_accepts_model_prefixed_axis_ref(tmp_path):
    session, delta = _panel_delta(tmp_path)

    out = decompose(delta, axis=DimensionRef("sales.region"), session=session)

    assert out.meta.driver_field == "region"
    assert "region" in out.to_pandas().columns


def test_decompose_panel_axis_not_in_dimensions(tmp_path):
    session, delta = _panel_delta(tmp_path)
    delta._df = delta.to_pandas().assign(channel="WEB")

    with pytest.raises(AxisNotInPanelDimensionsError) as exc_info:
        decompose(delta, axis=DimensionRef("channel"), session=session)

    assert exc_info.value.details["axis"] == "channel"
    assert exc_info.value.details["available_dimensions"] == ["region"]
