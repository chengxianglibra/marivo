"""Construct exact AnalysisFrame handles from owned inputs."""

from __future__ import annotations

from marivo.analysis.core.frame_data import FrameExpressionRoot, StoredFrameBacking
from marivo.analysis.dsl._frame_lifecycle import require_stored
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.model.artifact_data import ArtifactData, SavedFrameMethodFacts
from marivo.analysis.model.frame_contracts import frame_backing
from marivo.analysis.model.frame_metadata import StoredFrameMetadata
from marivo.analysis.model.frame_schema import LogicalSchema


def frame_from_expression(
    local: LocalFrameServices, schema: LogicalSchema, root: FrameExpressionRoot
) -> AnalysisFrame:
    return AnalysisFrame._from_expression(local, schema, root)  # noqa: SLF001 - Owner validation and fresh input identity.


def frame_from_storage(
    local: LocalFrameServices,
    data: ArtifactData,
    *,
    artifact_id: str,
    metadata: StoredFrameMetadata,
    method_facts: tuple[SavedFrameMethodFacts, ...] = (),
    description: str | None = None,
) -> AnalysisFrame:
    return AnalysisFrame._from_stored(  # noqa: SLF001 - Validate the exact Store and artifact binding.
        local,
        data,
        artifact_id=artifact_id,
        metadata=metadata,
        method_facts=method_facts,
        description=description,
    )


def require_stored_frame(frame: AnalysisFrame, member: str) -> StoredFrameBacking:
    return require_stored(frame_backing(frame), member)
