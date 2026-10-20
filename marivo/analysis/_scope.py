"""Bounded captured input scope for logical cards and saved declarations."""

from __future__ import annotations

import re

from marivo._definition.expressions import FieldPredicate
from marivo._definition.graph import DefinitionNode, children, direct_refs
from marivo._definition.observations import ObservationBinding
from marivo._definition.query import FilterValues, ResultQuery, TopValues
from marivo._definition.relations import (
    CalendarKeys,
    FrameKeys,
    IntervalKeys,
    LiteralPopulation,
    MembershipBinding,
    PopulationClassify,
    PopulationImage,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    SourceIdentities,
)
from marivo._definition.types import AxisBinding


def _bounded(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    return (
        text
        if len(encoded) <= limit
        else encoded[: limit - 12].decode("utf-8", errors="ignore") + "... omitted"
    )


def predicate_summary(predicate: FieldPredicate) -> str:
    """Name governed fields in a captured Ibis predicate without evaluating it."""
    names = {
        f"slot_{i}": ", ".join(
            sorted(
                item.ref.path
                for item in direct_refs(operand)
                if item.ref.kind.value in ("dimension", "time_dimension", "measure")
            )
        )
        for i, operand in enumerate(predicate.inputs)
    }
    return re.sub(r"\bslot_\d+\b", lambda match: names[match.group()], repr(predicate.recipe))


def _observation_summary(observation: ObservationBinding) -> str:
    period = observation.period
    time = (
        "none added"
        if period is None
        else f"[{period.start.isoformat() if period.start else 'unbounded'}, {period.end.isoformat()}) {period.timezone}"
    )
    where = (
        "none added"
        if observation.condition is None
        else _bounded(predicate_summary(observation.condition), 400)
    )
    return f"period={time}; field_where={where}"


def _observation_scopes(observation: ObservationBinding) -> tuple[tuple[str, DefinitionNode], ...]:
    scopes: list[tuple[str, DefinitionNode]] = []
    if observation.coordinates.population is not None:
        scopes.append(("coordinates", observation.coordinates.population))
    for index, (domain, evaluation) in enumerate(observation.count_populations):
        scopes.append((f"count[{index}].domain", domain))
        if evaluation is not domain:
            scopes.append((f"count[{index}].evaluation", evaluation))
    for index, contribution in enumerate(observation.contributions):
        for position, condition in enumerate(contribution.population_conditions):
            scopes.append((f"contribution[{index}].condition[{position}]", condition))
    return tuple(scopes)


def _axis_summary(axis: AxisBinding) -> str:
    target = axis.field if axis.field is not None else axis.entity
    return (
        f"{axis.role}:{target.ref.path}"
        + (f" via={tuple(item.ref.path for item in axis.path)}" if axis.path else "")
        + (f" at={axis.at.isoformat()}" if axis.at else "")
    )


def _node_summary(node: DefinitionNode) -> str:
    if isinstance(node, SourceIdentities):
        at = node.at.isoformat() if node.at else "none"
        predicate = node.predicate
        where = (
            "none added"
            if predicate is None
            else _bounded(predicate_summary(predicate), 400)
            if isinstance(predicate, FieldPredicate)
            else type(predicate).__name__
        )
        return f"SourceIdentities entity={node.entity.ref.path}; at={at}; where={where}"
    if isinstance(node, LiteralPopulation):
        keys = ", ".join(_bounded(repr(key), 160) for key in node.keys[:3])
        more = "; keys omitted" if len(node.keys) > 3 else ""
        axes = tuple(_axis_summary(axis) for axis in node.axes if isinstance(axis, AxisBinding))
        return f"LiteralPopulation axes={axes}; coordinates={len(node.keys)}; keys=[{keys}]{more}"
    if isinstance(node, FrameKeys):
        bindings = tuple(
            (binding.token, binding.key_names, _axis_summary(binding.target))
            for binding in node.bindings
        )
        return f"FrameKeys input={node.input_id}; on={bindings}"
    if isinstance(node, PopulationSet):
        return node.operation
    if isinstance(node, PopulationProject):
        return f"PopulationProject positions={node.positions}"
    if isinstance(node, PopulationClassify):
        return "PopulationClassify fields=" + ", ".join(
            field.field.ref.path for field in node.fields[:6]
        )
    if isinstance(node, (PopulationImage, PopulationPreimage)):
        return (
            type(node).__name__ + " path=" + ", ".join(item.ref.ref.path for item in node.path[:6])
        )
    if isinstance(node, IntervalKeys):
        interval = node.interval
        return f"IntervalKeys [{interval.start.isoformat()}, {interval.end.isoformat()}) {interval.timezone}"
    if isinstance(node, PopulationProduct):
        return f"PopulationProduct factors={len(node.factors)}; joint tuples preserved"
    if isinstance(node, CalendarKeys):
        return f"CalendarKeys grain={node.calendar.grain}; timezone={node.calendar.timezone}; cells={len(node.calendar.cells)}"
    if isinstance(node, ResultQuery):
        operation = node.operation
        if isinstance(operation, FilterValues):
            return "FilterValues predicate=" + _bounded(repr(operation.predicate), 400)
        if isinstance(operation, TopValues):
            return f"TopValues n={operation.n}; descending={operation.descending}"
        return type(operation).__name__
    return type(node).__name__


def scope_summary(root: DefinitionNode, observation: ObservationBinding | None = None) -> str:
    """Summarize captured inputs, preserving relation roles without source reads.

    This bounded display is neither a complete definition nor an executable
    serialization. Metric internals remain owned by their governed definitions.
    """
    lines = [_observation_summary(observation)] if observation is not None else []
    pending: list[tuple[str, str, DefinitionNode]] = [
        *(
            (role, "", value)
            for role, value in reversed(
                _observation_scopes(observation) if observation is not None else ()
            )
        ),
        ("scope", "", root),
    ]
    visited = 0
    seen: dict[int, str] = {}
    memberships: set[MembershipBinding] = set()
    while pending and visited < 12:
        path, role, node = pending.pop()
        label = _bounded(path + (" " + role if role else ""), 100)
        while isinstance(node, (PopulationProgram, LiteralPopulation)):
            for binding in node.memberships:
                if binding not in memberships:
                    memberships.add(binding)
                    fields = tuple(field.field.ref.path for field in binding.fields)
                    paths = tuple(
                        tuple(item.ref.ref.path for item in route) for route in binding.paths
                    )
                    lines.append(
                        _bounded(
                            f"{label}: MembershipBinding fields={fields}; paths={paths}; unmatched={binding.unmatched}",
                            600,
                        )
                    )
            if isinstance(node, LiteralPopulation):
                break
            node = node.root
        previous = seen.get(id(node))
        if previous is not None:
            lines.append(label + ": same as " + previous)
            visited += 1
            continue
        seen[id(node)] = label
        lines.append(label + ": " + _bounded(_node_summary(node), 600))
        visited += 1
        edges = children(node)
        if isinstance(node, SourceIdentities) and isinstance(node.predicate, FieldPredicate):
            continue
        pending.extend(
            (path + f".{edge.index}", edge.role, edge.child)
            for edge in reversed(edges)
            if edge.role != "metric"
        )
    if pending:
        lines.append("... scope nodes omitted")
    return _bounded("; ".join(lines), 3000)
