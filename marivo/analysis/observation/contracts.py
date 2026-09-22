"""Observation-owned immutable arguments, row contracts and private family assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TimeScope
from marivo.analysis.datasets.base import Dataset, DatasetOwner, MaterializedDataset, _dataset_repr
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFamilyRowSemantics,
    DatasetField,
    DatasetFieldId,
    DatasetRowContract,
    DatasetRowSetContract,
    _canonical_digest,
    _catalog_identity,
    _CatalogFieldIdentity,
    _complete_from_schema,
    _deferred_type,
    _entity_identity,
    _EntityFieldIdentity,
    _GeneratedFieldIdentity,
    _is_stable_identifier,
    _keyed_cardinality,
    _make_field,
    _make_field_id,
    _make_row_contract,
    _make_row_set_contract,
    _make_schema,
    _make_shape_id,
    _runtime_metric_identity,
    _RuntimeMetricFieldIdentity,
    _singleton_cardinality,
    _StableIdRegistry,
    _unknown_row_bound,
    _unordered_ordering,
)
from marivo.analysis.datasets.fields import DatasetFieldRef, validate_field_ref
from marivo.analysis.datasets.handles import CanonicalValue, _LogicalNodePayload
from marivo.analysis.datasets.registry import (
    ConsumerRegistration,
    DatasetFamilyRegistration,
    DatasetFamilyRegistry,
)
from marivo.analysis.datasets.state import MaterializedDatasetState, _validate_materialized_state
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.analysis.observation.fold_contracts import (
    DistinctMembershipAuthorityV1,
    DistributionAuthorityV1,
    MetricFoldAuthorityV1,
    RetainedFoldPayload,
    decode_fold_authority,
    fold_state_names,
    make_fold_authority,
)
from marivo.analysis.observation.predicates import BoundPredicate, PredicateField
from marivo.analysis.observation.temporal import ReportTimeAuthority
from marivo.datasource.ir import (
    CsvSourceIR,
    EntitySourceIR,
    JsonQueryParamValue,
    JsonSourceIR,
    ParquetSourceIR,
    SourceParamIR,
    TableSourceIR,
)
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    TimeDimensionKind,
    _create_ref,
)
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic._quantile import QuantileMetricInput
from marivo.semantic.catalog import (
    DimensionEntry,
    EntityEntry,
    MetricEntry,
    TimeDimensionEntry,
)
from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    SemanticParse,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)
from marivo.semantic.metric_graph import CatalogMetricIdentity, TargetMetricContract
from marivo.semantic.metric_graph_lowering import dependency_digest
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.domains.event import LogicalEventDataset, MaterializedEventDataset
    from marivo.analysis.domains.lifecycle import (
        LogicalLifecycleDataset,
        MaterializedLifecycleDataset,
    )
    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
    from marivo.analysis.observation.population import (
        LogicalPopulationDataset,
        MaterializedPopulationDataset,
    )
    from marivo.analysis.observation.source_bindings import (
        BoundSourceParametersV1,
        SourceBindingScopes,
    )
    from marivo.analysis.operators.association import (
        LogicalAssociationDataset,
        MaterializedAssociationDataset,
    )
    from marivo.analysis.operators.attribution import (
        LogicalAttributionDataset,
        MaterializedAttributionDataset,
    )
    from marivo.analysis.operators.candidate_contracts import CandidateDefinition
    from marivo.analysis.operators.candidate_dataset import (
        LogicalCandidateDataset,
        MaterializedCandidateDataset,
    )
    from marivo.analysis.operators.delta import LogicalDeltaDataset, MaterializedDeltaDataset
    from marivo.analysis.operators.driver_contracts import DriverCandidateDefinition
    from marivo.analysis.operators.forecast_dataset import (
        LogicalForecastDataset,
        MaterializedForecastDataset,
    )

EntityInput: TypeAlias = Ref[EntityKind] | EntityEntry
DimensionInput: TypeAlias = Ref[DimensionKind] | DimensionEntry
TimeDimensionInput: TypeAlias = Ref[TimeDimensionKind] | TimeDimensionEntry

MetricInput: TypeAlias = Ref[MetricKind] | MetricEntry | RuntimeMetricExpr | QuantileMetricInput
METRIC_SHAPES = (
    "entity",
    "entity-dimension",
    "entity-time",
    "entity-dimension-time",
    "scalar",
    "dimension",
    "time",
    "dimension-time",
)
IDENTITY_FIELD_ID = _make_field_id("identity.entity_identity@v1")


@dataclass(frozen=True, slots=True)
class ObservationProducerContract:
    """One pure owner registration shared by construction and materialization."""

    producer_id: str
    contract_stem: str

    @property
    def quality_id(self) -> str:
        return f"{self.contract_stem}_quality"

    @property
    def validation_id(self) -> str:
        return f"{self.contract_stem}_validation@v1"

    @property
    def evidence_id(self) -> str:
        return f"{self.contract_stem}_evidence"

    @property
    def retained_contract_ids(self) -> tuple[str, ...]:
        if self.producer_id == "session.lifecycle.replay":
            from marivo.analysis.domains.lifecycle import ROLES

            return ROLES
        if self.producer_id == "delta.funnel_attribute":
            return ("event_funnel.additive_components",)
        if self.producer_id.startswith(
            (
                "discover.",
                "candidate.",
                "session.events.",
                "event.",
                "session.lifecycle.",
                "lifecycle.",
            )
        ):
            return ()
        if self.contract_stem.startswith(("association", "forecast")):
            return ()
        if self.contract_stem.startswith("attribution"):
            return ("attribution.reconciliation",)
        if self.producer_id == "metric.compare" or self.producer_id.startswith("delta."):
            return (
                "delta.sufficient_components",
                "delta.distinct_membership",
                "delta.distribution",
            )
        return (
            ("metric.sufficient_components", "metric.distinct_membership", "metric.distribution")
            if self.producer_id != "metric.compare"
            and (self.producer_id.startswith("metric.") or self.producer_id == "session.observe")
            else ()
        )

    @property
    def versions(self) -> tuple[tuple[str, str], ...]:
        common = (
            (self.producer_id, "v1"),
            ("dataset_structure_quality", "v1"),
            (self.quality_id, "v1"),
            (self.validation_id, "v1"),
            (self.evidence_id, "v1"),
        )
        if self.producer_id == "event.compare":
            return (
                *common,
                ("event_funnel_checkpoint_scope", "v1"),
                ("funnel_delta_finding", "v1"),
                ("bounded_algebraic_findings", "v1"),
            )
        if self.producer_id == "delta.funnel_attribute":
            return (
                *common,
                ("contribution_finding", "v1"),
                ("bounded_algebraic_findings", "v1"),
                ("event_funnel.additive_components", "v1"),
            )
        if self.producer_id.startswith(
            (
                "discover.",
                "candidate.",
                "session.events.",
                "event.",
                "session.lifecycle.",
                "lifecycle.",
            )
        ):
            return (*common, ("none", "v1"), ("zero_findings", "v1"))
        if self.contract_stem.startswith("forecast"):
            return (
                *common,
                ("forecast_point_finding", "v1"),
                ("forecast_point_findings", "v1"),
            )
        if self.contract_stem.startswith("association"):
            return (
                *common,
                ("association_finding", "v1"),
                ("association_findings", "v1"),
            )
        if self.contract_stem.startswith("attribution"):
            producing = self.producer_id in ("delta.attribute", "delta.attribute_expanded")
            return (
                *common,
                ("contribution_finding" if producing else "none", "v1"),
                ("contribution_findings" if producing else "zero_findings", "v1"),
                ("attribution.reconciliation", "v1"),
            )
        comparison = self.producer_id == "metric.compare" or self.producer_id.startswith("delta.")
        return (
            *common,
            ("delta_finding" if comparison else "none", "v1"),
            ("delta_findings" if comparison else "zero_findings", "v1"),
            (
                "delta.sufficient_components"
                if comparison
                else "metric.sufficient_components"
                if self.producer_id.startswith("metric.") or self.producer_id == "session.observe"
                else "population_identity",
                "v1",
            ),
            *(
                (
                    (
                        "delta.distinct_membership" if comparison else "metric.distinct_membership",
                        "v1",
                    ),
                    ("delta.distribution" if comparison else "metric.distribution", "v1"),
                )
                if comparison
                or self.producer_id.startswith("metric.")
                or self.producer_id == "session.observe"
                else ()
            ),
        )


_PRODUCER_CONTRACTS = (
    ObservationProducerContract("session.lifecycle.replay", "lifecycle_history"),
    ObservationProducerContract("session.events.match", "event_journey"),
    ObservationProducerContract("event.compare", "funnel_delta"),
    ObservationProducerContract("delta.funnel_attribute", "funnel_attribution"),
    *(
        ObservationProducerContract(f"lifecycle.{name}", f"lifecycle_{name}")
        for name in ("distribution", "transitions", "dwell", "violations", "where")
    ),
    ObservationProducerContract("lifecycle.select_subjects", "subject_selection"),
    ObservationProducerContract("event.funnel", "event_funnel"),
    ObservationProducerContract("event.time_to_event", "event_time_to_event"),
    ObservationProducerContract("event.select_subjects", "subject_selection"),
    ObservationProducerContract("event.where", "event_filter"),
    ObservationProducerContract("discover.point_anomalies", "point_anomalies"),
    ObservationProducerContract("discover.interesting_windows", "interesting_windows"),
    ObservationProducerContract("discover.period_shifts", "period_shifts"),
    ObservationProducerContract("discover.entity_outliers", "entity_outliers"),
    ObservationProducerContract("discover.driver_axes", "driver_axes"),
    ObservationProducerContract("discover.driver_axes_expanded", "driver_axes"),
    ObservationProducerContract("candidate.where", "candidate_filter"),
    ObservationProducerContract("candidate.rank", "candidate_rank"),
    ObservationProducerContract("candidate.limit", "candidate_limit"),
    ObservationProducerContract("session.population", "population_root"),
    ObservationProducerContract("population.where", "population_filter"),
    ObservationProducerContract("session.observe", "metric_observation"),
    ObservationProducerContract("metric.where", "metric_filter"),
    ObservationProducerContract("metric.metric", "metric_projection"),
    ObservationProducerContract("metric.with_dimensions", "metric_coordinate"),
    ObservationProducerContract("metric.expand_axes", "metric_coordinate"),
    ObservationProducerContract("metric.with_time_axis", "metric_coordinate"),
    ObservationProducerContract("metric.aggregate", "metric_aggregation"),
    ObservationProducerContract("metric.rollup", "metric_rollup"),
    ObservationProducerContract("metric.rank", "metric_rank"),
    ObservationProducerContract("metric.limit", "metric_limit"),
    ObservationProducerContract("metric.forecast", "forecast"),
    ObservationProducerContract("forecast.where", "forecast_filter"),
    ObservationProducerContract("forecast.rank", "forecast_rank"),
    ObservationProducerContract("forecast.limit", "forecast_limit"),
    ObservationProducerContract("metric.correlate", "association"),
    ObservationProducerContract("association.where", "association_filter"),
    ObservationProducerContract("association.rank", "association_rank"),
    ObservationProducerContract("association.limit", "association_limit"),
    ObservationProducerContract("metric.compare", "delta"),
    ObservationProducerContract("delta.where", "delta_filter"),
    ObservationProducerContract("delta.rank", "delta_rank"),
    ObservationProducerContract("delta.limit", "delta_limit"),
    ObservationProducerContract("delta.attribute", "attribution"),
    ObservationProducerContract("delta.attribute_expanded", "attribution"),
    ObservationProducerContract("attribution.where", "attribution_filter"),
    ObservationProducerContract("attribution.rank", "attribution_rank"),
    ObservationProducerContract("attribution.limit", "attribution_limit"),
)


def producer_contract(operator_id: str) -> ObservationProducerContract:
    for registration in _PRODUCER_CONTRACTS:
        if registration.producer_id == operator_id:
            return registration
    raise construction_error("an exact registered Observation producer", "unsupported producer")


class ObservationActionPort(Protocol):
    """Required execution/read owner; definition construction never invokes this port."""

    def execute_lifecycle(
        self, dataset: LogicalLifecycleDataset
    ) -> MaterializedLifecycleDataset: ...

    def execute_event(self, dataset: LogicalEventDataset) -> MaterializedEventDataset: ...

    def execute_population(
        self, dataset: LogicalPopulationDataset
    ) -> MaterializedPopulationDataset: ...
    def execute_metric(self, dataset: LogicalMetricDataset) -> MaterializedMetricDataset: ...
    def execute_candidate(
        self, dataset: LogicalCandidateDataset
    ) -> MaterializedCandidateDataset: ...
    def execute_forecast(self, dataset: LogicalForecastDataset) -> MaterializedForecastDataset: ...

    def execute_association(
        self, dataset: LogicalAssociationDataset
    ) -> MaterializedAssociationDataset: ...
    def execute_delta(self, dataset: LogicalDeltaDataset) -> MaterializedDeltaDataset: ...
    def execute_attribution(
        self, dataset: LogicalAttributionDataset
    ) -> MaterializedAttributionDataset: ...
    def show(self, dataset: MaterializedDataset, *, max_output_bytes: int | None) -> None: ...
    def to_pandas(self, dataset: MaterializedDataset) -> pandas.DataFrame: ...
    def evidence_digest(self, dataset: MaterializedDataset) -> ArtifactDigest: ...
    def findings(
        self, dataset: MaterializedDataset, *, limit: int, cursor: str | None
    ) -> FindingPage: ...
    def finding(self, dataset: MaterializedDataset, finding_id: str) -> Finding: ...


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ObservationRuntimeOwner(DatasetOwner):
    """Retained action/read authority independent of any source semantic catalog."""

    action_port: ObservationActionPort = field(kw_only=True)
    source_context: ObservationSourceContext | None = field(default=None, kw_only=True)
    comparison_basis_snapshot: str | None = field(default=None, kw_only=True)
    candidate_definition_snapshot: CandidateDefinition | DriverCandidateDefinition | None = field(
        default=None, kw_only=True
    )


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class ObservationOwner(ObservationRuntimeOwner):
    report_time: ReportTimeAuthority = field(default_factory=ReportTimeAuthority, kw_only=True)
    semantic_registry: Registry = field(kw_only=True)
    sidecar: CompiledExpressionSidecar = field(kw_only=True)
    binding_scopes: SourceBindingScopes = field(kw_only=True)
    period_calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = field(
        default=(), kw_only=True
    )


@dataclass(slots=True, repr=False)
class ObservationSourceContext:
    """Explicit current Session semantics, resolved only for authored enrichment."""

    current: ObservationOwner | None = None


def owner_of(dataset: Dataset) -> ObservationRuntimeOwner:
    owner = dataset._owner
    if not isinstance(owner, ObservationRuntimeOwner):
        raise construction_error("private Observation owner", "foreign family owner")
    return owner


def source_owner_of(dataset: Dataset) -> ObservationOwner:
    """Require source authority only for operations that actually consume semantics."""
    owner = owner_of(dataset)
    if not isinstance(owner, ObservationOwner) and owner.source_context is not None:
        current = owner.source_context.current
        if current is not None:
            if (
                current.session_id != owner.session_id
                or current.store_id != owner.store_id
                or current.action_port is not owner.action_port
            ):
                raise construction_error("the current Session semantic context", "foreign context")
            return current
    if not isinstance(owner, ObservationOwner):
        raise construction_error(
            "current source-construction authority for semantic enrichment",
            "catalog-free retained Dataset owner",
            repair="Use retained fields for row operations, or construct a new source with explicit semantic authority.",
        )
    return owner


def construction_error(
    expected: str,
    received: str,
    *,
    repair: str = "Reconstruct the observation with current exact semantic inputs and supported coordinates.",
) -> ObservationConstructionError:
    return ObservationConstructionError(
        expected=expected, received=received, repair=repair, location="observation.construction"
    )


def scope_payload(scope: TimeScope | None) -> CanonicalValue:
    return (
        None if scope is None else (scope.start.isoformat(), scope.end.isoformat(), "closed_open")
    )


def _ref_payload(value: RefPayloadV1) -> CanonicalValue:
    return (value.schema, value.kind.value, value.path)


def _query_parameter_payload(value: JsonQueryParamValue) -> CanonicalValue:
    if type(value) is SourceParamIR:
        return ("source_parameter", value.name)
    if type(value) is str or type(value) is bool or type(value) is int or type(value) is float:
        return ("literal", value)
    if type(value) is tuple:
        return ("sequence", tuple(_query_parameter_payload(item) for item in value))
    raise construction_error(
        "immutable normalized JSON parameter declarations",
        "unsupported or mutable parameter declaration",
    )


def _source_payload(source: EntitySourceIR) -> CanonicalValue:
    if type(source) is TableSourceIR:
        return (
            "table",
            source.table,
            source.database,
            tuple((name, binding.source, binding.data_type) for name, binding in source.columns),
        )
    if type(source) is ParquetSourceIR:
        return ("parquet", source.path, source.hive_partitioning, source.columns)
    if type(source) is CsvSourceIR:
        return ("csv", source.path, source.schema, source.header, source.delimiter)
    if type(source) is JsonSourceIR:
        return (
            "json",
            source.path,
            source.schema,
            source.format,
            source.records_path,
            source.field_paths,
            tuple((name, _query_parameter_payload(value)) for name, value in source.query_params),
            source.method,
            source.body_json,
            tuple((path, parameter.name) for path, parameter in source.body_params),
        )
    raise construction_error("closed normalized source declaration", "unsupported source variant")


def _version_payload(
    version: TargetSnapshotVersion | TargetValidityVersion | None,
) -> CanonicalValue:
    if version is None:
        return None
    if type(version) is TargetSnapshotVersion:
        return (
            "snapshot",
            _ref_payload(version.coordinate_ref),
            version.source_column,
            version.logical_type,
            version.timezone,
            version.format,
            version.grain,
        )
    if type(version) is TargetValidityVersion:
        return (
            "validity",
            _ref_payload(version.valid_from_ref),
            _ref_payload(version.valid_to_ref),
            version.valid_from_column,
            version.valid_to_column,
            version.interval,
            version.open_end,
            version.timezone,
        )
    raise construction_error("closed normalized Entity version", "unsupported version variant")


def _version_selection_payload(
    selection: TargetSnapshotSelection | TargetValiditySelection | None,
) -> CanonicalValue:
    if selection is None:
        return None
    if type(selection) is TargetSnapshotSelection:
        return (
            "snapshot",
            _ref_payload(selection.coordinate_ref),
            selection.period,
            selection.interpretation,
        )
    if type(selection) is TargetValiditySelection:
        return (
            "validity",
            _ref_payload(selection.valid_from_ref),
            _ref_payload(selection.valid_to_ref),
            selection.boundary,
            selection.start_operator,
            selection.end_operator,
            selection.open_end,
            selection.interpretation,
        )
    raise construction_error("closed normalized version selection", "unsupported selection variant")


def entity_payload(entity: TargetEntityContract) -> CanonicalValue:
    declaration = (
        "observation.entity/v1",
        _ref_payload(entity.ref),
        _ref_payload(entity.datasource_ref),
        entity.dependency_fingerprint,
        _source_payload(entity.source),
        entity.primary_key,
        entity.identity_signature,
        entity.version_row_key,
        entity.columns,
        _version_payload(entity.version),
        tuple((obligation.kind, obligation.columns) for obligation in entity.obligations),
        entity.credential_slots,
    )
    return (entity.ref.path, entity.identity_signature, _canonical_digest(declaration))


def path_dependency_fingerprint(
    owner: ObservationOwner, source: str, paths: tuple[tuple[str, ...], ...]
) -> str:
    """Bind ordered paths to their owning semantic source and join definitions."""
    registry = owner.semantic_registry
    if source not in registry.entities:
        raise construction_error("current semantic source Entity", "unknown path source")
    entity_ids = {source}
    for path in paths:
        for relationship_id in path:
            relationship = registry.relationships.get(relationship_id)
            if relationship is None:
                raise construction_error(
                    "current governed relationship path", "unknown path relationship"
                )
            entity_ids.update((relationship.from_entity, relationship.to_entity))
    # The semantic owner includes relationships joining reached Entities in its
    # canonical dependency closure, together with source and datasource facts.
    # Retain authored path order separately; no second input graph is created.
    dependencies = dependency_digest(
        registry,
        sidecar=owner.sidecar,
        semantic_refs=tuple(_create_ref(SemanticKind.ENTITY, name) for name in sorted(entity_ids)),
    )
    return _canonical_digest(
        ("observation.path-dependencies/v1", source, paths, dependencies.digest)
    )


def _parse_payload(parse: SemanticParse | None) -> CanonicalValue:
    if parse is None:
        return None
    if isinstance(parse, DateParse):
        return (parse.kind,)
    interval = parse.sample_interval
    sample = None if interval is None else (interval.count, interval.unit)
    if isinstance(parse, StrptimeParse):
        return (parse.kind, parse.format, parse.timezone, sample)
    if isinstance(parse, HourPrefixParse):
        return (parse.kind, parse.prefix, sample)
    return (parse.kind, parse.timezone, sample)


def dimension_payload(dimension: TargetDimensionContract) -> CanonicalValue:
    return (
        dimension.ref.kind.value,
        dimension.ref.path,
        dimension.entity_ref.path,
        dimension.source_column,
        dimension.logical_type,
        dimension.nullable,
        dimension.granularity,
        dimension.timezone,
        dimension.physical_type,
        _parse_payload(dimension.parse),
    )


def grain_payload(grain: Grain | None) -> CanonicalValue:
    if grain is None:
        return None
    if grain.kind == "builtin":
        return ("builtin", grain.unit, grain.count)
    return ("semantic", None if grain.calendar is None else grain.calendar.path, grain.level)


@dataclass(frozen=True, slots=True, repr=False)
class CoordinatePathBinding:
    """One shared coordinate spine path and each source branch's exact mapping."""

    ref: str
    spine_path: tuple[str, ...]
    component_paths: tuple[tuple[str, tuple[str, ...]], ...]
    partition: Literal["functional", "disjoint", "overlapping"]

    def identity_payload(self) -> CanonicalValue:
        return (self.ref, self.spine_path, self.component_paths, self.partition)


