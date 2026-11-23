"""Source placement and loaded catalog identities for semantic declarations."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from marivo._authoring.model import AuthoringCapability
from marivo._kernel.refs import SemanticKind
from marivo.semantic._help_specs.model import AuthoringSourceContract, SemanticObjectContract


def _authoring_source_contract(kind: SemanticKind) -> AuthoringSourceContract:
    placement_kind: Literal["domain_entrypoint", "domain_module"]
    if kind is SemanticKind.DOMAIN:
        placement_kind = "domain_entrypoint"
        path_template = "models/semantic/<domain>/_domain.py"
        identity_template = "<domain>"
    elif kind in {
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
    }:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<entity>.<name>"
    elif kind is SemanticKind.ENTITY:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<entity>"
    else:
        placement_kind = "domain_module"
        path_template = "models/semantic/<domain>/<module>.py"
        identity_template = "<domain>.<name>"
    return AuthoringSourceContract(
        placement_kind=placement_kind,
        path_template=path_template,
        catalog_kind=kind.value,
        canonical_identity_template=identity_template,
    )


def _source_contracts(
    object_contracts: tuple[SemanticObjectContract, ...],
    descriptors: tuple[AuthoringCapability, ...],
) -> Mapping[str, AuthoringSourceContract]:
    """Build closed placement/handoff facts for every source-authored object."""

    contracts: dict[str, AuthoringSourceContract] = {}
    for object_contract in object_contracts:
        source_contract = _authoring_source_contract(object_contract.semantic_kind)
        construction_ids = {
            mode.target.canonical_id for mode in object_contract.construction_modes
        } - {"measure.define", "metric.define"}
        contracts.update(
            {
                descriptor.canonical_id: source_contract
                for descriptor in descriptors
                if descriptor.canonical_id in construction_ids
            }
        )
    return MappingProxyType(contracts)
