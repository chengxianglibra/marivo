"""One-source Ibis lowering of the authored private Observation graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal, cast
from zoneinfo import ZoneInfo

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.comparison import lower_compare
from marivo.analysis.compiler.distinct_fold import fold_memberships
from marivo.analysis.compiler.distribution import frequency_quantile, source_quantile
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.event_continuation import canonical_rows as canonical_event_rows
from marivo.analysis.compiler.event_continuation import result_proof as event_result_proof
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledRelationFence,
    CompiledSampleFence,
    CompiledValidation,
    RetainedPartSpec,
)
from marivo.analysis.compiler.normalize import (
    logical_roots,
    required_entities,
    required_source_dependencies,
)
from marivo.analysis.compiler.predicates import lower_bound_predicate, predicate_leaves
from marivo.analysis.compiler.private_parts import (
    PrivateRelations,
    comparison_private_parts,
    private_part_specs,
    private_part_validations,
    selected_private_parts,
)
from marivo.analysis.compiler.source_dependencies import SourceDependencies
from marivo.analysis.compiler.source_time import (
    boundary_instant,
    entity_engine,
    malformed_strptime_rows,
    source_time,
)
from marivo.analysis.compiler.temporal import bucket, bucket_end, cumulative_start
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    DatasetRowSetContract,
    _canonical_digest,
    _CatalogFieldIdentity,
    _EntityFieldIdentity,
    _OrderedOrdering,
)
from marivo.analysis.datasets.handles import (
    CanonicalValue,
    LogicalRootHandle,
    MaterializedScanLeafHandle,
)
from marivo.analysis.domains.completeness import EventCoverageResolution, resolve_event_coverage
from marivo.analysis.domains.contracts import (
    EventFunnelPayload,
    EventFunnelSemantics,
    EventPayload,
    EventSelectionPayload,
    EventTimeToEventPayload,
    EventTimeToEventSemantics,
)
from marivo.analysis.domains.event_attribution import FunnelAttributePayload
from marivo.analysis.domains.event_comparison import FunnelComparePayload, FunnelDeltaSemantics
from marivo.analysis.domains.lifecycle import LifecyclePayload, LifecycleSemantics
from marivo.analysis.domains.lifecycle_reducers import (
    LifecycleReducerPayload,
    LifecycleSelectionPayload,
)
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricDefinition,
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    RankSpec,
    RetainedRowsPayload,
    _version_selection_payload,
    metric_contracts,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import (
    functional_path,
    governed_path,
    relationship_columns,
)
from marivo.analysis.observation.distinct_contracts import (
    DISTINCT_KEY_COLUMN as DISTINCT_KEY,
)
from marivo.analysis.observation.distribution_contracts import (
    FREQUENCY,
    VALUE,
)
from marivo.analysis.observation.fold_contracts import (
    FoldSpecV1,
    MetricFoldAuthorityV1,
    RetainedFoldPayload,
    coverage_columns,
    decode_fold_authority,
    fold_part_role,
    fold_state_names,
)
from marivo.analysis.observation.population_sample import PopulationSamplePayload
from marivo.analysis.observation.private_parts import source_private_part_authorities
from marivo.analysis.observation.temporal import (
    SourceTimeAuthority,
    TemporalExecution,
    civil_bound,
    time_zone,
)
from marivo.analysis.operators.association_contracts import CorrelatePayload, association_orders
from marivo.analysis.operators.attribution_contracts import (
    AttributePayload,
    delta_part_authorities,
    delta_presence_name,
    delta_state_name,
)
from marivo.analysis.operators.candidate_contracts import CandidateDefinition, CandidatePayload
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.driver_contracts import (
    DriverCandidateDefinition,
    DriverCandidatePayload,
)
from marivo.refs import EntityKind, Ref, SemanticKind, _create_ref, _decode_ref_payload
from marivo.semantic._expression_binding import (
    ExpressionBody,
    evaluate_expression_body,
)
from marivo.semantic.decimal_precision import DecimalPrecision, DecimalType, min_max, sum_of
from marivo.semantic.ir import (
    AggKind,
    HourPrefixParse,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeAnchorV1,
    CumulativeNodeV1,
    LinearNodeV1,
    MetricGraphNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
    component_node,
    component_predicate,
)
from marivo.semantic.metric_graph_lowering import _derive_measure_result_type
from marivo.semantic.validator import (
    normalize_target_dimension,
    normalize_target_entity,
    normalize_target_version_selection,
)


@dataclass(frozen=True, slots=True, repr=False)
class _Selection:
    expression: ir.Table
    definition: MetricDefinition
    axis_expansion: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class _Rows:
    expression: ir.Table
    membership: ir.Table
    entity: TargetEntityContract
    definition: MetricDefinition | None = None
    selections: tuple[_Selection, ...] = ()
    ordering: tuple[tuple[str, str, str], ...] = ()
    parts: PrivateRelations = ()


def _authored_orders(
    row: DatasetRowContract, rows: DatasetRowSetContract
) -> dict[str, tuple[str | int, ...]]:
    orders = association_orders(row, rows)
    if isinstance(row.family_semantics, FunnelDeltaSemantics):
        orders["step_key"] = tuple(
            step.key for step in row.family_semantics.current.journey.pattern.steps
        )
    return orders


def _named_validations(
    validations: tuple[CompiledValidation, ...],
    preparations: tuple[CompiledValidation | CompiledSampleFence | CompiledRelationFence, ...] = (),
) -> tuple[
    tuple[CompiledValidation, ...],
    tuple[CompiledValidation | CompiledSampleFence | CompiledRelationFence, ...],
]:
    """Give every executed assertion a stable distinct receipt name across shared branches."""
    counts: dict[str, int] = {}
    renamed: dict[int, CompiledValidation] = {}
    for check in validations:
        occurrence = counts.get(check.name, 0) + 1
        counts[check.name] = occurrence
        renamed[id(check)] = replace(
            check, name=check.name if occurrence == 1 else f"{check.name}.occurrence_{occurrence}"
        )
    return tuple(renamed[id(check)] for check in validations), tuple(
        renamed[id(check)] if isinstance(check, CompiledValidation) else check
        for check in preparations
    )


def _boolean(value: ir.Value) -> ir.BooleanValue:
    if not isinstance(value, ir.BooleanValue):
        raise compilation_error("Boolean Ibis predicate", "unexpected expression type")
    return value


def _numeric(value: ir.Value) -> ir.NumericValue:
    if not isinstance(value, ir.NumericValue):
        raise compilation_error("numeric sufficient state", "unexpected expression type")
    return value


def _linear_coefficient(coefficient: float) -> int | float:
    """Return one linear term's coefficient without degrading typed terms.

    Integral coefficients become ``int`` literals so an int64 term stays int64
    and a Decimal term stays Decimal; non-integral coefficients stay float.
    """
    return int(coefficient) if float(coefficient).is_integer() else coefficient


def _identity(table: ir.Table, entity: TargetEntityContract) -> ir.StructValue:
    result = ibis.struct({name: table[name] for name in entity.primary_key})
    if not isinstance(result, ir.StructValue):
        raise compilation_error("typed ordered identity struct", "unexpected identity type")
    return result


def _and(predicates: list[ir.BooleanValue]) -> ir.BooleanValue:
    if not predicates:
        return ibis.literal(True)
    result = predicates[0]
    for predicate in predicates[1:]:
        result = result & predicate
    return result


_HOUR_LITERAL = r"^([01]?[0-9]|2[0-3])$"
_HOUR_PADDING = r"\s"


def _hour_range_violations(table: ir.Table, name: str) -> ir.Table:
    """Select cells outside the declared 0-23 integer-literal hour contract.

    The verdict is made on the raw cell, so string and integer hour columns
    share one value domain: implicit casts differ per engine and would turn
    malformed cells into legal hours instead of a diagnosable rejection.

    Branches are ordered most-specific-first, because the value classes are not
    disjoint: ``BooleanValue`` and ``GeoSpatialValue`` both subclass
    ``NumericValue`` while comparing them against an integer is undefined.
    Types outside these branches are judged through their text rendering.
    """
    column = table[name]
    if isinstance(column, ir.BooleanValue) or column.type().is_geospatial():
        # A flag or geometry is an admissible declared type but not a 0-23
        # integer literal, so the returned relation keeps every row: all cells
        # violate the contract. Accepting one would publish an implicit
        # true/false -> 1/0 hour or an opaque value.
        return table
    if isinstance(column, ir.NumericValue) and not isinstance(column, ir.IntegerValue):
        # Compared on the raw cell: a cast here would raise an engine-specific
        # conversion error for values no destination type can represent.
        return table.filter(column.isnull() | (column < 0) | (column > 23) | (column % 1 != 0))
    if isinstance(column, ir.IntegerValue):
        return table.filter(column.isnull() | (column < 0) | (column > 23))
    text = column if isinstance(column, ir.StringValue) else column.cast("string")
    if not isinstance(text, ir.StringValue):
        raise compilation_error("a string hour representation", "invalid hour representation")
    # The pattern is the whole value domain (0-23 as one or two digits), so no
    # engine cast participates: `^([01]?[0-9]|2[0-3])$` accepts exactly those
    # literals. A standalone `\s` test rejects padded and trailing-newline cells
    # that some engines' line-anchored regexes would otherwise accept.
    # `isnull()` is load-bearing: the pattern test alone is NULL-valued for a
    # missing cell, and a top-level NULL predicate silently drops the row
    # instead of failing the check.
    return table.filter(
        text.isnull() | text.re_search(_HOUR_PADDING) | ~text.re_search(_HOUR_LITERAL)
    )


def _hidden(metric: TargetMetricContract, node_id: str, state: str) -> str:
    digest = _canonical_digest((metric.key, node_id))[:20]
    return f"__mv_{digest}_{state}"


def _state_names(metric: TargetMetricContract) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _hidden(metric, component.node_id, state)
            for component in metric.components
            for state in component.required_state
        )
    )


def retained_part_specs(row: DatasetRowContract) -> tuple[RetainedPartSpec, ...]:
    """Resolve exact required roles from the frozen current row contract."""
    from marivo.analysis.domains.event_attribution import (
        COMPONENT_COLUMNS,
        COMPONENT_CONTRACT,
        COMPONENT_ROLE,
        FunnelAttributionSemantics,
    )

    if isinstance(row.family_semantics, FunnelAttributionSemantics):
        keys = tuple(f.name for f in row.schema.columns if f.field_id in row.key_field_ids)
        return (
            RetainedPartSpec(COMPONENT_ROLE, COMPONENT_CONTRACT, 1, (*keys, *COMPONENT_COLUMNS)),
        )
    semantics = row.family_semantics
    if row.shape_id.family_id == "delta":
        keys = tuple(
            field.name for field in row.schema.columns if field.field_id in row.key_field_ids
        )
        return tuple(
            RetainedPartSpec(
                role,
                "delta.sufficient_components",
                1,
                (
                    *keys,
                    *(
                        delta_state_name(role.rsplit(".", 1)[1], name)
                        for name in fold_state_names(authority)
                    ),
                    delta_presence_name(role.rsplit(".", 1)[1]),
                ),
            )
            for role, authority in delta_part_authorities(row)
        )
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        return ()
    required = {binding[0].value for binding in semantics.metric_bindings if binding[3]}
    keys = tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)
    return tuple(
        RetainedPartSpec(
            fold_part_role(authority),
            "metric.sufficient_components",
            1,
            (*keys, *fold_state_names(authority)),
        )
        for authority in semantics.metric_folds
        if authority.field_id in required
    )


def _state_projection(row: DatasetRowContract) -> tuple[str, ...]:
    keys = {field.name for field in row.schema.columns if field.field_id in row.key_field_ids}
    return tuple(
        dict.fromkeys(
            name
            for part in retained_part_specs(row)
            for name in part.column_names
            if name not in keys
        )
    )


def _declared_cast(
    value: ir.Value, logical_type: str, declared_decimal: DecimalPrecision | None = None
) -> ir.Value:
    """Cast one metric/measure result to its declared publication type.

    Decimal results carry exact precision and scale: either the resolved
    physical type (direct-column measures, already type-verified against the
    declared source) or the rule-derived ``(p, s)`` passed by the caller for
    ibis-misinferred computed row expressions. A resolved physical decimal
    normalizes to the declared target through the one-step value-exact rule:
    scale must not narrow, and the physical integer positions (precision minus
    scale) must not narrow — precision may only shrink by trimming high-order
    positions that cannot carry a value digit. An integral physical value
    embeds exactly into a declared scale-0 decimal. Every other shape is a
    structured failure, never a silent reinterpretation.
    """
    target = dt.dtype(logical_type)
    if logical_type == "decimal":
        physical = value.type()
        if declared_decimal is not None:
            exact_integral = physical.is_integer() and declared_decimal.scale == 0
            if not exact_integral:
                if (
                    not isinstance(physical, dt.Decimal)
                    or physical.precision is None
                    or physical.scale is None
                ):
                    raise compilation_error(
                        "resolved exact Decimal precision and scale",
                        "unknown Decimal physical type",
                    )
                if (
                    physical.scale > declared_decimal.scale
                    or physical.precision - physical.scale
                    > declared_decimal.precision - declared_decimal.scale
                ):
                    raise compilation_error(
                        (
                            "a Decimal physical type that normalizes to the declared "
                            f"({declared_decimal.precision}, {declared_decimal.scale}) "
                            "through the value-exact rule"
                        ),
                        (
                            f"Decimal({physical.precision}, {physical.scale}) narrows the "
                            "declared scale or integer digit positions"
                        ),
                    )
            target = dt.Decimal(declared_decimal.precision, declared_decimal.scale)
        else:
            if (
                not isinstance(physical, dt.Decimal)
                or physical.precision is None
                or physical.scale is None
            ):
                raise compilation_error(
                    "resolved exact Decimal precision and scale", "unknown Decimal physical type"
                )
            target = physical
    return ops.Cast(value, to=target).to_expr()


def _source_type_matches(actual: dt.DataType, declared: str) -> bool:
    expected = dt.dtype(declared)
    if actual == expected or (declared == "decimal" and isinstance(actual, dt.Decimal)):
        return True
    return (
        isinstance(actual, dt.Timestamp)
        and isinstance(expected, dt.Timestamp)
        and expected.scale is None
        and actual.scale in (None, 0, 1, 2, 3, 4, 5, 6)
        and expected.timezone == actual.timezone
        and expected.nullable == actual.nullable
    )


def _physical_casts(expression: ir.Table) -> ir.Table:
    # DuckDB widens integer SUM physically while Ibis retains its int64 type.
    return expression.select(
        **{
            name: ops.Cast(expression[name], to=expression[name].type()).to_expr()
            if expression[name].type().is_numeric() or expression[name].type().is_temporal()
            else expression[name]
            for name in expression.columns
        }
    )


def _fold_zone(semantics: EntityPresentMetricSemantics | EntityReducedMetricSemantics) -> str:
    authority = decode_fold_authority(semantics.fold_authority)
    snapshot = authority.temporal_snapshot()
    return authority.report_time.timezone if snapshot is None else snapshot.boundary_timezone


def _duration_seconds(start: ir.Value, end: ir.Value) -> ir.NumericValue:
    left, right = start.cast("timestamp"), end.cast("timestamp")
    if not isinstance(left, ir.TimestampValue) or not isinstance(right, ir.TimestampValue):
        raise compilation_error("exact retained interval endpoints", "invalid coverage interval")
    # Ibis's TimestampDelta has no SQLite/MySQL rule, so the shared epoch-second
    # difference is the one lowering every backend translates natively and
    # exactly over the admitted whole-second civil timestamps; the fractional
    # microseconds cancel because both endpoints are exact instant bounds.
    return (right.epoch_seconds() - left.epoch_seconds()).cast("float64")


def _fold_value(table: ir.Table, authority: MetricFoldAuthorityV1) -> ir.Value:
    nodes = {node.node_id: node for node in authority.nodes}
    components = {component.node_id: component for component in authority.components}

    def value(node_id: str) -> ir.Value:
        node = nodes[node_id]
        if node.kind == "component":
            component = components[node_id]
            names = dict(component.state_columns)
            if component.kind == "count":
                return table[names["count"]].fill_null(0)
            if component.kind in ("min", "max"):
                return table[names[component.kind]]
            if component.kind == "mean":
                return _numeric(table[names["sum"]]) / _numeric(
                    table[names["non_null_count"]]
                ).nullif(0)
            if component.kind == "weighted_mean":
                return _numeric(table[names["weighted_numerator"]]) / _numeric(
                    table[names["weight_sum"]]
                ).nullif(0)
            result = table[names["sum" if component.kind == "sum" else "value"]]
            return result.fill_null(0) if component.empty_rule == "zero" else result
        if node.kind == "identity":
            return value(node.children[0])
        if node.kind == "ratio":
            return _numeric(value(node.children[0])) / _numeric(value(node.children[1])).nullif(0)
        result = _numeric(value(node.children[0])) * _linear_coefficient(node.coefficients[0])
        for child, coefficient in zip(node.children[1:], node.coefficients[1:], strict=True):
            result = result + _numeric(value(child)) * _linear_coefficient(coefficient)
        return result

    return value(authority.root_id)


def lower_fold(
    table: ir.Table, spec: FoldSpecV1
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Merge the exact current rows/state; never evaluate a source Metric graph."""
    semantics = spec.input_row.family_semantics
    if not isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        raise compilation_error("exact retained Metric fold authority", "invalid fold input")
    output_fields = {field.field_id.value: field for field in spec.output_row.schema.columns}
    keys = tuple(
        field.name
        for field in spec.output_row.schema.columns
        if field.field_id in spec.output_row.key_field_ids
    )
    source_time = next(
        (
            field.name
            for field in spec.input_row.schema.columns
            if field.role_id == "time_dimension"
        ),
        None,
    )
    target_time = next(
        (
            field.name
            for field in spec.output_row.schema.columns
            if field.role_id == "time_dimension"
        ),
        None,
    )
    if spec.axis == "time" and spec.grain is not None:
        if source_time is None or target_time is None:
            raise compilation_error("one exact current time coordinate", "missing fold coordinate")
        table = table.mutate(
            **{
                target_time: bucket(
                    table[source_time], spec.grain, semantics.fold_temporal_snapshot
                )
            }
        )
    aggregates: dict[str, ir.Value] = {}
    unpack: dict[str, tuple[str, str]] = {}
    validations: list[CompiledValidation] = []
    for authority in semantics.metric_folds:
        for component in authority.components:
            merge = component.time_merge if spec.axis == "time" else component.spatial_merge
            if merge == "blocked":
                raise compilation_error(
                    "one registered exact axis fold", f"{authority.metric_ref}: blocked fold"
                )
            if merge in ("first", "last"):
                endpoint = coverage_columns(authority)[0]
                name = (
                    "__mv_fold_" + _canonical_digest((authority.metric_ref, component.node_id))[:20]
                )
                selected = ibis.struct(
                    {state: table[column] for state, column in component.state_columns}
                )
                aggregates[name] = (
                    selected.argmin(table[endpoint])
                    if merge == "first"
                    else selected.argmax(table[endpoint])
                )
                for state, column in component.state_columns:
                    unpack[column] = (name, state)
            else:
                for state, column in component.state_columns:
                    value = _numeric(table[column])
                    aggregates[column] = (
                        value.min()
                        if merge == "min" and state in ("value", "min", "max")
                        else value.max()
                        if merge == "max" and state in ("value", "min", "max")
                        else value.sum()
                    )
        if authority.cumulative:
            endpoint, start, end, seconds, complete = coverage_columns(authority)
            if spec.axis != "time":
                equality = _and(
                    [
                        table[name].identical_to(
                            table[name]
                            .first()
                            .over(ibis.window(group_by=[table[key] for key in keys]))
                        )
                        for name in (endpoint, start, end, seconds, complete)
                    ]
                )
                invalid = table.filter(~equality)
                validations.append(
                    CompiledValidation(
                        f"{authority.metric_ref}.fold_endpoint_coverage_alignment",
                        invalid.aggregate(violations=invalid.count()),
                    )
                )
                empty = (
                    _and([table[name].isnull() for name in (endpoint, start, end)])
                    & table[seconds].identical_to(0)
                    & table[complete].identical_to(False)
                )
                contiguous = table[seconds] == _duration_seconds(table[start], table[end])
                invalid = table.filter(~(empty | contiguous).fill_null(False))
                validations.append(
                    CompiledValidation(
                        f"{authority.metric_ref}.fold_contiguous_coverage",
                        invalid.aggregate(violations=invalid.count()),
                    )
                )
                for name in (endpoint, start, end, seconds, complete):
                    aggregates[name] = table[name].first()
            else:
                aggregates[endpoint] = table[endpoint].max()
                aggregates[start] = table[start].min()
                aggregates[end] = table[end].max()
                aggregates[seconds] = _numeric(table[seconds]).sum()
                aggregates[complete] = _boolean(table[complete]).all()
    grouped = (
        table.group_by(keys).aggregate(**aggregates) if keys else table.aggregate(**aggregates)
    )
    if unpack:
        grouped = grouped.mutate(
            **{column: grouped[parent][child] for column, (parent, child) in unpack.items()}
        )
    counters = {
        column
        for authority in semantics.metric_folds
        for component in authority.components
        for state, column in component.state_columns
        if state.endswith("count")
    }
    grouped = grouped.mutate(
        **{column: grouped[column].fill_null(0) for column in sorted(counters)}
    )
    for authority in semantics.metric_folds:
        if authority.cumulative and spec.axis == "time":
            _, start, end, seconds, complete = coverage_columns(authority)
            grouped = grouped.mutate(
                **{
                    seconds: grouped[seconds].fill_null(0),
                    complete: grouped[complete].fill_null(False),
                }
            )
            if spec.grain is not None and target_time is not None:
                target_start = grouped[target_time].cast("timestamp")
                target_end = bucket_end(target_start, spec.grain, semantics.fold_temporal_snapshot)
                if decode_fold_authority(semantics.fold_authority).instant_coverage:
                    target_start = boundary_instant(_fold_zone(semantics), target_start)
                    target_end = boundary_instant(_fold_zone(semantics), target_end)
            elif semantics.fold_time_scope is not None:
                fold = decode_fold_authority(semantics.fold_authority)
                target_start = ibis.literal(
                    civil_bound(
                        semantics.fold_time_scope.start,
                        report=fold.report_time.timezone,
                        boundary="UTC",
                        civil_date=not fold.instant_coverage,
                    )
                ).cast("timestamp")
                target_end = ibis.literal(
                    civil_bound(
                        semantics.fold_time_scope.end,
                        report=fold.report_time.timezone,
                        boundary="UTC",
                        civil_date=not fold.instant_coverage,
                    )
                ).cast("timestamp")
            else:
                target_start, target_end = grouped[start], grouped[end]
            duration = _duration_seconds(target_start, target_end)
            grouped = grouped.mutate(
                **{
                    complete: _boolean(grouped[complete])
                    & (grouped[start] == target_start)
                    & (grouped[end] == target_end)
                    & (grouped[seconds] == duration)
                }
            )
        for node in authority.nodes:
            if node.kind == "ratio" and node.zero_division == "error":
                child_authority = authority.model_copy(update={"root_id": node.children[1]})
                invalid = grouped.filter(_fold_value(grouped, child_authority) == 0)
                validations.append(
                    CompiledValidation(
                        f"{authority.metric_ref}.nonzero_denominator",
                        invalid.aggregate(violations=invalid.count()),
                    )
                )
    values = {
        output_fields[authority.field_id].name: _declared_cast(
            _fold_value(grouped, authority),
            output_fields[authority.field_id].logical_type_id,
        )
        for authority in semantics.metric_folds
    }
    result = grouped.mutate(**values)
    return result.select(
        *[field.name for field in spec.output_row.schema.columns],
        *_state_projection(spec.output_row),
    ), tuple(validations)