@dataclass(frozen=True, slots=True, repr=False)
class MetricCoordinateAggregationV1:
    """Exact source admission separated from the sufficient state of a read."""

    metric_ref: str
    required_components: tuple[str, ...]
    contribution_partition_by_reduced_axis: tuple[tuple[str, str], ...]
    required_time_axes: tuple[str, ...]
    source_requirements: tuple[str, ...]
    materialized_fold: Literal["exact_components", "source_required"]
    logical_recompute_mode: Literal["exact_source"] = "exact_source"
    ordered_aggregation_and_temporal_fold: tuple[str, ...] = ("space", "time", "compose")

    def identity_payload(self) -> CanonicalValue:
        return (
            self.metric_ref,
            self.required_components,
            self.contribution_partition_by_reduced_axis,
            self.required_time_axes,
            self.source_requirements,
            self.materialized_fold,
            self.logical_recompute_mode,
            self.ordered_aggregation_and_temporal_fold,
        )


@dataclass(frozen=True, slots=True, repr=False)
class MetricDefinition:
    entity: TargetEntityContract
    metrics: tuple[TargetMetricContract, ...]
    dimensions: tuple[TargetDimensionContract, ...]
    time_axis: TargetDimensionContract | None
    time_scope: TimeScope | None
    reference_axis: TargetDimensionContract | None
    population_definition: str
    entity_present: bool = True
    selection_boundaries: tuple[str, ...] = ()
    contribution_paths: tuple[tuple[str, ...], ...] = ()
    coordinate_dependencies: tuple[tuple[str, str], ...] = ()
    source_dependency_fingerprint: str = ""
    grain: Grain | None = None
    coordinate_paths: tuple[CoordinatePathBinding, ...] = ()
    aggregation_contracts: tuple[MetricCoordinateAggregationV1, ...] = ()
    temporal_snapshot: PeriodCalendarSnapshotV1 | None = None
    distinct_memberships: tuple[DistinctMembershipAuthorityV1, ...] = ()
    distributions: tuple[DistributionAuthorityV1, ...] = ()
    report_time: ReportTimeAuthority = field(default_factory=ReportTimeAuthority)

    def identity_payload(self) -> CanonicalValue:
        return (
            entity_payload(self.entity),
            tuple(
                (
                    item.key,
                    item.dependency_fingerprint,
                    item.required_state,
                    item.logical_type,
                    item.nullable,
                    item.unit,
                    item.evaluation_order,
                    item.null_rule,
                    item.empty_rule,
                )
                for item in self.metrics
            ),
            tuple(dimension_payload(item) for item in self.dimensions),
            None
            if self.time_axis is None
            else (dimension_payload(self.time_axis), grain_payload(self.grain)),
            scope_payload(self.time_scope),
            None if self.reference_axis is None else dimension_payload(self.reference_axis),
            self.population_definition,
            self.entity_present,
            self.selection_boundaries,
            self.contribution_paths,
            self.coordinate_dependencies,
            self.source_dependency_fingerprint,
            self.report_time.model_dump_json(),
            tuple(path.identity_payload() for path in self.coordinate_paths),
            tuple(contract.identity_payload() for contract in self.aggregation_contracts),
            None if self.temporal_snapshot is None else self.temporal_snapshot.snapshot_digest,
            tuple(item.model_dump_json() for item in self.distinct_memberships),
            tuple(item.model_dump_json() for item in self.distributions),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class PopulationPayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    entity: TargetEntityContract
    time_scope: TimeScope | None
    reference_axis: TargetDimensionContract | None
    version_selection: TargetSnapshotSelection | TargetValiditySelection | None
    captures: tuple[BoundSourceParametersV1, ...]
    predicate: BoundPredicate | None = None
    dependency_fingerprint: str = ""
    report_time: ReportTimeAuthority = field(default_factory=ReportTimeAuthority)

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            entity_payload(self.entity),
            scope_payload(self.time_scope),
            None if self.reference_axis is None else dimension_payload(self.reference_axis),
            _version_selection_payload(self.version_selection),
            tuple(item.identity_payload() for item in self.captures),
            None if self.predicate is None else self.predicate.identity_payload(),
            self.dependency_fingerprint,
            self.report_time.model_dump_json(),
        )


