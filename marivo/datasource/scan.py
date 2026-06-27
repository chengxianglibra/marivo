"""Scan DTOs and source constructors for marivo.datasource.

Provides agent-safe scan configuration (ScanScope), scan result reporting
(ScanReport), column profiling (ColumnProfile, ColumnInspection), and
join-key probing (JoinSide, JoinKeyProbe) DTOs, plus the canonical
table/parquet/csv source constructors that the semantic authoring surface reuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.ir import CsvSourceIR, ParquetSourceIR, TableSourceIR
from marivo.render import format_bounded_card, result_repr

ScanPartition = Mapping[str, str] | Literal["latest"] | None
PartitionResolution = Literal["explicit", "latest", "none", "unpruned"]

# Public datasource-side alias for physical source values returned by
# md.table(...), md.parquet(...), and md.csv(...). Named TableSource to avoid
# colliding with the semantic authoring DatasetSource alias. Exported in the
# public md surface in a later plan; defined here so discovery types can use it.
TableSource = TableSourceIR | ParquetSourceIR | CsvSourceIR


@dataclass(frozen=True)
class ScanScope:
    """Agent-safe scan configuration with bounded defaults.

    Defaults are chosen to keep automated scans safe and fast: at most
    1000 rows, 100 columns, and a 30-second timeout.

    Attributes:
        partition: Partition filter; ``"latest"`` (default), an explicit
            mapping like ``{"dt": "20260612"}``, or ``None`` for unpruned.
        max_rows: Maximum rows returned from the scan.
        max_columns: Maximum columns returned from the scan.
        timeout_seconds: Scan timeout in seconds; ``None`` means no limit.
    """

    partition: ScanPartition = "latest"
    max_rows: int = 1000
    max_columns: int = 100
    timeout_seconds: int | None = 30

    def __post_init__(self) -> None:
        if self.max_rows < 1:
            raise ValueError("ScanScope.max_rows must be positive.")
        if self.max_columns < 1:
            raise ValueError("ScanScope.max_columns must be positive.")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("ScanScope.timeout_seconds must be positive when provided.")


@dataclass(frozen=True, repr=False)
class ScanReport:
    """Summary of a completed datasource scan.

    Attributes:
        partition_used: The partition that was actually used, or ``None``.
        partition_resolution: How the partition was resolved.
        rows_scanned: Number of rows observed.
        columns_scanned: Column names in scan order.
        truncated: Whether the result was truncated by ScanScope limits.
        elapsed_seconds: Wall-clock scan duration.
        warnings: Non-fatal warnings encountered during the scan.
    """

    partition_used: Mapping[str, str] | None
    partition_resolution: PartitionResolution
    rows_scanned: int
    columns_scanned: tuple[str, ...]
    truncated: bool
    elapsed_seconds: float
    warnings: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"ScanReport rows={self.rows_scanned} "
            f"columns={len(self.columns_scanned)} "
            f"partition={self.partition_resolution}"
        )

    def render(self) -> str:
        partition = (
            "none"
            if self.partition_used is None
            else ", ".join(f"{key}={value}" for key, value in self.partition_used.items())
        )
        warnings = "none" if not self.warnings else "; ".join(self.warnings[:3])
        return format_bounded_card(
            identity=self._repr_identity(),
            status=f"partition={partition} truncated={self.truncated} warnings={warnings}",
            columns=list(self.columns_scanned),
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


def _format_profile_value(value: object | None) -> str:
    if value is None:
        return "none"
    text = str(value)
    if len(text) > 48:
        return text[:45] + "..."
    return text


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{value:.2f}"


@dataclass(frozen=True, repr=False)
class ColumnProfile:
    """Statistical profile of a single column from a datasource scan.

    Attributes:
        name: Column name.
        data_type: Resolved data type label.
        nullable: Whether the column can contain nulls, if known.
        comment: Column comment from the catalog, if any.
        null_count: Number of null values observed.
        empty_count: Number of empty string values observed.
        distinct_count: Number of distinct non-null values.
        top_values: Most frequent values with their counts.
        sample_values: Representative sample of non-null values.
        min_value: Minimum value (for orderable types).
        max_value: Maximum value (for orderable types).
        non_null_count: Number of non-null values observed.
        distinct_ratio: distinct_count / non_null_count, or ``None`` when no non-null values.
        top_value_concentration: top-value count / non_null_count, or ``None``.
        negative_count: Number of non-null numeric values below zero.
        zero_count: Number of non-null numeric values equal to zero.
        min_length: Minimum string length over non-null string values, or ``None``.
        max_length: Maximum string length over non-null string values, or ``None``.
        avg_length: Average string length over non-null string values, or ``None``.
        type_family: Coarse type family label from ``_coarse_type_family``.
    """

    name: str
    data_type: str
    nullable: bool | None
    comment: str | None
    null_count: int
    empty_count: int
    distinct_count: int
    top_values: tuple[tuple[object, int], ...]
    sample_values: tuple[object, ...]
    min_value: object | None
    max_value: object | None
    non_null_count: int = 0
    distinct_ratio: float | None = None
    top_value_concentration: float | None = None
    negative_count: int = 0
    zero_count: int = 0
    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    type_family: str = "unknown"

    def _repr_identity(self) -> str:
        return f"ColumnProfile column={self.name} type={self.data_type} family={self.type_family}"

    def _fact_rows(self) -> list[list[str]]:
        rows: list[list[str]] = [
            ["comment", _format_profile_value(self.comment)],
            [
                "range",
                f"{_format_profile_value(self.min_value)}..{_format_profile_value(self.max_value)}",
            ],
            [
                "top_values",
                ", ".join(
                    f"{_format_profile_value(value)}:{count}"
                    for value, count in self.top_values[:3]
                )
                or "none",
            ],
            [
                "sample_values",
                ", ".join(_format_profile_value(value) for value in self.sample_values[:3])
                or "none",
            ],
        ]
        if self.distinct_ratio is not None:
            rows.append(["distinct_ratio", _format_ratio(self.distinct_ratio)])
        if self.top_value_concentration is not None:
            rows.append(["top_value_concentration", _format_ratio(self.top_value_concentration)])
        if self.negative_count or self.zero_count:
            rows.append(
                ["numeric_counts", f"negative={self.negative_count} zero={self.zero_count}"]
            )
        if (
            self.min_length is not None
            or self.max_length is not None
            or self.avg_length is not None
        ):
            rows.append(
                [
                    "length",
                    (
                        f"min={_format_profile_value(self.min_length)} "
                        f"max={_format_profile_value(self.max_length)} "
                        f"avg={_format_ratio(self.avg_length)}"
                    ),
                ]
            )
        return rows

    def render(self) -> str:
        status = (
            f"type={self.data_type} family={self.type_family} nullable={self.nullable} "
            f"nulls={self.null_count} empty={self.empty_count} "
            f"distinct={self.distinct_count} non_null={self.non_null_count}"
        )
        return format_bounded_card(
            identity=self._repr_identity(),
            status=status,
            columns=["fact", "value"],
            rows=self._fact_rows(),
            row_count=len(self._fact_rows()),
            preview_truncation_hint="inspect profile attributes for all facts",
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class ColumnInspection:
    """Column inspection result for a single datasource source.

    Attributes:
        datasource: Datasource short name.
        source: Physical source that was inspected.
        profiles: Column profiles in scan order.
        scan: The scan report for this inspection.
    """

    datasource: str
    source: TableSource
    profiles: tuple[ColumnProfile, ...]
    scan: ScanReport

    def _repr_identity(self) -> str:
        return f"ColumnInspection datasource={self.datasource} columns={len(self.profiles)}"

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            columns=[profile.name for profile in self.profiles],
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True)
class JoinSide:
    """One side of a relationship discovery join.

    Attributes:
        datasource: Datasource reference from ``md.ref("name")``.
        source: Physical source returned by ``md.table()``, ``md.parquet()``, or ``md.csv()``.
        columns: Column names participating in the join key.

    Example:
        >>> import marivo.datasource as md
        >>> md.JoinSide(md.ref("warehouse"), md.table("orders"), columns=("customer_id",))

    Constraints:
        ``datasource`` identifies the configured connection. ``source`` identifies
        the physical table or file inside that datasource.
    """

    datasource: DatasourceRef
    source: TableSource
    columns: tuple[str, ...]


@dataclass(frozen=True)
class JoinKeyProbe:
    """Result of probing join compatibility between two sources.

    Attributes:
        type_compatible: Whether the join key types are compatible.
        sampled_key_count: Number of distinct keys in the sampled range.
        matched_key_count: Number of keys present on both sides.
        match_rate: Fraction of sampled keys that matched.
        max_rows_per_key: Maximum fan-out on any single key.
        avg_rows_per_key: Average fan-out across all keys.
        cardinality_estimate: Estimated join cardinality.
        from_scan: Scan report for the left (from) side.
        to_scan: Scan report for the right (to) side.
    """

    type_compatible: bool
    sampled_key_count: int
    matched_key_count: int
    match_rate: float
    max_rows_per_key: int
    avg_rows_per_key: float
    cardinality_estimate: Literal["one_to_one", "many_to_one", "indeterminate"]
    from_scan: ScanReport
    to_scan: ScanReport


def table(name: str, /, *, database: str | tuple[str, ...] | None = None) -> TableSourceIR:
    """Build a structured table source reference.

    Args:
        name: Table name.
        database: Optional database/catalog name or tuple of namespace
            parts (e.g. ``("catalog", "schema")``).

    Returns:
        A ``TableSourceIR`` suitable for ``ms.entity(source=...)`` or
        ``md.entity(source=...)``.
    """
    return TableSourceIR(table=name, database=database)


def _normalize_columns_input(
    columns: tuple[str, ...] | list[str] | None,
    *,
    field_name: str,
) -> tuple[str, ...] | None:
    if columns is None:
        return None
    if not isinstance(columns, list | tuple):
        raise TypeError(
            f"{field_name} must be list[str] or tuple[str, ...], got {type(columns).__name__}."
        )
    return tuple(columns)


def parquet(
    path: str,
    /,
    *,
    hive_partitioning: bool = False,
    columns: tuple[str, ...] | list[str] | None = None,
) -> ParquetSourceIR:
    """Build a structured parquet source reference.

    Args:
        path: File path or glob pattern.
        hive_partitioning: Whether the parquet source uses hive partitioning.
        columns: Optional subset of columns to read.

    Returns:
        A ``ParquetSourceIR`` suitable for ``ms.entity(source=...)`` or
        ``md.entity(source=...)``.
    """
    return ParquetSourceIR(
        path=path,
        hive_partitioning=hive_partitioning,
        columns=_normalize_columns_input(columns, field_name="ParquetSourceIR.columns"),
    )


def csv(
    path: str,
    /,
    *,
    header: bool = True,
    delimiter: str = ",",
    columns: tuple[str, ...] | list[str] | None = None,
) -> CsvSourceIR:
    """Build a structured CSV source reference.

    Args:
        path: File path or glob pattern.
        header: Whether the CSV file has a header row. Defaults to True.
        delimiter: Column delimiter. Defaults to ``","``.
        columns: Optional subset of columns to read.

    Returns:
        A ``CsvSourceIR`` suitable for ``ms.entity(source=...)`` or
        ``md.entity(source=...)``.
    """
    return CsvSourceIR(
        path=path,
        header=header,
        delimiter=delimiter,
        columns=_normalize_columns_input(columns, field_name="CsvSourceIR.columns"),
    )


def latest_partition(
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> ScanScope:
    """Build a ScanScope that uses the latest available partition.

    Args:
        max_rows: Maximum rows returned from the scan.
        max_columns: Maximum columns returned from the scan.
        timeout_seconds: Scan timeout in seconds; ``None`` means no limit.

    Returns:
        A ``ScanScope`` with ``partition="latest"``.

    Example:
        >>> from marivo.datasource.scan import latest_partition
        >>> latest_partition()
        ScanScope(...)

    Constraints:
        When a source has no partition metadata, discovery resolves this to an
        unpruned scan and emits ``discovery_unpruned_scan``.
    """
    return ScanScope(
        partition="latest",
        max_rows=max_rows,
        max_columns=max_columns,
        timeout_seconds=timeout_seconds,
    )


def partition(
    values: Mapping[str, str],
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> ScanScope:
    """Build a ScanScope with an explicit concrete partition selection.

    Args:
        values: Partition column-to-value mapping (e.g. ``{"dt": "20260612"}``).
        max_rows: Maximum rows returned from the scan.
        max_columns: Maximum columns returned from the scan.
        timeout_seconds: Scan timeout in seconds; ``None`` means no limit.

    Returns:
        A ``ScanScope`` with ``partition=values``.

    Example:
        >>> from marivo.datasource.scan import partition
        >>> partition({"dt": "20260612"})
        ScanScope(...)
    """
    return ScanScope(
        partition=dict(values),
        max_rows=max_rows,
        max_columns=max_columns,
        timeout_seconds=timeout_seconds,
    )


def unpruned(
    *,
    max_rows: int = 1000,
    max_columns: int = 100,
    timeout_seconds: int | None = 30,
) -> ScanScope:
    """Build a ScanScope that explicitly disables partition pruning.

    Args:
        max_rows: Maximum rows returned from the scan.
        max_columns: Maximum columns returned from the scan.
        timeout_seconds: Scan timeout in seconds; ``None`` means no limit.

    Returns:
        A ``ScanScope`` with ``partition=None``.

    Example:
        >>> from marivo.datasource.scan import unpruned
        >>> unpruned()
        ScanScope(...)

    Constraints:
        Discovery surfaces a ``discovery_unpruned_scan`` info issue so agents
        can see that the scan was intentionally broader.
    """
    return ScanScope(
        partition=None,
        max_rows=max_rows,
        max_columns=max_columns,
        timeout_seconds=timeout_seconds,
    )


def _coarse_type_family(data_type: str) -> str:
    """Classify a backend data type label into a coarse type family.

    Order matters: ``TIMESTAMP``/``DATETIME`` are checked before ``DATE``
    because ``"DATETIME"`` contains the substring ``"DATE"``.

    Args:
        data_type: Backend data type label (e.g. ``"TIMESTAMP"``, ``"VARCHAR"``).

    Returns:
        One of ``"timestamp"``, ``"date"``, ``"boolean"``, ``"integer"``,
        ``"numeric"``, ``"string"``, or ``"unknown"``.
    """
    upper = data_type.upper()
    if "TIMESTAMP" in upper or "DATETIME" in upper:
        return "timestamp"
    if "DATE" in upper:
        return "date"
    if "BOOL" in upper:
        return "boolean"
    if "INT" in upper:
        return "integer"
    if any(token in upper for token in ("DECIMAL", "FLOAT", "DOUBLE", "NUMERIC", "REAL")):
        return "numeric"
    if any(token in upper for token in ("VARCHAR", "CHAR", "TEXT", "STRING")):
        return "string"
    return "unknown"
