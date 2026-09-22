"""Pure admission for individually qualified relational scalar methods."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal, cast

import ibis

from marivo._temporal import Grain, civil_midnight_width_seconds
from marivo.analysis.compiler.normalize import (
    artifact_inputs,
    logical_roots,
    required_entities,
    required_source_dependencies,
)
from marivo.analysis.compiler.predicates import predicate_leaves
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _CatalogFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import (
    MetricDefinition,
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    source_owner_of,
)
from marivo.analysis.operators.association_contracts import CorrelatePayload
from marivo.analysis.operators.attribution_contracts import AttributePayload
from marivo.analysis.operators.contracts import ComparePayload
from marivo.refs import (
    EntityKind,
    Ref,
    RefPayloadV1,
    SemanticKind,
    _create_ref,
)
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    evaluate_expression_body,
)
from marivo.semantic.decimal_precision import DecimalPrecision, DecimalType, sum_of
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    HourPrefixParse,
    StrptimeParse,
    TableSourceIR,
    TargetDimensionContract,
    TargetSnapshotVersion,
    TimestampParse,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    LinearNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.metric_graph_lowering import _derive_measure_result_type
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity

ResolvedDecimalUnit = Literal["linear", "mean", "div"]

_METHODS = frozenset(
    {
        "session.population",
        "population.where",
        "session.observe",
        "metric.with_dimensions",
        "metric.with_time_axis",
        "metric.aggregate",
        "metric.where",
        "metric.metric",
        "metric.rank",
        "metric.limit",
    }
)


def entity_correlation_reason(dataset: LogicalDataset) -> str | None:
    """Admit exact Entity Pearson/Spearman source reduction only."""
    root = dataset._root
    if not isinstance(root, LogicalRootHandle) or not isinstance(root.payload, CorrelatePayload):
        return "this source method is not Entity correlation"
    semantics = root.payload.spec.semantics
    if semantics.input_shape != "entity" or semantics.method not in {"pearson", "spearman"}:
        return "this Entity correlation shape or method is not qualified"
    return None


def supports_scalar_type(value: str) -> bool:
    """Recognize the common scalar types, including derived generic Decimal."""
    if value in {
        "string",
        "int8",
        "int16",
        "int32",
        "int64",
        "float32",
        "float64",
        "date",
        "decimal",
    }:
        return True
    decimal = re.fullmatch(r"decimal\(([1-9][0-9]?),\s*([0-9]+)\)", value)
    return decimal is not None and 0 <= int(decimal[2]) <= int(decimal[1]) <= 38


def supports_plain_timestamp(value: str) -> bool:
    """Recognize civil timestamp types up to microseconds."""
    return re.fullmatch(r"timestamp(?:\([0-6]\))?", value) is not None


def supports_timestamp(value: str) -> bool:
    """Recognize native timestamp precision and optional exact physical timezone."""
    if supports_plain_timestamp(value):
        return True
    return re.fullmatch(r"timestamp\('[^']+'(?:, [0-6])?\)", value) is not None


_SINGLE_UNIT_SUBDAY = frozenset({"second", "minute", "hour"})
_TIMESTAMP_UNITS = frozenset({"hour", "day"})
_CALENDAR_UNITS = frozenset({"day", "week", "month", "quarter", "year"})


def _prefix_is_same_entity_civil_date(registry: Registry, axis: TargetDimensionContract) -> bool:
    """Resolve an hour-prefix axis's declared prefix for this backend's decision.

    The compiler owns the rule itself (:meth:`Lowering._prefix_axis`) and refuses
    the same declaration with its own diagnostic.  Admission only needs the
    resolved prefix to decide whether this backend may take the axis at all.  A
    prefix that is not a loaded Dimension raises the shared normalization error,
    exactly as :class:`ColumnCollector` already does for this axis.
    """
    parse = axis.parse
    if not isinstance(parse, HourPrefixParse):
        return False
    prefix = normalize_target_dimension(registry, parse.prefix)
    return prefix.entity_ref == axis.entity_ref and prefix.logical_type == "date"


def _admitted_bucket(definition: MetricDefinition, grain: Grain) -> bool:
    """Admit exactly the bucket widths every backend now implements identically.

    A semantic grain is admitted only over a native civil-date axis whose
    definition carries the certified snapshot of the grain's own calendar; the
    compiler's CASE-over-date-literals branches then produce identical results
    on every backend.  Parsed text axes keep the qualified-parser contract
    without opening calendar buckets.  A sub-day count above one is admitted
    only on the civil-midnight grid, which exists only when the width divides
    one civil day.  Calendar-variable units (week, month, quarter, year) and
    the day unit keep their existing ``count == 1`` contract, so any
    ``count > 1`` on them returns False.
    """
    snapshot = definition.temporal_snapshot
    if grain.kind == "semantic":
        axis = definition.time_axis
        native_date = (
            axis is not None
            and axis.logical_type == "date"
            and (axis.parse is None or isinstance(axis.parse, DateParse))
        )
        return native_date and snapshot is not None and snapshot.calendar_ref == grain.calendar
    if snapshot is not None:
        return False
    axis = definition.time_axis
    timestamp_axis = axis is not None and axis.logical_type == "timestamp"
    units = _TIMESTAMP_UNITS if timestamp_axis else _CALENDAR_UNITS
    if grain.count == 1:
        return grain.unit in units
    if not timestamp_axis or grain.unit not in _SINGLE_UNIT_SUBDAY:
        return False
    return civil_midnight_width_seconds(grain) is not None


_DECIMAL_MEAN_MAX_SCALE = 30
_MYSQL_MEAN_SCALE_INCREMENT = 4
_PLACEHOLDER_TABLE = "__mv_admission_decimal_facts"
# The one source requirement whose recomputation runs through the shared exact
# cumulative endpoint-window lowering rather than private backend state.
_CUMULATIVE_REQUIREMENT = "metric.source_cumulative@v1"
# Source requirements whose recomputation runs through the shared status-time
# fold lowering (its argmin/argmax/mean/min/max branches); percentile folds
# stay source-private and rejected.
_FOLD_REQUIREMENTS = frozenset(
    {
        "metric.source_temporal_fold@v1",
        "metric.source_quantile@v1",
    }
)


def _decimal_facts(value: str) -> DecimalType | None:
    """Parse one resolved decimal(p, s) type string, or return None."""
    try:
        parsed = DecimalPrecision.from_string(value)
    except ValueError:
        return None
    return DecimalType(parsed.precision, parsed.scale)


def _measure_input_facts(
    node: AggregateNodeV1, owner: ObservationOwner, sidecar: CompiledExpressionSidecar
) -> DecimalType | None:
    """Derive one aggregate's decimal measure-input facts from declared types.

    Only a Measure target qualifies. A computed row-expression body is
    evaluated on an ibis placeholder typed by the owner's declared columns and
    walked through the semantic derivation owner — the same rule table
    normalization applies at load. Every other shape returns ``None``.
    """
    if node.target_ref.kind is not SemanticKind.MEASURE:
        return None
    registry = owner.semantic_registry
    measure = registry.measures.get(node.target_ref.path)
    body = sidecar.bodies.get(_create_ref(SemanticKind.MEASURE, node.target_ref.path))
    if measure is None or body is None:
        return None
    entity = normalize_target_entity(registry, measure.entity)
    if body.source_column is not None:
        return _decimal_facts(dict(entity.columns).get(body.source_column, ""))
    placeholder = ibis.table(dict(entity.columns), name=_PLACEHOLDER_TABLE)
    entity_ref = cast("Ref[EntityKind]", _create_ref(SemanticKind.ENTITY, entity.ref.path))
    expression = evaluate_expression_body(
        catalog_definition_fingerprint=node.target_ref.path,
        expression_sidecar=sidecar,
        owning_ref=_create_ref(SemanticKind.MEASURE, node.target_ref.path),
        body=body,
        entity_refs=(entity_ref,),
        aliases=(placeholder,),
    )
    return _decimal_facts(_derive_measure_result_type(expression.op()))


def _decimal_unit(
    metric: TargetMetricContract,
    owner: ObservationOwner,
    sidecar: CompiledExpressionSidecar,
    units: frozenset[ResolvedDecimalUnit],
) -> str | None:
    """Return the unresolvable composed-decimal unit's diagnostic, or None.

    The metric's declared logical type carries no resolved (p, s), so each
    leaf's facts come from its declared owner column types through the shared
    semantic rule table. Only the metric's published root units are gated: a
    composing linear or mean node keeps the engine's own float/decimal
    contracts, and a decimal ratio publishes float64 through the engine's own
    division inference, so the div unit is reachable only when a decimal-rooted
    ratio exists. A leaf whose facts cannot be derived keeps its rejection with
    the unresolvable unit named.
    """
    nodes = {record.node_id: record.node for record in metric.graph.nodes}
    roots = set(metric.graph.roots)

    def leaf_facts(node_id: str) -> DecimalType | None:
        node = nodes[node_id]
        if isinstance(node, SliceNodeV1):
            return leaf_facts(node.child_id)
        if isinstance(node, AggregateNodeV1):
            facts = _measure_input_facts(node, owner, sidecar)
            if facts is None:
                return None
            # A sum leaf publishes dec(38, s); min/max publish the input type.
            return sum_of(facts) if node.agg == "sum" else facts
        return None

    def linear_unit(node: LinearNodeV1, is_root: bool) -> str | None:
        for term in node.terms:
            if leaf_facts(term.child_id) is None:
                return "linear over decimal components with unresolvable facts"
        # Sum-level decimal linear keeps the metric decimal and is engine-exact
        # on the qualifying backends (sum publishes dec(38, s); the add/sub
        # rule-table bound stays the semantic layer's rejection owner for
        # shapes it cannot resolve at load). The unit gate applies only when
        # the linear node publishes the metric's decimal root.
        if not is_root:
            return None
        return None if "linear" in units else "linear over Decimal components"

    def mean_unit(node: AggregateNodeV1, is_root: bool) -> str | None:
        facts = _measure_input_facts(node, owner, sidecar)
        if facts is None:
            return None if not is_root else "Decimal mean with unresolvable input facts"
        if not is_root:
            return None
        if "mean" not in units:
            return "Decimal mean requires a backend whose AVG scale is a public contract"
        if facts.scale + _MYSQL_MEAN_SCALE_INCREMENT > _DECIMAL_MEAN_MAX_SCALE:
            return (
                f"Decimal mean scale {facts.scale}+{_MYSQL_MEAN_SCALE_INCREMENT} exceeds the "
                f"{_DECIMAL_MEAN_MAX_SCALE}-scale publication bound"
            )
        return None

    def div_unit(node: RatioNodeV1) -> str | None:
        numerator = leaf_facts(node.numerator_id)
        denominator = leaf_facts(node.denominator_id)
        if numerator is None or denominator is None:
            return "Decimal ratio with unresolvable component facts"
        return None

    for record in metric.graph.nodes:
        node = record.node
        is_root = record.node_id in roots
        if isinstance(node, LinearNodeV1):
            reason = linear_unit(node, is_root)
        elif isinstance(node, AggregateNodeV1) and node.agg == "mean":
            reason = mean_unit(node, is_root)
        elif isinstance(node, RatioNodeV1):
            reason = div_unit(node)
        elif isinstance(node, WeightedMeanAggregateNodeV1):
            reason = "Decimal weighted mean stays a float contract" if is_root else None
        else:
            continue
        if reason is not None:
            return f"{reason}: Metric {metric.key}"
    return None


def _fold_kind(fold: str | tuple[str, float]) -> str:
    """Return the fold kind token for one canonical aggregate fold value."""
    if isinstance(fold, tuple):
        return fold[0]
    return fold


def unsupported_reason(
    dataset: LogicalDataset,
    supported_type: Callable[[str], bool],
    *,
    relationships: bool = False,
    versions: bool = False,
    date_buckets: bool = False,
    timestamp_buckets: bool = False,
    parsed_time_axes: bool = False,
    explicit_decimal_sources: bool = False,
    row_expressions: bool = False,
    linear_graphs: bool = False,
    resolved_decimal_units: frozenset[ResolvedDecimalUnit] = frozenset(),
    status_folds: frozenset[str] = frozenset(),
    distinct_memberships: frozenset[Literal["measure", "entity"]] = frozenset(),
    distributions: frozenset[Literal["linear_interpolation"]] = frozenset(),
    expanded_attribution: bool = False,
    expanded_top_k: bool = True,
) -> str | None:
    """Check methods/types without I/O; placement.source_binding owns exact source identity.

    The backend-qualification parameters extend the pure closure check:
    ``row_expressions`` admits computed Measure bodies (``source_column is
    None``) whose structure semantic load already verified, ``linear_graphs``
    admits ``LinearNodeV1`` in the computation whitelist, and
    ``resolved_decimal_units`` replaces the composed-Decimal blanket rejection
    with a per-published-unit decision through the derived precision facts.
    An empty unit set keeps the historical blanket rejection verbatim.
    Cumulative Metric graphs execute through the shared exact endpoint-window
    lowering, so only source-private requirements stay rejected here.
    ``status_folds`` names the qualified status-time fold kinds from
    ``{"first", "last", "mean", "min", "max"}``; an empty set keeps the
    status-time fold rejection verbatim. Component and node fold checks share
    this set. Percentile-tuple folds stay rejected on every backend until a
    backend qualifies its quantile fold lowering.
    ``distinct_memberships`` names the qualified exact distinct-membership key
    shapes from ``{"measure", "entity"}``, judged per authority through its
    ``target_kind``; a definition qualifies only when every one of its own
    authorities qualifies, and an empty set keeps the membership rejection.
    ``distributions`` names the qualified quantile interpretations; the
    ``linear_interpolation`` token admits only a ``linear_interpolation@v1``
    authority. An empty set keeps the admission outcome rejected; the rejection
    text is the shape-specific diagnostic. Each unqualified state shape carries
    its own diagnostic.
    ``expanded_attribution`` admits the qualified non-Entity expanded Compare,
    axis expansion, and additive/component Attribute closure. When
    ``expanded_top_k`` is false, that closure rejects Top-K before source I/O.
    """
    if artifact_inputs(dataset):
        return "remote retained import is not supported"
    roots = tuple(logical_roots(dataset))
    for root in roots:
        if expanded_attribution and root.operator_id == "metric.expand_axes":
            continue
        if expanded_attribution and isinstance(root.payload, ComparePayload):
            if root.payload.spec.output_row.shape_id.local_shape_id == "entity":
                return "Entity comparison requires a separate source-private qualification"
            continue
        if expanded_attribution and isinstance(root.payload, AttributePayload):
            spec = root.payload.spec
            if (
                spec.expanded_compare is None
                or spec.method not in {"additive_difference@v1", "component_mix@v1"}
                or spec.output_row.shape_id.local_shape_id == "entity"
            ):
                return "this expanded Attribution shape or method is not qualified"
            if spec.top_k is not None and not expanded_top_k:
                return "expanded Attribution Top-K exceeds this backend's qualified query plan"
            continue
        if root.operator_id not in _METHODS:
            return (
                f"{root.operator_id} requires a source preparation or private-state "
                "implementation that this backend has not qualified"
            )
    entities = required_entities(dataset)
    if not entities or (len(entities) != 1 and not relationships):
        return "multi-relation execution is not qualified for this backend"
    if any(not isinstance(entity.source, TableSourceIR) for entity in entities):
        return "only declared table sources are supported"
    if any(entity.version is not None for entity in entities) and not versions:
        return "semantic version selection is not qualified for this backend"
    dependencies = required_source_dependencies(dataset)
    for entry in dependencies.entries:
        for column in entry.columns:
            if not supported_type(column.declared_type):
                return (
                    "unsupported declared source type "
                    f"{column.declared_type[:100]} for Entity {entry.entity.ref.path[:160]} "
                    f"column {column.logical[:100]} -> {column.physical[:100]}"
                )
    entity_paths = {entity.ref.path for entity in entities}
    owner = source_owner_of(dataset)

    def dimension(axis: TargetDimensionContract | None) -> bool:
        return axis is None or (
            axis.entity_ref.path in entity_paths
            and supported_type(axis.logical_type)
            and (
                not axis.is_time_dimension
                or axis.logical_type == "date"
                or (timestamp_buckets and axis.logical_type == "timestamp")
            )
            and (
                axis.parse is None
                or isinstance(axis.parse, DateParse)
                or (
                    timestamp_buckets
                    and axis.logical_type == "timestamp"
                    and isinstance(axis.parse, (DatetimeParse, TimestampParse))
                )
                or (
                    parsed_time_axes
                    and axis.is_time_dimension
                    and (
                        # A date-only strptime format stays a civil date, which the
                        # time clause above already admits; a time-bearing one is a
                        # timestamp and is admitted only when this backend also
                        # qualified native timestamp buckets.
                        isinstance(axis.parse, StrptimeParse)
                        or (
                            # A composite hour-prefix axis is an hour value, so the
                            # validator always labels it a timestamp and the same
                            # timestamp gate above applies to it.
                            isinstance(axis.parse, HourPrefixParse)
                            and _prefix_is_same_entity_civil_date(owner.semantic_registry, axis)
                        )
                    )
                )
            )
        )

    def temporal(axis: TargetDimensionContract | None) -> bool:
        return axis is None or (
            dimension(axis)
            and (
                axis.logical_type == "date"
                or (timestamp_buckets and axis.logical_type == "timestamp")
            )
        )

    for entity in entities:
        version = entity.version
        if version is not None:
            axes = (
                (version.coordinate_ref,)
                if isinstance(version, TargetSnapshotVersion)
                else (version.valid_from_ref, version.valid_to_ref)
            )
            if any(
                normalize_target_dimension(owner.semantic_registry, axis.path).logical_type
                != "date"
                or not temporal(normalize_target_dimension(owner.semantic_registry, axis.path))
                for axis in axes
            ):
                return "semantic version selection requires qualified native civil-date axes"

    for root in roots:
        payload = root.payload
        if expanded_attribution and isinstance(payload, (ComparePayload, AttributePayload)):
            continue
        if not isinstance(payload, (PopulationPayload, MetricPayload)):
            return "the source payload has no qualified scalar implementation"
        for predicate in predicate_leaves(payload.predicate):
            if predicate.field is not None and isinstance(
                predicate.field.identity, _CatalogFieldIdentity
            ):
                family, _, path = predicate.field.identity.identity_id.partition(":")
                if family in {"dimension", "time_dimension"} and not dimension(
                    normalize_target_dimension(owner.semantic_registry, path)
                ):
                    return "a predicate requires an unsupported dimension or time parser"
        if isinstance(payload, PopulationPayload):
            if payload.version_selection is not None and not versions:
                return "semantic version selection is not qualified for this backend"
            if not dimension(payload.reference_axis):
                return "the population reference axis is not qualified"
            continue
        definition = payload.definition
        if definition.distinct_memberships and not all(
            item.target_kind in distinct_memberships for item in definition.distinct_memberships
        ):
            return "distinct membership state requires an unqualified source-private implementation"
        if definition.distributions and not all(
            item.quantile.method == "linear_interpolation@v1"
            and "linear_interpolation" in distributions
            for item in definition.distributions
        ):
            return "distribution state requires an unqualified source-private implementation"
        if not relationships and (
            any(definition.contribution_paths)
            or any(
                binding.spine_path or any(path for _, path in binding.component_paths)
                for binding in definition.coordinate_paths
            )
        ):
            return "relationship contribution paths are not qualified for this backend"
        if not dimension(definition.reference_axis) or not all(
            dimension(axis) for axis in definition.dimensions
        ):
            return "a Metric dimension requires an unsupported type or parser"
        if definition.time_axis is not None or definition.grain is not None:
            grain = definition.grain
            if (
                not date_buckets
                or not temporal(definition.time_axis)
                or grain is None
                or not _admitted_bucket(definition, grain)
            ):
                return "this temporal type, parser or bucket is not qualified for this backend"
        for metric in definition.metrics:
            composed = metric.logical_type == "decimal" and any(
                isinstance(record.node, (RatioNodeV1, LinearNodeV1, WeightedMeanAggregateNodeV1))
                or (isinstance(record.node, AggregateNodeV1) and record.node.agg == "mean")
                for record in metric.graph.nodes
            )
            if composed and not resolved_decimal_units:
                return "composed Decimal results require resolved precision and scale"
            if composed and resolved_decimal_units:
                unresolvable = _decimal_unit(metric, owner, owner.sidecar, resolved_decimal_units)
                if unresolvable is not None:
                    return (
                        "composed Decimal results require resolvable precision and scale: "
                        f"{unresolvable}"
                    )
            # The same qualification parameters yield the exact distinct and
            # quantile requirements for this Metric's own authorities: both
            # lower through the shared Observation lowerer beside the shared
            # cumulative and status-time fold branches, so a qualified state
            # keeps the Metric on the ordinary SourceStep execution path.
            state_qualified = any(
                item.metric_ref == metric.key and item.target_kind in distinct_memberships
                for item in definition.distinct_memberships
            ) or any(
                item.metric_ref == metric.key
                and item.quantile.method == "linear_interpolation@v1"
                and "linear_interpolation" in distributions
                for item in definition.distributions
            )
            if metric.cumulative:
                # The shared exact cumulative endpoint-window lowering is not
                # private state, but any other requirement on a cumulative
                # Metric keeps the historical rejection: only non-cumulative
                # graphs lower status-time folds through the shared fold
                # branches.
                exempt = {_CUMULATIVE_REQUIREMENT}
            else:
                exempt = {_CUMULATIVE_REQUIREMENT, *_FOLD_REQUIREMENTS}
            if state_qualified:
                exempt = {*exempt, "metric.source_distinct@v1"}
            if set(metric.source_requirements) - exempt:
                # The shared exact cumulative and status-time fold lowerings are
                # not private state; every other source requirement stays
                # rejected, including on cumulative Metrics.
                return "this Metric requires source-private state this backend has not qualified"
            elif any(
                component.time_fold is not None and isinstance(component.time_fold, tuple)
                for component in metric.components
            ):
                # Percentile-tuple folds lower through backend quantile
                # aggregates that no backend has qualified.
                return "the aggregate requires unqualified private or temporal state"
            elif any(component.time_fold is not None for component in metric.components) and not (
                status_folds
                and all(
                    _fold_kind(component.time_fold) in status_folds
                    for component in metric.components
                    if component.time_fold is not None
                )
            ):
                return "status-time folds require additional temporal-state qualification"
            elif metric.requires_source_recompute and not (
                metric.cumulative
                or state_qualified
                or any(component.time_fold is not None for component in metric.components)
            ):
                return "this Metric requires source-private state this backend has not qualified"
            if not supported_type(metric.logical_type):
                return "the Metric result type is not supported"
            for component in metric.components:
                if component.computation_root.path not in entity_paths:
                    return "the Metric computation root is outside the declared source closure"
                if component.time_fold is not None and (
                    not status_folds or _fold_kind(component.time_fold) not in status_folds
                ):
                    return "status-time folds require additional temporal-state qualification"
                if component.status_time_dimension is not None and component.time_fold is None:
                    return "status-time folds require additional temporal-state qualification"
                if component.requires_source_recompute and not (
                    metric.cumulative or state_qualified or component.time_fold is not None
                ):
                    return "the Metric requires source-private recomputation"
            for record in metric.graph.nodes:
                node = record.node
                if isinstance(node, (RatioNodeV1, CumulativeNodeV1)):
                    continue
                if linear_graphs and isinstance(node, LinearNodeV1):
                    continue
                if not isinstance(
                    node, (AggregateNodeV1, WeightedMeanAggregateNodeV1, SliceNodeV1)
                ):
                    return "the Metric graph contains an unqualified computation"
                conditions = node.predicates if isinstance(node, SliceNodeV1) else node.filter
                if any(
                    not dimension(
                        normalize_target_dimension(
                            owner.semantic_registry, condition.dimension_ref.path
                        )
                    )
                    for condition in conditions
                ):
                    return "a Metric slice requires an unsupported dimension or time parser"
                if isinstance(node, (SliceNodeV1, LinearNodeV1)):
                    continue
                references: tuple[RefPayloadV1, ...]
                if isinstance(node, AggregateNodeV1):
                    # State-node admission mirrors the per-Metric authority yield:
                    # an aggregate lowers through private-state qualification only
                    # when its owning Metric carries the matching authority, so a
                    # qualified sibling never admits an authority-less node.
                    if isinstance(node.agg, str):
                        admitted = (
                            node.agg in {"sum", "count", "min", "max", "mean"}
                            or (
                                node.agg == "count_distinct"
                                and any(
                                    item.metric_ref == metric.key
                                    and item.target_kind in distinct_memberships
                                    for item in definition.distinct_memberships
                                )
                            )
                            or (
                                node.agg == "median"
                                and any(
                                    item.metric_ref == metric.key
                                    and item.quantile.method == "linear_interpolation@v1"
                                    and "linear_interpolation" in distributions
                                    for item in definition.distributions
                                )
                            )
                        )
                    else:
                        admitted = any(
                            item.metric_ref == metric.key
                            and item.quantile.method == "linear_interpolation@v1"
                            and "linear_interpolation" in distributions
                            for item in definition.distributions
                        )
                    if not admitted:
                        return "the aggregate requires unqualified private or temporal state"
                    if isinstance(node.fold, tuple):
                        return "the aggregate requires unqualified private or temporal state"
                    if node.fold is not None and (
                        not status_folds or node.fold not in status_folds
                    ):
                        return "the aggregate requires unqualified private or temporal state"
                    references = (node.target_ref,)
                else:
                    references = (node.value_ref, node.weight_ref)
                for target in references:
                    if target.kind.value == "measure" and not any(
                        reference.path == target.path
                        and reference.kind == target.kind
                        and (
                            body.source_column is not None
                            or (row_expressions and body.source_column is None)
                        )
                        for reference, body in owner.sidecar.bodies.items()
                    ):
                        return "only direct-column measures are qualified for this backend"
    if explicit_decimal_sources and any(
        column.declared_type == "decimal"
        for entry in dependencies.entries
        for column in entry.columns
    ):
        return "Decimal source columns require explicit precision and scale"
    return None
