"""Cold construction and isolated warm reuse of physical execution inputs."""

from pathlib import Path

import duckdb
import ibis
import pytest

from marivo.analysis.compiler.nodes import CompiledValidation
from tests import lazy_execution_fixtures as fixtures


@pytest.mark.parametrize("counts", [(), (0,), (0, 0), (0, 1), (1, 0), (2, 1)])
def test_batched_validations_preserve_each_result_and_first_failure(
    counts: tuple[int, ...],
) -> None:
    checks = tuple(
        CompiledValidation(f"check.{index}", ibis.memtable({"violations": [count]}))
        for index, count in enumerate(counts)
    )
    failures = [index for index, count in enumerate(counts) if count]
    if failures:
        with pytest.raises(AssertionError, match=rf"check\.{failures[0]}"):
            fixtures.assert_compiled_validations(checks)
    else:
        fixtures.assert_compiled_validations(checks)


def test_execution_template_preserves_all_tables_and_isolates_warm_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures._execution_database_template.cache_clear()
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    try:
        fixtures.seed_execution_database(first)

        def unexpected_build(database: Path) -> None:
            raise AssertionError(f"Warm reuse rebuilt {database}")

        monkeypatch.setattr(fixtures, "_build_execution_database", unexpected_build)
        fixtures.seed_execution_database(second)
        with duckdb.connect(str(first), config={"threads": 1}) as connection:
            connection.execute("DELETE FROM orders")
        with duckdb.connect(str(second), config={"threads": 1}) as connection:
            expected_counts = {
                "orders": 6,
                "lines": 5,
                "customers": 4,
                "snapshots": 3,
                "validity": 3,
                "unkeyed": 0,
                "composite": 3,
            }
            assert {row[0] for row in connection.execute("SHOW TABLES").fetchall()} == set(
                expected_counts
            )
            for table, count in expected_counts.items():
                assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (count,)
                assert len(connection.execute(f'DESCRIBE "{table}"').fetchall()) == 11
            assert connection.execute(
                "SELECT id, customer_id, amount, weight, CAST(day AS VARCHAR) "
                "FROM orders ORDER BY id"
            ).fetchall() == list(fixtures.ORDER_VALUES)
        with pytest.raises(FileExistsError):
            fixtures.seed_execution_database(second)
    finally:
        fixtures._execution_database_template.cache_clear()