class _Compiler:
    def __init__(
        self,
        dataset: LogicalDataset,
        tables: Mapping[str, ir.Table],
        scans: Mapping[str, CompiledArtifactScan],
        source_owner: ObservationOwner | None = None,
        event_coverages: Mapping[str, EventCoverageResolution] | None = None,
        read_timezone: str | None = None,
        read_timezone_source: Literal["engine", "system_fallback"] = "engine",
        dependencies: SourceDependencies | None = None,
        replay_exact_quantile: bool = False,
        scalar_identity_distinct: bool = False,
    ) -> None:
        self.dataset = dataset
        self.owner = source_owner_of(dataset) if source_owner is None else source_owner
        self.registry = self.owner.semantic_registry
        self.read_timezone = read_timezone
        self.read_timezone_source = read_timezone_source
        self.replay_exact_quantile = replay_exact_quantile
        self.scalar_identity_distinct = scalar_identity_distinct
        self.time_authorities: dict[tuple[str, str], SourceTimeAuthority] = {}
        self.version_selections: dict[str, CanonicalValue] = {}
        dependencies = (
            required_source_dependencies(dataset, registry=self.registry)
            if dependencies is None
            else dependencies
        )
        if any(entry.owner is not self.owner for entry in dependencies.entries):
            raise compilation_error("the exact source dependency owner", "source binding mismatch")
        self.tables = dict(tables)
        self.scans = scans
        self.datasets: dict[int, Dataset] = {}

        def collect(value: Dataset) -> None:
            self.datasets[id(value._root)] = value
            if isinstance(value, LogicalDataset):
                for child in value._inputs:
                    collect(child)

        collect(dataset)
        self.validations: list[CompiledValidation] = []
        self.attribution_proof: ir.Table | None = None
        self.association_proof: ir.Table | None = None
        self.candidate_proof: ir.Table | None = None
        self.candidate_definition: CandidateDefinition | DriverCandidateDefinition | None = None
        self.lifecycle_coverage: EventCoverageResolution | None = None
        self.lifecycle_reducer_coverage: EventCoverageResolution | None = None
        self.lifecycle_selection_payload: LifecycleSelectionPayload | None = None
        self.lifecycle_selection_proof: ir.Table | None = None
        self.event_proof: ir.Table | None = None
        self.event_coverage: EventCoverageResolution | None = None
        self.event_coverages = dict(event_coverages or {})
        self.event_reducer_coverage: EventCoverageResolution | None = None
        self.selection_proof: ir.Table | None = None
        self.selection_coverage: EventCoverageResolution | None = None
        self.selection_payload: EventSelectionPayload | None = None
        self.selection_input_definition: str | None = None
        self.event_proofs: dict[int, ir.Table] = {}
        self.validation_occurrences: dict[str, int] = {}
        self.preparations: list[
            CompiledValidation | CompiledSampleFence | CompiledRelationFence
        ] = []
        self.prepared_validation_count = 0
        self.samples: dict[int, ir.Table] = {}
        self.cache: dict[int, _Rows] = {}
        self.entities = required_entities(dataset, registry=self.registry)
        if set(tables) != {entity.ref.path for entity in self.entities}:
            raise compilation_error(
                "exact reachable declared Entity table mapping", "missing or extra source table"
            )
        for entity in self.entities:
            table = tables[entity.ref.path]
            needed = dependencies.for_entity(entity).columns
            if any(column.logical not in table.columns for column in needed):
                raise compilation_error(
                    "ordered declared semantic source columns", "source schema mismatch"
                )
            if any(
                not _source_type_matches(table[column.logical].type(), column.declared_type)
                for column in needed
            ):
                raise compilation_error(
                    "exact declared semantic source types", "source type mismatch"
                )
            table = table.select(*(column.logical for column in needed))
            self.tables[entity.ref.path] = table
            self._validate_source(entity, table)
        validated_axes: set[str] = set()

        def validate(axis: TargetDimensionContract | None, zone: str | None = None) -> None:
            if axis is None or not axis.is_time_dimension or axis.ref.path in validated_axes:
                return
            self._validate_temporal_axis(axis, zone)
            validated_axes.add(axis.ref.path)

        for root in logical_roots(dataset):
            payload = root.payload
            if isinstance(payload, PopulationPayload):
                validate(payload.reference_axis)
                for axis, zone in self._version_time_axes(payload.entity):
                    validate(axis, zone)
            elif isinstance(payload, MetricPayload):
                definition = payload.definition
                validate(definition.reference_axis)
                validate(definition.time_axis)
                for axis in definition.dimensions:
                    validate(axis)
                for axis, zone in self._version_time_axes(definition.entity):
                    validate(axis, zone)

    def _version_time_axes(
        self, entity: TargetEntityContract
    ) -> tuple[tuple[TargetDimensionContract, str | None], ...]:
        """Resolve one Entity's own version coordinates and their read boundary zone.

        No payload field names these axes, yet the population version filter
        reads each one through :meth:`_time_column`, so they carry the same
        gap/fold duty.  Each is resolved here with the exact zone its lowering
        site passes, so the guard registers the same authority the read will
        reuse instead of a second, possibly disagreeing one.
        """
        version = entity.version
        if version is None:
            return ()
        if isinstance(version, TargetSnapshotVersion):
            zone = version.timezone or self.read_timezone or self.owner.report_time.timezone
            axis = normalize_target_dimension(self.registry, version.coordinate_ref.path)
            return ((axis, zone),)
        return tuple(
            (normalize_target_dimension(self.registry, ref.path), "UTC")
            for ref in (version.valid_from_ref, version.valid_to_ref)
        )

    def _validate_temporal_axis(
        self, axis: TargetDimensionContract, zone: str | None = None
    ) -> None:
        table = self.tables[axis.entity_ref.path]
        walls: dict[str, ir.TimestampValue] = {}
        self._time_column(table, axis.source_column, axis, zone, wall=walls)
        self._validate_strptime_cells(axis, table)
        # The gap/fold guard reads the wall clock the axis contributes, which
        # every naive time-bearing parse produces; gating it on the physical
        # column would let a parsed axis publish a gap or a repeated hour.
        key = (axis.ref.path, zone or self.owner.report_time.timezone)
        wall = walls.get(axis.ref.path)
        authority = self.time_authorities.get(key)
        if wall is None or authority is None or authority.read_timezone is None:
            return
        from marivo.analysis.compiler.source_time import local_time_invalid

        self._count(
            "temporal.local_time." + axis.ref.path,
            table.filter(local_time_invalid(wall, authority.read_timezone)),
        )

    def _validate_strptime_cells(self, axis: TargetDimensionContract, table: ir.Table) -> None:
        """Fail on unparseable cells where the engine answers them with NULL.

        SQLite, MySQL and ClickHouse return NULL for a cell the declared format
        cannot read. Without this assertion the row would silently leave the
        time axis; the count turns that into a structured failure before
        publication instead.
        """
        parse = axis.parse
        if not isinstance(parse, StrptimeParse):
            return
        malformed = malformed_strptime_rows(
            self._axis_engine(axis), table[axis.source_column], parse
        )
        if malformed is None:
            return
        self._count(
            "temporal.strptime_format." + axis.ref.path,
            malformed,
            expected=(
                "every non-null string cell parses under the declared strptime "
                f"format {parse.format[:80]!r}"
            ),
            repair=(
                f"Correct the physical cells behind {axis.ref.path} (declared on "
                f"{axis.entity_ref.path}) to match {parse.format[:80]!r}, or "
                "redeclare the axis with the format the stored text actually uses."
            ),
        )

    def _axis_engine(self, axis: TargetDimensionContract) -> str:
        """Resolve the engine owning *axis*, which may differ from the Metric root."""
        entity = next(
            (item for item in self.entities if item.ref.path == axis.entity_ref.path), None
        )
        if entity is None:
            raise compilation_error(
                "a declared Entity for every governed time axis", "unresolved time axis entity"
            )
        return entity_engine(self.registry, entity)

    def _axis_value(
        self,
        value: ir.Value,
        axis: TargetDimensionContract,
        zone: str | None = None,
        prefix: ir.Value | None = None,
        *,
        wall: dict[str, ir.TimestampValue] | None = None,
    ) -> ir.Value:
        if not axis.is_time_dimension:
            return value
        result, authority, resolved = source_time(
            value,
            axis,
            boundary_timezone=zone or self.owner.report_time.timezone,
            read_timezone=self.read_timezone,
            engine=self._axis_engine(axis),
            read_source=self.read_timezone_source,
            prefix=prefix,
        )
        self.time_authorities[(axis.ref.path, authority.boundary_timezone)] = authority
        if wall is not None and resolved is not None:
            # The gap/fold guard judges the same wall clock this call resolved,
            # whatever physical representation the axis reads it from.
            wall[axis.ref.path] = resolved
        return result

    def _prefix_axis(self, axis: TargetDimensionContract) -> TargetDimensionContract | None:
        parse = axis.parse
        if not isinstance(parse, HourPrefixParse):
            return None
        prefix = normalize_target_dimension(self.registry, parse.prefix)
        if prefix.entity_ref != axis.entity_ref or prefix.logical_type != "date":
            raise compilation_error(
                "a same-Entity civil-date hour prefix", "invalid composite time axis"
            )
        return prefix

    def _require_prefix_axis(self, axis: TargetDimensionContract) -> TargetDimensionContract:
        prefix = self._prefix_axis(axis)
        if prefix is None:
            raise compilation_error("an exact composite prefix", "missing prefix")
        return prefix

    def _time_column(
        self,
        table: ir.Table,
        name: str,
        axis: TargetDimensionContract,
        zone: str | None = None,
        *,
        wall: dict[str, ir.TimestampValue] | None = None,
    ) -> ir.Value:
        prefix_axis = self._prefix_axis(axis)
        prefix = None
        if prefix_axis is not None:
            prefix_name = (
                name + "__prefix"
                if name + "__prefix" in table.columns
                else prefix_axis.source_column
            )
            prefix = self._axis_value(table[prefix_name], prefix_axis, zone)
            self._count(
                "temporal.hour_range",
                _hour_range_violations(table, name),
                expected="one non-null 0-23 integer hour literal per hour-prefix cell",
                repair=(
                    f"Correct the physical cell behind {axis.ref.path} (declared on "
                    f"{axis.entity_ref.path}) to a plain 0-23 hour value: unpadded "
                    "one- or two-digit text or an integer in range, with no nulls."
                ),
            )
        return self._axis_value(table[name], axis, zone, prefix, wall=wall)

    def _zone(self, definition: MetricDefinition) -> str:
        return (
            definition.report_time.timezone
            if definition.temporal_snapshot is None
            else definition.temporal_snapshot.boundary_timezone
        )

    def _bound(
        self, value: date | datetime, definition: MetricDefinition | None = None
    ) -> date | datetime:
        zone = self.owner.report_time.timezone if definition is None else self._zone(definition)
        axis = None if definition is None else definition.reference_axis or definition.time_axis
        return civil_bound(
            value,
            report=self.owner.report_time.timezone,
            boundary=zone,
            civil_date=axis is not None and axis.logical_type == "date",
        )

    def _scope_bound(
        self, value: date | datetime, axis: TargetDimensionContract
    ) -> date | datetime:
        return civil_bound(
            value,
            report=self.owner.report_time.timezone,
            boundary="UTC",
            civil_date=axis.logical_type == "date",
        )

    def _instant(
        self, value: ir.Value, definition: MetricDefinition, axis: TargetDimensionContract
    ) -> ir.Value:
        if axis.logical_type == "date":
            return value
        return boundary_instant(self._zone(definition), value)

    def _cumulative_bounds(
        self,
        end: ir.Value,
        definition: MetricDefinition,
        axis: TargetDimensionContract,
        anchor: CumulativeAnchorV1,
        *,
        bucket_start: ir.Value | None = None,
    ) -> tuple[ir.Value, ir.Value | None]:
        instant_end = self._instant(end, definition, axis)
        if definition.time_scope is not None:
            instant_end = ibis.least(
                instant_end,
                ibis.literal(self._scope_bound(definition.time_scope.end, axis)).cast("timestamp"),
            )
        if anchor != "all_history" and anchor[0] == "trailing":
            return instant_end, cumulative_start(anchor, instant_end, definition.temporal_snapshot)
        lower = cumulative_start(
            anchor, end, definition.temporal_snapshot, bucket_start=bucket_start
        )
        return instant_end, None if lower is None else self._instant(lower, definition, axis)

    def _count(
        self,
        name: str,
        table: ir.Table,
        *,
        expected: str | None = None,
        repair: str | None = None,
    ) -> None:
        occurrence = self.validation_occurrences.get(name, 0)
        self.validation_occurrences[name] = occurrence + 1
        if occurrence:
            name = f"{name}.occurrence.{occurrence}"
        self.validations.append(
            CompiledValidation(
                name, table.aggregate(violations=table.count()), expected=expected, repair=repair
            )
        )

    def _unique(self, name: str, table: ir.Table, keys: tuple[str, ...]) -> None:
        grouped = table.group_by(list(keys)).aggregate(__mv_count=table.count())
        self._count(name, grouped.filter(grouped["__mv_count"] > 1))

    def _validate_source(self, entity: TargetEntityContract, table: ir.Table) -> None:
        prefix = entity.ref.path
        if entity.primary_key:
            non_null = _and([table[name].notnull() for name in entity.primary_key])
            self._count(f"{prefix}.identity_non_null", table.filter(~non_null))
            for name in entity.primary_key:
                identity_column = table[name]
                if isinstance(identity_column, ir.FloatingValue):
                    self._count(
                        f"{prefix}.identity_finite.{name}",
                        table.filter(identity_column.isnan() | identity_column.isinf()),
                    )
            self._unique(f"{prefix}.source_row_unique", table, entity.version_row_key)
        version = entity.version
        if isinstance(version, TargetSnapshotVersion):
            self._count(
                f"{prefix}.snapshot_non_null", table.filter(table[version.source_column].isnull())
            )
        elif isinstance(version, TargetValidityVersion):
            start_axis = normalize_target_dimension(self.registry, version.valid_from_ref.path)
            end_axis = normalize_target_dimension(self.registry, version.valid_to_ref.path)
            open_end = self._open_end(table[version.valid_to_column], version.open_end)
            table = table.mutate(__mv_open_end=open_end)
            table = table.mutate(
                **{
                    version.valid_to_column: table["__mv_open_end"].ifelse(
                        ibis.null(), table[version.valid_to_column]
                    )
                }
            )
            table = table.mutate(
                __mv_valid_start=self._time_column(
                    table, version.valid_from_column, start_axis, "UTC"
                ),
                __mv_valid_end=self._time_column(table, version.valid_to_column, end_axis, "UTC"),
            )
            start, end = table["__mv_valid_start"], table["__mv_valid_end"]
            open_end = table["__mv_open_end"]
            self._unique(
                f"{prefix}.parsed_version_unique", table, (*entity.primary_key, "__mv_valid_start")
            )
            invalid = start.isnull() | (
                ~open_end
                & _boolean(end <= start if version.interval == "closed_open" else end < start)
            )
            self._count(f"{prefix}.validity_well_formed", table.filter(invalid))
            left, right = table, table.view()
            keys = [_boolean(left[name] == right[name]) for name in entity.primary_key]
            earlier = _boolean(left["__mv_valid_start"] < right["__mv_valid_start"])
            left_end = left["__mv_valid_end"]
            overlaps = left["__mv_open_end"] | _boolean(
                left_end > right["__mv_valid_start"]
                if version.interval == "closed_open"
                else left_end >= right["__mv_valid_start"]
            )
            self._count(
                f"{prefix}.validity_non_overlapping",
                left.join(right, [*keys, earlier, overlaps], how="inner"),
            )

    @staticmethod
    def _open_end(column: ir.Value, values: tuple[str | None, ...]) -> ir.BooleanValue:
        result = ibis.literal(False)
        for value in values:
            result = result | (
                column.isnull()
                if value is None
                else _boolean(column == ibis.literal(value).cast(column.type()))
            )
        return result

    def _enrich(
        self,
        table: ir.Table,
        source: str,
        target: str,
        columns: tuple[tuple[str, str], ...],
        *,
        selected_target: ir.Table | None = None,
    ) -> ir.Table:
        extras = []
        for name, alias in columns:
            for dimension in self.registry.dimensions.values():
                if (
                    dimension.entity == target
                    and dimension.source_column == name
                    and isinstance(dimension.parse, HourPrefixParse)
                ):
                    axis = normalize_target_dimension(self.registry, dimension.semantic_id)
                    prefix_axis = self._prefix_axis(axis)
                    if prefix_axis is not None:
                        extras.append((prefix_axis.source_column, alias + "__prefix"))
        columns = (*columns, *dict.fromkeys(extras))
        if source == target:
            return table.mutate(**{alias: table[name] for name, alias in columns})
        route = functional_path(self.registry, source, target, allow_versioned_target=True)
        original = tuple(table.columns)
        current = source
        mapping = {name: name for name in table.columns}
        for index, relation_id in enumerate(route):
            relation = self.registry.relationships[relation_id]
            forward = current == relation.from_entity
            destination = relation.to_entity if forward else relation.from_entity
            right_source = (
                selected_target
                if destination == target and selected_target is not None
                else self.tables[destination]
            ).view()
            prefix = f"__mv_join_{index}_"
            renamed = {name: prefix + name for name in right_source.columns}
            right = right_source.select(**{new: right_source[old] for old, new in renamed.items()})
            conditions = [
                _boolean(
                    table[mapping[left_key if forward else right_key]]
                    == right[renamed[right_key if forward else left_key]]
                )
                for left_key, right_key in relationship_columns(self.registry, relation)
            ]
            table = table.join(right, conditions, how="left")
            mapping = renamed
            current = destination
        return table.select(
            *(name for name in original if name not in {alias for _, alias in columns}),
            **{alias: table[mapping[name]] for name, alias in columns},
        )

    def _population(self, root: LogicalRootHandle, payload: PopulationPayload) -> _Rows:
        entity = payload.entity
        selection = payload.version_selection
        version = entity.version
        if version is not None and payload.time_scope is not None:
            axis_ref = (
                version.coordinate_ref
                if isinstance(version, TargetSnapshotVersion)
                else version.valid_from_ref
            )
            version_axis = normalize_target_dimension(self.registry, axis_ref.path)
            end = payload.time_scope.end
            boundary = (
                end if isinstance(end, datetime) else datetime.combine(end, datetime.min.time())
            )
            if version_axis.logical_type != "date" and boundary.tzinfo is None:
                boundary = boundary.replace(tzinfo=time_zone(payload.report_time.timezone))
            zone = version.timezone or (
                self.read_timezone
                if version_axis.logical_type != "date"
                else payload.report_time.timezone
            )
            if zone is None:
                raise compilation_error(
                    "resolved version read timezone", "missing version authority"
                )
            resolved_zone = time_zone(zone)
            version_timezone = zone if isinstance(resolved_zone, ZoneInfo) else None
            if version_timezone is None and isinstance(version, TargetSnapshotVersion):
                boundary = (
                    boundary.replace(tzinfo=resolved_zone)
                    if boundary.tzinfo is None
                    else boundary.astimezone(resolved_zone)
                ).replace(tzinfo=None)
            selection = normalize_target_version_selection(
                replace(entity, version=replace(version, timezone=version_timezone)),
                boundary=boundary,
                interpretation="before_endpoint",
            )
            self.version_selections[root.definition_fingerprint] = _version_selection_payload(
                selection
            )
        if root.inputs:
            previous = self._visit(root.inputs[0].root)
            table = previous.membership
            if payload.predicate is not None:
                current = self.tables[entity.ref.path]
                missing = table.join(current, list(entity.primary_key), how="anti")
                self._count("population.enrichment_identity_complete", missing)
                table = current.join(
                    table.select(*entity.primary_key), list(entity.primary_key), how="semi"
                )
        else:
            table = self.tables[entity.ref.path]
            if isinstance(version, TargetSnapshotVersion) and isinstance(
                selection, TargetSnapshotSelection
            ):
                version_axis = normalize_target_dimension(
                    self.registry, version.coordinate_ref.path
                )
                version_value = self._time_column(
                    table,
                    version.source_column,
                    version_axis,
                    version.timezone or self.read_timezone or self.owner.report_time.timezone,
                )
                table = table.filter(
                    version_value == ibis.literal(selection.period).cast(version_value.type())
                )
                self.validations.append(
                    CompiledValidation(
                        f"{entity.ref.path}.exact_snapshot_available",
                        table.aggregate(violations=(table.count() == 0).cast("int64")),
                    )
                )
            elif isinstance(version, TargetValidityVersion) and isinstance(
                selection, TargetValiditySelection
            ):
                start_axis = normalize_target_dimension(self.registry, version.valid_from_ref.path)
                end_axis = normalize_target_dimension(self.registry, version.valid_to_ref.path)
                start = self._time_column(table, version.valid_from_column, start_axis, "UTC")
                end = self._time_column(table, version.valid_to_column, end_axis, "UTC")
                boundary = ibis.literal(
                    self._scope_bound(datetime.fromisoformat(selection.boundary), start_axis)
                ).cast(start.type())
                start_ok = _boolean(
                    start < boundary if selection.start_operator == "lt" else start <= boundary
                )
                end_ok = _boolean(
                    end > boundary if selection.end_operator == "gt" else end >= boundary
                )
                table = table.filter(start_ok & (end_ok | self._open_end(end, selection.open_end)))
            if payload.time_scope is not None and payload.reference_axis is not None:
                axis = payload.reference_axis
                table = self._enrich(
                    table,
                    entity.ref.path,
                    axis.entity_ref.path,
                    ((axis.source_column, "__mv_scope"),),
                )
                column = self._time_column(table, "__mv_scope", axis, "UTC")
                table = table.filter(
                    column >= self._scope_bound(payload.time_scope.start, axis),
                    column < self._scope_bound(payload.time_scope.end, axis),
                ).drop("__mv_scope")
            if version is not None:
                self._unique(
                    f"{entity.ref.path}.selected_identity_unique", table, entity.primary_key
                )
        for predicate in predicate_leaves(payload.predicate):
            field = predicate.field
            if field is None or not isinstance(field.identity, _CatalogFieldIdentity):
                raise compilation_error(
                    "membership Dimension identity", "invalid predicate identity"
                )
            dimension = normalize_target_dimension(
                self.registry, field.identity.identity_id.split(":", 1)[1]
            )
            table = self._enrich(
                table,
                entity.ref.path,
                dimension.entity_ref.path,
                ((dimension.source_column, field.name),),
            )
        if payload.predicate is not None:
            table = table.filter(lower_bound_predicate(table, payload.predicate))
        if root.operator_id == "population.sample":
            policy = payload.sampling
            if policy is None or len(root.realizations) != 1:
                raise compilation_error(
                    "one exact Entity sample realization", "missing sample policy"
                )
            handle_key = id(root.realizations[0].handle)
            sampled = self.samples.get(handle_key)
            if sampled is None:
                if policy.target_rows > 1_000_000_000 or (
                    policy.seed is not None and not 0 <= policy.seed <= 2**31 - 1
                ):
                    raise compilation_error(
                        "DuckDB reservoir target at most 1000000000 and seed in [0, 2147483647]",
                        "unsupported physical sampling request",
                    )
                name = f"__mv_sample_{len(self.samples)}"
                self._flush_validations()
                self.preparations.append(
                    CompiledSampleFence(
                        relation_name=name,
                        expression=table,
                        policy=policy,
                        population_definition_fingerprint=root.definition_fingerprint,
                        target_population_definition_fingerprint=payload.target_population_definition_fingerprint
                        or "",
                        identity_columns=entity.primary_key,
                        root_identity=id(root),
                    )
                )
                sampled = ibis.table(table.schema(), name=name)
                self.samples[handle_key] = sampled
            table = sampled
        return _Rows(table.select(entity_identity=_identity(table, entity)), table, entity)

    def _flush_validations(self) -> None:
        self.preparations.extend(self.validations[self.prepared_validation_count :])
        self.prepared_validation_count = len(self.validations)

    def _measure_expression(self, reference_path: str) -> ExpressionBody:
        """Resolve one Measure reference to its normalized expression body.

        Direct-column measures carry their physical column on
        ``body.source_column``; computed measures carry the full row-expression
        body. Callers evaluate computed bodies on the owning entity's table in
        the same scan that feeds the aggregate.
        """
        for reference, body in self.owner.sidecar.bodies.items():
            if reference.kind is SemanticKind.MEASURE and reference.path == reference_path:
                return body
        raise compilation_error("a normalized Measure", "missing expression binding")

    def _measure_column(self, reference_path: str) -> ir.Value:
        """Evaluate one Measure reference on the computation root's table.

        Direct-column measures return the physical column expression; computed
        measures evaluate their row-expression body on the measure's owning
        entity table at this lowering point, inside the current scan. The
        required entity table is already present because normalization
        collected the measure's source columns.
        """
        body = self._measure_expression(reference_path)
        measure = self.registry.measures.get(reference_path)
        if measure is None:
            raise compilation_error("a loaded Measure", f"unknown Measure {reference_path!r}")
        entity_path = measure.entity
        table = self.tables[entity_path]
        if body.source_column is not None:
            return table[body.source_column]
        entity_ref = cast(
            "Ref[EntityKind]",
            _decode_ref_payload(normalize_target_entity(self.registry, entity_path).ref),
        )
        return evaluate_expression_body(
            catalog_definition_fingerprint=reference_path,
            expression_sidecar=self.owner.sidecar,
            owning_ref=_create_ref(SemanticKind.MEASURE, reference_path),
            body=body,
            entity_refs=(entity_ref,),
            aliases=(table,),
        )

    def _resolve_coordinate_path(
        self, definition: MetricDefinition, source: str, axis: TargetDimensionContract
    ) -> tuple[str, ...]:
        for binding in definition.coordinate_paths:
            if binding.ref == axis.ref.path:
                if source == definition.entity.ref.path:
                    return binding.spine_path
                for root, path in binding.component_paths:
                    if root == source:
                        return path
        return functional_path(
            self.registry, source, axis.entity_ref.path, allow_versioned_target=True
        )

    def _coordinate_rows(
        self,
        table: ir.Table,
        source: str,
        definition: MetricDefinition,
        *,
        spine: bool = False,
        filter_axes: tuple[TargetDimensionContract, ...] | None = None,
    ) -> ir.Table:
        """Join shared coordinate paths once, preserving their governed tuples."""
        original = tuple(table.columns)
        aliases: dict[str, tuple[str, TargetDimensionContract, str | None]] = {}
        mappings: dict[tuple[str, ...], tuple[str, dict[str, str]]] = {
            (): (source, {name: name for name in table.columns})
        }
        axes = (
            filter_axes
            if filter_axes is not None
            else (
                *definition.dimensions,
                *((definition.time_axis,) if definition.time_axis else ()),
            )
        )
        for axis_index, axis in enumerate(axes):
            route = (
                governed_path(self.registry, source, axis.entity_ref.path)
                if filter_axes is not None
                else self._resolve_coordinate_path(definition, source, axis)
            )
            for depth in range(1, len(route) + 1):
                prefix = route[:depth]
                if prefix in mappings:
                    continue
                current, mapping = mappings[prefix[:-1]]
                relationship = self.registry.relationships[prefix[-1]]
                forward = current == relationship.from_entity
                destination = relationship.to_entity if forward else relationship.from_entity
                right_source = self.tables[destination].view()
                renamed = {
                    name: f"__mv_coord_{len(mappings)}_{name}" for name in right_source.columns
                }
                right = right_source.select(
                    **{alias: right_source[name] for name, alias in renamed.items()}
                )
                conditions = [
                    _boolean(
                        table[mapping[left_key if forward else right_key]]
                        == right[renamed[right_key if forward else left_key]]
                    )
                    for left_key, right_key in relationship_columns(self.registry, relationship)
                ]
                destination_keys = tuple(
                    right_key if forward else left_key
                    for left_key, right_key in relationship_columns(self.registry, relationship)
                )
                target = next(item for item in self.entities if item.ref.path == destination)
                fanout = set(destination_keys) != set(target.primary_key)
                table = table.join(right, conditions, how="inner" if spine and fanout else "left")
                mappings[prefix] = destination, renamed
            _, mapping = mappings[route]
            alias = (
                f"__mv_filter_{axis_index}"
                if filter_axes is not None
                else axis.ref.path.rsplit(".", 1)[-1]
            )
            prefix_axis = self._prefix_axis(axis)
            aliases[alias] = (
                mapping[axis.source_column],
                axis,
                None if prefix_axis is None else mapping[prefix_axis.source_column],
            )
        projected: dict[str, ir.Value] = {}
        for alias, (name, axis, prefix_name) in aliases.items():
            prefix_value = (
                None
                if prefix_name is None
                else self._axis_value(
                    table[prefix_name], self._require_prefix_axis(axis), self._zone(definition)
                )
            )
            projected[alias] = self._axis_value(
                table[name], axis, self._zone(definition), prefix_value
            )
            if axis.is_time_dimension and axis.logical_type == "timestamp":
                projected["__mv_instant_" + alias] = self._axis_value(
                    table[name], axis, "UTC", prefix_value
                )
        table = table.select(*(name for name in original if name not in projected), **projected)
        if definition.time_axis is not None and filter_axes is None:
            name = definition.time_axis.ref.path.rsplit(".", 1)[-1]
            if spine and definition.time_scope is not None:
                axis = definition.time_axis
                column = (
                    table["__mv_instant_" + name]
                    if axis.logical_type == "timestamp"
                    else table[name]
                )
                lower: ir.Value = ibis.literal(self._scope_bound(definition.time_scope.start, axis))
                if any(metric.cumulative for metric in definition.metrics):
                    display_start = bucket(
                        ibis.literal(self._bound(definition.time_scope.start, definition)),
                        definition.grain,
                        definition.temporal_snapshot,
                    )
                    lower = ibis.least(lower, self._instant(display_start, definition, axis))
                table = table.filter(
                    column >= lower, column < self._scope_bound(definition.time_scope.end, axis)
                )
            if definition.temporal_snapshot is not None:
                snapshot = definition.temporal_snapshot
                column = table[name]
                self._count(
                    "calendar.coordinate_coverage",
                    table.filter(
                        column.notnull()
                        & ((column < snapshot.coverage[0]) | (column >= snapshot.coverage[1]))
                    ),
                )
            table = table.mutate(
                **{name: bucket(table[name], definition.grain, definition.temporal_snapshot)}
            )
        return table

    def _selected_contributions(
        self,
        table: ir.Table,
        source: str,
        selections: tuple[_Selection, ...],
        *,
        coordinates_present: bool = False,
    ) -> ir.Table:
        original = tuple(table.columns)
        for selection in selections:
            if not coordinates_present:
                table = self._coordinate_rows(table, source, selection.definition)
            keys = tuple(selection.expression.columns)
            right = selection.expression.view()
            conditions = [table[name].identical_to(right[name]) for name in keys]
            table = table.join(right, conditions, how="semi")
        return table.select(*original)

    def _spine(
        self, definition: MetricDefinition, membership: ir.Table, selections: tuple[_Selection, ...]
    ) -> ir.Table:
        table = membership.mutate(entity_identity=_identity(membership, definition.entity))
        if (definition.dimensions or definition.time_axis is not None) and (
            definition.entity.version is not None
            or not set(self.tables[definition.entity.ref.path].columns).issubset(membership.columns)
        ):
            source_rows = self.tables[definition.entity.ref.path].view()
            source_rows = source_rows.mutate(
                entity_identity=_identity(source_rows, definition.entity)
            )
            if definition.entity.version is None:
                self._count(
                    "metric.enrichment_identity_complete",
                    table.join(
                        source_rows.select("entity_identity"), "entity_identity", how="anti"
                    ),
                )
            table = source_rows.join(table.select("entity_identity"), "entity_identity", how="semi")
        expansion = next((item for item in reversed(selections) if item.axis_expansion), None)
        if (
            expansion is not None
            and definition.time_axis is not None
            and len(definition.metrics) == 1
            and definition.metrics[0].cumulative
            and definition.distinct_memberships
        ):
            table = self._distinct_expansion_spine(table, definition, expansion)
        else:
            table = self._coordinate_rows(table, definition.entity.ref.path, definition, spine=True)
        table = self._selected_contributions(
            table, definition.entity.ref.path, selections, coordinates_present=True
        )
        names = [axis.ref.path.rsplit(".", 1)[-1] for axis in definition.dimensions]
        if definition.time_axis is not None:
            name = definition.time_axis.ref.path.rsplit(".", 1)[-1]
            names.append(name)
        if definition.entity_present:
            names.insert(0, "entity_identity")
        if not names:
            return table.aggregate(__mv_scalar=table.count())
        return table.select(*names).distinct()

    def _distinct_expansion_spine(
        self, table: ir.Table, definition: MetricDefinition, selection: _Selection
    ) -> ir.Table:
        """Partition selected cumulative endpoints using their complete prior contributions."""
        axis, grain = definition.time_axis, definition.grain
        if axis is None or grain is None:
            raise compilation_error("exact cumulative expansion endpoints", "missing time contract")
        time_name = axis.ref.path.rsplit(".", 1)[-1]
        cumulative = definition.metrics[0].cumulative[0]
        # Resolve all authored coordinates together so fanout paths retain one
        # governed tuple. Keep raw historical time until it is bound to a selected
        # endpoint; normal display bucketing would discard earlier contributions.
        coordinates = replace(
            definition,
            dimensions=(*definition.dimensions, axis),
            time_axis=None,
            grain=None,
        )
        table = self._coordinate_rows(
            table, definition.entity.ref.path, coordinates, spine=True
        ).view()
        endpoints = selection.expression.view()
        if time_name not in endpoints.columns:
            raise compilation_error("selected original cumulative times", "missing expansion time")
        end = bucket_end(endpoints[time_name], grain, definition.temporal_snapshot)
        if definition.time_scope is not None:
            end = ibis.least(
                end,
                ibis.literal(self._bound(definition.time_scope.end, definition)).cast("timestamp"),
            )
        conditions = [
            table[name].identical_to(endpoints[name])
            for name in endpoints.columns
            if name != time_name
        ]
        occurrence = (
            table["__mv_instant_" + time_name]
            if axis.logical_type == "timestamp"
            else table[time_name]
        )
        end, lower = self._cumulative_bounds(
            bucket_end(endpoints[time_name], grain, definition.temporal_snapshot),
            definition,
            axis,
            cumulative.anchor,
            bucket_start=endpoints[time_name],
        )
        conditions.append(_boolean(occurrence < end))
        if lower is not None:
            conditions.append(_boolean(occurrence >= lower))
        joined = table.join(endpoints, conditions, how="inner")
        return joined.select(
            **{
                name: endpoints[time_name] if name == time_name else table[name]
                for name in table.columns
            }
        )

    def _measure_decimal_facts(self, path: str, agg: AggKind) -> DecimalPrecision | None:
        """Derive one computed measure's declared decimal aggregate type.

        The row-expression's decimal type follows the semantic rule-table walk
        over the entity's declared column types (engine ibis inference is
        unreliable for computed decimal expressions), and the aggregate type
        follows the same aggregation rules normalization applies. Non-decimal
        results return ``None`` and keep the physical cast path.
        """
        body = self._measure_expression(path)
        if body.source_column is not None:
            return None
        measure = self.registry.measures.get(path)
        if measure is None:
            raise compilation_error("a loaded Measure", f"unknown Measure {path!r}")
        entity = normalize_target_entity(self.registry, measure.entity)
        columns = dict(entity.columns)
        placeholder = ibis.table(columns, name=path)
        expression = evaluate_expression_body(
            catalog_definition_fingerprint=path,
            expression_sidecar=self.owner.sidecar,
            owning_ref=_create_ref(SemanticKind.MEASURE, path),
            body=body,
            entity_refs=(cast("Ref[EntityKind]", _decode_ref_payload(entity.ref)),),
            aliases=(placeholder,),
        )
        derived = _derive_measure_result_type(expression.op())
        try:
            row_type = DecimalPrecision.from_string(derived)
        except ValueError:
            # The walk reports non-decimal results (for example int64 minus
            # int64) as their ibis-inferred type; they keep the physical cast.
            return None
        bounded = DecimalType(row_type.precision, row_type.scale)
        if agg == "sum":
            summed = sum_of(bounded)
            return DecimalPrecision(summed.precision, summed.scale)
        if agg in ("min", "max"):
            extreme = min_max(bounded)
            return DecimalPrecision(extreme.precision, extreme.scale)
        return None

    def _component(
        self,
        metric: TargetMetricContract,
        node_id: str,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...] = (),
    ) -> tuple[ir.Table, ir.Table | None]:
        node = component_node(metric.graph, node_id)
        component = next(item for item in metric.components if item.node_id == node_id)
        root = component.computation_root.path
        table = self.tables[root]
        target = definition.entity
        aliases = tuple(
            (name, f"__mv_identity_{index}") for index, name in enumerate(target.primary_key)
        )
        table = self._enrich(table, root, target.ref.path, aliases, selected_target=membership)
        identity = ibis.struct({name: table[alias] for name, alias in aliases})
        table = table.mutate(entity_identity=identity)
        population = membership.select(entity_identity=_identity(membership, target)).distinct()
        table = table.join(population, "entity_identity", how="semi")
        reference_axis = definition.reference_axis
        if metric.cumulative:
            reference_axis = normalize_target_dimension(
                self.registry, metric.cumulative[0].over_ref.path
            )
        if definition.time_scope is not None and reference_axis is not None:
            axis = reference_axis
            table = self._enrich(
                table, root, axis.entity_ref.path, ((axis.source_column, "__mv_scope"),)
            )
            column = self._time_column(table, "__mv_scope", axis, "UTC")
            table = table.filter(column < self._scope_bound(definition.time_scope.end, axis))
            if not metric.cumulative:
                table = table.filter(column >= self._scope_bound(definition.time_scope.start, axis))
            elif definition.time_axis is None:
                _, boundary = self._cumulative_bounds(
                    ibis.literal(self._bound(definition.time_scope.end, definition)),
                    definition,
                    axis,
                    metric.cumulative[0].anchor,
                )
                if boundary is not None:
                    table = table.filter(column >= boundary)
        if not isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
            raise compilation_error("registered aggregate component", "unsupported graph node")
        original_columns = tuple(table.columns)
        if node.filter:
            table = self._coordinate_rows(
                table,
                root,
                definition,
                filter_axes=tuple(
                    normalize_target_dimension(self.registry, condition.dimension_ref.path)
                    for condition in node.filter
                ),
            )
        for index, condition in enumerate(node.filter):
            alias = f"__mv_filter_{index}"
            op, value = component_predicate(condition.value)
            column = table[alias]
            if op == "in" and isinstance(value, tuple):
                table = table.filter(column.isin(value))
            elif op == "between" and isinstance(value, tuple):
                table = table.filter((column >= value[0]) & (column <= value[1]))
            elif not isinstance(value, tuple):
                if op == "==":
                    predicate = column.isnull() if value is None else column == value
                elif op == "!=":
                    predicate = column.notnull() if value is None else column != value
                elif op == ">":
                    predicate = column > value
                elif op == ">=":
                    predicate = column >= value
                elif op == "<":
                    predicate = column < value
                else:
                    predicate = column <= value
                table = table.filter(predicate)
            else:
                raise compilation_error("a registered component slice", "invalid slice value")
        if node.filter:
            table = table.select(*original_columns).distinct()
        if metric.cumulative and definition.time_axis is not None:
            table = self._cumulative_contributions(
                table, root, metric, definition, membership, selections
            )
        else:
            table = self._selected_contributions(table, root, selections)
            table = self._coordinate_rows(table, root, definition)
        row, _ = metric_contracts(
            definition, self.dataset._registry.get("metric").ids, self.registry
        )
        keys = [field.name for field in row.schema.columns if field.field_id in row.key_field_ids]
        if component.status_time_dimension is not None:
            status_axis = normalize_target_dimension(
                self.registry, component.status_time_dimension.path
            )
            table = self._enrich(
                table,
                root,
                status_axis.entity_ref.path,
                ((status_axis.source_column, "__mv_status"),),
            )
            table = table.mutate(
                __mv_status=self._time_column(
                    table, "__mv_status", status_axis, self._zone(definition)
                )
            )
            self._count(
                f"{metric.key}.status_time_non_null",
                table.filter(table["__mv_status"].isnull()),
            )
            keys.append("__mv_status")
        # A governed coordinate relation denotes a set of contributions. Repeated
        # bridge rows cannot multiply the same source representation in one tuple.
        table = table.distinct()
        private_relation: ir.Table | None = None
        exact_distribution = None
        distinct_members: ir.Table | None = None
        states: dict[str, ir.Value] = {"row_count": table.count()}
        if isinstance(node, AggregateNodeV1):
            value = (
                self._measure_column(node.target_ref.path)
                if node.target_ref.kind is SemanticKind.MEASURE
                else None
            )
            if node.agg == "count":
                states["count"] = value.count() if value is not None else table.count()
            elif node.agg == "count_distinct":
                identity = None
                if value is None:
                    identity = next(item for item in self.entities if item.ref.path == root)
                    value = _identity(table, identity)
                if self.scalar_identity_distinct and identity is not None:
                    fields = tuple(identity.primary_key)
                    complete = table
                    for name in fields:
                        complete = complete.filter(complete[name].notnull())
                    distinct_members = complete.select(
                        *keys,
                        **{
                            f"__mv_member_{index}": complete[name]
                            for index, name in enumerate(fields)
                        },
                    ).distinct()
                    # The final endpoint comes from the same exact member set as the part.
                    states["value"] = states["row_count"]
                else:
                    states["value"] = value.nunique()
                if component.time_fold is None:
                    private_relation = table.select(*keys, **{DISTINCT_KEY: value})
                    if identity is not None:
                        for name in identity.primary_key:
                            private_relation = private_relation.filter(
                                private_relation[DISTINCT_KEY][name].notnull()
                            )
                    else:
                        private_relation = private_relation.filter(
                            private_relation[DISTINCT_KEY].notnull()
                        )
                    private_relation = private_relation.distinct()
            else:
                if value is None:
                    raise compilation_error("numeric Measure aggregate", "missing Measure")
                numeric = _numeric(value)
                if node.agg in ("sum", "mean"):
                    states["sum"] = numeric.sum()
                elif node.agg == "min":
                    states["min"] = numeric.min()
                elif node.agg == "max":
                    states["max"] = numeric.max()
                else:
                    quantile = node.agg[1] if isinstance(node.agg, tuple) else 0.5
                    basis = next(
                        (
                            item
                            for item in definition.distributions
                            if item.metric_ref == metric.key
                        ),
                        None,
                    )
                    states["value"] = (
                        numeric.quantile(quantile)
                        if basis is None
                        else (
                            numeric.max()
                            if self.replay_exact_quantile
                            and basis.quantile.method == "linear_interpolation@v1"
                            else source_quantile(numeric, basis.quantile)
                        )
                    )
                    if basis is not None:
                        values = table.select(*keys, **{VALUE: value}).filter(value.notnull())
                        private_relation = values.group_by([*keys, VALUE]).aggregate(
                            **{FREQUENCY: values.count()}
                        )
                        if (
                            self.replay_exact_quantile
                            and basis.quantile.method == "linear_interpolation@v1"
                        ):
                            exact_distribution = basis.quantile
                states["non_null_count"] = value.count()
        else:
            value = _numeric(self._measure_column(node.value_ref.path))
            weight = _numeric(self._measure_column(node.weight_ref.path))
            pairs = value.notnull() & weight.notnull()
            states["weighted_numerator"] = (value * weight).sum(where=pairs)
            states["weight_sum"] = weight.sum(where=pairs)
            states["non_null_pair_count"] = pairs.cast("int64").sum()
        grouped = table.group_by(keys).aggregate(**states) if keys else table.aggregate(**states)
        if distinct_members is not None:
            member_counts = (
                distinct_members.group_by(keys).aggregate(value=distinct_members.count())
                if keys
                else distinct_members.aggregate(value=distinct_members.count())
            ).view()
            grouped = grouped.view()
            joined = (
                grouped.join(
                    member_counts,
                    [grouped[name].identical_to(member_counts[name]) for name in keys],
                    how="left",
                )
                if keys
                else grouped.cross_join(member_counts)
            )
            grouped = joined.select(
                **{name: grouped[name] for name in grouped.columns if name != "value"},
                value=member_counts.value.fill_null(0),
            )
        if exact_distribution is not None:
            if private_relation is None:
                raise compilation_error("exact distribution relation", "missing private relation")
            endpoints = frequency_quantile(private_relation, tuple(keys), exact_distribution).view()
            grouped = grouped.view()
            joined = (
                grouped.join(
                    endpoints,
                    [grouped[name].identical_to(endpoints[name]) for name in keys],
                    how="left",
                )
                if keys
                else grouped.cross_join(endpoints)
            )
            grouped = joined.select(
                **{name: grouped[name] for name in grouped.columns if name != "value"},
                value=endpoints["__mv_quantile"],
            )
        if component.time_fold is not None:
            if isinstance(node, WeightedMeanAggregateNodeV1):
                spatial = _numeric(grouped["weighted_numerator"]) / _numeric(
                    grouped["weight_sum"]
                ).nullif(0)
            elif node.agg == "mean":
                spatial = _numeric(grouped["sum"]) / _numeric(grouped["non_null_count"]).nullif(0)
            else:
                spatial = grouped[
                    node.agg if isinstance(node.agg, str) and node.agg in states else "value"
                ]
            grouped = grouped.mutate(__mv_spatial=spatial)
            column = _numeric(grouped["__mv_spatial"])
            fold = component.time_fold
            if fold == "first":
                folded = column.argmin(grouped["__mv_status"])
            elif fold == "last":
                folded = column.argmax(grouped["__mv_status"])
            elif fold == "mean":
                folded = column.mean()
            elif fold == "min":
                folded = column.min()
            elif fold == "max":
                folded = column.max()
            elif isinstance(fold, tuple):
                folded = column.quantile(fold[1])
            else:
                raise compilation_error("registered temporal fold", "unknown fold")
            support = (
                "non_null_pair_count"
                if isinstance(node, WeightedMeanAggregateNodeV1)
                else "non_null_count"
            )
            states = {
                "value": folded,
                support: column.count(),
                "row_count": grouped["row_count"].sum(),
            }
            keys.remove("__mv_status")
            grouped = (
                grouped.group_by(keys).aggregate(**states) if keys else grouped.aggregate(**states)
            )
        return (
            grouped.select(
                *keys,
                **{
                    _hidden(metric, node_id, name): grouped[name]
                    for name in component.required_state
                },
            ),
            private_relation,
        )

    def _cumulative_contributions(
        self,
        table: ir.Table,
        source: str,
        metric: TargetMetricContract,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...],
    ) -> ir.Table:
        """Bind each output endpoint to its exact base contribution window."""
        axis = definition.time_axis
        grain = definition.grain
        if axis is None or grain is None:
            raise compilation_error(
                "registered exact cumulative bucket", "unsupported endpoint implementation"
            )
        axis_name = axis.ref.path.rsplit(".", 1)[-1]
        without_time = replace(definition, time_axis=None, grain=None)
        table = self._coordinate_rows(table, source, without_time)
        over = normalize_target_dimension(self.registry, metric.cumulative[0].over_ref.path)
        table = self._enrich(
            table, source, over.entity_ref.path, ((over.source_column, "__mv_cumulative_time"),)
        )
        endpoints = self._spine(definition, membership, selections).view()
        endpoint_names = tuple(endpoints.columns)
        endpoints = endpoints.select(
            **{f"__mv_endpoint_{name}": endpoints[name] for name in endpoint_names}
        )
        start = endpoints[f"__mv_endpoint_{axis_name}"]
        end = bucket_end(start, grain, definition.temporal_snapshot)
        if definition.time_scope is not None:
            # A selected partial last bucket ends at the authored observation end.
            end = ibis.least(
                end,
                ibis.literal(self._bound(definition.time_scope.end, definition)).cast("timestamp"),
            )
        conditions = [
            table[name].identical_to(endpoints[f"__mv_endpoint_{name}"])
            for name in endpoint_names
            if name != axis_name
        ]
        source_time = self._time_column(table, "__mv_cumulative_time", over, "UTC")
        # The window bounds are exact timestamps on every axis; a civil-date
        # axis must join through its exact midnight cast so engines that
        # compare temporals as text (SQLite) cannot prefix-match the next
        # bucket's date into this endpoint's window.
        if isinstance(source_time, ir.DateValue):
            source_time = source_time.cast("timestamp")
        end, lower = self._cumulative_bounds(
            bucket_end(start, grain, definition.temporal_snapshot),
            definition,
            over,
            metric.cumulative[0].anchor,
            bucket_start=start,
        )
        conditions.append(_boolean(source_time < end))
        if lower is not None:
            conditions.append(_boolean(source_time >= lower))
        original = tuple(table.columns)
        table = table.join(endpoints, conditions, how="inner").select(
            *(name for name in original if name != axis_name),
            **{axis_name: endpoints[f"__mv_endpoint_{axis_name}"]},
        )
        return self._selected_contributions(table, source, selections, coordinates_present=True)

    def _values(self, table: ir.Table, definition: MetricDefinition) -> ir.Table:
        counters = {
            _hidden(metric, component.node_id, state)
            for metric in definition.metrics
            for component in metric.components
            for state in component.required_state
            if state.endswith("count")
        }
        table = table.mutate(**{name: table[name].fill_null(0) for name in sorted(counters)})
        row, _ = metric_contracts(
            definition, self.dataset._registry.get("metric").ids, self.registry
        )
        output: dict[str, ir.Value] = {}
        for metric, field in zip(
            definition.metrics,
            (field for field in row.schema.columns if field.role_id == "metric"),
            strict=True,
        ):
            nodes = {record.node_id: record.node for record in metric.graph.nodes}

            def value(
                node_id: str,
                metric: TargetMetricContract = metric,
                nodes: dict[str, MetricGraphNodeV1] = nodes,
            ) -> ir.Value:
                node = nodes[node_id]
                if any(component.node_id == node_id for component in metric.components):
                    node = component_node(metric.graph, node_id)
                if isinstance(node, AggregateNodeV1):
                    component = next(item for item in metric.components if item.node_id == node_id)
                    if component.requires_source_recompute:
                        component_value = table[_hidden(metric, node_id, "value")]
                        return (
                            component_value.fill_null(0)
                            if component.empty_rule == "zero"
                            else component_value
                        )
                    if node.agg == "count":
                        return table[_hidden(metric, node_id, "count")].fill_null(0)
                    if node.agg in ("min", "max"):
                        return table[_hidden(metric, node_id, node.agg)]
                    total = _numeric(table[_hidden(metric, node_id, "sum")])
                    count = _numeric(table[_hidden(metric, node_id, "non_null_count")])
                    return total if node.agg == "sum" else total / count.nullif(0)
                if isinstance(node, WeightedMeanAggregateNodeV1):
                    component = next(item for item in metric.components if item.node_id == node_id)
                    if component.requires_source_recompute:
                        return table[_hidden(metric, node_id, "value")]
                    numerator = _numeric(table[_hidden(metric, node_id, "weighted_numerator")])
                    denominator = _numeric(table[_hidden(metric, node_id, "weight_sum")])
                    return numerator / denominator.nullif(0)
                if isinstance(node, RatioNodeV1):
                    numerator, denominator = (
                        _numeric(value(node.numerator_id)),
                        _numeric(value(node.denominator_id)),
                    )
                    if node.zero_division == "error":
                        self._count(
                            f"{metric.key}.nonzero_denominator", table.filter(denominator == 0)
                        )
                    return numerator / denominator.nullif(0)
                if isinstance(node, LinearNodeV1):
                    terms = [
                        _numeric(value(term.child_id)) * _linear_coefficient(term.coefficient)
                        for term in node.terms
                    ]
                    result = terms[0]
                    for term in terms[1:]:
                        result = result + term
                    return result
                if isinstance(node, SliceNodeV1):
                    return value(node.child_id)
                if isinstance(node, CumulativeNodeV1):
                    return value(node.child_id)
                raise compilation_error(
                    "closed aggregate/weighted/ratio graph", "unsupported graph node"
                )

            root = component_node(metric.graph, metric.components[0].node_id)
            declared_decimal = (
                self._measure_decimal_facts(root.target_ref.path, root.agg)
                if isinstance(root, AggregateNodeV1)
                and root.target_ref.kind is SemanticKind.MEASURE
                and metric.components[0].node_id == metric.graph.roots[0]
                else None
            )
            output[field.name] = _declared_cast(
                value(metric.graph.roots[0]), metric.logical_type, declared_decimal
            )
        return table.mutate(**output)

    def _evaluate(
        self,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...] = (),
    ) -> _Rows:
        table = self._spine(definition, membership, selections)
        row, _ = metric_contracts(
            definition, self.dataset._registry.get("metric").ids, self.registry
        )
        required_private_parts = {
            authority.metric_ref: (role, authority)
            for role, authority in source_private_part_authorities(row)
        }
        retained_private_parts: list[tuple[str, ir.Table]] = []
        for metric in definition.metrics:
            for node_id in dict.fromkeys(component.node_id for component in metric.components):
                part, private_relation = self._component(
                    metric, node_id, definition, membership, selections
                )
                if private_relation is not None and metric.key in required_private_parts:
                    retained_private_parts.append(
                        (required_private_parts[metric.key][0], private_relation)
                    )
                names = _state_names(metric)
                keys = tuple(name for name in part.columns if not name.startswith("__mv_"))
                new_fields = tuple(name for name in part.columns if name in names)
                if keys:
                    right = part.view()
                    table = table.join(
                        right, [table[name].identical_to(right[name]) for name in keys], how="left"
                    ).select(*table.columns, *(right[name] for name in new_fields))
                else:
                    table = table.cross_join(part).select(*table.columns, *new_fields)
        table = self._values(table, definition)
        if (
            definition.time_axis is not None
            and definition.time_scope is not None
            and any(metric.cumulative for metric in definition.metrics)
        ):
            name = definition.time_axis.ref.path.rsplit(".", 1)[-1]
            table = table.filter(
                table[name]
                >= bucket(
                    ibis.literal(self._bound(definition.time_scope.start, definition)),
                    definition.grain,
                    definition.temporal_snapshot,
                )
            )
        if "__mv_scalar" in table.columns:
            table = table.drop("__mv_scalar")
        row, _ = metric_contracts(
            definition, self.dataset._registry.get("metric").ids, self.registry
        )
        semantics = row.family_semantics
        if isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
            for authority in semantics.metric_folds:
                if not authority.cumulative:
                    continue
                endpoint, start_name, end_name, seconds, complete = coverage_columns(authority)
                if definition.time_axis is not None and definition.grain is not None:
                    axis = definition.time_axis.ref.path.rsplit(".", 1)[-1]
                    bucket_start = table[axis].cast("timestamp")
                    bucket_finish = bucket_end(
                        bucket_start, definition.grain, definition.temporal_snapshot
                    )
                elif definition.time_scope is not None:
                    bucket_start = ibis.literal(
                        self._bound(definition.time_scope.start, definition)
                    ).cast("timestamp")
                    bucket_finish = ibis.literal(
                        self._bound(definition.time_scope.end, definition)
                    ).cast("timestamp")
                else:
                    raise compilation_error(
                        "an exact cumulative evaluation boundary", "missing endpoint"
                    )
                time_axis = definition.time_axis or definition.reference_axis
                if time_axis is not None:
                    if definition.time_axis is not None:
                        bucket_start = self._instant(bucket_start, definition, time_axis)
                        bucket_finish = self._instant(bucket_finish, definition, time_axis)
                    elif definition.time_scope is not None:
                        bucket_start = ibis.literal(
                            self._scope_bound(definition.time_scope.start, time_axis)
                        ).cast("timestamp")
                        bucket_finish = ibis.literal(
                            self._scope_bound(definition.time_scope.end, time_axis)
                        ).cast("timestamp")
                start, end = bucket_start, bucket_finish
                if definition.time_scope is not None and time_axis is not None:
                    start = ibis.greatest(
                        start,
                        ibis.literal(
                            self._scope_bound(definition.time_scope.start, time_axis)
                        ).cast("timestamp"),
                    )
                    end = ibis.least(
                        end,
                        ibis.literal(self._scope_bound(definition.time_scope.end, time_axis)).cast(
                            "timestamp"
                        ),
                    )
                table = table.mutate(
                    **{
                        endpoint: end,
                        start_name: start,
                        end_name: end,
                        seconds: _duration_seconds(start, end),
                        complete: (start == bucket_start) & (end == bucket_finish),
                    }
                )
        retained = selected_private_parts(tuple(retained_private_parts), row, table)
        self.validations.extend(private_part_validations(row, table, dict(retained)))
        return _Rows(table, membership, definition.entity, definition, selections, parts=retained)

    def _observe(self, payload: MetricPayload, previous: _Rows) -> _Rows:
        definition = payload.definition
        membership = previous.membership
        selected = previous.expression.select("entity_identity").distinct()
        membership = membership.mutate(entity_identity=_identity(membership, definition.entity))
        membership = membership.join(selected, "entity_identity", how="semi").drop(
            "entity_identity"
        )
        return self._evaluate(definition, membership)

    def _lifecycle_history(self, root: LogicalRootHandle, payload: LifecyclePayload) -> _Rows:
        from marivo.analysis.compiler.event_sources import lower_event_sources
        from marivo.analysis.compiler.lifecycle import compile_replay

        previous = self._visit(root.inputs[0].root)
        identity = previous.expression["entity_identity"]
        if not isinstance(identity, ir.StructValue):
            raise compilation_error("exact subject identity", "invalid Lifecycle membership")
        membership = previous.expression.select(
            **{name: identity[name] for name in payload.definition.entity.primary_key}
        )

        def freeze(table: ir.Table) -> ir.Table:
            self._flush_validations()
            name = f"__mv_lifecycle_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, table, id(root)))
            return ibis.table(table.schema(), name=name)

        membership = freeze(membership)
        occurrences = lower_event_sources(
            payload.definition,
            self.owner,
            self.tables,
            membership,
            freeze=freeze,
            add_validation=self.validations.append,
            from_inception=True,
        )
        coverage = self.event_coverages.get(root.definition_fingerprint) or resolve_event_coverage(
            payload.definition, require_source_origin=True
        )
        table, parts, checks = compile_replay(
            occurrences, membership, payload.semantics, coverage, freeze=freeze
        )
        self.validations.extend(checks)
        self.lifecycle_coverage = coverage
        self.event_coverages[root.definition_fingerprint] = coverage
        return _Rows(table, membership, payload.definition.entity, parts=parts)

    def _event_journey(self, root: LogicalRootHandle, payload: EventPayload) -> _Rows:
        from marivo.analysis.compiler.event import compile_event_match, event_output_proof
        from marivo.analysis.compiler.event_sources import lower_event_sources
        from marivo.analysis.domains.contracts import journey_identity_digest, journey_semantics

        definition = payload.definition
        previous = self._visit(root.inputs[0].root)
        identity = previous.expression["entity_identity"]
        if not isinstance(identity, ir.StructValue):
            raise compilation_error("exact selected subject identities", "invalid Event membership")
        membership = previous.expression.select(
            **{name: identity[name] for name in definition.entity.primary_key}
        )

        def freeze(table: ir.Table) -> ir.Table:
            self._flush_validations()
            name = f"__mv_event_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, table, id(root)))
            return ibis.table(table.schema(), name=name)

        membership = freeze(membership)
        occurrences = lower_event_sources(
            definition,
            self.owner,
            self.tables,
            membership,
            freeze=freeze,
            add_validation=self.validations.append,
        )
        coverage = self.event_coverages.get(root.definition_fingerprint)
        if coverage is None:
            coverage = resolve_event_coverage(definition)
        start, end = definition.cohort_window.start, definition.cohort_window.end
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise compilation_error("aware Event window instants", "invalid cohort window")
        table, checks, _ = compile_event_match(
            occurrences,
            matching=definition.matching,
            cohort_start=start,
            cohort_end=end,
            completion_through=definition.completion_through,
            definition_digest=journey_identity_digest(journey_semantics(definition)),
            coverage_complete=coverage.complete,
        )
        self.validations.extend(checks)
        table = freeze(table)
        self.event_proof = event_output_proof(
            table,
            step_keys=tuple(step.step.key for step in definition.steps),
            event_refs=tuple(step.step.event.path for step in definition.steps),
            matching=definition.matching,
            cohort_start=start,
            cohort_end=end,
            completion_through=definition.completion_through,
            definition_digest=journey_identity_digest(journey_semantics(definition)),
            coverage_complete=coverage.complete,
        )
        self.event_coverages[root.definition_fingerprint] = coverage
        self.event_proofs[id(root)] = self.event_proof
        self.validations.append(
            CompiledValidation("event.journey.output", self.event_proof.select("violations"))
        )
        self.event_coverage = coverage
        return _Rows(table, membership, definition.entity)

    def _lifecycle_reducer(
        self, root: LogicalRootHandle, payload: LifecycleReducerPayload | LifecycleSelectionPayload
    ) -> _Rows:
        from marivo.analysis.compiler.event_axes import lower_event_axes
        from marivo.analysis.compiler.lifecycle_reducers import reduce_lifecycle

        previous = self._visit(root.inputs[0].root)
        incoming = root.inputs[0].root
        key = (
            incoming.artifact_ref.ref
            if isinstance(incoming, MaterializedScanLeafHandle)
            else incoming.definition_fingerprint
        )
        coverage = self.event_coverages.get(key)
        if coverage is None:
            raise compilation_error("exact input Lifecycle coverage", "missing retained coverage")

        def freeze(table: ir.Table) -> ir.Table:
            self._flush_validations()
            name = f"__mv_lifecycle_reducer_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, table, id(root)))
            return ibis.table(table.schema(), name=name)

        def enrich(table: ir.Table, at: datetime) -> ir.Table:
            if not isinstance(payload, LifecycleReducerPayload) or not payload.axes:
                return table
            anchored = table.mutate(
                step_key=ibis.literal("checkpoint"), occurred_at=ibis.literal(at)
            )
            return lower_event_axes(
                anchored,
                payload.axes,
                self.owner,
                self.tables,
                step_key="checkpoint",
                freeze=freeze,
                add_validation=self.validations.append,
            ).drop("step_key", "occurred_at")

        result, checks, proof = reduce_lifecycle(
            previous.expression, dict(previous.parts), payload, freeze=freeze, enrich=enrich
        )
        self.validations.extend(checks)
        self.lifecycle_reducer_coverage = coverage
        if isinstance(payload, LifecycleSelectionPayload):
            self.lifecycle_selection_payload, self.lifecycle_selection_proof = payload, proof
            self.selection_input_definition = self.datasets[id(incoming)].definition_fingerprint
            result = freeze(result)
            identity = result.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error("complete selected identity", "invalid selection output")
            membership = result.select(
                **{name: identity[name] for name in previous.entity.primary_key}
            )
            return _Rows(result, membership, previous.entity)
        return _Rows(result, previous.membership, previous.entity)

    def _event_reducer(
        self,
        root: LogicalRootHandle,
        payload: EventFunnelPayload | EventTimeToEventPayload | EventSelectionPayload,
    ) -> _Rows:
        from marivo.analysis.compiler.event_continuation import reduce_event

        previous = self._visit(root.inputs[0].root)
        incoming = root.inputs[0].root
        key = (
            incoming.artifact_ref.ref
            if isinstance(incoming, MaterializedScanLeafHandle)
            else incoming.definition_fingerprint
        )
        coverage = self.event_coverages.get(key)
        if coverage is None:
            raise compilation_error(
                "exact retained Event coverage", "missing journey coverage authority"
            )

        def freeze(table: ir.Table) -> ir.Table:
            self._flush_validations()
            name = f"__mv_event_reducer_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, table, id(root)))
            return ibis.table(table.schema(), name=name)

        table = freeze(previous.expression)
        if isinstance(payload, EventFunnelPayload) and payload.axes:
            from marivo.analysis.compiler.event_axes import lower_event_axes

            table = lower_event_axes(
                table,
                payload.axes,
                self.owner,
                self.tables,
                step_key=payload.semantics.journey.pattern.steps[0].key,
                freeze=freeze,
                add_validation=self.validations.append,
            )
        result, checks, proof = reduce_event(table, payload, coverage)
        self.validations.extend(checks)
        self.validations.append(
            CompiledValidation("event.reducer.output", proof.select("violations"))
        )
        if isinstance(payload, EventSelectionPayload):
            self.selection_proof = proof
            self.selection_coverage = coverage
            self.selection_payload = payload
            self.selection_input_definition = self.datasets[id(incoming)].definition_fingerprint
            result = freeze(result)
            identity = result.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error(
                    "complete selected identity tuple", "invalid selection output"
                )
            membership = result.select(
                **{name: identity[name] for name in previous.entity.primary_key}
            )
            return _Rows(result, membership, previous.entity)
        self.event_reducer_coverage = coverage
        return _Rows(result, previous.membership, previous.entity)

    def _visit(self, root: LogicalRootHandle | MaterializedScanLeafHandle) -> _Rows:
        if isinstance(root, MaterializedScanLeafHandle):
            scan = self.scans.get(root.artifact_ref.ref)
            dataset = self.datasets[id(root)]
            semantics = dataset.row_contract.family_semantics
            if (
                scan is None
                or not isinstance(dataset, MaterializedDataset)
                or (
                    root.shape_id.family_id not in ("population", "delta", "event", "lifecycle")
                    and not (
                        root.shape_id.family_id == "candidate"
                        and root.shape_id.local_shape_id in ("entity-outlier", "driver-axis")
                        and root.shape_id.semantic_version == 1
                    )
                    and (
                        not isinstance(
                            semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)
                        )
                        or (
                            isinstance(semantics, EntityPresentMetricSemantics)
                            and any(
                                not facts or facts[0] != "entity_unique"
                                for _, _, facts in semantics.coordinate_semantics
                            )
                        )
                    )
                )
            ):
                raise compilation_error(
                    "an admitted engine membership or Entity-unique Metric scan",
                    "unsupported retained source input",
                )
            table, entity = scan.expression, scan.entity
            if isinstance(semantics, (EventFunnelSemantics, EventTimeToEventSemantics)):
                self.event_reducer_coverage = self.event_coverages.get(root.artifact_ref.ref)
            if root.shape_id.family_id in ("metric", "delta"):
                table, checks = _lower_retained_scan(dataset.row_contract, table, dict(scan.parts))
                self.validations.extend(checks)
            identities = tuple(
                field.identity
                for field in dataset.schema.columns
                if isinstance(field.identity, _EntityFieldIdentity)
            )
            if not identities:
                return _Rows(
                    table,
                    table,
                    entity,
                    parts=scan.parts
                    if dataset.kind == "lifecycle"
                    else selected_private_parts(
                        scan.parts, dataset.row_contract, table, required=False
                    ),
                )
            if (
                len(identities) != 1
                or identities[0].entity_ref.path != entity.ref.path
                or identities[0].identity_signature != entity.identity_signature
            ):
                raise compilation_error(
                    "the retained Entity identity signature", "changed identity"
                )
            identity = table["entity_identity"]
            if not isinstance(identity, ir.StructValue):
                raise compilation_error(
                    "the exact retained identity struct", "invalid engine identity"
                )
            membership = table.select(**{name: identity[name] for name in entity.primary_key})
            if (
                root.shape_id.family_id in ("delta", "event", "lifecycle")
                or root.shape_id.local_shape_id == "driver-axis"
            ):
                # Complete row keys are validated separately; driver rows may
                # repeat an Entity across axes. Deduplicate only the internal
                # source membership spine, preserving primary rows and the
                # separate restrictions on population input admission.
                membership = membership.distinct()
            self._unique("population.retained_identity_unique", membership, entity.primary_key)
            return _Rows(
                table,
                membership,
                entity,
                parts=scan.parts
                if dataset.kind == "lifecycle"
                else selected_private_parts(
                    scan.parts, dataset.row_contract, table, required=False
                ),
            )
        if id(root) in self.cache:
            return self.cache[id(root)]
        payload = root.payload
        if isinstance(payload, PopulationPayload):
            result = self._population(root, payload)
        elif isinstance(payload, PopulationSamplePayload):
            previous = self._visit(root.inputs[0].root)
            if len(root.realizations) != 1:
                raise compilation_error("one sample realization", "missing sample handle")
            self._flush_validations()
            name = f"__mv_sample_{len(self.preparations)}"
            identity = previous.expression.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error("complete selected identity tuple", "invalid sample input")
            table = previous.expression.select(
                **{key: identity[key] for key in previous.entity.primary_key}
            )
            self.preparations.append(
                CompiledSampleFence(
                    name,
                    table,
                    payload.policy,
                    root.definition_fingerprint,
                    payload.target_population_definition_fingerprint,
                    previous.entity.primary_key,
                    id(root),
                )
            )
            sampled = ibis.table(table.schema(), name=name)
            result = _Rows(
                sampled.select(entity_identity=_identity(sampled, previous.entity)),
                sampled,
                previous.entity,
            )
        elif isinstance(payload, (LifecycleReducerPayload, LifecycleSelectionPayload)):
            result = self._lifecycle_reducer(root, payload)
        elif isinstance(payload, LifecyclePayload):
            result = self._lifecycle_history(root, payload)
        elif isinstance(payload, EventPayload):
            result = self._event_journey(root, payload)
        elif isinstance(
            payload, (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload)
        ):
            result = self._event_reducer(root, payload)
        elif isinstance(payload, DriverCandidatePayload):
            from marivo.analysis.compiler.attribution import prepare_expanded_attribute
            from marivo.analysis.compiler.driver_candidate import lower_driver_candidate

            previous = self._visit(root.inputs[0].root)
            self._flush_validations()
            name = f"__mv_driver_input_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, previous.expression, id(root)))
            frozen = ibis.table(previous.expression.schema(), name=name)
            original: ir.Table | None = None
            if payload.spec.expanded_compare is not None:
                original = frozen
                current = self._visit(root.inputs[1].root)
                baseline = self._visit(root.inputs[2].root)
                expanded, driver_compare_checks = prepare_expanded_attribute(
                    original, current.expression, baseline.expression, payload.spec
                )
                self.validations.extend(driver_compare_checks)
                self._flush_validations()
                name = f"__mv_driver_expanded_{len(self.preparations)}"
                self.preparations.append(CompiledRelationFence(name, expanded, id(root)))
                frozen = ibis.table(expanded.schema(), name=name)
            table, checks, proof = lower_driver_candidate(frozen, payload.spec, original=original)
            self.validations.extend(checks)
            self.candidate_proof = proof
            self.candidate_definition = payload.spec.definition
            result = _Rows(table, previous.membership, previous.entity)
        elif isinstance(payload, CandidatePayload):
            from marivo.analysis.compiler.entity_candidate import lower_entity_candidate

            previous = self._visit(root.inputs[0].root)
            self._flush_validations()
            name = f"__mv_entity_candidate_{len(self.preparations)}"
            self.preparations.append(CompiledRelationFence(name, previous.expression, id(root)))
            frozen = ibis.table(previous.expression.schema(), name=name)
            table, checks, proof = lower_entity_candidate(frozen, payload.spec)
            self.validations.extend(checks)
            self.candidate_proof = proof
            self.candidate_definition = payload.spec.definition
            identity = table.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error("typed Candidate identity", "invalid lowered identity")
            # Membership projects this scored relation and reuses its frozen Metric input.
            membership = table.select(
                **{name: identity[name] for name in previous.entity.primary_key}
            )
            result = _Rows(table, membership, previous.entity)
        elif isinstance(payload, CorrelatePayload):
            from marivo.analysis.compiler.correlation import lower_correlate

            previous = self._visit(root.inputs[0].root)
            table, checks = lower_correlate(previous.expression, payload.spec)
            self.association_proof = table
            self.validations.extend(checks)
            result = _Rows(table, previous.membership, previous.entity)
        elif isinstance(payload, (FunnelComparePayload, FunnelAttributePayload)):
            from marivo.analysis.compiler.event_attribution import (
                lower_attribute as lower_funnel_attribute,
            )
            from marivo.analysis.compiler.event_comparison import (
                lower_compare as lower_funnel_compare,
            )

            inputs = tuple(self._visit(item.root) for item in root.inputs)
            if isinstance(payload, FunnelComparePayload):
                table, checks = lower_funnel_compare(
                    inputs[0].expression, inputs[1].expression, payload.spec
                )
            else:
                table, checks = lower_funnel_attribute(
                    inputs[0].expression, inputs[1].expression, inputs[2].expression, payload.spec
                )
            self.validations.extend(checks)
            result = _Rows(table, inputs[0].membership, inputs[0].entity)
        elif isinstance(payload, ComparePayload):
            current = self._visit(root.inputs[0].root)
            baseline = self._visit(root.inputs[1].root)
            table, validations = lower_compare(
                current.expression, baseline.expression, payload.spec
            )
            self.validations.extend(validations)
            parts = comparison_private_parts(table, current.parts, baseline.parts, payload.spec)
            self.validations.extend(
                private_part_validations(payload.spec.output_row, table, dict(parts))
            )
            result = _Rows(table, current.membership, current.entity, parts=parts)
        elif isinstance(payload, AttributePayload):
            from marivo.analysis.compiler.attribution import (
                lower_attribute,
                lower_expanded_attribute,
            )

            previous = self._visit(root.inputs[0].root)
            if payload.spec.method in ("distinct_membership@v1", "distribution_shapley@v1"):
                from marivo.analysis.compiler.distinct_attribution import lower_distinct_attribute

                input_table = previous.expression
                parts = previous.parts
                extra_checks: tuple[CompiledValidation, ...] = ()
                if payload.spec.expanded_compare is not None:
                    from marivo.analysis.compiler.attribution import (
                        prepare_expanded_attribute,
                    )

                    current = self._visit(root.inputs[1].root)
                    baseline = self._visit(root.inputs[2].root)
                    comparison = payload.spec.expanded_compare
                    input_table, extra_checks = prepare_expanded_attribute(
                        previous.expression, current.expression, baseline.expression, payload.spec
                    )
                    parts = comparison_private_parts(
                        input_table,
                        current.parts,
                        baseline.parts,
                        comparison,
                    )
                private_parts = dict(parts)
                if payload.spec.method == "distribution_shapley@v1":
                    from marivo.analysis.compiler.distribution_attribution import (
                        lower_distribution_attribute,
                    )

                    table, validations = lower_distribution_attribute(
                        input_table,
                        payload.spec,
                        current=private_parts["delta_distribution.current"],
                        baseline=private_parts["delta_distribution.baseline"],
                        original=previous.expression
                        if payload.spec.expanded_compare is not None
                        else None,
                    )
                else:
                    table, validations = lower_distinct_attribute(
                        input_table,
                        payload.spec,
                        current_membership=private_parts["delta_membership.current"],
                        baseline_membership=private_parts["delta_membership.baseline"],
                        original=previous.expression
                        if payload.spec.expanded_compare is not None
                        else None,
                    )
                validations = (*extra_checks, *validations)
            elif payload.spec.expanded_compare is None:
                table, validations = lower_attribute(previous.expression, payload.spec)
            else:
                current = self._visit(root.inputs[1].root)
                baseline = self._visit(root.inputs[2].root)
                table, validations = lower_expanded_attribute(
                    previous.expression, current.expression, baseline.expression, payload.spec
                )
            self.validations.extend(validations)
            result = _Rows(table, previous.membership, previous.entity)
            self.attribution_proof = table
        elif isinstance(payload, RetainedFoldPayload):
            previous = self._visit(root.inputs[0].root)
            table, validations = lower_fold(previous.expression, payload.spec)
            self.validations.extend(validations)
            result = _Rows(
                table,
                previous.membership,
                previous.entity,
                selections=previous.selections,
                parts=fold_memberships(previous.parts, previous.expression, table, payload.spec),
            )
        elif isinstance(payload, RetainedRowsPayload):
            previous = self._visit(root.inputs[0].root)
            value = self.datasets[id(root)]
            table = previous.expression
            keys = tuple(
                field.name
                for field in value.schema.columns
                if field.field_id in value.row_contract.key_field_ids
            )
            if payload.predicate is not None:
                table = table.filter(lower_bound_predicate(table, payload.predicate))
            if payload.rank is not None:
                table, _ = self._rank(table, payload.rank, keys)
            retained_ordering = value.row_set_contract.ordering
            if isinstance(retained_ordering, _OrderedOrdering):
                names = {field.field_id: field.name for field in value.schema.columns}
                table = self._order(
                    table,
                    tuple(
                        (names[term.field_id], term.direction, term.nulls)
                        for term in retained_ordering.terms
                    ),
                    _authored_orders(value.row_contract, value.row_set_contract),
                )
            if payload.limit_count is not None:
                table = table.limit(payload.limit_count)
            result = _Rows(
                table.select(
                    *[field.name for field in value.schema.columns],
                    *[
                        name
                        for name in _state_projection(value.row_contract)
                        if name in table.columns
                    ],
                ),
                previous.membership,
                previous.entity,
                previous.definition,
                previous.selections,
                parts=selected_private_parts(previous.parts, value.row_contract, table),
            )
        elif isinstance(payload, MetricPayload):
            previous = self._visit(root.inputs[0].root)
            definition = payload.definition
            if root.operator_id == "session.observe":
                result = self._observe(payload, previous)
            elif root.operator_id in (
                "metric.with_dimensions",
                "metric.with_time_axis",
                "metric.aggregate",
            ):
                result = self._evaluate(definition, previous.membership, previous.selections)
            elif root.operator_id == "metric.expand_axes":
                original = self.datasets[id(root.inputs[0].root)]
                from marivo.analysis.operators.attribute_expansion import _definition

                previous_definition = _definition(original)
                keys = tuple(
                    field.name
                    for field in original.schema.columns
                    if field.field_id in original.row_contract.key_field_ids
                )
                selections = previous.selections
                if keys:
                    selections = (
                        *selections,
                        _Selection(
                            previous.expression.select(*keys),
                            previous_definition,
                            axis_expansion=bool(definition.distinct_memberships),
                        ),
                    )
                result = self._evaluate(definition, previous.membership, selections)
            else:
                table = previous.expression
                ordering = previous.ordering
                selections = previous.selections
                row, _ = metric_contracts(
                    definition, self.dataset._registry.get("metric").ids, self.registry
                )
                keys = tuple(
                    field.name
                    for field in row.schema.columns
                    if field.field_id in row.key_field_ids
                )
                if payload.predicate is not None:
                    table = table.filter(lower_bound_predicate(table, payload.predicate))
                if payload.rank is not None:
                    table, ordering = self._rank(table, payload.rank, keys)
                if payload.limit_count is not None:
                    if not ordering:
                        raise compilation_error(
                            "complete logical ordering before limit", "unordered input"
                        )
                    table = self._order(table, ordering).limit(payload.limit_count)
                if (
                    payload.predicate is not None or payload.limit_count is not None
                ) and definition.entity_present:
                    selections = (*selections, _Selection(table.select(*keys), definition))
                visible = tuple(field.name for field in row.schema.columns)
                generated = ("rank",) if "rank" in table.columns and "rank" not in visible else ()
                state_names = tuple(
                    name for metric in definition.metrics for name in _state_names(metric)
                )
                state_names = tuple(dict.fromkeys((*state_names, *_state_projection(row))))
                table = table.select(*visible, *generated, *state_names)
                result = _Rows(
                    table,
                    previous.membership,
                    previous.entity,
                    definition,
                    selections,
                    ordering,
                    selected_private_parts(previous.parts, row, table),
                )
        else:
            raise compilation_error("registered Observation source/operator", "unsupported payload")
        self.cache[id(root)] = result
        return result

    @staticmethod
    def _order(
        table: ir.Table,
        ordering: tuple[tuple[str, str, str], ...],
        authored: Mapping[str, tuple[str | int, ...]] | None = None,
    ) -> ir.Table:
        expressions = {
            name: ibis.cases(
                *tuple(
                    (_boolean(table[name] == value), index) for index, value in enumerate(values)
                ),
                else_=-1,
            )
            for name, values in (authored or {}).items()
        }
        return table.order_by(
            [
                expressions.get(name, table[name]).asc(nulls_first=nulls == "first")
                if direction == "ascending"
                else expressions.get(name, table[name]).desc(nulls_first=nulls == "first")
                for name, direction, nulls in ordering
            ]
        )

    @staticmethod
    def _rank(
        table: ir.Table, spec: RankSpec, keys: tuple[str, ...]
    ) -> tuple[ir.Table, tuple[tuple[str, str, str], ...]]:
        column = _numeric(table[spec.by.name])
        valid = column.notnull()
        if isinstance(column, ir.FloatingValue):
            valid = valid & ~column.isnan() & ~column.isinf()
        ranked = valid.ifelse(column, ibis.null().cast(column.type()))
        partitions = [table[field.name] for field in spec.partition_fields]
        term = (
            ranked.asc(nulls_first=False)
            if spec.order == "ascending"
            else ranked.desc(nulls_first=False)
        )
        if spec.ties == "ordinal":
            order = [term, *(table[name].asc(nulls_first=False) for name in keys)]
            rank = ibis.row_number().over(ibis.window(group_by=partitions, order_by=order)) + 1
        elif spec.ties == "dense":
            rank = ibis.dense_rank().over(ibis.window(group_by=partitions, order_by=term)) + 1
        else:
            rank = ibis.rank().over(ibis.window(group_by=partitions, order_by=term)) + 1
            if spec.ties == "max":
                rank = rank + column.count().over(ibis.window(group_by=[*partitions, ranked])) - 1
        table = table.mutate(rank=valid.ifelse(rank, ibis.null().cast("int64")).cast("int64"))
        names = tuple(
            dict.fromkeys((*(field.name for field in spec.partition_fields), "rank", *keys))
        )
        return table, tuple((name, "ascending", "last") for name in names)

    def compile(self) -> CompiledDataset:
        rows = self._visit(self.dataset._root)
        root = self.dataset._root
        if (
            isinstance(root, LogicalRootHandle)
            and isinstance(root.payload, AttributePayload)
            and root.payload.spec.method == "distribution_shapley@v1"
        ):
            if self.preparations:
                self._flush_validations()
            checks, preparations = _named_validations(
                tuple(self.validations), tuple(self.preparations)
            )
            return CompiledDataset(
                rows.expression,
                checks,
                tuple(rows.expression.columns),
                (),
                preparations,
                numerical_input="distribution_coalitions",
            )
        primary = tuple(field.name for field in self.dataset.schema.columns)
        parts = retained_part_specs(self.dataset.row_contract)
        hidden = _state_projection(self.dataset.row_contract)
        expression = rows.expression.select(*primary, *hidden)
        expression = _physical_casts(expression)
        self.validations.extend(
            private_part_validations(self.dataset.row_contract, expression, dict(rows.parts))
        )
        fields = {field.field_id: field.name for field in self.dataset.schema.columns}
        key_names = tuple(fields[key] for key in self.dataset.row_contract.key_field_ids)
        if key_names:
            self._unique("dataset.final_row_key_unique", expression, key_names)
        ordering = self.dataset.row_set_contract.ordering
        if self.dataset.row_contract.shape_id.family_id == "event":
            expression = canonical_event_rows(expression, self.dataset.row_contract)
        elif self.dataset.kind == "lifecycle":
            from marivo.analysis.compiler.lifecycle_reducers import canonical_rows

            expression = canonical_rows(expression, self.dataset.row_contract)
        elif isinstance(ordering, _OrderedOrdering):
            field_names = {field.field_id: field.name for field in self.dataset.schema.columns}
            terms = tuple(
                (field_names[term.field_id], term.direction, term.nulls) for term in ordering.terms
            )
            expression = self._order(
                expression,
                terms,
                _authored_orders(self.dataset.row_contract, self.dataset.row_set_contract),
            )
        elif key_names:
            expression = self._order(
                expression, tuple((name, "ascending", "last") for name in key_names)
            )
        if self.preparations:
            self._flush_validations()
        validations, preparations = _named_validations(
            tuple(self.validations), tuple(self.preparations)
        )
        return CompiledDataset(
            expression,
            validations,
            primary,
            (
                *parts,
                *private_part_specs(
                    self.dataset.row_contract,
                    tuple((role, _physical_casts(table)) for role, table in rows.parts),
                ),
            ),
            preparations,
            self.attribution_proof,
            version_selections=tuple(self.version_selections.items()),
            temporal_execution=TemporalExecution(
                report=self.owner.report_time,
                axes=tuple(self.time_authorities[key] for key in sorted(self.time_authorities)),
            )
            if self.time_authorities
            else None,
            lifecycle_coverage=self.lifecycle_coverage
            if str(self.dataset.row_contract.shape_id) == "lifecycle/history@v1"
            else None,
            lifecycle_reducer_coverage=self.lifecycle_reducer_coverage,
            lifecycle_selection_payload=self.lifecycle_selection_payload
            if self.dataset.kind == "population"
            else None,
            lifecycle_selection_proof=self.lifecycle_selection_proof
            if self.dataset.kind == "population"
            else None,
            association_proof=self.association_proof,
            candidate_proof=self.candidate_proof if self.dataset.kind == "candidate" else None,
            candidate_definition=self.candidate_definition
            if self.dataset.kind == "candidate"
            else None,
            event_proof=self.event_proofs.get(id(root)),
            event_coverage=self.event_coverages.get(root.definition_fingerprint)
            if isinstance(root, LogicalRootHandle)
            else None,
            event_reducer_proof=(
                event_result_proof(
                    expression,
                    self.dataset.row_contract,
                    filtered=isinstance(root, LogicalRootHandle)
                    and root.operator_id == "event.where",
                )
                if isinstance(
                    self.dataset.row_contract.family_semantics,
                    (EventFunnelSemantics, EventTimeToEventSemantics),
                )
                else None
            ),
            event_reducer_coverage=self.event_reducer_coverage,
            selection_proof=self.selection_proof if self.dataset.kind == "population" else None,
            selection_coverage=self.selection_coverage,
            selection_payload=self.selection_payload,
            selection_input_definition=self.selection_input_definition,
        )


