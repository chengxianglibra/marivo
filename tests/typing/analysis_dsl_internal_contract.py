"""Static checks for the inactive private Analysis DSL contract seam."""

from __future__ import annotations

from typing import Literal

from typing_extensions import assert_type

from marivo.analysis.compiler.normalize import InputClassification
from marivo.analysis.datasets.descriptors import (
    AnalysisDomain,
    QuantityState,
    _ObservedQuantity,
    _RowStatisticQuantity,
)
from marivo.analysis.datasets.handles import LogicalRootHandle, _RunNodeBindings
from marivo.analysis.operators.registry import MethodContract, MethodImplementation


def _closed_contracts(
    domain: AnalysisDomain,
    quantity: QuantityState,
    classification: InputClassification,
    method: MethodContract,
    implementation: MethodImplementation,
) -> None:
    assert_type(domain.kind, Literal["entity", "group", "singleton"])
    assert_type(quantity.kind, Literal["observed", "row_statistic", "difference"])
    assert_type(classification.kind, Literal["source", "artifact", "mixed"])
    assert_type(method.version, int)
    assert_type(implementation.route, Literal["source", "source_numeric", "local"])


def _requires_observed(value: _ObservedQuantity) -> None:
    pass


def _state_roles_are_nominal(statistic: _RowStatisticQuantity) -> None:
    _requires_observed(statistic)  # type: ignore[arg-type]  # negative static contract


def _run_binding_is_local_and_typed(root: LogicalRootHandle) -> None:
    bindings = _RunNodeBindings[str](root.session_id)
    assert_type(bindings.bind(root, "compiled-node"), str)
