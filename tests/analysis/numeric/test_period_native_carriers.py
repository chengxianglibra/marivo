"""Qualified source carriers feed paired periods without losing endpoint presence."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.data_types import ScalarDataType
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend", source_params(("duckdb", "postgres", "mysql", "trino", "clickhouse"))
)
@pytest.mark.parametrize("kind", ["ordinal", "period_offset"])
def test_period_alignment_native_carriers(
    backend: str,
    kind: Literal["ordinal", "period_offset"],
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
        metric = session.catalog.metrics.get("sales.total")
        current_axis = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
        baseline_axis = (
            session.time_scope(start="2026-07-01", end="2026-07-04").by(mv.grain("day"))
            if kind == "ordinal"
            else session.time_scope(start="2026-07-31", end="2026-08-03").by(mv.grain("day"))
        )
        current = session.observe({"value": metric}, during=current_axis, by=members)
        baseline = session.observe({"value": metric}, during=baseline_axis, by=members)
        comparison = current.compare(
            baseline, match=mv.ordinal() if kind == "ordinal" else mv.period_offset(-1), how="outer"
        )
        result = comparison.execute()
        frame = result.to_pandas()
        assert len(frame) == 6
        assert set(frame.iloc[:, 1]) == {("a", 1, 1), ("b", 2, 1)}
        assert result._manifest.data.schema.axes[0].columns[0].dtype == ScalarDataType("string")
        assert frame[("presence", ("current",))].sum() == 4
        assert frame[("presence", ("baseline",))].sum() == 6
        assert frame[("value", "value.absolute")].dropna().tolist() == (
            [2, -4, 0, 0] if kind == "ordinal" else [2, 0, -2, 0]
        )
        missing = frame[~frame[("presence", ("current",))]]
        assert len(missing) == 2
        assert missing[("value", "value.absolute")].isna().all()
        projection = next(
            p for p in frame.attrs["marivo"]["axes"][0]["projections"] if p["path"] == ("current",)
        )
        assert sum(row[0] is None for row in projection["values"]) == 2
        source_trace.record(result)
        assert source_trace.native_sql
