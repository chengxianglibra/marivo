"""Version-addressed DuckDB relations written inside the existing source domain."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

import ibis
import ibis.expr.types as ir
import pyarrow as pa
from ibis.backends.duckdb import Backend
from sqlglot import expressions as sge

from marivo.analysis.compiler.nodes import CompiledDataset
from marivo.analysis.compiler.placement import ExecutionBinding
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _OrderedOrdering,
    _StaticRowBound,
)
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.contracts import EngineReceipt, ResourceRecord, RetainedPart
from marivo.analysis.materialization.errors import StorageAccessError
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    StoragePolicy,
    _checked_path,
    _create_directory,
    _fail,
    _fsync_directory,
    _hash_file,
    _integrity,
    _realized_schema,
)
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.targets import engine_domain


def _literal(value: str) -> str:
    return sge.Literal.string(value).sql(dialect="duckdb")


def ordered_relation(
    table: ir.Table,
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
) -> ir.Table:
    if not isinstance(rows.ordering, _OrderedOrdering):
        return table
    fields = {str(field.field_id): field.name for field in row.schema.columns}
    return table.order_by(
        [
            ibis.asc(table[fields[str(term.field_id)]], nulls_first=term.nulls == "first")
            if term.direction == "ascending"
            else ibis.desc(table[fields[str(term.field_id)]], nulls_first=term.nulls == "first")
            for term in rows.ordering.terms
        ]
    )


def checked_engine_path(project_root: Path, receipt: EngineReceipt) -> Path:
    path = _checked_path(project_root, Path(receipt.qualified_relation_ref))
    failed = False
    failure: Literal["missing", "unauthorized", "unknown"] | None = None
    try:
        failed = (
            (
                receipt.realized_byte_count is not None
                and path.stat().st_size != receipt.realized_byte_count
            )
            or _hash_file(path) != receipt.relation_version_or_snapshot_token
            or Path(str(path) + ".wal").exists()
        )
    except FileNotFoundError:
        failure = "missing"
    except PermissionError:
        failure = "unauthorized"
    except OSError:
        failure = "unknown"
    if failure is not None:
        raise StorageAccessError(failure)
    if failed:
        raise StorageAccessError("mutated")
    return path


def attach_engine_scan(backend: Backend, project_root: Path, receipt: EngineReceipt) -> ir.Table:
    """The Runtime-owned connection retains this read-only attachment until close."""
    path = checked_engine_path(project_root, receipt)
    name = "mv_artifact_" + uuid4().hex
    backend.raw_sql(f"ATTACH {_literal(str(path))} AS {name} (READ_ONLY)")
    return backend.table("rows", database=(name, "main"))


def validate_engine_relation(
    backend: Backend,
    table: ir.Table,
    receipt: EngineReceipt,
    row: DatasetRowContract,
    record: Callable[[str, str], None],
) -> None:
    count_expression = table.count()
    record("engine_check.input_count", backend.compile(count_expression))
    count: object = backend.execute(count_expression)
    if count != receipt.realized_row_count:
        _integrity("the exact engine receipt row count", "engine relation count differs")
    record("engine_check.input_schema", backend.compile(table.limit(0)))
    realized = _realized_schema(row.schema, backend.to_pyarrow(table.limit(0)).schema)
    if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
        _integrity("the exact engine receipt schema", "engine schema differs")


def write_engine_dataset(
    *,
    store: SessionStore,
    run_ref: str,
    session_ref: str,
    artifact_ref: str,
    staging: Path,
    final: Path,
    backend: Backend,
    binding: ExecutionBinding,
    recipe: CompiledDataset,
    row: DatasetRowContract,
    rows: DatasetRowSetContract,
    sampling: tuple[codec.SamplingRealization, ...],
    policy: StoragePolicy,
    event: Callable[[str], None],
    record: Callable[[str, str], None],
) -> DatasetWriteResult[EngineReceipt]:
    """Fence once, write each payload natively, and finalize every version before commit."""
    staging = _checked_path(store.project_root, staging)
    final = _checked_path(store.project_root, final)
    if staging.exists() or final.exists():
        _fail("unused reserved engine paths", "existing engine output", stage="storage_selection")
    _create_directory(staging)
    nonce = artifact_ref.removeprefix("artifact_")
    fence = "mv_output_" + nonce
    execution = next(
        item
        for item in store.resources(session_ref)
        if item.run_ref == run_ref
        and item.resource_kind == "backend_execution"
        and item.execution_domain_id == binding.datasource_id
    )
    store.reserve(
        ResourceRecord(
            run_ref,
            "planner_temporary_relation",
            binding.datasource_id,
            execution.ownership_nonce,
            execution.cleanup_capability_id,
            f"{execution.safe_locator}/{fence}",
        )
    )
    event("engine_producer_reserved")
    statement = f"CREATE TEMPORARY TABLE {fence} AS {backend.compile(recipe.expression)}"
    record("engine_producer", statement)
    event("source_statement")
    backend.raw_sql(statement)
    # The fence fixes all primary/part values; subsequent transactions scan only it.
    backend.raw_sql("COMMIT")
    fixed = backend.table(fence).cast(recipe.expression.schema())
    count_sql = f"SELECT count(*) FROM {fence}"
    record("engine_check.producer_count", count_sql)
    count_value: object = backend.con.execute(count_sql).fetchone()[0]
    if type(count_value) is not int:
        _fail("an exact engine row count", "invalid engine count")
    count = count_value
    bound = getattr(rows.cardinality, "row_bound", None)
    if (rows.cardinality.kind == "singleton" and count != 1) or (
        isinstance(bound, _StaticRowBound) and count > bound.max_rows
    ):
        _fail("the declared output cardinality", "engine row bound exceeded")
    nonnull = [field.name for field in row.schema.columns if not field.nullable]
    if nonnull:
        checks = [fixed[name].isnull() for name in nonnull]
        invalid = checks[0]
        for check in checks[1:]:
            invalid = invalid | check
        invalid_count = fixed.filter(invalid).count()
        record("engine_check.nullability", backend.compile(invalid_count))
        bad: object = backend.execute(invalid_count)
        if bad != 0:
            _fail("non-null required output fields", "null engine output field")
    primary = ordered_relation(fixed.select(recipe.primary_columns), row, rows)
    # LIMIT 0 obtains only the physical schema, never primary data.
    record("engine_check.primary_schema", backend.compile(primary.limit(0)))
    schema = backend.to_pyarrow(primary.limit(0)).schema
    realized = _realized_schema(row.schema, schema)
    specs: list[tuple[str, str, int, ir.Table, int]] = [
        ("primary", "", 0, primary, count),
        *(
            (
                f"parts/{part.role}",
                part.contract_id,
                part.contract_version,
                ordered_relation(fixed, row, rows).select(part.column_names),
                count,
            )
            for part in recipe.retained_parts
        ),
    ]
    if sampling:
        specs.append(
            (
                "parts/population_sampling_state",
                "population_sampling_state",
                1,
                ibis.literal(codec.digest(codec.sampling_payload(sampling)))
                .name("sampling_execution_digest")
                .as_table(),
                1,
            )
        )
    receipts: list[EngineReceipt] = []
    total = 0
    for index, (role, _contract, _version, expression, row_count) in enumerate(specs):
        directory = staging / role
        _create_directory(directory)
        path = directory / "payload.duckdb"
        alias = "mv_sink_" + uuid4().hex
        event("engine_payload_create")
        backend.raw_sql(f"ATTACH {_literal(str(path))} AS {alias} (READ_WRITE)")
        try:
            sql = f"CREATE TABLE {alias}.main.rows AS {backend.compile(expression)}"
            record("engine_write", sql)
            backend.raw_sql(sql)
            written = backend.table("rows", database=(alias, "main"))
            record("engine_check.written_schema", backend.compile(written.limit(0)))
            actual_schema = backend.to_pyarrow(written.limit(0)).schema
            if role == "parts/population_sampling_state":
                actual_schema = pa.schema(
                    [pa.field("sampling_execution_digest", pa.string(), nullable=False)]
                )
            count_sql = f"SELECT count(*) FROM {alias}.main.rows"
            record("engine_check.written_count", count_sql)
            actual_count: object = backend.con.execute(count_sql).fetchone()[0]
            if actual_count != row_count:
                _fail("the exact primary and part row counts", "written engine count differs")
        finally:
            backend.raw_sql(f"DETACH {alias}")
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        size = path.stat().st_size
        total += size
        if total > policy.max_stored_bytes:
            _fail(
                "combined engine payloads within the configured storage budget",
                "engine storage budget exceeded",
                stage="transfer_guard",
            )
        os.chmod(path, 0o444)
        _fsync_directory(directory)
        receipts.append(
            EngineReceipt(
                binding.datasource_id,
                engine_domain(binding),
                (final / role / "payload.duckdb").relative_to(store.project_root).as_posix(),
                _hash_file(path),
                codec.schema_fingerprint(realized)
                if index == 0
                else hashlib.sha256(actual_schema.serialize().to_pybytes()).hexdigest(),
                row_count,
                size,
            )
        )
    if (staging / "parts").exists():
        _fsync_directory(staging / "parts")
    _fsync_directory(staging)
    _create_directory(final.parent)
    event("before_rename")
    os.rename(staging, final)
    _fsync_directory(final.parent)
    _fsync_directory(staging.parent)
    event("after_rename")
    backend.raw_sql("BEGIN TRANSACTION")
    return DatasetWriteResult(
        receipts[0],
        tuple(
            RetainedPart(role.removeprefix("parts/"), contract, version, receipt)
            for (role, contract, version, _, _), receipt in zip(
                specs[1:], receipts[1:], strict=True
            )
        ),
        realized,
        count,
    )