@dataclass(frozen=True, slots=True, repr=False)
class RankSpec:
    """One bound rank request shared by logical and retained row operators."""

    by: DatasetField
    order: Literal["ascending", "descending"]
    ties: Literal["ordinal", "dense", "min", "max"]
    partition_fields: tuple[DatasetField, ...] = ()

    def identity_payload(self) -> CanonicalValue:
        return (
            self.by.field_id.value,
            self.order,
            self.ties,
            tuple(field.field_id.value for field in self.partition_fields),
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class MetricPayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    definition: MetricDefinition
    captures: tuple[BoundSourceParametersV1, ...]
    predicate: BoundPredicate | None = None
    selected_metric: str | None = None
    rank: RankSpec | None = None
    limit_count: int | None = None

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            self.definition.identity_payload(),
            tuple(item.identity_payload() for item in self.captures),
            None if self.predicate is None else self.predicate.identity_payload(),
            self.selected_metric,
            None if self.rank is None else self.rank.identity_payload(),
            self.limit_count,
        )


@dataclass(frozen=True, slots=True, repr=False, eq=False, kw_only=True)
class RetainedRowsPayload(_LogicalNodePayload, _token=_CORE_TOKEN):
    predicate: BoundPredicate | None = None
    selected_metric: str | None = None
    rank: RankSpec | None = None
    limit_count: int | None = None

    @property
    def identity_payload(self) -> CanonicalValue:
        return (
            None if self.predicate is None else self.predicate.identity_payload(),
            self.selected_metric,
            None if self.rank is None else self.rank.identity_payload(),
            self.limit_count,
        )


