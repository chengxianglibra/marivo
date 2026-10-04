"""Shared exact identity admission for Metric and Event source membership."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from marivo.analysis.datasets.base import Dataset, _validate_input_ownership
from marivo.analysis.datasets.descriptors import _EntityFieldIdentity
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    ObservationOwner,
    construction_error,
)
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
)
from marivo.semantic.ir import TargetEntityContract
from marivo.semantic.validator import normalize_target_entity

if TYPE_CHECKING:
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset

PopulationInput: TypeAlias = "LogicalPopulationDataset | MaterializedPopulationDataset | LogicalMetricDataset | MaterializedMetricDataset"


def admit_population(
    owner: ObservationOwner,
    registry: DatasetFamilyRegistry,
    population: Dataset,
    *,
    required_entity: TargetEntityContract | None = None,
) -> TargetEntityContract:
    """Check exact shape and ownership without reading or projecting identity rows."""
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset

    if type(population) not in (
        LogicalPopulationDataset,
        MaterializedPopulationDataset,
        LogicalMetricDataset,
        MaterializedMetricDataset,
    ):
        raise construction_error(
            "registered Population or Entity-present Metric input", "unsupported population input"
        )
    _validate_input_ownership(owner, (population,))
    identities = tuple(
        column.identity
        for column in population.schema.columns
        if isinstance(column.identity, _EntityFieldIdentity)
    )
    if len(identities) != 1:
        raise construction_error(
            "one complete Entity identity coordinate", "Entity-reduced or invalid population input"
        )
    identity = identities[0]
    if population.kind == "metric":
        semantics = population.row_contract.family_semantics
        if not isinstance(semantics, EntityPresentMetricSemantics) or any(
            not facts or facts[0] != "entity_unique"
            for _, _, facts in semantics.coordinate_semantics
        ):
            raise construction_error(
                "owner-proven Entity-unique Metric coordinates", "unsupported identity projection"
            )
    entity = normalize_target_entity(owner.semantic_registry, identity.entity_ref.path)
    if entity.identity_signature != identity.identity_signature:
        raise construction_error("same governed identity signature", "changed Entity key contract")
    if required_entity is not None and (
        entity.ref != required_entity.ref
        or entity.identity_signature != required_entity.identity_signature
    ):
        raise construction_error(
            "the exact Event subject Entity and complete ordered identity signature",
            "population belongs to a different subject Entity",
            repair="Construct or select membership at the exact Pattern participant Entity.",
        )
    return entity
