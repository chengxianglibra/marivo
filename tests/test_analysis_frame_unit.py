"""Frame meta unit field and render identity."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from marivo._compat import UTC
from marivo._temporal import BuiltinPeriodBindingV1, FrameTemporalContractV1
from marivo.analysis._semantic_persistence import MeasureBindingV1
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import (
    MetricFrame,
    MetricFrameMeta,
    _temporal_authority_line,
)
from marivo.analysis.lineage import Lineage
from tests.shared_fixtures import (
    make_test_metric_meta_contract,
    make_test_multi_metric_contract,
)


def test_metric_frame_identity_shows_unit_when_present() -> None:
    meta = MetricFrameMeta.model_construct(
        ref="frame_x",
        metric_id="sales.revenue",
        semantic_kind="scalar",
        row_count=1,
        unit="CNY",
        measure={"name": "revenue"},
    )
    frame = MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)
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
    frame = MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)
    assert "unit=" not in frame._repr_identity()


def test_delta_frame_identity_shows_unit_when_present() -> None:
    meta = DeltaFrameMeta.model_construct(
        ref="frame_d",
        metric_id="sales.revenue",
        row_count=1,
        unit="CNY",
    )
    frame = DeltaFrame(_df=pd.DataFrame({"delta": [1.0]}), meta=meta)
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
    assert contract.output_columns == tuple(frame.columns)
    assert {column.role for column in contract.artifact_schema.columns}
    assert not hasattr(contract.artifact_schema, "kind")
    assert not hasattr(contract.artifact_schema, "ref")
    assert len(contract.semantic_inputs) == 1
    metric = contract.semantic_inputs[0]
    assert metric.role == "metric"
    assert metric.semantic_path == "sales.revenue"
    assert metric.output_column == "revenue"
    assert metric.acquisition == 'session.catalog.metrics.get("sales.revenue")'
    assert metric.help_target == "analysis.catalog.metrics"
    rendered = frame.render(max_output_bytes=None)
    assert "output_columns:" not in rendered
    assert "semantic inputs:" not in rendered
    assert "acquire=" not in rendered
    contract_rendered = contract.render(max_output_bytes=None)
    assert "output_columns: ['bucket_start', 'revenue']" in contract_rendered
    assert 'acquire=session.catalog.metrics.get("sales.revenue")' in contract_rendered


def test_metric_frame_show_uses_public_measure_column_before_preview() -> None:
    rendered = _metric_frame_with_data().render(max_output_bytes=None)

    assert "sales.revenue column=revenue" in rendered
    assert rendered.index("measures:") < rendered.index("preview:")


def test_temporal_authority_is_a_stable_summary_not_raw_json() -> None:
    contract = FrameTemporalContractV1(
        observation_period=BuiltinPeriodBindingV1(
            level_name="month",
            boundary_timezone="Asia/Shanghai",
        ),
        actual_start=pd.Timestamp("2026-01-01").date(),
        actual_end=pd.Timestamp("2026-04-01").date(),
        data_extent_end=pd.Timestamp("2026-03-31").date(),
        display_timezone="Asia/Shanghai",
    )

    rendered = _temporal_authority_line(contract)

    assert "authority=builtin:gregorian-iso/v1" in rendered
    assert "level=month" in rendered
    assert "boundary_timezone=Asia/Shanghai" in rendered
    assert "display_timezone=Asia/Shanghai" in rendered
    assert "actual=[2026-01-01,2026-04-01)" in rendered
    assert "{" not in rendered


def test_wide_multi_metric_panel_keeps_all_measure_identity_before_preview() -> None:
    metric_ids = tuple(f"sales.metric_{index}" for index in range(8))
    axes = {
        "time": {"role": "time", "column": "bucket_start", "grain": "day"},
        "region": {"role": "dimension", "column": "region"},
    }
    contract = make_test_multi_metric_contract(*metric_ids, axes=axes)
    identities = contract["metric_identities"]
    bindings = tuple(
        MeasureBindingV1(
            identity=identity,
            value_column=metric_id.rsplit(".", 1)[-1],
            display_name=metric_id.rsplit(".", 1)[-1],
            unit="CNY",
        )
        for metric_id, identity in zip(metric_ids, identities, strict=True)
    )
    meta = MetricFrameMeta(
        **contract,
        kind="metric_frame",
        ref="frame_panel_8",
        session_id="sess_s",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime(2026, 6, 28, tzinfo=UTC),
        row_count=40,
        byte_size=0,
        lineage=Lineage(),
        metric_id=None,
        axes={},
        measure={},
        measures=None,
        measure_bindings=bindings,
        window={
            "start": "2026-01-01",
            "end": "2026-02-10",
            "grain": "day",
            "time_dimension": "sales.orders.created_at",
        },
        where={},
        semantic_kind="panel",
        semantic_model="sales",
        temporal_contract=FrameTemporalContractV1(
            observation_period=BuiltinPeriodBindingV1(
                level_name="day",
                boundary_timezone="Asia/Shanghai",
            ),
            actual_start=pd.Timestamp("2026-01-01").date(),
            actual_end=pd.Timestamp("2026-02-10").date(),
            display_timezone="Asia/Shanghai",
        ),
    )
    data: dict[str, list[object]] = {
        "bucket_start": list(pd.date_range("2026-01-01", periods=40, freq="D")),
        "region": ["north", "south"] * 20,
    }
    for index, metric_id in enumerate(metric_ids):
        data[metric_id.rsplit(".", 1)[-1]] = [float(index + row) for row in range(40)]
    frame = MetricFrame(_df=pd.DataFrame(data), meta=meta)

    rendered = frame.render()

    preview_at = rendered.index("preview:")
    assert rendered.index("observation_scope:") < preview_at
    assert rendered.index("temporal_authority:") < preview_at
    assert "{'schema':" not in rendered
    for metric_id in metric_ids:
        column = metric_id.rsplit(".", 1)[-1]
        assert f"{metric_id} column={column} unit=CNY" in rendered[:preview_at]


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
