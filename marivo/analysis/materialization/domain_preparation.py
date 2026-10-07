"""Registered occurrence input checks and fixed consumption of captured facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from itertools import groupby
from typing import TYPE_CHECKING

import pyarrow as pa
from pydantic import TypeAdapter, ValidationError

from marivo.analysis.core.domain_captures import CaptureAuthority, EventPrecision, fail
from marivo.analysis.core.model import OccurrencePart
from marivo.analysis.core.rules import OccurrencePrepare
from marivo.analysis.methods.domain_coverage import FACTS, CoverageFact, coverage

if TYPE_CHECKING:
    from marivo.analysis.core.graph import MethodNode
    from marivo.analysis.materialization.graph_exchange import (
        ExchangeContract,
        ExchangePart,
        ExchangeResult,
    )

AUTHORITY: TypeAdapter[CaptureAuthority] = TypeAdapter(CaptureAuthority)
PRECISION: TypeAdapter[tuple[EventPrecision, ...]] = TypeAdapter(tuple[EventPrecision, ...])


def validate_rows(params: OccurrencePrepare, table: pa.Table) -> tuple[int, ...]:
    """Check captured order values without searching permutations or choosing IDs."""
    from marivo.analysis.materialization.execute_deadline import check

    events = {event.ref.path: event for event in params.events}
    rows = table.to_pylist()
    subjects = tuple(f"subject__key_{i}" for i in range(len(params.events[0].subject.primary_key)))
    keys = tuple(f"key_{i}" for i in range(1 + len(params.events[0].identity)))
    identities: set[tuple[object, ...]] = set()
    sequence_values: dict[tuple[object, ...], set[int]] = {}
    declarations = (
        {}
        if params.order is None
        else {item.event_ref: item for item in params.order.definition.sequences}
    )
    ordered_indices: list[int] = []
    for row_index, row in enumerate(rows):
        row["__input_index"] = row_index
        check()
        identity = tuple(row[key] for key in keys)
        subject = tuple(row[key] for key in subjects)
        if (
            any(type(value) not in (str, int) or value == "" for value in (*identity, *subject))
            or identity in identities
        ):
            fail(
                "occurrence_identity",
                "null, duplicate or invalid complete occurrence/Subject key",
                stage="consume",
            )
        identities.add(identity)
        event = events.get(str(row["key_0"]))
        if event is None or row["occurrences__occurred_at"] is None:
            fail("input_binding", "unknown Event or missing instant", stage="consume")
        declaration = declarations.get(event.ref.path)
        if declaration is not None:
            value = (
                row["occurrences__sequence_int"]
                if declaration.order == "integer"
                else row["occurrences__sequence_enum"]
            )
            if declaration.order == "integer":
                if type(value) is not int:
                    fail(
                        "business_order",
                        f"integer sequence missing for {event.ref.path}",
                        stage="consume",
                    )
                ordinal = value
            else:
                if value not in declaration.order:
                    fail(
                        "business_order",
                        f"unknown sequence enum for {event.ref.path}",
                        stage="consume",
                    )
                ordinal = declaration.order.index(value)
            if ordinal in sequence_values.setdefault(subject, set()):
                fail(
                    "business_order",
                    "duplicate sequence across a Subject's captured Events",
                    stage="consume",
                )
            sequence_values[subject].add(ordinal)
            row["__ordinal"] = ordinal
    rows.sort(
        key=lambda row: (*tuple(row[key] for key in subjects), row["occurrences__occurred_at"])
    )
    for _, group in groupby(
        rows, lambda row: (*tuple(row[key] for key in subjects), row["occurrences__occurred_at"])
    ):
        check()
        tied = tuple(group)
        if len(tied) < 2:
            ordered_indices.extend(int(row["__input_index"]) for row in tied)
            continue
        edges: set[tuple[int, int]] = set()
        for i, first in enumerate(tied):
            check()
            for j, second in enumerate(tied):
                check()
                if (
                    i != j
                    and "__ordinal" in first
                    and "__ordinal" in second
                    and first["__ordinal"] < second["__ordinal"]
                ):
                    edges.add((i, j))
                if params.order is not None:
                    for edge in params.order.definition.conflicts:
                        if (
                            first["key_0"] == edge.before_event
                            and second["key_0"] == edge.after_event
                        ):
                            edges.add((i, j))
        remaining = set(range(len(tied)))
        ambiguous = False
        while remaining:
            check()
            ready = {
                i
                for i in remaining
                if not any(
                    destination == i and origin in remaining for origin, destination in edges
                )
            }
            if not ready:
                fail("business_order", "contradictory sequence/precedence cycle", stage="consume")
            ambiguous |= len(ready) > 1
            ordered_indices.extend(int(tied[index]["__input_index"]) for index in sorted(ready))
            remaining -= ready
        if ambiguous and params.order_use == "ordered":
            fail(
                "business_order",
                "simultaneous occurrences require exact business_order",
                stage="consume",
            )
        if (
            ambiguous
            and params.order_use == "after_terminal"
            and (
                params.model is None
                or params.terminal_state
                not in {state.name for state in params.model.definition.states if state.terminal}
            )
        ):
            fail(
                "business_order",
                "terminal-only tie requires an already known terminal-state proof",
                stage="consume",
            )
    coverage(params)
    return tuple(ordered_indices)


def validate_exchange(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    declaration = next(
        (part for part in contract.signature.parts if isinstance(part, OccurrencePart)), None
    )
    if declaration is None:
        fail("required_parts", "occurrence capture declaration missing", stage="recovery")
    occurrence = next((part.table for part in parts if part.role == "occurrences"), None)
    subject = next((part.table for part in parts if part.role == "subject"), None)
    if occurrence is None or subject is None:
        fail("required_parts", "occurrence or Subject part missing", stage="recovery")
    params = OccurrencePrepare(
        contract.signature.domain,
        declaration.events,
        declaration.start,
        declaration.end,
        declaration.order,
        declaration.model,
        declaration.completeness,
        declaration.order_use,
        declaration.terminal_state,
    )
    validate_metadata(params, primary.schema.metadata or {})
    if occurrence.schema.field("occurrences__occurred_at").type != pa.timestamp("us", tz="UTC"):
        fail(
            "physical_qualification",
            "occurrence part lacks the actual captured UTC/us carrier",
            stage="recovery",
        )
    names = contract.key_fields
    mappings = {tuple(row[name] for name in names): row for row in subject.to_pylist()}
    rows = [
        {**row, **mappings[tuple(row[name] for name in names)]} for row in occurrence.to_pylist()
    ]
    joined_schema = pa.schema(
        (
            *occurrence.schema,
            *(field for field in subject.schema if field.name not in occurrence.column_names),
        )
    )
    validate_rows(params, pa.Table.from_pylist(rows, schema=joined_schema))


def fixed(node: MethodNode, selected: ExchangeResult, binding: str) -> ExchangeResult:
    from marivo.analysis.materialization.graph_exchange import from_arrow

    validate_exchange(selected.contract, selected.primary, selected.parts)
    capture = next(
        part for part in selected.contract.signature.parts if isinstance(part, OccurrencePart)
    )
    params = node.parameters
    assert isinstance(params, OccurrencePrepare)
    if (
        capture.events != params.events
        or capture.order != params.order
        or capture.model != params.model
    ):
        fail(
            "input_binding",
            "fixed preparation cannot replace its captured definitions",
            stage="admission",
        )
    contract = replace(
        selected.contract, signature=node.signature, input_binding=binding, pending_checks=()
    )
    return from_arrow(
        selected.primary,
        contract,
        parts=selected.parts,
        method_state=selected.method_state,
        validate=False,
    )


def validate_metadata(
    params: OccurrencePrepare, metadata: dict[bytes, bytes]
) -> tuple[CoverageFact, ...]:
    try:
        authority = AUTHORITY.validate_json(metadata[b"r7.capture_authority"])
        precision = PRECISION.validate_json(metadata[b"r7.precision"])
        facts = FACTS.validate_json(metadata[b"r7.coverage"])
    except (KeyError, ValueError, TypeError, ValidationError) as error:
        fail(
            "required_parts",
            f"capture/precision/coverage metadata missing: {type(error).__name__}",
            stage="recovery",
        )
    if (
        {item.event for item in precision} != {event.ref.path for event in params.events}
        or len(precision) != len(params.events)
        or {item.event for item in facts} != {event.ref.path for event in params.events}
        or len(facts) != len(params.events)
    ):
        fail("input_binding", "retained authority or Event metadata differs", stage="recovery")
    payload = json.loads(metadata[b"r7.capture_authority"])
    payload.pop("digest", None)
    if (
        authority.digest != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        or not authority.capture_id
        or (authority.kind == "immutable_manifest") != bool(authority.files)
        or any(
            len(file.sha256) != 64 or file.bytes < 0 or not file.source_id or not file.path
            for file in authority.files
        )
    ):
        fail("input_binding", "damaged capture authority/manifest", stage="recovery")
    if any(
        item.effective_unit != "us"
        or item.possible_loss != (item.declared_unit == "ns" or item.source_unit == "ns")
        or not item.behavior
        for item in precision
    ):
        fail("physical_qualification", "damaged retained precision disclosure", stage="recovery")
    if facts != coverage(
        params, tuple(item.observed for item in facts if item.observed is not None)
    ) or any(
        item.observed is not None and item.observed.source_revision != authority.digest
        for item in facts
    ):
        fail(
            "coverage_binding",
            "retained coverage differs from its bound declarations/receipts",
            stage="recovery",
        )
    return facts
