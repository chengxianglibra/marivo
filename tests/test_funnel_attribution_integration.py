"""Funnel attribution layouts, structured rejections, timing, and rollback."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    FunnelAttributionUnsupportedError,
    InvalidSubjectAxisError,
    PatternStepMismatchError,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.intents.funnel_attribute import attribute_funnel
from marivo.introspection.live.model import LiveHelpTarget
from tests.shared_fixtures import (
    analysis_persistence_snapshot,
    grouped_two_scope_funnel_frames,
    pattern_step_for_tests,
    two_scope_funnel_frames,
)


def _metric_delta(session: Any) -> DeltaFrame:
    metric = session.catalog.require(ms.ref.metric("commerce.order_count")).ref
    current = session.observe(
        metric,
        time_scope={"start": "2026-07-08", "end": "2026-07-15"},
    )
    baseline = session.observe(
        metric,
        time_scope={"start": "2026-07-01", "end": "2026-07-08"},
    )
    return session.compare(current, baseline, alignment=mv.window_bucket())


def _assert_structured_error(
    error: AnalysisError,
    *,
    kind: str,
    location: str,
    repair_kind: str = "user_choice",
) -> None:
    assert error.kind == kind
    assert error.expected
    assert error.received
    assert error.location == location
    repair = error.repair
    assert repair is not None
    assert repair.kind == repair_kind
    assert repair.action
    assert repair.help_target == LiveHelpTarget(
        surface="analysis",
        canonical_id="attribute",
    )


def test_joint_mode_is_additive_and_reconciles_end_to_end(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
    plan_tier_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)

    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry, plan_tier_entry],
        mode="joint",
        target=mv.funnel_loss_rate(step=payment_step),
    )

    rows = drivers.to_pandas()
    assert type(drivers) is AttributionFrame
    assert drivers.meta.mode == "joint"
    assert {"acquisition_channel", "plan_tier"}.issubset(rows.columns)
    assert {
        "attribution_level",
        "attribution_axis",
        "attribution_driver",
        "attribution_path",
    }.isdisjoint(rows.columns)
    assert rows["contribution"].sum() == pytest.approx(
        drivers.meta.reconciliation.target_loss_rate_delta
    )
    positive = rows.loc[rows["contribution"] > 0]
    negative = rows.loc[rows["contribution"] < 0]
    if not positive.empty:
        assert positive["share_of_positive_pool"].sum() == pytest.approx(1.0)
    if not negative.empty:
        assert negative["share_of_negative_pool"].sum() == pytest.approx(1.0)


def test_hierarchy_pool_shares_are_level_local_and_metadata_uses_deepest_pool(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
    plan_tier_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry, plan_tier_entry],
        mode="hierarchy",
        target=mv.funnel_loss_rate(step=payment_step),
    )

    rows = drivers.to_pandas()
    for _, level_rows in rows.groupby("attribution_level"):
        positive = level_rows.loc[level_rows["contribution"] > 0]
        negative = level_rows.loc[level_rows["contribution"] < 0]
        if not positive.empty:
            assert positive["share_of_positive_pool"].sum() == pytest.approx(1.0)
        if not negative.empty:
            assert negative["share_of_negative_pool"].sum() == pytest.approx(1.0)
    deepest = rows.loc[rows["attribution_level"] == rows["attribution_level"].max()]
    assert deepest.loc[deepest["contribution"] > 0, "contribution"].sum() == pytest.approx(
        drivers.meta.reconciliation.positive_pool
    )
    assert deepest.loc[deepest["contribution"] < 0, "contribution"].sum() == pytest.approx(
        drivers.meta.reconciliation.negative_pool
    )
    assert "deepest_positive_pool=" in drivers.render()


def test_attribution_rejects_an_already_grouped_delta_with_repair(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = grouped_two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)

    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
    )
    assert "ungrouped" in excinfo.value.repair.action


@pytest.mark.parametrize(
    ("step_key", "error_type"),
    (
        ("refund", PatternStepMismatchError),
        ("cart", FunnelAttributionUnsupportedError),
    ),
)
def test_attribution_rejects_foreign_and_initial_targets_with_repair(
    funnel_session: Any,
    acquisition_channel_entry: Any,
    step_key: str,
    error_type: type[Exception],
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)

    with pytest.raises(error_type) as excinfo:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=pattern_step_for_tests(step_key)),
        )

    _assert_structured_error(
        excinfo.value,
        kind=(
            "pattern_step_mismatch"
            if error_type is PatternStepMismatchError
            else "funnel_attribution_unsupported"
        ),
        location="session.attribute(target)",
    )


def test_attribution_rejects_missing_target_and_axes_with_repair(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)

    with pytest.raises(FunnelAttributionUnsupportedError) as missing_target:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
        )
    _assert_structured_error(
        missing_target.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(target)",
    )

    with pytest.raises(FunnelAttributionUnsupportedError) as missing_axes:
        funnel_session.attribute(
            delta,
            axes=[],
            target=mv.funnel_loss_rate(step=payment_step),
        )
    _assert_structured_error(
        missing_axes.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(axes)",
    )


def test_attribution_rejects_invalid_multi_axis_mode_with_repair(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
    plan_tier_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)

    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry, plan_tier_entry],
            mode="positional",  # type: ignore[arg-type]
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(mode)",
    )


def test_metric_delta_rejects_a_funnel_target_with_repair(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        funnel_session.attribute(
            _metric_delta(funnel_session),
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(target)",
    )


def test_attribution_retargets_shared_axis_errors_to_its_public_entrypoint(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")

    def reject_fanout(*_args: object, **_kwargs: object) -> None:
        raise InvalidSubjectAxisError(
            message="axis relationship path is not to-one from the journey subject",
            expected="every directed edge is many-to-one or one-to-one",
            received="relationship:commerce.orders_to_items: one_to_many",
            location="session.events.funnel.axes[0]",
            repair=AnalysisRepair(
                kind="semantic_authoring",
                action="Repair relationship keys or choose a fanout-safe subject axis.",
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="events.funnel",
                ),
            ),
        )

    monkeypatch.setattr(module, "resolve_subject_axes", reject_fanout)
    with pytest.raises(InvalidSubjectAxisError) as excinfo:
        funnel_session.attribute(
            delta,
            axes=[ms.ref.dimension("commerce.orders.acquisition_channel")],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="invalid_subject_axis",
        location="session.attribute.axes[0]",
        repair_kind="semantic_authoring",
    )
    assert "fanout-safe" in excinfo.value.repair.action


def test_attribution_rejects_missing_source_journeys_and_components_with_repair(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    missing_meta = delta.meta.model_copy(
        update={"source_current_journey_ref": "art_missing_source_journey"}
    )
    missing_delta = DeltaFrame(_df=delta._dataframe_copy(), meta=missing_meta)

    with pytest.raises(FunnelAttributionUnsupportedError) as absent_journey:
        funnel_session.attribute(
            missing_delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )
    _assert_structured_error(
        absent_journey.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
        repair_kind="inspect",
    )
    assert "art_missing_source_journey" in absent_journey.value.received

    bad_meta = delta.meta.model_copy(
        update={"source_current_journey_ref": delta.meta.artifact_id or delta.ref}
    )
    bad_delta = DeltaFrame(_df=delta._dataframe_copy(), meta=bad_meta)

    with pytest.raises(FunnelAttributionUnsupportedError) as missing_journey:
        funnel_session.attribute(
            bad_delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )
    _assert_structured_error(
        missing_journey.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
        repair_kind="inspect",
    )

    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")

    def reject_components(*_args: object, **_kwargs: object) -> None:
        raise ValueError("zero resolved-entry denominator")

    monkeypatch.setattr(module, "decompose_loss_rate", reject_components)
    with pytest.raises(FunnelAttributionUnsupportedError) as invalid_components:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )
    _assert_structured_error(
        invalid_components.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
        repair_kind="inspect",
    )


def test_attribution_rejects_different_axis_bindings_with_repair(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")
    original = module._target_components
    calls = 0

    def differing_bindings(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        rows, bindings, lineage = original(*args, **kwargs)
        if calls == 2:
            binding = bindings[0].model_copy(update={"output_column": "other_channel"})
            bindings = (binding,)
        return rows, bindings, lineage

    monkeypatch.setattr(module, "_target_components", differing_bindings)
    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(axes)",
        repair_kind="inspect",
    )


def test_attribution_rejects_a_cross_session_delta_with_repair(
    funnel_session: Any,
    funnel_session_factory: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    other = funnel_session_factory("funnel-attribute-other-session")
    try:
        with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
            other.attribute(
                delta,
                axes=[acquisition_channel_entry],
                target=mv.funnel_loss_rate(step=payment_step),
            )
        _assert_structured_error(
            excinfo.value,
            kind="funnel_attribution_unsupported",
            location="session.attribute(frame)",
        )
    finally:
        other.close()


def test_attribution_rejects_unpersisted_delta_with_repair(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    unpersisted = DeltaFrame(
        _df=delta._dataframe_copy(),
        meta=delta.meta.model_copy(update={"content_hash": None}),
    )

    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        funnel_session.attribute(
            unpersisted,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
    )


def test_attribution_job_timestamps_bracket_materialization(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")
    original = module.decompose_loss_rate
    observed: dict[str, datetime] = {}

    def recording_decompose(*args: object, **kwargs: object) -> object:
        observed["computed_at"] = datetime.now(UTC)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "decompose_loss_rate", recording_decompose)
    drivers = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=mv.funnel_loss_rate(step=payment_step),
    )
    job = funnel_session.job(drivers.meta.produced_by_job)

    assert datetime.fromisoformat(job["started_at"]) <= observed["computed_at"]
    assert observed["computed_at"] <= datetime.fromisoformat(job["finished_at"])
    assert drivers.meta.created_at == datetime.fromisoformat(job["finished_at"])
    assert "event_journey" not in job
    assert job["funnel_attribution"]["artifact_ref"] == drivers.ref
    assert job["funnel_attribution"]["source_delta_ref"] == delta.ref
    assert job["funnel_attribution"]["target"]["step"]["key"] == payment_step.key


@pytest.mark.parametrize(
    "failure_target",
    ("register_frame_artifact", "persist_job_record"),
)
def test_attribution_rolls_back_new_output_on_late_failure(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
    acquisition_channel_entry: Any,
    failure_target: str,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")
    before = analysis_persistence_snapshot(funnel_session)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced funnel attribution persistence failure")

    monkeypatch.setattr(module, failure_target, fail)
    with pytest.raises(RuntimeError, match="forced funnel attribution persistence failure"):
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=mv.funnel_loss_rate(step=payment_step),
        )

    assert analysis_persistence_snapshot(funnel_session) == before
    assert funnel_session.get_frame(delta.meta.artifact_id or delta.ref).ref == delta.ref


def test_attribution_preserves_a_preexisting_output_on_late_failure(
    funnel_session: Any,
    monkeypatch: pytest.MonkeyPatch,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    target = mv.funnel_loss_rate(step=payment_step)
    first = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=target,
    )
    module = importlib.import_module("marivo.analysis.intents.funnel_attribute")
    before = analysis_persistence_snapshot(funnel_session)

    monkeypatch.setattr(module, "frame_exists_on_disk", lambda *_args: False)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced funnel attribution persistence failure")

    monkeypatch.setattr(module, "persist_job_record", fail)
    with pytest.raises(RuntimeError, match="forced funnel attribution persistence failure"):
        funnel_session.attribute(
            delta,
            axes=[acquisition_channel_entry],
            target=target,
        )

    assert analysis_persistence_snapshot(funnel_session) == before
    recovered = funnel_session.get_frame(first.meta.artifact_id or first.ref)
    assert recovered.to_pandas().equals(first.to_pandas())


def test_attribute_funnel_rejects_a_metric_delta_directly(
    funnel_session: Any,
    acquisition_channel_entry: Any,
) -> None:
    metric_delta = _metric_delta(funnel_session)

    with pytest.raises(FunnelAttributionUnsupportedError) as excinfo:
        attribute_funnel(
            metric_delta,
            axes=[acquisition_channel_entry],
            mode=None,
            target=None,
            analysis_purpose=None,
            session=funnel_session,
        )

    _assert_structured_error(
        excinfo.value,
        kind="funnel_attribution_unsupported",
        location="session.attribute(frame)",
    )


def test_attribution_funnel_repeated_call_records_reused_invocation_job(
    funnel_session: Any,
    payment_step: Any,
    acquisition_channel_entry: Any,
) -> None:
    """Repeated funnel attribute with different purposes must keep one job per
    invocation, marking the reuse (issue #38, funnel attribute path)."""
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    target = mv.funnel_loss_rate(step=payment_step)

    first = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=target,
        analysis_purpose="first attr purpose",
    )
    second = funnel_session.attribute(
        delta,
        axes=[acquisition_channel_entry],
        target=target,
        analysis_purpose="second attr purpose",
    )

    assert second.ref == first.ref
    attribute_jobs = [
        funnel_session.job(job.id)
        for job in funnel_session.jobs()
        if funnel_session.job(job.id).get("intent") == "attribute.funnel_loss_rate"
    ]
    assert {job.get("analysis_purpose") for job in attribute_jobs} >= {
        "first attr purpose",
        "second attr purpose",
    }
    reused_flags = [job.get("reused_artifact") for job in attribute_jobs]
    assert reused_flags.count(True) == 1
    assert reused_flags.count(False) == 1
    reused = next(job for job in attribute_jobs if job.get("reused_artifact") is True)
    assert reused["analysis_purpose"] == "second attr purpose"
