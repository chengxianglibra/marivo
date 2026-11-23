"""Check exact additive and Bennet Attribution laws at the native effect boundary."""

import math
import operator
import os
import random
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Literal, TypeAlias

import ibis
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.binding.attribute_rules import RatioComponentRule
from marivo.analysis.core.attribute import _coefficient_column
from marivo.analysis.methods import attribute_contribution_effects
from marivo.analysis.methods.attribute_contribution_evaluation import ContributionEvaluation
from marivo.analysis.methods.attribute_numeric import _numeric_binary, _Value, _with_roundoff
from marivo.analysis.model.data_types import DataColumn, ScalarDataType
from marivo.analysis.session.core import Session
from tests.analysis.materialization.test_frame_attribute_contribution import session as session
from tests.formal._smt import prove_unsat
from tests.shared_fixtures import catalog_value

_Operation: TypeAlias = Literal["add", "sub", "mul", "div"]
_LinearStep: TypeAlias = tuple[Literal["add", "sub", "mul"], int]
_EXACT = {"add": operator.add, "sub": operator.sub, "mul": operator.mul, "div": operator.truediv}
_FLOAT = DataColumn("operand", "operand", ScalarDataType("float64"), False, "1")
_INT = DataColumn("mass", "mass", ScalarDataType("int64"), False, "USD")


def _value(
    values: Sequence[int | float], *, exact_input: bool = False, integer: bool = False
) -> _Value:
    array = pa.array(values, type=pa.int64() if integer else pa.float64())
    return _Value(
        pd.Series(pd.arrays.ArrowExtensionArray(array)),
        _INT if integer else _FLOAT,
        pd.Series([0.0] * len(values), dtype=float) if exact_input else None,
    )


def _scalar(value: _Value, index: int = 0) -> Fraction:
    cell = value.values.iloc[index]
    assert isinstance(cell, (int, float))
    return Fraction(cell)


def _within_budget(value: _Value, expected: Fraction, *, index: int = 0) -> None:
    assert value.roundoff is not None
    actual = _scalar(value, index)
    error = Fraction(float(value.roundoff.iloc[index]))
    assert abs(actual - expected) <= error, (actual, expected, error)


def test_operation_roundoff_covers_the_halfway_addition() -> None:
    x, y = 1.0, 2.0**-53
    value = _numeric_binary("add", _value((x,), exact_input=True), _value((y,), exact_input=True))
    # One exact halfway case gives a positive observed error and makes a
    # weakened output-rounding term observable without upstream padding.
    assert _scalar(value) == 1
    expected = Fraction(x) + Fraction(y)
    assert abs(_scalar(value) - expected) == Fraction(1, 2**53)
    _within_budget(value, expected)


@pytest.mark.parametrize(
    "operation,x,y",
    [
        ("sub", math.nextafter(1.0, math.inf), 1.0),
        ("add", 2.0**53, 1.0),
        ("add", 2.0**900, -(2.0**840)),
        ("mul", math.nextafter(1.0, math.inf), math.nextafter(1.0, math.inf)),
        ("mul", 3 * math.ulp(0.0), 0.5),
        ("div", 7 * math.ulp(0.0), 5 * math.ulp(0.0)),
        ("div", 2.0**-970, 3.0 * 2.0**-1000),
        ("div", -3.0, 7.0),
    ],
)
def test_operation_roundoff_on_ieee_boundary_inputs(
    operation: _Operation, x: float, y: float
) -> None:
    expected = _EXACT[operation](Fraction(x), Fraction(y))
    output = _numeric_binary(
        operation, _value((x,), exact_input=True), _value((y,), exact_input=True)
    )
    _within_budget(output, expected)
    seeded_x = _with_roundoff(_value((x,)))
    seeded_y = _with_roundoff(_value((y,)))
    assert seeded_x.roundoff is not None and seeded_y.roundoff is not None
    assert seeded_x.roundoff.tolist() == [math.ulp(x)]
    assert seeded_y.roundoff.tolist() == [math.ulp(y)]
    output = _numeric_binary(operation, seeded_x, seeded_y)
    _within_budget(output, expected)


def test_propagated_roundoff_survives_cancellation_and_division_by_a_fixed_small_scalar() -> None:
    large, tiny = _with_roundoff(_value((2.0**53,))), _with_roundoff(_value((1.0,)))
    combined = _numeric_binary("add", large, tiny)
    cancelled = _numeric_binary("sub", combined, large)
    assert _scalar(cancelled) == 0
    _within_budget(cancelled, Fraction(1))
    scale = math.nextafter(1.0, math.inf)
    scaled = _numeric_binary("mul", cancelled, _value((scale,)))
    _within_budget(scaled, Fraction(scale))
    divisor = 3.0 * 2.0**-900
    quotient = _numeric_binary("div", scaled, _value((divisor,)))
    _within_budget(quotient, Fraction(scale) / Fraction(divisor))


