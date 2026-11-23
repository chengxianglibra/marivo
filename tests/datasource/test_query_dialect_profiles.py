"""Dialect declarations and direction-independent placement of special scores."""

from pathlib import Path

import ibis
import pytest

from marivo.datasource.spi import query_dialect
from tests.datasource.source_cases import SourceData, source_case, source_params

PROFILES = (
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
)


@pytest.mark.parametrize(
    ("provider", "conditional_style", "grouping_sets"),
    [
        ("duckdb", "filter_where", True),
        ("postgres", "filter_where", True),
        ("mysql", "case_when", False),
        ("trino", "filter_where", True),
        ("clickhouse", "clickhouse_if", True),
    ],
)
def test_query_dialect_capabilities(
    provider: str, conditional_style: str, grouping_sets: bool
) -> None:
    profile = query_dialect(provider)
    assert profile.conditional_aggregate_style == conditional_style
    assert profile.supports_sql_window_frames
    assert profile.supports_grouping_sets is grouping_sets
    assert profile.float_order_keys is not None


def test_unowned_dialect_does_not_admit_special_sql() -> None:
    profile = query_dialect(None)
    assert profile.conditional_aggregate_style == "none"
    assert not profile.supports_sql_window_frames
    assert not profile.window_frame_nulls_safe
    assert not profile.supports_grouping_sets
    assert profile.float_order_keys is None


def _scores(provider: str) -> SourceData:
    dtype = "DOUBLE PRECISION" if provider == "postgres" else "DOUBLE"
    regular = "(4, 0.0), (3, 1.0), (2, -1.0), (1, 1.0), (9, NULL)"
    if provider == "mysql":
        return SourceData(f"id BIGINT, score {dtype}", regular, "", [])
    if provider == "trino":
        special = "(6, infinity()), (5, -infinity()), (8, nan()), (7, nan())"
    else:
        special = ", ".join(
            f"({key}, CAST('{value}' AS {dtype}))"
            for key, value in ((6, "Infinity"), (5, "-Infinity"), (8, "NaN"), (7, "NaN"))
        )
    return SourceData(
        f"id BIGINT, score {dtype}",
        f"{regular}, {special}",
        "id Int64, score Nullable(Float64)",
        [
            {"id": key, "score": value}
            for key, value in (
                (4, 0.0),
                (3, 1.0),
                (2, -1.0),
                (1, 1.0),
                (9, None),
                (6, float("inf")),
                (5, float("-inf")),
                (8, float("nan")),
                (7, float("nan")),
            )
        ],
    )


@pytest.mark.parametrize("provider,source_profile", source_params(PROFILES))
def test_float_order_keeps_special_values_last_and_breaks_ties_by_key(
    provider: str, source_profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = query_dialect(provider)
    sort_keys = profile.float_order_keys
    assert sort_keys is not None
    expected = (
        ([2, 4, 1, 3, 9], [1, 3, 4, 2, 9])
        if provider == "mysql"
        else ([5, 2, 4, 1, 3, 6, 7, 8, 9], [6, 1, 3, 4, 2, 5, 7, 8, 9])
    )
    with source_case(provider, source_profile, tmp_path, monkeypatch, _scores(provider)) as case:
        source = case.session.bind(case.source, source_identity="float-order").relation
        for descending, ids in zip((False, True), expected, strict=True):
            ordered = source.order_by(*sort_keys(source.score, descending), source.id)
            result = case.session.collect_bounded(
                ordered.select("id"), purpose="query.float_order", max_rows=len(ids)
            )
            assert result["id"].tolist() == ids

        if profile.window_frame_nulls_safe:
            # The first row has no preceding rows. The second precedes a
            # wholly NULL frame. A later row sees one valid number.
            sample = (source.id == 2).ifelse(source.score, ibis.null().cast("float64"))
            window = ibis.rows_window(preceding=(2, 1), order_by=source.id)
            summary = source.select("id", total=sample.sum().over(window))
            summary = summary.mutate(missing=summary.total.isnull())
            result = case.session.collect_bounded(
                summary.order_by("id").limit(3), purpose="query.window_nulls", max_rows=3
            )
            assert result["id"].tolist() == [1, 2, 3]
            assert result["missing"].tolist() == [True, True, False]
            assert result["total"].iloc[2] == -1.0
