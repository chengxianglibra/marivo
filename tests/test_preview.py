"""Tests for shared preview DTOs and normalization helpers."""

from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from marivo.preview import (
    PreviewLimitError,
    PreviewSamplePolicy,
    display_column_names,
    normalize_preview_cell,
    preview_from_pandas,
    validate_preview_limit,
)


def test_validate_preview_limit_accepts_bounds() -> None:
    assert validate_preview_limit(1) == 1
    assert validate_preview_limit(100) == 100


def test_validate_preview_limit_rejects_invalid_values() -> None:
    with pytest.raises(PreviewLimitError) as low:
        validate_preview_limit(0)
    assert low.value.limit == 0
    assert low.value.min_limit == 1
    assert low.value.max_limit == 100

    with pytest.raises(PreviewLimitError) as high:
        validate_preview_limit(101)
    assert high.value.limit == 101


def test_display_column_names_disambiguates_duplicates() -> None:
    columns = display_column_names(["value", "value", "value#2"])
    assert columns == ("value", "value#2", "value#2#2")


def test_normalize_preview_cell_json_safe_scalars() -> None:
    assert normalize_preview_cell(float("nan")) is None
    assert normalize_preview_cell(pd.NA) is None
    assert normalize_preview_cell(pd.Timestamp("2026-05-29T10:11:12")) == "2026-05-29T10:11:12"
    assert normalize_preview_cell(datetime(2026, 5, 29, 10, 11, 12)) == "2026-05-29T10:11:12"
    assert normalize_preview_cell(date(2026, 5, 29)) == "2026-05-29"
    assert normalize_preview_cell(time(10, 11, 12)) == "10:11:12"
    assert normalize_preview_cell(pd.Timedelta(days=1, seconds=2)) == "1 days 00:00:02"


def test_preview_from_pandas_bounds_rows_and_reports_truncation() -> None:
    df = pd.DataFrame({"id": [1, 2, 3], "amount": [10.0, 20.0, 30.0]})
    preview = preview_from_pandas(
        df,
        kind="datasource_table",
        ref="warehouse.orders",
        requested_limit=2,
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=2),
        types={"id": "int64", "amount": "float64"},
    )
    assert preview.kind == "datasource_table"
    assert preview.ref == "warehouse.orders"
    assert preview.columns == ("id", "amount")
    assert preview.types == {"id": "int64", "amount": "float64"}
    assert preview.rows == ({"id": 1, "amount": 10.0}, {"id": 2, "amount": 20.0})
    assert preview.requested_limit == 2
    assert preview.returned_row_count == 2
    assert preview.is_truncated is True
    assert preview.status == "passed"
    assert preview.coverage.rows_observed == 2
    assert preview.coverage.scope_exhaustion == "truncated"
    assert preview.coverage.scope_exactness == "sample_only"
    assert preview.coverage.snapshot_ids == ()


def test_preview_result_carries_timezones_in_render() -> None:
    df = pd.DataFrame({"created_at": [pd.Timestamp("2026-06-17T08:00:00Z")]})
    preview = preview_from_pandas(
        df,
        kind="semantic_field",
        ref="sales.orders.created_at",
        requested_limit=1,
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=1),
        types={"created_at": "timestamp('UTC')"},
        timezones={
            "created_at": {
                "kind": "instant",
                "read_tz": None,
                "report_tz": "Asia/Shanghai",
                "read_tz_resolution": None,
            }
        },
        report_tz="Asia/Shanghai",
    )

    assert preview.timezones["created_at"]["report_tz"] == "Asia/Shanghai"
    assert preview.rows == ({"created_at": "2026-06-17T16:00:00"},)
    rendered = preview.render()
    assert "created_at" in rendered
    assert "report_tz=Asia/Shanghai" in rendered


def test_preview_from_pandas_warns_on_empty_preview() -> None:
    df = pd.DataFrame(columns=["id"])
    preview = preview_from_pandas(
        df,
        kind="semantic_dataset",
        ref="sales.orders",
        requested_limit=20,
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=20),
        types={"id": "int64"},
    )
    assert preview.rows == ()
    assert preview.returned_row_count == 0
    assert preview.is_truncated is False
    assert [warning.kind for warning in preview.warnings] == ["empty_preview"]
    assert preview.coverage.rows_observed == 0
    assert preview.coverage.scope_exhaustion == "exhaustive"
    assert preview.coverage.scope_exactness == "scope_exact"
