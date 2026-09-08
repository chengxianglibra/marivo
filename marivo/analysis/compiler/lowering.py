"""One-source Ibis lowering of the authored private Observation graph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, builtin_grain
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
from marivo.analysis.datasets.base import Dataset, LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.descriptors import (
    _canonical_digest,
    _CatalogFieldIdentity,
    _OrderedOrdering,
)
from marivo.analysis.datasets.handles import LogicalRootHandle, MaterializedScanLeafHandle
from marivo.analysis.observation.contracts import (
    MetricDefinition,
    MetricPayload,
    PopulationPayload,
    RankSpec,
    RetainedRowsPayload,
    metric_contracts,
    source_owner_of,
)
from marivo.analysis.observation.coordinates import functional_path, governed_path
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


@dataclass(frozen=True, slots=True, repr=False)
class _Rows:
    expression: ir.Table
    membership: ir.Table
    entity: TargetEntityContract
    definition: MetricDefinition | None = None
    selections: tuple[_Selection, ...] = ()
    ordering: tuple[tuple[str, str, str], ...] = ()


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


class _Compiler:
    def __init__(
        self,
        dataset: LogicalDataset,
        tables: Mapping[str, ir.Table],
        scans: Mapping[str, CompiledArtifactScan],
    ) -> None:
        self.dataset = dataset
        self.owner = source_owner_of(dataset)
        self.registry = self.owner.semantic_registry
        self.tables = tables
        self.scans = scans
        self.validations: list[CompiledValidation] = []
        self.validation_occurrences: dict[str, int] = {}
        self.preparations: list[CompiledValidation | CompiledSampleFence] = []
        self.prepared_validation_count = 0
        self.samples: dict[int, ir.Table] = {}
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
                    lower = self._bucket(lower, definition.grain, definition.temporal_snapshot)
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
                **{name: self._bucket(table[name], definition.grain, definition.temporal_snapshot)}
            )
        return table

    def _bucket(
        self, value: ir.Value, grain: Grain | None, snapshot: PeriodCalendarSnapshotV1 | None = None
    ) -> ir.Value:
        if grain is not None and grain.kind == "semantic":
            if snapshot is None or snapshot.calendar_ref != grain.calendar:
                raise compilation_error(
                    "the exact certified calendar snapshot", "missing period authority"
                )
            if grain.level == "day":
                return value.cast("date").cast(value.type())
            cases = tuple(
                (
                    _boolean((value >= period.start_date) & (value < period.end_date)),
                    ibis.literal(period.start_date).cast(value.type()),
                )
                for period in snapshot.periods
                if period.level_name == grain.level
            )
            if not cases:
                raise compilation_error("a certified calendar level", "missing calendar periods")
            return ibis.cases(*cases, else_=ibis.null().cast(value.type()))
        if grain is None or grain.unit is None or grain.count is None:
            raise compilation_error(
                "registered builtin source time bucket", "missing certified bucket implementation"
            )
        units = {
            "second": "s",
            "minute": "m",
            "hour": "h",
            "day": "D",
            "week": "W",
            "month": "M",
            "quarter": "Q",
            "year": "Y",
        }
        if not isinstance(value, (ir.DateValue, ir.TimestampValue)):
            raise compilation_error("governed date or timestamp bucket", "non-temporal coordinate")
        if grain.count == 1:
            return value.truncate(units[grain.unit])
        timestamp = value.cast("timestamp")
        if not isinstance(timestamp, ir.TimestampValue):
            raise compilation_error("timestamp bucket input", "invalid time representation")
        interval = ibis.interval(**{grain.unit + "s": grain.count})
        bucket = timestamp.bucket(interval)
        return bucket.cast("date") if isinstance(value, ir.DateValue) else bucket

    def _bucket_end(
        self, start: ir.Value, grain: Grain, snapshot: PeriodCalendarSnapshotV1 | None
    ) -> ir.Value:
        if grain.kind == "builtin":
            if grain.unit is None or grain.count is None:
                raise compilation_error("an exact builtin grain", "missing bucket width")
            return start.cast("timestamp") + ibis.interval(**{grain.unit + "s": grain.count})
        if snapshot is None or snapshot.calendar_ref != grain.calendar:
            raise compilation_error("the bound certified calendar", "missing endpoint authority")
        if grain.level == "day":
            return start.cast("timestamp") + ibis.interval(days=1)
        cases = tuple(
            (
                _boolean(start == period.start_date),
                ibis.literal(period.end_date).cast("timestamp"),
            )
            for period in snapshot.periods
            if period.level_name == grain.level
        )
        if not cases:
            raise compilation_error("certified endpoint periods", "missing calendar level")
        return ibis.cases(*cases, else_=ibis.null().cast("timestamp"))

    def _endpoint_reset_start(
        self, end: ir.Value, grain: Grain, snapshot: PeriodCalendarSnapshotV1 | None
    ) -> ir.Value:
        """Select the reset period immediately before an exclusive endpoint."""
        if grain.kind == "semantic" and grain.level != "day":
            if snapshot is None or snapshot.calendar_ref != grain.calendar:
                raise compilation_error("the bound reset calendar", "missing reset authority")
            cases = tuple(
                (
                    _boolean((end > period.start_date) & (end <= period.end_date)),
                    ibis.literal(period.start_date).cast("timestamp"),
                )
                for period in snapshot.periods
                if period.level_name == grain.level
            )
            if not cases:
                raise compilation_error("certified reset periods", "missing calendar level")
            return ibis.cases(*cases, else_=ibis.null().cast("timestamp"))
        start = self._bucket(end, grain, snapshot).cast("timestamp")
        unit, count = (grain.unit, grain.count) if grain.kind == "builtin" else ("day", 1)
        if unit is None or count is None:
            raise compilation_error("an exact reset grain", "missing reset width")
        previous = start - ibis.interval(**{unit + "s": count})
        return (start == end).ifelse(previous, start)

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
                        >= self._bucket(
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

    def _component(
        self,
        metric: TargetMetricContract,
        node_id: str,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...] = (),
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
                anchor = metric.cumulative[0].anchor
                if isinstance(anchor, tuple):
                    boundary = ibis.literal(definition.time_scope.end)
                    if anchor[0] == "trailing":
                        boundary = boundary - ibis.interval(**{anchor[2] + "s": anchor[1]})
                    else:
                        reset = (
                            builtin_grain(anchor[1]) if isinstance(anchor[1], str) else anchor[1]
                        )
                        boundary = self._endpoint_reset_start(
                            boundary, reset, definition.temporal_snapshot
                        )
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
                    states["value"] = numeric.quantile(quantile)
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
        return grouped.select(
            *keys,
            **{_hidden(metric, node_id, name): grouped[name] for name in component.required_state},
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
        end = self._bucket_end(start, grain, definition.temporal_snapshot)
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
        anchor = metric.cumulative[0].anchor
        if isinstance(anchor, tuple):
            if anchor[0] == "trailing":
                lower = end - ibis.interval(**{anchor[2] + "s": anchor[1]})
            else:
                reset = builtin_grain(anchor[1]) if isinstance(anchor[1], str) else anchor[1]
                lower = self._bucket(start, reset, definition.temporal_snapshot)
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

            output[field.name] = value(metric.graph.roots[0]).cast(metric.logical_type)
        return table.mutate(**output)

    def _evaluate(
        self,
        definition: MetricDefinition,
        membership: ir.Table,
        selections: tuple[_Selection, ...] = (),
    ) -> _Rows:
        table = self._spine(definition, membership, selections)
        for metric in definition.metrics:
            for node_id in dict.fromkeys(component.node_id for component in metric.components):
                part = self._component(metric, node_id, definition, membership, selections)
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
                >= self._bucket(
                    ibis.literal(definition.time_scope.start),
                    definition.grain,
                    definition.temporal_snapshot,
                )
            )
        if "__mv_scalar" in table.columns:
            table = table.drop("__mv_scalar")
        return _Rows(table, membership, definition.entity, definition, selections)

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
            if (
                scan is None
                or root.shape_id.family_id != "population"
                or scan.entity.version is not None
            ):
                raise compilation_error(
                    "an admitted non-versioned engine Population scan",
                    "unsupported retained source input",
                )
            table, entity = scan.expression, scan.entity
            identity = table["entity_identity"]
            if not isinstance(identity, ir.StructValue):
                raise compilation_error(
                    "the exact retained identity struct", "invalid engine identity"
                )
            membership = table.select(**{name: identity[name] for name in entity.primary_key})
            return _Rows(table, membership, entity)
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
            elif root.operator_id in (
                "metric.with_dimensions",
                "metric.with_time_axis",
                "metric.aggregate",
            ):
                result = self._evaluate(definition, previous.membership, previous.selections)
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
                table = table.select(*visible, *generated, *state_names)
                result = _Rows(
                    table, previous.membership, previous.entity, definition, selections, ordering
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
        primary = tuple(field.name for field in self.dataset.schema.columns)
        parts: list[RetainedPartSpec] = []
        hidden: tuple[str, ...] = ()
        if rows.definition is not None:
            keys = tuple(
                field.name
                for field in self.dataset.schema.columns
                if field.field_id in self.dataset.row_contract.key_field_ids
            )
            for metric in rows.definition.metrics:
                if not metric.required_state:
                    continue
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
                if expression[name].type().is_numeric() or expression[name].type().is_temporal()
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
        return CompiledDataset(
            expression, tuple(self.validations), primary, tuple(parts), tuple(self.preparations)
        )


def compile_dataset(
    dataset: LogicalDataset,
    tables: Mapping[str, ir.Table],
    *,
    scans: Mapping[str, CompiledArtifactScan] | None = None,
) -> CompiledDataset:
    """Lower a logical Dataset using exact source tables without executing or reading rows."""
    return _Compiler(dataset, tables, {} if scans is None else scans).compile()


def compile_retained_rows(dataset: LogicalDataset, table: ir.Table) -> CompiledDataset:
    """Lower only the admitted primary-only row algebra against an immutable engine leaf."""
    from marivo.analysis.operators.registry import admit_primary_only

    def visit(value: Dataset) -> ir.Table:
        admit_primary_only(value)
        if isinstance(value, MaterializedDataset):
            return table
        if not isinstance(value, LogicalDataset) or not isinstance(value._root, LogicalRootHandle):
            raise compilation_error("an exact retained row graph", "invalid retained row node")
        payload = value._root.payload
        if not isinstance(payload, RetainedRowsPayload) or len(value._inputs) != 1:
            raise compilation_error(
                "a registered primary-only row operation", "unsupported retained source method"
            )
        result = visit(value._inputs[0])
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
        return result.select(tuple(field.name for field in value.schema.columns))

    expression = visit(dataset)
    keys = tuple(
        field.name
        for field in dataset.schema.columns
        if field.field_id in dataset.row_contract.key_field_ids
    )
    validations: tuple[CompiledValidation, ...] = ()
    if keys:
        counts = expression.group_by(keys).aggregate(__mv_count=expression.count())
        validations = (
            CompiledValidation(
                "dataset.final_row_key_unique",
                counts.filter(counts.__mv_count > 1).aggregate(
                    violations=counts.filter(counts.__mv_count > 1).count()
                ),
            ),
        )
    return CompiledDataset(expression, validations, tuple(expression.columns), ())
