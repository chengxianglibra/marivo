"""Normative per-series forecasts with exact innovation and horizon variance facts."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from marivo.analysis.methods.deviation_numeric import RationalFact

Model = Literal["naive", "drift", "seasonal_naive"]


@dataclass(frozen=True, slots=True)
class Training:
    model: Model
    season: int | None
    n: int
    innovations: tuple[RationalFact, ...]
    df: int
    slope: RationalFact
    sigma2: RationalFact
    exact_zero: bool


def train(values: tuple[Fraction, ...], model: Model, season: int | None) -> Training:
    n = len(values)
    if (
        model not in ("naive", "drift", "seasonal_naive")
        or (model == "seasonal_naive" and (type(season) is not int or season <= 1))
        or (model != "seasonal_naive" and season is not None)
    ):
        raise ValueError("closed forecast model/season required")
    minimum = season + 1 if season is not None else 3 if model == "drift" else 2
    if n < minimum:
        raise ValueError(f"forecast requires at least {minimum} complete periods; received {n}")
    slope = (values[-1] - values[0]) / (n - 1) if model == "drift" else Fraction()
    distance = season if season is not None else 1
    innovations = tuple(values[i] - values[i - distance] - slope for i in range(distance, n))
    df = n - 2 if model == "drift" else n - distance
    sigma2 = sum((v * v for v in innovations), Fraction()) / df
    return Training(
        model,
        season,
        n,
        tuple(RationalFact.capture(v) for v in innovations),
        df,
        RationalFact.capture(slope),
        RationalFact.capture(sigma2),
        all(v == 0 for v in innovations),
    )


def point(values: tuple[Fraction, ...], training: Training, h: int) -> tuple[Fraction, Fraction]:
    if type(h) is not int or not 1 <= h <= 1000 or len(values) != training.n:
        raise ValueError("bound horizon ordinal and complete training vector required")
    sigma2 = training.sigma2.value()
    if training.model == "seasonal_naive":
        assert training.season is not None
        return values[-training.season + (h - 1) % training.season], sigma2 * (
            (h - 1) // training.season + 1
        )
    if training.model == "drift":
        return values[-1] + h * training.slope.value(), sigma2 * h * (
            1 + Fraction(h, training.n - 1)
        )
    return values[-1], sigma2 * h
