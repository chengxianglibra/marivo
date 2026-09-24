"""Private paired Dataset types for committed J1 exchange Artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.observation.contracts import owner_of

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


class LogicalJ1Dataset(LogicalDataset, _token=_CORE_TOKEN, family_id="dsl_j1"):
    """Registered internal partner; W4 owns top-level execution admission."""

    __slots__ = ()

    def execute(self) -> MaterializedJ1Dataset:
        raise DatasetConstructionError(
            expected="W4 J1 execution admission",
            received="private W3 logical partner",
            repair="Use the admitted private J1 publication action until W4 is connected.",
            location="dsl.j1.execute",
        )


class MaterializedJ1Dataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="dsl_j1"):
    """Exact internal J1 Artifact with ordinary governed Dataset reads."""

    __slots__ = ()

    def show(self, *, max_output_bytes: int | None = None) -> None:
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        return owner_of(self).action_port.finding(self, finding_id)
