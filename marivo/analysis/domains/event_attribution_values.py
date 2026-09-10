"""Independent compact-component Event allocation and endpoint reconciliation."""

from __future__ import annotations

import math
from functools import cmp_to_key

import pandas as pd
import pyarrow as pa

from marivo.analysis.domains.event_attribution import (
    COMPONENT_COLUMNS,
    COMPONENT_CONTRACT,
    COMPONENT_NAMES,
    COMPONENT_ROLE,
    METHOD,
    FunnelAttributeSpec,
)
from marivo.analysis.domains.event_comparison_values import execute_compare
from marivo.analysis.operators.attribute_values import reconciles
from marivo.analysis.operators.errors import attribution_error
from marivo.analysis.operators.row import PartFrame, ordered
from marivo.analysis.operators.row_values import compare_value, frame_keys


def _allocate(
    original: pd.DataFrame, current: pd.DataFrame, baseline: pd.DataFrame, spec: FunnelAttributeSpec
) -> pd.DataFrame:
    paired = execute_compare(current, baseline, spec.expanded)
    selected = original[original.step_key == spec.step_key]
    if len(selected) != 1 or selected.calculation_status.iloc[0] != "ok":
        raise attribution_error(
            "one defined target within the selected Delta scope", "target removed or undefined"
        )
    paired = paired[paired.step_key == spec.step_key].reset_index(drop=True)
    axes = tuple(f.name for f in spec.axis_fields)
    keys = frame_keys(paired, axes)
    states = [
        tuple(int(row[name]) for name in COMPONENT_NAMES)
        for row in paired.to_dict(orient="records")
    ]

    def fold(indices: list[int]) -> tuple[int, ...]:
        return tuple(sum(states[i][j] for i in indices) for j in range(4))

    totals = fold(list(range(len(states))))
    if (
        any(totals[i] != selected[name].iloc[0] for i, name in enumerate(COMPONENT_NAMES))
        or totals[1] <= 0
        or totals[3] <= 0
    ):
        raise attribution_error(
            "complete components reproducing the original endpoint", "endpoint component mismatch"
        )
    overall = float(totals[0]) / float(totals[1]) - float(totals[2]) / float(totals[3])
    if not reconciles(overall, float(selected.loss_rate_delta.iloc[0])):
        raise attribution_error("the exact selected endpoint delta", "endpoint mismatch")
    mapped, other = list(keys), [(False,) * len(axes) for _ in keys]
    if spec.top_k is not None:
        for j in range(len(axes)):
            parents: dict[tuple[object, ...], dict[object, list[int]]] = {}
            for i, key in enumerate(keys):
                parents.setdefault((*mapped[i][:j], other[i][:j]), {}).setdefault(
                    key[j], []
                ).append(i)
            for members in parents.values():
                scores = {
                    member: fold(indices)[1] + fold(indices)[3]
                    for member, indices in members.items()
                }

                def compare(a: object, b: object, scores: dict[object, int] = scores) -> int:
                    return -compare_value(scores[a], scores[b]) or compare_value(a, b)

                kept = set(sorted(members, key=cmp_to_key(compare))[: spec.top_k])
                for member, indices in members.items():
                    if member not in kept:
                        for i in indices:
                            mapped[i] = (*mapped[i][:j], None, *mapped[i][j + 1 :])
                            other[i] = (*other[i][:j], True, *other[i][j + 1 :])
    rows: list[dict[str, object]] = []
    for size in (len(axes),) if spec.mode == "joint" else range(1, len(axes) + 1):
        partitions: dict[tuple[object, ...], list[int]] = {}
        for i in range(len(keys)):
            coordinates = (*mapped[i][:size], *((None,) * (len(axes) - size)))
            mask = (*other[i][:size], *((False,) * (len(axes) - size)))
            partitions.setdefault((*coordinates, mask), []).append(i)
        resolution: list[dict[str, object]] = []
        for key, indices in partitions.items():
            components = fold(indices)
            a, _, b, _ = components
            for kind, ca, cb in (
                ("loss", float(a) / float(totals[1]), float(b) / float(totals[1])),
                ("denominator_mix", 0.0, -b * (1.0 / totals[1] - 1.0 / totals[3])),
            ):
                resolution.append(
                    {
                        **dict(zip(axes, key[:-1], strict=True)),
                        **dict(zip(COMPONENT_COLUMNS, (*components, *totals), strict=True)),
                        "active_axis_mask": tuple(i < size for i in range(len(axes))),
                        "other_mask": key[-1],
                        "contribution_kind": kind,
                        "current_value": ca,
                        "baseline_value": cb,
                        "overall_delta": overall,
                        "contribution": ca - cb,
                        "method": METHOD,
                        "causal_claim": "none",
                        "status": "zero_total_delta" if overall == 0 else "ok",
                    }
                )

        def number(row: dict[str, object]) -> float:
            value = row["contribution"]
            assert isinstance(value, float)
            return value

        if not reconciles(math.fsum(number(row) for row in resolution), overall):
            raise attribution_error("complete resolution reconciliation", "contribution mismatch")
        for name, endpoint in (
            ("current_value", float(totals[0]) / float(totals[1])),
            ("baseline_value", float(totals[2]) / float(totals[3])),
        ):
            values = [r[name] for r in resolution]
            if not all(isinstance(v, float) for v in values) or not reconciles(
                math.fsum(v for v in values if isinstance(v, float)), endpoint
            ):
                raise attribution_error(
                    "complete side terms reproducing the original rates", "side endpoint mismatch"
                )
        positive = math.fsum(max(number(row), 0) for row in resolution)
        negative = math.fsum(max(-number(row), 0) for row in resolution)

        def rank(a: dict[str, object], b: dict[str, object]) -> int:
            return -compare_value(abs(number(a)), abs(number(b))) or compare_value(
                tuple(a[n] for n in (*axes, "other_mask", "contribution_kind")),
                tuple(b[n] for n in (*axes, "other_mask", "contribution_kind")),
            )

        for i, row in enumerate(sorted(resolution, key=cmp_to_key(rank)), 1):
            value = number(row)
            row.update(
                contribution_rank=i,
                share_of_total_delta=value / overall if overall else None,
                share_of_positive_pool=max(value, 0) / positive if positive else None,
                share_of_negative_pool=max(-value, 0) / negative if negative else None,
            )
        rows.extend(resolution)
    types = {"string": pa.string(), "float64": pa.float64(), "int64": pa.int64()}
    result = pd.DataFrame(
        {
            f.name: pd.Series(
                [r[f.name] for r in rows],
                dtype=current[f.name].dtype
                if f.name in axes
                else pd.ArrowDtype(
                    pa.list_(pa.bool_(), len(axes))
                    if f.logical_type_id.startswith("bool_tuple:")
                    else types[f.logical_type_id]
                ),
            )
            for f in spec.output_row.schema.columns
        }
    )
    for name in COMPONENT_COLUMNS:
        result[name] = pd.Series([r[name] for r in rows], dtype=pd.ArrowDtype(pa.int64()))
    return ordered(result, spec.output_row, spec.output_rows)


def execute_attribute_with_parts(
    original: pd.DataFrame, current: pd.DataFrame, baseline: pd.DataFrame, spec: FunnelAttributeSpec
) -> tuple[pd.DataFrame, tuple[PartFrame, ...]]:
    complete = _allocate(original, current, baseline, spec)
    keys = tuple(
        f.name
        for f in spec.output_row.schema.columns
        if f.field_id in spec.output_row.key_field_ids
    )
    part = complete.loc[:, [*keys, *COMPONENT_COLUMNS]]
    schema = pa.Table.from_pandas(part, preserve_index=False).schema
    return complete.loc[:, [f.name for f in spec.output_row.schema.columns]], (
        PartFrame(COMPONENT_ROLE, COMPONENT_CONTRACT, 1, schema, keys, part),
    )


def execute_attribute(
    original: pd.DataFrame, current: pd.DataFrame, baseline: pd.DataFrame, spec: FunnelAttributeSpec
) -> pd.DataFrame:
    return execute_attribute_with_parts(original, current, baseline, spec)[0]
