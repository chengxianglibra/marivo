"""Closed, canonical v8 metadata; no legacy Artifact or journey codecs."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Annotated, Literal, TypeVar, get_args

import pyarrow as pa
from pydantic import BeforeValidator, PlainSerializer, TypeAdapter, ValidationError

from marivo.analysis.compiler.graph_plan import CheckRequirement, GraphPlan, RouteChoice
from marivo.analysis.compiler.graph_plan import plan as make_plan
from marivo.analysis.core.cell_encoding import CellTable
from marivo.analysis.core.graph import MethodNode, Node, SourceLeaf, topology
from marivo.analysis.core.model import (
    Evidence,
    Fact,
    OccurrencePart,
    PartRole,
    Signature,
)
from marivo.analysis.core.rules import (
    PreparedObservation,
)
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    canonical_json,
    decode_receipt,
    receipt_payload,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execution_key import (
    FixedPartKey,
    _ordered_plan,
)
from marivo.analysis.materialization.graph_snapshot import (
    GRAPH as GRAPH,
)
from marivo.analysis.materialization.graph_snapshot import (
    GraphDocument,
    MethodRecord,
    Record,
    SourceRecord,
    _order,
    read_graph_document,
)
from marivo.analysis.materialization.graph_snapshot import (
    freeze_graph as freeze_graph,
)
from marivo.analysis.materialization.graph_snapshot import (
    graph_document as graph_document,
)
from marivo.analysis.materialization.graph_snapshot import (
    thaw_graph as thaw_graph,
)
from marivo.analysis.methods.coordinate_state import (
    PartLayout,
    TablePartLayout,
)
from marivo.analysis.methods.coordinate_state import (
    declarations as coordinate_declarations,
)
from marivo.analysis.methods.coordinate_state import (
    key_fields as coordinate_keys,
)
from marivo.analysis.methods.coordinate_state import layout as coordinate_layout
from marivo.analysis.methods.physical import NoTime, Qualified, TimeShape
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.semantics import MethodKey, MethodName, PersistentStateKind

T = TypeVar("T")
PhysicalReceipt = Annotated[
    LocalReceipt, BeforeValidator(decode_receipt), PlainSerializer(receipt_payload)
]


def invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="one complete canonical v8 graph Artifact with exact bindings",
        received=received,
        repair="Preserve existing state and files; re-execute the source analysis for a current Artifact or restore its exact committed metadata.",
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
    except ValidationError as error:
        if adapter in (GRAPH, SNAPSHOT, DESCRIPTOR, STATE, RECEIPT) and any(
            item["loc"]
            in (("schema",), ("method_state", "schema"), ("method_state", "contract_version"))
            or (
                "parts" in item["loc"]
                and item["loc"][-1] in ("schema", "contract_version", "method_state_version")
            )
            or (adapter is STATE and item["loc"] == ("contract_version",))
            or (
                adapter is RECEIPT
                and item["loc"][-1] in ("schema", "contract_version", "method_state_version")
            )
            for item in error.errors()
        ):
            raise IntegrityError(
                expected="graph DAG v6, descriptor v6, part receipt v2, state contract 4 and continuation v5 schema versions",
                received="obsolete, absent or unknown frozen metadata schema version",
                repair="Preserve existing state and files. Re-execute the source analysis to produce a current snapshot; old snapshots cannot continue.",
                stage="graph_protocol",
                help_target="actions.execute",
            ) from error
        raise invalid(f"invalid closed metadata: {type(error).__name__}") from error
    except (ValueError, TypeError) as error:
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
    schema: Literal["marivo.analysis.receipt/v2"]
    kind: Literal["part"]
    input_binding: str
    key_fields: tuple[tuple[str, str], ...]
    local: PhysicalReceipt
    role: str
    contract_id: str
    contract_version: Literal[4]
    method_state_version: Literal[4]
    payload_digest: str
    layout: PartLayout = field(default_factory=TablePartLayout)
    cell_table: CellTable = field(default_factory=lambda: CellTable((), ()))

    def __post_init__(self) -> None:
        if len(self.payload_digest) != 64 or any(
            c not in "0123456789abcdef" for c in self.payload_digest
        ):
            raise invalid("part receipt requires one exact payload SHA-256 digest")


RECEIPT: TypeAdapter[PrimaryReceipt | PartReceipt] = TypeAdapter(PrimaryReceipt | PartReceipt)


@dataclass(frozen=True, slots=True)
class MethodState:
    schema: Literal["marivo.analysis.method_state/v2"]
    kind: PersistentStateKind
    contract_id: str
    contract_version: Literal[4]
    method_name: MethodName
    method_version: Literal[1]
    input_binding: str
    ordered_part_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        required = (
            ()
            if self.kind == "none"
            else ("subject", "retention")
            if self.kind in ("anchor_retention", "subject_retention")
            else ("history",)
            if self.kind == "canonical_history"
            else ("history_view",)
            if self.kind == "history_view"
            else ("entry_axes",)
            if self.kind == "entry_axes"
            else ("funnel_state",)
            if self.kind in ("funnel_components", "funnel_comparison", "funnel_allocation")
            else ("subject", "journey")
            if self.kind == "journey_assignment"
            else ("subject", "occurrences")
            if self.kind == "occurrence_inputs"
            else ("subject", "cohort_decision")
            if self.kind == "cohort"
            else ("fixed_reference", "reference_proof", "stratum_values", "strata")
            if self.kind == "standardized"
            else ("fixed_reference", "reference_proof", "stratum_values")
            if self.kind in ("share", "penetration", "standardized")
            else (
                "current_endpoint",
                "baseline_endpoint",
                "basis",
                "allocation",
                "reconciliation",
                "selection_scope",
            )
            if self.kind in ("attribution_additive", "attribution_component_mix")
            else ("values", "ranks", "ranking_domain", "partitions", "ordering")
            if self.kind == "ranking"
            else ("columns", "column_bindings")
            if self.kind == "table"
            else (
                ("pair_inputs", "association_state")
                if "pair_inputs" in self.ordered_part_roles
                else ("pair_counts",)
            )
            if self.kind == "spearman"
            else ("current_endpoint", "baseline_endpoint", "correspondence")
            if self.kind in ("difference", "relative_change", "relation_ratio")
            else ("original_state", "coverage")
            if self.kind
            in (
                "original_min",
                "original_max",
                "original_mean",
                "original_fold",
                "original_sum",
                "original_sum_zero",
                "original_count",
                "original_ratio",
                "original_weighted_mean",
                "original_linear",
            )
            else ("row_state",)
        )
        allowed_versions = (4,)
        if self.contract_version not in allowed_versions:
            raise IntegrityError(
                expected=f"{self.kind} state and part contract version in {allowed_versions}",
                received=f"state contract version {self.contract_version}",
                repair=(
                    "Re-execute the reference from complete verified inputs to produce its current retained state; incompatible reference state cannot continue."
                    if self.kind in ("share", "penetration", "standardized")
                    else "Re-execute the producing source analysis to create current retained state; old state cannot continue."
                ),
                stage="graph_protocol",
                help_target="actions.execute",
            )
        if (
            self.contract_id != f"marivo.analysis.state.{self.kind}"
            or len(set(self.ordered_part_roles)) != len(self.ordered_part_roles)
            or any(role not in get_args(PartRole) for role in self.ordered_part_roles)
            or not set(required).issubset(self.ordered_part_roles)
            or not self.input_binding
        ):
            raise invalid("method state, method or required roles differ")


STATE = TypeAdapter(MethodState)
SIGNATURE = TypeAdapter(Signature)
CHECK = TypeAdapter(CheckRequirement)


@dataclass(frozen=True, slots=True)
class Continuation:
    schema: Literal["marivo.analysis.continuation/v5"]
    root: str
    entity_facts: tuple[str, ...]
    dimension_facts: tuple[str, ...]
    semantic_versions: tuple[tuple[str, str], ...]
    method_versions: tuple[MethodKey, ...]
    input_binding: str


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
    fact: Fact


@dataclass(frozen=True, slots=True)
class RowContract:
    key_fields: tuple[str, ...]
    cell_reasons: tuple[tuple[str, tuple[str, ...]], ...]
    column_reasons: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...] = ()


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
    schema: Literal["marivo.analysis.artifact_descriptor/v6"]
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
    time_shape: NoTime | TimeShape


def fixed_signature(
    value: Descriptor, *, _validated: ValidatedDescriptor | None = None
) -> Signature:
    """Expose a checked Artifact's semantic signature to a fixed-only graph."""
    checked = checked_metadata(value, _validated)
    proven = tuple(
        Evidence(
            actual.fact,
            "check",
            digest(actual.producing_run_ref + actual.result_digest + actual.origin_node),
        )
        for actual in checked.descriptor.completed_checks
    )
    return replace(
        value.signature,
        obligations=(),
        evidence=tuple(dict.fromkeys((*value.signature.evidence, *proven))),
    )


