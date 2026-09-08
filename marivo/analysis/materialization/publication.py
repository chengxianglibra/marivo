"""Family-owned execution facts assembled into the single committed descriptor."""

from __future__ import annotations

from dataclasses import replace

from marivo.analysis.compiler.normalize import artifact_inputs, logical_roots
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    MaterializationContract,
    PopulationAuthority,
    SamplingRealization,
    StorageReceipt,
    required_retained_contracts,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.observation.contracts import (
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    _version_selection_payload,
    producer_contract,
    scope_payload,
    semantic_dependency_digest,
)


def materialization_contract(
    dataset: LogicalDataset, *, inherited: ArtifactDescriptor | None = None
) -> MaterializationContract:
    """Resolve the exact family registration before a producing Run can be admitted."""
    root = dataset._root
    if not isinstance(root, LogicalRootHandle):
        raise MaterializationError(
            expected="a registered logical Dataset root",
            received="a retained scan root",
            repair="Execute a logical Dataset constructed by the private source factory.",
            stage="implementation_registration",
        )
    registration = producer_contract(root.operator_id)
    if root.contract_versions != registration.versions:
        # Core preserves the owner's exact ordered versions.
        raise MaterializationError(
            expected="the exact construction-time producing contract versions",
            received="incompatible producer registration",
            repair="Reconstruct the logical definition with the current private source factory.",
            stage="implementation_registration",
        )
    return MaterializationContract(
        producer_id=registration.producer_id,
        producer_contract_version=1,
        shape_id=dataset.row_contract.shape_id,
        quality_contract_id=registration.quality_id,
        quality_contract_version=1,
        evidence_extractor_id=registration.evidence_id,
        evidence_extractor_version=1,
        finding_extractor_id="none",
        finding_extractor_version=1,
        validation_output_contract_ids=(registration.validation_id,),
        retained_private_state_contract_ids=required_retained_contracts(
            dataset.row_contract,
            registration.retained_contract_ids,
            sampled=(inherited is not None and inherited.sampling_execution is not None)
            or any(
                isinstance(item.payload, PopulationPayload) and item.payload.sampling is not None
                for item in logical_roots(dataset)
            ),
        ),
        finding_policy_id="zero_findings@v1",
    )


