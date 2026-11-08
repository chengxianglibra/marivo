"""Closed inert method facts, exact physical scalars and original-key associations."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

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
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodSupport,
    FrameMethodTarget,
    FrameMethodValue,
    FrameRatioMethod,
)
from marivo.analysis.core.frozen_calendar import calendar_directory_schema

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


def effect_names(
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | FrameDivisionMethod,
) -> tuple[Literal["within", "mix", "entry_exit", "additive"], ...]:
    if isinstance(method, FrameAdditiveMethod):
        return ()
    if isinstance(method, FrameRatioMethod):
        return ("within", "mix", "entry_exit")
    if isinstance(method, FrameDivisionMethod):
        return effect_names(method.method)
    children = tuple(effect_names(term.method) for term in method.terms)
    if not any(children):
        return ()
    if any(not child or "additive" in child for child in children):
        return ("within", "mix", "entry_exit", "additive")
    return ("within", "mix", "entry_exit")


def schema(
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod,
    axes: tuple[DataAxis | CalendarDataAxis | PairedDataAxis, ...] = (),
) -> DataSchema:
    name = method.left.column.name.split(".", 1)[1]
    values = [
        column(f"contribution.{name}", method.change.column.dtype, unit=method.change.column.unit),
        column(f"total.{name}", method.change.column.dtype, unit=method.change.column.unit),
        column(
            f"observed_total.{name}", method.change.column.dtype, unit=method.change.column.unit
        ),
        column(
            f"contribution_share.{name}",
            ScalarDataType("float64"),
            unit="1" if method.change.column.unit else None,
        ),
        replace(method.left.column, name=f"detail.current.{name}"),
        replace(method.right.column, name=f"detail.baseline.{name}"),
        replace(method.change.column, name=f"detail.absolute.{name}"),
    ]
    if isinstance(method, FrameRatioMethod):
        values.extend(
            (
                replace(method.numerator_left.column, name=f"component.current.numerator.{name}"),
                replace(method.numerator_right.column, name=f"component.baseline.numerator.{name}"),
                replace(
                    method.denominator_left.column, name=f"component.current.denominator.{name}"
                ),
                replace(
                    method.denominator_right.column, name=f"component.baseline.denominator.{name}"
                ),
            )
        )
    values.extend(
        column(
            f"effect.{effect}.{name}", method.change.column.dtype, unit=method.change.column.unit
        )
        for effect in effect_names(method)
    )
    presence = tuple(
        column(
            f"detail.{side}.present.{name}", ScalarDataType("boolean"), nullable=False, unit=None
        )
        for side in ("current", "baseline")
    )
    return DataSchema(
        axes,
        tuple(values),
        presence,
        value_paths=tuple(tuple(c.name.split(".")) for c in values),
        presence_paths=tuple(tuple(c.name.split(".")) for c in presence),
    )


def metric(
    method: FrameAdditiveMethod | FrameRatioMethod | FrameLinearMethod | None = None,
) -> FrameAttributeMetric:
    rule = method or additive()
    output = schema(rule)
    name = rule.left.column.name.split(".", 1)[1]
    mapping = [
        FrameMethodOutput("contribution", ("contribution", name)),
        FrameMethodOutput("total", ("total", name)),
        FrameMethodOutput("observed_total", ("observed_total", name)),
        FrameMethodOutput("share", ("contribution_share", name)),
        FrameMethodOutput("current", ("detail", "current", name)),
        FrameMethodOutput("baseline", ("detail", "baseline", name)),
        FrameMethodOutput("absolute", ("detail", "absolute", name)),
    ]
    if isinstance(rule, FrameRatioMethod):
        mapping.extend(
            (
                FrameMethodOutput("numerator_current", ("component", "current", "numerator", name)),
                FrameMethodOutput(
                    "numerator_baseline", ("component", "baseline", "numerator", name)
                ),
                FrameMethodOutput(
                    "denominator_current", ("component", "current", "denominator", name)
                ),
                FrameMethodOutput(
                    "denominator_baseline", ("component", "baseline", "denominator", name)
                ),
            )
        )
    effects = {
        "within": FrameMethodOutput("effect_within", ("effect", "within", name)),
        "mix": FrameMethodOutput("effect_mix", ("effect", "mix", name)),
        "entry_exit": FrameMethodOutput("effect_entry_exit", ("effect", "entry_exit", name)),
        "additive": FrameMethodOutput("effect_additive", ("effect", "additive", name)),
    }
    mapping.extend(effects[effect] for effect in effect_names(rule))
    mapping.extend(
        (
            FrameMethodOutput("current_present", ("detail", "current", "present", name)),
            FrameMethodOutput("baseline_present", ("detail", "baseline", "present", name)),
        )
    )
    return FrameAttributeMetric(
        (name,),
        "absolute",
        rule,
        FrameMethodValue(output.values[2], rule.change.value),
        FrameMethodValue(output.values[1], rule.change.value),
        tuple(mapping),
        "zero_total" if rule.change.value == 0 else "defined",
    )


def make_facts(
    kind: Literal["attribute"],
    origin: str,
    output: DataSchema,
    coordinates: tuple[FrameMethodCoordinate, ...],
    parent_axis_count: int,
    parents: tuple[FrameMethodParent, ...],
) -> FrameMethodFacts:
    metrics = parents[0].metrics
    targets = tuple(FrameMethodTarget(item.target, item.kind, item.outputs) for item in metrics)
    return FrameMethodFacts(
        kind,
        origin,
        output,
        coordinates,
        parent_axis_count,
        parents,
        DataSchema(output.axes[:parent_axis_count], ()),
        targets,
    )


def facts() -> FrameMethodFacts:
    rule = additive()
    return make_facts(
        "attribute", "result-1", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )


def test_exact_scalars_are_preserved() -> None:
    cases: tuple[tuple[DataType, bool | int | float | str | date | Decimal], ...] = (
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
    )
    for dtype, value in cases:
        record = FrameMethodValue(column("value", dtype, unit=None), value)
        assert record.value is value
        assert FrameMethodValue(record.column, None).value is None


def test_null_decimal_and_ticks_boundaries() -> None:
    value = Decimal("1.2300")
    assert FrameMethodValue(column("x", DecimalDataType(4, 2)), value).value is value


def test_nested_linear_terms_preserve_order_and_explanatory_values() -> None:
    leaf = additive()
    terms = (FrameLinearTerm(-1, leaf), FrameLinearTerm(Decimal("2.000"), leaf))
    rule = FrameLinearMethod("linear", leaf.left, leaf.right, leaf.change, terms)
    outer = FrameLinearMethod(
        "linear", leaf.left, leaf.right, leaf.change, (FrameLinearTerm(0.5, rule),)
    )
    inner = outer.terms[0].method
    assert isinstance(inner, FrameLinearMethod)
    assert inner.terms == terms
    coefficient = inner.terms[1].coefficient
    assert isinstance(coefficient, Decimal)
    assert coefficient.as_tuple().exponent == -3
    assert (
        make_facts(
            "attribute", "r", schema(outer), (), 0, (FrameMethodParent((), (metric(outer),)),)
        )
        .parents[0]
        .metrics[0]
        .method
        is outer
    )


def test_share_state_uses_exact_change_and_support_roots_are_consistent() -> None:
    assert metric(additive(change=0)).share_state == "zero_total"
    rule = additive()
    changed = replace(rule, support=(replace(support(), left_effective_count=1),))
    linear = FrameLinearMethod(
        "linear",
        rule.left,
        rule.right,
        rule.change,
        (FrameLinearTerm(1, rule), FrameLinearTerm(-1, changed)),
    )


def test_origin_parent_count_and_exact_output_schema() -> None:
    original = facts()
    cases: tuple[dict[str, object], ...] = (
        {"kind": "other"},
        {"origin_result_id": ""},
        {"origin_result_id": "a\nb"},
        {"parent_axis_count": True},
        {"parent_axis_count": -1},
        {"parent_axis_count": 1},
        {"parents": list(original.parents)},
        {"coordinates": []},
        {"output_schema": None},
    )
    assert replace(original, parents=()).parents == ()


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
    result = make_facts("attribute", "r", output, (), 1, parents)
    assert result.parents == parents


def test_metric_directory_and_ratio_component_association() -> None:
    original = facts()
    parent = original.parents[0]
    rule = ratio()
    ratio_facts = make_facts(
        "attribute", "ratio-r", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    assert ratio_facts.parents[0].metrics[0].method == rule
    changed = replace(
        rule,
        numerator_left=replace(
            rule.numerator_left, column=replace(rule.numerator_left.column, nullable=False)
        ),
    )
    missing = replace(
        original.output_schema,
        values=original.output_schema.values[:-1],
        value_paths=original.output_schema.value_paths[:-1],
    )
    wrong_unit = replace(
        original.output_schema,
        values=(
            *original.output_schema.values[:3],
            replace(original.output_schema.values[3], unit="USD"),
            *original.output_schema.values[4:],
        ),
    )


def calendar(token: str = "period") -> CalendarDataAxis:
    return CalendarDataAxis(
        token,
        (column(f"key.{token}", unit=None, nullable=False),),
        "UTC",
        frozen=FrozenCalendarData(BuiltinCalendarData("day", 1), f"calendar.{token}"),
    )


def coordinate(axis: CalendarDataAxis, key: int = 1) -> FrameMethodCoordinate:
    return FrameMethodCoordinate(
        f"calendar.{axis.token}",
        calendar_directory_schema(axis),
        (
            (
                key,
                "2026-10-20",
                0,
                datetime(2026, 10, 20).toordinal() - 1,
                "full",
                None,
            ),
        ),
    )


def test_coordinate_exact_scalars_width_and_calendar_parent_domain() -> None:
    axis = calendar()
    snap = coordinate(axis)
    rule = additive()
    parent = FrameMethodParent((FrameMethodValue(axis.columns[0], 1),), (metric(rule),))
    result = make_facts("attribute", "calendar-r", schema(rule, (axis,)), (snap,), 1, (parent,))
    assert result.coordinates == (snap,)
    malformed = replace(snap, rows=((*snap.rows[0][:2], 1, *snap.rows[0][3:]),))


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
        assert len(text) < 180
        record.show(n=0)
        output = capsys.readouterr().out
        assert len(output.splitlines()) == 2
        record.show(n=2)
        output = capsys.readouterr().out
        assert len(output.splitlines()) <= 4
        assert max(len(line) for line in output.splitlines()) <= 240


def test_opaque_metric_unit_keeps_dimensionless_share_policy() -> None:
    rule = additive(unit="USD(net)")
    result = make_facts(
        "attribute", "opaque-r", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    assert result.output_schema.values[3].unit == "1"


def test_same_origin_payload_comparison_preserves_scalar_representation() -> None:
    from marivo.analysis.core.frame_method_facts import same_method_facts

    rule = ratio()
    first = FrameLinearMethod(
        "linear", rule.left, rule.right, rule.change, (FrameLinearTerm(1, rule),)
    )
    second = replace(first, terms=(FrameLinearTerm(1.0, rule),))

    def wrapped(method: FrameLinearMethod) -> FrameMethodFacts:
        return make_facts(
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
        FrameMethodValue(column("absolute.sales", DecimalDataType(38, 9)), Decimal("8.000000000")),
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
    facts = make_facts(
        "attribute", "native", schema(rule), (), 0, (FrameMethodParent((), (metric(rule),)),)
    )
    validate_method_facts(facts)
    coefficient = rule.terms[0].coefficient
    assert isinstance(coefficient, Decimal)
    assert coefficient.as_tuple() == scalar.as_tuple()
    bad = replace(
        rule,
        left=replace(rule.left, value=Decimal("11.000000000")),
        change=replace(rule.change, value=Decimal("5.000000000")),
    )