DESCRIPTOR = TypeAdapter(Descriptor)


def semantic_versions(
    root: Node, *, _nodes: tuple[Node, ...] | None = None
) -> tuple[tuple[str, str], ...]:
    nodes = topology(root) if _nodes is None else _nodes
    return record_semantic_versions(nodes)


def record_semantic_versions(
    nodes: tuple[Node, ...] | tuple[Record, ...],
) -> tuple[tuple[str, str], ...]:
    versions = [
        (node.definition.ref.path, node.definition.fingerprint)
        for node in nodes
        if isinstance(node, (SourceLeaf, SourceRecord))
    ]
    for node in nodes:
        captures = tuple(
            part
            for part in (
                node.derivation.output if isinstance(node, MethodRecord) else node.signature
            ).parts
            if isinstance(part, OccurrencePart)
        )
        for capture in captures:
            versions.extend((event.ref.path, event.fingerprint) for event in capture.events)
            versions.extend(
                (event.source.ref.path, event.source.dependency_fingerprint)
                for event in capture.events
            )
            if capture.order is not None:
                versions.append((capture.order.ref.path, capture.order.fingerprint))
                versions.extend(capture.order.dependencies)
            if capture.model is not None:
                versions.append((capture.model.ref.path, capture.model.fingerprint))
                versions.extend(capture.model.dependencies)
        if isinstance(node, (MethodNode, MethodRecord)) and isinstance(
            node.parameters, PreparedObservation
        ):
            metric = node.parameters.observation.metric
            versions.append(
                (node.parameters.observation.quantity.definition_id, metric.dependency_fingerprint)
            )
    # Preserve the original source-only sequence for already connected methods.
    return (
        tuple(dict.fromkeys(versions))
        if any(
            isinstance(part, OccurrencePart)
            for node in nodes
            for part in (
                node.derivation.output if isinstance(node, MethodRecord) else node.signature
            ).parts
        )
        or any(
            isinstance(node, (MethodNode, MethodRecord))
            and isinstance(node.parameters, PreparedObservation)
            for node in nodes
        )
        else tuple(versions)
    )


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
        fact,
    )