def test_exact_bennet_identity_for_arbitrary_weights_and_rates() -> None:
    query = """(set-logic QF_NRA)
(declare-const w0 Real)
(declare-const w1 Real)
(declare-const r0 Real)
(declare-const r1 Real)
(define-fun within () Real (* (/ (+ w0 w1) 2.0) (- r1 r0)))
(define-fun mix () Real (* (/ (+ r0 r1) 2.0) (- w1 w0)))
(assert (distinct (+ within mix) (- (* w1 r1) (* w0 r0))))
"""
    prove_unsat(
        query,
        claim="Bennet's exact bilinear decomposition",
        witness=("w0", "w1", "r0", "r1", "within", "mix"),
    )
    print("Bennet decomposition: unsat")


def _multilevel_mass(
    columns: tuple[_Value, _Value, _Value], steps: Sequence[_LinearStep]
) -> _Value:
    mass = columns[0]
    for operation, argument in steps:
        operand = (
            _Value(
                pd.Series([argument] * len(mass.values), dtype=object),
                _coefficient_column(argument),
            )
            if operation == "mul"
            else columns[argument]
        )
        mass = _numeric_binary(operation, mass, operand)
    return mass


def _linear_coefficients(steps: Sequence[_LinearStep]) -> list[int]:
    terms = [1, 0, 0]
    for operation, argument in steps:
        if operation == "mul":
            terms = [argument * coefficient for coefficient in terms]
        else:
            terms[argument] += 1 if operation == "add" else -1
    return terms


def _scoped(
    endpoint: list[tuple[int, ...]], groups: list[int], size: int
) -> tuple[_Value, _Value, _Value]:
    arrays = tuple(
        tuple(
            sum(row[col] for row, index in zip(endpoint, groups, strict=True) if index == k)
            for k in range(size)
        )
        for col in range(3)
    )
    x, y, z = arrays
    return _value(x, integer=True), _value(y, integer=True), _value(z, integer=True)


def test_additive_multilevel_expressions_close_over_random_disjoint_partitions() -> None:
    rng = random.Random(9310)
    choices: tuple[_LinearStep, ...] = (
        ("add", 0),
        ("add", 1),
        ("add", 2),
        ("sub", 0),
        ("sub", 1),
        ("sub", 2),
        ("mul", -3),
        ("mul", -1),
        ("mul", 0),
        ("mul", 2),
        ("mul", 4),
    )
    count = 120 if os.environ.get("MARIVO_FORMAL_PROFILE") == "deep" else 20
    for _ in range(count):
        size = rng.randint(2, 8)
        steps = rng.choices(choices, k=rng.randint(3, 8))
        coefficients = _linear_coefficients(steps)
        rows = [
            tuple(rng.randint(-64, 64) for _ in range(3))
            for _ in range(rng.randint(size, size * 3))
        ]
        group = [i % size for i in rng.sample(range(len(rows)), len(rows))]
        baseline = [tuple(rng.randint(-64, 64) for _ in range(3)) for _ in rows]

        fine = _numeric_binary(
            "sub",
            _multilevel_mass(_scoped(rows, group, size), steps),
            _multilevel_mass(_scoped(baseline, group, size), steps),
        )
        parent_group = [0] * len(group)
        parent = _numeric_binary(
            "sub",
            _multilevel_mass(_scoped(rows, parent_group, 1), steps),
            _multilevel_mass(_scoped(baseline, parent_group, 1), steps),
        )
        expected = [
            Fraction(
                sum(
                    coefficient * (current - previous)
                    for coefficient, current, previous in zip(coefficients, a, b, strict=True)
                )
            )
            for a, b in zip(rows, baseline, strict=True)
        ]
        assert [_scalar(fine, index) for index in range(size)] == [
            sum(delta for delta, k in zip(expected, group, strict=True) if k == index)
            for index in range(size)
        ]
        assert (
            sum(_scalar(fine, index) for index in range(size)) == _scalar(parent) == sum(expected)
        )


_GROUPS = {
    "A": ((-6, 2), (15, 5)),
    "B": ((24, 6), (5, 1)),
    "C": ((0, 0), (10, 2)),
    "D": ((8, 2), (0, 0)),
    "E": ((0, 0), (0, 0)),
}


def _seed_ratios(path: Path, sign: int) -> None:
    source = ibis.duckdb.connect(str(path))
    try:
        source.raw_sql("DELETE FROM facts")
        rows = [
            (i * 2 + side + 1, phase, "east", group, sign * numerator, denominator)
            for i, (group, endpoints) in enumerate(_GROUPS.items())
            for side, (phase, (numerator, denominator)) in enumerate(
                zip(("base", "current"), endpoints, strict=True)
            )
        ]
        inserted = ", ".join(str(row) for row in rows)
        source.raw_sql(f"INSERT INTO facts VALUES {inserted}")
    finally:
        source.disconnect()


