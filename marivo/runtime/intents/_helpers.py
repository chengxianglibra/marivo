from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
