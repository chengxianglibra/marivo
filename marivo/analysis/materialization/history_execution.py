"""Canonical History exchange, publication validation and source-free recovery."""

from __future__ import annotations

import json
from datetime import datetime

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo._compat import UTC
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import HistoryPart
from marivo.analysis.core.rules import HistoryReplay, OccurrencePrepare
from marivo.analysis.materialization.domain_preparation import validate_exchange as validate_capture
from marivo.analysis.materialization.domain_preparation import validate_metadata
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.methods.domain_coverage import CoverageFact
from marivo.analysis.methods.history import (
    History,
    Occurrence,
    Transition,
    fragments,
    ordered,
    replay,
)

HISTORY: TypeAdapter[History] = TypeAdapter(History)


def prefix(facts: tuple[CoverageFact, ...], end: datetime) -> datetime | None:
    if not facts or any(
        item.source_origin is None
        or item.complete_from is not None
        or item.complete_through is None
        for item in facts
    ):
        return None
    return min(
        end,
        *(
            datetime.fromisoformat(item.complete_through)
            for item in facts
            if item.complete_through is not None
        ),
    ).astimezone(UTC)


def preparation(part: HistoryPart) -> OccurrencePrepare:
    capture = part.preparation
    return OccurrencePrepare(
        part.capture_domain,
        capture.events,
        capture.start,
        capture.end,
        capture.order,
        capture.model,
        capture.completeness,
        capture.order_use,
        capture.terminal_state,
    )