def _ratio_oracle(sign: int) -> tuple[Fraction, Fraction, dict[str, dict[str, Fraction]]]:
    baseline = sum(d0 for (_, d0), _ in _GROUPS.values())
    current = sum(d1 for _, (_, d1) in _GROUPS.values())
    r0 = Fraction(sign * sum(n0 for (n0, _), _ in _GROUPS.values()), baseline)
    r1 = Fraction(sign * sum(n1 for _, (n1, _) in _GROUPS.values()), current)
    expected: dict[str, dict[str, Fraction]] = {}
    for key, ((n0, d0), (n1, d1)) in _GROUPS.items():
        net = sign * (Fraction(n1, current) - Fraction(n0, baseline))
        if d0 and d1:
            w0, w1 = Fraction(d0, baseline), Fraction(d1, current)
            rate0, rate1 = sign * Fraction(n0, d0), sign * Fraction(n1, d1)
            within = (w0 + w1) * (rate1 - rate0) / 2
            mix = (rate0 + rate1) * (w1 - w0) / 2
            entry = Fraction(0)
        else:
            within = mix = Fraction(0)
            entry = net
        assert within + mix + entry == net
        expected[key] = {"within": within, "mix": mix, "entry_exit": entry, "contribution": net}
    assert sum(group["contribution"] for group in expected.values()) == r1 - r0
    return r0, r1 - r0, expected


def _assert_report_value(observed: object, expected: Fraction) -> None:
    assert isinstance(observed, (int, float))
    # The small report values have fewer than ten distinct kernel stages
    # on these fixtures. Use a fixed ULP-derived tolerance at unit scale.
    distance = abs(Fraction(observed) - expected)
    assert distance <= 16 * Fraction(math.ulp(max(1.0, abs(float(expected))))), (observed, expected)


@pytest.mark.parametrize("sign", [1, -1], ids=["positive-baseline", "negative-baseline"])
def test_production_ratio_effects_match_independent_fraction_oracle(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sign: int
) -> None:
    _seed_ratios(tmp_path / "contribution.duckdb", sign)
    observed: list[tuple[tuple[str, ...], dict[str, _Value]]] = []
    original = attribute_contribution_effects.ratio_effects

    def observe_effects(
        evaluation: ContributionEvaluation,
        rule: RatioComponentRule,
        other: RatioComponentRule,
        path: tuple[int, ...],
    ) -> dict[str, _Value]:
        result = original(evaluation, rule, other, path)
        (group,) = evaluation.inputs.fine_columns
        keys = tuple(evaluation.prepared.table[group.slot_id].tolist())
        assert all(isinstance(key, str) for key in keys)
        observed.append((keys, result))
        return result

    monkeypatch.setattr(attribute_contribution_effects, "ratio_effects", observe_effects)
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
    phase = catalog_value(catalog, "sales.facts.phase", kind="dimension")
    channel = catalog_value(catalog, "sales.facts.channel", kind="dimension")
    ratio = amount.sum() / weight.sum()
    current = session.observe({"q": ratio}, where=phase == "current")
    baseline = session.observe({"q": ratio}, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    targets = compared.select(
        {"absolute": compared["base.absolute.q"], "relative": compared["base.relative.q"]}
    )
    rows = targets.attribute(by=(channel,)).compute().to_pandas()
    r0, change, expected = _ratio_oracle(sign)
    assert observed
    for keys, effects in observed:
        assert set(keys) == set(expected)
        for index, key in enumerate(keys):
            for role, values in effects.items():
                _within_budget(values, expected[key][role], index=index)
    report_keys = rows["channel"].tolist()
    assert set(report_keys) == set(expected)
    for index, key in enumerate(report_keys):
        for role, exact in expected[key].items():
            column = "contribution" if role == "contribution" else f"effect.{role}"
            _assert_report_value(rows[f"{column}.absolute"].iloc[index], exact)
            _assert_report_value(rows[f"{column}.relative"].iloc[index], exact / abs(r0))
        _assert_report_value(rows["total.absolute"].iloc[index], change)
        _assert_report_value(rows["total.relative"].iloc[index], change / abs(r0))


def test_public_additive_formula_keeps_the_fresh_parent_and_group_changes(
    session: Session, tmp_path: Path
) -> None:
    _seed_ratios(tmp_path / "contribution.duckdb", 1)
    catalog = session.catalog
    amount = catalog_value(catalog, "sales.facts.amount", kind="measure")
    weight = catalog_value(catalog, "sales.facts.weight", kind="measure")
    phase = catalog_value(catalog, "sales.facts.phase", kind="dimension")
    channel = catalog_value(catalog, "sales.facts.channel", kind="dimension")
    x = amount.sum()
    y = ms.rescale(weight.sum(), divisor=2, unit="USD")
    z = 2 * x
    formula = 3 * (x - y) - (3 * y + z)
    current = session.observe({"q": formula}, where=phase == "current")
    baseline = session.observe({"q": formula}, where=phase == "base")
    compared = current.compare({"base": mv.baseline(baseline)})
    selected = compared.select({"q": compared["base.absolute.q"]})
    rows = selected.attribute(by=(channel,)).compute().to_pandas()
    expected = {key: n1 - n0 - 3 * (d1 - d0) for key, ((n0, d0), (n1, d1)) in _GROUPS.items()}
    assert dict(zip(rows["channel"], rows["contribution.q"], strict=True)) == expected
    assert rows["total.q"].tolist() == [sum(expected.values())] * len(expected)
