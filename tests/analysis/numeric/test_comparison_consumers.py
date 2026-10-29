"""Native composite-key comparisons and source-free terminal reads."""

from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.reference_fixtures import reference_data
from tests.analysis.graph.source_fixtures import author_source_project
from tests.analysis.numeric.comparison_fixtures import (
    DEFINED,
    Operation,
    assert_comparison,
    comparison_queries,
)
from tests.datasource.source_cases import source_case
from tests.shared_fixtures import terminal_runs
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize("operation", ("nested", "union", "ratio"))
def test_complete_keys_source_and_fixed(
    operation: Operation,
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
        session = mv.session.get_or_create("comparison-consumer", timezone="UTC")
        query = comparison_queries(session)[operation]
        source = query.persist()
        assert_comparison(source, operation)
        source_trace.record(source)
        if operation == "nested":
            selected_values = source.where(source["value"] > 0).compute()
            selected = session.population_from(
                selected_values,
                on=((selected_values.axes[0], session.catalog.get("entity:sales.facts")),),
            )
            keys = session.frame(selected).compute().to_pandas().iloc[:, 0]
            assert set(keys) == {
                key for key, value in DEFINED.items() if value is not None and value > 0
            }
        if operation == "ratio":
            assert not hasattr(query, "rollup")
        native = len(source_trace.native_sql)
        before = terminal_runs(session)

        def forbid_source(*args: object, **kwargs: object) -> NoReturn:
            pytest.fail("Terminal comparison read touched a source")

        monkeypatch.setattr(SourceSession, "execute", forbid_source)
        reopened = session.persisted_frame(source.artifact_id)
        assert_comparison(reopened, operation)
        assert len(source_trace.native_sql) == native and terminal_runs(session) == before
        assert session._runtime.store.resources(session.id) == ()
        source_trace.save(
            "comparison-consumer-" + operation,
            case.environment,
            {"columns": data.columns, "values": data.values},
            {
                "operation": operation,
                "keys": ["string", "int64", "int64"],
                "terminal_source_reads": 0,
            },
            reopened,
            (case.session,),
        )
