"""Closed semantic-layer persistence records."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.refs import RefPayloadV1, SemanticKind


@dataclass(frozen=True, slots=True)
class EntitySnapshotBindingV1:
    """Bind one evidence snapshot id to its exact semantic entity role."""

    entity_ref: RefPayloadV1
    snapshot_id: str

    def __post_init__(self) -> None:
        if type(self.entity_ref) is not RefPayloadV1:
            raise TypeError("entity snapshot binding ref must be an exact RefPayloadV1")
        if self.entity_ref.kind is not SemanticKind.ENTITY:
            raise ValueError("entity snapshot binding requires an entity ref")
        if type(self.snapshot_id) is not str or not self.snapshot_id:
            raise ValueError("entity snapshot binding snapshot_id must be a non-empty string")


__all__ = ["EntitySnapshotBindingV1"]
