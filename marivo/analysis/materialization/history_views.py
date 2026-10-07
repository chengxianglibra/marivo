"""Registered History projections with receipt-bound, source-free validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import ClassVar, Literal

import pyarrow as pa
from pydantic import ConfigDict, TypeAdapter, ValidationError

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.history_types import (
    Distribution,
    Dwell,
    Intervals,
    StateAt,
    Transitions,
    Violations,
)
from marivo.analysis.core.model import DerivedQuantity, HistoryViewPart
from marivo.analysis.core.rules import HistoryAxesPrepare, HistoryRead, HistoryView
from marivo.analysis.materialization.domain_preparation import validate_metadata
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.history_execution import (
    HISTORY,
    prefix,
    preparation,
    validate_record,
)
from marivo.analysis.methods.history import History, Key, ticks
from marivo.analysis.methods.registry import REGISTRY


@dataclass(frozen=True, slots=True)
class AxisRow:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    subject: Key
    checkpoint: datetime
    values: tuple[str | int | None, ...]


@dataclass(frozen=True, slots=True)
class ViewState:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    subject_types: tuple[Literal["string", "int64"], ...]
    histories: tuple[History, ...]
    metadata: tuple[tuple[str, str], ...]
    axes: tuple[AxisRow, ...] = ()


@dataclass(frozen=True, slots=True)
class AxisState:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    subject_types: tuple[Literal["string", "int64"], ...]
    subjects: tuple[Key, ...]
    axes: tuple[AxisRow, ...]
    authority: str


AXIS_STATE: TypeAdapter[AxisState] = TypeAdapter(AxisState)


def axes_load(parts: tuple[ExchangePart, ...]) -> AxisState:
    table = next((p.table for p in parts if p.role == "history_view"), None)
    if table is None or table.num_rows != 1 or table.column_names != ["history_view__retained"]:
        fail("required_parts", "restore exact checkpoint axis state", stage="recovery")
    encoded = table["history_view__retained"][0].as_py()
    try:
        value = AXIS_STATE.validate_json(encoded, strict=True)
    except (ValidationError, ValueError, TypeError):
        fail("history_axes", "invalid closed checkpoint state", stage="recovery")
    if AXIS_STATE.dump_json(value).decode() != encoded:
        fail("history_axes", "noncanonical checkpoint state", stage="recovery")
    return value


def axes_result(node: MethodNode, primary: pa.Table, binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, HistoryAxesPrepare)
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    rows = primary.to_pylist()
    subjects = tuple(tuple(row[k] for k in keys) for row in rows)
    state = AxisState(
        tuple(
            "string" if pa.types.is_string(primary.schema.field(k).type) else "int64" for k in keys
        ),
        subjects,
        tuple(
            AxisRow(
                subject,
                datetime.fromisoformat(at),
                tuple(row[f"axis_{point}_{axis}"] for axis in range(len(params.request.axes))),
            )
            for row, subject in zip(rows, subjects, strict=True)
            for point, at in enumerate(params.request.at)
        ),
        (primary.schema.metadata or {})[b"r7.capture_authority"].decode(),
    )
    retained = ExchangePart(
        "history_view", pa.table({"history_view__retained": [AXIS_STATE.dump_json(state).decode()]})
    )
    statuses = primary.select(keys).append_column(
        "status", pa.array(["accepted"] * primary.num_rows, type=pa.string())
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        (PartContract("history_view", retained.table.schema, ()),),
        (),
        "history_view",
        statuses.schema,
    )
    return from_arrow(primary, contract, parts=(retained,), method_state=statuses, validate=False)


STATE: TypeAdapter[ViewState] = TypeAdapter(ViewState)
AXIS: TypeAdapter[tuple[str | int | None, ...]] = TypeAdapter(tuple[str | int | None, ...])


def load(parts: tuple[ExchangePart, ...]) -> ViewState:
    table = next((p.table for p in parts if p.role == "history_view"), None)
    if table is None or table.num_rows != 1:
        fail("required_parts", "restore the exact History view state", stage="recovery")
    encoded = table["history_view__retained"][0].as_py()
    try:
        state = STATE.validate_json(encoded, strict=True)
    except (ValidationError, ValueError, TypeError):
        fail("history_view", "invalid closed History view state", stage="recovery")
    if STATE.dump_json(state).decode() != encoded:
        fail("history_view", "noncanonical History view state", stage="recovery")
    return state


def truth(history: History, at: datetime, end: datetime, terminals: set[str]) -> str | None:
    known = history.known_through
    state = None
    for evaluation in history.evaluations:
        check()
        point = evaluation.occurrence.occurred_at
        if point > at or point == end:
            break
        if evaluation.after is not None:
            state = evaluation.after
    if known is None or (at >= known and not (at == end == known) and state not in terminals):
        return None
    return state or "__not_started__"


def checked(value: int) -> int:
    if not -(2**63) <= value < 2**63:
        fail("duration_overflow", "stored count or Duration ticks exceed int64", stage="consume")
    return value


def quantile(values: list[int], q: Fraction) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = position.numerator // position.denominator
    upper = min(lower + 1, len(ordered) - 1)
    return checked(round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)))


def table(part: HistoryViewPart, state: ViewState, keys: tuple[str, ...]) -> pa.Table:
    request = part.request
    model = part.history.preparation.model
    assert model is not None
    states = tuple(s.name for s in model.definition.states)
    terminals = {s.name for s in model.definition.states if s.terminal}
    start, end = (
        datetime.fromisoformat(part.history.window_start),
        datetime.fromisoformat(part.history.window_end),
    )
    rows: list[dict[str, object]] = []
    fields: list[pa.Field] = []
    subject_types = tuple(pa.string() if t == "string" else pa.int64() for t in state.subject_types)
    if isinstance(request, StateAt):
        fields = [
            *(pa.field(k, t) for k, t in zip(keys, subject_types, strict=True)),
            pa.field("value", pa.bool_()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
        for h in state.histories:
            value = truth(h, datetime.fromisoformat(request.at), end, terminals)
            rows.append(
                {
                    **dict(zip(keys, h.subject, strict=True)),
                    "value": None if value is None else value == request.state,
                    "cell_tag": "unknown" if value is None else "defined",
                    "cell_reason": "insufficient_coverage" if value is None else None,
                }
            )
    elif isinstance(request, Distribution):
        fields = [
            pa.field(keys[0], pa.timestamp("us", "UTC")),
            pa.field(keys[1], pa.string()),
            *(pa.field(k, pa.string()) for k in keys[2:]),
            pa.field("checkpoint", pa.timestamp("us", "UTC")),
            pa.field("model_state", pa.string()),
            *(
                pa.field(
                    f"axis_{i}",
                    pa.string() if axis.dimension.logical_type == "string" else pa.int64(),
                )
                for i, axis in enumerate(request.axes)
            ),
            *(
                pa.field(name, pa.int64())
                for name in (
                    "known_state_count",
                    "seeded_subject_count",
                    "coverage_censored_count",
                    "not_started_count",
                )
            ),
            pa.field("share_among_seeded", pa.float64()),
            pa.field("share_among_seeded__cell_tag", pa.string()),
            pa.field("share_among_seeded__cell_reason", pa.string()),
        ]
        axes = {(r.subject, r.checkpoint): r.values for r in state.axes}
        for checkpoint in request.at:
            point = datetime.fromisoformat(checkpoint)
            groups: dict[tuple[str | int | None, ...], list[str | None]] = (
                {(): []} if not request.axes else {}
            )
            for h in state.histories:
                coordinate = axes[h.subject, point] if request.axes else ()
                groups.setdefault(coordinate, []).append(truth(h, point, end, terminals))
            for coordinate in sorted(groups, key=lambda v: AXIS.dump_json(v)):
                statuses = groups[coordinate]
                seeded = checked(sum(v in states for v in statuses))
                unknown = checked(sum(v is None for v in statuses))
                for name in states:
                    count = checked(statuses.count(name))
                    rows.append(
                        {
                            keys[0]: point,
                            keys[1]: name,
                            **{
                                k: AXIS.dump_json((v,)).decode()
                                for k, v in zip(keys[2:], coordinate, strict=True)
                            },
                            "checkpoint": point,
                            "model_state": name,
                            **{f"axis_{i}": v for i, v in enumerate(coordinate)},
                            "known_state_count": count,
                            "seeded_subject_count": seeded,
                            "coverage_censored_count": unknown,
                            "not_started_count": statuses.count("__not_started__"),
                            "share_among_seeded": float(Fraction(count, seeded))
                            if seeded
                            else None,
                            "share_among_seeded__cell_tag": "defined" if seeded else "undefined",
                            "share_among_seeded__cell_reason": None
                            if seeded
                            else "zero_denominator",
                        }
                    )
    elif isinstance(request, Transitions):
        pairs = tuple(
            dict.fromkeys((t.from_state, t.to_state) for t in model.definition.transitions)
        )
        trace = [
            (t.from_state, t.to_state)
            for h in state.histories
            for t in h.transitions
            if start <= t.occurrence.occurred_at < end
        ]
        denominator = checked(len(trace))
        fields = [pa.field(k, pa.string()) for k in keys] + [
            pa.field("from_state", pa.string()),
            pa.field("to_state", pa.string()),
            pa.field("count", pa.int64()),
            pa.field("share_of_modeled_transitions", pa.float64()),
            pa.field("share_of_modeled_transitions__cell_tag", pa.string()),
            pa.field("share_of_modeled_transitions__cell_reason", pa.string()),
        ]
        for pair in pairs:
            count = checked(trace.count(pair))
            rows.append(
                {
                    **dict(zip(keys, pair, strict=True)),
                    "from_state": pair[0],
                    "to_state": pair[1],
                    "count": count,
                    "share_of_modeled_transitions": float(Fraction(count, denominator))
                    if denominator
                    else None,
                    "share_of_modeled_transitions__cell_tag": "defined"
                    if denominator
                    else "undefined",
                    "share_of_modeled_transitions__cell_reason": None
                    if denominator
                    else "zero_denominator",
                }
            )
    elif isinstance(request, Dwell):
        counts = (
            "interval_count",
            "completed_count",
            "right_censored_count",
            "coverage_censored_count",
            "left_clipped_completed_count",
        )
        durations = ("mean_duration", "median_duration", "p90_duration")
        fields = [
            pa.field(keys[0], pa.string()),
            pa.field("model_state", pa.string()),
            *(pa.field(n, pa.int64()) for n in counts),
            *(
                field
                for n in durations
                for field in (
                    pa.field(n, pa.duration("us")),
                    pa.field(n + "__cell_tag", pa.string()),
                    pa.field(n + "__cell_reason", pa.string()),
                )
            ),
        ]
        for name in states:
            intervals = [i for h in state.histories for i in h.intervals if i.state == name]
            completed = [i for i in intervals if i.status == "completed"]
            values = [ticks(i.start, i.end) for i in completed]
            total = checked(sum(values))
            result = {
                keys[0]: name,
                "model_state": name,
                "interval_count": checked(len(intervals)),
                "completed_count": checked(len(values)),
                "right_censored_count": checked(
                    sum(i.status == "right_censored" for i in intervals)
                ),
                "coverage_censored_count": checked(
                    sum(i.status == "coverage_censored" for i in intervals)
                ),
                "left_clipped_completed_count": checked(sum(i.left_clipped for i in completed)),
            }
            for field, duration_value in zip(
                durations,
                (
                    round(Fraction(total, len(values))) if values else None,
                    quantile(values, Fraction(1, 2)),
                    quantile(values, Fraction(9, 10)),
                ),
                strict=True,
            ):
                result[field] = duration_value
                result[field + "__cell_tag"] = "defined" if values else "undefined"
                result[field + "__cell_reason"] = None if values else "empty_completed_set"
            rows.append(result)
    else:
        key_types = (
            (*subject_types, pa.int64())
            if isinstance(request, Intervals)
            else (
                pa.string(),
                *(
                    pa.string() if f.logical_type == "string" else pa.int64()
                    for f in model.triggers[0].identity
                ),
            )
        )
        fields = [
            *(pa.field(k, t) for k, t in zip(keys, key_types, strict=True)),
            *(pa.field(f"subject__key_{i}", t) for i, t in enumerate(subject_types)),
        ]
        if isinstance(request, Intervals):
            fields += [
                pa.field("state", pa.string()),
                pa.field("start", pa.timestamp("us", "UTC")),
                pa.field("end", pa.timestamp("us", "UTC")),
                pa.field("observed_duration", pa.duration("us")),
                pa.field("observed_duration__cell_tag", pa.string()),
                pa.field("observed_duration__cell_reason", pa.string()),
                pa.field("left_clipped", pa.bool_()),
                pa.field("status", pa.string()),
            ]
            for h in state.histories:
                for i in h.intervals:
                    rows.append(
                        {
                            **dict(zip(keys, (*h.subject, i.ordinal), strict=True)),
                            **{f"subject__key_{j}": v for j, v in enumerate(h.subject)},
                            "state": i.state,
                            "start": i.start,
                            "end": i.end,
                            "observed_duration": i.observed_ticks,
                            "observed_duration__cell_tag": "unknown"
                            if i.observed_ticks is None
                            else "defined",
                            "observed_duration__cell_reason": "insufficient_coverage"
                            if i.observed_ticks is None
                            else None,
                            "left_clipped": i.left_clipped,
                            "status": i.status,
                        }
                    )
        else:
            assert isinstance(request, Violations)
            fields += [
                pa.field("trigger", pa.string()),
                pa.field("occurred_at", pa.timestamp("us", "UTC")),
                pa.field("state_at_event", pa.string()),
                pa.field("kind", pa.string()),
            ]
            for h in state.histories:
                for e in h.evaluations:
                    if (
                        e.disposition not in ("illegal_transition", "transition_from_terminal")
                        or not start <= e.occurrence.occurred_at < end
                    ):
                        continue
                    rows.append(
                        {
                            **dict(zip(keys, (e.occurrence.event, *e.occurrence.key), strict=True)),
                            **{f"subject__key_{j}": v for j, v in enumerate(h.subject)},
                            "trigger": e.occurrence.event,
                            "occurred_at": e.occurrence.occurred_at,
                            "state_at_event": e.before,
                            "kind": e.disposition,
                        }
                    )
    check()
    return pa.Table.from_pylist(
        rows, schema=pa.schema(fields, metadata={k.encode(): v.encode() for k, v in state.metadata})
    )


def execute(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, (HistoryView, HistoryRead))
    part = next(p for p in node.signature.parts if isinstance(p, HistoryViewPart))
    source = inputs[0]
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    if isinstance(params, HistoryView):
        histories = tuple(
            HISTORY.validate_json(row["history__record"], strict=True)
            for row in next(p.table for p in source.parts if p.role == "history").to_pylist()
        )
        state = ViewState(
            tuple(
                "string" if pa.types.is_string(source.primary.schema.field(k).type) else "int64"
                for k in source.contract.key_fields
            ),
            histories,
            tuple(
                sorted(
                    (k.decode(), v.decode())
                    for k, v in (source.primary.schema.metadata or {}).items()
                )
            ),
            axes_load(inputs[1].parts).axes if len(inputs) == 2 else (),
        )
        if len(inputs) == 2:
            axes = axes_load(inputs[1].parts)
            if (
                set(axes.subjects) != {h.subject for h in histories}
                or axes.subject_types != state.subject_types
                or dict(state.metadata).get("r7.capture_authority") != axes.authority
            ):
                fail(
                    "history_axes",
                    "checkpoint preparation differs from full History source authority",
                    stage="consume",
                )
        primary = table(part, state, keys)
    else:
        state = load(source.parts)
        primary = source.primary.select(keys)
        field = params.field
        primary = primary.append_column("value", source.primary[field])
        tag = field + "__cell_tag"
        reason = field + "__cell_reason"
        primary = primary.append_column(
            "cell_tag",
            source.primary[tag]
            if tag in source.primary.column_names
            else pa.array(["defined"] * primary.num_rows, type=pa.string()),
        )
        primary = primary.append_column(
            "cell_reason",
            source.primary[reason]
            if reason in source.primary.column_names
            else pa.nulls(primary.num_rows, type=pa.string()),
        )
    retained = ExchangePart(
        "history_view", pa.table({"history_view__retained": [STATE.dump_json(state).decode()]})
    )
    parts: tuple[ExchangePart, ...] = (retained,)
    if any(p.role == "subject" for p in source.parts) and isinstance(params, HistoryRead):
        parts += tuple(p for p in source.parts if p.role == "subject")
    elif isinstance(part.request, (StateAt, Intervals, Violations)):
        subjects = pa.Table.from_arrays(
            [
                *(primary[k] for k in keys),
                *(
                    pa.array(
                        [row.subject[i] for row in state.histories],
                        type=primary.schema.field(keys[i]).type,
                    )
                    if isinstance(part.request, StateAt)
                    else primary[f"subject__key_{i}"]
                    for i in range(len(state.subject_types))
                ),
            ],
            names=[
                *keys,
                *(f"subject__key_{i}" for i in range(len(state.subject_types))),
            ],
        )
        parts += (ExchangePart("subject", subjects),)
    kind = REGISTRY.lookup(node.method).semantics.persistent_state_kind
    statuses = (
        None
        if kind == "none"
        else pa.Table.from_arrays(
            [
                *(primary[k] for k in keys),
                pa.array(["accepted"] * primary.num_rows, type=pa.string()),
            ],
            names=[*keys, "status"],
        )
    )
    return from_arrow(
        primary,
        ExchangeContract(
            node.signature,
            node.method,
            binding,
            primary.schema,
            keys,
            tuple(
                PartContract(p.role, p.table.schema, () if p.role == "history_view" else keys)
                for p in parts
            ),
            (
                ("undefined", ("empty_completed_set", "zero_denominator")),
                ("unknown", ("insufficient_coverage",)),
            ),
            kind or "none",
            None if statuses is None else statuses.schema,
        ),
        parts=parts,
        method_state=statuses,
        validate=False,
    )


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    part = next(p for p in contract.signature.parts if isinstance(p, HistoryViewPart))
    if contract.method.name == "history.axes":
        state_axes = axes_load(parts)
        rows = primary.to_pylist()
        axis_subjects = tuple(tuple(row[k] for k in contract.key_fields) for row in rows)
        request = part.request
        assert isinstance(request, Distribution)
        expected_axes = tuple(
            AxisRow(
                subject,
                datetime.fromisoformat(at),
                tuple(row[f"axis_{point}_{axis}"] for axis in range(len(request.axes))),
            )
            for row, subject in zip(rows, axis_subjects, strict=True)
            for point, at in enumerate(request.at)
        )
        if (
            axis_subjects != state_axes.subjects
            or expected_axes != state_axes.axes
            or not state_axes.authority
        ):
            fail(
                "history_axes",
                "checkpoint preparation differs from retained values",
                stage="recovery",
            )
        return
    state = load(parts)
    metadata = {k.encode(): v.encode() for k, v in state.metadata}
    if (primary.schema.metadata or {}) != metadata:
        fail(
            "history_binding",
            "primary capture authority/precision differs from retained state",
            stage="recovery",
        )
    facts = validate_metadata(preparation(part.history), metadata)
    known = prefix(facts, datetime.fromisoformat(part.history.window_end))
    subjects = set()
    occurrences = set()
    for h in state.histories:
        check()
        validate_record(h, part.history, known)
        if h.subject in subjects:
            fail("history_binding", "duplicate retained Subject", stage="recovery")
        if len(h.subject) != len(state.subject_types) or any(
            type(value) is not (str if kind == "string" else int)
            for value, kind in zip(h.subject, state.subject_types, strict=True)
        ):
            fail(
                "history_binding",
                "retained Subject keys differ from exact declared types",
                stage="recovery",
            )
        subjects.add(h.subject)
        for e in h.evaluations:
            identity = (e.occurrence.event, e.occurrence.key)
            if identity in occurrences:
                fail("history_binding", "duplicate retained occurrence", stage="recovery")
            occurrences.add(identity)
    request = part.request
    if isinstance(request, Distribution) and request.axes:
        expected = {
            (h.subject, datetime.fromisoformat(at)) for h in state.histories for at in request.at
        }
        actual = {(a.subject, a.checkpoint) for a in state.axes}
        if (
            actual != expected
            or len(actual) != len(state.axes)
            or any(
                len(a.values) != len(request.axes)
                or any(
                    v is not None
                    and type(v) is not (str if axis.dimension.logical_type == "string" else int)
                    for v, axis in zip(a.values, request.axes, strict=True)
                )
                for a in state.axes
            )
        ):
            fail(
                "history_axes",
                "checkpoint values lack exact total Subject/checkpoint binding",
                stage="recovery",
            )
    elif state.axes:
        fail("history_axes", "unexpected checkpoint axes", stage="recovery")
    expected_table = table(part, state, contract.key_fields)
    expected_rows = {
        tuple(row[k] for k in contract.key_fields): row for row in expected_table.to_pylist()
    }
    actual_keys = {tuple(row[k] for k in contract.key_fields) for row in primary.to_pylist()}
    if part.complete and actual_keys != set(expected_rows):
        fail("history_view", "complete view omitted retained rows", stage="recovery")
    quantity = contract.signature.quantity
    field = (
        quantity.input_ids[1]
        if isinstance(quantity, DerivedQuantity) and quantity.method_version == "history.read@v1"
        else None
    )
    for row in primary.to_pylist():
        key = tuple(row[k] for k in contract.key_fields)
        original = expected_rows.get(key)
        if original is None:
            fail("history_view", "primary escapes retained full view domain", stage="recovery")
        expected_row = (
            original
            if field is None
            else {
                **{k: original[k] for k in contract.key_fields},
                "value": original[field],
                "cell_tag": original.get(field + "__cell_tag", "defined"),
                "cell_reason": original.get(field + "__cell_reason"),
            }
        )
        if row != expected_row:
            fail(
                "history_view", "primary differs from canonical trace/statistics", stage="recovery"
            )

    subject_table = next((p.table for p in parts if p.role == "subject"), None)
    if subject_table is not None:
        expected_subjects = {}
        for h in state.histories:
            for row_identity in (
                (h.subject,)
                if isinstance(request, StateAt)
                else tuple((*h.subject, i.ordinal) for i in h.intervals)
                if isinstance(request, Intervals)
                else tuple(
                    (e.occurrence.event, *e.occurrence.key)
                    for e in h.evaluations
                    if e.disposition in ("illegal_transition", "transition_from_terminal")
                    and datetime.fromisoformat(part.history.window_start)
                    <= e.occurrence.occurred_at
                    < datetime.fromisoformat(part.history.window_end)
                )
            ):
                expected_subjects[row_identity] = h.subject
        for row in subject_table.to_pylist():
            key = tuple(row[k] for k in contract.key_fields)
            if tuple(
                row[f"subject__key_{i}"] for i in range(len(state.subject_types))
            ) != expected_subjects.get(key):
                fail(
                    "history_binding",
                    "Subject mapping differs from retained canonical History",
                    stage="recovery",
                )
