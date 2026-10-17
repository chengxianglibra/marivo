"""Explicit pandas allocation recipes over complete terminal fact exports."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from math import isnan
from numbers import Integral, Real
from typing import Literal

import pandas as pd
import pyarrow as pa

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.pandas_workflows import plain
from tests.shared_fixtures import DslCase

Method = Literal["sum", "count", "linear", "mean", "weighted", "ratio"]
Mode = Literal["joint", "hierarchy"]
Scalar = int | float | Decimal | pd.Timedelta


def basis_query(case: DslCase, month: int, *, weight: bool = False) -> mv.Query:
    session = case.session
    names = {
        "member": "customer_id",
        "channel": "channel",
        "status": "status",
        "amount": "amount",
        "time": "ordered_at",
    }
    if weight:
        names["weight"] = "weight"
    query = session.read(
        session.members(ms.ref.entity("sales.order")),
        {name: session.catalog.fields.get("sales.order." + path) for name, path in names.items()},
    )
    return query.where(
        (query["time"] >= datetime(2026, month, 1, tzinfo=timezone.utc))
        & (query["time"] < datetime(2026, month + 1, 1, tzinfo=timezone.utc))
        & ~query["member"].is_null()
    )


def basis(artifact: mv.Artifact) -> pd.DataFrame:
    return plain(artifact).drop(columns="key_0")


def _number(value: object) -> Fraction | None:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timedelta):
        return Fraction(value.value)
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, Integral):
        return Fraction(int(value))
    if isinstance(value, Real):
        return None if isnan(float(value)) else Fraction(float(value))
    raise ValueError("a finite numeric allocation input is required")


def allocation(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    method: Method = "sum",
    axes: tuple[str, ...] = ("channel",),
    parents: tuple[str, ...] = ("member",),
    top_k: int | None = None,
    mode: Mode = "joint",
) -> pd.DataFrame:
    """Allocate each parent's completed change using explicit shared axis buckets.

    Means use each side's own valid count; weighted means use paired weights;
    ratios use their independent weight sums. Synthetic Other is a separate
    flag, so a real string category named Other keeps its identity.
    """
    if (
        not axes
        or len(set(axes)) != len(axes)
        or any(name not in current.columns or name not in baseline.columns for name in axes)
    ):
        raise ValueError("unique existing allocation axes are required")
    if mode not in {"joint", "hierarchy"} or (mode == "hierarchy" and len(axes) < 2):
        raise ValueError("hierarchy requires at least two axes")
    if any(bool(frame.loc[:, list(axes)].isna().any().any()) for frame in (current, baseline)):
        raise ValueError("allocation axes require non-null labels")
    if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 1000):
        raise ValueError("top_k must be an integer from 1 through 1000")
    dtype = current["amount"].dtype
    scale = (
        dtype.pyarrow_dtype.scale
        if isinstance(dtype, pd.ArrowDtype) and pa.types.is_decimal(dtype.pyarrow_dtype)
        else None
    )
    duration = pd.api.types.is_timedelta64_dtype(dtype) or (
        isinstance(dtype, pd.ArrowDtype) and pa.types.is_duration(dtype.pyarrow_dtype)
    )

    def finish(value: Fraction) -> Scalar:
        if method == "count":
            return int(value)
        if scale is not None and method not in {"ratio", "weighted"}:
            with localcontext() as context:
                context.prec = 120
                return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
                    Decimal(1).scaleb(-scale), rounding=ROUND_HALF_EVEN
                )
        if duration and method in {"sum", "linear"}:
            return pd.Timedelta(round(value), unit="ns")
        return (
            int(value) if method in {"sum", "linear"} and value.denominator == 1 else float(value)
        )

    def components(
        frame: pd.DataFrame,
    ) -> tuple[dict[tuple[object, ...], Fraction], dict[tuple[object, ...], Fraction]]:
        numerators: dict[tuple[object, ...], Fraction] = defaultdict(Fraction)
        divisors: dict[tuple[object, ...], Fraction] = defaultdict(Fraction)
        own_weights: dict[tuple[object, ...], Fraction] = defaultdict(Fraction)
        for row in frame.to_dict(orient="records"):
            parent = tuple(row[name] for name in parents)
            key = (*parent, *(row[name] for name in axes))
            amount = _number(row["amount"])
            weight = _number(row["weight"]) if method in {"weighted", "ratio"} else None
            if method == "count":
                numerators[key] += 1
            elif method == "weighted":
                if amount is not None and weight is not None:
                    numerators[key] += amount * weight
                    divisors[parent] += weight
            elif method == "ratio":
                numerators[key] += amount or Fraction()
                divisors[parent] += weight or Fraction()
                own_weights[key] += weight or Fraction()
            elif amount is not None:
                numerators[key] += amount * (2 if method == "linear" else 1)
                divisors[parent] += 1
        if method == "ratio" and any(
            value and not own_weights[key] for key, value in numerators.items()
        ):
            raise ValueError("nonzero category numerator with zero category denominator")
        return dict(numerators), dict(divisors)

    left, left_den = components(current)
    right, right_den = components(baseline)
    records: list[dict[str, object]] = []
    parent_changes: dict[tuple[object, ...], Fraction] = {}
    for parent in {key[: len(parents)] for key in (*left, *right)}:
        denominators = (left_den.get(parent, Fraction()), right_den.get(parent, Fraction()))
        if method in {"mean", "weighted", "ratio"} and not all(denominators):
            raise ValueError("nonzero complete parent denominators are required")
        totals = []
        for values, denominator in ((left, denominators[0]), (right, denominators[1])):
            total = sum(
                (value for key, value in values.items() if key[: len(parents)] == parent),
                Fraction(),
            )
            totals.append(total / denominator if method in {"mean", "weighted", "ratio"} else total)
        parent_changes[parent] = (_number(finish(totals[0])) or Fraction()) - (
            _number(finish(totals[1])) or Fraction()
        )
    for key in sorted(set(left) | set(right), key=repr):
        parent = key[: len(parents)]
        amounts = [
            values.get(key, Fraction()) / denominators[parent]
            if method in {"mean", "weighted", "ratio"}
            else values.get(key, Fraction())
            for values, denominators in ((left, left_den), (right, right_den))
        ]
        a, b = finish(amounts[0]), finish(amounts[1])
        delta = finish((_number(a) or Fraction()) - (_number(b) or Fraction()))
        records.append(
            {
                **dict(zip(parents + axes, key, strict=True)),
                "current": a,
                "baseline": b,
                "contribution": delta,
                "other_flags": 0,
            }
        )
    for parent, expected in parent_changes.items():
        actual = sum(
            (
                _number(row["contribution"]) or Fraction()
                for row in records
                if tuple(row[name] for name in parents) == parent
            ),
            Fraction(),
        )
        if abs(actual - expected) > Fraction(1, 10**9):
            raise ValueError(
                "rounded allocation contributions do not reconcile to the parent change"
            )
    frame = pd.DataFrame.from_records(
        records, columns=(*parents, *axes, "current", "baseline", "contribution", "other_flags")
    )
    if frame.empty:
        return frame.assign(level=pd.Series(dtype="int64"))
    if top_k is not None:
        partitions = (
            frame.groupby(list(parents), dropna=False, sort=False) if parents else (((), frame),)
        )
        folded = []
        for _, group in partitions:
            for position, axis in enumerate(axes):
                scores: dict[object, Fraction] = defaultdict(Fraction)
                for row in group.to_dict(orient="records"):
                    scores[row[axis]] += max(
                        abs(_number(row["current"]) or Fraction()),
                        abs(_number(row["baseline"]) or Fraction()),
                    )
                kept = set(sorted(scores, key=lambda key: (-scores[key], repr(key)))[:top_k])
                other = ~group[axis].isin(kept)
                group = group.copy()
                group.loc[other, axis] = None
                group.loc[other, "other_flags"] += 1 << position
            folded.append(
                group.groupby([*parents, *axes, "other_flags"], dropna=False, sort=False)[
                    ["current", "baseline", "contribution"]
                ]
                .sum()
                .reset_index()
            )
        frame = pd.concat(folded, ignore_index=True)
    levels = []
    for level in range(1, len(axes) + 1) if mode == "hierarchy" else (len(axes),):
        keys = [*parents, *axes[:level], "other_flags"]
        result = (
            frame.groupby(keys, dropna=False, sort=False)[["current", "baseline", "contribution"]]
            .sum()
            .reset_index()
        )
        for axis in axes[level:]:
            result[axis] = None
        levels.append(result.assign(level=level))
    result = pd.concat(levels, ignore_index=True)
    return result.sort_values(
        [*parents, "level", "other_flags", *axes], na_position="last"
    ).reset_index(drop=True)
