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
    SourceBindingError,
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
        SourceBindingError,
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
    ("kind", "help_target"),
    [
        ("retry", "observe"),
        ("retry", "compare"),
        ("inspect", "artifacts"),
        ("environment", "observe"),
    ],
)
def test_frame_meta_invalid_explicit_repair_is_forwarded(kind: str, help_target: str) -> None:
    """Issue #65: every construction site passes a typed repair explicitly.

    The class no longer derives a repair from ``context`` (issue #65 review
    noted the 22-raise baseline was wrong and helpers are the lever). A repair
    passed at the construction site must reach the agent unchanged through
    ``.repair`` and ``str(e)``.
    """
    repair = AnalysisRepair(
        kind=kind,  # type: ignore[arg-type]
        action="Re-run the producing intent to regenerate the frame.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id=help_target),  # type: ignore[arg-type]
    )
    error = FrameMetaInvalidError(
        message="frame 'frame_a' is corrupt",
        context={"ref": "frame_a", "reason": "metadata fails validation"},
        repair=repair,
        location="frame 'frame_a'",
    )

    assert error.repair is repair
    assert error.repair.kind == kind
    assert error.location == "frame 'frame_a'"
    assert "Repair:" in str(error)


def test_frame_meta_invalid_explicit_repair_wins_over_derived() -> None:
    """An explicitly passed repair must not be clobbered by the derived one."""
    explicit = AnalysisRepair(
        kind="retry",
        action="Re-run the analysis to regenerate the frame.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
    )
    error = FrameMetaInvalidError(
        message="frame 'frame_a' uses unsupported artifact schema 'v7'",
        expected="analysis-artifact/v10",
        received="analysis-artifact/v7",
        repair=explicit,
        context={"ref": "frame_a", "got": "v7", "expected": "v10"},
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


def test_session_question_mismatch_error_removed_from_public_errors() -> None:
    import marivo.analysis.errors as errors

    assert not hasattr(errors, "SessionQuestionMismatchError")


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


# ---------------------------------------------------------------------------
# Issue #65: the three _load/candidate helpers must carry typed repairs
# ---------------------------------------------------------------------------
# Review (note_19814717) corrected the baseline from 22 to 54 raise points:
# three helpers construct the error and are raised 32 times. The class-level
# _derive_fields dispatch is removed (construction sites carry the repair
# explicitly), so the helpers must pass a typed repair themselves. These tests
# pin the helper contracts so removing the class-level derivation does not
# regress any of the 14 (_current_metric_state_error) / 9 (invalid) raise
# sites.


def test_current_metric_state_error_carries_typed_repair() -> None:
    """_current_metric_state_error (14 raise sites) must yield a typed repair."""
    from marivo.analysis.session._load import _current_metric_state_error

    err = _current_metric_state_error(
        "frame_a",
        path="expression_graph",
        reason="fingerprint does not match the canonical graph roots",
    )

    assert isinstance(err.repair, AnalysisRepair)
    assert err.repair.kind == "retry"
    assert err.repair.help_target.surface == "analysis"
    assert err.repair.help_target.canonical_id == "observe"
    assert "Re-run observe" in err.repair.action
    # A repair that renders into str(e) is what the agent actually sees.
    assert "Repair:" in str(err)


def test_delta_identity_recovery_error_carries_typed_repair() -> None:
    """_delta_identity_recovery_error (9 raise sites) already carries one."""
    from marivo.analysis.session._load import _delta_identity_recovery_error

    err = _delta_identity_recovery_error("frame_a", reason="source identity is missing")

    assert isinstance(err.repair, AnalysisRepair)
    assert err.repair.kind == "retry"
    assert err.repair.help_target.canonical_id == "compare"
    assert err.repair.snippet == "delta = session.compare(current, baseline, alignment=alignment)"
    assert "Repair:" in str(err)


def test_candidate_integrity_invalid_helper_carries_typed_repair() -> None:
    """candidate_identity.invalid (9 raise sites) must yield a typed repair."""
    # Reach the nested `invalid` helper through its public entry point with a
    # row that fails coordinate restoration.
    import pandas as pd

    from marivo.analysis.candidate_identity import (
        validate_semantic_hypothesis_frame_integrity,
    )

    df = pd.DataFrame(
        {
            "item_id": ["x"],
            "semantic_edge_ref": [object()],  # not a decodeable JSON cell
            "candidate_semantic_ref": [object()],
            "metric_ref": [object()],
            "edge_relation": ["influences"],
        }
    )
    with pytest.raises(FrameMetaInvalidError) as exc_info:
        validate_semantic_hypothesis_frame_integrity(
            dataframe=df,
            edge_contexts=(),
            readiness_fingerprints={},
            exclusions=(),
        )

    assert isinstance(exc_info.value.repair, AnalysisRepair)
    assert exc_info.value.repair.kind == "retry"
    assert exc_info.value.repair.help_target.surface == "analysis"
    assert exc_info.value.repair.help_target.canonical_id == "discover"
    assert "Re-run the candidate-producing intent" in exc_info.value.repair.action
    # Issue #65 review P2-1: session.discover is a property namespace, not
    # callable; a snippet like "session.discover(...)" would TypeError. The
    # candidate repair must not ship an un-executable snippet.
    assert exc_info.value.repair.snippet is None
    # Issue #65 review (location): without _derive_fields, location must be
    # carried explicitly by the construction site, not fall back to bare "frame".
    assert exc_info.value.location is not None
    assert "CandidateSet integrity" in exc_info.value.location
    assert "Repair:" in str(exc_info.value)


def test_candidate_identity_repair_has_no_unexecutable_snippet() -> None:
    """Issue #65 review P2-1: candidate repairs must not suggest calling
    ``session.discover(...)`` directly (it is a property namespace, not a
    callable), and should point at the ``discover`` help target."""
    import pandas as pd

    from marivo.analysis.candidate_identity import validate_candidate_frame_identity

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        validate_candidate_frame_identity(
            shape="point_anomaly",
            source_artifact_ref="art_a",
            dataframe=pd.DataFrame({"item_id": ["x", "x"]}),
        )

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "retry"
    assert repair.help_target.canonical_id == "discover"
    assert repair.snippet is None
    # Location carried by the construction site (no _derive_fields fallback).
    assert exc_info.value.location is not None
    assert "item_id identity" in exc_info.value.location


def test_candidate_columns_repair_has_no_unexecutable_snippet() -> None:
    """Issue #65 review P2-1: _candidate_columns repairs must not ship a
    session.discover(...) call snippet."""
    import pandas as pd

    from marivo.analysis.intents._candidate_columns import validate_shape_columns

    df = pd.DataFrame(
        {
            "item_id": pd.Series(["c"] * 8, dtype="string"),
            "score": pd.Series([0.5] * 8, dtype="float64"),
            "reason_codes_json": pd.Series(["[]"] * 8, dtype="string"),
            "source_refs_json": pd.Series(["[]"] * 8, dtype="string"),
            # missing required direction / observed_value / baseline_value / delta
            "direction": pd.Series([None] * 8, dtype="string"),
            "observed_value": pd.Series([None] * 8, dtype="float64"),
            "baseline_value": pd.Series([None] * 8, dtype="float64"),
            "delta": pd.Series([None] * 8, dtype="float64"),
        }
    )
    with pytest.raises(FrameMetaInvalidError) as exc_info:
        validate_shape_columns("point_anomaly", df)

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.help_target.canonical_id == "discover"
    assert repair.snippet is None
    # Location carried by the construction site (no _derive_fields fallback).
    assert exc_info.value.location is not None
    assert "column" in exc_info.value.location
    assert "point_anomaly shape" in exc_info.value.location


def test_every_frame_meta_invalid_construction_site_carries_repair_and_location() -> None:
    """Issue #65 review P3-2: every ``FrameMetaInvalidError(...)`` construction
    site must pass both ``repair`` and ``location`` explicitly.

    The class does not derive either from ``context`` (the class-level
    ``_derive_fields`` was removed in Plan A), so the docstring promise is
    enforced by an AST contract test rather than by discipline. This covers
    future sites too: a new ``raise FrameMetaInvalidError(...)`` that omits
    ``repair`` or ``location`` fails here regardless of runtime test coverage.
    """
    import ast
    import pathlib

    def module_files(root: str) -> list[pathlib.Path]:
        base = pathlib.Path(root)
        return [
            p
            for p in base.rglob("*.py")
            if p.is_file() and ".venv" not in p.parts and "site-packages" not in p.parts
        ]

    offenders: list[str] = []
    sites = 0
    for path in module_files("marivo"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else (fn.attr if isinstance(fn, ast.Attribute) else None)
            )
            if name != "FrameMetaInvalidError":
                continue
            sites += 1
            kws = {kw.arg for kw in node.keywords if kw.arg}
            missing = sorted({"repair", "location"} - kws)
            if missing:
                offenders.append(f"{path}:{node.lineno} missing {missing}")
            # Issue #65 review P2-1: a stable field key inside context but with
            # no matching explicit kwarg is a dead key (the class no longer
            # derives it), so the agent can never see it. Reject the shape.
            for kw in node.keywords:
                if kw.arg != "context" or not isinstance(kw.value, ast.Dict):
                    continue
                context_keys = {
                    ast.literal_eval(k) for k in kw.value.keys if isinstance(k, ast.Constant)
                }
                dead = sorted(context_keys & {"expected", "received", "got"})
                if dead:
                    offenders.append(
                        f"{path}:{node.lineno} dead context key(s) {dead} (pass as explicit kwarg or drop)"
                    )

    assert sites >= 20, f"expected FrameMetaInvalidError construction sites, found {sites}"
    assert offenders == [], (
        f"construction sites missing repair/location:\n{chr(10).join(offenders)}"
    )
