"""Cross-objective dispatch and source-kind / strategy gate for mv.discover."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

import marivo.analysis_py as mv
import marivo.analysis_py.session.attach as session_attach
from marivo.analysis_py.errors import SemanticKindMismatchError
from marivo.analysis_py.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.lineage import Lineage


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, semantic_kind="time_series"):
    return MetricFrame.from_dataframe(
        df,
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        session=session,
    )


def _delta(session, df, *, semantic_kind="time_series"):
    """Hand-build a DeltaFrame for dispatch tests; lineage is irrelevant here."""

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
        source_a_ref="frame_a",
        source_b_ref="frame_b",
        alignment={},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def test_unknown_objective_raises():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="not_an_objective", session=session)  # type: ignore[arg-type]


def test_period_shifts_rejects_metric_frame():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    with pytest.raises(SemanticKindMismatchError) as exc:
        mv.discover(frame, objective="period_shifts", session=session)
    assert exc.value.details.get("objective") == "period_shifts"
    assert exc.value.details.get("source_kind") == "metric_frame"


def test_driver_axes_rejects_metric_frame():
    session = session_attach.create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="driver_axes", session=session)


def test_driver_axes_requires_search_space():
    session = session_attach.create(name="demo")
    delta = _delta(session, pd.DataFrame({"country": ["US"], "delta": [1.0]}))
    with pytest.raises(SemanticKindMismatchError) as exc:
        mv.discover(delta, objective="driver_axes", session=session)
    assert exc.value.details.get("missing") == "search_space"


def test_cross_sectional_outliers_rejects_time_series():
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.discover(frame, objective="cross_sectional_outliers", session=session)


@pytest.mark.parametrize(
    "objective, allowed_strategy, rejected_strategy",
    [
        ("point_anomalies", "zscore", "iqr"),
        ("period_shifts", "delta_window_zscore", "cusum"),
        ("driver_axes", "variance_explained", "lasso"),
        ("interesting_slices", "delta_magnitude", "isolation_forest"),
        ("interesting_windows", "rolling_zscore", "stl_residual"),
        ("cross_sectional_outliers", "mad", "zscore"),
    ],
)
def test_non_default_strategy_rejected(objective, allowed_strategy, rejected_strategy):
    session = session_attach.create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c"], "value": [1.0, 2.0, 99.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        mv.discover(
            frame,
            objective=objective,  # type: ignore[arg-type]
            strategy=rejected_strategy,  # type: ignore[arg-type]
            session=session,
        )


def test_period_shifts_segment_merging():
    session = session_attach.create(name="demo")
    rng = np.arange(30, dtype=float)
    delta = np.zeros(30)
    delta[10:17] = 5.0
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "delta": delta,
        }
    )
    src = _delta(session, df, semantic_kind="time_series")
    out = mv.discover(
        src,
        objective="period_shifts",
        threshold=2.0,
        session=session,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert rows.loc[0, "direction"] == "high"
    assert pd.notna(rows.loc[0, "window_start"])
    assert pd.notna(rows.loc[0, "baseline_window_start"])
    assert pd.notna(rows.loc[0, "baseline_window_end"])


def test_period_shifts_panel_groups_independently():
    session = session_attach.create(name="demo")
    buckets = list(pd.date_range("2026-01-01", periods=15, freq="D", tz="UTC"))
    df = pd.DataFrame(
        {
            "bucket": buckets * 2,
            "region": ["x"] * 15 + ["y"] * 15,
            "delta": [0.0] * 5 + [5.0] * 5 + [0.0] * 5 + [0.0] * 15,
        }
    )
    src = _delta(session, df, semantic_kind="panel")
    out = mv.discover(
        src,
        objective="period_shifts",
        threshold=2.0,
        session=session,
    )
    rows = out.to_pandas()
    assert (rows["keys_json"] != "").all()
    assert all(json.loads(k).get("region") == "x" for k in rows["keys_json"])


def test_driver_axes_rank_one_is_largest_axis():
    session = session_attach.create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "US", "US", "JP", "DE"],
            "platform": ["mobile", "web", "tv", "mobile", "web"],
            "delta": [100.0, 100.0, 25.0, 50.0, 50.0],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = mv.discover(
        src,
        objective="driver_axes",
        search_space=[mv.DimensionRef("country"), mv.DimensionRef("platform")],
        session=session,
    )
    rows = out.to_pandas()
    assert len(rows) == 2
    # country: US contributes 225/325 ~= 0.69 (k=1).
    # platform: top group contributes 150/325 ~= 0.46 (<0.5), so k=2.
    # spec formula 1 / (k + cardinality/1000) ranks smaller k first -> country wins.
    assert rows.loc[0, "axis"] == "country"


def test_driver_axes_records_reason_codes():
    session = session_attach.create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "JP", "DE"],
            "delta": [10.0, 5.0, 0.5],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = mv.discover(
        src,
        objective="driver_axes",
        search_space=[mv.DimensionRef("country")],
        session=session,
    )
    rows = out.to_pandas()
    codes = json.loads(rows.loc[0, "reason_codes_json"])
    assert any(code.startswith("top_k_share=") for code in codes)
    assert any(code.startswith("axis_cardinality=") for code in codes)


def test_interesting_slices_returns_selector_dict_round_trip():
    session = session_attach.create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "US", "JP", "JP"],
            "platform": ["mobile", "web", "mobile", "web"],
            "delta": [50.0, 1.0, -0.5, 0.2],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = mv.discover(
        src,
        objective="interesting_slices",
        search_space=[mv.DimensionRef("country"), mv.DimensionRef("platform")],
        threshold=2.0,
        session=session,
    )
    rows = out.to_pandas()
    selectors = [json.loads(s) for s in rows["selector_json"]]
    assert any(sel.get("country") == "US" for sel in selectors)
    keys = [json.loads(k) for k in rows["keys_json"]]
    assert all(sel == key for sel, key in zip(selectors, keys, strict=True))


def test_interesting_slices_metric_input_uses_zscore():
    session = session_attach.create(name="demo")
    metric = _metric(
        session,
        pd.DataFrame(
            {
                "country": ["US", "JP", "DE"],
                "value": [100.0, 1.0, 1.0],
            }
        ),
        semantic_kind="segmented",
    )
    out = mv.discover(
        metric,
        objective="interesting_slices",
        search_space=[mv.DimensionRef("country")],
        threshold=1.0,
        session=session,
    )
    rows = out.to_pandas()
    assert len(rows) >= 1


def test_interesting_windows_metric_input_finds_segment():
    session = session_attach.create(name="demo")
    values = [1.0] * 20 + [50.0] * 5 + [1.0] * 5
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "value": values,
        }
    )
    metric = _metric(session, df, semantic_kind="time_series")
    out = mv.discover(
        metric,
        objective="interesting_windows",
        threshold=2.0,
        session=session,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert pd.notna(rows.loc[0, "window_start"])
    assert pd.notna(rows.loc[0, "window_end"])


def test_interesting_windows_delta_input_passes_dispatch():
    session = session_attach.create(name="demo")
    values = [0.0] * 20 + [10.0] * 5 + [0.0] * 5
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "delta": values,
        }
    )
    delta = _delta(session, df, semantic_kind="time_series")
    out = mv.discover(delta, objective="interesting_windows", threshold=2.0, session=session)
    assert out.meta.shape == "window"


def test_cross_sectional_outliers_segmented():
    session = session_attach.create(name="demo")
    metric = _metric(
        session,
        pd.DataFrame(
            {
                "region": ["a", "b", "c", "d", "e"],
                "value": [1.0, 1.0, 1.0, 1.0, 100.0],
            }
        ),
        semantic_kind="segmented",
    )
    out = mv.discover(
        metric,
        objective="cross_sectional_outliers",
        threshold=3.0,
        session=session,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert json.loads(rows.loc[0, "keys_json"]) == {"region": "e"}
    assert rows.loc[0, "direction"] == "high"


def test_cross_sectional_outliers_records_peer_scope():
    session = session_attach.create(name="demo")
    metric = _metric(
        session,
        pd.DataFrame(
            {
                "region": ["a", "b", "c", "d", "e"],
                "value": [1.0, 1.0, 1.0, 1.0, 100.0],
            }
        ),
        semantic_kind="segmented",
    )
    out = mv.discover(
        metric,
        objective="cross_sectional_outliers",
        threshold=3.0,
        peer_scope=[mv.DimensionRef("region")],
        session=session,
    )
    rows = out.to_pandas()
    assert json.loads(rows.loc[0, "peer_scope_json"]) == ["region"]


@pytest.mark.parametrize(
    "objective, source_kind, builder",
    [
        ("point_anomalies", "metric", "metric_time_series"),
        ("period_shifts", "delta", "delta_time_series"),
        ("driver_axes", "delta", "delta_segmented"),
        ("interesting_slices", "delta", "delta_segmented"),
        ("interesting_windows", "metric", "metric_time_series"),
        ("cross_sectional_outliers", "metric", "metric_segmented"),
    ],
)
def test_persistence_round_trip(objective, source_kind, builder):
    session = session_attach.create(name="demo")
    if builder == "metric_time_series":
        src = _metric(
            session,
            pd.DataFrame(
                {
                    "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
                    "value": [1.0] * 25 + [50.0] * 5,
                }
            ),
            semantic_kind="time_series",
        )
        kwargs: dict[str, Any] = {"threshold": 2.0}
    elif builder == "metric_segmented":
        src = _metric(
            session,
            pd.DataFrame(
                {"region": ["a", "b", "c", "d", "e"], "value": [1.0, 1.0, 1.0, 1.0, 100.0]}
            ),
            semantic_kind="segmented",
        )
        kwargs = {"threshold": 3.0}
    elif builder == "delta_time_series":
        src = _delta(
            session,
            pd.DataFrame(
                {
                    "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
                    "delta": [0.0] * 10 + [5.0] * 7 + [0.0] * 13,
                }
            ),
            semantic_kind="time_series",
        )
        kwargs = {"threshold": 2.0}
    elif builder == "delta_segmented":
        src = _delta(
            session,
            pd.DataFrame(
                {
                    "country": ["US", "JP", "DE"],
                    "delta": [10.0, 5.0, 0.5],
                }
            ),
            semantic_kind="segmented",
        )
        kwargs = {"search_space": [mv.DimensionRef("country")]}
        if objective == "interesting_slices":
            kwargs["threshold"] = 1.0
    else:
        pytest.fail(f"unknown builder {builder}")

    out = mv.discover(src, objective=objective, session=session, **kwargs)
    loaded = mv.load_frame(out.ref, session=session)
    assert loaded.meta.shape == out.meta.shape
    assert loaded.meta.objective == out.meta.objective
    assert loaded.meta.strategy == out.meta.strategy
    assert [action.operator for action in loaded.meta.recommended_followups] == ["assess_quality"]
    assert list(loaded.to_pandas().columns) == list(out.to_pandas().columns)
