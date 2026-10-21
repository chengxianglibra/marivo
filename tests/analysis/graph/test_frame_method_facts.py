"""Closed inert method facts, exact physical scalars and original-key associations."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    FrozenCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DataColumn,
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodMetric,
    FrameMethodParent,
    FrameMethodSupport,
    FrameMethodValue,
    FrameRatioMethod,
)
from marivo.analysis.core.frozen_calendar import calendar_directory_schema, paired_mapping_schema
from marivo.analysis.core.model import CoreRuleError

_INT64 = ScalarDataType("int64")


def column(
    name: str, dtype: DataType = _INT64, *, nullable: bool = True, unit: str | None = "USD"
) -> DataColumn:
    return DataColumn(name, name, dtype, nullable, unit)


def support(root: str = "r") -> FrameMethodSupport:
    return FrameMethodSupport(root, "rows", 3, 4, 2, 3)


def additive(
    name: str = "sales", *, unit: str | None = "USD", change: int = 4
) -> FrameAdditiveMethod:
    return FrameAdditiveMethod(
        "additive",
        FrameMethodValue(column(f"left.{name}", unit=unit), 10),
        FrameMethodValue(column(f"right.{name}", unit=unit), 6),
        FrameMethodValue(column(f"absolute.{name}", unit=unit), change),
        (support(),),
    )


def ratio() -> FrameRatioMethod:
    def value(role: str, number: float, unit: str) -> FrameMethodValue:
        return FrameMethodValue(
            column(f"{role}.rate", ScalarDataType("float64"), unit=unit), number
        )

    return FrameRatioMethod(
        "ratio_component",
        value("left", 0.5, "1"),
        value("right", 0.25, "1"),
        value("absolute", 0.25, "1"),
        value("numerator_left", 10.0, "USD"),
        value("numerator_right", 5.0, "USD"),
        value("denominator_left", 20.0, "USD"),
        value("denominator_right", 20.0, "USD"),
        (support(),),
    )


def schema(
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod,
    axes: tuple[DataAxis | CalendarDataAxis | PairedDataAxis, ...] = (),
) -> DataSchema:
    name = method.left.column.name.split(".", 1)[1]
    values = [
        method.left.column,
        method.right.column,
        method.change.column,
        column(f"contribution.{name}", method.change.column.dtype, unit=method.change.column.unit),
        column(
            f"contribution_share.{name}",
            ScalarDataType("float64"),
            unit="1" if method.change.column.unit else None,
        ),
    ]
    if isinstance(method, FrameRatioMethod):
        values.extend(
            (
                method.numerator_left.column,
                method.numerator_right.column,
                method.denominator_left.column,
                method.denominator_right.column,
            )
        )
    presence = tuple(
        column(side, ScalarDataType("boolean"), nullable=False, unit=None)
        for side in ("left_present", "right_present")
    )
    return DataSchema(
        axes,
        tuple(values),
        presence,
        value_paths=tuple(tuple(c.name.split(".")) for c in values),
        presence_paths=(("left_present",), ("right_present",)),
    )


def metric(
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | None = None,
) -> FrameMethodMetric:
    rule = method or additive()
    return FrameMethodMetric(
        rule.left.column.name.split(".", 1)[1],
        rule,
        "zero_total_change" if rule.change.value == 0 else "defined",
    )


def facts() -> FrameMethodFacts:
    rule = additive()
    return FrameMethodFacts(
        "attribute", "result-1", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (ScalarDataType("boolean"), True),
        (ScalarDataType("string"), "x"),
        (ScalarDataType("int64"), 2**63 - 1),
        (ScalarDataType("int64"), -(2**63)),
        (ScalarDataType("float64"), 1.5),
        (ScalarDataType("date"), date(2026, 10, 20)),
        (DecimalDataType(38, 18), Decimal("12345678901234567890.123456789012345678")),
        (TimestampDataType("ns", "UTC"), 1760000000000000001),
        (TimestampDataType("ns", None), -(2**63)),
        (DurationDataType("ns"), 1),
    ],
)
def test_exact_scalars_are_preserved(
    dtype: DataType, value: bool | int | float | str | date | Decimal
) -> None:
    record = FrameMethodValue(column("value", dtype, unit=None), value)
    assert record.value is value
    assert FrameMethodValue(record.column, None).value is None


@pytest.mark.parametrize(
    ("dtype", "value"),
    [
        (ScalarDataType("int64"), True),
        (ScalarDataType("float64"), 1),
        (ScalarDataType("boolean"), 1),
        (ScalarDataType("string"), b"x"),
        (ScalarDataType("date"), datetime(2026, 10, 20)),
        (ScalarDataType("int64"), 2**63),
        (ScalarDataType("int64"), -(2**63) - 1),
        (ScalarDataType("float64"), float("nan")),
        (ScalarDataType("float64"), float("inf")),
        (DecimalDataType(4, 2), Decimal("100.00")),
        (DecimalDataType(4, 2), Decimal("1.001")),
        (DecimalDataType(4, 2), Decimal("NaN")),
        (DecimalDataType(4, 2), 1.0),
        (TimestampDataType("ns", "UTC"), datetime.now(timezone.utc)),
        (DurationDataType("ns"), timedelta(microseconds=1)),
        (DurationDataType("ns"), True),
    ],
)
def test_wrong_scalar_carriers_are_rejected(dtype: DataType, value: object) -> None:
    with pytest.raises(CoreRuleError, match="exact finite scalar"):
        FrameMethodValue(column("value", dtype), value)  # Test malformed runtime input.


def test_null_decimal_and_ticks_boundaries() -> None:
    with pytest.raises(CoreRuleError, match="nonnull"):
        FrameMethodValue(column("x", nullable=False), None)
    value = Decimal("1.2300")
    assert FrameMethodValue(column("x", DecimalDataType(4, 2)), value).value is value
    for dtype in (TimestampDataType("s", None), DurationDataType("ms")):
        with pytest.raises(CoreRuleError):
            FrameMethodValue(column("x", dtype), 2**63)


def test_records_are_frozen_slotted_and_have_exact_fields() -> None:
    value = facts()
    assert tuple(field.name for field in fields(value)) == (
        "kind",
        "origin_result_id",
        "output_schema",
        "coordinates",
        "parent_axis_count",
        "parents",
    )
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        value.origin_result_id = "other"
    with pytest.raises(TypeError):
        FrameMethodFacts("attribute", "r", value.output_schema, (), 0, (), extra=True)


@pytest.mark.parametrize("counts", [(True, 1, 0, 0), (-1, 2, 0, 0), (1, 2, 2, 1), (1, 2, 1, 3)])
def test_support_counts_are_exact_and_bounded_by_input(counts: tuple[int, int, int, int]) -> None:
    with pytest.raises(CoreRuleError, match="nonnegative exact counts"):
        FrameMethodSupport("r", "rows", *counts)


def test_closed_support_policy_and_root_uniqueness() -> None:
    with pytest.raises(CoreRuleError):
        replace(support(), policy="unknown")
    with pytest.raises(CoreRuleError):
        replace(support(), root_id="")
    for supports in ((), (support(), support()), [support()]):
        with pytest.raises(CoreRuleError):
            replace(additive(), support=supports)


def test_method_types_units_nonnull_totals_and_zero_denominator() -> None:
    rule = additive()
    for changes in (
        {"kind": "ratio_component"},
        {"left": None},
        {"change": replace(rule.change, value=None)},
        {"right": replace(rule.right, column=replace(rule.right.column, unit="kg"))},
    ):
        with pytest.raises(CoreRuleError):
            replace(rule, **changes)
    ratio_rule = ratio()
    with pytest.raises(CoreRuleError, match="nonzero"):
        replace(ratio_rule, denominator_left=replace(ratio_rule.denominator_left, value=0.0))
    with pytest.raises(CoreRuleError, match="component units"):
        replace(
            ratio_rule,
            numerator_left=replace(
                ratio_rule.numerator_left,
                column=replace(ratio_rule.numerator_left.column, unit="kg"),
            ),
            numerator_right=replace(
                ratio_rule.numerator_right,
                column=replace(ratio_rule.numerator_right.column, unit="kg"),
            ),
        )
    with pytest.raises(CoreRuleError, match="matching exact Duration"):
        replace(rule, left=FrameMethodValue(column("left.sales", DurationDataType("ns")), 1))


@pytest.mark.parametrize(
    "coefficient", [True, float("nan"), float("inf"), Decimal("Infinity"), "1"]
)
def test_linear_coefficients_are_closed_finite_original_scalars(coefficient: object) -> None:
    with pytest.raises(CoreRuleError, match="finite non-bool"):
        FrameLinearTerm(coefficient, additive())


def test_nested_linear_terms_preserve_order_and_explanatory_values() -> None:
    leaf = additive()
    terms = (FrameLinearTerm(-1, leaf), FrameLinearTerm(Decimal("2.000"), leaf))
    rule = FrameLinearMethod("linear", leaf.left, leaf.right, leaf.change, terms)
    outer = FrameLinearMethod(
        "linear", leaf.left, leaf.right, leaf.change, (FrameLinearTerm(0.5, rule),)
    )
    assert outer.terms[0].method.terms == terms
    assert outer.terms[0].method.terms[1].coefficient.as_tuple().exponent == -3
    assert (
        FrameMethodFacts(
            "attribute", "r", schema(outer), (), 0, (FrameMethodParent((), (metric(outer),)),)
        )
        .parents[0]
        .metrics[0]
        .method
        is outer
    )
    for invalid in ((), [terms[0]], (leaf,)):
        with pytest.raises(CoreRuleError):
            replace(rule, terms=invalid)
    with pytest.raises(CoreRuleError):
        FrameLinearTerm(1, "recipe")


def test_share_state_uses_exact_change_and_support_roots_are_consistent() -> None:
    with pytest.raises(CoreRuleError, match="share_state"):
        replace(metric(), share_state="zero_total_change")
    assert metric(additive(change=0)).share_state == "zero_total_change"
    rule = additive()
    changed = replace(rule, support=(replace(support(), left_effective_count=1),))
    linear = FrameLinearMethod(
        "linear",
        rule.left,
        rule.right,
        rule.change,
        (FrameLinearTerm(1, rule), FrameLinearTerm(-1, changed)),
    )
    with pytest.raises(CoreRuleError, match="shared local sample root"):
        FrameMethodParent((), (metric(linear),))


def test_origin_parent_count_and_exact_output_schema() -> None:
    original = facts()
    for changes in (
        {"kind": "other"},
        {"origin_result_id": ""},
        {"origin_result_id": "a\nb"},
        {"parent_axis_count": True},
        {"parent_axis_count": -1},
        {"parent_axis_count": 1},
        {"parents": list(original.parents)},
        {"coordinates": []},
        {"output_schema": None},
    ):
        with pytest.raises(CoreRuleError):
            replace(original, **changes)
    assert replace(original, parents=()).parents == ()
    with pytest.raises(CoreRuleError, match="unique complete parent"):
        replace(original, parents=original.parents * 2)
    with pytest.raises(CoreRuleError, match="exact original output columns"):
        replace(
            original,
            parents=(
                FrameMethodParent(
                    (),
                    (
                        metric(
                            replace(
                                additive(),
                                left=replace(
                                    additive().left,
                                    column=replace(additive().left.column, slot_id="wrong"),
                                ),
                            )
                        ),
                    ),
                ),
            ),
        )


def test_complete_composite_parent_keys_canonical_order_and_nulls() -> None:
    rule = additive()
    columns = (column("tenant", unit=None), column("key", ScalarDataType("string"), unit=None))
    output = schema(
        rule, (DataAxis("parent", columns), DataAxis("group", (column("group", unit=None),)))
    )

    def parent(tenant: int | None, key: str) -> FrameMethodParent:
        return FrameMethodParent(
            (FrameMethodValue(columns[0], tenant), FrameMethodValue(columns[1], key)),
            (metric(rule),),
        )

    parents = (parent(1, "a"), parent(1, "b"), parent(None, "a"))
    result = FrameMethodFacts("attribute", "r", output, (), 1, parents)
    assert result.parents == parents
    for invalid in (
        parents[::-1],
        parents + parents[:1],
        (replace(parents[0], key=parents[0].key[:1]),),
        (replace(parents[0], key=parents[0].key[::-1]),),
    ):
        with pytest.raises(CoreRuleError):
            replace(result, parents=invalid)


def test_metric_directory_and_ratio_component_association() -> None:
    original = facts()
    parent = original.parents[0]
    with pytest.raises(CoreRuleError, match="all original metrics"):
        replace(original, parents=(replace(parent, metrics=(replace(metric(), name="other"),)),))
    with pytest.raises(CoreRuleError):
        replace(parent, metrics=(metric(), metric()))
    rule = ratio()
    ratio_facts = FrameMethodFacts(
        "attribute", "ratio-r", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    assert ratio_facts.parents[0].metrics[0].method == rule
    changed = replace(
        rule,
        numerator_left=replace(
            rule.numerator_left, column=replace(rule.numerator_left.column, slot_id="bad")
        ),
    )
    with pytest.raises(CoreRuleError, match="exact original ratio component"):
        replace(ratio_facts, parents=(FrameMethodParent((), (metric(changed),)),))
    missing = replace(
        original.output_schema,
        values=original.output_schema.values[:-1],
        value_paths=original.output_schema.value_paths[:-1],
    )
    with pytest.raises(CoreRuleError, match="complete ordered contribution"):
        replace(original, output_schema=missing)
    wrong_unit = replace(
        original.output_schema,
        values=(
            *original.output_schema.values[:-1],
            replace(original.output_schema.values[-1], unit="USD"),
        ),
    )
    with pytest.raises(CoreRuleError, match="dimensionless"):
        replace(original, output_schema=wrong_unit)


def calendar(token: str = "period") -> CalendarDataAxis:
    return CalendarDataAxis(
        token,
        (column(f"key.{token}", unit=None, nullable=False),),
        "UTC",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), f"calendar.{token}"),
    )


def coordinate(axis: CalendarDataAxis, key: int = 1) -> FrameMethodCoordinate:
    start = int(datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()) * 1_000_000
    end = start + 86_400_000_000
    return FrameMethodCoordinate(
        f"calendar.{axis.token}",
        calendar_directory_schema(axis),
        (
            (
                key,
                "2026-10-20",
                0,
                datetime(2026, 10, 20).toordinal() - 1,
                start,
                end,
                start,
                end,
                "2026-10-20T00:00:00.000000+00:00",
                "2026-10-21T00:00:00.000000+00:00",
                "2026-10-20T00:00:00.000000+00:00",
                "2026-10-21T00:00:00.000000+00:00",
            ),
        ),
    )


def test_coordinate_exact_scalars_width_and_calendar_parent_domain() -> None:
    axis = calendar()
    snap = coordinate(axis)
    rule = additive()
    parent = FrameMethodParent((FrameMethodValue(axis.columns[0], 1),), (metric(rule),))
    result = FrameMethodFacts(
        "attribute", "calendar-r", schema(rule, (axis,)), (snap,), 1, (parent,)
    )
    assert result.coordinates == (snap,)
    for changes in (
        {"coordinates": ()},
        {"coordinates": (snap, snap)},
        {"coordinates": (replace(snap, name="wrong"),)},
        {"parents": (replace(parent, key=(FrameMethodValue(axis.columns[0], 2),)),)},
    ):
        with pytest.raises(CoreRuleError):
            replace(result, **changes)
    with pytest.raises(CoreRuleError):
        replace(snap, rows=((1,),))
    with pytest.raises(CoreRuleError):
        replace(snap, rows=((True, *snap.rows[0][1:]),))
    with pytest.raises(CoreRuleError):
        replace(snap, rows=[snap.rows[0]])
    malformed = replace(snap, rows=((*snap.rows[0][:2], 1, *snap.rows[0][3:]),))
    with pytest.raises(CoreRuleError, match="ordinal sequence"):
        replace(result, coordinates=(malformed,))


def test_paired_snapshot_closure_and_canonical_names() -> None:
    left, right = calendar("left"), calendar("right")
    axis = PairedDataAxis(
        "pair", (column("pair", unit=None, nullable=False),), left, right, "pair.mapping"
    )
    mapping = FrameMethodCoordinate("pair.mapping", paired_mapping_schema(axis), ((3, 1, 1),))
    coordinates = tuple(
        sorted((coordinate(left), coordinate(right), mapping), key=lambda c: c.name)
    )
    rule = additive()
    parent = FrameMethodParent((FrameMethodValue(axis.columns[0], 3),), (metric(rule),))
    result = FrameMethodFacts(
        "attribute", "paired-r", schema(rule, (axis,)), coordinates, 1, (parent,)
    )
    with pytest.raises(CoreRuleError):
        replace(result, coordinates=coordinates[::-1])
    bad_mapping = replace(mapping, rows=((3, 999, 1),))
    with pytest.raises(CoreRuleError, match="resolving in their frozen directory"):
        replace(
            result,
            coordinates=tuple(
                sorted((coordinate(left), coordinate(right), bad_mapping), key=lambda c: c.name)
            ),
        )
    with pytest.raises(CoreRuleError, match="not both absent"):
        replace(result, coordinates=(*coordinates[:-1], replace(mapping, rows=((3, None, None),))))


@pytest.mark.parametrize("n", [True, -1, 1.0, "1"])
def test_show_count_matches_frame_exact_integer_rule(n: object) -> None:
    with pytest.raises(CoreRuleError, match="nonnegative integer preview"):
        facts().show(n=n)


def test_all_records_have_bounded_repr_and_metadata_only_show(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rule = additive()
    linear = FrameLinearMethod(
        "linear", rule.left, rule.right, rule.change, (FrameLinearTerm(-(10**5000), rule),)
    )
    records = (
        rule.left,
        support(),
        rule,
        ratio(),
        linear.terms[0],
        linear,
        metric(),
        facts().parents[0],
        facts(),
        FrameMethodCoordinate(
            "huge",
            DataSchema(
                (),
                (column("message", ScalarDataType("string"), unit=None),),
                value_paths=(("message",),),
            ),
            (("secret-row-" + "x" * 10000,),) * 1000,
        ),
    )
    for record in records:
        text = repr(record)
        assert "\n" not in text and ".show()" in text and len(text) < 180
        record.show(n=0)
        output = capsys.readouterr().out
        assert "Fields:" in output and "secret-row" not in output
        assert len(output.splitlines()) == 2
        record.show(n=2)
        output = capsys.readouterr().out
        assert len(output.splitlines()) <= 4
        assert max(len(line) for line in output.splitlines()) <= 240


def test_large_exact_counts_and_parent_identity_are_bounded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    count = 10**5000
    record = FrameMethodSupport("r", "rows", count, count, count, count)
    record.show(n=1)
    assert "int(bits=" in capsys.readouterr().out
    with pytest.raises(CoreRuleError):
        replace(record, left_effective_count=count + 1)
    axis = DataAxis("parent", (column("tenant", unit=None),))
    parent = FrameMethodParent((FrameMethodValue(axis.columns[0], 123),), (metric(),))
    assert "123" in repr(parent)
    parent.show(n=0)
    assert "123" not in capsys.readouterr().out


def test_opaque_metric_unit_keeps_dimensionless_share_policy() -> None:
    rule = additive(unit="USD(net)")
    result = FrameMethodFacts(
        "attribute", "opaque-r", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    assert result.output_schema.values[-1].unit == "1"


def test_invalid_component_unit_uses_structured_error() -> None:
    rule = ratio()
    invalid = replace(rule.numerator_left.column, unit="invalid unit")
    invalid_right = replace(rule.numerator_right.column, unit="invalid unit")
    with pytest.raises(CoreRuleError, match="governed ratio component units"):
        replace(
            rule,
            numerator_left=replace(rule.numerator_left, column=invalid),
            numerator_right=replace(rule.numerator_right, column=invalid_right),
        )


def test_saved_numeric_consistency_reuses_original_order_and_exact_carriers() -> None:
    from marivo.analysis.methods.frame_method_facts import validate_method_facts

    def wrapped(
        rule: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod,
    ) -> FrameMethodFacts:
        return FrameMethodFacts(
            "attribute", "numeric", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
        )

    original = additive()
    validate_method_facts(wrapped(original))
    with pytest.raises(CoreRuleError, match="consistent"):
        validate_method_facts(wrapped(replace(original, change=replace(original.change, value=5))))
    large = replace(
        original,
        left=replace(original.left, value=2**60 + 5),
        right=replace(original.right, value=2**60),
        change=replace(original.change, value=5),
    )
    validate_method_facts(wrapped(large))
    twice = FrameLinearMethod(
        "linear",
        replace(original.left, value=20),
        replace(original.right, value=12),
        replace(original.change, value=8),
        (FrameLinearTerm(2, original),),
    )
    difference = FrameLinearMethod(
        "linear",
        original.left,
        original.right,
        original.change,
        (FrameLinearTerm(1, twice), FrameLinearTerm(-1, original)),
    )
    validate_method_facts(wrapped(difference))
    validate_method_facts(wrapped(ratio()))
    broken = ratio()
    with pytest.raises(CoreRuleError, match="consistent"):
        validate_method_facts(
            wrapped(replace(broken, numerator_left=replace(broken.numerator_left, value=11.0)))
        )


@pytest.mark.parametrize("duration", [False, True])
def test_saved_ratio_finishes_decimal_half_up_and_duration_half_even(duration: bool) -> None:
    from marivo.analysis.methods.frame_method_facts import validate_method_facts

    dtype = DurationDataType("ns") if duration else DecimalDataType(12, 2)
    left_value = 2 if duration else Decimal("0.13")
    right_value = 2 if duration else Decimal("0.25")
    numerator_left = 5 if duration else Decimal("1.00")
    numerator_right = 3 if duration else Decimal("2.00")
    denominator = 2 if duration else 8
    change = left_value - right_value
    rule = FrameRatioMethod(
        "ratio_component",
        FrameMethodValue(column("left.rate", dtype), left_value),
        FrameMethodValue(column("right.rate", dtype), right_value),
        FrameMethodValue(
            column("absolute.rate", dtype if duration else DecimalDataType(13, 2)), change
        ),
        FrameMethodValue(column("numerator_left.rate", dtype), numerator_left),
        FrameMethodValue(column("numerator_right.rate", dtype), numerator_right),
        FrameMethodValue(column("denominator_left.rate", unit="1"), denominator),
        FrameMethodValue(column("denominator_right.rate", unit="1"), denominator),
        (support(),),
    )
    result = FrameMethodFacts(
        "attribute", "rounded", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    validate_method_facts(result)


def test_same_origin_payload_comparison_preserves_scalar_representation() -> None:
    from marivo.analysis.core.frame_method_facts import same_method_facts

    rule = ratio()
    first = FrameLinearMethod(
        "linear", rule.left, rule.right, rule.change, (FrameLinearTerm(1, rule),)
    )
    second = replace(first, terms=(FrameLinearTerm(1.0, rule),))

    def wrapped(method: FrameLinearMethod) -> FrameMethodFacts:
        return FrameMethodFacts(
            "attribute", "r", schema(method), (), 0, (FrameMethodParent((), (metric(method),)),)
        )

    assert first == second  # Native numeric equality alone erases the original coefficient type.
    assert not same_method_facts(wrapped(first), wrapped(second))


def test_pure_decimal_scale_uses_captured_actual_carrier_and_preserves_direct_scalar() -> None:
    from marivo.analysis.methods.frame_method_facts import validate_method_facts

    dtype = DecimalDataType(38, 9)
    leaf = FrameAdditiveMethod(
        "additive",
        FrameMethodValue(column("left.sales", dtype), Decimal("20.000000000")),
        FrameMethodValue(column("right.sales", dtype), Decimal("12.000000000")),
        FrameMethodValue(column("absolute.sales", DecimalDataType(38, 8)), Decimal("8.00000000")),
        (support(),),
    )
    scalar = Decimal("0.500")
    rule = FrameLinearMethod(
        "linear",
        replace(leaf.left, value=Decimal("10.000000000")),
        replace(leaf.right, value=Decimal("6.000000000")),
        replace(leaf.change, value=Decimal("4.000000000")),
        (FrameLinearTerm(scalar, leaf),),
    )
    facts = FrameMethodFacts(
        "attribute", "native", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    validate_method_facts(facts)
    assert rule.terms[0].coefficient.as_tuple() == scalar.as_tuple()
    bad = replace(
        rule,
        left=replace(rule.left, value=Decimal("11.000000000")),
        change=replace(rule.change, value=Decimal("5.000000000")),
    )
    with pytest.raises(CoreRuleError, match="consistent"):
        validate_method_facts(replace(facts, parents=(FrameMethodParent((), (metric(bad),)),)))
