"""Shared assertion compilation within ordered source preparation boundaries."""

from __future__ import annotations

from dataclasses import dataclass

import ibis
from duckdb import DuckDBPyConnection
from ibis.backends.duckdb import Backend

from marivo.analysis.compiler.nodes import (
    CompiledRelationFence,
    CompiledSampleFence,
    CompiledValidation,
)
from marivo.analysis.materialization.errors import MaterializationError


@dataclass(frozen=True, slots=True, repr=False)
class ValidationBatch:
    checks: tuple[CompiledValidation, ...]
    sql: str


def compile_preparations(
    backend: Backend,
    preparations: tuple[CompiledValidation | CompiledSampleFence | CompiledRelationFence, ...],
    *,
    run_ref: str,
) -> tuple[ValidationBatch | CompiledSampleFence | CompiledRelationFence, ...]:
    result: list[ValidationBatch | CompiledSampleFence | CompiledRelationFence] = []
    pending: list[CompiledValidation] = []

    def flush() -> None:
        if not pending:
            return
        tables = []
        for index, check in enumerate(pending):
            if (
                tuple(check.expression.columns) != ("violations",)
                or not check.expression.schema()["violations"].is_integer()
            ):
                raise MaterializationError(
                    expected="one integer violations column per compiled source validation",
                    received=f"invalid validation relation: {check.name}",
                    repair="Correct the registered source validation expression.",
                    stage="implementation_registration",
                    run_ref=run_ref,
                )
            tables.append(check.expression.mutate(validation_ordinal=ibis.literal(index)))
        expression = ibis.union(*tables) if len(tables) > 1 else tables[0]
        result.append(
            ValidationBatch(
                tuple(pending), backend.compile(expression.order_by("validation_ordinal"))
            )
        )
        pending.clear()

    for preparation in preparations:
        if isinstance(preparation, (CompiledSampleFence, CompiledRelationFence)):
            flush()
            if isinstance(preparation, CompiledRelationFence):
                backend.compile(preparation.expression)
            result.append(preparation)
        else:
            pending.append(preparation)
            # Even small unions retain too much aggregate state for complex checks.
            # Plan separate queries up front under the unchanged native memory cap.
            flush()
    flush()
    return tuple(result)


def execute_batch(
    backend: Backend, batch: ValidationBatch, *, run_ref: str
) -> tuple[tuple[str, int], ...]:
    cursor: object = backend.raw_sql(batch.sql)
    if not isinstance(cursor, DuckDBPyConnection):
        raise _failure(batch.checks[0], run_ref)
    results: list[tuple[str, int]] = []
    row: object = cursor.fetchone()
    for index, check in enumerate(batch.checks):
        if (
            not isinstance(row, tuple)
            or len(row) != 2
            or type(row[0]) is not int
            or row[0] != 0
            or type(row[1]) is not int
            or row[1] != index
        ):
            raise _failure(check, run_ref)
        results.append((check.name, row[0]))
        row = cursor.fetchone()
        if isinstance(row, tuple) and len(row) == 2 and row[1] == index:
            raise _failure(check, run_ref)
    if row is not None:
        raise _failure(batch.checks[-1], run_ref)
    return tuple(results)


def _failure(check: CompiledValidation, run_ref: str) -> MaterializationError:
    return MaterializationError(
        expected=check.expected or "zero violations of the declared source validation",
        received=f"source validation failed: {check.name}",
        repair=check.repair
        or "Repair the governed source identity, temporal coverage or component reconciliation.",
        stage="output_validation",
        run_ref=run_ref,
    )
