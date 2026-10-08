"""Private immutable definition graph; no execution or historical lineage traversal."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field, fields, is_dataclass, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, TypeAlias
from uuid import uuid4

from marivo.analysis.core.model import Signature, reject
from marivo.analysis.core.rules import (
    AnchorObserve,
    AttributionDerive,
    BindProject,
    CellDerive,
    DisplayRank,
    DisplayTable,
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelRead,
    HistoryAxesPrepare,
    MapCorrespond,
    ObserveCount,
    ObserveMetric,
    ObserveWeightedMean,
    OccurrencePrepare,
    OriginalReduce,
    PartsTransport,
    PreparedObservation,
    ReferenceDerive,
    RowState,
    RuleDerivation,
    RuleParameters,
    TimeProduct,
    TimeRuns,
)
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType, SourceShape, ValueType
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
        self.definition.__post_init__()
        object.__setattr__(
            self,
            "signature",
            replace(self.signature, node_id=self.identity, key_domain_id=self.identity),
        )
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
        object.__setattr__(
            self,
            "signature",
            replace(self.signature, node_id=self.identity, key_domain_id=self.artifact.ref),
        )

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
    derivation: RuleDerivation = field(init=False)
    value_type: ValueType
    sources: tuple[SourceLeaf, ...] = ()
    identity: str = field(default_factory=lambda: uuid4().hex)
    retained_endpoints: tuple[MethodNode, ...] = ()

    registry: InitVar[MethodRegistry] = field(default=REGISTRY, kw_only=True)

    def __post_init__(self, registry: MethodRegistry) -> None:
        _validate_method(self, registry, constructing=True)

    @property
    def signature(self) -> Signature:
        return self.derivation.output

    @property
    def fingerprint(self) -> str:
        return definition_fingerprints(self)[self.identity]


def definition_fingerprints(root: Node) -> dict[str, str]:
    """Hash each captured definition once per call, never cache across executions."""
    return _fingerprints(_ordered_nodes(root, retained=True))


def _fingerprints(nodes: tuple[Node, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, MethodNode):
            result[node.identity] = _digest(
                (
                    node.method,
                    node.parameters,
                    tuple((edge.role, result[edge.node.identity]) for edge in node.inputs),
                    node.derivation,
                    node.value_type,
                    tuple(result[source.identity] for source in node.sources),
                    tuple(result[endpoint.identity] for endpoint in node.retained_endpoints),
                )
            )
        else:
            result[node.identity] = node.fingerprint
    return result


Node: TypeAlias = SourceLeaf | FixedLeaf | MethodNode


def _value_type(value: ValueType) -> None:
    from marivo.analysis.methods.physical import DecimalType, DurationType

    if type(value) not in (ScalarType, DecimalType, DurationType):
        _fail("a precise physical value type", repr(value))


def retained_nodes(root: Node) -> tuple[Node, ...]:
    """Traverse definition evidence as well as data edges, without source admission."""
    ordered: list[Node] = []
    seen: set[int] = set()

    def visit(node: Node) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, MethodNode):
            for child in (
                *tuple(edge.node for edge in node.inputs),
                *node.sources,
                *node.retained_endpoints,
            ):
                visit(child)
        ordered.append(node)

    visit(root)
    return tuple(ordered)


def retained_inclusion(receiver: Node, dependency: Node) -> bool:
    """Prove an ancestor or total field/observation map on a retained ancestor."""
    topology(receiver)
    return _retained_inclusion(receiver, dependency)


def _retained_inclusion(receiver: Node, dependency: Node) -> bool:
    nodes = retained_nodes(receiver)

    def captured(candidate: Node) -> bool:
        return any(
            node.identity == candidate.identity
            or (
                isinstance(node, FixedLeaf)
                and isinstance(candidate, FixedLeaf)
                and node.artifact == candidate.artifact
            )
            for node in nodes
        )

    def captured_domain(candidate: Node) -> bool:
        if captured(candidate):
            return True
        if isinstance(candidate, SourceLeaf):
            return any(
                isinstance(node, SourceLeaf)
                and node.signature.domain == candidate.signature.domain
                and node.definition.ref == candidate.definition.ref
                and node.definition.datasource == candidate.definition.datasource
                and node.definition.fingerprint == candidate.definition.fingerprint
                and replace(node.definition.shape, time=NoTime())
                == replace(candidate.definition.shape, time=NoTime())
                for node in nodes
            )
        if isinstance(candidate, MethodNode) and isinstance(candidate.parameters, TimeProduct):
            return any(
                isinstance(node, MethodNode)
                and node.parameters == candidate.parameters
                and node.signature.domain == candidate.signature.domain
                for node in nodes
            ) and captured_domain(candidate.inputs[0].node)
        return (
            isinstance(candidate, MethodNode)
            and (
                (
                    isinstance(candidate.parameters, MapCorrespond)
                    and candidate.parameters.mode == "subjects"
                )
                or (
                    isinstance(candidate.parameters, PartsTransport)
                    and candidate.parameters.mode == "view"
                    and not candidate.parameters.predicates
                )
            )
            and len(candidate.inputs) == 1
            and candidate.signature.domain.instance_key
            == candidate.inputs[0].node.signature.domain.instance_key
            and captured_domain(candidate.inputs[0].node)
        )

    if captured(dependency):
        return True
    if isinstance(dependency, MethodNode) and isinstance(
        dependency.parameters, (BindProject, ObserveMetric, ObserveCount, ObserveWeightedMean)
    ):
        domain = dependency.inputs[0].node.signature.domain
        return domain.instance_key == dependency.signature.domain.instance_key and captured_domain(
            dependency.inputs[0].node
        )
    return False


def _validate_method(
    node: MethodNode,
    registry: MethodRegistry,
    *,
    constructing: bool = False,
    fingerprints: Mapping[str, str] | None = None,
) -> None:
    _identifier(node.identity)
    _value_type(node.value_type)
    if (
        type(node.inputs) is not tuple
        or not node.inputs
        or any(type(e) is not Edge for e in node.inputs)
    ):
        _fail("ordered immutable data dependencies", node.identity)
    direct: dict[str, Node] = {node.identity: node}
    for edge in node.inputs:
        edge.__post_init__()
        prior = direct.setdefault(edge.node.identity, edge.node)
        if prior is not edge.node:
            _fail("one object per explicit node identity", edge.node.identity)
    if constructing:
        object.__setattr__(
            node,
            "derivation",
            registry.derive(
                tuple(edge.node.signature for edge in node.inputs),
                node.parameters,
                output_node_id=node.identity,
            ),
        )
    if isinstance(node.parameters, (PartsTransport, TimeRuns)):
        from marivo.analysis.core.predicates import leaves
        from marivo.analysis.methods.predicates import validate_operand

        for tree in (
            (node.parameters.predicate,)
            if isinstance(node.parameters, TimeRuns)
            else node.parameters.predicates
        ):
            for predicate in leaves(tree):
                if predicate.binding != node.inputs[0].node.signature.domain.binding:
                    _fail("predicate binding in the receiver scope", repr(predicate.binding))
                if not 0 <= predicate.input_index < len(node.inputs) or (
                    predicate.right_index is not None
                    and not 0 <= predicate.right_index < len(node.inputs)
                ):
                    _fail("predicate references within ordered inputs", repr(predicate))
                left = node.inputs[predicate.input_index].node
                right = (
                    None
                    if predicate.right_index is None
                    else node.inputs[predicate.right_index].node
                )
                validate_operand(
                    predicate, left.value_type, None if right is None else right.value_type
                )
                if right is not None:
                    lq, rq = left.signature.quantity, right.signature.quantity
                    if (None if lq is None else lq.unit) != (None if rq is None else rq.unit):
                        _fail("compatible predicate units", repr((lq, rq)))
        for index in (
            () if isinstance(node.parameters, TimeRuns) else node.parameters.inclusion_inputs
        ):
            if type(index) is not int or not 1 <= index < len(node.inputs):
                _fail("a valid retained inclusion input", repr(index))
            input_node = node.inputs[index].node
            if isinstance(input_node, FixedLeaf) and node.retained_endpoints:
                input_node = node.retained_endpoints[index]
            receiver = (
                node.retained_endpoints[0] if node.retained_endpoints else node.inputs[0].node
            )
            if not _retained_inclusion(receiver, input_node):
                _fail("a retained ancestor inclusion", input_node.identity)
    if node.method != key_for_parameters(node.parameters):
        _fail("the parameter variant's exact method version", str(node.method))
    roles = tuple(e.role for e in node.inputs)
    if isinstance(node.parameters, (CellDerive, FunnelCompare)):
        expected: tuple[str, ...] = ("current", "baseline")
    elif isinstance(node.parameters, ReferenceDerive):
        expected = tuple(
            "reference"
            if index == 1
            else "subject"
            if edge.node.signature.quantity is None
            else "quantity"
            for index, edge in enumerate(node.inputs)
        )
    else:
        expected = tuple(
            "subject" if e.node.signature.quantity is None else "quantity" for e in node.inputs
        )
    if roles != expected:
        _fail(f"ordered input roles {expected}", repr(roles))
    if type(node.sources) is not tuple or any(type(s) is not SourceLeaf for s in node.sources):
        _fail("immutable explicit source dependencies", node.identity)
    if isinstance(node.parameters, OccurrencePrepare):
        required_ids = {event.source_id for event in node.parameters.events}
        if not required_ids <= {source.identity for source in node.sources} and not isinstance(
            node.inputs[0].node, FixedLeaf
        ):
            _fail("explicit captured Event source dependencies", node.identity)
        if not isinstance(node.inputs[0].node, FixedLeaf):
            required = {event.source.ref.path for event in node.parameters.events}
            required.update(event.subject.ref.path for event in node.parameters.events)
            required.update(
                hop.to_entity_ref.path for event in node.parameters.events for hop in event.path
            )
            actual = {source.definition.ref.path for source in node.sources}
            if actual != required or len(actual) != len(node.sources):
                _fail(
                    f"all captured participant dependencies for {sorted(required)}",
                    repr(sorted(actual)),
                )
    elif isinstance(node.parameters, HistoryAxesPrepare):
        if {s.identity for s in node.sources} != {
            identity for axis in node.parameters.request.axes for identity in axis.source_ids
        }:
            _fail("all exact checkpoint-axis path dependencies", node.identity)
    elif isinstance(node.parameters, FunnelAxesPrepare):
        if {s.identity for s in node.sources} != {
            identity for axis in node.parameters.axes for identity in axis.source_ids
        }:
            _fail("all exact entry-axis path dependencies", node.identity)
    elif isinstance(node.parameters, PreparedObservation):
        if len(node.inputs) != 2:
            _fail("selected and original population dependencies", node.identity)
        observation = node.parameters.observation
        required = {observation.contribution.path, observation.event.entity_ref.path}
        for path in observation.path:
            required.update((path.from_entity_ref.path, path.to_entity_ref.path))
        required.update(field.entity_ref.path for field in observation.coordinates)
        actual = {source.definition.ref.path for source in node.sources}
        if actual != required or len(actual) != len(node.sources):
            _fail(f"explicit preparation dependencies for {sorted(required)}", repr(sorted(actual)))
    elif isinstance(node.parameters, (ObserveMetric, ObserveCount, ObserveWeightedMean)):
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
    elif isinstance(node.parameters, AnchorObserve):
        required = {observation.contribution.path for observation in node.parameters.observations}
        for observation in node.parameters.observations:
            for hop in observation.path:
                required.update((hop.from_entity_ref.path, hop.to_entity_ref.path))
        actual = {source.definition.ref.path for source in node.sources}
        if not required <= actual:
            _fail("all frozen relative Metric sources", repr(sorted(actual)))
    elif node.sources:
        _fail("source dependencies only for source-binding methods", node.identity)
    if type(node.retained_endpoints) is not tuple or any(
        type(endpoint) is not MethodNode for endpoint in node.retained_endpoints
    ):
        _fail("immutable typed retained endpoint definitions", node.identity)
    if node.retained_endpoints:
        if not isinstance(
            node.parameters,
            (
                FunnelCompare,
                FunnelRead,
                FunnelAxesPrepare,
                HistoryAxesPrepare,
                FunnelAttribute,
                AttributionDerive,
                CellDerive,
                TimeRuns,
                PartsTransport,
                OriginalReduce,
                RowState,
                ReferenceDerive,
                DisplayRank,
                DisplayTable,
            ),
        ):
            _fail("retained endpoints only for comparison or state transport", node.identity)
        if len(node.retained_endpoints) != len(node.inputs):
            _fail("one retained endpoint per ordered input", node.identity)
        for endpoint, edge in zip(node.retained_endpoints, node.inputs, strict=True):
            if endpoint.signature.domain.binding != edge.node.signature.domain.binding:
                _fail("retained endpoints with exact input ownership and scope", node.identity)
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
    if not constructing and node.derivation != registry.derive(
        signatures, node.parameters, output_node_id=node.identity
    ):
        _fail("the registered semantic derivation without promoted Post", node.identity)
    if node.retained_endpoints and not constructing:
        _validate_endpoints(
            node, definition_fingerprints(node) if fingerprints is None else fingerprints
        )


def _validate_endpoints(node: MethodNode, fingerprints: Mapping[str, str]) -> None:
    if not node.retained_endpoints:
        return
    for endpoint, edge in zip(node.retained_endpoints, node.inputs, strict=True):
        expected = (
            edge.node.definition_fingerprint
            if isinstance(edge.node, FixedLeaf)
            else fingerprints[edge.node.identity]
        )
        if fingerprints[endpoint.identity] != expected:
            _fail("a retained endpoint matching its exact input definition", node.identity)


def _ordered_nodes(root: Node, *, retained: bool) -> tuple[Node, ...]:
    ordered: list[Node] = []
    identities: dict[str, Node] = {}
    active: set[str] = set()
    done: set[int] = set()

    def visit(node: Node) -> None:
        if type(node) not in (SourceLeaf, FixedLeaf, MethodNode):
            _fail("a typed graph root", type(node).__name__)
        _identifier(node.identity)
        previous = identities.setdefault(node.identity, node)
        if previous is not node and (
            not retained
            or _node_digest(previous, capture_ids=True) != _node_digest(node, capture_ids=True)
        ):
            _fail("one object per explicit node identity", node.identity)
        if node.identity in active:
            _fail("an acyclic definition graph", node.identity)
        if id(node) in done:
            return
        active.add(node.identity)
        if isinstance(node, MethodNode):
            if type(node.derivation) is not RuleDerivation:
                _fail("a typed registered semantic derivation", node.identity)
            if type(node.inputs) is not tuple or any(type(e) is not Edge for e in node.inputs):
                _fail("immutable typed edges", node.identity)
            if type(node.sources) is not tuple or any(
                type(s) is not SourceLeaf for s in node.sources
            ):
                _fail("immutable explicit source dependencies", node.identity)
            if type(node.retained_endpoints) is not tuple or any(
                type(endpoint) is not MethodNode for endpoint in node.retained_endpoints
            ):
                _fail("immutable typed retained endpoint definitions", node.identity)
            for edge in node.inputs:
                edge.__post_init__()
                visit(edge.node)
            for source in node.sources:
                visit(source)
            if retained:
                for endpoint in node.retained_endpoints:
                    visit(endpoint)
        active.remove(node.identity)
        done.add(id(node))
        ordered.append(node)

    visit(root)
    return tuple(ordered)


def _static_digest(value: object, *, capture_ids: bool = False) -> str:
    """Snapshot exact static values without expanding shared value objects."""
    return _static_value_digest(value, {}, {}, capture_ids).hex()


def _static_value_digest(
    item: object, memo: dict[int, bytes], kinds: dict[type, bytes], capture_ids: bool
) -> bytes:
    previous = memo.get(id(item))
    if previous is not None:
        return previous
    kind = kinds.get(type(item))
    if kind is None:
        kind = f"{type(item).__module__}.{type(item).__qualname__}".encode()
        kinds[type(item)] = kind
    if isinstance(item, (SourceLeaf, FixedLeaf, MethodNode)):
        payload = (b"" if capture_ids else str(id(item)).encode() + b"\0") + _static_value_digest(
            item.identity, memo, kinds, capture_ids
        )
    elif isinstance(item, Ref):
        payload = _static_value_digest(item.kind, memo, kinds, capture_ids) + _static_value_digest(
            item.path, memo, kinds, capture_ids
        )
    elif isinstance(item, tuple):
        payload = len(item).to_bytes(8, "big") + b"".join(
            _static_value_digest(child, memo, kinds, capture_ids) for child in item
        )
    elif is_dataclass(item) and not isinstance(item, type):
        payload = b"".join(
            f.name.encode()
            + b"\0"
            + _static_value_digest(getattr(item, f.name), memo, kinds, capture_ids)
            for f in fields(item)
        )
    else:
        payload = repr(item).encode()
    result = sha256(kind + b"\0" + payload).digest()
    memo[id(item)] = result
    return result


def _node_digest(node: Node, *, capture_ids: bool = False) -> str:
    return _static_digest(
        tuple((f.name, getattr(node, f.name)) for f in fields(node)), capture_ids=capture_ids
    )


@dataclass(frozen=True, slots=True)
class _CapturedGraph:
    """One invocation's validated definitions; never a source or receipt proof."""

    root: Node
    registry: MethodRegistry
    nodes: tuple[Node, ...]
    retained: tuple[Node, ...]
    index: Mapping[str, Node]
    fingerprints: Mapping[str, str]

    def dependencies(self, root: Node) -> tuple[Node, ...]:
        """Read a validated subgraph without repeating semantic checks."""
        if self.index.get(root.identity) is not root:
            _fail("a node in this exact captured closure", root.identity)
        pending = [root]
        reached: set[str] = set()
        while pending:
            node = pending.pop()
            if node.identity in reached:
                continue
            reached.add(node.identity)
            if isinstance(node, MethodNode):
                pending.extend(edge.node for edge in node.inputs)
                pending.extend(node.sources)
        return tuple(node for node in self.nodes if node.identity in reached)


