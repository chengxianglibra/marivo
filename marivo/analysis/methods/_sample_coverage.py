"""Retain sample witnesses through local demand masks and native compression."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from marivo._definition.observations import SampleCoverage
from marivo.analysis.core.frozen_calendar import FrozenScalar
from marivo.analysis.core.sampling import SamplingFailureContext, SamplingGroup
from marivo.analysis.materialization.errors import MaterializationError


@dataclass
class SamplingWitness:
    coverage: SampleCoverage
    rows: pd.DataFrame
    keys: tuple[str, ...]
    group_count: int | None = None

    def demand(self, coordinates: pd.DataFrame, keys: tuple[str, ...]) -> None:
        """Keep only witnesses whose complete current coordinate is demanded."""
        if keys:
            self.rows = self.rows.merge(
                coordinates.loc[:, list(keys)].drop_duplicates(), on=list(keys)
            )
        elif coordinates.empty:
            self.rows = self.rows.iloc[:0]


def _scalar(value: object) -> FrozenScalar | None:
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, str, int, float, date, Decimal)):
        return (
            None
            if (
                (isinstance(value, float) and value != value)
                or (isinstance(value, Decimal) and value.is_nan())
            )
            else value
        )
    raise TypeError(f"Unsupported sampling coordinate: {type(value).__name__}")


def sampling_context(
    witness: SamplingWitness, *, group_count: int | None = None
) -> SamplingFailureContext:
    coverage, rows, keys = witness.coverage, witness.rows, witness.keys
    group_count = witness.group_count if group_count is None else group_count
    assert coverage.period is not None
    names = dict(coverage.coordinate_names)
    groups: dict[tuple[FrozenScalar | None, ...], list[int]] = {}
    for index, values in enumerate(rows.loc[:, list(keys)].itertuples(index=False, name=None)):
        groups.setdefault(tuple(_scalar(value) for value in values), []).append(index)
    ordered = sorted(groups, key=lambda values: tuple(repr(value) for value in values))
    captured: list[SamplingGroup] = []
    for values in ordered[:6]:
        group = rows.iloc[groups[values]]
        points = tuple(
            sorted({pd.Timestamp(value).to_pydatetime() for value in group[coverage.key]})
        )
        observed = int(group["__sampling_observed"].iloc[0])
        captured.append(
            SamplingGroup(
                tuple(
                    (names.get(key, key), value)
                    for key, value in zip(keys, values, strict=True)
                    if key not in (coverage.output_slot,) or names.get(key) != "period"
                ),
                len(coverage.points),
                observed,
                points[:3],
                len(coverage.points) - observed > 3,
            )
        )
    return SamplingFailureContext(
        coverage.metric,
        coverage.field,
        coverage.reducer,
        coverage.occurrence,
        coverage.period,
        tuple(captured),
        (group_count if group_count is not None else len(groups)) > 6,
    )


def sampling_error(context: SamplingFailureContext) -> MaterializationError:
    return MaterializationError(
        expected="complete fixed-cadence state samples",
        received=f"missing state samples for {context.metric} on {context.field}",
        repair=(
            f"Check {context.field}'s sample_interval declaration and the missing instants below; "
            "restore missing source samples or select the intended observation interval. "
            "sample_interval declares exact clock points, not hourly buckets."
        ),
        stage="sample_coverage",
        help_target="session.observe",
        sampling_context=context,
    )


def record_witness(
    coverage: SampleCoverage,
    domain: pd.DataFrame,
    missing: pd.Series,
    keys: tuple[str, ...],
) -> SamplingWitness:
    group_keys = tuple(key for key in keys if key != coverage.key)
    rows = domain.loc[missing, list(keys)].drop_duplicates().copy()
    if group_keys:
        counts = rows.groupby(list(group_keys), dropna=False)[coverage.key].transform("count")
        rows["__sampling_observed"] = len(coverage.points) - counts
    else:
        rows["__sampling_observed"] = len(coverage.points) - len(rows)
    return SamplingWitness(coverage, rows, group_keys)


def native_sampling_witness(
    coverages: tuple[SampleCoverage, ...],
    frame: pd.DataFrame,
) -> SamplingWitness | None:
    from marivo.analysis.compiler.sampled_mean import expected_wall_points

    for index, coverage in enumerate(coverages):
        prefix = f"__sampling_{index}_"
        if not coverage.field or prefix + "count" not in frame:
            continue
        keys = tuple(
            name for name in frame.columns if name.startswith("key.") and name != coverage.key
        )
        selected = frame.loc[frame[prefix + "count"].fillna(0) > 0]
        if selected.empty:
            continue
        points = (
            expected_wall_points(coverage.points, coverage.wall_timezone)
            if coverage.wall_timezone
            else coverage.points
        )
        instants = dict(zip(points, coverage.points, strict=True))
        pieces: list[pd.DataFrame] = []
        for position in range(3):
            column = prefix + str(position)
            part = selected.loc[selected[column].notna(), [*keys, column, prefix + "count"]].copy()
            part[coverage.key] = [
                instants[pd.Timestamp(point).to_pydatetime()] for point in part[column]
            ]
            part["__sampling_observed"] = len(coverage.points) - part[prefix + "count"]
            if not part.empty:
                pieces.append(part.loc[:, [*keys, coverage.key, "__sampling_observed"]])
        return SamplingWitness(
            coverage,
            pd.concat(pieces, ignore_index=True),
            keys,
            int(selected["__sampling_group_count"].iloc[0]),
        )
    return None
