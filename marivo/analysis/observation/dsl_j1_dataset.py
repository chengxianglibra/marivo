"""Private paired Dataset types for committed J1 exchange Artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.observation.contracts import owner_of

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class J1SourcePayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    """Declare the live closure of one private J1 execution binding."""

    semantic_definition: str
    sources: tuple[str, ...]
    method_id: str
    method_version: int

    @property
    def identity_payload(self) -> CanonicalValue:
        return (self.semantic_definition, self.sources, self.method_id, self.method_version)

    @property
    def live_source_dependencies(self) -> tuple[str, ...]:
        return self.sources


class LogicalJ1Dataset(LogicalDataset, _token=_CORE_TOKEN, family_id="dsl_j1"):
    """Registered internal partner for Runtime-owned J1 execution bindings."""

    __slots__ = ()

    def execute(self) -> MaterializedJ1Dataset:
        raise DatasetConstructionError(
            expected="a bound private J1 Runtime invocation",
            received="unbound J1 logical partner",
            repair="Use DatasetRuntime.execute_j1 with a source factory or exact saved predecessor.",
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
