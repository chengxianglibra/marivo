"""Qualified PostgreSQL Query relations preserve host byte ordering and checks."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.session.core import Session
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_query_fusion import _execute
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
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
        yield mv.session.get_or_create("postgres-source-query", report_timezone="UTC")


def _attributes(session: Session) -> mv.Query:
    return session.read(
        session.members(ms.ref.entity("sales.facts")),
        {
            "category": session.catalog.fields.get("sales.facts.category"),
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
    baseline = _execute(session, query, fuse=False)
    optimized = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(optimized.table, baseline.table)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    assert trace.returned_rows == [len(result)]
    assert trace.uploaded_key_rows == []
    if operation == "string_top":
        assert result[("value", "category")].tolist() == ["ä   ", "z   "]
        assert "CONVERT_TO" in trace.native_sql[0].upper()


@pytest.mark.parametrize("literal,expected_rows", (("a", 0), ("a   ", 1)))
def test_postgres_char_predicate_uses_returned_bytes(
    postgres_query_session: Session, literal: str, expected_rows: int
) -> None:
    session = postgres_query_session
    source = _attributes(session)
    query = source.where(source["category"] == literal)
    baseline = _execute(session, query, fuse=False)
    optimized = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(optimized.table, baseline.table)
    assert len(optimized.table) == expected_rows


@pytest.mark.parametrize("selection", ("where", "top"))
def test_postgres_aggregate_population_has_no_host_key_transport(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    session = postgres_query_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"count": amount.count()}, by=(category,))
    selected = (
        producer.where(producer["count"] > 0)
        if selection == "where"
        else producer.top(1, by=producer["count"])
    )
    query = session.observe(
        {"count": amount.count()}, where=mv.in_population(selected.population())
    )
    baseline = _execute(session, query, fuse=False)
    optimized = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(optimized.table, baseline.table)
    trace = capture_source(monkeypatch)
    result = query.execute().to_pandas()
    assert result[("value", "count")].tolist() == [3 if selection == "where" else 1]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []


def test_postgres_parent_strict_null_check_survives_empty_selection(
    postgres_query_session: Session,
) -> None:
    session = postgres_query_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    producer = session.observe({"sum": amount.sum(nulls="error")}, by=(category,))
    selected = producer.where(producer["sum"] > 1000)
    query = session.observe({"sum": amount.sum()}, where=mv.in_population(selected.population()))
    for fuse in (False, True):
        with pytest.raises(SemanticExpressionError, match="non-NA"):
            _execute(session, query, fuse=fuse)


@pytest.mark.parametrize("operation", ("where", "top"))
def test_postgres_nullable_numeric_fields_keep_pandas_conversion_boundary(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    session = postgres_query_session
    source = session.read(
        session.members(ms.ref.entity("sales.facts")),
        {"amount": session.catalog.fields.get("sales.facts.amount")},
    )
    query = (
        source.where(source["amount"] > 20)
        if operation == "where"
        else source.top(2, by=source["amount"])
    )
    baseline = _execute(session, query, fuse=False)
    optimized = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(optimized.table, baseline.table)
    trace = capture_source(monkeypatch)
    query.execute()
    assert trace.returned_rows == [4, 4]


def test_postgres_large_nullable_integer_predicate_keeps_existing_conversion(
    postgres_query_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = postgres_query_session
    source = session.read(
        session.members(ms.ref.entity("sales.facts")),
        {"big": session.catalog.fields.get("sales.facts.big")},
    )
    query = source.where(source["big"] > 2**53)
    baseline = _execute(session, query, fuse=False)
    optimized = _execute(session, query, fuse=True)
    pd.testing.assert_frame_equal(optimized.table, baseline.table)
    assert optimized.table.empty
    trace = capture_source(monkeypatch)
    query.execute()
    assert trace.returned_rows == [4, 4]
