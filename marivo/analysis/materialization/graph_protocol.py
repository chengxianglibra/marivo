"""Closed, canonical v7 metadata; no legacy Artifact or journey codecs."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Annotated, Literal, TypeVar, get_args

import pyarrow as pa
from pydantic import BeforeValidator, PlainSerializer, TypeAdapter, ValidationError

from marivo.analysis.compiler.graph_plan import CheckRequirement, GraphPlan, RouteChoice
from marivo.analysis.compiler.graph_plan import plan as make_plan
from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf, topology
from marivo.analysis.core.model import Evidence, PartRole, Signature, part_role
from marivo.analysis.core.rules import PartsTransport
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    canonical_json,
    decode_receipt,
    receipt_payload,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execution_key import FixedPartKey, _ordered_plan
from marivo.analysis.methods.physical import Qualified
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import MethodKey, MethodName, PersistentStateKind

T = TypeVar("T")
PhysicalReceipt = Annotated[
    LocalReceipt, BeforeValidator(decode_receipt), PlainSerializer(receipt_payload)
]


def invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="one complete canonical v7 graph Artifact with exact bindings",
        received=received,
        repair="Preserve existing state; use a fresh project for v7 or restore the exact committed files and metadata.",
        stage="graph_protocol",
        help_target="session.artifact",
    )


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def encode(value: T, adapter: TypeAdapter[T]) -> str:
    return canonical_json(json.loads(adapter.dump_json(value, warnings="error")))


def decode(text: str, adapter: TypeAdapter[T]) -> T:
    try:
        value = adapter.validate_json(text, strict=True)
        # Also rejects ignored extra fields, absent defaults, coercions, duplicate
        # keys and ambiguous union variants in nested pre-existing dataclasses.
        if encode(value, adapter) != text:
            raise invalid("noncanonical, missing or extra metadata fields")
        return value
    except (ValidationError, ValueError, TypeError) as error:
        raise invalid(f"invalid closed metadata: {type(error).__name__}") from error


def schema_text(schema: pa.Schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode("ascii")


def schema_from(text: str) -> pa.Schema:
    try:
        schema = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(text, validate=True)))
        if schema_text(schema) != text:
            raise invalid("noncanonical Arrow schema")
        return schema
    except (ValueError, pa.ArrowException) as error:
        raise invalid("invalid Arrow schema") from error


@dataclass(frozen=True, slots=True)
class SourceRunInput:
    schema: Literal["marivo.analysis.run_input/v1"]
    kind: Literal["source"]
    definition_fingerprint: str
    plan_digest: str
    ordered_source_bindings: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class FixedInputBinding:
    session_ref: str
    artifact_ref: str
    producing_run_ref: str
    primary_receipt_digest: str
    ordered_parts: tuple[FixedPartKey, ...]
    input_binding: str
    method_state_contract_id: str
    method_state_version: int
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class FixedRunInput:
    schema: Literal["marivo.analysis.run_input/v1"]
    kind: Literal["fixed"]
    definition_fingerprint: str
    plan_digest: str
    ordered_artifact_inputs: tuple[FixedInputBinding, ...]


RunInput = SourceRunInput | FixedRunInput
RUN_INPUT: TypeAdapter[RunInput] = TypeAdapter(RunInput)


@dataclass(frozen=True, slots=True)
class PrimaryReceipt:
    schema: Literal["marivo.analysis.receipt/v1"]
    kind: Literal["primary"]
    input_binding: str
    key_fields: tuple[tuple[str, str], ...]
    local: PhysicalReceipt


@dataclass(frozen=True, slots=True)
class PartReceipt:
    schema: Literal["marivo.analysis.receipt/v1"]
    kind: Literal["part"]
    input_binding: str
    key_fields: tuple[tuple[str, str], ...]
    local: PhysicalReceipt
    role: str
    contract_id: str
    contract_version: Literal[1]
    method_state_version: Literal[1]


RECEIPT: TypeAdapter[PrimaryReceipt | PartReceipt] = TypeAdapter(PrimaryReceipt | PartReceipt)


@dataclass(frozen=True, slots=True)
class MethodState:
    schema: Literal["marivo.analysis.method_state/v1"]
    kind: PersistentStateKind
    contract_id: str
    contract_version: Literal[1]
    method_name: MethodName
    method_version: Literal[1]
    input_binding: str
    ordered_part_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        required = (
            ()
            if self.kind == "none"
            else ("pair_counts",)
            if self.kind == "spearman"
            else ("current_endpoint", "baseline_endpoint")
            if self.kind == "difference"
            else ("original_state", "coverage")
            if self.kind
            in ("original_sum", "original_sum_zero", "original_count", "original_ratio")
            else ("row_state",)
        )
        if (
            self.contract_id != f"marivo.analysis.state.{self.kind}"
            or len(set(self.ordered_part_roles)) != len(self.ordered_part_roles)
            or any(role not in get_args(PartRole) for role in self.ordered_part_roles)
            or not set(required).issubset(self.ordered_part_roles)
            or not self.input_binding
            or REGISTRY.lookup(MethodKey(self.method_name)).semantics.persistent_state_kind
            != self.kind
        ):
            raise invalid("method state, method or required roles differ")


STATE = TypeAdapter(MethodState)
NODE: TypeAdapter[Node] = TypeAdapter(Node)
SIGNATURE = TypeAdapter(Signature)
CHECK = TypeAdapter(CheckRequirement)


def thaw_graph(text: str) -> Node:
    root = decode(text, NODE)
    nodes: dict[str, Node] = {}
    bodies: dict[str, str] = {}

    def intern(node: Node) -> Node:
        body = encode(node, NODE)
        if node.identity in nodes:
            if bodies[node.identity] != body:
                raise invalid("one node identity has different frozen definitions")
            return nodes[node.identity]
        if isinstance(node, MethodNode):
            sources = tuple(intern(source) for source in node.sources)
            if any(not isinstance(source, SourceLeaf) for source in sources):
                raise invalid("invalid frozen source edge")
            node = replace(
                node,
                inputs=tuple(replace(e, node=intern(e.node)) for e in node.inputs),
                sources=tuple(s for s in sources if isinstance(s, SourceLeaf)),
            )
        nodes[node.identity] = node
        bodies[node.identity] = body
        return node

    root = intern(root)
    topology(root)
    return root


@dataclass(frozen=True, slots=True)
class Continuation:
    schema: Literal["marivo.analysis.continuation/v1"]
    root: str
    entity_facts: tuple[str, ...]
    dimension_facts: tuple[str, ...]
    semantic_versions: tuple[tuple[str, str], ...]
    method_versions: tuple[MethodKey, ...]
    input_binding: str
    primary_receipt_digest: str
    part_receipt_digests: tuple[str, ...]
    method_state_digest: str


SNAPSHOT = TypeAdapter(Continuation)


@dataclass(frozen=True, slots=True)
class CompletedEvidence:
    origin_node: str
    check_id: str
    scope: str
    ordered_input_occurrences: tuple[str, ...]
    deadline: Literal["consume", "publish"]
    status: Literal["completed"]
    producing_run_ref: str
    result_digest: str


@dataclass(frozen=True, slots=True)
class RowContract:
    key_fields: tuple[str, ...]
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class RowSetContract:
    kind: Literal["keyed", "singleton", "optional_singleton"]
    ordering: Literal["unordered"]


@dataclass(frozen=True, slots=True)
class MethodBinding:
    method: MethodKey
    implementation_id: str
    implementation_version: int


@dataclass(frozen=True, slots=True)
class Descriptor:
    schema: Literal["marivo.analysis.artifact_descriptor/v1"]
    definition_fingerprint: str
    producing_run_ref: str
    execution_key_digest: str
    signature: Signature
    row_contract: RowContract
    row_set_contract: RowSetContract
    realized_schema: str
    semantic_dependency_digest: str
    method_bindings: tuple[MethodBinding, ...]
    completed_checks: tuple[CompletedEvidence, ...]
    primary_receipt: PrimaryReceipt
    parts: tuple[PartReceipt, ...]
    method_state: MethodState
    continuation_snapshot: str
    continuation_snapshot_digest: str


def fixed_signature(value: Descriptor) -> Signature:
    """Expose a checked Artifact's semantic signature to a fixed-only graph."""
    root = validate_descriptor(value)
    admitted = descriptor_plan(value, root)
    proven = tuple(
        Evidence(
            check.obligation.fact,
            "check",
            digest(actual.producing_run_ref + actual.result_digest + actual.origin_node),
        )
        for check, actual in zip(admitted.checks, value.completed_checks, strict=True)
    )
    return replace(
        value.signature,
        obligations=(),
        evidence=tuple(dict.fromkeys((*value.signature.evidence, *proven))),
    )


