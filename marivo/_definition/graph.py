"""Definition edges, generic traversal, and computation fingerprints.

Edges carry the closed input role and the ordered usage position inside the
consuming node, so ``m + m`` keeps two uses of one shared definition.
Fingerprints are process-local identities of the canonical computation; there
is no persistence codec, decoder, or Ibis mirror.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, TypeAlias, TypeGuard, get_args

from typing_extensions import assert_never

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricAdd,
    MetricConstant,
    MetricDivide,
    MetricMultiply,
    MetricNegate,
    MetricNode,
    MetricSubtract,
    PerReduce,
    PerWeightedMean,
    RowAdd,
    RowConstant,
    RowDivide,
    RowField,
    RowMultiply,
    RowNegate,
    RowNode,
    RowSubtract,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.relations import (
    LiteralPopulation,
    ObserveQuery,
    PopulationNode,
    PopulationProgram,
    QueryKeys,
    QueryNode,
    SourceIdentities,
)
from marivo._definition.types import (
    SUPPORTED_RULE_VERSIONS,
    ExactRef,
    Owner,
    invalid,
    literal_token,
)
from marivo._temporal import Grain as TemporalGrain
from marivo.refs import Ref

DefinitionNode: TypeAlias = (
    RowNode | MetricNode | LiteralPopulation | PopulationProgram | PopulationNode | QueryNode
)

EdgeRole: TypeAlias = Literal[
    "operand",
    "left",
    "right",
    "numerator",
    "denominator",
    "value",
    "weight",
    "predicate",
    "samples",
    "members",
    "population",
    "query",
    "metric",
]


# Canonical encoding ------------------------------------------------------


def canonical_value(value: object) -> object:
    """Convert supported typed payloads to deterministic JSON values."""
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, TemporalGrain):
        if value.kind == "builtin":
            return value.to_token()
        assert value.calendar is not None and value.level is not None
        return {
            "kind": "semantic",
            "calendar_ref": value.calendar.path,
            "level": value.level,
        }
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            invalid("canonical payload rejects non-finite floats")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            invalid("canonical mappings require string keys")
        return {key: canonical_value(value[key]) for key in sorted(value)}
    invalid(f"unsupported canonical payload type: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Encode one supported payload as stable UTF-8 JSON bytes."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    """Return the lowercase SHA-256 fingerprint of canonical bytes."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


# Edges -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Edge:
    """One ordered usage of ``child`` by its consuming node under a closed role."""

    role: EdgeRole
    index: int
    child: DefinitionNode


def _edges(*inputs: tuple[EdgeRole, DefinitionNode]) -> tuple[Edge, ...]:
    return tuple(Edge(role, index, child) for index, (role, child) in enumerate(inputs))


def _population_inputs(
    role: EdgeRole, value: object
) -> tuple[tuple[EdgeRole, DefinitionNode], ...]:
    if isinstance(value, (LiteralPopulation, PopulationProgram)):
        return ((role, value),)
    return ()


def children(node: DefinitionNode) -> tuple[Edge, ...]:
    """Return the ordered role edges of ``node``; declared refs expand only during ``walk``."""
    match node:
        case (
            RowField()
            | RowConstant()
            | MetricConstant()
            | EntityCount()
            | DeclaredMetricRef()
            | LiteralPopulation()
            | SourceIdentities()
        ):
            return ()
        case RowNegate() | MetricNegate() | MeasureAssertion() | SourceAggregate():
            return _edges(("operand", node.operand))
        case RowAdd() | RowSubtract() | RowMultiply():
            return _edges(("left", node.left), ("right", node.right))
        case MetricAdd() | MetricSubtract() | MetricMultiply():
            return _edges(("left", node.left), ("right", node.right))
        case RowDivide():
            return _edges(("numerator", node.numerator), ("denominator", node.denominator))
        case MetricDivide():
            return _edges(("numerator", node.numerator), ("denominator", node.denominator))
        case SourceWeightedMean():
            return _edges(("value", node.value), ("weight", node.weight))
        case ContributionFilter():
            return _edges(
                ("operand", node.operand), *_population_inputs("predicate", node.predicate)
            )
        case PerReduce():
            return _edges(("operand", node.operand), *_population_inputs("samples", node.samples))
        case PerWeightedMean():
            return _edges(
                ("value", node.value),
                ("weight", node.weight),
                *_population_inputs("samples", node.samples),
            )
        case MemberCount():
            return _edges(("members", node.population))
        case PopulationProgram():
            return _edges(("population", node.root))
        case QueryKeys():
            return _edges(("query", node.query))
        case ObserveQuery():
            return tuple(
                Edge("metric", index, metric) for index, (_, metric) in enumerate(node.metrics)
            )
        case _:
            assert_never(node)


# Fingerprints ------------------------------------------------------------


_NODE_TYPES: tuple[type, ...] = get_args(DefinitionNode)


def _is_node(value: object) -> TypeGuard[DefinitionNode]:
    return isinstance(value, _NODE_TYPES)


