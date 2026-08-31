"""Component-aware compare and decompose behavior."""

from datetime import datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo._compat import UTC
from marivo.analysis.errors import (
    AttributeAdmissionBlockedError,
    ComponentDecompositionError,
    ComponentFrameMismatchError,
    ComponentFrameUnavailableError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.policies import window_bucket
from marivo.analysis.session._runtime import persist_frame
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import (
    make_test_component_contract,
    make_test_delta_contract,
    make_test_metric_contract,
)


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    datasets = tmp_path / "models" / "semantic" / "sales" / "datasets.py"
    datasets.write_text(
        datasets.read_text()
        + "\n@ms.metric(entities=[orders], additivity='non_additive', name='failure_rate')\n"
        + "def failure_rate(orders):\n"
        + "    return orders.amount.mean()\n"
    )
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
    additivity: str | None = "non_additive",
    linear_terms: tuple[tuple[str, str], ...] = (),
):
    component_map = components or {
        "numerator": "sales.failed_count",
        "denominator": "sales.total_count",
    }
    axes = {"region": {"role": "dimension", "column": "region"}}
    metric_df = pd.DataFrame(rows)
    metric = MetricFrame(
        _df=metric_df,
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
            **make_test_metric_contract(
                metric_df,
                metric_id="sales.failure_rate",
                axes=axes,
                session=session,
            ),
            axes=axes,
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="segmented",
            semantic_model="sales",
            composition={"kind": composition_kind, "components": component_map},
            additivity=additivity,  # type: ignore[arg-type]
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
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components=component_map,
                axes=axes,
            ),
            composition_kind=composition_kind,
            linear_terms=linear_terms,
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
    metric_df = pd.DataFrame(rows)
    metric = MetricFrame(
        _df=metric_df,
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
            **make_test_metric_contract(
                metric_df,
                metric_id="sales.failure_rate",
                axes=axes,
                session=session,
            ),
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
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components=component_map,
                axes=axes,
            ),
            composition_kind=composition_kind,
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

    delta = session.compare(current, baseline, alignment=window_bucket())

    assert delta.meta.additivity == "non_additive"
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


