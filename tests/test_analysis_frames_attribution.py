"""AttributionFrame metadata, immutability, persistence, and load dispatch."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import FrameMutationError, SemanticKindMismatchError
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session.persistence import write_frame_to_disk


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _meta(session_id="sess_x", project_root="/p"):
    return AttributionFrameMeta(
        kind="attribution_frame",
        ref="frame_attr_001",
        session_id=session_id,
        project_root=project_root,
        produced_by_job="job_attr",
        created_at=_now(),
        row_count=2,
        byte_size=128,
        lineage=Lineage(
            steps=[
                LineageStep(
                    intent="decompose",
                    job_ref="job_attr",
                    inputs=["frame_delta"],
                    params_digest="sha256:test",
                )
            ]
        ),
        metric_ids=["sales.revenue"],
        source_refs=["frame_delta"],
        attribution_kind="decomposition",
        driver_field="region",
        value_column="delta",
        contribution_column="contribution",
        method="sum",
        params={"by": "region", "value": "delta"},
        semantic_kind="segmented",
        semantic_model="sales",
    )


def test_attribution_frame_meta_kind_literal():
    meta = _meta()
    assert meta.kind == "attribution_frame"
    assert meta.metric_ids == ["sales.revenue"]
    assert meta.source_refs == ["frame_delta"]
    assert meta.attribution_kind == "decomposition"


def test_attribution_frame_wraps_df_and_blocks_mutation():
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["north"], "contribution": [10.0]}),
        meta=_meta(),
    )
    assert frame.columns == ["region", "contribution"]
    assert frame.to_pandas().iloc[0]["contribution"] == 10.0
    with pytest.raises(FrameMutationError):
        frame["contribution"] = [0.0]


def test_to_pandas_returns_copy():
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["north"], "contribution": [10.0]}),
        meta=_meta(),
    )
    df = frame.to_pandas()
    df.loc[0, "contribution"] = 999.0
    assert frame.to_pandas().iloc[0]["contribution"] == 10.0


def test_load_frame_round_trips_attribution_frame(tmp_path):
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame({"region": ["north", "south"], "contribution": [10.0, -2.0]})
    meta = _meta(session_id=session.id, project_root=str(session.project_root))
    written = write_frame_to_disk(session.layout, AttributionFrame(_df=df, meta=meta))

    loaded = mv.load_frame(written.ref, session=session)

    assert isinstance(loaded, AttributionFrame)
    assert loaded.meta.kind == "attribution_frame"
    assert loaded.meta.byte_size > 0
    assert list(loaded.to_pandas()["region"]) == ["north", "south"]


def test_attribution_shape_reads_method():
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=_meta()
    )
    assert frame.attribution_shape == "sum"


def test_attribution_frame_as_sum_returns_self():
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=_meta()
    )
    assert frame.as_sum() is frame


def test_attribution_frame_as_ratio_mix_narrows_and_rejects():
    meta = _meta().model_copy(update={"method": "ratio_mix"})
    frame = AttributionFrame(_df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=meta)
    assert frame.attribution_shape == "ratio_mix"
    assert frame.as_ratio_mix() is frame
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        frame.as_sum()
    rendered = str(excinfo.value)
    assert "attribution_shape" in rendered
    assert "ratio_mix" in rendered
    assert "sum" in rendered


def test_attribution_frame_as_weighted_mix():
    meta = _meta().model_copy(update={"method": "weighted_mix"})
    frame = AttributionFrame(_df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=meta)
    assert frame.as_weighted_mix() is frame
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        frame.as_ratio_mix()
    rendered = str(excinfo.value)
    assert "weighted_mix" in rendered
    assert "ratio_mix" in rendered


def test_attribution_frame_as_sum_rejects_mismatch():
    meta = _meta().model_copy(update={"method": "weighted_mix"})
    frame = AttributionFrame(_df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=meta)
    with pytest.raises(SemanticKindMismatchError):
        frame.as_sum()
