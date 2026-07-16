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
from marivo.analysis.errors import (
    AnalysisError,
    DiscoverAxisNotMaterializedError,
    DiscoverInsufficientDataError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref
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
                search_space=[make_ref("country", SemanticKind.DIMENSION)],
            ),
            "driver_axes",
            "driver_axis",
            "concentration",
        ),
        (
            session.discover.interesting_slices(delta_segmented, threshold=1.0),
            "interesting_slices",
            "slice",
            "slice_zscore",
        ),
        (
            session.discover.interesting_windows(delta_series, threshold=2.0),
            "interesting_windows",
            "window",
            "global_zscore_runs",
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
    with pytest.raises(AnalysisError) as exc:
        session.discover.period_shifts(frame)  # type: ignore[arg-type]
    assert exc.value.location == "discover.period_shifts.source"


@pytest.mark.parametrize("row_count", [1, 3, 4, 5, 6])
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

    # minimum matches the scorer's rolling-window floor max(7, n // 10) (issue #11)
    assert exc.value._context["minimum"] == 7
    assert exc.value._context["row_count"] == row_count


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

    assert exc.value._context["minimum"] == 7
    assert exc.value._context["row_count"] == 3
    assert exc.value._context["group_columns"] == ["region"]


def test_period_shifts_allows_panel_when_one_series_has_enough_buckets():
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": [
                    *pd.date_range("2026-01-01", periods=8, freq="D", tz="UTC"),
                    *pd.date_range("2026-01-01", periods=2, freq="D", tz="UTC"),
                ],
                "region": ["north"] * 8 + ["south"] * 2,
                "delta": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 0.0, 1.0],
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
    with pytest.raises(AnalysisError) as exc:
        session.discover.driver_axes(
            frame, search_space=[make_ref("country", SemanticKind.DIMENSION)]
        )  # type: ignore[arg-type]
    assert exc.value.location == "discover.driver_axes.source"


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


def test_point_anomalies_rejects_unsupported_strategy():
    session = session_attach.get_or_create(name="demo")
    frame = _metric(
        session,
        pd.DataFrame({"bucket": ["a", "b", "c"], "value": [1.0, 2.0, 99.0]}),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(frame, strategy="iqr")  # type: ignore[arg-type]


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


def test_point_anomalies_populates_observed_baseline_delta():
    session = session_attach.get_or_create(name="demo")
    values = [1.0] * 20 + [50.0]
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=21, freq="D", tz="UTC"),
            "value": values,
        }
    )
    src = _metric(session, df, semantic_kind="time_series")
    out = session.discover.point_anomalies(src, threshold=2.0)
    rows = out.to_pandas()
    assert len(rows) >= 1
    row = rows.iloc[0]
    assert pd.notna(row["observed_value"])
    assert pd.notna(row["baseline_value"])
    assert pd.notna(row["delta"])
    assert row["observed_value"] == 50.0
    expected_mean = float(np.mean(values))
    assert abs(row["baseline_value"] - expected_mean) < 0.01
    assert abs(row["delta"] - (50.0 - expected_mean)) < 0.01


def test_point_anomalies_select_observed_baseline_delta():
    session = session_attach.get_or_create(name="demo")
    values = [1.0] * 20 + [50.0]
    df = pd.DataFrame(
        {
            "bucket": pd.date_range("2026-01-01", periods=21, freq="D", tz="UTC"),
            "value": values,
        }
    )
    src = _metric(session, df, semantic_kind="time_series")
    out = session.discover.point_anomalies(src, threshold=2.0)
    assert out.select(rank=1, attribute="observed_value") == 50.0
    assert isinstance(out.select(rank=1, attribute="baseline_value"), float)
    assert isinstance(out.select(rank=1, attribute="delta"), float)


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
            make_ref("country", SemanticKind.DIMENSION),
            make_ref("platform", SemanticKind.DIMENSION),
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
        search_space=[make_ref("country", SemanticKind.DIMENSION)],
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
            make_ref("country", SemanticKind.DIMENSION),
            make_ref("platform", SemanticKind.DIMENSION),
        ],
        threshold=1.0,
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
        search_space=[make_ref("country", SemanticKind.DIMENSION)],
        threshold=1.0,
    )
    rows = out.to_pandas()
    assert len(rows) >= 1


