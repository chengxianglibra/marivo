"""Cached seed bytes retain physical types and independent writable databases."""

from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from tests.shared_fixtures import DSL_NAMES, DslCaseFactory, DslRows, seed_analysis_dsl_database


@pytest.mark.parametrize("float_amount", (False, True))
def test_dsl_seed_cold_warm_and_mutation_isolation(tmp_path: Path, float_amount: bool) -> None:
    # A unique quoted table name forces a cold key, then the same inputs reuse it.
    names = replace(DSL_NAMES, order=f'orders"{tmp_path.name}')
    rows = DslRows(
        (("A", "east"), (None, None)),
        (("one", "A", "web", "paid", "2026-08-01T00:00:00Z", 7),),
        (("line", "one", 3),),
    )
    table = '"' + names.order.replace('"', '""') + '"'
    for index in range(3):
        database = tmp_path / f"copy-{index}.duckdb"
        seed_analysis_dsl_database(database, names, rows, float_amount=float_amount)
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            assert connection.execute(f"SELECT amount FROM {table}").fetchall() == [(7,)]
            assert connection.execute(f"DESCRIBE {table}").fetchall()[-1][1] == (
                "DOUBLE" if float_amount else "BIGINT"
            )
            assert connection.execute("SELECT * FROM customer ORDER BY customer_id").fetchall() == [
                ("A", "east"),
                (None, None),
            ]
            assert connection.execute("SELECT * FROM order_line").fetchall() == [("line", "one", 3)]
            connection.execute(f"DELETE FROM {table}")


def test_dsl_case_uses_one_catalog_per_session(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    first = analysis_dsl_case_factory("j1")
    second = analysis_dsl_case_factory("j1")
    assert first.catalog is first.session.catalog
    assert second.catalog is second.session.catalog
    assert first.catalog is not second.catalog
    assert first.catalog.get("metric:sales.revenue").path == "sales.revenue"
    assert second.catalog.get("metric:sales.revenue").path == "sales.revenue"
