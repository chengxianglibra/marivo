"""Exact owner registrations for source support and bounded row continuations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventFunnelSemantics,
    EventJourneySemantics,
    EventPayload,
    EventSelectionPayload,
    EventTimeToEventPayload,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.event_attribution import (
    FunnelAttributePayload,
    FunnelAttributionSemantics,
)
from marivo.analysis.domains.event_comparison import FunnelComparePayload, FunnelDeltaSemantics
from marivo.analysis.domains.lifecycle import LifecyclePayload, LifecycleSemantics
from marivo.analysis.domains.lifecycle_reducers import (
    REDUCER_TYPES,
    LifecycleReducerPayload,
    LifecycleSelectionPayload,
)
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricPayload,
    RetainedRowsPayload,
    producer_contract,
)
from marivo.analysis.observation.fold_contracts import RetainedFoldPayload
from marivo.analysis.observation.private_parts import source_private_part_authorities
from marivo.analysis.operators.association_contracts import AssociationSemantics, CorrelatePayload
from marivo.analysis.operators.attribution_contracts import AttributePayload, AttributionSemantics
from marivo.analysis.operators.candidate_contracts import CandidatePayload, CandidateSemantics
from marivo.analysis.operators.contracts import ComparePayload, DeltaSemantics
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
from marivo.analysis.operators.forecast_contracts import ForecastPayload, ForecastSemantics

BackendName: TypeAlias = Literal["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"]
PreparationKind: TypeAlias = Literal["correlation", "distribution"]


@dataclass(frozen=True, slots=True)
class BackendExecution:
    """Pure execution declaration shared by the registered backend's methods."""

    backend: BackendName
    retained_import: bool


def backend_execution(backend: str) -> BackendExecution | None:
    """Resolve implemented backend capabilities without importing Runtime."""
    return {
        "duckdb": BackendExecution("duckdb", retained_import=True),
        "postgres": BackendExecution("postgres", retained_import=False),
    }.get(backend)


@dataclass(frozen=True, slots=True)
class BackendRegistration:
    """Exact source operations available for one already-validated method invocation."""

    backend: BackendName
    source: bool
    preparation: PreparationKind | None = None

    def __post_init__(self) -> None:
        if not self.source and self.preparation is None:
            raise DatasetRegistrationError(
                expected="an implemented source operation or preparation",
                received=f"{self.backend}: empty backend registration",
                repair="Declare an implemented source operation or preparation, or omit this backend entry.",
                location="dataset.implementation_registry",
            )


@dataclass(frozen=True, slots=True)
class ImplementationRegistration:
    operator_id: str
    input_roles: tuple[str, ...]
    backends: tuple[BackendRegistration, ...]
    local_method: str | None
    version: int = 1

    def __post_init__(self) -> None:
        names = tuple(item.backend for item in self.backends)
        if len(set(names)) != len(names):
            raise DatasetRegistrationError(
                expected="one registration per method/backend",
                received=f"{self.operator_id}: duplicate backend registration in {names!r}",
                repair="Merge each backend's source and preparation declarations into one entry for this method.",
                location="dataset.implementation_registry",
            )

    def for_backend(self, backend: str) -> BackendRegistration | None:
        return next((item for item in self.backends if item.backend == backend), None)


def supports_retained_import(backend: str) -> bool:
    """Read retained-import support from the concrete backend declaration."""
    implementation = backend_execution(backend)
    return implementation is not None and implementation.retained_import


_DUCKDB = (BackendRegistration("duckdb", source=True),)


