"""Dynamic grain coordinates and admission through the actual lazy compiler."""

from pathlib import Path

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.observation.contracts import EntityReducedMetricSemantics
from marivo.semantic.ir import TimestampParse
from tests.lazy_temporal_fixtures import AXIS, temporal_fixture


def test_observe_five_minute_grain(tmp_path: Path) -> None:
    with temporal_fixture(
        tmp_path,
        parse=TimestampParse(timezone="UTC"),
        report_zone="UTC",
        granularity="minute",
        values=("2026-06-03 00:07:30", "2026-06-03 00:12:00", "2026-06-03 00:18:00"),
    ) as fixture:
        fixture.backend.raw_sql("UPDATE orders SET amount=4 WHERE id=3")
        logical = (
            fixture.sources.observe(
                ms.ref.metric("sales.revenue"),
                time_scope=mv.time_scope(start="2026-06-03T00:00:00", end="2026-06-03T01:00:00"),
            )
            .with_time_axis(ms.ref.time_dimension(AXIS), grain=mv.grain("minute", count=5))
            .aggregate()
        )
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert {str(row["order_time"]): row["revenue"] for row in rows} == {
            "2026-06-03 00:05:00": 1.0,
            "2026-06-03 00:10:00": 2.0,
            "2026-06-03 00:15:00": 4.0,
        }
        assert str(logical.row_contract.shape_id) == "metric/time@v1"
        semantics = logical.row_contract.family_semantics
        assert isinstance(semantics, EntityReducedMetricSemantics)
        assert semantics.fold_time_grain == mv.grain("minute", count=5)


def test_observe_grain_finer_than_base_rejected(tmp_path: Path) -> None:
    with temporal_fixture(tmp_path, granularity="minute") as fixture:
        logical = fixture.sources.observe(ms.ref.metric("sales.revenue"))
        with pytest.raises(DatasetConstructionError):
            logical.with_time_axis(ms.ref.time_dimension(AXIS), grain=mv.grain("second", count=30))
