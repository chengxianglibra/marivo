"""Exact v7 member graph construction and R1 source binding."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import uuid4

import ibis
import pyarrow as pa

from marivo._temporal import BeforeEndBoundary
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
from marivo.analysis.core.model import Binding, Coordinate, DomainSignature, SubjectPart
from marivo.analysis.core.predicates import ValuePredicate
from marivo.analysis.core.rules import BindProject, MapCorrespond, PartsTransport, entity_members
from marivo.analysis.core.time_grid import GridPoint, GridVersionSelection
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_fields import RootRoutesValue
from marivo.analysis.materialization.graph_preflight import EntitySchema, preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.methods.physical import ScalarType, ValueType
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.runtime import DatasourceConnectionService
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
    normalize_target_relationship,
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
        via: Ref[RelationshipKind] | RootRoutesValue | None = None,
        sidecar: CompiledExpressionSidecar | None = None,
        report_timezone: str = "UTC",
        inherit_member_version: bool = False,
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
        if dimension.kind is SemanticKind.MEASURE:
            measure = self.registry.measures.get(dimension.path)
            body = (
                next(
                    (
                        value
                        for key, value in sidecar.bodies.items()
                        if key.path == dimension.path and key.kind == "measure"
                    ),
                    None,
                )
                if sidecar
                else None
            )
            if measure is None or body is None:
                raise reject("Measure needs one compiled expression body")
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
        if isinstance(via, RootRoutesValue):
            matching = tuple(item for item in via.routes if item.root.path == start)
            if len(matching) != 1 or len(via.routes) != 1:
                raise reject("read requires one route rooted at the current member Entity")
            refs = matching[0].through
        elif via is None:
            refs = ()
        elif isinstance(via, Ref) and via.kind is SemanticKind.RELATIONSHIP:
            refs = (via,)
        else:
            raise reject("invalid route")
        contracts = tuple(normalize_target_relationship(self.registry, item.path) for item in refs)
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
                at if item.version is not None or item.ref.path == owner else None,
                report_timezone,
            )
            for item in normalized
        )
        schemas = preflight_entities(self.registry, self.runtime.store.project_root, paths)
        expression_bodies: tuple[tuple[str, str, str], ...] = ()
        if body is not None and body.source_column is None:
            if sidecar is None:
                raise reject("computed Measure has no loaded expression sidecar")
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
            if expression_type not in ("int64", "float64"):
                raise reject(f"computed Measure has unqualified {expression_value.type()} value")
            physical: ValueType = (
                ScalarType("int64") if expression_type == "int64" else ScalarType("float64")
            )
        else:
            physical = schemas[-1].field_type(field.source_column)
        if field.parse is not None and not isinstance(
            field.parse, (DateParse, TimestampParse, DatetimeParse)
        ):
            raise reject("unqualified field parsing")
        if dimension.kind is SemanticKind.MEASURE and physical.name not in ("int64", "float64"):
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
        """Publish one fresh source evaluation through the common v7 Runtime."""
        contract = self.entity_schema.contract
        datasource = self.registry.datasources[contract.datasource_ref.path]
        service = DatasourceConnectionService(
            self.runtime.store.project_root, include_semantic_layers=True
        )
        entries = self.sources or ((self.entity_schema, self.leaf),)
        by_identity = {leaf.identity: (schema, leaf) for schema, leaf in entries}
        ordered = tuple(
            by_identity[node.identity]
            for node in topology(self.root)
            if isinstance(node, SourceLeaf)
        )

        @contextmanager
        def source_factory() -> Iterator[tuple[SourceSession, tuple[SourceBinding, ...]]]:
            with (
                service.use_backend(datasource.name, read_only=True) as backend,
                SourceSession(
                    provider_for(self.entity_schema.shape.backend),
                    datasource,
                    backend,
                    owns_backend=False,
                ) as source,
            ):
                from marivo.datasource.timezone import probe_engine_timezone

                authority = probe_engine_timezone(backend)
                if any(
                    schema.engine_timezone is not None
                    and schema.engine_timezone != authority.engine_timezone_name
                    for schema, _ in ordered
                ):
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
                            leaf, bound, layout, expression_sidecar=self.expression_sidecar
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
                    if node.method.name in ("cell.relative_change", "cell.ratio")
                    else "ibis",
                )
                for node in topology(self.root)
                if isinstance(node, MethodNode)
            ),
            source_bindings=selected,
            source_factory=source_factory,
            source_schemas=tuple(schema.schema for schema, _ in ordered),
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
    selected = preflight_entities(registry, runtime.store.project_root, (entity.path,))[0]
    binding = Binding(runtime.session_ref, runtime.store.store_id, uuid4().hex, "all")
    leaf = _member_leaf(selected, binding, anchor)
    root = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", leaf.signature.domain, ("subject",), False),
        value_type=selected.identity_type,
    )
    return MemberGraph(runtime, registry, selected, leaf, root)
