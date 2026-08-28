"""session.attribute public attribution operator."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo._compat import UTC
from marivo.analysis.errors import (
    ArtifactQualityError,
    AttributionMaterializationError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames._quality import evaluate_frame_quality
from marivo.analysis.frames._quality_checks import run_attribution_checks
from marivo.analysis.frames.attribution import AttributionFrame, validate_generic_attribution_rows
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.semantic.catalog import SemanticKind
from tests.conftest import bootstrap_sales_project
from tests.ref_helpers import make_ref
from tests.shared_fixtures import make_metric_frame, make_test_delta_contract


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    bootstrap_sales_project(tmp_path)
    yield


def _now() -> datetime:
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _delta(
    session: mv.Session,
    df: pd.DataFrame,
    *,
    semantic_kind: str = "segmented",
    additivity: str | None = "additive",
) -> DeltaFrame:
    meta = DeltaFrameMeta(
        **make_test_delta_contract("sales.revenue"),
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_compare",
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_compare",
                    inputs=["frame_current", "frame_baseline"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_current",
        source_baseline_ref="frame_baseline",
        alignment={
            "kind": "window_bucket",
            "axes": {
                "region": {
                    "role": "dimension",
                    "column": "region",
                    "ref": "sales.orders.region",
                },
                "platform": {
                    "role": "dimension",
                    "column": "platform",
                    "ref": "sales.orders.platform",
                },
            },
        },
        semantic_kind=semantic_kind,  # type: ignore[arg-type]
        semantic_model="sales",
        additivity=additivity,  # type: ignore[arg-type]
    )
    return DeltaFrame(_df=df, meta=meta)


def test_attribute_single_axis_returns_attribution_frame_with_public_lineage() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN", "US"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.kind == "attribution_frame"
    assert out.lineage.steps[-1].intent == "attribute"
    assert out.meta.method == "sum"
    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert out.meta.driver_field == "region"
    result = out.to_pandas()
    assert {
        "attribution_driver",
        "attribution_path",
        "attribution_level",
        "attribution_axis",
    }.isdisjoint(result.columns)
    assert result[["region", "contribution"]].to_dict("records") == [
        {"region": "US", "contribution": 14.0},
        {"region": "CN", "contribution": -2.0},
    ]
    merged = frame.to_pandas().merge(result, on="region")
    assert list(merged["region"]) == ["US", "CN", "US"]
    loaded = session.get_frame(out.ref)
    assert loaded.meta.driver_field == "region"
    assert list(loaded.to_pandas().columns) == list(result.columns)
    contract = out.contract()
    assert any(item.capability_id == "BaseFrame.quality_report" for item in contract.affordances)

    quality = out.quality_report()
    assert quality is not None
    assert quality.meta.report_shape == "attribution"
    assert quality.meta.target_metric_id == "sales.revenue"
    assert quality.meta.target_semantic_kind == "segmented"
    assert quality.meta.overall_status == "ok"
    assert quality.evidence_digest is None
    assert quality.to_pandas()["metric_id"].isna().all()
    assert set(quality.to_pandas()["check_id"]) == {
        "attribution_row_count",
        "attribution_row_contract",
        "attribution_contribution_values",
        "attribution_reconciliation",
    }
    recovered_quality = loaded.quality_report()
    assert recovered_quality is not None
    assert recovered_quality.meta.report_shape == "attribution"
    assert quality.meta.produced_by_job == out.meta.produced_by_job


def test_attribute_single_axis_top_k_preserves_null_and_real_other_identity() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["A", "Other", None, "C", "D"],
                "current": [100.0, 80.0, 60.0, 1.0, 0.0],
                "baseline": [90.0, 70.0, 60.0, 0.0, 50.0],
                "delta": [10.0, 10.0, 0.0, 1.0, -50.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        top_k=3,
    )

    rows = out.to_pandas().sort_values(
        ["attribution_other_mask", "contribution"], ascending=[True, False]
    )
    assert out.meta.top_k_selection is not None
    assert out.meta.top_k_selection.original_partition_count == 5
    assert out.meta.top_k_selection.effective_partition_count == 4
    assert rows.iloc[:2][["region", "attribution_other_mask", "contribution"]].to_dict(
        "records"
    ) == [
        {"region": "A", "attribution_other_mask": 0, "contribution": 10.0},
        {"region": "Other", "attribution_other_mask": 0, "contribution": 10.0},
    ]
    assert rows.iloc[2:]["region"].isna().all()
    assert rows.iloc[2:][["attribution_other_mask", "contribution"]].to_dict("records") == [
        {"attribution_other_mask": 0, "contribution": 0.0},
        {"attribution_other_mask": 1, "contribution": -49.0},
    ]
    preview_rows = list(out._preview_rows_provider())
    region_index = out.columns.index("region")
    mask_index = out.columns.index("attribution_other_mask")
    masked_preview = next(row for row in preview_rows if row[mask_index] == "1")
    assert masked_preview[region_index] == "Other"


def test_attribute_single_axis_top_k_preserves_large_integer_identities() -> None:
    session = mv.session.get_or_create(name="demo")
    first = 2**53
    second = first + 1
    third = first + 2
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": [first, second, third],
                "current": [100.0, 90.0, 1.0],
                "baseline": [0.0, 0.0, 0.0],
                "delta": [100.0, 90.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        top_k=2,
    )

    rows = out.to_pandas()
    named = rows.loc[rows["attribution_other_mask"] == 0, "region"]
    assert named.tolist() == [first, second]
    assert str(named.dtype) == "Int64"
    assert rows.loc[rows["attribution_other_mask"] == 1, "region"].isna().all()
    assert out.meta.top_k_selection is not None
    assert out.meta.top_k_selection.effective_partition_count == 3


def test_attribute_hierarchy_top_k_selects_children_per_mapped_parent() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN", "DE", "DE"],
                "platform": ["web", "store", "web", "store", "app", "store"],
                "current": [100.0, 10.0, 80.0, 5.0, 60.0, 2.0],
                "baseline": [90.0, 9.0, 70.0, 4.0, 50.0, 1.0],
                "delta": [10.0, 1.0, 10.0, 1.0, 10.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
        mode="hierarchy",
        top_k=1,
    )

    deepest = out.to_pandas().query("attribution_level == 2")
    assert set(deepest["attribution_other_mask"]) == {0, 1, 2, 3}
    assert deepest["contribution"].sum() == pytest.approx(frame.to_pandas()["delta"].sum())
    assert out.meta.resolution_evidence is not None
    assert out.meta.resolution_evidence.resolution_semantics == "rollup"
    assert out.meta.resolution_evidence.rollup_safe is True
    assert "resolution_semantics=rollup rollup_safe=true" in out.render()
    corrupted = out.to_pandas()
    first_parent = corrupted.index[corrupted["attribution_level"] == 1][0]
    corrupted.loc[first_parent, "attribution_other_mask"] = 2
    with pytest.raises(ValueError, match="exceeds its hierarchy row prefix"):
        validate_generic_attribution_rows(out.meta, corrupted)


def test_attribute_joint_top_k_uses_the_same_ordered_parent_mapping() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "DE"],
                "platform": ["web", "store", "web", "app"],
                "current": [100.0, 10.0, 80.0, 60.0],
                "baseline": [90.0, 9.0, 70.0, 50.0],
                "delta": [10.0, 1.0, 10.0, 10.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
        mode="joint",
        top_k=1,
    )

    rows = out.to_pandas()
    assert set(rows["attribution_other_mask"]) == {0, 1, 2, 3}
    assert rows["contribution"].sum() == pytest.approx(31.0)
    assert out.meta.resolution_evidence is None


def test_attribute_panel_top_k_uses_one_fixed_cross_bucket_selection() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-02"],
                    utc=True,
                ),
                "region": ["A", "B", "A", "B"],
                "current": [100.0, 1.0, 1.0, 80.0],
                "baseline": [90.0, 1.0, 1.0, 70.0],
                "delta": [10.0, 0.0, 0.0, 10.0],
            }
        ),
        semantic_kind="panel",
    )
    alignment = dict(frame.meta.alignment)
    alignment["axes"] = {
        **alignment["axes"],
        "time": {"role": "time", "column": "bucket_start", "grain": "day"},
    }
    frame.meta = frame.meta.model_copy(update={"alignment": alignment})

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        top_k=1,
    )

    rows = out.to_pandas()
    for _, bucket_rows in rows.groupby("bucket_start"):
        named = bucket_rows[bucket_rows["attribution_other_mask"] == 0]
        assert named["region"].tolist() == ["A"]
        assert set(bucket_rows["attribution_other_mask"]) == {0, 1}


def test_attribute_reconciles_nullable_unsigned_one_sided_segments() -> None:
    session = mv.session.get_or_create(name="demo")
    axes = {
        "region": {
            "role": "dimension",
            "column": "region",
            "ref": "sales.orders.region",
        }
    }
    current = make_metric_frame(
        pd.DataFrame(
            {
                "region": ["stable", "new"],
                "value": pd.Series([7, 3], dtype="UInt64"),
            }
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )
    baseline = make_metric_frame(
        pd.DataFrame(
            {
                "region": ["stable", "churned"],
                "value": pd.Series([5, 4], dtype="UInt64"),
            }
        ),
        metric_id="sales.revenue",
        axes=axes,
        measure={"name": "value"},
        semantic_kind="segmented",
        semantic_model="sales",
        session=session,
    )

    delta = session.compare(current, baseline)
    attribution = session.attribute(
        delta,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    delta_rows = delta.to_pandas().set_index("region")
    assert delta_rows["delta"].to_dict() == {"churned": -4.0, "new": 3.0, "stable": 2.0}
    rows = attribution.to_pandas().set_index("region")
    assert rows["contribution"].to_dict() == {
        "churned": -4.0,
        "new": 3.0,
        "stable": 2.0,
    }
    assert rows.loc["churned", "share_of_negative_pool"] == pytest.approx(1.0)
    assert rows.loc["new", "share_of_positive_pool"] == pytest.approx(0.6)
    assert rows.loc["stable", "share_of_positive_pool"] == pytest.approx(0.4)
    assert rows["share_of_total_delta"].to_dict() == {
        "churned": -4.0,
        "new": 3.0,
        "stable": 2.0,
    }
    assert attribution.meta.reconciliation is not None
    assert attribution.meta.reconciliation.status == "reconciled"
    assert attribution.meta.reconciliation.residual == pytest.approx(0.0)


def test_generic_attribution_quality_reports_row_and_reconciliation_corruption() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN"],
                "delta": [10.0, -2.0],
            }
        ),
    )
    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    missing_share = out._dataframe_copy().drop(columns=["share_of_total_delta"])
    missing_share_frame = AttributionFrame(_df=missing_share, meta=out.meta)
    missing_status = {
        row["check_id"]: row["severity"] for row in run_attribution_checks(missing_share_frame)
    }
    assert missing_status["attribution_row_contract"] == "blocking"

    non_finite = out._dataframe_copy()
    non_finite.loc[0, "contribution"] = float("inf")
    non_finite_frame = AttributionFrame(_df=non_finite, meta=out.meta)
    non_finite_status = {
        row["check_id"]: row["severity"] for row in run_attribution_checks(non_finite_frame)
    }
    assert non_finite_status["attribution_contribution_values"] == "blocking"
    assert non_finite_status["attribution_reconciliation"] == "blocking"

    wrong_rank = out._dataframe_copy()
    wrong_rank["rank"] = 2
    wrong_rank_frame = AttributionFrame(_df=wrong_rank, meta=out.meta)
    wrong_rank_status = {
        row["check_id"]: row["severity"] for row in run_attribution_checks(wrong_rank_frame)
    }
    assert wrong_rank_status["attribution_row_contract"] == "blocking"


def test_panel_attribution_quality_validates_each_bucket_reconciliation() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "bucket_start": pd.to_datetime(
                    ["2026-07-01", "2026-07-01", "2026-07-02", "2026-07-02"],
                    utc=True,
                ),
                "region": ["US", "CN", "US", "CN"],
                "delta": [10.0, -2.0, 8.0, -1.0],
            }
        ),
        semantic_kind="panel",
    )
    alignment = dict(frame.meta.alignment)
    alignment["axes"] = {
        **alignment["axes"],
        "time": {"role": "time", "column": "bucket_start", "grain": "day"},
    }
    frame.meta = frame.meta.model_copy(update={"alignment": alignment})

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert out.meta.reconciliation is not None
    assert len(out.meta.reconciliation.bucket_reconciliations) == 2
    reloaded = session.get_frame(out.ref)
    assert reloaded.meta.reconciliation == out.meta.reconciliation

    corrupted = out._dataframe_copy()
    corrupted.loc[0, "contribution"] += 1_000.0
    corrupted_frame = AttributionFrame(_df=corrupted, meta=out.meta)
    with pytest.raises(ArtifactQualityError) as exc_info:
        evaluate_frame_quality(corrupted_frame, artifact_id="prospective")
    failed = {
        str(row["check_id"]): str(row["severity"])
        for row in exc_info.value._context["failed_checks"]
    }
    assert failed["attribution_row_contract"] == "blocking"
    assert failed["attribution_reconciliation"] == "blocking"


def test_empty_attribution_quality_warns_on_row_count_not_reconciliation() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": pd.Series(dtype="object"),
                "delta": pd.Series(dtype="float64"),
            }
        ),
    )
    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    quality = out.quality_report()
    assert quality is not None
    status = dict(
        zip(quality.to_pandas()["check_id"], quality.to_pandas()["severity"], strict=True)
    )

    assert quality.meta.overall_status == "warning"
    assert quality.meta.blocking_issue_count == 0
    assert quality.evidence_digest is None
    assert status["attribution_row_count"] == "warning"
    assert status["attribution_row_contract"] == "ok"
    assert status["attribution_reconciliation"] == "ok"
    issue_kinds = {issue.kind for issue in quality.meta.issues}
    assert "sample_size_low" in issue_kinds
    assert "attribution_reconciliation_invalid" not in issue_kinds


def test_attribute_accepts_current_catalog_axis_entry() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["US", "CN"], "delta": [10.0, -2.0]}),
    )
    region = session.catalog.dimensions.get("sales.orders.region")

    out = session.attribute(frame, axes=[region])

    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert list(out.to_pandas()["region"]) == ["US", "CN"]


def test_attribute_nested_axes_returns_flattened_hierarchy_rows() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN"],
                "platform": ["ios", "android", "ios", "android"],
                "delta": [6.0, 4.0, -3.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
        mode="hierarchy",
    )

    df = out.to_pandas()
    assert out.meta.method == "sum"
    assert out.attribution_mode == "hierarchy"
    assert out.meta.driver_field == "attribution_path"
    assert {
        "region",
        "platform",
        "value_effect",
        "mix_effect",
        "residual",
        "attribution_level",
    }.issubset(df.columns)
    assert df.loc[df["attribution_level"] == 2, "contribution"].sum() == pytest.approx(8.0)
    assert df.loc[df["attribution_level"] == 1, "platform"].isna().all()
    assert out.meta.resolution_evidence is not None
    assert out.meta.resolution_evidence.resolution_semantics == "rollup"
    assert out.meta.resolution_evidence.rollup_safe is True
    child_totals = df[df["attribution_level"] == 2].groupby("region")["contribution"].sum()
    parent_totals = df[df["attribution_level"] == 1].set_index("region")["contribution"]
    pd.testing.assert_series_equal(
        child_totals.sort_index(), parent_totals.sort_index(), check_names=False
    )
    selected = out.at_resolution(axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])
    assert selected.to_pandas()["contribution"].sum() == pytest.approx(8.0)
    assert selected.contract().row_arithmetic == "additive_once_per_comparison_bucket"
    corrupted = out.to_pandas()
    corrupted.loc[corrupted["attribution_level"] == 1, "contribution"] += 1.0
    with pytest.raises(ValueError, match="parent contribution differs from children"):
        validate_generic_attribution_rows(out.meta, corrupted)


def test_attribute_multi_axis_defaults_to_joint() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN"],
                "platform": ["ios", "android", "ios", "android"],
                "delta": [6.0, 4.0, -3.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
    )

    df = out.to_pandas()
    assert out.attribution_mode == "joint"
    assert out.meta.params["mode"] == "joint"
    assert {"region", "platform"}.issubset(df.columns)
    assert {
        "attribution_level",
        "attribution_axis",
        "attribution_driver",
        "attribution_path",
    }.isdisjoint(df.columns)
    assert df["contribution"].sum() == pytest.approx(8.0)


def test_attribute_rejects_legacy_multiresolution_with_hierarchy_repair() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US"],
                "delta": [1.0],
            }
        ),
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            frame,
            axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
            mode="multiresolution",  # type: ignore[arg-type]
        )

    assert exc_info.value.repair is not None
    assert "mode='hierarchy'" in exc_info.value.repair.action
    assert exc_info.value._context["replacement_mode"] == "hierarchy"


def test_attribute_requires_explicit_axes() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError, match="attribute requires at least one axis"):
        session.attribute(frame, axes=[])


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5])
def test_attribute_rejects_invalid_top_k(top_k) -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["US"], "delta": [1.0]}),
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            frame,
            axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
            top_k=top_k,
        )

    assert exc_info.value._context["reason"] == "invalid_top_k"


def test_attribute_present_axes_delegates_to_decompose_without_materialization() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN", "US"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert isinstance(out, AttributionFrame)
    assert out.lineage.steps[-1].intent == "attribute"
    assert out.meta.params["materialization_status"] == "not_required"
    assert out.meta.params["source_ref"] == "frame_delta"
    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert "mode" not in out.meta.params


def test_attribute_single_axis_ignores_mode_parameter() -> None:
    """A single-axis attribution has no joint/hierarchy distinction, so ``mode``
    is meaningless and must be ignored rather than rejected — letting callers
    pass a fixed ``mode`` without branching on axis count (see issue #23).
    """
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame({"region": ["US", "CN", "US"], "delta": [10.0, -2.0, 4.0]}),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
        mode="joint",
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.params["axes"] == ["sales.orders.region"]
    # mode is not applicable to a single axis, so it is dropped from params.
    assert "mode" not in out.meta.params
    assert out.attribution_mode is None


def test_attribute_rejects_duplicate_axes() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            frame,
            axes=[
                make_ref("sales.orders.region", SemanticKind.DIMENSION),
                make_ref("sales.orders.region", SemanticKind.DIMENSION),
            ],
        )

    assert exc_info.value._context["reason"] == "duplicate_axes"


@pytest.mark.parametrize(
    "axis_name",
    ["contribution", "rank", "share_of_total_delta", "attribution_other_mask"],
)
def test_attribute_rejects_reserved_single_axis_column(
    semantic_project_factory,
    axis_name: str,
) -> None:
    semantic_project_factory(
        {
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity("
                "name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
                f"reserved_axis = ms.dimension_column(name={axis_name!r}, "
                f"entity=orders, column={axis_name!r})\n"
            ),
        }
    )
    session = mv.session.get_or_create(name="demo")
    axis = session.catalog.require(
        make_ref(f"sales.orders.{axis_name}", SemanticKind.DIMENSION)
    ).ref
    frame = _delta(
        session,
        pd.DataFrame({axis_name: ["A", "B"], "delta": [3.0, -1.0]}),
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(frame, axes=[axis])

    error = exc_info.value
    assert error._context["reason"] == "reserved_axis_column"
    assert error._context["axis_column"] == axis_name
    assert axis_name in error._context["reserved_columns"]
    assert error.location == "session.attribute axes"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"


def _reserved_axis_project(axis_name: str) -> str:
    """Return datasets.py source declaring a dimension named ``axis_name``."""
    return (
        "import marivo.datasource as md\n"
        "import marivo.semantic as ms\n"
        "orders = ms.entity("
        "name='orders', datasource=ms.ref.datasource('warehouse'), "
        "source=md.table('orders'))\n"
        f"reserved_axis = ms.dimension_column(name={axis_name!r}, "
        f"entity=orders, column={axis_name!r})\n"
    )


@pytest.mark.parametrize(
    "mode,axis_name",
    [
        ("joint", "contribution"),
        ("joint", "rank"),
        ("joint", "value_effect"),
        ("joint", "mix_effect"),
        ("joint", "residual"),
        ("joint", "attribution_other_mask"),
        ("hierarchy", "contribution"),
        ("hierarchy", "rank"),
        ("hierarchy", "value_effect"),
        ("hierarchy", "mix_effect"),
        ("hierarchy", "residual"),
        ("hierarchy", "attribution_level"),
        ("hierarchy", "attribution_axis"),
        ("hierarchy", "attribution_driver"),
        ("hierarchy", "attribution_path"),
        ("hierarchy", "attribution_other_mask"),
    ],
)
def test_attribute_multi_axis_rejects_reserved_axis_column(
    semantic_project_factory,
    mode: str,
    axis_name: str,
) -> None:
    """Multi-axis joint/hierarchy must fail closed when an axis column collides
    with an attribution protocol column (issue #40).

    Previously the reserved-name check only ran in the single-axis additive
    path, so a reserved-named axis combined with another axis reached
    pandas and raised a raw ``ValueError`` instead of a typed error.
    """
    semantic_project_factory(
        {
            "sales/datasets.py": (
                _reserved_axis_project(axis_name) + "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
            ),
        }
    )
    session = mv.session.get_or_create(name="demo")
    reserved_axis = session.catalog.require(
        make_ref(f"sales.orders.{axis_name}", SemanticKind.DIMENSION)
    ).ref
    region_axis = session.catalog.require(
        make_ref("sales.orders.region", SemanticKind.DIMENSION)
    ).ref
    frame = _delta(
        session,
        pd.DataFrame(
            {
                axis_name: ["US", "US", "CN"],
                "region": ["east", "west", "east"],
                "delta": [6.0, 4.0, -3.0],
            }
        ),
    )

    with pytest.raises(SemanticKindMismatchError) as exc_info:
        session.attribute(
            frame,
            axes=[reserved_axis, region_axis],
            mode=mode,  # type: ignore[arg-type]
        )

    error = exc_info.value
    assert error._context["reason"] == "reserved_axis_column"
    assert error._context["axis_column"] == axis_name
    assert error.location == "session.attribute axes"
    assert error.repair is not None
    assert error.repair.kind == "semantic_authoring"


@pytest.mark.parametrize(
    "axis_name",
    [
        "dimension",
        "contribution_value",
        "contribution_share",
        "direction",
        "method",
        "reconciliation_residual",
        # Single-axis additive does NOT emit these columns, so a dimension
        # named like one of them must stay usable (issue #40 P1 regression).
        "value_effect",
        "mix_effect",
        "residual",
        "current_share",
        "baseline_share",
        "path",
        "driver",
        # NOTE: ``level`` is intentionally absent: decompose's finalize step
        # sniffs for a "level" column and keeps only the deepest rows
        # (decompose.py _finalize_attribution_output), so a dimension literally
        # named "level" with >1 distinct value drops rows and fails
        # reconciliation.  That is a pre-existing bug tracked separately, not a
        # reserved protocol name (MR !36 re-review).
    ],
)
def test_attribute_evidence_protocol_does_not_reserve_dimension_names(
    semantic_project_factory,
    axis_name: str,
) -> None:
    semantic_project_factory(
        {
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity("
                "name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
                f"protocol_named_axis = ms.dimension_column(name={axis_name!r}, "
                f"entity=orders, column={axis_name!r})\n"
            ),
        }
    )
    session = mv.session.get_or_create(name="demo")
    axis = session.catalog.require(
        make_ref(f"sales.orders.{axis_name}", SemanticKind.DIMENSION)
    ).ref
    frame = _delta(
        session,
        pd.DataFrame({axis_name: ["A", "B"], "delta": [3.0, -1.0]}),
    )

    attribution = session.attribute(frame, axes=[axis])

    assert attribution.meta.evidence_status == "complete"
    assert attribution.meta.driver_field == axis_name
    assert attribution.to_pandas()[axis_name].tolist() == ["A", "B"]
    assert attribution.evidence_digest is not None
    assert {
        item.dimension_keys[axis_name]: item.contribution_value
        for item in attribution.evidence_digest.items
    } == {"A": 3.0, "B": -1.0}
    assert {item.dimension for item in attribution.evidence_digest.items} == {axis_name}
    assert {item.decomposition_method for item in attribution.evidence_digest.items} == {"sum"}


def test_attribute_missing_axis_materializes_expanded_delta(semantic_project_factory) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = ms.ref.datasource('warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day')\n"
                "def created_at(orders):\n"
                "    return orders.created_at.cast('date')\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (id INTEGER, created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'US', 100.0),"
        "(2, DATE '2026-07-02', 'CN', 20.0),"
        "(3, DATE '2025-07-01', 'US', 70.0),"
        "(4, DATE '2025-07-02', 'CN', 30.0)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    revenue = session.catalog.require(make_ref("sales.revenue", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    cur = session.observe(
        revenue,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
    )
    base = session.observe(
        revenue,
        time_scope=mv.time_scope(start="2025-07-01", end="2025-08-01"),
    )
    delta = session.compare(cur, base)

    out = session.attribute(delta, axes=[region])

    assert isinstance(out, AttributionFrame)
    assert out.meta.params["materialization_status"] == "expanded"
    assert out.meta.params["original_delta_ref"] == delta.ref
    assert out.meta.params["missing_axes"] == ["sales.orders.region"]
    assert out.meta.params["expanded_delta_ref"]
    assert out.meta.driver_field == "region"
    assert out.to_pandas()[["region", "contribution"]].to_dict("records") == [
        {"region": "US", "contribution": 30.0},
        {"region": "CN", "contribution": -10.0},
    ]
    assert "session.forecast(...)" not in cur.render()
    assert "session.attribute(...): supported; attribution_shape=sum" in delta.render()
    assert "AttributionFrame" in out.render()
    contract_text = delta.contract().render()
    assert len(contract_text.encode("utf-8")) <= 8192
    assert "session.attribute(...) -> AttributionFrame" in contract_text
    with pytest.raises(ValueError, match="never truncates affordances or repairs"):
        delta.contract().render(max_output_bytes=1024)
    assert [job.intent for job in session.jobs()].count("observe") == 4
    assert [job.intent for job in session.jobs()].count("compare") == 2


def test_attribute_validates_original_delta_before_axis_materialization(
    semantic_project_factory,
) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "warehouse = ms.ref.datasource('warehouse')\n"
                "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
                "@ms.time_dimension(entity=orders, granularity='day')\n"
                "def created_at(orders):\n"
                "    return orders.created_at.cast('date')\n"
                "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
                "@ms.metric(entities=[orders], additivity='additive', name='revenue')\n"
                "def revenue(orders):\n"
                "    return orders.amount.sum()\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (id INTEGER, created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 'US', 100.0),"
        "(2, DATE '2025-07-01', 'US', 70.0)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    revenue = session.catalog.require(make_ref("sales.revenue", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    current = session.observe(
        revenue,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
    )
    baseline = session.observe(
        revenue,
        time_scope=mv.time_scope(start="2025-07-01", end="2025-08-01"),
    )
    delta = session.compare(current, baseline)
    delta.meta = delta.meta.model_copy(update={"additivity": None})

    with pytest.raises(mv.errors.AttributeAdmissionBlockedError) as exc_info:
        session.attribute(delta, axes=[region])

    assert exc_info.value._context["blocker"] == "unsupported_aggregate"
    assert [job.intent for job in session.jobs()].count("observe") == 2
    assert [job.intent for job in session.jobs()].count("compare") == 1


def test_attribute_lowers_tier1_mean_to_exact_non_null_components(
    semantic_project_factory,
) -> None:
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
            "sales/datasets.py": (
                "import marivo.datasource as md\n"
                "import marivo.semantic as ms\n"
                "orders = ms.entity("
                "name='orders', datasource=ms.ref.datasource('warehouse'), "
                "source=md.table('orders'))\n"
                "created_at = ms.time_dimension_column("
                "name='created_at', entity=orders, column='created_at', "
                "granularity='day', is_default=True)\n"
                "region = ms.dimension_column("
                "name='region', entity=orders, column='region')\n"
                "amount = ms.measure_column("
                "name='amount', entity=orders, column='amount', additivity='additive')\n"
                "avg_amount = ms.aggregate("
                "name='avg_amount', measure=amount, agg='mean')\n"
            ),
        }
    )
    import ibis

    con = ibis.duckdb.connect(":memory:")
    con.raw_sql("CREATE TABLE orders (created_at DATE, region VARCHAR, amount DOUBLE)")
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(DATE '2026-07-01', 'US', 100.0),"
        "(DATE '2026-07-02', 'US', 200.0),"
        "(DATE '2026-07-03', 'CN', 10.0),"
        "(DATE '2026-07-04', 'US', NULL),"
        "(DATE '2025-07-01', 'US', 100.0),"
        "(DATE '2025-07-02', 'CN', 10.0),"
        "(DATE '2025-07-03', 'CN', 20.0),"
        "(DATE '2025-07-04', 'US', NULL)"
    )
    session = mv.session.get_or_create(name="demo", backends={"warehouse": lambda: con})
    avg_amount = session.catalog.require(make_ref("sales.avg_amount", SemanticKind.METRIC)).ref
    region = session.catalog.require(make_ref("sales.orders.region", SemanticKind.DIMENSION)).ref
    cur = session.observe(
        avg_amount,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-08-01"),
    )
    base = session.observe(
        avg_amount,
        time_scope=mv.time_scope(start="2025-07-01", end="2025-08-01"),
    )
    delta = session.compare(cur, base)

    assert cur.meta.additivity == "non_additive"
    assert cur.meta.aggregation == "mean"
    assert cur.meta.status_time_dimension is None
    assert cur.meta.composition is not None
    assert cur.meta.composition["kind"] == "weighted_mean"
    assert cur.meta.composition["lowered_from"] == "mean"
    assert cur.meta.composition["denominator_semantics"] == "count_non_null"
    assert cur.components().to_pandas()["__weighted_mean_weight"].iloc[0] == 3
    assert delta.meta.additivity == "non_additive"
    assert delta.meta.aggregation == "mean"
    assert delta.meta.status_time_dimension is None
    loaded_delta = session.get_frame(delta.ref)
    assert isinstance(loaded_delta, DeltaFrame)
    assert loaded_delta.meta.additivity == "non_additive"
    assert loaded_delta.meta.aggregation == "mean"
    assert delta.to_pandas().iloc[0]["delta"] == pytest.approx(60.0)
    attribution = session.attribute(delta, axes=[region])

    assert attribution.meta.method == "weighted_mix"
    assert attribution.to_pandas()["contribution"].sum() == pytest.approx(60.0)


def test_attribute_missing_axis_without_replayable_sources_fails_closed() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"delta": [10.0]}))

    with pytest.raises(AttributionMaterializationError) as exc_info:
        session.attribute(frame, axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)])

    assert exc_info.value._context["delta_ref"] == "frame_delta"
    assert exc_info.value._context["missing_axes"] == ["sales.orders.region"]
    assert exc_info.value._context["recoverability_status"] in {
        "source_frame_missing",
        "observe_params_missing",
    }


def test_attribute_single_axis_with_level_dimension_is_not_cropped(
    semantic_project_factory,
) -> None:
    """A legal business dimension named ``level`` must attribute normally
    instead of being cropped by the hierarchy deepest-level reconciliation
    sniff (issue #43).

    Pre-fix, decompose's finalize step sniffed for a ``level`` column and kept
    only the deepest rows, dropping every other level value and failing
    reconciliation.
    """
    semantic_project_factory(
        {
            "sales/datasets.py": (_reserved_axis_project("level")),
        }
    )
    session = mv.session.get_or_create(name="demo")
    level_axis = session.catalog.require(make_ref("sales.orders.level", SemanticKind.DIMENSION)).ref
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "level": ["l1", "l2", "l3"],
                "delta": [3.0, -1.0, 4.0],
            }
        ),
    )

    attribution = session.attribute(frame, axes=[level_axis])

    assert attribution.meta.evidence_status == "complete"
    result = attribution.to_pandas()
    assert set(result["level"]) == {"l1", "l2", "l3"}
    assert result["contribution"].sum() == pytest.approx(6.0)


def test_attribute_joint_level_dimension_is_not_cropped(
    semantic_project_factory,
) -> None:
    """Joint-mode attribution with a legal ``level`` dimension must not be
    cropped by the hierarchy deepest-level reconciliation sniff (issue #43,
    joint path)."""
    semantic_project_factory(
        {
            "sales/datasets.py": (
                _reserved_axis_project("level") + "@ms.dimension(entity=orders)\n"
                "def region(orders):\n"
                "    return orders.region\n"
            ),
        }
    )
    session = mv.session.get_or_create(name="demo")
    level_axis = session.catalog.require(make_ref("sales.orders.level", SemanticKind.DIMENSION)).ref
    region_axis = session.catalog.require(
        make_ref("sales.orders.region", SemanticKind.DIMENSION)
    ).ref
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "level": ["l1", "l1", "l2", "l2"],
                "region": ["north", "south", "north", "south"],
                "delta": [6.0, 4.0, -3.0, -2.0],
            }
        ),
    )

    attribution = session.attribute(
        frame,
        axes=[level_axis, region_axis],
        mode="joint",
    )

    assert attribution.meta.evidence_status == "complete"
    result = attribution.to_pandas()
    assert {"level", "region"}.issubset(result.columns)
    assert result["contribution"].sum() == pytest.approx(5.0)
