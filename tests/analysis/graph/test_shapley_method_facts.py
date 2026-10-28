"""Inert sampled facts preserve native endpoints and reject malformed associations."""

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.core.data_schema import DataSchema
from marivo.analysis.core.data_types import DataColumn, DecimalDataType, ScalarDataType
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodFacts,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodSupport,
    FrameMethodTarget,
    FrameMethodValue,
    FrameShapleyCapture,
    FrameShapleyGame,
    FrameShapleyLeaf,
    FrameShapleyMethod,
    FrameShapleyQuantile,
    FrameShapleyReduction,
)
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.terminal_protocol import (
    decode_method_record,
    encode_method_record,
)
from marivo.analysis.methods.frame_method_facts import validate_method_facts

_INT = ScalarDataType("int64")
_FLOAT = ScalarDataType("float64")


def _facts(kind: Literal["absolute", "relative"] = "absolute") -> FrameMethodFacts:
    quantity = "USD" if kind == "absolute" else "1"
    native = DecimalDataType(38, 0)
    values = tuple(
        DataColumn(role, role + ".q", dtype, True, unit)
        for role, dtype, unit in (
            ("contribution", _FLOAT, quantity),
            ("total", native if kind == "absolute" else _FLOAT, quantity),
            ("observed_total", native if kind == "absolute" else _FLOAT, quantity),
            ("share", _FLOAT, "1"),
            ("standard_error", _FLOAT, quantity),
            ("current", native, "USD"),
            ("baseline", native, "USD"),
            ("absolute", native, "USD"),
        )
    )
    paths = tuple(
        (role, "q") if role not in ("current", "baseline", "absolute") else ("detail", role, "q")
        for role in (
            "contribution",
            "total",
            "observed_total",
            "contribution_share",
            "contribution_std_error",
            "current",
            "baseline",
            "absolute",
        )
    )
    values = tuple(
        replace(column, name=".".join(path)) for column, path in zip(values, paths, strict=True)
    )
    presence = tuple(
        DataColumn(side + ".present", f"detail.{side}.present.q", ScalarDataType("boolean"), False)
        for side in ("current", "baseline")
    )
    schema = DataSchema(
        (),
        values,
        presence,
        value_paths=paths,
        presence_paths=(
            ("detail", "current", "present", "q"),
            ("detail", "baseline", "present", "q"),
        ),
    )
    game = FrameShapleyGame(
        "group_replacement",
        "group_replacement/v1",
        "a" * 64,
        "b" * 64,
        "joint_key_union/v1",
        "finite_interval_demand/v1",
        ("occurrence=(0,0);effective_support_min=1", "denominator=(0,1);interval=[1,2]"),
        "exact_metric/v1",
        "permutation_mean/v1",
        "typed_marginal_float64_sum/v1",
    )
    captures = tuple(
        FrameShapleyCapture(
            "capture." + endpoint,
            endpoint,
            "duckdb",
            "1.5.3",
            "typed_sufficient_state/v1",
            4,
            32,
            2,
            96,
            1,
            0,
            0,
            None,
            None,
            None,
        )
        for endpoint in ("current", "baseline")
    )
    leaf = FrameShapleyLeaf(
        (0, 0),
        ("capture.current",),
        ("capture.baseline",),
        FrameShapleyReduction("aggregate", "min"),
        (native,),
        DataColumn("leaf", "leaf.q", native, True, "USD"),
        "non_null_value",
        "skip",
        "not_applicable",
    )
    baseline = Decimal(-(2**53 + 7))
    method = FrameShapleyMethod(
        "shapley",
        FrameMethodValue(values[5], baseline + 4),
        FrameMethodValue(values[6], baseline),
        FrameMethodValue(values[7], Decimal(4)),
        4,
        7,
        "permutation/v1",
        "typed_sufficient_state/v1",
        8,
        2,
        4,
        4,
        2,
        8,
        game,
        (leaf,),
        captures,
    )
    mappings = (
        *tuple(
            FrameMethodOutput(role, path)
            for role, path in zip(
                (
                    "contribution",
                    "total",
                    "observed_total",
                    "share",
                    "contribution_std_error",
                    "current",
                    "baseline",
                    "absolute",
                ),
                paths,
                strict=True,
            )
        ),
        FrameMethodOutput("current_present", schema.presence_paths[0]),
        FrameMethodOutput("baseline_present", schema.presence_paths[1]),
    )
    total = Decimal(4) if kind == "absolute" else float(Decimal(4) / abs(baseline))
    observed = Decimal(3) if kind == "absolute" else 0.5
    metric = FrameAttributeMetric(
        ("q",),
        kind,
        method,
        FrameMethodValue(values[2], observed),
        FrameMethodValue(values[1], total),
        mappings,
        "defined",
    )
    return FrameMethodFacts(
        "attribute",
        "sampled-native-q",
        schema,
        (),
        0,
        (FrameMethodParent((), (metric,)),),
        parent_schema=DataSchema((), ()),
        targets=(FrameMethodTarget(metric.target, metric.kind, metric.outputs),),
    )


