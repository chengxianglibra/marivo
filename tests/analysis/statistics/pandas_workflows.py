"""Explicit pandas recipes consuming terminal Query exports in statistics tests."""

from __future__ import annotations

from decimal import Context, Decimal, localcontext
from fractions import Fraction
from math import sqrt
from pathlib import Path
from statistics import NormalDist
from typing import Literal

import pandas as pd

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCase

DeviationMethod = Literal["zscore", "mad"]
ForecastModel = Literal["naive", "drift", "seasonal_naive"]


def plain(artifact: mv.Artifact) -> pd.DataFrame:
    """Keep every exported key/value while giving pandas recipes simple labels."""
    frame = artifact.to_pandas()
    names: list[str] = []
    key = 0
    for label in frame.columns:
        if not isinstance(label, tuple) or len(label) != 2:
            raise AssertionError("terminal exports require kind/name column pairs")
        kind, name = label
        if kind == "value":
            assert isinstance(name, str)
            names.append(name)
        else:
            names.append(f"key_{key}")
            key += 1
    frame.columns = names
    return frame


def orders(case: DslCase) -> mv.Query:
    session = case.session
    members = session.members(ms.ref.entity("sales.order"))
    return session.read(
        members,
        {
            "value": session.catalog.fields.get("sales.order.amount"),
            "category": session.catalog.fields.get("sales.order.channel"),
        },
    )


def monthly(case: DslCase, start: str, end: str) -> mv.Query:
    session = case.session
    return session.observe(
        {
            "value": session.catalog.metrics.get("sales.revenue"),
            "count": session.catalog.metrics.get("sales.order_count"),
        },
        during=session.time_scope(start=start, end=end),
        by=session.members(ms.ref.entity("sales.customer")),
    )


def daily(case: DslCase, *, end: str = "2026-08-04") -> mv.Query:
    session = case.session
    period = session.time_scope(start="2026-08-01", end=end)
    return session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        during=period.by(mv.grain("day")),
    )


def deviation(frame: pd.DataFrame, method: DeviationMethod) -> pd.DataFrame:
    """Fit represented non-NA values explicitly; selecting output never refits."""
    result = frame.copy(deep=True)
    values = result["value"].dropna().map(Fraction)
    for label in ("reference", "deviation", "score"):
        result[label] = pd.Series(pd.NA, index=result.index, dtype=object)
    if values.empty:
        return result
    ordered = values.sort_values().tolist()
    size = len(values)
    center = (
        values.sum() / size
        if method == "zscore"
        else (ordered[(size - 1) // 2] + ordered[size // 2]) / 2
    )
    delta = values.map(lambda value: value - center)
    absolute = delta.abs().sort_values().tolist()
    mad = (absolute[(size - 1) // 2] + absolute[size // 2]) / 2
    scale = (
        delta.map(lambda value: value * value).sum() / size
        if method == "zscore"
        else mad * Fraction(7413, 5000)
        if mad
        else delta.abs().sum() / size
    )
    result["reference"] = center
    result.loc[values.index, "deviation"] = delta
    if size >= 2 and scale:
        with localcontext(Context(prec=240, Emin=-999999, Emax=999999)):
            divisor = Decimal(scale.numerator) / Decimal(scale.denominator)
            if method == "zscore":
                divisor = divisor.sqrt()
            result.loc[values.index, "score"] = delta.map(
                lambda value: float(
                    (Decimal(value.numerator) / Decimal(value.denominator)) / divisor
                )
            )
    return result


def forecast(
    values: pd.Series,
    model: ForecastModel = "naive",
    *,
    horizon: int = 2,
    season: int = 2,
    level: float = 0.95,
) -> pd.DataFrame:
    """Compute point and interval columns from an explicitly exported series."""
    if type(horizon) is not int or not 1 <= horizon <= 1000 or not 0 < level < 1:
        raise ValueError("positive bounded horizon and open-unit interval level required")
    raw = values.map(Fraction).reset_index(drop=True)
    size = len(raw)
    distance = season if model == "seasonal_naive" else 1
    if size < (season + 1 if model == "seasonal_naive" else 3 if model == "drift" else 2):
        raise ValueError("complete history is too short")
    slope = (raw.iloc[-1] - raw.iloc[0]) / (size - 1) if model == "drift" else Fraction()
    innovations = (raw - raw.shift(distance)).dropna().map(lambda value: value - slope)
    variance = innovations.map(lambda value: value * value).sum() / (
        size - 2 if model == "drift" else size - distance
    )
    rows = []
    z = NormalDist().inv_cdf((1 + level) / 2)
    for step in range(1, horizon + 1):
        point = (
            raw.iloc[-season + (step - 1) % season]
            if model == "seasonal_naive"
            else raw.iloc[-1] + step * slope
        )
        uncertainty = variance * (
            (step - 1) // season + 1
            if model == "seasonal_naive"
            else step * (1 + Fraction(step, size - 1))
            if model == "drift"
            else step
        )
        margin = z * sqrt(uncertainty)
        rows.append((float(point), float(point) - margin, float(point) + margin))
    return pd.DataFrame(rows, columns=["prediction", "lower", "upper"])


def receipt_paths(root: Path, artifact: mv.Artifact) -> tuple[Path, ...]:
    data = artifact._manifest.data
    handles = (data.rows, *(part.rows for part in data.attachments))
    return tuple(
        root / handle.receipt.project_relative_path / handle.receipt.file_manifest[0].relative_path
        for handle in handles
    )


def assert_clean(session: mv.Session, *, artifacts: int) -> None:
    runtime = session._runtime
    assert runtime.store.resources(session.id) == ()
    with runtime.store._read() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == artifacts
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM dataset_evidence").fetchone()[0] == artifacts
        )
        assert connection.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0
