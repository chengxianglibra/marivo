"""Public funnel comparison and attribution over a real DuckDB project."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import (
    AnalysisError,
    EventCoverageUnknownError,
    FunnelAttributionUnsupportedError,
    FunnelComparisonMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.intents._quality_checks import (
    run_funnel_attribution_checks,
    run_funnel_delta_checks,
)
from marivo.analysis.intents.funnel_compare import _COMPATIBILITY_FACETS
from marivo.refs import RefPayloadV1
from tests.shared_fixtures import (
    analysis_persistence_snapshot,
    grouped_two_scope_funnel_frames,
    pattern_step_for_tests,
    two_scope_funnel_frames,
)


def _clone_event_frame(
    frame: EventFrame,
    *,
    rows: pd.DataFrame | None = None,
    **meta_updates: object,
) -> EventFrame:
    return EventFrame(
        _df=frame._dataframe_copy() if rows is None else rows,
        meta=frame.meta.model_copy(update=meta_updates),
    )


def _single_step_funnel(session: Any) -> EventFrame:
    journeys = session.events.match(
        pattern=mv.sequence(pattern_step_for_tests("cart")),
        cohort_window=mv.TimeScope(
            start="2026-07-08T00:00:00Z",
            end="2026-07-15T00:00:00Z",
        ),
        completion_through="2026-07-22T00:00:00Z",
        matching=mv.first_per_subject(),
    )
    return session.events.funnel(journeys)


def _assert_structured_error(
    error: AnalysisError,
    *,
    kind: str,
    location: str,
) -> None:
    assert error.kind == kind
    assert error.expected
    assert error.received
    assert error.location == location
    repair = error.repair
    assert repair is not None
    assert repair.kind in {"inspect", "user_choice"}
    assert repair.action
    assert repair.help_target.canonical_id in {"compare", "attribute"}


def test_compare_produces_an_aligned_funnel_delta(funnel_session: Any) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    assert type(delta) is DeltaFrame
    assert delta.meta.semantic_kind == "funnel"
    assert delta.meta.aligned_step_keys == ("cart", "payment")
    assert delta.evidence_status == "complete", delta.meta.issues
    contract = delta.contract().render()
    assert "session.attribute(...)" in contract
    assert "session.assess_quality(...)" in contract
    assert "session.correlate" not in contract


def test_compare_rejects_a_caller_supplied_alignment(funnel_session: Any) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(current, baseline, alignment=mv.window_bucket())
    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare.alignment",
    )


def test_compare_rejects_a_non_funnel_pair_with_repair(funnel_session: Any) -> None:
    current, _ = two_scope_funnel_frames(funnel_session)

    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(current, object())  # type: ignore[arg-type]

    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare.baseline",
    )


def test_compare_rejects_cross_session_funnels_with_repair(
    funnel_session: Any,
    funnel_session_factory: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    other = funnel_session_factory("funnel-compare-other-session")
    try:
        with pytest.raises(FunnelComparisonMismatchError) as excinfo:
            other.compare(current, baseline)
        _assert_structured_error(
            excinfo.value,
            kind="funnel_comparison_mismatch",
            location="session.compare.current",
        )
    finally:
        other.close()


def test_compare_compatibility_facets_read_the_published_metadata(
    funnel_session: Any,
) -> None:
    current, _ = two_scope_funnel_frames(funnel_session)
    meta = current.meta
    facts = {name: read(meta) for name, read in _COMPATIBILITY_FACETS}

    assert facts == {
        "catalog_definition_fingerprint": meta.catalog_definition_fingerprint,
        "subject_entity": meta.subject_entity_ref.path,
        "subject_identity": meta.subject_identity,
        "pattern": meta.pattern.fingerprint,
        "pattern_step_keys": tuple(step.key for step in meta.pattern.steps),
        "pattern_step_definitions": tuple(step.fingerprint for step in meta.pattern.steps),
        "matching": meta.matching.kind,
        "completion_through": meta.completion_through,
        "axis_dimensions": (),
        "axis_columns": (),
        "axis_anchors": (),
    }


@pytest.mark.parametrize(
    ("facet", "updates"),
    (
        ("catalog_definition_fingerprint", {"catalog_definition_fingerprint": "sha256:stale"}),
        ("subject_identity", {"subject_identity": ("commerce.orders.other_id",)}),
        (
            "matching",
            {"matching": mv.every_start(completion_assignment="exclusive")},
        ),
        ("completion_through", {"completion_through": "2026-07-23T00:00:00Z"}),
    ),
)
def test_compare_rejects_each_core_structural_contract_with_repair(
    funnel_session: Any,
    facet: str,
    updates: dict[str, object],
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    incompatible = _clone_event_frame(baseline, **updates)

    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(current, incompatible)

    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare(current, baseline)",
    )
    assert facet in excinfo.value.received


def test_compare_rejects_a_different_pattern_with_repair(funnel_session: Any) -> None:
    current, _ = two_scope_funnel_frames(funnel_session)

    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(current, _single_step_funnel(funnel_session))

    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare(current, baseline)",
    )
    assert "pattern" in excinfo.value.received


@pytest.mark.parametrize(
    "facet",
    ("axis_dimensions", "axis_columns", "axis_anchors"),
)
def test_compare_rejects_each_axis_contract_with_repair(
    funnel_session: Any,
    facet: str,
) -> None:
    current, baseline = grouped_two_scope_funnel_frames(funnel_session)
    binding = baseline.meta.axes[0]
    if facet == "axis_dimensions":
        replacement = binding.model_copy(
            update={
                "dimension_ref": RefPayloadV1.from_ref(
                    ms.ref.dimension("commerce.orders.plan_tier")
                )
            }
        )
    elif facet == "axis_columns":
        replacement = binding.model_copy(update={"output_column": "other_channel"})
    else:
        replacement = binding.model_copy(update={"anchor": "other_anchor"})
    incompatible = _clone_event_frame(
        baseline,
        axes=(replacement,),
    )

    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(current, incompatible)

    assert facet in excinfo.value.received
    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare(current, baseline)",
    )


def test_compare_rejects_coverage_censoring_with_inspect_repair(
    funnel_session: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    censored_rows = current._dataframe_copy()
    censored_rows.loc[censored_rows.index[-1], "coverage_censored_count"] = 1
    censored = _clone_event_frame(current, rows=censored_rows)

    with pytest.raises(EventCoverageUnknownError) as excinfo:
        funnel_session.compare(censored, baseline)

    _assert_structured_error(
        excinfo.value,
        kind="event_coverage_unknown",
        location="session.compare(current, baseline)",
    )
    assert excinfo.value.repair.kind == "inspect"


def test_compare_rejects_an_unpersisted_source_with_repair(
    funnel_session: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    unpersisted = _clone_event_frame(current, content_hash=None)

    with pytest.raises(FunnelComparisonMismatchError) as excinfo:
        funnel_session.compare(unpersisted, baseline)

    _assert_structured_error(
        excinfo.value,
        kind="funnel_comparison_mismatch",
        location="session.compare(current, baseline)",
    )


def test_compare_job_timestamps_bracket_delta_computation(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    module = importlib.import_module("marivo.analysis.intents.funnel_compare")
    original = module.build_funnel_delta
    observed: dict[str, datetime] = {}

    def recording_build(*args: object, **kwargs: object) -> object:
        observed["computed_at"] = datetime.now(UTC)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "build_funnel_delta", recording_build)
    delta = funnel_session.compare(current, baseline)
    job = funnel_session.job(delta.meta.produced_by_job)

    assert datetime.fromisoformat(job["started_at"]) <= observed["computed_at"]
    assert observed["computed_at"] <= datetime.fromisoformat(job["finished_at"])
    assert delta.meta.created_at == datetime.fromisoformat(job["finished_at"])
    assert "event_reducer" not in job
    assert job["funnel_comparison"]["artifact_ref"] == delta.ref
    assert job["funnel_comparison"]["source_current_ref"] == current.ref
    assert job["funnel_comparison"]["source_baseline_ref"] == baseline.ref


@pytest.mark.parametrize(
    "failure_target",
    ("register_frame_artifact", "persist_job_record"),
)
def test_compare_rolls_back_new_output_on_late_failure(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    module = importlib.import_module("marivo.analysis.intents.funnel_compare")
    before = analysis_persistence_snapshot(funnel_session)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced funnel compare persistence failure")

    monkeypatch.setattr(module, failure_target, fail)
    with pytest.raises(RuntimeError, match="forced funnel compare persistence failure"):
        funnel_session.compare(current, baseline)

    assert analysis_persistence_snapshot(funnel_session) == before
    assert funnel_session.get_frame(current.meta.artifact_id or current.ref).ref == current.ref


def test_compare_preserves_a_preexisting_output_on_late_failure(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    first = funnel_session.compare(current, baseline)
    module = importlib.import_module("marivo.analysis.intents.funnel_compare")
    before = analysis_persistence_snapshot(funnel_session)

    monkeypatch.setattr(module, "frame_exists_on_disk", lambda *_args: False)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced funnel compare persistence failure")

    monkeypatch.setattr(module, "persist_job_record", fail)
    with pytest.raises(RuntimeError, match="forced funnel compare persistence failure"):
        funnel_session.compare(current, baseline)

    assert analysis_persistence_snapshot(funnel_session) == before
    recovered = funnel_session.get_frame(first.meta.artifact_id or first.ref)
    assert recovered.to_pandas().equals(first.to_pandas())


def test_single_axis_attribution_reconciles(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=mv.funnel_loss_rate(step=payment_step),
    )
    assert type(drivers) is AttributionFrame
    assert drivers.meta.semantic_kind == "funnel_loss_rate"
    assert "acquisition_channel" in drivers.columns
    assert "step_key" not in drivers.columns
    assert drivers.meta.reconciliation.status == "reconciled"
    assert drivers.evidence_status == "complete", drivers.meta.issues


def test_multi_axis_requires_mode_and_hierarchy_has_layout(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
    plan_tier_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    target = mv.funnel_loss_rate(step=payment_step)
    with pytest.raises(FunnelAttributionUnsupportedError):
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry, plan_tier_entry],
            target=target,
        )
    hierarchy = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry, plan_tier_entry],
        mode="hierarchy",
        target=target,
    )
    assert {"level", "axis", "driver", "path"}.issubset(hierarchy.columns)


def test_quality_reports_cover_both_new_shapes(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=mv.funnel_loss_rate(step=payment_step),
    )
    delta_report = funnel_session.assess_quality(delta)
    attribution_report = funnel_session.assess_quality(drivers)
    assert delta_report.meta.report_shape == "funnel_delta"
    assert attribution_report.meta.report_shape == "funnel_attribution"
    assert {
        "funnel_delta_alignment",
        "funnel_delta_components",
        "funnel_delta_coverage",
        "funnel_delta_row_contract",
    }.issubset(set(delta_report.to_pandas()["check_id"]))
    assert {
        "funnel_attribution_components",
        "funnel_attribution_pools",
        "funnel_attribution_residual",
        "funnel_attribution_reconciliation",
    }.issubset(set(attribution_report.to_pandas()["check_id"]))
    delta_quality_job = funnel_session.job(delta_report.meta.produced_by_job)
    attribution_quality_job = funnel_session.job(attribution_report.meta.produced_by_job)
    assert "event_reducer" not in delta_quality_job
    assert delta_quality_job["funnel_comparison"]["artifact_ref"] == delta.ref
    assert "event_journey" not in attribution_quality_job
    assert attribution_quality_job["funnel_attribution"]["artifact_ref"] == drivers.ref


def test_funnel_quality_detects_row_corruption_without_crashing(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    missing_component_rows = delta._dataframe_copy().drop(columns=["current_lost_count"])
    missing_component = DeltaFrame(_df=missing_component_rows, meta=delta.meta)

    delta_checks = run_funnel_delta_checks(missing_component)
    delta_status = {row["check_id"]: row["status"] for row in delta_checks}
    assert delta_status["funnel_delta_components"] == "blocking"
    assert delta_status["funnel_delta_row_contract"] == "blocking"

    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=mv.funnel_loss_rate(step=payment_step),
    )
    driver_rows = drivers._dataframe_copy()
    removed_index = driver_rows["contribution"].abs().idxmax()
    truncated_rows = driver_rows.drop(index=removed_index).reset_index(drop=True)
    truncated = AttributionFrame(
        _df=truncated_rows,
        meta=drivers.meta.model_copy(update={"row_count": len(truncated_rows)}),
    )
    attribution_checks = run_funnel_attribution_checks(truncated)
    attribution_status = {row["check_id"]: row["status"] for row in attribution_checks}
    assert attribution_status["funnel_attribution_components"] == "blocking"
    assert attribution_status["funnel_attribution_pools"] == "blocking"
    assert attribution_status["funnel_attribution_residual"] == "blocking"
    assert attribution_status["funnel_attribution_reconciliation"] == "blocking"


def test_cold_recovery_restores_funnel_variants(
    funnel_session_factory: Any,
    payment_step: Any,
) -> None:
    session = funnel_session_factory("funnel-cold-recovery")
    current, baseline = two_scope_funnel_frames(session)
    delta = session.compare(current, baseline)
    channel = session.catalog.dimensions.get("acquisition_channel")
    drivers = session.attribute(
        delta,
        axes=[channel],
        target=mv.funnel_loss_rate(step=payment_step),
    )
    delta_ref = delta.meta.ref
    drivers_ref = drivers.meta.ref
    warm = (delta.render(), delta.contract().render(), drivers.contract().render())
    session.close()

    recovered = funnel_session_factory("funnel-cold-recovery")
    try:
        cold_delta = recovered.get_frame(delta_ref)
        cold_drivers = recovered.get_frame(drivers_ref)
        assert cold_delta.meta.semantic_kind == "funnel"
        assert cold_drivers.meta.semantic_kind == "funnel_loss_rate"
        assert cold_drivers.meta.target.step == payment_step
        assert (
            cold_delta.render(),
            cold_delta.contract().render(),
            cold_drivers.contract().render(),
        ) == warm
    finally:
        recovered.close()
