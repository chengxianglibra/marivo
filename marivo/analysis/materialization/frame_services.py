"""Shared local Store authority without production Session or source bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo._definition.types import Owner
from marivo.analysis.materialization.contracts import SessionRecord, invalid
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session import _lazy_graph, _lazy_runtime_reads
from marivo.analysis.session._lazy_read_model import (
    GraphDirection,
    RunLifecycle,
    RunPage,
    RunRecord,
    SessionGraph,
)

if TYPE_CHECKING:
    import pandas as pd

    from marivo.analysis.core.artifact_data import ArtifactManifest
    from marivo.analysis.dsl.artifact import Artifact
    from marivo.analysis.materialization.graph_reading import ExportRead, PreviewRead


@dataclass(frozen=True, slots=True)
class LocalFrameServices:
    """Exact local ownership shared by a Session and its independent data roots.

    Source configuration and hooks belong to the source-capable runtime. This
    authority retains neither that runtime nor any result it reads or publishes.
    """

    store: SessionStore
    session_ref: str

    def __post_init__(self) -> None:
        self.record()

    @property
    def owner(self) -> Owner:
        return Owner(self.session_ref, self.store.store_id)

    def record(self) -> SessionRecord:
        record = self.store.session(self.session_ref)
        if record is None:
            raise invalid("Session is absent from its owning Store")
        return record

    def artifact(self, artifact_id: str) -> Artifact:
        """Open same-Session committed metadata without reading any payload."""
        from marivo.analysis.materialization.graph_reading import open_artifact

        return open_artifact(self.store, self.session_ref, artifact_id)

    def read(self, manifest: ArtifactManifest, request: PreviewRead | ExportRead) -> pd.DataFrame:
        """Read exact committed data through the existing receipt-validating reader."""
        from marivo.analysis.errors import ArtifactNotFoundError
        from marivo.analysis.materialization.graph_reading import PreviewRead, TerminalReader

        if isinstance(request, PreviewRead) and (type(request.n) is not int or request.n < 0):
            from marivo.analysis.core.model import reject

            reject(
                "a nonnegative preview row count",
                repr(request.n),
                "Use a nonnegative integer for the requested preview row count.",
                "frame.read",
            )
        if manifest.session_id != self.session_ref:
            raise ArtifactNotFoundError.for_ref(manifest.artifact_id)
        return TerminalReader(self.store)._frame(manifest, request)

    def runs(
        self, *, status: RunLifecycle | None = None, limit: int = 20, cursor: str | None = None
    ) -> RunPage:
        return _lazy_runtime_reads.runs(
            self.store, self.session_ref, status=status, limit=limit, cursor=cursor
        )

    def get_run(self, run_id: str) -> RunRecord:
        return _lazy_runtime_reads.get_run(self.store, self.session_ref, run_id)

    def graph(
        self,
        *,
        artifact_ref: str | ArtifactRef | None = None,
        direction: GraphDirection = "ancestors",
        max_nodes: int = 100,
    ) -> SessionGraph:
        ref = ArtifactRef(ref=artifact_ref) if isinstance(artifact_ref, str) else artifact_ref
        return _lazy_graph.graph(
            self.store, self.session_ref, artifact_ref=ref, direction=direction, max_nodes=max_nodes
        )
