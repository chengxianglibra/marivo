"""Family-owned execution facts assembled into the single committed descriptor."""

from __future__ import annotations

from marivo.analysis.compiler.normalize import logical_roots
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    MaterializationContract,
    PopulationAuthority,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import LocalWriteResult
from marivo.analysis.observation.contracts import (
    MetricPayload,
    PopulationPayload,
    _version_selection_payload,
    producer_contract,
    scope_payload,
    semantic_dependency_digest,
)


def materialization_contract(dataset: LogicalDataset) -> MaterializationContract:
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
        retained_private_state_contract_ids=registration.retained_contract_ids,
        finding_policy_id="zero_findings@v1",
    )


def make_descriptor(
    dataset: LogicalDataset,
    materialization: MaterializationContract,
    storage: LocalWriteResult,
    validations: tuple[tuple[str, int], ...],
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
    current_payload = current_root.payload
    population: LogicalRootHandle | None
    if isinstance(current_payload, PopulationPayload):
        population = current_root
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
        inherited = next(
            (
                root
                for root in roots
                if root.definition_fingerprint == payload.definition.population_definition
            ),
            None,
        )
        if inherited is None:
            raise MaterializationError(
                expected="the exact inherited Population definition in the logical inputs",
                received="a missing inherited Population definition",
                repair="Reconstruct the Metric from its selected Population.",
                stage="publication",
            )
        payload = inherited.payload
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
        sampling_execution=None,
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
