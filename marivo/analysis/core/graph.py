"""Private immutable definition graph; no execution or historical lineage traversal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
from typing import Literal, TypeAlias
from uuid import uuid4

from marivo.analysis.core.model import Signature, reject
from marivo.analysis.core.rules import (
    BindProject,
    CellDerive,
    ObserveCount,
    ObserveMetric,
    PartsTransport,
    RuleDerivation,
    RuleParameters,
)
from marivo.analysis.methods.physical import FixedShape, ScalarType, SourceShape, ValueType
from marivo.analysis.methods.registry import REGISTRY, MethodRegistry
from marivo.analysis.methods.semantics import MethodKey, key_for_parameters
from marivo.analysis.refs import ArtifactRef
from marivo.refs import DatasourceKind, EntityKind, MetricKind, Ref, SemanticKind
from marivo.semantic.ir import TargetSnapshotVersion, TargetValidityVersion


def _fail(expected: str, received: str) -> None:
    reject(expected, received, "Rebuild the exact bound definition graph.", "analysis.graph")


def _identifier(value: str) -> None:
    if type(value) is not str or not value:
        _fail("a nonempty definition or node identity", repr(value))


def _digest(value: object) -> str:
    # Only closed immutable definition values enter this fingerprint, never nodes.
    return sha256(repr(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """Captured Semantic identity and declared shape, not observed physical facts."""

    ref: Ref[EntityKind] | Ref[MetricKind]
    fingerprint: str
    datasource: Ref[DatasourceKind]
    shape: SourceShape
    version: TargetSnapshotVersion | TargetValidityVersion | None = None

    def __post_init__(self) -> None:
        _identifier(self.fingerprint)
        if (
            type(self.ref) is not Ref
            or self.ref.kind not in (SemanticKind.ENTITY, SemanticKind.METRIC)
            or type(self.datasource) is not Ref
            or self.datasource.kind is not SemanticKind.DATASOURCE
            or type(self.shape) is not SourceShape
        ):
            _fail("exact Semantic and datasource Refs and source shape", repr(self))


@dataclass(frozen=True, slots=True, eq=False)
class SourceLeaf:
    definition: SourceDefinition
    signature: Signature
    value_type: ValueType
    identity: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _identifier(self.identity)
        if type(self.definition) is not SourceDefinition or type(self.signature) is not Signature:
            _fail("a typed source definition and signature", self.identity)
        if any(item.basis not in ("declaration", "builder") for item in self.signature.evidence):
            _fail(
                "a live source signature with only static declaration or builder evidence",
                self.identity,
            )
        refs = tuple(item.entity_ref for item in self.signature.domain.instance_key)
        quantity = self.signature.quantity
        if self.definition.ref.kind is SemanticKind.ENTITY:
            if self.definition.ref not in refs or quantity is not None:
                _fail("an Entity domain bound to its source Ref", self.identity)
        else:
            from marivo.analysis.core.model import ObservedQuantity

            if (
                not isinstance(quantity, ObservedQuantity)
                or quantity.metric_ref != self.definition.ref
                or quantity.graph_fingerprint != self.definition.fingerprint
            ):
                _fail("an observed Metric with its canonical graph fingerprint", self.identity)
        _value_type(self.value_type)

    @property
    def fingerprint(self) -> str:
        return _digest((self.definition, self.signature, self.value_type))


@dataclass(frozen=True, slots=True, eq=False)
class FixedLeaf:
    """A captured Artifact contract; origin history deliberately is not an input."""

    artifact: ArtifactRef
    definition_fingerprint: str
    signature: Signature
    value_type: ValueType
    shape: FixedShape
    identity: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _identifier(self.identity)
        _identifier(self.definition_fingerprint)
        if (
            type(self.artifact) is not ArtifactRef
            or type(self.signature) is not Signature
            or type(self.shape) is not FixedShape
        ):
            _fail("a fixed Artifact ref, signature and time shape", self.identity)
        _value_type(self.value_type)

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                self.artifact.ref,
                self.definition_fingerprint,
                self.signature,
                self.value_type,
                self.shape,
            )
        )


EdgeRole: TypeAlias = Literal["subject", "quantity", "current", "baseline", "reference"]


@dataclass(frozen=True, slots=True)
class Edge:
    role: EdgeRole
    node: Node

    def __post_init__(self) -> None:
        if self.role not in ("subject", "quantity", "current", "baseline", "reference"):
            _fail("a closed data dependency role", str(self.role))
        if type(self.node) not in (SourceLeaf, FixedLeaf, MethodNode):
            _fail("a typed definition node", type(self.node).__name__)


@dataclass(frozen=True, slots=True, eq=False)
class MethodNode:
    method: MethodKey
    parameters: RuleParameters
    inputs: tuple[Edge, ...]
    derivation: RuleDerivation
    value_type: ValueType
    sources: tuple[SourceLeaf, ...] = ()
    identity: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _validate_method(self, REGISTRY)

    @property
    def signature(self) -> Signature:
        return self.derivation.output

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                self.method,
                self.parameters,
                tuple((e.role, e.node.fingerprint) for e in self.inputs),
                self.derivation,
                self.value_type,
                tuple(s.fingerprint for s in self.sources),
            )
        )


Node: TypeAlias = SourceLeaf | FixedLeaf | MethodNode


def _value_type(value: ValueType) -> None:
    from marivo.analysis.methods.physical import DecimalType, ScalarType

    if type(value) not in (ScalarType, DecimalType):
        _fail("a precise physical value type", repr(value))


def _validate_method(node: MethodNode, registry: MethodRegistry) -> None:
    _identifier(node.identity)
    _value_type(node.value_type)
    if (
        type(node.inputs) is not tuple
        or not node.inputs
        or any(type(e) is not Edge for e in node.inputs)
    ):
        _fail("ordered immutable data dependencies", node.identity)
    if isinstance(node.parameters, PartsTransport):
        physical = node.inputs[0].node.value_type
        for predicate in node.parameters.predicates:
            value = predicate.literal
            if not isinstance(physical, ScalarType) or not (
                (physical.name == "string" and type(value) is str and predicate.operator == "eq")
                or (physical.name == "int64" and type(value) is int)
                or (physical.name == "boolean" and type(value) is bool)
                or (physical.name == "date" and type(value) is date)
                or (physical.name == "timestamp" and type(value) is datetime)
                or (
                    physical.name == "float64"
                    and (
                        type(value) is float or (type(value) is int and -(2**53) <= value <= 2**53)
                    )
                )
            ):
                predicate_expected = (
                    physical.name if isinstance(physical, ScalarType) else "precise scalar"
                )
                repair = (
                    "Use a date literal for this civil-date value."
                    if predicate_expected == "date"
                    else "Use an aware datetime literal for this timestamp value."
                    if predicate_expected == "timestamp"
                    else f"Use a lossless {predicate_expected} literal for this relation.value predicate."
                )
                reject(
                    f"a lossless {predicate_expected} predicate literal",
                    f"{predicate.operator} {value!r} ({type(value).__name__})",
                    repair,
                    "analysis.graph.predicate",
                )
    if node.method != key_for_parameters(node.parameters):
        _fail("the parameter variant's exact method version", str(node.method))
    roles = tuple(e.role for e in node.inputs)
    if isinstance(node.parameters, CellDerive):
        expected: tuple[str, ...] = ("current", "baseline")
    else:
        expected = tuple(
            "subject" if e.node.signature.quantity is None else "quantity" for e in node.inputs
        )
    if roles != expected:
        _fail(f"ordered input roles {expected}", repr(roles))
    if type(node.sources) is not tuple or any(type(s) is not SourceLeaf for s in node.sources):
        _fail("immutable explicit source dependencies", node.identity)
    if isinstance(node.parameters, (ObserveMetric, ObserveCount)):
        required = {node.parameters.contribution.path}
        for path in node.parameters.path:
            required.update((path.from_entity_ref.path, path.to_entity_ref.path))
        actual = {source.definition.ref.path for source in node.sources}
        if actual != required or len(actual) != len(node.sources):
            _fail(f"explicit observation sources for {sorted(required)}", repr(sorted(actual)))
    elif isinstance(node.parameters, BindProject):
        params = node.parameters
        required = {params.field_owner.path}
        for path in params.path_contracts:
            required.update((path.from_entity_ref.path, path.to_entity_ref.path))
        if params.metric_contract is not None:
            required.update(ref.path for ref in params.metric_contract.computation_roots)
        actual = {
            source.definition.ref.path
            for source in node.sources
            if source.definition.ref.kind is SemanticKind.ENTITY
        }
        if actual != required or len(actual) != len(node.sources):
            _fail(f"explicit source bindings for {sorted(required)}", repr(sorted(actual)))
    elif node.sources:
        _fail("source dependencies only for source-binding methods", node.identity)
    signatures = tuple(e.node.signature for e in node.inputs)
    owners = {(s.domain.binding.session_id, s.domain.binding.owner_id) for s in signatures}
    if len(owners) != 1:
        _fail("one Session and owner", repr(owners))
    binding = signatures[0].domain.binding
    if any(
        (
            source.signature.domain.binding.session_id,
            source.signature.domain.binding.owner_id,
            source.signature.domain.binding.scope_id,
        )
        != (binding.session_id, binding.owner_id, binding.scope_id)
        for source in node.sources
    ):
        _fail("source dependencies in the exact Session, owner and scope", node.identity)
    registry.lookup(node.method).semantics.validate_output_type(
        tuple(edge.node.value_type for edge in node.inputs), node.value_type, node.parameters
    )
    if node.derivation != registry.derive(signatures, node.parameters):
        _fail("the registered semantic derivation without promoted Post", node.identity)


def topology(root: Node, *, registry: MethodRegistry = REGISTRY) -> tuple[Node, ...]:
    """Validate the reachable DAG and retain explicit sharing in dependency order."""
    ordered: list[Node] = []
    identities: dict[str, Node] = {}
    active: set[str] = set()
    done: set[str] = set()

    def visit(node: Node) -> None:
        if type(node) not in (SourceLeaf, FixedLeaf, MethodNode):
            _fail("a typed graph root", type(node).__name__)
        _identifier(node.identity)
        previous = identities.setdefault(node.identity, node)
        if previous is not node:
            _fail("one object per explicit node identity", node.identity)
        if node.identity in active:
            _fail("an acyclic definition graph", node.identity)
        if node.identity in done:
            return
        active.add(node.identity)
        if isinstance(node, MethodNode):
            if type(node.inputs) is not tuple or any(type(e) is not Edge for e in node.inputs):
                _fail("immutable typed edges", node.identity)
            for edge in node.inputs:
                visit(edge.node)
            for source in node.sources:
                visit(source)
            _validate_method(node, registry)
        else:
            node.__post_init__()
        active.remove(node.identity)
        done.add(node.identity)
        ordered.append(node)

    visit(root)
    owners = {
        (n.signature.domain.binding.session_id, n.signature.domain.binding.owner_id)
        for n in ordered
    }
    if len(owners) != 1:
        _fail("one graph Session and owner", repr(owners))
    return tuple(ordered)


def method_node(
    inputs: tuple[Edge, ...],
    parameters: RuleParameters,
    *,
    value_type: ValueType,
    registry: MethodRegistry = REGISTRY,
    sources: tuple[SourceLeaf, ...] = (),
) -> MethodNode:
    """Construct through the sole semantic owner; execution qualification is separate."""
    for edge in inputs:
        topology(edge.node, registry=registry)
    node = MethodNode(
        key_for_parameters(parameters),
        parameters,
        inputs,
        registry.derive(tuple(e.node.signature for e in inputs), parameters),
        value_type,
        sources,
    )
    topology(node, registry=registry)
    return node
