"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marivo.analysis.compiler import required_entities
from marivo.analysis.compiler.normalize import (
    artifact_inputs,
    logical_roots,
)
from marivo.analysis.compiler.placement import (
    ExecutionBinding,
    ParquetBinding,
    PhysicalStageGraph,
    SourceBinding,
    SourceStep,
    place,
    source_binding,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle, _validate_logical_root
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventPayload,
)
from marivo.analysis.domains.lifecycle import (
    LifecyclePayload,
)
from marivo.analysis.domains.lifecycle_reducers import (
    LifecycleReducerPayload,
)
from marivo.analysis.materialization import contracts as codec
from marivo.analysis.materialization.attribution_publication import AttributionSourceSummary
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    LocalReceipt,
    MaterializationContract,
    RunDatasetInput,
    RunRecord,
    run_failure_phase,
)
from marivo.analysis.materialization.errors import (
    MaterializationError,
)
from marivo.analysis.materialization.errors import _execution_error as _error
from marivo.analysis.materialization.event_codec import EventEvidenceSummary
from marivo.analysis.materialization.event_reducer_codec import (
    EventReducerEvidenceSummary,
    EventSelectionEvidenceSummary,
)
from marivo.analysis.materialization.execution_key import execution_key
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from marivo.analysis.materialization.lifecycle_reducer_codec import (
    ContinuationEvidence,
)
from marivo.analysis.materialization.ownership import owns_resource
from marivo.analysis.materialization.publication import materialization_contract
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.resources import (
    discharge_resources,
)
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.observation.contracts import (
    MetricPayload,
    PopulationPayload,
)
from marivo.analysis.operators.association_contracts import (
    AssociationSearchSummary,
)
from marivo.analysis.operators.candidate_contracts import (
    CandidateSearchSummary,
)
from marivo.analysis.operators.forecast_contracts import (
    ForecastTrainingSummary,
)

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

from marivo.analysis.materialization import dataset_publication, local_stage, source_stage
from marivo.analysis.materialization.admission import (
    _READ_POLICY,
    ExecutionStatistics,
    producer_contract_versions,
)
from marivo.analysis.materialization.execution_state import ExecutionProgress, StageResult


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    dataset: LogicalDataset
    roots: tuple[LogicalRootHandle, ...]
    records: dict[str, ArtifactRecord]
    retained_inputs: tuple[MaterializedDataset, ...]
    physical: PhysicalStageGraph
    source_steps: tuple[SourceStep, ...]
    source_step: SourceStep | None
    inherited: ArtifactDescriptor | None
    contract: MaterializationContract
    run: RunRecord