MetricBinding: TypeAlias = tuple[DatasetFieldId, str | None, str, tuple[str, ...], str, str]
CoordinateBinding: TypeAlias = tuple[DatasetFieldId, str, tuple[str, ...]]


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class EntityPresentMetricSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    fold_authority: str
    metric_bindings: tuple[MetricBinding, ...]
    coordinate_semantics: tuple[CoordinateBinding, ...]
    kind: Literal["metric/entity-present@v1"] = field(
        default="metric/entity-present@v1", init=False
    )

    @property
    def metric_folds(self) -> tuple[MetricFoldAuthorityV1, ...]:
        return decode_fold_authority(self.fold_authority).metrics

    @property
    def fold_time_grain(self) -> Grain | None:
        return decode_fold_authority(self.fold_authority).time_grain()

    @property
    def fold_time_scope(self) -> TimeScope | None:
        return decode_fold_authority(self.fold_authority).time_scope()

    @property
    def fold_temporal_snapshot(self) -> PeriodCalendarSnapshotV1 | None:
        return decode_fold_authority(self.fold_authority).temporal_snapshot()


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class EntityReducedMetricSemantics(DatasetFamilyRowSemantics, _token=_CORE_TOKEN):
    reduced_entity_ref: str
    reduced_identity_signature: tuple[tuple[str, str], ...]
    fold_authority: str
    metric_bindings: tuple[MetricBinding, ...]
    coordinate_semantics: tuple[CoordinateBinding, ...]
    kind: Literal["metric/entity-reduced@v1"] = field(
        default="metric/entity-reduced@v1", init=False
    )

    @property
    def metric_folds(self) -> tuple[MetricFoldAuthorityV1, ...]:
        return decode_fold_authority(self.fold_authority).metrics

    @property
    def fold_time_grain(self) -> Grain | None:
        return decode_fold_authority(self.fold_authority).time_grain()

    @property
    def fold_time_scope(self) -> TimeScope | None:
        return decode_fold_authority(self.fold_authority).time_scope()

    @property
    def fold_temporal_snapshot(self) -> PeriodCalendarSnapshotV1 | None:
        return decode_fold_authority(self.fold_authority).temporal_snapshot()


def make_ids(entities: tuple[TargetEntityContract, ...]) -> _StableIdRegistry:
    if any(not _is_stable_identifier(kind) for entity in entities for _, kind in entity.columns):
        raise construction_error(
            "registered primitive logical types for private source construction",
            "unsupported parameterized source type",
            repair="Use declared int64, float64, string, date or timestamp columns for this slice; parameterized types require explicit registered contracts.",
        )
    types = frozenset(
        {
            "identity_tuple",
            "duration",
            "bool_tuple",
            "candidate_reasons",
            "boolean",
            "bool",
            "integer",
            "int64",
            "int32",
            "uint8",
            "uint16",
            "uint32",
            "uint64",
            "floating",
            "float64",
            "float32",
            "string",
            "date",
            "timestamp",
            *(f"timestamp({scale})" for scale in range(7)),
            "datetime",
            "decimal",
            *(kind for entity in entities for _, kind in entity.columns),
        }
    )
    return _StableIdRegistry(
        families=frozenset(
            {
                "population",
                "metric",
                "delta",
                "attribution",
                "association",
                "forecast",
                "candidate",
                "event",
                "lifecycle",
            }
        ),
        shapes=frozenset(
            {
                *(
                    ("association", shape, 1)
                    for shape in ("entity", "dimension", "time-lag", "dimension-time-lag")
                ),
                *(
                    ("candidate", shape, 1)
                    for shape in (
                        "point-anomaly",
                        "interesting-window",
                        "period-shift",
                        "entity-outlier",
                        "driver-axis",
                    )
                ),
                *(("forecast", shape, 1) for shape in ("time", "dimension-time")),
                ("population", "entity-membership", 1),
                ("event", "journey", 1),
                *(
                    ("lifecycle", shape, 1)
                    for shape in ("history", "distribution", "transitions", "dwell", "violations")
                ),
                ("event", "funnel", 1),
                ("delta", "funnel", 1),
                ("attribution", "funnel-loss-rate", 1),
                ("event", "time-to-event", 1),
                ("attribution", "joint", 1),
                ("attribution", "hierarchy", 1),
                *(("metric", shape, 1) for shape in METRIC_SHAPES),
                *(
                    ("delta", shape, 1)
                    for shape in ("entity", "scalar", "dimension", "time", "dimension-time")
                ),
            }
        ),
        roles=frozenset(
            {
                "candidate_reason_codes",
                "candidate_coordinate",
                "metric_identity",
                "entity_identity",
                "journey_identity",
                "pattern_step_identity",
                "event_occurrence_identity",
                "time_coordinate",
                "duration_value",
                "additive_count",
                "rate_value",
                "metric",
                "dimension",
                "time_dimension",
                "rank",
                "comparison_coordinate",
                "comparison_time",
                "comparison_value",
                "effect_value",
                "attribution_partition_identity",
                "method_identity",
                "status",
            }
        ),
        logical_types=types,
        physical_types=types,
        admitted_types=types,
        physical_type_classes=frozenset((kind, kind) for kind in types),
        value_orders=frozenset(
            {
                "observation.identity_tuple@v1",
                "observation.scalar_order@v1",
                "association.metric_request_order@v1",
                "association.lag_request_order@v1",
                "event.journey_anchor@v1",
                "event.pattern_step@v1",
            }
        ),
        storage_kinds=frozenset({"parquet", "engine", "object"}),
        byte_unavailable_reasons=frozenset({"not_measured"}),
    )


