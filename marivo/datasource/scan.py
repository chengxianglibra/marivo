"""Scan DTOs and source constructors for marivo.datasource.

Provides agent-safe scan configuration (ScanScope), scan result reporting
(ScanReport), column profiling (ColumnProfile, ColumnInspection), and
join-key probing (JoinSide, JoinKeyProbe) DTOs, plus the canonical
table/file source constructors that the semantic authoring surface reuses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from marivo.datasource.ir import FileSourceIR, TableSourceIR

ScanPartition = Mapping[str, str] | Literal["latest"] | None
PartitionResolution = Literal["explicit", "latest", "none", "unpruned"]


@dataclass(frozen=True)
class ScanScope:
    """Agent-safe scan configuration with bounded defaults.

    Defaults are chosen to keep automated scans safe and fast: at most
    1000 rows, 50 columns, and a 30-second timeout.

    Attributes:
        partition: Partition filter; ``"latest"`` (default), an explicit
            mapping like ``{"dt": "20260612"}``, or ``None`` for unpruned.
        max_rows: Maximum rows returned from the scan.
        max_columns: Maximum columns returned from the scan.
        timeout_seconds: Scan timeout in seconds; ``None`` means no limit.
    """

    partition: ScanPartition = "latest"
    max_rows: int = 1000
    max_columns: int = 50
    timeout_seconds: int | None = 30

    def __post_init__(self) -> None:
        if self.max_rows < 1:
            raise ValueError("ScanScope.max_rows must be positive.")
        if self.max_columns < 1:
            raise ValueError("ScanScope.max_columns must be positive.")
        if self.timeout_seconds is not None and self.timeout_seconds < 1:
            raise ValueError("ScanScope.timeout_seconds must be positive when provided.")


@dataclass(frozen=True)
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

    def __repr__(self) -> str:
        return (
            f"<ScanReport rows={self.rows_scanned} "
            f"columns={len(self.columns_scanned)} "
            f"partition={self.partition_resolution}>"
        )

    def render(self) -> str:
        """Render a human-readable summary of the scan result."""
        partition = (
            "none"
            if self.partition_used is None
            else ", ".join(f"{key}={value}" for key, value in self.partition_used.items())
        )
        warnings = "none" if not self.warnings else "; ".join(self.warnings[:3])
        return (
            f"ScanReport rows={self.rows_scanned} "
            f"columns={len(self.columns_scanned)} "
            f"partition={self.partition_resolution} ({partition}) "
            f"truncated={self.truncated} warnings={warnings}"
        )

    def show(self) -> None:
        """Print the rendered scan report to stdout."""
        print(self.render())


@dataclass(frozen=True)
class ColumnProfile:
    """Statistical profile of a single column from a datasource scan.

    Attributes:
        column: Column name.
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

    column: str
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


@dataclass(frozen=True)
class ColumnInspection:
    """Column inspection result for a single datasource source.

    Attributes:
        datasource: Datasource short name.
        source: Physical source that was inspected.
        profiles: Column profiles in scan order.
        scan: The scan report for this inspection.
    """

    datasource: str
    source: TableSourceIR | FileSourceIR
    profiles: tuple[ColumnProfile, ...]
    scan: ScanReport


@dataclass(frozen=True)
class JoinSide:
    """One side of a join-key probe.

    Attributes:
        datasource: Datasource short name.
        source: Physical source on this side of the join.
        columns: Column names participating in the join key.
    """

    datasource: str
    source: TableSourceIR | FileSourceIR
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


def file(
    path: str,
    /,
    *,
    format: Literal["parquet", "csv"],
    **options: object,
) -> FileSourceIR:
    """Build a structured file source reference.

    Args:
        path: File path or glob pattern.
        format: File format; must be ``"parquet"`` or ``"csv"``.
        **options: Additional format-specific options (e.g. ``delimiter=","``
            for CSV).

    Returns:
        A ``FileSourceIR`` suitable for ``ms.entity(source=...)`` or
        ``md.entity(source=...)``.

    Raises:
        ValueError: If ``format`` is not ``"parquet"`` or ``"csv"``.
    """
    if format not in ("parquet", "csv"):
        raise ValueError("md.file(format=...) format must be 'parquet' or 'csv'.")
    return FileSourceIR(path=path, format=format, options=dict(options))
