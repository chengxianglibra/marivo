"""Canonical retained instance truth, complete fibers and bounded projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from typing import Literal

import pyarrow as pa
from pydantic import ConfigDict, TypeAdapter, ValidationError

from marivo.analysis.anchors import AnyAnchor, deadline
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    InstanceRetentionPart,
    SubjectPart,
    SubjectRetentionPart,
    require_part,
)
from marivo.analysis.core.rules import AnchorRetention, OccurrencePrepare, RetentionBySubject
from marivo.analysis.domains.completeness import BoundedCoverageStartV1
from marivo.analysis.materialization.execute_deadline import check
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_arrow,
)
from marivo.analysis.materialization.graph_protocol import decode, encode
from marivo.analysis.methods.domain_coverage import FACTS, CoverageFact, coverage

Key = tuple[str | int, ...]
Truth = Literal["true", "false", "unknown"]


@dataclass(frozen=True, slots=True)
class ReturnUse:
    key: Key
    instant: datetime
    sequence_int: int | None
    sequence_enum: str | None
    __pydantic_config__ = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class InstanceStatus:
    key: Key
    subject: Key
    started_at: datetime
    deadline: datetime
    sequence_int: int | None
    sequence_enum: str | None
    uses: tuple[ReturnUse, ...]
    status: Truth
    assignment: str | None = None
    __pydantic_config__ = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class RetentionLedger:
    definition: str
    instances: tuple[InstanceStatus, ...]
    coverage: tuple[CoverageFact, ...]
    version: Literal["v1"] = "v1"
    __pydantic_config__ = ConfigDict(extra="forbid")


LEDGER = TypeAdapter(RetentionLedger)


def _definition(part: InstanceRetentionPart) -> str:
    return sha256(repr(replace(part, selection="full")).encode()).hexdigest()


def read(part: ExchangePart) -> RetentionLedger:
    if (
        part.table.schema != pa.schema([("retention__retained", pa.string())])
        or part.table.num_rows != 1
    ):
        fail(
            "retention_integrity",
            "one typed complete retention ledger is required",
            stage="recovery",
        )
    text = part.table.column(0)[0].as_py()
    if not isinstance(text, str):
        fail("retention_integrity", "retained ledger is not canonical text", stage="recovery")
    return decode(text, LEDGER)


def _later(part: InstanceRetentionPart, row: InstanceStatus, use: ReturnUse) -> bool:
    order = part.anchors.preparation.order
    returning = part.returning.order
    if order is None or returning is None or order.definition != returning.definition:
        fail(
            "business_order",
            "same-instant return requires the Anchor's exact captured order",
            stage="consume",
        )
    before, after = False, False

    def ordinal(event: str, integer: int | None, label: str | None) -> int | None:
        declaration = next(
            (item for item in order.definition.sequences if item.event_ref == event), None
        )
        if declaration is None:
            return None
        if declaration.order == "integer":
            if integer is None:
                fail("business_order", "declared integer sequence is missing", stage="consume")
            return integer
        if label not in declaration.order:
            fail("business_order", "unknown captured enum sequence", stage="consume")
        assert label is not None
        return declaration.order.index(label)

    left = ordinal(str(row.key[len(row.subject)]), row.sequence_int, row.sequence_enum)
    right = ordinal(part.returning.events[0].ref.path, use.sequence_int, use.sequence_enum)
    if left is not None and right is not None:
        if left == right:
            fail(
                "business_order",
                "distinct simultaneous occurrences share a sequence value",
                stage="consume",
            )
        before, after = left < right, left > right
    edges = {(edge.before_event, edge.after_event) for edge in order.definition.conflicts}
    # Consume only the declared precedence relation, never occurrence identity order.
    while True:
        check()
        expanded = edges | {(a, d) for a, b in edges for c, d in edges if b == c}
        if expanded == edges:
            break
        edges = expanded
    first, second = str(row.key[len(row.subject)]), part.returning.events[0].ref.path
    before |= (first, second) in edges
    after |= (second, first) in edges
    if before == after:
        fail(
            "business_order",
            "same-instant return order is absent or contradictory",
            stage="consume",
        )
    return before


def _qualifies(part: InstanceRetentionPart, row: InstanceStatus, use: ReturnUse) -> bool:
    if use.instant.utcoffset() is None or not row.started_at <= use.instant < row.deadline:
        return False
    event = part.returning.events[0].ref.path
    if row.key[len(row.subject) :] == (event, *use.key):
        return False
    return use.instant > row.started_at or _later(part, row, use)


def _truth(row: InstanceStatus, fact: CoverageFact) -> Truth:
    if row.uses:
        return "true"
    intervals: list[tuple[datetime, datetime]] = []
    if fact.complete_through is not None:
        if fact.source_origin is not None:
            lower = row.started_at
        elif fact.complete_from is not None:
            lower = datetime.fromisoformat(fact.complete_from)
        else:
            fail("retention_coverage", "coverage lacks a bound start", stage="consume")
        intervals.append((lower, datetime.fromisoformat(fact.complete_through)))
    if fact.observed is not None:
        start = fact.observed.coverage_start
        intervals.append(
            (
                start.complete_from
                if isinstance(start, BoundedCoverageStartV1)
                else row.started_at,
                fact.observed.complete_through,
            )
        )
    covered_through = row.started_at
    for lower, upper in sorted(intervals):
        if lower > covered_through:
            break
        covered_through = max(covered_through, upper)
        if covered_through >= row.deadline:
            return "false"
    return "unknown"


def truths(
    ledger: RetentionLedger, part: InstanceRetentionPart | SubjectRetentionPart
) -> dict[Key, Truth]:
    if isinstance(part, InstanceRetentionPart):
        return {row.key: row.status for row in ledger.instances}
    fibers: dict[Key, list[Truth]] = {}
    for row in ledger.instances:
        check()
        fibers.setdefault(row.subject, []).append(row.status)
    result: dict[Key, Truth] = {}
    for key, statuses in fibers.items():
        if isinstance(part.rule, AnyAnchor):
            result[key] = (
                "true"
                if "true" in statuses
                else "false"
                if all(v == "false" for v in statuses)
                else "unknown"
            )
        else:
            result[key] = (
                "false"
                if "false" in statuses
                else "true"
                if all(v == "true" for v in statuses)
                else "unknown"
            )
    return result


def summary(
    ledger: RetentionLedger, part: InstanceRetentionPart | SubjectRetentionPart
) -> tuple[tuple[str, str], ...]:
    values = tuple(truths(ledger, part).values())
    total, positive, negative, unknown = (
        len(values),
        values.count("true"),
        values.count("false"),
        values.count("unknown"),
    )
    if total >= 2**63:
        fail("retention_count", "Omega count exceeds int64", stage="consume")
    bounds = (
        f"[{float(Fraction(positive, total))!r},{float(Fraction(positive + unknown, total))!r}]"
        if total
        else "Undefined(empty_omega), Undefined(empty_omega)"
    )
    return (
        ("omega_count", str(total)),
        ("known_true_count", str(positive)),
        ("known_false_count", str(negative)),
        ("unknown_count", str(unknown)),
        ("deterministic_bounds", bounds),
    )


def _assemble(
    node: MethodNode,
    ledger: RetentionLedger,
    binding: str,
    key_types: tuple[pa.DataType, ...],
    metadata: dict[bytes, bytes] | None,
) -> ExchangeResult:
    part = require_part(node.signature, "retention")
    assert isinstance(part, (InstanceRetentionPart, SubjectRetentionPart))
    statuses = truths(ledger, part)
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    schema = pa.schema(
        [
            *(pa.field(k, t) for k, t in zip(keys, key_types, strict=True)),
            pa.field("value", pa.bool_()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        ]
    )
    ordered = sorted(statuses, key=lambda key: tuple((type(v).__name__, v) for v in key))
    rows = [
        {
            **dict(zip(keys, key, strict=True)),
            "value": None if statuses[key] == "unknown" else statuses[key] == "true",
            "cell_tag": "unknown" if statuses[key] == "unknown" else "defined",
            "cell_reason": "insufficient_followup" if statuses[key] == "unknown" else None,
        }
        for key in ordered
    ]
    primary = pa.Table.from_pylist(rows, schema=schema).replace_schema_metadata(metadata)
    subject = require_part(node.signature, "subject")
    assert isinstance(subject, SubjectPart)
    mapping = primary.select(keys)
    for i in range(len(subject.subject_key)):
        mapping = mapping.append_column(f"subject__key_{i}", primary[keys[i]])
    retained = pa.table(
        {"retention__retained": pa.array([encode(ledger, LEDGER)], type=pa.string())}
    )
    parts = (ExchangePart("subject", mapping), ExchangePart("retention", retained))
    state = primary.select(keys).append_column("status", primary["cell_tag"])
    contract = ExchangeContract(
        node.signature,
        node.method,
        binding,
        primary.schema,
        keys,
        (
            PartContract("subject", mapping.schema, keys),
            PartContract("retention", retained.schema, ()),
        ),
        (("unknown", ("insufficient_followup",)),),
        "anchor_retention" if isinstance(part, InstanceRetentionPart) else "subject_retention",
        state.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=state)


def execute(node: MethodNode, inputs: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
    part = require_part(node.signature, "retention")
    assert isinstance(part, (InstanceRetentionPart, SubjectRetentionPart))
    selected = inputs[0]
    if isinstance(node.parameters, RetentionBySubject):
        ledger = read(next(p for p in selected.parts if p.role == "retention"))
        width = len(node.signature.domain.instance_key)
        types = tuple(
            selected.primary.schema.field(k).type for k in selected.contract.key_fields[:width]
        )
        return _assemble(node, ledger, binding, types, selected.primary.schema.metadata)
    assert isinstance(node.parameters, AnchorRetention) and isinstance(part, InstanceRetentionPart)
    captured = inputs[1]
    occurrences = next(p.table for p in captured.parts if p.role == "occurrences")
    mapping = next(p.table for p in captured.parts if p.role == "subject")
    occurrence_keys = captured.contract.key_fields
    subjects = {
        tuple(r[k] for k in occurrence_keys): tuple(
            r[f"subject__key_{i}"] for i in range(len(part.returning.events[0].subject.primary_key))
        )
        for r in mapping.to_pylist()
    }
    candidates: dict[Key, list[ReturnUse]] = {}
    for r in occurrences.to_pylist():
        check()
        key = tuple(r[k] for k in occurrence_keys)
        candidates.setdefault(subjects[key], []).append(
            ReturnUse(
                key[1:],
                r["occurrences__occurred_at"],
                r.get("occurrences__sequence_int"),
                r.get("occurrences__sequence_enum"),
            )
        )
    facts = FACTS.validate_json((captured.primary.schema.metadata or {})[b"r7.coverage"])
    instances = []
    anchors = next(p.table for p in selected.parts if p.role == "anchor")
    keys = selected.contract.key_fields
    width = len(part.returning.events[0].subject.primary_key)
    for r in anchors.to_pylist():
        check()
        key = tuple(r[k] for k in keys)
        row = InstanceStatus(
            key,
            key[:width],
            r["anchor__started_at"],
            deadline(r["anchor__started_at"], part.window),
            r["anchor__sequence_int"],
            r["anchor__sequence_enum"],
            (),
            "unknown",
            r.get("anchor__assignment"),
        )
        uses = tuple(
            sorted(
                (use for use in candidates.get(row.subject, ()) if _qualifies(part, row, use)),
                key=lambda use: tuple((type(v).__name__, v) for v in use.key),
            )
        )
        row = replace(row, uses=uses)
        instances.append(replace(row, status=_truth(row, facts[0])))
    ledger = RetentionLedger(
        _definition(part),
        tuple(sorted(instances, key=lambda r: tuple((type(v).__name__, v) for v in r.key))),
        facts,
    )
    types = tuple(selected.primary.schema.field(k).type for k in keys)
    return _assemble(node, ledger, binding, types, captured.primary.schema.metadata)


def native_result(node: MethodNode, table: pa.Table, captured: ExchangeResult) -> ExchangeResult:
    """Finish native witnesses with the exact captured coverage authority."""
    part = require_part(node.signature, "retention")
    assert isinstance(part, InstanceRetentionPart)
    facts = FACTS.validate_json((captured.primary.schema.metadata or {})[b"r7.coverage"])
    keys = tuple(f"key_{i}" for i in range(len(node.signature.domain.instance_key)))
    width = len(part.returning.events[0].subject.primary_key)
    identity_width = len(part.returning.events[0].identity)
    rows = []
    for raw in table.to_pylist():
        check()
        key = tuple(raw[k] for k in keys)
        uses = tuple(
            ReturnUse(
                tuple(use[f"identity_{i}"] for i in range(identity_width)),
                use["instant"],
                use["sequence_int"],
                use["sequence_enum"],
            )
            for use in raw["retention__uses"]
        )
        row = InstanceStatus(
            key,
            key[:width],
            raw["anchor__started_at"],
            raw["anchor__deadline"],
            raw["anchor__sequence_int"],
            raw["anchor__sequence_enum"],
            tuple(sorted(uses, key=lambda use: tuple((type(v).__name__, v) for v in use.key))),
            "unknown",
        )
        rows.append(replace(row, status=_truth(row, facts[0])))
    ledger = RetentionLedger(
        _definition(part),
        tuple(sorted(rows, key=lambda row: tuple((type(v).__name__, v) for v in row.key))),
        facts,
    )
    return _assemble(
        node,
        ledger,
        node.identity,
        tuple(table.schema.field(k).type for k in keys),
        captured.primary.schema.metadata,
    )


def validate(
    contract: ExchangeContract, primary: pa.Table, parts: tuple[ExchangePart, ...]
) -> None:
    part = require_part(contract.signature, "retention")
    assert isinstance(part, (InstanceRetentionPart, SubjectRetentionPart))
    original = part.instances if isinstance(part, SubjectRetentionPart) else part
    ledger = read(next(p for p in parts if p.role == "retention"))
    if ledger.definition != _definition(original) or ledger.version != "v1":
        fail(
            "retention_binding",
            "retained ledger differs from the frozen method definition",
            stage="recovery",
        )
    capture = original.returning
    params = OccurrencePrepare(
        original.returning_domain,
        capture.events,
        capture.start,
        capture.end,
        capture.order,
        capture.model,
        capture.completeness,
        capture.order_use,
        capture.terminal_state,
    )
    # Validate declarations and observed receipts against the original preparation scope.
    try:
        expected = coverage(
            params, tuple(f.observed for f in ledger.coverage if f.observed is not None)
        )
    except (ValueError, ValidationError) as error:
        fail("retention_coverage", str(error), stage="recovery")
    if expected != ledger.coverage or len(expected) != 1:
        fail(
            "retention_coverage",
            "coverage differs from its exact Event/source/version/capture binding",
            stage="recovery",
        )
    if len({r.key for r in ledger.instances}) != len(ledger.instances):
        fail("retention_identity", "duplicate original Omega identity", stage="recovery")
    width = len(capture.events[0].subject.primary_key)
    original_events = {e.ref.path: e for e in original.anchors.preparation.events}
    journey_assignments = []
    for row in ledger.instances:
        check()
        if original.anchors.journey is not None:
            from marivo.analysis.materialization.journey_execution import ASSIGNMENT

            if row.assignment is None:
                fail(
                    "retention_anchor",
                    "Journey Anchor lacks its canonical assignment",
                    stage="recovery",
                )
            assignment = ASSIGNMENT.validate_json(row.assignment, strict=True)
            if (
                row.subject != assignment.subject
                or row.key != (*assignment.subject, assignment.start.event, *assignment.start.key)
                or row.started_at != assignment.start.instant
            ):
                fail(
                    "retention_anchor",
                    "Journey assignment differs from its retained start",
                    stage="recovery",
                )
            journey_assignments.append(assignment)
        elif row.assignment is not None:
            fail(
                "retention_anchor",
                "Event Anchor cannot carry a Journey assignment",
                stage="recovery",
            )
        if (
            len(row.subject) != width
            or row.key[:width] != row.subject
            or len(row.key) <= width
            or row.key[width] not in original_events
        ):
            fail("retention_identity", "invalid full Anchor-to-Subject identity", stage="recovery")
        event = original_events[str(row.key[width])]
        if (
            len(row.key) != width + 1 + len(event.identity)
            or any(type(v) not in (str, int) for v in row.key)
            or row.started_at.utcoffset() is None
            or not datetime.fromisoformat(original.anchors.during_start)
            <= row.started_at
            < datetime.fromisoformat(original.anchors.during_end)
            or row.deadline != deadline(row.started_at, original.window)
        ):
            fail(
                "retention_window",
                "retained Anchor identity/start/deadline differs",
                stage="recovery",
            )
        if (
            len({u.key for u in row.uses}) != len(row.uses)
            or any(
                len(u.key) != len(capture.events[0].identity) or not _qualifies(original, row, u)
                for u in row.uses
            )
            or row.status != _truth(row, expected[0])
        ):
            fail(
                "retention_partition",
                "return witnesses, coverage and truth partition disagree",
                stage="recovery",
            )
    if original.anchors.journey is not None:
        from marivo.analysis.materialization.journey_execution import validate_assignments

        validate_assignments(tuple(journey_assignments), original.anchors.journey)
    statuses = truths(ledger, part)
    rows = primary.to_pylist()
    keys = contract.key_fields
    actual = {tuple(r[k] for k in keys) for r in rows}
    if not actual <= set(statuses) or (part.selection == "full" and actual != set(statuses)):
        fail(
            "retention_omega",
            "primary view escapes or truncates its declared original Omega",
            stage="recovery",
        )
    mapping = next(p.table for p in parts if p.role == "subject")
    mapped = {
        tuple(r[k] for k in keys): tuple(r[f"subject__key_{i}"] for i in range(width))
        for r in mapping.to_pylist()
    }
    for row in rows:
        key = tuple(row[k] for k in keys)
        truth = statuses[key]
        target = key if isinstance(part, SubjectRetentionPart) else key[:width]
        if (
            mapped[key] != target
            or row["value"] is not (None if truth == "unknown" else truth == "true")
            or row["cell_tag"] != ("unknown" if truth == "unknown" else "defined")
            or row["cell_reason"] != ("insufficient_followup" if truth == "unknown" else None)
            or (part.selection in ("true", "false", "unknown") and part.selection != truth)
        ):
            fail(
                "retention_partition",
                "selected Cells or Subject mapping differ from full retained truth",
                stage="recovery",
            )