def _method(facts: FrameMethodFacts) -> FrameShapleyMethod:
    method = facts.parents[0].metrics[0].method
    assert isinstance(method, FrameShapleyMethod)
    return method


@pytest.mark.parametrize("kind", ["absolute", "relative"])
def test_sampled_facts_roundtrip_preserves_native_values_and_separation(
    kind: Literal["absolute", "relative"],
) -> None:
    facts = _facts(kind)
    validate_method_facts(facts)
    parent = facts.parents[0]
    encoded = encode_method_record(parent)
    decoded = decode_method_record(encoded, FrameMethodParent)
    assert decoded == parent
    method = _method(facts)
    assert method.right.value == Decimal(-(2**53 + 7))
    assert method.change.value == Decimal(4)
    assert method.game.metric_approximation == "exact_metric/v1"
    assert method.game.standard_error == "permutation_mean/v1"
    assert method.captures[0].scanned_rows is None
    assert method.captures[0].wire_bytes is None
    for forbidden in (
        "MetricExpression",
        "FrequencyState",
        "coalition_table",
        "SourceSession",
        "SELECT",
    ):
        assert forbidden not in encoded
    with pytest.raises(FrozenInstanceError):
        method.seed = 9


def test_sampled_facts_count_shared_endpoint_capture_once() -> None:
    method = _method(_facts())
    capture = replace(method.captures[0], endpoint="both")
    leaf = replace(method.leaves[0], baseline_capture_ids=(capture.capture_id,))
    shared = replace(
        method, captures=(capture,), leaves=(leaf,), state_entries=4, captured_source_reads=1
    )
    assert shared.captured_source_reads == 1
    assert shared.leaves[0].current_capture_ids == shared.leaves[0].baseline_capture_ids


def test_sampled_cost_directory_includes_auxiliary_and_split_component_reads() -> None:
    method = _method(_facts())
    auxiliary = replace(method.captures[0], capture_id="domain", endpoint="both", state_entries=0)
    split = replace(method.captures[0], capture_id="extra.component")
    leaf = replace(method.leaves[0], current_capture_ids=("capture.current", "extra.component"))
    captured = replace(
        method,
        leaves=(leaf,),
        captures=(*method.captures, auxiliary, split),
        state_entries=12,
        captured_source_reads=4,
    )
    assert captured.leaves[0].current_capture_ids == ("capture.current", "extra.component")
    assert captured.captures[2].capture_id == "domain"
    with pytest.raises(CoreRuleError):
        replace(leaf, current_capture_ids=())
    with pytest.raises(CoreRuleError):
        replace(leaf, current_capture_ids=("capture.current", "capture.current"))


