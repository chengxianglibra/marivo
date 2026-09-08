"""Exact owner registrations for source support and bounded row continuations."""

from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricPayload,
    RetainedRowsPayload,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload


@dataclass(frozen=True, slots=True)
class ImplementationRegistration:
    operator_id: str
    input_roles: tuple[str, ...]
    source_adapter: str | None
    local_method: str | None
    version: int = 1
    source_versions: tuple[str, str] = ("1.5.3", "12.0.0")


_ROW_METHODS = frozenset({"metric.where", "metric.metric", "metric.rank", "metric.limit"})
_FOLD_METHODS = frozenset({"metric.aggregate", "metric.rollup"})


def implementation(dataset: LogicalDataset) -> ImplementationRegistration:
    root = dataset._root
    if not isinstance(root, LogicalRootHandle):
        raise compilation_error("a registered logical method", "invalid implementation root")
    registration = producer_contract(root.operator_id)
    if root.contract_versions != registration.versions:
        raise compilation_error("exact registered contract versions", "method version mismatch")
    roles = tuple(item.role for item in root.inputs)
    if dataset._inputs and root.operator_id.startswith("metric."):
        consumer = dataset._registry.consumer(dataset._inputs[0], root.operator_id)
        if roles != consumer.input_roles:
            raise compilation_error("exact registered method input roles", "input role mismatch")
    # Source behavior is owned by the existing complete Observation lowerer.
    return ImplementationRegistration(
        root.operator_id,
        roles,
        "duckdb",
        root.operator_id
        if root.operator_id in _ROW_METHODS
        or (root.operator_id in _FOLD_METHODS and isinstance(root.payload, RetainedFoldPayload))
        else None,
    )


def admit_local(dataset: LogicalDataset, registration: ImplementationRegistration) -> None:
    root = dataset._root
    if (
        registration.local_method not in (_ROW_METHODS | _FOLD_METHODS)
        or not isinstance(root, LogicalRootHandle)
        or not isinstance(root.payload, (MetricPayload, RetainedRowsPayload, RetainedFoldPayload))
        or len(dataset._inputs) != 1
    ):
        raise compilation_error("an exact registered pandas input role", "source-required method")
    for value in (*dataset._inputs, dataset):
        admit_retained_rows(value)


def admit_retained_rows(dataset: Dataset) -> None:
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error(
            "Metric rows with their exact retained computational roles",
            "unsupported retained family",
        )
