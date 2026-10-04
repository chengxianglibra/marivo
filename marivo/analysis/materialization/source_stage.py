"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import ExitStack
from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    RetainedPartSpec,
)
from marivo.analysis.compiler.placement import (
    SourceStep,
)
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.materialization import dataset_publication
from marivo.analysis.materialization.contracts import (
    ArtifactRecord,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.execution_state import ExecutionProgress
from marivo.analysis.materialization.source_preparation import prepared_source
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    IndependentPartWrite,
    PartWriteSpec,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.dataset_execution import ExecutionEvidence


def batches(
    self: DatasetRuntime,
    backend: ExecutionAdapter,
    expression: ir.Table,
    batch_rows: int,
    *,
    role: str = "primary",
) -> Iterator[pa.RecordBatch]:
    reader = backend.batches(expression, role=role, chunk_size=batch_rows)
    seen = False
    try:
        for batch in reader:
            self._event("transfer")
            seen = True
            self.statistics.transferred_rows += batch.num_rows
            self.statistics.transferred_bytes += batch.nbytes
            yield batch
        if not seen:
            yield pa.RecordBatch.from_arrays(
                [pa.array([], type=field.type) for field in reader.schema], schema=reader.schema
            )
    finally:
        failed = sys.exc_info()[0] is not None
        try:
            reader.close()
        except BaseException:
            if not failed:
                raise


def prepare_sources(
    self: DatasetRuntime,
    source_steps: tuple[SourceStep, ...],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
    evidence: ExecutionEvidence,
    source_contexts: ExitStack,
) -> dict[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]]:
    prepared: dict[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]] = {}
    for source_boundary in source_steps:
        boundary_validations: list[tuple[str, int]] = []
        prepared[source_boundary.output] = source_contexts.enter_context(
            prepared_source(self, run_ref, source_boundary, records, boundary_validations)
        )
        evidence.validations.extend(
            (
                (
                    f"source.{source_boundary.output}.{name}" if len(source_steps) > 1 else name,
                    value,
                )
                for name, value in boundary_validations
            )
        )
    return prepared


def execute_source_only(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    source_step: SourceStep | None,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    run_ref: str,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    if source_step is None:
        raise _error("execution_boundary", run_ref)
    current_backend, recipe, _tables = prepared[source_step.output]
    from marivo.analysis.compiler.nodes import RetainedRelationSpec
    from marivo.analysis.materialization.retained import (
        validate_source_private_relation,
    )

    for part in recipe.retained_parts:
        if isinstance(part, RetainedRelationSpec):
            validate_source_private_relation(
                current_backend,
                part.expression,
                recipe.expression.select(recipe.primary_columns),
                dataset.row_contract,
                part.role,
            )
    independent_parts = tuple(
        IndependentPartWrite(
            part.role,
            batches(
                self,
                current_backend,
                part.expression,
                1024,
                role="part." + part.role,
            ),
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedRelationSpec)
    )
    incoming = batches(
        self,
        current_backend,
        recipe.expression,
        1024,
    )
    output_parts = tuple(
        PartWriteSpec(
            part.role,
            part.contract_id,
            part.contract_version,
            part.column_names,
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedPartSpec)
    )
    progress.phase = "storage_staging"
    artifact_ref, storage = dataset_publication.write_output(
        self,
        dataset,
        incoming,
        run_ref,
        parts=output_parts,
        independent_parts=independent_parts,
        source_key_validation=True,
    )
    return artifact_ref, storage
