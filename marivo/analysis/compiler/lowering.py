"""One-source Ibis lowering of the authored private Observation graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.compiler.nodes import CompiledDataset, CompiledValidation, RetainedPartSpec
from marivo.analysis.compiler.normalize import logical_roots, predicate_leaves, required_entities
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.descriptors import _canonical_digest, _CatalogFieldIdentity
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.observation.contracts import (
    MetricDefinition,
    MetricPayload,
    PopulationPayload,
    metric_contracts,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import functional_path
from marivo.analysis.observation.predicates import BoundPredicate
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
    MetricGraphNodeV1,
    RatioNodeV1,
    TargetMetricContract,
    WeightedMeanAggregateNodeV1,
)
from marivo.semantic.validator import normalize_target_dimension


@dataclass(frozen=True, slots=True, repr=False)
class _Rows:
    expression: ir.Table
    membership: ir.Table
    entity: TargetEntityContract
    definition: MetricDefinition | None = None


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


def _predicate(table: ir.Table, predicate: BoundPredicate) -> ir.BooleanValue:
    if predicate.kind == "all_of":
        return _and([_predicate(table, child) for child in predicate.children])
    if predicate.field is None or not isinstance(predicate.literal, tuple):
        raise compilation_error("complete bound predicate", "invalid predicate")
    kind, value = predicate.literal
    if kind == "decimal" and isinstance(value, str):
        literal = ibis.literal(Decimal(value))
    elif kind == "date" and isinstance(value, str):
        literal = ibis.literal(date.fromisoformat(value))
    elif kind == "instant" and isinstance(value, str):
        literal = ibis.literal(datetime.fromisoformat(value))
    elif value is None or isinstance(value, (str, int, float, bool)):
        literal = ibis.literal(value)
    else:
        raise compilation_error("closed scalar predicate literal", "unsupported literal")
    column = table[predicate.field.name]
    return _boolean(column == literal if predicate.kind == "eq" else column > literal)


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


class _Compiler:
    def __init__(self, dataset: LogicalDataset, tables: Mapping[str, ir.Table]) -> None:
        self.dataset = dataset
        self.owner = source_owner_of(dataset)
        self.registry = self.owner.semantic_registry
        self.tables = tables
        self.validations: list[CompiledValidation] = []
        self.cache: dict[int, _Rows] = {}
        self.entities = required_entities(dataset)
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
            if any(table[name].type() != dt.dtype(kind) for name, kind in entity.columns):
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
        self.validations.append(CompiledValidation(name, table.aggregate(violations=table.count())))

    def _unique(self, name: str, table: ir.Table, keys: tuple[str, ...]) -> None:
        grouped = table.group_by(list(keys)).aggregate(__mv_count=table.count())
        self._count(name, grouped.filter(grouped["__mv_count"] > 1))

    def _validate_source(self, entity: TargetEntityContract, table: ir.Table) -> None:
        prefix = entity.ref.path
        if entity.primary_key:
            non_null = _and([table[name].notnull() for name in entity.primary_key])
            self._count(f"{prefix}.identity_non_null", table.filter(~non_null))
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
            )
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
            elif payload.time_scope is not None and payload.reference_axis is not None:
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
            table = table.filter(_predicate(table, predicate))
        return _Rows(table.select(entity_identity=_identity(table, entity)), table, entity)

    def _measure_column(self, reference_path: str) -> str:
        for reference, body in self.owner.sidecar.bodies.items():
            if (
                reference.kind is SemanticKind.MEASURE
                and reference.path == reference_path
                and body.source_column is not None
            ):
                return body.source_column
        raise compilation_error("normalized direct-column Measure", "missing expression binding")

    def _component(
        self,
        metric: TargetMetricContract,
        node_id: str,
        definition: MetricDefinition,
        membership: ir.Table,
    ) -> ir.Table:
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
        if definition.time_scope is not None and definition.reference_axis is not None:
            axis = definition.reference_axis
            table = self._enrich(
                table, root, axis.entity_ref.path, ((axis.source_column, "__mv_scope"),)
            )
            column = table["__mv_scope"]
            table = table.filter(
                column >= definition.time_scope.start, column < definition.time_scope.end
            )
        if not isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
            raise compilation_error("registered aggregate component", "unsupported graph node")
        for index, condition in enumerate(node.filter):
            dimension = normalize_target_dimension(self.registry, condition.dimension_ref.path)
            alias = f"__mv_filter_{index}"
            table = self._enrich(
                table, root, dimension.entity_ref.path, ((dimension.source_column, alias),)
            )
            value = condition.value
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise compilation_error("scalar component slice", "unsupported slice literal")
            table = table.filter(table[alias].isnull() if value is None else table[alias] == value)
        states: dict[str, ir.Value] = {"row_count": table.count()}
        if isinstance(node, AggregateNodeV1):
            value = (
                table[self._measure_column(node.target_ref.path)]
                if node.target_ref.kind is SemanticKind.MEASURE
                else None
            )
            if node.agg == "count":
                states["count"] = value.count() if value is not None else table.count()
            else:
                if value is None:
                    raise compilation_error("numeric Measure aggregate", "missing Measure")
                states["sum"] = _numeric(value).sum()
                states["non_null_count"] = value.count()
        else:
            value = _numeric(table[self._measure_column(node.value_ref.path)])
            weight = _numeric(table[self._measure_column(node.weight_ref.path)])
            pairs = value.notnull() & weight.notnull()
            states["weighted_numerator"] = (value * weight).sum(where=pairs)
            states["weight_sum"] = weight.sum(where=pairs)
            states["non_null_pair_count"] = pairs.cast("int64").sum()
        return table.group_by("entity_identity").aggregate(
            **{_hidden(metric, node_id, name): value for name, value in states.items()}
        )

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
                    if node.agg == "count":
                        return table[_hidden(metric, node_id, "count")].fill_null(0)
                    total = _numeric(table[_hidden(metric, node_id, "sum")])
                    count = _numeric(table[_hidden(metric, node_id, "non_null_count")])
                    return total if node.agg == "sum" else total / count.nullif(0)
                if isinstance(node, WeightedMeanAggregateNodeV1):
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
                raise compilation_error(
                    "closed aggregate/weighted/ratio graph", "unsupported graph node"
                )

            output[field.name] = value(metric.graph.roots[0]).cast(metric.logical_type)
        return table.mutate(**output)

    def _observe(self, payload: MetricPayload, previous: _Rows) -> _Rows:
        definition = payload.definition
        membership = previous.membership
        selected = previous.expression.select("entity_identity").distinct()
        membership = membership.mutate(entity_identity=_identity(membership, definition.entity))
        membership = membership.join(selected, "entity_identity", how="semi").drop(
            "entity_identity"
        )
        table = selected
        for metric in definition.metrics:
            for node_id in dict.fromkeys(component.node_id for component in metric.components):
                part = self._component(metric, node_id, definition, membership)
                table = table.join(part, "entity_identity", how="left").select(
                    *table.columns, *(name for name in part.columns if name != "entity_identity")
                )
        return _Rows(self._values(table, definition), membership, definition.entity, definition)

    def _visit(self, root: LogicalRootHandle | MaterializedScanLeafHandle) -> _Rows:
        if isinstance(root, MaterializedScanLeafHandle):
            raise compilation_error(
                "logical source recipe", "retained input needs registered later recipe"
            )
        if id(root) in self.cache:
            return self.cache[id(root)]
        payload = root.payload
        if isinstance(payload, PopulationPayload):
            result = self._population(root, payload)
        elif isinstance(payload, MetricPayload):
            previous = self._visit(root.inputs[0].root)
            definition = payload.definition
            if root.operator_id == "session.observe":
                result = self._observe(payload, previous)
            else:
                table = previous.expression
                if payload.predicate is not None:
                    table = table.filter(_predicate(table, payload.predicate))
                row, _ = metric_contracts(
                    definition, self.dataset._registry.get("metric").ids, self.registry
                )
                keys = tuple(
                    field.name for field in row.schema.columns if field.role_id != "metric"
                )
                if root.operator_id in ("metric.with_dimensions", "metric.with_time_axis"):
                    lookup = previous.membership
                    coordinates = (
                        *definition.dimensions,
                        *((definition.time_axis,) if definition.time_axis else ()),
                    )
                    fields = tuple(
                        field
                        for field in row.schema.columns
                        if field.role_id in ("dimension", "time_dimension")
                    )
                    added: list[str] = []
                    for coordinate, field in zip(coordinates, fields, strict=True):
                        if field.name in table.columns:
                            continue
                        lookup = self._enrich(
                            lookup,
                            definition.entity.ref.path,
                            coordinate.entity_ref.path,
                            ((coordinate.source_column, field.name),),
                        )
                        if field.role_id == "time_dimension" and isinstance(
                            lookup[field.name], ir.TimestampValue
                        ):
                            timestamp = lookup[field.name]
                            if isinstance(timestamp, ir.TimestampValue):
                                lookup = lookup.mutate(**{field.name: timestamp.truncate("D")})
                        added.append(field.name)
                    lookup = lookup.select(
                        entity_identity=_identity(lookup, definition.entity),
                        **{name: lookup[name] for name in added},
                    )
                    table = table.join(lookup, "entity_identity", how="left").select(
                        *table.columns, *added
                    )
                if root.operator_id == "metric.aggregate":
                    names = tuple(
                        name for metric in definition.metrics for name in _state_names(metric)
                    )
                    aggregates = {name: _numeric(table[name]).sum() for name in names}
                    table = (
                        table.group_by(list(keys)).aggregate(**aggregates)
                        if keys
                        else table.aggregate(**aggregates)
                    )
                    table = self._values(table, definition)
                visible = tuple(field.name for field in row.schema.columns)
                state_names = tuple(
                    name for metric in definition.metrics for name in _state_names(metric)
                )
                table = table.select(*visible, *state_names)
                result = _Rows(table, previous.membership, previous.entity, definition)
        else:
            raise compilation_error("registered Observation source/operator", "unsupported payload")
        self.cache[id(root)] = result
        return result

    def compile(self) -> CompiledDataset:
        rows = self._visit(self.dataset._root)
        primary = tuple(field.name for field in self.dataset.schema.columns)
        parts: list[RetainedPartSpec] = []
        hidden: tuple[str, ...] = ()
        if rows.definition is not None:
            keys = tuple(
                field.name for field in self.dataset.schema.columns if field.role_id != "metric"
            )
            for metric in rows.definition.metrics:
                names = _state_names(metric)
                role = f"metric_components.{_canonical_digest(metric.ref.path)[:20]}"
                parts.append(
                    RetainedPartSpec(role, "metric.sufficient_components", 1, (*keys, *names))
                )
                hidden += names
        expression = rows.expression.select(*primary, *hidden)
        # DuckDB widens integer SUM physically while Ibis retains its int64 type.
        # Keep an explicit source cast: Value.cast elides same-type conversions.
        expression = expression.select(
            **{
                name: ops.Cast(expression[name], to=expression[name].type()).to_expr()
                if expression[name].type().is_numeric()
                else expression[name]
                for name in expression.columns
            }
        )
        key_names = tuple(
            field.name
            for field in self.dataset.schema.columns
            if field.field_id in self.dataset.row_contract.key_field_ids
        )
        if key_names:
            expression = expression.order_by(
                [expression[name].asc(nulls_first=False) for name in key_names]
            )
        return CompiledDataset(expression, tuple(self.validations), primary, tuple(parts))


def compile_dataset(dataset: LogicalDataset, tables: Mapping[str, ir.Table]) -> CompiledDataset:
    """Lower a logical Dataset using exact source tables without executing or reading rows."""
    return _Compiler(dataset, tables).compile()