DESCRIPTOR = TypeAdapter(Descriptor)


def plan_digest(plan: GraphPlan) -> str:
    return digest(canonical_json(_ordered_plan(plan)))


def receipt_digest(value: PrimaryReceipt | PartReceipt) -> str:
    return digest(encode(value, RECEIPT))


def evidence_identity(check: CheckRequirement, run_ref: str, result: str) -> CompletedEvidence:
    fact = check.obligation.fact
    return CompletedEvidence(
        check.node_id,
        check.obligation.check_id,
        fact.binding.scope_id,
        tuple(
            digest(encode(Signature(item.domain, item.quantity), SIGNATURE)) for item in fact.inputs
        ),
        check.obligation.before,
        "completed",
        run_ref,
        result,
    )


def descriptor_plan(value: Descriptor, root: Node) -> GraphPlan:
    nodes = tuple(n for n in topology(root) if isinstance(n, MethodNode))
    if len(nodes) != len(value.method_bindings):
        raise invalid("method implementation inventory differs from frozen graph")
    routes: list[RouteChoice] = []
    for node, binding in zip(nodes, value.method_bindings, strict=True):
        choices = {
            impl.key.route
            for impl in REGISTRY.lookup(node.method).implementations
            if isinstance(impl.qualification, Qualified)
            and impl.qualification.implementation_id == binding.implementation_id
            and impl.contract_version == binding.implementation_version
            and node.method == binding.method
        }
        if len(choices) != 1:
            raise invalid("unknown or ambiguous frozen implementation/version")
        routes.append(RouteChoice(node.identity, next(iter(choices))))
    admitted = make_plan(root, routes=tuple(routes))
    for physical, binding in zip(
        admitted.physical_requirements, value.method_bindings, strict=True
    ):
        qualification = physical.implementation.qualification
        if (
            not isinstance(qualification, Qualified)
            or qualification.implementation_id != binding.implementation_id
            or physical.implementation.contract_version != binding.implementation_version
        ):
            raise invalid("frozen physical implementation differs")
    if len(admitted.checks) != len(value.completed_checks):
        raise invalid("missing or duplicate durable check evidence")
    expected = tuple(
        evidence_identity(check, value.producing_run_ref, actual.result_digest)
        for check, actual in zip(admitted.checks, value.completed_checks, strict=True)
    )
    if expected != value.completed_checks:
        raise invalid("completed check origin, scope or ordered inputs differ")
    return admitted


