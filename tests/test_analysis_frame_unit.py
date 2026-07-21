"""Frame meta unit field and render identity."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from tests.shared_fixtures import make_test_metric_meta_contract


def test_metric_frame_identity_shows_unit_when_present() -> None:
    meta = MetricFrameMeta.model_construct(
        ref="frame_x",
        metric_id="sales.revenue",
        semantic_kind="scalar",
        row_count=1,
        unit="CNY",
        measure={"name": "revenue"},
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
        measure={"name": "revenue"},
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


def _metric_frame_with_data() -> MetricFrame:
    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        kind="metric_frame",
        ref="frame_schema",
        session_id="sess_s",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime(2026, 6, 28, tzinfo=UTC),
        row_count=2,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    return MetricFrame(
        _df=pd.DataFrame({"bucket_start": ["2026-06-01", "2026-06-02"], "value": [1.0, 2.0]}),
        meta=meta,
    )


def test_frame_contract_embeds_schema() -> None:
    frame = _metric_frame_with_data()
    contract = frame.contract()
    assert contract.kind == frame.kind
    assert contract.ref == frame.ref
    assert contract.artifact_schema.semantic_shape == frame.meta.semantic_kind
    assert [column.name for column in contract.artifact_schema.columns] == list(frame.columns)
    assert {column.role for column in contract.artifact_schema.columns}
    assert not hasattr(contract.artifact_schema, "kind")
    assert not hasattr(contract.artifact_schema, "ref")


@pytest.mark.parametrize(
    ("semantic_kind", "data", "expected_columns"),
    [
        ("scalar", {"value": [1.0]}, ["revenue"]),
        (
            "time_series",
            {"bucket_start": ["2026-06-01"], "value": [1.0]},
            ["bucket_start", "revenue"],
        ),
        ("segmented", {"region": ["NORTH"], "value": [1.0]}, ["region", "revenue"]),
        (
            "panel",
            {"bucket_start": ["2026-06-01"], "region": ["NORTH"], "value": [1.0]},
            ["bucket_start", "region", "revenue"],
        ),
    ],
)
def test_metric_frame_public_reads_share_metric_named_schema(
    semantic_kind: str,
    data: dict[str, list[object]],
    expected_columns: list[str],
) -> None:
    frame = _metric_frame_with_data()
    frame._df = pd.DataFrame(data)
    frame.meta = frame.meta.model_copy(
        update={"semantic_kind": semantic_kind, "row_count": len(frame._df)}
    )

    assert frame.columns == expected_columns
    assert list(frame) == expected_columns
    assert list(frame.to_pandas().columns) == expected_columns
    assert [column.name for column in frame.contract().artifact_schema.columns] == expected_columns
    assert frame.contract().artifact_schema.columns[-1].role == "value"
    assert frame["revenue"].tolist() == [1.0]
    assert "revenue" in frame.render(max_output_bytes=None)
    with pytest.raises(KeyError):
        frame["value"]

    selected = frame["revenue"]
    selected.iloc[0] = 99.0
    assert frame["revenue"].iloc[0] == 1.0
