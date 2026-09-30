"""One bounded definition closure, with references distinct from execution edges."""

from __future__ import annotations

import base64
import zlib
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from marivo.analysis.core.graph import (
    Edge,
    EdgeRole,
    FixedLeaf,
    MethodNode,
    Node,
    SourceDefinition,
    SourceLeaf,
)
from marivo.analysis.core.model import Signature
from marivo.analysis.core.rules import (
    CellDerive,
    OriginalReduce,
    PartsTransport,
    RowState,
    RuleDerivation,
    RuleParameters,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.methods.physical import FixedShape, ValueType
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef

MAX_NODES = 4096
MAX_REFERENCES = 16384
MAX_DEPTH = 128
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
MAX_ENCODED_BYTES = 262144
PREFIX = "graph-dag-v1:"


@dataclass(frozen=True, slots=True)
class InputReference:
    role: EdgeRole
    node: str


@dataclass(frozen=True, slots=True)
class SourceRecord:
    kind: Literal["source"]
    identity: str
    definition: SourceDefinition
    signature: Signature
    value_type: ValueType


@dataclass(frozen=True, slots=True)
class FixedRecord:
    kind: Literal["fixed"]
    identity: str
    artifact: ArtifactRef
    definition_fingerprint: str
    signature: Signature
    value_type: ValueType
    shape: FixedShape


@dataclass(frozen=True, slots=True)
class MethodRecord:
    kind: Literal["method"]
    identity: str
    method: MethodKey
    parameters: RuleParameters
    inputs: tuple[InputReference, ...]
    derivation: RuleDerivation
    value_type: ValueType
    sources: tuple[str, ...]
    retained_endpoints: tuple[str, ...]


Record = Annotated[SourceRecord | FixedRecord | MethodRecord, Field(discriminator="kind")]


@dataclass(frozen=True, slots=True)
class GraphDocument:
    schema: Literal["marivo.analysis.graph_dag/v1"]
    root: str
    nodes: tuple[Record, ...]


GRAPH = TypeAdapter(GraphDocument)


def node_record(node: Node) -> Record:
    """Describe just this node, never recursively serialize its dependencies."""
    if isinstance(node, SourceLeaf):
        return SourceRecord(
            "source", node.identity, node.definition, node.signature, node.value_type
        )
    if isinstance(node, FixedLeaf):
        return FixedRecord(
            "fixed",
            node.identity,
            node.artifact,
            node.definition_fingerprint,
            node.signature,
            node.value_type,
            node.shape,
        )
    return MethodRecord(
        "method",
        node.identity,
        node.method,
        node.parameters,
        tuple(InputReference(edge.role, edge.node.identity) for edge in node.inputs),
        node.derivation,
        node.value_type,
        tuple(source.identity for source in node.sources),
        tuple(endpoint.identity for endpoint in node.retained_endpoints),
    )


def _record_text(record: Record) -> str:
    from marivo.analysis.materialization.graph_protocol import encode

    # Dataclass equality can equate differently typed literals (e.g. 1 and 1.0).
    # Compare the exact closed wire facts, without recursively expanding inputs.
    return encode(GraphDocument("marivo.analysis.graph_dag/v1", record.identity, (record,)), GRAPH)


def same_node_definition(first: Node, second: Node) -> bool:
    """Compare exact shallow definitions while preserving typed literal distinctions."""
    if first is second:
        return True
    return _record_text(node_record(first)) == _record_text(node_record(second))


def _references(record: Record) -> tuple[str, ...]:
    if isinstance(record, MethodRecord):
        return (
            *tuple(edge.node for edge in record.inputs),
            *record.sources,
            *record.retained_endpoints,
        )
    return ()


def _signature(record: Record) -> Signature:
    return record.derivation.output if isinstance(record, MethodRecord) else record.signature


def _order(document: GraphDocument) -> tuple[Record, ...]:
    """Check the entire reference envelope before constructing any graph node."""
    from marivo.analysis.materialization.graph_protocol import invalid

    if not 0 < len(document.nodes) <= MAX_NODES:
        raise invalid("definition node budget exceeded or empty node table")
    records = {record.identity: record for record in document.nodes}
    if len(records) != len(document.nodes) or any(not identity for identity in records):
        raise invalid("duplicate or empty captured node identity")
    if tuple(records) != tuple(sorted(records)):
        raise invalid("noncanonical node table order")
    if sum(len(_references(record)) for record in document.nodes) > MAX_REFERENCES:
        raise invalid("definition reference budget exceeded")
    for record in document.nodes:
        if any(identity not in records for identity in _references(record)):
            raise invalid("missing definition reference")
        if isinstance(record, MethodRecord):
            if not record.inputs or any(
                edge.role not in ("subject", "quantity", "current", "baseline", "reference")
                for edge in record.inputs
            ):
                raise invalid("invalid ordered input role")
            expected_roles = (
                ("current", "baseline")
                if isinstance(record.parameters, CellDerive)
                else tuple(
                    "subject" if _signature(records[edge.node]).quantity is None else "quantity"
                    for edge in record.inputs
                )
            )
            if tuple(edge.role for edge in record.inputs) != expected_roles:
                raise invalid("ordered input roles differ from the method contract")
            if record.retained_endpoints and (
                not isinstance(
                    record.parameters, (CellDerive, PartsTransport, OriginalReduce, RowState)
                )
                or len(record.retained_endpoints) != len(record.inputs)
            ):
                raise invalid("retained endpoint slots differ from the method contract")
            if any(not isinstance(records[identity], SourceRecord) for identity in record.sources):
                raise invalid("source reference does not name a source definition")
            if any(
                not isinstance(records[identity], MethodRecord)
                for identity in record.retained_endpoints
            ):
                raise invalid("retained endpoint reference does not name a method definition")
    active: set[str] = set()
    depths: dict[str, int] = {}
    ordered: list[Record] = []

    def visit(identity: str, depth: int) -> int:
        if depth > MAX_DEPTH:
            raise invalid("definition depth budget exceeded")
        if identity in active:
            raise invalid("cyclic definition reference")
        if identity in depths:
            return depths[identity]
        record = records.get(identity)
        if record is None:
            raise invalid("missing root definition reference")
        active.add(identity)
        height = 1 + max((visit(child, depth + 1) for child in _references(record)), default=0)
        if height > MAX_DEPTH:
            raise invalid("definition depth budget exceeded")
        active.remove(identity)
        depths[identity] = height
        ordered.append(record)
        return height

    visit(document.root, 1)
    if len(ordered) != len(records):
        raise invalid("unreachable injected definition nodes")
    return tuple(ordered)


def graph_document(root: Node) -> GraphDocument:
    """Collect definitions by capture identity, including non-executable retention."""
    from marivo.analysis.materialization.graph_protocol import invalid

    records: dict[str, Record] = {}
    seen: set[int] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if type(node) not in (MethodNode, SourceLeaf, FixedLeaf):
            raise invalid("untyped definition node")
        record = node_record(node)
        prior = records.get(node.identity)
        if prior is not None and _record_text(prior) != _record_text(record):
            raise invalid("one node identity has different frozen definitions")
        records[node.identity] = record
        if len(records) > MAX_NODES or len(seen) > MAX_REFERENCES + 1:
            raise invalid("definition node or occurrence budget exceeded")
        if isinstance(node, MethodNode):
            pending.extend(edge.node for edge in node.inputs)
            pending.extend(node.sources)
            pending.extend(node.retained_endpoints)
    document = GraphDocument(
        "marivo.analysis.graph_dag/v1",
        root.identity,
        tuple(records[identity] for identity in sorted(records)),
    )
    _order(document)
    return document


def _restore(document: GraphDocument) -> Node:
    from marivo.analysis.materialization.graph_protocol import invalid

    ordered = _order(document)
    nodes: dict[str, Node] = {}
    for record in ordered:
        node: Node
        if isinstance(record, SourceRecord):
            node = SourceLeaf(
                record.definition, record.signature, record.value_type, record.identity
            )
        elif isinstance(record, FixedRecord):
            node = FixedLeaf(
                record.artifact,
                record.definition_fingerprint,
                record.signature,
                record.value_type,
                record.shape,
                record.identity,
            )
        else:
            sources = tuple(nodes[identity] for identity in record.sources)
            endpoints = tuple(nodes[identity] for identity in record.retained_endpoints)
            node = MethodNode(
                record.method,
                record.parameters,
                tuple(Edge(edge.role, nodes[edge.node]) for edge in record.inputs),
                record.derivation,
                record.value_type,
                tuple(source for source in sources if isinstance(source, SourceLeaf)),
                record.identity,
                tuple(endpoint for endpoint in endpoints if isinstance(endpoint, MethodNode)),
            )
        nodes[record.identity] = node
    owners = {
        (node.signature.domain.binding.session_id, node.signature.domain.binding.owner_id)
        for node in nodes.values()
    }
    if len(owners) != 1:
        raise invalid("definition closure has multiple Session or owner bindings")
    return nodes[document.root]


def freeze_graph(root: Node) -> str:
    """Encode the single current graph format, independent of method family."""
    from marivo.analysis.materialization.graph_protocol import encode, invalid

    document = graph_document(root)
    _restore(document)
    body = encode(document, GRAPH).encode()
    if len(body) > MAX_EXPANDED_BYTES:
        raise invalid("definition exceeds its 4 MiB expanded bound")
    text = PREFIX + base64.b64encode(zlib.compress(body, level=9)).decode("ascii")
    if len(text.encode()) > MAX_ENCODED_BYTES:
        raise invalid("definition exceeds the 256 KiB continuation budget")
    return text


def thaw_graph(text: str) -> Node:
    """Validate a bounded canonical DAG before admitting its exact definitions."""
    from marivo.analysis.errors import AnalysisError
    from marivo.analysis.materialization.graph_protocol import decode, invalid

    if not text.startswith(PREFIX):
        raise IntegrityError(
            expected="the current graph-dag-v1 frozen definition",
            received="obsolete or unknown graph snapshot format",
            repair="Re-execute the source analysis to produce a current snapshot; old snapshots cannot continue.",
            stage="graph_protocol",
            help_target="actions.execute",
        )
    if len(text.encode()) > MAX_ENCODED_BYTES:
        raise invalid("definition exceeds the 256 KiB continuation budget")
    try:
        compressed = base64.b64decode(text.removeprefix(PREFIX), validate=True)
        decoder = zlib.decompressobj()
        body = decoder.decompress(compressed, MAX_EXPANDED_BYTES + 1)
        if (
            len(body) > MAX_EXPANDED_BYTES
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise invalid("invalid bounded compressed definition")
        if PREFIX + base64.b64encode(zlib.compress(body, level=9)).decode("ascii") != text:
            raise invalid("noncanonical compressed definition")
        document = decode(body.decode("utf-8"), GRAPH)
        return _restore(document)
    except IntegrityError:
        raise
    except (
        ValueError,
        TypeError,
        zlib.error,
        UnicodeError,
        RecursionError,
        AnalysisError,
    ) as error:
        raise invalid(f"invalid frozen DAG definition: {type(error).__name__}") from error
