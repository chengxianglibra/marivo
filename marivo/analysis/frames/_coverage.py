"""CoverageFrame lookup logic for MetricFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marivo.analysis.frames.coverage import CoverageFrame


def _load_coverage_frame(
    *,
    parent_ref: str,
    session_id: str,
    project_root: str,
    artifact_id: str | None,
    coverage_ref: str | None,
) -> CoverageFrame | None:
    """Two-phase CoverageFrame lookup for sampled metrics.

    Returns ``None`` when the parent frame has no coverage sidecar
    (``coverage_ref`` is ``None``) — this is the ordinary "no coverage"
    state, not an error. A set ``coverage_ref`` whose sidecar is missing or
    corrupt on disk still raises a fail-closed ``FrameReadError``.
    """
    from marivo.analysis.errors import (
        AnalysisRepair,
        FrameCacheCorruptedError,
        FrameReadError,
        FrameRefNotFound,
    )
    from marivo.analysis.evidence.identity import make_coverage_artifact_id
    from marivo.analysis.frames.coverage import CoverageFrame
    from marivo.analysis.session._load import load_frame
    from marivo.analysis.session._resolve import resolve_frame_session
    from marivo.introspection.live.model import LiveHelpTarget

    if coverage_ref is None:
        return None

    session = resolve_frame_session(session_id, project_root)

    # Phase 1: try stored coverage_ref
    try:
        loaded = load_frame(coverage_ref, session=session)
        if isinstance(loaded, CoverageFrame):
            return loaded
    except (FrameRefNotFound, FrameCacheCorruptedError):
        pass

    # Phase 2: derive deterministic ref from parent's artifact_id/ref
    resolved_parent = artifact_id or parent_ref
    deterministic_ref = make_coverage_artifact_id(resolved_parent)
    try:
        loaded = load_frame(deterministic_ref, session=session)
        if isinstance(loaded, CoverageFrame):
            return loaded
    except (FrameRefNotFound, FrameCacheCorruptedError):
        pass

    raise FrameReadError(
        message=(
            "coverage frame referenced by this metric frame is no longer "
            "available on disk; re-run observe() to regenerate it"
        ),
        location="frame.coverage()",
        expected=(f"a loadable coverage sidecar for parent {resolved_parent!r}"),
        received=(
            f"coverage_ref {coverage_ref!r} and deterministic ref "
            f"{deterministic_ref!r} both unresolvable on disk"
        ),
        repair=AnalysisRepair(
            kind="retry",
            action=(
                "The coverage sidecar for this metric frame is missing or "
                "corrupt on disk. Re-run observe() to regenerate the coverage "
                "frame."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        ),
        context={
            "parent_ref": parent_ref,
            "coverage_ref": coverage_ref,
            "deterministic_ref": deterministic_ref,
            "session_id": session_id,
        },
    )
