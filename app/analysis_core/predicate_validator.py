"""Legacy analysis_core.predicate_validator stubs — preserved for import compatibility.

Removed during OSI v2 migration.  See Task 7.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict


@dataclass(slots=True)
class PredicateRefWithUsage:
    ref: str
    required_usage: Any = ""  # PredicateUsage value
    usage: str = ""
    scope_expression: dict[str, Any] | None = None


@dataclass(slots=True)
class PredicateLayerRef:
    layer: str
    ref: str


@dataclass(slots=True)
class ResolvedAtom:
    ref: str
    usage: str
    operator: str = ""
    value: Any = None
    scope_expression: dict[str, Any] | None = None


class NormalizedPredicateAtom(TypedDict, total=False):
    ref: str
    usage: str
    operator: str
    value: Any
    scope_expression: dict[str, Any]


class NormalizedComponentPredicateInput(TypedDict, total=False):
    component_ref: str
    atoms: list[NormalizedPredicateAtom]


class NormalizedPredicateInput(TypedDict, total=False):
    metric_ref: str
    components: list[NormalizedComponentPredicateInput]


class ComponentLoweringInput(TypedDict, total=False):
    component_ref: str
    binding_ref: str
    atoms: list[NormalizedPredicateAtom]


class LoweringPrecheckDiagnostic(TypedDict, total=False):
    component_ref: str
    atom_ref: str
    code: str
    message: str


def validate_predicate_contracts(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def validate_request_scope(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def validate_predicate_conflicts(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def build_predicate_filter_lineage(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {}


def build_normalized_predicate_input(*_args: Any, **_kwargs: Any) -> NormalizedPredicateInput:
    return NormalizedPredicateInput()


def build_component_lowering_inputs(*_args: Any, **_kwargs: Any) -> list[ComponentLoweringInput]:
    return []


def run_lowering_precheck(*_args: Any, **_kwargs: Any) -> list[Any]:
    return []


def collect_component_fields(*_args: Any, **_kwargs: Any) -> list[str]:
    return []


def collect_layered_predicate_refs(*_args: Any, **_kwargs: Any) -> list[PredicateLayerRef]:
    return []
