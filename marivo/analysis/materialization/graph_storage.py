"""Receipt-checked v8 Arrow/Parquet storage, without source or DuckDB reads."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.core.cell_encoding import EncodedCell, KnownCell, ValidityCell
from marivo.analysis.materialization.cell_arrow import (
    binding,
    schema_binding,
    storage_schema,
)
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
)
from marivo.analysis.materialization.graph_protocol import (
    Descriptor,
    ValidatedDescriptor,
    checked_metadata,
    invalid,
    schema_from,
)
from marivo.analysis.materialization.storage import (
    _checked_path,
    _fsync_directory,
    _manifest_bytes,
    _open_payload,
)
from marivo.analysis.methods.semantics import MethodKey


def payload_digest(root: Path, directory: Path, receipt: LocalReceipt) -> str:
    """Bind an immutable part receipt to all its exact persisted file bytes."""

    digest = hashlib.sha256()
    for entry in receipt.file_manifest:
        path = _checked_path(root, directory / entry.relative_path)
        digest.update(entry.relative_path.encode())
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def write_table(root: Path, staging: Path, final: Path, table: pa.Table) -> LocalReceipt:
    created = False
    try:
        _checked_path(root, staging)
        _checked_path(root, final)
        staging.mkdir(parents=True, exist_ok=False)
        created = True
        data = staging / "data.parquet"
        # Preserve nested Arrow field names covered by the exact schema receipt.
        pq.write_table(
            table.replace_schema_metadata(storage_schema(table.schema).metadata),
            data,
            write_page_checksum=False,
            use_compliant_nested_type=False,
        )
        with data.open("rb") as stream:
            os.fsync(stream.fileno())
        entry = FileEntry("data.parquet", data.stat().st_size)
        manifest = _manifest_bytes((entry,))
        with (staging / "manifest.json").open("xb") as stream:
            stream.write(manifest)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(staging)
        return LocalReceipt(
            final.relative_to(root).as_posix(),
            (entry,),
            table.num_rows,
            entry.size_bytes + len(manifest),
        )
    except (OSError, pa.ArrowException) as error:
        if created:
            shutil.rmtree(staging, ignore_errors=True)
        raise MaterializationError(
            expected="complete durable Parquet in the Run-owned output directory",
            received=f"local Parquet write failed: {type(error).__name__}",
            repair="Inspect local write permissions and available space, then reconcile the original Run before a new invocation.",
            stage="storage_staging",
        ) from None
    except BaseException:
        if created:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def read_table(root: Path, receipt: LocalReceipt) -> pa.Table:

    parquet, _path = _open_payload(root, receipt)
    try:
        schema = parquet.schema_arrow
        batches = []
        for batch in parquet.iter_batches(batch_size=1024):
            batches.append(batch)
        table = pa.Table.from_batches(batches, schema=schema)
        return table
    except (OSError, pa.ArrowException):
        raise invalid("committed Parquet could not be read completely") from None
    finally:
        read_failed = sys.exc_info()[0] is not None
        try:
            parquet.close()
        except (OSError, pa.ArrowException):
            if not read_failed:
                raise invalid("committed Parquet reader did not close successfully") from None


def _read_pandas_payload(
    root: Path,
    receipt: LocalReceipt,
    *,
    expected_schema: pa.Schema | None = None,
) -> tuple[pd.DataFrame, pa.Schema]:
    parquet, path = _open_payload(root, receipt)
    try:
        schema = parquet.schema_arrow
        if expected_schema is not None and not schema.equals(
            storage_schema(expected_schema), check_metadata=True
        ):
            raise invalid("primary physical schema differs from its frozen descriptor")
        if (
            parquet.metadata.num_rows != receipt.realized_row_count
            or len(receipt.file_manifest) != 1
            or path.stat().st_size != receipt.file_manifest[0].size_bytes
        ):
            raise invalid("committed Parquet payload differs from its receipt")
        frame = pd.read_parquet(path, dtype_backend="pyarrow")
        if tuple(frame.columns) != tuple(schema.names) or len(frame) != receipt.realized_row_count:
            raise invalid("committed pandas result differs from its frozen Parquet payload")
        return frame, schema
    except (OSError, pa.ArrowException, ValueError) as error:
        if isinstance(error, MaterializationError):
            raise
        raise invalid("committed Parquet could not be read completely") from error
    finally:
        read_failed = sys.exc_info()[0] is not None
        try:
            parquet.close()
        except (OSError, pa.ArrowException):
            if not read_failed:
                raise invalid("committed Parquet reader did not close successfully") from None


def _validate_pandas_cells(frame: pd.DataFrame, schema: pa.Schema) -> None:
    for cell in binding(schema, exact=True).cells:
        values = frame[cell.fields.value]
        missing = values.isna()
        if isinstance(cell, KnownCell):
            if len(frame) and (missing.any() if cell.code == 0 else not missing.all()):
                raise invalid("Cell state disagrees with value validity")
            continue
        if isinstance(cell, ValidityCell):
            continue
        states = frame[cell.state]
        if states.isna().any():
            raise invalid("physical Cell state cannot be null")
        admitted = (
            *((-1,) if isinstance(cell, EncodedCell) and cell.optional else ()),
            0,
            *cell.book.codes(),
        )
        for raw_state, absent in zip(states, missing, strict=True):
            if isinstance(raw_state, bool):
                raise invalid("physical Cell state is not an integer code")
            try:
                code = int(raw_state)
            except (OverflowError, TypeError, ValueError) as error:
                raise invalid("physical Cell state is not an integer code") from error
            if code != raw_state or code not in admitted:
                raise invalid("undeclared physical Cell code")
            if (code == 0) == bool(absent):
                raise invalid("Cell state disagrees with value validity")


def _pandas_cell_tags(frame: pd.DataFrame, schema: pa.Schema, name: str) -> list[str | None]:
    cell = next(
        (item for item in binding(schema, exact=True).cells if item.fields.tag == name), None
    )
    if cell is None:
        values = frame[name].tolist()
        return [None if pd.isna(value) else str(value) for value in values]
    if isinstance(cell, KnownCell):
        tag, _reason = cell.book.decode(cell.code)
        return [tag] * len(frame)
    if isinstance(cell, ValidityCell):
        defined = cell.book.decode(0)[0]
        missing = cell.book.decode(cell.missing_code)[0]
        return [defined if not absent else missing for absent in frame[cell.fields.value].isna()]
    result: list[str | None] = []
    for raw_state in frame[cell.state]:
        code = int(raw_state)
        result.append(None if code == -1 else cell.book.decode(code)[0])
    return result


def read_result(
    root: Path, descriptor: Descriptor, *, _validated: ValidatedDescriptor | None = None
) -> ExchangeResult:
    checked = checked_metadata(descriptor, _validated)
    schema = schema_from(descriptor.realized_schema)
    primary, _ = _read_pandas_payload(
        root, descriptor.primary_receipt.local, expected_schema=schema
    )
    _validate_pandas_cells(primary, schema)
    for receipt in descriptor.parts:
        try:
            actual = payload_digest(root, root / receipt.local.project_relative_path, receipt.local)
        except OSError as error:
            raise invalid("committed part payload cannot be verified") from error
        if actual != receipt.payload_digest:
            raise invalid("committed part payload differs from its immutable receipt digest")
    parts: list[ExchangePart] = []
    part_schemas: list[pa.Schema] = []
    for receipt in descriptor.parts:
        frame, storage_part_schema = _read_pandas_payload(root, receipt.local)
        if binding(storage_part_schema).cells:
            raise invalid("part payload carries a competing Cell binding")
        part_schema = schema_binding(
            storage_part_schema, receipt.cell_table.cells, receipt.cell_table.logical_columns
        )
        if binding(part_schema, exact=True) != receipt.cell_table:
            raise invalid("part Cell binding differs from its frozen receipt")
        _validate_pandas_cells(frame, part_schema)
        parts.append(ExchangePart(receipt.role, frame))
        part_schemas.append(part_schema)
    method = descriptor.method_bindings[-1].method
    keys = descriptor.row_contract.key_fields
    state = descriptor.method_state
    statuses: pd.DataFrame | None = None
    state_schema: pa.Schema | None = None
    if state.kind != "none":
        status_column = "status" if state.kind == "spearman" else "cell_tag"
        status_values = (
            ["accepted"] * len(primary)
            if state.kind in ("cohort", "table")
            else primary[status_column].tolist()
            if status_column == "status"
            else _pandas_cell_tags(primary, schema, status_column)
        )
        state_columns: dict[str, object] = {key: primary[key].tolist() for key in keys}
        state_columns["status"] = status_values
        state_fields = [*(schema.field(key) for key in keys), pa.field("status", pa.string())]
        if state.kind in ("share", "penetration", "standardized"):
            from marivo.analysis.materialization.graph_exchange import reference_parameters
            from marivo.analysis.materialization.graph_reference import _pandas_error_bounds

            state_columns["error_bound"] = _pandas_error_bounds(
                reference_parameters(checked.descriptor.signature),
                tuple(parts),
                primary,
                {
                    part.role: part_schema
                    for part, part_schema in zip(parts, part_schemas, strict=True)
                },
                schema,
            )
            state_fields.append(pa.field("error_bound", pa.float64()))
        state_schema = pa.schema(state_fields)
        statuses = pd.DataFrame(state_columns)
    contract = ExchangeContract(
        checked.descriptor.signature,
        MethodKey(method.name, method.version),
        state.input_binding,
        schema_from(descriptor.realized_schema),
        keys,
        tuple(
            PartContract(
                p.role,
                part_schema,
                tuple(name for name, _ in descriptor.parts[index].key_fields),
            )
            for index, (p, part_schema) in enumerate(zip(parts, part_schemas, strict=True))
        ),
        descriptor.row_contract.cell_reasons,
        state.kind,
        state_schema,
        (),
        descriptor.row_set_contract.kind == "optional_singleton",
        descriptor.row_contract.column_reasons,
        _frozen=checked,
    )
    return ExchangeResult(contract, primary, tuple(parts), (), statuses)
