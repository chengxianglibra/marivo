"""Complete identity-free pair preparation and descriptive numerical reductions."""

from __future__ import annotations

import math
from datetime import date, datetime
from itertools import combinations

import numpy as np
import pandas as pd
import pyarrow as pa
from scipy import stats

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.observation.fold_contracts import decode_fold_authority
from marivo.analysis.operators.association_contracts import (
    COUNT_NAMES,
    MAX_CANDIDATES,
    PAIR_NAMES,
    AssociationSearchSummary,
    CorrelateSpecV1,
    candidate_count,
    selection_key,
)
from marivo.analysis.operators.errors import correlation_error
from marivo.analysis.operators.row_values import compare_value


def shifted_time(value: object, offset: int, spec: CorrelateSpecV1) -> pd.Timestamp | None:
    authority = decode_fold_authority(spec.semantics.fold_authority)
    grain = authority.time_grain()
    if grain is None:
        raise correlation_error("bound time grain", "missing lag authority")
    if not isinstance(value, (date, datetime, str, pd.Timestamp)):
        raise correlation_error("temporal bucket coordinate", "invalid timestamp")
    stamp = pd.Timestamp(value)
    if grain.kind == "semantic" and grain.level != "day":
        snapshot = authority.temporal_snapshot()
        if snapshot is None:
            raise correlation_error("retained certified calendar", "missing calendar snapshot")
        starts = tuple(
            pd.Timestamp(p.start_date)
            for p in sorted(snapshot.periods, key=lambda p: p.start_date)
            if p.level_name == grain.level
        )
        local = stamp.tz_localize(None) if stamp.tzinfo is not None else stamp
        if local not in starts:
            raise correlation_error("certified time bucket", "unregistered calendar coordinate")
        position = starts.index(local) + offset
        if not 0 <= position < len(starts):
            return None
        result = starts[position]
        return result.tz_localize(stamp.tzinfo) if stamp.tzinfo is not None else result
    unit = grain.unit if grain.kind == "builtin" else "day"
    count = (grain.count or 1) * offset
    if unit == "quarter":
        unit, count = "month", count * 3
    try:
        offsets = {
            "second": lambda: pd.DateOffset(seconds=count),
            "minute": lambda: pd.DateOffset(minutes=count),
            "hour": lambda: pd.DateOffset(hours=count),
            "day": lambda: pd.DateOffset(days=count),
            "week": lambda: pd.DateOffset(weeks=count),
            "month": lambda: pd.DateOffset(months=count),
            "year": lambda: pd.DateOffset(years=count),
        }
        return stamp + offsets[str(unit)]()
    except (ValueError, OverflowError, pd.errors.OutOfBoundsDatetime):
        return None


def prepare_local(frame: pd.DataFrame, spec: CorrelateSpecV1) -> pd.DataFrame:
    if spec.semantics.input_shape == "entity":
        raise correlation_error("source-private Entity pair preparation", "raw local Entity input")
    dims = spec.dimensions
    # Iterating explicitly avoids pandas GroupBy.__len__ rejecting null groups.
    groups = (
        list(iter(frame.groupby(list(dims), sort=False, dropna=False))) if dims else [((), frame)]
    )
    candidates = candidate_count(
        len(spec.metric_names), len(spec.semantics.lag_offsets), len(groups)
    )
    if candidates > MAX_CANDIDATES or not groups:
        raise correlation_error(
            "1-4096 pair/lag/series candidates", "empty observations or candidate ceiling exceeded"
        )
    rows: list[dict[str, object]] = []
    for key, group in groups:
        coordinate = key if isinstance(key, tuple) else (key,)
        dimension_values = dict(zip(dims, coordinate, strict=True))
        for a, b in combinations(range(len(spec.metric_names)), 2):
            for lag in spec.semantics.lag_offsets:
                left = group[spec.metric_names[a]].tolist()
                right = group[spec.metric_names[b]].tolist()
                # Missing values stay missing; NaN and infinities are contradictions.
                for value in (*left, *right):
                    if value is not None and value is not pd.NA and not math.isfinite(value):
                        raise correlation_error(
                            "finite non-null observations", "non-finite Metric input"
                        )
                positions = list(range(len(group)))
                if spec.time_name is not None:
                    times = group[spec.time_name].tolist()
                    lookup = {pd.Timestamp(value): index for index, value in enumerate(times)}
                    positions = [
                        lookup.get(shifted, -1)
                        if (shifted := shifted_time(value, lag, spec)) is not None
                        else -1
                        for value in times
                    ]
                pairs = [(left[i], right[j]) for i, j in enumerate(positions) if j >= 0]
                complete = [
                    (x, y)
                    for x, y in pairs
                    if x is not None and x is not pd.NA and y is not None and y is not pd.NA
                ]
                base = {
                    **dimension_values,
                    "metric_key_a": spec.semantics.metric_keys[a],
                    "metric_key_b": spec.semantics.metric_keys[b],
                    "lag_offset": lag,
                    "input_observation_count": len(group),
                    "matched_observation_count": len(pairs),
                    "null_pair_count": len(pairs) - len(complete),
                    "complete_pair_count": len(complete),
                }
                for x, y in complete or [(None, None)]:
                    rows.append({**base, "value_a": x, "value_b": y})
    return pd.DataFrame(rows, columns=(*dims, *PAIR_NAMES)).convert_dtypes(dtype_backend="pyarrow")


