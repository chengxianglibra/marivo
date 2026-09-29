"""Caller-owned R4.3 fixed execution over fully checked local receipts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import date, datetime

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
    MapCorrespond,
    OriginalReduce,
    PartsTransport,
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
)
from marivo.analysis.materialization.graph_execution import PreparedGraph
from marivo.analysis.materialization.graph_spearman_execution import finish_spearman
from marivo.analysis.methods.local import arithmetic, count, count_defined
from marivo.analysis.methods.physical import ScalarType, matches_arrow_scalar
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
        or not isinstance(read.leaf.value_type, ScalarType)
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
        or not isinstance(read.leaf.value_type, ScalarType)
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
    predicate_source: ExchangeResult | None = None,
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, PartsTransport)
    keys = source.contract.key_fields
    predicate_rows = (
        {}
        if predicate_source is None
        else {tuple(row[k] for k in keys): row for row in predicate_source.primary.to_pylist()}
    )
    if predicate_source is not None and (
        predicate_source.contract.key_fields != keys
        or set(predicate_rows)
        != {tuple(row[k] for k in keys) for row in source.primary.to_pylist()}
    ):
        raise _invalid("predicate dependency lacks equal complete input keys")
    selected_keys: set[tuple[object, ...]] = set()
    keep: list[bool] = []
    for row in source.primary.to_pylist():
        predicate_row = (
            row if predicate_source is None else predicate_rows[tuple(row[k] for k in keys)]
        )
        accepted = True
        for predicate in params.predicates:
            if predicate_row["cell_tag"] != "defined":
                if predicate.unknown == "reject":
                    raise _invalid("fixed predicate received a non-Defined Cell")
                accepted = False
                break
            if not _matches(predicate_row["value"], predicate.operator, predicate.literal):
                accepted = False
                break
        keep.append(accepted)
        if accepted:
            selected_keys.add(tuple(row[key] for key in keys))
    filtered = source.primary.filter(pa.array(keep, type=pa.bool_()))
    columns = (*keys, "value", "cell_tag", "cell_reason") if params.keep_quantity else keys
    primary = filtered.select(columns)
    parts: list[ExchangePart] = []
    for role in params.retained_roles:
        prior = next((part for part in source.parts if part.role == role), None)
        if prior is None:
            raise _invalid("required retained transport part is absent")
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
        tuple(PartContract(part.role, part.table.schema, keys) for part in parts),
        source.contract.cell_reasons if params.keep_quantity else (),
        "none",
        None,
        (),
        params.mode == "where" and not keys,
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
            or not isinstance(read.leaf.value_type, ScalarType)
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
    """Subtract ordered int64 endpoint Cells from two exact retained Artifacts."""
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
            or read.leaf.value_type != ScalarType("int64")
            or contract.schema.field("value").type != pa.int64()
        ):
            raise _invalid("fixed Difference Artifact, binding, signature or int64 type differs")
        values.append(item.result)
    return _difference_stage(
        methods[0],
        (values[0], values[1]),
        selected[0].receipt.bytes_hash + selected[1].receipt.bytes_hash,
        ",".join(item.artifact_ref for item in selected),
        lowered.admitted.checks,
    )


def _difference_stage(
    method: LoweredLocal,
    values: tuple[ExchangeResult, ExchangeResult],
    receipt_hash: str,
    input_binding: str,
    checks: tuple[CheckRequirement, ...],
) -> ExchangeResult:
    current, baseline = values
    keys = current.contract.key_fields
    if not keys or keys != baseline.contract.key_fields:
        raise _invalid("fixed Difference complete endpoint keys differ")
    current_rows = {tuple(row[key] for key in keys): row for row in current.primary.to_pylist()}
    baseline_rows = {tuple(row[key] for key in keys): row for row in baseline.primary.to_pylist()}
    if current_rows.keys() != baseline_rows.keys():
        raise _invalid("fixed Difference endpoint key sets differ")
    primary_rows: list[dict[str, object]] = []
    current_parts: list[dict[str, object]] = []
    baseline_parts: list[dict[str, object]] = []
    for key, first in current_rows.items():
        second = baseline_rows[key]
        left, right = first["value"], second["value"]
        if (
            first["cell_tag"] != "defined"
            or second["cell_tag"] != "defined"
            or type(left) is not int
            or type(right) is not int
            or not -(2**63) <= left - right < 2**63
        ):
            raise _invalid("fixed Difference requires finite Defined int64 endpoints")
        identity = dict(zip(keys, key, strict=True))
        primary_rows.append(
            {**identity, "value": left - right, "cell_tag": "defined", "cell_reason": None}
        )
        current_parts.append(
            {
                **identity,
                "current_endpoint__value": left,
                "current_endpoint__cell_tag": "defined",
                "current_endpoint__cell_reason": None,
            }
        )
        baseline_parts.append(
            {
                **identity,
                "baseline_endpoint__value": right,
                "baseline_endpoint__cell_tag": "defined",
                "baseline_endpoint__cell_reason": None,
            }
        )
    key_schema = tuple(current.primary.schema.field(key) for key in keys)
    primary_schema = pa.schema(
        (
            *key_schema,
            pa.field("value", pa.int64()),
            pa.field("cell_tag", pa.string()),
            pa.field("cell_reason", pa.string()),
        )
    )
    part_schemas = tuple(
        pa.schema(
            (
                *key_schema,
                pa.field(f"{role}__value", pa.int64()),
                pa.field(f"{role}__cell_tag", pa.string()),
                pa.field(f"{role}__cell_reason", pa.string()),
            )
        )
        for role in ("current_endpoint", "baseline_endpoint")
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
            not in ("source.exact_pairing@v1", "source.finite_numeric@v1")
        ):
            raise _invalid("unmatched fixed Difference check")
        result_digest = hashlib.sha256(
            (receipt_hash + check.obligation.check_id).encode()
        ).hexdigest()
        completed.append(CompletedCheck(check, result_digest))
    status_schema = pa.schema((*key_schema, pa.field("status", pa.string())))
    statuses = pa.Table.from_pylist(
        [{**dict(zip(keys, key, strict=True)), "status": "defined"} for key in current_rows],
        schema=status_schema,
    )
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary_schema,
        keys,
        tuple(PartContract(part.role, part.table.schema, keys) for part in parts),
        (),
        "difference",
        status_schema,
        checks,
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
    groups: dict[tuple[object, ...], dict[str, list[int | float]]] = {}
    for item in entries:
        label = tuple(item[key] for key in key_fields)
        if any(value is None for value in label):
            raise _invalid("coordinate state has a missing classification")
        values = groups.setdefault(label, {name: [] for name in components})
        for name in components:
            component_value: object = item[name]
            if type(component_value) not in (int, float) or not isinstance(
                component_value, (int, float)
            ):
                raise _invalid("coordinate state has a nonnumeric component")
            values[name].append(component_value)
    primary_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    for label, values in sorted(groups.items()):
        totals: dict[str, int | float] = {}
        for name, operands in values.items():
            floating = (
                source.contract.schema.field("value").type == pa.float64()
                and params.method in ("sum", "sum_zero")
                and name in ("sum", "numerator_sum")
            )
            try:
                total = math.fsum(operands) if floating else sum(operands)
            except OverflowError:
                raise _invalid("coordinate component overflow") from None
            if not math.isfinite(total) or (not floating and not -(2**63) <= total < 2**63):
                raise _invalid("coordinate component exceeds its exact numeric type")
            totals[name] = total
        reason: str | None = None
        value: int | float | None
        tag = "defined"
        if params.method == "ratio":
            denominator = totals["denominator_sum"]
            contributed = all(
                totals[f"{prefix}_non_null_count"] > 0 or rule == "zero"
                for prefix, rule in zip(
                    ("numerator", "denominator"), original_contract.empty_rules, strict=True
                )
            )
            value = totals["numerator_sum"] / denominator if contributed and denominator else None
            if not contributed:
                tag, reason = "null", "empty_contribution"
            elif not denominator:
                tag, reason = "undefined", "zero_denominator"
        elif params.method == "mean":
            support = totals["non_null_count"]
            value = totals["sum"] / support if support else None
            if not support:
                tag, reason = "null", "empty_contribution"
        elif params.method == "weighted_mean":
            support, denominator = totals["non_null_pair_count"], totals["weight_sum"]
            value = totals["weighted_numerator"] / denominator if support and denominator else None
            if not support or not denominator:
                tag, reason = "null", "empty_contribution" if not support else "zero_weight_sum"
        elif params.method == "linear":
            value = sum(
                totals[name] * (1 if name.startswith("plus_") else -1) for name in components[::2]
            )
            if not -(2**63) <= value < 2**63:
                raise _invalid("linear finish exceeds int64")
            if not all(
                totals[name] > 0 or rule == "zero"
                for name, rule in zip(components[1::2], original_contract.empty_rules, strict=True)
            ):
                value, tag, reason = None, "null", "empty_contribution"
        elif params.method == "count":
            value = totals["count"]
        else:
            value = totals["sum"]
            if params.method == "sum" and totals["non_null_count"] == 0:
                value, tag, reason = None, "null", "empty_contribution"
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
    value_type = method.stage.node.value_type
    assert isinstance(value_type, ScalarType)
    primary_schema = pa.schema(
        [
            *list(zip(key_fields, key_types, strict=True)),
            ("value", pa.type_for_alias(value_type.name)),
            ("cell_tag", pa.string()),
            ("cell_reason", pa.string()),
        ]
    )
    primary = pa.Table.from_pylist(primary_rows, schema=primary_schema)
    state_schema = pa.schema(
        [
            *list(zip(key_fields, key_types, strict=True)),
            *[
                (
                    f"original_state__{name}",
                    pa.float64()
                    if source.contract.schema.field("value").type == pa.float64()
                    and params.method in ("sum", "sum_zero")
                    and name in ("sum", "numerator_sum")
                    else pa.int64(),
                )
                for name in components
            ],
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
    totals = dict.fromkeys(components, 0)
    for row in source.primary.to_pylist():
        original = keyed[tuple(row[key] for key in keys)]
        if not state_matches(state_kind, row, original, empty_rules=original_contract.empty_rules):
            raise _invalid("original ratio Cells differ from their components")
        for component in components:
            value: object = original[f"original_state__{component}"]
            if type(value) is not int:
                raise _invalid("original ratio component is not int64")
            totals[component] += value
    if any(not -(2**63) <= total < 2**63 for total in totals.values()):
        raise _invalid("original ratio component rollup exceeds int64")
    if state_kind == "original_linear":
        numerator = sum(
            totals[name] * (1 if name.startswith("plus_") else -1) for name in components[::2]
        )
        denominator = 1
        defined = all(
            totals[name] > 0 or rule == "zero"
            for name, rule in zip(components[1::2], original_contract.empty_rules, strict=True)
        )
        tag = "defined" if defined else "null"
        reason = None if defined else "empty_contribution"
        if not -(2**63) <= numerator < 2**63:
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
    primary = pa.table(
        {
            "value": pa.array(
                [
                    (numerator if state_kind == "original_linear" else numerator / denominator)
                    if defined
                    else None
                ],
                type=pa.int64() if state_kind == "original_linear" else pa.float64(),
            ),
            "cell_tag": [tag],
            "cell_reason": pa.array(
                [reason],
                type=pa.string(),
            ),
        }
    )
    original = pa.table(
        {
            f"original_state__{name}": pa.array([total], type=pa.int64())
            for name, total in totals.items()
        }
    )
    parts = (
        ExchangePart("original_state", original),
        ExchangePart("coverage", pa.table({"coverage__complete": [True]})),
    )
    status = pa.table({"status": primary["cell_tag"]})
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
    for row in source.primary.to_pylist():
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
    parts = (ExchangePart("original_state", state), ExchangePart("coverage", coverage))
    status = pa.table({"status": ["defined"]})
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
    from marivo.analysis.methods.temporal_fold import (
        Samples,
        decode_samples,
        encode_samples,
        fold_value,
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
    values: list[float | None] = []
    for identity, periods in grouped.items():
        combined: list[tuple[datetime, float, int]] = []
        for rows in periods.values():
            sample_keys = tuple(key for key, _, _ in rows[0])
            if any(tuple(key for key, _, _ in row) != sample_keys for row in rows[1:]):
                raise _invalid("unaligned pre-fold sample coordinates; keep the spatial groups")
            for i, key in enumerate(sample_keys):
                try:
                    total = math.fsum(row[i][1] for row in rows)
                except OverflowError as error:
                    raise _invalid("pre-fold spatial sum exceeds float64") from error
                combined.append((key, total, sum(row[i][2] for row in rows)))
        combined.sort()
        try:
            encoded = encode_samples(tuple(combined))
            value = fold_value(decode_samples(encoded), kind)
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
    primary = pa.table(
        {
            **arrays,
            "value": pa.array(values, type=pa.float64()),
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
    state_kind = (
        "original_sum_zero"
        if method.stage.node.method.name == "state_rollup.sum_zero"
        else "original_sum"
    )
    state = next((part.table for part in source.parts if part.role == "original_state"), None)
    coverage = next((part.table for part in source.parts if part.role == "coverage"), None)
    if state is None or coverage is None:
        raise _invalid("original rollup lacks its bound state or coverage")
    keys = source.contract.key_fields
    keyed = {tuple(row[key] for key in keys): row for row in state.to_pylist()}
    if any(row["coverage__complete"] is not True for row in coverage.to_pylist()):
        raise _invalid("original rollup has incomplete coverage")
    for row in source.primary.to_pylist():
        if not state_matches(state_kind, row, keyed[tuple(row[key] for key in keys)]):
            raise _invalid("original rollup state differs from its retained Cells")
    totals = state.column("original_state__sum").to_pylist()
    support = sum(state.column("original_state__non_null_count").to_pylist())
    value_type = method.stage.node.value_type
    assert isinstance(value_type, ScalarType)
    if value_type.name == "int64":
        total = sum(totals)
        if not -(2**63) <= total < 2**63:
            raise _invalid("original sum exceeds int64")
        physical = pa.int64()
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
            "original_state__sum": pa.array([total], type=physical),
            "original_state__non_null_count": pa.array([support], type=pa.int64()),
        }
    )
    covered = pa.table({"coverage__complete": [True]})
    parts = (ExchangePart("original_state", original), ExchangePart("coverage", covered))
    status = pa.table({"status": ["defined" if defined else "null"]})
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
            2
            if isinstance(stage.stage.node.parameters, PartsTransport)
            and stage.stage.node.parameters.external_predicate
            else 2
            if name in ("cell.difference", "association.spearman", "group.attach", "group.complete")
            else 1
        )
        if (
            name
            not in (
                "group.complete",
                "group.attach",
                "parts_transport",
                "map_correspond",
                "state_rollup",
                "state_rollup.count",
                "state_rollup.sum_zero",
                "state_rollup.ratio",
                "state_rollup.weighted_mean",
                "state_rollup.mean",
                "state_rollup.fold",
                "state_rollup.linear",
                "cell.difference",
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
        if arity == 2 and name not in ("group.attach", "group.complete"):
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
                or not isinstance(stage.leaf.value_type, ScalarType)
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
        if name == "group.complete":
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
        elif name == "parts_transport":
            if checks:
                raise _invalid("transport carries an unqualified local check")
            result = _transport_stage(
                stage, values[0], binding, values[1] if len(values) == 2 else None
            )
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
        elif name in ("state_rollup", "state_rollup.sum_zero"):
            result = _original_sum_stage(stage, values[0], binding)
        elif name == "cell.difference":
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
        else source.primary.to_pylist()
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
    for index, row in enumerate(source.primary.to_pylist()):
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
            )

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
    rows = {tuple(row[k] for k in keys): row for row in source.primary.to_pylist()}
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
        {tuple(row[column] for column in columns) for row in source.primary.to_pylist()}
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
