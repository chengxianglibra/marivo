"""AnalysisError hierarchy for the analysis runtime."""

import pytest
from pydantic import ValidationError

import marivo.semantic as ms
from marivo.analysis.errors import (
    AlignmentFailedError,
    AnalysisError,
    AnalysisRepair,
    AttributeAdmissionBlockedError,
    AttributionBasisMismatchError,
    AttributionDistributionError,
    AttributionResolutionError,
    AttributionShapeUnavailableError,
    BackendError,
    CrossBackendMetricError,
    CrossSessionFrameError,
    DimensionFieldNotFoundError,
    DuplicateSessionNameError,
    FrameMetaInvalidError,
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
    for kind in ("retry", "inspect", "user_choice", "semantic_authoring", "environment"):
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


@pytest.mark.parametrize(
    ("error_type", "repair_kind", "help_target"),
    [
        (AttributionBasisMismatchError, "retry", "attribute"),
        (AttributionShapeUnavailableError, "inspect", "attribute"),
        (AttributeAdmissionBlockedError, "inspect", "attribute"),
        (AttributionResolutionError, "retry", "AttributionFrame.at_resolution"),
    ],
)
def test_attribution_errors_derive_actionable_repairs(
    error_type: type[AnalysisError],
    repair_kind: str,
    help_target: str,
) -> None:
    error = error_type(message="test attribution error")

    assert error.repair is not None
    assert error.repair.kind == repair_kind
    assert error.repair.action
    assert error.repair.help_target.canonical_id == help_target


@pytest.mark.parametrize(
    ("reason", "repair_kind", "action_fragment"),
    [
        ("empty_coalition_distribution", "retry", "overlapping partitions"),
        ("partition_limit_exceeded", "retry", "lower-cardinality axis"),
        ("frequency_row_limit_exceeded", "retry", "lower-cardinality axis"),
        ("endpoint_reproduction_mismatch", "inspect", "active datasource"),
        (None, "inspect", "persisted source method"),
    ],
)
def test_attribution_distribution_error_derives_reason_specific_repair(
    reason: str | None,
    repair_kind: str,
    action_fragment: str,
) -> None:
    context = {} if reason is None else {"reason": reason}
    error = AttributionDistributionError(message="test distribution error", context=context)

    assert error.repair is not None
    assert error.repair.kind == repair_kind
    assert action_fragment in error.repair.action
    assert error.repair.help_target.canonical_id == "attribute"


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(
            {
                "ref": "frame_a",
                "artifact_schema_version": "analysis-artifact/v8",
                "path": "lineage",
                "reason": "typed replay params are missing",
            },
            id="metric-state",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "artifact_schema_version": "analysis-artifact/v8",
                "missing_fields": ["attribution_basis"],
            },
            id="missing-fields",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "artifact_schema_version": "analysis-artifact/v8",
                "missing_state": ["comparison_identity"],
            },
            id="missing-state",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "artifact_schema_version": "analysis-artifact/v8",
                "validation_errors": [{"msg": "boom"}],
            },
            id="validation-errors",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "got_semantic_kind": "bogus",
                "expected_semantic_kinds": ("journey", "funnel"),
            },
            id="semantic-shape",
        ),
        pytest.param(
            {"ref": "frame_a", "got_columns": ["x"], "expected_columns": ["item_id"]},
            id="candidate-columns",
        ),
        pytest.param(
            {"kind": "CandidateIdentityInvalid", "reason": "duplicate"},
            id="candidate-identity",
        ),
        pytest.param(
            {
                "artifact_id": "artifact_x",
                "got": "analysis-artifact/v7",
                "expected": "analysis-artifact/v8",
            },
            id="non-current-schema",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "kind": "unsupported_artifact_schema",
                "expected": "cumulative-delta/v1",
                "received": None,
            },
            id="cumulative-schema",
        ),
        pytest.param(
            {
                "ref": "frame_a",
                "artifact_schema_version": "analysis-artifact/v8",
                "expected_basis_fingerprint": "abc",
            },
            id="attribution-basis",
        ),
    ],
)
def test_frame_meta_invalid_derives_repair_from_context(context: dict[str, object]) -> None:
    """Issue #65: context-only FrameMetaInvalidError raises must yield an
    actionable, machine-readable repair instead of a bare message."""
    error = FrameMetaInvalidError(message="test frame meta invalid", context=context)

    assert error.repair is not None
    assert error.repair.kind in {"retry", "inspect", "environment"}
    assert error.repair.action
    assert error.repair.help_target.surface == "analysis"
    assert error.repair.help_target.canonical_id in {"observe", "compare", "artifacts", "recovery"}
    # A repair that renders into str(e) is what the agent actually sees.
    assert "Repair:" in str(error)


