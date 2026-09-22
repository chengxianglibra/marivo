"""Real SQL checks for shared compilation and ordered preparation barriers."""

from collections.abc import Iterator

import ibis
import pytest

from marivo.analysis.compiler.nodes import CompiledValidation
from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter as Backend
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.validation import (
    ValidationBatch,
    compile_preparations,
    execute_batch,
)


@pytest.fixture
def backend() -> Iterator[Backend]:
    connection = Backend(ibis.duckdb.connect())
    try:
        yield connection
    finally:
        connection.disconnect()


@pytest.mark.parametrize("counts", [(), (0,), (0, 0), (0, 1), (1, 0), (2, 1), (0, None)])
def test_batch_keeps_named_results_and_first_failure(
    backend: Backend, counts: tuple[int | None, ...]
) -> None:
    checks = tuple(
        CompiledValidation(
            f"check.{index}", ibis.literal(count, type="int64").name("violations").as_table()
        )
        for index, count in enumerate(counts)
    )
    batches = compile_preparations(backend, checks, run_ref="run-test")
    if not checks:
        assert batches == ()
        return
    assert len(batches) == len(checks)
    assert all(isinstance(batch, ValidationBatch) for batch in batches)
    failed = [index for index, count in enumerate(counts) if count != 0]
    completed: list[tuple[str, int]] = []
    for index, batch in enumerate(batches):
        assert isinstance(batch, ValidationBatch)
        if failed and index == failed[0]:
            with pytest.raises(MaterializationError, match=rf"check\.{failed[0]}"):
                execute_batch(backend, batch, run_ref="run-test")
            break
        completed.extend(execute_batch(backend, batch, run_ref="run-test"))
    expected = checks[: failed[0]] if failed else checks
    assert completed == [(check.name, 0) for check in expected]


@pytest.mark.parametrize("count", [False, 0.0])
def test_batch_rejects_noninteger_counts_before_union_coercion(
    backend: Backend, count: bool | float
) -> None:
    checks = (
        CompiledValidation("valid", ibis.literal(0).name("violations").as_table()),
        CompiledValidation("invalid", ibis.literal(count).name("violations").as_table()),
    )
    with pytest.raises(MaterializationError, match="invalid validation relation: invalid"):
        compile_preparations(backend, checks, run_ref="run-test")


@pytest.mark.parametrize("failed_index", [None, 8, 16])
def test_large_check_sequence_keeps_order_and_owning_failure_across_batches(
    backend: Backend, failed_index: int | None
) -> None:
    checks = tuple(
        CompiledValidation(
            f"check.{index}",
            ibis.literal(int(index == failed_index)).name("violations").as_table(),
        )
        for index in range(17)
    )
    steps = compile_preparations(backend, checks, run_ref="run-test")
    batches = tuple(step for step in steps if isinstance(step, ValidationBatch))
    assert len(batches) == 17
    assert tuple(check for batch in batches for check in batch.checks) == checks
    completed: list[tuple[str, int]] = []
    for batch in batches:
        if any(check.name == f"check.{failed_index}" for check in batch.checks):
            with pytest.raises(MaterializationError, match=rf"check\.{failed_index}"):
                execute_batch(backend, batch, run_ref="run-test")
            break
        completed.extend(execute_batch(backend, batch, run_ref="run-test"))
    expected_count = 17 if failed_index is None else failed_index
    assert completed == [(f"check.{index}", 0) for index in range(expected_count)]


@pytest.mark.parametrize("rows", [0, 2])
def test_batch_rejects_missing_or_duplicate_scalar_rows_at_the_owning_check(
    backend: Backend, rows: int
) -> None:
    single = ibis.literal(0).name("violations").as_table()
    malformed = single.limit(0) if rows == 0 else ibis.union(single, single, distinct=False)
    checks = (CompiledValidation("malformed", malformed), CompiledValidation("next", single))
    batches = compile_preparations(backend, checks, run_ref="run-test")
    assert isinstance(batches[0], ValidationBatch)
    with pytest.raises(MaterializationError, match="source validation failed: malformed"):
        execute_batch(backend, batches[0], run_ref="run-test")