def validate_pair_schema(schema: pa.Schema, spec: CorrelateSpecV1) -> None:
    if tuple(schema.names) != (*spec.dimensions, *PAIR_NAMES):
        raise correlation_error("exact identity-free pair schema", "unexpected pair columns")
    from marivo.analysis.materialization.storage import _matches_type

    dims = {f.name: f for f in spec.output_row.schema.columns if f.role_id == "dimension"}
    for name, field in dims.items():
        if not _matches_type(field.logical_type_id, schema.field(name).type):
            raise correlation_error(
                "exact retained Dimension types", "pair coordinate type mismatch"
            )
    for name in ("lag_offset", *COUNT_NAMES):
        if schema.field(name).type != pa.int64():
            raise correlation_error(
                "signed int64 lag and pair counts",
                f"invalid count type for {name}: {schema.field(name).type}",
            )
    for name in ("metric_key_a", "metric_key_b"):
        if not pa.types.is_string(schema.field(name).type):
            raise correlation_error(
                "canonical Metric identity strings", "invalid pair identity type"
            )
    for name in ("value_a", "value_b"):
        kind = schema.field(name).type
        if not (
            pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_decimal(kind)
        ):
            raise correlation_error("complete numeric pair values", "non-numeric pair input")


def select_candidates(rows: list[dict[str, object]], spec: CorrelateSpecV1) -> None:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        key = tuple(row[name] for name in (*spec.dimensions, "metric_key_a", "metric_key_b"))
        groups.setdefault(key, []).append(row)
    if not groups:
        raise correlation_error(
            "at least one valid candidate per pair/series", "empty observations"
        )
    for candidates in groups.values():
        valid = [row for row in candidates if row["status"] == "valid"]
        if not valid:
            raise correlation_error(
                "at least one valid candidate per pair/series", "no valid correlation candidate"
            )

        def selection(row: dict[str, object]) -> tuple[float | int, ...]:
            coefficient = row["coefficient"]
            lag = row.get("lag_offset", 0)
            assert isinstance(coefficient, float) and isinstance(lag, int)
            return selection_key(coefficient, lag)

        selected = min(valid, key=selection)
        if spec.time_name is not None:
            for row in candidates:
                row["selected_for_pair"] = row is selected


