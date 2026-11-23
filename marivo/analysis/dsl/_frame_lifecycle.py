"""Build captured Frame lineage and complete explicit pending inputs."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from json import dumps
from typing import TypeVar
from uuid import uuid4

from marivo.analysis.core.frame_data import (
    CompletedFrameData,
    CompletedFrameResult,
    ExpressionFrameBacking,
    FrameBacking,
    FrameDerivation,
    FrameNode,
    MemoryFrameBacking,
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
from marivo.analysis.model.frame_contracts import frame_derivation_of, frame_local
from marivo.analysis.model.frame_integrity import share_contexts, validate_current_facts
from marivo.analysis.model.frame_metadata import StoredFrameMetadata
from marivo.analysis.model.frame_schema import LogicalSchema
from marivo.analysis.model.frame_structure import PendingDataSchema
from marivo.analysis.model.lineage import FrameLineage
from marivo.analysis.model.lineage import validate as validate_lineage

FrameT = TypeVar("FrameT", bound=FrameNode)


def new_frame_id() -> str:
    return "frame_" + uuid4().hex


def bind_frame_owner(
    cls: type[FrameT],
    local: LocalFrameServices,
    schema: LogicalSchema,
    backing: FrameBacking,
    input_id: str,
    derivation: FrameDerivation | None = None,
) -> FrameT:
    schema = share_contexts(schema)
    validate_current_facts(schema)
    result = object.__new__(cls)
    object.__setattr__(result, "_local", local)
    object.__setattr__(result, "_input_id", input_id)
    object.__setattr__(result, "_schema", schema)
    object.__setattr__(result, "_backing", backing)
    object.__setattr__(result, "_derivation", derivation)
    lineage = capture_lineage(schema, backing, input_id)
    object.__setattr__(result, "_lineage", lineage)
    return result


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
    frame_type: type[FrameNode],
) -> tuple[CompletedFrameResult, FrameDerivation | None]:
    """Return a completed result and its detached key declaration."""
    if isinstance(backing, StoredFrameBacking):
        return frame_local(frame).read_complete(backing), frame_derivation_of(frame)
    keys: dict[str, CompletedFrameData] = {}
    needs_keys = frame_derivation.uses_frame_keys(frame_derivation_of(frame))
    result = frame_evaluation.evaluate_frame(frame, declaration_keys=keys if needs_keys else None)

    def bind_key(
        local: LocalFrameServices, result: CompletedFrameResult, input_id: str
    ) -> FrameNode:
        return bind_frame_owner(
            frame_type, local, result.data.schema, MemoryFrameBacking(result), input_id, None
        )

    declaration = frame_derivation.complete_declaration(
        frame_derivation_of(frame), keys, result.metadata, bind_key
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
