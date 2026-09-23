"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pyarrow as pa

from marivo.analysis.compiler.normalize import (
    artifact_inputs,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventFunnelSemantics,
    EventTimeToEventSemantics,
)
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
from marivo.analysis.materialization.lifecycle_reducer_codec import (
    LifecycleReducerEvidence,
    LifecycleSelectionEvidence,
)
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
from marivo.analysis.operators.candidate_contracts import (
    EntityCandidateEvaluationSummary,
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
    dataset = plan.dataset
    roots = plan.roots
    records = plan.records
    retained_inputs = plan.retained_inputs
    source_steps = plan.source_steps
    physical = plan.physical
    inherited = plan.inherited
    contract = plan.contract
    run = plan.run
    artifact_ref = stage.artifact_ref
    storage = stage.storage
    from marivo.analysis.materialization.parquet_scan import checked_local_path
    from marivo.analysis.materialization.retained import selected_parts

    for reference, input_record in records.items():
        consumed_receipts = [input_record.descriptor.storage_receipt]
        if any(
            value.state.artifact_ref.ref == reference
            for boundary in source_steps
            for value in artifact_inputs(boundary.dataset)
        ):
            consumed_receipts.extend(
                part.storage_receipt
                for part in selected_parts(
                    input_record.descriptor,
                    dataset,
                    input_dataset=next(
                        value
                        for value in retained_inputs
                        if value.state.artifact_ref.ref == reference
                    ),
                )
            )
        for receipt in consumed_receipts:
            if isinstance(receipt, LocalReceipt):
                checked_local_path(self.store.project_root, receipt)
    progress.phase = "quality"
    self._event("quality")
    descriptor = make_descriptor(
        dataset,
        contract,
        storage,
        tuple(evidence.validations),
        inherited=inherited,
        input_descriptors=tuple(
            records[value.state.artifact_ref.ref].descriptor for value in retained_inputs
        ),
    )
    temporal = {
        item.model_dump_json(): item
        for record in records.values()
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
    descriptor = replace(
        descriptor, temporal_execution=tuple(temporal[key] for key in sorted(temporal))
    )
    findings: tuple[Finding, ...] = ()
    if dataset.kind == "lifecycle" or (
        dataset.kind == "population"
        and isinstance(evidence.lifecycle_summary, LifecycleSelectionEvidence)
    ):
        if (
            isinstance(evidence.lifecycle_summary, LifecycleReducerEvidence)
            and physical.local_steps
        ):
            from marivo.analysis.materialization.lifecycle_reducer_publication import (
                summary_from_batches as lifecycle_batch_summary,
            )
            from marivo.analysis.materialization.reads import payload_batches

            evidence.lifecycle_summary = lifecycle_batch_summary(
                payload_batches(
                    self.store.project_root,
                    descriptor.storage_receipt,
                    policy=policy,
                    row=descriptor.row_contract,
                    rows=descriptor.row_set_contract,
                    audit=True,
                ),
                evidence.lifecycle_summary,
            )
        if isinstance(evidence.lifecycle_summary, LifecycleSelectionEvidence):
            evidence.lifecycle_summary = replace(
                evidence.lifecycle_summary, row_count=storage.primary_receipt.realized_row_count
            )
        from marivo.analysis.materialization.lifecycle_codec import (
            validate_descriptor as validate_lifecycle,
        )

        descriptor = replace(descriptor, lifecycle_evidence=evidence.lifecycle_summary)
        validate_lifecycle(descriptor)
    if dataset.kind == "event":
        from marivo.analysis.materialization.event_publication import bind_event_summary

        if evidence.event_summary is None:
            raise _error("output_validation", run.run_ref)
        if physical.local_steps and isinstance(
            dataset.row_contract.family_semantics,
            (EventFunnelSemantics, EventTimeToEventSemantics),
        ):
            from marivo.analysis.materialization.event_reducer_publication import (
                summary_from_batches,
            )
            from marivo.analysis.materialization.reads import payload_batches

            evidence.event_summary = summary_from_batches(
                dataset.row_contract.family_semantics,
                payload_batches(
                    self.store.project_root,
                    descriptor.storage_receipt,
                    policy=policy,
                    row=descriptor.row_contract,
                    rows=descriptor.row_set_contract,
                    audit=True,
                ),
                evidence.event_summary.coverage,
            )
        descriptor = bind_event_summary(descriptor, evidence.event_summary)
    elif dataset.kind == "population" and evidence.selection_summary is not None:
        from marivo.analysis.materialization.event_reducer_publication import (
            bind_selection_summary,
        )

        descriptor = bind_selection_summary(
            descriptor,
            replace(
                evidence.selection_summary, row_count=storage.primary_receipt.realized_row_count
            ),
        )
    elif dataset.kind == "candidate":
        from marivo.analysis.materialization.candidate_publication import (
            build_candidate_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches

        if evidence.candidate_summary is None:
            raise _error("output_validation", run.run_ref)
        descriptor, findings = build_candidate_publication(
            descriptor,
            None
            if isinstance(evidence.candidate_summary.evaluation, EntityCandidateEvaluationSummary)
            or any(f.role_id == "entity_identity" for f in dataset.schema.columns)
            else payload_batches(
                self.store.project_root,
                descriptor.storage_receipt,
                policy=policy,
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            ),
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
            definition=evidence.candidate_summary.definition,
            evaluation=evidence.candidate_summary.evaluation,
        )
    elif dataset.kind == "forecast":
        from marivo.analysis.materialization.forecast_publication import (
            build_forecast_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches

        descriptor, findings = build_forecast_publication(
            descriptor,
            payload_batches(
                self.store.project_root,
                descriptor.storage_receipt,
                policy=policy,
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            ),
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
            training=evidence.forecast_summary,
        )
    elif dataset.kind == "association":
        from marivo.analysis.materialization.association_publication import (
            build_association_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches

        descriptor, findings = build_association_publication(
            descriptor,
            payload_batches(
                self.store.project_root,
                descriptor.storage_receipt,
                policy=policy,
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            ),
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
            search_summary=evidence.association_summary,
        )
    elif dataset.row_contract.family_semantics.kind in (
        "delta/funnel@v1",
        "attribution/funnel-loss-rate@v1",
    ):
        from marivo.analysis.materialization.event_comparison_publication import (
            build_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches

        descriptor, findings = build_publication(
            descriptor,
            payload_batches(
                self.store.project_root,
                descriptor.storage_receipt,
                policy=policy,
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            ),
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
        )
    elif dataset.kind == "delta":
        from marivo.analysis.materialization.comparison_publication import (
            build_delta_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches

        rows = payload_batches(
            self.store.project_root,
            descriptor.storage_receipt,
            policy=policy,
            row=descriptor.row_contract,
            rows=descriptor.row_set_contract,
            audit=True,
        )
        descriptor, findings = build_delta_publication(
            descriptor,
            rows,
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
        )
    elif dataset.kind == "attribution":
        from marivo.analysis.materialization.attribution_publication import (
            build_attribution_publication,
        )
        from marivo.analysis.materialization.reads import payload_batches
        from marivo.analysis.operators.attribution_contracts import AttributePayload

        entity = any(field.role_id == "entity_identity" for field in dataset.schema.columns)
        payload = dataset._root.payload if isinstance(dataset._root, LogicalRootHandle) else None
        continuation = not isinstance(payload, AttributePayload)
        proof_definition = next(
            (
                root.definition_fingerprint
                for root in reversed(roots)
                if isinstance(root.payload, AttributePayload)
            ),
            None,
        )
        top_k = next(
            (
                root.payload.spec.top_k
                for root in reversed(roots)
                if isinstance(root.payload, AttributePayload)
            ),
            None,
        )
        descriptor, findings = build_attribution_publication(
            descriptor,
            None
            if entity or continuation
            else payload_batches(
                self.store.project_root,
                descriptor.storage_receipt,
                policy=policy,
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            ),
            artifact_ref=artifact_ref,
            session_ref=self.session_ref,
            top_k=top_k,
            source_summary=evidence.attribution_summary,
            continuation=continuation,
            proof_definition_fingerprint=proof_definition,
        )
    return descriptor, findings
