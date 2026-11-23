"""Assemble the Semantic Help manifest from capability specs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

from marivo._kernel.refs import SemanticKind
from marivo.semantic._help_specs.authoring_specs import SPECS as AUTHORING
from marivo.semantic._help_specs.builders import _canonical_link
from marivo.semantic._help_specs.catalog_specs import SPECS as CATALOG
from marivo.semantic._help_specs.catalog_specs import _result_method_capabilities
from marivo.semantic._help_specs.check_specs import SPECS as CHECKS
from marivo.semantic._help_specs.expression_specs import expression_specs
from marivo.semantic._help_specs.loading_specs import SPECS as LOAD
from marivo.semantic._help_specs.model import (
    SEMANTIC_HELP_RENDER_BUDGETS,
    AuthoringSourceContract,
    SemanticHelpDescriptor,
    SemanticHelpRenderBudget,
    SemanticHelpRenderClass,
    SemanticManifest,
    SemanticObjectContract,
    SemanticRootSection,
)
from marivo.semantic._help_specs.navigation import (
    NAVIGATION,
    _navigation_descriptors,
    _root_sections,
    expression_members,
    expression_topics,
)
from marivo.semantic._help_specs.object_specs import OBJECT_CONTRACTS
from marivo.semantic._help_specs.placement import _source_contracts
from marivo.semantic.error_repairs import ERROR_REPAIRS, SemanticRepairContract
from marivo.semantic.errors import (
    CatalogSearchError,
    DeclaredTimezoneContradiction,
    SemanticContractScopeError,
    SemanticDecoratorError,
    SemanticError,
    SemanticExpressionError,
    SemanticHelpTargetError,
    SemanticLoadError,
    SemanticLoadFailed,
    SemanticRuntimeError,
    SourceTimezoneMissing,
)


def finalize_registry(
    *,
    root_sections: tuple[SemanticRootSection, ...],
    source_contracts: Mapping[str, AuthoringSourceContract],
    repair_contracts: Mapping[str, SemanticRepairContract],
    help_descriptors: tuple[SemanticHelpDescriptor, ...],
    object_contracts: tuple[SemanticObjectContract, ...],
    render_budgets: Mapping[
        SemanticHelpRenderClass, SemanticHelpRenderBudget
    ] = SEMANTIC_HELP_RENDER_BUDGETS,
) -> SemanticManifest:
    return SemanticManifest(
        "semantic",
        help_descriptors,
        root_sections,
        MappingProxyType(dict(source_contracts)),
        MappingProxyType(dict(repair_contracts)),
        object_contracts,
        MappingProxyType(dict(render_budgets)),
    )


def _build_manifest() -> SemanticManifest:
    rows = (
        *LOAD,
        *AUTHORING,
        *CHECKS,
        *CATALOG,
        *expression_specs(),
        *_result_method_capabilities(),
    )
    objects = tuple(
        replace(
            contract,
            supporting_targets=tuple(
                dict.fromkeys((*contract.supporting_targets, *expression_members(rows, "metric")))
            ),
        )
        if contract.semantic_kind is SemanticKind.METRIC
        else contract
        for contract in OBJECT_CONTRACTS
    )
    return finalize_registry(
        root_sections=_root_sections(),
        source_contracts=_source_contracts(objects, rows),
        repair_contracts={
            key: replace(repair, help_target=_canonical_link(repair.help_target))
            for key, repair in ERROR_REPAIRS.items()
        },
        help_descriptors=(
            *rows,
            *expression_topics(rows),
            *NAVIGATION,
            *_navigation_descriptors(objects),
        ),
        object_contracts=objects,
    )


REGISTRY = _build_manifest()


def _error_types() -> Mapping[str, type]:

    return MappingProxyType(
        {
            "CatalogSearchError": CatalogSearchError,
            "SemanticError": SemanticError,
            "SemanticExpressionError": SemanticExpressionError,
            "SourceTimezoneMissing": SourceTimezoneMissing,
            "DeclaredTimezoneContradiction": DeclaredTimezoneContradiction,
            "SemanticDecoratorError": SemanticDecoratorError,
            "SemanticLoadError": SemanticLoadError,
            "SemanticRuntimeError": SemanticRuntimeError,
            "SemanticHelpTargetError": SemanticHelpTargetError,
            "SemanticContractScopeError": SemanticContractScopeError,
            "SemanticLoadFailed": SemanticLoadFailed,
        }
    )


ERROR_TYPES = _error_types()
