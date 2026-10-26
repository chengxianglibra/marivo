"""Actual remote relation routes retain the native-reader and host contracts."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case
from tests.shared_fixtures import catalog_value
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
        session = mv.session.get_or_create("remote-query", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {
                "category": catalog_value(session.catalog, "sales.facts.category", kind="field"),
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
        trace = capture_source(monkeypatch)
        result = query.compute().to_pandas()
        assert trace.uploaded_key_rows == []
        assert len(result) == (
            1 if operation in ("where", "row_count") else 2 if operation == "top" else 4
        )
        if operation == "row_count":
            assert result["n"].tolist() == [4]
        elif operation == "where":
            assert result["category"].tolist() == ["a"]
            assert result.iloc[:, 0].tolist() == [("t3", 3)]
        elif operation == "top":
            assert result["category"].tolist() == ["ä", "z"]
            assert result.iloc[:, 0].tolist() == [("t2", 2), ("t1", 1)]


@pytest.mark.parametrize("backend,profile", CASES)
def test_remote_aggregate_frame_bridge_uses_typed_key_transport(
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
        session = mv.session.get_or_create("remote-keys", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        producer = session.observe({"n": amount.count()}, by=(category,))
        selected = producer.top(1, by=producer["n"])
        fixed = selected.compute()
        keys = session.population_from(fixed, on=((fixed.axes[0], category),))
        query = session.observe({"n": amount.count()}, where=mv.in_population(keys))
        trace = capture_source(monkeypatch)
        frame = query.compute().to_pandas()
        assert frame["n"].tolist() == [1]
        assert trace.uploaded_key_rows == [1]


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
        session = mv.session.get_or_create("remote-char", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {
                "category": catalog_value(session.catalog, "sales.facts.category", kind="field"),
            },
        )
        for index, query in enumerate(
            (
                source.where(source["category"] == "a"),
                source.top(2, by=source["category"]),
                source.top(0, by=source["category"]),
            )
        ):
            if backend == "clickhouse":
                from marivo.analysis.materialization.errors import MaterializationError

                with pytest.raises(MaterializationError, match="lossless string carrier"):
                    query.compute()
                continue
            rows = query.compute().to_pandas()
            if index == 0:
                assert rows["category"].tolist() == ([] if backend == "trino" else ["a"])
            elif index == 1:
                assert rows["category"].tolist() == (
                    ["ä   ", "z   "] if backend == "trino" else ["ä", "z"]
                )
            else:
                assert rows.empty


def test_mysql_native_collated_groups_keep_the_existing_source_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("mysql", "innodb-table", tmp_path, monkeypatch, _data()) as case:
        _project("mysql", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("mysql-native-group", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        source = session.observe({"n": amount.count()}, by=(category,))
        selected = source.top(1, by=source["n"])
        fixed = selected.compute()
        keys = session.population_from(fixed, on=((fixed.axes[0], category),))
        query = session.observe({"n": amount.count()}, where=mv.in_population(keys))
        assert fixed.to_pandas()["n"].tolist() == [2]
        table = case.session.bind(case.source, source_identity="collation-oracle").relation
        key = fixed.to_pandas().iloc[0, 0]
        filtered = table.filter(table.category.identical_to(key))
        expected = case.session._backend.execute(filtered.aggregate(n=filtered.amount.count()))
        assert isinstance(expected, pd.DataFrame)
        rows = query.compute().to_pandas()
        assert rows["n"].tolist() == expected.n.tolist()