@pytest.mark.parametrize(
    "changed",
    [
        {"seed": True},
        {"sampled_transitions": 7},
        {"state_entries": 9},
        {"captured_source_reads": 3},
        {"unique_coalitions": 5, "coalition_evaluations": 5},
    ],
)
def test_sampled_facts_reject_inconsistent_costs(changed: dict[str, object]) -> None:
    with pytest.raises(CoreRuleError):
        replace(_method(_facts()), **changed)


def test_sampled_facts_reject_wrong_capture_endpoints_and_masks() -> None:
    method = _method(_facts())
    with pytest.raises(CoreRuleError):
        replace(
            method, leaves=(replace(method.leaves[0], baseline_capture_ids=("capture.current",)),)
        )
    with pytest.raises(CoreRuleError):
        replace(method, captures=(method.captures[0], method.captures[0]))
    with pytest.raises(CoreRuleError):
        replace(method.leaves[0], mask_policy="co_valid_value_weight")
    with pytest.raises(CoreRuleError):
        replace(method.captures[0], scanned_bytes=True)
    with pytest.raises(CoreRuleError):
        replace(method.game, current_formula_id="sum(raw_value)")
    with pytest.raises(CoreRuleError):
        replace(method.game, domain_proof=())
    weighted = replace(
        method.leaves[0],
        reduction=FrameShapleyReduction("aggregate", "weighted_mean"),
        input_types=method.leaves[0].input_types * 2,
        mask_policy="co_valid_value_weight",
        weight_null_policy="skip",
    )
    with pytest.raises(CoreRuleError, match="one captured NULL policy"):
        replace(weighted, weight_null_policy="error")


def test_quantile_facts_require_original_parameter_and_frequency_route() -> None:
    method = _method(_facts())
    with pytest.raises(CoreRuleError):
        FrameShapleyQuantile("quantile", "median", 0.9, "rank_bounds")
    leaf = replace(
        method.leaves[0],
        reduction=FrameShapleyQuantile("quantile", "quantile", 0.95, "rank_bounds"),
    )
    with pytest.raises(CoreRuleError):
        replace(method, leaves=(leaf,))
    captures = tuple(
        replace(item, state_route="typed_value_frequency/v1") for item in method.captures
    )
    frequency = replace(
        method, leaves=(leaf,), captures=captures, state_route="typed_value_frequency/v1"
    )
    assert frequency.leaves[0].reduction.q == 0.95
    auxiliary = replace(
        method.captures[0], capture_id="shared.domain", endpoint="both", state_entries=0
    )
    leaf = replace(
        leaf,
        current_capture_ids=(*leaf.current_capture_ids, "shared.domain"),
        baseline_capture_ids=(*leaf.baseline_capture_ids, "shared.domain"),
    )
    mixed = replace(
        frequency,
        leaves=(leaf,),
        captures=(*captures, auxiliary),
        state_route="typed_sufficient_frequency/v1",
        captured_source_reads=3,
    )
    assert mixed.leaves[0].reduction.q == 0.95


@pytest.mark.parametrize("finisher", ["rank_bounds", "duckdb_native", "postgres_native"])
def test_quantile_finisher_is_closed_captured_metadata_and_survives_wire(
    finisher: Literal["rank_bounds", "duckdb_native", "postgres_native"],
    capsys: pytest.CaptureFixture[str],
) -> None:
    facts = _facts()
    method = _method(facts)
    reduction = FrameShapleyQuantile("quantile", "quantile", 0.95, finisher)
    leaf = replace(method.leaves[0], reduction=reduction)
    captures = tuple(
        replace(item, state_route="typed_value_frequency/v1") for item in method.captures
    )
    method = replace(
        method, leaves=(leaf,), captures=captures, state_route="typed_value_frequency/v1"
    )
    metric = replace(facts.parents[0].metrics[0], method=method)
    parent = FrameMethodParent((), (metric,))
    decoded = decode_method_record(encode_method_record(parent), FrameMethodParent)
    sampled = decoded.metrics[0].method
    assert isinstance(sampled, FrameShapleyMethod)
    captured = sampled.leaves[0].reduction
    assert isinstance(captured, FrameShapleyQuantile)
    assert captured.finisher == finisher
    captured.show(n=20)
    assert f"finisher='{finisher}'" in capsys.readouterr().out
    with pytest.raises(CoreRuleError, match="finishing recipe"):
        replace(reduction, finisher="other_interpolation")
    wire = json.loads(encode_method_record(parent))
    wire["value"]["fields"]["metrics"][0]["fields"]["method"]["fields"]["leaves"][0]["fields"][
        "reduction"
    ]["fields"]["finisher"] = "other_interpolation"
    with pytest.raises(IntegrityError):
        decode_method_record(json.dumps(wire), FrameMethodParent)


