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
from marivo.analysis.observation.distinct_contracts import membership_part_authorities
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.operators.attribution_contracts import AttributePayload, AttributionSemantics
from marivo.analysis.operators.contracts import ComparePayload, DeltaSemantics


@dataclass(frozen=True, slots=True)
class ImplementationRegistration:
    operator_id: str
    input_roles: tuple[str, ...]
    source_adapter: str | None
    local_method: str | None
    version: int = 1
    source_versions: tuple[str, str] = ("1.5.3", "12.0.0")


_ROW_METHODS = frozenset(
    {
        "metric.where",
        "metric.metric",
        "metric.rank",
        "metric.limit",
        "delta.where",
        "delta.rank",
        "delta.limit",
        "attribution.where",
        "attribution.rank",
        "attribution.limit",
    }
)
_FOLD_METHODS = frozenset({"metric.aggregate", "metric.rollup"})


def implementation(dataset: LogicalDataset) -> ImplementationRegistration:
    root = dataset._root
    if not isinstance(root, LogicalRootHandle):
        raise compilation_error("a registered logical method", "invalid implementation root")
    registration = producer_contract(root.operator_id)
    if root.contract_versions != registration.versions:
        raise compilation_error("exact registered contract versions", "method version mismatch")
    roles = tuple(item.role for item in root.inputs)
    if dataset._inputs and root.operator_id.startswith(("metric.", "delta.", "attribution.")):
        consumer = dataset._registry.consumer(dataset._inputs[0], root.operator_id)
        if roles != consumer.input_roles:
            raise compilation_error("exact registered method input roles", "input role mismatch")
    entity_scoped_result = any(
        field.role_id == "entity_identity" for field in dataset.schema.columns
    ) and dataset.kind in ("delta", "attribution")
    source_membership = bool(membership_part_authorities(dataset.row_contract)) or (
        isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distinct_membership@v1"
    )
    # Source behavior is owned by the existing complete Observation lowerer.
    return ImplementationRegistration(
        root.operator_id,
        roles,
        "duckdb",
        root.operator_id
        if not entity_scoped_result
        and not source_membership
        and (
            (
                root.operator_id == "metric.compare"
                and dataset.row_contract.shape_id.local_shape_id != "entity"
            )
            or root.operator_id == "delta.attribute"
            or root.operator_id in _ROW_METHODS
            or (root.operator_id in _FOLD_METHODS and isinstance(root.payload, RetainedFoldPayload))
        )
        else None,
    )


def admit_local(dataset: LogicalDataset, registration: ImplementationRegistration) -> None:
    root = dataset._root
    if membership_part_authorities(dataset.row_contract) or (
        isinstance(root, LogicalRootHandle)
        and isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distinct_membership@v1"
    ):
        raise compilation_error(
            "source execution for exact distinct membership", "source-required membership state"
        )
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, ComparePayload):
        if (
            registration.local_method != "metric.compare"
            or len(dataset._inputs) != 2
            or root.payload.spec.output_row.shape_id.local_shape_id == "entity"
        ):
            raise compilation_error(
                "non-Entity registered two-operand comparison", "source-required comparison"
            )
        for value in (*dataset._inputs, dataset):
            admit_retained_rows(value)
        return
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, AttributePayload):
        if (
            registration.local_method != "delta.attribute"
            or len(dataset._inputs) != 1
            or any(field.role_id == "entity_identity" for field in dataset.schema.columns)
        ):
            raise compilation_error(
                "non-Entity retained-axis attribution", "source-required attribution"
            )
        for value in (*dataset._inputs, dataset):
            admit_retained_rows(value)
        return
    if dataset.kind in ("delta", "attribution") and any(
        field.role_id == "entity_identity" for field in dataset.schema.columns
    ):
        raise compilation_error(
            "source execution for Entity Delta or Attribution row operations",
            "source-required identity rows",
        )
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
    if not isinstance(
        semantics,
        (
            EntityPresentMetricSemantics,
            EntityReducedMetricSemantics,
            DeltaSemantics,
            AttributionSemantics,
        ),
    ):
        raise compilation_error(
            "Metric, Delta or Attribution rows with their exact retained computational roles",
            "unsupported retained family",
        )