def execute(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    assert isinstance(node.parameters, HistoryReplay)
    members, captured = inputs
    validate_capture(captured.contract, captured.primary, captured.parts)
    part = next(item for item in node.signature.parts if isinstance(item, HistoryPart))
    model = part.preparation.model
    assert model is not None
    facts = validate_metadata(preparation(part), captured.primary.schema.metadata or {})
    start, end = datetime.fromisoformat(part.window_start), datetime.fromisoformat(part.window_end)
    known = prefix(facts, end)
    keys = members.contract.key_fields
    subject_names = tuple(f"subject__key_{i}" for i in range(len(keys)))
    occurrence_keys = captured.contract.key_fields
    mapping = next(item.table for item in captured.parts if item.role == "subject")
    images = {
        tuple(row[name] for name in occurrence_keys): tuple(row[name] for name in subject_names)
        for row in mapping.to_pylist()
    }
    groups: dict[tuple[str | int, ...], list[Occurrence]] = {}
    for row in next(
        item.table for item in captured.parts if item.role == "occurrences"
    ).to_pylist():
        check()
        identity = tuple(row[name] for name in occurrence_keys)
        point = row["occurrences__occurred_at"]
        value = row.get("occurrences__sequence_int", row.get("occurrences__sequence_enum"))
        groups.setdefault(images[identity], []).append(
            Occurrence(str(identity[0]), identity[1:], point, value)
        )
    histories = []
    for row in members.primary.to_pylist():
        check()
        subject = tuple(row[name] for name in keys)
        histories.append(
            replay(
                subject,
                tuple(groups.pop(subject, ())),
                model,
                start=start,
                end=end,
                known_through=known,
            )
        )
    if groups:
        fail(
            "history_binding",
            "captured participant escapes the original full Subject domain",
            stage="consume",
        )
    schema = pa.schema(
        [
            *(members.primary.schema.field(name) for name in keys),
            pa.field("classification", pa.string()),
            pa.field("inception_at", pa.timestamp("us", "UTC")),
            pa.field("known_through", pa.timestamp("us", "UTC")),
        ],
        metadata=captured.primary.schema.metadata,
    )
    primary = pa.Table.from_pylist(
        [
            {
                **dict(zip(keys, item.subject, strict=True)),
                "classification": item.classification,
                "inception_at": None if item.inception is None else item.inception.occurred_at,
                "known_through": item.known_through,
            }
            for item in histories
        ],
        schema=schema,
    )
    retained = pa.Table.from_arrays(
        [
            *(primary[name] for name in keys),
            pa.array([HISTORY.dump_json(item).decode() for item in histories], type=pa.string()),
        ],
        names=[*keys, "history__record"],
    )
    state = pa.Table.from_arrays(
        [*(primary[name] for name in keys), primary["classification"]], names=[*keys, "status"]
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        schema,
        keys,
        (PartContract("history", retained.schema, keys),),
        state_kind="canonical_history",
        state_schema=state.schema,
    )
    return from_arrow(
        primary, contract, parts=(ExchangePart("history", retained),), method_state=state
    )


def validate_record(item: History, part: HistoryPart, known: datetime | None) -> None:
    """Verify the saved trace and its projections without invoking replay."""
    model = part.preparation.model
    assert model is not None
    definition = model.definition
    start, end = datetime.fromisoformat(part.window_start), datetime.fromisoformat(part.window_end)
    events = {event.ref.path: event for event in model.triggers}
    inceptions = {entry.trigger.event_ref for entry in definition.inceptions}
    initial = next(state.name for state in definition.states if state.initial)
    terminals = {state.name for state in definition.states if state.terminal}
    rules = {
        (entry.from_state, entry.trigger.event_ref): entry.to_state
        for entry in definition.transitions
    }
    state: str | None = None
    inception = None
    legal: list[Transition] = []
    entries: list[tuple[str, Occurrence]] = []
    identities = set()
    sequences = set()
    declarations = (
        {}
        if model.order is None
        else {entry.event_ref: entry for entry in model.order.definition.sequences}
    )
    previous_time = None
    for evaluation in item.evaluations:
        check()
        occurrence = evaluation.occurrence
        event = events.get(occurrence.event)
        identity = (occurrence.event, occurrence.key)
        if (
            event is None
            or identity in identities
            or len(occurrence.key) != len(event.identity)
            or any(
                type(value) is not (str if field.logical_type == "string" else int)
                or value == ""
                or (type(value) is int and not -(2**63) <= value < 2**63)
                for value, field in zip(occurrence.key, event.identity, strict=True)
            )
            or occurrence.occurred_at.utcoffset() is None
            or occurrence.occurred_at >= end
            or (previous_time is not None and occurrence.occurred_at < previous_time)
        ):
            fail(
                "history_trace",
                "invalid exact trigger identity/time or trace ordering",
                stage="recovery",
            )
        identities.add(identity)
        previous_time = occurrence.occurred_at
        if occurrence.event not in declarations and occurrence.sequence is not None:
            fail(
                "business_order",
                "retained sequence has no frozen business authority",
                stage="recovery",
            )
        if occurrence.sequence is not None:
            if occurrence.sequence in sequences:
                fail("business_order", "duplicate retained Subject sequence", stage="recovery")
            sequences.add(occurrence.sequence)
        uncertain = known is None or (occurrence.occurred_at >= known and state not in terminals)
        if uncertain:
            state = None
        if evaluation.before != state:
            fail("history_trace", "state-before contradicts canonical prefix", stage="recovery")
        if uncertain:
            disposition, after = "unknown_origin" if known is None else "unknown_followup", None
        elif state is None:
            disposition = "inception" if occurrence.event in inceptions else "pre_inception"
            after = initial if disposition == "inception" else None
            if after is not None:
                inception = occurrence
                entries.append((after, occurrence))
        elif state in terminals:
            disposition, after = "transition_from_terminal", state
        elif occurrence.event in inceptions:
            disposition, after = "illegal_transition", state
        elif (state, occurrence.event) in rules:
            disposition, after = "legal_transition", rules[state, occurrence.event]
            legal.append(Transition(len(legal) + 1, occurrence, state, after))
            entries.append((after, occurrence))
        else:
            disposition, after = "illegal_transition", state
        if evaluation.after != after or evaluation.disposition != disposition:
            fail(
                "history_trace",
                "saved trigger disposition/state contradicts the frozen StateModel",
                stage="recovery",
            )
        state = after
    from itertools import groupby

    for _point, grouped in groupby(item.evaluations, lambda entry: entry.occurrence.occurred_at):
        group = tuple(grouped)
        expected, ambiguous = ordered(
            tuple(entry.occurrence for entry in group),
            model,
            terminal=group[0].before in terminals,
        )
        if expected != tuple(entry.occurrence for entry in group) or any(
            entry.terminal_set != ambiguous for entry in group
        ):
            fail(
                "business_order",
                "retained business order/set discriminator differs",
                stage="recovery",
            )
    trusted = (
        inception
        if inception is not None and known is not None and inception.occurred_at < known
        else None
    )
    classification = (
        "seeded"
        if known == end and trusted is not None
        else "not_started"
        if known == end
        else "coverage_censored"
    )
    if known == end and item.evaluations and inception is None:
        fail("history_inception", "complete retained origin lacks inception", stage="recovery")
    if (
        item.known_through != known
        or item.inception != trusted
        or item.classification != classification
        or item.transitions != tuple(legal)
        or item.violations
        != tuple(
            entry
            for entry in item.evaluations
            if entry.disposition in ("illegal_transition", "transition_from_terminal")
        )
        or item.pre_inception
        != tuple(
            entry.occurrence for entry in item.evaluations if entry.disposition == "pre_inception"
        )
        or item.intervals != fragments(tuple(entries), start=start, end=end, known_through=known)
    ):
        fail(
            "history_parts",
            "classification/coverage/transition/violation/interval projection contradicts retained canonical trace",
            stage="recovery",
        )


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    part = next((item for item in contract.signature.parts if isinstance(item, HistoryPart)), None)
    if part is None:
        fail("required_parts", "missing canonical History declaration", stage="recovery")
    facts = validate_metadata(preparation(part), primary.schema.metadata or {})
    known = prefix(facts, datetime.fromisoformat(part.window_end))
    retained = next((item.table for item in parts if item.role == "history"), None)
    if retained is None:
        fail("required_parts", "missing canonical History rows", stage="recovery")
    keys = contract.key_fields
    ledger = {tuple(row[name] for name in keys): row for row in primary.to_pylist()}
    identities = set()
    for row in retained.to_pylist():
        check()
        try:
            item = HISTORY.validate_json(row["history__record"], strict=True)
            if json.loads(HISTORY.dump_json(item)) != json.loads(row["history__record"]):
                fail(
                    "history_parts",
                    "retained encoding loses time/key precision or is not canonical",
                    stage="recovery",
                )
        except (KeyError, TypeError, ValidationError):
            fail("history_parts", "invalid closed History encoding", stage="recovery")
        key = tuple(row[name] for name in keys)
        if item.subject != key:
            fail(
                "history_binding",
                "History record belongs to another complete Subject",
                stage="recovery",
            )
        validate_record(item, part, known)
        for entry in item.evaluations:
            identity = (entry.occurrence.event, entry.occurrence.key)
            if identity in identities:
                fail(
                    "history_binding",
                    "one occurrence belongs to multiple Subjects",
                    stage="recovery",
                )
            identities.add(identity)
        expected = ledger[key]
        if (
            expected["classification"] != item.classification
            or expected["inception_at"]
            != (None if item.inception is None else item.inception.occurred_at)
            or expected["known_through"] != item.known_through
        ):
            fail(
                "history_parts",
                "primary Subject ledger contradicts canonical History",
                stage="recovery",
            )