def entity_ref(path: str) -> Ref[EntityKind]:
    # The closed runtime discriminator establishes this exact static marker.
    return cast("Ref[EntityKind]", _create_ref(SemanticKind.ENTITY, path))


def additional_captures(
    dataset: Dataset, entities: tuple[TargetEntityContract, ...]
) -> tuple[BoundSourceParametersV1, ...]:
    """Capture only newly introduced sources; upstream source captures are final."""
    from marivo.analysis.datasets.handles import LogicalRootHandle

    captured: set[str] = set()
    roots = [dataset._root]
    while roots:
        root = roots.pop()
        if isinstance(root, LogicalRootHandle):
            payload = root.payload
            if isinstance(payload, (PopulationPayload, MetricPayload)):
                captured.update(item.entity_ref.path for item in payload.captures)
            roots.extend(item.root for item in root.inputs)
    return source_owner_of(dataset).binding_scopes.capture(
        tuple(entity for entity in entities if entity.ref.path not in captured)
    )


def identity_field(entity: TargetEntityContract, ids: _StableIdRegistry) -> DatasetField:
    if not entity.identity_signature:
        raise construction_error("non-empty governed Entity identity", "source-only Entity")
    return _make_field(
        field_id=IDENTITY_FIELD_ID,
        name="entity_identity",
        role_id="entity_identity",
        identity=_entity_identity(entity_ref(entity.ref.path), entity.identity_signature, ids=ids),
        derivation_identity="identity.entity_identity@v1",
        logical_type_id="identity_tuple",
        physical_type_state=_deferred_type("identity_tuple", ids=ids),
        nullable=False,
        ids=ids,
    )


def dimension_field(
    dimension: TargetDimensionContract,
    ids: _StableIdRegistry,
    *,
    time: bool = False,
    dependency_fingerprint: str = "",
    grain: Grain | None = None,
) -> DatasetField:
    role = "time_dimension" if time else "dimension"
    identity = f"{dimension.ref.kind.value}:{dimension.ref.path}"
    derived = _canonical_digest(
        (
            dimension_payload(dimension),
            grain_payload(grain) if time else None,
            dependency_fingerprint,
        )
    )
    return _make_field(
        field_id=_make_field_id(f"{role}.{derived[:32]}@v1"),
        name=dimension.ref.path.rsplit(".", 1)[-1],
        role_id=role,
        identity=_catalog_identity(identity),
        derivation_identity=f"coordinate.{derived}",
        logical_type_id=dimension.logical_type,
        physical_type_state=_deferred_type(dimension.logical_type, ids=ids),
        nullable=dimension.nullable,
        ids=ids,
    )


def population_contracts(
    entity: TargetEntityContract, ids: _StableIdRegistry
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("population", "entity-membership", 1, ids=ids),
        schema=_make_schema((identity_field(entity, ids),)),
        coordinate_field_ids=(IDENTITY_FIELD_ID,),
        key_field_ids=(IDENTITY_FIELD_ID,),
        family_semantics=_complete_from_schema(),
    )
    return row, _make_row_set_contract(
        schema_version=1,
        cardinality=_keyed_cardinality(_unknown_row_bound()),
        ordering=_unordered_ordering(),
    )


def metric_contracts(
    definition: MetricDefinition, ids: _StableIdRegistry, registry: Registry
) -> tuple[DatasetRowContract, DatasetRowSetContract]:
    fold_authority = make_fold_authority(definition)
    fold_metrics = {item.metric_ref: item for item in decode_fold_authority(fold_authority).metrics}
    coordinates: list[DatasetField] = []
    if definition.entity_present:
        coordinates.append(identity_field(definition.entity, ids))
    dependencies = dict(definition.coordinate_dependencies)
    coordinates.extend(
        dimension_field(item, ids, dependency_fingerprint=dependencies.get(item.ref.path, ""))
        for item in definition.dimensions
    )
    if definition.time_axis is not None:
        coordinates.append(
            dimension_field(
                definition.time_axis,
                ids,
                time=True,
                grain=definition.grain,
                dependency_fingerprint=_canonical_digest(
                    (
                        dependencies.get(definition.time_axis.ref.path, ""),
                        definition.report_time.model_dump_json(),
                        None
                        if definition.temporal_snapshot is None
                        else definition.temporal_snapshot.snapshot_digest,
                    )
                ),
            )
        )
    columns = list(coordinates)
    bindings: list[MetricBinding] = []
    for metric in definition.metrics:
        field_id = _make_field_id(f"metric.{_canonical_digest(metric.key)[:32]}@v1")
        columns.append(
            _make_field(
                field_id=field_id,
                name=metric.name,
                role_id="metric",
                identity=(
                    _catalog_identity(metric.identity_id)
                    if isinstance(metric.identity, CatalogMetricIdentity)
                    else _runtime_metric_identity(metric.identity.expression_fingerprint)
                ),
                derivation_identity=metric.dependency_fingerprint,
                logical_type_id=metric.logical_type,
                physical_type_state=_deferred_type(metric.logical_type, ids=ids),
                nullable=True,
                ids=ids,
            )
        )
        bindings.append(
            (
                field_id,
                metric.unit,
                metric.dependency_fingerprint,
                metric.required_state
                or tuple(
                    dict.fromkeys(
                        state
                        for component in fold_metrics[metric.key].components
                        for state, _ in component.state_columns
                    )
                ),
                metric.null_rule,
                metric.empty_rule,
            )
        )
    path_bindings = {path.ref: path for path in definition.coordinate_paths}
    coordinate_semantics = tuple(
        (
            item.field_id,
            definition.grain.to_token()
            if item.role_id == "time_dimension" and definition.grain is not None
            else path_bindings[item.identity.identity_id.split(":", 1)[1]].partition,
            (
                ("entity_unique",)
                if path_bindings[item.identity.identity_id.split(":", 1)[1]].partition
                == "functional"
                else ("contribution_coordinates",)
            )
            + path_bindings[item.identity.identity_id.split(":", 1)[1]].spine_path,
        )
        for item in coordinates
        if isinstance(item.identity, _CatalogFieldIdentity)
    )
    if definition.entity_present:
        semantics: DatasetFamilyRowSemantics = EntityPresentMetricSemantics(
            _token=_CORE_TOKEN,
            fold_authority=fold_authority,
            metric_bindings=tuple(bindings),
            coordinate_semantics=coordinate_semantics,
        )
    else:
        semantics = EntityReducedMetricSemantics(
            _token=_CORE_TOKEN,
            reduced_entity_ref=definition.entity.ref.path,
            reduced_identity_signature=definition.entity.identity_signature,
            fold_authority=fold_authority,
            metric_bindings=tuple(bindings),
            coordinate_semantics=coordinate_semantics,
        )
    shape_parts = []
    if definition.entity_present:
        shape_parts.append("entity")
    if definition.dimensions:
        shape_parts.append("dimension")
    if definition.time_axis is not None:
        shape_parts.append("time")
    shape = "-".join(shape_parts) or "scalar"
    coordinate_ids = tuple(item.field_id for item in coordinates)
    names = [column.name for column in columns]
    normalized_columns = tuple(
        replace(
            column,
            _token=_CORE_TOKEN,
            name=(
                column.identity.identity_id.replace(":", "__").replace(".", "__")
                if isinstance(column.identity, (_CatalogFieldIdentity, _RuntimeMetricFieldIdentity))
                and (names.count(column.name) > 1 or column.name == "entity_identity")
                else column.name
            ),
        )
        for column in columns
    )
    row = _make_row_contract(
        schema_version=1,
        shape_id=_make_shape_id("metric", shape, 1, ids=ids),
        schema=_make_schema(normalized_columns),
        coordinate_field_ids=coordinate_ids,
        key_field_ids=coordinate_ids,
        family_semantics=semantics,
    )
    cardinality = (
        _singleton_cardinality() if shape == "scalar" else _keyed_cardinality(_unknown_row_bound())
    )
    return row, _make_row_set_contract(
        schema_version=1, cardinality=cardinality, ordering=_unordered_ordering()
    )


