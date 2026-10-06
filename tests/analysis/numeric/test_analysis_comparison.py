"""Independent numerical oracles for the comparison methods."""

from __future__ import annotations

import math
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from marivo.analysis.core.model import Defined, Undefined
from marivo.analysis.methods.comparison import evaluate, output_type
from marivo.analysis.methods.physical import DecimalType, DurationType, ScalarType

I64 = ScalarType("int64")
F64 = ScalarType("float64")


def test_integer_relative_change_retains_widened_intermediate() -> None:
    current, baseline = 2**63 - 1, -(2**63)
    assert evaluate("relative_change", current, baseline, I64, I64) == Defined(
        float(Fraction(current - baseline, abs(baseline)))
    )
    assert evaluate("ratio", current, baseline, I64, I64) == Defined(
        float(Fraction(current, baseline))
    )
    with pytest.raises(OverflowError):
        evaluate("difference", current, baseline, I64, I64)


def test_zero_and_negative_baseline_policies_are_distinct() -> None:
    assert evaluate("relative_change", -5, -10, I64, I64) == Defined(0.5)
    assert evaluate("ratio", -5, -10, I64, I64) == Defined(0.5)
    assert evaluate("relative_change", 2, 0, I64, I64) == Undefined("zero_baseline")
    assert evaluate("ratio", 2, 0, I64, I64) == Undefined("zero_denominator")


@pytest.mark.parametrize("value", [True, 1.0, 2**63, -(2**63) - 1])
def test_integer_operands_are_checked_before_zero_policy(value: object) -> None:
    with pytest.raises((ValueError, OverflowError)):
        evaluate("ratio", value, 0, I64, I64)


def test_decimal_finish_ignores_ambient_context_and_rounds_half_even() -> None:
    physical = DecimalType(38, 6)
    with localcontext() as context:
        context.prec = 3
        assert evaluate(
            "difference",
            Decimal("10000000000000000001.123456"),
            Decimal("0.000001"),
            physical,
            physical,
        ) == Defined(Decimal("10000000000000000001.123455"))
        assert evaluate("ratio", Decimal("0.000001"), Decimal(2), physical, physical) == Defined(
            Decimal("0.000000")
        )
        assert evaluate("ratio", Decimal("0.000003"), Decimal(2), physical, physical) == Defined(
            Decimal("0.000002")
        )


def test_decimal_scale_precision_and_family_boundaries() -> None:
    with pytest.raises(ValueError, match="scale"):
        output_type("difference", DecimalType(18, 2), DecimalType(18, 3))
    with pytest.raises(ValueError, match="scale"):
        evaluate(
            "difference", Decimal("1.001"), Decimal("1"), DecimalType(18, 2), DecimalType(18, 2)
        )
    with pytest.raises(OverflowError):
        evaluate(
            "difference", Decimal(10**38 - 1), Decimal(-1), DecimalType(38, 0), DecimalType(38, 0)
        )
    with pytest.raises(ValueError):
        output_type("ratio", I64, F64)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_duration_ticks_remain_exact(unit: str) -> None:
    assert unit in ("s", "ms", "us", "ns")
    physical = DurationType(unit)
    assert evaluate("difference", 2**53 + 3, 2**53 + 1, physical, physical) == Defined(2)
    assert evaluate("ratio", 1, 3, physical, physical) == Defined(float(Fraction(1, 3)))
    with pytest.raises(ValueError):
        output_type("ratio", physical, DurationType("ms" if unit != "ms" else "us"))


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, True, 1])
def test_nonfinite_and_wrong_float_payloads_reject(value: object) -> None:
    with pytest.raises(ValueError):
        evaluate("difference", value, 1.0, F64, F64)


def test_float_overflow_rejects() -> None:
    with pytest.raises(OverflowError):
        evaluate("difference", 1.7e308, -1.7e308, F64, F64)


