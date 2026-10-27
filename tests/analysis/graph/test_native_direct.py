"""Native terminal output, exact identity and publication failure boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.adapters import CompiledRead, SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.physical_workloads import Workload, workload
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.materialization.publication_fixtures import publication_counts
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import catalog_value, terminal_runs


def _scalar_output(result: mv.AnalysisFrame, total: int | None) -> None:
    values = result.to_pandas()["value"]
    assert values.isna().all() if total is None else values.tolist() == [total]
    assert tuple(axis.role for axis in result.axes) in ((), ("interval",))
    assert result._local._audit_manifest(result._require_stored("artifact_id")).data.schema.values[
        0
    ].dtype == ScalarDataType("int64")
    assert all(
        item.name != "original_state"
        for item in result._local._audit_manifest(
            result._require_stored("artifact_id")
        ).data.attachments
    )


def _support(work: Workload) -> mv.AnalysisFrame:
    raw = work.session.read(
        work.session.members(work.session.catalog.get("entity:cost.facts").value),
        {"value": catalog_value(work.session.catalog, "cost.facts.amount", kind="field")},
    )
    return raw.where(~raw["value"].is_null()).reduce({"count": mv.row_count()})


def test_native_sum_reads_only_terminal_exchange_with_all_checks_and_fresh_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        logical = work.total()
        reads: list[CompiledRead] = []
        execute = SourceSession.execute

        def read(source: SourceSession, issued: CompiledRead) -> pd.DataFrame:
            reads.append(issued)
            return execute(source, issued)

        monkeypatch.setattr(SourceSession, "execute", read)
        results = []
        for _ in range(2):
            before = len(reads)
            result = logical.persist()
            _scalar_output(result, 529)
            submitted = reads[before:]
            aggregates = [item for item in submitted if "SUM(" in item.sql.upper()]
            assert len(aggregates) == 1
            assert aggregates[0].purpose.startswith("query.")
            assert all(item.purpose != "analysis.domain.prepare" for item in submitted)
            results.append(result)
        first, second = results
        assert first.artifact_id != second.artifact_id
        assert (
            first._local._audit_manifest(first._require_stored("artifact_id")).run_id
            != second._local._audit_manifest(second._require_stored("artifact_id")).run_id
        )
        assert len(terminal_runs(work.session)) == 2
        assert _support(work).persist().to_pandas()["count"].tolist() == [61]
        captured = work.values().persist()
        frame = captured.to_pandas()
        assert [
            column.dtype
            for column in captured._local._audit_manifest(captured._require_stored("artifact_id"))
            .data.schema.axes[1]
            .columns
        ] == [
            ScalarDataType("string"),
            ScalarDataType("int64"),
            ScalarDataType("int64"),
        ]
        actual = {
            key: None if pd.isna(value) else value
            for key, value in zip(frame[ordinary_columns(frame)[0]], frame["value"], strict=True)
        }
        expected = {(row["tenant"], row["id"], row["revision"]): row["amount"] for row in work.rows}
        assert actual == expected and len(actual) == 64
        assert work.session._runtime.store.resources(work.session.id) == ()


def test_native_sum_distinguishes_all_null_and_empty_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for scenario in ("all_null", "empty"):
        with workload(
            "duckdb", "ordinary-table", 64, scenario, tmp_path / scenario, monkeypatch
        ) as work:
            _scalar_output(work.total().persist(), None if scenario == "all_null" else 0)
            assert _support(work).persist().to_pandas()["count"].tolist() == [0]
            assert work.session._runtime.store.resources(work.session.id) == ()


def test_native_late_invalid_source_frame_never_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with workload("duckdb", "ordinary-table", 64, "baseline", tmp_path, monkeypatch) as work:
        logical = work.total()
        before = publication_counts(work.session)
        execute = SourceSession.execute
        corrupted: list[str] = []

        def read(source: SourceSession, issued: CompiledRead) -> pd.DataFrame:
            frame = execute(source, issued)
            if "SUM(" in issued.sql.upper():
                corrupted.append("schema")
                return frame.drop(columns=[frame.columns[-1]])
            return frame

        monkeypatch.setattr(SourceSession, "execute", read)
        with pytest.raises(MaterializationError):
            logical.persist()
        assert corrupted == ["schema"]
        assert publication_counts(work.session) == before
        runs = terminal_runs(work.session)
        assert runs == ()
        assert work.session._runtime.store.resources(work.session.id) == ()


def test_source_local_comparison_keeps_its_existing_staged_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    with source_case("duckdb", "table", tmp_path, monkeypatch, reference_data("duckdb")) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-hybrid", timezone="UTC")
        values = session.observe(
            {"value": session.catalog.get("metric:sales.total").value},
            by=session.members(session.catalog.get("entity:sales.facts").value),
        )
        result = values.select({"ratio": values["value"] / values["value"]}).persist()
        frame = result.to_pandas()
        ratios = dict(zip(frame[ordinary_columns(frame)[0]], frame["ratio"], strict=True))
        assert ratios[("a", 9007199254740992, 1)] == 1
        assert ratios[("b", 9007199254740993, 1)] == 1
        assert pd.isna(ratios[("a", 9007199254740993, 2)])
        assert session._runtime.store.resources(session.id) == ()
