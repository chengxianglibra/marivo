"""Frozen-schema pandas encoding and receipt validation for terminal outputs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from marivo.analysis.core.artifact_data import DataSchema, ParquetHandle
from marivo.analysis.core.data_validation import normalize_complete_frame, stage_arrow_type
from marivo.analysis.materialization.contracts import FileEntry, LocalReceipt
from marivo.analysis.materialization.storage import (
    _checked_path,
    _fsync_directory,
    _manifest_bytes,
    payload_digest,
)
from marivo.analysis.materialization.terminal_protocol import invalid


@dataclass(frozen=True, slots=True)
class TerminalStoragePolicy:
    parquet_version: str = "2.6"
    compression: str = "zstd"
    compression_level: int = 3
    row_group_rows: int = 128 * 1024
    batch_rows: int = 64 * 1024


POLICY = TerminalStoragePolicy()


def arrow_schema(schema: DataSchema) -> pa.Schema:
    if not schema.columns:
        return pa.schema([pa.field("__unit", pa.uint8(), nullable=False)])
    return pa.schema(
        [
            pa.field(column.slot_id, stage_arrow_type(column.dtype), column.nullable)
            for column in schema.columns
        ]
    )


def physical_schema(schema: DataSchema) -> pa.Schema:
    # Parquet has no timestamp seconds. Persist exact int64 seconds instead of
    # narrowing the admitted range through an overflowing millisecond cast.
    return pa.schema(
        [
            pa.field(
                field.name,
                pa.int64()
                if pa.types.is_timestamp(field.type) and field.type.unit == "s"
                else field.type,
                field.nullable,
            )
            for field in arrow_schema(schema)
        ]
    )


def complete_output(schema: DataSchema, rows: pd.DataFrame) -> pd.DataFrame:
    if not schema.key_fields and len(rows) > 1:
        raise invalid("zero-axis terminal output must contain zero or one row")
    if not schema.columns:
        if len(rows.columns):
            raise invalid("zero-column output has undeclared physical columns")
        return pd.DataFrame(index=pd.RangeIndex(len(rows)))
    return normalize_complete_frame(rows, schema.columns, schema.key_fields)


def write_output(
    root: Path, staging: Path, final: Path, schema: DataSchema, rows: pd.DataFrame
) -> ParquetHandle:
    """Encode final pandas output, without requiring an upstream Arrow handle."""
    frame = complete_output(schema, rows)
    _checked_path(root, staging)
    _checked_path(root, final)
    staging.mkdir(parents=True, exist_ok=False)
    data = staging / "data.parquet"
    target = physical_schema(schema)
    with pq.ParquetWriter(
        data,
        target,
        version=POLICY.parquet_version,
        compression=POLICY.compression,
        compression_level=POLICY.compression_level,
        use_dictionary=True,
        write_statistics=True,
        allow_truncated_timestamps=False,
        use_compliant_nested_type=False,
    ) as writer:
        for start in range(0, max(1, len(frame)), POLICY.row_group_rows):
            chunk = frame.iloc[start : start + POLICY.row_group_rows]
            table = (
                pa.Table.from_pandas(
                    chunk, schema=arrow_schema(schema), preserve_index=False, safe=True
                )
                .replace_schema_metadata(None)
                .cast(target, safe=True)
                if schema.columns
                else pa.Table.from_arrays(
                    [pa.array([1] * len(chunk), type=pa.uint8())], schema=target
                )
            )
            writer.write_table(table, row_group_size=POLICY.row_group_rows)
    with data.open("rb") as stream:
        os.fsync(stream.fileno())
    entry = FileEntry("data.parquet", data.stat().st_size)
    manifest = _manifest_bytes((entry,))
    with (staging / "manifest.json").open("xb") as stream:
        stream.write(manifest)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(staging)
    receipt = LocalReceipt(
        final.relative_to(root).as_posix(), (entry,), len(frame), entry.size_bytes + len(manifest)
    )
    handle = ParquetHandle(receipt, payload_digest(root, staging, receipt))
    # Verify the produced physical layout before the publication transaction.
    with pq.ParquetFile(data) as parquet:
        if not parquet.schema_arrow.equals(
            target, check_metadata=True
        ) or parquet.metadata.num_rows != len(frame):
            raise invalid("encoded terminal output differs from its frozen schema")
    return handle


def read_output(root: Path, schema: DataSchema, handle: ParquetHandle) -> pd.DataFrame:
    """Read the complete physical output through the shared terminal data reader."""
    from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead, ReadLayout

    return OutputRead(root).read(ReadLayout(schema, handle), ExportRead())
