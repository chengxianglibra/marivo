"""Caller-owned R4.3 fixed execution over fully checked local receipts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

import pandas as pd
import pyarrow as pa

from marivo.analysis.compiler.graph_lowering import LoweredLocal, LoweredPlan, LoweredRelation
from marivo.analysis.compiler.graph_plan import ArtifactReadStage, CheckRequirement
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.model import (
    Cell,
    CoordinateStatePart,
    Defined,
    Null,
    OriginalStatePart,
    SubjectPart,
    Undefined,
    Unknown,
)
from marivo.analysis.core.rules import (
    AttachCategory,
    AttributionDerive,
    CellDerive,
    DisplayRank,
    DisplayTable,
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
    ReferenceDerive,
    RowState,
)
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import (
    CompletedCheck,
    ExchangeContract,
    ExchangePart,
    ExchangeResult,
    FixedInput,
    PartContract,
    VerifiedFixedInput,
    from_arrow,
    from_pandas,
    from_receipts,
    numeric_primary,
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_spearman_execution import finish_spearman
from marivo.analysis.methods.comparison import evaluate as evaluate_comparison
from marivo.analysis.methods.comparison import propagated_error, roundoff
from marivo.analysis.methods.local import arithmetic, count, count_defined
from marivo.analysis.methods.physical import (
    DecimalType,
    DurationType,
    ScalarType,
    arrow_scalar_type,
    matches_arrow_scalar,
)
from marivo.analysis.methods.registry import REGISTRY
from marivo.analysis.methods.state_validation import state_matches


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
    for row in (
        numeric_primary(input_value.primary)
        .select(("value", "cell_tag", "cell_reason"))
        .to_pylist()
    ):
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
    selected: FixedInput | VerifiedFixedInput,
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
        or not isinstance(read.leaf.value_type, (ScalarType, DecimalType, DurationType))
        or "value" not in input_contract.schema.names
        or input_contract.schema.field("value").type != pa.type_for_alias(read.leaf.value_type.name)
    ):
        raise _invalid("fixed Artifact, signature, ordered binding or value type differs")
    verified = (
        selected.result
        if isinstance(selected, VerifiedFixedInput)
        else from_receipts(selected, input_contract)
    )
    return _row_result(
        method,
        verified,
        selected.receipt.bytes_hash,
        read.leaf.artifact.ref,
        lowered.admitted.checks,
    )


def _row_result(
    method: LoweredLocal,
    verified: ExchangeResult,
    receipt_hash: str,
    input_binding: str,
    checks: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    """Consume one verified current-row stage, including an in-memory predecessor."""
    if method.stage.node.signature.domain.instance_key:
        return _grouped_row_result(method, verified, receipt_hash, input_binding, checks)
    if isinstance(method.stage.node.value_type, DurationType):
        return _duration_mean(method, verified, receipt_hash, input_binding, checks)
    cells = _cells(verified)
    name = method.stage.node.method.name
    state: dict[str, list[int | float | None]]
    params = method.stage.node.parameters
    if isinstance(params, RowState) and params.merge:
        retained = next(p.table for p in verified.parts if p.role == "row_state")
        state = {}
        for field in retained.column_names:
            if not field.startswith("row_state__"):
                continue
            operands: list[int | float] = []
            for raw in retained[field].to_pylist():
                if raw is None and field in ("row_state__min", "row_state__max"):
                    continue
                if (
                    type(raw) not in (int, float)
                    or not isinstance(raw, (int, float))
                    or not math.isfinite(raw)
                ):
                    raise _invalid("invalid retained row state operand")
                operands.append(raw)
            floating = pa.types.is_floating(retained.schema.field(field).type)
            total = (
                (min(operands) if operands else None)
                if field == "row_state__min"
                else (max(operands) if operands else None)
                if field == "row_state__max"
                or (field == "row_state__error_bound" and params.method in ("min", "max"))
                else math.fsum(operands)
                if floating
                else sum(operands)
            )
            if total is not None and (
                not math.isfinite(total) or (not floating and not -(2**63) <= total < 2**63)
            ):
                raise _invalid("row state merge exceeds its exact numeric type")
            state[field] = [total]
        support = state["row_state__count"][0] if params.method in ("mean", "min", "max") else 1
        assert isinstance(support, (int, float))
        if params.method == "mean":
            numerator = state["row_state__sum"][0]
            assert isinstance(numerator, (int, float))
            value: int | float | None = float(numerator) / support if support else None
        else:
            value = state[f"row_state__{params.method}"][0]
        tag, reason = ("defined", None) if support else ("undefined", "empty_" + params.method)
        output_type = method.stage.node.value_type
        assert isinstance(output_type, ScalarType)
        value_type = pa.type_for_alias(output_type.name)
    elif name == "row.count":
        row_count = count(method.stage, cells).count
        value = row_count
        tag, reason = "defined", None
        state = {"row_state__count": [row_count]}
        value_type = pa.int64()
    elif name == "row.count_defined":
        row_count = count_defined(method.stage, cells).count
        value = row_count
        tag, reason = "defined", None
        state = {"row_state__count_defined": [row_count]}
        value_type = pa.int64()
    elif name in ("row.min", "row.max"):
        values: list[int | float] = []
        for cell in cells:
            if not isinstance(cell, Defined) or type(cell.value) not in (int, float):
                raise _invalid("current-row extrema require finite Defined numeric Cells")
            raw = cell.value
            assert isinstance(raw, (int, float))
            if not math.isfinite(raw) or (type(raw) is int and not -(2**63) <= raw < 2**63):
                raise _invalid("current-row extremum operand exceeds its exact numeric type")
            values.append(raw)
        value = (min(values) if name == "row.min" else max(values)) if values else None
        tag, reason = ("defined", None) if values else ("undefined", "empty_" + name[4:])
        state = {"row_state__" + name[4:]: [value], "row_state__count": [len(values)]}
        output_type = method.stage.node.value_type
        assert isinstance(output_type, ScalarType)
        value_type = pa.type_for_alias(output_type.name)
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
        state["row_state__count"] = [outcome.current_count]
        value_type = (
            pa.float64() if name == "row.mean" or type(outcome.current_sum) is float else pa.int64()
        )
    else:
        raise _invalid("unqualified fixed row method")
    if isinstance(params, RowState) and params.retain_error:
        if params.merge:
            bound = state["row_state__error_bound"][0]
            assert isinstance(bound, float)
            if params.method in ("sum", "mean"):
                magnitude = math.fsum(abs(item) for item in retained["row_state__sum"].to_pylist())
                bound += roundoff(magnitude)
        else:
            indexed = _operand_components(verified)
            rows = verified.primary.to_pylist()
            bounds = [
                _fixed_operand_bound(
                    verified,
                    row,
                    indexed.get(tuple(row[key] for key in verified.contract.key_fields), {}),
                )
                for row in rows
            ]
            bound = (
                math.fsum(bounds) + roundoff(math.fsum(abs(row["value"]) for row in rows))
                if params.method in ("sum", "mean")
                else max(bounds, default=0.0)
            )
        if not math.isfinite(bound) or bound < 0:
            raise _invalid("row statistic error bound is nonfinite or negative")
        state["row_state__error_bound"] = [bound]
    completed: list[CompletedCheck] = []
    for check in checks:
        if not isinstance(check, CheckRequirement) or check.node_id != method.stage.node.identity:
            raise _invalid("unmatched local check requirement")
        if (
            name == "row.count"
            or (
                name == "row.count_defined" and check.obligation.check_id != "source.cell_policy@v1"
            )
            or (
                name in ("row.sum", "row.mean", "row.min", "row.max")
                and check.obligation.check_id != "source.finite_numeric@v1"
            )
        ):
            raise _invalid("unqualified local check")
        digest = hashlib.sha256((receipt_hash + check.obligation.check_id).encode()).hexdigest()
        completed.append(CompletedCheck(check, digest))
    primary_schema = pa.schema(
        (
            pa.field("value", value_type),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    part_schema = pa.schema(
        tuple(
            pa.field(
                field,
                pa.float64()
                if (field in ("row_state__min", "row_state__max") and value_type == pa.float64())
                or field == "row_state__error_bound"
                or (field == "row_state__sum" and type(values[0]) is float)
                else pa.int64(),
            )
            for field, values in state.items()
        )
    )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary_schema,
        (),
        (PartContract("row_state", part_schema, ()),),
        REGISTRY.lookup(method.stage.node.method).semantics.empty_cell_reasons,
        {
            "row.count": "row_count",
            "row.count_defined": "row_count_defined",
            "row.sum": "row_sum",
            "row.mean": "row_mean",
            "row.min": "row_min",
            "row.max": "row_max",
        }[name],
        pa.schema((pa.field("status", pa.string()),)),
        checks,
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
    selected: FixedInput | VerifiedFixedInput,
    input_contract: ExchangeContract,
) -> ExchangeResult:
    """Retain the R3.4 fixed-count handoff during private method expansion."""
    if (
        not isinstance(lowered.admitted.root, MethodNode)
        or lowered.admitted.root.method.name != "row.count"
    ):
        raise _invalid("fixed count method")
    return execute_fixed_row(prepared, lowered, selected, input_contract)


def _matches(
    value: object, operator: str, expected: int | float | str | bool | date | datetime
) -> bool:
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
    if (isinstance(value, datetime) and isinstance(expected, datetime)) or (
        type(value) is date and type(expected) is date
    ):
        assert isinstance(expected, (date, datetime))
        return {
            "lt": value < expected,
            "le": value <= expected,
            "gt": value > expected,
            "ge": value >= expected,
        }[operator]
    if (
        not isinstance(expected, (int, float))
        or isinstance(expected, bool)
        or not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise _invalid("ordered fixed predicate requires exact numeric values")
    if type(value) is float and (
        not math.isfinite(value)
        or (type(expected) is int and not -(2**53) <= expected <= 2**53)
        or (type(expected) is float and not math.isfinite(expected))
    ):
        raise _invalid("float64 predicate has a nonfinite value or lossy threshold")
    return {
        "lt": value < expected,
        "le": value <= expected,
        "gt": value > expected,
        "ge": value >= expected,
    }[operator]


def execute_fixed_transport(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: VerifiedFixedInput,
) -> ExchangeResult:
    """Transport exact saved rows and selected keyed parts without source access."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "artifact"
    ):
        raise _invalid("fixed transport graph identity or input class differs")
    reads = tuple(stage for stage in lowered.stages if isinstance(stage, ArtifactReadStage))
    methods = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if (
        len(reads) != 1
        or len(methods) != 1
        or not isinstance(methods[0].stage.node.parameters, PartsTransport)
        or methods[0].stage.inputs != (reads[0].output,)
        or lowered.primary_output != methods[0].stage.output
        or lowered.checks
    ):
        raise _invalid("only one qualified fixed transport method is selected")
    return _transport_result(reads[0], methods[0], selected)


