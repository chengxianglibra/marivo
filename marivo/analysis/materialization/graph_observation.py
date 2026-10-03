"""Frozen window observation construction over the qualified Entity graph."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, time, timezone
from itertools import pairwise

import pyarrow as pa

from marivo._temporal import TimeScope
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import ObservedQuantity
from marivo.analysis.core.rules import (
    BindProject,
    DirectMetricDefinition,
    EntityObservationTarget,
    GroupObservationTarget,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrenceFilter,
    OccurrencePrepare,
    PreparedObservation,
    entity_candidates,
    entity_members,
)
from marivo.analysis.core.time_authority import civil_bound
from marivo.analysis.core.time_grid import BoundTimeGrid, GridPoint, bind_cumulative
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.graph_members import MemberGraph
from marivo.analysis.materialization.graph_preflight import preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType, TimeShape
from marivo.refs import (
    DimensionKind,
    MetricKind,
    Ref,
    RelationshipKind,
    TimeDimensionKind,
    ref,
)
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import (
    DIRECT_ONLY_AGGREGATES,
    DateParse,
    DatetimeParse,
    StrptimeParse,
    TargetRelationshipContract,
    TimestampParse,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
    component_node,
)
from marivo.semantic.metric_graph_lowering import normalize_target_metric
from marivo.semantic.runtime_metric import RuntimeMetricExpr
from marivo.semantic.validator import (
    Registry,
    normalize_target_dimension,
    normalize_target_relationship,
)


def _reject(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a qualified numeric occurrence, member route and declared or driver-bound event axis",
        received=received,
        repair="Use direct numeric Measure columns, an explicit default event axis for runtime leaves, and the exact contribution-to-member relationship.",
        location="analysis.graph_observation",
        help_target="dsl.LogicalAnalysisDomain.observe",
    )


def _reject_ratio(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a qualified additive ratio or signed linear graph with one route per distinct root",
        received=received,
        repair="Bind one route per distinct contribution root in canonical order; use sum/count leaves for ratio and linear composition.",
        location="analysis.graph_observation",
        help_target="dsl.LogicalAnalysisDomain.observe",
    )


def normalize_metric_input(
    registry: Registry,
    metric_ref: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    sidecar: CompiledExpressionSidecar,
) -> TargetMetricContract:
    """Resolve a Metric Ref or a runtime expression to the same target contract."""
    from marivo.semantic.metric_graph_lowering import normalize_target_metric_inputs
    from marivo.semantic.runtime_metric import RuntimeMetricExpr as RuntimeExpression

    if isinstance(metric_ref, RuntimeExpression):
        return normalize_target_metric_inputs(registry, (metric_ref,), sidecar=sidecar)[0]
    return normalize_target_metric(registry, metric_ref.path, sidecar=sidecar)


def _window_bounds(
    during: TimeScope | None,
    report_timezone: str,
) -> tuple[str | None, str | None]:
    """Normalize one observation window, or report an omitted restriction.

    An omitted window means the admitted source without an added time limit; it
    is not a claim of all-history completeness.
    """
    if during is None:
        return None, None
    normalized = tuple(
        (value if isinstance(value, datetime) else datetime.combine(value, time()))
        .replace(tzinfo=timezone.utc)
        .isoformat()
        for value in (
            civil_bound(
                item,
                report=report_timezone if during.kind == "absolute" else during.boundary_timezone,
                boundary="UTC",
            )
            for item in (during.start, during.end)
        )
    )
    return normalized[0], normalized[1]


def _default_time_axis(registry: Registry, owner_path: str) -> Ref[TimeDimensionKind] | None:
    """Return one Entity's unambiguous declared default time axis, if any."""
    defaults = tuple(
        dimension
        for dimension in registry.dimensions.values()
        if dimension.entity == owner_path and dimension.is_time_dimension and dimension.is_default
    )
    if len(defaults) != 1:
        return None
    return ref.time_dimension(defaults[0].semantic_id)