def test_sampled_facts_require_float_contribution_and_error_in_target_unit() -> None:
    facts = _facts()
    for position, change in (
        (0, {"dtype": _INT}),
        (4, {"dtype": _INT}),
        (4, {"unit": "seconds"}),
        (3, {"unit": "USD"}),
    ):
        values = list(facts.output_schema.values)
        values[position] = replace(values[position], **change)
        with pytest.raises(CoreRuleError):
            replace(facts, output_schema=replace(facts.output_schema, values=tuple(values)))
    metric = facts.parents[0].metrics[0]
    with pytest.raises(CoreRuleError):
        replace(
            metric,
            outputs=tuple(
                output for output in metric.outputs if output.role != "contribution_std_error"
            ),
        )
    with pytest.raises(CoreRuleError):
        replace(
            metric,
            outputs=(
                *metric.outputs,
                FrameMethodOutput("effect_within", ("effect", "within", "q")),
            ),
        )


def test_sampled_fact_wire_rejects_executable_and_unknown_record_fields() -> None:
    parent = _facts().parents[0]
    envelope = json.loads(encode_method_record(parent))
    method = envelope["value"]["fields"]["metrics"][0]["fields"]["method"]
    method["fields"]["coalition_table"] = "SELECT secret FROM source"
    with pytest.raises(IntegrityError, match=r"fields|record"):
        decode_method_record(json.dumps(envelope), FrameMethodParent)


