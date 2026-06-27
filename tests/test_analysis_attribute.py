"""session.attribute public attribution operator."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import SemanticKindMismatchError
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.semantic.catalog import SemanticKind
from marivo.semantic.refs import make_ref


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now() -> datetime:
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _delta(
    session: mv.Session, df: pd.DataFrame, *, semantic_kind: str = "segmented"
) -> DeltaFrame:
    meta = DeltaFrameMeta(
        kind="delta_frame",
        ref="frame_delta",
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job="job_compare",
        created_at=_now(),
        row_count=len(df),
        byte_size=0,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="compare",
                    job_ref="job_compare",
                    inputs=["frame_current", "frame_baseline"],
                    params_digest="sha256:compare",
                )
            ]
        ),
        metric_id="sales.revenue",
        source_current_ref="frame_current",
        source_baseline_ref="frame_baseline",
        alignment={
            "kind": "window_bucket",
            "axes": {
                "region": {
                    "role": "dimension",
                    "column": "region",
                    "ref": "sales.orders.region",
                },
                "platform": {
                    "role": "dimension",
                    "column": "platform",
                    "ref": "sales.orders.platform",
                },
            },
        },
        semantic_kind=semantic_kind,  # type: ignore[arg-type]
        semantic_model="sales",
    )
    return DeltaFrame(_df=df, meta=meta)


def test_attribute_single_axis_returns_attribution_frame_with_public_lineage() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "CN", "US"],
                "delta": [10.0, -2.0, 4.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
    )

    assert isinstance(out, AttributionFrame)
    assert out.meta.kind == "attribution_frame"
    assert out.lineage.steps[-1].intent == "attribute"
    assert out.meta.method == "sum"
    assert out.meta.params["mode"] == "flat"
    assert out.meta.params["axes"] == ["sales.orders.region"]
    assert out.meta.driver_field == "region"
    assert out.to_pandas()[["region", "contribution"]].to_dict("records") == [
        {"region": "US", "contribution": 14.0},
        {"region": "CN", "contribution": -2.0},
    ]


def test_attribute_nested_axes_returns_flattened_hierarchy_rows() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(
        session,
        pd.DataFrame(
            {
                "region": ["US", "US", "CN", "CN"],
                "platform": ["ios", "android", "ios", "android"],
                "delta": [6.0, 4.0, -3.0, 1.0],
            }
        ),
    )

    out = session.attribute(
        frame,
        axes=[
            make_ref("sales.orders.region", SemanticKind.DIMENSION),
            make_ref("sales.orders.platform", SemanticKind.DIMENSION),
        ],
        mode="nested",
    )

    rows = out.to_pandas().to_dict("records")
    assert out.meta.method == "nested_sum"
    assert out.meta.driver_field == "path"
    assert rows == [
        {
            "level": 1,
            "axis": "region",
            "driver": "US",
            "path": "US",
            "contribution": 10.0,
            "pct_contribution": 1.25,
            "rank": 1,
        },
        {
            "level": 1,
            "axis": "region",
            "driver": "CN",
            "path": "CN",
            "contribution": -2.0,
            "pct_contribution": -0.25,
            "rank": 2,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "ios",
            "path": "US > ios",
            "contribution": 6.0,
            "pct_contribution": 0.75,
            "rank": 1,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "android",
            "path": "US > android",
            "contribution": 4.0,
            "pct_contribution": 0.5,
            "rank": 2,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "ios",
            "path": "CN > ios",
            "contribution": -3.0,
            "pct_contribution": -0.375,
            "rank": 3,
        },
        {
            "level": 2,
            "axis": "platform",
            "driver": "android",
            "path": "CN > android",
            "contribution": 1.0,
            "pct_contribution": 0.125,
            "rank": 4,
        },
    ]


def test_attribute_requires_explicit_axes() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError, match="attribute requires at least one axis"):
        session.attribute(frame, axes=[])


def test_attribute_rejects_unknown_mode() -> None:
    session = mv.session.get_or_create(name="demo")
    frame = _delta(session, pd.DataFrame({"region": ["US"], "delta": [10.0]}))

    with pytest.raises(SemanticKindMismatchError, match="unsupported attribute mode"):
        session.attribute(  # type: ignore[arg-type]
            frame,
            axes=[make_ref("sales.orders.region", SemanticKind.DIMENSION)],
            mode="magic",
        )
