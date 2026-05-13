from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from marivo.contracts.aoi_runtime import artifact_to_envelope_result, validate_aoi_artifact
from marivo.contracts.envelope import ExecutionEnvelope, StepRef
from marivo.contracts.ids import ArtifactId

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


def commit_step_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    artifact_payload: dict[str, Any],
    summary: str,
    provenance: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commit an artifact and insert a step record.

    Replaces the repeated 5-8 line pattern across 10+ intent runners:
      1. runtime.commit_artifact_with_extraction(...) -> artifact_id
      2. Build result dict with step_ref and artifact_id
      3. runtime.insert_step(...)

    Returns the result dict with intent_type, step_type, step_ref, artifact_id,
    and all keys from artifact_payload merged in.
    """
    artifact_id: str = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        artifact_type,
        artifact_name,
        artifact_payload,
        step_type=step_type,
    )

    result: dict[str, Any] = {
        "intent_type": step_type,
        "step_type": step_type,
        "step_ref": {
            "session_id": session_id,
            "step_id": step_id,
            "step_type": step_type,
        },
        "artifact_id": artifact_id,
        **artifact_payload,
    }

    runtime.insert_step(
        step_id,
        session_id,
        step_type,
        summary,
        result,
        provenance=provenance,
        semantic_metadata=semantic_metadata,
    )

    return result


def build_envelope(
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_id: str,
    artifact_payload: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Build an ExecutionEnvelope from intent execution results.

    This is the successor to commit_step_result()'s dict construction.
    Intent handlers should migrate to use this + runtime artifact commit
    separately.
    """
    return ExecutionEnvelope(
        intent_type=step_type,
        step_type=step_type,
        step_ref=StepRef(
            session_id=session_id,
            step_id=step_id,
            step_type=step_type,
        ),
        artifact_id=artifact_id,
        result=artifact_payload,
        provenance=provenance,
        product_metadata=product_metadata,
    )


def commit_aoi_artifact_result(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    step_type: str,
    artifact_type: str,
    artifact_name: str,
    artifact_payload: dict[str, Any],
    summary: str,
    provenance: dict[str, Any] | None = None,
    product_metadata: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
) -> ExecutionEnvelope:
    """Commit a canonical AOI artifact and return an execution envelope."""
    canonical_artifact = artifact_to_envelope_result(validate_aoi_artifact(artifact_payload))
    artifact_body_key = "result" if "result" in canonical_artifact else "failure"
    artifact_body = canonical_artifact[artifact_body_key]
    artifact_id = ArtifactId(f"art_{uuid4().hex[:12]}")
    final_artifact = artifact_to_envelope_result(
        validate_aoi_artifact(
            {
                "artifact_id": artifact_id,
                artifact_body_key: artifact_body,
            }
        )
    )

    committed_artifact_id: str = runtime.commit_artifact_with_extraction(
        session_id,
        step_id,
        artifact_type,
        artifact_name,
        final_artifact,
        step_type=step_type,
        artifact_id=artifact_id,
    )

    envelope = build_envelope(
        session_id=session_id,
        step_id=step_id,
        step_type=step_type,
        artifact_id=committed_artifact_id,
        artifact_payload=final_artifact,
        provenance=provenance,
        product_metadata=product_metadata,
    )

    runtime.insert_step(
        step_id,
        session_id,
        step_type,
        summary,
        envelope.model_dump(),
        provenance=provenance,
        semantic_metadata=semantic_metadata,
    )

    return envelope