_ROW_METHODS = frozenset(
    {
        "event.where",
        "lifecycle.where",
        "candidate.where",
        "candidate.rank",
        "candidate.limit",
        "forecast.where",
        "forecast.rank",
        "forecast.limit",
        "association.where",
        "association.rank",
        "association.limit",
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
    if isinstance(
        root.payload,
        (
            LifecyclePayload,
            LifecycleReducerPayload,
            LifecycleSelectionPayload,
            EventPayload,
            EventFunnelPayload,
            EventTimeToEventPayload,
            EventSelectionPayload,
        ),
    ):
        return ImplementationRegistration(root.operator_id, roles, _DUCKDB, None)
    if dataset._inputs and root.operator_id.startswith(
        (
            "event.",
            "lifecycle.",
            "metric.",
            "delta.",
            "attribution.",
            "association.",
            "forecast.",
            "candidate.",
            "discover.",
        )
    ):
        consumer = dataset._registry.consumer(dataset._inputs[0], root.operator_id)
        if roles != consumer.input_roles:
            raise compilation_error("exact registered method input roles", "input role mismatch")
    if isinstance(root.payload, (FunnelComparePayload, FunnelAttributePayload)):
        return ImplementationRegistration(root.operator_id, roles, _DUCKDB, root.operator_id)
    if isinstance(root.payload, DriverCandidatePayload):
        identity = any(f.role_id == "entity_identity" for f in dataset.schema.columns)
        return ImplementationRegistration(
            root.operator_id, roles, _DUCKDB, None if identity else root.operator_id
        )
    if isinstance(root.payload, CandidatePayload):
        if root.payload.spec.definition.objective == "entity_outliers":
            return ImplementationRegistration(root.operator_id, roles, _DUCKDB, None)
        return ImplementationRegistration(root.operator_id, roles, (), root.operator_id)
    if isinstance(root.payload, ForecastPayload):
        return ImplementationRegistration(root.operator_id, roles, (), "metric.forecast")
    if isinstance(root.payload, CorrelatePayload):
        return ImplementationRegistration(
            root.operator_id,
            roles,
            (
                BackendRegistration(
                    "duckdb",
                    source=root.payload.spec.semantics.method != "kendall",
                    preparation="correlation",
                ),
            ),
            "metric.correlate",
        )
    if (
        isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distribution_shapley@v1"
    ):
        return ImplementationRegistration(
            root.operator_id,
            roles,
            (BackendRegistration("duckdb", source=False, preparation="distribution"),),
            root.operator_id,
        )
    entity_scoped_result = any(
        field.role_id == "entity_identity" for field in dataset.schema.columns
    ) and dataset.kind in ("delta", "attribution", "candidate")
    source_private_state = bool(source_private_part_authorities(dataset.row_contract)) or (
        isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distinct_membership@v1"
    )
    from marivo.analysis.operators.postgres_support import supports as supports_postgres

    backends = (
        (*_DUCKDB, BackendRegistration("postgres", source=True))
        if supports_postgres(dataset)
        else _DUCKDB
    )
    # Source behavior is owned by the existing complete Observation lowerer.
    return ImplementationRegistration(
        root.operator_id,
        roles,
        backends,
        root.operator_id
        if not entity_scoped_result
        and not source_private_state
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
    if isinstance(root, LogicalRootHandle) and isinstance(
        root.payload, (FunnelComparePayload, FunnelAttributePayload)
    ):
        for operand in (*dataset._inputs, dataset):
            admit_retained_rows(operand)
        return
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, DriverCandidatePayload):
        expected = 3 if root.payload.spec.expanded_compare is not None else 1
        if (
            len(dataset._inputs) != expected
            or registration.local_method != root.operator_id
            or any(f.role_id == "entity_identity" for f in dataset.schema.columns)
        ):
            raise compilation_error(
                "complete non-Entity driver screening inputs", "source-required driver scope"
            )
        for operand in dataset._inputs:
            admit_retained_rows(operand)
        return
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, CandidatePayload):
        if root.payload.spec.definition.objective == "entity_outliers":
            raise compilation_error(
                "source-native Entity Candidate scoring", "source-required Entity identity rows"
            )
        if len(dataset._inputs) != 1 or registration.local_method != root.operator_id:
            raise compilation_error(
                "one registered time discovery input", "invalid Candidate invocation"
            )
        admit_retained_rows(dataset._inputs[0])
        return
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, ForecastPayload):
        if len(dataset._inputs) != 1 or registration.local_method != "metric.forecast":
            raise compilation_error(
                "one exact registered Forecast input", "invalid local invocation"
            )
        admit_retained_rows(dataset._inputs[0])
        return
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, CorrelatePayload):
        if root.payload.spec.semantics.input_shape == "entity":
            raise compilation_error(
                "source-private Entity pair preparation", "source-required Entity correlation"
            )
        if registration.local_method != "metric.correlate":
            raise compilation_error(
                "registered exact correlation method", "missing local implementation"
            )
        return
    if (
        isinstance(root, LogicalRootHandle)
        and isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distribution_shapley@v1"
    ):
        raise compilation_error(
            "the registered source-produced coalition preparation input",
            "source-required distribution preparation",
        )
    if source_private_part_authorities(dataset.row_contract) or (
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
    if dataset.kind in ("delta", "attribution", "candidate") and any(
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
    if str(dataset.row_contract.shape_id) == "population/entity-membership@v1":
        return
    if not isinstance(
        semantics,
        (
            LifecycleSemantics,
            *REDUCER_TYPES,
            EventJourneySemantics,
            FunnelDeltaSemantics,
            FunnelAttributionSemantics,
            EventFunnelSemantics,
            EventTimeToEventSemantics,
            EntityPresentMetricSemantics,
            EntityReducedMetricSemantics,
            DeltaSemantics,
            AttributionSemantics,
            AssociationSemantics,
            ForecastSemantics,
            CandidateSemantics,
        ),
    ):
        raise compilation_error(
            "Metric, Delta or Attribution rows with their exact retained computational roles",
            "unsupported retained family",
        )
