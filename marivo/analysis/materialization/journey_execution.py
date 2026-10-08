"""Canonical Journey consumption and retained assignment validation."""

from __future__ import annotations

from datetime import datetime

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import JourneyPart, OccurrencePart
from marivo.analysis.core.rules import JourneyMatch, OccurrencePrepare
from marivo.analysis.event import EveryStart, FirstPerSubject
from marivo.analysis.materialization.domain_preparation import validate_exchange as validate_capture
from marivo.analysis.materialization.domain_preparation import validate_rows
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.methods.domain_coverage import FACTS
from marivo.analysis.methods.journey_matching import (
    CoverageWindow,
    JourneyAssignment,
    OrderedOccurrence,
    match,
)

ASSIGNMENT: TypeAdapter[JourneyAssignment] = TypeAdapter(JourneyAssignment)


def execute(node: MethodNode, selected: ExchangeResult, binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, JourneyMatch)
    validate_capture(selected.contract, selected.primary, selected.parts)
    capture = next(
        part for part in selected.contract.signature.parts if isinstance(part, OccurrencePart)
    )
    occurrence = next(part.table for part in selected.parts if part.role == "occurrences")
    subject = next(part.table for part in selected.parts if part.role == "subject")
    keys = selected.contract.key_fields
    mappings = {tuple(row[key] for key in keys): row for row in subject.to_pylist()}
    rows = [{**row, **mappings[tuple(row[key] for key in keys)]} for row in occurrence.to_pylist()]
    schema = pa.schema((*occurrence.schema, *(f for f in subject.schema if f.name not in keys)))
    table = pa.Table.from_pylist(rows, schema=schema)
    preparation = OccurrencePrepare(
        selected.contract.signature.domain,
        capture.events,
        capture.start,
        capture.end,
        capture.order,
        capture.model,
        capture.completeness,
        capture.order_use,
        capture.terminal_state,
    )
    order = validate_rows(preparation, table)
    subject_names = tuple(
        f"subject__key_{i}" for i in range(len(capture.events[0].subject.primary_key))
    )
    ordered: list[OrderedOccurrence] = []
    for ordinal, index in enumerate(order):
        check()
        row = rows[index]
        point = row["occurrences__occurred_at"]
        if not isinstance(point, datetime):
            fail("journey_input", "captured timestamp is not an instant", stage="consume")
        ordered.append(
            OrderedOccurrence(
                str(row[keys[0]]),
                tuple(row[key] for key in keys[1:]),
                tuple(row[key] for key in subject_names),
                point,
                ordinal,
            )
        )
    facts = FACTS.validate_json((selected.primary.schema.metadata or {})[b"r7.coverage"])
    assignments = match(
        tuple(ordered),
        events=params.events,
        policy=FirstPerSubject()
        if params.policy == "first_per_subject"
        else EveryStart(completion_assignment=params.policy),
        cohort_start=datetime.fromisoformat(params.cohort_start),
        cohort_end=datetime.fromisoformat(params.cohort_end),
        completion_through=datetime.fromisoformat(params.completion_through),
        coverage=tuple(
            CoverageWindow(
                fact.event,
                None if fact.complete_from is None else datetime.fromisoformat(fact.complete_from),
                datetime.fromisoformat(fact.complete_through),
            )
            for fact in facts
            if fact.complete_through is not None
            and (fact.complete_from is not None or fact.source_origin is not None)
        ),
    )
    key_fields = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    expected_width = len(subject_names) + len(keys)
    if len(key_fields) != expected_width:
        fail(
            "journey_binding",
            "Journey key must contain Subject and complete start occurrence",
            stage="consume",
        )
    key_schema = pa.schema(
        [
            *(
                pa.field(key_fields[i], subject.schema.field(name).type)
                for i, name in enumerate(subject_names)
            ),
            *(
                pa.field(key_fields[len(subject_names) + i], occurrence.schema.field(name).type)
                for i, name in enumerate(keys)
            ),
        ]
    )
    values = [(*item.subject, item.start.event, *item.start.key) for item in assignments]
    columns = [
        pa.array([row[i] for row in values], type=field.type) for i, field in enumerate(key_schema)
    ]
    primary = pa.Table.from_arrays(columns, schema=key_schema).replace_schema_metadata(
        selected.primary.schema.metadata
    )
    subject_part = pa.Table.from_arrays(
        [
            *columns,
            *(
                pa.array(
                    [item.subject[i] for item in assignments], type=subject.schema.field(name).type
                )
                for i, name in enumerate(subject_names)
            ),
        ],
        names=[*key_fields, *subject_names],
    )
    assignment_part = pa.Table.from_arrays(
        [
            *columns,
            pa.array(
                [ASSIGNMENT.dump_json(item).decode() for item in assignments], type=pa.string()
            ),
        ],
        names=[*key_fields, "journey__assignment"],
    )
    parts = (ExchangePart("subject", subject_part), ExchangePart("journey", assignment_part))
    state = pa.Table.from_arrays(
        [*columns, pa.array(["accepted"] * len(assignments), type=pa.string())],
        names=[*key_fields, "status"],
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        key_fields,
        tuple(PartContract(part.role, part.table.schema, key_fields) for part in parts),
        state_kind="journey_assignment",
        state_schema=state.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=state, validate=False)


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declaration = next(
        (part for part in contract.signature.parts if isinstance(part, JourneyPart)), None
    )
    if declaration is None:
        fail("required_parts", "missing Journey declaration", stage="recovery")
    assignments = next(part.table for part in parts if part.role == "journey")
    subjects = next(part.table for part in parts if part.role == "subject")
    mappings = {tuple(row[key] for key in contract.key_fields): row for row in subjects.to_pylist()}
    items: list[JourneyAssignment] = []
    for row in assignments.to_pylist():
        check()
        try:
            item = ASSIGNMENT.validate_json(row["journey__assignment"], strict=True)
        except ValidationError:
            fail("journey_assignment", "invalid retained assignment encoding", stage="recovery")
        key = tuple(row[name] for name in contract.key_fields)
        if (
            key != (*item.subject, item.start.event, *item.start.key)
            or len(item.steps) != len(declaration.steps)
            or len(item.reach) != len(item.steps)
        ):
            fail(
                "journey_binding",
                "retained Journey identity or step count differs",
                stage="recovery",
            )
        if (
            tuple(mappings[key][f"subject__key_{i}"] for i in range(len(item.subject)))
            != item.subject
        ):
            fail("journey_binding", "retained Subject image differs", stage="recovery")
        items.append(item)
    validate_assignments(tuple(items), declaration)