def test_decompose_rejects_non_additive_linear_composition() -> None:
    session = session_attach.get_or_create(name="demo")
    components = {"gross": "sales.gross", "refunds": "sales.refunds"}
    terms = (("+", "sales.gross"), ("-", "sales.refunds"))
    current = _component_aware_metric(
        session,
        ref="frame_current",
        rows=[{"region": "US", "failure_rate": 13.0}],
        component_rows=[{"region": "US", "gross": 15.0, "refunds": 2.0, "failure_rate": 13.0}],
        composition_kind="linear",
        components=components,
        linear_terms=terms,
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline",
        rows=[{"region": "US", "failure_rate": 9.0}],
        component_rows=[{"region": "US", "gross": 10.0, "refunds": 1.0, "failure_rate": 9.0}],
        composition_kind="linear",
        components=components,
        linear_terms=terms,
    )
    delta = session.compare(current, baseline)

    with pytest.raises(AttributeAdmissionBlockedError) as exc_info:
        session.attribute(delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])

    assert exc_info.value._context["blocker"] == "unsupported_aggregate"
    assert exc_info.value._context["composition_kind"] == "linear"


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
    baseline_df = pd.DataFrame({"region": ["NORTH"], "failure_rate": [0.10]})
    baseline_axes = {"region": {"role": "dimension", "column": "region"}}
    baseline = MetricFrame(
        _df=baseline_df,
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
            **make_test_metric_contract(
                baseline_df,
                metric_id="sales.failure_rate",
                axes=baseline_axes,
                session=session,
            ),
            axes=baseline_axes,
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
        composition_kind="weighted_mean",
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

    attribution = session.attribute(
        delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    )

    assert attribution.meta.method == "ratio_mix"
    assert attribution.meta.contribution_column == "contribution"
    df = attribution.to_pandas()
    assert list(df.columns) == [
        "region",
        "contribution",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
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
    # Contribution sum equals the overall weighted-mean change, not the
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
        composition_kind="weighted_mean",
        components={"numerator": "sales.weighted_failed", "weight": "sales.total_weight"},
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
        composition_kind="weighted_mean",
        components={"numerator": "sales.weighted_failed", "weight": "sales.total_weight"},
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(
        delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    )

    assert attribution.meta.method == "weighted_mix"
    df = attribution.to_pandas()
    assert "current_total_weight" in df.columns
    assert "baseline_total_weight" in df.columns
    assert "current_total_count" not in df.columns
    # Contribution sum equals the overall weighted-mean change.
    assert df["contribution"].sum() == pytest.approx(0.175)


@pytest.mark.parametrize(
    ("composition_kind", "components", "numerator_column", "exposure_column", "score_method"),
    [
        (
            "ratio",
            {"numerator": "sales.failed_count", "denominator": "sales.total_count"},
            "failed_count",
            "total_count",
            "denominator_exposure",
        ),
        (
            "weighted_mean",
            {"numerator": "sales.weighted_failed", "weight": "sales.total_weight"},
            "weighted_failed",
            "total_weight",
            "weight_exposure",
        ),
    ],
)
def test_attribute_component_top_k_uses_natural_exposure_scale(
    composition_kind,
    components,
    numerator_column,
    exposure_column,
    score_method,
) -> None:
    session = session_attach.get_or_create(name="demo")

    def component_rows(north_numerator: float, south_numerator: float):
        return [
            {
                "region": "NORTH",
                numerator_column: north_numerator,
                exposure_column: 1.0,
                "failure_rate": north_numerator,
            },
            {
                "region": "SOUTH",
                numerator_column: south_numerator,
                exposure_column: 100.0,
                "failure_rate": south_numerator / 100.0,
            },
        ]

    current = _component_aware_metric(
        session,
        ref="frame_current_top_k",
        rows=[
            {"region": "NORTH", "failure_rate": 1.0},
            {"region": "SOUTH", "failure_rate": 0.2},
        ],
        component_rows=component_rows(1.0, 20.0),
        composition_kind=composition_kind,
        components=components,
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline_top_k",
        rows=[
            {"region": "NORTH", "failure_rate": 0.5},
            {"region": "SOUTH", "failure_rate": 0.1},
        ],
        component_rows=component_rows(0.5, 10.0),
        composition_kind=composition_kind,
        components=components,
    )

    attribution = session.attribute(
        session.compare(current, baseline),
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        top_k=1,
    )

    rows = attribution.to_pandas()
    named = rows[rows["attribution_other_mask"] == 0]
    assert named["region"].tolist() == ["SOUTH"]
    assert set(rows["attribution_other_mask"]) == {0, 1}
    assert attribution.meta.top_k_selection is not None
    assert attribution.meta.top_k_selection.score_method == score_method
    assert attribution.meta.reconciliation is not None
    assert attribution.meta.reconciliation.max_abs_residual <= 1e-9


def test_decompose_weighted_mix_reconciles_new_and_churned_segments():
    session = session_attach.get_or_create(name="demo")
    components = {"numerator": "sales.weighted_failed", "weight": "sales.total_weight"}
    current = _component_aware_metric(
        session,
        ref="frame_current_one_sided",
        rows=[
            {"region": "MATCHED", "failure_rate": 0.20},
            {"region": "NEW", "failure_rate": 0.30},
        ],
        component_rows=[
            {
                "region": "MATCHED",
                "weighted_failed": 20.0,
                "total_weight": 100.0,
                "failure_rate": 0.20,
            },
            {
                "region": "NEW",
                "weighted_failed": 30.0,
                "total_weight": 100.0,
                "failure_rate": 0.30,
            },
        ],
        composition_kind="weighted_mean",
        components=components,
    )
    baseline = _component_aware_metric(
        session,
        ref="frame_baseline_one_sided",
        rows=[
            {"region": "MATCHED", "failure_rate": 0.10},
            {"region": "CHURNED", "failure_rate": 0.20},
        ],
        component_rows=[
            {
                "region": "MATCHED",
                "weighted_failed": 10.0,
                "total_weight": 100.0,
                "failure_rate": 0.10,
            },
            {
                "region": "CHURNED",
                "weighted_failed": 20.0,
                "total_weight": 100.0,
                "failure_rate": 0.20,
            },
        ],
        composition_kind="weighted_mean",
        components=components,
    )
    delta = session.compare(current, baseline)

    attribution = session.attribute(
        delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    )

    df = attribution.to_pandas().set_index("region")
    assert df["contribution"].notna().all()
    assert df.loc["MATCHED", "contribution"] == pytest.approx(0.05)
    assert df.loc["NEW", "contribution"] == pytest.approx(0.15)
    assert df.loc["CHURNED", "contribution"] == pytest.approx(-0.10)
    assert df["contribution"].sum() == pytest.approx(0.10)
    assert df.loc["NEW", "share_of_total_delta"] == pytest.approx(1.5)
    assert df.loc["NEW", "share_of_positive_pool"] == pytest.approx(0.75)
    assert df.loc["CHURNED", "share_of_negative_pool"] == pytest.approx(1.0)
    assert "pct_contribution" not in df.columns
    reconciliation = attribution.meta.reconciliation
    assert reconciliation is not None
    assert reconciliation.status == "reconciled"
    assert reconciliation.total_delta == pytest.approx(0.10)
    assert reconciliation.contribution_sum == pytest.approx(0.10)
    assert reconciliation.one_sided_contribution_sum == pytest.approx(0.05)
    assert reconciliation.unattributed_contribution_sum == pytest.approx(0.0, abs=1e-12)
    assert reconciliation.residual == pytest.approx(0.0, abs=1e-12)
    assert "one_sided_contribution_sum=0.05" in attribution.render()
    assert attribution.evidence_status == "complete"


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
        session.attribute(delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])


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

    delta = session.compare(current, baseline, alignment=window_bucket())

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

    delta = session.compare(current, baseline, alignment=window_bucket())

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

    attribution = session.attribute(
        delta, axes=[make_ref("sales.orders.bucket_start", SemanticKind.DIMENSION)]
    )

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

    attribution = session.attribute(
        delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)]
    )

    df = attribution.to_pandas()
    assert list(df.columns) == [
        "bucket_start",
        "region",
        "contribution",
        "share_of_total_delta",
        "share_of_positive_pool",
        "share_of_negative_pool",
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
            make_ref("sales.orders.category", SemanticKind.DIMENSION),
            make_ref("sales.orders.channel", SemanticKind.DIMENSION),
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


def test_decompose_component_aware_ratio_delta_hierarchy_paths_are_populated() -> None:
    """Component-aware hierarchy decomposition must produce real attribution paths.

    Regression for the P1 found in review: ``_component_multi_axis_output`` built
    the attribution_path column with ``DataFrame.apply(..., axis=1)`` dropped, so
    rows were iterated per-column and the whole column collapsed to NaN.
    """
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
        ref="frame_current_hierarchy",
        rows=current_rows,
        component_rows=current_components,
        axes=axes,
        semantic_kind="segmented",
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_hierarchy",
        rows=baseline_rows,
        component_rows=baseline_components,
        axes=axes,
        semantic_kind="segmented",
    )

    delta = session.compare(current, baseline)
    attribution = session.attribute(
        delta,
        axes=[
            make_ref("sales.orders.category", SemanticKind.DIMENSION),
            make_ref("sales.orders.channel", SemanticKind.DIMENSION),
        ],
        mode="hierarchy",
    )

    df = attribution.to_pandas()
    assert attribution.meta.method == "ratio_mix"
    assert attribution.meta.driver_field == "attribution_path"
    assert attribution.attribution_mode == "hierarchy"
    # The persisted path must be non-empty; a NaN attribution_path means the
    # component path was built by iterating columns instead of rows.
    assert not df["attribution_path"].isna().any()
    assert set(df["attribution_path"]) == {
        "B",
        "A",
        "B > store",
        "A > online",
        "A > store",
        "B > online",
    }
    assert {"contribution", "value_effect", "mix_effect", "residual"}.issubset(df.columns)
    # Each hierarchy level reconciles to the full delta (0.125); level-2 rows
    # are the deepest decomposition of the parent rows.
    assert df["contribution"].sum() == pytest.approx(0.25)
    assert df[df["attribution_level"] == 2]["contribution"].sum() == pytest.approx(0.125)


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
            **make_test_delta_contract("sales.failure_rate", session=session),
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
            alignment={
                "kind": "day_of_week",
                "within": {"kind": "builtin", "unit": "month", "count": 1},
                "unmatched": "fail",
            },
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
            **make_test_component_contract(
                metric_id="sales.failure_rate",
                components={
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
                axes=axes,
            ),
            composition_kind="ratio",
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    component.meta = persist_frame(session, component)
    compared.meta = compared.meta.model_copy(update={"component_ref": component.ref})
    compared.meta = persist_frame(session, compared)

    attribution = session.attribute(
        compared, axes=[make_ref("sales.orders.bucket_start", SemanticKind.DIMENSION)]
    )

    assert "bucket_start_a" in attribution.to_pandas().columns
    assert attribution.meta.driver_field == "bucket_start_a"


def test_decompose_component_ratio_rejects_reserved_axis_column(
    semantic_project_factory,
) -> None:
    """Component-aware ratio path must fail closed when the axis column collides
    with an attribution protocol column (issue #40).

    Previously the reserved-name check only ran in the single-axis additive
    path, so a component ratio/weighted delta with a ``contribution`` axis
    reached the reconciliation step and surfaced a raw pandas ``TypeError``.
    """

    semantic_project_factory(
        {
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity("
                "name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.dimension(entity=orders)\n"
                "def contribution(orders):\n"
                "    return orders.contribution\n"
                "@ms.metric(entities=[orders], additivity='non_additive', "
                "name='failure_rate')\n"
                "def failure_rate(orders):\n"
                "    return orders.amount.mean()\n"
            ),
        }
    )
    session = session_attach.get_or_create(name="demo")
    current = _component_aware_metric_with_axes(
        session,
        ref="frame_current",
        rows=[
            {"contribution": "NORTH", "failure_rate": 0.25},
            {"contribution": "SOUTH", "failure_rate": 0.50},
        ],
        component_rows=[
            {
                "contribution": "NORTH",
                "failed_count": 25.0,
                "total_count": 100.0,
                "failure_rate": 0.25,
            },
            {
                "contribution": "SOUTH",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
        axes={"contribution": {"role": "dimension", "column": "contribution"}},
        semantic_kind="segmented",
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline",
        rows=[
            {"contribution": "NORTH", "failure_rate": 0.10},
            {"contribution": "SOUTH", "failure_rate": 0.40},
        ],
        component_rows=[
            {
                "contribution": "NORTH",
                "failed_count": 10.0,
                "total_count": 100.0,
                "failure_rate": 0.10,
            },
            {
                "contribution": "SOUTH",
                "failed_count": 20.0,
                "total_count": 50.0,
                "failure_rate": 0.40,
            },
        ],
        axes={"contribution": {"role": "dimension", "column": "contribution"}},
        semantic_kind="segmented",
    )
    delta = session.compare(current, baseline)

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            delta,
            axes=[make_ref("sales.orders.contribution", SemanticKind.DIMENSION)],
        )

    error = exc_info.value
    assert error._context["reason"] == "reserved_axis_column"
    assert error._context["axis_column"] == "contribution"
    assert error.location == "session.attribute axes"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"
    # Failing closed must not leave a partially-persisted attribution artifact.
    failed = session.runs(status="failed", capability_id="attribute").items
    assert len(failed) == 1
    assert failed[0].failure.error_type == "SemanticKindMismatchError"


def _reattach_component_frame(session, frame: MetricFrame, time_column: str) -> MetricFrame:
    """Rebuild and persist a metric's component frame with the time column
    coerced to datetime64, simulating the real-data form where a missing
    temporal coordinate survives as a genuine ``pd.NaT``."""
    component = frame.components()
    df = component._dataframe_copy()
    df[time_column] = pd.to_datetime(df[time_column])
    rebuilt = ComponentFrame(_df=df, meta=component.meta)
    rebuilt.meta = persist_frame(session, rebuilt)
    frame.meta = frame.meta.model_copy(update={"component_ref": rebuilt.ref})
    frame.meta = persist_frame(session, frame)
    return frame


def test_compare_time_series_component_naT_time_key_does_not_crash():
    """Issue #75: a component row whose temporal key is NaT must not crash
    component alignment with ``ValueError: NaTType does not support time``.

    Previously ``temporal_key`` fed a datetime64 NaT straight into
    ``pd.Timestamp(value).time()`` which raises for NaT. The component
    temporal join must treat a missing temporal coordinate as an
    un-matchable key (dropped from the join) rather than blowing up.
    """
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
        ref="frame_current_nat",
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
                # Missing temporal coordinate for this component row.
                "bucket_start": pd.NaT,
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_nat",
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

    # Coerce the component time columns to datetime64 so the missing row
    # survives as a genuine NaT — the exact form that previously crashed.
    current = _reattach_component_frame(session, current, "bucket_start")
    baseline = _reattach_component_frame(session, baseline, "bucket_start")

    delta = session.compare(current, baseline, alignment=window_bucket())

    # The delta itself must still be produced; the unmatched NaT row is
    # dropped from the component projection instead of crashing.
    assert delta.meta.component_ref is not None
    component_df = delta.components().to_pandas()
    # Only the pair-matching rows survive the temporal join.
    assert not component_df.empty
    assert "__component_time" not in component_df.columns
    assert "__temporal_join_key" not in component_df.columns


def test_attribute_panel_component_naT_time_key_raises_typed_error():
    """Issue #75: attribute over a component-aware panel delta whose component
    rows carry a NaT temporal key must surface a structured typed error, not a
    raw ``ValueError: NaTType does not support time``.

    Mirrors the q04/q05 scenario. The NaT component row is un-matchable and is
    dropped by the temporal join; when that leaves the decomposition unable to
    form every contribution row, attribute must fail with the typed
    ``ComponentDecompositionError`` (with kind/repair) instead of leaking the
    pandas/stdlib crash.
    """
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
        ref="frame_current_attr_nat",
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
                # Missing temporal coordinate for this component row.
                "bucket_start": pd.NaT,
                "region": "SOUTH",
                "failed_count": 50.0,
                "total_count": 100.0,
                "failure_rate": 0.50,
            },
        ],
    )
    baseline = _component_aware_metric_with_axes(
        session,
        ref="frame_baseline_attr_nat",
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

    current = _reattach_component_frame(session, current, "bucket_start")
    baseline = _reattach_component_frame(session, baseline, "bucket_start")

    delta = session.compare(current, baseline)

    with pytest.raises(ComponentDecompositionError) as exc_info:
        session.attribute(delta, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])
    # The structured typed error must carry kind + context + repair, not leak
    # the raw ``NaTType does not support time`` ValueError.
    assert exc_info.value.kind == "ComponentDecomposition"
    assert exc_info.value.repair is not None
    assert exc_info.value.repair.kind == "inspect"
    assert "contribution" in exc_info.value.message