def execute_pairs(frame: pd.DataFrame, spec: CorrelateSpecV1) -> pd.DataFrame:
    keys = (*spec.dimensions, "metric_key_a", "metric_key_b", "lag_offset")
    groups = list(frame.groupby(list(keys), sort=False, dropna=False))
    expected_pairs = set(combinations(spec.semantics.metric_keys, 2))
    if not groups or len(groups) > MAX_CANDIDATES:
        raise correlation_error("1-4096 complete candidates", "empty or excessive pair candidates")
    rows: list[dict[str, object]] = []
    coverage: dict[tuple[object, ...], set[tuple[str, str, int]]] = {}
    for key, group in groups:
        coordinate = key if isinstance(key, tuple) else (key,)
        row = dict(zip(keys, coordinate, strict=True))
        a, b, lag = row["metric_key_a"], row["metric_key_b"], row["lag_offset"]
        if (
            not isinstance(a, str)
            or not isinstance(b, str)
            or not isinstance(lag, int)
            or (a, b) not in expected_pairs
            or lag not in spec.semantics.lag_offsets
        ):
            raise correlation_error(
                "authored pair and lag identities", "unknown numerical candidate"
            )
        series = tuple(
            None
            if row[name] is None
            or row[name] is pd.NA
            or (isinstance((coordinate_value := row[name]), float) and math.isnan(coordinate_value))
            else row[name]
            for name in spec.dimensions
        )
        coverage.setdefault(series, set()).add((a, b, lag))
        counts = []
        for name in COUNT_NAMES:
            unique = group[name].drop_duplicates().tolist()
            if (
                len(unique) != 1
                or not isinstance(unique[0], int)
                or isinstance(unique[0], bool)
                or unique[0] < 0
            ):
                raise correlation_error(
                    "consistent source-certified candidate counts", "invalid pair count"
                )
            counts.append(unique[0])
            row[name] = unique[0]
        total, matched, nulls, complete = counts
        if matched > total or nulls + complete != matched or len(group) != max(complete, 1):
            raise correlation_error(
                "complete numeric pairs and exact null accounting", "pair counts contradict input"
            )
        xs = group.value_a.tolist()
        ys = group.value_b.tolist()
        if complete == 0:
            if any(x is not None and x is not pd.NA for x in (*xs, *ys)):
                raise correlation_error("one empty candidate sentinel", "invalid empty pair")
            xs = []
            ys = []
        elif any(x is None or x is pd.NA or not math.isfinite(x) for x in (*xs, *ys)):
            raise correlation_error(
                "finite complete numeric pairs", "null or non-finite prepared pair"
            )
        constant_a = complete >= 2 and all(compare_value(xs[0], x) == 0 for x in xs)
        constant_b = complete >= 2 and all(compare_value(ys[0], y) == 0 for y in ys)
        status = (
            "insufficient_pairs"
            if complete < 2
            else "constant_both"
            if constant_a and constant_b
            else "constant_a"
            if constant_a
            else "constant_b"
            if constant_b
            else "valid"
        )
        coefficient: float | None = None
        if status == "valid":
            if spec.semantics.method == "spearman":
                value = stats.pearsonr(
                    stats.rankdata(xs, method="average"), stats.rankdata(ys, method="average")
                ).statistic
            elif spec.semantics.method == "kendall":
                value = stats.kendalltau(xs, ys, variant="b", method="auto").statistic
            else:
                value = stats.pearsonr(
                    np.asarray([x - xs[0] for x in xs], dtype=float),
                    np.asarray([y - ys[0] for y in ys], dtype=float),
                ).statistic
            coefficient = float(value)
            if not math.isfinite(coefficient) or abs(coefficient) > 1 + 1e-12:
                raise correlation_error(
                    "finite coefficient in [-1,1]", "numerical execution contradiction"
                )
            if abs(coefficient) >= 1 - 1e-12:
                coefficient = math.copysign(1.0, coefficient)
        row.update(status=status, coefficient=coefficient, lag_boundary_drop_count=total - matched)
        rows.append(row)
    expected = {(a, b, k) for a, b in expected_pairs for k in spec.semantics.lag_offsets}
    if any(items != expected for items in coverage.values()):
        raise correlation_error(
            "all authored pair/lag candidates for every series", "missing prepared candidate"
        )
    select_candidates(rows, spec)
    fields = spec.output_row.schema.columns
    arrays = []
    from marivo.analysis.materialization.storage import _matches_type

    for f in fields:
        values = [row[f.name] for row in rows]
        if f.role_id == "dimension":
            kind = frame[f.name].dtype
            dtype = kind.pyarrow_dtype if isinstance(kind, pd.ArrowDtype) else pa.array(values).type
        else:
            dtype = {
                "int64": pa.int64(),
                "float64": pa.float64(),
                "boolean": pa.bool_(),
                "string": pa.string(),
            }[f.logical_type_id]
        if not _matches_type(f.logical_type_id, dtype):
            raise correlation_error("exact output type", "Association type contradiction")
        arrays.append(pa.array(values, type=dtype, from_pandas=True))
    result: pd.DataFrame = pa.Table.from_arrays(arrays, names=[f.name for f in fields]).to_pandas(
        types_mapper=pd.ArrowDtype
    )
    from marivo.analysis.operators.row import ordered

    return ordered(result, spec.output_row, spec.output_rows)


def summarize_search(frame: pd.DataFrame, row: DatasetRowContract) -> AssociationSearchSummary:
    """Summarize a complete bounded result before any downstream selection."""
    dims = [f.name for f in row.schema.columns if f.role_id == "dimension"]
    series = len(frame[dims].drop_duplicates()) if dims else int(len(frame) > 0)
    counts = [int(v) for v in frame.complete_pair_count.tolist()]
    nulls = [int(v) for v in frame.null_pair_count.tolist()]
    return AssociationSearchSummary(
        series,
        len(frame),
        (min(counts, default=0), max(counts, default=0)),
        (min(nulls, default=0), max(nulls, default=0)),
    )