def test_frame_meta_invalid_derives_location_from_ref() -> None:
    error = FrameMetaInvalidError(
        message="frame 'frame_a' is corrupt",
        context={"ref": "frame_a", "reason": "metadata fails validation"},
    )

    assert error.location is not None
    assert "frame_a" in error.location


def test_frame_meta_invalid_explicit_repair_wins_over_derived() -> None:
    """An explicitly passed repair must not be clobbered by the derived one."""
    explicit = AnalysisRepair(
        kind="retry",
        action="Re-run the analysis to regenerate the frame.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
    )
    error = FrameMetaInvalidError(
        message="frame 'frame_a' uses unsupported artifact schema 'v7'",
        expected="analysis-artifact/v8",
        received="analysis-artifact/v7",
        repair=explicit,
        context={"ref": "frame_a", "got": "v7", "expected": "v8"},
    )

    assert error.repair is explicit
    assert error.repair.kind == "retry"
    assert error.repair.action == "Re-run the analysis to regenerate the frame."


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
        snippet='session.observe(catalog.require(ms.ref.metric("sales.revenue")), time_scope=window)',
        candidates=("metric.sales.revenue",),
    )
    error = MetricNotFoundError(
        message="metric is not registered",
        expected="registered metric semantic object",
        received="metric.sales.revene",
        location="observe.metrics",
        repair=repair,
    )

    assert error.expected == "registered metric semantic object"
    assert error.received == "metric.sales.revene"
    assert error.location == "observe.metrics"
    assert error.repair == repair
    assert "Help: marivo.help('analysis.observe')" in str(error)


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
    assert "marivo.help('analysis')" in rendered


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
    assert err.location == "marivo.help.target"


# ---------------------------------------------------------------------------
# semantic_authoring vs retry repair dispatch for metric/dimension lookup
# ---------------------------------------------------------------------------


def test_metric_not_found_uses_retry_when_candidates_exist() -> None:
    """When available_ids has close matches, repair kind is 'retry' with candidates."""

    err = MetricNotFoundError(
        message="metric 'revenu' is not registered",
        context={
            "metric_ref": ms.ref.metric("sales.revenu"),
            "available_refs": [
                ms.ref.metric("sales.revenue"),
                ms.ref.metric("sales.orders"),
            ],
        },
    )

    assert err.repair is not None
    assert err.repair.kind == "retry"
    assert err.repair.candidates == ("metric:sales.revenue", "metric:sales.orders")
    assert err.repair.help_target == LiveHelpTarget(surface="analysis", canonical_id="observe")
    assert err.received == "metric:sales.revenu"


def test_metric_not_found_uses_semantic_authoring_when_no_candidates() -> None:
    """When available_ids is empty, repair routes to semantic authoring."""

    err = MetricNotFoundError(
        message="metric 'nonexistent' is not registered",
        context={
            "metric_ref": ms.ref.metric("sales.nonexistent"),
            "available_refs": [],
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
    assert err.received == "metric:sales.nonexistent"
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

    assert err.expected == "a cumulative frame supported by the selected intent"
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
    assert "compatible cumulative anchor" in err.hint
