"""Exact owner registrations for source support and bounded row continuations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.base import Dataset, LogicalDataset
from marivo.analysis.datasets.descriptors import _is_stable_identifier
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.domains.contracts import (
    EventDefinition,
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
from marivo.analysis.event import FirstPerSubject
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
from marivo.datasource.ir import TableSourceIR

BackendName: TypeAlias = Literal["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"]
PreparationKind: TypeAlias = Literal["correlation", "distribution"]


def legacy_source_migration_stage(operator_id: str) -> Literal[6, 7, 8] | None:
    """Return the remaining domain owner, or None for a retired R5 route."""
    if operator_id.startswith(("session.events.", "event.", "session.lifecycle.", "lifecycle.")):
        return 7
    if operator_id.startswith(("delta.attribute", "attribution.", "delta.", "metric.compare")):
        return 6
    if operator_id.startswith(
        (
            "candidate.",
            "forecast.",
            "association.",
            "discover.",
            "metric.forecast",
            "metric.correlate",
        )
    ):
        return 8
    return None


@dataclass(frozen=True, slots=True)
class BackendExecution:
    """Pure execution declaration shared by the registered backend's methods."""

    backend: BackendName
    retained_import: bool


def backend_execution(backend: str) -> BackendExecution | None:
    """Resolve implemented backend capabilities without importing Runtime."""
    from marivo.datasource.engines import SUPPORTED_BACKEND_TYPES

    if backend not in SUPPORTED_BACKEND_TYPES:
        return None
    # The datasource registry validates the string; this cast preserves the
    # closed backend type for Analysis's method-specific declarations.
    return BackendExecution(cast("BackendName", backend), retained_import=backend == "duckdb")


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


MethodKind: TypeAlias = Literal[
    "domain",
    "observed",
    "row_statistic",
    "difference",
    "numeric_relation",
    "predicate",
    "association",
]
MethodRoute: TypeAlias = Literal["source", "source_numeric", "local"]
MethodBackend: TypeAlias = BackendName | Literal["pandas"]
MethodDomain: TypeAlias = Literal["entity", "group", "singleton"]
CoreCapability: TypeAlias = Literal[
    "bind_project",
    "domain_correspondence",
    "cell_calculation",
    "current_row_state",
    "original_state_reduction",
    "part_transport",
]
_METHOD_KINDS = frozenset(
    {
        "domain",
        "observed",
        "row_statistic",
        "difference",
        "numeric_relation",
        "predicate",
        "association",
    }
)
_CAPABILITIES = frozenset(
    {
        "bind_project",
        "domain_correspondence",
        "cell_calculation",
        "current_row_state",
        "original_state_reduction",
        "part_transport",
    }
)
_BACKENDS = frozenset({"duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse", "pandas"})
_DOMAINS = frozenset({"entity", "group", "singleton"})
_UNIT_POLICIES = frozenset({"preserve", "count", "ratio", "mean", "difference", "coefficient"})
_CELL_POLICIES = frozenset({"strict", "count_all", "total_is_defined", "spearman_pairs"})
_NUMERIC_POLICIES = frozenset(
    {"none", "int64_checked", "float64_finite", "int64_or_float64", "pair_ranks"}
)
_PART_EFFECTS = frozenset({"preserve", "build_current", "merge_original", "transport", "discard"})


def _method_error(expected: str, received: str) -> DatasetRegistrationError:
    return DatasetRegistrationError(
        expected=expected,
        received=received,
        repair="Register one exact versioned method contract and only qualified implementations.",
        location="dataset.method_registry",
    )


