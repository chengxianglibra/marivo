"""Global exact-vector association kernels and retained arithmetic witnesses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from marivo.analysis.methods.deviation_numeric import RationalFact, RootCertificate
from marivo.analysis.methods.statistical_numeric import coefficient, unbounded

Method = Literal["pearson", "spearman", "kendall"]
Status = Literal["valid", "insufficient_pairs", "constant_a", "constant_b", "constant_both"]


@dataclass(frozen=True, slots=True)
class Score:
    status: Status
    coefficient: float | None
    numerator: RationalFact
    radicand: RationalFact
    root: RootCertificate | None
    ranks_a: tuple[RationalFact, ...] = ()
    ranks_b: tuple[RationalFact, ...] = ()
    concordant: int = 0
    discordant: int = 0
    ties_a: int = 0
    ties_b: int = 0


def ranks(values: tuple[Fraction, ...]) -> tuple[Fraction, ...]:
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    output = [Fraction()] * len(values)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[ordered[start]] == values[ordered[end]]:
            end += 1
        for i in ordered[start:end]:
            output[i] = Fraction(start + 1 + end, 2)
        start = end
    return tuple(output)


def score(
    a: tuple[Fraction, ...],
    b: tuple[Fraction, ...],
    method: Method,
    *,
    checkpoint: Callable[[], None] = unbounded,
) -> Score:
    if len(a) != len(b) or method not in ("pearson", "spearman", "kendall"):
        raise ValueError("exact complete paired vectors and a registered method required")
    status: Status = (
        "insufficient_pairs"
        if len(a) < 2
        else "constant_both"
        if len(set(a)) == len(set(b)) == 1
        else "constant_a"
        if len(set(a)) == 1
        else "constant_b"
        if len(set(b)) == 1
        else "valid"
    )
    zero = RationalFact.capture(Fraction())
    if status != "valid":
        return Score(status, None, zero, zero, None)
    ra, rb = (ranks(a), ranks(b)) if method == "spearman" else ((), ())
    c = d = ta = tb = 0
    if method == "kendall":
        for i in range(len(a)):
            checkpoint()
            for j in range(i):
                if j % 256 == 0:
                    checkpoint()
                dx, dy = a[i] - a[j], b[i] - b[j]
                if dx == dy == 0:
                    continue
                if dx == 0:
                    ta += 1
                elif dy == 0:
                    tb += 1
                elif dx * dy > 0:
                    c += 1
                else:
                    d += 1
        num, rad = Fraction(c - d), Fraction((c + d + ta) * (c + d + tb))
    else:
        x, y = (ra, rb) if method == "spearman" else (a, b)
        mx, my = sum(x, Fraction()) / len(x), sum(y, Fraction()) / len(y)
        xx, yy = tuple(v - mx for v in x), tuple(v - my for v in y)
        num = sum((u * v for u, v in zip(xx, yy, strict=True)), Fraction())
        rad = sum((u * u for u in xx), Fraction()) * sum((v * v for v in yy), Fraction())
    value, certificate = coefficient(num, rad, checkpoint=checkpoint)
    return Score(
        status,
        value,
        RationalFact.capture(num),
        RationalFact.capture(rad),
        certificate,
        tuple(RationalFact.capture(v) for v in ra),
        tuple(RationalFact.capture(v) for v in rb),
        c,
        d,
        ta,
        tb,
    )