def _transport_result(
    read: ArtifactReadStage, method: LoweredLocal, selected: VerifiedFixedInput
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, PartsTransport)
    source = selected.result
    if (
        read.leaf.artifact.ref != selected.artifact_ref
        or read.leaf.signature != source.contract.signature
        or source.contract.input_binding != selected.artifact_ref
        or not isinstance(read.leaf.value_type, (ScalarType, DecimalType, DurationType))
        or (
            "value" in source.primary.column_names
            and not matches_arrow_scalar(
                source.primary.schema.field("value").type, read.leaf.value_type
            )
        )
    ):
        raise _invalid("fixed transport input signature, binding or type differs")
    return _transport_stage(method, source, selected.artifact_ref)


def _transport_stage(
    method: LoweredLocal,
    source: ExchangeResult,
    input_binding: str,
    predicate_sources: tuple[ExchangeResult, ...] = (),
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, PartsTransport)
    if params.mode == "cohort":
        return _cohort_stage(method, source, predicate_sources, input_binding)
    keys = source.contract.key_fields
    from marivo.analysis.core.predicates import compose, leaves
    from marivo.analysis.methods.predicates import evaluate_leaf

    input_rows = [
        {tuple(row[k] for k in keys): row for row in numeric_primary(item.primary).to_pylist()}
        for item in (source, *predicate_sources)
    ]
    receiver_keys = set(input_rows[0])
    for index, (item, rows) in enumerate(zip(predicate_sources, input_rows[1:], strict=True), 1):
        if item.contract.key_fields != keys or (
            not receiver_keys <= set(rows)
            if index in params.inclusion_inputs
            else set(rows) != receiver_keys
        ):
            raise _invalid("predicate dependency lacks equal complete input keys")
    selected_keys: set[tuple[object, ...]] = set()
    keep: list[bool] = []
    for row in numeric_primary(source.primary).to_pylist():
        key = tuple(row[k] for k in keys)
        truths = tuple(
            compose(
                tree,
                tuple(
                    evaluate_leaf(
                        leaf,
                        input_rows[leaf.input_index][key],
                        None if leaf.right_index is None else input_rows[leaf.right_index][key],
                    )
                    for leaf in leaves(tree)
                ),
            )
            for tree in params.predicates
        )
        accepted = all(value is True for value in truths)
        keep.append(accepted)
        if accepted:
            selected_keys.add(key)
    if params.mode == "limit":
        assert params.limit_count is not None
        ordering = next(part.table for part in source.parts if part.role == "ordering")
        positions = {
            tuple(row[k] for k in keys): row["ordering__position"] for row in ordering.to_pylist()
        }
        ordered = sorted(selected_keys, key=lambda key: positions[key])
        selected_keys = set(ordered[: params.limit_count])
        keep = [tuple(row[k] for k in keys) in selected_keys for row in source.primary.to_pylist()]
    filtered = source.primary.filter(pa.array(keep, type=pa.bool_()))
    columns = (*keys, "value", "cell_tag", "cell_reason") if params.keep_quantity else keys
    primary = filtered.select(columns)
    if params.attribution_view is not None:
        allocation = next(p.table for p in source.parts if p.role == "allocation")
        rows = {tuple(r[k] for k in keys): r for r in allocation.to_pylist()}
        primary = primary.set_column(
            primary.schema.get_field_index("value"),
            "value",
            pa.array(
                [
                    rows[tuple(r[k] for k in keys)]["allocation__" + params.attribution_view]
                    for r in primary.to_pylist()
                ],
                type=primary.schema.field("value").type,
            ),
        )
    if params.display_view == "ranks":
        ranks = next(part.table for part in source.parts if part.role == "ranks")
        rows = {tuple(row[k] for k in keys): row for row in ranks.to_pylist()}
        for field in ("value", "cell_tag", "cell_reason"):
            primary = primary.set_column(
                primary.schema.get_field_index(field),
                field,
                pa.array(
                    [
                        rows[tuple(row[k] for k in keys)]["ranks__" + field]
                        for row in primary.to_pylist()
                    ],
                    type=ranks.schema.field("ranks__" + field).type,
                ),
            )
    parts: list[ExchangePart] = []
    for role in params.retained_roles:
        prior = next((part for part in source.parts if part.role == role), None)
        if prior is None:
            raise _invalid("required retained transport part is absent")
        from marivo.analysis.core.model import AttributionPart

        if any(
            isinstance(p, AttributionPart) and p.role == role
            for p in source.contract.signature.parts
        ) and role in ("current_endpoint", "baseline_endpoint"):
            parts.append(prior)
            continue
        if role in (
            "fixed_reference",
            "reference_proof",
            "strata",
            "stratum_values",
            "basis",
            "allocation",
            "reconciliation",
            "ranking_domain",
            "partitions",
            "ordering",
        ):
            parts.append(prior)
            continue
        mask = pa.array(
            [tuple(row[key] for key in keys) in selected_keys for row in prior.table.to_pylist()],
            type=pa.bool_(),
        )
        parts.append(ExchangePart(role, prior.table.filter(mask)))
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        keys,
        tuple(
            PartContract(
                part.role,
                part.table.schema,
                next(item.key_fields for item in source.contract.parts if item.role == part.role),
            )
            for part in parts
        ),
        source.contract.cell_reasons if params.keep_quantity else (),
        "none",
        None,
        (),
        (params.mode in ("where", "limit") or params.display_view is not None) and not keys,
    )
    return from_arrow(primary, contract, parts=tuple(parts))


def execute_fixed_spearman(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: tuple[FixedInput | VerifiedFixedInput, FixedInput | VerifiedFixedInput],
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
            or not isinstance(read.leaf.value_type, (ScalarType, DecimalType, DurationType))
            or contract.schema.field("value").type != pa.type_for_alias(read.leaf.value_type.name)
        ):
            raise _invalid("fixed Spearman Artifact, binding, signature or type differs")
        if output in verified:
            if item != selected[0] or contract != input_contracts[0]:
                raise _invalid("shared fixed input occurrence changed its selected receipt")
        else:
            verified[output] = (
                item.result
                if isinstance(item, VerifiedFixedInput)
                else from_receipts(item, contract)
            )
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


def execute_fixed_difference(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    selected: tuple[VerifiedFixedInput, VerifiedFixedInput],
) -> ExchangeResult:
    """Subtract homogeneous numeric endpoints from two exact retained Artifacts."""
    if (
        prepared.admitted is not lowered.admitted
        or lowered.admitted.classification.kind != "artifact"
        or any(isinstance(stage, LoweredRelation) for stage in lowered.stages)
    ):
        raise _invalid("fixed Difference graph identity or class differs")
    reads = tuple(stage for stage in lowered.stages if isinstance(stage, ArtifactReadStage))
    methods = tuple(stage for stage in lowered.stages if isinstance(stage, LoweredLocal))
    if (
        len(reads) not in (1, 2)
        or len(methods) != 1
        or methods[0].stage.node.method.name != "cell.difference"
        or len(methods[0].stage.inputs) != 2
        or set(methods[0].stage.inputs) != {read.output for read in reads}
        or lowered.primary_output != methods[0].stage.output
    ):
        raise _invalid("only one two-input fixed Difference method is qualified")
    by_output = {read.output: read for read in reads}
    values: list[ExchangeResult] = []
    for output, item in zip(methods[0].stage.inputs, selected, strict=True):
        read = by_output[output]
        contract = item.result.contract
        if (
            item.artifact_ref != read.leaf.artifact.ref
            or contract.input_binding != item.artifact_ref
            or contract.signature != read.leaf.signature
            or not matches_arrow_scalar(contract.schema.field("value").type, read.leaf.value_type)
        ):
            raise _invalid("fixed Difference Artifact, binding, signature or numeric type differs")
        values.append(item.result)
    return _difference_stage(
        methods[0],
        (values[0], values[1]),
        selected[0].receipt.bytes_hash + selected[1].receipt.bytes_hash,
        ",".join(item.artifact_ref for item in selected),
        lowered.admitted.checks,
    )


def _operand_components(source: ExchangeResult) -> dict[tuple[object, ...], dict[str, object]]:
    """Index retained operand components once per endpoint, using complete keys."""
    result: dict[tuple[object, ...], dict[str, object]] = {}
    quantity = source.contract.signature.quantity
    from marivo.analysis.core.model import AttributionPart

    attribution = next(
        (
            p
            for p in source.contract.signature.parts
            if isinstance(p, AttributionPart) and p.role == "allocation"
        ),
        None,
    )
    if attribution is not None:
        allocation = next(p.table for p in source.parts if p.role == "allocation")
        return {
            tuple(row[name] for name in source.contract.key_fields): {
                "allocation_error_bound": row["allocation__" + attribution.view + "_error_bound"]
            }
            for row in allocation.to_pylist()
        }
    if quantity is not None and quantity.method_version.startswith("reference."):
        from marivo.analysis.materialization.graph_exchange import reference_parameters
        from marivo.analysis.materialization.graph_reference import error_bounds

        bounds = error_bounds(
            reference_parameters(source.contract.signature), source.parts, source.primary
        )
        return {
            tuple(row[name] for name in source.contract.key_fields): {
                "reference_error_bound": bound
            }
            for row, bound in zip(source.primary.to_pylist(), bounds, strict=True)
        }
    for part in source.parts:
        for item in part.table.to_pylist():
            key = tuple(item[name] for name in source.contract.key_fields)
            result.setdefault(key, {}).update(item)
    return result


