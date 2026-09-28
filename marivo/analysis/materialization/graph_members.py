"""Exact v7 member graph construction and R1 source binding."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from uuid import uuid4

from marivo.analysis.compiler.graph_lowering import (
    ComponentColumn,
    CoordinateColumn,
    PartColumns,
    RelationLayout,
    SourceBinding,
)
from marivo.analysis.compiler.graph_plan import RouteChoice
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
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import canonical_json
from marivo.analysis.materialization.execution_key import SourceKeyBinding
from marivo.analysis.materialization.graph_preflight import EntitySchema, preflight_entities
from marivo.analysis.materialization.graph_protocol import digest, schema_text
from marivo.analysis.materialization.graph_store import GraphArtifact
from marivo.analysis.methods.physical import ScalarType
from marivo.datasource.adapters import SourceSession, provider_for
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import DimensionKind, EntityKind, Ref, ref
from marivo.semantic.validator import Registry, normalize_target_dimension


@dataclass(frozen=True, slots=True)
class MemberGraph:
    """One typed Entity member graph with selected physical schema facts."""

    runtime: DatasetRuntime
    registry: Registry
    entity_schema: EntitySchema
    leaf: SourceLeaf
    root: MethodNode
    sources: tuple[tuple[EntitySchema, SourceLeaf], ...] = ()

    def read(self, dimension: Ref[DimensionKind]) -> MemberGraph:
        """Bind an exact direct string or int64 Entity Dimension to these members."""
        field = normalize_target_dimension(self.registry, dimension.path)
        if field.entity_ref.path != self.entity_schema.contract.ref.path:
            raise DatasetConstructionError(
                expected="a direct Dimension on the exact member Entity",
                received=dimension.path,
                repair="Select a Dimension declared on the member Entity.",
                location="analysis.graph_members.read",
            )
        physical = self.entity_schema.field_type(field.source_column)
        if (
            field.parse is not None
            or field.logical_type not in ("unknown", physical.name)
            or physical.name
            not in (
                "string",
                "int64",
            )
        ):
            raise DatasetConstructionError(
                expected="a direct int64 or string Dimension with exact physical type",
                received=f"{dimension.path}: {field.logical_type}/{physical.name}",
                repair="Use a qualified direct member Dimension.",
                location="analysis.graph_members.read",
            )
        root = method_node(
            (Edge("subject", self.root),),
            BindProject(
                dimension,
                ref.entity(self.entity_schema.contract.ref.path),
                replace(field, logical_type=physical.name),
                None,
                (),
                (),
            ),
            sources=(self.leaf,),
            value_type=physical,
        )
        return replace(self, root=root)

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
        read = self.read(dimension)
        if read.root.value_type != ScalarType("string"):
            raise DatasetConstructionError(
                expected="a string valued member Group coordinate",
                received=repr(read.root.value_type),
                repair="Select a qualified categorical member Dimension.",
                location="analysis.graph_members.group",
            )
        domain = self.root.signature.domain
        group_key = (
            Coordinate(ref.entity(self.entity_schema.contract.ref.path), dimension.path, "group"),
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
        return replace(self, root=root)

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
                    provider_for("duckdb"), datasource, backend, owns_backend=False
                ) as source,
            ):
                bindings: list[SourceBinding] = []
                for schema, leaf in ordered:
                    bound = source.bind(schema.contract.source, source_identity=leaf.identity)
                    schema.verify(bound)
                    coordinate = leaf.signature.domain.instance_key[0]
                    subject = next(
                        part for part in leaf.signature.parts if isinstance(part, SubjectPart)
                    )
                    column = schema.contract.primary_key[0]
                    layout = RelationLayout(
                        (CoordinateColumn(coordinate, column),),
                        None,
                        (PartColumns(subject, (ComponentColumn("key_0", column),)),),
                    )
                    bindings.append(SourceBinding(leaf, bound, layout))
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
                RouteChoice(node.identity, "ibis")
                for node in topology(self.root)
                if isinstance(node, MethodNode)
            ),
            source_bindings=selected,
            source_factory=source_factory,
            source_schemas=tuple(schema.schema for schema, _ in ordered),
        )


def construct_members(
    runtime: DatasetRuntime, registry: Registry, entity: Ref[EntityKind]
) -> MemberGraph:
    """Resolve only R1 schema facts and construct one exact member graph."""
    selected = preflight_entities(registry, runtime.store.project_root, (entity.path,))[0]
    binding = Binding(runtime.session_ref, runtime.store.store_id, uuid4().hex, "all")
    physical_fingerprint = digest(
        selected.contract.dependency_fingerprint + schema_text(selected.schema)
    )
    selected_contract = replace(
        selected.contract,
        columns=tuple((field.name, str(field.type)) for field in selected.schema),
    )
    signature = entity_members(selected_contract, entity, binding)
    leaf = SourceLeaf(
        SourceDefinition(
            entity,
            physical_fingerprint,
            ref.datasource(selected.contract.datasource_ref.path),
            selected.shape,
        ),
        signature,
        selected.identity_type,
    )
    root = method_node(
        (Edge("subject", leaf),),
        PartsTransport("view", signature.domain, ("subject",), False),
        value_type=selected.identity_type,
    )
    return MemberGraph(runtime, registry, selected, leaf, root)
