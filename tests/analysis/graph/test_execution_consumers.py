"""Real source executions, explicit input sharing and terminal output reads."""

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.data_types import ScalarDataType
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.execution_fixtures import source_graph
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.datasource.source_cases import source_case, source_params
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import SourceTrace


def _values(session: mv.Session) -> mv.AnalysisFrame:
    return session.observe(
        {"value": session.catalog.get("metric:sales.total").value},
        by=session.members(session.catalog.get("entity:sales.facts").value),
    )


def _assert_values(result: mv.AnalysisFrame) -> None:
    frame = result.to_pandas()
    assert {
        key: None if pd.isna(value) else value
        for key, value in zip(frame.iloc[:, 0], frame["value"], strict=True)
    } == {
        ("a", 9007199254740992, 1): 2,
        ("a", 9007199254740993, 2): None,
        ("b", 9007199254740993, 1): 4,
    }
    assert [column.dtype for column in result.artifact._manifest.data.schema.axes[0].columns] == [
        ScalarDataType("string"),
        ScalarDataType("int64"),
        ScalarDataType("int64"),
    ]


def _assert_ratio(result: mv.AnalysisFrame) -> None:
    frame = result.to_pandas()
    assert frame["value"].dropna().tolist() == [1.0, 1.0]
    assert frame["value"].isna().tolist() == [False, True, False]


@pytest.mark.parametrize("backend", source_params(("duckdb",)))
def test_public_source_reexecution_and_terminal_read(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data(backend)
    with source_case(backend, "table", tmp_path, monkeypatch, data) as case:
        author_source_project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source-execution", timezone="UTC")
        query = _values(session)
        shared = query.select({"value": query["value"] / query["value"]})
        captures = []
        counts = []
        for _ in range(2):
            before = len(source_trace.native_sql)
            saved = query.persist()
            _assert_values(saved)
            counts.append(len(source_trace.native_sql) - before)
            assert counts[-1] > 0
            source_trace.record(saved)
            captures.append(saved)
        first, second = captures
        assert first.artifact.id != second.artifact.id
        assert first.artifact._manifest.run_id != second.artifact._manifest.run_id
        shared_result = shared.persist()
        _assert_ratio(shared_result)
        memory = first.compute()
        mixed = memory.align(query, on=tuple(zip(memory.axes, query.axes, strict=True)))
        before_mixed = len(source_trace.native_sql)
        mixed_result = mixed.select({"value": mixed["left.value"] / mixed["right.value"]}).compute()
        _assert_ratio(mixed_result)
        assert sum("SUM(" in sql.upper() for sql in source_trace.native_sql[before_mixed:]) == 1
        before_native = len(source_trace.native_sql)
        before_runs = terminal_runs(session)

        def forbidden(*args: object, **kwargs: object) -> NoReturn:
            raise AssertionError("Terminal read opened a business source")

        monkeypatch.setattr(SourceSession, "execute", forbidden)
        reopened = session.persisted_frame(first.artifact.id)
        _assert_values(reopened)
        _assert_ratio(session.persisted_frame(shared_result.artifact.id))
        exported = reopened.to_pandas()
        values = exported["value"]
        assert values[values > 0].tolist() == [2, 4]
        assert len(source_trace.native_sql) == before_native
        assert terminal_runs(session) == before_runs
        assert session._runtime.store.resources(session.id) == ()
        assert source_trace.qualifications
        source_trace.save(
            "source-reexecution-" + backend,
            case.environment,
            {"columns": data.columns, "values": data.values},
            {
                "values": [2, 0, 4],
                "source_native_counts": counts,
                "independent_runs": True,
                "offline_output_reads": True,
            },
            shared_result,
            (case.session,),
        )


def test_independent_equal_queries_execute_distinct_source_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    data = reference_data("duckdb")
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        author_source_project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("independent-work", timezone="UTC")
        first, second = _values(session), _values(session)
        assert (
            first is not second
            and source_graph(first).fingerprint == source_graph(second).fingerprint
        )
        shared = first.select({"value": first["value"] / first["value"]})
        aligned = first.align(second, on=tuple(zip(first.axes, second.axes, strict=True)))
        independent = aligned.select({"value": aligned["left.value"] / aligned["right.value"]})
        metric_reads = []
        native_counts = []
        for query in (shared, independent):
            before = len(source_trace.native_sql)
            before_owners = len(source_trace.owners)
            result = query.persist()
            _assert_ratio(result)
            submitted = source_trace.native_sql[before:]
            metric_reads.append(sum("SUM(" in sql.upper() for sql in submitted))
            native_counts.append(len(submitted))
            owners = source_trace.owners[before_owners:]
            assert owners and all(owner._closed for owner in owners)
            assert all(item.state == "succeeded" for owner in owners for item in owner.submissions)
            source_trace.record(result)
        assert metric_reads[0] > 0 and metric_reads[1] == metric_reads[0] + 1
        assert native_counts[1] > native_counts[0] > 0
        assert session._runtime.store.resources(session.id) == ()
        source_trace.save(
            "independent-source-work",
            case.environment,
            {"columns": data.columns, "values": data.values},
            {
                "sum_source_reads": metric_reads,
                "native_submissions": native_counts,
                "nullable_ratio": [1, None, 1],
            },
            None,
            (case.session,),
        )
