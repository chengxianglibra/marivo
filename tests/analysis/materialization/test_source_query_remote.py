"""Actual remote relation routes retain the native-reader and host contracts."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_query_fusion import _execute
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime
CASES = (("mysql", "innodb-table"), ("trino", "iceberg"), ("clickhouse", "mergetree"))


def _data(*, fixed: bool = False, binary: bool = False) -> SourceData:
    category = "CHAR(4)" if fixed else "VARCHAR(16)"
    if binary:
        category += " COLLATE utf8mb4_0900_bin"
    return SourceData(
        f"id BIGINT, tenant VARCHAR(16), category {category}, amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        "(1,'t1','z',1,9007199254740993,1,DATE '2026-07-01'),"
        "(2,'t2','ä',1,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','a',1,NULL,1,DATE '2026-07-01'),"
        "(4,'t4',NULL,NULL,NULL,NULL,DATE '2026-07-01')",
        "id Int64, tenant String, category Nullable("
        + ("FixedString(4)" if fixed else "String")
        + "), amount Nullable(Int64), big Nullable(Int64), weight Nullable(Int64), day Date",
        [
            {
                "id": index,
                "tenant": f"t{index}",
                "category": label,
                "amount": 1 if index != 4 else None,
                "big": 9007199254740993 if index == 1 else None,
                "weight": 1 if index != 4 else None,
                "day": date(2026, 7, 1),
            }
            for index, label in enumerate(("z", "ä", "a", None), 1)
        ],
    )


@pytest.mark.parametrize("backend,profile", CASES)
@pytest.mark.parametrize("operation", ("read", "where", "top", "row_count"))
def test_remote_terminal_string_relations(
    backend: str,
    profile: str,
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, _data(binary=backend == "mysql")
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-query", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {
                "category": session.catalog.fields.get("sales.facts.category"),
            },
        )
        query = (
            source.where(source["category"] == "a")
            if operation == "where"
            else (
                source.top(2, by=source["category"])
                if operation == "top"
                else (
                    source.reduce(values={"n": mv.row_count()})
                    if operation == "row_count"
                    else source
                )
            )
        )
        baseline = _execute(session, query, fuse=False)
        optimized = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(optimized.table, baseline.table)
        trace = capture_source(monkeypatch)
        result = query.execute().to_pandas()
        assert trace.returned_rows == [{"read": 4, "where": 1, "top": 2, "row_count": 1}[operation]]
        assert trace.uploaded_key_rows == []
        assert len(result) == (
            1 if operation in ("where", "row_count") else 2 if operation == "top" else 4
        )


@pytest.mark.parametrize("backend,profile", CASES)
def test_remote_aggregate_key_producer_has_no_host_roundtrip(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, _data(binary=backend == "mysql")
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-keys", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        producer = session.observe({"n": amount.count()}, by=(category,))
        selected = producer.top(1, by=producer["n"])
        query = session.observe(
            {"n": amount.count()}, where=mv.in_population(selected.population())
        )
        baseline = _execute(session, query, fuse=False)
        optimized = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(optimized.table, baseline.table)
        trace = capture_source(monkeypatch)
        frame = query.execute().to_pandas()
        assert frame[("value", "n")].tolist() == [1]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", CASES)
def test_remote_fixed_strings_keep_reader_bytes(
    backend: str,
    profile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, _data(fixed=True, binary=backend == "mysql")
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-char", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {
                "category": session.catalog.fields.get("sales.facts.category"),
            },
        )
        for query in (
            source.where(source["category"] == "a"),
            source.top(2, by=source["category"]),
            source.top(0, by=source["category"]),
        ):
            if backend == "clickhouse":
                from marivo.analysis.materialization.errors import MaterializationError

                for fuse in (False, True):
                    with pytest.raises(MaterializationError, match="lossless string carrier"):
                        _execute(session, query, fuse=fuse)
                continue
            baseline = _execute(session, query, fuse=False)
            optimized = _execute(session, query, fuse=True)
            pd.testing.assert_frame_equal(optimized.table, baseline.table)


def test_mysql_native_collated_groups_keep_the_existing_source_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, _data()) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-native-group", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        category = session.catalog.fields.get("sales.facts.category")
        source = session.observe({"n": amount.count()}, by=(category,))
        selected = source.top(1, by=source["n"])
        query = session.observe(
            {"n": amount.count()}, where=mv.in_population(selected.population())
        )
        baseline = _execute(session, query, fuse=False)
        optimized = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(optimized.table, baseline.table)
        assert baseline.table["value.0"].tolist() == [2]
