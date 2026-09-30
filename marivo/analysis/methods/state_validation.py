"""Numerical invariants owned by the qualified transient method states."""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from typing import Literal

from marivo.analysis.core.model import OriginalStatePart, RowStatisticQuantity, Signature


def _numeric(value: object) -> bool:
    return (
        (type(value) is int and -(2**63) <= value < 2**63)
        or (type(value) is float and math.isfinite(value))
        or (type(value) is Decimal and value.is_finite())
    )


def _absolute_sum_matches(total: object, absolute: object, support: object) -> bool:
    """Validate the retained magnitude used by the float denominator error bound."""
    return (
        type(total) is float
        and type(absolute) is float
        and math.isfinite(absolute)
        and absolute >= 0
        and (support != 0 or absolute == 0)
        and abs(total) <= absolute + absolute * 1e-12 + 1e-12
    )


def _denominator_qualified(total: float, absolute: float) -> bool:
    """Keep the zero policy; nonzero denominators must exclude zero from their bound."""
    return total == 0 or abs(total) > absolute * 1e-12 + 1e-12


def denominator_interval_spans_zero(kind: str, part: Mapping[str, object]) -> bool:
    """Identify an unstable nonzero floating denominator for actionable rejection."""
    names = {
        "original_ratio": ("denominator_sum", "denominator_absolute_sum"),
        "original_weighted_mean": ("weight_sum", "absolute_weight_sum"),
    }.get(kind)
    if names is None:
        return False
    total = part.get("original_state__" + names[0])
    absolute = part.get("original_state__" + names[1])
    return (
        type(total) is float
        and type(absolute) is float
        and math.isfinite(total)
        and math.isfinite(absolute)
        and absolute >= 0
        and not _denominator_qualified(total, absolute)
    )


def _division_matches(
    value: object, numerator: int | float | Decimal, denominator: int | float | Decimal
) -> bool:
    if type(value) is int and type(numerator) is int and type(denominator) is int:
        return value == round(Fraction(numerator, denominator))
    if type(value) is Decimal:
        if (
            not isinstance(numerator, (Decimal, int))
            or not isinstance(denominator, (Decimal, int))
            or not value.is_finite()
        ):
            return False
        exponent = value.as_tuple().exponent
        assert isinstance(exponent, int)
        with localcontext() as context:
            context.prec = 100
            return value == (Decimal(numerator) / Decimal(denominator)).quantize(
                Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN
            )
    return (
        isinstance(numerator, (int, float))
        and isinstance(denominator, (int, float))
        and type(value) is float
        and math.isfinite(value)
        and value == numerator / denominator
    )


def difference_matches(
    primary: Mapping[str, object],
    current: Mapping[str, object],
    baseline: Mapping[str, object],
    *,
    method: str = "cell.difference@v1",
) -> bool:
    """Verify finite homogeneous endpoints and a once-rounded exact difference."""
    first = current.get("current_endpoint__value")
    second = baseline.get("baseline_endpoint__value")
    value = primary.get("value")
    if not (
        _numeric(first)
        and _numeric(second)
        and type(first) is type(second)
        and current.get("current_endpoint__cell_tag") == "defined"
        and baseline.get("baseline_endpoint__cell_tag") == "defined"
        and current.get("current_endpoint__cell_reason") is None
        and baseline.get("baseline_endpoint__cell_reason") is None
    ):
        return False
    assert isinstance(first, (int, float, Decimal))
    assert isinstance(second, (int, float, Decimal))
    if method != "cell.difference@v1" and second == 0:
        return (
            value is None
            and primary.get("cell_tag") == "undefined"
            and primary.get("cell_reason")
            == ("zero_baseline" if method == "cell.relative_change@v1" else "zero_denominator")
        )
    if (
        not _numeric(value)
        or primary.get("cell_tag") != "defined"
        or primary.get("cell_reason") is not None
    ):
        return False
    exact = Fraction(first) - Fraction(second)
    if method == "cell.relative_change@v1":
        exact /= abs(Fraction(second))
    elif method == "cell.ratio@v1":
        exact = Fraction(first) / Fraction(second)
    if isinstance(value, Decimal) and method != "cell.difference@v1":
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            return False
        return Fraction(value) == Fraction(round(exact * 10 ** (-exponent)), 10 ** (-exponent))
    if type(value) is float:
        try:
            return value == float(exact)
        except OverflowError:
            return False
    assert isinstance(value, (int, Decimal))
    return Fraction(value) == exact


