"""Closed typed CandidateSet selection contract."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.candidate import (
    CandidateSet,
    CandidateSetMeta,
    CrossSectionalOutlierSelection,
    DriverAxisSelection,
    PeriodShiftSelection,
    PointAnomalySelection,
    SliceSelection,
    WindowSelection,
)
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.intents._candidate_columns import (
    build_union_columns,
    validate_shape_columns,
)
from marivo.analysis.lineage import Lineage
from marivo.analysis.windows import AbsoluteWindow
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
from tests.conftest import bootstrap_sales_project
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, semantic_kind="time_series"):
    axes: dict[str, Any] = {}
    if "bucket" in df.columns:
        axes["time"] = {"role": "time", "column": "bucket"}
    return make_metric_frame(
        df,
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def _delta(session, df, *, semantic_kind="segmented"):
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_d",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        additivity="additive",
    )
    return DeltaFrame(_df=df, meta=meta)


def _candidate_set(session, *, shape: str, rows: list[dict[str, Any]]) -> CandidateSet:
    df = build_union_columns(shape, rows)  # type: ignore[arg-type]
    validate_shape_columns(shape, df)  # type: ignore[arg-type]
    objectives = {
        "point_anomaly": "point_anomalies",
        "period_shift": "period_shifts",
        "driver_axis": "driver_axes",
        "slice": "interesting_slices",
        "window": "interesting_windows",
        "cross_sectional_outlier": "cross_sectional_outliers",
    }
    strategies = {
        "point_anomaly": "zscore",
        "period_shift": "delta_window_zscore",
        "driver_axis": "concentration",
        "slice": "slice_zscore",
        "window": "global_zscore_runs",
        "cross_sectional_outlier": "mad",
    }
    meta = CandidateSetMeta(
        ref=f"frame_{shape}",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        shape=shape,  # type: ignore[arg-type]
        objective=objectives[shape],  # type: ignore[arg-type]
        strategy=strategies[shape],  # type: ignore[arg-type]
        source_ref="frame_src",
        source_kind="metric_frame",
        metric_ids=["sales.revenue"],
        semantic_kind="time_series",
        semantic_model="sales",
        source_refs=["frame_src"],
        params={},
    )
    return CandidateSet(_df=df, meta=meta)


@pytest.mark.parametrize(
    ("shape", "row", "expected_type"),
    [
        (
            "point_anomaly",
            {
                "item_id": "point_1",
                "score": 3.5,
                "observed_value": 50.0,
                "baseline_value": 3.5,
                "delta": 46.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-16"},
                "reason_codes": ["zscore_above_threshold"],
            },
            PointAnomalySelection,
        ),
        (
            "period_shift",
            {
                "item_id": "shift_1",
                "score": 4.0,
                "direction": "high",
                "window": {"start": "2026-02-10", "end": "2026-02-17"},
                "baseline_window": {"start": "2026-02-03", "end": "2026-02-10"},
            },
            PeriodShiftSelection,
        ),
        (
            "driver_axis",
            {"item_id": "axis_1", "score": 0.9, "axis": "country"},
            DriverAxisSelection,
        ),
        (
            "slice",
            {
                "item_id": "slice_1",
                "score": 5.0,
                "selector": {"country": "US"},
                "keys": {"country": "US"},
            },
            SliceSelection,
        ),
        (
            "window",
            {
                "item_id": "window_1",
                "score": 4.0,
                "window": {"start": "2026-03-01", "end": "2026-03-08"},
            },
            WindowSelection,
        ),
        (
            "cross_sectional_outlier",
            {
                "item_id": "outlier_1",
                "score": 4.0,
                "direction": "high",
                "keys": {"region": "US"},
                "peer_scope": ["region"],
            },
            CrossSectionalOutlierSelection,
        ),
    ],
)
def test_select_dispatches_all_six_shapes(shape, row, expected_type):
    session = session_attach.get_or_create(name="demo")
    selection = _candidate_set(session, shape=shape, rows=[row]).select()

    assert isinstance(selection, expected_type)
    assert selection.kind == shape
    assert selection.candidate_ref == row["item_id"]
    assert selection.source_artifact_ref == "frame_src"
    assert selection.rank == 1
    assert selection.score == row["score"]
    assert not hasattr(selection, "affordances")
    assert not hasattr(selection, "constraints")


def test_select_has_no_attribute_route_and_returns_complete_point_value():
    session = session_attach.get_or_create(name="demo")
    candidates = _candidate_set(
        session,
        shape="point_anomaly",
        rows=[
            {
                "item_id": "point_1",
                "score": 3.5,
                "observed_value": 50.0,
                "baseline_value": 3.5,
                "delta": 46.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-16"},
            }
        ],
    )

    assert "attribute" not in inspect.signature(CandidateSet.select).parameters
    selected = candidates.select()
    assert isinstance(selected, PointAnomalySelection)
    assert selected.observed_value == 50.0
    assert selected.baseline_value == 3.5
    assert selected.delta == 46.5
    assert isinstance(selected.window, AbsoluteWindow)


def test_select_rank_out_of_range_raises_without_creating_a_job():
    session = session_attach.get_or_create(name="demo")
    candidates = _candidate_set(
        session,
        shape="driver_axis",
        rows=[{"item_id": "axis_1", "score": 0.9, "axis": "country"}],
    )
    before = len(session.jobs())
    with pytest.raises(SemanticKindMismatchError) as exc:
        candidates.select(rank=5)
    assert exc.value._context == {"row_count": 1, "requested_rank": 5}
    assert len(session.jobs()) == before


def test_driver_axis_selection_feeds_attribute(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    src = _delta(
        session,
        pd.DataFrame({"region": ["US", "JP", "DE"], "delta": [10.0, 5.0, 0.5]}),
    )
    candidates = session.discover.driver_axes(
        src,
        search_space=[session.catalog.get("dimension.sales.orders.region").ref],
    )

    selected = candidates.select()
    assert isinstance(selected, DriverAxisSelection)
    assert selected.axis == make_ref("sales.orders.region", SemanticKind.DIMENSION)
    drivers = session.attribute(src, axes=[selected.axis])
    assert drivers.meta.kind == "attribution_frame"


def test_window_selection_feeds_transform_window():
    session = session_attach.get_or_create(name="demo")
    metric = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
                "value": [1.0] * 25 + [50.0] * 5,
            }
        ),
    )
    selected = session.discover.interesting_windows(metric, threshold=2.0).select()
    assert isinstance(selected, WindowSelection)
    assert metric.transform.window(window=selected.window).meta.kind == "metric_frame"


def test_slice_selection_feeds_transform_slice(tmp_path):
    bootstrap_sales_project(tmp_path)
    session = session_attach.get_or_create(name="demo")
    src = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "JP", "JP", "DE", "DE"],
                "delta": [50.0, 50.0, 1.0, 1.0, 1.0, 1.0],
            }
        ),
    )
    src.meta.alignment["axes"] = {  # type: ignore[index]
        "region": {"role": "dimension", "column": "region", "ref": "sales.orders.region"}
    }
    selected = session.discover.interesting_slices(src, threshold=1.0).select()
    assert isinstance(selected, SliceSelection)
    assert selected.selector == {make_ref("sales.orders.region", SemanticKind.DIMENSION): "US"}
    assert src.transform.slice(slice_by=selected.selector).meta.kind == "delta_frame"
