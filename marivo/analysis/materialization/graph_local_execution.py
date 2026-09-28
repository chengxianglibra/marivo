"""Caller-owned R4.3 fixed execution over fully checked local receipts."""

from __future__ import annotations

import hashlib

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredLocal, LoweredPlan, LoweredRelation
from marivo.analysis.compiler.graph_plan import ArtifactReadStage, CheckRequirement
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import Cell, Defined, Null, Undefined, Unknown
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    FixedInput,
    PartContract,
    from_pandas,
    from_receipts,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_spearman_execution import finish_spearman
from marivo.analysis.methods.local import arithmetic, count, count_defined
from marivo.analysis.methods.physical import ScalarType
from marivo.analysis.methods.registry import REGISTRY


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="one exact fixed graph with verified primary and required parts",
        received=received,
        repair="Use the exact selected Artifact and a qualified local method.",
        stage="graph_local",
    )


def _cells(input_value: ExchangeResult) -> tuple[Cell, ...]:
    if not {"value", "cell_tag", "cell_reason"} <= set(input_value.primary.column_names):
        raise _invalid("fixed input lacks Cell fields")
    cells: list[Cell] = []
    for row in input_value.primary.select(("value", "cell_tag", "cell_reason")).to_pylist():
        value, tag, reason = row["value"], row["cell_tag"], row["cell_reason"]
        if tag == "defined":
            cells.append(Defined(value))
        elif tag == "null":
            cells.append(Null(reason))
        elif tag == "undefined":
            cells.append(Undefined(reason))
        elif tag == "unknown":
            cells.append(Unknown(reason))
        else:
            raise _invalid("unknown Cell tag")
    return tuple(cells)


