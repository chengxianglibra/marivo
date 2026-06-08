"""Shared ComponentFrame lookup logic for MetricFrame and DeltaFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from marivo.analysis.frames.component import ComponentFrame


def _load_component_frame(
    *,
    parent_ref: str,
    parent_kind: str,
    session_id: str,
    project_root: str,
    artifact_id: str | None,
    component_ref: str | None,
    decomposition: dict[str, Any] | None,
    advice: str,
) -> ComponentFrame:
    """Two-phase ComponentFrame lookup shared by MetricFrame and DeltaFrame."""
    from marivo.analysis.errors import (
        ComponentFrameUnavailableError,
        FrameCacheCorruptedError,
        FrameRefNotFound,
    )
    from marivo.analysis.evidence.identity import make_component_artifact_id
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.session._load import load_frame
    from marivo.analysis.session._resolve import resolve_frame_session

    if component_ref is None and decomposition is None:
        raise ComponentFrameUnavailableError(
            message=(
                "components are only available for derived ratio or "
                "weighted-average frames produced by component-aware observe"
            ),
            details={"parent_ref": parent_ref, "parent_kind": parent_kind},
        )

    session = resolve_frame_session(session_id, project_root)

    # Phase 1: try stored component_ref (covers legacy random refs)
    if component_ref is not None:
        try:
            loaded = load_frame(component_ref, session=session)
            if isinstance(loaded, ComponentFrame):
                return loaded
        except (FrameRefNotFound, FrameCacheCorruptedError):
            pass

    # Phase 2: derive deterministic ref from parent's artifact_id/ref
    resolved_parent = artifact_id or parent_ref
    deterministic_ref = make_component_artifact_id(resolved_parent)
    try:
        loaded = load_frame(deterministic_ref, session=session)
        if isinstance(loaded, ComponentFrame):
            return loaded
    except (FrameRefNotFound, FrameCacheCorruptedError):
        pass

    raise ComponentFrameUnavailableError(
        message=(
            f"component frame referenced by this {parent_kind} is no longer "
            f"available on disk; {advice}"
        ),
        details={
            "parent_ref": parent_ref,
            "parent_kind": parent_kind,
            "component_ref": component_ref,
            "deterministic_ref": deterministic_ref,
            "session_id": session_id,
        },
    )
