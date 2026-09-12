"""The Dataset error family keeps exact typed, bounded actionable failures."""

import pytest

from marivo.analysis.datasets.errors import (
    DatasetConstructionError,
    DatasetDefinitionError,
    DatasetFieldSelectionError,
    DatasetOwnershipError,
    DatasetRegistrationError,
)
from marivo.analysis.errors import AnalysisError, AnalysisRepair
from marivo.analysis.observation.errors import (
    ObservationBindingError,
    ObservationConstructionError,
    ObservationPredicateError,
)


@pytest.mark.parametrize(
    "cls",
    [
        DatasetConstructionError,
        DatasetRegistrationError,
        DatasetFieldSelectionError,
        DatasetOwnershipError,
        DatasetDefinitionError,
        ObservationConstructionError,
        ObservationBindingError,
        ObservationPredicateError,
    ],
)
def test_dataset_error_hierarchy_and_bounded_repair(cls: type[DatasetConstructionError]) -> None:
    error = cls(
        expected="e" * 400,
        received="r" * 400,
        repair="Construct one same-Session Dataset.",
        location="dataset.compare",
    )
    assert isinstance(error, AnalysisError)
    assert isinstance(error.repair, AnalysisRepair)
    assert error.expected == "e" * 320
    assert error.received == "r" * 320
    assert error.location == "dataset.compare"
    assert error.repair.action == "Construct one same-Session Dataset."
    assert "Help: marivo.help('analysis')" in str(error)
    assert type(error).__name__ in str(error)
