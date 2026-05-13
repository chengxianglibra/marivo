from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from marivo.contracts.ids import ArtifactId
from marivo.core.intent.primitives import new_step_id

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


def aoi_artifact_dump(result: dict[str, Any]) -> dict[str, Any]:
    """Return the AOI artifact-shaped dump from an atomic runtime result."""
    raw = result.get("result")
    if isinstance(raw, dict) and raw.get("artifact_id"):
        return raw
    return {
        "artifact_id": result.get("artifact_id"),
        "result": raw if isinstance(raw, dict) else {},
    }


def build_derived_bundle_envelope(
    *,
    runtime: MarivoRuntime,
    session_id: str,
    step_type: str,
    bundle_type: str,
    artifact_name: str,
    aoi_artifacts: list[dict[str, Any]],
    summary: str,
    product_status: str,
    issues: list[dict[str, Any]],
    legacy_bundle: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist and return a derived non-AOI bundle envelope.

    The envelope keeps the public derived interface while making the bundle
    payload separate from product-level status and issues.
    """
    step_id = new_step_id()
    result = {
        "bundle_type": bundle_type,
        "aoi_artifacts": aoi_artifacts,
    }
    product_metadata = {
        "derived_operation": step_type,
        "status": product_status,
        "issues": issues,
        "aoi_artifacts": aoi_artifacts,
    }
    artifact_id = ArtifactId(f"art_{uuid4().hex[:12]}")
    envelope: dict[str, Any] = {
        "intent_type": step_type,
        "step_type": step_type,
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": step_type,
        },
        "artifact_id": str(artifact_id),
        "result": result,
        "product_metadata": product_metadata,
    }
    if provenance is not None:
        envelope["provenance"] = provenance

    if legacy_bundle is not None:
        legacy_payload = dict(legacy_bundle)
        legacy_payload.pop("intent_type", None)
        legacy_payload.pop("step_type", None)
        legacy_payload.pop("step_ref", None)
        legacy_payload.pop("artifact_id", None)
        legacy_payload.pop("result", None)
        legacy_payload.pop("product_metadata", None)
        envelope.update(legacy_payload)

    committed_artifact_id = runtime.insert_artifact(
        session_id,
        step_id,
        bundle_type,
        artifact_name,
        envelope,
        artifact_id=artifact_id,
    )
    envelope["artifact_id"] = str(committed_artifact_id)
    runtime.insert_step(step_id, session_id, step_type, summary, envelope, provenance=provenance)
    return envelope


def build_failed_derived_bundle_envelope(
    *,
    runtime: MarivoRuntime,
    session_id: str,
    step_type: str,
    bundle_type: str,
    artifact_name: str,
    exc: Exception,
) -> dict[str, Any]:
    issue = {
        "code": "derived_orchestration_failed",
        "message": str(exc),
    }
    return build_derived_bundle_envelope(
        runtime=runtime,
        session_id=session_id,
        step_type=step_type,
        bundle_type=bundle_type,
        artifact_name=artifact_name,
        aoi_artifacts=[],
        summary=f"{step_type}: failed derived orchestration",
        product_status="failed",
        issues=[issue],
        provenance={
            "derived_logic_version": "1.0",
            "failure_code": "derived_orchestration_failed",
        },
    )