def test_interesting_slices_delta_score_is_unit_invariant():
    """Slice scores are z-scores: rescaling the value unit must not change
    which slices surface or their scores (issue #16)."""
    from marivo.analysis.intents._discover_scorers import score_interesting_slices

    countries = ["US", "JP", "DE", "FR"]
    base = pd.DataFrame({"country": countries, "value": [10.0, -2.0, 0.5, -0.5]})
    scaled = pd.DataFrame({"country": countries, "value": [10.0e6, -2.0e6, 0.5e6, -0.5e6]})
    rows_base, _ = score_interesting_slices(
        base,
        source_ref="s",
        value_column="value",
        axes=["country"],
        threshold=0.5,
        limit=None,
    )
    rows_scaled, _ = score_interesting_slices(
        scaled,
        source_ref="s",
        value_column="value",
        axes=["country"],
        threshold=0.5,
        limit=None,
    )
    assert [r["selector"] for r in rows_base] == [r["selector"] for r in rows_scaled]
    assert [r["score"] for r in rows_base] == pytest.approx([r["score"] for r in rows_scaled])


def test_interesting_slices_delta_uses_zscore_not_raw_magnitude():
    """A delta slice whose mean is well above the population baseline clears
    threshold; a near-baseline slice does not. Score is a z-score of the slice
    mean, not the raw delta magnitude or sum (issues #9, #16)."""
    session = session_attach.get_or_create(name="zscore_delta")
    df = pd.DataFrame(
        {
            "country": ["US"] * 4 + ["JP"] * 4 + ["DE"] * 4,
            "delta": [10.0] * 4 + [0.0] * 4 + [0.0] * 4,
        }
    )
    src = _delta(session, df, semantic_kind="segmented")
    out = session.discover.interesting_slices(
        src,
        search_space=[make_ref("country", SemanticKind.DIMENSION)],
        threshold=1.0,
    ).to_pandas()
    selectors = [json.loads(s) for s in out["selector_json"]]
    assert any(sel.get("country") == "US" for sel in selectors)
    assert all(sel.get("country") != "JP" for sel in selectors)
    # score is a z-score of the slice mean (~1.414), not the raw mean (10) or sum (40)
    assert abs(float(out.loc[0, "score"]) - 1.4142) < 0.01


def test_interesting_slices_skips_high_cardinality_axis_pair():
    """Axis pairs whose cardinality product exceeds the guard are skipped and
    recorded in params rather than materializing an explosive groupby."""
    from marivo.analysis.intents._discover_scorers import _SLICE_MAX_GROUPS

    session = session_attach.get_or_create(name="guard")
    n = 1000
    df = pd.DataFrame(
        {
            "a": [f"a{i % 300}" for i in range(n)],
            "b": [f"b{i % 200}" for i in range(n)],
            "value": [float(i) for i in range(n)],
        }
    )
    metric = _metric(session, df, semantic_kind="segmented")
    out = session.discover.interesting_slices(
        metric,
        search_space=[
            make_ref("a", SemanticKind.DIMENSION),
            make_ref("b", SemanticKind.DIMENSION),
        ],
    )
    skipped = out.meta.params.get("skipped_subsets")
    assert skipped is not None
    assert any(set(entry["axes"]) == {"a", "b"} for entry in skipped)
    # 300 * 200 = 60000 > default guard
    pair_entry = next(entry for entry in skipped if set(entry["axes"]) == {"a", "b"})
    assert pair_entry["cardinality"] == 300 * 200
    assert pair_entry["cardinality"] > _SLICE_MAX_GROUPS
    # single-axis subsets stay under the guard and are not skipped
    assert all(set(entry["axes"]) != {"a"} for entry in skipped)
    assert all(set(entry["axes"]) != {"b"} for entry in skipped)


def test_score_interesting_slices_zscores_and_returns_skip_log():
    """Direct scorer contract: rows are z-scores above threshold, skip log
    records high-cardinality pairs."""
    from marivo.analysis.intents._discover_scorers import score_interesting_slices

    n = 1000
    df = pd.DataFrame(
        {
            "a": [f"a{i % 300}" for i in range(n)],
            "b": [f"b{i % 200}" for i in range(n)],
            # one a-group ("a0") has an extreme mean so a slice clears threshold
            "value": [100.0 if (i % 300) == 0 else 1.0 for i in range(n)],
        }
    )
    rows, skipped = score_interesting_slices(
        df,
        source_ref="src",
        value_column="value",
        axes=["a", "b"],
        threshold=2.0,
        limit=None,
        max_groups=50_000,
    )
    assert any(set(entry["axes"]) == {"a", "b"} for entry in skipped)
    assert all(entry["score"] >= 2.0 for entry in rows)
    # reason_codes report a z-score, not a raw magnitude
    flat_codes = [code for row in rows for code in row["reason_codes"]]
    assert any(code.startswith("abs_z=") for code in flat_codes)


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


