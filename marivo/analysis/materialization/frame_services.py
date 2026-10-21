"""Shared local Store authority without production Session or source bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from marivo._definition.types import Owner
from marivo.analysis.materialization.contracts import SessionRecord, invalid
from marivo.analysis.materialization.store import SessionStore

if TYPE_CHECKING:
    from marivo.analysis.core.frame_data import (
        CompletedFrameData,
        CompletedFrameResult,
        StoredFrameBacking,
    )
    from marivo.analysis.core.frame_method_facts import FrameMethodFacts
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
        return StoredFrameBacking(value.artifact_id, value.data, value.metadata, value.method_facts)

    def read_method_facts(self, backing: StoredFrameBacking) -> tuple[FrameMethodFacts, ...]:
        """Consume checked owned method payloads without reading current rows."""
        from marivo.analysis.materialization.graph_reading import FrameReader

        return FrameReader(self.store, self.session_ref).method_facts(backing)

    def read_data(self, backing: StoredFrameBacking) -> CompletedFrameData:
        """Consume current rows and coordinates without historical method payloads."""
        from marivo.analysis.materialization.graph_reading import FrameReader

        return FrameReader(self.store, self.session_ref).data(backing)

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
