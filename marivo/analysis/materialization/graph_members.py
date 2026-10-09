"""Exact v8 member graph construction and R1 source binding."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import uuid4

import ibis
import pyarrow as pa

from marivo._temporal import BeforeEndBoundary
from marivo.analysis.anchors import CalendarWindow
from marivo.analysis.compiler.graph_lowering import (
    ComponentColumn,
    CoordinateColumn,
    PartColumns,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import RouteChoice
from marivo.analysis.compiler.member_version import selection
from marivo.analysis.core.graph import (
    Edge,
    MethodNode,
    SourceDefinition,
    SourceLeaf,
    method_node,
    topology,
)
from marivo.analysis.core.model import (
    AnchorDomainPart,
    Binding,
    ConditionCellsPart,
    Coordinate,
    CoveragePart,
    DomainSignature,
    FitInputsPart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    HistoryViewPart,
    InstanceRetentionPart,
    PairInputsPart,
    RunCellsPart,
    SubjectPart,
    SubjectRetentionPart,
    TrainingInputsPart,
)
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    AnchorRetention,
    AssociationFit,
    AssociationRead,
    AttributionDerive,
    BindProject,
    CellDerive,
    DeviationFit,
    ForecastFit,
    ForecastRead,
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    RetentionBySubject,
    RowState,
    TimeProduct,
    TimeRuns,
    entity_members,
)
from marivo.analysis.core.time_grid import GridPoint, GridVersionSelection
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_preflight import (
    EntitySchema,
    bind_entity_output,
    preflight_entities,
)
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.methods.physical import ScalarType, ValueType
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.analysis.observation.route_inputs import RelationshipPath
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.refs import (
    DimensionKind,
    EntityKind,
    MeasureKind,
    Ref,
    RefPayloadV1,
    RelationshipKind,
    SemanticKind,
    TimeDimensionKind,
    ref,
)
from marivo.semantic._expression_binding import (
    CompiledExpressionSidecar,
    ExpressionBody,
    evaluate_expression_body,
)
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    TargetDimensionContract,
    TargetSnapshotSelection,
    TargetValiditySelection,
    TimestampParse,
)
from marivo.semantic.validator import (
    Registry,
    normalize_target_dimension,
    normalize_target_entity,
)


@dataclass(frozen=True, slots=True)
class MemberGraph:
    """One typed Entity member graph with selected physical schema facts."""

    runtime: DatasetRuntime
    registry: Registry
    entity_schema: EntitySchema
    leaf: SourceLeaf
    root: MethodNode
    sources: tuple[tuple[EntitySchema, SourceLeaf], ...] = ()
    expression_sidecar: CompiledExpressionSidecar | None = None

    def read(
        self,
        dimension: Ref[DimensionKind] | Ref[MeasureKind] | Ref[TimeDimensionKind],
        *,
        at: datetime | BeforeEndBoundary | GridPoint | None = None,
        via: Ref[RelationshipKind] | RelationshipPath | None = None,
        sidecar: CompiledExpressionSidecar | None = None,
        report_timezone: str = "UTC",
        inherit_member_version: bool = False,
        resolver: RelationshipResolver | None = None,
    ) -> MemberGraph:
        """Bind a typed scalar field through one complete, single-valued path."""

        def reject(received: str) -> DatasetConstructionError:
            return DatasetConstructionError(
                expected="an exact scalar field, explicit version and single-valued member-to-owner path",
                received=received,
                repair="Select a declared field and an unambiguous to-one route; supply its own at for historical attributes.",
                location="analysis.graph_members.read",
            )

        if not isinstance(dimension, Ref) or dimension.kind not in (
            SemanticKind.DIMENSION,
            SemanticKind.TIME_DIMENSION,
            SemanticKind.MEASURE,
        ):
            raise reject(repr(dimension))
        if isinstance(at, GridPoint) and at.grid != self.root.signature.domain.time_grid:
            raise reject("attribute endpoint belongs to a different grid")
        body: ExpressionBody | None = None
        if dimension.kind is SemanticKind.MEASURE or (
            dimension.kind is SemanticKind.DIMENSION
            and (declared := self.registry.dimensions.get(dimension.path)) is not None
            and declared.source_column is None
        ):
            measure = (
                self.registry.measures.get(dimension.path)
                if dimension.kind is SemanticKind.MEASURE
                else self.registry.dimensions.get(dimension.path)
            )
            body = (
                next(
                    (
                        value
                        for key, value in sidecar.bodies.items()
                        if key.path == dimension.path and key.kind == dimension.kind.value
                    ),
                    None,
                )
                if sidecar
                else None
            )
            if measure is None or body is None:
                raise reject("Computed field needs one compiled expression body")
            field = TargetDimensionContract(
                RefPayloadV1.from_ref(dimension),
                RefPayloadV1.from_ref(ref.entity(measure.entity)),
                body.source_column or "__marivo_expression_value__",
                "unknown",
                True,
                False,
                None,
                False,
                None,
            )
        else:
            field = normalize_target_dimension(self.registry, dimension.path)
            if field.ref.kind != dimension.kind:
                raise reject("field kind differs from its declaration")
        start = self.root.signature.domain.instance_key[0].entity_ref.path
        owner = field.entity_ref.path
        if isinstance(via, tuple):
            raise reject("read accepts one Relationship Ref or RelationshipPath")
        resolver = resolver or RelationshipResolver.build(self.registry)
        overrides = resolver.overrides(
            via, roots=(ref.entity(start),), target="dsl.LogicalAnalysisDomain.read"
        )
        refs = resolver.resolve(
            start, owner, explicit=overrides.get(start), target="dsl.LogicalAnalysisDomain.read"
        )
        contracts = tuple(resolver.by_ref[item.path] for item in refs)
        current = start
        for item in contracts:
            if item.from_entity_ref.path != current or item.cardinality not in (
                "one_to_one",
                "many_to_one",
            ):
                raise reject("route is not directed and single-valued")
            current = item.to_entity_ref.path
        if len({start, *(item.to_entity_ref.path for item in contracts)}) != len(contracts) + 1:
            raise reject("a cyclic relationship route is not a scalar attribute path")
        if current != owner:
            raise reject("an explicit path ending at the field owner is required")
        paths = tuple(dict.fromkeys((start, *(item.to_entity_ref.path for item in contracts))))
        normalized = tuple(normalize_target_entity(self.registry, path) for path in paths)
        # Resolve all static version errors before schema access.
        anchors = tuple(
            self.root.signature.domain.version_selection
            if (contracts or inherit_member_version) and item.ref.path == start
            else selection(
                item,
                at
                if item.version is not None
                or (item.ref.path == owner and not isinstance(at, GridPoint))
                else None,
                report_timezone,
            )
            for item in normalized
        )
        schemas = preflight_entities(
            self.registry,
            self.runtime.store.project_root,
            paths,
            frozen_reader=self.entity_schema.reader_timezone,
            expression_sidecar=sidecar,
            connections=self.runtime.connection_service(),
        )
        expression_bodies: tuple[tuple[str, str, str], ...] = ()
        if body is not None and body.source_column is None:
            if sidecar is None:
                raise reject("computed field has no loaded expression sidecar")
            seen: set[tuple[str, str]] = set()
            dependencies: list[tuple[str, str, str]] = []

            def collect(
                expression_ref: Ref[MeasureKind | DimensionKind | TimeDimensionKind],
            ) -> None:
                key = (expression_ref.kind.value, expression_ref.path)
                if key in seen:
                    return
                seen.add(key)
                expression = next(
                    (value for item, value in sidecar.bodies.items() if item == expression_ref),
                    None,
                )
                if expression is None:
                    raise reject(f"missing bound expression body for {expression_ref.path}")
                dependencies.append((*key, expression.body_ast_hash))
                for binding in expression.bindings:
                    collect(binding.to_ref())

            collect(dimension)
            expression_bodies = tuple(dependencies)
            alias = ibis.table(ibis.schema(schemas[-1].schema), name="member_read_schema")
            expression_value = evaluate_expression_body(
                catalog_definition_fingerprint=body.body_ast_hash,
                expression_sidecar=sidecar,
                owning_ref=dimension,
                body=body,
                entity_refs=(ref.entity(owner),),
                aliases=(alias,),
            )
            expression_type = str(expression_value.type())
            if dimension.kind is SemanticKind.DIMENSION:
                if expression_type != "boolean":
                    raise reject(f"computed Dimension requires boolean, received {expression_type}")
                physical: ValueType = ScalarType("boolean")
            else:
                if expression_type not in ("int64", "float64"):
                    raise reject(
                        f"computed Measure has unqualified {expression_value.type()} value"
                    )
                physical = (
                    ScalarType("int64") if expression_type == "int64" else ScalarType("float64")
                )
        else:
            physical = schemas[-1].field_type(field.source_column)
        if field.parse is not None and not isinstance(
            field.parse, (DateParse, TimestampParse, DatetimeParse)
        ):
            raise reject("unqualified field parsing")
        from marivo.analysis.methods.physical import DecimalType

        if (
            dimension.kind is SemanticKind.MEASURE
            and physical.name not in ("int64", "float64")
            and not isinstance(physical, DecimalType)
        ):
            raise reject("Measure requires a qualified numeric physical type")
        field_type = (
            schemas[-1].schema.field(field.source_column).type if not expression_bodies else None
        )
        if (
            field.is_time_dimension
            and field_type is not None
            and pa.types.is_timestamp(field_type)
            and field_type.tz is None
        ):
            raise reject(
                "naive timestamp attribute conversion is not qualified; use a native aware timestamp"
            )
        if field.is_time_dimension and physical.name not in ("date", "timestamp"):
            raise reject("TimeDimension requires a native temporal physical type")
        if (
            not field.is_time_dimension
            and dimension.kind is not SemanticKind.MEASURE
            and physical.name not in ("string", "int64", "boolean")
        ):
            raise reject("Dimension requires a categorical or boolean physical type")
        leaves = tuple(
            self.leaf
            if schema.contract == self.entity_schema.contract
            and anchor == self.leaf.signature.domain.version_selection
            else _member_leaf(schema, self.root.signature.domain.binding, anchor, auxiliary=True)
            for schema, anchor in zip(schemas, anchors, strict=True)
        )
        root = method_node(
            (Edge("subject", self.root),),
            BindProject(
                dimension,
                ref.entity(owner),
                replace(field, logical_type=physical.name),
                None,
                refs,
                contracts,
                resolved_versions=tuple(
                    item.ref.path for item in normalized if item.version is not None
                ),
                expression_bodies=expression_bodies,
                measure_unit=self.registry.measures[dimension.path].unit
                if dimension.kind is SemanticKind.MEASURE
                else None,
                attribute_time=digest(repr(anchors))
                if dimension.kind is SemanticKind.MEASURE
                and any(anchor is not None for anchor in anchors)
                else "untimed",
                owner_selection=leaves[-1].signature.domain.version_selection,
            ),
            sources=leaves,
            value_type=physical,
        )
        entries = self.sources or ((self.entity_schema, self.leaf),)
        return replace(
            self,
            root=root,
            sources=(*entries, *zip(schemas, leaves, strict=True)),
            expression_sidecar=sidecar,
        )

    def where(self, predicate: ValuePredicate) -> MemberGraph:
        """Filter the exact current Cell and retain its member Subject part."""
        if (
            not isinstance(self.root.parameters, BindProject)
            or predicate.binding != self.root.signature.domain.binding
        ):
            raise DatasetConstructionError(
                expected="a direct field read and predicate bound to the same member graph",
                received=repr(predicate.binding),
                repair="Read an exact member Dimension, then build its predicate.",
                location="analysis.graph_members.where",
            )
        if (self.root.value_type == ScalarType("string") and type(predicate.value) is not str) or (
            self.root.value_type == ScalarType("int64") and type(predicate.value) is not int
        ):
            raise DatasetConstructionError(
                expected=f"a {self.root.value_type} predicate literal",
                received=type(predicate.value).__name__,
                repair="Use a literal with the selected field's exact physical type.",
                location="analysis.graph_members.where",
            )
        root = method_node(
            (Edge("subject", self.root),),
            PartsTransport("where", self.root.signature.domain, ("subject",), False, (predicate,)),
            value_type=self.root.value_type,
        )
        return replace(self, root=root)

    def group_by_value(self, dimension: Ref[DimensionKind]) -> MemberGraph:
        """Map one direct string member field to its complete Group set."""
        read = self.read(dimension, inherit_member_version=True)
        if read.root.value_type != ScalarType("string"):
            raise DatasetConstructionError(
                expected="a string valued member Group coordinate",
                received=repr(read.root.value_type),
                repair="Select a qualified categorical member Dimension.",
                location="analysis.graph_members.group",
            )
        domain = self.root.signature.domain
        group_key = (
            Coordinate(
                self.root.signature.domain.instance_key[0].entity_ref, dimension.path, "group"
            ),
        )
        target = DomainSignature(
            domain.binding,
            "group",
            group_key,
            group_key,
            f"group:{dimension.path}",
        )
        root = method_node(
            (Edge("subject", read.root),),
            MapCorrespond("group", target, "source.group_mapping@v1"),
            value_type=read.root.value_type,
        )
        return replace(read, root=root)

    def execute(self) -> GraphArtifact:
        """Publish one fresh source evaluation through the common v8 Runtime."""
        contract = self.entity_schema.contract
        datasource = self.registry.datasources[contract.datasource_ref.path]
        service = self.runtime.connection_service()
        entries = self.sources or ((self.entity_schema, self.leaf),)
        schemas = {leaf.identity: schema for schema, leaf in entries}
        ordered = tuple(
            (schemas[node.identity], node)
            for node in topology(self.root)
            if isinstance(node, SourceLeaf)
        )

        @contextmanager
        def source_factory() -> Iterator[tuple[SourceSession, tuple[SourceBinding, ...]]]:
            owner: SourceSession | None = None

            def disconnected(succeeded: bool) -> None:
                if succeeded and owner is not None:
                    owner.mark_backend_disconnected()
                elif owner is not None:
                    raise DatasetConstructionError(
                        expected="confirmed source connection release",
                        received="selected backend disconnect failed or unavailable",
                        repair="Close the source connection and repair its driver before retrying execution.",
                        location="analysis.graph_members",
                    )

            with (
                service.use_backend(
                    datasource.name, read_only=True, on_disconnect=disconnected
                ) as backend,
                SourceSession(
                    provider_for(self.entity_schema.shape.backend),
                    datasource,
                    backend,
                    owns_backend=False,
                ) as source,
            ):
                owner = source
                if datasource.backend_type == "sqlite":
                    from sqlite3 import Connection

                    from marivo.analysis.materialization.temporal_sql import (
                        _initialize_sqlite_functions,
                    )

                    connection: object = getattr(backend, "con", None)
                    if not isinstance(connection, Connection):
                        raise DatasetConstructionError(
                            expected="the selected native SQLite connection",
                            received=type(connection).__name__,
                            repair="Reconnect the governed SQLite datasource and retry execution.",
                            location="analysis.graph_members",
                        )
                    _initialize_sqlite_functions(connection)
                from marivo.datasource.timezone import probe_engine_timezone

                frozen_timezones = {
                    schema.reader_timezone
                    for schema, _ in ordered
                    if schema.reader_timezone is not None
                    and schema.reader_timezone.read_tz_resolution == "engine"
                }
                if frozen_timezones and frozen_timezones != {probe_engine_timezone(backend)}:
                    raise DatasetConstructionError(
                        expected="the frozen driver-reported source timezone",
                        received="source timezone changed after graph construction",
                        repair="Rebuild the logical graph against the current datasource configuration.",
                        location="analysis.graph_members",
                    )
                bindings: list[SourceBinding] = []
                for schema, leaf in ordered:
                    bound = source.bind(schema.contract.source, source_identity=leaf.identity)
                    schema.verify(bound)
                    entity_relation = bind_entity_output(
                        self.registry,
                        self.expression_sidecar,
                        schema.contract.ref.path,
                        source,
                        bound,
                        schema.contract.dependency_fingerprint,
                    )
                    if (
                        self.expression_sidecar is not None
                        and self.expression_sidecar.bodies.get(ref.entity(schema.contract.ref.path))
                        is not None
                        and not entity_relation.schema()
                        .to_pyarrow()
                        .equals(schema.schema, check_metadata=True)
                    ):
                        raise DatasetConstructionError(
                            expected="the frozen Entity output schema",
                            received="Entity output changed after graph construction",
                            repair="Rebuild the logical graph against the current Entity definition.",
                            location="analysis.graph_members",
                        )
                    subject = next(
                        part for part in leaf.signature.parts if isinstance(part, SubjectPart)
                    )
                    columns = schema.contract.primary_key
                    layout = RelationLayout(
                        tuple(
                            CoordinateColumn(coordinate, column)
                            for coordinate, column in zip(
                                leaf.signature.domain.instance_key, columns, strict=True
                            )
                        ),
                        None,
                        (
                            PartColumns(
                                subject,
                                tuple(
                                    ComponentColumn(f"key_{i}", column)
                                    for i, column in enumerate(columns)
                                ),
                            ),
                        ),
                    )
                    bindings.append(
                        SourceBinding(
                            leaf,
                            bound,
                            layout,
                            expression_sidecar=self.expression_sidecar,
                            entity_relation=entity_relation,
                        )
                    )
                yield source, tuple(bindings)

        selected = tuple(
            SourceKeyBinding(
                leaf,
                leaf.definition.shape,
                leaf.definition.fingerprint,
                digest(canonical_json(schema.contract.source.to_dict())),
            )
            for schema, leaf in ordered
        )
        return self.runtime._execute_graph(
            self.root,
            tuple(
                RouteChoice(
                    node.identity,
                    "ibis_python"
                    if (
                        isinstance(node.parameters, PartsTransport)
                        and (
                            node.parameters.mode == "business_coverage"
                            or any(
                                isinstance(p, CoveragePart) and p.business_windows is not None
                                for p in node.inputs[0].node.signature.parts
                            )
                        )
                    )
                    or (
                        isinstance(node.parameters, AnchorBind)
                        and node.inputs[0].node.signature.domain.kind == "journey"
                    )
                    or (
                        isinstance(node.parameters, RowState)
                        and isinstance(node.inputs[0].node, MethodNode)
                        and isinstance(node.inputs[0].node.parameters, PreparedObservation)
                    )
                    or (
                        isinstance(node.parameters, (OriginalReduce, CellDerive, AttributionDerive))
                        and any(
                            isinstance(ancestor, MethodNode)
                            and isinstance(ancestor.parameters, PreparedObservation)
                            and ancestor.inputs[0].node.identity == ancestor.inputs[1].node.identity
                            for ancestor in topology(node)
                        )
                    )
                    or (
                        isinstance(node.parameters, TimeProduct)
                        and any(
                            isinstance(ancestor, MethodNode)
                            and isinstance(ancestor.parameters, (DeviationFit, TimeRuns))
                            for ancestor in topology(node.inputs[0].node)
                        )
                    )
                    or (
                        isinstance(node.parameters, AnchorObserve)
                        and (
                            isinstance(node.parameters.window, CalendarWindow)
                            or any(
                                isinstance(ancestor, SourceLeaf)
                                and ancestor.definition.shape.backend != "duckdb"
                                for ancestor in topology(node)
                            )
                            or any(
                                isinstance(p, AnchorDomainPart) and p.journey is not None
                                for p in node.inputs[0].node.signature.parts
                            )
                        )
                    )
                    or (
                        isinstance(node.parameters, PartsTransport)
                        and node.parameters.mode == "cohort"
                        and node.parameters.opportunity_domain is not None
                        and node.parameters.opportunity_domain.kind == "journey"
                    )
                    or (
                        node.inputs[0].node.signature.domain.kind
                        in ("journey", "interval", "anchor")
                        and isinstance(node.parameters, (MapCorrespond, PartsTransport, RowState))
                    )
                    or any(
                        isinstance(
                            p,
                            (
                                ConditionCellsPart,
                                RunCellsPart,
                                PairInputsPart,
                                TrainingInputsPart,
                                FitInputsPart,
                                FunnelPart,
                                FunnelComparisonPart,
                                FunnelAllocationPart,
                                InstanceRetentionPart,
                                SubjectRetentionPart,
                                HistoryViewPart,
                            ),
                        )
                        for e in node.inputs
                        for p in e.node.signature.parts
                    )
                    or (
                        isinstance(node.parameters, AnchorRetention)
                        and (
                            isinstance(node.parameters.window, CalendarWindow)
                            or any(
                                isinstance(ancestor, SourceLeaf)
                                and ancestor.definition.shape.backend != "duckdb"
                                for ancestor in topology(node)
                            )
                            or any(
                                isinstance(p, AnchorDomainPart) and p.journey is not None
                                for p in node.inputs[0].node.signature.parts
                            )
                        )
                    )
                    or isinstance(
                        node.parameters,
                        (
                            AssociationFit,
                            AssociationRead,
                            ForecastFit,
                            ForecastRead,
                            RetentionBySubject,
                            PreparedObservation,
                        ),
                    )
                    or node.method.name
                    in (
                        "association.pearson",
                        "association.kendall",
                        "association.read",
                        "forecast.naive",
                        "forecast.drift",
                        "forecast.seasonal_naive",
                        "forecast.read",
                        "time.runs",
                        "time.runs_read",
                        "deviation.zscore",
                        "deviation.mad",
                        "deviation.read",
                        "funnel.reduce",
                        "funnel.compare",
                        "funnel.read",
                        "funnel_ratio_mix",
                        "history.replay",
                        "history.in_state",
                        "history.distribution",
                        "history.transitions",
                        "history.violations",
                        "history.intervals",
                        "history.dwell",
                        "history.read",
                        "journey.match",
                        "journey.duration",
                        "journey.completed",
                        "journey.read",
                        "cell.relative_change",
                        "cell.ratio",
                        "reference.share",
                        "reference.penetration",
                        "reference.standardize",
                        "attribution.additive_difference",
                        "attribution.component_mix",
                        "display.rank",
                        "display.table",
                    )
                    else "ibis",
                )
                for node in topology(self.root)
                if isinstance(node, MethodNode)
            ),
            source_bindings=selected,
            source_factory=source_factory,
            source_schemas=tuple(
                schema.source_schema if schema.source_schema is not None else schema.schema
                for schema, _ in ordered
            ),
        )


def _member_leaf(
    selected: EntitySchema,
    binding: Binding,
    anchor: TargetSnapshotSelection | TargetValiditySelection | GridVersionSelection | None,
    *,
    auxiliary: bool = False,
) -> SourceLeaf:
    contract = replace(
        selected.contract, columns=tuple((field.name, str(field.type)) for field in selected.schema)
    )
    signature = entity_members(
        contract, ref.entity(contract.ref.path), binding, version_selection=anchor
    )
    if auxiliary:
        signature = replace(signature, obligations=())
    return SourceLeaf(
        SourceDefinition(
            ref.entity(contract.ref.path),
            digest(contract.dependency_fingerprint + schema_text(selected.schema)),
            ref.datasource(contract.datasource_ref.path),
            selected.shape,
            contract.version,
        ),
        signature,
        selected.identity_type,
    )


def construct_members(
    runtime: DatasetRuntime,
    registry: Registry,
    entity: Ref[EntityKind],
    *,
    at: datetime | BeforeEndBoundary | None = None,
    report_timezone: str = "UTC",
    expression_sidecar: CompiledExpressionSidecar | None = None,
) -> MemberGraph:
    """Resolve only R1 schema facts and construct one exact member graph."""
    if not isinstance(entity, Ref) or entity.kind is not SemanticKind.ENTITY:
        raise DatasetConstructionError(
            expected="an Entity Ref",
            received=repr(entity),
            repair="Use a declared Entity Ref.",
            location="analysis.members",
        )
    contract = normalize_target_entity(registry, entity.path)
    anchor = selection(contract, at, report_timezone)
    selected = preflight_entities(
        registry,
        runtime.store.project_root,
        (entity.path,),
        expression_sidecar=expression_sidecar,
        connections=runtime.connection_service(),
    )[0]
    binding = Binding(runtime.session_ref, runtime.store.store_id, uuid4().hex, "all")
    leaf = _member_leaf(selected, binding, anchor)
    root = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", leaf.signature.domain, ("subject",), False),
        value_type=selected.identity_type,
    )
    return MemberGraph(
        runtime, registry, selected, leaf, root, expression_sidecar=expression_sidecar
    )
