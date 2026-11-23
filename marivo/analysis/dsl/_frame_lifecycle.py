"""Build captured Frame lineage and complete explicit pending inputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from json import dumps

from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    FrameBacking,
    FrameDerivation,
    FrameNode,
    OperationFrameBinding,
    StoredFrameBacking,
)
from marivo.analysis.core.frame_lineage import derive
from marivo.analysis.core.source_lineage import source_lineage
from marivo.analysis.dsl import frame_derivation
from marivo.analysis.materialization import frame_evaluation
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.model.artifact_data import ArtifactData, SavedFrameMethodFacts
from marivo.analysis.model.data_schema import DataSchema
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_metadata import StoredFrameMetadata
from marivo.analysis.model.frame_structure import PendingDataSchema
from marivo.analysis.model.lineage import FrameLineage
from marivo.analysis.model.lineage import validate as validate_lineage


def capture_lineage(
    schema: DataSchema | PendingDataSchema, backing: FrameBacking, input_id: str
) -> FrameLineage:
    if isinstance(backing, ExpressionFrameBacking):
        if isinstance(backing.root, OperationFrameBinding):
            lineage = derive(schema, backing.root.operation, backing.root.inputs)
        else:
            lineage = source_lineage(schema, backing.root)
    else:
        assert backing.metadata.lineage is not None
        lineage = backing.metadata.lineage
    lineage = replace(lineage, input_id=input_id)
    validate_lineage(lineage, schema)
    return lineage


def complete_frame(
    frame: FrameNode,
    backing: ExpressionFrameBacking | StoredFrameBacking,
    bind_key: Callable[[LocalFrameServices, CompletedFrameResult, str], FrameNode],
) -> tuple[CompletedFrameResult, FrameDerivation | None]:
    """Return a completed result and its detached key declaration."""
    if isinstance(backing, StoredFrameBacking):
        return frame._local.read_complete(backing), frame._derivation
    keys: dict[str, CompletedFrameData] = {}
    needs_keys = frame_derivation.uses_frame_keys(frame._derivation)
    result = frame_evaluation.evaluate_frame(frame, declaration_keys=keys if needs_keys else None)
    declaration = frame_derivation.complete_declaration(
        frame._derivation, keys, result.metadata, bind_key
    )
    return result, declaration


def require_stored(backing: FrameBacking, member: str) -> StoredFrameBacking:
    if not isinstance(backing, StoredFrameBacking):
        reject(
            "an exact stored Frame root",
            type(backing).__name__,
            "Call persist() and use the returned Frame.",
            f"frame.{member}",
            canonical_id=f"analysis_frame.{member}",
        )
    return backing


def stored_backing(
    local: LocalFrameServices,
    data: ArtifactData,
    artifact_id: str,
    metadata: StoredFrameMetadata,
    method_facts: tuple[SavedFrameMethodFacts, ...],
    description: str | None,
) -> tuple[StoredFrameBacking, str]:
    backing = StoredFrameBacking(artifact_id, data, metadata, method_facts, description)
    identity = dumps((local.store.store_id, local.session_ref, artifact_id), separators=(",", ":"))
    input_id = "stored_" + sha256(identity.encode()).hexdigest()
    return backing, input_id
