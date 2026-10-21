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
from datetime import date, timedelta
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
    MetricExpression,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.query import (
    ResultQuery,
)
from marivo._definition.relations import (
    AttributeKeys,
    CalendarKeys,
    FrameKeys,
    IntervalKeys,
    LiteralPopulation,
    ObserveQuery,
    PopulationClassify,
    PopulationContains,
    PopulationFilter,
    PopulationImage,
    PopulationNode,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    QueryNode,
    ReadQuery,
    SampleDomain,
    SourceIdentities,
)
from marivo._definition.types import (
    SUPPORTED_RULE_VERSIONS,
    ExactRef,
    Owner,
    invalid,
    literal_token,
)
from marivo._definition.windows import ToDate
from marivo._temporal import Grain as TemporalGrain
from marivo.refs import PeriodCalendarKind, Ref

DefinitionNode: TypeAlias = (
    RowNode
    | MetricNode
    | LiteralPopulation
    | PopulationProgram
    | PopulationNode
    | PredicateExpression
    | PopulationContains
    | QueryNode
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
            return value._to_token()
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
    if isinstance(value, SampleDomain):
        return ((role, value.population),) if value.population is not None else ()
    if isinstance(value, (LiteralPopulation, PopulationProgram)):
        return ((role, value),)
    return ()


def _predicate_inputs(predicate: object) -> tuple[tuple[EdgeRole, DefinitionNode], ...]:
    """Row expressions compared inside a field predicate, in predicate order."""
    if isinstance(predicate, PredicateExpression):
        return (("predicate", predicate),)
    return _population_inputs("predicate", predicate)


def children(node: DefinitionNode) -> tuple[Edge, ...]:
    """Return the ordered role edges of ``node``; declared refs expand only during ``walk``."""
    match node:
        case PredicateExpression():
            return _edges(*(("predicate", item) for item in node.inputs))
        case RowExpression():
            return _edges(*(("operand", item) for item in node.inputs))
        case MetricExpression():
            return _edges(*(("operand", item) for item in node.operands))
        case (
            RowField()
            | EntityCount()
            | DeclaredMetricRef()
            | LiteralPopulation()
            | CalendarKeys()
            | IntervalKeys()
            | FrameKeys()
            | AttributeKeys()
        ):
            return ()
        case SourceIdentities():
            return _edges(*_predicate_inputs(node.predicate))
        case MeasureAssertion() | SourceAggregate() | MetricWindow():
            return _edges(("operand", node.operand))
        case SourceWeightedMean():
            return _edges(("value", node.value), ("weight", node.weight))
        case ContributionFilter():
            return _edges(("operand", node.operand), *_predicate_inputs(node.predicate))
        case PerReduce():
            return _edges(("operand", node.operand), *_population_inputs("samples", node.samples))
        case PerWeightedMean():
            return _edges(
                ("value", node.value),
                ("weight", node.weight),
                *_population_inputs("samples", node.samples),
            )
        case MemberCount():
            return _edges(("population", node.population))
        case PopulationProgram():
            return _edges(("population", node.root))
        case ReadQuery():
            return _edges(("population", node.population))
        case ResultQuery():
            return ()
        case PopulationClassify() | PopulationProject():
            return _edges(("population", node.operand))
        case PopulationProduct():
            return _edges(*(("population", factor) for factor in node.factors))
        case PopulationSet():
            return _edges(("left", node.left), ("right", node.right))
        case PopulationContains():
            return _edges(("population", node.population))
        case PopulationFilter():
            return _edges(("population", node.operand), *_predicate_inputs(node.predicate))
        case PopulationImage():
            return _edges(("population", node.operand))
        case PopulationPreimage():
            return _edges(("population", node.operand), ("members", node.targets))
        case ObserveQuery():
            return (
                *tuple(
                    Edge("metric", index, metric) for index, (_, metric) in enumerate(node.metrics)
                ),
                *(
                    (Edge("population", len(node.metrics), node.population),)
                    if node.population is not None
                    else ()
                ),
                *tuple(
                    Edge("population", len(node.metrics) + 1 + index, population)
                    for index, population in enumerate(node.count_populations)
                ),
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
    if isinstance(value, timedelta):
        return ["timedelta", value.days, value.seconds, value.microseconds]
    if isinstance(value, TemporalGrain):
        return canonical_value(value)
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
    if isinstance(node, (RowExpression, PredicateExpression, MetricExpression)):
        from marivo._definition.deferred import canonical_recipe

        inputs = (
            node.inputs if isinstance(node, (RowExpression, PredicateExpression)) else node.operands
        )
        return {
            "node": type(node).__name__,
            "recipe": canonical_recipe(
                node.recipe, slots=tuple(f"slot_{index}" for index in range(len(inputs)))
            ),
            "inputs": [_encode(item, memo) for item in inputs],
        }
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


def direct_refs(root: DefinitionNode) -> frozenset[ExactRef]:
    """Exact refs ``root`` consumes; declared Metric references are kept, not expanded.

    A Measure assertion's authorization names the declaration that asserted a
    fact; it is provenance, not a consumed definition.
    """
    found: set[ExactRef] = set()
    pending: list[DefinitionNode] = []
    if _is_node(root):
        pending.append(root)
    else:
        _collect_refs(root, found)
        pending.extend(child for _role, child in _predicate_inputs(root))
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        _consumed_refs(node, found)
        pending.extend(edge.child for edge in children(node))
    return frozenset(found)


def direct_calendar_refs(root: DefinitionNode) -> frozenset[Ref[PeriodCalendarKind]]:
    """Calendar lookup dependencies of window rules, without definition versions.

    Declared Metric references remain edges to their own published dependencies.
    A Grain's captured snapshot is not an authored dependency graph.
    """
    found: set[Ref[PeriodCalendarKind]] = set()
    pending = [root]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, MetricWindow) and isinstance(node.rule, ToDate):
            grain = node.rule.grain
            if grain.kind == "semantic":
                assert grain.calendar is not None
                found.add(grain.calendar)
        pending.extend(edge.child for edge in children(node))
    return frozenset(found)


def _consumed_refs(node: DefinitionNode, into: set[ExactRef]) -> None:
    """Collect the exact refs one node's own fields consume; authorization is provenance."""
    for item in fields(node):
        if isinstance(node, MeasureAssertion) and item.name == "authorization":
            continue
        _collect_refs(getattr(node, item.name), into)


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
    done: set[tuple[int, int]] = set()
    active: set[tuple[int, int]] = set()
    active_refs: set[tuple[ExactRef, int]] = set()
    resolved_refs: dict[tuple[ExactRef, int], MetricNode] = {}

    def visit(
        node: DefinitionNode,
        path: tuple[str, ...],
        captured: dict[ExactRef, MetricNode] | None = None,
        scope_id: int = 0,
    ) -> None:
        node_key = (id(node), scope_id)
        if node_key in done:
            return
        declared = node.ref if isinstance(node, DeclaredMetricRef) else None
        if node_key in active or (declared is not None and (declared, scope_id) in active_refs):
            invalid(
                "definition dependency cycle detected; break the cycle by removing one "
                "of the dependencies along the path",
                kind="cycle",
                path=path,
            )
        active.add(node_key)
        if declared is not None:
            active_refs.add((declared, scope_id))
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
        _consumed_refs(node, refs)
        node_edges = children(node)
        if declared is not None:
            resolution_key = (declared, scope_id)
            resolved = resolved_refs.get(resolution_key)
            if resolved is None:
                resolved = captured.get(declared) if captured is not None else None
                if resolved is None:
                    resolved = resolve(declared)
                if resolved is None:
                    invalid(
                        f"declared metric {declared.ref.key}@{declared.version} could not be "
                        "resolved; publish the declaration or reference an available version",
                        kind="unresolved",
                        path=path,
                    )
                resolved_refs[resolution_key] = resolved
            node_edges = (Edge("metric", 0, resolved),)
        for edge in node_edges:
            edges.append((node, edge))
            visit(
                edge.child,
                (*path, f"{type(node).__name__}.{edge.role}[{edge.index}]"),
                captured,
                scope_id,
            )
        active.discard(node_key)
        if declared is not None:
            active_refs.discard((declared, scope_id))
        done.add(node_key)
        order.append(node)

    for root in roots:
        visit(root, ())
    owner = next(iter(owners), None)
    return DefinitionClosure(
        order=tuple(order), edges=tuple(edges), refs=frozenset(refs), owner=owner
    )
