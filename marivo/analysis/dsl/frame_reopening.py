"""Assemble the DSL handle for one exact committed result."""

from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.frame_services import LocalFrameServices


def reopen_frame(local: LocalFrameServices, artifact_id: str) -> AnalysisFrame:
    """Bind a committed root without activating its producing Session."""
    backing = local.open_frame(artifact_id)
    return AnalysisFrame._from_stored(
        local,
        backing.data,
        artifact_id=backing.artifact_id,
        metadata=backing.metadata,
        method_facts=backing.method_facts,
        description=backing.description,
    )