def _occurrence_filters(
    registry: Registry,
    aggregate: AggregateNodeV1 | WeightedMeanAggregateNodeV1,
    entity_paths: tuple[str, ...],
    path: tuple[TargetRelationshipContract, ...],
) -> tuple[OccurrenceFilter, ...]:
    """Resolve one component's canonical slice predicates to bound occurrence filters."""
    from marivo.semantic.metric_graph import component_predicate

    if not aggregate.filter:
        return ()
    route_paths = {
        path[0].from_entity_ref.path if path else entity_paths[0],
        *(relationship.to_entity_ref.path for relationship in path),
    }
    filters: list[OccurrenceFilter] = []
    for condition in aggregate.filter:
        dimension = normalize_target_dimension(registry, condition.dimension_ref.path)
        if dimension.entity_ref.path not in route_paths:
            raise _reject("slice Dimension is outside the bound contribution route")
        if dimension.parse is not None or dimension.is_time_dimension:
            raise _reject("slice Dimension must be a direct categorical field")
        try:
            operator, value = component_predicate(condition.value)
        except ValueError as error:
            raise _reject(f"closed slice predicate ({error})") from error
        filters.append(OccurrenceFilter(dimension, operator, value))
    return tuple(filters)


def bound_routes(
    registry: Registry,
    metric: TargetMetricContract,
    routes: tuple[tuple[Ref[RelationshipKind], ...], ...],
) -> dict[str, tuple[Ref[RelationshipKind], ...]]:
    """Bind each distinct contribution root to its one ordered relationship route.

    Routes are keyed by root, so several occurrences of the same contribution
    table share one route while keeping their own branch filters.
    """
    roots = metric.computation_roots
    if not roots:
        raise _reject("at least one distinct contribution root")
    if len(roots) == 1 and len(routes) == 1:
        return {roots[0].path: routes[0]}
    if len(roots) != len(routes):
        raise _reject(f"{len(roots)} distinct contribution roots for {len(routes)} routes")
    for route in routes:
        normalize_target_relationship(registry, route[0].path)
    return {root.path: route for root, route in zip(roots, routes, strict=True)}


