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
from marivo.analysis.frames.coverage import CoverageFrame, CoverageFrameMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.refs import ArtifactRef
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
    "coverage_frame": (CoverageFrame, CoverageFrameMeta),
}


def load_frame(ref: str | ArtifactRef, *, session: Session) -> BaseFrame:
    """Load a persisted analysis frame by ref from the given or active session."""
    import json

    if isinstance(ref, ArtifactRef):
        ref = ref.id

    # Check the store first — the artifacts table is the source of truth.
    artifact_row = session._store.get_artifact(session.id, ref)
    if artifact_row is not None:
        # Use store-registered paths to locate the on-disk data.
        meta_path = session.project_root / artifact_row["meta_path"]
        if not meta_path.is_file():
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' is registered but meta file is missing",
                details={"ref": ref, "meta_path": str(meta_path)},
            )
        data_path = session.project_root / artifact_row["path"]
        if not data_path.is_file():
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' is registered but data file is missing",
                details={"ref": ref, "data_path": str(data_path)},
            )
        try:
            import pandas as pd

            df = pd.read_parquet(data_path, engine="pyarrow", to_pandas_kwargs={})
            meta = json.loads(meta_path.read_text())
        except Exception as exc:
            raise FrameCacheCorruptedError(
                message=f"frame '{ref}' exists on disk but cannot be loaded",
                details={"ref": ref, "cause": str(exc)},
            ) from exc
    else:
        # No store row — the frame is not registered in the session's artifacts
        # table, so it cannot be loaded through this session.
        raise FrameRefNotFound(
            message=f"no frame '{ref}' under session {session.id!r}",
            details={"session_id": session.id, "ref": ref},
        )

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
    # Backward compat: frames persisted before the value-column rename used the
    # metric name (e.g. "revenue") as the DataFrame value column.  Rename it to
    # the canonical "value" so downstream consumers always see a uniform schema.
    if kind == "metric_frame":
        measure_name = (
            meta.get("measure", {}).get("name") if isinstance(meta.get("measure"), dict) else None
        )
        if measure_name and str(measure_name) in df.columns and "value" not in df.columns:
            df = df.rename(columns={str(measure_name): "value"})
    frame_cls, meta_cls = _FRAME_CLASSES[kind]
    return cast("BaseFrame", frame_cls(_df=df, meta=meta_cls(**meta)))


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
