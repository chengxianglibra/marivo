from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from marivo.analysis_py.errors import WindowRelativeParseError

RelativeUnit = Literal["day", "week", "month", "quarter", "year"]


@dataclass(frozen=True)
class RelativeKind:
    op: Literal["last_n", "this", "to_date", "today", "yesterday"]
    unit: RelativeUnit | None = None
    n: int | None = None


_UNIT_ALIASES: dict[str, RelativeUnit] = {
    "day": "day",
    "days": "day",
    "week": "week",
    "weeks": "week",
    "month": "month",
    "months": "month",
    "quarter": "quarter",
    "quarters": "quarter",
    "year": "year",
    "years": "year",
}

_TO_DATE_ALIASES: dict[str, RelativeUnit] = {
    "wtd": "week",
    "mtd": "month",
    "qtd": "quarter",
    "ytd": "year",
}

_THIS_PERIOD_ALIASES: dict[str, RelativeUnit] = {
    "this week": "week",
    "this month": "month",
    "this quarter": "quarter",
    "this year": "year",
}


def _canonical(expr: str) -> str:
    return " ".join(expr.strip().lower().split())


def parse_relative_expr(expr: str) -> RelativeKind:
    if not isinstance(expr, str):
        raise WindowRelativeParseError(
            message=f"unsupported relative window expression {expr!r}",
            hint="Use forms like 'last 7 days', 'this month', 'mtd', 'today', or 'yesterday'.",
            details={"expr": expr},
        )

    text = _canonical(expr)
    if text == "today":
        return RelativeKind(op="today")
    if text == "yesterday":
        return RelativeKind(op="yesterday")

    to_date_unit = _TO_DATE_ALIASES.get(text)
    if to_date_unit is not None:
        return RelativeKind(op="to_date", unit=to_date_unit)

    this_unit = _THIS_PERIOD_ALIASES.get(text)
    if this_unit is not None:
        return RelativeKind(op="this", unit=this_unit)

    match = re.fullmatch(r"last ([1-9][0-9]*) ([a-z]+)", text)
    if match is not None:
        unit = _UNIT_ALIASES.get(match.group(2))
        if unit is not None:
            return RelativeKind(op="last_n", unit=unit, n=int(match.group(1)))

    raise WindowRelativeParseError(
        message=f"unsupported relative window expression {expr!r}",
        hint="Use forms like 'last 7 days', 'this month', 'mtd', 'today', or 'yesterday'.",
        details={"expr": expr},
    )