def validate_assignments(items: tuple[JourneyAssignment, ...], declaration: JourneyPart) -> None:
    """Verify retained assignment structure without replaying matching or opening sources."""
    observed: dict[tuple[str, tuple[str | int, ...]], OrderedOccurrence] = {}
    finals: set[tuple[str, tuple[str | int, ...]]] = set()
    seen_subjects: set[tuple[str | int, ...]] = set()
    through = datetime.fromisoformat(declaration.completion_through)
    for item in items:
        check()
        if declaration.policy == "first_per_subject" and item.subject in seen_subjects:
            fail("journey_assignment", "multiple first-per-subject attempts", stage="recovery")
        seen_subjects.add(item.subject)
        if len(item.steps) != len(declaration.steps) or len(item.reach) != len(item.steps):
            fail("journey_binding", "retained step count differs", stage="recovery")
        previous = None
        missing = None
        for event, step, reach in zip(declaration.events, item.steps, item.reach, strict=True):
            if (step is not None) != (reach == "reached") or (
                missing is not None and reach != missing
            ):
                fail(
                    "journey_assignment", "retained reach differs from assignment", stage="recovery"
                )
            if step is not None:
                if (
                    step.event != event
                    or step.subject != item.subject
                    or not step.key
                    or step.instant.utcoffset() is None
                    or step.instant >= through
                    or any(type(value) not in (str, int) or value == "" for value in step.key)
                    or (
                        previous is not None
                        and (step.ordinal <= previous.ordinal or step.instant < previous.instant)
                    )
                ):
                    fail(
                        "journey_assignment",
                        "retained step binding or order differs",
                        stage="recovery",
                    )
                identity = (step.event, step.key)
                if identity in observed and observed[identity] != step:
                    fail("journey_assignment", "inconsistent reused occurrence", stage="recovery")
                observed[identity] = step
                previous = step
            else:
                missing = reach
        if item.steps[0] != item.start or not datetime.fromisoformat(
            declaration.cohort_start
        ) <= item.start.instant < datetime.fromisoformat(declaration.cohort_end):
            fail("journey_assignment", "retained start differs from cohort", stage="recovery")
        final = item.steps[-1]
        if declaration.policy == "exclusive" and final is not None:
            identity = (final.event, final.key)
            if identity in finals:
                fail("journey_assignment", "exclusive completion reused", stage="recovery")
            finals.add(identity)
