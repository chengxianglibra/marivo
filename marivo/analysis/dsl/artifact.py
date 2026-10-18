"""Terminal Artifact: committed output identity, metadata and one read service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from marivo.analysis.core.artifact_data import ArtifactManifest
from marivo.analysis.core.model import reject
from marivo.analysis.evidence._dataset_types import ArtifactDigest, FindingPage
from marivo.render import result_repr


class ArtifactReader(Protocol):
    """Read committed output only, without retaining data or execution state."""

    def show(self, manifest: ArtifactManifest, *, n: int) -> None: ...

    def to_pandas(self, manifest: ArtifactManifest) -> pd.DataFrame: ...

    def findings(
        self, manifest: ArtifactManifest, *, limit: int, cursor: str | None
    ) -> FindingPage: ...

    def evidence_digest(self, manifest: ArtifactManifest) -> ArtifactDigest: ...


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Artifact:
    """Read-only committed output; acquire through Query.execute or Session.artifact.

    Artifact never owns a Query, definition graph, execution context or in-memory
    table. New and reopened handles read the same committed output.
    """

    _manifest: ArtifactManifest
    _reader: ArtifactReader

    def __init__(self) -> None:
        raise TypeError("Use query.execute() or session.artifact(artifact_id).")

    @classmethod
    def _from_manifest(cls, manifest: ArtifactManifest, reader: ArtifactReader) -> Artifact:
        if type(manifest) is not ArtifactManifest:
            reject(
                "a committed output manifest",
                type(manifest).__name__,
                "Open an exact committed Artifact ID.",
                "artifact.open",
            )
        result = object.__new__(cls)
        object.__setattr__(result, "_manifest", manifest)
        object.__setattr__(result, "_reader", reader)
        return result

    @property
    def id(self) -> str:
        """Return the stable identity of this committed Artifact.

        Returns: The captured artifact id used by session.artifact(id).
        Example: ``reopened = session.artifact(artifact.id)``.
        Constraints: Read-only metadata; reading does not open Store data, load semantic definitions or access a source.
        """
        return self._manifest.artifact_id

    @property
    def session_id(self) -> str:
        """Return the owning Session ID."""
        return self._manifest.session_id

    def __repr__(self) -> str:
        return result_repr(f"Artifact id={self.id[:80]} session={self.session_id[:80]}")

    def show(self, *, n: int = 20) -> None:
        """Print a bounded preview of committed output rows.

        Args: n: Maximum rows to decode for the preview.
        Returns: None; prints the output card.
        Example: ``artifact.show(n=20)``.
        Constraints: Bounded decoding; receipt hashing can read a complete consumed part. No source lookup.
        """
        if type(n) is not int or n < 0:
            reject(
                "a nonnegative preview row count",
                repr(n),
                "Use artifact.show(n=20).",
                "Artifact.show",
            )
        self._reader.show(self._manifest, n=n)

    def to_pandas(self) -> pd.DataFrame:
        """Export all committed output rows as an isolated pandas DataFrame.

        Args: None.
        Returns: A complete isolated DataFrame in the saved output layout.
        Example: ``frame = artifact.to_pandas()``.
        Constraints: Never samples rows or recreates a Query; complete export must fit in memory.
        """
        return self._reader.to_pandas(self._manifest)

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
