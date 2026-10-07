"""Private Arrow/pandas Spearman continuation shared by source and fixed inputs."""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredLocal
from marivo.analysis.compiler.graph_plan import CheckRequirement
from marivo.analysis.core.model import ObservedQuantity
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    PartContract,
    from_pandas,
)
from marivo.analysis.methods.local import score_spearman
from marivo.analysis.methods.registry import REGISTRY


def finish_spearman(
    lowered: LoweredLocal,
    left: pa.Table,
    right: pa.Table,
    keys: tuple[str, ...],
    binding: str,
    completed: tuple[CompletedCheck, ...],
    pending: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    """Produce independent coefficient, status and pair-count vectors."""
    stage = lowered.stage
    quantity_a = stage.node.inputs[0].node.signature.quantity
    quantity_b = stage.node.inputs[1].node.signature.quantity
    assert isinstance(quantity_a, ObservedQuantity)
    assert isinstance(quantity_b, ObservedQuantity)
    scored = score_spearman(stage, left, right, keys)
    primary_schema = pa.schema(
        (
            pa.field("status", pa.string()),
            pa.field("value", pa.float64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    part_schema = pa.schema(
        (
            pa.field("pair_counts__metric_key_a", pa.string()),
            pa.field("pair_counts__metric_key_b", pa.string()),
            pa.field("pair_counts__input_observation_count", pa.int64()),
            pa.field("pair_counts__matched_observation_count", pa.int64()),
            pa.field("pair_counts__null_pair_count", pa.int64()),
            pa.field("pair_counts__complete_pair_count", pa.int64()),
        )
    )
    contract = ExchangeContract(
        stage.node.signature,
        stage.node.method,
        binding,
        primary_schema,
        (),
        (PartContract("pair_counts", part_schema, ()),),
        REGISTRY.lookup(stage.node.method).semantics.empty_cell_reasons,
        "spearman",
        pa.schema((pa.field("status", pa.string()),)),
        pending,
    )
    part = pa.Table.from_pandas(
        pd.DataFrame(
            {
                "pair_counts__metric_key_a": [str(quantity_a.metric_ref)],
                "pair_counts__metric_key_b": [str(quantity_b.metric_ref)],
                "pair_counts__input_observation_count": [scored.input_observation_count],
                "pair_counts__matched_observation_count": [scored.matched_observation_count],
                "pair_counts__null_pair_count": [scored.null_pair_count],
                "pair_counts__complete_pair_count": [scored.complete_pair_count],
            }
        ),
        schema=part_schema,
        preserve_index=False,
    )
    return from_pandas(
        pd.DataFrame(
            {
                "status": [scored.status],
                "value": [scored.coefficient],
                "cell_tag": ["defined" if scored.coefficient is not None else "undefined"],
                "cell_reason": [None if scored.coefficient is not None else scored.status],
            }
        ),
        contract,
        parts=(ExchangePart("pair_counts", part),),
        completed_checks=completed,
        validate=False,
        method_state=pa.table({"status": pa.array([scored.status], type=pa.string())}),
    )
