"""Literal frozen-directory and map witnesses, without source calendar objects."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import pandas as pd
import pyarrow as pa
import pytest

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis.core.data_schema import (
    BuiltinCalendarData,
    CalendarDataAxis,
    DataColumn,
    DataSchema,
    FrozenCalendarData,
    NamedCalendarData,
    PairedDataAxis,
)
from marivo.analysis.core.data_types import (
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frozen_calendar import (
    FrozenAttachment,
    FrozenCalendarDirectory,
    build_partial_calendar_map,
    calendar_directory_rows,
    calendar_directory_schema,
    coordinate_attachment_schemas,
    merge_equal_calendar_directories,
    paired_mapping_schema,
    read_calendar_directory,
    require_matchable_calendar_directory,
    validate_frozen_coordinates,
    validate_paired_mapping,
)
from marivo.analysis.core.model import CoreRuleError


def axis(
    token: str = "axis.0",
    *,
    unit: Literal["day", "week", "month", "quarter", "year", "hour"] = "day",
    step: int = 1,
) -> CalendarDataAxis:
    return CalendarDataAxis(
        token,
        (DataColumn(f"key.{token}", "bucket", ScalarDataType("int64"), False),),
        "UTC",
        name="period",
        frozen=FrozenCalendarData(BuiltinCalendarData(unit, step), f"calendar.{token}"),
    )


def directory(
    endpoint: CalendarDataAxis,
    labels: tuple[str, ...],
    *,
    keys: tuple[int, ...] | None = None,
    ends: tuple[str, ...] | None = None,
) -> FrozenAttachment:
    starts = tuple(
        datetime.combine(date.fromisoformat(label), datetime.min.time(), timezone.utc)
        for label in labels
    )
    bounds = (
        tuple(
            datetime.combine(date.fromisoformat(label), datetime.min.time(), timezone.utc)
            for label in ends
        )
        if ends is not None
        else tuple(start + timedelta(days=1) for start in starts)
    )
    rows = pd.DataFrame(
        {
            endpoint.columns[0].slot_id: pd.Series(
                keys or tuple(range(len(labels))), dtype="int64[pyarrow]"
            ),
            "nominal_label": pd.Series(labels, dtype="string[pyarrow]"),
            "ordinal": pd.Series(range(len(labels)), dtype="int64[pyarrow]"),
            **{
                name: pd.Series(values, dtype="timestamp[us, tz=UTC][pyarrow]")
                for name, values in (
                    ("nominal_start", starts),
                    ("nominal_end", bounds),
                    ("actual_start", starts),
                    ("actual_end", bounds),
                )
            },
        }
    )
    assert endpoint.frozen is not None
    return FrozenAttachment(
        endpoint.frozen.directory_name, calendar_directory_schema(endpoint), rows
    )


def read(attachment: FrozenAttachment, endpoint: CalendarDataAxis) -> FrozenCalendarDirectory:
    return read_calendar_directory(endpoint, attachment.schema, attachment.rows)


def test_directory_retains_original_ordinals_and_all_unobserved_buckets() -> None:
    endpoint = axis()
    attachment = directory(endpoint, ("2024-02-28", "2024-02-29", "2024-03-01"))
    frozen = read(attachment, endpoint)
    assert tuple(bucket.ordinal for bucket in frozen.buckets) == (0, 1, 2)
    assert tuple(bucket.nominal_label for bucket in frozen.buckets) == (
        "2024-02-28",
        "2024-02-29",
        "2024-03-01",
    )
    rows = pd.DataFrame({endpoint.columns[0].slot_id: pd.Series([2], dtype="int64[pyarrow]")})
    validate_frozen_coordinates(DataSchema((endpoint,), ()), rows, (attachment,))
    assert len(frozen.buckets) == 3


def test_partial_actual_bucket_reads_but_never_becomes_matchable() -> None:
    endpoint = axis(unit="month")
    attachment = directory(endpoint, ("2026-08-01",), ends=("2026-09-01",))
    attachment.rows.loc[0, "actual_start"] = datetime(2026, 8, 15, tzinfo=timezone.utc)
    frozen = read(attachment, endpoint)
    validate_frozen_coordinates(
        DataSchema((endpoint,), ()), attachment.rows.iloc[:0, :1], (attachment,)
    )
    for rule in (
        CalendarCorrespondence("ordinal"),
        CalendarCorrespondence("previous_year"),
        CalendarCorrespondence("period_offset", -1),
    ):
        with pytest.raises(CoreRuleError, match="complete actual"):
            build_partial_calendar_map(frozen, frozen, rule)


def test_leap_day_mapping_is_civil_and_independent_of_actual_rows() -> None:
    left, right = axis("left"), axis("right")
    a = read(directory(left, ("2024-02-28", "2024-02-29", "2024-03-01")), left)
    b = read(directory(right, ("2023-02-28", "2023-03-01"), keys=(40, 50)), right)
    pairs = build_partial_calendar_map(a, b, CalendarCorrespondence("previous_year"))
    assert tuple((p.left_key, p.right_key) for p in pairs) == (
        (a.buckets[0].key, b.buckets[0].key),
        (a.buckets[1].key, None),
        (a.buckets[2].key, b.buckets[1].key),
    )


def test_ordinal_31_30_tail_and_sparse_rows_do_not_change_map() -> None:
    left, right = axis("left"), axis("right")
    a = read(directory(left, tuple(f"2026-07-{i:02}" for i in range(1, 32))), left)
    b = read(directory(right, tuple(f"2026-06-{i:02}" for i in range(1, 31))), right)
    pairs = build_partial_calendar_map(a, b, CalendarCorrespondence("ordinal"))
    assert len(pairs) == 31
    assert pairs[2].left_key == a.buckets[2].key
    assert pairs[2].right_key == b.buckets[2].key
    assert pairs[-1].left_key == a.buckets[30].key
    assert pairs[-1].right_key is None


def test_calendar_equality_preserves_data_and_rejects_ordinal_conflicts() -> None:
    left, right = axis("left"), axis("right")
    a = read(directory(left, ("2026-08-01", "2026-08-02")), left)
    b = read(directory(right, ("2026-08-01", "2026-08-02")), right)
    merged = merge_equal_calendar_directories(a, b, left)
    assert merged.buckets == a.buckets
    conflict = read(directory(right, ("2026-08-02",)), right)
    with pytest.raises(CoreRuleError, match="conflict"):
        merge_equal_calendar_directories(a, conflict, left)


def test_named_identity_includes_level_and_only_ordinal_is_supported() -> None:
    base = axis()
    assert base.frozen is not None
    named = replace(
        base,
        frozen=replace(base.frozen, definition=NamedCalendarData("business", "digest", "month")),
    )
    attachment = directory(named, ("2026-08-01",))
    attachment.rows["nominal_label"] = pd.Series(['"August"'], dtype="string[pyarrow]")
    frozen = read(attachment, named)
    assert len(build_partial_calendar_map(frozen, frozen, CalendarCorrespondence("ordinal"))) == 1
    for kind in ("previous_year", "period_offset"):
        rule = (
            CalendarCorrespondence("previous_year")
            if kind == "previous_year"
            else CalendarCorrespondence("period_offset", -1)
        )
        with pytest.raises(CoreRuleError, match="builtin"):
            build_partial_calendar_map(frozen, frozen, rule)
    other = replace(
        named,
        frozen=replace(named.frozen, definition=NamedCalendarData("business", "digest", "quarter")),
    )
    with pytest.raises(CoreRuleError, match="compatible frozen"):
        build_partial_calendar_map(
            frozen, replace(frozen, axis=other), CalendarCorrespondence("ordinal")
        )


def test_paired_map_schema_is_closed_and_region_presence_is_irrelevant() -> None:
    left, right = axis("pair.left"), axis("pair.right")
    paired = PairedDataAxis(
        "pair",
        (DataColumn("pair.key", "pair", ScalarDataType("int64"), False),),
        left,
        right,
        "mapping.pair",
        name="period",
    )
    a, b = directory(left, ("2026-08-01",)), directory(right, ("2026-07-01",))
    schema = paired_mapping_schema(paired)
    assert schema.value_paths == (("left", "0"), ("right", "0"))
    assert tuple(column.slot_id for column in schema.values) == ("left.0", "right.0")
    rows = pd.DataFrame(
        {
            name: pd.Series(values, dtype="int64[pyarrow]")
            for name, values in {"pair.key": [5], "left.0": [0], "right.0": [0]}.items()
        }
    )
    mapping = FrozenAttachment("mapping.pair", schema, rows)
    validate_paired_mapping(paired, schema, rows, read(a, left), read(b, right))
    primary = pd.DataFrame({"pair.key": pd.Series([5], dtype="int64[pyarrow]")})
    validate_frozen_coordinates(DataSchema((paired,), ()), primary, (a, b, mapping))
    assert tuple(name for name, _ in coordinate_attachment_schemas(DataSchema((paired,), ()))) == (
        "calendar.pair.left",
        "calendar.pair.right",
        "mapping.pair",
    )
    rows.loc[0, ["left.0", "right.0"]] = pd.NA
    with pytest.raises(CoreRuleError, match="both absent"):
        validate_paired_mapping(paired, schema, rows, read(a, left), read(b, right))


@pytest.mark.parametrize("dtype", [DurationDataType("ns"), TimestampDataType("ns", "UTC")])
def test_temporal_int64_minimum_is_valid_key_not_pandas_nat(
    dtype: DurationDataType | TimestampDataType,
) -> None:
    endpoint = axis()
    key = replace(endpoint.columns[0], dtype=dtype)
    endpoint = replace(endpoint, columns=(key,))
    attachment = directory(axis(), ("2026-08-01",))
    arrow_type = (
        pa.duration("ns") if isinstance(dtype, DurationDataType) else pa.timestamp("ns", "UTC")
    )
    attachment.rows[key.slot_id] = pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array([-(2**63)], type=pa.int64()).cast(arrow_type))
    )
    attachment = replace(attachment, schema=calendar_directory_schema(endpoint))
    frozen = read(attachment, endpoint)
    assert frozen.buckets[0].key == ((dtype, -(2**63)),)
    validate_frozen_coordinates(
        DataSchema((endpoint,), ()), attachment.rows.iloc[:, :1], (attachment,)
    )


@pytest.mark.parametrize(
    ("column", "values", "message"),
    [
        ("ordinal", (0, 0), "original ordinal"),
        ("ordinal", (0, 2), "original ordinal"),
        ("nominal_label", ("2026-08-01", "2026-08-01"), "unique nominal"),
        ("nominal_label", ("2026-08-01", "2026-02-31"), "canonical frozen"),
        ("nominal_label", ("2026-08-01", "20260802"), "canonical frozen"),
        ("key.axis.0", (0, 0), "unique complete calendar"),
    ],
)
def test_bad_unobserved_directory_record_is_not_an_absent_match(
    column: str, values: tuple[int, int] | tuple[str, str], message: str
) -> None:
    endpoint = axis()
    attachment = directory(endpoint, ("2026-08-01", "2026-08-02"))
    attachment.rows[column] = pd.Series(
        values, dtype="string[pyarrow]" if isinstance(values[0], str) else "int64[pyarrow]"
    )
    empty = pd.DataFrame({endpoint.columns[0].slot_id: pd.Series([], dtype="int64[pyarrow]")})
    with pytest.raises(CoreRuleError, match=message):
        validate_frozen_coordinates(DataSchema((endpoint,), ()), empty, (attachment,))


@pytest.mark.parametrize("column", ["nominal_end", "actual_start", "actual_end"])
def test_invalid_bounds_are_rejected_on_read(column: str) -> None:
    endpoint = axis()
    attachment = directory(endpoint, ("2026-08-01",))
    attachment.rows.loc[0, column] = datetime(2026, 7, 31, tzinfo=timezone.utc)
    with pytest.raises(CoreRuleError, match="half-open"):
        read(attachment, endpoint)


def test_exact_directory_layout_and_types_are_not_silently_coerced() -> None:
    endpoint = axis()
    attachment = directory(endpoint, ("2026-08-01",))
    with pytest.raises(CoreRuleError, match="one exact ordered path for each named column"):
        replace(attachment.schema, value_paths=())
    with pytest.raises(CoreRuleError, match="exact slot order"):
        read_calendar_directory(endpoint, attachment.schema, attachment.rows.iloc[:, ::-1])
    attachment.rows["ordinal"] = pd.Series([0.0], dtype="float64[pyarrow]")
    with pytest.raises(CoreRuleError, match="exact declared physical"):
        read(attachment, endpoint)


@pytest.mark.parametrize("overlap", [False, True])
def test_nominal_partition_checks_all_buckets_before_any_rule(overlap: bool) -> None:
    endpoint = axis()
    attachment = directory(
        endpoint,
        ("2026-08-01", "2026-08-02" if overlap else "2026-08-03"),
        ends=("2026-08-03", "2026-08-04") if overlap else None,
    )
    frozen = read(attachment, endpoint)
    for rule in (
        CalendarCorrespondence("ordinal"),
        CalendarCorrespondence("previous_year"),
        CalendarCorrespondence("period_offset", -1),
    ):
        with pytest.raises(CoreRuleError, match="continuous nonoverlapping"):
            build_partial_calendar_map(frozen, frozen, rule)


@pytest.mark.parametrize(
    ("unit", "left_label", "left_end", "right_label", "right_end"),
    [
        ("day", "2026-03-03", "2026-03-05", "2026-03-01", "2026-03-03"),
        ("week", "2026-03-16", "2026-03-30", "2026-03-02", "2026-03-16"),
        ("month", "2026-05-01", "2026-07-01", "2026-03-01", "2026-05-01"),
        ("quarter", "2026-07-01", "2027-01-01", "2026-01-01", "2026-07-01"),
        ("year", "2026-01-01", "2028-01-01", "2024-01-01", "2026-01-01"),
    ],
)
def test_period_offset_uses_current_to_baseline_direction_and_captured_step(
    unit: Literal["day", "week", "month", "quarter", "year"],
    left_label: str,
    left_end: str,
    right_label: str,
    right_end: str,
) -> None:
    left, right = axis("left", unit=unit, step=2), axis("right", unit=unit, step=2)
    a = read(directory(left, (left_label,), ends=(left_end,)), left)
    b = read(directory(right, (right_label,), ends=(right_end,)), right)
    (pair,) = build_partial_calendar_map(a, b, CalendarCorrespondence("period_offset", -1))
    assert (pair.left_key, pair.right_key) == (a.buckets[0].key, b.buckets[0].key)


def test_month_label_offset_does_not_clamp_day_or_subtract_local_ordinals() -> None:
    left, right = axis("left", unit="month"), axis("right", unit="month")
    a = read(directory(left, ("2024-03-31",), ends=("2024-04-30",)), left)
    b = read(directory(right, ("2024-02-29",), ends=("2024-03-31",)), right)
    pairs = build_partial_calendar_map(a, b, CalendarCorrespondence("period_offset", -1))
    assert tuple((p.left_key, p.right_key) for p in pairs) == (
        (a.buckets[0].key, None),
        (None, b.buckets[0].key),
    )


def test_subday_offset_rejects_without_timezone_lookup() -> None:
    endpoint = axis(unit="hour")
    attachment = directory(endpoint, ("2026-08-01",))
    attachment.rows["nominal_label"] = pd.Series(
        ["2026-08-01T00:00:00.000000+00:00"], dtype="string[pyarrow]"
    )
    frozen = read(attachment, endpoint)
    with pytest.raises(CoreRuleError, match="supported builtin"):
        build_partial_calendar_map(frozen, frozen, CalendarCorrespondence("period_offset", -1))


@pytest.mark.parametrize("label", ["null", "[]", "{}", "NaN", '"a" ', "01"])
def test_named_labels_are_exact_canonical_nonnull_json_scalars(label: str) -> None:
    endpoint = axis()
    assert endpoint.frozen is not None
    endpoint = replace(
        endpoint,
        frozen=replace(endpoint.frozen, definition=NamedCalendarData("named", "digest", "month")),
    )
    attachment = directory(endpoint, ("2026-08-01",))
    attachment.rows["nominal_label"] = pd.Series([label], dtype="string[pyarrow]")
    with pytest.raises(CoreRuleError, match="canonical frozen"):
        read(attachment, endpoint)


def test_date_key_uses_exact_physical_days_outside_python_date_range() -> None:
    endpoint = axis()
    endpoint = replace(
        endpoint, columns=(replace(endpoint.columns[0], dtype=ScalarDataType("date")),)
    )
    attachment = directory(axis(), ("2026-08-01",))
    attachment.rows[endpoint.columns[0].slot_id] = pd.Series(
        pd.arrays.ArrowExtensionArray(pa.array([-(2**31)], type=pa.int32()).cast(pa.date32()))
    )
    frozen = read_calendar_directory(endpoint, calendar_directory_schema(endpoint), attachment.rows)
    assert frozen.buckets[0].key == ((ScalarDataType("date"), -(2**31)),)
    restored = read_calendar_directory(
        endpoint, calendar_directory_schema(endpoint), calendar_directory_rows(frozen)
    )
    assert restored == frozen


def test_empty_directory_and_primary_domain_are_valid_without_synthetic_rows() -> None:
    endpoint = axis()
    attachment = directory(endpoint, ())
    frozen = read(attachment, endpoint)
    require_matchable_calendar_directory(frozen)
    assert build_partial_calendar_map(frozen, frozen, CalendarCorrespondence("ordinal")) == ()
    validate_frozen_coordinates(
        DataSchema((endpoint,), ()), attachment.rows.iloc[:, :1], (attachment,)
    )
    invalid = pd.DataFrame({endpoint.columns[0].slot_id: pd.Series([0], dtype="int64[pyarrow]")})
    with pytest.raises(CoreRuleError, match="resolving"):
        validate_frozen_coordinates(DataSchema((endpoint,), ()), invalid, (attachment,))


def test_required_attachment_set_and_ownership_reject_ambiguity() -> None:
    endpoint = axis()
    attachment = directory(endpoint, ("2026-08-01",))
    schema, rows = DataSchema((endpoint,), ()), attachment.rows.iloc[:, :1]
    for attachments in ((), (attachment, attachment), (replace(attachment, name="wrong"),)):
        with pytest.raises(CoreRuleError, match="required unique"):
            validate_frozen_coordinates(schema, rows, attachments)
    assert endpoint.frozen is not None
    other = replace(
        endpoint, token="other", columns=(replace(endpoint.columns[0], slot_id="other.key"),)
    )
    with pytest.raises(CoreRuleError, match="one role and exact descriptor"):
        coordinate_attachment_schemas(DataSchema((endpoint, other), ()))


@pytest.mark.parametrize(
    ("left_values", "right_values", "pair_values", "message"),
    [
        ((0, 0), (0, None), (5, 6), "one-to-one endpoint"),
        ((0, None), (0, 0), (5, 6), "one-to-one endpoint"),
        ((0, 1), (0, None), (5, 6), "resolving"),
        ((0, None), (0, None), (5, 6), "both absent"),
        ((0, None), (None, 0), (5, 5), "unique complete paired"),
    ],
)
def test_paired_map_rejects_complete_unused_corrupt_rows(
    left_values: tuple[int | None, ...],
    right_values: tuple[int | None, ...],
    pair_values: tuple[int, ...],
    message: str,
) -> None:
    left, right = axis("left"), axis("right")
    paired = PairedDataAxis(
        "pair",
        (DataColumn("pair.key", "pair", ScalarDataType("int64"), False),),
        left,
        right,
        "mapping",
        name="period",
    )
    a, b = directory(left, ("2026-08-01",)), directory(right, ("2026-07-01",))
    rows = pd.DataFrame(
        {
            name: pd.Series(values, dtype="int64[pyarrow]")
            for name, values in (
                ("pair.key", pair_values),
                ("left.0", left_values),
                ("right.0", right_values),
            )
        }
    )
    mapping = FrozenAttachment("mapping", paired_mapping_schema(paired), rows)
    empty = pd.DataFrame({"pair.key": pd.Series([], dtype="int64[pyarrow]")})
    with pytest.raises(CoreRuleError, match=message):
        validate_frozen_coordinates(DataSchema((paired,), ()), empty, (a, b, mapping))


def test_composite_endpoint_null_group_is_all_or_none() -> None:
    left, right = axis("left"), axis("right")
    left = replace(
        left,
        columns=(*left.columns, DataColumn("left.extra", "part", ScalarDataType("string"), False)),
    )
    right = replace(
        right,
        columns=(
            *right.columns,
            DataColumn("right.extra", "part", ScalarDataType("string"), False),
        ),
    )
    a, b = directory(axis("left"), ("2026-08-01",)), directory(axis("right"), ("2026-07-01",))
    a.rows["left.extra"] = pd.Series(["A"], dtype="string[pyarrow]")
    b.rows["right.extra"] = pd.Series(["A"], dtype="string[pyarrow]")
    # Put each complete composite key before values, matching the literal schema.
    a = replace(
        a,
        schema=calendar_directory_schema(left),
        rows=a.rows.loc[:, ["key.left", "left.extra", *a.rows.columns[1:-1]]],
    )
    b = replace(
        b,
        schema=calendar_directory_schema(right),
        rows=b.rows.loc[:, ["key.right", "right.extra", *b.rows.columns[1:-1]]],
    )
    paired = PairedDataAxis(
        "pair",
        (DataColumn("pair.key", "pair", ScalarDataType("int64"), False),),
        left,
        right,
        "map",
        name="period",
    )
    rows = pd.DataFrame(
        {
            "pair.key": pd.Series([1], dtype="int64[pyarrow]"),
            "left.0": pd.Series([0], dtype="int64[pyarrow]"),
            "left.1": pd.Series([None], dtype="string[pyarrow]"),
            "right.0": pd.Series([0], dtype="int64[pyarrow]"),
            "right.1": pd.Series(["A"], dtype="string[pyarrow]"),
        }
    )
    with pytest.raises(CoreRuleError, match="all-or-none"):
        validate_paired_mapping(
            paired, paired_mapping_schema(paired), rows, read(a, left), read(b, right)
        )


def test_exact_calendar_descriptor_reuse_retains_one_attachment_owner() -> None:
    left, right, another = axis("left"), axis("right"), axis("another")
    first = PairedDataAxis(
        "pair.0",
        (DataColumn("key.0", "pair", ScalarDataType("int64"), False),),
        left,
        right,
        "mapping.0",
        name="period",
    )
    second = PairedDataAxis(
        "pair.1",
        (DataColumn("key.1", "pair", ScalarDataType("int64"), False),),
        left,
        another,
        "mapping.1",
        name="other",
    )
    assert tuple(
        name for name, _ in coordinate_attachment_schemas(DataSchema((first, second), ()))
    ) == ("calendar.left", "calendar.right", "mapping.0", "calendar.another", "mapping.1")


def test_paired_row_presence_only_implies_a_directory_endpoint_in_one_direction() -> None:
    left, right = axis("left"), axis("right")
    paired = PairedDataAxis(
        "pair",
        (DataColumn("pair.key", "pair", ScalarDataType("int64"), False),),
        left,
        right,
        "map",
        name="period",
    )
    a, b = directory(left, ("2026-08-01",)), directory(right, ())
    mapping = FrozenAttachment(
        "map",
        paired_mapping_schema(paired),
        pd.DataFrame(
            {
                "pair.key": pd.Series([0], dtype="int64[pyarrow]"),
                "left.0": pd.Series([0], dtype="int64[pyarrow]"),
                "right.0": pd.Series([None], dtype="int64[pyarrow]"),
            }
        ),
    )
    presence = DataColumn("present", "right_present", ScalarDataType("boolean"), False)
    schema = DataSchema((paired,), (), (presence,), presence_paths=(("right_present",),))
    rows = pd.DataFrame(
        {
            "pair.key": pd.Series([0], dtype="int64[pyarrow]"),
            "present": pd.Series([False], dtype="bool[pyarrow]"),
        }
    )
    validate_frozen_coordinates(schema, rows, (a, b, mapping))
    rows.loc[0, "present"] = True
    with pytest.raises(CoreRuleError, match="presence consistent"):
        validate_frozen_coordinates(schema, rows, (a, b, mapping))
