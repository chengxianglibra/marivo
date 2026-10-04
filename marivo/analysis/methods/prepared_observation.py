"""Exact restriction of prepared contribution rows by retained Subject/window keys."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

import pyarrow as pa

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.rules import ObserveCount, ObserveMetric
from marivo.analysis.materialization.execute_deadline import check


@dataclass(frozen=True, slots=True)
class Restriction:
    key: tuple[str | int, ...]
    subject: tuple[str | int, ...]
    start: datetime
    end: datetime


def restrict(
    candidates: pa.Table, selections: tuple[Restriction, ...]
) -> tuple[tuple[Mapping[str, object], ...], ...]:
    """Preserve every contribution use for overlapping actual Anchor windows."""
    by_subject: dict[tuple[str | int, ...], list[Mapping[str, object]]] = {}
    member_names = tuple(name for name in candidates.column_names if name.startswith("member_"))
    for row in candidates.to_pylist():
        check()
        subject: list[str | int] = []
        for name in member_names:
            value = row[name]
            if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
                fail(
                    "input_binding",
                    "candidate has an invalid complete Subject key",
                    stage="consume",
                )
            subject.append(value)
        by_subject.setdefault(tuple(subject), []).append(row)
    if len({selection.key for selection in selections}) != len(selections):
        fail("input_binding", "duplicate complete selection/Anchor keys", stage="consume")
    result = []
    for selection in selections:
        check()
        if (
            len(selection.subject) != len(member_names)
            or selection.start.utcoffset() is None
            or selection.end.utcoffset() is None
            or selection.start >= selection.end
        ):
            fail("preparation_bounds", "invalid actual Subject/Anchor window", stage="consume")
        rows = []
        for row in by_subject.get(selection.subject, []):
            check()
            point = row["event_time"]
            if not isinstance(point, datetime) or point.utcoffset() is None:
                fail(
                    "input_binding",
                    "candidate instant is not the captured aware time",
                    stage="consume",
                )
            if selection.start <= point < selection.end:
                rows.append(row)
        result.append(tuple(rows))
    return tuple(result)


def state(
    params: ObserveMetric | ObserveCount, rows: tuple[Mapping[str, object], ...]
) -> dict[str, int | float | Decimal]:
    check()
    if isinstance(params, ObserveCount):
        if len(rows) >= 2**63:
            fail("numeric_state", "count exceeds int64", stage="consume")
        return {"count": len(rows)}
    if params.amount_type.startswith("decimal("):
        from marivo.analysis.methods.deviation_numeric import exact, finish
        from marivo.analysis.methods.deviation_physical import parse_type
        from marivo.analysis.methods.physical import DecimalType

        typ = parse_type(params.amount_type)
        assert isinstance(typ, DecimalType)
        total_exact = Fraction()
        non_null = 0
        for row in rows:
            check()
            value = row["amount"]
            if value is None:
                continue
            if not isinstance(value, Decimal):
                fail(
                    "numeric_state", "prepared amount is not the captured Decimal", stage="consume"
                )
            try:
                total_exact += exact(value, typ)
            except ValueError as error:
                fail("numeric_state", str(error), stage="consume")
            non_null += 1
        try:
            total_decimal = finish(total_exact, DecimalType(38, typ.scale))
        except OverflowError as error:
            fail("numeric_state", str(error), stage="consume")
        assert isinstance(total_decimal, Decimal)
        return {"sum": total_decimal, "non_null_count": non_null}
    amounts: list[int | float] = []
    for row in rows:
        check()
        value = row["amount"]
        if value is None:
            continue
        if (
            type(value) not in (int, float)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or (
                params.amount_type == "int64"
                and (type(value) is not int or not -(2**63) <= value < 2**63)
            )
            or (params.amount_type == "float64" and type(value) is not float)
        ):
            fail(
                "numeric_state",
                "candidate amount differs from its finite exact numeric type",
                stage="consume",
            )
        amounts.append(value)
    try:
        total = math.fsum(amounts) if params.amount_type == "float64" else sum(amounts)
        absolute = (
            math.fsum(abs(value) for value in amounts) if params.amount_type == "float64" else 0
        )
    except OverflowError:
        fail("numeric_state", "prepared component overflow", stage="consume")
    if (
        not math.isfinite(total)
        or not math.isfinite(absolute)
        or (type(total) is int and not -(2**63) <= total < 2**63)
        or len(rows) >= 2**63
    ):
        fail(
            "numeric_state",
            "prepared component exceeds its published numeric type",
            stage="consume",
        )
    result: dict[str, int | float | Decimal] = {"sum": total, "non_null_count": len(amounts)}
    if params.method == "mean":
        result["row_count"] = len(rows)
    if params.amount_type == "float64":
        result["absolute_sum"] = absolute
    return result
