"""AttributionFrame metadata, immutability, persistence, and load dispatch."""

import json
from datetime import datetime

import pandas as pd
import pytest

import marivo.analysis.session as session_attach
from marivo._compat import UTC
from marivo.analysis.attribution_contract import AttributionAxisBindingV1
from marivo.analysis.errors import (
    FrameMetaInvalidError,
    FrameMutationError,
    SemanticKindMismatchError,
)
from marivo.analysis.frames.attribution import (
    AttributionFrame,
    AttributionFrameMeta,
    AttributionReconciliation,
    AttributionTopKSelectionV1,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import persist_frame
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _meta(session_id="sess_x", project_root="/p", row_count=1):
    return AttributionFrameMeta(
        kind="attribution_frame",
        ref="frame_attr_001",
        session_id=session_id,
        project_root=project_root,
        produced_by_job="job_attr",
        created_at=_now(),
        row_count=row_count,
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
        row_contract_version="generic-attribution-rows/v3",
        axis_bindings=(
            AttributionAxisBindingV1(
                ref=RefPayloadV1.from_ref(ref_factory.dimension("sales.orders.region")),
                output_column="region",
            ),
        ),
        reconciliation=AttributionReconciliation(
            partition_count=1,
            total_delta=8.0,
            contribution_sum=8.0,
            residual=0.0,
            max_abs_residual=0.0,
        ),
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
    df = pd.DataFrame(
        {
            "region": ["north", "south"],
            "contribution": [10.0, -2.0],
            "share_of_total_delta": [1.0, 1.0],
            "share_of_positive_pool": [1.0, 0.0],
            "share_of_negative_pool": [None, 1.0],
            "rank": [1, 2],
        }
    )
    meta = _meta(
        session_id=session.id,
        project_root=str(session.project_root),
        row_count=2,
    )
    written = persist_frame(session, AttributionFrame(_df=df, meta=meta))

    loaded = session.get_frame(written.ref)

    assert isinstance(loaded, AttributionFrame)
    assert loaded.meta.kind == "attribution_frame"
    assert loaded.meta.byte_size > 0
    assert list(loaded.to_pandas()["region"]) == ["north", "south"]


def test_load_v3_attribution_rows_rejects_missing_required_column() -> None:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "region": ["north"],
            "contribution": [1.0],
            "share_of_total_delta": [1.0],
            "share_of_positive_pool": [1.0],
            "share_of_negative_pool": [None],
            "rank": [1],
        }
    )
    meta = _meta(
        session_id=session.id,
        project_root=str(session.project_root),
    ).model_copy(
        update={
            "row_contract_version": "generic-attribution-rows/v3",
            "axis_bindings": (
                AttributionAxisBindingV1(
                    ref=RefPayloadV1.from_ref(ref_factory.dimension("sales.orders.region")),
                    output_column="region",
                ),
            ),
            "reconciliation": AttributionReconciliation(
                partition_count=1,
                total_delta=1.0,
                contribution_sum=1.0,
                residual=0.0,
                max_abs_residual=0.0,
            ),
        }
    )
    written = persist_frame(session, AttributionFrame(_df=df, meta=meta))
    data_path = session._layout.frames_dir / written.ref / "data.parquet"
    corrupted = pd.read_parquet(data_path).drop(columns="rank")
    corrupted.to_parquet(data_path, index=False)

    with pytest.raises(FrameMetaInvalidError, match="corrupt generic attribution rows"):
        session.get_frame(written.ref)


def test_load_v3_attribution_rows_rejects_non_null_other_axis_cell() -> None:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "region": ["masked-but-not-null"],
            "attribution_other_mask": [1],
            "contribution": [8.0],
            "share_of_total_delta": [1.0],
            "share_of_positive_pool": [1.0],
            "share_of_negative_pool": [None],
            "rank": [1],
        }
    )
    meta = _meta(session_id=session.id, project_root=str(session.project_root)).model_copy(
        update={
            "top_k_selection": AttributionTopKSelectionV1(
                limit=1,
                score_method="metric_magnitude",
                original_partition_count=2,
                effective_partition_count=2,
            )
        }
    )
    written = persist_frame(session, AttributionFrame(_df=df, meta=meta))

    with pytest.raises(FrameMetaInvalidError, match="corrupt generic attribution rows") as exc_info:
        session.get_frame(written.ref)

    assert exc_info.value._context["reason"] == (
        "attribution Other mask bit requires a null axis cell"
    )


def test_load_v2_attribution_rows_requires_rerunning_hierarchy() -> None:
    session = session_attach.get_or_create(name="demo")
    df = pd.DataFrame(
        {
            "region": ["north"],
            "contribution": [8.0],
            "share_of_total_delta": [1.0],
            "share_of_positive_pool": [1.0],
            "share_of_negative_pool": [None],
            "rank": [1],
        }
    )
    meta = _meta(session_id=session.id, project_root=str(session.project_root))
    written = persist_frame(session, AttributionFrame(_df=df, meta=meta))
    meta_path = session._layout.frames_dir / written.ref / "meta.json"
    payload = json.loads(meta_path.read_text())
    payload["row_contract_version"] = "generic-attribution-rows/v2"
    payload["attribution_mode"] = "multiresolution"
    meta_path.write_text(json.dumps(payload))

    with pytest.raises(FrameMetaInvalidError) as exc_info:
        session.get_frame(written.ref)

    assert exc_info.value.repair is not None
    assert "Re-run session.attribute" in exc_info.value.repair.action
    assert "mode='hierarchy'" in exc_info.value.repair.action


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


@pytest.mark.parametrize("mode", ["joint", "hierarchy"])
def test_attribution_mode_is_distinct_from_weighted_mix_method(mode):
    meta = _meta().model_copy(update={"method": "weighted_mix", "attribution_mode": mode})
    frame = AttributionFrame(_df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=meta)

    assert frame.attribution_mode == mode
    assert frame.attribution_shape == "weighted_mix"
    assert f"method=weighted_mix mode={mode}" in repr(frame)
    assert f"method=weighted_mix mode={mode}" in frame.render()


def test_attribution_mode_is_none_for_legacy_or_single_axis_artifacts():
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=_meta()
    )

    assert frame.attribution_mode is None


def test_attribution_frame_as_sum_rejects_mismatch():
    meta = _meta().model_copy(update={"method": "weighted_mix"})
    frame = AttributionFrame(_df=pd.DataFrame({"region": ["n"], "contribution": [1.0]}), meta=meta)
    with pytest.raises(SemanticKindMismatchError):
        frame.as_sum()
