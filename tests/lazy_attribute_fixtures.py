"""Pure exact side-state examples shared by Attribution numerical and compiler tests."""

from dataclasses import replace
from typing import Literal

import pandas as pd
import pyarrow as pa

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.contracts import EntityReducedMetricSemantics
from marivo.analysis.observation.fold_contracts import fold_part_role
from marivo.analysis.operators.attribution_contracts import AttributePayload, AttributeSpecV1
from marivo.analysis.operators.compare import execute_compare
from marivo.analysis.operators.contracts import ComparePayload
from marivo.analysis.operators.delta_state import execute_compare_parts
from marivo.analysis.operators.row import PartFrame
from marivo.refs import ref
from tests.lazy_observation_fixtures import make_sources

REGION = ref.dimension("sales.customers.region")
CHANNEL = ref.dimension("sales.orders.channel")


def inputs(
    name: str,
    keys: list[tuple[str | None, ...]],
    current: list[tuple[float, int]],
    baseline: list[tuple[float, int]],
    *,
    mode: Literal["joint", "hierarchy"] = "joint",
    top_k: int | None = None,
    decompose: Literal["all", "first"] = "all",
    current_absent: tuple[int, ...] = (),
    baseline_absent: tuple[int, ...] = (),
) -> tuple[pd.DataFrame, AttributeSpecV1, tuple[PartFrame, ...]]:
    axes = (REGION,) if len(keys[0]) == 1 else (REGION, CHANNEL)
    metric = make_sources().observe(ref.metric(f"sales.{name}")).with_dimensions(*axes).aggregate()
    delta = metric.compare(metric)
    attribution = delta.attribute(
        axes=axes if decompose == "all" else (REGION,), mode=mode, top_k=top_k
    )
    assert isinstance(delta._root, LogicalRootHandle) and isinstance(
        delta._root.payload, ComparePayload
    )
    assert isinstance(attribution._root, LogicalRootHandle) and isinstance(
        attribution._root.payload, AttributePayload
    )
    compare_spec = delta._root.payload.spec
    semantics = metric.row_contract.family_semantics
    assert isinstance(semantics, EntityReducedMetricSemantics)
    authority = semantics.metric_folds[0]
    key_names = tuple(field.name for field in metric.schema.columns if field.role_id == "dimension")
    metric_name = next(field.name for field in metric.schema.columns if field.role_id == "metric")
    sides: list[tuple[pd.DataFrame, tuple[PartFrame, ...]]] = []
    for rows, absent in ((current, current_absent), (baseline, baseline_absent)):
        values = [
            int(n)
            if name == "order_count"
            else n
            if name == "revenue"
            else None
            if w == 0
            else n / w
            for n, w in rows
        ]
        primary = pd.DataFrame(
            {
                **{
                    key_name: pd.Series([key[i] for key in keys], dtype="string[pyarrow]")
                    for i, key_name in enumerate(key_names)
                },
                metric_name: pd.Series(
                    values, dtype="int64[pyarrow]" if name == "order_count" else "float64[pyarrow]"
                ),
            }
        )
        state: dict[str, pd.Series] = {key_name: primary[key_name] for key_name in key_names}
        for component in authority.components:
            for kind, column in component.state_columns:
                if kind in ("sum", "weighted_numerator"):
                    numbers = [n for n, _ in rows]
                    dtype = "float64[pyarrow]"
                elif kind == "weight_sum":
                    numbers = [float(w) for _, w in rows]
                    dtype = "float64[pyarrow]"
                else:
                    numbers = [int(n) if name == "order_count" else w for n, w in rows]
                    dtype = "int64[pyarrow]"
                state[column] = pd.Series(numbers, dtype=dtype)
        selected = [index for index in range(len(keys)) if index not in absent]
        primary = primary.iloc[selected].reset_index(drop=True)
        part_frame = pd.DataFrame(state).iloc[selected].reset_index(drop=True)
        schema = pa.Schema.from_pandas(part_frame, preserve_index=False)
        part = PartFrame(
            fold_part_role(authority),
            "metric.sufficient_components",
            1,
            schema,
            key_names,
            part_frame,
        )
        sides.append((primary, (part,)))
    output = execute_compare(sides[0][0], sides[1][0], compare_spec)
    parts = execute_compare_parts(
        sides[0][0], sides[1][0], compare_spec, sides[0][1], sides[1][1], output
    )
    return output, attribution._root.payload.spec, parts


def signed_basis_inputs() -> tuple[pd.DataFrame, AttributeSpecV1, tuple[PartFrame, ...]]:
    """Retain positive support counts while one partition has a negative weight sum."""
    frame, spec, parts = inputs(
        "weighted_amount",
        [("a",), ("b",)],
        [(10, 1), (12, 3)],
        [(2, 1), (4, 1)],
        top_k=1,
    )
    frame.loc[0, "current_value"] = -10
    frame.loc[0, "delta"] = -12
    frame.loc[0, "relative_delta"] = -6
    state = parts[0].frame.copy()
    weight = next(name for name in state.columns if name.endswith("_weight_sum"))
    state.loc[0, weight] = -1
    return frame, spec, (replace(parts[0], frame=state), parts[1])