def _fixed_operand_bound(
    source: ExchangeResult,
    row: dict[str, object] | None,
    components: dict[str, object],
) -> float:
    if row is None or row["cell_tag"] != "defined" or type(row["value"]) is not float:
        return 0.0
    for name in (
        "allocation_error_bound",
        "reference_error_bound",
        "correspondence__result_error_bound",
        "original_state__absolute_sum",
    ):
        value = components.get(name)
        if type(value) is float:
            if name.startswith(("allocation", "correspondence", "reference")):
                return value
            bound = roundoff(value)
            quantity = source.contract.signature.quantity
            if quantity is not None and quantity.method_version == "mean@v1":
                count = components.get("original_state__non_null_count")
                if type(count) is not int or count <= 0:
                    raise _invalid("defined mean lacks a positive retained count")
                result = row["value"]
                assert isinstance(result, float)
                return bound / count + roundoff(result)
            return bound
    if "row_state__error_bound" in components:
        row_bound = components["row_state__error_bound"]
        if type(row_bound) is not float or not math.isfinite(row_bound) or row_bound < 0:
            raise _invalid("row statistic lacks a finite nonnegative error bound")
        quantity = source.contract.signature.quantity
        if quantity is not None and quantity.method_version == "row.mean@v1":
            count = components.get("row_state__count")
            value = row["value"]
            if type(count) is not int or count <= 0 or type(value) is not float:
                raise _invalid("defined row mean lacks its retained positive count")
            return row_bound / count + roundoff(value)
        return row_bound
    if type(components.get("row_state__sum")) is int:
        value = row["value"]
        assert isinstance(value, float)
        return roundoff(value)
    quantity = source.contract.signature.quantity
    if quantity is not None and quantity.method_version in (
        "mean@v1",
        "weighted_mean@v1",
        "ratio@v1",
    ):
        component = {
            "mean@v1": "sum",
            "weighted_mean@v1": "weighted_numerator",
            "ratio@v1": "numerator_sum",
        }[quantity.method_version]
        if type(components.get("original_state__" + component)) is int:
            result = row["value"]
            assert isinstance(result, float)
            return roundoff(result)
    if quantity is not None and quantity.method_version in (
        "ratio@v1",
        "weighted_mean@v1",
        "linear@v1",
    ):

        def magnitude_component(name: str) -> float:
            value = components.get("original_state__" + name)
            if type(value) is not float or not math.isfinite(value):
                raise _invalid("missing finite original error magnitude")
            return value

        result = row["value"]
        assert isinstance(result, float)
        if quantity.method_version == "linear@v1":
            magnitudes = [
                name.removeprefix("original_state__")
                for name in components
                if name.endswith("_absolute_sum")
            ]
            if not magnitudes:
                raise _invalid("float linear lacks original error magnitudes")
            return sum(roundoff(magnitude_component(name)) for name in magnitudes) + roundoff(
                result
            )
        weighted = quantity.method_version == "weighted_mean@v1"
        numerator = magnitude_component(
            "absolute_weighted_numerator" if weighted else "numerator_absolute_sum"
        )
        error_n = roundoff(numerator)
        if weighted:
            count = components.get("original_state__non_null_pair_count")
            if type(count) is not int:
                raise _invalid("weighted mean lacks original pair count")
            error_n += 1e-12 * (numerator + count)
        denominator = magnitude_component("weight_sum" if weighted else "denominator_sum")
        error_d = roundoff(
            magnitude_component("absolute_weight_sum" if weighted else "denominator_absolute_sum")
        )
        return propagated_error("ratio", None, denominator, result, error_n, error_d)
    if quantity is not None and quantity.method_version in (
        "sum@v1",
        "sum_zero@v1",
        "mean@v1",
        "weighted_mean@v1",
        "ratio@v1",
        "linear@v1",
    ):
        raise _invalid("float aggregate lacks its retained rounding envelope")
    if "row_state__sum" in components:
        raise _invalid("float row statistic lacks its retained rounding envelope")
    return 0.0