@dataclass(frozen=True, slots=True)
class MethodContract:
    """Inactive first-round method semantics, independent of execution routes."""

    method_id: str
    version: int
    input_kinds: tuple[MethodKind, ...]
    input_domains: tuple[MethodDomain, ...]
    output_kind: MethodKind
    domain_policy: Literal["same", "mapped", "new"]
    unit_policy: Literal["preserve", "count", "ratio", "mean", "difference", "coefficient"]
    cell_policy: Literal["strict", "count_all", "total_is_defined", "spearman_pairs"]
    numeric_policy: Literal[
        "none", "int64_checked", "float64_finite", "int64_or_float64", "pair_ranks"
    ]
    capabilities: tuple[CoreCapability, ...]
    part_effect: Literal["preserve", "build_current", "merge_original", "transport", "discard"]
    required_parts: tuple[str, ...]
    required_checks: tuple[str, ...]
    continuations: tuple[str, ...]
    cell_reasons: tuple[tuple[Literal["null", "undefined", "unknown"], tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        if (
            not _is_stable_identifier(self.method_id)
            or type(self.version) is not int
            or self.version < 1
        ):
            raise _method_error("stable method id and positive version", "invalid identity")
        if (
            type(self.input_kinds) is not tuple
            or not self.input_kinds
            or any(
                type(values) is not tuple or len(set(values)) != len(values)
                for values in (
                    self.input_domains,
                    self.capabilities,
                    self.required_parts,
                    self.required_checks,
                    self.continuations,
                )
            )
        ):
            raise _method_error(
                "nonempty inputs and unique immutable contract facts", "invalid facts"
            )
        if (
            any(kind not in _METHOD_KINDS for kind in self.input_kinds)
            or not self.input_domains
            or any(domain not in _DOMAINS for domain in self.input_domains)
            or self.output_kind not in _METHOD_KINDS
            or not self.capabilities
            or any(capability not in _CAPABILITIES for capability in self.capabilities)
            or self.unit_policy not in _UNIT_POLICIES
            or self.cell_policy not in _CELL_POLICIES
            or self.numeric_policy not in _NUMERIC_POLICIES
            or self.part_effect not in _PART_EFFECTS
            or any(
                not _is_stable_identifier(value)
                for values in (self.required_parts, self.required_checks, self.continuations)
                for value in values
            )
        ):
            raise _method_error("closed method kind, policy and capability facts", "invalid policy")
        if self.domain_policy not in ("same", "mapped", "new"):
            raise _method_error("closed domain policy", "invalid domain policy")
        if type(self.cell_reasons) is not tuple or any(
            type(entry) is not tuple or len(entry) != 2 for entry in self.cell_reasons
        ):
            raise _method_error("closed Cell reasons owned by each method", "invalid reasons")
        if any(
            type(entry[0]) is not str
            or type(entry[1]) is not tuple
            or any(type(reason) is not str for reason in entry[1])
            for entry in self.cell_reasons
        ):
            raise _method_error("closed Cell reasons owned by each method", "invalid reasons")
        if len({entry[0] for entry in self.cell_reasons}) != len(self.cell_reasons) or any(
            entry[0] not in ("null", "undefined", "unknown")
            or type(entry[1]) is not tuple
            or not entry[1]
            or len(set(entry[1])) != len(entry[1])
            or any(not _is_stable_identifier(reason) for reason in entry[1])
            for entry in self.cell_reasons
        ):
            raise _method_error("closed Cell reasons owned by each method", "invalid reasons")
        if (
            (
                "current_row_state" in self.capabilities
                and (self.output_kind != "row_statistic" or self.part_effect != "build_current")
            )
            or (
                "original_state_reduction" in self.capabilities
                and (
                    self.input_kinds != ("observed",)
                    or self.output_kind != "observed"
                    or self.part_effect != "merge_original"
                    or not self.required_parts
                )
            )
            or (self.cell_policy == "total_is_defined" and self.output_kind != "predicate")
            or (
                self.part_effect == "build_current" and "current_row_state" not in self.capabilities
            )
            or (
                self.part_effect == "merge_original"
                and "original_state_reduction" not in self.capabilities
            )
            or (self.cell_policy == "spearman_pairs" and self.numeric_policy != "pair_ranks")
            or (self.numeric_policy == "pair_ranks" and self.cell_policy != "spearman_pairs")
            or (
                "cell_calculation" in self.capabilities
                and ("domain" in self.input_kinds or self.output_kind == "domain")
            )
            or (
                "bind_project" in self.capabilities
                and (
                    self.input_kinds != ("domain",)
                    or self.output_kind not in ("domain", "observed", "row_statistic")
                )
            )
            or (
                "domain_correspondence" in self.capabilities
                and (
                    any(kind != "domain" for kind in self.input_kinds)
                    or self.output_kind != "domain"
                )
            )
        ):
            raise _method_error("capability-specific input, output and part effect", "invalid rule")


@dataclass(frozen=True, slots=True)
class MethodImplementation:
    """A route's explicit qualification; no executable route is published by T2."""

    method_id: str
    version: int
    route: MethodRoute
    backend: MethodBackend
    input_domains: tuple[MethodDomain, ...]
    logical_types: tuple[str, ...]
    supported_parts: tuple[str, ...]
    supported_checks: tuple[str, ...]
    batch_mode: Literal["stream", "complete"]
    resource_owner: Literal["producer", "caller"]

    def __post_init__(self) -> None:
        if (
            not _is_stable_identifier(self.method_id)
            or type(self.version) is not int
            or self.version < 1
            or self.route not in ("source", "source_numeric", "local")
            or self.backend not in _BACKENDS
            or (self.route in ("source", "source_numeric") and self.backend == "pandas")
            or (self.route == "local" and self.backend != "pandas")
            or (
                self.route in ("source", "source_numeric")
                and (self.batch_mode, self.resource_owner) != ("stream", "producer")
            )
            or (
                self.route == "local"
                and (self.batch_mode, self.resource_owner) != ("complete", "caller")
            )
            or not self.logical_types
            or not self.input_domains
            or any(domain not in _DOMAINS for domain in self.input_domains)
            or len(set(self.input_domains)) != len(self.input_domains)
            or len(set(self.logical_types)) != len(self.logical_types)
            or len(set(self.supported_parts)) != len(self.supported_parts)
            or len(set(self.supported_checks)) != len(self.supported_checks)
        ):
            raise _method_error(
                "one typed route with matching batch and resource owner", "invalid route"
            )


@dataclass(frozen=True, slots=True)
class MethodRegistration:
    """One semantic owner with independently qualified implementation routes."""

    contract: MethodContract
    implementations: tuple[MethodImplementation, ...]

    def __post_init__(self) -> None:
        if type(self.contract) is not MethodContract or type(self.implementations) is not tuple:
            raise _method_error(
                "one immutable method and implementation tuple", "invalid registration"
            )
        keys: set[tuple[str, str]] = set()
        for implementation in self.implementations:
            if type(implementation) is not MethodImplementation or (
                implementation.method_id,
                implementation.version,
            ) != (self.contract.method_id, self.contract.version):
                raise _method_error("matching method id and version", "mismatched implementation")
            key = (implementation.route, implementation.backend)
            if key in keys:
                raise _method_error(
                    "one qualification per route/backend", "duplicate implementation"
                )
            keys.add(key)
            if not set(self.contract.required_checks).issubset(implementation.supported_checks):
                raise _method_error("implementation of every required check", "missing check")
            if not set(self.contract.required_parts).issubset(implementation.supported_parts):
                raise _method_error("implementation of every required part", "missing part")
            if not set(implementation.input_domains).issubset(self.contract.input_domains):
                raise _method_error("method-admitted implementation domains", "invalid domain")

    def require_route(
        self, route: MethodRoute, backend: MethodBackend, domain: MethodDomain, logical_type: str
    ) -> MethodImplementation:
        """Select one exact qualified implementation or reject the requested route."""
        for implementation in self.implementations:
            if (
                implementation.route == route
                and implementation.backend == backend
                and domain in implementation.input_domains
                and logical_type in implementation.logical_types
            ):
                return implementation
        raise _method_error("qualified exact method route/type", "unsupported route")


def supports_retained_import(backend: str) -> bool:
    """Read retained-import support from the concrete backend declaration."""
    implementation = backend_execution(backend)
    return implementation is not None and implementation.retained_import


_DUCKDB = (BackendRegistration("duckdb", source=True),)


def _postgres_event_reason(definition: EventDefinition) -> str | None:
    """Keep the first native Event bundle limited to its proven identity shape."""
    if (
        len(definition.steps) not in (2, 3)
        or definition.sampling_authority != "exact"
        or definition.entity.version is not None
        or not isinstance(definition.entity.source, TableSourceIR)
        or any(
            logical not in ("unknown", "int64")
            for _, logical in definition.entity.identity_signature
        )
        or any(
            step.source.version is not None
            or not isinstance(step.source.source, TableSourceIR)
            or any(axis.logical_type not in ("unknown", "int64") for axis in step.identity)
            for step in definition.steps
        )
    ):
        return (
            "PostgreSQL Event matching currently requires two or three steps with "
            "first-per-subject or every-start matching; "
            "all shapes require exact int64 subject and occurrence identities and "
            "unversioned table sources"
        )
    return None


def _clickhouse_event_reason(definition: EventDefinition) -> str | None:
    if len(definition.steps) != 2 or _postgres_event_reason(definition) is not None:
        return (
            "ClickHouse Event matching currently requires two steps with exact int64 "
            "subject and occurrence identities and unversioned table sources"
        )
    return None


def _trino_event_reason(definition: EventDefinition) -> str | None:
    if len(definition.steps) != 2 or _postgres_event_reason(definition) is not None:
        return (
            "Trino Event matching currently requires two steps with exact int64 "
            "subject and occurrence identities and unversioned table sources"
        )
    return None


def _lifecycle_reason(definition: EventDefinition, backend: BackendName = "postgres") -> str | None:
    if len(definition.steps) != 2 or _postgres_event_reason(definition) is not None:
        return (
            f"{backend} Lifecycle replay currently requires two trigger Events with exact "
            "int64 subject and occurrence identities and unversioned table sources"
        )
    if backend in ("trino", "clickhouse") and (
        len(definition.entity.identity_signature) != 1
        or any(len(step.identity) != 1 for step in definition.steps)
    ):
        return f"{backend} Lifecycle currently qualifies one int64 component per subject and occurrence identity"
    return None


def _postgres_continuation_reason(dataset: LogicalDataset) -> str | None:
    if len(dataset._inputs) == 1:
        incoming = dataset._inputs[0]
        if isinstance(incoming, LogicalDataset) and isinstance(incoming._root, LogicalRootHandle):
            payload = incoming._root.payload
            if isinstance(payload, EventPayload):
                return _postgres_event_reason(payload.definition)
            if isinstance(payload, LifecyclePayload):
                return _lifecycle_reason(payload.definition)
    return "this PostgreSQL continuation requires one directly admitted Event or Lifecycle source"


def _trino_continuation_reason(dataset: LogicalDataset) -> str | None:
    root = dataset._root
    if (
        isinstance(root, LogicalRootHandle)
        and isinstance(root.payload, EventFunnelPayload)
        and root.payload.axes
    ):
        return "Trino grouped Event funnel reconciliation exceeds the qualified stage budget"
    if len(dataset._inputs) == 1:
        incoming = dataset._inputs[0]
        if isinstance(incoming, LogicalDataset) and isinstance(incoming._root, LogicalRootHandle):
            payload = incoming._root.payload
            if isinstance(payload, EventPayload):
                if (
                    isinstance(root, LogicalRootHandle)
                    and isinstance(root.payload, EventTimeToEventPayload)
                    and not isinstance(payload.definition.matching, FirstPerSubject)
                ):
                    return "direct Trino time-to-event currently requires first_per_subject"
                return _trino_event_reason(payload.definition)
    return "this Trino continuation requires one directly admitted Event source"


def _source_admissions() -> dict[BackendName, Callable[[LogicalDataset], str | None]]:
    """Keep eligibility and diagnostics on the same concrete backend owner."""
    from marivo.analysis.operators.clickhouse_support import unsupported_reason as clickhouse_reason
    from marivo.analysis.operators.mysql_support import unsupported_reason as mysql_reason
    from marivo.analysis.operators.postgres_support import unsupported_reason as postgres_reason
    from marivo.analysis.operators.sqlite_support import unsupported_reason as sqlite_reason
    from marivo.analysis.operators.trino_support import unsupported_reason as trino_reason

    return {
        "postgres": postgres_reason,
        "mysql": mysql_reason,
        "sqlite": sqlite_reason,
        "clickhouse": clickhouse_reason,
        "trino": trino_reason,
    }


def source_unsupported_reason(dataset: LogicalDataset, backend: str) -> str | None:
    """Resolve a source admission diagnostic through its concrete backend owner."""
    execution = backend_execution(backend)
    if execution is None:
        return None
    root = dataset._root
    if execution.backend != "duckdb" and isinstance(root, LogicalRootHandle):
        if isinstance(root.payload, EventPayload):
            if execution.backend == "postgres":
                return _postgres_event_reason(root.payload.definition)
            if execution.backend == "clickhouse":
                return _clickhouse_event_reason(root.payload.definition)
            if execution.backend == "trino":
                return _trino_event_reason(root.payload.definition)
            return (
                "Event matching requires source-side occurrence identity, governed-order "
                "assignment, and complete assertions; this backend has no validated "
                "matching lowering"
            )
        if isinstance(root.payload, LifecyclePayload):
            if execution.backend in ("postgres", "trino", "clickhouse"):
                return _lifecycle_reason(root.payload.definition, execution.backend)
            return (
                "Lifecycle replay requires validated source-side recursive replay and "
                "equal-time confluence proof; this "
                "read-only backend has no admitted implementation"
            )
        if isinstance(
            root.payload,
            (
                EventFunnelPayload,
                EventTimeToEventPayload,
                EventSelectionPayload,
                LifecycleReducerPayload,
                LifecycleSelectionPayload,
            ),
        ):
            if execution.backend == "postgres":
                return _postgres_continuation_reason(dataset)
            if execution.backend == "trino" and isinstance(
                root.payload, (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload)
            ):
                return _trino_continuation_reason(dataset)
            return (
                "this Event/Lifecycle continuation requires an admitted source-private "
                "implementation or complete retained input authority"
            )
    reason = _source_admissions().get(execution.backend)
    return None if reason is None else reason(dataset)


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
    if isinstance(root.payload, EventPayload):
        postgres_event: tuple[BackendRegistration, ...] = (
            (BackendRegistration("postgres", source=True),)
            if _postgres_event_reason(root.payload.definition) is None
            else ()
        )
        clickhouse_event: tuple[BackendRegistration, ...] = (
            (BackendRegistration("clickhouse", source=True),)
            if _clickhouse_event_reason(root.payload.definition) is None
            else ()
        )
        trino_event: tuple[BackendRegistration, ...] = (
            (BackendRegistration("trino", source=True),)
            if _trino_event_reason(root.payload.definition) is None
            else ()
        )
        return ImplementationRegistration(
            root.operator_id,
            roles,
            (*_DUCKDB, *postgres_event, *clickhouse_event, *trino_event),
            None,
        )
    if isinstance(root.payload, LifecyclePayload):
        source_lifecycle = tuple(
            BackendRegistration(backend, source=True)
            for backend in ("postgres", "trino", "clickhouse")
            if _lifecycle_reason(root.payload.definition, backend) is None
        )
        return ImplementationRegistration(
            root.operator_id, roles, (*_DUCKDB, *source_lifecycle), None
        )
    if isinstance(
        root.payload,
        (
            LifecyclePayload,
            LifecycleReducerPayload,
            LifecycleSelectionPayload,
            EventFunnelPayload,
            EventTimeToEventPayload,
            EventSelectionPayload,
        ),
    ):
        postgres_continuation: tuple[BackendRegistration, ...] = (
            (BackendRegistration("postgres", source=True),)
            if _postgres_continuation_reason(dataset) is None
            else ()
        )
        trino_continuation: tuple[BackendRegistration, ...] = (
            (BackendRegistration("trino", source=True),)
            if isinstance(
                root.payload, (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload)
            )
            and _trino_continuation_reason(dataset) is None
            else ()
        )
        return ImplementationRegistration(
            root.operator_id,
            roles,
            (*_DUCKDB, *postgres_continuation, *trino_continuation),
            None,
        )
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
        remote = tuple(
            BackendRegistration(name, source=True)
            for name, reason in _source_admissions().items()
            if reason(dataset) is None
        )
        return ImplementationRegistration(
            root.operator_id,
            roles,
            (
                BackendRegistration(
                    "duckdb",
                    source=root.payload.spec.semantics.method != "kendall",
                    preparation="correlation",
                ),
                *remote,
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
    backends = (
        *_DUCKDB,
        *(
            BackendRegistration(name, source=True)
            for name, reason in _source_admissions().items()
            if reason(dataset) is None
        ),
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
