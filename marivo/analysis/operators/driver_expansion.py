"""Exact selected-coordinate anchoring for guarded local driver expansion."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.descriptors import DatasetRowContract
from marivo.analysis.operators.compare import execute_compare
from marivo.analysis.operators.delta_state import execute_compare_parts
from marivo.analysis.operators.driver_contracts import DriverCandidateSpecV1
from marivo.analysis.operators.errors import discovery_error
from marivo.analysis.operators.row import PartFrame, aligned_part_positions
from marivo.analysis.operators.row_values import frame_keys, row_key_names


def _anchor(
    original: pd.DataFrame,
    expanded: pd.DataFrame,
    parts: tuple[PartFrame, ...],
    row: DatasetRowContract,
    original_row: DatasetRowContract,
    side: str,
) -> tuple[pd.DataFrame, tuple[PartFrame, ...]]:
    fields = {field.field_id: field.name for field in row.schema.columns}
    anchors = tuple(
        field
        for field in original_row.schema.columns
        if field.field_id in original_row.key_field_ids and field.name != "comparison_ordinal"
    )
    time = next((f.name for f in row.schema.columns if f.role_id == "time_dimension"), None)
    original_names = tuple(f.name for f in anchors) + ((side + "_time",) if time else ())
    expanded_names = tuple(fields[f.field_id] for f in anchors) + ((time,) if time else ())
    original_keys = frame_keys(original, original_names)
    ordinals = original.comparison_ordinal.tolist() if time else [0] * len(original)
    selected: dict[tuple[object, ...], int] = {}
    for key, ordinal in zip(original_keys, ordinals, strict=True):
        if type(ordinal) is not int or (key in selected and selected[key] != ordinal):
            raise discovery_error(
                "one exact original ordinal per side coordinate", "ambiguous anchor"
            )
        selected[key] = ordinal
    positions = [
        index for index, key in enumerate(frame_keys(expanded, expanded_names)) if key in selected
    ]
    result = expanded.iloc[positions].reset_index(drop=True)
    if time:
        result["comparison_ordinal"] = pd.Series(
            [selected[key] for key in frame_keys(result, expanded_names)],
            dtype=pd.ArrowDtype(pa.int64()),
        )
    keys = row_key_names(row)
    expected, retained = frame_keys(expanded, keys), frame_keys(result, keys)
    filtered: list[PartFrame] = []
    for part in parts:
        if part.role == "population_sampling_state":
            filtered.append(part)
            continue
        by_key = aligned_part_positions(part, keys, expected)
        filtered.append(
            replace(
                part,
                frame=part.frame.iloc[[by_key[key] for key in retained]].reset_index(drop=True),
            )
        )
    return result, tuple(filtered)


def prepare_local_driver_expansion(
    original: pd.DataFrame,
    current: pd.DataFrame,
    baseline: pd.DataFrame,
    spec: DriverCandidateSpecV1,
    current_parts: tuple[PartFrame, ...],
    baseline_parts: tuple[PartFrame, ...],
) -> tuple[pd.DataFrame, tuple[PartFrame, ...]]:
    """Keep original selections and paired ordinals across independent source branches."""
    comparison, original_row = spec.expanded_compare, spec.original_input_row
    if comparison is None or original_row is None:
        raise discovery_error("complete logical expansion authority", "missing original contract")
    left, left_parts = _anchor(
        original, current, current_parts, comparison.current_row, original_row, "current"
    )
    right, right_parts = _anchor(
        original, baseline, baseline_parts, comparison.baseline_row, original_row, "baseline"
    )
    result = execute_compare(left, right, comparison, ordinal_preassigned=True)
    if "comparison_ordinal" in result:
        keys = row_key_names(original_row)
        by_key = {key: index for index, key in enumerate(frame_keys(original, keys))}
        output_keys = frame_keys(result, keys)
        if any(key not in by_key for key in output_keys):
            raise discovery_error("original selected comparison keys", "expanded scope escaped")
        for name in ("current_time", "baseline_time"):
            result[name] = pd.Series(
                [original[name].iloc[by_key[key]] for key in output_keys],
                dtype=original[name].dtype,
            )
    return result, execute_compare_parts(left, right, comparison, left_parts, right_parts, result)
