"""Cross-objective dispatch and source-kind / strategy gate for session.discover."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import DiscoverInsufficientDataError, SemanticKindMismatchError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.semantic.catalog import SemanticKind, SemanticRef
from tests.shared_fixtures import make_metric_frame


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _metric(session, df, *, semantic_kind="time_series"):
    return make_metric_frame(
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
        source_current_ref="frame_a",
        source_baseline_ref="frame_b",
        alignment={},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def test_discover_api_exposes_typed_method_signatures():
    session = session_attach.get_or_create(name="demo")
    assert not callable(session.discover)

    driver_axes_signature = inspect.signature(session.discover.driver_axes)
    assert "objective" not in driver_axes_signature.parameters
    assert driver_axes_signature.parameters["search_space"].default is inspect.Parameter.empty
    assert "session" not in driver_axes_signature.parameters

    point_signature = inspect.signature(session.discover.point_anomalies)
    assert "objective" not in point_signature.parameters
    assert "threshold" in point_signature.parameters
    assert "session" not in point_signature.parameters


def test_discover_api_methods_set_objective_shape_and_strategy():
    session = session_attach.get_or_create(name="demo")
    metric_series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC"),
                "value": [0.0, 0.0, 0.0, 10.0],
            }
        ),
        semantic_kind="time_series",
    )
    metric_segmented = _metric(
        session,
        pd.DataFrame({"country": ["US", "CA", "JP"], "value": [10.0, 20.0, 100.0]}),
        semantic_kind="segmented",
    )
    delta_series = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=8, freq="D", tz="UTC"),
                "delta": [0.0, 0.0, 5.0, 5.0, 5.0, 0.0, 0.0, 0.0],
            }
        ),
        semantic_kind="time_series",
    )
    delta_segmented = _delta(
        session,
        pd.DataFrame({"country": ["US", "CA"], "delta": [10.0, -2.0]}),
        semantic_kind="segmented",
    )

    cases = [
        (
            session.discover.point_anomalies(metric_series, threshold=1.0),
            "point_anomalies",
            "point_anomaly",
            "zscore",
        ),
        (
            session.discover.period_shifts(delta_series, threshold=2.0),
            "period_shifts",
            "period_shift",
            "delta_window_zscore",
        ),
        (
            session.discover.driver_axes(
                delta_segmented,
                search_space=[SemanticRef("country", kind=SemanticKind.DIMENSION)],
            ),
            "driver_axes",
            "driver_axis",
            "variance_explained",
        ),
        (
            session.discover.interesting_slices(delta_segmented, threshold=1.0),
            "interesting_slices",
            "slice",
            "delta_magnitude",
        ),
        (
            session.discover.interesting_windows(delta_series, threshold=2.0),
            "interesting_windows",
            "window",
            "rolling_zscore",
        ),
        (
            session.discover.cross_sectional_outliers(
                metric_segmented,
                threshold=1.0,
            ),
            "cross_sectional_outliers",
            "cross_sectional_outlier",
            "mad",
        ),
    ]
    for candidate_set, objective, shape, strategy in cases:
        assert candidate_set.meta.objective == objective
        assert candidate_set.meta.shape == shape
        assert candidate_set.meta.strategy == strategy
        assert candidate_set.meta.params["objective"] == objective


def test_unknown_objective_raises():
    session = session_attach.get_or_create(name="demo")
    name = "not_an_objective"
    with pytest.raises(AttributeError):
        getattr(session.discover, name)


def test_period_shifts_rejects_metric_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.period_shifts(frame)  # type: ignore[arg-type]
    assert exc.value.details.get("objective") == "period_shifts"
    assert exc.value.details.get("source_kind") == "metric_frame"


@pytest.mark.parametrize("row_count", [1, 3])
def test_period_shifts_rejects_time_series_with_too_few_buckets(row_count: int):
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=row_count, freq="D", tz="UTC"),
                "delta": [1.0] * row_count,
            }
        ),
        semantic_kind="time_series",
    )

    with pytest.raises(DiscoverInsufficientDataError) as exc:
        session.discover.period_shifts(delta)

    assert exc.value.details["minimum"] == 4
    assert exc.value.details["row_count"] == row_count


def test_period_shifts_rejects_panel_when_all_series_have_too_few_buckets():
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=3, freq="D", tz="UTC").tolist() * 2,
                "region": ["north"] * 3 + ["south"] * 3,
                "delta": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0],
            }
        ),
        semantic_kind="panel",
    )

    with pytest.raises(DiscoverInsufficientDataError) as exc:
        session.discover.period_shifts(delta)

    assert exc.value.details["minimum"] == 4
    assert exc.value.details["row_count"] == 3
    assert exc.value.details["group_columns"] == ["region"]


def test_period_shifts_allows_panel_when_one_series_has_enough_buckets():
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": [
                    *pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC"),
                    *pd.date_range("2026-01-01", periods=2, freq="D", tz="UTC"),
                ],
                "region": ["north"] * 4 + ["south"] * 2,
                "delta": [0.0, 1.0, 2.0, 3.0, 0.0, 1.0],
            }
        ),
        semantic_kind="panel",
    )

    out = session.discover.period_shifts(delta)

    assert out.meta.objective == "period_shifts"
    assert out.meta.shape == "period_shift"


def test_driver_axes_rejects_metric_frame():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0, 2.0, 3.0]}))
    with pytest.raises(SemanticKindMismatchError):
        session.discover.driver_axes(
            frame, search_space=[SemanticRef("country", kind=SemanticKind.DIMENSION)]
        )  # type: ignore[arg-type]


def test_driver_axes_requires_search_space():
    session = session_attach.get_or_create(name="demo")
    delta = _delta(session, pd.DataFrame({"country": ["US"], "delta": [1.0]}))
    with pytest.raises(TypeError):
        session.discover.driver_axes(delta)  # type: ignore[call-arg]


def test_cross_sectional_outliers_rejects_time_series():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b"], "value": [1.0, 2.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        session.discover.cross_sectional_outliers(frame)


def test_typed_discover_helpers_do_not_accept_strategy():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c"], "value": [1.0, 2.0, 99.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(TypeError):
        session.discover.point_anomalies(frame, strategy="iqr")  # type: ignore[call-arg]


def test_period_shifts_segment_merging():
    session = session_attach.get_or_create(name="demo")
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
    out = session.discover.period_shifts(
        src,
        threshold=2.0,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert rows.loc[0, "direction"] == "high"
    assert pd.notna(rows.loc[0, "window_start"])
    assert pd.notna(rows.loc[0, "baseline_window_start"])
    assert pd.notna(rows.loc[0, "baseline_window_end"])


def test_point_anomalies_baseline_window_populated():
    session = session_attach.get_or_create(name="demo")
    values = [1.0] * 20 + [50.0]
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=21, freq="D", tz="UTC"),
            "value": values,
        }
    )
    src = _metric(session, df, semantic_kind="time_series")
    out = session.discover.point_anomalies(
        src,
        threshold=2.0,
    )
    rows = out.to_pandas()
    assert len(rows) >= 1
    assert pd.notna(rows.loc[0, "window_start"])
    assert pd.notna(rows.loc[0, "window_end"])
    assert pd.notna(rows.loc[0, "baseline_window_start"])
    assert pd.notna(rows.loc[0, "baseline_window_end"])


def test_period_shifts_panel_groups_independently():
    session = session_attach.get_or_create(name="demo")
    buckets = list(pd.date_range("2026-01-01", periods=15, freq="D", tz="UTC"))
    df = pd.DataFrame(
        {
            "bucket": buckets * 2,
            "region": ["x"] * 15 + ["y"] * 15,
            "delta": [0.0] * 5 + [5.0] * 5 + [0.0] * 5 + [0.0] * 15,
        }
    )
    src = _delta(session, df, semantic_kind="panel")
    out = session.discover.period_shifts(
        src,
        threshold=2.0,
    )
    rows = out.to_pandas()
    assert (rows["keys_json"] != "").all()
    assert all(json.loads(k).get("region") == "x" for k in rows["keys_json"])


def test_driver_axes_rank_one_is_largest_axis():
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "US", "US", "JP", "DE"],
            "platform": ["mobile", "web", "tv", "mobile", "web"],
            "delta": [100.0, 100.0, 25.0, 50.0, 50.0],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = session.discover.driver_axes(
        src,
        search_space=[
            SemanticRef("country", kind=SemanticKind.DIMENSION),
            SemanticRef("platform", kind=SemanticKind.DIMENSION),
        ],
    )
    rows = out.to_pandas()
    assert len(rows) == 2
    # country: US contributes 225/325 ~= 0.69 (k=1).
    # platform: top group contributes 150/325 ~= 0.46 (<0.5), so k=2.
    # spec formula 1 / (k + cardinality/1000) ranks smaller k first -> country wins.
    assert rows.loc[0, "axis"] == "country"


def test_driver_axes_records_reason_codes():
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "JP", "DE"],
            "delta": [10.0, 5.0, 0.5],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = session.discover.driver_axes(
        src,
        search_space=[SemanticRef("country", kind=SemanticKind.DIMENSION)],
    )
    rows = out.to_pandas()
    codes = json.loads(rows.loc[0, "reason_codes_json"])
    assert any(code.startswith("top_k_share=") for code in codes)
    assert any(code.startswith("axis_cardinality=") for code in codes)


def test_interesting_slices_returns_selector_dict_round_trip():
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "country": ["US", "US", "JP", "JP"],
            "platform": ["mobile", "web", "mobile", "web"],
            "delta": [50.0, 1.0, -0.5, 0.2],
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = session.discover.interesting_slices(
        src,
        search_space=[
            SemanticRef("country", kind=SemanticKind.DIMENSION),
            SemanticRef("platform", kind=SemanticKind.DIMENSION),
        ],
        threshold=2.0,
    )
    rows = out.to_pandas()
    selectors = [json.loads(s) for s in rows["selector_json"]]
    assert any(sel.get("country") == "US" for sel in selectors)
    keys = [json.loads(k) for k in rows["keys_json"]]
    assert all(sel == key for sel, key in zip(selectors, keys, strict=True))


def test_interesting_slices_metric_input_uses_zscore():
    session = session_attach.get_or_create(name="demo")
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
    out = session.discover.interesting_slices(
        metric,
        search_space=[SemanticRef("country", kind=SemanticKind.DIMENSION)],
        threshold=1.0,
    )
    rows = out.to_pandas()
    assert len(rows) >= 1


def test_interesting_windows_metric_input_finds_segment():
    session = session_attach.get_or_create(name="demo")
    values = [1.0] * 20 + [50.0] * 5 + [1.0] * 5
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "value": values,
        }
    )
    metric = _metric(session, df, semantic_kind="time_series")
    out = session.discover.interesting_windows(
        metric,
        threshold=2.0,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert pd.notna(rows.loc[0, "window_start"])
    assert pd.notna(rows.loc[0, "window_end"])


def test_interesting_windows_delta_input_passes_dispatch():
    session = session_attach.get_or_create(name="demo")
    values = [0.0] * 20 + [10.0] * 5 + [0.0] * 5
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC"),
            "delta": values,
        }
    )
    delta = _delta(session, df, semantic_kind="time_series")
    out = session.discover.interesting_windows(delta, threshold=2.0)
    assert out.meta.shape == "window"


def test_cross_sectional_outliers_segmented():
    session = session_attach.get_or_create(name="demo")
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
    out = session.discover.cross_sectional_outliers(
        metric,
        threshold=3.0,
    )
    rows = out.to_pandas()
    assert len(rows) == 1
    assert json.loads(rows.loc[0, "keys_json"]) == {"region": "e"}
    assert rows.loc[0, "direction"] == "high"


def test_cross_sectional_outliers_records_peer_scope():
    session = session_attach.get_or_create(name="demo")
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
    out = session.discover.cross_sectional_outliers(
        metric,
        threshold=3.0,
        peer_scope=[SemanticRef("region", kind=SemanticKind.DIMENSION)],
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
    session = session_attach.get_or_create(name="demo")
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
        kwargs = {"search_space": [SemanticRef("country", kind=SemanticKind.DIMENSION)]}
        if objective == "interesting_slices":
            kwargs["threshold"] = 1.0
    else:
        pytest.fail(f"unknown builder {builder}")

    helper = getattr(session.discover, objective)
    out = helper(src, **kwargs)
    loaded = session.get_frame(out.ref)
    assert loaded.meta.shape == out.meta.shape
    assert loaded.meta.objective == out.meta.objective
    assert loaded.meta.strategy == out.meta.strategy
    assert [action.operator for action in loaded.meta.recommended_followups] == ["assess_quality"]
    assert list(loaded.to_pandas().columns) == list(out.to_pandas().columns)
