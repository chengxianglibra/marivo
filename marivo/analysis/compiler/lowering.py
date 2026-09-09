"""One-source Ibis lowering of the authored private Observation graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.comparison import lower_compare
from marivo.analysis.compiler.distinct_fold import fold_memberships
from marivo.analysis.compiler.distribution import source_quantile
from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import (
    CompiledArtifactScan,
    CompiledDataset,
    CompiledSampleFence,
    CompiledValidation,
    RetainedPartSpec,
)
from marivo.analysis.compiler.normalize import logical_roots, required_entities
from marivo.analysis.compiler.predicates import lower_bound_predicate, predicate_leaves
from marivo.analysis.compiler.private_parts import (
    PrivateRelations,
    comparison_private_parts,
    private_part_specs,
    private_part_validations,
    selected_private_parts,
)
from marivo.analysis.compiler.temporal import bucket, bucket_end, cumulative_start
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    DatasetRowContract,
    _canonical_digest,
    _CatalogFieldIdentity,
    _EntityFieldIdentity,
    _OrderedOrdering,
)
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.observation.contracts import (
    EntityPresentMetricSemantics,
    EntityReducedMetricSemantics,
    MetricDefinition,
    MetricPayload,
    ObservationOwner,
    PopulationPayload,
    RankSpec,
    RetainedRowsPayload,
    metric_contracts,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import functional_path, governed_path
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
    fold_part_role,
    fold_state_names,
)
from marivo.analysis.observation.private_parts import source_private_part_authorities
from marivo.analysis.operators.attribution_contracts import (
    AttributePayload,
    delta_part_authorities,
    delta_presence_name,
    delta_state_name,
)
from marivo.analysis.operators.contracts import ComparePayload
from marivo.refs import SemanticKind
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TargetSnapshotSelection,
    TargetSnapshotVersion,
    TargetValiditySelection,
    TargetValidityVersion,
    TimestampParse,
)
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeNodeV1,
    LinearNodeV1,
    MetricGraphNodeV1,
    RatioNodeV1,
    SliceNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.validator import normalize_target_dimension


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


def _named_validations(
    validations: tuple[CompiledValidation, ...],
    preparations: tuple[CompiledValidation | CompiledSampleFence, ...] = (),
) -> tuple[tuple[CompiledValidation, ...], tuple[CompiledValidation | CompiledSampleFence, ...]]:
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


def _hidden(metric: TargetMetricContract, node_id: str, state: str) -> str:
    digest = _canonical_digest((metric.ref.path, node_id))[:20]
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
    """Resolve exact required Metric roles from the frozen current row contract."""
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


def _declared_cast(value: ir.Value, logical_type: str) -> ir.Value:
    target = dt.dtype(logical_type)
    if logical_type == "decimal":
        physical = value.type()
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


def _duration_seconds(start: ir.Value, end: ir.Value) -> ir.NumericValue:
    left, right = start.cast("timestamp"), end.cast("timestamp")
    if not isinstance(left, ir.TimestampValue) or not isinstance(right, ir.TimestampValue):
        raise compilation_error("exact retained interval endpoints", "invalid coverage interval")
    return right.delta(left, unit="microsecond") / 1_000_000


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
        result = _numeric(value(node.children[0])) * node.coefficients[0]
        for child, coefficient in zip(node.children[1:], node.coefficients[1:], strict=True):
            result = result + _numeric(value(child)) * coefficient
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
            elif semantics.fold_time_scope is not None:
                target_start = ibis.literal(semantics.fold_time_scope.start).cast("timestamp")
                target_end = ibis.literal(semantics.fold_time_scope.end).cast("timestamp")
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
    ) -> None:
        self.dataset = dataset
        self.owner = source_owner_of(dataset) if source_owner is None else source_owner
        self.registry = self.owner.semantic_registry
        self.tables = tables
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
        self.validation_occurrences: dict[str, int] = {}
        self.preparations: list[CompiledValidation | CompiledSampleFence] = []
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
            if tuple(table.columns) != tuple(name for name, _ in entity.columns):
                raise compilation_error(
                    "ordered declared semantic source columns", "source schema mismatch"
                )
            if any(
                table[name].type() != dt.dtype(kind)
                and not (kind == "decimal" and isinstance(table[name].type(), dt.Decimal))
                for name, kind in entity.columns
            ):
                raise compilation_error(
                    "exact declared semantic source types", "source type mismatch"
                )
            self._validate_source(entity, table)
            if isinstance(entity.version, TargetSnapshotVersion):
                if entity.version.format is not None or entity.version.timezone is not None:
                    raise compilation_error(
                        "native unformatted snapshot coordinate",
                        "unsupported snapshot format or timezone",
                    )
                self._validate_temporal_axis(
                    normalize_target_dimension(self.registry, entity.version.coordinate_ref.path)
                )
            elif isinstance(entity.version, TargetValidityVersion):
                if entity.version.timezone is not None:
                    raise compilation_error(
                        "native validity coordinate", "unsupported validity timezone"
                    )
                for reference in (entity.version.valid_from_ref, entity.version.valid_to_ref):
                    self._validate_temporal_axis(
                        normalize_target_dimension(self.registry, reference.path)
                    )
        for root in logical_roots(dataset):
            payload = root.payload
            if isinstance(payload, PopulationPayload) and payload.reference_axis is not None:
                self._validate_temporal_axis(payload.reference_axis)
            elif isinstance(payload, MetricPayload):
                for axis in (payload.definition.reference_axis, payload.definition.time_axis):
                    if axis is not None:
                        self._validate_temporal_axis(axis)

    def _validate_temporal_axis(self, axis: TargetDimensionContract) -> None:
        source_type = self.tables[axis.entity_ref.path][axis.source_column].type()
        parse = self.registry.dimensions[axis.ref.path].parse
        if (
            axis.timezone is not None
            or source_type != dt.dtype(axis.logical_type)
            or (
                parse is not None
                and not isinstance(parse, (DateParse, DatetimeParse, TimestampParse))
            )
        ):
            raise compilation_error(
                "native date/timestamp coordinate without parsing or timezone conversion",
                "unsupported temporal source representation",
            )

    def _count(self, name: str, table: ir.Table) -> None:
        occurrence = self.validation_occurrences.get(name, 0)
        self.validation_occurrences[name] = occurrence + 1
        if occurrence:
            name = f"{name}.occurrence.{occurrence}"
        self.validations.append(CompiledValidation(name, table.aggregate(violations=table.count())))

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
            start, end = table[version.valid_from_column], table[version.valid_to_column]
            open_end = self._open_end(end, version.open_end)
            invalid = start.isnull() | (
                ~open_end
                & _boolean(end <= start if version.interval == "closed_open" else end < start)
            )
            self._count(f"{prefix}.validity_well_formed", table.filter(invalid))
            left, right = table, table.view()
            keys = [_boolean(left[name] == right[name]) for name in entity.primary_key]
            earlier = _boolean(left[version.valid_from_column] < right[version.valid_from_column])
            left_end = left[version.valid_to_column]
            overlaps = self._open_end(left_end, version.open_end) | _boolean(
                left_end > right[version.valid_from_column]
                if version.interval == "closed_open"
                else left_end >= right[version.valid_from_column]
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
                    table[mapping[key.from_key if forward else key.to_key]]
                    == right[renamed[key.to_key if forward else key.from_key]]
                )
                for key in relation.keys
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
        if root.inputs:
            previous = self._visit(root.inputs[0].root)
            table = previous.membership
        else:
            table = self.tables[entity.ref.path]
            version, selection = entity.version, payload.version_selection
            if isinstance(version, TargetSnapshotVersion) and isinstance(
                selection, TargetSnapshotSelection
            ):
                table = table.filter(
                    table[version.source_column]
                    == ibis.literal(selection.period).cast(table[version.source_column].type())
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
                start, end = table[version.valid_from_column], table[version.valid_to_column]
                boundary = ibis.literal(selection.boundary).cast(start.type())
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
                column = table["__mv_scope"]
                table = table.filter(
                    column >= payload.time_scope.start, column < payload.time_scope.end
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

    def _measure_column(self, reference_path: str) -> str:
        for reference, body in self.owner.sidecar.bodies.items():
            if (
                reference.kind is SemanticKind.MEASURE
                and reference.path == reference_path
                and body.source_column is not None
            ):
                return body.source_column
        raise compilation_error("normalized direct-column Measure", "missing expression binding")

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
        aliases: dict[str, str] = {}
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
                        table[mapping[key.from_key if forward else key.to_key]]
                        == right[renamed[key.to_key if forward else key.from_key]]
                    )
                    for key in relationship.keys
                ]
                destination_keys = tuple(
                    key.to_key if forward else key.from_key for key in relationship.keys
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
            aliases[alias] = mapping[axis.source_column]
        table = table.select(
            *(name for name in original if name not in aliases),
            **{alias: table[name] for alias, name in aliases.items()},
        )
        if definition.time_axis is not None and filter_axes is None:
            name = definition.time_axis.ref.path.rsplit(".", 1)[-1]
            if spine and definition.time_scope is not None:
                lower: ir.Value = ibis.literal(definition.time_scope.start)
                if any(metric.cumulative for metric in definition.metrics):
                    # Display buckets require calendar coverage; earlier base
                    # contributions retain their independent history authority.
                    lower = bucket(lower, definition.grain, definition.temporal_snapshot)
                table = table.filter(
                    table[name] >= lower,
                    table[name] < definition.time_scope.end,
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
        if definition.entity.version is not None and (
            definition.dimensions or definition.time_axis is not None
        ):
            source_rows = self.tables[definition.entity.ref.path].view()
            source_rows = source_rows.mutate(
                entity_identity=_identity(source_rows, definition.entity)
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
            if definition.time_scope is not None:
                table = table.filter(table[name] < definition.time_scope.end)
                if not any(metric.cumulative for metric in definition.metrics):
                    table = table.filter(
                        table[name]
                        >= bucket(
                            ibis.literal(definition.time_scope.start),
                            definition.grain,
                            definition.temporal_snapshot,
                        )
                    )
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
            end = ibis.least(end, ibis.literal(definition.time_scope.end).cast("timestamp"))
        conditions = [
            table[name].identical_to(endpoints[name])
            for name in endpoints.columns
            if name != time_name
        ]
        timestamp = table[time_name]
        conditions.append(_boolean(timestamp < end))
        lower = cumulative_start(
            cumulative.anchor,
            end,
            definition.temporal_snapshot,
            bucket_start=endpoints[time_name],
        )
        if lower is not None:
            conditions.append(_boolean(timestamp >= lower))
        joined = table.join(endpoints, conditions, how="inner")
        return joined.select(
            **{
                name: endpoints[time_name] if name == time_name else table[name]
                for name in table.columns
            }
        )

    def _component(
        self,
        metric: TargetMetricContract,
        node_id: str,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...] = (),
    ) -> tuple[ir.Table, ir.Table | None]:
        node = next(record.node for record in metric.graph.nodes if record.node_id == node_id)
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
            column = table["__mv_scope"]
            table = table.filter(column < definition.time_scope.end)
            if not metric.cumulative:
                table = table.filter(column >= definition.time_scope.start)
            elif definition.time_axis is None:
                boundary = cumulative_start(
                    metric.cumulative[0].anchor,
                    ibis.literal(definition.time_scope.end),
                    definition.temporal_snapshot,
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
            value = condition.value
            if (
                isinstance(value, tuple)
                and len(value) == 2
                and value[0] == ("op", "in")
                and isinstance(value[1], tuple)
                and len(value[1]) == 2
                and value[1][0] == "value"
            ):
                value = value[1][1]
            if isinstance(value, tuple) and all(
                isinstance(item, (str, int, float, bool)) for item in value
            ):
                table = table.filter(table[alias].isin(value))
            elif value is None or isinstance(value, (str, int, float, bool)):
                table = table.filter(
                    table[alias].isnull() if value is None else table[alias] == value
                )
            else:
                raise compilation_error("scalar component slice", "unsupported slice literal")
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
            self._count(
                f"{metric.ref.path}.status_time_non_null",
                table.filter(table["__mv_status"].isnull()),
            )
            keys.append("__mv_status")
        # A governed coordinate relation denotes a set of contributions. Repeated
        # bridge rows cannot multiply the same source representation in one tuple.
        table = table.distinct()
        private_relation: ir.Table | None = None
        states: dict[str, ir.Value] = {"row_count": table.count()}
        if isinstance(node, AggregateNodeV1):
            value = (
                table[self._measure_column(node.target_ref.path)]
                if node.target_ref.kind is SemanticKind.MEASURE
                else None
            )
            if node.agg == "count":
                states["count"] = value.count() if value is not None else table.count()
            elif node.agg == "count_distinct":
                value = (
                    value
                    if value is not None
                    else _identity(
                        table, next(item for item in self.entities if item.ref.path == root)
                    )
                )
                states["value"] = value.nunique()
                if component.time_fold is None:
                    private_relation = table.select(*keys, **{DISTINCT_KEY: value})
                    private_relation = private_relation.filter(
                        private_relation[DISTINCT_KEY].notnull()
                    ).distinct()
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
                            if item.metric_ref == metric.ref.path
                        ),
                        None,
                    )
                    states["value"] = (
                        numeric.quantile(quantile)
                        if basis is None
                        else source_quantile(numeric, basis.quantile)
                    )
                    if basis is not None:
                        values = table.select(*keys, **{VALUE: value}).filter(value.notnull())
                        private_relation = values.group_by([*keys, VALUE]).aggregate(
                            **{FREQUENCY: values.count()}
                        )
                states["non_null_count"] = value.count()
        else:
            value = _numeric(table[self._measure_column(node.value_ref.path)])
            weight = _numeric(table[self._measure_column(node.weight_ref.path)])
            pairs = value.notnull() & weight.notnull()
            states["weighted_numerator"] = (value * weight).sum(where=pairs)
            states["weight_sum"] = weight.sum(where=pairs)
            states["non_null_pair_count"] = pairs.cast("int64").sum()
        grouped = table.group_by(keys).aggregate(**states) if keys else table.aggregate(**states)
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
            end = ibis.least(end, ibis.literal(definition.time_scope.end).cast("timestamp"))
        conditions = [
            table[name].identical_to(endpoints[f"__mv_endpoint_{name}"])
            for name in endpoint_names
            if name != axis_name
        ]
        source_time = table["__mv_cumulative_time"]
        conditions.append(_boolean(source_time < end))
        lower = cumulative_start(
            metric.cumulative[0].anchor, end, definition.temporal_snapshot, bucket_start=start
        )
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
                            f"{metric.ref.path}.nonzero_denominator", table.filter(denominator == 0)
                        )
                    return numerator / denominator.nullif(0)
                if isinstance(node, LinearNodeV1):
                    terms = [
                        _numeric(value(term.child_id)) * term.coefficient for term in node.terms
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

            output[field.name] = _declared_cast(value(metric.graph.roots[0]), metric.logical_type)
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
                if private_relation is not None and metric.ref.path in required_private_parts:
                    retained_private_parts.append(
                        (required_private_parts[metric.ref.path][0], private_relation)
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
                    ibis.literal(definition.time_scope.start),
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
                    bucket_start = ibis.literal(definition.time_scope.start).cast("timestamp")
                    bucket_finish = ibis.literal(definition.time_scope.end).cast("timestamp")
                else:
                    raise compilation_error(
                        "an exact cumulative evaluation boundary", "missing endpoint"
                    )
                start, end = bucket_start, bucket_finish
                if definition.time_scope is not None:
                    start = ibis.greatest(
                        start, ibis.literal(definition.time_scope.start).cast("timestamp")
                    )
                    end = ibis.least(end, ibis.literal(definition.time_scope.end).cast("timestamp"))
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

    def _visit(self, root: LogicalRootHandle | MaterializedScanLeafHandle) -> _Rows:
        if isinstance(root, MaterializedScanLeafHandle):
            scan = self.scans.get(root.artifact_ref.ref)
            dataset = self.datasets[id(root)]
            semantics = dataset.row_contract.family_semantics
            if (
                scan is None
                or not isinstance(dataset, MaterializedDataset)
                or (
                    root.shape_id.family_id != "population"
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
            if root.shape_id.family_id == "metric":
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
                    parts=selected_private_parts(
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
            self._unique("population.retained_identity_unique", membership, entity.primary_key)
            return _Rows(
                table,
                membership,
                entity,
                parts=selected_private_parts(
                    scan.parts, dataset.row_contract, table, required=False
                ),
            )
        if id(root) in self.cache:
            return self.cache[id(root)]
        payload = root.payload
        if isinstance(payload, PopulationPayload):
            result = self._population(root, payload)
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
    def _order(table: ir.Table, ordering: tuple[tuple[str, str, str], ...]) -> ir.Table:
        return table.order_by(
            [
                table[name].asc(nulls_first=nulls == "first")
                if direction == "ascending"
                else table[name].desc(nulls_first=nulls == "first")
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
        if isinstance(ordering, _OrderedOrdering):
            field_names = {field.field_id: field.name for field in self.dataset.schema.columns}
            terms = tuple(
                (field_names[term.field_id], term.direction, term.nulls) for term in ordering.terms
            )
            expression = self._order(expression, terms)
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
            (*parts, *private_part_specs(self.dataset.row_contract, rows.parts)),
            preparations,
            self.attribution_proof,
        )


def compile_dataset(
    dataset: LogicalDataset,
    tables: Mapping[str, ir.Table],
    *,
    scans: Mapping[str, CompiledArtifactScan] | None = None,
    source_owner: ObservationOwner | None = None,
) -> CompiledDataset:
    """Lower a logical Dataset using exact source tables without executing or reading rows."""
    return _Compiler(dataset, tables, {} if scans is None else scans, source_owner).compile()


def _lower_retained_scan(
    row: DatasetRowContract,
    selected_table: ir.Table,
    selected_parts: Mapping[str, ir.Table] | None,
) -> tuple[ir.Table, tuple[CompiledValidation, ...]]:
    """Attach and reconcile only the consumer-selected immutable component roles."""
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
    dataset: LogicalDataset,
    table: ir.Table | Mapping[str, ir.Table],
    *,
    parts: Mapping[str, ir.Table] | None = None,
    input_parts: Mapping[str, Mapping[str, ir.Table]] | None = None,
) -> CompiledDataset:
    """Compose exact row/state operations over one immutable engine Artifact."""
    from marivo.analysis.operators.registry import admit_retained_rows

    validations: list[CompiledValidation] = []
    attribution_proof: ir.Table | None = None
    private_parts: dict[int, PrivateRelations] = {}

    def read(value: MaterializedDataset) -> ir.Table:
        selected_table = (
            table if isinstance(table, ir.Table) else table[value.state.artifact_ref.ref]
        )
        selected_parts = (
            parts if input_parts is None else input_parts.get(value.state.artifact_ref.ref)
        )
        result, checks = _lower_retained_scan(value.row_contract, selected_table, selected_parts)
        validations.extend(checks)
        private_parts[id(value)] = selected_private_parts(
            tuple(({} if selected_parts is None else selected_parts).items()),
            value.row_contract,
            result,
            required=False,
        )
        return result

    def visit(value: Dataset) -> ir.Table:
        nonlocal attribution_proof
        admit_retained_rows(value)
        if isinstance(value, MaterializedDataset):
            return read(value)
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            raise compilation_error("an exact retained row graph", "invalid retained row node")
        payload = value._root.payload
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
    if isinstance(ordering, _OrderedOrdering):
        names = {field.field_id: field.name for field in dataset.schema.columns}
        expression = _Compiler._order(
            expression,
            tuple((names[term.field_id], term.direction, term.nulls) for term in ordering.terms),
        )
    elif keys:
        expression = _Compiler._order(expression, tuple((key, "ascending", "last") for key in keys))
    checks, _ = _named_validations(tuple(validations))
    return CompiledDataset(
        expression,
        checks,
        tuple(field.name for field in dataset.schema.columns),
        (
            *retained_part_specs(dataset.row_contract),
            *private_part_specs(dataset.row_contract, private_parts[id(dataset)]),
        ),
        attribution_proof=attribution_proof,
    )