def validate_descriptor(value: Descriptor) -> Node:
    if len(value.continuation_snapshot.encode("utf-8")) > 262144:
        raise invalid("frozen continuation exceeds the 256 KiB metadata budget")
    snapshot = decode(value.continuation_snapshot, SNAPSHOT)
    root = thaw_graph(snapshot.root)
    state = value.method_state
    schema = schema_from(value.realized_schema)
    keys = tuple((name, str(schema.field(name).type)) for name in value.row_contract.key_fields)
    if (
        not isinstance(root, MethodNode)
        or value.definition_fingerprint != root.fingerprint
        or value.signature != root.signature
        or not value.method_bindings
        or value.method_bindings[-1].method != root.method
        or state.method_name != root.method.name
        or value.continuation_snapshot_digest != digest(value.continuation_snapshot)
        or snapshot.input_binding != state.input_binding
        or snapshot.primary_receipt_digest != receipt_digest(value.primary_receipt)
        or snapshot.part_receipt_digests != tuple(receipt_digest(p) for p in value.parts)
        or snapshot.method_state_digest != digest(encode(state, STATE))
        or value.primary_receipt.input_binding != state.input_binding
        or value.primary_receipt.key_fields != keys
        or tuple(p.role for p in value.parts) != state.ordered_part_roles
        or state.ordered_part_roles != tuple(part_role(p) for p in value.signature.parts)
        or value.primary_receipt.local.schema_fingerprint
        != hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
        or value.row_set_contract.kind
        != (
            "keyed"
            if keys
            else "optional_singleton"
            if isinstance(root.parameters, PartsTransport) and root.parameters.mode == "where"
            else "singleton"
        )
        or any(
            p.input_binding != state.input_binding
            or p.key_fields != keys
            or p.contract_id != f"marivo.analysis.part.{state.kind}.{p.role}"
            for p in value.parts
        )
        or any(
            c.producing_run_ref != value.producing_run_ref or len(c.result_digest) != 64
            for c in value.completed_checks
        )
    ):
        raise invalid("descriptor, snapshot, state or receipt authority differs")
    nodes = topology(root)
    if (
        snapshot.entity_facts
        != tuple(
            dict.fromkeys(c.entity_ref.path for n in nodes for c in n.signature.domain.instance_key)
        )
        or snapshot.dimension_facts
        != tuple(dict.fromkeys(c.field for n in nodes for c in n.signature.domain.instance_key))
        or snapshot.semantic_versions
        != tuple(
            (n.definition.ref.path, n.definition.fingerprint)
            for n in nodes
            if isinstance(n, SourceLeaf)
        )
        or snapshot.method_versions != tuple(n.method for n in nodes if isinstance(n, MethodNode))
        or value.semantic_dependency_digest != digest(canonical_json(snapshot.semantic_versions))
    ):
        raise invalid("frozen facts or semantic dependency digest differ")
    descriptor_plan(value, root)
    return root
