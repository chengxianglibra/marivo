"""Qualified source carriers feed paired periods without losing endpoint presence."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.core.artifact_data import DataSchema
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.numeric.test_period_alignment import paired_tables, temporal_keys
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend", source_params(("duckdb", "postgres", "mysql", "trino", "clickhouse"))
)
@pytest.mark.parametrize("kind", ["ordinal", "periods"])
def test_period_alignment_native_carriers(
    backend: str,
    kind: Literal["ordinal", "periods"],
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
        session = mv.session.get_or_create("period-native", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.facts"))
        metric = session.catalog.metrics.get("sales.total")
        current_axis = session.time_scope(start="2026-08-01", end="2026-08-03").by(mv.grain("day"))
        baseline_axis = (
            session.time_scope(start="2026-07-01", end="2026-07-04").by(mv.grain("day"))
            if kind == "ordinal"
            else session.time_scope(start="2026-07-31", end="2026-08-03").by(mv.grain("day"))
        )
        current = session.observe({"value": metric}, by=mv.product(current_axis, members))
        baseline = session.observe({"value": metric}, by=mv.product(baseline_axis, members))
        comparison = current.compare(
            {
                "reference": mv.baseline(
                    baseline,
                    match=mv.ordinal() if kind == "ordinal" else mv.offset(periods=-1),
                    on=tuple(zip(current.axes, baseline.axes, strict=True)),
                )
            }
        )
        with pytest.raises(AnalysisError, match="actual baseline time row"):
            comparison.compute()
        aligned = current.align(
            baseline,
            match=mv.ordinal() if kind == "ordinal" else mv.offset(periods=-1),
            on=tuple(zip(current.axes, baseline.axes, strict=True)),
        )
        result = aligned.select(
            {"change": aligned["left.value"] - aligned["right.value"]}
        ).compute()
        frame = result.to_pandas()
        assert len(frame) == 6
        assert set(frame[frame.attrs["axes"][1]["name"]]) == {("a", 1, 1), ("b", 2, 1)}
        assert isinstance(result._schema, DataSchema)
        assert result._schema.axes[0].columns[0].dtype == ScalarDataType("int64")
        assert frame["left_present"].sum() == 4
        assert frame["right_present"].sum() == 6
        assert frame["change"].dropna().tolist() == (
            [2, -4, 0, 0] if kind == "ordinal" else [2, 0, -2, 0]
        )
        missing = frame[~frame["left_present"]]
        assert len(missing) == 2
        assert missing["change"].isna().all()
        mapping, _, _ = paired_tables(frame)
        assert mapping["left.0"].isna().sum() == 1
        absent_pairs = set(mapping.loc[mapping["left.0"].isna()].iloc[:, 0])
        assert set(temporal_keys(result).loc[~frame["left_present"]]) == absent_pairs
        source_trace.record(result)
        assert source_trace.native_sql
