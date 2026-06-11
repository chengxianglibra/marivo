"""Frame meta unit field and render identity."""

from __future__ import annotations

import pandas as pd

from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta


def test_metric_frame_identity_shows_unit_when_present() -> None:
    meta = MetricFrameMeta.model_construct(
        ref="frame_x",
        metric_id="sales.revenue",
        semantic_kind="scalar",
        row_count=1,
        unit="CNY",
    )
    frame = MetricFrame(_df=pd.DataFrame(), meta=meta)
    identity = frame._repr_identity()
    assert "unit=CNY" in identity


def test_metric_frame_identity_omits_unit_when_absent() -> None:
    meta = MetricFrameMeta.model_construct(
        ref="frame_x",
        metric_id="sales.revenue",
        semantic_kind="scalar",
        row_count=1,
        unit=None,
    )
    frame = MetricFrame(_df=pd.DataFrame(), meta=meta)
    assert "unit=" not in frame._repr_identity()


def test_delta_frame_identity_shows_unit_when_present() -> None:
    meta = DeltaFrameMeta.model_construct(
        ref="frame_d",
        metric_id="sales.revenue",
        row_count=1,
        unit="CNY",
    )
    frame = DeltaFrame(_df=pd.DataFrame(), meta=meta)
    assert "unit=CNY" in frame._repr_identity()
