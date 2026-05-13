"""Marivo execution envelope — wraps AOI artifacts with platform metadata.

The envelope is the runtime's return type for all intent executions.
AOI artifact data lives in `result`; Marivo platform metadata
(lineage, provenance, product-level semantics) lives alongside it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StepRef(BaseModel):
    """Reference to a step within a session."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    step_id: str
    step_type: str


class ExecutionEnvelope(BaseModel):
    """Marivo execution envelope.

    Wraps an AOI artifact result with platform metadata needed for
    lineage, composition, and product-level semantics.

    - ``result``: AOI artifact payload (the analysis output)
    - ``provenance``: execution trace metadata (query hash, timing, etc.)
    - ``product_metadata``: derived-intent product semantics
      (e.g. validation.status, issues) — lives here, not in AOI result
    """

    model_config = ConfigDict(extra="forbid")

    intent_type: str
    step_type: str
    step_ref: StepRef
    artifact_id: str
    result: dict[str, Any]
    provenance: dict[str, Any] | None = None
    product_metadata: dict[str, Any] | None = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Produce the flat dict shape for explicit migration callers only.

        Merges ``result`` keys at top level alongside step_ref and artifact_id.
        Target HTTP/MCP/runtime paths must return ``ExecutionEnvelope`` directly
        and must not call this method.

        Note: ``result`` and ``product_metadata`` keys that collide with envelope
        fields (intent_type, step_type, step_ref, artifact_id) will silently
        overwrite the envelope values. AOI artifacts don't contain these keys,
        so this is safe in practice.
        """
        out: dict[str, Any] = {
            "intent_type": self.intent_type,
            "step_type": self.step_type,
            "step_ref": self.step_ref.model_dump(),
            "artifact_id": self.artifact_id,
        }
        out.update(self.result)
        if self.provenance is not None:
            out["provenance"] = self.provenance
        if self.product_metadata is not None:
            out.update(self.product_metadata)
        return out