def retained_field(dataset: Dataset, operand: PredicateField) -> DatasetField:
    if isinstance(operand, DatasetFieldRef):
        return validate_field_ref(
            dataset, operand, allowed_roles=("metric", "dimension", "time_dimension", "rank")
        )
    if isinstance(operand, (MetricEntry, DimensionEntry, TimeDimensionEntry)):
        if operand._catalog is not dataset._owner.catalog_identity:
            raise construction_error(
                "entry in the current owning catalog", "foreign or stale entry"
            )
        ref = operand.ref
    elif type(operand) is Ref:
        ref = operand
    else:
        raise construction_error("exact retained field", "unsupported operand")
    matches = tuple(
        column
        for column in dataset.schema.columns
        if isinstance(column.identity, _CatalogFieldIdentity)
        and column.identity.identity_id == ref.key
    )
    if len(matches) != 1:
        raise construction_error("one exact retained field binding", "missing or ambiguous field")
    return matches[0]


def metric_definition(dataset: Dataset) -> MetricDefinition:
    from marivo.analysis.datasets.handles import LogicalRootHandle

    root = dataset._root
    if not isinstance(root, LogicalRootHandle) or not isinstance(root.payload, MetricPayload):
        raise construction_error(
            "logical source contributions for this transition",
            "retained rows without source contribution authority",
            repair="Declare coordinates and aggregate before execute(); retained folds are not implemented in this slice.",
        )
    return root.payload.definition


