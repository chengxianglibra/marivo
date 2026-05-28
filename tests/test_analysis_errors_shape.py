"""Smoke tests for new shape-coverage error classes."""

import pytest

from marivo.analysis.errors import (
    AlignmentFailedError,
    AlignmentPolicyNotApplicableError,
    AmbiguousDimensionError,
    AxisNotInPanelDimensionsError,
    DimensionAcrossDatasetsError,
    DimensionFieldNotFoundError,
    PanelGrainMismatchError,
    SegmentDimensionMismatchError,
    SemanticKindMismatchError,
)


@pytest.mark.parametrize(
    "cls, parent",
    [
        (DimensionFieldNotFoundError, SemanticKindMismatchError),
        (AmbiguousDimensionError, SemanticKindMismatchError),
        (DimensionAcrossDatasetsError, SemanticKindMismatchError),
        (AxisNotInPanelDimensionsError, SemanticKindMismatchError),
        (PanelGrainMismatchError, AlignmentFailedError),
        (SegmentDimensionMismatchError, AlignmentFailedError),
        (AlignmentPolicyNotApplicableError, AlignmentFailedError),
    ],
)
def test_new_error_is_subclass(cls, parent):
    assert issubclass(cls, parent)


def test_dimension_not_found_renders_hint():
    err = DimensionFieldNotFoundError(
        message="dimension 'foo' not found",
        details={"dimension_id": "foo", "searched_datasets": ["orders"]},
    )
    rendered = str(err)
    assert "DimensionFieldNotFoundError" in rendered
    assert "foo" in rendered
