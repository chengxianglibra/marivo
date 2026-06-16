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

from marivo.datasource.ir import CsvSourceIR, EntitySourceIR, ParquetSourceIR, TableSourceIR
from marivo.render import format_bounded_card, result_repr

ScanPartition = Mapping[str, str] | Literal["latest"] | None
PartitionResolution = Literal["explicit", "latest", "none", "unpruned"]


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


@dataclass(frozen=True)
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
    source: EntitySourceIR
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
    """One side of a join-key probe.

    Attributes:
        datasource: Datasource short name.
        source: Physical source on this side of the join.
        columns: Column names participating in the join key.
    """

    datasource: str
    source: EntitySourceIR
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
        columns=tuple(columns) if columns is not None else None,
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
        columns=tuple(columns) if columns is not None else None,
    )