def test_saved_float_values_are_not_recomputed(monkeypatch: pytest.MonkeyPatch) -> None:
    import marivo.analysis.methods.frame_method_facts as checks

    facts = _facts()
    metric = facts.parents[0].metrics[0]
    method = _method(facts)
    current = FrameMethodValue(replace(method.left.column, dtype=_FLOAT), 1e308)
    baseline = FrameMethodValue(replace(method.right.column, dtype=_FLOAT), -1e308)
    change = FrameMethodValue(replace(method.change.column, dtype=_FLOAT), 7.0)
    method = replace(method, left=current, right=baseline, change=change)
    values = tuple(
        replace(column, dtype=_FLOAT) if index in (1, 2, 5, 6, 7) else column
        for index, column in enumerate(facts.output_schema.values)
    )
    metric = replace(
        metric,
        method=method,
        total=FrameMethodValue(values[1], 7.0),
        observed_total=FrameMethodValue(values[2], 9.0),
    )
    captured = replace(
        facts,
        output_schema=replace(facts.output_schema, values=values),
        parents=(FrameMethodParent((), (metric,)),),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("saved Float64 formula was recomputed")

    monkeypatch.setattr(checks, "_numeric_binary", forbidden)
    validate_method_facts(captured)


def test_replacement_method_cannot_be_nested_under_an_exact_rule() -> None:
    method = _method(_facts())
    with pytest.raises(CoreRuleError, match="complete target"):
        FrameLinearTerm(1, method)
    with pytest.raises(CoreRuleError, match="complete target"):
        FrameDivisionMethod(
            "division", method.left, method.right, method.change, 2, "divide", method
        )

    # Cold decoding also owns the exact child variant boundary. Replacing the
    # nested wire record must fail before an inert record can acquire wrong semantics.
    additive = FrameAdditiveMethod(
        "additive",
        method.left,
        method.right,
        method.change,
        (FrameMethodSupport("input", "rows", 2, 2, 2, 2),),
    )
    linear = FrameLinearMethod(
        "linear", method.left, method.right, method.change, (FrameLinearTerm(1, additive),)
    )
    exact = replace(
        _facts().parents[0].metrics[0],
        method=linear,
        outputs=tuple(
            item
            for item in _facts().parents[0].metrics[0].outputs
            if item.role != "contribution_std_error"
        ),
    )
    parent = FrameMethodParent((), (exact,))
    wire = json.loads(encode_method_record(parent))
    sampled = json.loads(encode_method_record(_facts().parents[0]))["value"]["fields"]["metrics"][
        0
    ]["fields"]["method"]
    wire["value"]["fields"]["metrics"][0]["fields"]["method"]["fields"]["terms"][0]["fields"][
        "method"
    ] = sampled
    with pytest.raises(IntegrityError):
        decode_method_record(json.dumps(wire), FrameMethodParent)


def test_sampled_facts_store_roundtrip_is_source_offline_and_checks_logical_digest(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys
    from datetime import datetime, timezone

    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    from marivo.analysis.core.data_schema import DataAxis
    from marivo.analysis.core.frame_audit import EvaluationRecord, FrameAudit, FrameResultMetadata
    from marivo.analysis.core.frame_data import CompletedFrameData, CompletedFrameResult
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.frame_method_facts import decode_method_payload
    from marivo.analysis.materialization.graph_reading import open_manifest

    facts = _facts()
    key = DataColumn("player", "player", ScalarDataType("string"), False)
    facts = replace(
        facts,
        output_schema=replace(
            facts.output_schema, axes=(DataAxis("players", (key,), name="player"),)
        ),
    )
    method = _method(facts)
    rows = pd.DataFrame(
        {
            "player": pd.Series(["A", "B"], dtype="string[pyarrow]"),
            "contribution": pd.Series([2.0, 2.0], dtype="float64[pyarrow]"),
            "total": pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([Decimal(4)] * 2, type=pa.decimal128(38, 0)))
            ),
            "observed_total": pd.Series(
                pd.arrays.ArrowExtensionArray(pa.array([Decimal(3)] * 2, type=pa.decimal128(38, 0)))
            ),
            "share": pd.Series([0.5, 0.5], dtype="float64[pyarrow]"),
            "standard_error": pd.Series([0.0, 0.0], dtype="float64[pyarrow]"),
            **{
                role: pd.Series(
                    pd.arrays.ArrowExtensionArray(
                        pa.array([value.value] * 2, type=pa.decimal128(38, 0))
                    )
                )
                for role, value in (
                    ("current", method.left),
                    ("baseline", method.right),
                    ("absolute", method.change),
                )
            },
            "current.present": pd.Series([True, True], dtype="bool[pyarrow]"),
            "baseline.present": pd.Series([True, True], dtype="bool[pyarrow]"),
        }
    )
    local = DatasetRuntime.create(tmp_path, "sampled-facts", timezone="UTC").local
    metadata = FrameResultMetadata(
        facts.origin_result_id,
        FrameAudit(
            (), EvaluationRecord(datetime(2026, 10, 28, tzinfo=timezone.utc), (), ("literal",))
        ),
    )
    saved = local.publish(
        CompletedFrameResult(CompletedFrameData(facts.output_schema, rows), metadata, (facts,))
    )
    code = """
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.session.core import Session
from marivo.datasource.adapters import SourceSession

def forbidden(*args, **kwargs):
    raise AssertionError('cold sampled facts attempted source access')
with patch.object(Session, 'catalog', property(forbidden)), patch.object(SourceSession, '__init__', forbidden):
    local = LocalFrameServices(SessionStore.open_existing(Path(sys.argv[1])), sys.argv[2])
    saved = local.open_frame(sys.argv[3])
    facts = local.read_method_facts(saved)[0]
    method = facts.parents[0].metrics[0].method
    assert method.kind == 'shapley'
    assert method.right.value == Decimal(-(2**53+7))
    assert method.game.semantics_version == 'group_replacement/v1'
    assert method.game.domain_proof == ('occurrence=(0,0);effective_support_min=1', 'denominator=(0,1);interval=[1,2]')
    assert method.captures[0].scanned_rows is None
    assert not hasattr(method, 'coalition_table') and not hasattr(method, 'states')
    assert local.read_data(saved).copy_rows()['contribution'].tolist() == [2.0, 2.0]
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), local.session_ref, saved.artifact_id],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    descriptor = open_manifest(local.store, local.session_ref, saved.artifact_id).method_facts[0]
    path = tmp_path / descriptor.payload.rows.receipt.project_relative_path / "data.parquet"
    payload = pq.read_table(path).to_pandas()
    assert decode_method_payload(descriptor, payload) == facts
    payload.loc[0, "record"] = payload.loc[0, "record"].replace('"seed":7', '"seed":8')
    with pytest.raises(IntegrityError, match="logical digest"):
        decode_method_payload(descriptor, payload)


def test_sampled_native_change_aligns_losslessly_to_common_report_carrier() -> None:
    facts = _facts()
    method = _method(facts)
    values = tuple(
        replace(column, dtype=DecimalDataType(38, 1)) if index in (1, 2) else column
        for index, column in enumerate(facts.output_schema.values)
    )
    metric = replace(
        facts.parents[0].metrics[0],
        total=FrameMethodValue(values[1], Decimal("4.0")),
        observed_total=FrameMethodValue(values[2], Decimal("3.0")),
    )
    aligned = replace(
        facts,
        output_schema=replace(facts.output_schema, values=values),
        parents=(FrameMethodParent((), (metric,)),),
    )
    validate_method_facts(aligned)
    assert _method(aligned).left == method.left
    assert _method(aligned).right == method.right
    assert _method(aligned).change == method.change
    assert _method(aligned).change.column.dtype == DecimalDataType(38, 0)
    assert aligned.parents[0].metrics[0].total.column.dtype == DecimalDataType(38, 1)
    corrupt = replace(metric, total=FrameMethodValue(values[1], Decimal("4.1")))
    with pytest.raises(CoreRuleError):
        validate_method_facts(replace(aligned, parents=(FrameMethodParent((), (corrupt,)),)))


def test_sampled_report_alignment_rejects_precision_loss_and_changed_unit() -> None:
    facts = _facts()
    method = _method(facts)
    native = DecimalDataType(38, 1)
    values = tuple(
        replace(column, dtype=native) if index in (5, 6, 7) else column
        for index, column in enumerate(facts.output_schema.values)
    )
    assert isinstance(method.right.value, Decimal)
    method = replace(
        method,
        left=FrameMethodValue(values[5], method.right.value + Decimal("4.1")),
        right=FrameMethodValue(values[6], method.right.value),
        change=FrameMethodValue(values[7], Decimal("4.1")),
    )
    metric = replace(facts.parents[0].metrics[0], method=method)
    lossy = replace(
        facts,
        output_schema=replace(facts.output_schema, values=values),
        parents=(FrameMethodParent((), (metric,)),),
    )
    with pytest.raises(CoreRuleError, match="lossless"):
        validate_method_facts(lossy)

    values = tuple(
        replace(column, unit="seconds") if index in (0, 1, 2, 4) else column
        for index, column in enumerate(facts.output_schema.values)
    )
    metric = replace(
        facts.parents[0].metrics[0],
        total=FrameMethodValue(values[1], Decimal(4)),
        observed_total=FrameMethodValue(values[2], Decimal(3)),
    )
    wrong_unit = replace(
        facts,
        output_schema=replace(facts.output_schema, values=values),
        parents=(FrameMethodParent((), (metric,)),),
    )
    with pytest.raises(CoreRuleError, match="native change unit"):
        validate_method_facts(wrong_unit)
