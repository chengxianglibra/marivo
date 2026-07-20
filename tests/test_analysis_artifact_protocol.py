"""Phase 1 protocol consistency across all public artifact families."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd
import pytest

from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactBoundaryPort,
    ArtifactContract,
    ArtifactPrecondition,
    ArtifactSchema,
    ArtifactState,
)
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.lineage import Lineage
from marivo.introspection.live.model import LiveHelpTarget
from tests.shared_fixtures import make_test_delta_contract, make_test_metric_meta_contract


def _base_meta(kind: str, ref: str, row_count: int = 1) -> dict[str, Any]:
    return {
        "kind": kind,
        "ref": ref,
        "session_id": "sess_protocol",
        "project_root": "/tmp/project",
        "produced_by_job": "job_protocol",
        "created_at": datetime(2026, 6, 26, 8, 0, 0, tzinfo=UTC),
        "row_count": row_count,
        "byte_size": 0,
        "lineage": Lineage(),
        "content_hash": "sha256:" + "a" * 64,
    }


def _delta_contract_frame(
    *,
    additivity: Literal["additive", "semi_additive", "non_additive"] | None,
    status_time_dimension: str | None = None,
    composition_kind: Literal["ratio", "weighted_average"] | None = None,
) -> DeltaFrame:
    component_ref = "frame_delta_components" if composition_kind is not None else None
    composition = (
        {"kind": composition_kind, "components": {}} if composition_kind is not None else None
    )
    return DeltaFrame(
        _df=pd.DataFrame({"delta": [1.0]}),
        meta=DeltaFrameMeta(
            **make_test_delta_contract(
                "sales.revenue",
                status_time_dimension=status_time_dimension,
            ),
            **_base_meta("delta_frame", "frame_delta_contract"),
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket"},
            semantic_kind="segmented",
            semantic_model="sales",
            component_ref=component_ref,
            composition=composition,
            additivity=additivity,
            status_time_dimension=status_time_dimension,
        ),
    )


def _attribute_affordance(frame: DeltaFrame) -> ArtifactAffordance:
    return next(
        affordance
        for affordance in frame.contract().affordances
        if affordance.capability_id == "attribute"
    )


def _artifact_cases():
    yield MetricFrame(
        _df=pd.DataFrame({"bucket_start": ["2026-06-18"], "value": [1.0]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.revenue"),
            **_base_meta("metric_frame", "frame_metric"),
            metric_id="sales.revenue",
            axes={},
            measure={"name": "revenue"},
            window=None,
            where={},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    yield DeltaFrame(
        _df=pd.DataFrame({"delta": [1.0]}),
        meta=DeltaFrameMeta(
            **make_test_delta_contract("sales.revenue"),
            **_base_meta("delta_frame", "frame_delta"),
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket"},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    yield AttributionFrame(
        _df=pd.DataFrame({"region": ["US"], "contribution": [1.0]}),
        meta=AttributionFrameMeta(
            **_base_meta("attribution_frame", "frame_attr"),
            metric_ids=["sales.revenue"],
            source_refs=["frame_delta"],
            scope_delta_ref="frame_delta",
            attribution_kind="decomposition",
            driver_field="region",
            value_column="delta",
            contribution_column="contribution",
            method="sum",
            params={"by": "region"},
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )
    yield CandidateSet(
        _df=pd.DataFrame({"item_id": ["cand_1"], "score": [3.0]}),
        meta=CandidateSetMeta(
            **_base_meta("candidate_set", "frame_candidates"),
            shape="point_anomaly",
            objective="point_anomalies",
            strategy="zscore",
            source_ref="frame_metric",
            source_kind="metric_frame",
            metric_ids=["sales.revenue"],
            semantic_kind="time_series",
            semantic_model="sales",
            source_refs=["frame_metric"],
            params={"threshold": 3.0},
        ),
    )
    yield AssociationResult(
        _df=pd.DataFrame({"metric_a": ["a"], "metric_b": ["b"], "correlation": [0.5]}),
        meta=AssociationResultMeta(
            **_base_meta("association_result", "frame_assoc"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.a", "sales.b"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            method="pearson",
            alignment={"kind": "window_bucket"},
            lag_policy={},
            aligned_row_count=5,
            dropped_row_count=0,
            correlation=0.5,
        ),
    )
    yield HypothesisTestResult(
        _df=pd.DataFrame({"segment": ["all"], "rejected": [False]}),
        meta=HypothesisTestResultMeta(
            **_base_meta("hypothesis_test_result", "frame_test"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            hypothesis="mean_changed",
            method="paired_t",
            alignment={"kind": "window_bucket"},
            sampling={"unit": "day"},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=0,
            not_enough_data_count=0,
        ),
    )
    yield ForecastFrame(
        _df=pd.DataFrame({"bucket_start": ["2026-06-27"], "forecast": [2.0]}),
        meta=ForecastFrameMeta(
            **_base_meta("forecast_frame", "frame_forecast"),
            source_refs=["frame_history"],
            metric_id="sales.revenue",
            semantic_model="sales",
            semantic_kind="time_series",
            measure={"name": "revenue"},
            axes={},
            history_window={"start": "2026-06-01", "end": "2026-06-26"},
            forecast_window={"start": "2026-06-26", "end": "2026-06-27"},
            horizon=1,
            horizon_unit="day",
            model="naive",
            seasonality_period=None,
            interval_level=0.95,
            interval_method="normal_residual",
            train_row_count_per_segment={"all": 10},
            segment_dimensions=[],
        ),
    )
    yield QualityReport(
        _df=pd.DataFrame({"check_id": ["row_count"], "status": ["ok"], "message": ["ok"]}),
        meta=QualityReportMeta(
            **_base_meta("quality_report", "frame_quality"),
            source_refs=["frame_metric"],
            report_shape="metric",
            target_kind="metric_frame",
            target_metric_id="sales.revenue",
            target_semantic_model="sales",
            target_semantic_kind="time_series",
            checks_run=["row_count"],
            overall_status="ok",
            blocking_issue_count=0,
            warning_count=0,
        ),
    )


def test_public_artifact_families_share_phase1_protocol() -> None:
    forbidden = {
        "headline",
        "conclusion",
        "recommendation",
        "recommended_followups",
        "next_actions",
        "decision_descriptor",
    }

    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        assert artifact.ref, f"{tag}: ref is falsy"
        assert artifact.kind == artifact.meta.kind, f"{tag}: kind mismatch"
        assert isinstance(artifact.contract(), ArtifactContract), f"{tag}: contract() type"
        assert isinstance(artifact.state, ArtifactState), f"{tag}: state type"
        assert artifact.state.content_hash == "sha256:" + "a" * 64, f"{tag}: content_hash"
        assert artifact.to_pandas() is not artifact._df, f"{tag}: to_pandas not isolated"

        contract = artifact.contract()
        assert isinstance(contract.artifact_schema, ArtifactSchema), (
            f"{tag}: contract().artifact_schema type"
        )
        assert contract.artifact_schema.columns, f"{tag}: contract().artifact_schema columns empty"

        payload = {
            "contract": contract.model_dump(mode="json"),
            "state": artifact.state.model_dump(mode="json"),
        }
        for projection_name, projection in payload.items():
            assert forbidden.isdisjoint(projection.keys()), (
                f"{tag}: {projection_name} leaked forbidden keys"
            )
            assert "recommend" not in str(projection).lower(), (
                f"{tag}: {projection_name} contains 'recommend'"
            )


# ---------------------------------------------------------------------------
# Task 7: capability_id affordances, typed repair preconditions, boundary ports
# ---------------------------------------------------------------------------


def test_artifact_affordance_model_fields_use_capability_id_not_operator() -> None:
    fields = ArtifactAffordance.model_fields
    assert "capability_id" in fields
    assert "public_entrypoint" in fields
    assert "help_target" in fields
    assert "operator" not in fields


def test_artifact_precondition_has_repair_field() -> None:
    fields = ArtifactPrecondition.model_fields
    assert "repair" in fields


def test_artifact_boundary_port_model_fields() -> None:
    fields = ArtifactBoundaryPort.model_fields
    assert "kind" in fields
    assert "capability_id" in fields
    assert "public_entrypoint" in fields
    assert "help_target" in fields
    assert "preserves" in fields
    assert "does_not_preserve" in fields


def test_artifact_contract_has_boundary_ports() -> None:
    fields = ArtifactContract.model_fields
    assert "boundary_ports" in fields


def test_every_artifact_has_one_terminal_boundary_port() -> None:
    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        contract = artifact.contract()
        assert len(contract.boundary_ports) == 1, f"{tag}: expected 1 boundary port"
        port = contract.boundary_ports[0]
        assert isinstance(port, ArtifactBoundaryPort), f"{tag}: boundary port type"
        assert port.kind == "terminal_exit", f"{tag}: boundary port kind"
        assert port.capability_id == "boundary.to_pandas", f"{tag}: boundary capability_id"
        assert port.help_target == "boundary.to_pandas", f"{tag}: boundary help_target"


def test_affordances_match_registry_edges() -> None:
    """Every affordance capability_id is a registered consumer of this family."""
    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        family = type(artifact).__name__
        contract = artifact.contract()
        registered = set(REGISTRY.constructor_consumers.get(family, ()))
        # boundary.to_pandas is in boundary_ports, not affordances.
        affordance_ids = {a.capability_id for a in contract.affordances}
        # All affordance ids must be registered consumers (excluding boundary).
        non_boundary = affordance_ids - {"boundary.to_pandas"}
        assert non_boundary <= registered, (
            f"{tag}: affordance ids {non_boundary - registered} not in registry consumers"
        )


def test_every_affordance_output_family_is_non_null() -> None:
    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        contract = artifact.contract()
        for aff in contract.affordances:
            assert aff.expected_output_family is not None, (
                f"{tag}: affordance {aff.capability_id} has null expected_output_family"
            )


def test_every_affordance_has_public_entrypoint_and_help_target() -> None:
    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        contract = artifact.contract()
        for aff in contract.affordances:
            assert aff.public_entrypoint, (
                f"{tag}: affordance {aff.capability_id} has empty public_entrypoint"
            )
            assert aff.help_target, f"{tag}: affordance {aff.capability_id} has empty help_target"


def test_failed_precondition_without_repair_suppresses_affordance() -> None:
    """An affordance with a failed precondition and no repair is suppressed."""
    preconditions = [
        ArtifactPrecondition(
            check="blocking_issue",
            status="fail",
            reason="something is wrong",
            repair=None,
        )
    ]
    affordance = ArtifactAffordance(
        capability_id="compare",
        public_entrypoint="session.compare(...)",
        help_target="compare",
        input_requirements=(),
        preconditions=tuple(preconditions),
        expected_output_family="delta_frame",
    )
    from marivo.analysis.frames.base import _visible_precondition

    assert not _visible_precondition(preconditions[0])


def test_failed_precondition_with_repair_remains_visible() -> None:
    """A failed precondition with a non-empty repair action is visible."""
    preconditions = [
        ArtifactPrecondition(
            check="single_metric",
            status="fail",
            reason="frame carries 2 metrics",
            repair=AnalysisRepair(
                kind="retry",
                action='Call .metric("sales.revenue") first',
                help_target=LiveHelpTarget(surface="analysis", canonical_id="MetricFrame.metric"),
            ),
        )
    ]
    from marivo.analysis.frames.base import _visible_precondition

    assert _visible_precondition(preconditions[0])


@pytest.mark.parametrize(
    ("additivity", "reason_fragment"),
    [
        (None, "persisted additivity metadata"),
        ("non_additive", "non-additive metric delta"),
    ],
)
def test_delta_contract_fails_unconditionally_invalid_attribution(
    additivity,
    reason_fragment,
) -> None:
    affordance = _attribute_affordance(_delta_contract_frame(additivity=additivity))

    precondition = next(
        item
        for item in affordance.preconditions
        if item.check == "attribution_additivity_compatible"
    )
    assert precondition.status == "fail"
    assert reason_fragment in (precondition.reason or "")
    assert precondition.repair is not None
    assert precondition.repair.help_target.canonical_id == "attribute"


def test_delta_contract_surfaces_semi_additive_axis_condition() -> None:
    affordance = _attribute_affordance(
        _delta_contract_frame(
            additivity="semi_additive",
            status_time_dimension="sales.inventory.snapshot_at",
        )
    )

    precondition = next(
        item
        for item in affordance.preconditions
        if item.check == "attribution_status_time_axis_excluded"
    )
    assert precondition.status == "fail"
    assert "sales.inventory.snapshot_at" in (precondition.reason or "")
    assert precondition.repair is not None
    assert "exclude" in precondition.repair.action


@pytest.mark.parametrize("composition_kind", ["ratio", "weighted_average"])
def test_delta_contract_preserves_component_aware_attribution_exception(
    composition_kind,
) -> None:
    affordance = _attribute_affordance(
        _delta_contract_frame(
            additivity="non_additive",
            composition_kind=composition_kind,
        )
    )

    assert not any(
        item.check
        in {
            "attribution_additivity_compatible",
            "attribution_status_time_axis_excluded",
        }
        for item in affordance.preconditions
    )
    available = next(
        item for item in affordance.preconditions if item.check == "component_attribution_available"
    )
    assert available.status == "pass"
    expected_shape = "ratio_mix" if composition_kind == "ratio" else "weighted_mix"
    assert expected_shape in (available.reason or "")


def test_delta_show_surfaces_direct_component_attribution() -> None:
    frame = _delta_contract_frame(
        additivity="non_additive",
        composition_kind="weighted_average",
    )
    frame.meta.composition = {
        **(frame.meta.composition or {}),
        "lowered_from": "mean",
    }

    rendered = frame.render()

    assert "attribute: direct attribute is supported" in rendered
    assert "attribution_shape=weighted_mix" in rendered
    assert "lowered_from=mean" in rendered


def test_delta_show_surfaces_blocked_non_additive_attribution() -> None:
    frame = _delta_contract_frame(additivity="non_additive")

    rendered = frame.render()

    assert "attribute: blocked:" in rendered
    assert "inspect .contract() for repair" in rendered


def test_delta_show_keeps_attribution_guidance_before_bounded_preview() -> None:
    frame = _delta_contract_frame(
        additivity="non_additive",
        composition_kind="ratio",
    )
    frame._df = pd.DataFrame(
        {
            "bucket_start": ["2026-07-06"] * 7,
            "bucket_start_b": ["2026-07-06"] * 7,
            "current": [0.12345678901234567] * 7,
            "baseline": [0.9876543210987654] * 7,
            "delta": [-0.8641975320864197] * 7,
            "pct_change": [-0.8750000000000001] * 7,
            "diagnostic": ["x" * 1024] * 7,
        }
    )

    rendered = frame.render()

    assert "attribute: direct attribute is supported" in rendered
    assert rendered.index("attribute:") < rendered.index("preview:")


def test_delta_contract_keeps_additive_attribution_unblocked() -> None:
    affordance = _attribute_affordance(_delta_contract_frame(additivity="additive"))

    assert not any(
        item.check
        in {
            "attribution_additivity_compatible",
            "attribution_status_time_axis_excluded",
        }
        for item in affordance.preconditions
    )


def test_passing_precondition_visible_only_with_non_empty_reason() -> None:
    """A passing precondition is visible only when reason is non-empty."""
    from marivo.analysis.frames.base import _visible_precondition

    visible = ArtifactPrecondition(
        check="to_date_baseline_tail",
        status="pass",
        reason="ordinal alignment matched 3 buckets",
    )
    invisible = ArtifactPrecondition(
        check="ok",
        status="pass",
        reason=None,
    )
    empty_reason = ArtifactPrecondition(
        check="ok",
        status="pass",
        reason="  ",
    )
    assert _visible_precondition(visible)
    assert not _visible_precondition(invisible)
    assert not _visible_precondition(empty_reason)


def test_removed_pandas_conveniences_are_plain_attribute_errors() -> None:
    """describe() and plot() must raise plain AttributeError, not return data."""
    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        with pytest.raises(AttributeError):
            artifact.describe()  # type: ignore[attr-defined]
        with pytest.raises(AttributeError):
            artifact.plot()  # type: ignore[attr-defined]
