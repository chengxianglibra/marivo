"""Registered projections of retained assignments, without event matching."""

from datetime import datetime

import pyarrow as pa

from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import Cell, Defined, JourneyPart
from marivo.analysis.core.rules import JourneyCompleted, JourneyDuration, JourneyRead
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.journey_execution import ASSIGNMENT, validate
from marivo.analysis.methods.domain_coverage import FACTS
from marivo.analysis.methods.journey_duration import dropped_before, duration
from marivo.analysis.methods.journey_matching import CoverageWindow
from marivo.analysis.methods.physical import arrow_scalar_type


def execute(node: MethodNode, source: ExchangeResult, binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, (JourneyDuration, JourneyCompleted, JourneyRead))
    validate(source.contract, source.primary, source.parts)
    part = next(p for p in source.contract.signature.parts if isinstance(p, JourneyPart))
    keys = source.contract.key_fields
    rows = next(p.table for p in source.parts if p.role == "journey").to_pylist()
    assignments = {
        tuple(row[key] for key in keys): ASSIGNMENT.validate_json(
            row["journey__assignment"], strict=True
        )
        for row in rows
    }
    facts = FACTS.validate_json((source.primary.schema.metadata or {})[b"r7.coverage"])
    coverage = tuple(
        CoverageWindow(
            fact.event,
            None if fact.complete_from is None else datetime.fromisoformat(fact.complete_from),
            datetime.fromisoformat(fact.complete_through),
        )
        for fact in facts
        if fact.complete_through is not None
        and (fact.complete_from is not None or fact.source_origin is not None)
    )
    indexes: list[int] = []
    cells: list[Cell] = []
    for index, row in enumerate(source.primary.to_pylist()):
        check()
        assignment = assignments[tuple(row[key] for key in keys)]
        observation = duration(
            assignment,
            from_step=params.from_step,
            to_step=params.to_step,
            events=part.events,
            completion_through=datetime.fromisoformat(part.completion_through),
            coverage=coverage,
        )
        if isinstance(params, JourneyCompleted) and observation.status != "complete":
            continue
        indexes.append(index)
        if isinstance(params, JourneyRead):
            cells.append(
                dropped_before(assignment, step=params.to_step, policy=part.policy)
                if params.field == "dropout"
                else Defined(observation.status)
                if params.field == "status"
                else {
                    "started_at": observation.started_at,
                    "completed_at": observation.completed_at,
                    "duration": observation.duration,
                    "observed_duration": observation.observed_duration,
                    "followup_until": observation.followup_until,
                }[params.field]
            )
    primary = source.primary.select(keys).take(pa.array(indexes, type=pa.int64()))
    selected_keys = {tuple(row[key] for key in keys) for row in primary.to_pylist()}
    parts = tuple(
        ExchangePart(
            p.role,
            p.table.filter(
                pa.array(
                    [
                        tuple(row[key] for key in keys) in selected_keys
                        for row in p.table.to_pylist()
                    ],
                    type=pa.bool_(),
                )
            ),
        )
        for p in source.parts
    )
    reasons: dict[str, set[str]] = {}
    state = None
    state_kind = "journey_assignment"
    if isinstance(params, JourneyRead):
        values: list[object] = []
        tags: list[str] = []
        explanations: list[str | None] = []
        for cell in cells:
            tags.append(type(cell).__name__.lower())
            values.append(cell.value if isinstance(cell, Defined) else None)
            reason = None if isinstance(cell, Defined) else cell.reason
            explanations.append(reason)
            if reason is not None:
                reasons.setdefault(type(cell).__name__.lower(), set()).add(reason)
        primary = primary.append_column(
            "value", pa.array(values, type=arrow_scalar_type(node.value_type))
        )
        primary = primary.append_column("cell_tag", pa.array(tags, type=pa.string()))
        primary = primary.append_column("cell_reason", pa.array(explanations, type=pa.string()))
        state_kind = "none"
    else:
        state = primary.append_column(
            "status", pa.array(["accepted"] * primary.num_rows, type=pa.string())
        )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        cell_reasons=tuple((tag, tuple(sorted(values))) for tag, values in sorted(reasons.items())),
        state_kind=state_kind,
        state_schema=None if state is None else state.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=state)
