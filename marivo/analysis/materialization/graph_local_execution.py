"""Caller-owned R4.3 fixed execution over fully checked local receipts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace

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
    SubjectPart,
    Undefined,
    Unknown,
)
from marivo.analysis.core.rules import OriginalReduce, PartsTransport
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
from marivo.analysis.methods.physical import ScalarType
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
    cells = _cells(verified)
    name = method.stage.node.method.name
    state: dict[str, list[int | float]]
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
                name in ("row.sum", "row.mean")
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
                if field == "row_state__sum" and type(values[0]) is float
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


def _matches(value: object, operator: str, expected: int | float | str) -> bool:
    if operator == "eq":
        return value == expected
    if operator == "ne":
        return value != expected
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
        or "value" not in source.primary.column_names
        or source.primary.schema.field("value").type != pa.type_for_alias(read.leaf.value_type.name)
    ):
        raise _invalid("fixed transport input signature, binding or type differs")
    return _transport_stage(method, source, selected.artifact_ref)


def _transport_stage(
    method: LoweredLocal, source: ExchangeResult, input_binding: str
) -> ExchangeResult:
    params = method.stage.node.parameters
    assert isinstance(params, PartsTransport)
    keys = source.contract.key_fields
    selected_keys: set[tuple[object, ...]] = set()
    keep: list[bool] = []
    for row in source.primary.to_pylist():
        accepted = True
        for predicate in params.predicates:
            if row["cell_tag"] != "defined":
                if predicate.unknown == "reject":
                    raise _invalid("fixed predicate received a non-Defined Cell")
                accepted = False
                break
            if not _matches(row["value"], predicate.operator, predicate.value):
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
    assert isinstance(params, OriginalReduce) and params.coordinate is not None
    coordinate = next(
        p for p in source.contract.signature.parts if isinstance(p, CoordinateStatePart)
    )
    state = next(p.table for p in source.parts if p.role == "coordinate_state")
    coordinate_column = coordinate.column_for(params.coordinate)
    groups: dict[str, dict[str, list[int | float]]] = {}
    for row in state.to_pylist():
        entries: object = row["coordinate_state__groups"]
        if not isinstance(entries, list):
            raise _invalid("coordinate state is not a complete list")
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get(coordinate_column), str):
                raise _invalid("coordinate state has an invalid key")
            label = item[coordinate_column]
            values = groups.setdefault(label, {name: [] for name in coordinate.components})
            for name in coordinate.components:
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
        for name, entries in values.items():
            floating = coordinate.value_type == "float64" and name in ("sum", "numerator_sum")
            try:
                total = math.fsum(entries) if floating else sum(entries)
            except OverflowError:
                raise _invalid("coordinate component overflow") from None
            if not math.isfinite(total) or (not floating and not -(2**63) <= total < 2**63):
                raise _invalid("coordinate component exceeds its exact numeric type")
            totals[name] = total
        reason: str | None = None
        value: int | float | None
        tag = "defined"
        if params.method == "ratio":
            denominator = totals["denominator_count"]
            value = totals["numerator_sum"] / denominator if denominator else None
            if not denominator:
                tag, reason = "undefined", "zero_denominator"
        elif params.method == "count":
            value = totals["count"]
        else:
            value = totals["sum"]
            if params.method == "sum" and totals["non_null_count"] == 0:
                value, tag, reason = None, "null", "empty_contribution"
        primary_rows.append(
            {"key_0": label, "value": value, "cell_tag": tag, "cell_reason": reason}
        )
        state_rows.append(
            {"key_0": label, **{f"original_state__{name}": value for name, value in totals.items()}}
        )
    value_type = method.stage.node.value_type
    assert isinstance(value_type, ScalarType)
    primary_schema = pa.schema(
        [
            ("key_0", pa.string()),
            ("value", pa.type_for_alias(value_type.name)),
            ("cell_tag", pa.string()),
            ("cell_reason", pa.string()),
        ]
    )
    primary = pa.Table.from_pylist(primary_rows, schema=primary_schema)
    state_schema = pa.schema(
        [
            ("key_0", pa.string()),
            *[
                (
                    f"original_state__{name}",
                    pa.float64()
                    if coordinate.value_type == "float64" and name in ("sum", "numerator_sum")
                    else pa.int64(),
                )
                for name in coordinate.components
            ],
        ]
    )
    original = pa.Table.from_pylist(state_rows, schema=state_schema)
    labels = pa.array(sorted(groups), type=pa.string())
    coverage = pa.table(
        {"key_0": labels, "coverage__complete": pa.array([True] * len(groups), type=pa.bool_())}
    )
    status = pa.table({"key_0": labels, "status": primary["cell_tag"]})
    parts = (ExchangePart("original_state", original), ExchangePart("coverage", coverage))
    semantics = REGISTRY.lookup(method.stage.node.method).semantics
    state_kind = semantics.persistent_state_kind
    assert state_kind is not None
    contract = ExchangeContract(
        method.stage.node.signature,
        method.stage.node.method,
        input_binding,
        primary_schema,
        ("key_0",),
        tuple(PartContract(p.role, p.table.schema, ("key_0",)) for p in parts),
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
    components = ("numerator_sum", "numerator_non_null_count", "denominator_count")
    totals = dict.fromkeys(components, 0)
    for row in source.primary.to_pylist():
        original = keyed[tuple(row[key] for key in keys)]
        if not state_matches("original_ratio", row, original):
            raise _invalid("original ratio Cells differ from their components")
        for component in components:
            value: object = original[f"original_state__{component}"]
            if type(value) is not int:
                raise _invalid("original ratio component is not int64")
            totals[component] += value
    if any(not -(2**63) <= total < 2**63 for total in totals.values()):
        raise _invalid("original ratio component rollup exceeds int64")
    defined = totals["denominator_count"] > 0
    primary = pa.table(
        {
            "value": pa.array(
                [totals["numerator_sum"] / totals["denominator_count"] if defined else None],
                type=pa.float64(),
            ),
            "cell_tag": ["defined" if defined else "undefined"],
            "cell_reason": pa.array([None if defined else "zero_denominator"], type=pa.string()),
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
        (("undefined", ("zero_denominator",)),),
        "original_ratio",
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
        arity = 2 if name in ("cell.difference", "association.spearman") else 1
        if (
            name
            not in (
                "parts_transport",
                "state_rollup",
                "state_rollup.count",
                "state_rollup.sum_zero",
                "state_rollup.ratio",
                "cell.difference",
                "association.spearman",
                "row.count",
                "row.count_defined",
                "row.sum",
                "row.mean",
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
        ) and any(check.node_id == stage.stage.node.identity for check in lowered.admitted.checks):
            raise _invalid("fixed rollup lacks frozen completed partition and coverage evidence")
        if arity == 2:
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
                or selected_input.result.primary.schema.field("value").type
                != pa.type_for_alias(stage.leaf.value_type.name)
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
        if name == "parts_transport":
            if checks:
                raise _invalid("transport carries an unqualified local check")
            result = _transport_stage(stage, values[0], binding)
        elif (
            isinstance(stage.stage.node.parameters, OriginalReduce)
            and stage.stage.node.parameters.coordinate is not None
        ):
            result = _coordinate_rollup_stage(stage, values[0], binding)
        elif name == "state_rollup.ratio":
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