def _validate_population(row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
    if (
        len(row.schema.columns) != 1
        or row.key_field_ids != (IDENTITY_FIELD_ID,)
        or row.coordinate_field_ids != (IDENTITY_FIELD_ID,)
    ):
        raise construction_error("one complete Entity tuple key", "invalid Population row contract")
    identity = row.schema.columns[0]
    if (
        not isinstance(identity.identity, _EntityFieldIdentity)
        or identity.nullable
        or identity.logical_type_id != "identity_tuple"
        or identity.name != "entity_identity"
        or identity.field_id != IDENTITY_FIELD_ID
        or identity.role_id != "entity_identity"
    ):
        raise construction_error("non-null exact Entity identity tuple", "invalid identity field")
    if row.family_semantics.kind != "complete_from_schema" or row_set.cardinality.kind != "keyed":
        raise construction_error(
            "schema-complete keyed Population rows", "invalid family semantics or cardinality"
        )


def _validate_metric(row: DatasetRowContract, row_set: DatasetRowSetContract) -> None:
    semantics = row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise construction_error("closed Metric row semantics", "invalid row semantics")
    try:
        authority = decode_fold_authority(semantics.fold_authority)
    except (ValueError, TypeError) as exc:
        raise construction_error(
            "complete current retained fold authority", "invalid fold authority"
        ) from exc
    if tuple(item.field_id for item in authority.metrics) != tuple(
        item[0].value for item in semantics.metric_bindings
    ):
        raise construction_error(
            "one exact fold closure per Metric binding", "mismatched fold authority"
        )
    shape = row.shape_id.local_shape_id
    entity_present = shape.startswith("entity")
    if entity_present != isinstance(semantics, EntityPresentMetricSemantics):
        raise construction_error("shape-matched Entity context", "mismatched Entity state")
    columns = row.schema.columns
    coordinates = tuple(
        column
        for column in columns
        if column.role_id in ("entity_identity", "dimension", "time_dimension")
    )
    values = tuple(column for column in columns if column.role_id == "metric")
    ranks = tuple(column for column in columns if column.role_id == "rank")
    if len(ranks) > 1 or any(
        field.field_id.value != "generated.rank@v1"
        or field.name != "rank"
        or field.logical_type_id != "int64"
        or not field.nullable
        or not isinstance(field.identity, _GeneratedFieldIdentity)
        or field.identity.producer_field_id != field.field_id
        for field in ranks
    ):
        raise construction_error("one exact nullable generated rank field", "invalid rank binding")
    if not values or columns != (*coordinates, *values, *ranks):
        raise construction_error(
            "coordinates followed by ordered Metric values", "invalid field role order"
        )
    if any(
        not isinstance(column.identity, (_CatalogFieldIdentity, _RuntimeMetricFieldIdentity))
        for column in values
    ):
        raise construction_error(
            "exact catalog or runtime Metric identities", "invalid value identity"
        )
    for column, fold in zip(values, authority.metrics, strict=True):
        value_identity = column.identity
        if isinstance(value_identity, _CatalogFieldIdentity):
            valid = value_identity.identity_id == "metric:" + fold.metric_ref
        elif isinstance(value_identity, _RuntimeMetricFieldIdentity):
            valid = (
                value_identity.identity_id == fold.metric_ref
                and value_identity.expression_fingerprint == fold.root_id
            )
        else:
            valid = False
        if not valid:
            raise construction_error(
                "Metric identity matching its retained graph", "mismatched fold identity"
            )
    if any(
        bool(binding[3]) != bool(fold_state_names(fold))
        for binding, fold in zip(semantics.metric_bindings, authority.metrics, strict=True)
    ):
        raise construction_error(
            "retained state requirements matching fold authority",
            "mismatched component part requirements",
        )
    if tuple(column.field_id for column in values) != tuple(
        binding[0] for binding in semantics.metric_bindings
    ):
        raise construction_error("ordered exact Metric value bindings", "mismatched Metric values")
    if len(
        {
            column.identity.identity_id
            for column in values
            if isinstance(column.identity, (_CatalogFieldIdentity, _RuntimeMetricFieldIdentity))
        }
    ) != len(values):
        raise construction_error("distinct Metric semantic identities", "duplicate Metric identity")
    coordinate_ids = tuple(column.field_id for column in coordinates)
    if row.coordinate_field_ids != coordinate_ids or row.key_field_ids != coordinate_ids:
        raise construction_error(
            "exact ordered coordinate row key", "mismatched coordinate/key contract"
        )
    remaining = coordinates
    if entity_present:
        if not remaining or remaining[0].field_id != IDENTITY_FIELD_ID:
            raise construction_error(
                "leading Entity identity coordinate", "missing Entity coordinate"
            )
        identity, *rest = remaining
        if (
            identity.role_id != "entity_identity"
            or identity.name != "entity_identity"
            or identity.logical_type_id != "identity_tuple"
            or identity.nullable
            or not isinstance(identity.identity, _EntityFieldIdentity)
        ):
            raise construction_error(
                "exact non-null Entity tuple binding", "invalid Entity coordinate"
            )
        remaining = tuple(rest)
    elif isinstance(semantics, EntityReducedMetricSemantics):
        entity_ref(semantics.reduced_entity_ref)
        if not semantics.reduced_identity_signature:
            raise construction_error(
                "retained reduced Entity signature", "missing identity context"
            )
    dimensions = tuple(column for column in remaining if column.role_id == "dimension")
    times = tuple(column for column in remaining if column.role_id == "time_dimension")
    if (
        remaining != (*dimensions, *times)
        or len(times) > 1
        or bool(dimensions) != ("dimension" in shape)
        or bool(times) != ("time" in shape)
    ):
        raise construction_error(
            "shape-exact ordered Dimension and time coordinates", "invalid coordinate roles"
        )
    if tuple(binding[0] for binding in semantics.coordinate_semantics) != tuple(
        column.field_id for column in remaining
    ) or any(not binding[1] for binding in semantics.coordinate_semantics):
        raise construction_error(
            "exact ordered coordinate semantics", "mismatched coordinate meaning"
        )
    retained_grain = authority.time_grain()
    if bool(times) != (retained_grain is not None) or any(
        kind != retained_grain.to_token()
        for field_id, kind, _ in semantics.coordinate_semantics
        if field_id in {item.field_id for item in times} and retained_grain is not None
    ):
        raise construction_error(
            "time coordinates matching retained fold authority", "mismatched retained grain"
        )
    if row_set.cardinality.kind != ("singleton" if shape == "scalar" else "keyed"):
        raise construction_error("shape-exact cardinality", "invalid row-set cardinality")


def _consumer_admission(dataset: Dataset, consumer_id: str) -> bool:
    if consumer_id in (
        "discover.point_anomalies",
        "discover.interesting_windows",
        "discover.entity_outliers",
    ):
        return sum(field.role_id == "metric" for field in dataset.schema.columns) == 1
    if consumer_id == "metric.forecast":
        return sum(field.role_id == "metric" for field in dataset.schema.columns) == 1
    if consumer_id == "metric.correlate":
        return 2 <= sum(field.role_id == "metric" for field in dataset.schema.columns) <= 16
    if consumer_id == "metric.rank":
        return not any(field.role_id == "rank" for field in dataset.schema.columns)
    if consumer_id == "metric.limit":
        return dataset.row_set_contract.ordering.kind == "ordered"
    if consumer_id == "population.where":
        identity = dataset.schema.columns[0].identity
        if not isinstance(identity, _EntityFieldIdentity):
            return False
        from marivo.analysis.datasets.handles import LogicalRootHandle

        try:
            owner = source_owner_of(dataset)
        except ObservationConstructionError:
            return False
        entity = owner.semantic_registry.entities.get(identity.entity_ref.path)
        return entity is not None and entity.versioning is None
    if consumer_id == "metric.aggregate":
        from marivo.analysis.datasets.handles import LogicalRootHandle
        from marivo.analysis.observation.rollup import _admit

        root = dataset._root
        if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
            return root.payload.definition.entity_present
        if not isinstance(dataset.row_contract.family_semantics, EntityPresentMetricSemantics):
            return False
        try:
            _admit(dataset, "entity", (IDENTITY_FIELD_ID,))
        except ObservationConstructionError:
            return False
        return True
    if consumer_id == "metric.rollup":
        from marivo.analysis.observation.rollup import _admit

        for coordinate in dataset.schema.columns:
            if coordinate.role_id not in ("dimension", "time_dimension"):
                continue
            try:
                _admit(
                    dataset,
                    "time" if coordinate.role_id == "time_dimension" else "dimension",
                    (coordinate.field_id,),
                )
            except ObservationConstructionError:
                continue
            return True
        return False
    if consumer_id in ("metric.with_dimensions", "metric.with_time_axis"):
        from marivo.analysis.datasets.handles import LogicalRootHandle

        root = dataset._root
        if not isinstance(root, LogicalRootHandle) or not isinstance(root.payload, MetricPayload):
            return False
        return consumer_id != "metric.with_time_axis" or root.payload.definition.time_axis is None
    return True


def _contract_facts(dataset: Dataset) -> tuple[tuple[str, str], ...]:
    from marivo.analysis.datasets.handles import LogicalRootHandle

    root = dataset._root
    facts: list[tuple[str, str]] = [
        (
            "source_checks",
            "Identity, branch reconciliation and source capability remain action-time requirements.",
        )
    ]
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
        definition = root.payload.definition
        facts.extend(
            (
                ("population", definition.population_definition),
                ("observation_scope", repr(scope_payload(definition.time_scope))),
            )
        )
        for contract in definition.aggregation_contracts:
            if contract.source_requirements:
                facts.append(
                    (
                        f"source_requirements:{contract.metric_ref}",
                        ", ".join(contract.source_requirements),
                    )
                )
            if contract.materialized_fold == "source_required":
                facts.append(
                    (
                        f"aggregation:{contract.metric_ref}",
                        "Exact source recomputation; projected retained values do not authorize a coordinate fold.",
                    )
                )
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, PopulationPayload):
        facts.append(("membership_scope", repr(scope_payload(root.payload.time_scope))))
        if root.payload.version_selection is not None:
            facts.append(
                (
                    "version_selection",
                    "Exact excluded-endpoint representation; availability and uniqueness require execution.",
                )
            )
    return tuple(facts)


def make_family_registry(ids: _StableIdRegistry) -> DatasetFamilyRegistry:
    from marivo.analysis.domains.contracts import EventSelectionPayload
    from marivo.analysis.domains.lifecycle_reducers import LifecycleSelectionPayload
    from marivo.analysis.observation.metric import LogicalMetricDataset, MaterializedMetricDataset
    from marivo.analysis.observation.population import (
        LogicalPopulationDataset,
        MaterializedPopulationDataset,
    )

    registry = DatasetFamilyRegistry()

    def state_decoder(state: MaterializedDatasetState) -> MaterializedDatasetState:
        _validate_materialized_state(state, ids=ids)
        return state

    population_shape = _make_shape_id("population", "entity-membership", 1, ids=ids)
    registry.register(
        DatasetFamilyRegistration(
            family_id="population",
            logical_type=LogicalPopulationDataset,
            materialized_type=MaterializedPopulationDataset,
            shape_ids=(population_shape,),
            owner_id="observation.population",
            ids=ids,
            row_validator=_validate_population,
            consumers=(
                ConsumerRegistration(
                    "population.where",
                    ("input",),
                    "population",
                    (population_shape,),
                    ("population_membership_stable@v1",),
                ),
            ),
            repr_renderer=_dataset_repr,
            materialized_state_decoder=state_decoder,
            node_payload_types=(
                PopulationPayload,
                EventSelectionPayload,
                LifecycleSelectionPayload,
            ),
            consumer_admission=_consumer_admission,
            contract_facts=_contract_facts,
        )
    )
    shapes = tuple(_make_shape_id("metric", shape, 1, ids=ids) for shape in METRIC_SHAPES)
    entity_shapes = shapes[:4]
    consumers = tuple(
        ConsumerRegistration(
            f"metric.{method}",
            ("input",),
            "metric",
            accepted,
            ("metric_source_validation@v1",),
        )
        for method, accepted in (
            ("where", tuple(shape for shape in shapes if shape.local_shape_id != "scalar")),
            ("metric", shapes),
            ("with_dimensions", entity_shapes),
            ("with_time_axis", entity_shapes),
            ("aggregate", entity_shapes),
            ("rollup", shapes[5:]),
            (
                "rank",
                tuple(
                    shape
                    for shape in shapes
                    if shape.local_shape_id in ("entity", "dimension", "time", "dimension-time")
                ),
            ),
            ("limit", tuple(shape for shape in shapes if shape.local_shape_id != "scalar")),
        )
    )
    from marivo.analysis.operators.discovery import MetricDiscovery

    consumers = (
        *consumers,
        *(
            ConsumerRegistration(
                f"discover.{method}",
                ("metric_time",),
                "candidate",
                tuple(
                    shape for shape in shapes if shape.local_shape_id in ("time", "dimension-time")
                ),
                ("candidate.metric_time@v1",),
                namespace_type=MetricDiscovery,
            )
            for method in ("point_anomalies", "interesting_windows")
        ),
        ConsumerRegistration(
            "discover.entity_outliers",
            ("metric_entity",),
            "candidate",
            tuple(shape for shape in shapes if shape.local_shape_id == "entity"),
            ("candidate.metric_entity@v1",),
            namespace_type=MetricDiscovery,
        ),
        ConsumerRegistration(
            "metric.forecast",
            ("input",),
            "forecast",
            tuple(shape for shape in shapes if shape.local_shape_id in ("time", "dimension-time")),
            ("forecast.metric@v1",),
        ),
        ConsumerRegistration(
            "metric.correlate",
            ("input",),
            "association",
            tuple(
                shape
                for shape in shapes
                if shape.local_shape_id in ("entity", "dimension", "time", "dimension-time")
            ),
            ("correlate.metric@v1",),
        ),
        ConsumerRegistration(
            "metric.expand_axes",
            ("input",),
            "metric",
            shapes,
            ("metric_source_validation@v1",),
            discoverable=False,
        ),
        ConsumerRegistration(
            "metric.compare",
            ("current", "baseline"),
            "delta",
            tuple(
                shape
                for shape in shapes
                if shape.local_shape_id
                in ("entity", "scalar", "dimension", "time", "dimension-time")
            ),
            ("compare.metric@v1",),
        ),
    )
    registry.register(
        DatasetFamilyRegistration(
            family_id="metric",
            logical_type=LogicalMetricDataset,
            materialized_type=MaterializedMetricDataset,
            shape_ids=shapes,
            owner_id="observation.metric",
            ids=ids,
            row_validator=_validate_metric,
            consumers=consumers,
            repr_renderer=_dataset_repr,
            materialized_state_decoder=state_decoder,
            node_payload_types=(MetricPayload, RetainedRowsPayload, RetainedFoldPayload),
            consumer_admission=_consumer_admission,
            contract_facts=_contract_facts,
        )
    )
    from marivo.analysis.operators.attribute import register_attribution
    from marivo.analysis.operators.compare import register_delta
    from marivo.analysis.operators.correlate import register_association

    register_association(registry, ids)
    from marivo.analysis.operators.forecast import register_forecast

    register_forecast(registry, ids)
    from marivo.analysis.operators.discovery import register_candidate

    register_candidate(registry, ids)
    register_delta(registry, ids)
    register_attribution(registry, ids)
    from marivo.analysis.domains.event import register_event

    register_event(registry, ids)
    from marivo.analysis.domains.lifecycle import register_lifecycle

    register_lifecycle(registry, ids)
    registry.freeze()
    return registry


