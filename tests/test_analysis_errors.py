"""AnalysisError hierarchy for the analysis runtime."""

import pytest
from pydantic import ValidationError

from marivo.analysis.errors import (
    AnalysisError,
    AnalysisRepair,
    HelpTargetError,
)
from marivo.introspection.live.model import LiveHelpTarget


def test_base_is_exception():
    assert issubclass(AnalysisError, Exception)


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


def test_analysis_repair_candidates_is_tuple() -> None:
    repair = AnalysisRepair(
        kind="retry",
        action="Use the registered metric id.",
        help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        candidates=["metric.sales.revenue", "metric.sales.orders"],
    )
    assert repair.candidates == ("metric.sales.revenue", "metric.sales.orders")
    assert isinstance(repair.candidates, tuple)


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


# ---------------------------------------------------------------------------
# CumulativeFrameUnsupportedError uses _derive_fields pattern
# ---------------------------------------------------------------------------


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
