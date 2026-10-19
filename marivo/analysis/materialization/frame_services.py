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
    from marivo.analysis.core.frame_data import CompletedFrameResult, StoredFrameBacking
    from marivo.analysis.dsl.artifact import Artifact
    from marivo.analysis.materialization.graph_reading import FramePreview


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

    def open_frame(self, artifact_id: str) -> StoredFrameBacking:
        """Validate one same-Session committed descriptor, without payload reads."""
        from marivo.analysis.core.frame_data import StoredFrameBacking
        from marivo.analysis.materialization.graph_reading import open_manifest

        value = open_manifest(self.store, self.session_ref, artifact_id)
        return StoredFrameBacking(value.artifact_id, value.data, value.metadata)

    def read_complete(self, backing: StoredFrameBacking) -> CompletedFrameResult:
        """Load and fully validate independent saved data, preserving result facts."""
        from marivo.analysis.materialization.graph_reading import FrameReader

        return FrameReader(self.store, self.session_ref).complete(backing)

    def preview(self, backing: StoredFrameBacking, n: int) -> FramePreview:
        """Decode an ordered prefix and only its necessary coordinate records."""
        from marivo.analysis.materialization.graph_reading import FrameReader

        return FrameReader(self.store, self.session_ref).preview(backing, n)

    def publish(self, result: CompletedFrameResult) -> StoredFrameBacking:
        """Publish an already completed result through the original local Store."""
        from marivo.analysis.materialization.terminal_publication import publish_completed

        return publish_completed(self, result)

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
