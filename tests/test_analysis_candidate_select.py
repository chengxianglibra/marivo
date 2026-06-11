"""CandidateSet.select extracts typed values from CandidateSet rows."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.intents._candidate_columns import (
    build_union_columns,
    validate_shape_columns,
)
from marivo.analysis.lineage import Lineage
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
    from datetime import UTC, datetime

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
    )
    return DeltaFrame(_df=df, meta=meta)


def _hand_built_candidate_set(session, *, shape: str, rows: list[dict[str, Any]]) -> CandidateSet:
    """Construct a CandidateSet without going through discover; used so select
    matrix tests can isolate select behavior from scorer correctness."""

    from datetime import UTC, datetime

    df = build_union_columns(shape, rows)  # type: ignore[arg-type]
    validate_shape_columns(shape, df)  # type: ignore[arg-type]
    objective_for_shape = {
        "point_anomaly": "point_anomalies",
        "period_shift": "period_shifts",
        "driver_axis": "driver_axes",
        "slice": "interesting_slices",
        "window": "interesting_windows",
        "cross_sectional_outlier": "cross_sectional_outliers",
    }
    strategy_for_shape = {
        "point_anomaly": "zscore",
        "period_shift": "delta_window_zscore",
        "driver_axis": "variance_explained",
        "slice": "delta_magnitude",
        "window": "rolling_zscore",
        "cross_sectional_outlier": "mad",
    }
    meta = CandidateSetMeta(
        kind="candidate_set",
        ref=f"frame_{shape}",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=None,
        created_at=datetime.now(UTC),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(),
        shape=shape,  # type: ignore[arg-type]
        objective=objective_for_shape[shape],  # type: ignore[arg-type]
        strategy=strategy_for_shape[shape],  # type: ignore[arg-type]
        source_ref="frame_src",
        source_kind="metric_frame",
        metric_ids=["sales.revenue"],
        semantic_kind="time_series",
        semantic_model="sales",
        source_refs=["frame_src"],
        params={},
    )
    return CandidateSet(_df=df, meta=meta)


def test_select_axis_returns_dimension_ref():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[
            {"item_id": "axis_0", "score": 0.9, "axis": "country"},
            {"item_id": "axis_1", "score": 0.5, "axis": "platform"},
        ],
    )
    selected = cs.select(rank=1, attribute="axis")
    assert isinstance(selected, mv.DimensionRef)
    assert selected.semantic_id == "country"


def test_select_window_returns_absolute_window():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="point_anomaly",
        rows=[
            {
                "item_id": "cand_0",
                "score": 3.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-15"},
            }
        ],
    )
    from marivo.analysis.windows import AbsoluteWindow

    window = cs.select(rank=1, attribute="window")
    assert isinstance(window, AbsoluteWindow)
    assert window.start.startswith("2026-01-15")
    assert window.end.startswith("2026-01-15")


def test_select_baseline_window_for_period_shift():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="period_shift",
        rows=[
            {
                "item_id": "shift_0",
                "score": 4.0,
                "direction": "high",
                "window": {"start": "2026-02-10", "end": "2026-02-16"},
                "baseline_window": {"start": "2026-02-03", "end": "2026-02-09"},
            }
        ],
    )
    from marivo.analysis.windows import AbsoluteWindow

    baseline = cs.select(rank=1, attribute="baseline_window")
    assert isinstance(baseline, AbsoluteWindow)
    assert baseline.start.startswith("2026-02-03")


def test_select_selector_returns_dimension_ref_keyed_dict():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="slice",
        rows=[
            {
                "item_id": "slice_0",
                "score": 5.0,
                "selector": {"country": "US", "platform": "mobile"},
                "keys": {"country": "US", "platform": "mobile"},
            }
        ],
    )
    selector = cs.select(rank=1, attribute="selector")
    assert isinstance(selector, dict)
    assert mv.DimensionRef("country") in selector
    assert selector[mv.DimensionRef("country")] == "US"
    assert selector[mv.DimensionRef("platform")] == "mobile"


def test_select_keys_dot_path_returns_scalar():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="slice",
        rows=[
            {
                "item_id": "slice_0",
                "score": 5.0,
                "selector": {"country": "US"},
                "keys": {"country": "US"},
            }
        ],
    )
    assert cs.select(rank=1, attribute="keys.country") == "US"
    assert cs.select(rank=1, attribute="selector.country") == "US"


def test_select_recommended_followups_returns_typed_list():
    session = session_attach.get_or_create(name="demo")
    followup = mv.FollowupAction(action_id="a1", kind="submit_step")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[
            {
                "item_id": "axis_0",
                "score": 0.9,
                "axis": "country",
                "recommended_followups": [followup.model_dump(mode="json")],
            }
        ],
    )
    actions = cs.select(rank=1, attribute="recommended_followups")
    assert isinstance(actions, list)
    assert len(actions) == 1
    assert isinstance(actions[0], mv.FollowupAction)
    assert actions[0].action_id == "a1"


def test_select_empty_recommended_followups_returns_empty_list():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[{"item_id": "axis_0", "score": 0.9, "axis": "country"}],
    )
    assert cs.select(rank=1, attribute="recommended_followups") == []


def test_select_field_incompatible_with_shape_raises():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="point_anomaly",
        rows=[
            {
                "item_id": "cand_0",
                "score": 3.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-15"},
            }
        ],
    )
    with pytest.raises(SemanticKindMismatchError) as exc:
        cs.select(rank=1, attribute="axis")
    assert exc.value.details.get("shape") == "point_anomaly"
    assert exc.value.details.get("attribute") == "axis"


def test_select_rank_out_of_range_raises():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[{"item_id": "axis_0", "score": 0.9, "axis": "country"}],
    )
    with pytest.raises(SemanticKindMismatchError) as exc:
        cs.select(rank=5, attribute="axis")
    assert exc.value.details.get("row_count") == 1
    assert exc.value.details.get("requested_rank") == 5


def test_select_unknown_dot_path_key_raises():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="slice",
        rows=[
            {
                "item_id": "slice_0",
                "score": 5.0,
                "selector": {"country": "US"},
                "keys": {"country": "US"},
            }
        ],
    )
    with pytest.raises(SemanticKindMismatchError):
        cs.select(rank=1, attribute="keys.unknown")


def test_select_does_not_create_jobs_or_lineage():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[{"item_id": "axis_0", "score": 0.9, "axis": "country"}],
    )
    jobs_before = len(session.jobs())
    cs.select(rank=1, attribute="axis")
    assert len(session.jobs()) == jobs_before


def test_select_axis_feeds_decompose():
    session = session_attach.get_or_create(name="demo")
    delta_df = pd.DataFrame({"country": ["US", "JP", "DE"], "delta": [10.0, 5.0, 0.5]})
    src = _delta(session, delta_df, semantic_kind="segmented")
    axis_candidates = session.discover.driver_axes(
        src,
        search_space=[mv.DimensionRef("country")],
    )
    selected_axis = axis_candidates.select(rank=1, attribute="axis")
    drivers = session.decompose(src, axis=selected_axis)
    assert drivers.meta.kind == "attribution_frame"


def test_select_window_feeds_transform_window():
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "value": [1.0] * 25 + [50.0] * 5,
        }
    )
    metric = _metric(session, df, semantic_kind="time_series")
    windows = session.discover.interesting_windows(
        metric,
        threshold=2.0,
    )
    window = windows.select(rank=1, attribute="window")
    local = session.transform.window(metric, window=window)
    assert local.meta.kind == "metric_frame"


def test_select_selector_feeds_transform_slice():
    session = session_attach.get_or_create(name="demo")
    delta_df = pd.DataFrame(
        {
            "country": ["US", "US", "JP", "JP"],
            "platform": ["mobile", "web", "mobile", "web"],
            "delta": [50.0, 1.0, -0.5, 0.2],
        }
    )
    src = _delta(session, delta_df, semantic_kind="segmented")
    src.meta.alignment["axes"] = {  # type: ignore[index]
        "country": {"role": "dimension", "column": "country"},
        "platform": {"role": "dimension", "column": "platform"},
    }
    slice_cands = session.discover.interesting_slices(
        src,
        search_space=[mv.DimensionRef("country"), mv.DimensionRef("platform")],
        threshold=2.0,
    )
    selector = slice_cands.select(rank=1, attribute="selector")
    focus = session.transform.slice(src, where=selector)
    assert focus.meta.kind == "delta_frame"


def test_as_driver_axis_passes_when_shape_matches():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="driver_axis",
        rows=[{"item_id": "axis_0", "score": 0.9, "axis": "country"}],
    )
    assert cs.as_driver_axis() is cs


def test_as_driver_axis_fails_when_shape_mismatches():
    session = session_attach.get_or_create(name="demo")
    cs = _hand_built_candidate_set(
        session,
        shape="point_anomaly",
        rows=[
            {
                "item_id": "cand_0",
                "score": 3.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-15"},
            }
        ],
    )
    with pytest.raises(SemanticKindMismatchError) as exc:
        cs.as_driver_axis()
    assert exc.value.details.get("got_shape") == "point_anomaly"
    assert exc.value.details.get("expected_shape") == "driver_axis"


@pytest.mark.parametrize(
    "method, shape",
    [
        ("as_point_anomaly", "point_anomaly"),
        ("as_period_shift", "period_shift"),
        ("as_driver_axis", "driver_axis"),
        ("as_slice", "slice"),
        ("as_window", "window"),
        ("as_cross_sectional_outlier", "cross_sectional_outlier"),
    ],
)
def test_all_six_as_methods_exposed(method, shape):
    session = session_attach.get_or_create(name="demo")
    rows: list[dict[str, Any]]
    if shape == "point_anomaly":
        rows = [
            {
                "item_id": "cand_0",
                "score": 3.5,
                "direction": "high",
                "window": {"start": "2026-01-15", "end": "2026-01-15"},
            }
        ]
    elif shape == "period_shift":
        rows = [
            {
                "item_id": "shift_0",
                "score": 4.0,
                "direction": "high",
                "window": {"start": "2026-02-10", "end": "2026-02-16"},
                "baseline_window": {"start": "2026-02-03", "end": "2026-02-09"},
            }
        ]
    elif shape == "driver_axis":
        rows = [{"item_id": "axis_0", "score": 0.9, "axis": "country"}]
    elif shape == "slice":
        rows = [
            {
                "item_id": "slice_0",
                "score": 5.0,
                "selector": {"country": "US"},
                "keys": {"country": "US"},
            }
        ]
    elif shape == "window":
        rows = [
            {
                "item_id": "window_0",
                "score": 4.0,
                "window": {"start": "2026-03-01", "end": "2026-03-07"},
            }
        ]
    else:
        rows = [
            {
                "item_id": "outlier_0",
                "score": 4.0,
                "direction": "high",
                "keys": {"region": "x"},
            }
        ]
    cs = _hand_built_candidate_set(session, shape=shape, rows=rows)
    assert getattr(cs, method)() is cs
