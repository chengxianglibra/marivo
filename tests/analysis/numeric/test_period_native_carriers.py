"""Minimal native period boundary: integer aligned keys and string original buckets."""

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.numeric.test_period_alignment import single_sql_difference
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend", source_params(("duckdb", "postgres", "mysql", "trino", "clickhouse"))
)
def test_period_alignment_native_carriers(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened VARCHAR(32)",
        "(1,1,'a',2,'2026-08-01 00:00:00'),(2,1,'b',4,'2026-07-01 00:00:00')",
        "id Int64, revision Int64, tenant String, amount Int64, happened String",
        [
            {"id": 1, "revision": 1, "tenant": "a", "amount": 2, "happened": "2026-08-01 00:00:00"},
            {"id": 2, "revision": 1, "tenant": "b", "amount": 4, "happened": "2026-07-01 00:00:00"},
        ],
    )
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        author_source_project(
            backend, case, monkeypatch, semantic_project_factory, time_parse="string"
        )
        session = mv.session.get_or_create("period-native", report_timezone="UTC")
        members = session.members(ms.ref.entity("sales.facts"))

        def observe(month: int, count: int) -> mv.LogicalNumericRelation:
            result = members.observe(
                ms.ref.metric("sales.total"),
                during=mv.time_grid(
                    during=mv.time_scope(
                        start=f"2026-{month:02d}-01", end=f"2026-{month:02d}-{count + 1:02d}"
                    ),
                    grain=mv.grain("day"),
                    timezone="UTC",
                ),
                by=(mv.member(),),
            )
            assert isinstance(result, mv.LogicalNumericRelation)
            return result

        current, baseline = observe(8, 2), observe(7, 3)
        comparison = current.compare(baseline, align=mv.outer(time=mv.ordinal()))
        result = comparison.execute()
        single = single_sql_difference(comparison)
        assert single.to_pandas().equals(result.absolute.to_pandas())
        frame = result.to_pandas()
        assert len(frame) == 6 and set(frame.coord_2) == {1, 2, 3}
        missing = frame[frame.coord_2 == 3]
        for field in ("absolute", "relative"):
            assert set(missing[field + "_cell_reason"]) == {"missing_side"}
        assert result._dataset is not None
        exchange = result._dataset.verified()
        assert exchange.primary.schema.field("key_3").type == pa.int64()
        correspondence = next(p.table for p in exchange.parts if p.role == "correspondence")
        assert correspondence.schema.field("correspondence__baseline_key_3").type == pa.string()
        assert (
            correspondence.filter(pa.compute.equal(correspondence["key_3"], 3))[
                "correspondence__current_key_3"
            ].null_count
            == 2
        )
        source_trace.record(single)
        assert source_trace.native_sql
