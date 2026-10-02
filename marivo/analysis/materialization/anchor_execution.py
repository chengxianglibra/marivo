"""Retained Anchor starts and precise per-instance recovery validation."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

import ibis.expr.datatypes as dt
import pyarrow as pa

from marivo.analysis.anchors import deadline
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    AnchorDomainPart,
    AnchorObservationPart,
    OriginalStatePart,
    SubjectPart,
    require_part,
)
from marivo.analysis.core.rules import AnchorBind
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.journey_execution import ASSIGNMENT
from marivo.analysis.methods.comparison import roundoff
from marivo.analysis.methods.numeric_state import Number
from marivo.analysis.methods.physical import ValueType, arrow_scalar_type


def bind(
    node: MethodNode, selected: ExchangeResult, binding: str, captured: ExchangeResult | None = None
) -> ExchangeResult:
    params = node.parameters
    assert isinstance(params, AnchorBind)
    declaration = require_part(node.signature, "anchor")
    assert isinstance(declaration, AnchorDomainPart)
    assignments = next(p.table for p in selected.parts if p.role == "journey")
    values = [
        ASSIGNMENT.validate_json(row["journey__assignment"], strict=True)
        for row in assignments.to_pylist()
    ]
    start, end = (
        datetime.fromisoformat(params.during_start),
        datetime.fromisoformat(params.during_end),
    )
    values = [item for item in values if start <= item.start.instant < end]
    keys = selected.contract.key_fields
    schemas = [selected.primary.schema.field(k) for k in keys]
    rows = [
        {**dict(zip(keys, (*item.subject, item.start.event, *item.start.key), strict=True))}
        for item in values
    ]
    primary = pa.Table.from_pylist(rows, schema=pa.schema(schemas)).replace_schema_metadata(
        selected.primary.schema.metadata
    )
    mapping = require_part(node.signature, "subject")
    assert isinstance(mapping, SubjectPart)
    subject = primary
    for i in range(len(mapping.subject_key)):
        subject = subject.append_column(f"subject__key_{i}", primary[keys[i]])
    anchor = primary.append_column(
        "anchor__started_at",
        pa.array([item.start.instant for item in values], type=pa.timestamp("us", tz="UTC")),
    )
    anchor = anchor.append_column(
        "anchor__sequence_int", pa.array([None] * len(values), type=pa.int64())
    )
    anchor = anchor.append_column(
        "anchor__sequence_enum", pa.array([None] * len(values), type=pa.string())
    )
    anchor = anchor.append_column(
        "anchor__assignment",
        pa.array([ASSIGNMENT.dump_json(item).decode() for item in values], type=pa.string()),
    )
    if captured is not None:
        occurrences = next(p.table for p in captured.parts if p.role == "occurrences")
        lookup = {
            tuple(row[k] for k in captured.contract.key_fields): row
            for row in occurrences.to_pylist()
        }
        for column in ("sequence_int", "sequence_enum"):
            anchor = anchor.set_column(
                anchor.schema.get_field_index("anchor__" + column),
                "anchor__" + column,
                pa.array(
                    [
                        lookup[(item.start.event, *item.start.key)].get("occurrences__" + column)
                        for item in values
                    ],
                    type=anchor.schema.field("anchor__" + column).type,
                ),
            )
    parts = (ExchangePart("subject", subject), ExchangePart("anchor", anchor))
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
    )
    return from_arrow(primary, contract, parts=parts)


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declaration = require_part(contract.signature, "anchor")
    observation = declaration if isinstance(declaration, AnchorObservationPart) else None
    domain = observation.domain if observation else declaration
    assert isinstance(domain, AnchorDomainPart)
    anchors = next(p.table for p in parts if p.role == "anchor")
    mapping = require_part(contract.signature, "subject")
    assert isinstance(mapping, SubjectPart)
    subjects = next(p.table for p in parts if p.role == "subject")
    mapped = {tuple(row[k] for k in contract.key_fields): row for row in subjects.to_pylist()}
    original = require_part(contract.signature, "original_state") if observation else None
    original_table = next((p.table for p in parts if p.role == "original_state"), None)
    states = (
        {tuple(row[k] for k in contract.key_fields): row for row in original_table.to_pylist()}
        if original_table is not None
        else {}
    )
    for row in anchors.to_pylist():
        check()
        key = tuple(row[k] for k in contract.key_fields)
        point = row["anchor__started_at"]
        if (
            not isinstance(point, datetime)
            or point.utcoffset() is None
            or not (
                datetime.fromisoformat(domain.during_start)
                <= point
                < datetime.fromisoformat(domain.during_end)
            )
        ):
            fail(
                "anchor_binding", "retained start escapes the bound during window", stage="recovery"
            )
        width = len(mapping.subject_key)
        if key[:width] != tuple(mapped[key][f"subject__key_{i}"] for i in range(width)):
            fail("anchor_binding", "retained full Subject mapping differs", stage="recovery")
        allowed = (
            {domain.journey.events[0]}
            if domain.journey is not None
            else {event.ref.path for event in domain.preparation.events}
        )
        if key[width] not in allowed:
            fail("anchor_binding", "retained Event definition differs", stage="recovery")
        if observation and row["anchor__deadline"] != deadline(point, observation.window):
            fail(
                "anchor_window",
                "retained deadline differs from the frozen relative window",
                stage="recovery",
            )
        if domain.journey is not None:
            assignment = ASSIGNMENT.validate_json(row["anchor__assignment"], strict=True)
            if (
                *assignment.subject,
                assignment.start.event,
                *assignment.start.key,
            ) != key or assignment.start.instant != point:
                fail(
                    "anchor_binding",
                    "retained Journey assignment differs from the Anchor",
                    stage="recovery",
                )
        if observation:
            assert isinstance(original, OriginalStatePart) and original_table is not None
            uses = tuple(row[f"anchor__uses_{i}"] for i in range(len(observation.component_roots)))
            for rows, root in zip(uses, observation.component_roots, strict=True):
                identities = [
                    tuple(item[name] for name in item if name.startswith("candidate__key_"))
                    for item in rows
                ]
                if any(
                    not identity or any(type(v) not in (int, str) or v == "" for v in identity)
                    for identity in identities
                ) or len(set(identities)) != len(identities):
                    fail(
                        "anchor_binding",
                        "duplicate or incomplete component use identity",
                        stage="recovery",
                    )
                if (
                    _selected(
                        row,
                        rows,
                        domain,
                        contract.key_fields,
                        width,
                        row["anchor__deadline"],
                        root.path,
                    )
                    != rows
                ):
                    fail(
                        "anchor_window",
                        "retained use escapes Subject/order/window or includes the Anchor itself",
                        stage="recovery",
                    )
            from marivo.analysis.materialization.graph_reference import physical_type

            try:
                components, _, _, _ = _numeric(
                    observation,
                    original,
                    uses,
                    original_table.schema,
                    physical_type(primary.schema.field("value").type),
                )
            except (ValueError, OverflowError) as error:
                fail("numeric_state", str(error), stage="recovery")
            for name, expected in components.items():
                actual = states[key]["original_state__" + name]
                if type(expected) is float and type(actual) is float:
                    absolute = name.removesuffix("sum") + "absolute_sum"
                    magnitude = components.get(absolute, abs(expected))
                    assert isinstance(magnitude, (int, float, Decimal))
                    equal = math.isfinite(actual) and abs(actual - expected) <= roundoff(
                        float(magnitude)
                    )
                else:
                    equal = actual == expected
                if not equal:
                    fail(
                        "anchor_binding",
                        "original component state differs from retained uses",
                        stage="recovery",
                    )


def _numeric(
    declaration: AnchorObservationPart,
    original: OriginalStatePart,
    uses: tuple[list[dict[str, object]], ...],
    schema: pa.Schema,
    output: ValueType,
) -> tuple[dict[str, Number], Number | None, str, str | None]:
    from marivo.analysis.compiler.anchors import component_names
    from marivo.analysis.methods.numeric_state import checked_sum, merge_original

    components: dict[str, Number] = {}
    for i, rows in enumerate(uses):
        check()
        magnitude, support = component_names(original, i, len(uses))
        active = [row["amount"] for row in rows if row["amount"] is not None]
        physical = schema.field("original_state__" + magnitude).type
        components[magnitude] = checked_sum(active, physical)
        if support != magnitude:
            components[support] = len(active)
        absolute = (
            magnitude.removesuffix("sum") + "absolute_sum"
            if magnitude != "count"
            else "absolute_sum"
        )
        if absolute in original.components:
            components[absolute] = checked_sum(
                [abs(value) for value in active if isinstance(value, (int, float, Decimal))],
                physical,
            )
    return merge_original(
        (components,),
        schema,
        original.components,
        original.method_version.removesuffix("@v1"),
        output,
        original.empty_rules,
    )


def _selected(
    anchor: dict[str, object],
    rows: list[dict[str, object]],
    domain: AnchorDomainPart,
    keys: tuple[str, ...],
    width: int,
    end: datetime,
    root: str,
) -> list[dict[str, object]]:
    point = anchor["anchor__started_at"]
    assert isinstance(point, datetime)
    subject = tuple(anchor[key] for key in keys[:width])
    order = domain.preparation.order
    selected = []
    for row in rows:
        check()
        member = tuple(row[name] for name in row if name.startswith("member_"))
        time = row["event_time"]
        if not isinstance(time, datetime) or time.utcoffset() is None:
            fail("anchor_window", "component lacks a captured aware instant", stage="consume")
        if member != subject or not point <= time < end:
            continue
        identity = tuple(row[name] for name in row if name.startswith("anchor_key_"))
        own_root = any(
            event.ref.path == anchor[keys[width]] and event.source.ref.path == root
            for event in domain.preparation.events
        )
        if own_root and identity and identity == tuple(anchor[key] for key in keys[width + 1 :]):
            continue
        if time == point:
            sequence, left = row.get("sequence"), anchor["anchor__sequence_int"]
            if isinstance(sequence, str):
                left = anchor["anchor__sequence_enum"]
                scales = (
                    tuple(
                        item.order for item in order.definition.sequences if item.order != "integer"
                    )
                    if order
                    else ()
                )
                if not scales or sequence not in scales[0] or left not in scales[0]:
                    fail(
                        "business_order",
                        "same-instant component lacks captured enum order",
                        stage="consume",
                    )
                sequence, left = scales[0].index(sequence), scales[0].index(left)
            if type(sequence) is not int or type(left) is not int or sequence == left:
                fail(
                    "business_order",
                    "distinct same-instant component needs strict captured business order",
                    stage="consume",
                )
            if sequence < left:
                continue
        selected.append(row)
    return selected


def observe(
    node: MethodNode, selected: ExchangeResult, candidates: pa.Table, binding: str
) -> ExchangeResult:
    declaration = require_part(node.signature, "anchor")
    assert isinstance(declaration, AnchorObservationPart)
    original = require_part(node.signature, "original_state")
    assert isinstance(original, OriginalStatePart)
    subject = require_part(node.signature, "subject")
    assert isinstance(subject, SubjectPart)
    keys = selected.contract.key_fields
    anchors = next(p.table for p in selected.parts if p.role == "anchor")
    width = len(subject.subject_key)
    assert candidates.num_rows == 1
    pools = candidates.to_pylist()[0]
    fields = [
        *selected.primary.schema,
        pa.field("value", arrow_scalar_type(node.value_type)),
        pa.field("cell_tag", pa.string()),
        pa.field("cell_reason", pa.string()),
    ]
    state_fields = []
    for name in original.components:
        index = (
            0
            if name in ("count", "sum", "non_null_count", "absolute_sum")
            or name.startswith("numerator_")
            else 1
            if name.startswith("denominator_")
            else int(name.split("_")[1])
        )
        dtype = (
            pa.int64()
            if name == "count" or "count" in name
            else dt.dtype(declaration.component_types[index]).to_pyarrow()
        )
        if pa.types.is_duration(dtype):
            dtype = pa.int64()
        state_fields.append(pa.field("original_state__" + name, dtype))
    state_schema = pa.schema([*selected.primary.schema, *state_fields])
    primary_rows, anchor_rows, state_rows = [], [], []
    for row in anchors.to_pylist():
        check()
        point = row["anchor__started_at"]
        assert isinstance(point, datetime)
        end = deadline(point, declaration.window)
        uses = tuple(
            _selected(
                row,
                pools[f"uses_{i}"],
                declaration.domain,
                keys,
                width,
                end,
                declaration.component_roots[i].path,
            )
            for i in range(len(declaration.component_roots))
        )
        try:
            components, value, tag, reason = _numeric(
                declaration, original, uses, state_schema, node.value_type
            )
        except (ValueError, OverflowError) as error:
            fail("numeric_state", str(error), stage="consume")
        identity = {key: row[key] for key in keys}
        primary_rows.append({**identity, "value": value, "cell_tag": tag, "cell_reason": reason})
        anchor_rows.append(
            {
                **row,
                "anchor__deadline": end,
                **{f"anchor__uses_{i}": rows for i, rows in enumerate(uses)},
            }
        )
        state_rows.append(
            {**identity, **{"original_state__" + name: value for name, value in components.items()}}
        )
    primary = pa.Table.from_pylist(primary_rows, schema=pa.schema(fields)).replace_schema_metadata(
        selected.primary.schema.metadata
    )
    anchor_schema = pa.schema(
        [
            *anchors.schema,
            pa.field("anchor__deadline", pa.timestamp("us", tz="UTC")),
            *(
                pa.field(f"anchor__uses_{i}", candidates.schema.field(f"uses_{i}").type)
                for i in range(len(declaration.component_roots))
            ),
        ]
    )
    anchor = pa.Table.from_pylist(anchor_rows, schema=anchor_schema)
    parts = (
        next(p for p in selected.parts if p.role == "subject"),
        ExchangePart("anchor", anchor),
        ExchangePart("original_state", pa.Table.from_pylist(state_rows, schema=state_schema)),
        ExchangePart(
            "coverage",
            selected.primary.append_column(
                "coverage__complete", pa.array([True] * len(primary), type=pa.bool_())
            ),
        ),
    )
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        (("null", ("empty_contribution",)), ("undefined", ("zero_denominator",))),
    )
    return from_arrow(primary, contract, parts=parts)
