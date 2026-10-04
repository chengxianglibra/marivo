"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

import ibis.expr.types as ir
import pyarrow as pa

from marivo.analysis.compiler.nodes import (
    CompiledDataset,
    RetainedPartSpec,
)
from marivo.analysis.compiler.placement import (
    ArtifactReadStep,
    PhysicalStageGraph,
    SourceStep,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.materialization import dataset_publication, source_stage
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    StorageReceipt,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.execution_state import ExecutionProgress
from marivo.analysis.materialization.local_execution import (
    ArtifactInput,
    LocalBoundary,
    LocalGraphRequest,
    LocalInputStreams,
    LocalPartInput,
    LocalResult,
    LocalStage,
    StreamInput,
    execute_local,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    PartWriteSpec,
)
from marivo.analysis.observation.contracts import (
    MetricPayload,
    RetainedRowsPayload,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.operators.row import RowCall

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.dataset_execution import ExecutionEvidence


def _local_output_batches(table: pa.Table) -> list[pa.RecordBatch]:
    """Keep the exact schema when a valid local selection produces zero rows."""
    return table.to_batches(max_chunksize=1024) or [
        pa.RecordBatch.from_arrays(
            [pa.array([], type=field.type) for field in table.schema], schema=table.schema
        )
    ]


def local_input_parts(
    self: DatasetRuntime,
    descriptor: ArtifactDescriptor,
    dataset: LogicalDataset,
    *,
    input_dataset: MaterializedDataset | None = None,
) -> tuple[tuple[LocalPartInput, ...], tuple[Iterable[pa.RecordBatch], ...]]:
    from marivo.analysis.materialization.reads import part_schema
    from marivo.analysis.materialization.retained import component_schema, selected_parts

    selected = selected_parts(descriptor, dataset, input_dataset=input_dataset)
    inputs: list[LocalPartInput] = []
    for part in selected:
        schema = part_schema(self.store.project_root, part)
        keys = component_schema(descriptor.row_contract, part.role, schema)
        inputs.append(
            LocalPartInput(
                part.role,
                part.contract_id,
                part.contract_version,
                schema,
                keys,
                part.storage_receipt,
            )
        )
    return tuple(inputs), ()


def local_output_parts(result: LocalResult) -> tuple[PartWriteSpec, ...]:
    return tuple(
        PartWriteSpec(
            part.role, part.contract_id, part.contract_version, tuple(part.table.column_names)
        )
        for part in result.parts
        if part.contract_id
        in (
            "metric.sufficient_components",
            "delta.sufficient_components",
            "event_funnel.additive_components",
        )
    )


def require_projected_parts(recipe: CompiledDataset) -> None:
    from marivo.analysis.materialization.retained import reject_source_private_transfer

    if any(not isinstance(part, RetainedPartSpec) for part in recipe.retained_parts):
        reject_source_private_transfer()


def source_local_parts(
    dataset: LogicalDataset, recipe: CompiledDataset
) -> tuple[LocalPartInput, ...]:
    from marivo.analysis.materialization.retained import component_schema

    require_projected_parts(recipe)
    schema = recipe.expression.schema().to_pyarrow()
    return tuple(
        LocalPartInput(
            part.role,
            part.contract_id,
            part.contract_version,
            pa.schema([schema.field(name) for name in part.column_names]),
            component_schema(
                dataset.row_contract,
                part.role,
                pa.schema([schema.field(name) for name in part.column_names]),
            ),
        )
        for part in recipe.retained_parts
        if isinstance(part, RetainedPartSpec)
    )


def run_local_graph(
    self: DatasetRuntime,
    physical: PhysicalStageGraph,
    boundaries: tuple[LocalBoundary, ...],
    streams: tuple[LocalInputStreams, ...],
    run_ref: str,
    *,
    cancel_source: Callable[[], None],
) -> LocalResult:

    stages: list[LocalStage] = []
    for step in physical.local_steps:
        root = step.dataset._root
        if not isinstance(root, LogicalRootHandle):
            raise _error("implementation_registration", run_ref)
        payload = root.payload
        call: RowCall
        if isinstance(payload, (MetricPayload, RetainedRowsPayload, RetainedFoldPayload)):
            if len(step.inputs) != 1:
                raise _error("implementation_registration", run_ref)
            source = step.dataset._inputs[0]
            call = RowCall(
                step.implementation.local_method or "",
                source.row_contract,
                source.row_set_contract,
                step.dataset.row_contract,
                step.dataset.row_set_contract,
                payload.predicate if not isinstance(payload, RetainedFoldPayload) else None,
                payload.rank if not isinstance(payload, RetainedFoldPayload) else None,
                payload.limit_count if not isinstance(payload, RetainedFoldPayload) else None,
                payload.spec if isinstance(payload, RetainedFoldPayload) else None,
            )
        else:
            raise _error("implementation_registration", run_ref)
        stages.append(LocalStage(step.output, step.inputs, call))
    request = LocalGraphRequest(boundaries, tuple(stages), physical.primary_output)
    self._event("local_execution_started")
    result = execute_local(request, streams)
    self.statistics.local_handoffs = result.handoffs
    self._event("local_execution_completed")
    return result


def _source_count(backend: ExecutionAdapter, recipe: CompiledDataset, *, role: str) -> object:
    count_expression = recipe.expression.aggregate(__mv_rows=recipe.expression.count())
    return backend.read_scalar(backend.prepare(count_expression, role=role))


def _projected_source_input(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    step: SourceStep,
    backend: ExecutionAdapter,
    recipe: CompiledDataset,
) -> tuple[LocalBoundary, LocalInputStreams]:
    from marivo.analysis.materialization.retained import required_part_roles

    needed_roles = required_part_roles(dataset, input_dataset=step.dataset)
    selected_specs = tuple(part for part in recipe.retained_parts if part.role in needed_roles)
    require_projected_parts(replace(recipe, retained_parts=selected_specs))
    names = tuple(
        dict.fromkeys(
            (
                *recipe.primary_columns,
                *(
                    name
                    for part in selected_specs
                    if isinstance(part, RetainedPartSpec)
                    for name in part.column_names
                ),
            )
        )
    )
    projected = replace(
        recipe,
        expression=recipe.expression.select(*names),
        retained_parts=selected_specs,
    )
    local_parts = source_local_parts(step.dataset, projected)
    return (
        LocalBoundary(
            step.output,
            StreamInput(
                step.dataset.row_contract,
                step.dataset.row_set_contract,
                wide_parts=bool(local_parts),
            ),
            local_parts,
        ),
        LocalInputStreams(source_stage.batches(self, backend, projected.expression, 1024)),
    )


def _retained_input(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    step: ArtifactReadStep,
    records: Mapping[str, ArtifactRecord],
) -> tuple[LocalBoundary, LocalInputStreams]:
    descriptor = records[step.dataset.state.artifact_ref.ref].descriptor
    local_parts, part_batches = local_input_parts(
        self, descriptor, dataset, input_dataset=step.dataset
    )
    selected = ArtifactInput(
        self.store.project_root,
        descriptor.storage_receipt,
        step.dataset.row_contract,
        step.dataset.row_set_contract,
    )
    return LocalBoundary(step.output, selected, local_parts), LocalInputStreams((), part_batches)


def _local_inputs(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    physical: PhysicalStageGraph,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
) -> tuple[tuple[LocalBoundary, ...], tuple[LocalInputStreams, ...]]:
    boundaries: list[LocalBoundary] = []
    streams: list[LocalInputStreams] = []
    for step in physical.steps:
        if isinstance(step, SourceStep):
            backend, recipe, _ = prepared[step.output]
            boundary, stream = _projected_source_input(self, dataset, step, backend, recipe)
        elif isinstance(step, ArtifactReadStep):
            boundary, stream = _retained_input(self, dataset, step, records)
        else:
            continue
        boundaries.append(boundary)
        streams.append(stream)
    return tuple(boundaries), tuple(streams)


def execute_local_stages(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    physical: PhysicalStageGraph,
    prepared: Mapping[int, tuple[ExecutionAdapter, CompiledDataset, dict[str, ir.Table]]],
    records: Mapping[str, ArtifactRecord],
    run_ref: str,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    boundaries, streams = _local_inputs(self, dataset, physical, prepared, records, run_ref)
    progress.phase = "stage_execution"

    def cancel_sources() -> None:
        for backend, _, _ in prepared.values():
            backend.interrupt()

    local_result = run_local_graph(
        self, physical, boundaries, streams, run_ref, cancel_source=cancel_sources
    )
    evidence.validations = [
        (
            "source_prefix.final_row_key_unique"
            if name == "dataset.final_row_key_unique"
            else name,
            value,
        )
        for name, value in evidence.validations
    ]
    evidence.validations.append(("dataset.final_row_key_unique", 0))
    progress.phase = "storage_staging"
    return dataset_publication.write_output(
        self,
        dataset,
        _local_output_batches(local_result.table),
        run_ref,
        parts=local_output_parts(local_result),
        source_key_validation=True,
    )
