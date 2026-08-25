"""Internal deterministic Top-K grouping for attribution players."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype, is_unsigned_integer_dtype

from marivo.analysis.frames._attribution_columns import ATTRIBUTION_OTHER_MASK_COLUMN

_OTHER_TOKEN = ("other", "")


def _scalar_token(value: object) -> tuple[str, str]:
    if bool(pd.isna(cast("Any", value))):
        return ("null", "")
    item = getattr(value, "item", None)
    if callable(item):
        value = item()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return (type(value).__name__, str(isoformat()))
    return (type(value).__name__, repr(value))


def _mask_axis_values(series: pd.Series, collapsed: list[bool]) -> pd.Series:
    mask = pd.Series(collapsed, index=series.index, dtype=bool)
    if not bool(mask.any()):
        return series
    dtype = series.dtype
    if not isinstance(dtype, pd.api.extensions.ExtensionDtype):
        if is_bool_dtype(dtype):
            series = series.astype("boolean")
        elif is_integer_dtype(dtype):
            bits = int(dtype.itemsize) * 8
            prefix = "U" if is_unsigned_integer_dtype(dtype) else ""
            series = series.astype(pd.api.types.pandas_dtype(f"{prefix}Int{bits}"))
    return series.mask(mask, pd.NA)


def _column_position(frame: pd.DataFrame, column: str) -> int:
    location = frame.columns.get_loc(column)
    if not isinstance(location, int):
        raise ValueError(f"Top-K input column {column!r} must be unique")
    return location


@dataclass(frozen=True)
class AttributionTopKMapV1:
    """Execution-local selected member identities for each ordered parent path."""

    limit: int
    axis_columns: tuple[str, ...]
    kept_by_level: tuple[dict[tuple[tuple[str, str], ...], frozenset[tuple[str, str]]], ...]
    value_by_token: dict[tuple[str, str], object]
    original_scope_count: int
    collapsed_scope_count: int

    def map_frame(self, frame: pd.DataFrame, *, level: int | None = None) -> pd.DataFrame:
        effective_level = len(self.axis_columns) if level is None else level
        if not 1 <= effective_level <= len(self.axis_columns):
            raise ValueError("Top-K mapping level must identify one non-empty axis prefix")
        out = frame.copy()
        axis_positions = {
            column: _column_position(out, column) for column in self.axis_columns[:effective_level]
        }
        masks: list[int] = []
        collapsed_values: dict[str, list[bool]] = {
            column: [] for column in self.axis_columns[:effective_level]
        }
        for row in out.itertuples(index=False, name=None):
            parent: tuple[tuple[str, str], ...] = ()
            mask = 0
            for index, column in enumerate(self.axis_columns[:effective_level]):
                token = _scalar_token(row[axis_positions[column]])
                kept = self.kept_by_level[index].get(parent, frozenset())
                if token in kept:
                    collapsed_values[column].append(False)
                    parent = (*parent, token)
                else:
                    collapsed_values[column].append(True)
                    parent = (*parent, _OTHER_TOKEN)
                    mask |= 1 << index
            masks.append(mask)
        for column, collapsed in collapsed_values.items():
            out[column] = _mask_axis_values(out[column], collapsed)
        out[ATTRIBUTION_OTHER_MASK_COLUMN] = masks
        return out


def build_top_k_map_from_level_scores(
    level_scores: Sequence[pd.DataFrame],
    *,
    axis_columns: list[str],
    score_column: str,
    limit: int,
) -> AttributionTopKMapV1:
    """Select K children from independently computed scores for every axis prefix."""
    if len(level_scores) != len(axis_columns):
        raise ValueError("Top-K level scores must cover every ordered axis prefix")
    normalized_scores: list[pd.DataFrame] = []
    value_by_token: dict[tuple[str, str], object] = {}
    for level, frame in enumerate(level_scores, start=1):
        prefix = axis_columns[:level]
        scoped = frame[[*prefix, score_column]].copy()
        scoped[score_column] = pd.to_numeric(scoped[score_column], errors="raise").abs()
        scoped = scoped.groupby(prefix, dropna=False, sort=False)[score_column].sum().reset_index()
        normalized_scores.append(scoped)
        for column in prefix:
            for value in scoped[column].array:
                value_by_token.setdefault(_scalar_token(value), value)

    kept_levels: list[dict[tuple[tuple[str, str], ...], frozenset[tuple[str, str]]]] = []
    for level_index, (column, scores) in enumerate(
        zip(axis_columns, normalized_scores, strict=True)
    ):
        positions = {
            name: _column_position(scores, name) for name in axis_columns[: level_index + 1]
        }
        score_position = _column_position(scores, score_column)
        candidates: dict[tuple[tuple[str, str], ...], dict[tuple[str, str], float]] = {}
        for row in scores.itertuples(index=False, name=None):
            mapped_parent: tuple[tuple[str, str], ...] = ()
            for parent_index, parent_column in enumerate(axis_columns[:level_index]):
                token = _scalar_token(row[positions[parent_column]])
                kept = kept_levels[parent_index].get(mapped_parent, frozenset())
                mapped_parent = (
                    *mapped_parent,
                    token if token in kept else _OTHER_TOKEN,
                )
            token = _scalar_token(row[positions[column]])
            by_child = candidates.setdefault(mapped_parent, {})
            by_child[token] = by_child.get(token, 0.0) + float(row[score_position])
        selected: dict[tuple[tuple[str, str], ...], frozenset[tuple[str, str]]] = {}
        for parent, children in candidates.items():
            ordered = sorted(children.items(), key=lambda item: (-item[1], item[0]))
            selected[parent] = frozenset(token for token, _ in ordered[:limit])
        kept_levels.append(selected)

    full_scores = normalized_scores[-1]
    full_positions = {column: _column_position(full_scores, column) for column in axis_columns}
    collapsed_paths: set[tuple[tuple[str, str], ...]] = set()
    for row in full_scores.itertuples(index=False, name=None):
        path: tuple[tuple[str, str], ...] = ()
        for index, column in enumerate(axis_columns):
            token = _scalar_token(row[full_positions[column]])
            kept = kept_levels[index].get(path, frozenset())
            path = (*path, token if token in kept else _OTHER_TOKEN)
        collapsed_paths.add(path)
    return AttributionTopKMapV1(
        limit=limit,
        axis_columns=tuple(axis_columns),
        kept_by_level=tuple(kept_levels),
        value_by_token=value_by_token,
        original_scope_count=len(full_scores),
        collapsed_scope_count=len(collapsed_paths),
    )


def build_top_k_map(
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    axis_columns: list[str],
    score_column: str,
    limit: int,
) -> AttributionTopKMapV1:
    """Select K children per mapped parent using one stable two-period score."""
    combined = pd.concat(
        [current[[*axis_columns, score_column]], baseline[[*axis_columns, score_column]]],
        ignore_index=True,
    )
    combined[score_column] = pd.to_numeric(combined[score_column], errors="raise").abs()
    combined = (
        combined.groupby(axis_columns, dropna=False, sort=False)[score_column].sum().reset_index()
    )
    return build_top_k_map_from_level_scores(
        [
            combined.groupby(axis_columns[:level], dropna=False, sort=False)[score_column]
            .sum()
            .reset_index()
            for level in range(1, len(axis_columns) + 1)
        ],
        axis_columns=axis_columns,
        score_column=score_column,
        limit=limit,
    )


def validate_top_k(top_k: int | None) -> int | None:
    if top_k is None:
        return None
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        from marivo.analysis.errors import SemanticKindMismatchError

        raise SemanticKindMismatchError(
            message="attribute top_k must be a positive integer",
            context={
                "argument": "top_k",
                "reason": "invalid_top_k",
                "top_k": top_k,
            },
        )
    return top_k


__all__ = [
    "AttributionTopKMapV1",
    "build_top_k_map",
    "build_top_k_map_from_level_scores",
    "validate_top_k",
]
