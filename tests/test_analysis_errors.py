"""AnalysisError hierarchy for the analysis runtime."""

import pytest

from marivo.analysis.errors import (
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


def test_transform_op_unsupported_error_is_analysis_error():
    from marivo.analysis.errors import AnalysisError, TransformOpUnsupportedError

    err = TransformOpUnsupportedError(
        message="op 'explode' is not supported",
        hint="use one of: filter, slice, rollup, topk, bottomk, rank, normalize, window",
        details={"op": "explode", "supported_ops": ["filter", "slice"]},
    )
    assert isinstance(err, AnalysisError)
    assert err.kind == "TransformOpUnsupported"
    assert err.hint == "use one of: filter, slice, rollup, topk, bottomk, rank, normalize, window"
    assert err.details["op"] == "explode"
    assert err.details["supported_ops"] == ["filter", "slice"]


def test_transform_shape_unsupported_error_carries_axes():
    from marivo.analysis.errors import TransformShapeUnsupportedError

    err = TransformShapeUnsupportedError(
        message="window requires a time axis",
        details={"axes": {}, "required": "time"},
    )
    assert err.details["axes"] == {}
    assert err.details["required"] == "time"


def test_transform_arg_error_carries_op():
    from marivo.analysis.errors import TransformArgError

    err = TransformArgError(
        message="topk requires a positive 'limit'",
        details={"op": "topk", "limit": 0},
    )
    assert err.details["op"] == "topk"


def test_transform_dimension_not_found_error_lists_axes():
    from marivo.analysis.errors import TransformDimensionNotFoundError

    err = TransformDimensionNotFoundError(
        message="dimension 'platform' not in frame axes",
        details={"dimension": "platform", "axes": ["country", "time"]},
    )
    assert err.details["axes"] == ["country", "time"]


def test_new_operator_errors_are_structured():
    from marivo.analysis.errors import (
        AnalysisError,
        ForecastPolicyError,
        QualityShapeUnsupportedError,
        TestPolicyError,
    )

    for cls in (TestPolicyError, ForecastPolicyError, QualityShapeUnsupportedError):
        err = cls(message="bad policy", details={"operator": cls.__name__})
        assert isinstance(err, AnalysisError)
        assert err._template_fields()["doc"].endswith("references/pitfalls.md")
