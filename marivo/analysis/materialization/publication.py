"""Family-owned execution facts assembled into the single committed descriptor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from marivo.analysis.compiler.normalize import artifact_inputs, logical_roots
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.domains.contracts import EventPayload, EventSelectionPayload
from marivo.analysis.evidence.types import QualitySummary
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ComparisonInputAuthority,
    MaterializationContract,
    PopulationAuthority,
    SamplingRealization,
    StorageReceipt,
    finding_extractor,
    finding_policy,
    required_retained_contracts,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.storage import DatasetWriteResult
from marivo.analysis.observation.contracts import (
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    RetainedRowsPayload,
    _version_selection_payload,
    producer_contract,
    scope_payload,
    semantic_dependency_digest,
)
from marivo.analysis.observation.population_sample import PopulationSamplePayload
from marivo.analysis.observation.sampling import EntitySamplingPolicy


def materialization_contract(
    dataset: LogicalDataset,
    *,
    inherited: ArtifactDescriptor | None = None,
    input_descriptors: tuple[ArtifactDescriptor, ...] = (),
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
        finding_extractor_id=finding_extractor(dataset.row_contract, registration.producer_id),
        finding_extractor_version=1,
        validation_output_contract_ids=(registration.validation_id,),
        retained_private_state_contract_ids=required_retained_contracts(
            dataset.row_contract,
            registration.retained_contract_ids,
            sampled=(inherited is not None and inherited.sampling_execution is not None)
            or any(item.sampling_execution is not None for item in input_descriptors)
            or any(
                isinstance(item.payload, PopulationSamplePayload)
                or (
                    isinstance(item.payload, PopulationPayload)
                    and item.payload.sampling is not None
                )
                for item in logical_roots(dataset)
            ),
        ),
        finding_policy_id=finding_policy(dataset.row_contract, registration.producer_id),
    )


def make_descriptor(
    dataset: LogicalDataset,
    materialization: MaterializationContract,
    storage: DatasetWriteResult[StorageReceipt],
    validations: tuple[tuple[str, int], ...],
    sampling: tuple[SamplingRealization, ...] = (),
    *,
    inherited: ArtifactDescriptor | None = None,
    input_descriptors: tuple[ArtifactDescriptor, ...] = (),
    sampling_by_root: Mapping[int, SamplingRealization] | None = None,
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
    from marivo.analysis.operators.candidate_contracts import CandidateSemantics

    semantics = dataset.row_contract.family_semantics
    delta_candidate = (
        isinstance(semantics, CandidateSemantics)
        and semantics.objective in ("period_shifts", "driver_axes")
        and (inherited is None or inherited.row_contract.shape_id.family_id != "candidate")
    )
    if delta_candidate:
        paired = _delta_descriptor(
            dataset,
            materialization,
            storage,
            validations,
            sampling,
            inherited=inherited,
            input_descriptors=input_descriptors,
            sampling_by_root=sampling_by_root,
        )
        return replace(
            paired,
            comparison_basis=paired.comparison_inputs[0].comparison_basis,
            comparison_inputs=(),
        )
    if dataset.row_contract.shape_id.family_id in ("delta", "attribution"):
        return _delta_descriptor(
            dataset,
            materialization,
            storage,
            validations,
            sampling,
            inherited=inherited,
            input_descriptors=input_descriptors,
            sampling_by_root=sampling_by_root,
        )
    from marivo.analysis.operators.contracts import comparison_basis

    basis = comparison_basis(dataset)
    selection_authority = (
        _selection_population_authority(
            dataset, input_descriptors or (() if inherited is None else (inherited,)), validations
        )
        if any(isinstance(root.payload, EventSelectionPayload) for root in roots)
        or (
            inherited is not None
            and inherited.subject_selection_evidence is not None
            and dataset.kind == "population"
        )
        else None
    )
    retained_inputs = (inherited,) if inherited is not None else input_descriptors
    _validate_sampling(roots, sampling, retained_inputs)
    if inherited is not None:
        population_definition = (
            dataset.definition_fingerprint
            if dataset.kind == "population"
            else inherited.population_authority.definition_fingerprint
        )
        for root in roots:
            if root.operator_id in ("session.observe", "session.events.match"):
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
            population_authority=selection_authority
            or replace(
                inherited.population_authority,
                definition_fingerprint=population_definition,
                validation_results=validations,
            ),
            sampling_execution=sampling or None,
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
            comparison_basis=basis,
            event_evidence=inherited.event_evidence if dataset.kind == "event" else None,
            subject_selection_evidence=inherited.subject_selection_evidence
            if dataset.kind == "population"
            else None,
            candidate_evidence=(
                inherited.candidate_evidence
                if dataset.row_contract.shape_id.family_id == "candidate"
                else None
            ),
        )
    if selection_authority is not None:
        return ArtifactDescriptor(
            definition_fingerprint=dataset.definition_fingerprint,
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            realized_schema=storage.realized_schema,
            bounded_lineage=dataset._lineage,
            semantic_dependency_digest=semantic_dependency_digest(dataset),
            population_authority=selection_authority,
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
            comparison_basis=basis,
        )
    owning_root = current_root
    while not isinstance(owning_root.payload, (PopulationPayload, MetricPayload, EventPayload)):
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
    elif isinstance(current_payload, (MetricPayload, EventPayload)):
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
    from marivo.analysis.operators.candidate_contracts import CandidatePayload

    membership_root = population
    payload = membership_root.payload
    while not isinstance(payload, PopulationPayload):
        if isinstance(payload, MetricPayload):
            inherited_root = next(
                (
                    root
                    for root in roots
                    if root.definition_fingerprint == payload.definition.population_definition
                ),
                None,
            )
        elif (
            str(membership_root.shape_id) == "candidate/entity-outlier@v1"
            and (
                (
                    type(payload) is CandidatePayload
                    and payload.spec.definition.objective == "entity_outliers"
                )
                or type(payload) is RetainedRowsPayload
            )
            and len(membership_root.inputs) == 1
            and isinstance(membership_root.inputs[0].root, LogicalRootHandle)
        ):
            # The selected Candidate remains authority; only the governed
            # membership scope is inherited from its original observation.
            inherited_root = membership_root.inputs[0].root
        else:
            raise MaterializationError(
                expected="a Population, Entity-unique Metric, or Entity-outlier membership definition",
                received="an unsupported membership payload",
                repair="Reconstruct the Metric from its selected Population.",
                stage="publication",
            )
        if inherited_root is None:
            raise MaterializationError(
                expected="the exact inherited Population definition in the logical inputs",
                received="a missing inherited Population definition",
                repair="Reconstruct the Metric from its selected Population.",
                stage="publication",
            )
        membership_root = inherited_root
        payload = membership_root.payload
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
        comparison_basis=basis,
    )


def _sample_policy(root: LogicalRootHandle) -> tuple[EntitySamplingPolicy, str] | None:
    payload = root.payload
    if isinstance(payload, PopulationSamplePayload):
        return payload.policy, payload.target_population_definition_fingerprint
    if (
        isinstance(payload, PopulationPayload)
        and payload.sampling is not None
        and payload.target_population_definition_fingerprint is not None
    ):
        return payload.sampling, payload.target_population_definition_fingerprint
    return None


def _validate_sampling(
    roots: tuple[LogicalRootHandle, ...],
    sampling: tuple[SamplingRealization, ...],
    retained_inputs: tuple[ArtifactDescriptor, ...],
) -> None:
    inherited = tuple(
        receipt for item in retained_inputs for receipt in item.sampling_execution or ()
    )
    sampled_roots = tuple(root for root in roots if root.operator_id == "population.sample")
    if inherited and sampled_roots:
        raise MaterializationError(
            expected="one sampling call on an unsampled Population",
            received="a sampling request after retained sampling authority",
            repair="Construct a new sample branch from the unsampled Population.",
            stage="publication",
        )
    if sampling[: len(inherited)] != inherited or len(sampling) != len(inherited) + len(
        sampled_roots
    ):
        raise MaterializationError(
            expected="the exact inherited sampling receipts followed by each authored realization",
            received="missing, altered or additional sampling execution facts",
            repair="Execute the complete sampled Population through its registered physical fence.",
            stage="publication",
        )
    for ordinal, (root, receipt) in enumerate(
        zip(sampled_roots, sampling[len(inherited) :], strict=True), start=len(inherited)
    ):
        request = _sample_policy(root)
        if request is None or (
            receipt.ordinal != ordinal
            or receipt.population_definition_fingerprint != root.definition_fingerprint
            or receipt.target_population_definition_fingerprint != request[1]
            or receipt.target_rows != request[0].target_rows
            or receipt.seed != request[0].seed
        ):
            raise MaterializationError(
                expected="one exact realized receipt for every authored sampling requirement",
                received="inconsistent sampling execution facts",
                repair="Execute the complete sampled Population through its registered physical fence.",
                stage="publication",
            )


def _selection_population_authority(
    dataset: Dataset,
    inputs: tuple[ArtifactDescriptor, ...],
    validations: tuple[tuple[str, int], ...],
) -> PopulationAuthority:
    retained = {item.definition_fingerprint: item for item in inputs}

    def visit(value: Dataset) -> PopulationAuthority:
        if isinstance(value, MaterializedDataset):
            return replace(
                retained[value.definition_fingerprint].population_authority,
                validation_results=validations,
            )
        root = value._root
        if not isinstance(root, LogicalRootHandle):
            raise MaterializationError(
                expected="exact selected Population ancestry",
                received="invalid definition",
                repair="Reconstruct the selected Population.",
                stage="publication",
            )
        payload = root.payload
        if isinstance(payload, PopulationPayload) and not value._inputs:
            return PopulationAuthority(
                value.definition_fingerprint,
                payload.entity.ref.path,
                payload.entity.identity_signature,
                scope_payload(payload.time_scope),
                _version_selection_payload(payload.version_selection),
                validations,
            )
        if not value._inputs:
            raise MaterializationError(
                expected="complete selected Population ancestry",
                received="missing input",
                repair="Reconstruct the selected Population.",
                stage="publication",
            )
        authority = visit(value._inputs[0])
        if value.kind == "population":
            return replace(
                authority,
                definition_fingerprint=value.definition_fingerprint,
                validation_results=validations,
            )
        if isinstance(payload, (MetricPayload, EventPayload)):
            return replace(
                authority,
                definition_fingerprint=payload.definition.population_definition,
                validation_results=validations,
            )
        return authority

    return visit(dataset)


def _delta_descriptor(
    dataset: LogicalDataset,
    materialization: MaterializationContract,
    storage: DatasetWriteResult[StorageReceipt],
    validations: tuple[tuple[str, int], ...],
    sampling: tuple[SamplingRealization, ...],
    *,
    inherited: ArtifactDescriptor | None,
    input_descriptors: tuple[ArtifactDescriptor, ...],
    sampling_by_root: Mapping[int, SamplingRealization] | None,
) -> ArtifactDescriptor:
    """Bind both operand authorities without borrowing the first input's meaning."""
    from marivo.analysis.operators.contracts import ComparePayload, comparison_basis

    leaves = tuple(artifact_inputs(dataset))
    if not input_descriptors and inherited is not None and len(leaves) == 1:
        input_descriptors = (inherited,)
    selected = {
        leaf.state.artifact_ref.ref: descriptor
        for leaf, descriptor in zip(leaves, input_descriptors, strict=True)
    }
    if not selected and inherited is not None and len(leaves) == 1:
        selected[leaves[0].state.artifact_ref.ref] = inherited

    def authority(value: Dataset) -> PopulationAuthority:
        if isinstance(value, MaterializedDataset):
            return selected[value.state.artifact_ref.ref].population_authority
        root = value._root
        if not isinstance(root, LogicalRootHandle):
            raise MaterializationError(
                expected="exact comparison operand authority",
                received="unknown operand",
                repair="Reconstruct the comparison from its two retained or logical Metrics.",
                stage="publication",
            )
        if isinstance(root.payload, PopulationPayload):
            payload = root.payload
            return PopulationAuthority(
                root.definition_fingerprint,
                payload.entity.ref.path,
                payload.entity.identity_signature,
                scope_payload(payload.time_scope),
                _version_selection_payload(payload.version_selection),
                validations,
            )
        if not value._inputs:
            raise MaterializationError(
                expected="complete comparison Population ancestry",
                received="missing operand ancestry",
                repair="Reconstruct both Metrics with their selected Population.",
                stage="publication",
            )
        result = authority(value._inputs[0])
        if isinstance(root.payload, MetricPayload):
            return replace(
                result,
                definition_fingerprint=root.payload.definition.population_definition,
                validation_results=validations,
            )
        return result

    comparison: Dataset = dataset
    while not isinstance(comparison._root, LogicalRootHandle) or not isinstance(
        comparison._root.payload, ComparePayload
    ):
        if isinstance(comparison, MaterializedDataset):
            retained = selected[comparison.state.artifact_ref.ref]
            inputs = retained.comparison_inputs
            break
        if isinstance(comparison._root, LogicalRootHandle) and comparison._root.operator_id in (
            "delta.attribute_expanded",
            "discover.driver_axes_expanded",
        ):
            comparison = comparison._inputs[0]
            continue
        if len(comparison._inputs) != 1:
            raise MaterializationError(
                expected="a comparison or its single-input row continuation",
                received="missing comparison owner",
                repair="Construct the Delta through Metric.compare().",
                stage="publication",
            )
        comparison = comparison._inputs[0]
    else:
        operands: list[ComparisonInputAuthority] = []
        for role, operand in zip(("current", "baseline"), comparison._inputs, strict=True):
            refs = tuple(item.state.artifact_ref.ref for item in artifact_inputs(operand))
            sampled_roots = (
                tuple(
                    root
                    for root in logical_roots(operand)
                    if root.operator_id == "population.sample"
                )
                if isinstance(operand, LogicalDataset)
                else ()
            )
            retained_sampling = tuple(
                receipt for ref in refs for receipt in (selected[ref].sampling_execution or ())
            )
            logical_sampling: list[SamplingRealization] = []
            for root in sampled_roots:
                receipt = None if sampling_by_root is None else sampling_by_root.get(id(root))
                if receipt is None:
                    raise MaterializationError(
                        expected="one exact realized receipt per sampled logical input identity",
                        received="missing sampled comparison operand authority",
                        repair="Bind both sampled branches to their realized execution receipts.",
                        stage="publication",
                    )
                logical_sampling.append(receipt)
            receipts = (*retained_sampling, *logical_sampling)
            operands.append(
                ComparisonInputAuthority(
                    "current" if role == "current" else "baseline",
                    operand.definition_fingerprint,
                    authority(operand),
                    tuple(replace(item, ordinal=index) for index, item in enumerate(receipts))
                    or None,
                    refs,
                    comparison_basis(operand),
                )
            )
        inputs = tuple(operands)
    if len(inputs) != 2:
        raise MaterializationError(
            expected="two exact ordered comparison operands",
            received="incomplete comparison authority",
            repair="Reconstruct the comparison from complete current and baseline Metrics.",
            stage="publication",
        )
    roots = tuple(logical_roots(dataset))
    from marivo.analysis.operators.attribution_contracts import AttributePayload

    attribute_payload = next(
        (root.payload for root in reversed(roots) if isinstance(root.payload, AttributePayload)),
        None,
    )
    attribution_folds = None
    if dataset.row_contract.shape_id.family_id == "attribution":
        if attribute_payload is not None:
            attribution_folds = (
                attribute_payload.spec.current_fold_authority,
                attribute_payload.spec.baseline_fold_authority,
            )
        elif inherited is not None:
            attribution_folds = inherited.attribution_fold_authority
    return ArtifactDescriptor(
        definition_fingerprint=dataset.definition_fingerprint,
        row_contract=dataset.row_contract,
        row_set_contract=dataset.row_set_contract,
        realized_schema=storage.realized_schema,
        bounded_lineage=dataset._lineage,
        semantic_dependency_digest=semantic_dependency_digest(
            dataset,
            retained_semantic_digests={
                ref: item.semantic_dependency_digest for ref, item in selected.items()
            },
        ),
        population_authority=replace(
            inputs[0].population_authority, validation_results=validations
        ),
        sampling_execution=sampling or None,
        operator_implementation_versions=tuple(
            dict.fromkeys(
                (
                    *(
                        version
                        for item in selected.values()
                        for version in item.operator_implementation_versions
                    ),
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
        comparison_inputs=inputs,
        attribution_evidence=(
            inherited.attribution_evidence
            if inherited is not None and dataset.row_contract.shape_id.family_id == "attribution"
            else None
        ),
        attribution_fold_authority=attribution_folds,
    )
