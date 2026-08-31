"""Public funnel comparison and attribution over a real DuckDB project."""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.analysis.errors import (
    AnalysisError,
    EventCoverageUnknownError,
    FunnelAttributionUnsupportedError,
    FunnelComparisonMismatchError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames._quality import evaluate_frame_quality
from marivo.analysis.frames._quality_checks import (
    run_funnel_attribution_checks,
    run_funnel_delta_checks,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.intents._event_funnel import (
    FUNNEL_ADDITIVE_COLUMNS,
    funnel_reconciliation_hash,
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
        cohort_window=mv.time_scope(
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
    assert "quality_report" not in contract
    assert "session.correlate" not in contract


def test_compare_persists_declared_completeness(funnel_session: Any) -> None:
    pattern = mv.sequence(
        pattern_step_for_tests("cart"),
        pattern_step_for_tests("payment"),
    )
    declaration = mv.declared_complete_through(
        inputs=(
            ms.ref.event("commerce.order_created"),
            ms.ref.event("commerce.payment_captured"),
        ),
        through="2026-07-22T00:00:00Z",
        rationale="The deterministic comparison fixture is complete.",
    )
    funnels = []
    for start, end in (("2026-07-08", "2026-07-15"), ("2026-07-01", "2026-07-08")):
        journeys = funnel_session.events.match(
            pattern=pattern,
            cohort_window=mv.time_scope(
                start=f"{start}T00:00:00Z",
                end=f"{end}T00:00:00Z",
            ),
            completion_through="2026-07-22T00:00:00Z",
            matching=mv.first_per_subject(),
            completeness=(declaration,),
        )
        funnels.append(funnel_session.events.funnel(journeys))

    delta = funnel_session.compare(funnels[0], funnels[1])

    assert delta.meta.current_completeness == (declaration,)
    assert delta.meta.baseline_completeness == (declaration,)
    job = funnel_session.get_run(delta.meta.produced_by_job)
    assert job.capability_id == "compare"


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
    job = funnel_session.get_run(delta.meta.produced_by_job)

    assert job.started_at <= observed["computed_at"]
    assert observed["computed_at"] <= job.finished_at
    assert delta.meta.created_at == job.finished_at
    assert job.input_artifact_refs == (current.ref, baseline.ref)


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
    assert funnel_session.artifact(current.meta.artifact_id or current.ref).ref == current.ref


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
    recovered = funnel_session.artifact(first.meta.artifact_id or first.ref)
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
    assert {
        "attribution_level",
        "attribution_axis",
        "attribution_driver",
        "attribution_path",
    }.issubset(hierarchy.columns)


def test_quality_summaries_cover_both_new_shapes(
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
    assert delta.quality_summary is not None
    assert delta.quality_summary.evaluated_check_count >= 4
    assert delta.quality_summary.failed_check_count == 0
    assert drivers.quality_summary is not None
    assert drivers.quality_summary.evaluated_check_count >= 4
    assert drivers.quality_summary.failed_check_count == 0
    assert "quality_report" not in delta.contract().render()
    assert "quality_report" not in drivers.contract().render()


def test_empty_funnel_delta_is_row_count_warning_not_row_contract_blocker(
    funnel_session: Any,
) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    empty = DeltaFrame(
        _df=delta._dataframe_copy().iloc[0:0],
        meta=delta.meta.model_copy(update={"row_count": 0, "zero_filled_tuple_count": 0}),
    )

    checks = {row["check_id"]: row["severity"] for row in run_funnel_delta_checks(empty)}

    assert checks["row_count"] == "warning"
    assert checks["funnel_delta_row_contract"] == "ok"
    assert "blocking" not in checks.values()


def test_empty_event_funnel_is_warning_when_reconciliation_remains_valid(
    funnel_session: Any,
) -> None:
    source, _baseline = two_scope_funnel_frames(funnel_session)
    empty_df = source._dataframe_copy().iloc[0:0]
    step_keys = tuple(step.key for step in source.meta.pattern.steps)
    totals = (
        empty_df.groupby("step_key", dropna=False, sort=False)[list(FUNNEL_ADDITIVE_COLUMNS)]
        .sum()
        .reindex(step_keys, fill_value=0)
        .reset_index()
    )
    empty_hash = funnel_reconciliation_hash(totals, step_keys=step_keys)
    receipt = source.meta.grouped_reconciliation.model_copy(
        update={"status": "pass", "ungrouped_hash": empty_hash, "grouped_hash": empty_hash}
    )
    empty = EventFrame(
        _df=empty_df,
        meta=source.meta.model_copy(
            update={
                "ref": "frame_empty_event_funnel",
                "row_count": 0,
                "grouped_reconciliation": receipt,
            }
        ),
    )

    evaluation = evaluate_frame_quality(empty, artifact_id=empty.ref)
    assert evaluation is not None
    checks = dict(
        zip(
            evaluation.dataframe["check_id"],
            evaluation.dataframe["severity"],
            strict=True,
        )
    )

    assert checks["row_count"] == "warning"
    assert checks["event_funnel_row_contract"] == "ok"
    assert evaluation.overall_status == "warning"
    assert evaluation.blocking_issue_count == 0


def test_empty_funnel_attribution_is_warning_with_undefined_zero_receipt(
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
    receipt = drivers.meta.reconciliation.model_copy(
        update={
            "target_loss_rate_delta": None,
            "contribution_sum": None,
            "positive_pool": 0.0,
            "negative_pool": 0.0,
            "residual": 0.0,
            "max_abs_residual": 0.0,
        }
    )
    empty = AttributionFrame(
        _df=drivers._dataframe_copy().iloc[0:0],
        meta=drivers.meta.model_copy(
            update={
                "ref": "frame_empty_funnel_attribution",
                "row_count": 0,
                "reconciliation": receipt,
            }
        ),
    )

    evaluation = evaluate_frame_quality(empty, artifact_id=empty.ref)
    assert evaluation is not None

    assert evaluation.overall_status == "warning"
    assert evaluation.blocking_issue_count == 0
    assert {issue.kind for issue in evaluation.issues} == {"sample_size_low"}


def test_funnel_delta_unknown_coverage_is_warning(funnel_session: Any) -> None:
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    unknown = DeltaFrame(
        _df=delta._dataframe_copy(),
        meta=delta.meta.model_copy(
            update={
                "current_coverage_basis": "unknown",
                "baseline_coverage_basis": "unknown",
            }
        ),
    )

    checks = {row["check_id"]: row["severity"] for row in run_funnel_delta_checks(unknown)}

    assert checks["funnel_delta_coverage"] == "warning"
    assert "blocking" not in checks.values()


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


def test_funnel_delta_does_not_pretend_metric_delta_semantics(
    funnel_session: Any,
) -> None:
    """FunnelDelta consumers must dispatch on the closed union, not project
    Metric Delta optional facets onto a funnel shape.

    A funnel delta has no component graph, no metric additivity/aggregation,
    no cumulative alignment, and no metric display identity. The removed
    compatibility projections let generic readers silently treat those as
    absent Metric facets; the continuation contract is instead a closed
    registry-owned ``SemanticKindMismatchError`` so the funnel shape exposes only its own
    fields.
    """
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    assert type(delta.meta).__name__ == "FunnelDeltaFrameMeta"
    assert delta.meta.semantic_kind == "funnel"

    # Metric-only facets are no longer projected onto the funnel shape.
    for removed in (
        "component_ref",
        "composition",
        "status_time_dimension",
        "fold",
        "additivity",
        "aggregation",
        "cumulative",
        "metric_id",
        "semantic_model",
    ):
        assert not hasattr(delta.meta, removed), removed

    # components() fails closed instead of silently pretending there is no
    # component graph (the old projection returned None for component_ref).
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        delta.components()
    assert excinfo.value.received == "funnel"
    assert excinfo.value.location == "DeltaFrame.components.receiver.semantic_shape"


def test_metric_delta_retains_metric_semantics(funnel_session: Any) -> None:
    """The metric DeltaFrameMeta keeps its component and metric facets; the
    funnel continuation matrix must not over-narrow the generic shape."""
    metric = funnel_session.catalog.require(ms.ref.metric("commerce.order_count"))
    current = funnel_session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-07-01", end="2026-07-15"),
    )
    baseline = funnel_session.observe(
        metric,
        time_scope=mv.time_scope(start="2026-06-01", end="2026-06-15"),
    )
    delta = funnel_session.compare(current, baseline)
    assert delta.meta.semantic_kind in {"scalar", "time_series", "segmented", "panel"}
    assert hasattr(delta.meta, "component_ref")
    assert hasattr(delta.meta, "composition")
    assert hasattr(delta.meta, "additivity")
    assert hasattr(delta.meta, "metric_id")


def test_funnel_delta_transform_fails_closed(funnel_session: Any) -> None:
    """transform is a Metric continuation; a funnel delta must reject it
    explicitly instead of projecting a nonexistent metric contract."""
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        delta.transform.filter(predicate=lambda df: df["step_key"] == "cart")
    assert excinfo.value.received == "funnel"
    assert excinfo.value.location == "transform.filter.receiver.semantic_shape"


def test_funnel_delta_contract_hides_metric_only_affordances(
    funnel_session: Any,
) -> None:
    """contract() must advertise exactly the real funnel continuations.

    The surviving affordance set is the closed-union contract: a new
    _MF_OR_DF transform op or a leaked Metric-only continuation must make
    this assertion red so its author decides explicitly whether it applies
    to a funnel delta.
    """
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    assert delta.meta.semantic_kind == "funnel"

    advertised = {affordance.capability_id for affordance in delta.contract().affordances}
    assert advertised == {
        "attribute",
    }


def test_funnel_delta_discover_rejects_objective_with_expected_kind(
    funnel_session: Any,
) -> None:
    """A discover objective fed a funnel delta fails closed with the funnel
    kind and the objective's accepted semantic kinds in context."""
    current, baseline = two_scope_funnel_frames(funnel_session)
    delta = funnel_session.compare(current, baseline)
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        funnel_session.discover.period_shifts(delta)
    assert excinfo.value.received == "funnel"
    assert excinfo.value.expected == "panel | time_series"
    assert excinfo.value.location == "discover.period_shifts.source.semantic_shape"


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
        cold_delta = recovered.artifact(delta_ref)
        cold_drivers = recovered.artifact(drivers_ref)
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


def test_compare_funnel_repeated_call_records_reused_invocation_job(
    funnel_session: Any,
) -> None:
    """Repeated funnel compare with different purposes must keep one job per
    invocation, marking the reuse (issue #38, funnel compare path)."""
    current, baseline = two_scope_funnel_frames(funnel_session)

    first = funnel_session.compare(
        current,
        baseline,
        analysis_purpose="first funnel purpose",
    )
    second = funnel_session.compare(
        current,
        baseline,
        analysis_purpose="second funnel purpose",
    )

    assert second.ref == first.ref
    compare_purposes = {
        run.analysis_purpose
        for run in funnel_session.runs(capability_id="compare", limit=100).items
    }
    assert "first funnel purpose" in compare_purposes
    assert "second funnel purpose" in compare_purposes
    reused = [
        run
        for run in funnel_session.runs(capability_id="compare", limit=100).items
        if run.output_mode == "reused"
    ]
    assert len(reused) == 1
    assert reused[0].analysis_purpose == "second funnel purpose"