def test_difference_state_refuses_old_version() -> None:
    from dataclasses import replace

    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.graph_protocol import MethodState

    state = MethodState(
        "marivo.analysis.method_state/v1",
        "difference",
        "marivo.analysis.state.difference",
        2,
        "cell.difference",
        1,
        "ordered-inputs",
        ("current_endpoint", "baseline_endpoint", "correspondence"),
    )
    with pytest.raises(IntegrityError, match="Re-execute"):
        replace(state, contract_version=1)
    with pytest.raises(IntegrityError):
        replace(state, ordered_part_roles=("current_endpoint", "baseline_endpoint"))


def test_complete_pairing_handles_singleton_double_empty_and_composites() -> None:
    from marivo.analysis.core.rules import pair_coordinates

    assert pair_coordinates((), (), exact=True).keys == frozenset()
    assert pair_coordinates(((),), ((),), exact=True).keys == frozenset({()})
    assert pair_coordinates(((),), (), exact=False).missing[0].key == ()
    keys = (("A", 1), ("A", 2), ("B", 1))
    assert pair_coordinates(keys, tuple(reversed(keys)), exact=True).keys == frozenset(keys)
    union = pair_coordinates((("A", 1),), (("A", 2),), exact=False)
    assert not union.keys
    assert {(item.side, item.key) for item in union.missing} == {
        ("baseline", ("A", 1)),
        ("current", ("A", 2)),
    }


def test_complete_pairing_rejects_duplicates_and_type_coercion() -> None:
    from marivo.analysis.core.model import CoreRuleError
    from marivo.analysis.core.rules import pair_coordinates

    with pytest.raises(CoreRuleError):
        pair_coordinates((("A", 1), ("A", 1)), (("A", 1),), exact=False)
    with pytest.raises(CoreRuleError):
        pair_coordinates(((1,),), (("1",),), exact=False)
    with pytest.raises(CoreRuleError):
        pair_coordinates((("A", 1),), (("A", 2),), exact=True)


def test_float_division_bound_rejects_unstable_nonzero_denominator() -> None:
    from marivo.analysis.methods.comparison import propagated_error

    with pytest.raises(ValueError, match="interval"):
        propagated_error("ratio", 1.0, 1e-14, 1e14, 0.0, 1e-12)
    assert propagated_error("ratio", 1, 0, None, 0.0, 0.0) == 0.0
    with pytest.raises(ValueError, match="finite"):
        propagated_error("difference", 1.0, 2.0, -1.0, math.nan, 0.0)


def test_compressed_comparison_recovery_rejects_invalid_frames() -> None:
    import base64
    import zlib

    from marivo.analysis.materialization.errors import IntegrityError
    from marivo.analysis.materialization.graph_protocol import thaw_graph

    for body in (b"not a node", b"x" * (4 * 1024 * 1024 + 1)):
        token = "comparison-v2:" + base64.b64encode(zlib.compress(body)).decode()
        with pytest.raises(IntegrityError):
            thaw_graph(token)
    with pytest.raises(IntegrityError):
        thaw_graph("comparison-v2:not-valid-base64")


@pytest.mark.parametrize("name", ["cell.ratio", "cell.relative_change"])
def test_duration_division_registration_reports_float_finish(name: str) -> None:
    from dataclasses import replace

    from marivo.analysis.methods.builtin import implementations, specialize_numeric
    from marivo.analysis.methods.semantics import MethodKey

    for implementation in implementations(MethodKey(name)):
        key = replace(implementation.key, input_types=(DurationType("ns"), DurationType("ns")))
        assert specialize_numeric(implementation, key).precision == "finite_float64"


@pytest.mark.parametrize("bound", [-1.0, float("inf"), float("nan"), 0, None])
def test_row_error_state_rejects_invalid_bounds(bound: object) -> None:
    from marivo.analysis.methods.state_validation import state_matches

    assert not state_matches(
        "row_mean",
        {"value": 1.5, "cell_tag": "defined", "cell_reason": None},
        {"row_state__sum": 3.0, "row_state__count": 2, "row_state__error_bound": bound},
    )