def state_matches(
    kind: str,
    primary: Mapping[str, object],
    part: Mapping[str, object],
    *,
    empty_rules: tuple[Literal["null", "zero"], ...] = (),
) -> bool:
    """Check one complete-key-associated primary and required state part."""
    if "row_state__error_bound" in part:
        bound = part["row_state__error_bound"]
        if type(bound) is not float or not math.isfinite(bound) or bound < 0:
            return False
    value = primary.get("value")
    if kind == "original_fold":
        from marivo.analysis.methods.physical import DurationType
        from marivo.analysis.methods.temporal_fold import decode_samples, fold_value

        unit = primary.get("__duration_unit")
        duration = DurationType(unit) if unit in ("s", "ms", "us", "ns") else None
        try:
            expected = fold_value(
                decode_samples(part.get("original_state__samples")),
                part.get("original_state__fold_kind"),
                duration,
            )
        except (ValueError, TypeError, OverflowError):
            return False
        return (
            value == expected
            and primary.get("cell_tag") == ("null" if expected is None else "defined")
            and primary.get("cell_reason") == ("empty_contribution" if expected is None else None)
        )
    if kind == "original_linear":
        terms = sorted(
            name.removeprefix("original_state__").removesuffix("_sum")
            for name in part
            if name.startswith("original_state__")
            and name.endswith("_sum")
            and not name.endswith("_absolute_sum")
        )
        if len(terms) < 2 or len(empty_rules) != len(terms):
            return False
        signed_total: int | float | Decimal = 0
        float_terms: list[float] = []
        contributed = True
        indices: set[int] = set()
        for term in terms:
            sign, separator, position = term.partition("_")
            if sign not in ("plus", "minus") or not separator or not position.isdecimal():
                return False
            index = int(position)
            if index >= len(terms) or index in indices:
                return False
            indices.add(index)
            magnitude = part.get(f"original_state__{term}_sum")
            support = part.get(f"original_state__{term}_non_null_count")
            if (
                not isinstance(magnitude, (int, float, Decimal))
                or not _numeric(magnitude)
                or type(support) is not int
                or not 0 <= support < 2**63
                or (support == 0 and magnitude != 0)
            ):
                return False
            if (
                type(magnitude) is float
                and f"original_state__{term}_absolute_sum" in part
                and not _absolute_sum_matches(
                    magnitude, part[f"original_state__{term}_absolute_sum"], support
                )
            ):
                return False
            with localcontext() as context:
                context.prec = 100
                operand = magnitude if term.startswith("plus_") else -magnitude
                if isinstance(operand, Decimal) or isinstance(signed_total, Decimal):
                    if isinstance(operand, float) or isinstance(signed_total, float):
                        return False
                    signed_total = Decimal(signed_total) + Decimal(operand)
                else:
                    signed_total += operand
                    if isinstance(operand, float):
                        float_terms.append(operand)
            contributed = contributed and (support > 0 or empty_rules[index] == "zero")
        if not contributed:
            # No component rows: an empty contribution, never a silent zero.
            return (
                value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason") == "empty_contribution"
            )
        agrees = value == signed_total
        if type(value) is float and float_terms:
            if len(float_terms) != len(terms):
                return False
            try:
                reference = math.fsum(float_terms)
                budget = math.fsum(abs(term) * 1e-12 for term in float_terms) + 1e-12
            except OverflowError:
                return False
            agrees = math.isclose(value, reference, rel_tol=1e-12, abs_tol=budget)
        return (
            isinstance(value, (int, float, Decimal))
            and math.isfinite(value)
            and agrees
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "original_ratio":
        if len(empty_rules) != 2:
            return False
        magnitudes: list[int | float | Decimal] = []
        contributed = True
        for index, prefix in enumerate(("numerator", "denominator")):
            magnitude = part.get(f"original_state__{prefix}_sum")
            support = part.get(f"original_state__{prefix}_non_null_count")
            if (
                not isinstance(magnitude, (int, float, Decimal))
                or not _numeric(magnitude)
                or type(support) is not int
                or not 0 <= support < 2**63
                or (support == 0 and magnitude != 0)
            ):
                return False
            magnitudes.append(magnitude)
            contributed = contributed and (support > 0 or empty_rules[index] == "zero")
        if not contributed:
            return (
                value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason") == "empty_contribution"
            )
        numerator, denominator = magnitudes
        if (
            type(numerator) is float
            and "original_state__numerator_absolute_sum" in part
            and not _absolute_sum_matches(
                numerator,
                part["original_state__numerator_absolute_sum"],
                part.get("original_state__numerator_non_null_count"),
            )
        ):
            return False
        if type(denominator) is float:
            absolute = part.get("original_state__denominator_absolute_sum")
            if (
                not _absolute_sum_matches(
                    denominator, absolute, part.get("original_state__denominator_non_null_count")
                )
                or not isinstance(absolute, float)
                or not _denominator_qualified(denominator, absolute)
            ):
                return False
        if denominator == 0:
            return (
                value is None
                and primary.get("cell_tag") == "undefined"
                and primary.get("cell_reason") == "zero_denominator"
            )
        return (
            _division_matches(value, numerator, denominator)
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "original_weighted_mean":
        weighted_total = part.get("original_state__weighted_numerator")
        weight_total = part.get("original_state__weight_sum")
        pairs = part.get("original_state__non_null_pair_count")
        rows = part.get("original_state__row_count")
        if (
            not isinstance(weighted_total, (int, float, Decimal))
            or type(weighted_total) not in (int, float, Decimal)
            or not isinstance(weight_total, (int, float, Decimal))
            or type(weight_total) is not type(weighted_total)
            or type(pairs) is not int
            or type(rows) is not int
            or not math.isfinite(weighted_total)
            or not math.isfinite(weight_total)
            or (type(weighted_total) is int and not -(2**63) <= weighted_total < 2**63)
            or (type(weight_total) is int and not -(2**63) <= weight_total < 2**63)
            or not 0 <= pairs <= rows < 2**63
            or (pairs == 0 and (weighted_total != 0 or weight_total != 0))
        ):
            return False
        if (
            type(weighted_total) is float
            and "original_state__absolute_weighted_numerator" in part
            and not _absolute_sum_matches(
                weighted_total, part["original_state__absolute_weighted_numerator"], pairs
            )
        ):
            return False
        if type(weight_total) is float:
            absolute = part.get("original_state__absolute_weight_sum")
            if (
                not _absolute_sum_matches(weight_total, absolute, pairs)
                or not isinstance(absolute, float)
                or not _denominator_qualified(weight_total, absolute)
            ):
                return False
        if pairs == 0 or weight_total == 0:
            return (
                value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason")
                == ("empty_contribution" if pairs == 0 else "zero_weight_sum")
            )
        return (
            _division_matches(value, weighted_total, weight_total)
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "original_count":
        count = part.get("original_state__count")
        return (
            type(count) is int
            and 0 <= count < 2**63
            and type(value) is int
            and value == count
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind in ("original_min", "original_max"):
        total = part.get("original_state__" + kind.removeprefix("original_"))
        count = part.get("original_state__non_null_count")
        if (
            not isinstance(total, (int, float, Decimal))
            or type(total) not in (int, float, Decimal)
            or not math.isfinite(total)
            or type(count) is not int
            or not 0 <= count < 2**63
        ):
            return False
        if type(total) is int and not -(2**63) <= total < 2**63:
            return False
        return (
            (value == total if count else value is None and total == 0)
            and primary.get("cell_tag") == ("defined" if count else "null")
            and primary.get("cell_reason") == (None if count else "empty_contribution")
        )
    if kind in ("original_sum", "original_sum_zero"):
        total = part.get("original_state__sum")
        count = part.get("original_state__non_null_count")
        if type(total) is float and not _absolute_sum_matches(
            total, part.get("original_state__absolute_sum"), count
        ):
            return False
        if (
            type(total) not in (int, float, Decimal)
            or not isinstance(total, (int, float, Decimal))
            or not math.isfinite(total)
            or type(count) is not int
            or not 0 <= count < 2**63
        ):
            return False
        if type(total) is int and not -(2**63) <= total < 2**63:
            return False
        if count == 0 and kind == "original_sum_zero":
            return (
                total == 0
                and value == 0
                and primary.get("cell_tag") == "defined"
                and primary.get("cell_reason") is None
            )
        if (
            type(total) is float
            and "original_state__absolute_sum" in part
            and not _absolute_sum_matches(total, part["original_state__absolute_sum"], count)
        ):
            return False
        if count == 0:
            return (
                total == 0
                and value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason") == "empty_contribution"
            )
        return (
            value == total
            and type(value) is type(total)
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind in ("row_sum", "row_count", "row_count_defined"):
        if kind == "row_sum":
            support = part.get("row_state__count")
            if (
                type(support) is not int
                or not 0 <= support < 2**63
                or (support == 0 and part.get("row_state__sum") != 0)
            ):
                return False
        component = part.get(f"row_state__{kind.removeprefix('row_')}")
        return (
            (
                (type(component) is int and -(2**63) <= component < 2**63)
                or (kind == "row_sum" and type(component) is float and math.isfinite(component))
            )
            and isinstance(component, (int, float))
            and (kind == "row_sum" or component >= 0)
            and primary.get("cell_tag") == "defined"
            and type(value) is type(component)
            and value == component
        )
    if kind in ("row_min", "row_max"):
        component = part.get(f"row_state__{kind.removeprefix('row_')}")
        support = part.get("row_state__count")
        if type(support) is not int or not 0 <= support < 2**63:
            return False
        if support == 0:
            return (
                component is None
                and value is None
                and primary.get("cell_tag") == "undefined"
                and primary.get("cell_reason") == "empty_" + kind.removeprefix("row_")
            )
        return (
            (
                (type(component) is int and -(2**63) <= component < 2**63)
                or (type(component) is float and math.isfinite(component))
            )
            and type(value) is type(component)
            and value == component
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "original_mean":
        total, count, rows = (
            part.get("original_state__" + name) for name in ("sum", "non_null_count", "row_count")
        )
        if (
            not isinstance(total, (int, float, Decimal))
            or type(total) not in (int, float, Decimal)
            or not math.isfinite(total)
            or (type(total) is int and not -(2**63) <= total < 2**63)
            or type(count) is not int
            or type(rows) is not int
            or not 0 <= count <= rows < 2**63
        ):
            return False
        if (
            type(total) is float
            and "original_state__absolute_sum" in part
            and not _absolute_sum_matches(total, part["original_state__absolute_sum"], count)
        ):
            return False
        if count == 0:
            return (
                total == 0
                and value is None
                and primary.get("cell_tag") == "null"
                and primary.get("cell_reason") == "empty_contribution"
            )
        return (
            _division_matches(value, total, count)
            and primary.get("cell_tag") == "defined"
            and primary.get("cell_reason") is None
        )
    if kind == "row_mean":
        total, count = part.get("row_state__sum"), part.get("row_state__count")
        if (
            not (
                (type(total) is int and -(2**63) <= total < 2**63)
                or (type(total) is float and math.isfinite(total))
            )
            or not isinstance(total, (int, float, Decimal))
            or type(count) is not int
            or not 0 <= count < 2**63
        ):
            return False
        if (
            type(total) is float
            and "original_state__absolute_sum" in part
            and not _absolute_sum_matches(total, part["original_state__absolute_sum"], count)
        ):
            return False
        if count == 0:
            return (
                total == 0
                and value is None
                and primary.get("cell_tag") == "undefined"
                and primary.get("cell_reason") == "empty_mean"
            )
        return (
            primary.get("cell_tag") == "defined"
            and type(value) is float
            and math.isfinite(value)
            and value == float(total) / count
        )
    if kind == "spearman":
        counts = tuple(
            part.get(f"pair_counts__{name}_count")
            for name in ("input_observation", "matched_observation", "null_pair", "complete_pair")
        )
        if any(type(count) is not int or not 0 <= count < 2**63 for count in counts):
            return False
        # Narrow independently of the physical producer or Arrow scalar conversion.
        incoming, matched, nulls, complete = counts
        assert isinstance(incoming, int) and isinstance(matched, int)
        assert isinstance(nulls, int) and isinstance(complete, int)
        if incoming != matched or matched != nulls + complete:
            return False
        status = primary.get("status")
        if complete < 2:
            return status == "insufficient_pairs" and value is None
        if status == "valid":
            return type(value) is float and math.isfinite(value) and -1 <= value <= 1
        return status in ("constant_a", "constant_b", "constant_both") and value is None
    return False


def coordinate_state_matches(
    components: tuple[str, ...],
    value_type: str,
    groups: object,
    original: Mapping[str, object],
    coordinate_columns: tuple[str, ...] = ("coordinate",),
) -> bool:
    """Verify a complete ordered coordinate partition against its original state."""
    if not isinstance(groups, list):
        return False
    labels: list[tuple[str, ...]] = []
    values: dict[str, list[int | float | Decimal]] = {name: [] for name in components}
    for item in groups:
        if not isinstance(item, dict) or set(item) != {*coordinate_columns, *components}:
            return False
        label = tuple(item[name] for name in coordinate_columns)
        if any(not isinstance(value, str) for value in label):
            return False
        labels.append(label)
        for name in components:
            value: object = item[name]
            floating = value_type == "float64" and "count" not in name
            if floating:
                if type(value) is not float or not math.isfinite(value):
                    return False
            elif value_type.startswith("decimal(") and "count" not in name:
                if type(value) is not Decimal or not value.is_finite():
                    return False
            elif type(value) is not int or not -(2**63) <= value < 2**63:
                return False
            if "count" in name and value < 0:
                return False
            values[name].append(value)
        pairs = [
            ("sum", "non_null_count"),
            ("numerator_sum", "numerator_non_null_count"),
            ("denominator_sum", "denominator_non_null_count"),
            ("weighted_numerator", "non_null_pair_count"),
            ("weight_sum", "non_null_pair_count"),
            *(
                (name, name.removesuffix("_sum") + "_non_null_count")
                for name in components
                if name.startswith(("plus_", "minus_"))
                and name.endswith("_sum")
                and not name.endswith("_absolute_sum")
            ),
        ]
        for total_name, count_name in pairs:
            if (
                total_name in components
                and count_name in components
                and item[count_name] == 0
                and item[total_name] != 0
            ):
                return False
        for support in ("non_null_count", "non_null_pair_count"):
            if (
                support in components
                and "row_count" in components
                and item[support] > item["row_count"]
            ):
                return False
    if labels != sorted(set(labels)):
        return False
    for name, entries in values.items():
        expected = original.get(f"original_state__{name}")
        floating = value_type == "float64" and "count" not in name
        if floating:
            if type(expected) is not float or not math.isfinite(expected):
                return False
            try:
                active = [group[name] for group in groups if group.get("non_null_count", 1) > 0]
                total = (
                    ((min(active) if name == "min" else max(active)) if active else 0.0)
                    if name in ("min", "max")
                    else math.fsum(entries)
                )
            except OverflowError:
                return False
            if not math.isclose(total, expected, rel_tol=1e-12, abs_tol=1e-12):
                return False
        else:
            with localcontext() as context:
                context.prec = 120
                if name in ("min", "max"):
                    active = [group[name] for group in groups if group["non_null_count"] > 0]
                    total = (min(active) if name == "min" else max(active)) if active else 0
                else:
                    total = sum(entries)
                if total != expected:
                    return False
    return True


def empty_reduction_cell(signature: Signature) -> tuple[int | None, str, str | None]:
    """Finish a lawful empty group from its frozen method and component policies."""
    quantity = signature.quantity
    if isinstance(quantity, RowStatisticQuantity):
        method = quantity.method_version
        if method in ("row.count@v1", "row.count_defined@v1", "row.sum@v1"):
            return 0, "defined", None
        if method in ("row.mean@v1", "row.min@v1", "row.max@v1"):
            return None, "undefined", "empty_" + method.removeprefix("row.").removesuffix("@v1")
    original = next((p for p in signature.parts if isinstance(p, OriginalStatePart)), None)
    if original is not None:
        if original.method_version in ("sum_zero@v1", "count@v1"):
            return 0, "defined", None
        if original.method_version == "linear@v1" and all(
            rule == "zero" for rule in original.empty_rules
        ):
            return 0, "defined", None
        if original.method_version == "ratio@v1" and all(
            rule == "zero" for rule in original.empty_rules
        ):
            return None, "undefined", "zero_denominator"
        return None, "null", "empty_contribution"
    raise ValueError("no registered empty reduction state")
