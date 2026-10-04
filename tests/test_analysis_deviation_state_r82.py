"""Pure exchange/state oracles; these do not qualify source or cold Runtime routes."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pyarrow as pa
import pytest

import marivo.semantic as ms
from marivo.analysis.core.graph import Edge, FixedLeaf, MethodNode, method_node
from marivo.analysis.core.model import (
    Binding,
    Coordinate,
    DerivedQuantity,
    DomainSignature,
    Signature,
    TableFitsPart,
)
from marivo.analysis.core.rules import DeviationFit, DeviationRead, DisplayTable
from marivo.analysis.errors import StatisticalRelationError
from marivo.analysis.materialization import deviation_execution as execution
from marivo.analysis.materialization.graph_exchange import (
    ExchangeContract,
    ExchangeResult,
    from_arrow,
)
from marivo.analysis.methods.deviation_numeric import DeviationMethod
from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
from marivo.analysis.methods.semantics import MethodKey
from marivo.analysis.refs import ArtifactRef
from scripts.r81_static_freeze import array_json, checked, object_json


def _input(
    cells: tuple[tuple[float | None, str, str | None], ...], method: DeviationMethod
) -> tuple[MethodNode, ExchangeResult]:
    binding = Binding("r82", "sales", "customers", "original")
    keys = (Coordinate(ms.ref.entity("sales.customer"), "id", "identity"),)
    signature = Signature(
        DomainSignature(binding, "entity", keys, keys, "members"),
        DerivedQuantity("input", "input@v1", ("fact",), "CNY", "original", "strict"),
    )
    table = pa.table(
        {
            "key_0": pa.array([str(i) for i in range(len(cells))], type=pa.string()),
            "value": pa.array([cell[0] for cell in cells], type=pa.float64()),
            "cell_tag": pa.array([cell[1] for cell in cells], type=pa.string()),
            "cell_reason": pa.array([cell[2] for cell in cells], type=pa.string()),
        }
    )
    contract = ExchangeContract(
        signature,
        MethodKey("bind_project"),
        "input",
        table.schema,
        ("key_0",),
        cell_reasons=(
            ("null", ("source_null",)),
            ("undefined", ("missing_side",)),
            ("unknown", ("coverage_censored",)),
        ),
    )
    source = from_arrow(table, contract)
    leaf = FixedLeaf(
        ArtifactRef(ref="artifact_r82"),
        "a" * 64,
        signature,
        ScalarType("float64"),
        FixedShape(NoTime()),
    )
    node = method_node(
        (Edge("quantity", leaf),),
        DeviationFit(method, "float64", "fit"),
        value_type=ScalarType("float64"),
    )
    return node, source


@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("values", ((), (1.0,), (2.0, 2.0), (1.0, 1.0, 4.0), (1.0, 2.0, 7.0)))
def test_four_cell_policies_and_exact_scope(
    method: DeviationMethod, values: tuple[float, ...]
) -> None:
    cells = (
        *tuple((value, "defined", None) for value in values),
        (None, "null", "source_null"),
        (None, "undefined", "missing_side"),
        (None, "unknown", "coverage_censored"),
    )
    node, source = _input(cells, method)
    result = execution.execute(node, (source,), "result")
    inputs, state = execution._decode(result.parts)
    rows = execution.load(state.views).to_pylist()
    partition = state.partitions[0]
    assert (partition.defined, partition.null, partition.undefined, partition.unknown) == (
        len(values),
        1,
        1,
        1,
    )
    center = (
        sum(Fraction(value) for value in values) / len(values)
        if values and method == "zscore"
        else Fraction(sorted(values)[len(values) // 2])
        if values
        else None
    )
    for row, original in zip(rows, cells, strict=True):
        assert (
            row["observed__value"],
            row["observed__cell_tag"],
            row["observed__cell_reason"],
        ) == original
        if original[1] != "defined":
            assert row["score__cell_tag"] == row["deviation__cell_tag"] == original[1]
        assert row["reference__value"] == (float(center) if center is not None else None)
    if len(values) == 1:
        assert rows[0]["score__cell_reason"] == "insufficient_samples"
    if len(values) == 2:
        assert rows[0]["score__cell_reason"] == "zero_scale"
    if method == "mad" and values == (1.0, 1.0, 4.0):
        assert partition.fit.branch == "mean_absolute_deviation"
        assert [row["score__value"] for row in rows[:3]] == [0.0, 0.0, 3.0]
    assert execution.load(inputs.primary).num_rows == len(cells)


@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_empty_receiver_has_no_synthetic_row(method: DeviationMethod) -> None:
    node, source = _input((), method)
    result = execution.execute(node, (source,), "result")
    _, state = execution._decode(result.parts)
    assert result.primary.num_rows == 0
    assert state.partitions[0].fit.n == 0


@pytest.mark.parametrize(
    "damage",
    (
        "center",
        "zero_center_denominator",
        "zero_scale_denominator",
        "count",
        "order",
        "scope",
        "view",
        "certificate",
        "certificate_decimal_encoding",
        "certificate_precision_step",
        "certificate_precision_digits",
        "certificate_precision_padding",
        "policy",
    ),
)
def test_self_consistent_digests_do_not_repair_damaged_authority(damage: str) -> None:
    from marivo.analysis.methods.deviation_numeric import RationalFact

    node, source = _input(
        ((1.0, "defined", None), (2.0, "defined", None), (7.0, "defined", None)), "zscore"
    )
    result = execution.execute(node, (source,), "result")
    execution.validate(result.contract, result.primary, result.parts)
    _, state = execution._decode(result.parts)
    part = state.partitions[0]
    if damage == "center":
        state = replace(
            state, partitions=(replace(part, fit=replace(part.fit, center=RationalFact("0", "1"))),)
        )
    elif damage == "zero_center_denominator":
        state = replace(
            state, partitions=(replace(part, fit=replace(part.fit, center=RationalFact("1", "0"))),)
        )
    elif damage == "zero_scale_denominator":
        state = replace(
            state,
            partitions=(replace(part, fit=replace(part.fit, raw_scale=RationalFact("1", "0"))),),
        )
    elif damage == "count":
        state = replace(state, partitions=(replace(part, defined=4),))
    elif damage == "order":
        state = replace(state, partitions=(replace(part, order=tuple(reversed(part.order))),))
    elif damage == "scope":
        state = replace(state, partitions=(replace(part, indices=(0, 1)),))
    elif damage == "view":
        table = execution.load(state.views)
        table = table.set_column(
            table.schema.get_field_index("reference__value"),
            "reference__value",
            pa.array([0.0, 0.0, 0.0]),
        )
        state = replace(state, views=execution.save(table))
    elif damage.startswith("certificate"):
        witness = state.scores[0]
        assert witness.root is not None
        damaged_root = (
            replace(witness.root, lower="invalid")
            if damage == "certificate_decimal_encoding"
            else replace(witness.root, lower="1", upper="1")
            if damage == "certificate"
            else replace(
                witness.root,
                precision=witness.root.precision
                + (1 if damage == "certificate_precision_step" else 40),
            )
        )
        if damage == "certificate_precision_padding":

            def pad(value: str) -> str:
                fact = Decimal(value).as_tuple()
                assert isinstance(fact.exponent, int)
                return str(
                    Decimal(
                        (fact.sign, (*fact.digits, *(0 for _ in range(40))), fact.exponent - 40)
                    )
                )

            damaged_root = replace(
                damaged_root, lower=pad(witness.root.lower), upper=pad(witness.root.upper)
            )
        state = replace(
            state,
            scores=(
                replace(witness, root=damaged_root),
                *state.scores[1:],
            ),
        )
    parts = tuple(
        execution._part("fit_state", execution.STATE.dump_json(state).decode())
        if p.role == "fit_state"
        else execution._part("finding_policy", "{}")
        if damage == "policy" and p.role == "finding_policy"
        else p
        for p in result.parts
    )
    with pytest.raises(StatisticalRelationError) as raised:
        execution.validate(result.contract, result.primary, parts)
    assert raised.value.code == "r8.retained_part"


def _mixed_fit_table() -> tuple[ExchangeResult, ExchangeResult]:
    from marivo.analysis.materialization.graph_display import fixed

    node, source = _input(
        ((1.0, "defined", None), (2.0, "defined", None), (7.0, "defined", None)), "zscore"
    )
    scored = execution.execute(node, (source,), "scored")
    table_node = method_node(
        (Edge("quantity", node), Edge("quantity", node.inputs[0].node)),
        DisplayTable(
            ("score", "original"),
            ("float64", "float64"),
            (node.fingerprint, node.inputs[0].node.fingerprint),
        ),
        value_type=ScalarType("float64"),
    )
    result = fixed(table_node, (scored, source), "table")
    assert any(isinstance(p, TableFitsPart) for p in result.contract.signature.parts)
    from_arrow(
        result.primary, result.contract, parts=result.parts, method_state=result.method_state
    )
    return result, scored


def test_mixed_table_verification_preserves_execute_deadline() -> None:
    from marivo.analysis.core.domain_captures import DomainPreparationError
    from marivo.analysis.materialization.execute_deadline import CURRENT, ExecuteDeadline

    result, _ = _mixed_fit_table()
    token = CURRENT.set(ExecuteDeadline(0.0, clock=lambda: 601.0))
    try:
        with pytest.raises(DomainPreparationError) as raised:
            execution.validate_table_fits(result.contract, result.primary, result.parts)
        assert raised.value.constraint_id == "r7.execute_timeout"
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize(
    "damage",
    ("version", "index", "count", "keys", "input_binding", "primary", "missing_fit", "fit_state"),
)
def test_mixed_table_capture_preserves_independent_fit_authority(damage: str) -> None:
    from marivo.analysis.methods.deviation_numeric import RationalFact

    result, scored = _mixed_fit_table()
    payload = next(p.table for p in result.parts if p.role == "table_fits")["table_fits__retained"][
        0
    ].as_py()
    capture = execution.TABLE_FITS.validate_json(payload, strict=True)
    column = capture.columns[0]
    if damage == "version":
        payload = payload.replace('"r8.table_fits/v1"', '"r8.table_fits/v2"')
    else:
        if damage == "index":
            column = replace(column, index=1)
        elif damage == "count":
            capture = replace(capture, columns=())
        elif damage == "keys":
            column = replace(column, keys=("wrong_key",))
        elif damage == "input_binding":
            column = replace(column, input_binding="")
        elif damage == "primary":
            primary = execution.load(column.primary).set_column(
                1, "value", pa.array([0.0, 0.0, 0.0])
            )
            column = replace(column, primary=execution.save(primary))
        elif damage == "missing_fit":
            column = replace(column, parts=tuple(p for p in column.parts if p.role != "fit_state"))
        else:
            state = execution._decode(scored.parts)[1]
            part = state.partitions[0]
            state = replace(
                state,
                partitions=(replace(part, fit=replace(part.fit, center=RationalFact("1", "0"))),),
            )
            column = replace(
                column,
                parts=tuple(
                    replace(
                        p,
                        table=execution.save(
                            execution._part(
                                "fit_state", execution.STATE.dump_json(state).decode()
                            ).table
                        ),
                    )
                    if p.role == "fit_state"
                    else p
                    for p in column.parts
                ),
            )
        if damage != "count":
            capture = replace(capture, columns=(column,))
        payload = execution.TABLE_FITS.dump_json(capture).decode()
    parts = tuple(
        execution._part("table_fits", payload) if p.role == "table_fits" else p
        for p in result.parts
    )
    with pytest.raises(StatisticalRelationError) as raised:
        from_arrow(result.primary, result.contract, parts=parts, method_state=result.method_state)
    assert raised.value.code == "r8.retained_part"


@pytest.mark.parametrize("role", ("fit_inputs", "fit_state", "finding_policy"))
def test_each_required_part_is_independently_required(role: str) -> None:
    node, source = _input(((1.0, "defined", None), (2.0, "defined", None)), "mad")
    result = execution.execute(node, (source,), "result")
    with pytest.raises(StatisticalRelationError):
        execution.validate(
            result.contract, result.primary, tuple(p for p in result.parts if p.role != role)
        )


@pytest.mark.parametrize("damage", ("input_carrier", "key_carrier", "view_carrier", "null_key"))
def test_self_consistent_ipc_digests_cannot_change_typed_carriers(damage: str) -> None:
    node, source = _input(
        ((1.0, "defined", None), (2.0, "defined", None), (7.0, "defined", None)), "zscore"
    )
    result = execution.execute(node, (source,), "fixture")
    execution.validate(result.contract, result.primary, result.parts)
    inputs, state = execution._decode(result.parts)
    if damage == "view_carrier":
        table = execution.load(state.views)
        field = "observed__value"
        table = table.set_column(
            table.schema.get_field_index(field), field, table[field].cast(pa.float32())
        )
        state = replace(state, views=execution.save(table))
    else:
        table = execution.load(inputs.primary)
        field = "value" if damage == "input_carrier" else inputs.keys[0]
        table = table.set_column(
            table.schema.get_field_index(field),
            field,
            pa.array([None, "1", "2"], type=pa.string())
            if damage == "null_key"
            else table[field].cast(
                pa.float32() if damage == "input_carrier" else pa.large_string()
            ),
        )
        inputs = replace(inputs, primary=execution.save(table))
        state = replace(
            state, input_digest=hashlib.sha256(execution.INPUTS.dump_json(inputs)).hexdigest()
        )
    parts = tuple(
        execution._part(p.role, execution.INPUTS.dump_json(inputs).decode())
        if p.role == "fit_inputs"
        else execution._part(p.role, execution.STATE.dump_json(state).decode())
        if p.role == "fit_state"
        else p
        for p in result.parts
    )
    with pytest.raises(StatisticalRelationError):
        execution.validate(result.contract, result.primary, parts)


@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize(
    "damage",
    (
        "input_version",
        "state_version",
        "fit_version",
        "numeric_policy",
        "transform",
        "input_scope",
        "input_keys",
        "fit_identity",
        "sample_count",
        "state_count",
        "scale_branch",
        "current_key",
        "current_value",
    ),
)
def test_each_closed_recovery_fact_rejects_after_clean_authority(
    method: DeviationMethod, damage: str
) -> None:
    node, source = _input(
        ((1.0, "defined", None), (2.0, "defined", None), (7.0, "defined", None)), method
    )
    result = execution.execute(node, (source,), "original")
    execution.validate(result.contract, result.primary, result.parts)
    inputs, state = execution._decode(result.parts)
    input_payload = object_json(checked(json.loads(execution.INPUTS.dump_json(inputs))))
    state_payload = object_json(checked(json.loads(execution.STATE.dump_json(state))))
    partition_payload = object_json(array_json(state_payload["partitions"])[0])
    fit_payload = object_json(partition_payload["fit"])
    primary = result.primary
    if damage == "input_version":
        input_payload["version"] = "r8.fit_inputs/v2"
    elif damage == "state_version":
        state_payload["version"] = "r8.fit_state/v2"
    elif damage == "fit_version":
        fit_payload["version"] = "v2"
    elif damage == "numeric_policy":
        fit_payload["numeric_policy"] = "other_policy"
    elif damage == "transform":
        state_payload["transform"] = "refit_selected_rows@v1"
    elif damage == "input_scope":
        object_json(object_json(input_payload["signature"])["domain"])["definition_id"] = (
            "other_scope"
        )
    elif damage == "input_keys":
        input_payload["keys"] = ["other_key"]
    elif damage == "fit_identity":
        state_payload["fit_id"] = "other_fit"
    elif damage == "sample_count":
        fit_payload["n"] = 4
    elif damage == "state_count":
        partition_payload["unknown"] = 1
    elif damage == "scale_branch":
        fit_payload["branch"] = "scaled_mad" if method == "zscore" else "population_stddev"
    else:
        column = "key_0" if damage == "current_key" else "value"
        primary = primary.set_column(
            primary.schema.get_field_index(column),
            column,
            pa.array(["escape", "1", "2"])
            if damage == "current_key"
            else pa.array([100.0, 100.0, 100.0]),
        )
    input_json = json.dumps(input_payload, separators=(",", ":"))
    state_payload["input_digest"] = hashlib.sha256(input_json.encode()).hexdigest()
    state_json = json.dumps(state_payload, separators=(",", ":"))
    parts = tuple(
        execution._part(p.role, input_json)
        if p.role == "fit_inputs"
        else execution._part(p.role, state_json)
        if p.role == "fit_state"
        else p
        for p in result.parts
    )
    with pytest.raises(StatisticalRelationError) as error:
        execution.validate(result.contract, primary, parts)
    assert error.value.code == "r8.retained_part"


@pytest.mark.parametrize("method", ("zscore", "mad"))
@pytest.mark.parametrize("damage", ("version", "center"))
def test_nested_fit_recovery_checks_prior_authority_without_refitting(
    method: DeviationMethod, damage: str
) -> None:
    from marivo.analysis.materialization.graph_findings import POLICY

    first, raw = _input(
        ((1.0, "defined", None), (2.0, "defined", None), (7.0, "defined", None)), method
    )
    fitted = execution.execute(first, (raw,), "first")
    read = method_node(
        (Edge("quantity", first),),
        DeviationRead("float64", "score"),
        value_type=ScalarType("float64"),
    )
    field = execution.project(fitted, "score", node=read, binding="field")
    next_fit = method_node(
        (Edge("quantity", read),),
        DeviationFit(method, "float64", "next_fit"),
        value_type=ScalarType("float64"),
    )
    result = execution.execute(next_fit, (field,), "second")
    execution.validate(result.contract, result.primary, result.parts)
    inputs, state = execution._decode(result.parts)
    saved_state = next(p for p in inputs.parts if p.role == "fit_state")
    table = execution.load(saved_state.table)
    payload = object_json(checked(json.loads(table["fit_state__retained"][0].as_py())))
    if damage == "version":
        payload["version"] = "r8.fit_state/v2"
    else:
        fit = object_json(object_json(array_json(payload["partitions"])[0])["fit"])
        fit["center"] = {"numerator": "0", "denominator": "1"}
    damaged = execution._part("fit_state", json.dumps(payload, separators=(",", ":")))
    inputs = replace(
        inputs,
        parts=tuple(
            replace(p, table=execution.save(damaged.table)) if p.role == "fit_state" else p
            for p in inputs.parts
        ),
    )
    encoded = execution.INPUTS.dump_json(inputs)
    digest = hashlib.sha256(encoded).hexdigest()
    state = replace(state, input_digest=digest)
    policy_part = next(p for p in result.parts if p.role == "finding_policy")
    policy = POLICY.validate_json(policy_part.table["finding_policy__retained"][0].as_py())
    policy = replace(policy, ordered_input_bindings=("capture:" + digest,))
    parts = tuple(
        execution._part(p.role, encoded.decode())
        if p.role == "fit_inputs"
        else execution._part(p.role, execution.STATE.dump_json(state).decode())
        if p.role == "fit_state"
        else execution._part(p.role, POLICY.dump_json(policy).decode())
        if p.role == "finding_policy"
        else p
        for p in result.parts
    )
    with pytest.raises(StatisticalRelationError):
        execution.validate(result.contract, result.primary, parts)