def _admit_miss(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    key: str,
    roots: tuple[LogicalRootHandle, ...],
) -> ExecutionPlan:
    retained_inputs = artifact_inputs(dataset)
    records = {value.state.artifact_ref.ref: self._selected(value) for value in retained_inputs}
    source_candidates: list[SourceBinding] = []

    def discover_candidates(value: LogicalDataset | MaterializedDataset) -> None:
        if isinstance(value, LogicalDataset):
            if (
                isinstance(value._root, LogicalRootHandle)
                and isinstance(
                    value._root.payload,
                    (PopulationPayload, MetricPayload, EventPayload, LifecyclePayload),
                )
            ) or (
                isinstance(value._root, LogicalRootHandle)
                and isinstance(value._root.payload, (EventFunnelPayload, LifecycleReducerPayload))
                and bool(value._root.payload.axes)
            ):
                binding = source_binding(value)
                if not any(binding.same_domain(item) for item in source_candidates):
                    source_candidates.append(binding)
            for child in value._inputs:
                if isinstance(child, (LogicalDataset, MaterializedDataset)):
                    discover_candidates(child)

    discover_candidates(dataset)

    def admitted_binding(value: MaterializedDataset) -> ExecutionBinding | None:
        receipt = records[value.state.artifact_ref.ref].descriptor.storage_receipt
        if not isinstance(receipt, LocalReceipt):
            raise _error("execution_boundary")
        # A fixed Parquet adapter can participate in the one existing
        # source domain, or own a source-free native retained stage.
        from marivo.analysis.operators.registry import supports_retained_import

        if len(source_candidates) == 1 and supports_retained_import(source_candidates[0].adapter):
            return source_candidates[0]
        from marivo.analysis.operators.registry import admit_retained_rows

        admit_retained_rows(value)
        return ParquetBinding(
            self,
            "parquet",
            codec.digest(("parquet", 1)),
        )

    physical = place(dataset, artifact_binding=admitted_binding)
    source_steps = tuple(step for step in physical.steps if isinstance(step, SourceStep))
    from marivo.analysis.materialization.execution import resolve_execution

    for admitted_step in source_steps:
        implementation = resolve_execution(admitted_step.implementation.backend)
        if implementation is None:
            raise _error("implementation_registration")
        implementation.admit(admitted_step.dataset)
    if (
        physical.steps[-1].output != physical.primary_output
        or physical.steps[-1].dataset is not dataset
    ):
        raise _error("implementation_registration")
    source_step = source_steps[0] if source_steps else None
    entities = tuple(
        entity
        for step in source_steps
        if isinstance(step.binding, SourceBinding)
        for entity in required_entities(step.dataset, registry=step.binding.owner.semantic_registry)
    )
    inherited = next(iter(records.values())).descriptor if records else None
    contract = materialization_contract(
        dataset,
        inherited=inherited,
        input_descriptors=tuple(
            records[value.state.artifact_ref.ref].descriptor for value in retained_inputs
        ),
    )
    from marivo.analysis.materialization.retained import (
        reject_source_private_transfer,
        required_part_roles,
        source_private_role,
    )

    if physical.local_steps and any(
        source_private_role(role)
        for step in physical.steps
        for role in required_part_roles(dataset, input_dataset=step.dataset)
    ):
        reject_source_private_transfer()
    run = self.store.admit(
        self.session_ref,
        key,
        RunDatasetInput(
            dataset.definition_fingerprint,
            dataset.row_contract.shape_id,
            dataset._root.row_contract_fingerprint,
            dataset._root.row_set_contract_fingerprint,
            tuple(dict.fromkeys(root.operator_id for root in roots))[:64],
            tuple(
                dict.fromkeys(f"{entity.ref.kind.value}:{entity.ref.path}" for entity in entities)
            )[:64],
        ),
        input_artifact_refs=tuple(value.state.artifact_ref.ref for value in retained_inputs),
    )
    self.last_run_ref = run.run_ref
    return ExecutionPlan(
        dataset=dataset,
        roots=roots,
        records=records,
        retained_inputs=retained_inputs,
        physical=physical,
        source_steps=source_steps,
        source_step=source_step,
        inherited=inherited,
        contract=contract,
        run=run,
    )


@dataclass(slots=True)
class ExecutionEvidence:
    validations: list[tuple[str, int]] = field(default_factory=list)
    attribution_summary: AttributionSourceSummary | None = None
    association_summary: AssociationSearchSummary | None = None
    forecast_summary: ForecastTrainingSummary | None = None
    candidate_summary: CandidateSearchSummary | None = None
    lifecycle_summary: LifecycleEvidenceSummary | ContinuationEvidence | None = None
    event_summary: EventEvidenceSummary | EventReducerEvidenceSummary | None = None
    selection_summary: EventSelectionEvidenceSummary | None = None

    @classmethod
    def from_records(cls, records: Mapping[str, ArtifactRecord]) -> ExecutionEvidence:
        state = cls()
        for input_record in records.values():
            descriptor = input_record.descriptor
            if descriptor.lifecycle_evidence is not None:
                state.lifecycle_summary = descriptor.lifecycle_evidence
            if descriptor.event_evidence is not None:
                state.event_summary = descriptor.event_evidence
            if descriptor.subject_selection_evidence is not None:
                state.selection_summary = descriptor.subject_selection_evidence
            if descriptor.candidate_evidence is not None:
                state.candidate_summary = CandidateSearchSummary(
                    descriptor.candidate_evidence.definition,
                    descriptor.candidate_evidence.evaluation,
                )
            if descriptor.forecast_evidence is not None:
                state.forecast_summary = descriptor.forecast_evidence.training
            if descriptor.association_evidence is not None:
                association = descriptor.association_evidence
                state.association_summary = AssociationSearchSummary(
                    association.searched_series_count,
                    association.original_candidate_count,
                    association.complete_pair_range,
                    association.null_pair_range,
                )
        return state