@dataclass(frozen=True, slots=True)
class ValidatedDescriptor:
    descriptor: Descriptor
    snapshot: Continuation
    document: GraphDocument
    root: MethodRecord
    nodes: tuple[Record, ...]


def validate_metadata(value: Descriptor) -> ValidatedDescriptor:
    if len(value.continuation_snapshot.encode("utf-8")) > 262144:
        raise invalid("frozen continuation exceeds the 256 KiB metadata budget")
    snapshot = decode(value.continuation_snapshot, SNAPSHOT)
    document = read_graph_document(snapshot.root)
    _order(document)
    nodes = execution_records(document)
    root = next(record for record in nodes if record.identity == document.root)
    if not isinstance(root, MethodRecord):
        raise invalid("Artifact has no frozen method result")
    for part in value.parts:
        if part.layout != coordinate_layout(value.signature, part.role):
            raise invalid("contribution receipt layout or owning receipt differs")
    receipts = {part.role: part for part in value.parts}
    if len(receipts) != len(value.parts):
        raise invalid("duplicate part receipt roles")
    for role, _, _, owner in coordinate_declarations(value.signature):
        if role not in receipts or owner not in receipts:
            raise invalid("contribution receipt or its explicit owning receipt is absent")
        keys = coordinate_keys(value.signature, role)
        if tuple(name for name, _ in receipts[role].key_fields) != keys:
            raise invalid("contribution receipt does not retain its complete physical key")
        if receipts[role].input_binding != receipts[owner].input_binding:
            raise invalid("contribution receipt differs from its owning receipt binding")
    checked = ValidatedDescriptor(value, snapshot, document, root, nodes)
    return checked


def validate_descriptor(value: Descriptor) -> Node:
    """Validate frozen metadata then restore semantics for a new continuation."""
    checked = validate_metadata(value)
    return thaw_graph(checked.snapshot.root)


def execution_records(document: GraphDocument) -> tuple[Record, ...]:
    records = {record.identity: record for record in document.nodes}
    seen: set[str] = set()
    ordered: list[Record] = []

    def visit(identity: str) -> None:
        if identity in seen:
            return
        seen.add(identity)
        record = records[identity]
        if isinstance(record, MethodRecord):
            for edge in record.inputs:
                visit(edge.node)
            for source in record.sources:
                visit(source)
        ordered.append(record)

    visit(document.root)
    return tuple(ordered)


def checked_metadata(value: Descriptor, checked: ValidatedDescriptor | None) -> ValidatedDescriptor:
    if checked is None:
        return validate_metadata(value)
    if checked.descriptor is not value:
        raise invalid("validated metadata belongs to a different descriptor")
    return checked


def descriptor_plan(value: Descriptor, root: Node) -> GraphPlan:
    """Explicit producer diagnostics; fixed readers never invoke this planner."""
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