def _validated_closure(
    root: Node, registry: MethodRegistry, *, summaries: bool = False
) -> tuple[tuple[Node, ...], dict[str, str]]:
    retained = _ordered_nodes(root, retained=True)
    for node in retained:
        if not isinstance(node, MethodNode):
            node.__post_init__()
    fingerprints = (
        _fingerprints(retained)
        if summaries
        or any(isinstance(node, MethodNode) and node.retained_endpoints for node in retained)
        else {}
    )
    for node in retained:
        if isinstance(node, MethodNode):
            _validate_method(node, registry, fingerprints=fingerprints)
    owners = {
        (node.signature.domain.binding.session_id, node.signature.domain.binding.owner_id)
        for node in retained
    }
    if len(owners) != 1:
        _fail("one graph Session and owner", repr(owners))
    return retained, fingerprints


def capture_graph(root: Node, *, registry: MethodRegistry = REGISTRY) -> _CapturedGraph:
    """Validate data and retained definitions once for this compiler invocation."""
    retained, fingerprints = _validated_closure(root, registry, summaries=True)
    nodes = _ordered_nodes(root, retained=False)
    index = {node.identity: node for node in retained}
    index.update((node.identity, node) for node in nodes)
    return _CapturedGraph(
        root,
        registry,
        nodes,
        retained,
        MappingProxyType(index),
        MappingProxyType(fingerprints),
    )


def topology(root: Node, *, registry: MethodRegistry = REGISTRY) -> tuple[Node, ...]:
    """Validate the complete definition closure and return execution dependencies."""
    _validated_closure(root, registry)
    return _ordered_nodes(root, retained=False)


def method_node(
    inputs: tuple[Edge, ...],
    parameters: RuleParameters,
    *,
    value_type: ValueType,
    registry: MethodRegistry = REGISTRY,
    sources: tuple[SourceLeaf, ...] = (),
    retained_endpoints: tuple[MethodNode, ...] = (),
) -> MethodNode:
    """Construct through the sole semantic owner; execution qualification is separate."""
    return MethodNode(
        key_for_parameters(parameters),
        parameters,
        inputs,
        value_type,
        sources,
        retained_endpoints=retained_endpoints,
        registry=registry,
    )
