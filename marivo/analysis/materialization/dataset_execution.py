"""Private first-route execution from pure definition to committed Dataset authority."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from marivo.analysis.compiler import required_entities
from marivo.analysis.compiler.normalize import (
    artifact_inputs,
    classify_inputs,
    logical_roots,
    required_source_dependencies,
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
from marivo.analysis.compiler.source_admission import (
    basic_metric_candidate,
    basic_population_candidate,
    mysql_composite_identity_candidate,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle, _validate_logical_root
from marivo.analysis.materialization import contracts as codec
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
from marivo.analysis.materialization.execution_key import execution_key
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
    producer_contract,
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
from marivo.analysis.operators.registry import legacy_source_migration_stage
from marivo.datasource.adapters import provider_names
from marivo.datasource.ir import TableSourceIR

if TYPE_CHECKING:
    from marivo.analysis.materialization.admission import DatasetRuntime

from marivo.analysis.materialization import dataset_publication, local_stage, source_stage
from marivo.analysis.materialization.admission import (
    _READ_POLICY,
    ExecutionStatistics,
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
    basic_source: bool
    inherited: ArtifactDescriptor | None
    contract: MaterializationContract
    run: RunRecord


def _admit_miss(
    self: DatasetRuntime,
    dataset: LogicalDataset,
    key: str,
    roots: tuple[LogicalRootHandle, ...],
    run_ref: str | None = None,
) -> ExecutionPlan:
    retained_inputs = artifact_inputs(dataset)
    records = {value.state.artifact_ref.ref: self._selected(value) for value in retained_inputs}
    source_candidates: list[SourceBinding] = []

    def discover_candidates(value: LogicalDataset | MaterializedDataset) -> None:
        if isinstance(value, LogicalDataset):
            if isinstance(value._root, LogicalRootHandle) and isinstance(
                value._root.payload, (PopulationPayload, MetricPayload)
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
    if mysql_composite_identity_candidate(dataset):
        raise MaterializationError(
            expected="Ibis lowering of the complete composite identity",
            received="MySQL Ibis compiler has no StructColumn rule",
            repair="Use a single-column Entity key or a backend with qualified composite identity lowering.",
            stage="source_admission",
        )
    basic_source = (
        (basic_population_candidate(dataset) or basic_metric_candidate(dataset))
        and not retained_inputs
        and not physical.local_steps
        and len(source_steps) == 1
        and source_steps[0].operation == "source"
        and isinstance(source_steps[0].binding, SourceBinding)
        and source_steps[0].binding.adapter in provider_names()
    )
    if basic_source:
        selected_binding = source_steps[0].binding
        assert isinstance(selected_binding, SourceBinding)
        selected_dependencies = required_source_dependencies(
            dataset, registry=selected_binding.owner.semantic_registry
        )
        basic_source = len(selected_dependencies.entries) == 1 and isinstance(
            selected_dependencies.entries[0].entity.source, TableSourceIR
        )
    if source_steps and not basic_source:
        operator_ids: list[str] = []
        for step in source_steps:
            root = step.dataset._root
            if not isinstance(root, LogicalRootHandle):
                raise _error("graph_validation")
            operator_ids.append(root.operator_id)
        final_root = dataset._root
        if not isinstance(final_root, LogicalRootHandle):
            raise _error("graph_validation")
        stages = tuple(
            stage
            for operator_id in (*operator_ids, final_root.operator_id)
            if (stage := legacy_source_migration_stage(operator_id)) is not None
        )
        repair = (
            f"Use a source method qualified after its R{max(stages)} migration; the old route cannot execute in R1.1."
            if stages
            else "This R5 route is retired. Use session.members(...).observe(...) through the public graph."
        )
        raise MaterializationError(
            expected="a source route through a session-issued Ibis read",
            received=f"legacy text-backed source steps: {', '.join(operator_ids[:4])}",
            repair=repair,
            stage="source_admission",
        )
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
        run_ref=run_ref,
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
        basic_source=basic_source,
        inherited=inherited,
        contract=contract,
        run=run,
    )


@dataclass(slots=True)
class ExecutionEvidence:
    validations: list[tuple[str, int]] = field(default_factory=list)
    association_summary: AssociationSearchSummary | None = None
    forecast_summary: ForecastTrainingSummary | None = None
    candidate_summary: CandidateSearchSummary | None = None

    @classmethod
    def from_records(cls, records: Mapping[str, ArtifactRecord]) -> ExecutionEvidence:
        state = cls()
        for input_record in records.values():
            descriptor = input_record.descriptor
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
        if root.contract_versions != producer_contract(root.operator_id).versions:
            raise _error("implementation_registration")
    from marivo.analysis.materialization.store import _new_run_ref

    source_invocation = bool(classify_inputs(root_handle).source_nodes)
    run_ref = _new_run_ref() if source_invocation else None
    key = (
        codec.digest((dataset.definition_fingerprint, run_ref))
        if source_invocation
        else execution_key(dataset.definition_fingerprint)
    )
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
        hit = None if source_invocation else self.store.lookup(self.session_ref, key)
        if hit is not None:
            self.last_run_ref = hit.producing_run_ref
            return self._recover(hit)
        plan = _admit_miss(self, dataset, key, roots, run_ref)
        records = plan.records
        physical = plan.physical
        source_steps = plan.source_steps
        source_step = plan.source_step
        run = plan.run
        progress = ExecutionProgress()
        try:
            evidence = ExecutionEvidence.from_records(records)
            if plan.basic_source:
                from marivo.analysis.materialization.basic_source import execute_basic_source

                if source_step is None:
                    raise _error("execution_boundary", run.run_ref)
                artifact_ref, storage, recipe = execute_basic_source(
                    self, dataset, source_step, run.run_ref
                )
                stage = StageResult(artifact_ref, storage, (recipe,))
            else:
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
                            self,
                            dataset,
                            physical,
                            prepared,
                            records,
                            run.run_ref,
                            evidence,
                            progress,
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
