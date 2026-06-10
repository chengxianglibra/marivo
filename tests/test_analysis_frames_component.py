"""ComponentFrame contract and load behavior."""

from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session.attach as session_attach
from marivo.analysis.errors import ComponentFrameUnavailableError
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from marivo.analysis.session.persistence import write_frame_to_disk


@pytest.fixture(autouse=True)
def _chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    yield


def _now():
    return datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)


def test_component_frame_meta_kind_and_next_intents():
    meta = ComponentFrameMeta(
        ref="frame_component",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        decomposition_kind="ratio",
        components={
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
        axes={"region": {"role": "dimension", "column": "region"}},
        semantic_kind="segmented",
        semantic_model="sales",
    )
    frame = ComponentFrame(
        _df=pd.DataFrame(
            {
                "region": ["NORTH"],
                "numerator": [1.0],
                "denominator": [3.0],
                "failure_rate": [1.0 / 3.0],
            }
        ),
        meta=meta,
    )

    assert meta.kind == "component_frame"
    assert frame.next_intents() == ()
    assert frame.to_pandas().iloc[0]["failure_rate"] == pytest.approx(1.0 / 3.0)


def test_load_frame_round_trips_component_frame():
    session = session_attach.get_or_create(name="demo")
    component = ComponentFrame(
        _df=pd.DataFrame(
            {
                "region": ["NORTH"],
                "numerator": [1.0],
                "denominator": [3.0],
                "failure_rate": [1.0 / 3.0],
            }
        ),
        meta=ComponentFrameMeta(
            ref="frame_component",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref="frame_parent",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            decomposition_kind="ratio",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes={"region": {"role": "dimension", "column": "region"}},
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )
    component.meta = write_frame_to_disk(session.layout, component)

    loaded = mv.load_frame(component.ref, session=session)

    assert isinstance(loaded, ComponentFrame)
    assert loaded.meta.parent_kind == "metric_frame"
    assert loaded.to_pandas().iloc[0]["denominator"] == pytest.approx(3.0)


def test_metric_frame_components_loads_linked_component_frame():
    session = session_attach.get_or_create(name="demo")
    component = ComponentFrame(
        _df=pd.DataFrame({"numerator": [1.0], "denominator": [2.0], "failure_rate": [0.5]}),
        meta=ComponentFrameMeta(
            ref="frame_component",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref="frame_metric",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            decomposition_kind="ratio",
            components={
                "numerator": "sales.failed_count",
                "denominator": "sales.total_count",
            },
            axes={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    component.meta = write_frame_to_disk(session.layout, component)
    parent = MetricFrame(
        _df=pd.DataFrame({"failure_rate": [0.5]}),
        meta=MetricFrameMeta(
            ref="frame_metric",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes={},
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
            component_ref=component.ref,
            decomposition={
                "kind": "ratio",
                "components": {
                    "numerator": "sales.failed_count",
                    "denominator": "sales.total_count",
                },
            },
        ),
    )

    loaded = parent.components()

    assert isinstance(loaded, ComponentFrame)
    assert loaded.ref == component.ref


def test_ordinary_metric_frame_components_raise_structured_unavailable_error():
    session = session_attach.get_or_create(name="demo")
    parent = MetricFrame(
        _df=pd.DataFrame({"revenue": [100.0]}),
        meta=MetricFrameMeta(
            ref="frame_metric",
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.revenue",
            axes={},
            measure={"name": "revenue"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )

    with pytest.raises(ComponentFrameUnavailableError) as exc_info:
        parent.components()

    assert exc_info.value.details["parent_ref"] == "frame_metric"
    assert exc_info.value.details["parent_kind"] == "metric_frame"


def test_component_frame_meta_accepts_time_series_semantic_kind():
    meta = ComponentFrameMeta(
        ref="frame_component_ts",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        decomposition_kind="ratio",
        components={
            "numerator": "sales.failed_count",
            "denominator": "sales.total_count",
        },
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            }
        },
        semantic_kind="time_series",
        semantic_model="sales",
    )

    assert meta.semantic_kind == "time_series"


def test_component_frame_meta_accepts_panel_semantic_kind():
    meta = ComponentFrameMeta(
        ref="frame_component_panel",
        session_id="sess_x",
        project_root="/p",
        produced_by_job="job_observe",
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        parent_ref="frame_parent",
        parent_kind="metric_frame",
        metric_id="sales.failure_rate",
        decomposition_kind="weighted_average",
        components={
            "numerator": "sales.weighted_score",
            "weight": "sales.weight",
        },
        axes={
            "time": {
                "role": "time",
                "column": "bucket_start",
                "grain": "day",
                "time_dimension": "order_date",
            },
            "region": {"role": "dimension", "column": "region"},
        },
        semantic_kind="panel",
        semantic_model="sales",
    )

    assert meta.semantic_kind == "panel"


def test_metric_frame_components_fallback_to_deterministic_ref():
    """When component_ref points to a missing frame, fallback to deterministic ref."""
    from marivo.analysis.evidence.identity import make_component_artifact_id

    session = session_attach.get_or_create(name="demo")

    # Create a parent MetricFrame with an artifact_id
    parent_artifact_id = "art_abcd1234efgh"
    parent = MetricFrame(
        _df=pd.DataFrame({"revenue": [100.0]}),
        meta=MetricFrameMeta(
            ref=parent_artifact_id,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            metric_id="sales.failure_rate",
            axes={},
            measure={"name": "failure_rate"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
            artifact_id=parent_artifact_id,
            component_ref="frame_deadbeef",  # stale ref pointing to nothing
            decomposition={"kind": "ratio", "components": {"numerator": "a", "denominator": "b"}},
        ),
    )
    parent.meta = write_frame_to_disk(session.layout, parent)

    # Create the ComponentFrame at the deterministic ref
    det_ref = make_component_artifact_id(parent_artifact_id)
    component = ComponentFrame(
        _df=pd.DataFrame({"numerator": [1.0], "denominator": [2.0], "failure_rate": [0.5]}),
        meta=ComponentFrameMeta(
            ref=det_ref,
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job="job_observe",
            created_at=_now(),
            row_count=1,
            byte_size=0,
            lineage=Lineage(),
            parent_ref=parent_artifact_id,
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            decomposition_kind="ratio",
            components={"numerator": "a", "denominator": "b"},
            axes={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )
    component.meta = write_frame_to_disk(session.layout, component)

    # components() should fall back to the deterministic ref
    loaded = parent.components()
    assert isinstance(loaded, ComponentFrame)
    assert loaded.ref == det_ref
