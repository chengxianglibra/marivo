"""AnalysisError hierarchy for the analysis_py runtime."""

import pytest

from marivo.analysis_py.errors import (
    AlignmentFailedError,
    AnalysisError,
    BackendError,
    CrossBackendMetricError,
    CrossSessionFrameError,
    DuplicateSessionNameError,
    FrameMutationError,
    FrameRefNotFound,
    MetricNotFoundError,
    NoActiveSessionError,
    NoBackendFactoryError,
    SemanticKindMismatchError,
    SessionStateError,
    SliceAmbiguousError,
    SliceInvalidError,
    WindowAmbiguousError,
    WindowInvalidError,
)


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
        MetricNotFoundError,
        NoActiveSessionError,
        NoBackendFactoryError,
        SemanticKindMismatchError,
        SessionStateError,
        SliceAmbiguousError,
        SliceInvalidError,
        WindowAmbiguousError,
        WindowInvalidError,
    ],
)
def test_all_subclasses_are_analysis_errors(cls):
    assert issubclass(cls, AnalysisError)


def test_error_carries_kind_message_hint_details():
    err = MetricNotFoundError(
        message="metric 'sales.revenue' not found",
        hint="Check that sales is a loaded model.",
        details={"available_models": ["product"]},
    )
    assert err.kind == "MetricNotFound"
    assert "sales.revenue" in err.message
    assert err.hint and "loaded model" in err.hint
    assert err.details == {"available_models": ["product"]}


def test_str_includes_kind_and_message():
    err = SliceInvalidError(message="field 'foo' not found on dataset 'orders'")
    s = str(err)
    assert "SliceInvalid" in s
    assert "orders" in s


def test_optional_hint_and_details():
    err = FrameMutationError(message="frame is immutable")
    assert err.hint is None
    assert err.details == {}
