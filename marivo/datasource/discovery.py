"""Discovery evidence vocabulary for the datasource discovery surface.

Defines the frozen evidence, signal, issue, evidence-subject, and result types
used by ``md.discover_*`` (wired in a later plan). Nothing here infers business
meaning; rules describe evidence shape only.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.ir import EntitySourceIR
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import (
    ColumnProfile,
    JoinSide,
    ScanReport,
)
from marivo.render import result_repr

# Public datasource-side alias for physical source values. Mirrors
# ``marivo.datasource.scan.TableSource``; re-exported here so discovery
# signatures reference one name.
TableSource = EntitySourceIR

DiscoverySeverity = Literal["blocker", "warning", "info"]
EvidenceValue = str | int | float | bool | None
DiscoveryObjectKind = Literal[
    "entity",
    "dimension",
    "time_dimension",
    "measure",
    "relationship",
]


@dataclass(frozen=True)
class DiscoveryEvidenceEntry:
    """One scalar evidence fact attached to a signal or issue.

    Attributes:
        key: Stable evidence key (e.g. ``"distinct_count"``).
        value: Scalar evidence value. Structured facts use their own typed
            fields on candidates, not this slot.
    """

    key: str
    value: EvidenceValue


@dataclass(frozen=True)
class DiscoverySignal:
    """Deterministic, evidence-backed rule signal (no severity).

    Attributes:
        rule_id: Stable rule identifier from the discovery rule catalog.
        kind: Object kind the signal applies to.
        subject: Column or table the signal is about.
        evidence: Scalar evidence entries supporting the signal.
    """

    rule_id: str
    kind: DiscoveryObjectKind
    subject: str
    evidence: tuple[DiscoveryEvidenceEntry, ...]


@dataclass(frozen=True)
class DiscoveryIssue:
    """Deterministic, evidence-backed rule issue with a severity.

    Attributes:
        rule_id: Stable rule identifier from the discovery rule catalog.
        kind: Object kind the issue applies to.
        severity: ``blocker``, ``warning``, or ``info``.
        subject: Column or table the issue is about.
        message: Human-readable description of the evidence.
        evidence: Scalar evidence entries supporting the issue.
    """

    rule_id: str
    kind: DiscoveryObjectKind
    severity: DiscoverySeverity
    subject: str
    message: str
    evidence: tuple[DiscoveryEvidenceEntry, ...]


@dataclass(frozen=True)
class TimeValueRange:
    """Typed inclusive value range for a time-like column.

    Attributes:
        lower: Minimum sampled value (string, integer, or datetime), or ``None``.
        upper: Maximum sampled value, or ``None``.
    """

    lower: str | int | datetime.datetime | None
    upper: str | int | datetime.datetime | None


@dataclass(frozen=True)
class DimensionValueFact:
    """One bounded distinct value and its count for a dimension column.

    Attributes:
        value: Scalar distinct value (runtime evidence, never persisted).
        count: Number of occurrences in the bounded sample.
    """

    value: EvidenceValue
    count: int


# ----- Evidence-subject types -----


@dataclass(frozen=True, repr=False)
class PrimaryKeyCandidate:
    """One primary-key candidate with its evidence source.

    Attributes:
        column: Candidate column name.
        source: ``"declared_primary"`` for backend-declared primary keys,
            ``"declared_unique"`` for declared unique constraints, or
            ``"sampled_unique"`` for columns whose bounded sample is fully unique.
        evidence: Scalar evidence entries supporting the candidate.
    """

    column: str
    source: Literal["declared_primary", "declared_unique", "sampled_unique"]
    evidence: tuple[DiscoveryEvidenceEntry, ...]

    def _identity(self) -> str:
        return f"PrimaryKeyCandidate column={self.column} source={self.source}"

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=f"evidence={_format_evidence_entries(self.evidence)}",
            available=(".evidence", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class FormatCandidate:
    """One supported time-parse format observed in a bounded sample.

    Attributes:
        format: strptime format string or integer-encoding label
            (e.g. ``"%Y-%m-%d"`` or ``"epoch_millis"``).
        kind: ``"string"`` for strptime formats, ``"integer"`` for integer encodings.
        matched_count: Number of sampled non-null values that matched the format.
        ambiguous: Whether the encoding matches multiple plausible time meanings.
    """

    format: str
    kind: Literal["string", "integer"]
    matched_count: int
    ambiguous: bool

    def _identity(self) -> str:
        return f"FormatCandidate format={self.format} kind={self.kind}"

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=f"matched_count={self.matched_count} ambiguous={self.ambiguous}",
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True)
class KeyTypeEvidence:
    """One side of a relationship join key with its type family.

    Attributes:
        side: ``"from"`` for the left side, ``"to"`` for the right side.
        column: Key column name.
        type_family: Coarse type family of the key column.
        data_type: Backend data type label of the key column.
    """

    side: Literal["from", "to"]
    column: str
    type_family: str
    data_type: str


@dataclass(frozen=True, repr=False)
class ColumnDiscovery:
    """Column-level evidence for a dimension or measure.

    Attributes:
        column: Column name.
        profile: Bounded column profile.
        signals: Column-scope signals for this column.
        issues: Column-scope issues for this column.
    """

    column: str
    profile: ColumnProfile
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        return f"ColumnDiscovery column={self.column}"

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=(
                f"{_format_profile_summary(self.profile)} "
                f"signals={_signal_ids(self.signals)} issues={_issue_count(self.issues)}"
            ),
            available=(".profile", ".signals", ".issues", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class TimeColumnDiscovery:
    """Column-level evidence for a time-dimension column.

    Attributes:
        column: Column name.
        profile: Bounded column profile.
        detected_formats: Supported parse candidates (populated in Plan 2).
        value_range: Typed inclusive sampled value range.
        partition_aligned: Whether the column is a metadata partition column.
        signals: Column-scope signals for this column.
        issues: Column-scope issues for this column.
    """

    column: str
    profile: ColumnProfile
    detected_formats: tuple[FormatCandidate, ...]
    value_range: TimeValueRange
    partition_aligned: bool
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        return f"TimeColumnDiscovery column={self.column}"

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=(
                f"{_format_profile_summary(self.profile)} "
                f"formats={_format_formats(self.detected_formats)} "
                f"range={_format_time_range(self.value_range)} "
                f"partition_aligned={self.partition_aligned} "
                f"signals={_signal_ids(self.signals)} issues={_issue_count(self.issues)}"
            ),
            available=(
                ".profile",
                ".detected_formats",
                ".value_range",
                ".signals",
                ".issues",
                ".render()",
                ".show()",
            ),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class RelationshipDiscoveryEvidence:
    """Relationship-level evidence from a bounded join-key probe.

    Attributes:
        from_side: Left side of the join.
        to_side: Right side of the join.
        key_type_evidence: Left/right key type families (populated in Plan 2).
        sampled_key_count: Distinct keys sampled from the from-side.
        matched_key_count: Sampled keys present on the to-side.
        match_rate: matched_key_count / sampled_key_count.
        max_rows_per_key: Maximum fan-out on any single key.
        avg_rows_per_key: Average fan-out across sampled keys.
        cardinality_evidence: one_to_one, many_to_one, or indeterminate.
        from_scan: Scan report for the from-side.
        to_scan: Scan report for the to-side.
        signals: Candidate-scope signals for this relationship.
        issues: Candidate-scope issues for this relationship.
    """

    from_side: JoinSide
    to_side: JoinSide
    key_type_evidence: tuple[KeyTypeEvidence, ...]
    sampled_key_count: int
    matched_key_count: int
    match_rate: float
    max_rows_per_key: int
    avg_rows_per_key: float
    cardinality_evidence: Literal["one_to_one", "many_to_one", "indeterminate"]
    from_scan: ScanReport
    to_scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        from_cols = ",".join(self.from_side.columns)
        to_cols = ",".join(self.to_side.columns)
        return f"RelationshipDiscoveryEvidence from={from_cols} to={to_cols}"

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("side", "column", "type_family", "data_type")
        rows = tuple(
            (entry.side, entry.column, entry.type_family, entry.data_type)
            for entry in self.key_type_evidence
        )
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        return _format_discovery_card(
            identity=self._identity(),
            status=(
                f"sampled_keys={self.sampled_key_count} matched={self.matched_key_count} "
                f"match_rate={self.match_rate:.2f} max_rows_per_key={self.max_rows_per_key} "
                f"avg_rows_per_key={self.avg_rows_per_key:.2f} "
                f"cardinality={self.cardinality_evidence}"
            ),
            table_header=header,
            table_rows=rows,
            available=(
                ".key_type_evidence",
                ".from_scan",
                ".to_scan",
                ".signals",
                ".issues",
                ".render()",
                ".show()",
            ),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


# ----- Shared card formatter -----


_MAX_TABLE_ROWS = 8
_MAX_INLINE_ITEMS = 3


def _format_scalar(value: object | None) -> str:
    if value is None:
        return "none"
    text = str(value)
    if len(text) > 40:
        return text[:37] + "..."
    return text


def _format_evidence_entries(entries: tuple[DiscoveryEvidenceEntry, ...]) -> str:
    if not entries:
        return "none"
    visible = ", ".join(f"{entry.key}={_format_scalar(entry.value)}" for entry in entries[:3])
    if len(entries) > 3:
        visible += f", +{len(entries) - 3} more"
    return visible


def _format_top_values(profile: ColumnProfile) -> str:
    if not profile.top_values:
        return "none"
    visible = ", ".join(
        f"{_format_scalar(value)}:{count}"
        for value, count in profile.top_values[:_MAX_INLINE_ITEMS]
    )
    if len(profile.top_values) > _MAX_INLINE_ITEMS:
        visible += f", +{len(profile.top_values) - _MAX_INLINE_ITEMS} more"
    return visible


def _format_sample_values(profile: ColumnProfile) -> str:
    if not profile.sample_values:
        return "none"
    visible = ", ".join(
        _format_scalar(value) for value in profile.sample_values[:_MAX_INLINE_ITEMS]
    )
    if len(profile.sample_values) > _MAX_INLINE_ITEMS:
        visible += f", +{len(profile.sample_values) - _MAX_INLINE_ITEMS} more"
    return visible


def _format_profile_range(profile: ColumnProfile) -> str:
    return f"{_format_scalar(profile.min_value)}..{_format_scalar(profile.max_value)}"


def _format_profile_summary(profile: ColumnProfile) -> str:
    return (
        f"type={profile.data_type} family={profile.type_family} nullable={profile.nullable} "
        f"distinct={profile.distinct_count} nulls={profile.null_count} "
        f"non_null={profile.non_null_count} range={_format_profile_range(profile)}"
    )


def _format_formats(formats: tuple[FormatCandidate, ...]) -> str:
    if not formats:
        return "none"
    visible = ", ".join(
        f"{candidate.format}:{candidate.matched_count}"
        + (" ambiguous" if candidate.ambiguous else "")
        for candidate in formats[:_MAX_INLINE_ITEMS]
    )
    if len(formats) > _MAX_INLINE_ITEMS:
        visible += f", +{len(formats) - _MAX_INLINE_ITEMS} more"
    return visible


def _format_time_range(value_range: TimeValueRange) -> str:
    return f"{_format_scalar(value_range.lower)}..{_format_scalar(value_range.upper)}"


def _format_discovery_card(
    *,
    identity: str,
    status: str,
    table_header: tuple[str, ...] | None = None,
    table_rows: tuple[tuple[str, ...], ...] | None = None,
    available: tuple[str, ...],
) -> str:
    """Render a bounded discovery result card without a trailing newline."""
    lines: list[str] = [identity, f"status: {status}"]
    if table_header is not None and table_rows is not None:
        lines.append("columns: " + " | ".join(table_header))
        for row in table_rows[:_MAX_TABLE_ROWS]:
            lines.append(" | ".join(row))
    lines.append("available:")
    for entry in available:
        lines.append(f"- {entry}")
    return "\n".join(lines)


def _signal_ids(signals: tuple[DiscoverySignal, ...]) -> str:
    return ", ".join(s.rule_id for s in signals) or "none"


def _issue_count(issues: tuple[DiscoveryIssue, ...]) -> int:
    return len(issues)


def _scan_status(scan: ScanReport, issues: int) -> str:
    return (
        f"evidence_only rows={scan.rows_scanned} "
        f"partition={scan.partition_resolution} "
        f"truncated={scan.truncated} issues={issues}"
    )


# ----- Result types -----


@dataclass(frozen=True, repr=False)
class EntityDiscoveryResult:
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    table: str
    primary_key_evidence: tuple[PrimaryKeyCandidate, ...]
    time_like_columns: tuple[str, ...]
    partition_columns: tuple[str, ...]
    column_profiles: tuple[ColumnProfile, ...]
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        return f"EntityDiscoveryResult datasource={self.datasource.id} table={self.table}"

    def render(self) -> str:
        lines = [self._identity(), f"status: {_scan_status(self.scan, _issue_count(self.issues))}"]
        if self.primary_key_evidence:
            lines.append("primary key evidence:")
            for candidate in self.primary_key_evidence[:_MAX_TABLE_ROWS]:
                lines.append(
                    "  "
                    f"{candidate.column} source={candidate.source} "
                    f"evidence={_format_evidence_entries(candidate.evidence)}"
                )
            if len(self.primary_key_evidence) > _MAX_TABLE_ROWS:
                lines.append(
                    f"  ... {len(self.primary_key_evidence) - _MAX_TABLE_ROWS} more; "
                    "inspect .primary_key_evidence"
                )
        else:
            lines.append("primary key evidence: none")
        lines.append(
            "time-like columns: " + (", ".join(self.time_like_columns[:_MAX_TABLE_ROWS]) or "none")
        )
        if len(self.time_like_columns) > _MAX_TABLE_ROWS:
            lines.append(f"  ... {len(self.time_like_columns) - _MAX_TABLE_ROWS} more")
        lines.append(
            "partition columns: " + (", ".join(self.partition_columns[:_MAX_TABLE_ROWS]) or "none")
        )
        if len(self.partition_columns) > _MAX_TABLE_ROWS:
            lines.append(f"  ... {len(self.partition_columns) - _MAX_TABLE_ROWS} more")
        if self.column_profiles:
            lines.append("column profiles:")
            for profile in self.column_profiles[:_MAX_TABLE_ROWS]:
                lines.append(
                    "  "
                    f"{profile.name} {_format_profile_summary(profile)} "
                    f"top={_format_top_values(profile)} samples={_format_sample_values(profile)}"
                )
            if len(self.column_profiles) > _MAX_TABLE_ROWS:
                lines.append(
                    f"  ... {len(self.column_profiles) - _MAX_TABLE_ROWS} more; "
                    "inspect .column_profiles"
                )
        else:
            lines.append("column profiles: none")
        lines.append("available:")
        for entry in (
            ".primary_key_evidence",
            ".time_like_columns",
            ".partition_columns",
            ".column_profiles",
            ".signals",
            ".issues",
            ".scan",
            ".render()",
            ".show()",
        ):
            lines.append(f"- {entry}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class DimensionDiscoveryResult:
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[ColumnDiscovery, ...]

    def _identity(self) -> str:
        return (
            f"DimensionDiscoveryResult datasource={self.datasource.id} columns={len(self.columns)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "profile", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                f"distinct={c.profile.distinct_count} nulls={c.profile.null_count}",
                _signal_ids(c.signals),
                str(_issue_count(c.issues)),
            )
            for c in self.columns
        )
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            table_header=header,
            table_rows=rows,
            available=(".columns", ".signals", ".issues", ".scan", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class TimeDimensionDiscoveryResult:
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[TimeColumnDiscovery, ...]

    def _identity(self) -> str:
        return (
            f"TimeDimensionDiscoveryResult datasource={self.datasource.id} "
            f"columns={len(self.columns)}"
        )

    def render(self) -> str:
        lines = [self._identity(), f"status: {_scan_status(self.scan, _issue_count(self.issues))}"]
        if self.columns:
            lines.append("time column evidence:")
            for column in self.columns[:_MAX_TABLE_ROWS]:
                lines.append(
                    "  "
                    f"{column.column} {_format_profile_summary(column.profile)} "
                    f"formats={_format_formats(column.detected_formats)} "
                    f"range={_format_time_range(column.value_range)} "
                    f"partition_aligned={column.partition_aligned} "
                    f"issues={_issue_count(column.issues)}"
                )
            if len(self.columns) > _MAX_TABLE_ROWS:
                lines.append(f"  ... {len(self.columns) - _MAX_TABLE_ROWS} more; inspect .columns")
        else:
            lines.append("time column evidence: none")
        lines.append("available:")
        for entry in (".columns", ".signals", ".issues", ".scan", ".render()", ".show()"):
            lines.append(f"- {entry}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class MeasureDiscoveryResult:
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[ColumnDiscovery, ...]

    def _identity(self) -> str:
        return f"MeasureDiscoveryResult datasource={self.datasource.id} columns={len(self.columns)}"

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "profile", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                f"distinct={c.profile.distinct_count} nulls={c.profile.null_count}",
                _signal_ids(c.signals),
                str(_issue_count(c.issues)),
            )
            for c in self.columns
        )
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            table_header=header,
            table_rows=rows,
            available=(".columns", ".signals", ".issues", ".scan", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class RelationshipDiscoveryResult:
    evidence: RelationshipDiscoveryEvidence
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        e = self.evidence
        from_name = getattr(e.from_side.datasource, "name", e.from_side.datasource)
        return f"RelationshipDiscoveryResult from={from_name} match_rate={e.match_rate:.2f}"

    def render(self) -> str:
        e = self.evidence
        status = (
            f"evidence_only sampled_keys={e.sampled_key_count} "
            f"matched={e.matched_key_count} match_rate={e.match_rate:.2f} "
            f"cardinality={e.cardinality_evidence} issues={_issue_count(self.issues)}"
        )
        return _format_discovery_card(
            identity=self._identity(),
            status=status,
            available=(".evidence", ".signals", ".issues", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class DimensionValueDiscoveryResult:
    datasource: DatasourceRef
    source: TableSource
    column: str
    values: tuple[DimensionValueFact, ...]
    complete: bool
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        return (
            f"DimensionValueDiscoveryResult datasource={self.datasource.id} "
            f"column={self.column} values={len(self.values)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("value", "count")
        rows = tuple((str(v.value), str(v.count)) for v in self.values)
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        exhaustive = "exhaustive" if self.complete else "not_exhaustive"
        status = f"{exhaustive} rows={self.scan.rows_scanned} truncated={self.scan.truncated}"
        return _format_discovery_card(
            identity=self._identity(),
            status=status,
            table_header=header,
            table_rows=rows,
            available=(
                ".values",
                ".complete",
                ".signals",
                ".issues",
                ".scan",
                ".render()",
                ".show()",
            ),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class RawSqlResult:
    """Bounded result from the explicit datasource raw-SQL escape hatch.

    Attributes:
        datasource: Datasource reference used for execution.
        backend_type: Backend type label.
        sql: Executed SQL text.
        reason: Required diagnostic reason supplied by the caller.
        columns: Returned column names.
        types: Returned column type labels when available.
        rows: Bounded row dictionaries.
        requested_limit: Requested row limit.
        returned_row_count: Number of rows returned.
        is_truncated: Whether the result hit ``limit``.
        warnings: Capability or execution warnings.

    Example:
        >>> import marivo.datasource as md
        >>> md.raw_sql(md.ref("warehouse"), "SELECT 1", reason="check connectivity")

    Constraints:
        This is an escape hatch for diagnostics only. SQL text must not become
        an executable semantic expression body.
    """

    datasource: DatasourceRef
    backend_type: str
    sql: str
    reason: str
    columns: tuple[str, ...]
    types: dict[str, str]
    rows: tuple[dict[str, object], ...]
    requested_limit: int
    returned_row_count: int
    is_truncated: bool
    warnings: tuple[str, ...]

    def _identity(self) -> str:
        return (
            f"RawSqlResult datasource={self.datasource.id} "
            f"rows={self.returned_row_count} escape_hatch"
        )

    def render(self) -> str:
        from marivo.render import format_bounded_card

        preview_rows = [[str(row.get(column)) for column in self.columns] for row in self.rows[:8]]
        status = (
            f"escape_hatch reason={self.reason} "
            f"truncated={self.is_truncated} warnings={len(self.warnings)}"
        )
        return format_bounded_card(
            identity=self._identity(),
            status=status,
            columns=list(self.columns),
            rows=preview_rows,
            row_count=self.returned_row_count,
            preview_truncation_hint="increase limit for more diagnostic rows",
            available=(".rows", ".columns", ".types", ".reason", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())