def execute(self: DatasetRuntime, dataset: LogicalDataset) -> MaterializedDataset:
    if (
        dataset._owner.session_id != self.session_ref
        or dataset._owner.store_id != self.store.store_id
    ):
        raise _error("graph_validation")
    root_handle = dataset._root
    if not isinstance(root_handle, LogicalRootHandle):
        raise _error("graph_validation")
    _validate_logical_root(root_handle)
    materialization_contract(dataset)
    roots = tuple(logical_roots(dataset))
    for root in roots:
        if root.contract_versions != producer_contract_versions(root.operator_id):
            raise _error("implementation_registration")
    key = execution_key(dataset.definition_fingerprint)
    with session_writer_guard(
        self.store.layout.lock_path(self.session_ref), session_ref=self.session_ref
    ):
        self.statistics = ExecutionStatistics()
        self.last_run_ref = None
        reconcile_session(
            self.store,
            self.session_ref,
            event=self._event,
        )
        hit = self.store.lookup(self.session_ref, key)
        if hit is not None:
            self.last_run_ref = hit.producing_run_ref
            return self._recover(hit)
        plan = _admit_miss(self, dataset, key, roots)
        records = plan.records
        physical = plan.physical
        source_steps = plan.source_steps
        source_step = plan.source_step
        run = plan.run
        progress = ExecutionProgress()
        try:
            from marivo.analysis.materialization.event_comparison_publication import (
                validate_checkpoint_inputs,
            )

            validate_checkpoint_inputs(
                dataset, {ref: record.descriptor for ref, record in records.items()}
            )
            evidence = ExecutionEvidence.from_records(records)
            with ExitStack() as source_contexts:
                prepared = source_stage.prepare_sources(
                    self, source_steps, records, run.run_ref, evidence, source_contexts
                )
                if not physical.local_steps:
                    artifact_ref, storage = source_stage.execute_source_only(
                        self, dataset, source_step, prepared, run.run_ref, evidence, progress
                    )
                else:
                    artifact_ref, storage = local_stage.execute_local_stages(
                        self, dataset, physical, prepared, records, run.run_ref, evidence, progress
                    )
            stage = StageResult(
                artifact_ref,
                storage,
                tuple(recipe for _, recipe, _ in prepared.values()),
            )
            descriptor, findings = dataset_publication.prepare_publication(
                self, plan, stage, evidence, progress, policy=_READ_POLICY
            )
            progress.phase = "evidence"
            self._event("evidence")
            progress.phase = "publication"
            resources = tuple(
                item
                for item in self.store.resources(self.session_ref)
                if item.run_ref == run.run_ref
            )
            receipts = (
                descriptor.storage_receipt,
                *(part.storage_receipt for part in descriptor.retained_parts),
            )
            outputs = tuple(
                item
                for item in resources
                if any(owns_resource(receipt, item) for receipt in receipts)
            )
            garbage = tuple(item for item in resources if item not in outputs)
            resolved = discharge_resources(self.store, garbage)
            record = self.store.publish(
                run.run_ref,
                artifact_ref,
                descriptor,
                findings=findings,
                resolved_resources=(*outputs, *resolved),
                event=self._event,
            )
            progress.phase = "presentation"
            self._event("delivery")
            return self._recover(record)
        except BaseException as exc:
            if isinstance(exc, MaterializationError) and exc.run_ref is None:
                exc.run_ref = run.run_ref
            # Retain only owner-safe error facts, never a datasource exception chain.
            safe = (
                exc
                if isinstance(exc, MaterializationError)
                else _error(progress.phase, run.run_ref)
            )
            try:
                recovered = dataset_publication.resolve_outcome(
                    self, run, safe, run_failure_phase(safe.stage, progress.phase)
                )
                if recovered is not None:
                    return recovered
            except BaseException:
                # Recovery retains unresolved obligations; preserve the original failure.
                pass
            raise
