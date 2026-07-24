"""Shared bounded preview DTOs and normalization helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from zoneinfo import ZoneInfo

import pandas as pd

from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from marivo.datasource.source import AuthoringScope

PreviewKind = Literal[
    "datasource_table",
    "semantic_dataset",
    "semantic_dimension",
    "semantic_measure",
    "semantic_field",
    "semantic_metric",
    "semantic_event",
    "analysis_frame",
]

PreviewWarningKind = Literal[
    "wide_table",
    "null_heavy_column",
    "constant_column",
    "time_parse_risk",
    "empty_preview",
    "backend_limit_unknown",
    "approximate_preview",
]

PreviewSampleMethod = Literal["head", "bounded_limit", "ordered_limit", "pre_aggregate_limit"]

PREVIEW_DEFAULT_LIMIT = 20
PREVIEW_MAX_LIMIT = 100
METRIC_PREVIEW_SAMPLE_SIZE = 10_000
_PREVIEW_MIN_LIMIT = 1
_WIDE_TABLE_THRESHOLD = 50


class PreviewFilter(TypedDict, total=False):
    column: str
    op: Literal["=", "!=", "<", "<=", ">", ">=", "in", "is_null", "is_not_null"]
    value: object


class PreviewOrder(TypedDict, total=False):
    column: str
    direction: Literal["asc", "desc"]


PreviewTimezoneInfo = dict[str, str | None]


class PreviewLimitError(ValueError):
    """Raised when a preview limit is outside the public contract."""

    def __init__(self, limit: int, *, min_limit: int, max_limit: int) -> None:
        super().__init__(f"preview limit must be between {min_limit} and {max_limit}")
        self.limit = limit
        self.min_limit = min_limit
        self.max_limit = max_limit


@dataclass(frozen=True)
class PreviewWarning:
    kind: PreviewWarningKind
    message: str
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreviewSamplePolicy:
    method: PreviewSampleMethod
    limit: int
    order_by: tuple[str, ...] = ()
    filters: tuple[PreviewFilter, ...] = ()


@dataclass(frozen=True)
class PreviewCoverage:
    scopes: tuple[tuple[str, AuthoringScope], ...]
    rows_observed: int
    scope_exhaustion: Literal["exhaustive", "truncated"]
    scope_exactness: Literal["scope_exact", "sample_only"]
    snapshot_ids: tuple[str, ...]
    cache_status: Literal["fresh", "cached", "stale"]


@dataclass(frozen=True, repr=False)
class PreviewResult(RenderableResult):
    kind: PreviewKind
    ref: str
    columns: tuple[str, ...]
    types: dict[str, str]
    rows: tuple[dict[str, object], ...]
    requested_limit: int
    returned_row_count: int
    is_truncated: bool
    status: Literal["passed"]
    coverage: PreviewCoverage
    warnings: tuple[PreviewWarning, ...] = field(default_factory=tuple)
    timezones: dict[str, PreviewTimezoneInfo] = field(default_factory=dict)
    sample_policy: PreviewSamplePolicy = field(
        default_factory=lambda: PreviewSamplePolicy(
            method="bounded_limit",
            limit=PREVIEW_DEFAULT_LIMIT,
        )
    )

    def _repr_identity(self) -> str:
        return (
            f"PreviewResult kind={self.kind} ref={self.ref} "
            f"rows={self.returned_row_count}/{self.requested_limit}"
        )

    def _card(self) -> Card:
        preview_rows = [tuple(str(row.get(col, "")) for col in self.columns) for row in self.rows]
        status_parts = [
            f"status={self.status}",
            f"truncated={self.is_truncated}",
            f"coverage={self.coverage.scope_exhaustion}/{self.coverage.scope_exactness}",
        ]
        if self.timezones:
            labels = [
                f"{column}:read_tz={info.get('read_tz')} report_tz={info.get('report_tz')}"
                for column, info in sorted(self.timezones.items())
            ]
            status_parts.append("; ".join(labels))
        return (
            Card(identity=self._repr_identity(), available=(".render()", ".show()"))
            .status(" ".join(status_parts))
            .table(
                columns=list(self.columns),
                rows=preview_rows,
                row_count=self.returned_row_count,
            )
        )


def validate_preview_limit(
    limit: int,
    *,
    min_limit: int = _PREVIEW_MIN_LIMIT,
    max_limit: int = PREVIEW_MAX_LIMIT,
) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise PreviewLimitError(limit, min_limit=min_limit, max_limit=max_limit)
    if limit < min_limit or limit > max_limit:
        raise PreviewLimitError(limit, min_limit=min_limit, max_limit=max_limit)
    return limit


def display_column_names(columns: Iterable[Any]) -> tuple[str, ...]:
    display_columns: list[str] = []
    used_columns: set[str] = set()
    for column in columns:
        column_name = str(column)
        display_name = column_name
        suffix = 2
        while display_name in used_columns:
            display_name = f"{column_name}#{suffix}"
            suffix += 1
        used_columns.add(display_name)
        display_columns.append(display_name)
    return tuple(display_columns)


def _is_missing(value: Any) -> bool:
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, bool):
        return missing
    item = getattr(missing, "item", None)
    if callable(item):
        try:
            scalar = item()
        except (TypeError, ValueError):
            return False
        return scalar if isinstance(scalar, bool) else False
    return False


def normalize_preview_cell(value: Any, *, report_tz: str | None = None) -> object:
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None and report_tz is not None:
            return value.tz_convert(ZoneInfo(report_tz)).tz_localize(None).isoformat()
        return value.isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            return value
    return value


def _types_from_dataframe(
    dataframe: pd.DataFrame,
    display_columns: Sequence[str],
    explicit_types: Mapping[str, str] | None,
) -> dict[str, str]:
    if explicit_types is not None:
        return {str(key): str(value) for key, value in explicit_types.items()}
    return {
        display_column: str(dtype)
        for display_column, dtype in zip(display_columns, dataframe.dtypes, strict=True)
    }


def preview_from_pandas(
    dataframe: pd.DataFrame,
    *,
    kind: PreviewKind,
    ref: str,
    requested_limit: int,
    sample_policy: PreviewSamplePolicy,
    types: Mapping[str, str] | None = None,
    timezones: Mapping[str, PreviewTimezoneInfo] | None = None,
    report_tz: str | None = None,
    warnings: Iterable[PreviewWarning] = (),
) -> PreviewResult:
    limit = validate_preview_limit(requested_limit)
    display_columns = display_column_names(dataframe.columns)
    source = dataframe.head(limit)
    rows: list[dict[str, object]] = []

    for row in source.itertuples(index=False, name=None):
        out_row: dict[str, object] = {}
        for column, value in zip(display_columns, row, strict=True):
            out_row[column] = normalize_preview_cell(value, report_tz=report_tz)
        rows.append(out_row)

    result_warnings = list(warnings)
    if len(display_columns) > _WIDE_TABLE_THRESHOLD:
        result_warnings.append(
            PreviewWarning(
                kind="wide_table",
                message=f"preview has {len(display_columns)} columns",
                columns=display_columns,
            )
        )
    if not rows:
        result_warnings.append(
            PreviewWarning(kind="empty_preview", message="preview returned no rows")
        )

    return PreviewResult(
        kind=kind,
        ref=ref,
        columns=display_columns,
        types=_types_from_dataframe(dataframe, display_columns, types),
        rows=tuple(rows),
        requested_limit=limit,
        returned_row_count=len(rows),
        is_truncated=len(dataframe) > limit,
        status="passed",
        coverage=PreviewCoverage(
            scopes=(),
            rows_observed=len(rows),
            scope_exhaustion=("truncated" if len(dataframe) > limit else "exhaustive"),
            scope_exactness=("sample_only" if len(dataframe) > limit else "scope_exact"),
            snapshot_ids=(),
            cache_status="fresh",
        ),
        warnings=tuple(result_warnings),
        sample_policy=sample_policy,
        timezones=dict(timezones or {}),
    )


def preview_ibis_table(
    table: Any,
    *,
    kind: PreviewKind,
    ref: str,
    limit: int,
    sample_policy: PreviewSamplePolicy,
    include_types: bool = True,
    timezones: Mapping[str, PreviewTimezoneInfo] | None = None,
    report_tz: str | None = None,
) -> PreviewResult:
    limit = validate_preview_limit(limit)
    dataframe = table.limit(limit + 1).execute()
    schema_types = (
        {name: str(dtype) for name, dtype in table.schema().items()} if include_types else {}
    )
    return preview_from_pandas(
        dataframe,
        kind=kind,
        ref=ref,
        requested_limit=limit,
        sample_policy=sample_policy,
        types=schema_types,
        timezones=timezones,
        report_tz=report_tz,
    )


def preview_ibis_value(
    value: Any,
    *,
    kind: PreviewKind,
    ref: str,
    limit: int,
    column_name: str,
    sample_policy: PreviewSamplePolicy,
    include_types: bool = True,
    timezones: Mapping[str, PreviewTimezoneInfo] | None = None,
    report_tz: str | None = None,
) -> PreviewResult:
    named_value = value.name(column_name) if callable(getattr(value, "name", None)) else value
    table = named_value.as_table()
    return preview_ibis_table(
        table,
        kind=kind,
        ref=ref,
        limit=limit,
        sample_policy=sample_policy,
        include_types=include_types,
        timezones=timezones,
        report_tz=report_tz,
    )