def execute_fixed_row(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: FixedInput,
    input_contract: ExchangeContract,
) -> ExchangeResult:
    """Run a qualified fixed current-row method without source or DuckDB access."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "artifact"
    ):
        raise _invalid("prepared and lowered graph identity or input class differs")
    if any(isinstance(stage, LoweredRelation) for stage in lowered.stages):
        raise _invalid("fixed graph contains a source relation")
    reads = tuple(stage for stage in lowered.stages if isinstance(stage, ArtifactReadStage))
    methods = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if len(reads) != 1 or len(methods) != 1 or lowered.primary_output != methods[0].stage.output:
        raise _invalid("only one fixed current-row method is qualified")
    read = reads[0]
    method = methods[0]
    if (
        selected.artifact_ref != read.leaf.artifact.ref
        or input_contract.signature != read.leaf.signature
        or method.stage.inputs != (read.output,)
        or input_contract.input_binding != read.leaf.artifact.ref
        or not isinstance(read.leaf.value_type, ScalarType)
        or "value" not in input_contract.schema.names
        or input_contract.schema.field("value").type != pa.type_for_alias(read.leaf.value_type.name)
    ):
        raise _invalid("fixed Artifact, signature, ordered binding or value type differs")
    verified = from_receipts(selected, input_contract)
    cells = _cells(verified)
    name = method.stage.node.method.name
    if name == "row.count":
        row_count = count(method.stage, cells).count
        value: int | float | None = row_count
        tag, reason = "defined", None
        state = {"row_state__count": [row_count]}
        value_type = pa.int64()
    elif name == "row.count_defined":
        row_count = count_defined(method.stage, cells).count
        value = row_count
        tag, reason = "defined", None
        state = {"row_state__count_defined": [row_count]}
        value_type = pa.int64()
    elif name in ("row.sum", "row.mean"):
        outcome = arithmetic(method.stage, cells)
        if isinstance(outcome.cell, Defined):
            raw = outcome.cell.value
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise _invalid("non-numeric local arithmetic result")
            value = raw
        else:
            value = None
        tag = "defined" if isinstance(outcome.cell, Defined) else "undefined"
        reason = None if isinstance(outcome.cell, Defined) else outcome.cell.reason
        state = {"row_state__sum": [outcome.current_sum]}
        if name == "row.mean":
            state["row_state__count"] = [outcome.current_count]
        value_type = pa.float64() if name == "row.mean" else pa.int64()
    else:
        raise _invalid("unqualified fixed row method")
    completed: list[CompletedCheck] = []
    for check in lowered.checks:
        if not isinstance(check, CheckRequirement) or check.node_id != method.stage.node.identity:
            raise _invalid("unmatched local check requirement")
        if (
            name == "row.count"
            or (
                name == "row.count_defined" and check.obligation.check_id != "source.cell_policy@v1"
            )
            or (
                name in ("row.sum", "row.mean")
                and check.obligation.check_id != "source.finite_numeric@v1"
            )
        ):
            raise _invalid("unqualified local check")
        digest = hashlib.sha256(
            (selected.receipt.bytes_hash + check.obligation.check_id).encode()
        ).hexdigest()
        completed.append(CompletedCheck(check, digest))
    primary_schema = pa.schema(
        (
            pa.field("value", value_type),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    part_schema = pa.schema(tuple(pa.field(field, pa.int64()) for field in state))
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        read.leaf.artifact.ref,
        primary_schema,
        (),
        (PartContract("row_state", part_schema, ()),),
        REGISTRY.lookup(method.stage.node.method).semantics.empty_cell_reasons,
        {
            "row.count": "row_count",
            "row.count_defined": "row_count_defined",
            "row.sum": "row_sum",
            "row.mean": "row_mean",
        }[name],
        pa.schema((pa.field("status", pa.string()),)),
        lowered.admitted.checks,
    )
    part = pa.Table.from_pandas(
        pd.DataFrame(state),
        schema=part_schema,
        preserve_index=False,
    )
    return from_pandas(
        pd.DataFrame({"value": [value], "cell_tag": [tag], "cell_reason": [reason]}),
        contract,
        parts=(ExchangePart("row_state", part),),
        completed_checks=tuple(completed),
        method_state=pa.table({"status": pa.array([tag], type=pa.string())}),
    )


def execute_fixed_count(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: FixedInput,
    input_contract: ExchangeContract,
) -> ExchangeResult:
    """Retain the R3.4 fixed-count handoff during private method expansion."""
    if (
        not isinstance(lowered.admitted.root, MethodNode)
        or lowered.admitted.root.method.name != "row.count"
    ):
        raise _invalid("fixed count method")
    return execute_fixed_row(prepared, lowered, selected, input_contract)


def execute_fixed_spearman(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: tuple[FixedInput, FixedInput],
    input_contracts: tuple[ExchangeContract, ExchangeContract],
) -> ExchangeResult:
    """Score two independently verified fixed observations by complete key."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "artifact"
        or any(isinstance(stage, LoweredRelation) for stage in lowered.stages)
    ):
        raise _invalid("fixed Spearman graph identity or class differs")
    reads = tuple(stage for stage in lowered.stages if isinstance(stage, ArtifactReadStage))
    methods = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if (
        len(reads) not in (1, 2)
        or len(methods) != 1
        or methods[0].stage.node.method.name != "association.spearman"
        or len(methods[0].stage.inputs) != 2
        or set(methods[0].stage.inputs) != {read.output for read in reads}
        or lowered.primary_output != methods[0].stage.output
    ):
        raise _invalid("only one two-input fixed Spearman method is qualified")
    if input_contracts[0].key_fields != input_contracts[1].key_fields:
        raise _invalid("ordered fixed endpoint keys differ")
    by_output = {read.output: read for read in reads}
    verified: dict[str, ExchangeResult] = {}
    values: list[ExchangeResult] = []
    for output, item, contract in zip(
        methods[0].stage.inputs, selected, input_contracts, strict=True
    ):
        read = by_output[output]
        if (
            item.artifact_ref != read.leaf.artifact.ref
            or contract.input_binding != item.artifact_ref
            or contract.signature != read.leaf.signature
            or not isinstance(read.leaf.value_type, ScalarType)
            or contract.schema.field("value").type != pa.type_for_alias(read.leaf.value_type.name)
        ):
            raise _invalid("fixed Spearman Artifact, binding, signature or type differs")
        if output in verified:
            if item != selected[0] or contract != input_contracts[0]:
                raise _invalid("shared fixed input occurrence changed its selected receipt")
        else:
            verified[output] = from_receipts(item, contract)
        values.append(verified[output])
    left, right = values
    completed: list[CompletedCheck] = []
    for check in lowered.checks:
        if (
            not isinstance(check, CheckRequirement)
            or check.node_id != methods[0].stage.node.identity
            or check.obligation.check_id
            not in ("source.exact_pairing@v1", "source.finite_numeric@v1")
        ):
            raise _invalid("unmatched fixed Spearman check")
        digest = hashlib.sha256(
            (
                selected[0].receipt.bytes_hash
                + selected[1].receipt.bytes_hash
                + check.obligation.check_id
            ).encode()
        ).hexdigest()
        completed.append(CompletedCheck(check, digest))
    return finish_spearman(
        methods[0],
        left.primary,
        right.primary,
        input_contracts[0].key_fields,
        ",".join(item.artifact_ref for item in selected),
        tuple(completed),
        lowered.admitted.checks,
    )
