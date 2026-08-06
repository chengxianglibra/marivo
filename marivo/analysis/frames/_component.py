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
    composition: dict[str, Any] | None,
    advice: str,
) -> ComponentFrame:
    """Load the persisted ComponentFrame for a MetricFrame or DeltaFrame.

    Single-reference, fail-closed lookup (issue #57): the persisted
    ``component_ref`` is the only authority for the sidecar location. A stale
    ref, a wrong-kind target, or a corrupted sidecar surfaces as a typed error
    rather than being silently recovered by re-deriving a deterministic ref
    from the parent artifact id.
    """
    from marivo.analysis.errors import (
        ComponentFrameUnavailableError,
        FrameRefNotFound,
    )
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.session._load import load_frame
    from marivo.analysis.session._resolve import resolve_frame_session

    if component_ref is None:
        raise ComponentFrameUnavailableError(
            message=(
                "components are unavailable because no component sidecar was "
                "persisted for this frame"
            ),
            context={"parent_ref": parent_ref, "parent_kind": parent_kind},
        )

    session = resolve_frame_session(session_id, project_root)
    try:
        loaded = load_frame(component_ref, session=session)
    except FrameRefNotFound as exc:
        raise ComponentFrameUnavailableError(
            message=(
                f"component frame referenced by this {parent_kind} is no longer "
                f"available on disk; {advice}"
            ),
            context={
                "parent_ref": parent_ref,
                "parent_kind": parent_kind,
                "component_ref": component_ref,
                "session_id": session_id,
            },
        ) from exc
    if not isinstance(loaded, ComponentFrame):
        raise ComponentFrameUnavailableError(
            message=(f"component_ref resolved to {loaded.meta.kind!r}, expected component_frame"),
            context={
                "parent_ref": parent_ref,
                "parent_kind": parent_kind,
                "component_ref": component_ref,
                "loaded_kind": loaded.meta.kind,
                "session_id": session_id,
            },
        )
    return loaded
