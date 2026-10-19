"""Canonical inert output records shared by completed audit and saved data."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.core.model import reject
from marivo.analysis.evidence._dataset_types import (
    AssociationFindingValueV1,
    FindingValueV1,
    ForecastPointFindingValueV1,
    _coordinates,
)
from marivo.analysis.evidence._dataset_types import (
    OutputCoordinate as OutputCoordinate,
)
from marivo.analysis.evidence._dataset_types import (
    OutputEvidence as OutputEvidence,
)


@dataclass(frozen=True, slots=True)
class OutputFinding:
    """An explicit method output, without a graph derivation or semantic subject."""

    finding_id: str
    value: FindingValueV1
    coordinates: tuple[OutputCoordinate, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.finding_id) is not str
            or not self.finding_id
            or any(c in self.finding_id for c in "\n\r\t")
        ):
            reject(
                "nonempty single-line data text",
                repr(self.finding_id),
                "Use exact output metadata.",
                "output.finding_id",
            )
        if type(self.value) not in (AssociationFindingValueV1, ForecastPointFindingValueV1):
            reject(
                "a self-contained Finding value",
                type(self.value).__name__,
                "Publish the method's output value without a graph derivation.",
                "output.finding",
            )
        _coordinates(self.coordinates)
