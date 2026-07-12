"""Component-aware compare and decompose behavior."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo.analysis.errors import (
    ComponentDecompositionError,
    ComponentFrameMismatchError,
    ComponentFrameUnavailableError,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.session._runtime import persist_frame
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)


def _component_aware_metric(
    session,
    *,
    ref: str,
    rows: list[dict[str, object]],
    component_rows: list[dict[str, object]],
    composition_kind: str = "ratio",
    components: dict[str, str] | None = None,
):
    component_map = components or {
        "numerator": "sales.failed_count",
        "denominator": "sales.total_count",
    }
    axes = {"region": {"role": "dimension", "column": "region"}}
    metric = MetricFrame(
        _df=pd.DataFrame(rows),
        meta=MetricFrameMeta(
            ref=ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(rows),
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes=axes,
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="segmented",
            semantic_model="sales",
            composition={"kind": composition_kind, "components": component_map},
        ),
    )
    metric.meta = persist_frame(session, metric)
    component = ComponentFrame(
        _df=pd.DataFrame(component_rows),
        meta=ComponentFrameMeta(
            ref=f"{ref}_components",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(component_rows),
            byte_size=0,
            lineage=Lineage(),
            parent_ref=metric.ref,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            composition_kind=composition_kind,
            components=component_map,
            axes=axes,
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    metric.meta = metric.meta.model_copy(update={"component_ref": component.ref})
    metric.meta = persist_frame(session, metric)
    return metric


def _component_aware_metric_with_axes(
    session,
    *,
    ref: str,
    rows: list[dict[str, object]],
    component_rows: list[dict[str, object]],
    axes: dict[str, object],
    semantic_kind: str,
    window: dict[str, object] | None = None,
    composition_kind: str = "ratio",
    components: dict[str, str] | None = None,
):
    component_map = components or {
        "numerator": "sales.failed_count",
        "denominator": "sales.total_count",
    }
    metric = MetricFrame(
        _df=pd.DataFrame(rows),
        meta=MetricFrameMeta(
            ref=ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(rows),
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes=axes,
            measure={"name": "failure_rate"},
            window=window,
            where={},
            semantic_kind=semantic_kind,
            semantic_model="sales",
            composition={"kind": composition_kind, "components": component_map},
        ),
    )
    metric.meta = persist_frame(session, metric)
    component = ComponentFrame(
        _df=pd.DataFrame(component_rows),
        meta=ComponentFrameMeta(
            ref=f"{ref}_components",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=len(component_rows),
            byte_size=0,
            lineage=Lineage(),
            parent_ref=metric.ref,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            composition_kind=composition_kind,
            components=component_map,
            axes=axes,
            semantic_kind=semantic_kind,
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    metric.meta = metric.meta.model_copy(update={"component_ref": component.ref})
    metric.meta = persist_frame(session, metric)
    return metric


def test_compare_segmented_ratio_persists_clean_delta_and_component_delta():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[
            {"region": "NORTH", "failure_rate": 0.25},
            {"region": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {"region": "NORTH", "failed_count": 25.0, "total_count": 100.0, "failure_rate": 0.25},
            {"region": "SOUTH", "failed_count": 50.0, "total_count": 100.0, "failure_rate": 0.50},
        ],
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[
            {"region": "NORTH", "failure_rate": 0.10},
            {"region": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {"region": "NORTH", "failed_count": 10.0, "total_count": 100.0, "failure_rate": 0.10},
            {"region": "SOUTH", "failed_count": 20.0, "total_count": 50.0, "failure_rate": 0.40},
        ],
    )

    delta = session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))

    assert delta.meta.component_ref is not None
    assert delta.meta.composition == {
        "kind": "ratio",
        "components": {
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
    }
    assert list(delta.to_pandas().columns) == [
        "region",
        "presence_status",
        "current",
        "baseline",
        "delta",
        "pct_change",
        "pct_change_status",
    ]
    components = delta.components()
    assert components.meta.parent_ref == delta.ref
    assert components.meta.parent_kind == "delta_frame"
    component_df = components.to_pandas()
    assert list(component_df.columns) == [
        "region",
        "current_failed_count",
        "baseline_failed_count",
        "delta_failed_count",
        "current_total_count",
        "baseline_total_count",
        "delta_total_count",
        "current_failure_rate",
        "baseline_failure_rate",
        "delta_failure_rate",
    ]
    north = component_df.set_index("region").loc["NORTH"]
    assert north["current_failed_count"] == pytest.approx(25.0)
    assert north["baseline_failed_count"] == pytest.approx(10.0)
    assert north["delta_failed_count"] == pytest.approx(15.0)
    assert north["current_failure_rate"] == pytest.approx(0.25)
    assert north["baseline_failure_rate"] == pytest.approx(0.10)
    assert north["delta_failure_rate"] == pytest.approx(0.15)


def test_compare_component_aware_metric_missing_component_frame_fails_closed():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[{"region": "NORTH", "failure_rate": 0.25}],
        component_rows=[
            {"region": "NORTH", "failed_count": 25.0, "total_count": 100.0, "failure_rate": 0.25}
        ],
    )
    baseline = MetricFrame(
        _df=pd.DataFrame({"region": ["NORTH"], "failure_rate": [0.10]}),
        meta=MetricFrameMeta(
            ref="frame_baseline",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes={"region": {"role": "dimension", "column": "region"}},
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="segmented",
            semantic_model="sales",
            composition={
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        ),
    )
    baseline.meta = persist_frame(session, baseline)

    with pytest.raises(ComponentFrameUnavailableError):
        session.compare(current, baseline)


def test_compare_component_frame_metadata_mismatch_fails_closed():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[{"region": "NORTH", "failure_rate": 0.25}],
        component_rows=[
            {"region": "NORTH", "failed_count": 25.0, "total_count": 100.0, "failure_rate": 0.25}
        ],
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[{"region": "NORTH", "failure_rate": 0.10}],
        component_rows=[
            {
                "region": "NORTH",
                "weighted_failed": 10.0,
                "total_weight": 100.0,
                "failure_rate": 0.10,
            }
        ],
        composition_kind="weighted_average",
        components={"numerator": "sales.failed_count", "weight": "sales.total_count"},
    )

    with pytest.raises(ComponentFrameMismatchError):
        session.compare(current, baseline)


def test_decompose_component_aware_ratio_delta_emits_value_and_mix_effects():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[
            {"region": "NORTH", "failure_rate": 0.25},
            {"region": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {"region": "NORTH", "failed_count": 25.0, "total_count": 100.0, "failure_rate": 0.25},
            {"region": "SOUTH", "failed_count": 50.0, "total_count": 100.0, "failure_rate": 0.50},
        ],
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[
            {"region": "NORTH", "failure_rate": 0.10},
            {"region": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {"region": "NORTH", "failed_count": 10.0, "total_count": 100.0, "failure_rate": 0.10},
            {"region": "SOUTH", "failed_count": 20.0, "total_count": 50.0, "failure_rate": 0.40},
        ],
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(delta, axes=[make_ref("region", SemanticKind.DIMENSION)])

    assert attribution.meta.method == "ratio_mix"
    assert attribution.meta.contribution_column == "contribution"
    df = attribution.to_pandas()
    assert list(df.columns) == [
        "region",
        "contribution",
        "pct_contribution",
        "value_effect",
        "mix_effect",
        "residual",
        "current_failed_count",
        "baseline_failed_count",
        "current_total_count",
        "baseline_total_count",
        "current_failure_rate",
        "baseline_failure_rate",
        "current_share",
        "baseline_share",
        "rank",
    ]
    by_region = df.set_index("region")
    assert by_region.loc["NORTH", "current_share"] == pytest.approx(0.5)
    assert by_region.loc["NORTH", "baseline_share"] == pytest.approx(2.0 / 3.0)
    assert by_region.loc["NORTH", "contribution"] == pytest.approx(0.05833333333333332)
    assert by_region.loc["NORTH", "value_effect"] == pytest.approx(0.075)
    assert by_region.loc["NORTH", "mix_effect"] == pytest.approx(-0.016666666666666663)
    assert by_region.loc["NORTH", "residual"] == pytest.approx(0.0)
    # Contribution sum equals the overall weighted-average change, not the
    # per-row delta sum.  overall_current = 75/200 = 0.375, overall_baseline = 30/150 = 0.2.
    assert df["contribution"].sum() == pytest.approx(0.175)
    assert sorted(df["rank"].tolist()) == [1, 2]


def test_decompose_component_aware_weighted_delta_uses_weight_share():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[
            {"region": "NORTH", "failure_rate": 0.25},
            {"region": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "region": "NORTH",
                "weighted_failed": 25.0,
                "total_weight": 100.0,
                "failure_rate": 0.25,
            },
            {
                "region": "SOUTH",
                "weighted_failed": 50.0,
                "total_weight": 100.0,
                "failure_rate": 0.50,
            },
        ],
        composition_kind="weighted_average",
        components={"value": "sales.weighted_failed", "weight": "sales.total_weight"},
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[
            {"region": "NORTH", "failure_rate": 0.10},
            {"region": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "region": "NORTH",
                "weighted_failed": 10.0,
                "total_weight": 100.0,
                "failure_rate": 0.10,
            },
            {
                "region": "SOUTH",
                "weighted_failed": 20.0,
                "total_weight": 50.0,
                "failure_rate": 0.40,
            },
        ],
        composition_kind="weighted_average",
        components={"value": "sales.weighted_failed", "weight": "sales.total_weight"},
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(delta, axes=[make_ref("region", SemanticKind.DIMENSION)])

    assert attribution.meta.method == "weighted_mix"
    df = attribution.to_pandas()
    assert "current_total_weight" in df.columns
    assert "baseline_total_weight" in df.columns
    assert "current_total_count" not in df.columns
    # Contribution sum equals the overall weighted-average change.
    assert df["contribution"].sum() == pytest.approx(0.175)


def test_decompose_component_aware_ratio_with_no_valid_denominators_raises():
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[{"region": "NORTH", "failure_rate": float("nan")}],
        component_rows=[
            {
                "region": "NORTH",
                "failed_count": 1.0,
                "total_count": 0.0,
                "failure_rate": float("nan"),
            }
        ],
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[{"region": "NORTH", "failure_rate": float("nan")}],
        component_rows=[
            {
                "region": "NORTH",
                "failed_count": 1.0,
                "total_count": 0.0,
                "failure_rate": float("nan"),
            }
        ],
    )
    delta = session.compare(current, baseline)

    with pytest.raises(ComponentDecompositionError):
        session.attribute(delta, axes=[make_ref("region", SemanticKind.DIMENSION)])


def test_compare_time_series_ratio_window_bucket_persists_component_delta():
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    }
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current_ts",
        semantic_kind="time_series",
        axes=axes,
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        rows=[
            {"bucket_start": "2026-07-01", "failure_rate": 0.25},
            {"bucket_start": "2026-07-02", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "bucket_start": "2026-07-01",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "bucket_start": "2026-07-02",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_ts",
        semantic_kind="time_series",
        axes=axes,
        window={"start": "2026-06-24", "end": "2026-06-26", "grain": "day"},
        rows=[
            {"bucket_start": "2026-06-24", "failure_rate": 0.10},
            {"bucket_start": "2026-06-25", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "bucket_start": "2026-06-24",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
            {
                "bucket_start": "2026-06-25",
                "failed_count": 20.0,
                "total_count": 50.0,
                "failure_rate": 0.40,
            },
        ],
    )

    delta = session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))

    assert delta.meta.component_ref is not None
    component_df = delta.components().to_pandas()
    assert list(component_df.columns) == [
        "bucket_start",
        "bucket_start_b",
        "current_failed_count",
        "baseline_failed_count",
        "delta_failed_count",
        "current_total_count",
        "baseline_total_count",
        "delta_total_count",
        "current_failure_rate",
        "baseline_failure_rate",
        "delta_failure_rate",
    ]
    first = component_df.iloc[0]
    assert str(first["bucket_start"]) == "2026-07-01"
    assert str(first["bucket_start_b"]) == "2026-06-24"
    assert first["current_failed_count"] == pytest.approx(25.0)
    assert first["baseline_failed_count"] == pytest.approx(10.0)


def test_compare_panel_ratio_window_bucket_persists_component_delta():
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
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current_panel",
        semantic_kind="panel",
        axes=axes,
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        rows=[
            {"bucket_start": "2026-07-01", "region": "NORTH", "failure_rate": 0.25},
            {"bucket_start": "2026-07-01", "region": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "bucket_start": "2026-07-01",
                "region": "NORTH",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "bucket_start": "2026-07-01",
                "region": "SOUTH",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_panel",
        semantic_kind="panel",
        axes=axes,
        window={"start": "2026-06-24", "end": "2026-06-25", "grain": "day"},
        rows=[
            {"bucket_start": "2026-06-24", "region": "NORTH", "failure_rate": 0.10},
            {"bucket_start": "2026-06-24", "region": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "bucket_start": "2026-06-24",
                "region": "NORTH",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
            {
                "bucket_start": "2026-06-24",
                "region": "SOUTH",
                "failed_count": 20.0,
                "total_count": 50.0,
                "failure_rate": 0.40,
            },
        ],
    )

    delta = session.compare(current, baseline, alignment=AlignmentPolicy(kind="window_bucket"))

    component_df = delta.components().to_pandas()
    assert {"bucket_start", "bucket_start_b", "region"}.issubset(component_df.columns)
    north_data = component_df[component_df["region"] == "NORTH"].dropna(
        subset=["current_total_count"]
    )
    south_data = component_df[component_df["region"] == "SOUTH"].dropna(
        subset=["baseline_total_count"]
    )
    assert north_data.iloc[0]["current_total_count"] == pytest.approx(100.0)
    assert south_data.iloc[0]["baseline_total_count"] == pytest.approx(50.0)


def test_decompose_component_aware_time_series_ratio_delta_by_bucket():
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    }
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current_ts_decomp",
        semantic_kind="time_series",
        axes=axes,
        window={"start": "2026-07-01", "end": "2026-07-03", "grain": "day"},
        rows=[
            {"bucket_start": "2026-07-01", "failure_rate": 0.25},
            {"bucket_start": "2026-07-02", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "bucket_start": "2026-07-01",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "bucket_start": "2026-07-02",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_ts_decomp",
        semantic_kind="time_series",
        axes=axes,
        window={"start": "2026-06-24", "end": "2026-06-26", "grain": "day"},
        rows=[
            {"bucket_start": "2026-06-24", "failure_rate": 0.10},
            {"bucket_start": "2026-06-25", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "bucket_start": "2026-06-24",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
            {
                "bucket_start": "2026-06-25",
                "failed_count": 20.0,
                "total_count": 50.0,
                "failure_rate": 0.40,
            },
        ],
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(delta, axes=[make_ref("bucket_start", SemanticKind.DIMENSION)])

    assert attribution.meta.method == "ratio_mix"
    df = attribution.to_pandas()
    assert "bucket_start" in df.columns
    assert "value_effect" in df.columns
    assert "mix_effect" in df.columns
    assert df["contribution"].sum() == pytest.approx(0.175)
    assert sorted(df["rank"].tolist()) == [1, 2]


def test_decompose_component_aware_panel_ratio_delta_per_bucket():
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
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current_panel_decomp",
        semantic_kind="panel",
        axes=axes,
        window={"start": "2026-07-01", "end": "2026-07-02", "grain": "day"},
        rows=[
            {"bucket_start": "2026-07-01", "region": "NORTH", "failure_rate": 0.25},
            {"bucket_start": "2026-07-01", "region": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "bucket_start": "2026-07-01",
                "region": "NORTH",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "bucket_start": "2026-07-01",
                "region": "SOUTH",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_panel_decomp",
        semantic_kind="panel",
        axes=axes,
        window={"start": "2026-06-24", "end": "2026-06-25", "grain": "day"},
        rows=[
            {"bucket_start": "2026-06-24", "region": "NORTH", "failure_rate": 0.10},
            {"bucket_start": "2026-06-24", "region": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "bucket_start": "2026-06-24",
                "region": "NORTH",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
            {
                "bucket_start": "2026-06-24",
                "region": "SOUTH",
                "failed_count": 20.0,
                "total_count": 50.0,
                "failure_rate": 0.40,
            },
        ],
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(delta, axes=[make_ref("region", SemanticKind.DIMENSION)])

    df = attribution.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "contribution",
        "pct_contribution",
        "value_effect",
        "mix_effect",
        "residual",
        "current_failed_count",
        "baseline_failed_count",
        "current_total_count",
        "baseline_total_count",
        "current_failure_rate",
        "baseline_failure_rate",
        "current_share",
        "baseline_share",
        "rank",
    ]
    for _, bucket_df in df.groupby("bucket_start", sort=False):
        assert sorted(bucket_df["rank"].tolist()) == list(range(1, len(bucket_df) + 1))
        assert bucket_df["contribution"].sum() == pytest.approx(0.175)


def test_decompose_component_aware_ratio_delta_by_axis_combination():
    session = session_attach.get_or_create(name="demo")
    axes = {
        "category": {"role": "dimension", "column": "category"},
        "channel": {"role": "dimension", "column": "channel"},
    }
    current_rows = [
        {"category": "A", "channel": "online", "failure_rate": 0.20},
        {"category": "A", "channel": "store", "failure_rate": 0.30},
        {"category": "B", "channel": "online", "failure_rate": 0.30},
        {"category": "B", "channel": "store", "failure_rate": 0.40},
    ]
    current_components = [
        {
            "category": "A",
            "channel": "online",
            "failed_count": 20.0,
            "total_count": 100.0,
            "failure_rate": 0.20,
        },
        {
            "category": "A",
            "channel": "store",
            "failed_count": 30.0,
            "total_count": 100.0,
            "failure_rate": 0.30,
        },
        {
            "category": "B",
            "channel": "online",
            "failed_count": 30.0,
            "total_count": 100.0,
            "failure_rate": 0.30,
        },
        {
            "category": "B",
            "channel": "store",
            "failed_count": 40.0,
            "total_count": 100.0,
            "failure_rate": 0.40,
        },
    ]
    baseline_rows = [
        {"category": "A", "channel": "online", "failure_rate": 0.10},
        {"category": "A", "channel": "store", "failure_rate": 0.20},
        {"category": "B", "channel": "online", "failure_rate": 0.20},
        {"category": "B", "channel": "store", "failure_rate": 0.20},
    ]
    baseline_components = [
        {
            "category": "A",
            "channel": "online",
            "failed_count": 10.0,
            "total_count": 100.0,
            "failure_rate": 0.10,
        },
        {
            "category": "A",
            "channel": "store",
            "failed_count": 20.0,
            "total_count": 100.0,
            "failure_rate": 0.20,
        },
        {
            "category": "B",
            "channel": "online",
            "failed_count": 20.0,
            "total_count": 100.0,
            "failure_rate": 0.20,
        },
        {
            "category": "B",
            "channel": "store",
            "failed_count": 20.0,
            "total_count": 100.0,
            "failure_rate": 0.20,
        },
    ]
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current_combination",
        rows=current_rows,
        component_rows=current_components,
        axes=axes,
        semantic_kind="segmented",
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_combination",
        rows=baseline_rows,
        component_rows=baseline_components,
        axes=axes,
        semantic_kind="segmented",
    )

    delta = session.compare(current, baseline)
    attribution = session.attribute(
        delta,
        axes=[
            make_ref("category", SemanticKind.DIMENSION),
            make_ref("channel", SemanticKind.DIMENSION),
        ],
        mode="joint",
    )

    df = attribution.to_pandas()
    assert attribution.meta.method == "ratio_mix"
    assert attribution.meta.driver_field is None
    assert set(df[["category", "channel"]].itertuples(index=False, name=None)) == {
        ("B", "store"),
        ("A", "store"),
        ("A", "online"),
        ("B", "online"),
    }
    assert {"contribution", "value_effect", "mix_effect", "residual"}.issubset(df.columns)
    assert df["contribution"].sum() == pytest.approx(0.125)
    assert df["residual"].abs().max() == pytest.approx(0.0)


def test_decompose_calendar_time_series_ratio_accepts_bucket_start_alias():
    session = session_attach.get_or_create(name="demo")
    axes = {
        "time": {
            "role": "time",
            "column": "bucket_start",
            "grain": "day",
            "time_dimension": "order_date",
        }
    }
    compared = DeltaFrame(
        _df=pd.DataFrame(
            [
                {
                    "align_key": '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
                    "align_quality": "exact",
                    "bucket_start_a": "2026-05-05",
                    "bucket_start_b": "2026-04-07",
                    "current": 0.25,
                    "baseline": 0.10,
                    "delta": 0.15,
                    "pct_change": 1.5,
                }
            ]
        ),
        meta=DeltaFrameMeta(
            ref="frame_calendar_delta",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_compare",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "dow_aligned", "axes": axes},
            semantic_kind="time_series",
            semantic_model="sales",
            composition={
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        ),
    )
    compared.meta = persist_frame(session, compared)
    component = ComponentFrame(
        _df=pd.DataFrame(
            [
                {
                    "align_key": '{"kind":"dow","iso_weekday":2,"period_week_offset":0}',
                    "align_quality": "exact",
                    "bucket_start_a": "2026-05-05",
                    "bucket_start_b": "2026-04-07",
                    "current_failed_count": 25.0,
                    "baseline_failed_count": 10.0,
                    "delta_failed_count": 15.0,
                    "current_total_count": 100.0,
                    "baseline_total_count": 100.0,
                    "delta_total_count": 0.0,
                    "current_failure_rate": 0.25,
                    "baseline_failure_rate": 0.10,
                    "delta_failure_rate": 0.15,
                }
            ]
        ),
        meta=ComponentFrameMeta(
            ref="frame_calendar_delta_components",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_compare",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref=compared.ref,
            parent_kind="delta_frame",
            metric_id="sales.failure_rate",
            composition_kind="ratio",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes=axes,
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    compared.meta = compared.meta.model_copy(update={"component_ref": component.ref})
    compared.meta = persist_frame(session, compared)

    attribution = session.attribute(
        compared, axes=[make_ref("bucket_start", SemanticKind.DIMENSION)]
    )

    assert "bucket_start_a" in attribution.to_pandas().columns
    assert attribution.meta.driver_field == "bucket_start_a"