def make_descriptor(
    dataset: LogicalDataset,
    materialization: MaterializationContract,
    storage: DatasetWriteResult[StorageReceipt],
    validations: tuple[tuple[str, int], ...],
    sampling: tuple[SamplingRealization, ...] = (),
    *,
    inherited: ArtifactDescriptor | None = None,
) -> ArtifactDescriptor:
    current_root = dataset._root
    if not isinstance(current_root, LogicalRootHandle):
        raise MaterializationError(
            expected="a producing logical Dataset root",
            received="a retained scan root",
            repair="Publish only the output of an admitted logical Dataset execution.",
            stage="publication",
        )
    roots = tuple(logical_roots(dataset))
    if inherited is not None:
        if sampling != (inherited.sampling_execution or ()):
            raise MaterializationError(
                expected="the exact committed sampling realization of the input Artifact",
                received="retained continuation changed its sampling realization",
                repair="Consume the selected checkpoint without resampling its membership.",
                stage="publication",
            )
        population_definition = inherited.population_authority.definition_fingerprint
        for root in roots:
            if root.operator_id == "session.observe":
                selected = root.inputs[0].root
                population_definition = (
                    inherited.definition_fingerprint
                    if isinstance(selected, MaterializedScanLeafHandle)
                    else selected.definition_fingerprint
                )
        return replace(
            inherited,
            definition_fingerprint=dataset.definition_fingerprint,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            realized_schema=storage.realized_schema,
            bounded_lineage=dataset._lineage,
            semantic_dependency_digest=(
                semantic_dependency_digest(
                    dataset,
                    retained_semantic_digests={
                        leaf.state.artifact_ref.ref: inherited.semantic_dependency_digest
                        for leaf in artifact_inputs(dataset)
                    },
                )
                if isinstance(dataset._owner, ObservationOwner)
                else inherited.semantic_dependency_digest
            ),
            population_authority=replace(
                inherited.population_authority,
                definition_fingerprint=population_definition,
                validation_results=validations,
            ),
            operator_implementation_versions=tuple(
                dict.fromkeys(
                    (
                        *inherited.operator_implementation_versions,
                        *((root.operator_id, 1) for root in roots),
                    )
                )
            ),
            dataset_materialization_contract=materialization,
            storage_receipt=storage.primary_receipt,
            retained_parts=storage.retained_parts,
            quality_summary=QualitySummary(
                sample_size=storage.realized_row_count,
                evaluated_check_count=len(validations) + 1,
                failed_check_count=0,
                warning_check_count=0,
            ),
        )
    sampled_roots = tuple(root for root in roots if root.operator_id == "population.sample")
    if len(sampled_roots) != len(sampling) or any(
        not isinstance(root.payload, PopulationPayload)
        or root.payload.sampling is None
        or receipt.ordinal != ordinal
        or receipt.population_definition_fingerprint != root.definition_fingerprint
        or receipt.target_population_definition_fingerprint
        != root.payload.target_population_definition_fingerprint
        or receipt.target_rows != root.payload.sampling.target_rows
        or receipt.seed != root.payload.sampling.seed
        for ordinal, (root, receipt) in enumerate(zip(sampled_roots, sampling, strict=True))
    ):
        raise MaterializationError(
            expected="one exact realized receipt for every authored sampling requirement",
            received="inconsistent sampling execution facts",
            repair="Execute the complete sampled Population through its registered physical fence.",
            stage="publication",
        )
    owning_root = current_root
    while not isinstance(owning_root.payload, (PopulationPayload, MetricPayload)):
        # A retained row/fold suffix keeps the nearest observation's selected
        # membership. Earlier observations may have a different Population.
        if len(owning_root.inputs) != 1 or not isinstance(
            owning_root.inputs[0].root, LogicalRootHandle
        ):
            raise MaterializationError(
                expected="one logical input leading to the owning Observation definition",
                received="a missing or ambiguous Observation owner",
                repair="Reconstruct the logical definition with the private source factory.",
                stage="publication",
            )
        owning_root = owning_root.inputs[0].root
    current_payload = owning_root.payload
    population: LogicalRootHandle | None
    if isinstance(current_payload, PopulationPayload):
        population = owning_root
    elif isinstance(current_payload, MetricPayload):
        population = next(
            (
                root
                for root in roots
                if root.definition_fingerprint == current_payload.definition.population_definition
            ),
            None,
        )
    else:
        raise MaterializationError(
            expected="a registered Population or Metric producing definition",
            received="an unsupported Observation payload",
            repair="Reconstruct the logical definition with the private source factory.",
            stage="publication",
        )
    if population is None:
        raise MaterializationError(
            expected="the exact selected Population definition in the logical inputs",
            received="a missing Population definition",
            repair="Reconstruct the Metric from its selected Population.",
            stage="publication",
        )
    payload = population.payload
    while not isinstance(payload, PopulationPayload):
        # A proven Entity-unique Metric can also supply the selected membership.
        if not isinstance(payload, MetricPayload):
            raise MaterializationError(
                expected="a Population or Entity-unique Metric membership definition",
                received="an unsupported membership payload",
                repair="Reconstruct the Metric from its selected Population.",
                stage="publication",
            )
        inherited_root = next(
            (
                root
                for root in roots
                if root.definition_fingerprint == payload.definition.population_definition
            ),
            None,
        )
        if inherited_root is None:
            raise MaterializationError(
                expected="the exact inherited Population definition in the logical inputs",
                received="a missing inherited Population definition",
                repair="Reconstruct the Metric from its selected Population.",
                stage="publication",
            )
        payload = inherited_root.payload
    return ArtifactDescriptor(
        definition_fingerprint=dataset.definition_fingerprint,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        realized_schema=storage.realized_schema,
        bounded_lineage=dataset._lineage,
        semantic_dependency_digest=semantic_dependency_digest(dataset),
        population_authority=PopulationAuthority(
            definition_fingerprint=population.definition_fingerprint,
            entity_ref=payload.entity.ref.path,
            identity_signature=payload.entity.identity_signature,
            membership_scope=scope_payload(payload.time_scope),
            version_selection=_version_selection_payload(payload.version_selection),
            validation_results=validations,
        ),
        sampling_execution=sampling or None,
        operator_implementation_versions=tuple(
            (name, 1) for name in dict.fromkeys(root.operator_id for root in roots)
        ),
        dataset_materialization_contract=materialization,
        storage_receipt=storage.primary_receipt,
        retained_parts=storage.retained_parts,
        quality_summary=QualitySummary(
            sample_size=storage.realized_row_count,
            evaluated_check_count=len(validations) + 1,
            failed_check_count=0,
            warning_check_count=0,
        ),
    )