def semantic_dependency_digest(
    dataset: Dataset,
    *,
    retained_semantic_digests: Mapping[str, str] | None = None,
) -> str:
    """Hash the complete frozen semantic closure without inspecting live authoring state."""
    from marivo.analysis.datasets.descriptors import _field_binding_fingerprint
    from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
    from marivo.analysis.domains.contracts import (
        EventFunnelPayload,
        EventPayload,
        EventSelectionPayload,
        EventTimeToEventPayload,
    )
    from marivo.analysis.domains.event_attribution import FunnelAttributePayload
    from marivo.analysis.domains.event_comparison import FunnelComparePayload
    from marivo.analysis.domains.lifecycle import LifecyclePayload
    from marivo.analysis.domains.lifecycle_reducers import (
        LifecycleReducerPayload,
        LifecycleSelectionPayload,
    )
    from marivo.analysis.operators.association_contracts import CorrelatePayload
    from marivo.analysis.operators.attribution_contracts import AttributePayload
    from marivo.analysis.operators.candidate_contracts import CandidatePayload
    from marivo.analysis.operators.contracts import ComparePayload
    from marivo.analysis.operators.driver_contracts import DriverCandidatePayload
    from marivo.analysis.operators.forecast_contracts import ForecastPayload

    facts: set[str] = set()
    visited: set[int] = set()
    roots = [dataset._root]
    while roots:
        root = roots.pop()
        if isinstance(root, MaterializedScanLeafHandle) and retained_semantic_digests is not None:
            retained = retained_semantic_digests.get(root.artifact_ref.ref)
            if retained is None:
                raise construction_error(
                    "selected committed semantic dependency authority", "missing retained authority"
                )
            facts.add(retained)
            continue
        if not isinstance(root, LogicalRootHandle):
            raise construction_error(
                "frozen logical Observation semantic dependencies",
                "retained scan requires its committed dependency authority",
            )
        if id(root) in visited:
            continue
        visited.add(id(root))
        payload = root.payload
        if isinstance(payload, PopulationPayload):
            semantic_facts: CanonicalValue = (
                "population",
                entity_payload(payload.entity),
                payload.dependency_fingerprint,
                None
                if payload.reference_axis is None
                else dimension_payload(payload.reference_axis),
            )
        elif isinstance(payload, MetricPayload):
            definition = payload.definition
            semantic_facts = (
                "metric",
                entity_payload(definition.entity),
                tuple((metric.key, metric.dependency_fingerprint) for metric in definition.metrics),
                definition.source_dependency_fingerprint,
                definition.coordinate_dependencies,
                None
                if definition.temporal_snapshot is None
                else definition.temporal_snapshot.snapshot_digest,
                tuple(dimension_payload(dimension) for dimension in definition.dimensions),
                None if definition.time_axis is None else dimension_payload(definition.time_axis),
                None
                if definition.reference_axis is None
                else dimension_payload(definition.reference_axis),
            )
        elif isinstance(payload, (LifecycleReducerPayload, LifecycleSelectionPayload)):
            semantic_facts = ("lifecycle_operator", payload.identity_payload)
        elif isinstance(payload, LifecyclePayload):
            semantic_facts = ("lifecycle", payload.identity_payload)
        elif isinstance(payload, EventPayload):
            semantic_facts = (
                "event",
                payload.definition.source_dependency_fingerprint,
                tuple(step.event_fingerprint for step in payload.definition.steps),
            )
        elif isinstance(
            payload, (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload)
        ):
            semantic_facts = ("event_continuation", payload.identity_payload)
        elif isinstance(payload, (CandidatePayload, DriverCandidatePayload)):
            semantic_facts = ("discovery", payload.spec.identity_payload())
        elif isinstance(payload, ForecastPayload):
            semantic_facts = ("metric_forecast", payload.spec.identity_payload())
        elif isinstance(payload, CorrelatePayload):
            semantic_facts = ("metric_correlate", payload.spec.identity_payload())
        elif isinstance(payload, (FunnelComparePayload, FunnelAttributePayload)):
            semantic_facts = ("event_operator", payload.identity_payload)
        elif isinstance(payload, ComparePayload):
            semantic_facts = ("metric_compare",)
        elif isinstance(payload, AttributePayload):
            semantic_facts = ("metric_attribute", payload.spec.identity_payload())
        elif isinstance(payload, RetainedFoldPayload):
            semantic_facts = ("retained_fold", payload.spec.identity_payload())
        elif isinstance(payload, RetainedRowsPayload):
            semantic_facts = (
                "retained_rows",
                payload.selected_metric,
                None
                if payload.rank is None
                else (
                    _field_binding_fingerprint(payload.rank.by),
                    tuple(
                        _field_binding_fingerprint(field) for field in payload.rank.partition_fields
                    ),
                ),
            )
        else:
            raise construction_error(
                "closed logical Observation semantic payload",
                "unsupported semantic dependency payload",
            )
        facts.add(_canonical_digest(semantic_facts))
        predicates = (
            [payload.predicate]
            if isinstance(payload, (PopulationPayload, MetricPayload, RetainedRowsPayload))
            and payload.predicate is not None
            else []
        )
        while predicates:
            predicate = predicates.pop()
            if predicate.field is not None:
                facts.add(
                    _canonical_digest(
                        ("predicate_field", _field_binding_fingerprint(predicate.field))
                    )
                )
            predicates.extend(predicate.children)
        roots.extend(item.root for item in root.inputs)
    return _canonical_digest(("observation.semantic_dependencies/v1", tuple(sorted(facts))))
