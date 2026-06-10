"""Load persisted analysis frames."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from marivo.analysis.errors import (
    CrossSessionFrameError,
    FrameCacheCorruptedError,
    FrameMetaInvalidError,
    FrameRefNotFound,
)
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.refs import ArtifactRef
from marivo.analysis.session.persistence import read_frame_from_disk
from marivo.analysis.windows import AbsoluteWindow

if TYPE_CHECKING:
    from marivo.analysis.session.core import Session

_FRAME_CLASSES = {
    "metric_frame": (MetricFrame, MetricFrameMeta),
    "delta_frame": (DeltaFrame, DeltaFrameMeta),
    "attribution_frame": (AttributionFrame, AttributionFrameMeta),
    "candidate_set": (CandidateSet, CandidateSetMeta),
    "association_result": (AssociationResult, AssociationResultMeta),
    "hypothesis_test_result": (HypothesisTestResult, HypothesisTestResultMeta),
    "forecast_frame": (ForecastFrame, ForecastFrameMeta),
    "quality_report": (QualityReport, QualityReportMeta),
    "exploration_result": (ExplorationResult, ExplorationResultMeta),
    "component_frame": (ComponentFrame, ComponentFrameMeta),
}


def load_frame(ref: str | ArtifactRef, *, session: Session) -> BaseFrame:
    """Load a persisted analysis frame by ref from the given or active session."""
    if isinstance(ref, ArtifactRef):
        ref = ref.id

    frame_dir = session.layout.frames_dir / ref
    if not (frame_dir / "meta.json").is_file():
        owner = _find_frame_owner(ref, session=session)
        if owner is not None and owner != session.id:
            raise CrossSessionFrameError(
                message=(
                    f"frame '{ref}' belongs to session {owner!r} "
                    f"but was loaded through session {session.id!r}"
                ),
            )
        raise FrameRefNotFound(
            message=f"no frame '{ref}' under session {session.id!r}",
            details={"session_id": session.id, "ref": ref},
        )
    try:
        df, meta = read_frame_from_disk(session.layout, ref)
    except Exception as exc:
        raise FrameCacheCorruptedError(
            message=f"frame '{ref}' exists on disk but cannot be loaded",
            details={"ref": ref, "cause": str(exc)},
        ) from exc
    if meta.get("session_id") != session.id:
        raise CrossSessionFrameError(
            message=(
                f"frame '{ref}' belongs to session {meta.get('session_id')!r} "
                f"but was loaded through session {session.id!r}"
            ),
        )
    kind = meta["kind"]
    if kind not in _FRAME_CLASSES:
        raise FrameRefNotFound(message=f"unknown frame kind '{kind}' for ref '{ref}'")
    _coerce_metric_window_meta(meta, frame_ref=ref)
    frame_cls, meta_cls = _FRAME_CLASSES[kind]
    return cast("BaseFrame", frame_cls(_df=df, meta=meta_cls(**meta)))


def _find_frame_owner(ref: str, *, session: Session) -> str | None:
    sessions_dir = session.layout.sessions_dir
    if not sessions_dir.is_dir():
        return None
    for candidate in sessions_dir.iterdir():
        if (candidate / "frames" / ref / "meta.json").is_file():
            return candidate.name
    return None


def _coerce_metric_window_meta(meta: dict[str, object], *, frame_ref: str) -> None:
    if meta.get("kind") != "metric_frame":
        return
    window = meta.get("window")
    if window is None or not isinstance(window, dict):
        return
    if "kind" in window:
        return

    if "start" in window and "end" in window:
        allowed_keys = {"start", "end", "grain", "tz", "time_dimension"}
        normalized = {key: window[key] for key in allowed_keys if key in window}
        try:
            absolute = AbsoluteWindow.model_validate(normalized)
        except ValidationError as exc:
            raise FrameMetaInvalidError(
                message=f"frame '{frame_ref}' has invalid legacy metric window metadata",
                details={
                    "kind": "LegacyWindowShapeInvalid",
                    "ref": frame_ref,
                    "window": window,
                    "validation_errors": exc.errors(),
                },
            ) from exc
        meta["window"] = absolute.model_dump(mode="json")
        return

    raise FrameMetaInvalidError(
        message=f"frame '{frame_ref}' has unparseable legacy metric window metadata",
        details={
            "kind": "LegacyWindowShapeInvalid",
            "ref": frame_ref,
            "window": window,
        },
    )
