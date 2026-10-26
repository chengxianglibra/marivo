"""Qualified PostgreSQL Query relations preserve host byte ordering and checks."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime


@pytest.fixture
def postgres_query_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> Iterator[Session]:
    data = SourceData(
        'id BIGINT, tenant CHAR(4) COLLATE "en-x-icu", category CHAR(4) COLLATE "en-x-icu", amount BIGINT, big BIGINT, weight BIGINT, day DATE',
        "(1,'ä','z',20,9007199254740993,1,DATE '2026-07-01'),"
        "(2,'z','ä',20,NULL,1,DATE '2026-07-01'),"
        "(3,'a','a',30,NULL,1,DATE '2026-07-01'),"
        "(4,'b',NULL,NULL,NULL,NULL,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("postgres-source-query", timezone="UTC")


def _attributes(session: Session) -> mv.AnalysisFrame:
    return session.read(
        session.members(session.catalog.get("entity:sales.facts").value),
        {
            "category": catalog_value(session.catalog, "sales.facts.category", kind="field"),
        },
    )


@pytest.mark.parametrize("operation", ("read", "select", "where", "string_top"))
def test_postgres_terminal_field_relation(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = postgres_query_session
    source = _attributes(session)
    query = (
        source.select({"category": source["category"]})
        if operation == "select"
        else source.where(source["category"] == "a   ")
        if operation == "where"
        else source.top(2, by=source["category"])
        if operation == "string_top"
        else source
    )
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert trace.uploaded_key_rows == []
    assert len(result) == (1 if operation == "where" else 2 if operation == "string_top" else 4)
    if operation == "where":
        assert [key[-1] for key in result.iloc[:, 0]] == [3]
    if operation == "string_top":
        assert result["category"].tolist() == ["ä   ", "z   "]
        assert [key[-1] for key in result.iloc[:, 0]] == [2, 1]


@pytest.mark.parametrize("literal,expected_rows", (("a", 0), ("a   ", 1)))
def test_postgres_char_predicate_uses_returned_bytes(
    postgres_query_session: Session, literal: str, expected_rows: int
) -> None:
    session = postgres_query_session
    source = _attributes(session)
    query = source.where(source["category"] == literal)
    optimized = query.compute().to_pandas()
    assert len(optimized) == expected_rows


@pytest.mark.parametrize("selection", ("where", "top"))
def test_postgres_aggregate_frame_bridge_uses_typed_key_transport(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    session = postgres_query_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    producer = session.observe({"count": amount.count()}, by=(category,))
    selected = (
        producer.where(producer["count"] > 0)
        if selection == "where"
        else producer.top(1, by=producer["count"])
    )
    fixed = selected.compute()
    keys = session.population_from(fixed, on=((fixed.axes[0], category),))
    query = session.observe({"count": amount.count()}, where=mv.in_population(keys))
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert result["count"].tolist() == [3 if selection == "where" else 1]
    assert trace.uploaded_key_rows == [3 if selection == "where" else 1]


def test_postgres_parent_strict_null_check_survives_empty_selection(
    postgres_query_session: Session,
) -> None:
    session = postgres_query_session
    amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
    category = catalog_value(session.catalog, "sales.facts.category", kind="field")
    producer = session.observe({"sum": amount.sum(nulls="error")}, by=(category,))
    selected = producer.where(producer["sum"] > 1000)
    keys = session.population_from(selected, on=((selected.axes[0], category),))
    query = session.observe({"sum": amount.sum()}, where=mv.in_population(keys))
    with pytest.raises(MaterializationError, match="non-NA"):
        query.compute()


@pytest.mark.parametrize("operation", ("where", "top"))
def test_postgres_nullable_numeric_fields_keep_pandas_conversion_boundary(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = postgres_query_session
    source = session.read(
        session.members(session.catalog.get("entity:sales.facts").value),
        {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
    )
    query = (
        source.where(source["amount"] > 20)
        if operation == "where"
        else source.top(2, by=source["amount"])
    )
    trace = capture_source(monkeypatch)
    result = query.compute().to_pandas()
    assert [key[-1] for key in result.iloc[:, 0]] == ([3] if operation == "where" else [3, 2])
    assert result["amount"].tolist() == ([30] if operation == "where" else [30, 20])
    assert trace.uploaded_key_rows == []


def test_postgres_large_nullable_integer_predicate_keeps_existing_conversion(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_query_session
    source = session.read(
        session.members(session.catalog.get("entity:sales.facts").value),
        {"big": catalog_value(session.catalog, "sales.facts.big", kind="field")},
    )
    query = source.where(source["big"] > 2**53)
    optimized = query.compute().to_pandas()
    assert optimized.empty
    trace = capture_source(monkeypatch)
    assert query.compute().to_pandas().empty
    assert trace.uploaded_key_rows == []