def compile_dataset(
    dataset: LogicalDataset,
    tables: Mapping[str, ir.Table],
    *,
    dependencies: SourceDependencies | None = None,
    scans: Mapping[str, CompiledArtifactScan] | None = None,
    source_owner: ObservationOwner | None = None,
    event_coverages: Mapping[str, EventCoverageResolution] | None = None,
    read_timezone: str | None = None,
    read_timezone_source: Literal["engine", "system_fallback"] = "engine",
    replay_exact_quantile: bool = False,
    scalar_identity_distinct: bool = False,
) -> CompiledDataset:
    """Lower a logical Dataset using exact source tables without executing or reading rows."""
    return _Compiler(
        dataset,
        tables,
        {} if scans is None else scans,
        source_owner,
        event_coverages,
        read_timezone,
        read_timezone_source,
        dependencies,
        replay_exact_quantile,
        scalar_identity_distinct,
    ).compile()


def _lower_retained_scan(
    row: DatasetRowContract,
    selected_table: ir.Table,
    selected_parts: Mapping[str, ir.Table] | None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Attach and reconcile only the consumer-selected immutable component roles."""
    if isinstance(row.family_semantics, LifecycleSemantics):
        return selected_table, ()
    validations: list[CompiledValidation] = []

    def assertion(name: str, invalid: ir.Table) -> None:
        validations.append(CompiledValidation(name, invalid.aggregate(violations=invalid.count())))

    expected = {part.role: part for part in retained_part_specs(row)}
    membership_roles = {role for role, _ in source_private_part_authorities(row)}
    validations.extend(
        private_part_validations(
            row, selected_table, {} if selected_parts is None else selected_parts, required=False
        )
    )
    result = selected_table
    keys = tuple(field.name for field in row.schema.columns if field.field_id in row.key_field_ids)
    for role, incoming in () if selected_parts is None else selected_parts.items():
        if role in membership_roles:
            continue
        spec = expected.get(role)
        if spec is None or tuple(incoming.columns) != spec.column_names:
            raise compilation_error(
                "the exact registered Metric part columns",
                "unknown role or invalid part schema",
            )
        if keys:
            counts = incoming.group_by(keys).aggregate(__mv_count=incoming.count())
            assertion(f"{role}.keys_unique", counts.filter(counts.__mv_count > 1))
            right = incoming.view()
            conditions = [selected_table[key].identical_to(right[key]) for key in keys]
            assertion(
                f"{role}.primary_keys_complete",
                selected_table.join(right, conditions, how="anti"),
            )
            assertion(
                f"{role}.part_keys_complete", right.join(selected_table, conditions, how="anti")
            )
            joined = result.join(
                right, [result[key].identical_to(right[key]) for key in keys], how="left"
            )
            result = joined.select(
                *result.columns,
                *[right[name] for name in spec.column_names if name not in keys],
            )
        else:
            count = incoming.aggregate(__mv_count=incoming.count())
            assertion(f"{role}.singleton", count.filter(count.__mv_count != 1))
            result = result.cross_join(incoming)
    semantics = row.family_semantics
    if isinstance(semantics, (EntityPresentMetricSemantics, EntityReducedMetricSemantics)):
        fields = {field.field_id.value: field for field in row.schema.columns}
        selected = {} if selected_parts is None else selected_parts
        for authority in semantics.metric_folds:
            role = fold_part_role(authority)
            if role not in selected:
                continue
            field = fields[authority.field_id]
            finalized = _declared_cast(_fold_value(result, authority), field.logical_type_id)
            assertion(
                f"{role}.primary_value_reconciliation",
                result.filter(~result[field.name].identical_to(finalized)),
            )
            for component in authority.components:
                names = dict(component.state_columns)
                counters = tuple(
                    column for state, column in component.state_columns if state.endswith("count")
                )
                for column in counters:
                    bad = result[column].isnull() | (result[column] < 0)
                    if "row_count" in names and column != names["row_count"]:
                        bad = bad | (result[column] > result[names["row_count"]])
                    assertion(f"{role}.{column}.support_valid", result.filter(bad))
            if authority.cumulative:
                endpoint, start, end, seconds, complete = coverage_columns(authority)
                duration = _duration_seconds(result[start], result[end])
                empty = (
                    _and([result[name].isnull() for name in (endpoint, start, end)])
                    & result[seconds].identical_to(0)
                    & result[complete].identical_to(False)
                )
                for component in authority.components:
                    empty = empty & _and(
                        [
                            result[column].identical_to(0)
                            for state, column in component.state_columns
                            if state.endswith("count")
                        ]
                    )
                populated = (
                    _and(
                        [
                            result[name].notnull()
                            for name in (endpoint, start, end, seconds, complete)
                        ]
                    )
                    & (result[start] <= result[end])
                    & (result[endpoint] == result[end])
                    & (result[seconds] >= 0)
                    & (result[seconds] <= duration)
                    & (~_boolean(result[complete]) | (result[seconds] == duration))
                )
                assertion(
                    f"{role}.coverage_valid",
                    result.filter(~(empty | populated).fill_null(False)),
                )
    return result, tuple(validations)


def compile_retained_rows(
    dataset: Dataset,
    table: ir.Table | Mapping[str, ir.Table],
    *,
    parts: Mapping[str, ir.Table] | None = None,
    input_parts: Mapping[str, Mapping[str, ir.Table]] | None = None,
    event_coverages: Mapping[str, EventCoverageResolution] | None = None,
) -> CompiledDataset:
    """Compose exact row/state operations over one immutable engine Artifact."""
    from marivo.analysis.operators.registry import admit_retained_rows

    validations: list[CompiledValidation] = []
    attribution_proof: ir.Table | None = None
    association_proof: ir.Table | None = None
    candidate_proof: ir.Table | None = None
    candidate_definition: CandidateDefinition | DriverCandidateDefinition | None = None
    preparations: list[CompiledValidation | CompiledSampleFence | CompiledRelationFence] = []
    prepared_count = 0
    event_reducer_coverage: EventCoverageResolution | None = None
    lifecycle_reducer_coverage: EventCoverageResolution | None = None
    lifecycle_selection_payload: LifecycleSelectionPayload | None = None
    lifecycle_selection_proof: ir.Table | None = None
    selection_proof: ir.Table | None = None
    selection_coverage: EventCoverageResolution | None = None
    selection_payload: EventSelectionPayload | None = None
    selection_input_definition: str | None = None
    coverages = {} if event_coverages is None else event_coverages
    cache: dict[int, ir.Table] = {}
    private_parts: dict[int, PrivateRelations] = {}

    def read(value: MaterializedDataset) -> ir.Table:
        nonlocal event_reducer_coverage, lifecycle_reducer_coverage
        if value.kind == "lifecycle":
            lifecycle_reducer_coverage = coverages.get(value.state.artifact_ref.ref)
        if value.kind == "event":
            event_reducer_coverage = coverages.get(value.state.artifact_ref.ref)
        selected_table = (
            table if isinstance(table, ir.Table) else table[value.state.artifact_ref.ref]
        )
        selected_parts = (
            parts if input_parts is None else input_parts.get(value.state.artifact_ref.ref)
        )
        result, checks = _lower_retained_scan(value.row_contract, selected_table, selected_parts)
        validations.extend(checks)
        private_parts[id(value)] = (
            tuple(({} if selected_parts is None else selected_parts).items())
            if value.kind == "lifecycle"
            else selected_private_parts(
                tuple(({} if selected_parts is None else selected_parts).items()),
                value.row_contract,
                result,
                required=False,
            )
        )
        return result

    def visit(value: Dataset) -> ir.Table:
        if id(value) not in cache:
            cache[id(value)] = visit_node(value)
        return cache[id(value)]

    def freeze(value: ir.Table, root: LogicalRootHandle) -> ir.Table:
        nonlocal prepared_count
        preparations.extend(validations[prepared_count:])
        prepared_count = len(validations)
        name = f"__mv_event_reducer_{len(preparations)}"
        preparations.append(CompiledRelationFence(name, value, id(root)))
        return ibis.table(value.schema(), name=name)

    def visit_node(value: Dataset) -> ir.Table:
        nonlocal lifecycle_reducer_coverage, lifecycle_selection_payload, lifecycle_selection_proof
        nonlocal \
            event_reducer_coverage, \
            selection_proof, \
            selection_coverage, \
            selection_payload, \
            selection_input_definition
        nonlocal \
            attribution_proof, \
            association_proof, \
            candidate_proof, \
            candidate_definition, \
            prepared_count
        admit_retained_rows(value)
        if isinstance(value, MaterializedDataset):
            return read(value)
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            raise compilation_error("an exact retained row graph", "invalid retained row node")
        payload = value._root.payload
        if isinstance(payload, PopulationSamplePayload):
            previous = visit(value._inputs[0])
            field_identity = value.schema.columns[0].identity
            if not isinstance(field_identity, _EntityFieldIdentity):
                raise compilation_error("complete Population identity", "invalid sample shape")
            keys = tuple(key for key, _ in field_identity.identity_signature)
            identity = previous.entity_identity
            if not isinstance(identity, ir.StructValue):
                raise compilation_error("complete selected identity tuple", "invalid sample input")
            table_input = previous.select(**{key: identity[key] for key in keys})
            preparations.extend(validations[prepared_count:])
            prepared_count = len(validations)
            name = f"__mv_sample_{len(preparations)}"
            preparations.append(
                CompiledSampleFence(
                    name,
                    table_input,
                    payload.policy,
                    value.definition_fingerprint,
                    payload.target_population_definition_fingerprint,
                    keys,
                    id(value._root),
                )
            )
            sampled = ibis.table(table_input.schema(), name=name)
            private_parts[id(value)] = ()
            return sampled.select(entity_identity=ibis.struct({key: sampled[key] for key in keys}))
        if isinstance(payload, (LifecycleReducerPayload, LifecycleSelectionPayload)):
            from marivo.analysis.compiler.lifecycle_reducers import reduce_lifecycle

            incoming = value._inputs[0]
            previous = visit(incoming)
            key = (
                incoming.state.artifact_ref.ref
                if isinstance(incoming, MaterializedDataset)
                else incoming.definition_fingerprint
            )
            coverage = coverages.get(key)
            if coverage is None:
                raise compilation_error(
                    "exact retained Lifecycle coverage", "missing input authority"
                )
            if isinstance(payload, LifecycleReducerPayload) and payload.axes:
                raise compilation_error("current semantic axis binding", "retained-only enrichment")
            root_handle = value._root
            result, checks, proof = reduce_lifecycle(
                previous,
                dict(private_parts[id(incoming)]),
                payload,
                freeze=lambda t: freeze(t, root_handle),
            )
            validations.extend(checks)
            lifecycle_reducer_coverage = coverage
            private_parts[id(value)] = ()
            if isinstance(payload, LifecycleSelectionPayload):
                lifecycle_selection_payload, lifecycle_selection_proof = payload, proof
                selection_input_definition = incoming.definition_fingerprint
                return freeze(result, value._root)
            return result
        if isinstance(
            payload, (EventFunnelPayload, EventTimeToEventPayload, EventSelectionPayload)
        ):
            from marivo.analysis.compiler.event_continuation import reduce_event

            incoming = value._inputs[0]
            previous = visit(incoming)
            key = (
                incoming.state.artifact_ref.ref
                if isinstance(incoming, MaterializedDataset)
                else incoming.definition_fingerprint
            )
            coverage = coverages.get(key)
            if coverage is None:
                raise compilation_error(
                    "exact retained journey coverage", "missing Event authority"
                )
            if isinstance(payload, EventFunnelPayload) and payload.axes:
                raise compilation_error(
                    "explicit current semantic source binding", "retained-only axis enrichment"
                )
            result, checks, proof = reduce_event(freeze(previous, value._root), payload, coverage)
            validations.extend(checks)
            validations.append(
                CompiledValidation("event.reducer.output", proof.select("violations"))
            )
            private_parts[id(value)] = ()
            if isinstance(payload, EventSelectionPayload):
                selection_proof, selection_coverage = proof, coverage
                selection_payload, selection_input_definition = (
                    payload,
                    incoming.definition_fingerprint,
                )
                return freeze(result, value._root)
            event_reducer_coverage = coverage
            return result
        if isinstance(payload, DriverCandidatePayload):
            from marivo.analysis.compiler.driver_candidate import lower_driver_candidate

            if payload.spec.expanded_compare is not None:
                raise compilation_error(
                    "logical source axis expansion", "retained expansion boundary"
                )
            previous = visit(value._inputs[0])
            preparations.extend(validations[prepared_count:])
            prepared_count = len(validations)
            name = f"__mv_driver_input_{len(preparations)}"
            preparations.append(CompiledRelationFence(name, previous, id(value._root)))
            frozen = ibis.table(previous.schema(), name=name)
            result, checks, candidate_proof = lower_driver_candidate(frozen, payload.spec)
            candidate_definition = payload.spec.definition
            validations.extend(checks)
            private_parts[id(value)] = ()
            return result
        if isinstance(payload, CandidatePayload):
            from marivo.analysis.compiler.entity_candidate import lower_entity_candidate

            previous = visit(value._inputs[0])
            preparations.extend(validations[prepared_count:])
            prepared_count = len(validations)
            name = f"__mv_entity_candidate_{len(preparations)}"
            preparations.append(CompiledRelationFence(name, previous, id(value._root)))
            frozen = ibis.table(previous.schema(), name=name)
            result, checks, candidate_proof = lower_entity_candidate(frozen, payload.spec)
            candidate_definition = payload.spec.definition
            validations.extend(checks)
            private_parts[id(value)] = ()
            return result
        if isinstance(payload, CorrelatePayload):
            from marivo.analysis.compiler.correlation import lower_correlate

            result, checks = lower_correlate(visit(value._inputs[0]), payload.spec)
            association_proof = result
            validations.extend(checks)
            private_parts[id(value)] = ()
            return result
        if isinstance(payload, (FunnelComparePayload, FunnelAttributePayload)):
            from marivo.analysis.compiler.event_attribution import (
                lower_attribute as lower_funnel_attribute,
            )
            from marivo.analysis.compiler.event_comparison import (
                lower_compare as lower_funnel_compare,
            )

            inputs = tuple(visit(item) for item in value._inputs)
            if isinstance(payload, FunnelComparePayload):
                result, checks = lower_funnel_compare(inputs[0], inputs[1], payload.spec)
            else:
                result, checks = lower_funnel_attribute(
                    inputs[0], inputs[1], inputs[2], payload.spec
                )
            validations.extend(checks)
            private_parts[id(value)] = ()
            return result
        if isinstance(payload, ComparePayload):
            current = visit(value._inputs[0])
            baseline = visit(value._inputs[1])
            result, checks = lower_compare(current, baseline, payload.spec)
            private_parts[id(value)] = comparison_private_parts(
                result,
                private_parts[id(value._inputs[0])],
                private_parts[id(value._inputs[1])],
                payload.spec,
            )
            validations.extend(
                private_part_validations(value.row_contract, result, dict(private_parts[id(value)]))
            )
            validations.extend(checks)
            return result
        if isinstance(payload, AttributePayload):
            from marivo.analysis.compiler.attribution import lower_attribute

            if payload.spec.expanded_compare is not None:
                raise compilation_error(
                    "logical source axis expansion", "retained expansion boundary"
                )
            previous = visit(value._inputs[0])
            if payload.spec.method == "distribution_shapley@v1":
                from marivo.analysis.compiler.distribution_attribution import (
                    lower_distribution_attribute,
                )

                basis = dict(private_parts[id(value._inputs[0])])
                result, checks = lower_distribution_attribute(
                    previous,
                    payload.spec,
                    current=basis["delta_distribution.current"],
                    baseline=basis["delta_distribution.baseline"],
                )
            elif payload.spec.method == "distinct_membership@v1":
                from marivo.analysis.compiler.distinct_attribution import lower_distinct_attribute

                basis = dict(private_parts[id(value._inputs[0])])
                result, checks = lower_distinct_attribute(
                    previous,
                    payload.spec,
                    current_membership=basis["delta_membership.current"],
                    baseline_membership=basis["delta_membership.baseline"],
                )
            else:
                result, checks = lower_attribute(previous, payload.spec)
            private_parts[id(value)] = ()
            validations.extend(checks)
            attribution_proof = result
            return result
        if (
            not isinstance(payload, (RetainedRowsPayload, RetainedFoldPayload))
            or len(value._inputs) != 1
        ):
            raise compilation_error(
                "a registered retained row or fold operation", "unsupported retained source method"
            )
        result = visit(value._inputs[0])
        if isinstance(payload, RetainedFoldPayload):
            missing = set(_state_projection(payload.spec.input_row)) - set(result.columns)
            if missing:
                raise compilation_error(
                    "complete named sufficient state", "missing required fold part"
                )
            original = result
            result, checks = lower_fold(result, payload.spec)
            validations.extend(checks)
            private_parts[id(value)] = fold_memberships(
                private_parts[id(value._inputs[0])],
                original,
                result,
                payload.spec,
            )
            return result
        keys = tuple(
            field.name
            for field in value.schema.columns
            if field.field_id in value.row_contract.key_field_ids
        )
        if payload.predicate is not None:
            result = result.filter(lower_bound_predicate(result, payload.predicate))
        if payload.rank is not None:
            result, _ = _Compiler._rank(result, payload.rank, keys)
        ordering = value.row_set_contract.ordering
        if isinstance(ordering, _OrderedOrdering):
            names = {field.field_id: field.name for field in value.schema.columns}
            result = _Compiler._order(
                result,
                tuple(
                    (names[term.field_id], term.direction, term.nulls) for term in ordering.terms
                ),
                _authored_orders(value.row_contract, value.row_set_contract),
            )
        if payload.limit_count is not None:
            result = result.limit(payload.limit_count)
        private_parts[id(value)] = selected_private_parts(
            private_parts[id(value._inputs[0])],
            value.row_contract,
            result,
        )
        return result.select(
            *[field.name for field in value.schema.columns],
            *[name for name in _state_projection(value.row_contract) if name in result.columns],
        )

    expression = _physical_casts(visit(dataset))
    root = dataset._root
    if (
        isinstance(root, LogicalRootHandle)
        and isinstance(root.payload, AttributePayload)
        and root.payload.spec.method == "distribution_shapley@v1"
    ):
        checks, _ = _named_validations(tuple(validations))
        return CompiledDataset(
            expression,
            checks,
            tuple(expression.columns),
            (),
            numerical_input="distribution_coalitions",
        )
    validations.extend(
        private_part_validations(dataset.row_contract, expression, dict(private_parts[id(dataset)]))
    )
    fields = {field.field_id: field.name for field in dataset.schema.columns}
    keys = tuple(fields[key] for key in dataset.row_contract.key_field_ids)
    missing = set(_state_projection(dataset.row_contract)) - set(expression.columns)
    if missing:
        raise compilation_error("complete output Metric state", "missing required output part")
    if keys:
        counts = expression.group_by(keys).aggregate(__mv_count=expression.count())
        validations.append(
            CompiledValidation(
                "dataset.final_row_key_unique",
                counts.filter(counts.__mv_count > 1).aggregate(
                    violations=counts.filter(counts.__mv_count > 1).count()
                ),
            )
        )
    ordering = dataset.row_set_contract.ordering
    if dataset.row_contract.shape_id.family_id == "event":
        expression = canonical_event_rows(expression, dataset.row_contract)
    elif dataset.kind == "lifecycle":
        from marivo.analysis.compiler.lifecycle_reducers import canonical_rows

        expression = canonical_rows(expression, dataset.row_contract)
    elif isinstance(ordering, _OrderedOrdering):
        names = {field.field_id: field.name for field in dataset.schema.columns}
        expression = _Compiler._order(
            expression,
            tuple((names[term.field_id], term.direction, term.nulls) for term in ordering.terms),
            _authored_orders(dataset.row_contract, dataset.row_set_contract),
        )
    elif keys:
        expression = _Compiler._order(expression, tuple((key, "ascending", "last") for key in keys))
    if preparations:
        preparations.extend(validations[prepared_count:])
    checks, named_preparations = _named_validations(tuple(validations), tuple(preparations))
    return CompiledDataset(
        expression,
        checks,
        tuple(field.name for field in dataset.schema.columns),
        (
            *retained_part_specs(dataset.row_contract),
            *private_part_specs(
                dataset.row_contract,
                tuple((role, _physical_casts(table)) for role, table in private_parts[id(dataset)]),
            ),
        ),
        preparations=named_preparations,
        attribution_proof=attribution_proof,
        association_proof=association_proof,
        candidate_proof=candidate_proof,
        candidate_definition=candidate_definition,
        event_reducer_proof=(
            event_result_proof(
                expression,
                dataset.row_contract,
                filtered=isinstance(root, LogicalRootHandle) and root.operator_id == "event.where",
            )
            if isinstance(
                dataset.row_contract.family_semantics,
                (EventFunnelSemantics, EventTimeToEventSemantics),
            )
            else None
        ),
        lifecycle_reducer_coverage=lifecycle_reducer_coverage,
        lifecycle_selection_payload=lifecycle_selection_payload
        if dataset.kind == "population"
        else None,
        lifecycle_selection_proof=lifecycle_selection_proof
        if dataset.kind == "population"
        else None,
        event_reducer_coverage=event_reducer_coverage,
        selection_proof=selection_proof if dataset.kind == "population" else None,
        selection_coverage=selection_coverage,
        selection_payload=selection_payload,
        selection_input_definition=selection_input_definition,
    )
