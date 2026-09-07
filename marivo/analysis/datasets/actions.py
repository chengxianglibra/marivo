"""Pure operator construction, leaving execution to producing family owners."""

from __future__ import annotations

from marivo.analysis.datasets.base import (
    Dataset,
    DatasetOwner,
    LogicalDataset,
    _construction_error,
    _make_logical_dataset,
    _validate_input_ownership,
)
from marivo.analysis.datasets.descriptors import DatasetRowContract, DatasetRowSetContract
from marivo.analysis.datasets.handles import (
    CanonicalValue,
    RealizationRequirement,
    _LogicalNodePayload,
)
from marivo.analysis.datasets.registry import DatasetFamilyRegistry


def construct_operator(
    *,
    owner: DatasetOwner,
    registry: DatasetFamilyRegistry,
    operator_id: str,
    inputs: tuple[Dataset, ...],
    row_contract: DatasetRowContract,
    row_set_contract: DatasetRowSetContract,
    parameters: CanonicalValue = (),
    realizations: tuple[RealizationRequirement, ...] = (),
    dependency_facts: tuple[str, ...] = (),
    contract_versions: tuple[tuple[str, str], ...] = (),
    payload: _LogicalNodePayload | None = None,
) -> LogicalDataset:
    _validate_input_ownership(owner, inputs)
    if not inputs:
        raise _construction_error("registered downstream Dataset inputs", "no receiver")
    consumer = registry.consumer(inputs[0], operator_id)
    if len(inputs) != len(consumer.input_roles):
        raise _construction_error("registered ordered input roles", "wrong operand count")
    for item in inputs:
        if item.row_contract.shape_id not in consumer.accepted_shape_ids:
            raise _construction_error(
                "admitted family and exact shape for each operand", "unsupported input shape"
            )
    return _make_logical_dataset(
        owner=owner,
        registry=registry,
        family_id=consumer.output_family,
        row_contract=row_contract,
        row_set_contract=row_set_contract,
        operator_id=operator_id,
        inputs=inputs,
        input_roles=consumer.input_roles,
        parameters=parameters,
        realizations=realizations,
        requirements=consumer.requirements,
        dependency_facts=dependency_facts,
        contract_versions=contract_versions,
        payload=payload,
    )