def test_cross_sectional_outliers_peer_scope_groups_comparison():
    """peer_scope must actually group the comparison: a small-magnitude
    region's intra-region outlier surfaces only when compared against its
    peers, not against the whole cross-section (issue #7)."""
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "region": [*["A"] * 5, *["B"] * 5],
            "store": ["a1", "a2", "a3", "a4", "a5", "b1", "b2", "b3", "b4", "b5"],
            # A ~1 with a spike to 100; B ~100 (100x) with a spike to 10000
            "value": [1.0, 1.0, 1.0, 1.0, 100.0, 100.0, 100.0, 100.0, 100.0, 10000.0],
        }
    )
    metric = _metric(session, df, semantic_kind="segmented")

    without_peer = session.discover.cross_sectional_outliers(metric, threshold=3.0).to_pandas()
    without_stores = {json.loads(k)["store"] for k in without_peer["keys_json"]}
    # global median sits at 100: only B's 10000 spike clears, A's spike is buried
    assert "b5" in without_stores
    assert "a5" not in without_stores

    with_peer = session.discover.cross_sectional_outliers(
        metric,
        threshold=3.0,
        peer_scope=[make_ref("region", SemanticKind.DIMENSION)],
    ).to_pandas()
    peer_stores = {json.loads(k)["store"] for k in with_peer["keys_json"]}
    # each region compared internally: both A's and B's intra-region spikes surface
    assert "a5" in peer_stores
    assert "b5" in peer_stores
    # peer_scope is still recorded on each candidate
    assert json.loads(with_peer.loc[0, "peer_scope_json"]) == ["region"]
    assert with_peer["item_id"].is_unique


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
        kwargs = {"search_space": [make_ref("country", SemanticKind.DIMENSION)]}
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
    assert [aff.capability_id for aff in loaded.meta.affordances] == ["assess_quality"]
    assert list(loaded.to_pandas().columns) == list(out.to_pandas().columns)


def test_discover_dispatch_drops_dead_sensitivity_parameter():
    """sensitivity was a documented but unimplemented dead parameter; it must
    stay removed and the DiscoverSensitivity alias must not be exported
    (issue #13)."""
    import marivo.analysis as mv
    from marivo.analysis.intents.discover import _discover_dispatch

    assert "sensitivity" not in inspect.signature(_discover_dispatch).parameters
    assert not hasattr(mv, "DiscoverSensitivity")


@pytest.mark.parametrize(
    "name",
    [
        "point_anomalies",
        "period_shifts",
        "driver_axes",
        "interesting_slices",
        "interesting_windows",
        "cross_sectional_outliers",
    ],
)
def test_discover_methods_expose_limit(name):
    """Every discover objective exposes ``limit`` (issue #15)."""
    session = session_attach.get_or_create(name="demo")
    method = getattr(session.discover, name)
    assert "limit" in inspect.signature(method).parameters


def test_point_anomalies_default_limit_is_applied_and_visible():
    """Omitting limit applies the conservative default (50) and records it in
    params; no silent truncation when under the cap (issue #15)."""
    session = session_attach.get_or_create(name="demo")
    series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC"),
                "value": [0.0, 0.0, 0.0, 0.0, 10.0, -10.0, 8.0, -8.0, 5.0, -5.0],
            }
        ),
        semantic_kind="time_series",
    )
    out = session.discover.point_anomalies(series, threshold=1.0)
    assert out.meta.params["limit"] == 50
    # 4 candidates (< 50) so no truncation flag
    assert "truncated" not in out.meta.params


def test_point_anomalies_limit_truncates_by_abs_score_and_records():
    """limit truncates to top candidates by |score| and records the fact."""
    session = session_attach.get_or_create(name="demo")
    series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC"),
                "value": [0.0, 0.0, 0.0, 0.0, 10.0, -10.0, 8.0, -8.0, 5.0, -5.0],
            }
        ),
        semantic_kind="time_series",
    )
    out = session.discover.point_anomalies(series, threshold=1.0, limit=3)
    rows = out.to_pandas()
    # 4 raw candidates (|z| >= 1.0) truncated to 3, top by |z|
    assert len(rows) == 3
    assert out.meta.params["limit"] == 3
    assert out.meta.params["truncated"] is True
    assert out.meta.params["candidate_count_before_limit"] == 4
    assert out.meta.params["candidate_count"] == 3
    scores = [abs(float(s)) for s in rows["score"]]
    assert scores == sorted(scores, reverse=True)