def observe_members(
    members: MemberGraph,
    metric_ref: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    during: TimeScope | BoundTimeGrid | None,
    via: Ref[RelationshipKind] | tuple[Ref[RelationshipKind], ...],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
    component_index: int = 0,
    at: datetime | GridPoint | None = None,
    relative: bool = False,
) -> MemberGraph:
    """Resolve schema only and capture one component's observation before admission.

    ``via`` is this occurrence's single ordered route. Callers observing a
    multi-component input resolve each distinct contribution root to its own
    route and pass the one bound to ``component_index``.
    """
    registry = members.registry
    metric = normalize_metric_input(registry, metric_ref, sidecar=sidecar)
    if component_index >= len(metric.components):
        raise _reject("an existing component occurrence index")
    component = metric.components[component_index]
    route_refs = (via,) if type(via) is Ref else via
    path = tuple(normalize_target_relationship(registry, item.path) for item in route_refs)
    if component.computation_root.path != (
        path[0].from_entity_ref.path if path else members.entity_schema.contract.ref.path
    ):
        raise _reject("a route bound to this occurrence's distinct contribution root")
    if metric.cumulative and at is None:
        # A cumulative occurrence consumes [anchor(e), e) per endpoint, so an
        # omitted window cannot degrade to an unrestricted read.
        raise DatasetConstructionError(
            expected="an observation whose named components need no endpoint",
            received="a cumulative Metric requiring an explicit endpoint window",
            repair=(
                "Pass at=grid.end or an aware datetime for a cumulative "
                "Metric, or observe a non-cumulative base instead."
            ),
            location="analysis.graph_observation",
            help_target="dsl.LogicalAnalysisDomain.observe",
        )
    if (
        len(path) not in ((0, 1, 2, 3) if relative else (0, 1, 2))
        or (path and path[-1].to_entity_ref.path != members.entity_schema.contract.ref.path)
        or any(
            relationship.cardinality not in ("many_to_one", "one_to_one")
            or not relationship.keys
            or (
                not relative
                and (
                    relationship.from_version_resolution_required
                    or relationship.to_version_resolution_required
                )
            )
            for relationship in path
        )
        or any(first.to_entity_ref != second.from_entity_ref for first, second in pairwise(path))
        or component.empty_rule not in ("null", "zero")
    ):
        raise _reject("Metric roots or relationship endpoints differ")
    aggregate = component_node(metric.graph, component.node_id)
    aggregate_spec = (
        "weighted_mean" if isinstance(aggregate, WeightedMeanAggregateNodeV1) else aggregate.agg
    )
    quantile = aggregate_spec[1] if isinstance(aggregate_spec, tuple) else None
    aggregate_kind = aggregate_spec[0] if isinstance(aggregate_spec, tuple) else aggregate_spec
    direct_only = aggregate_kind in DIRECT_ONLY_AGGREGATES
    if direct_only and coordinates:
        raise _reject(
            "direct distribution observations do not retain contribution-coordinate state"
        )
    if aggregate_kind not in (
        "sum",
        "mean",
        "min",
        "max",
        "count",
        "weighted_mean",
        "count_distinct",
        "approx_count_distinct",
        "median",
        "approx_median",
        "percentile",
        "approx_percentile",
    ):
        raise _reject("Metric is not a sum or Entity count")
    if (
        isinstance(aggregate, AggregateNodeV1)
        and aggregate.agg == "count"
        and (
            component.empty_rule != "zero"
            or aggregate.target_ref.kind != "entity"
            or aggregate.target_ref.path != component.computation_root.path
        )
    ):
        raise _reject("Metric empty policy differs from the qualified method")
    value_ref = (
        aggregate.value_ref
        if isinstance(aggregate, WeightedMeanAggregateNodeV1)
        else aggregate.target_ref
    )
    body = next(
        (
            body
            for key, body in sidecar.bodies.items()
            if key.path == value_ref.path and key.kind == value_ref.kind
        ),
        None,
    )
    entity_distinct = (
        aggregate_kind in ("count_distinct", "approx_count_distinct") and value_ref.kind == "entity"
    )
    if (
        aggregate_kind != "count"
        and not entity_distinct
        and (body is None or body.source_column is None)
    ):
        raise _reject("Measure is not a frozen direct column")
    event_path = (
        component.status_time_dimension.path
        if component.time_fold is not None and component.status_time_dimension is not None
        else component.event_time_dimension.path
        if component.event_time_dimension is not None
        else None
    )
    if event_path is None:
        # Runtime aggregates use the root Entity's explicitly declared default axis.
        default_axis = _default_time_axis(registry, component.computation_root.path)
        if default_axis is None:
            raise _reject("a declared event time axis on the contribution root")
        event_path = default_axis.path
    event = normalize_target_dimension(registry, event_path)
    entity_paths = tuple(
        dict.fromkeys(
            (
                members.entity_schema.contract.ref.path,
                *(relationship.from_entity_ref.path for relationship in path),
                *(schema.contract.ref.path for schema, _ in members.sources),
            )
        )
    )
    filters = _occurrence_filters(registry, aggregate, entity_paths, path)
    if len(set(coordinates)) != len(coordinates):
        raise _reject("distinct string contribution coordinates are required")
    coordinate_fields = tuple(
        normalize_target_dimension(registry, item.path) for item in coordinates
    )
    if any(
        field.entity_ref.path not in entity_paths
        or field.parse is not None
        or field.is_time_dimension
        for field in coordinate_fields
    ):
        raise _reject("coordinate must be a direct Dimension on the bound contribution route")
    selected_schemas = preflight_entities(
        registry, members.runtime.store.project_root, entity_paths
    )
    schemas = {schema.contract.ref.path: schema for schema in selected_schemas}
    member_schema = schemas[members.entity_schema.contract.ref.path]
    contribution_schema = schemas[component.computation_root.path]
    if isinstance(aggregate, AggregateNodeV1):
        from marivo.semantic._aggregate_accuracy import aggregate_repair

        action = aggregate_repair(aggregate.agg, contribution_schema.shape.backend)
        if action is not None:
            raise DatasetConstructionError(
                expected=f"source-native agg={aggregate.agg!r} with its declared exactness",
                received=f"backend={contribution_schema.shape.backend}",
                repair=action,
                location="analysis.graph_observation",
                help_target="dsl.LogicalAnalysisDomain.observe",
            )
    if event.entity_ref.path not in schemas:
        raise _reject("event time is outside the qualified contribution route")
    if member_schema != members.entity_schema:
        raise _reject("member schema changed after construction")
    distinct_columns = contribution_schema.contract.primary_key if entity_distinct else ()
    amount_type = (
        contribution_schema.field_type(distinct_columns[0])
        if entity_distinct
        else contribution_schema.field_type(body.source_column)
        if aggregate_kind != "count" and body is not None and body.source_column is not None
        else ScalarType("int64")
    )
    if amount_type.name not in ("int64", "float64") and not (
        (
            isinstance(amount_type, DecimalType)
            and aggregate_kind
            in (
                "sum",
                "mean",
                "weighted_mean",
                "min",
                "max",
                "median",
                "percentile",
                "count_distinct",
            )
        )
        or (
            aggregate_kind == "count_distinct"
            and amount_type.name in ("string", "boolean", "date", "timestamp")
        )
        or (
            isinstance(amount_type, DurationType)
            and aggregate_kind in ("sum", "min", "max", "mean", "weighted_mean", "count_distinct")
        )
        or entity_distinct
    ):
        raise _reject("unqualified amount physical type")
    event_type = schemas[event.entity_ref.path].schema.field(event.source_column).type
    if not (
        (pa.types.is_date(event_type) and event.parse in (None, DateParse()))
        or (
            pa.types.is_timestamp(event_type)
            and event_type.unit == "us"
            and (event.parse is None or isinstance(event.parse, (TimestampParse, DatetimeParse)))
        )
        or (pa.types.is_string(event_type) and isinstance(event.parse, StrptimeParse))
    ):
        raise _reject("unqualified event physical type or precision")
    if pa.types.is_date(event_type):
        event = replace(event, logical_type="date")
    elif event.timezone is None and (
        not pa.types.is_timestamp(event_type) or event_type.tz is None
    ):
        engine_timezone = schemas[event.entity_ref.path].engine_timezone
        if engine_timezone is None:
            raise _reject("event time requires a declared or driver-reported read timezone")
        event = replace(event, timezone=engine_timezone)
    if contribution_schema.shape.backend == "sqlite" and event.timezone not in (None, "UTC"):
        raise _reject("SQLite non-UTC observation has no qualified source route")
    for relationship in path:
        first, second = (
            schemas[relationship.from_entity_ref.path],
            schemas[relationship.to_entity_ref.path],
        )
        if tuple(to for _, to in relationship.keys) != second.contract.primary_key or any(
            first.field_type(source) != second.field_type(destination)
            for source, destination in relationship.keys
        ):
            raise _reject("relationship keys differ from the complete destination identity")
    for field in coordinate_fields:
        if schemas[field.entity_ref.path].field_type(field.source_column) != ScalarType("string"):
            raise _reject("coordinate physical type is not string")
    coordinate_fields = tuple(replace(field, logical_type="string") for field in coordinate_fields)
    cumulative = None
    if at is not None:
        if during is not None:
            raise _reject("at and during are independent alternatives; do not supply both")
        occurrences = tuple(
            item for item in metric.cumulative if component.role.startswith(item.role + ".base")
        )
        if len(occurrences) != 1:
            raise _reject(
                "endpoint observation requires one cumulative anchor per component occurrence"
            )
        if isinstance(at, GridPoint) and at.grid != members.root.signature.domain.time_grid:
            raise _reject("endpoint belongs to a different grid")
        occurrence = occurrences[0]
        if occurrence.over_ref.path != event.ref.path:
            raise _reject("cumulative axis differs from the bound occurrence event axis")
        snapshot = None
        if (
            occurrence.anchor != "all_history"
            and occurrence.anchor[0] == "grain_to_date"
            and not isinstance(occurrence.anchor[1], str)
            and occurrence.anchor[1].kind == "semantic"
        ):
            owner = members.runtime._source_context.current
            snapshot = (
                next(
                    (
                        item
                        for item in owner.period_calendar_snapshots
                        if item.calendar_ref == occurrence.anchor[1].calendar
                    ),
                    None,
                )
                if owner is not None
                else None
            )
        cumulative = bind_cumulative(occurrence.anchor, at, report_timezone, snapshot)
    grid_window = isinstance(during, BoundTimeGrid)
    if grid_window and during != members.root.signature.domain.time_grid:
        raise _reject("grid window differs from the receiver's exact bound grid")
    start, end = (
        (None, None)
        if grid_window
        else _window_bounds(None if isinstance(during, BoundTimeGrid) else during, report_timezone)
    )
    window = (
        canonical_json({"start": start, "end": end})
        if start is not None and end is not None
        else canonical_json({"grid_window": during.identity})
        if isinstance(during, BoundTimeGrid)
        else canonical_json({"window": "unrestricted"})
    )
    if isinstance(during, TimeScope) and during.kind != "absolute":
        window = canonical_json(
            {"start": start, "end": end, "scope": during.model_dump(mode="json")}
        )
    if cumulative is not None:
        window = canonical_json(asdict(cumulative))
    temporal = TimeShape("instant", "us", "UTC")
    nodes: dict[str, Node] = {}
    for node in topology(members.root):
        if isinstance(node, SourceLeaf):
            nodes[node.identity] = replace(
                node,
                definition=replace(
                    node.definition, shape=replace(node.definition.shape, time=temporal)
                ),
            )
        elif isinstance(node, MethodNode):
            sources = tuple(nodes[source.identity] for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise _reject("invalid member source dependency")
            nodes[node.identity] = replace(
                node,
                inputs=tuple(Edge(edge.role, nodes[edge.node.identity]) for edge in node.inputs),
                sources=tuple(source for source in sources if isinstance(source, SourceLeaf)),
            )
        else:
            raise _reject("fixed members cannot open a live observation source")
    member_root = nodes[members.root.identity]
    member_leaf = nodes[members.leaf.identity]
    assert isinstance(member_root, MethodNode) and isinstance(member_leaf, SourceLeaf)
    target: EntityObservationTarget | GroupObservationTarget = EntityObservationTarget(
        member_root.signature.domain
    )
    if isinstance(member_root.parameters, MapCorrespond) and member_root.parameters.mode == "group":
        projection = member_root.inputs[0].node
        if (
            not isinstance(projection, MethodNode)
            or not isinstance(projection.parameters, BindProject)
            or projection.parameters.field_contract is None
        ):
            raise _reject("Group has no exact member field projection")
        target = GroupObservationTarget(
            member_root.signature.domain, projection.parameters.field_contract
        )
        member_root = projection
    contribution_ref = ref.entity(contribution_schema.contract.ref.path)
    binding = member_root.signature.domain.binding
    source_entries = [(member_schema, member_leaf)]
    for schema in selected_schemas:
        if schema.contract.ref.path == member_schema.contract.ref.path:
            continue
        entity_ref = ref.entity(schema.contract.ref.path)
        leaf = SourceLeaf(
            SourceDefinition(
                entity_ref,
                digest(schema.contract.dependency_fingerprint + schema_text(schema.schema)),
                ref.datasource(schema.contract.datasource_ref.path),
                replace(schema.shape, time=temporal),
                schema.contract.version,
            ),
            (entity_candidates if relative else entity_members)(
                replace(
                    schema.contract,
                    columns=tuple((field.name, str(field.type)) for field in schema.schema),
                ),
                entity_ref,
                binding,
            ),
            schema.identity_type,
            identity=digest(
                "contribution:"
                + member_leaf.identity
                + entity_ref.path
                + schema.contract.dependency_fingerprint
                + schema_text(schema.schema)
            ),
        )
        prior = nodes.get(leaf.identity)
        if prior is not None:
            if not isinstance(prior, SourceLeaf) or prior.fingerprint != leaf.fingerprint:
                raise _reject("an existing source node has different frozen binding facts")
            leaf = prior
        source_entries.append((schema, leaf))
    contribution = next(
        leaf for schema, leaf in source_entries if schema.contract.ref.path == contribution_ref.path
    )
    occurrence_slice = canonical_json(
        tuple([item.dimension.ref.path, item.operator, item.value] for item in filters)
    )
    quantity = ObservedQuantity(
        digest(
            metric.bound_graph_fingerprint
            + component.node_id
            + occurrence_slice
            + window
            + member_root.fingerprint
        ),
        metric_ref,
        metric.bound_graph_fingerprint,
        metric.unit,
        window,
        digest(
            contribution.definition.fingerprint
            + window
            + ",".join(item.path for item in route_refs)
            + component.role
            + occurrence_slice
            + member_root.fingerprint
        ),
        "ignore_null_inputs",
        "fold@v1"
        if component.time_fold is not None
        else "sum_zero@v1"
        if aggregate_kind == "sum" and component.empty_rule == "zero"
        else f"{aggregate_kind}@v1",
    )
    definition = DirectMetricDefinition(
        metric_ref,
        metric.graph,
        component.node_id,
        metric.bound_graph_fingerprint,
        metric.dependency_fingerprint,
        contribution_ref,
        ref.time_dimension(event.ref.path),
        metric.unit,
        component.empty_rule,
        tuple(ref.relationship(item.path) for item in component.event_time_path),
    )
    parameters: ObserveMetric | ObserveCount | ObserveWeightedMean
    if isinstance(aggregate, WeightedMeanAggregateNodeV1):
        weight_body = next(
            (
                body
                for key, body in sidecar.bodies.items()
                if key.path == aggregate.weight_ref.path and key.kind == aggregate.weight_ref.kind
            ),
            None,
        )
        if (
            body is None
            or body.source_column is None
            or weight_body is None
            or weight_body.source_column is None
            or not (
                amount_type in (ScalarType("int64"), ScalarType("float64"))
                or isinstance(amount_type, DurationType)
                or (isinstance(amount_type, DecimalType) and amount_type.scale * 2 <= 38)
            )
            or contribution_schema.field_type(weight_body.source_column)
            != (ScalarType("int64") if isinstance(amount_type, DurationType) else amount_type)
        ):
            raise _reject(
                "weighted mean requires matching int64/float64 or Decimal value/weight columns "
                "with product scale <= 38, or Duration values with int64 weights"
            )
        parameters = ObserveWeightedMean(
            definition,
            target,
            quantity,
            contribution_ref,
            path,
            event,
            start,
            end,
            body.source_column,
            amount_type.name,
            weight_body.source_column,
            coordinate_fields,
            filters,
        )
    elif aggregate_kind == "count":
        parameters = ObserveCount(
            definition,
            target,
            quantity,
            contribution_ref,
            path,
            event,
            start,
            end,
            coordinate_fields,
            filters,
        )
    else:
        assert entity_distinct or (body is not None and body.source_column is not None)
        assert aggregate_kind != "weighted_mean"
        parameters = ObserveMetric(
            definition,
            target,
            quantity,
            contribution_ref,
            path,
            event,
            start,
            end,
            distinct_columns[0]
            if entity_distinct
            else body.source_column
            if body is not None and body.source_column is not None
            else "",
            amount_type.name,
            coordinate_fields,
            filters,
            method=aggregate_kind,
            quantile=quantile,
            distinct_columns=distinct_columns,
        )
    if component.time_fold is not None:
        if component.time_fold not in ("first", "last", "mean", "min", "max") or not isinstance(
            parameters, ObserveMetric
        ):
            raise _reject("unqualified time fold")
        parameters = replace(parameters, fold=component.time_fold)
    parameters = replace(
        parameters,
        grid_window=grid_window,
        cumulative=cumulative,
        report_timezone=report_timezone,
        window_timezone=during.boundary_timezone
        if isinstance(during, TimeScope) and during.kind != "absolute"
        else report_timezone,
    )
    if relative:
        if not isinstance(parameters, (ObserveMetric, ObserveCount)):
            raise _reject("relative capture requires count or additive components")
        parameters = replace(parameters, capture_versions=True)
    observation_inputs: tuple[Edge, ...] = (Edge("subject", member_root),)
    local_populations = tuple(
        {
            node.inputs[0].node.identity: node.inputs[0].node
            for node in topology(member_root)
            if isinstance(node, MethodNode) and isinstance(node.parameters, OccurrencePrepare)
        }.values()
    )
    prepared_parameters = None
    if local_populations:
        if len(local_populations) != 1 or not isinstance(parameters, (ObserveMetric, ObserveCount)):
            raise _reject("one captured Journey population and a qualified prepared Metric")
        original = local_populations[0]
        observation_inputs = (Edge("subject", member_root), Edge("subject", original))
        prepared_parameters = PreparedObservation(parameters)
    prepared_sources = {parameters.contribution.path}
    prepared_sources.update((parameters.event.entity_ref.path,))
    for hop in parameters.path:
        prepared_sources.update((hop.from_entity_ref.path, hop.to_entity_ref.path))
    prepared_sources.update(field.entity_ref.path for field in parameters.coordinates)
    root = method_node(
        observation_inputs,
        prepared_parameters if prepared_parameters is not None else parameters,
        sources=tuple(
            leaf
            for _, leaf in source_entries
            if prepared_parameters is None or leaf.definition.ref.path in prepared_sources
        ),
        value_type=amount_type
        if isinstance(amount_type, DurationType) and aggregate_kind != "count_distinct"
        else (
            DecimalType(38, max(amount_type.scale, 6))
            if parameters.fold == "mean"
            else DecimalType(38, amount_type.scale)
        )
        if isinstance(parameters, ObserveMetric)
        and parameters.fold is not None
        and isinstance(amount_type, DecimalType)
        else (ScalarType("float64") if parameters.fold == "mean" else amount_type)
        if isinstance(parameters, ObserveMetric) and parameters.fold is not None
        else DecimalType(38, max(amount_type.scale, 6))
        if isinstance(amount_type, DecimalType) and aggregate_kind in ("mean", "weighted_mean")
        else DecimalType(38, amount_type.scale)
        if isinstance(amount_type, DecimalType) and aggregate_kind == "sum"
        else amount_type
        if isinstance(amount_type, DecimalType) and aggregate_kind in ("median", "percentile")
        else ScalarType("float64")
        if isinstance(parameters, ObserveWeightedMean)
        or aggregate_kind in ("mean", "median", "approx_median", "percentile", "approx_percentile")
        or component.time_fold is not None
        else ScalarType("int64")
        if aggregate_kind in ("count_distinct", "approx_count_distinct")
        else amount_type,
    )

    return replace(
        members,
        root=root,
        leaf=member_leaf,
        sources=tuple(
            {
                leaf.identity: (schema, leaf)
                for schema, leaf in (
                    *((schema, nodes[leaf.identity]) for schema, leaf in members.sources),
                    *source_entries,
                )
                if isinstance(leaf, SourceLeaf)
            }.values()
        ),
    )


def observe_ratio_members(
    members: MemberGraph,
    metric_ref: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    during: TimeScope | BoundTimeGrid | None,
    paths: tuple[tuple[Ref[RelationshipKind], ...], ...],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
    at: datetime | GridPoint | None = None,
    relative: bool = False,
) -> MemberGraph:
    """Bind a closed ratio to its independently aggregated ordered components."""
    from marivo.analysis.materialization.graph_composition import combine_observations
    from marivo.semantic.metric_graph import RatioNodeV1

    registry = members.registry
    metric = normalize_metric_input(registry, metric_ref, sidecar=sidecar)
    root_node = next(
        record.node for record in metric.graph.nodes if record.node_id == metric.graph.roots[0]
    )
    if len(metric.components) != 2:
        raise _reject_ratio(f"{len(metric.components)} canonical components for a two-part ratio")
    if (
        not isinstance(root_node, RatioNodeV1)
        or root_node.zero_division not in ("undefined", "null")
        or (metric.requires_source_recompute and not metric.cumulative)
    ):
        raise _reject_ratio("ratio must be a two-component ratio with declared zero policy")
    by_root = bound_routes(registry, metric, paths)
    observed = tuple(
        observe_members(
            members,
            metric_ref,
            during=during,
            via=by_root[component.computation_root.path],
            sidecar=sidecar,
            report_timezone=report_timezone,
            coordinates=coordinates,
            component_index=index,
            at=at,
            relative=relative,
        )
        for index, component in enumerate(metric.components)
    )
    quantities = observed_quantities(observed)
    definition = digest(
        metric.bound_graph_fingerprint
        + quantities[0].time_scope
        + members.root.fingerprint
        + ",".join(item.contribution_id for item in quantities)
    )
    quantity = ObservedQuantity(
        definition,
        metric_ref,
        metric.bound_graph_fingerprint,
        metric.unit,
        quantities[0].time_scope,
        digest(",".join(item.contribution_id for item in quantities)),
        "strict",
        "ratio@v1",
    )
    return combine_observations(observed[0], observed[1], "ratio", ratio=quantity)


def observe_linear_members(
    members: MemberGraph,
    metric_ref: Ref[MetricKind] | RuntimeMetricExpr,
    *,
    during: TimeScope | BoundTimeGrid | None,
    paths: tuple[tuple[Ref[RelationshipKind], ...], ...],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
    at: datetime | GridPoint | None = None,
    relative: bool = False,
) -> MemberGraph:
    """Bind a signed linear combination to its independently reduced occurrences."""
    from marivo.analysis.materialization.graph_composition import combine_linear_occurrences
    from marivo.semantic.metric_graph import LinearNodeV1

    registry = members.registry
    metric = normalize_metric_input(registry, metric_ref, sidecar=sidecar)
    root_node = next(
        record.node for record in metric.graph.nodes if record.node_id == metric.graph.roots[0]
    )
    if not isinstance(root_node, LinearNodeV1):
        raise _reject_ratio("input is not a linear combination")
    if metric.requires_source_recompute and not metric.cumulative:
        raise _reject_ratio("linear combination requires retained component state")
    by_root = bound_routes(registry, metric, paths)
    observed = tuple(
        observe_members(
            members,
            metric_ref,
            during=during,
            via=by_root[component.computation_root.path],
            sidecar=sidecar,
            report_timezone=report_timezone,
            coordinates=coordinates,
            component_index=index,
            at=at,
            relative=relative,
        )
        for index, component in enumerate(metric.components)
    )
    quantities = observed_quantities(observed)

    def leaf_signs(node_id: str, sign: int = 1) -> tuple[int, ...]:
        from marivo.semantic.metric_graph import SliceNodeV1

        node = next(record.node for record in metric.graph.nodes if record.node_id == node_id)
        if isinstance(node, (SliceNodeV1, CumulativeNodeV1)):
            return leaf_signs(node.child_id, sign)
        if isinstance(node, LinearNodeV1):
            return tuple(
                item
                for term in node.terms
                for item in leaf_signs(term.child_id, sign * (1 if term.coefficient > 0 else -1))
            )
        if not isinstance(node, AggregateNodeV1) or node.agg not in ("sum", "count"):
            raise _reject_ratio("linear observation requires additive sum/count leaves")
        return (sign,)

    signs = leaf_signs(metric.graph.roots[0])
    if len(signs) != len(observed):
        raise _reject_ratio(f"{len(signs)} signed terms for {len(observed)} occurrences")
    quantity = ObservedQuantity(
        digest(
            metric.bound_graph_fingerprint
            + quantities[0].time_scope
            + members.root.fingerprint
            + ",".join(item.contribution_id for item in quantities)
        ),
        metric_ref,
        metric.bound_graph_fingerprint,
        metric.unit,
        quantities[0].time_scope,
        digest(",".join(item.contribution_id for item in quantities)),
        "strict",
        "linear@v1",
    )
    return combine_linear_occurrences(observed, quantities, signs, quantity)


def observed_quantities(observed: tuple[MemberGraph, ...]) -> tuple[ObservedQuantity, ...]:
    """Return each occurrence's exact observed quantity, rejecting other kinds."""
    quantities: list[ObservedQuantity] = []
    for item in observed:
        quantity = item.root.signature.quantity
        if not isinstance(quantity, ObservedQuantity):
            raise _reject_ratio("an occurrence without one original observed quantity")
        quantities.append(quantity)
    return tuple(quantities)


def _runtime_ratio_inputs(
    expression: RuntimeMetricExpr,
) -> tuple[Ref[MetricKind], ...]:
    """Return the ordered leaf Metric Refs of a runtime ratio expression."""
    from marivo.semantic.runtime_metric import RuntimeRatioExpr

    if not isinstance(expression, RuntimeRatioExpr):
        raise _reject_ratio(f"runtime expression kind {expression.kind!r} is not a ratio")
    leaves: list[Ref[MetricKind]] = []
    pending: list[Ref[MetricKind] | RuntimeMetricExpr] = [
        expression.numerator,
        expression.denominator,
    ]
    while pending:
        item = pending.pop(0)
        if isinstance(item, RuntimeMetricExpr):
            if not isinstance(item, RuntimeRatioExpr):
                raise _reject_ratio(
                    f"ratio component kind {item.kind!r} is not a governed Metric or nested ratio"
                )
            pending.insert(0, item.denominator)
            pending.insert(0, item.numerator)
        else:
            leaves.append(item)
    return tuple(leaves)


def _observe_ratio_expression(
    members: MemberGraph,
    expression: RuntimeMetricExpr,
    *,
    during: TimeScope | BoundTimeGrid | None,
    paths: tuple[tuple[Ref[RelationshipKind], ...], ...],
    sidecar: CompiledExpressionSidecar,
    report_timezone: str,
    coordinates: tuple[Ref[DimensionKind], ...] = (),
) -> MemberGraph:
    """Bind a runtime ratio to its independently aggregated named components."""
    return observe_ratio_members(
        members,
        expression,
        during=during,
        paths=paths,
        sidecar=sidecar,
        report_timezone=report_timezone,
        coordinates=coordinates,
    )
