"""In-memory directional protocol between semantic readiness and analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from marivo.introspection.live.model import EnvironmentFingerprint, LiveHelpTarget
from marivo.refs import SemanticRef, SymbolKind


class AnalysisToSemanticHandoff(BaseModel):
    """Typed request from analysis for a genuinely missing semantic object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_kind: SymbolKind | None
    requirement: str
    affected_capability_id: str
    environment_fingerprint: EnvironmentFingerprint
    semantic_context_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    project_fingerprint: str | None = None


class SemanticToAnalysisHandoff(BaseModel):
    """Typed readiness response from semantic to analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    help_target: LiveHelpTarget
    ready_refs: tuple[SemanticRef, ...]
    project_fingerprint: str
    catalog_fingerprint: str
    environment_fingerprint: EnvironmentFingerprint
    readiness_status: Literal["ready", "ready_with_warnings"]
    warning_ids: tuple[str, ...] = ()
    preview_evidence_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


class SemanticHandoffReceipt(BaseModel):
    """In-memory receipt for a validated semantic handoff."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready_refs: tuple[SemanticRef, ...]
    project_fingerprint: str
    catalog_fingerprint: str
    environment_fingerprint: EnvironmentFingerprint
    readiness_status: Literal["ready", "ready_with_warnings"]
    warning_ids: tuple[str, ...] = ()
    preview_evidence_ids: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the ordinary masked display projection of this receipt."""
        from marivo.introspection.live.render import mask_fingerprint

        return {
            "ready_refs": [str(ref) for ref in self.ready_refs],
            "project_fingerprint": self.project_fingerprint,
            "catalog_fingerprint": self.catalog_fingerprint,
            "environment_fingerprint": mask_fingerprint(self.environment_fingerprint),
            "readiness_status": self.readiness_status,
            "warning_ids": list(self.warning_ids),
            "preview_evidence_ids": list(self.preview_evidence_ids),
            "caveats": list(self.caveats),
        }

    def __repr__(self) -> str:
        return (
            f"SemanticHandoffReceipt ready_refs={len(self.ready_refs)} "
            f"status={self.readiness_status}"
        )
