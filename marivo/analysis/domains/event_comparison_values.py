"""Independent bounded pandas comparison over complete compact Event cells."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from marivo.analysis.domains.event_comparison import COUNTS, FunnelCompareSpec, FunnelDeltaSemantics
from marivo.analysis.operators.errors import comparison_error
from marivo.analysis.operators.row import ordered, predicate_mask
from marivo.analysis.operators.row_values import frame_keys


def execute_compare(
    current: pd.DataFrame, baseline: pd.DataFrame, spec: FunnelCompareSpec
) -> pd.DataFrame:
    semantics = spec.output_row.family_semantics
    assert isinstance(semantics, FunnelDeltaSemantics)
    keys = tuple(
        field.name
        for field in spec.current_row.schema.columns
        if field.field_id in spec.current_row.key_field_ids
    )
    maps = []
    for frame, predicates in (
        (current, spec.current_predicates),
        (baseline, spec.baseline_predicates),
    ):
        if (
            (frame.coverage_censored_count != 0)
            | (frame.resolved_cohort_count != frame.cohort_count)
        ).any():
            raise comparison_error("complete Event follow-up", "censored funnel cells")
        for predicate in predicates:
            frame = frame.loc[predicate_mask(frame, predicate)].reset_index(drop=True)
        coordinates = frame_keys(frame, keys)
        if len(set(coordinates)) != len(coordinates):
            raise comparison_error("unique exact funnel coordinates", "duplicate funnel cells")
        maps.append(dict(zip(coordinates, frame.to_dict(orient="records"), strict=True)))
    rows: list[dict[str, object]] = []
    for key in maps[0].keys() | maps[1].keys():
        a, b = maps[0].get(key), maps[1].get(key)
        row: dict[str, object] = dict(zip(keys, key, strict=True))
        row["coordinate_presence"] = (
            "matched"
            if a is not None and b is not None
            else "current_only"
            if a is not None
            else "baseline_only"
        )
        for side, record in (("current", a), ("baseline", b)):
            for name in COUNTS:
                value = 0 if record is None else record[name]
                if type(value) is not int or value < 0:
                    raise comparison_error(
                        "exact nonnegative additive counts", "invalid funnel count"
                    )
                row[f"{side}_{name}"] = value
        ca, cb = row["current_resolved_entry_count"], row["baseline_resolved_entry_count"]
        la, lb = row["current_lost_count"], row["baseline_lost_count"]
        assert (
            isinstance(ca, int)
            and isinstance(cb, int)
            and isinstance(la, int)
            and isinstance(lb, int)
        )
        noninitial = row["step_key"] != semantics.current.journey.pattern.steps[0].key
        ok = a is not None and b is not None and ca > 0 and cb > 0 and noninitial
        row.update(
            current_loss_rate_from_previous=float(la) / float(ca) if noninitial and ca else None,
            baseline_loss_rate_from_previous=float(lb) / float(cb) if noninitial and cb else None,
            loss_rate_delta=float(la) / float(ca) - float(lb) / float(cb) if ok else None,
            calculation_status="missing_side"
            if a is None or b is None
            else "ok"
            if ok
            else "zero_denominator",
        )
        rows.append(row)
    types = {"int64": pa.int64(), "float64": pa.float64(), "string": pa.string()}
    output = pd.DataFrame(
        {
            field.name: pd.Series(
                [row[field.name] for row in rows],
                dtype=current[field.name].dtype
                if field.name in keys
                else pd.ArrowDtype(types[field.logical_type_id]),
            )
            for field in spec.output_row.schema.columns
        }
    )
    return ordered(output, spec.output_row, spec.output_rows)