def test_point_anomalies_limit_none_is_unbounded():
    """Explicit ``limit=None`` opts out of the default and stays unbounded."""
    session = session_attach.get_or_create(name="demo")
    series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC"),
                "value": [0.0, 0.0, 0.0, 0.0, 10.0, -10.0, 8.0, -8.0, 5.0, -5.0],
            }
        ),
        semantic_kind="time_series",
    )
    out = session.discover.point_anomalies(series, threshold=1.0, limit=None)
    rows = out.to_pandas()
    assert len(rows) == 4
    assert out.meta.params["limit"] is None
    assert "truncated" not in out.meta.params


def test_interesting_windows_limit_truncates_and_records():
    """limit applies to interesting_windows too (separate scorer path)."""
    session = session_attach.get_or_create(name="demo")
    values = [1.0] * 15 + [50.0] * 4 + [1.0] * 15 + [60.0] * 4 + [1.0] * 10
    series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=len(values), freq="D", tz="UTC"),
                "value": values,
            }
        ),
        semantic_kind="time_series",
    )
    out = session.discover.interesting_windows(series, threshold=1.5, limit=1)
    rows = out.to_pandas()
    # two spike windows, truncated to the top one by |z|
    assert len(rows) == 1
    assert out.meta.params["limit"] == 1
    assert out.meta.params["truncated"] is True
    assert out.meta.params["candidate_count_before_limit"] == 2
    assert out.meta.params["candidate_count"] == 1


@pytest.mark.parametrize("bad_limit", [0, -1, 1.5, True])
def test_limit_rejects_invalid(bad_limit):
    session = session_attach.get_or_create(name="demo")
    series = _metric(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC"),
                "value": [0.0, 0.0, 0.0, 0.0, 10.0, -10.0, 8.0, -8.0, 5.0, -5.0],
            }
        ),
        semantic_kind="time_series",
    )
    with pytest.raises(SemanticKindMismatchError):
        session.discover.point_anomalies(series, threshold=1.0, limit=bad_limit)  # type: ignore[arg-type]


def test_period_shifts_accepts_time_series_at_min_buckets():
    """7 buckets is the rolling-window floor; validation passes (issue #11)."""
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame(
            {
                "bucket": pd.date_range("2026-01-01", periods=7, freq="D", tz="UTC"),
                "delta": [0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 5.0],
            }
        ),
        semantic_kind="time_series",
    )
    out = session.discover.period_shifts(delta)  # must not raise
    assert out.meta.objective == "period_shifts"


def test_interesting_slices_panel_ranks_small_mutated_segment_first():
    """On a ragged panel a small mutated segment must outrank large average
    ones. Sum-based scoring buries it (score proportional to group size);
    mean-based scoring surfaces it (issue #9)."""
    session = session_attach.get_or_create(name="panel")
    days = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
    records: list[dict[str, object]] = []
    # four large, average segments
    for region in ["north", "south", "east", "west"]:
        records += [{"bucket": d, "region": region, "value": 10.0} for d in days]
    # one small segment with a mutation (few buckets, high value)
    records += [{"bucket": d, "region": "spike", "value": 50.0} for d in days[:3]]
    metric = _metric(session, pd.DataFrame(records), semantic_kind="panel")
    out = session.discover.interesting_slices(
        metric,
        search_space=[make_ref("region", SemanticKind.DIMENSION)],
        threshold=2.0,
    ).to_pandas()
    selectors = [json.loads(s) for s in out["selector_json"]]
    assert selectors, "expected at least one slice candidate"
    assert selectors[0].get("region") == "spike"
    # the large average segments must not clear the threshold
    assert all(sel.get("region") != "north" for sel in selectors)


def test_interesting_slices_rejects_scalar_frame():
    """scalar has no dimensions to slice; fail closed instead of returning a
    silent empty CandidateSet (issue #9)."""
    session = session_attach.get_or_create(name="demo")
    frame = _metric(session, pd.DataFrame({"value": [1.0]}), semantic_kind="scalar")
    with pytest.raises(SemanticKindMismatchError):
        session.discover.interesting_slices(frame, threshold=1.0)