def _difference_stage(
    method: LoweredLocal,
    values: tuple[ExchangeResult, ExchangeResult],
    receipt_hash: str,
    input_binding: str,
    checks: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, CellDerive)
    current, baseline = values
    keys = current.contract.key_fields
    if keys != baseline.contract.key_fields:
        raise _invalid("fixed Difference complete endpoint keys differ")
    if any(
        current.primary.schema.field(key).type != baseline.primary.schema.field(key).type
        for key in keys
    ):
        raise _invalid("fixed Difference endpoint key types differ")
    current_rows = {
        tuple(row[key] for key in keys): row for row in numeric_primary(current.primary).to_pylist()
    }
    baseline_rows = {
        tuple(row[key] for key in keys): row
        for row in numeric_primary(baseline.primary).to_pylist()
    }
    original_baseline_keys: dict[tuple[object, ...], tuple[object, ...]] = {}
    if params.time_index is not None:
        time_index = params.time_index
        for index, rows in enumerate((current_rows, baseline_rows)):
            expected = {pair[index] for pair in params.bucket_mapping}
            groups: dict[tuple[object, ...], set[object]] = {}
            for key in rows:
                non_time = (*key[:time_index], *key[time_index + 1 :])
                groups.setdefault(non_time, set()).add(key[time_index])
            if any(actual != expected for actual in groups.values()):
                raise _invalid(
                    "complete original period buckets required; filtered rows cannot be renumbered"
                )
        mapping = {right: left for left, right in params.bucket_mapping}
        translated: dict[tuple[object, ...], dict[str, object]] = {}
        for key, row in baseline_rows.items():
            token = key[time_index]
            if not isinstance(token, str) or token not in mapping:
                raise _invalid("baseline bucket is absent from the frozen complete grid")
            paired_key = (*key[:time_index], mapping[token], *key[time_index + 1 :])
            translated[paired_key] = row
            original_baseline_keys[paired_key] = key
        baseline_rows = translated
    if (
        len(current_rows) != current.primary.num_rows
        or len(baseline_rows) != baseline.primary.num_rows
    ):
        raise _invalid("fixed Difference endpoint keys are not injective")
    if params.pairing == "exact" and current_rows.keys() != baseline_rows.keys():
        raise _invalid("fixed Difference endpoint key sets differ")
    if params.pairing == "metric_empty":
        for source in values:
            coverage = next((part.table for part in source.parts if part.role == "coverage"), None)
            if coverage is None or any(
                value is not True for value in coverage["coverage__complete"].to_pylist()
            ):
                raise _invalid("metric_empty requires complete retained original coverage")
    primary_rows: list[dict[str, object]] = []
    current_parts: list[dict[str, object]] = []
    baseline_parts: list[dict[str, object]] = []
    value: object
    tag: str
    reason: str | None
    error_rows: list[tuple[float, float, float]] = []
    operand_components = (_operand_components(current), _operand_components(baseline))
    union_keys = tuple(dict.fromkeys((*current_rows, *baseline_rows)))
    for key in union_keys:
        first, second = current_rows.get(key), baseline_rows.get(key)
        both = first is not None and second is not None
        error_a, error_b = (
            _fixed_operand_bound(current, first, operand_components[0].get(key, {})),
            _fixed_operand_bound(
                baseline,
                second,
                operand_components[1].get(original_baseline_keys.get(key, key), {}),
            ),
        )
        endpoints: list[dict[str, object]] = []
        identity = dict(zip(keys, key, strict=True))
        for index, row in enumerate((first, second)):
            if row is None:
                if params.pairing == "metric_empty":
                    zero: object = (
                        Decimal(0)
                        if isinstance(method.stage.node.inputs[index].node.value_type, DecimalType)
                        else 0.0
                        if method.stage.node.inputs[index].node.value_type == ScalarType("float64")
                        else 0
                    )
                    row = {
                        "value": zero if params.empty_rules[index] == "zero" else None,
                        "cell_tag": "defined"
                        if params.empty_rules[index] == "zero"
                        else "null"
                        if params.empty_rules[index] == "null"
                        else "undefined",
                        "cell_reason": None
                        if params.empty_rules[index] == "zero"
                        else "empty_contribution"
                        if params.empty_rules[index] == "null"
                        else "zero_denominator",
                    }
                else:
                    row = {"value": None, "cell_tag": None, "cell_reason": None}
            elif (both or params.pairing == "metric_empty") and row["cell_tag"] != "defined":
                raise _invalid("comparison requires finite Defined existing operands")
            endpoints.append(row)
        left, right = endpoints
        if not both and params.pairing == "keep":
            value, tag, reason = None, "undefined", "missing_side"
        elif left["cell_tag"] != "defined" or right["cell_tag"] != "defined":
            empty = left if left["cell_tag"] != "defined" else right
            value = None
            tag, reason = str(empty["cell_tag"]), str(empty["cell_reason"])
        else:
            try:
                cell = evaluate_comparison(
                    params.method,
                    left["value"],
                    right["value"],
                    method.stage.node.inputs[0].node.value_type,
                    method.stage.node.inputs[1].node.value_type,
                )
            except (ValueError, OverflowError) as error:
                raise _invalid(f"comparison numeric input or result is invalid: {error}") from error
            value, tag, reason = (
                (cell.value, "defined", None)
                if isinstance(cell, Defined)
                else (None, "undefined", cell.reason)
            )
        try:
            error_rows.append(
                (
                    error_a,
                    error_b,
                    propagated_error(
                        params.method, left["value"], right["value"], value, error_a, error_b
                    ),
                )
            )
        except (ValueError, OverflowError) as error:
            raise _invalid(str(error)) from error
        primary_rows.append({**identity, "value": value, "cell_tag": tag, "cell_reason": reason})
        for role, row, output in (
            ("current_endpoint", left, current_parts),
            ("baseline_endpoint", right, baseline_parts),
        ):
            output.append(
                {
                    **identity,
                    **{
                        f"{role}__{name}": row[name]
                        for name in ("value", "cell_tag", "cell_reason")
                    },
                }
            )
    key_schema = tuple(current.primary.schema.field(key) for key in keys)
    primary_schema = pa.schema(
        (
            *key_schema,
            pa.field("value", arrow_scalar_type(method.stage.node.value_type)),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    part_schemas = tuple(
        pa.schema(
            (
                *key_schema,
                pa.field(
                    f"{role}__value",
                    numeric_primary(values[index].primary).schema.field("value").type,
                ),
                pa.field(f"{role}__cell_tag", pa.string()),
                pa.field(f"{role}__cell_reason", pa.string()),
            )
        )
        for index, role in enumerate(("current_endpoint", "baseline_endpoint"))
    )
    primary = pa.Table.from_pylist(primary_rows, schema=primary_schema)
    parts: tuple[ExchangePart, ...] = (
        ExchangePart(
            "current_endpoint", pa.Table.from_pylist(current_parts, schema=part_schemas[0])
        ),
        ExchangePart(
            "baseline_endpoint", pa.Table.from_pylist(baseline_parts, schema=part_schemas[1])
        ),
    )
    correspondence = primary.select(keys)
    for side in ("current", "baseline"):
        correspondence = correspondence.append_column(
            f"correspondence__{side}_present",
            pa.array(
                [
                    key in (current_rows if side == "current" else baseline_rows)
                    for key in union_keys
                ],
                type=pa.bool_(),
            ),
        )
    for index, side in enumerate(("current", "baseline", "result")):
        correspondence = correspondence.append_column(
            f"correspondence__{side}_error_bound",
            pa.array([row[index] for row in error_rows], type=pa.float64()),
        )
    for side in ("current", "baseline"):
        for index, key_name in enumerate(keys):
            correspondence = correspondence.append_column(
                f"correspondence__{side}_key_{index}",
                pa.array(
                    [
                        (
                            original_baseline_keys.get(key, key)[index]
                            if side == "baseline"
                            else key[index]
                        )
                        if key in (current_rows if side == "current" else baseline_rows)
                        else None
                        for key in union_keys
                    ],
                    type=primary.schema.field(key_name).type,
                ),
            )
    from marivo.analysis.materialization.graph_attribution import retain_endpoint_states

    parts = retain_endpoint_states(
        method.stage.node.signature, parts, values, original_baseline_keys
    )
    parts = (*parts, ExchangePart("correspondence", correspondence))
    if any(isinstance(p, SubjectPart) for p in method.stage.node.signature.parts):
        subject = primary.select(keys)
        for index, key_name in enumerate(keys):
            subject = subject.append_column(f"subject__key_{index}", primary[key_name])
        parts = (ExchangePart("subject", subject), *parts)
    completed: list[CompletedCheck] = []
    for check in checks:
        if (
            not isinstance(check, CheckRequirement)
            or check.node_id != method.stage.node.identity
            or check.obligation.check_id
            not in ("source.unique_key@v1", "source.exact_pairing@v1", "source.finite_numeric@v1")
        ):
            raise _invalid("unmatched fixed Difference check")
        result_digest = hashlib.sha256(
            (receipt_hash + check.obligation.check_id).encode()
        ).hexdigest()
        completed.append(CompletedCheck(check, result_digest))
    status_schema = pa.schema((*key_schema, pa.field("status", pa.string())))
    statuses = pa.Table.from_pylist(
        [{**{key: row[key] for key in keys}, "status": row["cell_tag"]} for row in primary_rows],
        schema=status_schema,
    )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary_schema,
        keys,
        tuple(PartContract(part.role, part.table.schema, keys) for part in parts),
        REGISTRY.lookup(method.stage.node.method).semantics.empty_cell_reasons,
        REGISTRY.lookup(method.stage.node.method).semantics.persistent_state_kind or "none",
        status_schema,
        checks,
        not keys,
    )
    return from_arrow(
        primary,
        contract,
        parts=parts,
        completed_checks=tuple(completed),
        method_state=statuses,
    )


def _coordinate_rollup_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, OriginalReduce) and params.coordinates
    original_contract = next(
        p for p in source.contract.signature.parts if isinstance(p, OriginalStatePart)
    )
    components = original_contract.components
    source_keys = source.contract.signature.domain.instance_key
    if params.time_mapping:
        target_grid = params.output_domain.time_grid
        assert target_grid is not None
        source_keys = tuple(
            replace(c, field="time:" + target_grid.identity) if c.role == "anchor" else c
            for c in source_keys
        )
    direct = set(params.coordinates) <= set(source_keys)
    key_fields = tuple(f"key_{i}" for i in range(len(params.coordinates)))
    entries: list[dict[str, object]]
    if direct:
        state = next(p.table for p in source.parts if p.role == "original_state")
        columns = tuple(f"key_{source_keys.index(c)}" for c in params.coordinates)
        key_types = tuple(state.schema.field(column).type for column in columns)
        entries = [
            {
                **{key: row[column] for key, column in zip(key_fields, columns, strict=True)},
                **{name: row[f"original_state__{name}"] for name in components},
            }
            for row in state.to_pylist()
        ]
    else:
        coordinate = next(
            p for p in source.contract.signature.parts if isinstance(p, CoordinateStatePart)
        )
        state = next(p.table for p in source.parts if p.role == "coordinate_state")
        columns = tuple(
            f"key_{source_keys.index(c)}"
            if c in source_keys
            else coordinate.columns[coordinate.coordinates.index(c)]
            for c in params.coordinates
        )
        key_types = tuple(
            source.primary.schema.field(column).type
            if column in source.contract.key_fields
            else pa.string()
            for column in columns
        )
        entries = []
        for row in state.to_pylist():
            nested: object = row["coordinate_state__groups"]
            if not isinstance(nested, list):
                raise _invalid("coordinate state is not a complete list")
            for item in nested:
                if not isinstance(item, dict):
                    raise _invalid("coordinate state has an invalid entry")
                entries.append(
                    {
                        **{
                            key: row[column]
                            if column in source.contract.key_fields
                            else item[column]
                            for key, column in zip(key_fields, columns, strict=True)
                        },
                        **{name: item[name] for name in components},
                    }
                )
    if params.time_mapping:
        mapping = dict(params.time_mapping)
        column = key_fields[next(i for i, c in enumerate(params.coordinates) if c.role == "anchor")]
        for row in entries:
            old = row[column]
            if not isinstance(old, str) or old not in mapping:
                raise _invalid("time coordinate is outside its frozen coarsening map")
            row[column] = mapping[old]
    if any(
        row["coverage__complete"] is not True
        for p in source.parts
        if p.role == "coverage"
        for row in p.table.to_pylist()
    ):
        raise _invalid("coordinate reduction requires complete retained coverage")
    from marivo.analysis.methods.numeric_state import merge_original

    original_table = next(p.table for p in source.parts if p.role == "original_state")
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for item in entries:
        label = tuple(item[key] for key in key_fields)
        if any(value is None for value in label):
            raise _invalid("coordinate state has a missing classification")
        groups.setdefault(label, []).append(item)
    primary_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    value_type = method.stage.node.value_type
    for label, rows in sorted(groups.items()):
        try:
            totals, value, tag, reason = merge_original(
                rows,
                original_table.schema,
                components,
                params.method,
                value_type,
                original_contract.empty_rules,
            )
        except (ValueError, OverflowError) as error:
            raise _invalid(str(error)) from error
        primary_rows.append(
            {
                **dict(zip(key_fields, label, strict=True)),
                "value": value,
                "cell_tag": tag,
                "cell_reason": reason,
            }
        )
        state_rows.append(
            {
                **dict(zip(key_fields, label, strict=True)),
                **{f"original_state__{name}": value for name, value in totals.items()},
            }
        )
    assert isinstance(value_type, (ScalarType, DecimalType, DurationType))
    primary_schema = pa.schema(
        [
            *list(zip(key_fields, key_types, strict=True)),
            (
                "value",
                arrow_scalar_type(value_type),
            ),
            ("cell_tag", pa.string()),
            ("cell_reason", pa.string()),
        ]
    )
    primary = pa.Table.from_pylist(primary_rows, schema=primary_schema)
    state_schema = pa.schema(
        [
            *list(zip(key_fields, key_types, strict=True)),
            *(original_table.schema.field(f"original_state__{name}") for name in components),
        ]
    )
    original = pa.Table.from_pylist(state_rows, schema=state_schema)
    labels = {key: primary[key] for key in key_fields}
    coverage = pa.table(
        {**labels, "coverage__complete": pa.array([True] * len(groups), type=pa.bool_())}
    )
    status = pa.table({**labels, "status": primary["cell_tag"]})
    parts: tuple[ExchangePart, ...] = (
        ExchangePart("original_state", original),
        ExchangePart("coverage", coverage),
    )
    subject = next(
        (p for p in method.stage.node.signature.parts if isinstance(p, SubjectPart)), None
    )
    if subject is not None:
        subject_table = pa.table(
            {
                **labels,
                **{
                    f"subject__key_{i}": primary[
                        f"key_{params.output_domain.instance_key.index(c)}"
                    ]
                    for i, c in enumerate(subject.subject_key)
                },
            }
        )
        parts = (ExchangePart("subject", subject_table), *parts)

    semantics = REGISTRY.lookup(method.stage.node.method).semantics
    state_kind = semantics.persistent_state_kind
    assert state_kind is not None
    from marivo.analysis.materialization.graph_attribution import retain_partition

    parts = retain_partition(method.stage.node, source, primary, parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary_schema,
        key_fields,
        tuple(PartContract(p.role, p.table.schema, key_fields) for p in parts),
        semantics.empty_cell_reasons,
        state_kind,
        status.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def _original_ratio_rollup_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    state = next((part.table for part in source.parts if part.role == "original_state"), None)
    coverage = next((part.table for part in source.parts if part.role == "coverage"), None)
    if state is None or coverage is None:
        raise _invalid("original ratio lacks its complete components or coverage")
    keys = source.contract.key_fields
    keyed = {tuple(row[key] for key in keys): row for row in state.to_pylist()}
    if any(row["coverage__complete"] is not True for row in coverage.to_pylist()):
        raise _invalid("original ratio has incomplete coverage")
    original_contract = next(
        p for p in source.contract.signature.parts if isinstance(p, OriginalStatePart)
    )
    state_kind = REGISTRY.lookup(method.stage.node.method).semantics.persistent_state_kind
    assert state_kind is not None
    components = original_contract.components
    operands: dict[str, list[int | float | Decimal]] = {name: [] for name in components}
    totals: dict[str, int | float | Decimal] = {}
    for row in numeric_primary(source.primary).to_pylist():
        original = keyed[tuple(row[key] for key in keys)]
        if not state_matches(state_kind, row, original, empty_rules=original_contract.empty_rules):
            raise _invalid("original ratio Cells differ from their components")
        for component in components:
            value: object = original[f"original_state__{component}"]
            if not isinstance(value, (int, float, Decimal)) or type(value) not in (
                int,
                float,
                Decimal,
            ):
                raise _invalid("original ratio component is not int64")
            operands[component].append(value)
    for component, values in operands.items():
        floating = pa.types.is_floating(state.schema.field(f"original_state__{component}").type)
        decimal = pa.types.is_decimal(state.schema.field(f"original_state__{component}").type)
        with localcontext() as context:
            context.prec = 100
            total = math.fsum(values) if floating else sum(values)
        if not math.isfinite(total) or (
            not floating and not decimal and not -(2**63) <= total < 2**63
        ):
            raise _invalid("original component rollup exceeds its declared numeric type")
        totals[component] = total
    numerator: int | float | Decimal
    denominator: int | float | Decimal
    if state_kind == "original_linear":
        with localcontext() as context:
            context.prec = 100
            numerator = sum(
                totals[name] * (1 if name.startswith("plus_") else -1)
                for name in components[: 2 * len(original_contract.empty_rules) : 2]
            )
        denominator = 1
        defined = all(
            totals[name] > 0 or rule == "zero"
            for name, rule in zip(
                components[1 : 2 * len(original_contract.empty_rules) : 2],
                original_contract.empty_rules,
                strict=True,
            )
        )
        tag = "defined" if defined else "null"
        reason = None if defined else "empty_contribution"
        if type(numerator) is int and not -(2**63) <= numerator < 2**63:
            raise _invalid("linear finish exceeds int64")
    elif state_kind == "original_mean":
        numerator, denominator = totals["sum"], totals["non_null_count"]
        defined = denominator > 0
        tag, reason = ("defined", None) if defined else ("null", "empty_contribution")
    elif state_kind == "original_weighted_mean":
        numerator, denominator = totals["weighted_numerator"], totals["weight_sum"]
        contributed = totals["non_null_pair_count"] > 0
        defined = contributed and denominator != 0
        tag = "defined" if defined else "null"
        reason = None if defined else "zero_weight_sum" if contributed else "empty_contribution"
    else:
        numerator, denominator = totals["numerator_sum"], totals["denominator_sum"]
        contributed = all(
            totals[f"{prefix}_non_null_count"] > 0 or rule == "zero"
            for prefix, rule in zip(
                ("numerator", "denominator"), original_contract.empty_rules, strict=True
            )
        )
        defined = contributed and denominator != 0
        tag = "defined" if defined else "undefined" if contributed else "null"
        reason = None if defined else "zero_denominator" if contributed else "empty_contribution"
    output_type = method.stage.node.value_type
    physical = arrow_scalar_type(output_type)
    result: int | float | Decimal | None = None
    if defined:
        with localcontext() as context:
            context.prec = 100
            if isinstance(output_type, DecimalType):
                assert isinstance(numerator, (int, Decimal)) and isinstance(
                    denominator, (int, Decimal)
                )
                result = (
                    Decimal(numerator)
                    if state_kind == "original_linear"
                    else Decimal(numerator) / Decimal(denominator)
                )
                result = result.quantize(
                    Decimal(1).scaleb(-output_type.scale), rounding=ROUND_HALF_EVEN
                )
            else:
                assert isinstance(numerator, (int, float)) and isinstance(denominator, (int, float))
                from marivo.analysis.methods.numeric_state import finish_division

                result = (
                    numerator
                    if state_kind == "original_linear"
                    else finish_division(numerator, denominator, output_type)
                )
    primary = pa.table(
        {
            "value": pa.array([result], type=physical),
            "cell_tag": [tag],
            "cell_reason": pa.array([reason], type=pa.string()),
        }
    )
    original = pa.table(
        {
            f"original_state__{name}": pa.array(
                [total], type=state.schema.field(f"original_state__{name}").type
            )
            for name, total in totals.items()
        }
    )
    parts: tuple[ExchangePart, ...] = (
        ExchangePart("original_state", original),
        ExchangePart("coverage", pa.table({"coverage__complete": [True]})),
    )
    status = pa.table({"status": primary["cell_tag"]})
    from marivo.analysis.materialization.graph_attribution import retain_partition

    parts = retain_partition(method.stage.node, source, primary, parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        (),
        tuple(PartContract(part.role, part.table.schema, ()) for part in parts),
        REGISTRY.lookup(method.stage.node.method).semantics.empty_cell_reasons,
        state_kind,
        status.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def _original_count_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    state = next((part.table for part in source.parts if part.role == "original_state"), None)
    coverage = next((part.table for part in source.parts if part.role == "coverage"), None)
    if state is None or coverage is None:
        raise _invalid("original count lacks its bound state or coverage")
    keys = source.contract.key_fields
    keyed = {tuple(row[key] for key in keys): row for row in state.to_pylist()}
    if any(row["coverage__complete"] is not True for row in coverage.to_pylist()):
        raise _invalid("original count has incomplete coverage")
    total = 0
    for row in numeric_primary(source.primary).to_pylist():
        original = keyed[tuple(row[key] for key in keys)]
        if not state_matches("original_count", row, original):
            raise _invalid("original count state differs from its retained Cells")
        count_value = original["original_state__count"]
        assert type(count_value) is int
        total += count_value
    if total >= 2**63:
        raise _invalid("original count exceeds int64")
    primary = pa.table(
        {
            "value": pa.array([total], type=pa.int64()),
            "cell_tag": ["defined"],
            "cell_reason": pa.array([None], type=pa.string()),
        }
    )
    state = pa.table({"original_state__count": pa.array([total], type=pa.int64())})
    coverage = pa.table({"coverage__complete": [True]})
    parts: tuple[ExchangePart, ...] = (
        ExchangePart("original_state", state),
        ExchangePart("coverage", coverage),
    )
    status = pa.table({"status": ["defined"]})
    from marivo.analysis.materialization.graph_attribution import retain_partition

    parts = retain_partition(method.stage.node, source, primary, parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        (),
        tuple(PartContract(part.role, part.table.schema, ()) for part in parts),
        (),
        "original_count",
        status.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def _fold_rollup_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    from marivo.analysis.methods.numeric_state import Number, checked_sum
    from marivo.analysis.methods.temporal_fold import (
        Samples,
        decode_samples,
        encode_samples,
        fold_value,
        sample_type,
    )

    params = method.stage.node.parameters
    assert isinstance(params, OriginalReduce)
    state = next(p.table for p in source.parts if p.role == "original_state")
    coverage = next(p.table for p in source.parts if p.role == "coverage")
    if any(v is not True for v in coverage["coverage__complete"].to_pylist()):
        raise _invalid("fold requires complete coverage")
    coordinates = source.contract.signature.domain.instance_key
    time_index = next((i for i, c in enumerate(coordinates) if c.role == "anchor"), None)
    if params.time_mapping:
        assert params.output_domain.time_grid is not None
        coordinates = tuple(
            replace(c, field="time:" + params.output_domain.time_grid.identity)
            if c.role == "anchor"
            else c
            for c in coordinates
        )
    selected = tuple(f"key_{coordinates.index(c)}" for c in params.coordinates)
    keys = tuple(f"key_{i}" for i in range(len(selected)))
    # Distinct original time cells can concatenate; spatial inputs within each
    # cell must agree on the complete sample coordinate set before merging.
    grouped: dict[tuple[object, ...], dict[object, list[Samples]]] = {}
    kinds: set[str] = set()
    for row in state.to_pylist():
        identity = tuple(
            dict(params.time_mapping).get(row[k], row[k])
            if time_index is not None and k == f"key_{time_index}"
            else row[k]
            for k in selected
        )
        period = row[f"key_{time_index}"] if time_index is not None else None
        try:
            samples = decode_samples(row["original_state__samples"])
        except (ValueError, TypeError, OverflowError) as error:
            raise _invalid("invalid ordered pre-fold samples") from error
        kinds.add(row["original_state__fold_kind"])
        grouped.setdefault(identity, {}).setdefault(period, []).append(samples)
    declared = next(p for p in source.contract.signature.parts if isinstance(p, OriginalStatePart))
    kind = declared.fold_kind
    if kind is None or (kinds and kinds != {kind}):
        raise _invalid("fold kinds differ from the bound declaration")
    if not grouped and not keys:
        grouped[()] = {}
    labels: dict[str, list[object]] = {k: [] for k in keys}
    encodings: list[str] = []
    values: list[Number | None] = []
    for identity, periods in grouped.items():
        combined: list[tuple[datetime, Number, int]] = []
        for rows in periods.values():
            sample_keys = tuple(key for key, _, _ in rows[0])
            if any(tuple(key for key, _, _ in row) != sample_keys for row in rows[1:]):
                raise _invalid("unaligned pre-fold sample coordinates; keep the spatial groups")
            for i, key in enumerate(sample_keys):
                try:
                    total = checked_sum([row[i][1] for row in rows], sample_type(rows[0]))
                except OverflowError as error:
                    raise _invalid("pre-fold spatial sum exceeds float64") from error
                combined.append((key, total, sum(row[i][2] for row in rows)))
        combined.sort()
        try:
            encoded = encode_samples(tuple(combined))
            value = fold_value(
                decode_samples(encoded),
                kind,
                method.stage.node.value_type
                if isinstance(method.stage.node.value_type, DurationType)
                else None,
            )
        except (ValueError, OverflowError) as error:
            raise _invalid("overlapping or invalid pre-fold components") from error
        for label, item in zip(keys, identity, strict=True):
            labels[label].append(item)
        encodings.append(encoded)
        values.append(value)
    arrays = {
        key: pa.array(labels[key], type=state.schema.field(column).type)
        for key, column in zip(keys, selected, strict=True)
    }
    tags = pa.array(["null" if v is None else "defined" for v in values], type=pa.string())
    value_type = method.stage.node.value_type
    physical = arrow_scalar_type(value_type)
    primary = pa.table(
        {
            **arrays,
            "value": pa.array(values, type=physical),
            "cell_tag": tags,
            "cell_reason": pa.array(
                ["empty_contribution" if v is None else None for v in values], type=pa.string()
            ),
        }
    )
    parts: tuple[ExchangePart, ...] = (
        ExchangePart(
            "original_state",
            pa.table(
                {
                    **arrays,
                    "original_state__samples": pa.array(encodings, type=pa.string()),
                    "original_state__fold_kind": pa.array([kind] * len(values), type=pa.string()),
                }
            ),
        ),
        ExchangePart(
            "coverage",
            pa.table(
                {**arrays, "coverage__complete": pa.array([True] * len(values), type=pa.bool_())}
            ),
        ),
    )
    subject = next(
        (p for p in method.stage.node.signature.parts if isinstance(p, SubjectPart)), None
    )
    if subject is not None:
        parts = (
            ExchangePart(
                "subject",
                pa.table(
                    {
                        **arrays,
                        **{
                            f"subject__key_{i}": arrays[f"key_{params.coordinates.index(c)}"]
                            for i, c in enumerate(subject.subject_key)
                        },
                    }
                ),
            ),
            *parts,
        )
    status = pa.table({**arrays, "status": tags})
    from marivo.analysis.materialization.graph_attribution import retain_partition

    parts = retain_partition(method.stage.node, source, primary, parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        (("null", ("empty_contribution",)),),
        "original_fold",
        status.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def _original_sum_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    name = method.stage.node.method.name
    component = (
        "min" if name == "state_rollup.min" else "max" if name == "state_rollup.max" else "sum"
    )
    state_kind = "original_sum_zero" if name == "state_rollup.sum_zero" else "original_" + component
    state = next((part.table for part in source.parts if part.role == "original_state"), None)
    coverage = next((part.table for part in source.parts if part.role == "coverage"), None)
    if state is None or coverage is None:
        raise _invalid("original rollup lacks its bound state or coverage")
    keys = source.contract.key_fields
    keyed = {tuple(row[key] for key in keys): row for row in state.to_pylist()}
    if any(row["coverage__complete"] is not True for row in coverage.to_pylist()):
        raise _invalid("original rollup has incomplete coverage")
    for row in numeric_primary(source.primary).to_pylist():
        if not state_matches(state_kind, row, keyed[tuple(row[key] for key in keys)]):
            raise _invalid("original rollup state differs from its retained Cells")
    totals = state.column("original_state__" + component).to_pylist()
    if component in ("min", "max"):
        contributing = [
            row["original_state__" + component]
            for row in state.to_pylist()
            if row["original_state__non_null_count"] > 0
        ]
        totals = [
            (min(contributing) if component == "min" else max(contributing)) if contributing else 0
        ]
    support = sum(state.column("original_state__non_null_count").to_pylist())
    value_type = method.stage.node.value_type
    assert isinstance(value_type, (ScalarType, DecimalType, DurationType))
    if isinstance(value_type, DecimalType):
        with localcontext() as context:
            context.prec = 100
            total = sum(totals, Decimal(0))
        if not total.is_finite() or total.copy_abs() >= Decimal(10) ** (
            value_type.precision - value_type.scale
        ):
            raise _invalid("original Decimal state exceeds its declared precision")
        physical = pa.decimal128(value_type.precision, value_type.scale)
    elif value_type.name == "int64" or isinstance(value_type, DurationType):
        total = sum(totals)
        if not -(2**63) <= total < 2**63:
            raise _invalid("original sum exceeds int64")
        physical = (
            pa.duration(value_type.unit) if isinstance(value_type, DurationType) else pa.int64()
        )
    else:
        try:
            total = math.fsum(totals)
        except OverflowError as error:
            raise _invalid("original sum exceeds float64") from error
        if not math.isfinite(total):
            raise _invalid("original sum is not finite")
        physical = pa.float64()
    if not 0 <= support < 2**63:
        raise _invalid("original support count exceeds int64")
    defined = support > 0 or state_kind == "original_sum_zero"
    primary = pa.table(
        {
            "value": pa.array([total if defined else None], type=physical),
            "cell_tag": ["defined" if defined else "null"],
            "cell_reason": pa.array([None if defined else "empty_contribution"], type=pa.string()),
        }
    )
    original = pa.table(
        {
            "original_state__" + component: pa.array(
                [total], type=pa.int64() if isinstance(value_type, DurationType) else physical
            ),
            "original_state__non_null_count": pa.array([support], type=pa.int64()),
            **(
                {
                    "original_state__absolute_sum": pa.array(
                        [math.fsum(state.column("original_state__absolute_sum").to_pylist())],
                        type=pa.float64(),
                    )
                }
                if "original_state__absolute_sum" in state.column_names
                else {}
            ),
        }
    )
    covered = pa.table({"coverage__complete": [True]})
    parts: tuple[ExchangePart, ...] = (
        ExchangePart("original_state", original),
        ExchangePart("coverage", covered),
    )
    status = pa.table({"status": ["defined" if defined else "null"]})
    from marivo.analysis.materialization.graph_attribution import retain_partition

    parts = retain_partition(method.stage.node, source, primary, parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        (),
        tuple(PartContract(part.role, part.table.schema, ()) for part in parts),
        (("null", ("empty_contribution",)),),
        state_kind,
        status.schema,
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def validate_fixed_schedule(lowered: LoweredPlan) -> None:
    """Reject unsupported schedules before any Artifact read or Run allocation."""
    if lowered.admitted.classification.kind != "artifact":
        raise _invalid("fixed schedule contains live inputs")
    available: set[str] = set()
    for stage in lowered.stages:
        if isinstance(stage, ArtifactReadStage):
            available.add(stage.output)
            continue
        if not isinstance(stage, LoweredLocal):
            raise _invalid("fixed schedule contains a source stage")
        name = stage.stage.node.method.name
        arity = (
            len(stage.stage.node.inputs)
            if isinstance(
                stage.stage.node.parameters,
                (AttributionDerive, PartsTransport, ReferenceDerive, DisplayRank, DisplayTable),
            )
            else 2
            if name
            in (
                "cell.difference",
                "cell.relative_change",
                "cell.ratio",
                "association.spearman",
                "group.attach",
                "group.complete",
            )
            else 1
        )
        if (
            name
            not in (
                "journey.match",
                "journey.duration",
                "journey.completed",
                "journey.read",
                "occurrence.prepare",
                "group.complete",
                "group.attach",
                "parts_transport",
                "domain.cohort",
                "reference.share",
                "reference.penetration",
                "reference.standardize",
                "attribution.additive_difference",
                "attribution.component_mix",
                "display.rank",
                "display.table",
                "map_correspond",
                "state_rollup.min",
                "state_rollup.max",
                "state_rollup",
                "state_rollup.count",
                "state_rollup.sum_zero",
                "state_rollup.ratio",
                "state_rollup.weighted_mean",
                "state_rollup.mean",
                "state_rollup.fold",
                "state_rollup.linear",
                "cell.difference",
                "cell.relative_change",
                "cell.ratio",
                "association.spearman",
                "row.count",
                "row.count_defined",
                "row.sum",
                "row.mean",
                "row.min",
                "row.max",
            )
            or len(stage.stage.inputs) != arity
            or not set(stage.stage.inputs) <= available
        ):
            raise _invalid("unqualified fixed method schedule")
        if name in (
            "state_rollup.min",
            "state_rollup.max",
            "state_rollup",
            "state_rollup.count",
            "state_rollup.sum_zero",
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ) and any(check.node_id == stage.stage.node.identity for check in lowered.admitted.checks):
            raise _invalid("fixed rollup lacks frozen completed partition and coverage evidence")
        if (
            arity == 2
            and name not in ("group.attach", "group.complete", "domain.cohort")
            and not isinstance(
                stage.stage.node.parameters,
                (AttributionDerive, CellDerive, ReferenceDerive, DisplayRank, DisplayTable),
            )
        ):
            domains = tuple(edge.node.signature.domain for edge in stage.stage.node.inputs)
            if any(
                domain.binding != domains[0].binding
                or domain.instance_key != domains[0].instance_key
                or domain.target_key != domains[0].target_key
                for domain in domains[1:]
            ):
                raise _invalid("fixed endpoints lack one frozen common member binding")
        available.add(stage.stage.output)
    if lowered.primary_output not in available:
        raise _invalid("fixed schedule lacks its primary output")


def execute_verified_fixed(
    prepared: PreparedGraph,
    lowered: LoweredPlan,
    inputs: tuple[VerifiedFixedInput, ...],
) -> ExchangeResult:
    """Execute each admitted fixed stage once without intermediate publication."""
    if prepared.admitted is not lowered.admitted:
        raise _invalid("prepared and lowered fixed plans differ")
    validate_fixed_schedule(lowered)
    selected: dict[str, VerifiedFixedInput] = {}
    for item in inputs:
        prior = selected.get(item.artifact_ref)
        if prior is not None and prior is not item:
            raise _invalid("repeated Artifact input changed its verified receipt")
        selected[item.artifact_ref] = item
    results: dict[str, ExchangeResult] = {}
    proofs: dict[str, str] = {}
    completed: list[CompletedCheck] = []
    binding = ",".join(item.artifact_ref for item in inputs)
    for stage in lowered.stages:
        if isinstance(stage, ArtifactReadStage):
            selected_input = selected.get(stage.leaf.artifact.ref)
            if (
                selected_input is None
                or selected_input.result.contract.signature != stage.leaf.signature
                or selected_input.result.contract.input_binding != selected_input.artifact_ref
                or not isinstance(stage.leaf.value_type, (ScalarType, DecimalType, DurationType))
                or (
                    "value" in selected_input.result.primary.column_names
                    and not matches_arrow_scalar(
                        selected_input.result.primary.schema.field("value").type,
                        stage.leaf.value_type,
                    )
                )
            ):
                raise _invalid("fixed stage differs from its exact verified Artifact")
            results[stage.output] = selected_input.result
            proofs[stage.output] = selected_input.receipt.bytes_hash
            continue
        assert isinstance(stage, LoweredLocal)
        values = tuple(results[key] for key in stage.stage.inputs)
        proof = hashlib.sha256(
            (
                stage.stage.node.identity + "".join(proofs[key] for key in stage.stage.inputs)
            ).encode()
        ).hexdigest()
        checks = tuple(
            check for check in lowered.admitted.checks if check.node_id == stage.stage.node.identity
        )
        name = stage.stage.node.method.name
        if name in ("journey.duration", "journey.completed", "journey.read"):
            from marivo.analysis.materialization.journey_views import execute as journey_view

            result = journey_view(stage.stage.node, values[0], binding)
        elif name == "journey.match":
            from marivo.analysis.materialization.journey_execution import execute as match_journeys

            result = match_journeys(stage.stage.node, values[0], binding)
        elif name == "occurrence.prepare":
            from marivo.analysis.materialization.domain_preparation import (
                fixed as fixed_occurrences,
            )

            result = fixed_occurrences(stage.stage.node, values[0], binding)
        elif isinstance(stage.stage.node.parameters, AttributionDerive):
            from marivo.analysis.materialization.graph_attribution import fixed

            result = fixed(stage.stage.node, values, binding)
        elif isinstance(stage.stage.node.parameters, (DisplayRank, DisplayTable)):
            from marivo.analysis.materialization.graph_display import fixed

            result = fixed(stage.stage.node, values, binding)
        elif isinstance(stage.stage.node.parameters, ReferenceDerive):
            from marivo.analysis.materialization.graph_reference import fixed_parts
            from marivo.analysis.materialization.graph_reference import result as reference_result

            result = reference_result(
                stage.stage.node, fixed_parts(stage.stage.node, values), binding
            )
        elif name == "group.complete":
            result = _complete_groups_stage(stage, values[0], values[1], binding)
        elif name == "group.attach":
            result = _attach_category_stage(stage, values[0], values[1], binding)
        elif name == "map_correspond":
            params = stage.stage.node.parameters
            if (
                not isinstance(params, MapCorrespond)
                or params.mode not in ("subjects", "group_keys")
                or checks
            ):
                raise _invalid("fixed correspondence requires an exact retained Subject image")
            result = (
                _group_domain_stage(stage, values[0], binding)
                if params.mode == "group_keys"
                else _subject_image(stage, values[0], binding)
            )
        elif name in ("parts_transport", "domain.cohort"):
            if checks:
                raise _invalid("transport carries an unqualified local check")
            result = _transport_stage(stage, values[0], binding, tuple(values[1:]))
        elif name == "state_rollup.fold":
            result = _fold_rollup_stage(stage, values[0], binding)
        elif isinstance(stage.stage.node.parameters, OriginalReduce) and bool(
            stage.stage.node.parameters.coordinates
        ):
            result = _coordinate_rollup_stage(stage, values[0], binding)
        elif name in (
            "state_rollup.ratio",
            "state_rollup.weighted_mean",
            "state_rollup.mean",
            "state_rollup.fold",
            "state_rollup.linear",
        ):
            result = _original_ratio_rollup_stage(stage, values[0], binding)
        elif name == "state_rollup.count":
            result = _original_count_stage(stage, values[0], binding)
        elif name in (
            "state_rollup",
            "state_rollup.sum_zero",
            "state_rollup.min",
            "state_rollup.max",
        ):
            result = _original_sum_stage(stage, values[0], binding)
        elif name in ("cell.difference", "cell.relative_change", "cell.ratio"):
            result = _difference_stage(stage, (values[0], values[1]), proof, binding, checks)
        elif name == "association.spearman":
            if values[0].contract.key_fields != values[1].contract.key_fields:
                raise _invalid("fixed Spearman endpoint keys differ")
            if any(
                check.obligation.check_id
                not in ("source.exact_pairing@v1", "source.finite_numeric@v1")
                for check in checks
            ):
                raise _invalid("unqualified fixed Spearman check")
            result = finish_spearman(
                stage,
                values[0].primary,
                values[1].primary,
                values[0].contract.key_fields,
                binding,
                tuple(CompletedCheck(check, proof) for check in checks),
                checks,
            )
        else:
            result = _row_result(stage, values[0], proof, binding, checks)
        results[stage.stage.output] = result
        proofs[stage.stage.output] = proof
        completed.extend(result.completed_checks)
    result = results[lowered.primary_output]
    return from_arrow(
        result.primary,
        replace(result.contract, pending_checks=lowered.admitted.checks),
        parts=result.parts,
        completed_checks=tuple(completed),
        method_state=result.method_state,
    )


def _subject_image(method: LoweredLocal, source: ExchangeResult, binding: str) -> ExchangeResult:
    """Project complete retained Subject tuples using local set-image semantics."""
    from marivo.analysis.core.model import SubjectPart
    from marivo.analysis.materialization.execute_deadline import check

    subject = next(
        part for part in source.contract.signature.parts if isinstance(part, SubjectPart)
    )
    retained = next(part.table for part in source.parts if part.role == "subject")
    fields = tuple(f"subject__key_{i}" for i in range(len(subject.subject_key)))
    keys = tuple(f"key_{i}" for i in range(len(fields)))
    rows = retained.select(fields).to_pylist()
    seen: set[tuple[object, ...]] = set()
    indices: list[int] = []
    for index, row in enumerate(rows):
        check()
        identity = tuple(row[name] for name in fields)
        if identity in seen:
            if subject.injective:
                raise _invalid("declared injective Subject mapping contains duplicate identities")
            continue
        if any(value is None for value in identity):
            raise _invalid("Subject mapping contains a null identity")
        seen.add(identity)
        indices.append(index)
    primary = retained.select(fields).take(pa.array(indices, type=pa.int64())).rename_columns(keys)
    if b"r7.precision" in (source.primary.schema.metadata or {}):
        primary = primary.replace_schema_metadata(source.primary.schema.metadata)
    part_table = primary
    for key, name in zip(keys, fields, strict=True):
        part_table = part_table.append_column(name, primary[key])
    part = ExchangePart("subject", part_table)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        binding,
        primary.schema,
        keys,
        (PartContract("subject", part_table.schema, keys),),
        (),
        "none",
        None,
        (),
    )
    return from_arrow(primary, contract, parts=(part,))


def _attach_category_stage(
    method: LoweredLocal, source: ExchangeResult, category: ExchangeResult, binding: str
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, AttachCategory)
    category_keys = category.contract.key_fields
    labels: dict[tuple[object, ...], str | int] = {}
    category_rows = {
        tuple(row[k] for k in category_keys): row for row in category.primary.to_pylist()
    }
    if len(category_rows) != category.primary.num_rows:
        raise _invalid("classification has duplicate complete keys")
    mapping = next((p.table for p in source.parts if p.role == "subject"), None)
    rows = (
        mapping.to_pylist()
        if params.subject_mapping and mapping is not None
        else numeric_primary(source.primary).to_pylist()
    )
    for row in rows:
        match = (
            tuple(row[f"subject__key_{i}"] for i in range(len(category_keys)))
            if params.subject_mapping
            else tuple(row[k] for k in source.contract.key_fields)
        )
        classified = category_rows.get(match)
        if (
            classified is None
            or classified["cell_tag"] != "defined"
            or type(classified["value"]) not in (str, int)
        ):
            raise _invalid("classification must be total and Defined on consumed complete keys")
        labels[tuple(row[k] for k in source.contract.key_fields)] = classified["value"]
    new_key = f"key_{len(source.contract.key_fields)}"
    keys = (*source.contract.key_fields, new_key)

    def attach(table: pa.Table) -> pa.Table:
        values = [
            labels[tuple(row[k] for k in source.contract.key_fields)] for row in table.to_pylist()
        ]
        return table.append_column(
            new_key, pa.array(values, type=category.primary.schema.field("value").type)
        ).select(
            (
                *keys,
                *(name for name in table.column_names if name not in source.contract.key_fields),
            )
        )

    primary = attach(source.primary)
    parts = tuple(ExchangePart(p.role, attach(p.table)) for p in source.parts)
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        source.contract.cell_reasons,
        "none",
        None,
    )
    return from_arrow(primary, contract, parts=parts)


def _grouped_row_result(
    method: LoweredLocal,
    source: ExchangeResult,
    proof: str,
    binding: str,
    checks: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    node = method.stage.node
    params = node.parameters
    assert isinstance(params, RowState)
    domain = node.signature.domain
    source_keys = source.contract.signature.domain.instance_key
    columns = tuple(f"key_{source_keys.index(c)}" for c in domain.instance_key)
    keys = tuple(f"key_{i}" for i in range(len(columns)))
    groups: dict[tuple[object, ...], list[int]] = {}
    for index, row in enumerate(numeric_primary(source.primary).to_pylist()):
        groups.setdefault(tuple(row[name] for name in columns), []).append(index)
    scalar_domain = replace(domain, kind="singleton", instance_key=(), target_key=())
    scalar_node = replace(
        node,
        parameters=replace(params, output_domain=scalar_domain),
        derivation=replace(node.derivation, output=replace(node.signature, domain=scalar_domain)),
    )
    scalar_method = replace(method, stage=replace(method.stage, node=scalar_node))
    outputs: list[ExchangeResult] = []
    scalar_template: ExchangeResult | None = None
    for label, indices in sorted(groups.items()):
        primary_slice = source.primary.take(indices)
        identities = {
            tuple(row[k] for k in source.contract.key_fields) for row in primary_slice.to_pylist()
        }
        selected = replace(
            source,
            primary=primary_slice,
            parts=tuple(
                replace(
                    part,
                    table=part.table.take(
                        [
                            index
                            for index, row in enumerate(part.table.to_pylist())
                            if tuple(row[k] for k in source.contract.key_fields) in identities
                        ]
                    ),
                )
                for part in source.parts
            ),
        )
        scalar = _row_result(scalar_method, selected, proof, binding, checks)
        scalar_template = scalar

        def keyed(table: pa.Table, label: tuple[object, ...] = label) -> pa.Table:
            fields = [
                pa.array([value] * table.num_rows, type=source.primary.schema.field(column).type)
                for value, column in zip(label, columns, strict=True)
            ]
            return pa.Table.from_arrays(
                [*fields, *table.columns], names=[*keys, *table.column_names]
            ).replace_schema_metadata(table.schema.metadata)

        outputs.append(
            replace(
                scalar,
                primary=keyed(scalar.primary),
                parts=tuple(replace(p, table=keyed(p.table)) for p in scalar.parts),
                method_state=keyed(scalar.method_state)
                if scalar.method_state is not None
                else None,
            )
        )
    template = (
        scalar_template
        if scalar_template is not None
        else _row_result(
            scalar_method,
            replace(
                source,
                primary=source.primary.slice(0, 0),
                parts=tuple(replace(p, table=p.table.slice(0, 0)) for p in source.parts),
            ),
            proof,
            binding,
            checks,
        )
    )

    def combine(tables: list[pa.Table], schema: pa.Schema) -> pa.Table:
        if tables:
            return pa.concat_tables(tables)
        return pa.Table.from_pylist(
            [],
            schema=pa.schema(
                [
                    *(
                        pa.field(key, source.primary.schema.field(column).type)
                        for key, column in zip(keys, columns, strict=True)
                    ),
                    *schema,
                ]
            ),
        )

    primary = combine([o.primary for o in outputs], template.primary.schema)
    parts = tuple(
        ExchangePart(
            p.role,
            combine(
                [next(v.table for v in o.parts if v.role == p.role) for o in outputs],
                p.table.schema,
            ),
        )
        for p in template.parts
    )
    assert template.method_state is not None
    state = combine(
        [o.method_state for o in outputs if o.method_state is not None],
        template.method_state.schema,
    )
    contract = replace(
        template.contract,
        signature=node.signature,
        schema=primary.schema,
        key_fields=keys,
        parts=tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        state_schema=state.schema,
    )
    return from_arrow(
        primary,
        contract,
        parts=parts,
        method_state=state,
        completed_checks=template.completed_checks,
    )


def _complete_groups_stage(
    method: LoweredLocal, source: ExchangeResult, target: ExchangeResult, binding: str
) -> ExchangeResult:
    from marivo.analysis.methods.state_validation import empty_reduction_cell

    keys = source.contract.key_fields
    targets = [
        tuple(row[k] for k in target.contract.key_fields) for row in target.primary.to_pylist()
    ]
    if len(set(targets)) != len(targets) or any(any(v is None for v in key) for key in targets):
        raise _invalid("explicit target requires unique complete keys")
    rows = {tuple(row[k] for k in keys): row for row in numeric_primary(source.primary).to_pylist()}
    if not rows.keys() <= set(targets):
        raise _invalid("consumed groups are absent from the explicit target")
    if source.contract.signature.quantity is None:
        contract = ExchangeContract(
            method.stage.node.signature,
            method.stage.node.method,
            binding,
            target.primary.schema,
            target.contract.key_fields,
        )
        return from_arrow(target.primary, contract)
    value, tag, reason = empty_reduction_cell(source.contract.signature)
    primary_rows = [
        rows[key]
        if key in rows
        else {
            **dict(zip(keys, key, strict=True)),
            "value": value,
            "cell_tag": tag,
            "cell_reason": reason,
        }
        for key in sorted(targets)
    ]
    primary = pa.Table.from_pylist(primary_rows, schema=source.primary.schema)
    subject_fields = {
        f"subject__key_{i}": source.contract.signature.domain.instance_key.index(coordinate)
        for declaration in source.contract.signature.parts
        if isinstance(declaration, SubjectPart)
        for i, coordinate in enumerate(declaration.subject_key)
    }
    parts: list[ExchangePart] = []
    for part in source.parts:
        retained = {tuple(row[k] for k in keys): row for row in part.table.to_pylist()}
        completed = [
            retained[key]
            if key in retained
            else {
                **dict(zip(keys, key, strict=True)),
                **{
                    name: key[subject_fields[name]]
                    if name in subject_fields
                    else True
                    if name == "coverage__complete"
                    else None
                    if name in ("row_state__min", "row_state__max")
                    else 0
                    for name in part.table.column_names
                    if name not in keys
                },
            }
            for key in sorted(targets)
        ]
        parts.append(
            ExchangePart(part.role, pa.Table.from_pylist(completed, schema=part.table.schema))
        )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(p.role, p.table.schema, keys) for p in parts),
        source.contract.cell_reasons,
        "none",
        None,
    )
    return from_arrow(primary, contract, parts=tuple(parts))


def _group_domain_stage(
    method: LoweredLocal, source: ExchangeResult, binding: str
) -> ExchangeResult:
    domain = method.stage.node.signature.domain
    columns = tuple(
        f"key_{source.contract.signature.domain.instance_key.index(c)}" for c in domain.instance_key
    )
    keys = tuple(f"key_{i}" for i in range(len(columns)))
    identities = sorted(
        {
            tuple(row[column] for column in columns)
            for row in numeric_primary(source.primary).to_pylist()
        }
    )
    schema = pa.schema(
        [
            pa.field(key, source.primary.schema.field(column).type)
            for key, column in zip(keys, columns, strict=True)
        ]
    )
    primary = pa.Table.from_pylist(
        [dict(zip(keys, identity, strict=True)) for identity in identities], schema=schema
    )
    if not columns:
        primary = pa.table({"singleton": pa.array([1], type=pa.int64())})
        schema = primary.schema
    contract = ExchangeContract(
        method.stage.node.signature, method.stage.node.method, binding, schema, keys
    )
    return from_arrow(primary, contract)


def _cohort_stage(
    method: LoweredLocal, target: ExchangeResult, inputs: tuple[ExchangeResult, ...], binding: str
) -> ExchangeResult:
    from marivo.analysis._cohort import decide
    from marivo.analysis.core.predicates import compose, leaves
    from marivo.analysis.methods.predicates import evaluate_leaf

    params = method.stage.node.parameters
    assert (
        isinstance(params, PartsTransport)
        and params.opportunity_domain is not None
        and params.cohort_rule is not None
    )
    keys = target.contract.key_fields
    opportunity_keys = inputs[0].contract.key_fields
    targets = target.primary.to_pylist()
    grid = params.opportunity_domain.time_grid
    expected = {
        (*tuple(row[k] for k in keys), item.identity)
        if item is not None
        else tuple(row[k] for k in keys)
        for row in targets
        for item in (grid.cells if grid is not None else (None,))
    }
    subject_map: dict[tuple[object, ...], tuple[object, ...]] = {}
    if params.opportunity_domain.kind == "journey":
        retained_subject = next(p.table for p in inputs[0].parts if p.role == "subject")
        subject_map = {
            tuple(row[k] for k in opportunity_keys): tuple(
                row[f"subject__key_{i}"] for i in range(len(keys))
            )
            for row in retained_subject.to_pylist()
        }
        if not set(subject_map.values()) <= expected:
            raise _invalid("Journey subjects escape the target population")
        expected = set(subject_map)
    indexed: list[dict[tuple[object, ...], dict[str, object]]] = [{}] + [
        {
            tuple(row[k] for k in opportunity_keys): row
            for row in numeric_primary(item.primary).to_pylist()
        }
        for item in inputs
    ]
    if any(item.contract.key_fields != opportunity_keys for item in inputs) or any(
        set(rows) != expected for rows in indexed[1:]
    ):
        raise _invalid("missing complete opportunity keys or coverage")
    counts: dict[tuple[object, ...], list[int]] = {
        tuple(row[k] for k in keys): [0, 0, 0] for row in targets
    }
    for key in expected:
        truths = tuple(
            compose(
                tree,
                tuple(
                    evaluate_leaf(
                        leaf,
                        indexed[leaf.input_index][key],
                        None if leaf.right_index is None else indexed[leaf.right_index][key],
                        cohort=True,
                    )
                    for leaf in leaves(tree)
                ),
            )
            for tree in params.predicates
        )
        truth = False if False in truths else None if None in truths else True
        counts[subject_map.get(key, key[: len(keys)])][
            1 if truth is None else 0 if truth else 2
        ] += 1
    kept = []
    selected_keys: set[tuple[object, ...]] = set()
    decision_rows = []
    for row in targets:
        key = tuple(row[k] for k in keys)
        t, u, f = counts[key]
        accepted = decide(params.cohort_rule, params.cohort_count, params.cohort_empty, t, u, f)
        if accepted is None:
            raise _invalid("undecidable cohort qualification for a target Subject")
        kept.append(accepted)
        if accepted:
            selected_keys.add(key)
        decision_rows.append(
            {
                **dict(zip(keys, key, strict=True)),
                "cohort_decision__true_count": t,
                "cohort_decision__unknown_count": u,
                "cohort_decision__false_count": f,
                "cohort_decision__accepted": accepted,
                **(
                    {"cohort_decision__opportunity_count": t + u + f}
                    if params.opportunity_domain.kind == "journey"
                    else {}
                ),
            }
        )
    mask = pa.array(kept, type=pa.bool_())
    primary = target.primary.select(keys).filter(mask)
    subject = next(part for part in target.parts if part.role == "subject")
    subject_mask = pa.array(
        [tuple(row[key] for key in keys) in selected_keys for row in subject.table.to_pylist()],
        type=pa.bool_(),
    )
    decision_schema = pa.schema(
        [
            *primary.schema,
            pa.field("cohort_decision__true_count", pa.int64()),
            pa.field("cohort_decision__unknown_count", pa.int64()),
            pa.field("cohort_decision__false_count", pa.int64()),
            pa.field("cohort_decision__accepted", pa.bool_()),
            *(
                [pa.field("cohort_decision__opportunity_count", pa.int64())]
                if params.opportunity_domain.kind == "journey"
                else []
            ),
        ]
    )
    parts = (
        ExchangePart("subject", subject.table.filter(subject_mask)),
        ExchangePart(
            "cohort_decision", pa.Table.from_pylist(decision_rows, schema=decision_schema)
        ),
    )
    status = pa.Table.from_arrays(
        [
            *(primary.column(key) for key in keys),
            pa.array(["accepted"] * len(primary), type=pa.string()),
        ],
        names=[*keys, "status"],
    )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        binding,
        primary.schema,
        keys,
        tuple(PartContract(part.role, part.table.schema, keys) for part in parts),
        (),
        "cohort",
        status.schema,
        (),
    )
    return from_arrow(primary, contract, parts=parts, method_state=status)


def _duration_mean(
    method: LoweredLocal,
    source: ExchangeResult,
    receipt_hash: str,
    input_binding: str,
    checks: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    """Finish an exact tick sum/count once at the retained Duration unit."""
    from fractions import Fraction

    from marivo.analysis.materialization.execute_deadline import check

    params = method.stage.node.parameters
    output_type = method.stage.node.value_type
    assert isinstance(params, RowState) and isinstance(output_type, DurationType)
    if params.method != "mean" or params.output_domain.instance_key:
        raise _invalid("Duration currently qualifies only an ungrouped current-row mean")
    if params.merge:
        retained_input = next(p.table for p in source.parts if p.role == "row_state")
        totals = retained_input["row_state__sum"].to_pylist()
        supports = retained_input["row_state__count"].to_pylist()
        if any(type(v) is not int for v in (*totals, *supports)):
            raise _invalid("Duration merge requires exact retained sum/count")
        total, support = sum(totals), sum(supports)
    else:
        values: list[int] = []
        for cell in _cells(source):
            check()
            if not isinstance(cell, Defined) or type(cell.value) is not int:
                raise _invalid("Duration mean requires Defined ticks; use completed().duration")
            assert isinstance(cell.value, int)
            values.append(cell.value)
        total, support = sum(values), len(values)
    if not -(2**63) <= total < 2**63:
        raise _invalid("Duration sum exceeds int64 ticks")
    value = round(Fraction(total, support)) if support else None
    tag, reason = ("defined", None) if support else ("undefined", "empty_completed_set")
    primary = pa.table(
        {
            "value": pa.array([value], type=arrow_scalar_type(output_type)),
            "cell_tag": pa.array([tag], type=pa.string()),
            "cell_reason": pa.array([reason], type=pa.string()),
        }
    )
    primary = primary.replace_schema_metadata(source.primary.schema.metadata)
    retained = pa.table(
        {
            "row_state__sum": pa.array([total], type=pa.int64()),
            "row_state__count": pa.array([support], type=pa.int64()),
            "row_state__error_bound": pa.array([0.5 if support else 0.0], type=pa.float64()),
        }
    )
    state = pa.table({"status": [tag]})
    proofs = tuple(
        CompletedCheck(
            requirement,
            hashlib.sha256((receipt_hash + requirement.obligation.check_id).encode()).hexdigest(),
        )
        for requirement in checks
    )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary.schema,
        (),
        (PartContract("row_state", retained.schema, ()),),
        (("undefined", ("empty_completed_set",)),),
        "row_mean",
        state.schema,
        checks,
    )
    return from_arrow(
        primary,
        contract,
        parts=(ExchangePart("row_state", retained),),
        method_state=state,
        completed_checks=proofs,
    )
