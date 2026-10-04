"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Generator, Iterable
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo.analysis.compiler.normalize import (
    artifact_inputs,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.evidence._dataset_types import (
    Finding,
)
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    LocalReceipt,
    RunFailure,
    RunRecord,
    StorageReceipt,
    run_failure_phase,
)
from marivo.analysis.materialization.errors import (
    MaterializationError,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.execution_state import ExecutionProgress, StageResult
from marivo.analysis.materialization.publication import make_descriptor
from marivo.analysis.materialization.resources import (
    discharge_resources,
    reserve_output,
)
from marivo.analysis.materialization.storage import (
    DatasetWriteResult,
    IndependentPartWrite,
    PartWriteSpec,
    ReadPolicy,
    StoragePolicy,
    write_local_dataset,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.dataset_execution import ExecutionEvidence, ExecutionPlan

_LOCAL_STORAGE_POLICY = StoragePolicy()


def write_output(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    batches: Iterable[pa.RecordBatch],
    run_ref: str,
    *,
    parts: tuple[PartWriteSpec, ...] = (),
    independent_parts: tuple[IndependentPartWrite, ...] = (),
    source_key_validation: bool,
) -> tuple[str, DatasetWriteResult[StorageReceipt]]:
    nonce = uuid4().hex
    artifact_ref = "artifact_" + nonce
    staging, final, _ = reserve_output(
        self.store,
        run_ref=run_ref,
        session_ref=self.session_ref,
        artifact_ref=artifact_ref,
        nonce=nonce,
    )
    self._event("output_reserved")
    storage = write_local_dataset(
        project_root=self.store.project_root,
        staging_path=staging,
        final_path=final,
        batches=batches,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        parts=parts,
        independent_parts=independent_parts,
        source_key_validation=source_key_validation,
        policy=_LOCAL_STORAGE_POLICY,
        event=self._event,
    )
    return artifact_ref, storage


def resolve_outcome(
    self: DatasetRuntime,
    run: RunRecord,
    error: MaterializationError,
    phase: str,
) -> MaterializedDataset | None:
    self._event("readback")
    current = self.store.run(run.run_ref)
    if current is None:
        raise _error("publication", run.run_ref)
    if current.lifecycle == "succeeded":
        if current.output_artifact_ref is None:
            raise _error("publication", run.run_ref)
        return self.artifact(current.output_artifact_ref)
    if current.lifecycle == "failed":
        return None
    resources = tuple(
        item for item in self.store.resources(self.session_ref) if item.run_ref == run.run_ref
    )
    resolved = discharge_resources(self.store, resources)
    self.store.fail(
        run.run_ref,
        RunFailure(
            phase=run_failure_phase(phase, phase),
            kind="execution_failed",
            safe_message="The Dataset action failed before publication.",
            safe_location=f"dataset.{phase}",
            expected=error.expected or "a complete registered execution",
            received=error.received or "the action failed",
            repair=error.repair,
        ),
        resolved_resources=resolved,
    )
    return None


def prepare_publication(
    self: DatasetRuntime,
    plan: ExecutionPlan,
    stage: StageResult,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
    *,
    policy: ReadPolicy,
) -> tuple[ArtifactDescriptor, tuple[Finding, ...]]:
    _validate_consumed_receipts(self, plan)
    descriptor = _base_descriptor(self, plan, stage, evidence, progress)
    return descriptor, ()


def _audit_batches(
    self: DatasetRuntime, descriptor: ArtifactDescriptor, policy: ReadPolicy
) -> Generator[pa.RecordBatch, None, None]:
    from marivo.analysis.materialization.reads import payload_batches

    return payload_batches(
        self.store.project_root,
        descriptor.storage_receipt,
        policy=policy,
        row=descriptor.row_contract,
        rows=descriptor.row_set_contract,
        audit=True,
    )


def _validate_consumed_receipts(self: DatasetRuntime, plan: ExecutionPlan) -> None:
    from marivo.analysis.materialization.parquet_scan import checked_local_path
    from marivo.analysis.materialization.retained import selected_parts

    for reference, input_record in plan.records.items():
        consumed_receipts = [input_record.descriptor.storage_receipt]
        if any(
            value.state.artifact_ref.ref == reference
            for boundary in plan.source_steps
            for value in artifact_inputs(boundary.dataset)
        ):
            consumed_receipts.extend(
                part.storage_receipt
                for part in selected_parts(
                    input_record.descriptor,
                    plan.dataset,
                    input_dataset=next(
                        value
                        for value in plan.retained_inputs
                        if value.state.artifact_ref.ref == reference
                    ),
                )
            )
        for receipt in consumed_receipts:
            if isinstance(receipt, LocalReceipt):
                checked_local_path(self.store.project_root, receipt)


def _base_descriptor(
    self: DatasetRuntime,
    plan: ExecutionPlan,
    stage: StageResult,
    evidence: ExecutionEvidence,
    progress: ExecutionProgress,
) -> ArtifactDescriptor:
    progress.phase = "quality"
    self._event("quality")
    descriptor = make_descriptor(
        plan.dataset,
        plan.contract,
        stage.storage,
        tuple(evidence.validations),
        inherited=plan.inherited,
        input_descriptors=tuple(
            plan.records[value.state.artifact_ref.ref].descriptor for value in plan.retained_inputs
        ),
    )
    temporal = {
        item.model_dump_json(): item
        for record in plan.records.values()
        for item in record.descriptor.temporal_execution
    }
    for recipe in stage.recipes:
        if recipe.temporal_execution is not None:
            temporal[recipe.temporal_execution.model_dump_json()] = recipe.temporal_execution
    for recipe in stage.recipes:
        selections = dict(recipe.version_selections)
        population = descriptor.population_authority
        if population.definition_fingerprint in selections:
            descriptor = replace(
                descriptor,
                population_authority=replace(
                    population,
                    version_selection=selections[population.definition_fingerprint],
                ),
            )
    self._event("temporal_authority")
    return replace(descriptor, temporal_execution=tuple(temporal[key] for key in sorted(temporal)))