def test_cross_sectional_outliers_rejects_unmaterialized_peer_scope():
    """An unmaterialized peer_scope axis fails closed instead of silently
    falling back to a global comparison (issue #7)."""
    session = session_attach.get_or_create(name="demo")
    metric = _metric(
        session,
        pd.DataFrame({"region": ["a", "b", "c"], "value": [1.0, 1.0, 100.0]}),
        semantic_kind="segmented",
    )
    with pytest.raises(SemanticKindMismatchError) as exc:
        session.discover.cross_sectional_outliers(
            metric,
            threshold=3.0,
            peer_scope=[make_ref("nonexistent", SemanticKind.DIMENSION)],
        )
    assert exc.value._context["objective"] == "cross_sectional_outliers"
    assert "nonexistent" in exc.value._context["missing_axes"]


def test_point_anomalies_seasonal_robust_resists_masking():
    """A big spike must not mask a second, moderate spike via std inflation.
    The seasonal_robust_zscore baseline (per-day-of-week median/MAD) surfaces
    both; the default global z-score misses the moderate one (issue #6)."""
    session = session_attach.get_or_create(name="demo")
    # 8 weeks starting Monday: weekday 10, weekend 2; two spikes
    days = pd.date_range("2026-01-05", periods=56, freq="D", tz="UTC")
    weekly = [10.0, 10.0, 10.0, 10.0, 10.0, 2.0, 2.0]
    values = [
        weekly[i % 7] + (100.0 if i == 20 else 0.0) + (30.0 if i == 40 else 0.0) for i in range(56)
    ]
    series = _metric(
        session,
        pd.DataFrame({"bucket": days, "value": values}),
        semantic_kind="time_series",
    )

    def candidate_days(out: pd.DataFrame) -> set[str]:
        return {pd.Timestamp(w).date().isoformat() for w in out["window_start"]}

    big_day = days[20].date().isoformat()
    mod_day = days[40].date().isoformat()

    default_out = session.discover.point_anomalies(series, threshold=3.0).to_pandas()
    assert big_day in candidate_days(default_out)
    assert mod_day not in candidate_days(default_out)  # masked by the big spike

    robust_out = session.discover.point_anomalies(
        series, threshold=3.0, strategy="seasonal_robust_zscore"
    ).to_pandas()
    robust_days = candidate_days(robust_out)
    assert big_day in robust_days
    assert mod_day in robust_days  # robust baseline surfaces the moderate spike
    # weekly seasonality is not flagged, only the two spikes
    assert robust_out["item_id"].is_unique


def test_driver_axes_rejects_unmaterialized_search_space():
    """A search_space axis not materialized in the frame fails closed instead
    of being silently dropped (issue #10)."""
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame({"country": ["US", "CA"], "delta": [10.0, -2.0]}),
        semantic_kind="segmented",
    )
    with pytest.raises(DiscoverAxisNotMaterializedError) as exc:
        session.discover.driver_axes(
            delta,
            search_space=[make_ref("nonexistent", SemanticKind.DIMENSION)],
        )
    assert exc.value._context["objective"] == "driver_axes"
    assert "nonexistent" in exc.value._context["missing_axes"]
    assert "country" in exc.value._context["available_dimension_columns"]


def test_interesting_slices_rejects_partially_unmaterialized_search_space():
    """Partial missing still fails closed; only the missing axis is reported."""
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame({"country": ["US", "CA", "DE"], "delta": [10.0, -2.0, 0.5]}),
        semantic_kind="segmented",
    )
    with pytest.raises(DiscoverAxisNotMaterializedError) as exc:
        session.discover.interesting_slices(
            delta,
            search_space=[
                make_ref("country", SemanticKind.DIMENSION),
                make_ref("nonexistent", SemanticKind.DIMENSION),
            ],
            threshold=1.0,
        )
    assert exc.value._context["objective"] == "interesting_slices"
    assert exc.value._context["missing_axes"] == ["nonexistent"]
    assert "country" in exc.value._context["available_dimension_columns"]


def test_interesting_slices_without_search_space_does_not_raise_on_missing():
    """No explicit search_space falls back to the frame's own dimensions; the
    materialized-axis check must not fire (issue #10)."""
    session = session_attach.get_or_create(name="demo")
    delta = _delta(
        session,
        pd.DataFrame({"country": ["US", "CA", "DE"], "delta": [10.0, -2.0, 0.5]}),
        semantic_kind="segmented",
    )
    out = session.discover.interesting_slices(delta, threshold=1.0)
    assert out.meta.objective == "interesting_slices"
