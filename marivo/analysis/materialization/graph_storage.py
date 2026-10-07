"""Receipt-checked v8 Arrow/Parquet storage, without source or DuckDB reads."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

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


def write_table(root: Path, staging: Path, final: Path, table: pa.Table) -> LocalReceipt:
    try:
        _checked_path(root, staging)
        _checked_path(root, final)
        staging.mkdir(parents=True, exist_ok=False)
        data = staging / "data.parquet"
        # Preserve nested Arrow field names covered by the exact schema receipt.
        pq.write_table(table, data, write_page_checksum=False, use_compliant_nested_type=False)
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
        raise MaterializationError(
            expected="complete durable Parquet in the Run-owned output directory",
            received=f"local Parquet write failed: {type(error).__name__}",
            repair="Inspect local write permissions and available space, then reconcile the original Run before a new invocation.",
            stage="storage_staging",
        ) from None


def read_table(root: Path, receipt: LocalReceipt) -> pa.Table:
    from marivo.analysis.materialization.execute_deadline import check

    check()
    parquet, _path = _open_payload(root, receipt)
    try:
        schema = parquet.schema_arrow
        batches = []
        for batch in parquet.iter_batches(batch_size=1024):
            check()
            batches.append(batch)
        table = pa.Table.from_batches(batches, schema=schema)
        check()
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


def read_result(
    root: Path, descriptor: Descriptor, *, _validated: ValidatedDescriptor | None = None
) -> ExchangeResult:
    checked = checked_metadata(descriptor, _validated)
    primary = read_table(root, descriptor.primary_receipt.local)
    parts = tuple(ExchangePart(p.role, read_table(root, p.local)) for p in descriptor.parts)
    method = descriptor.method_bindings[-1].method
    keys = descriptor.row_contract.key_fields
    state = descriptor.method_state
    statuses = None
    if state.kind != "none":
        column = "status" if state.kind == "spearman" else "cell_tag"
        statuses = pa.Table.from_arrays(
            [
                *(primary.column(key) for key in keys),
                primary.column("classification")
                if state.kind == "canonical_history"
                else pa.array(["accepted"] * len(primary), type=pa.string())
                if descriptor.method_state.kind
                in (
                    "cohort",
                    "table",
                    "occurrence_inputs",
                    "canonical_history",
                    "journey_assignment",
                    "history_view",
                    "entry_axes",
                    "funnel_components",
                    "funnel_comparison",
                    "funnel_allocation",
                )
                else primary.column(column),
            ],
            names=[*keys, "status"],
        )
        if state.kind in ("share", "penetration", "standardized"):
            from marivo.analysis.materialization.graph_exchange import reference_parameters
            from marivo.analysis.materialization.graph_reference import error_bounds

            statuses = statuses.append_column(
                "error_bound",
                pa.array(
                    error_bounds(
                        reference_parameters(checked.descriptor.signature), parts, primary
                    ),
                    type=pa.float64(),
                ),
            )
    contract = ExchangeContract(
        checked.descriptor.signature,
        MethodKey(method.name, method.version),
        state.input_binding,
        schema_from(descriptor.realized_schema),
        keys,
        tuple(
            PartContract(
                p.role,
                p.table.schema,
                tuple(name for name, _ in descriptor.parts[index].key_fields),
            )
            for index, p in enumerate(parts)
        ),
        descriptor.row_contract.cell_reasons,
        state.kind,
        None if statuses is None else statuses.schema,
        (),
        descriptor.row_set_contract.kind == "optional_singleton",
        descriptor.row_contract.column_reasons,
        _frozen=checked,
    )
    return ExchangeResult(contract, primary, parts, (), statuses)
