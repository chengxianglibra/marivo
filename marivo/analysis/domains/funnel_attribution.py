"""Private R7 funnel allocation states; no Metric attribution variants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.observation.contracts import (
    owner_of,
)

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage


class LogicalFunnelAttributionDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="attribution"):
    """Complete logical attribution rows; execution belongs to the runtime."""

    __slots__ = ()

    def execute(self) -> MaterializedFunnelAttributionDataset:
        """Commit attribution rows through the runtime; no parameters.

        Returns: Materialized Attribution. Example: ``attribution.execute()``.
        Constraints: Both inputs must pass complete guarded validation.
        """
        return owner_of(self).action_port.execute_attribution(self)


class MaterializedFunnelAttributionDataset(
    MaterializedDataset, _token=_CORE_TOKEN, family_id="attribution"
):
    """Committed immutable attribution rows with original Evidence and Findings."""

    __slots__ = ()

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print a bounded committed preview, optionally limited by max_output_bytes.

        Returns: None. Example: ``attribution.show()``. Constraints: Uses only committed rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete DataFrame; no parameters.

        Example: ``frame = attribution.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest; no new execution occurs."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read committed Findings with limit and opaque cursor; return FindingPage.

        Example: ``attribution.findings(limit=10)``. Constraints: Pages never infer new Findings.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Return the committed Finding named by finding_id.

        Example: ``attribution.finding('finding-id')``. Constraints: Exact retained id required.
        """
        return owner_of(self).action_port.finding(self, finding_id)
