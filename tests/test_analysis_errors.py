"""AnalysisError hierarchy for the analysis runtime."""

import pytest
from pydantic import ValidationError

from marivo.analysis.errors import (
    AlignmentFailedError,
    AnalysisError,
    AnalysisRepair,
    BackendError,
    CrossBackendMetricError,
    CrossSessionFrameError,
    DimensionFieldNotFoundError,
    DuplicateSessionNameError,
    FrameMutationError,
    FrameRefNotFound,
    HelpTargetError,
    MetricNotFoundError,
    NoActiveSessionError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SessionStateError,
    SliceAmbiguousError,
    SliceEmptyResultError,
    SliceInvalidError,
    WindowAmbiguousError,
    WindowInvalidError,
)
from marivo.introspection.live.model import LiveHelpTarget


def test_base_is_exception():
    assert issubclass(AnalysisError, Exception)


@pytest.mark.parametrize(
    "cls",
    [
        AlignmentFailedError,
        BackendError,
        CrossBackendMetricError,
        CrossSessionFrameError,
        DuplicateSessionNameError,
        FrameMutationError,
        FrameRefNotFound,
        HelpTargetError,
        MetricNotFoundError,
        NoActiveSessionError,
        NoBackendFactoryError,
        SemanticKindMismatchError,
        SessionStateError,
        SliceAmbiguousError,
        SliceEmptyResultError,
        SliceInvalidError,
        WindowAmbiguousError,
        WindowInvalidError,
    ],
)
def test_all_subclasses_are_analysis_errors(cls):
    assert issubclass(cls, AnalysisError)


def test_analysis_repair_accepts_known_kinds() -> None:
    for kind in ("retry", "inspect", "semantic_authoring", "environment"):
        repair = AnalysisRepair(
            kind=kind,
            action="do something",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )
        assert repair.kind == kind


def test_analysis_repair_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        AnalysisRepair(
            kind="custom",  # type: ignore[arg-type]
            action="do something",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )


def test_analysis_repair_is_frozen() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Use the registered metric id.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
    )
    with pytest.raises(ValidationError):
        repair.action = "mutated"  # type: ignore[misc]


def test_analysis_repair_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AnalysisRepair(
            kind="retry",
            action="do something",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_analysis_repair_defaults() -> None:
    repair = AnalysisRepair(
        kind="inspect",
        action="Check the catalog.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="help"),
    )
    assert repair.snippet is None
    assert repair.candidates == ()


def test_analysis_repair_candidates_is_tuple() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Use the registered metric id.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        candidates=["metric.sales.revenue", "metric.sales.orders"],
    )
    assert repair.candidates == ("metric.sales.revenue", "metric.sales.orders")
    assert isinstance(repair.candidates, tuple)


def test_actionable_analysis_error_exposes_typed_repair() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Use the registered metric id.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        snippet='session.observe(catalog.get("metric.sales.revenue"), time_scope=window)',
        candidates=("metric.sales.revenue",),
    )
    error = MetricNotFoundError(
        message="metric is not registered",
        expected="registered metric semantic object",
        received="metric.sales.revene",
        location="observe.metric",
        repair=repair,
    )

    assert error.expected == "registered metric semantic object"
    assert error.received == "metric.sales.revene"
    assert error.location == "observe.metric"
    assert error.repair == repair
    assert "Help: mv.help('observe')" in str(error)


def test_analysis_error_has_no_details_property() -> None:
    err = AnalysisError(message="something happened")
    assert not hasattr(err, "details")


def test_analysis_error_stable_fields_default_to_none() -> None:
    err = AnalysisError(message="something happened")
    assert err.expected is None
    assert err.received is None
    assert err.location is None
    assert err.repair is None


def test_analysis_error_context_is_private() -> None:
    err = AnalysisError(message="something happened", context={"key": "value"})
    assert not hasattr(err, "details")
    assert err._context == {"key": "value"}


def test_str_includes_kind_and_message():
    err = SliceInvalidError(message="field 'foo' not found on dataset 'orders'")
    s = str(err)
    assert "SliceInvalid" in s
    assert "orders" in s


def test_optional_hint_defaults_from_catalog() -> None:
    err = FrameMutationError(message="frame is immutable")
    assert (
        err.hint
        == "Call frame.to_pandas() and mutate the copy when ad hoc analysis needs local changes."
    )


def test_transform_op_unsupported_error_removed_from_public_errors() -> None:
    import marivo.analysis.errors as errors

    assert not hasattr(errors, "TransformOpUnsupportedError")


def test_help_target_error_is_analysis_error() -> None:
    err = HelpTargetError(target=123, suggestions=("observe", "compare"))
    assert isinstance(err, AnalysisError)


def test_help_target_error_renders_received_type_for_non_string() -> None:
    err = HelpTargetError(target=123, suggestions=("observe",))
    rendered = str(err)
    assert "int" in rendered
    assert "mv.help('help')" in rendered


def test_help_target_error_renders_received_string() -> None:
    err = HelpTargetError(target="observ", suggestions=("observe",))
    rendered = str(err)
    assert "observ" in rendered
    assert "observe" in rendered


