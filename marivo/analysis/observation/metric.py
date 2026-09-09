"""Paired Metric values and the private shared-Population source."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal, TypeAlias

from marivo._temporal import Grain, TimeScope
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import (
    Dataset,
    LogicalDataset,
    MaterializedDataset,
    _make_logical_dataset,
    _validate_input_ownership,
)
from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    _canonical_digest,
    _EntityFieldIdentity,
    _make_row_contract,
    _make_schema,
)
from marivo.analysis.datasets.fields import DatasetFieldRef
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.datasets.registry import DatasetFamilyRegistry
from marivo.analysis.observation import aggregation, coordinates
from marivo.analysis.observation.contracts import (
    DimensionInput,
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricDefinition,
    MetricInput,
    MetricPayload,
    ObservationOwner,
    RetainedRowsPayload,
    TimeDimensionInput,
    construction_error,
    entity_ref,
    metric_contracts,
    owner_of,
    path_dependency_fingerprint,
    producer_contract,
    retained_field,
)
from marivo.analysis.observation.distinct_contracts import make_distinct_membership
from marivo.analysis.observation.distribution_contracts import make_distribution
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.observation.population import (
    LogicalPopulationDataset,
    MaterializedPopulationDataset,
    make_population,
)
from marivo.analysis.observation.predicates import AnalysisPredicate, bind_predicates
from marivo.analysis.observation.rollup import rollup as _rollup
from marivo.analysis.operators.contracts import DEFAULT_ALIGNMENT, WindowBucketAlignment
from marivo.analysis.operators.forecast_contracts import (
    DEFAULT_MODEL,
    ForecastHorizon,
    ForecastModel,
)
from marivo.refs import Ref, SemanticKind
from marivo.semantic._quantile import QuantileMetricInput
from marivo.semantic.catalog import MetricEntry
from marivo.semantic.ir import TargetDimensionContract
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import normalize_target_entity

if TYPE_CHECKING:
    import pandas

    from marivo.analysis.evidence._dataset_types import ArtifactDigest, Finding, FindingPage
    from marivo.analysis.operators.association import LogicalAssociationDataset
    from marivo.analysis.operators.delta import LogicalDeltaDataset
    from marivo.analysis.operators.forecast_dataset import LogicalForecastDataset

PopulationInput: TypeAlias = "LogicalPopulationDataset | MaterializedPopulationDataset | LogicalMetricDataset | MaterializedMetricDataset"


class LogicalMetricDataset(LogicalDataset, _token=_CORE_TOKEN, family_id="metric"):
    """Complete logical Metric row meaning without executing contributions."""

    __slots__ = ()

    def forecast(
        self,
        *,
        horizon: ForecastHorizon,
        model: ForecastModel = DEFAULT_MODEL,
        interval_level: float = 0.95,
    ) -> LogicalForecastDataset:
        """Project this single Metric over a certified future horizon.

        Args: horizon: Future period count. model: Named model. interval_level: Nominal level.
        Returns: Logical Forecast. Example: ``history.forecast(horizon=periods(14))``.
        Constraints: Complete consecutive history and finite model-conditional intervals.
        """
        from marivo.analysis.operators.forecast import forecast

        return forecast(self, horizon=horizon, model=model, interval_level=interval_level)

    def correlate(
        self,
        *,
        method: Literal["pearson", "spearman", "kendall"] = "pearson",
        lag_range: range | None = None,
    ) -> LogicalAssociationDataset:
        """Describe association among the current quantitative Metric bindings.

        Args: method: Pearson, Spearman or Kendall tau-b. lag_range: Signed bucket offsets.
        Returns: Logical Association. Example: ``metrics.correlate(method="spearman")``.
        Constraints: 2-16 Metrics; explicit lags require a bound time coordinate.
        """
        from marivo.analysis.operators.correlate import correlate

        return correlate(self, method=method, lag_range=lag_range)

    def compare(
        self,
        baseline: LogicalMetricDataset | MaterializedMetricDataset,
        *,
        alignment: WindowBucketAlignment = DEFAULT_ALIGNMENT,
    ) -> LogicalDeltaDataset:
        """Compare current rows with baseline using ordinal window alignment.

        Args: baseline: Compatible single-Metric input. alignment: window_bucket() policy.
        Returns: Logical Delta. Example: ``current.compare(baseline)``.
        Constraints: Same membership and non-time selection; source ownership is validated.
        """
        from marivo.analysis.operators.compare import compare

        return compare(self, baseline, alignment=alignment)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalMetricDataset:
        """Rank current numeric values by the exact by selector.

        Args: by: Current value field. order: Direction. ties: Tie method.
            partition_by: Distinct current row-key coordinates.
        Returns: Logical Metric with nullable rank and deterministic total order.
        Example: ``metrics.rank(metrics.fields.metric(revenue)).limit(10)``.
        Constraints: Registered non-singleton shapes only; no data work occurs.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalMetricDataset:
        """Keep the first count rows of the existing logical order.

        Args: count: Exact integer from 1 through 100000.
        Returns: Logical Metric with the same row contract and a bounded row set.
        Example: ``ranked.limit(10)``. Constraints: Requires a registered total order.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def where(self, *predicates: AnalysisPredicate) -> LogicalMetricDataset:
        """Select current rows using predicates; return a new Logical Metric.

        Example: ``metrics.where(gt(revenue, 0))``. Constraints: Scalar rows reject filtering.
        """
        return _where(self, predicates)

    def with_dimensions(self, *dimensions: DimensionInput) -> LogicalMetricDataset:
        """Add ordered governed dimensions and return a Logical Metric.

        Example: ``metrics.with_dimensions(region)``. Constraints: Entity must remain present.
        """
        return _checked(coordinates.with_dimensions(self, dimensions))

    def with_time_axis(
        self, time_dimension: TimeDimensionInput, *, grain: Grain
    ) -> LogicalMetricDataset:
        """Add time_dimension at grain and return a Logical Metric.

        Example: ``metrics.with_time_axis(day, grain=grain('day'))``.
        Constraints: One governed time axis is admitted; scope is unchanged.
        """
        return _checked(coordinates.with_time_axis(self, time_dimension, grain))

    def aggregate(self) -> LogicalMetricDataset:
        """Reduce Entity using exact component recomputation; no parameters.

        Returns: Logical Metric. Example: ``metrics.aggregate()``.
        Constraints: Already reduced input and unsupported folds are rejected.
        """
        return _checked(aggregation.aggregate(self))

    def rollup(
        self,
        *,
        drop_dimensions: tuple[DimensionInput, ...] = (),
        grain: Grain | None = None,
        drop_time: bool = False,
    ) -> LogicalMetricDataset:
        """Fold current rows across specified coordinates.

        Args: drop_dimensions: Exact retained Dimensions to remove.
            grain: Strictly coarser time grain. drop_time: Remove time instead.
        Returns: A Logical Metric over the same current contribution state.
        Example: ``metrics.aggregate().rollup(drop_time=True)``.
        Constraints: Entity must be absent; every component fold must be exact.
        """
        return _checked(
            _rollup(self, drop_dimensions=drop_dimensions, grain=grain, drop_time=drop_time)
        )

    def metric(self, metric: MetricInput) -> LogicalMetricDataset:
        """Project one retained metric identity and return a Logical Metric.

        Example: ``metrics.metric(revenue)``. Constraints: Exact retained identity only.
        """
        return _project(self, metric)

    def execute(self) -> MaterializedMetricDataset:
        """Delegate execution to the required runtime owner; no parameters.

        Returns: Committed Metric. Example: ``metrics.execute()``.
        Constraints: Admission and publication belong to that runtime.
        """
        return owner_of(self).action_port.execute_metric(self)


class MaterializedMetricDataset(MaterializedDataset, _token=_CORE_TOKEN, family_id="metric"):
    """Retained Metric rows backed by an exact immutable Artifact scan leaf."""

    __slots__ = ()

    def forecast(
        self,
        *,
        horizon: ForecastHorizon,
        model: ForecastModel = DEFAULT_MODEL,
        interval_level: float = 0.95,
    ) -> LogicalForecastDataset:
        """Project this single Metric over a certified future horizon.

        Args: horizon: Future period count. model: Named model. interval_level: Nominal level.
        Returns: Logical Forecast. Example: ``history.forecast(horizon=periods(14))``.
        Constraints: Complete consecutive history and finite model-conditional intervals.
        """
        from marivo.analysis.operators.forecast import forecast

        return forecast(self, horizon=horizon, model=model, interval_level=interval_level)

    def correlate(
        self,
        *,
        method: Literal["pearson", "spearman", "kendall"] = "pearson",
        lag_range: range | None = None,
    ) -> LogicalAssociationDataset:
        """Describe association among the current quantitative Metric bindings.

        Args: method: Pearson, Spearman or Kendall tau-b. lag_range: Signed bucket offsets.
        Returns: Logical Association. Example: ``metrics.correlate(method="spearman")``.
        Constraints: 2-16 Metrics; explicit lags require a bound time coordinate.
        """
        from marivo.analysis.operators.correlate import correlate

        return correlate(self, method=method, lag_range=lag_range)

    def compare(
        self,
        baseline: LogicalMetricDataset | MaterializedMetricDataset,
        *,
        alignment: WindowBucketAlignment = DEFAULT_ALIGNMENT,
    ) -> LogicalDeltaDataset:
        """Compare current rows with baseline using ordinal window alignment.

        Args: baseline: Compatible single-Metric input. alignment: window_bucket() policy.
        Returns: Logical Delta. Example: ``current.compare(baseline)``.
        Constraints: Same membership and non-time selection; source ownership is validated.
        """
        from marivo.analysis.operators.compare import compare

        return compare(self, baseline, alignment=alignment)

    def rank(
        self,
        by: DatasetFieldRef,
        *,
        order: Literal["ascending", "descending"] = "descending",
        ties: Literal["ordinal", "dense", "min", "max"] = "ordinal",
        partition_by: tuple[DatasetFieldRef, ...] = (),
    ) -> LogicalMetricDataset:
        """Describe ranking over exact retained rows using the current by selector.

        Args: by: Numeric field. order: Direction. ties: Tie method.
            partition_by: Distinct current key coordinates.
        Returns: Logical Metric. Example: ``retained.rank(retained.fields.get('revenue'))``.
        Constraints: Retained execution needs its separately registered local method.
        """
        from marivo.analysis.observation.ordering import rank

        return _checked(rank(self, by, order=order, ties=ties, partition_by=partition_by))

    def limit(self, count: int) -> LogicalMetricDataset:
        """Describe a count-row prefix of the retained logical ordering.

        Args: count: Exact integer from 1 through 100000.
        Returns: Logical Metric. Example: ``retained.limit(10)``.
        Constraints: File or preview order cannot authorize this operation.
        """
        from marivo.analysis.observation.ordering import limit

        return _checked(limit(self, count))

    def where(self, *predicates: AnalysisPredicate) -> LogicalMetricDataset:
        """Select retained rows using predicates and return a Logical Metric.

        Example: ``metrics.where(gt(revenue, 0))``. Constraints: No missing source fields.
        """
        return _where(self, predicates)

    def with_dimensions(self, *dimensions: DimensionInput) -> LogicalMetricDataset:
        """Request dimensions on retained rows; return Logical only when admitted.

        Example: ``metrics.with_dimensions(region)``. Constraints: This slice rejects retained enrichment.
        """
        return _checked(coordinates.with_dimensions(self, dimensions))

    def with_time_axis(
        self, time_dimension: TimeDimensionInput, *, grain: Grain
    ) -> LogicalMetricDataset:
        """Request time_dimension and grain on retained rows.

        Returns: Logical Metric when admitted. Example: ``metrics.with_time_axis(day, grain=grain('day'))``.
        Constraints: This slice rejects coordinate introduction after materialization.
        """
        return _checked(coordinates.with_time_axis(self, time_dimension, grain))

    def aggregate(self) -> LogicalMetricDataset:
        """Request Entity reduction of retained rows; no parameters.

        Returns: Logical Metric when admitted. Example: ``metrics.aggregate()``.
        Constraints: Every retained component requires an exact Entity-axis fold.
        """
        return _checked(aggregation.aggregate(self))

    def rollup(
        self,
        *,
        drop_dimensions: tuple[DimensionInput, ...] = (),
        grain: Grain | None = None,
        drop_time: bool = False,
    ) -> LogicalMetricDataset:
        """Fold current rows across specified coordinates.

        Args: drop_dimensions: Exact retained Dimensions to remove.
            grain: Strictly coarser time grain. drop_time: Remove time instead.
        Returns: A Logical Metric over the same current contribution state.
        Example: ``metrics.aggregate().rollup(drop_time=True)``.
        Constraints: Entity must be absent; every component fold must be exact.
        """
        return _checked(
            _rollup(self, drop_dimensions=drop_dimensions, grain=grain, drop_time=drop_time)
        )

    def metric(self, metric: MetricInput) -> LogicalMetricDataset:
        """Project one retained metric identity and return Logical Metric.

        Example: ``metrics.metric(revenue)``. Constraints: Consumes the exact scan leaf.
        """
        return _project(self, metric)

    def show(self, *, max_output_bytes: int | None = None) -> None:
        """Print committed rows within max_output_bytes; returns None.

        Example: ``metrics.show()``. Constraints: Only the runtime reads rows.
        """
        owner_of(self).action_port.show(self, max_output_bytes=max_output_bytes)

    def to_pandas(self) -> pandas.DataFrame:
        """Return an isolated complete retained DataFrame with no parameters.

        Example: ``metrics.to_pandas()``. Constraints: Runtime collection guards apply.
        """
        return owner_of(self).action_port.to_pandas(self)

    @property
    def evidence_digest(self) -> ArtifactDigest:
        """Return the committed Evidence digest through the runtime read owner."""
        return owner_of(self).action_port.evidence_digest(self)

    def findings(self, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
        """Read a bounded retained Finding page using limit and opaque cursor.

        Returns: FindingPage. Example: ``metrics.findings(limit=10)``.
        Constraints: No new Findings are inferred from retained rows.
        """
        return owner_of(self).action_port.findings(self, limit=limit, cursor=cursor)

    def finding(self, finding_id: str) -> Finding:
        """Read the exact retained Finding identified by finding_id.

        Returns: Finding. Example: ``metrics.finding('finding-id')``.
        Constraints: Missing IDs are handled by the owning runtime.
        """
        return owner_of(self).action_port.finding(self, finding_id)


def _checked(dataset: Dataset) -> LogicalMetricDataset:
    if not isinstance(dataset, LogicalMetricDataset):
        raise construction_error("paired Logical Metric", "invalid family registration")
    return dataset


def make_observation(
    owner: ObservationOwner,
    registry: DatasetFamilyRegistry,
    metrics: MetricInput | list[MetricInput] | tuple[MetricInput, ...],
    *,
    population: PopulationInput | None = None,
    time_scope: TimeScope | None = None,
    time_dimension: TimeDimensionInput | None = None,
) -> LogicalMetricDataset:
    """Bind actual target graphs to one complete shared identity spine without I/O."""
    submitted = tuple(metrics) if isinstance(metrics, (list, tuple)) else (metrics,)
    if not submitted or len(submitted) > 16:
        raise construction_error(
            "one to sixteen ordered Metrics", "empty or oversized Metric inputs"
        )
    references = []
    for item in submitted:
        if isinstance(item, QuantileMetricInput):
            item = item.metric
        if isinstance(item, MetricEntry):
            if type(item) is not MetricEntry or item._catalog is not owner.catalog_identity:
                raise construction_error("current exact Metric entry", "foreign or stale entry")
            reference = item.ref
        elif type(item) is Ref and item.kind is SemanticKind.METRIC:
            reference = item
        elif isinstance(item, RuntimeMetricExpr):
            raise construction_error(
                "initial governed catalog Metric graph", "runtime expression outside this slice"
            )
        else:
            raise construction_error("exact Metric ref or current entry", type(item).__name__)
        references.append(reference)
    if len(set(references)) != len(references):
        raise construction_error("duplicate-free exact Metric identities", "duplicate Metric")
    normalized = tuple(
        normalize_target_metric(owner.semantic_registry, item.path, sidecar=owner.sidecar)
        for item in references
    )
    distributions = []
    for item, metric in zip(submitted, normalized, strict=True):
        basis = make_distribution(
            metric,
            owner.semantic_registry,
            owner.sidecar,
            item.method if isinstance(item, QuantileMetricInput) else "linear_interpolation@v1",
        )
        if isinstance(item, QuantileMetricInput) and basis is None:
            raise construction_error(
                "a governed root median or percentile", "unsupported explicit quantile method input"
            )
        if basis is not None:
            distributions.append(basis)
    roots = tuple(
        dict.fromkeys(root.path for item in normalized for root in item.computation_roots)
    )
    if not roots:
        raise construction_error("complete computation roots", "missing computation root")
    if population is None:
        if len(roots) != 1:
            raise construction_error(
                "one exact default computation-root Entity",
                "different component roots",
                repair="Construct one explicit governed Population with safe component paths, or observe the Metrics separately.",
            )
        population = make_population(owner, registry, entity_ref(roots[0]))
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
    paths = tuple(
        coordinates.functional_path(
            owner.semantic_registry,
            root,
            entity.ref.path,
            allow_versioned_target=True,
            allow_versioned_source=True,
        )
        for root in roots
    )
    required_axes = tuple(
        dict.fromkeys(
            (
                *[
                    component.status_time_dimension.path
                    for metric in normalized
                    for component in metric.components
                    if component.status_time_dimension is not None
                ],
                *[item.over_ref.path for metric in normalized for item in metric.cumulative],
            )
        )
    )
    if len(required_axes) > 1:
        raise construction_error(
            "one common observation time axis", "incompatible component status/cumulative axes"
        )
    axis: TargetDimensionContract | None
    if time_scope is not None and time_dimension is None and required_axes:
        from marivo.semantic.validator import normalize_target_dimension

        axis = normalize_target_dimension(owner.semantic_registry, required_axes[0])
        for root in roots:
            coordinates.functional_path(
                owner.semantic_registry, root, axis.entity_ref.path, allow_versioned_source=True
            )
    else:
        axis = coordinates.resolve_time_axis(owner, roots, time_scope, time_dimension)
    if axis is not None and required_axes and axis.ref.path != required_axes[0]:
        raise construction_error("the governed status/cumulative reference axis", axis.ref.path)
    for root in roots:
        if normalize_target_entity(owner.semantic_registry, root).version is not None and any(
            component.computation_root.path == root and component.status_time_dimension is None
            for metric in normalized
            for component in metric.components
        ):
            raise construction_error(
                "owning temporal fold for versioned Metric contributions",
                "versioned Metric lacks a status-time fold",
                repair="Declare the exact business status axis and temporal fold for every versioned contribution.",
            )
    source_ids = set(roots) | set(
        coordinates.path_entities(owner.semantic_registry, entity.ref.path, paths)
    )
    if entity.ref.path not in roots:
        source_ids.discard(entity.ref.path)
    if axis is not None:
        for root in roots:
            source_ids.update(
                coordinates.path_entities(
                    owner.semantic_registry,
                    root,
                    (
                        coordinates.functional_path(
                            owner.semantic_registry,
                            root,
                            axis.entity_ref.path,
                            allow_versioned_source=True,
                        ),
                    ),
                )
            )
    from marivo.semantic.metric_graph import AggregateNodeV1, WeightedMeanAggregateNodeV1
    from marivo.semantic.validator import normalize_target_dimension

    filter_dependencies: list[str] = []
    for metric in normalized:
        nodes = {record.node_id: record.node for record in metric.graph.nodes}
        for component in metric.components:
            node = nodes[component.node_id]
            if not isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
                continue
            for condition in node.filter:
                dimension = normalize_target_dimension(
                    owner.semantic_registry, condition.dimension_ref.path
                )
                component_root = component.computation_root.path
                route = coordinates.governed_path(
                    owner.semantic_registry, component_root, dimension.entity_ref.path
                )
                source_ids.update(
                    coordinates.path_entities(owner.semantic_registry, component_root, (route,))
                )
                filter_dependencies.append(
                    path_dependency_fingerprint(owner, component_root, (route,))
                )
    captures = owner.binding_scopes.capture(
        tuple(normalize_target_entity(owner.semantic_registry, name) for name in sorted(source_ids))
    )
    population_identity = (
        population.definition_fingerprint
        if isinstance(population, LogicalDataset)
        else population.state.artifact_ref.ref
    )
    definition = MetricDefinition(
        entity,
        normalized,
        (),
        None,
        time_scope,
        axis,
        population_identity,
        contribution_paths=paths,
        source_dependency_fingerprint=_canonical_digest(
            (path_dependency_fingerprint(owner, entity.ref.path, paths), tuple(filter_dependencies))
        ),
        distributions=tuple(distributions),
        distinct_memberships=tuple(
            membership
            for metric in normalized
            if (
                membership := make_distinct_membership(
                    metric, owner.semantic_registry, owner.sidecar
                )
            )
            is not None
        ),
    )
    definition = coordinates.bind_aggregation(owner, definition)
    row, row_set = metric_contracts(definition, registry.get("metric").ids, owner.semantic_registry)
    return _checked(
        _make_logical_dataset(
            owner=owner,
            registry=registry,
            family_id="metric",
            row_contract=row,
            row_set_contract=row_set,
            operator_id="session.observe",
            inputs=(population,),
            input_roles=("population",),
            payload=MetricPayload(_token=_CORE_TOKEN, definition=definition, captures=captures),
            requirements=(
                "metric.shared_population_spine@v1",
                "metric.component_reconciliation@v1",
                "metric.source_capability@v1",
            ),
            dependency_facts=tuple(f"metric:{item.ref.path}" for item in normalized),
            contract_versions=producer_contract("session.observe").versions,
        )
    )


def _where(dataset: Dataset, predicates: tuple[AnalysisPredicate, ...]) -> LogicalMetricDataset:
    if dataset.row_contract.shape_id.local_shape_id == "scalar":
        raise construction_error(
            "non-singleton rows for filtering",
            "scalar singleton",
            repair="Filter Entity or coordinate rows before scalar aggregation.",
        )
    bound = bind_predicates(predicates, lambda operand: retained_field(dataset, operand))
    root = dataset._root
    payload: MetricPayload | RetainedRowsPayload
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
        definition = replace(
            root.payload.definition,
            selection_boundaries=(
                *root.payload.definition.selection_boundaries,
                dataset.definition_fingerprint,
            ),
        )
        payload = MetricPayload(
            _token=_CORE_TOKEN, definition=definition, captures=(), predicate=bound
        )
    else:
        payload = RetainedRowsPayload(_token=_CORE_TOKEN, predicate=bound)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="metric.where",
            contract_versions=producer_contract("metric.where").versions,
            inputs=(dataset,),
            row_contract=dataset.row_contract,
            row_set_contract=dataset.row_set_contract,
            payload=payload,
        )
    )


def _project(dataset: Dataset, metric: MetricInput) -> LogicalMetricDataset:
    if isinstance(metric, QuantileMetricInput):
        raise construction_error(
            "a Metric ref selecting an existing percentile method",
            "a quantile method input at projection",
            repair="Select the Metric ref here; author percentile methods only in observe().",
        )
    selector = dataset.fields.metric(metric)
    from marivo.analysis.datasets.fields import validate_field_ref

    selected = validate_field_ref(dataset, selector, allowed_roles=("metric",))
    semantics = dataset.row_contract.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise construction_error("Metric row semantics", "invalid family payload")
    filtered_semantics = replace(
        semantics,
        _token=_CORE_TOKEN,
        metric_bindings=tuple(
            item for item in semantics.metric_bindings if item[0] == selected.field_id
        ),
        fold_authority=decode_fold_authority(semantics.fold_authority)
        .model_copy(
            update={
                "metrics": tuple(
                    item
                    for item in semantics.metric_folds
                    if item.field_id == selected.field_id.value
                )
            }
        )
        .to_json(),
    )
    row = _make_row_contract(
        schema_version=dataset.row_contract.schema_version,
        shape_id=dataset.row_contract.shape_id,
        schema=_make_schema(
            tuple(
                column
                for column in dataset.schema.columns
                if column.role_id != "metric" or column.field_id == selected.field_id
            )
        ),
        coordinate_field_ids=dataset.row_contract.coordinate_field_ids,
        key_field_ids=dataset.row_contract.key_field_ids,
        family_semantics=filtered_semantics,
    )
    root = dataset._root
    payload: MetricPayload | RetainedRowsPayload
    if isinstance(root, LogicalRootHandle) and isinstance(root.payload, MetricPayload):
        identity = selected.identity
        from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity

        if not isinstance(identity, _CatalogFieldIdentity):
            raise construction_error(
                "initial catalog Metric identity", "unsupported runtime Metric"
            )
        definition = replace(
            root.payload.definition,
            metrics=tuple(
                item
                for item in root.payload.definition.metrics
                if f"metric:{item.ref.path}" == identity.identity_id
            ),
            distributions=tuple(
                item
                for item in root.payload.definition.distributions
                if f"metric:{item.metric_ref}" == identity.identity_id
            ),
            distinct_memberships=tuple(
                item
                for item in root.payload.definition.distinct_memberships
                if f"metric:{item.metric_ref}" == identity.identity_id
            ),
        )
        payload = MetricPayload(
            _token=_CORE_TOKEN,
            definition=definition,
            captures=(),
            selected_metric=selected.field_id.value,
        )
    else:
        payload = RetainedRowsPayload(_token=_CORE_TOKEN, selected_metric=selected.field_id.value)
    return _checked(
        construct_operator(
            owner=owner_of(dataset),
            registry=dataset._registry,
            operator_id="metric.metric",
            contract_versions=producer_contract("metric.metric").versions,
            inputs=(dataset,),
            row_contract=row,
            row_set_contract=dataset.row_set_contract,
            payload=payload,
        )
    )
