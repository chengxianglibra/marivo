"""Selected receipt dispatch with bounded engine and version-pinned PyArrow reads."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization import storage
from marivo.analysis.materialization.contracts import (
    EngineReceipt,
    LocalReceipt,
    ObjectReceipt,
    RetainedPart,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import MaterializationError, StorageAccessError
from marivo.analysis.materialization.storage import ReadPolicy, _integrity, _limited
from marivo.analysis.materialization.targets import (
    S3Access,
    access_payload,
    decode_access,
    object_access,
)

_DEFAULT_READ_POLICY = ReadPolicy()
_WORKER_CODE = (
    "from marivo.analysis.materialization.reads import read_worker_entry; read_worker_entry()"
)


def _object_read_access(bindings: tuple[S3Access, ...], reference: str) -> S3Access:
    """Classify unavailable reader authority without changing target selection errors."""
    try:
        return object_access(bindings, reference)
    except MaterializationError as error:
        if type(error) is not MaterializationError or error.stage != "storage_selection":
            raise
    raise StorageAccessError("unauthorized")


def _payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Iterator[pa.RecordBatch]:
    """Read only this payload. Complete exhaustion includes content verification."""
    if not preview and not audit and receipt.realized_row_count > policy.max_rows:
        _limited("committed row count exceeds the collection limit")
    started = time.monotonic()
    count = decoded = 0

    def checked(incoming: Iterator[pa.RecordBatch]) -> Iterator[pa.RecordBatch]:
        nonlocal count, decoded
        seen = False
        for batch in incoming:
            seen = True
            batch = storage._normalize_batch(batch, policy.max_batch_bytes)
            if preview:
                batch = batch.slice(0, max(0, policy.preview_rows - count))
            count += batch.num_rows
            decoded += batch.nbytes
            if not audit and (
                count > (policy.preview_rows if preview else policy.max_rows)
                or decoded > policy.max_decoded_bytes
            ):
                _limited("selected payload exceeds its collection budget")
            if time.monotonic() - started > policy.deadline_seconds:
                _limited("selected payload read deadline exceeded")
            yield batch
            if preview and count >= policy.preview_rows:
                return
        if not seen and receipt.realized_row_count:
            _integrity("all selected payload rows", "missing payload stream")

    if isinstance(receipt, EngineReceipt):
        # This connection reads an engine Artifact. It never imports Parquet or origins.
        import ibis

        from marivo.analysis.materialization.engine import checked_engine_path, ordered_relation

        path = checked_engine_path(project_root, receipt)
        backend = ibis.duckdb.connect(str(path), read_only=True)
        try:
            backend.raw_sql("SET threads=1")
            backend.raw_sql("SET memory_limit='256MiB'")
            backend.raw_sql("SET max_temp_directory_size='0B'")
            table = backend.table("rows")
            if row is not None and rows is not None:
                table = ordered_relation(table, row, rows)
            if preview:
                table = table.limit(policy.preview_rows)
            # Prove a bounded fetch including variable-width values before allocation.
            strings = [
                table[name].length().fill_null(0) * 4
                for name, kind in table.schema().items()
                if kind.is_string()
            ]
            maximum: object = (
                backend.execute(ibis.greatest(*strings).max().fill_null(0)) if strings else 0
            )
            if not isinstance(maximum, int):
                _integrity("an exact engine fetch width", "unknown engine fetch width")
            width = len(table.columns) * (int(maximum) + 64) * 2
            if width > policy.max_batch_bytes:
                _limited("engine value exceeds bounded fetch budget")
            reader = backend.to_pyarrow_batches(
                table, chunk_size=max(1, min(1024, policy.max_batch_bytes // max(1, width)))
            )
            try:

                def batches() -> Iterator[pa.RecordBatch]:
                    yield pa.RecordBatch.from_arrays(
                        [pa.array([], type=f.type) for f in reader.schema], schema=reader.schema
                    )
                    yield from reader

                yield from checked(batches())
            finally:
                reader.close()
            checked_engine_path(project_root, receipt)
        finally:
            backend.disconnect()
    elif isinstance(receipt, ObjectReceipt):
        from marivo.analysis.materialization.object_storage import (
            ObjectRangeFile,
            client,
            open_manifest,
        )

        access = _object_read_access(bindings, receipt.object_store_ref)
        with client(access) as s3:
            file = open_manifest(s3, access, receipt)
            with (
                ObjectRangeFile(s3, access, file, policy.max_batch_bytes) as stream,
                pq.ParquetFile(stream, page_checksum_verification=True) as parquet,
            ):
                if parquet.metadata.num_rows != receipt.realized_row_count:
                    _integrity("the committed object row count", "object row count differs")
                yield pa.RecordBatch.from_arrays(
                    [pa.array([], type=f.type) for f in parquet.schema_arrow],
                    schema=parquet.schema_arrow,
                )
                yield from checked(
                    storage._bounded_batches(parquet, policy, preview=preview, use_threads=False)
                )
            if not preview:
                file.verify(s3, access)
    else:
        parquet, path = storage._open_payload(project_root, receipt)
        try:
            if audit and storage._hash_file(path) != receipt.bytes_hash:
                raise StorageAccessError("mutated")
            yield pa.RecordBatch.from_arrays(
                [pa.array([], type=f.type) for f in parquet.schema_arrow],
                schema=parquet.schema_arrow,
            )
            yield from checked(storage._bounded_batches(parquet, policy, preview=preview))
            if not preview and (
                storage._hash_file(path) != receipt.bytes_hash
                or receipt.file_manifest[0].sha256 != receipt.bytes_hash
            ):
                _integrity("the immutable selected local payload", "local content changed")
        finally:
            parquet.close()
    if not preview and count != receipt.realized_row_count:
        _integrity("the complete exact payload count", "incomplete selected payload")


def payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Generator[pa.RecordBatch, None, None]:
    """Reject private membership before allocating any generic reader or iterator."""
    from marivo.analysis.materialization.retained import guard_receipt_transfer

    guard_receipt_transfer(receipt)
    return _guarded_payload_batches(
        project_root,
        receipt,
        policy=policy,
        bindings=bindings,
        preview=preview,
        row=row,
        rows=rows,
        audit=audit,
    )


def _guarded_payload_batches(
    project_root: Path,
    receipt: StorageReceipt,
    *,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
    preview: bool = False,
    row: DatasetRowContract | None = None,
    rows: DatasetRowSetContract | None = None,
    audit: bool = False,
) -> Generator[pa.RecordBatch, None, None]:
    """Keep native reader diagnostics and raw locators outside error chains."""
    failure: Literal["missing", "unauthorized", "mutated", "unknown"] = "unknown"
    try:
        yield from _payload_batches(
            project_root,
            receipt,
            policy=policy,
            bindings=bindings,
            preview=preview,
            row=row,
            rows=rows,
            audit=audit,
        )
        return
    except MaterializationError:
        raise
    except FileNotFoundError:
        failure = "missing"
    except PermissionError:
        failure = "unauthorized"
    except pa.ArrowException:
        failure = "mutated"
    except Exception:
        pass
    raise StorageAccessError(failure)


def part_schema(
    project_root: Path,
    part: RetainedPart,
    *,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
    bindings: tuple[S3Access, ...] = (),
) -> pa.Schema:
    """Read selected storage schema and bind it to its immutable receipt.

    Family consumers independently check the returned fields against their
    registered key/state contracts. Reading a header is not content validation;
    a consuming action must exhaust the subsequent guarded part read.
    """
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    stream = payload_batches(project_root, part.storage_receipt, policy=policy, bindings=bindings)
    try:
        batch = next(stream, None)
        if batch is None or batch.num_rows:
            _integrity("a bounded selected part schema header", "missing part schema header")
        schema = batch.schema
        if hashlib.sha256(schema.serialize().to_pybytes()).hexdigest() != (
            part.storage_receipt.schema_fingerprint
        ):
            _integrity("the receipt-bound exact part schema", "part schema fingerprint differs")
        return schema
    finally:
        stream.close()


def read_table(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
    preview: bool = False,
) -> pa.Table:
    if isinstance(receipt, LocalReceipt):
        return storage._read(
            project_root=project_root,
            receipt=receipt,
            row_contract=row_contract,
            row_set_contract=row_set_contract,
            policy=policy,
            preview=preview,
        )
    validator = storage._RowValidator(row_contract, row_set_contract, source_key_validation=True)
    retained: list[pa.RecordBatch] = []
    schema: pa.Schema | None = None
    for batch in payload_batches(
        project_root,
        receipt,
        policy=policy,
        bindings=bindings,
        preview=preview,
        row=row_contract,
        rows=row_set_contract,
    ):
        realized = storage._realized_schema(row_contract, batch.schema)
        if codec.schema_fingerprint(realized) != receipt.schema_fingerprint:
            _integrity("the exact selected realized schema", "receipt schema differs")
        if schema is not None and not schema.equals(batch.schema, check_metadata=False):
            _integrity("one complete selected schema", "changing payload schema")
        schema = batch.schema
        validator.accept(batch)
        retained.append(batch)
    if schema is None:
        _integrity("the selected payload schema", "missing payload schema")
    if not preview:
        validator.finish()
    return pa.Table.from_batches(retained, schema=schema)


def read_preview(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
) -> pa.Table:
    return read_table(
        project_root=project_root,
        receipt=receipt,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        policy=policy,
        bindings=bindings,
        preview=True,
    )


def read_primary(
    *,
    project_root: Path,
    receipt: StorageReceipt,
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...] = (),
) -> pd.DataFrame:
    if isinstance(receipt, LocalReceipt):
        return storage.read_primary(
            project_root=project_root,
            receipt=receipt,
            row_contract=row_contract,
            row_set_contract=row_set_contract,
            policy=policy,
        )
    if receipt.realized_row_count > policy.max_rows:
        _limited("committed row count exceeds the collection limit")
    selected = (
        ()
        if isinstance(receipt, EngineReceipt)
        else (_object_read_access(bindings, receipt.object_store_ref),)
    )
    request = codec.parse_json(
        storage._read_request(project_root, receipt, row_contract, row_set_contract, policy)
    )
    if not isinstance(request, dict):
        _integrity("one private read request", "invalid read request")
    request["schema"] = "marivo.external_primary_read/v1"
    request["access"] = [access_payload(value) for value in selected]
    return storage._supervise_read(
        codec.canonical_json(request), policy.deadline_seconds, worker_code=_WORKER_CODE
    )


def read_worker_entry() -> None:
    """Select the external reader without reversing the storage dependency."""
    storage._read_worker_entry(read_worker_value)


def read_worker_value(text: str) -> pd.DataFrame:
    from marivo.analysis.observation.contracts import make_ids

    obj = codec._obj(
        codec.parse_json(text), "schema project_root receipt row row_set policy access"
    )
    if obj["schema"] != "marivo.external_primary_read/v1":
        _integrity("the supported external read request", "unsupported external read version")
    settings = codec._obj(
        obj["policy"], "preview_rows max_rows max_decoded_bytes deadline_seconds max_batch_bytes"
    )
    seconds = settings["deadline_seconds"]
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        _limited("invalid external read deadline")
    policy = ReadPolicy(
        codec._int(settings["preview_rows"], minimum=1),
        codec._int(settings["max_rows"]),
        codec._int(settings["max_decoded_bytes"], minimum=1),
        float(seconds),
        codec._int(settings["max_batch_bytes"], minimum=1),
    )
    ids = make_ids(())
    row = codec.decode_row(obj["row"], ids)
    started = time.monotonic()
    table = read_table(
        project_root=Path(codec._text(obj["project_root"])),
        receipt=codec.decode_receipt(obj["receipt"]),
        row_contract=row,
        row_set_contract=codec.decode_row_set(obj["row_set"], ids),
        policy=policy,
        bindings=tuple(decode_access(value) for value in codec._array(obj["access"])),
    )
    # Guard the Python identity conversion and complete DataFrame as in local execution.
    if table.nbytes * 4 + table.num_rows * (256 + 64 * table.num_columns) > 268_435_456:
        _limited("complete conversion allocation exceeds the intermediate budget")
    result = storage._to_dataframe(table, row)
    if int(result.memory_usage(index=True, deep=True).sum()) > policy.max_decoded_bytes:
        _limited("complete DataFrame byte limit exceeded")
    if time.monotonic() - started > policy.deadline_seconds:
        _limited("complete external collection deadline exceeded")
    return result


def read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy = _DEFAULT_READ_POLICY,
    bindings: tuple[S3Access, ...] = (),
) -> Iterator[pa.RecordBatch]:
    from marivo.analysis.materialization.retained import guard_part_transfer

    guard_part_transfer(part)
    return _read_part_batches(
        project_root, part, expected_schema=expected_schema, policy=policy, bindings=bindings
    )


def _read_part_batches(
    project_root: Path,
    part: RetainedPart,
    *,
    expected_schema: pa.Schema,
    policy: ReadPolicy,
    bindings: tuple[S3Access, ...],
) -> Iterator[pa.RecordBatch]:
    receipt = part.storage_receipt
    if (
        receipt.schema_fingerprint
        != hashlib.sha256(expected_schema.serialize().to_pybytes()).hexdigest()
    ):
        _integrity("the exact registered part schema fingerprint", "required part schema differs")
    for batch in payload_batches(project_root, receipt, policy=policy, bindings=bindings):
        # Engine physical fields do not retain Arrow's nullability annotation.
        if not batch.schema.equals(expected_schema, check_metadata=False) and (
            not isinstance(receipt, EngineReceipt)
            or len(batch.schema) != len(expected_schema)
            or any(
                left.name != right.name or left.type != right.type
                for left, right in zip(batch.schema, expected_schema, strict=True)
            )
        ):
            _integrity("the exact registered part schema", "required part schema differs")
        for field in expected_schema:
            if not field.nullable and batch.column(field.name).null_count:
                _integrity("non-null required part fields", "null retained field")
        yield batch


def validate_sampling_state(
    project_root: Path,
    state: storage.SamplingStateRead | None,
    bindings: tuple[S3Access, ...] = (),
) -> None:
    if state is None:
        return
    if isinstance(state.receipt, LocalReceipt):
        storage.validate_sampling_state(project_root, state)
        return
    schema = pa.schema([pa.field("sampling_execution_digest", pa.string(), nullable=False)])
    part = RetainedPart("population_sampling_state", "population_sampling_state", 1, state.receipt)
    batches = tuple(
        read_part_batches(
            project_root,
            part,
            expected_schema=schema,
            policy=ReadPolicy(max_rows=1, max_decoded_bytes=65_536),
            bindings=bindings,
        )
    )
    table = pa.Table.from_batches(batches)
    if table.num_rows != 1 or table["sampling_execution_digest"][0].as_py() != codec.digest(
        codec.sampling_payload(state.sampling)
    ):
        _integrity("the exact retained sampling receipt binding", "sampling state differs")