def _encode(value: object, memo: dict[int, str]) -> object:
    if _is_node(value):
        return _fingerprint_node(value, memo)
    if isinstance(value, Ref):
        return ["ref", value.key]
    if value is None or isinstance(value, (str, int, float, Decimal, date)):
        return list(literal_token(value))
    if isinstance(value, tuple):
        return [_encode(item, memo) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            **{field.name: _encode(getattr(value, field.name), memo) for field in fields(value)},
        }
    invalid(f"unsupported definition value type: {type(value).__name__}")


def _fingerprint_node(node: DefinitionNode, memo: dict[int, str]) -> str:
    cached = memo.get(id(node))
    if cached is None:
        cached = fingerprint(_encode_fields(node, memo))
        memo[id(node)] = cached
    return cached


def _encode_fields(node: DefinitionNode, memo: dict[int, str]) -> dict[str, object]:
    return {
        "node": type(node).__name__,
        **{field.name: _encode(getattr(node, field.name), memo) for field in fields(node)},
    }


def computation_fingerprint(node: DefinitionNode) -> str:
    """Return the canonical computation identity of ``node``.

    Declaration names, domains, ai_context, and source locations live on the
    declaration records and never enter this fingerprint. Policies, rule
    versions, exact dependency versions, and explicit Measure assertions do.
    """
    return _fingerprint_node(node, {})


# Traversal ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefinitionClosure:
    """Topologically ordered dependency closure of a set of definition roots."""

    order: tuple[DefinitionNode, ...]
    edges: tuple[tuple[DefinitionNode, Edge], ...]
    refs: frozenset[ExactRef]
    owner: Owner | None


def _collect_refs(value: object, into: set[ExactRef]) -> None:
    if isinstance(value, ExactRef):
        into.add(value)
    elif isinstance(value, tuple):
        for item in value:
            _collect_refs(item, into)
    elif is_dataclass(value) and not isinstance(value, type) and not _is_node(value):
        for field in fields(value):
            _collect_refs(getattr(value, field.name), into)


def walk(
    roots: Iterable[DefinitionNode],
    *,
    resolve: Callable[[ExactRef], MetricNode | None],
) -> DefinitionClosure:
    """Traverse ``roots`` through role edges and resolved declared metrics.

    Nodes are tracked by object identity, so structurally equal but distinct
    nodes (``1`` and ``1.0``) both stay in the closure and shared DAGs are
    visited once. Each declared ref resolves once; cycles through declarations
    are detected by exact ref. Fails with a located ``DefinitionGraphError`` on
    a real dependency cycle, conflicting Session ownership, an unresolved
    declared metric, or an unsupported rule version.
    """
    order: list[DefinitionNode] = []
    edges: list[tuple[DefinitionNode, Edge]] = []
    refs: set[ExactRef] = set()
    owners: dict[Owner, tuple[str, ...]] = {}
    done: set[int] = set()
    active: set[int] = set()
    active_refs: set[ExactRef] = set()
    resolved_refs: dict[ExactRef, MetricNode] = {}

    def visit(node: DefinitionNode, path: tuple[str, ...]) -> None:
        if id(node) in done:
            return
        declared = node.ref if isinstance(node, DeclaredMetricRef) else None
        if id(node) in active or (declared is not None and declared in active_refs):
            invalid(
                "definition dependency cycle detected; break the cycle by removing one "
                "of the dependencies along the path",
                kind="cycle",
                path=path,
            )
        active.add(id(node))
        if declared is not None:
            active_refs.add(declared)
        rule_version = getattr(node, "rule_version", None)
        if rule_version is not None and rule_version not in SUPPORTED_RULE_VERSIONS:
            invalid(
                f"{type(node).__name__} expected rule version in "
                f"{sorted(SUPPORTED_RULE_VERSIONS)}; received {rule_version!r}. "
                "Rebuild the definition with the current Marivo version.",
                kind="unknown_rule_version",
                path=path,
            )
        owner = getattr(node, "owner", None)
        if isinstance(owner, Owner):
            owners.setdefault(owner, path)
            if len(owners) > 1:
                first, second = owners
                invalid(
                    f"definition combines owners {first.session_id}/{first.owner_id} and "
                    f"{second.session_id}/{second.owner_id}; build every lazy Population "
                    "and Query input from one Session owner",
                    kind="ownership_conflict",
                    path=path,
                )
        for field in fields(node):
            _collect_refs(getattr(node, field.name), refs)
        node_edges = children(node)
        if declared is not None:
            resolved = resolved_refs.get(declared)
            if resolved is None:
                resolved = resolve(declared)
                if resolved is None:
                    invalid(
                        f"declared metric {declared.ref.key}@{declared.version} could not be "
                        "resolved; publish the declaration or reference an available version",
                        kind="unresolved",
                        path=path,
                    )
                resolved_refs[declared] = resolved
            node_edges = (Edge("metric", 0, resolved),)
        for edge in node_edges:
            edges.append((node, edge))
            visit(edge.child, (*path, f"{type(node).__name__}.{edge.role}[{edge.index}]"))
        active.discard(id(node))
        if declared is not None:
            active_refs.discard(declared)
        done.add(id(node))
        order.append(node)

    for root in roots:
        visit(root, ())
    owner = next(iter(owners), None)
    return DefinitionClosure(
        order=tuple(order), edges=tuple(edges), refs=frozenset(refs), owner=owner
    )