def test_help_target_error_carries_suggestions_as_candidates() -> None:
    err = HelpTargetError(target="observ", suggestions=("observe", "compare"))
    assert err.repair is not None
    assert err.repair.kind == "inspect"
    assert "observe" in err.repair.candidates
    assert "compare" in err.repair.candidates


def test_help_target_error_location_is_help_target() -> None:
    err = HelpTargetError(target="observ", suggestions=("observe",))
    assert err.location == "mv.help.target"


# ---------------------------------------------------------------------------
# semantic_authoring vs retry repair dispatch for metric/dimension lookup
# ---------------------------------------------------------------------------


def test_metric_not_found_uses_retry_when_candidates_exist() -> None:
    """When available_ids has close matches, repair kind is 'retry' with candidates."""

    err = MetricNotFoundError(
        message="metric 'revenu' is not registered",
        context={
            "metric_id": "sales.revenu",
            "available_ids": ["sales.revenue", "sales.orders"],
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "retry"
    assert err.repair.candidates == ("sales.revenue", "sales.orders")
    assert err.repair.help_target == LiveHelpTarget(surface="analysis", canonical_id="observe")
    assert err.received == "sales.revenu"


def test_metric_not_found_uses_semantic_authoring_when_no_candidates() -> None:
    """When available_ids is empty, repair routes to semantic authoring."""

    err = MetricNotFoundError(
        message="metric 'nonexistent' is not registered",
        context={
            "metric_id": "sales.nonexistent",
            "available_ids": [],
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "semantic_authoring"
    assert err.repair.candidates == ()
    assert err.repair.help_target == LiveHelpTarget(surface="semantic")
    assert set(type(err.repair).model_fields) == {
        "kind",
        "action",
        "help_target",
        "snippet",
        "candidates",
    }
    assert err.received == "sales.nonexistent"
    assert "md.raw_sql" in err.repair.action
    assert "closeout" in err.repair.action
    assert err.repair.snippet is None


def test_metric_not_found_uses_semantic_authoring_when_available_ids_absent() -> None:
    """When available_ids is absent, repair routes to semantic authoring."""

    err = MetricNotFoundError(
        message="metric 'foo' is not registered",
        context={"metric_id": "sales.foo"},
    )

    assert err.repair is not None
    assert err.repair.kind == "semantic_authoring"
    assert err.repair.candidates == ()


def test_dimension_field_not_found_uses_retry_when_candidates_exist() -> None:
    """When available_ids has close matches, repair kind is 'retry' with candidates."""

    err = DimensionFieldNotFoundError(
        message="dimension 'regio' not found on metric datasets",
        context={
            "dimension_id": "regio",
            "available_ids": ["region", "country"],
            "searched_datasets": ["orders"],
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "retry"
    assert err.repair.candidates == ("region", "country")
    assert err.repair.help_target == LiveHelpTarget(surface="analysis", canonical_id="observe")
    assert err.received == "regio"


def test_dimension_field_not_found_uses_semantic_authoring_when_no_candidates() -> None:
    """When available_ids is empty, repair routes to semantic authoring."""

    err = DimensionFieldNotFoundError(
        message="dimension 'unknown' not found on metric datasets",
        context={
            "dimension_id": "unknown",
            "available_ids": [],
            "searched_datasets": ["orders"],
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "semantic_authoring"
    assert err.repair.candidates == ()
    assert err.repair.help_target == LiveHelpTarget(surface="semantic")
    assert set(type(err.repair).model_fields) == {
        "kind",
        "action",
        "help_target",
        "snippet",
        "candidates",
    }
    assert err.received == "unknown"
    assert "md.raw_sql" in err.repair.action
    assert "closeout" in err.repair.action
    assert err.repair.snippet is None


# ---------------------------------------------------------------------------
# CumulativeFrameUnsupportedError uses _derive_fields pattern
# ---------------------------------------------------------------------------


def test_cumulative_frame_unsupported_derives_fields_via_derive_fields() -> None:
    """CumulativeFrameUnsupportedError must derive fields via _derive_fields, not mutation."""

    from marivo.analysis.errors import CumulativeFrameUnsupportedError

    err = CumulativeFrameUnsupportedError(
        intent="forecast",
        frame_ref="frame-1",
        metric_id="sales.gmv",
        cumulative={"base": "sales.gmv_base", "kind": "all_history"},
    )

    assert err.expected == "period-level flow metric frame"
    assert err.received == "cumulative metric frame"
    assert err.location == "session.forecast"
    assert err.repair is not None
    assert err.repair.kind == "retry"
    assert err.repair.help_target == LiveHelpTarget(surface="analysis", canonical_id="forecast")
    assert "sales.gmv_base" in err.repair.action
    assert "forecast the base flow" in err.hint.lower()


def test_cumulative_frame_unsupported_derives_fields_for_compare() -> None:
    """Verify _derive_fields works for a non-forecast intent."""

    from marivo.analysis.errors import CumulativeFrameUnsupportedError

    err = CumulativeFrameUnsupportedError(
        intent="compare",
        frame_ref="frame-2",
        metric_id="sales.gmv",
        cumulative={"base": "sales.gmv_base", "kind": "all_history"},
    )

    assert err.location == "session.compare"
    assert err.repair is not None
    assert err.repair.help_target == LiveHelpTarget(surface="analysis", canonical_id="compare")
    assert "base total over that window" in err.hint
