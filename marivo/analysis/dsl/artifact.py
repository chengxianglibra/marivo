"""Terminal Artifact: committed identity, submission metadata and audit reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from marivo.analysis.core.artifact_data import ArtifactManifest
from marivo.analysis.core.model import reject
from marivo.analysis.evidence._dataset_types import ArtifactDigest, FindingPage
from marivo.render import result_repr


class ArtifactReader(Protocol):
    """Read committed audit facts without result rows or execution state."""

    def show(self, manifest: ArtifactManifest) -> None: ...

    def findings(
        self, manifest: ArtifactManifest, *, limit: int, cursor: str | None
    ) -> FindingPage: ...

    def evidence_digest(self, manifest: ArtifactManifest) -> ArtifactDigest: ...


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Artifact:
    """Read-only audit view acquired through a persisted AnalysisFrame's artifact.

    This view retains the exact committed identity and audit reader. Read data
    through the persisted Frame; audit reads never load result rows or sources.
    """

    _manifest: ArtifactManifest
    _reader: ArtifactReader

    def __init__(self) -> None:
        raise TypeError(
            "Use frame.persist().artifact or session.persisted_frame(artifact_id).artifact."
        )

    @classmethod
    def _from_manifest(cls, manifest: ArtifactManifest, reader: ArtifactReader) -> Artifact:
        if type(manifest) is not ArtifactManifest:
            reject(
                "a committed output manifest",
                type(manifest).__name__,
                "Use session.persisted_frame(artifact_id).artifact.",
                "artifact.open",
            )
        result = object.__new__(cls)
        object.__setattr__(result, "_manifest", manifest)
        object.__setattr__(result, "_reader", reader)
        return result

    @property
    def id(self) -> str:
        """Return the stable identity of this committed Artifact.

        Args: None.
        Returns: The captured Artifact ID used by session.persisted_frame(id).
        Example: ``reopened = session.persisted_frame(artifact.id)``.
        Constraints: Read-only metadata; reading does not open Store data, load semantic definitions or access a source.
        """
        return self._manifest.artifact_id

    @property
    def session_id(self) -> str:
        """Return the owning Session ID.

        Args: None.
        Returns: The exact Session identity captured by this committed output.
        Example: ``session = mv.session.resume(artifact.session_id)``.
        Constraints: Reads metadata without loading result rows or semantic definitions.
        """
        return self._manifest.session_id

    def __repr__(self) -> str:
        return result_repr(f"Artifact id={self.id[:80]} session={self.session_id[:80]}")

    def show(self) -> None:
        """Print bounded submission metadata and audit summaries.

        Args: None.

        Returns:
            None; prints the audit card and exact Frame recovery guidance.

        Example:
            ``saved.artifact.show()``.

        Constraints: Does not read result rows or coordinate attachments. For a data preview,
        use session.persisted_frame(artifact.id).show(n=20).
        """
        self._reader.show(self._manifest)

    def findings(self, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read a bounded page of committed method Findings.

        Args: limit: Page size from 1 through 100. cursor: Previous page cursor or None.
        Returns: The Artifact-scoped FindingPage.
        Example: ``page = artifact.findings(limit=20)``.
        Constraints: Reads exact output records without loading result tables or a definition graph.
        """
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (cursor is not None and type(cursor) is not str)
        ):
            reject(
                "limit 1..100 and a cursor string or None",
                repr((limit, cursor)),
                "Use the preceding page cursor with the same limit.",
                "Artifact.findings",
            )
        return self._reader.findings(self._manifest, limit=limit, cursor=cursor)

    def evidence_digest(self) -> ArtifactDigest:
        """Read all committed Evidence facts and their collection digests.

        Args: None.
        Returns: ArtifactDigest containing every saved Evidence fact in output order.
        Example: ``digest = artifact.evidence_digest()``.
        Constraints: No result-table reads, current semantic checks or source-freshness claims.
        """
        return self._reader.evidence_digest(self._manifest)
