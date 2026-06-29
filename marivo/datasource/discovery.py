"""Result and evidence vocabulary for the datasource agent surface.

Defines the frozen evidence, signal, issue, evidence-subject, and result types
used by ``md.discover_*`` (wired in a later plan). Nothing here infers business
meaning; rules describe evidence shape only.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from marivo.datasource.authoring import DatasourceRef
from marivo.datasource.ir import EntitySourceIR
from marivo.datasource.metadata import TableMetadata
from marivo.datasource.scan import (
    ColumnProfile,
    JoinSide,
    ScanReport,
)
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, Card, RenderableResult

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


@runtime_checkable
class DatasourceResult(Protocol):
    """Opaque datasource result shown to agents via render/show.

    Public ``md.discover_*`` and ``md.inspect_*`` calls return this protocol.
    Concrete result dataclasses and evidence DTOs are implementation details;
    agents should read the bounded evidence text instead of traversing fields.
    """

    def render(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> str: ...

    def show(self, *, max_output_bytes: int | None = _DEFAULT_MAX_OUTPUT_BYTES) -> None: ...


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
class PrimaryKeyCandidate(RenderableResult):
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

    def _repr_identity(self) -> str:
        return f"PrimaryKeyCandidate column={_format_head_scalar(self.column)} source={self.source}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".evidence", ".render()", ".show()"),
        ).status(f"evidence={_format_evidence_entries(self.evidence)}")


@dataclass(frozen=True, repr=False)
class FormatCandidate(RenderableResult):
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

    def _repr_identity(self) -> str:
        return f"FormatCandidate format={_format_head_scalar(self.format)} kind={self.kind}"

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(".format", ".render()", ".show()"),
            )
            .status(f"matched_count={self.matched_count} ambiguous={self.ambiguous}")
            .field("format", self.format)
        )


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
class ColumnDiscovery(RenderableResult):
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

    def _repr_identity(self) -> str:
        return f"ColumnDiscovery column={_format_head_scalar(self.column)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".profile", ".signals", ".issues", ".render()", ".show()"),
        ).status(
            f"{_format_profile_summary(self.profile)} "
            f"signals={_signal_ids(self.signals)} issues={_issue_count(self.issues)}"
        )


@dataclass(frozen=True, repr=False)
class TimeColumnDiscovery(RenderableResult):
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

    def _repr_identity(self) -> str:
        return f"TimeColumnDiscovery column={_format_head_scalar(self.column)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(
                ".profile",
                ".detected_formats",
                ".value_range",
                ".signals",
                ".issues",
                ".render()",
                ".show()",
            ),
        ).status(
            f"{_format_profile_summary(self.profile)} "
            f"formats={_format_formats(self.detected_formats)} "
            f"range={_format_time_range(self.value_range)} "
            f"partition_aligned={self.partition_aligned} "
            f"signals={_signal_ids(self.signals)} issues={_issue_count(self.issues)}"
        )


@dataclass(frozen=True, repr=False)
class RelationshipDiscoveryEvidence(RenderableResult):
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

    def _repr_identity(self) -> str:
        from_cols = _format_head_scalar(",".join(self.from_side.columns))
        to_cols = _format_head_scalar(",".join(self.to_side.columns))
        return f"RelationshipDiscoveryEvidence from={from_cols} to={to_cols}"

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("side", "column", "type_family", "data_type")
        rows = tuple(
            (entry.side, entry.column, entry.type_family, entry.data_type)
            for entry in self.key_type_evidence
        )
        return header, rows

    def _card(self) -> Card:
        header, rows = self._table()
        return (
            Card(
                identity=self._repr_identity(),
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
            .status(
                f"sampled_keys={self.sampled_key_count} matched={self.matched_key_count} "
                f"match_rate={self.match_rate:.2f} max_rows_per_key={self.max_rows_per_key} "
                f"avg_rows_per_key={self.avg_rows_per_key:.2f} "
                f"cardinality={self.cardinality_evidence}"
            )
            .table(header, rows)
        )


# ----- Shared card helpers -----


_MAX_INLINE_ITEMS = 3


def _format_scalar(value: object | None) -> str:
    if value is None:
        return "none"
    text = str(value)
    if len(text) > 40:
        return text[:37] + "..."
    return text


def _format_head_scalar(value: object | None) -> str:
    text = _format_scalar(value)
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _format_evidence_entries(entries: tuple[DiscoveryEvidenceEntry, ...]) -> str:
    if not entries:
        return "none"
    visible = ", ".join(f"{entry.key}={_format_scalar(entry.value)}" for entry in entries[:3])
    if len(entries) > 3:
        visible += f", +{len(entries) - 3} more"
    return visible


def _format_issue_line(issue: DiscoveryIssue) -> str:
    return (
        f"{issue.rule_id} severity={issue.severity} subject={issue.subject} "
        f"message={issue.message} evidence={_format_evidence_entries(issue.evidence)}"
    )


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
        f"type={_format_head_scalar(profile.data_type)} "
        f"family={_format_head_scalar(profile.type_family)} nullable={profile.nullable} "
        f"distinct={profile.distinct_count} nulls={profile.null_count} "
        f"non_null={profile.non_null_count} range={_format_profile_range(profile)}"
    )


def _format_formats(formats: tuple[FormatCandidate, ...]) -> str:
    if not formats:
        return "none"
    visible = ", ".join(
        f"{_format_head_scalar(candidate.format)}:{candidate.matched_count}"
        + (" ambiguous" if candidate.ambiguous else "")
        for candidate in formats[:_MAX_INLINE_ITEMS]
    )
    if len(formats) > _MAX_INLINE_ITEMS:
        visible += f", +{len(formats) - _MAX_INLINE_ITEMS} more"
    return visible


def _format_time_range(value_range: TimeValueRange) -> str:
    return f"{_format_scalar(value_range.lower)}..{_format_scalar(value_range.upper)}"


def _signal_ids(signals: tuple[DiscoverySignal, ...]) -> str:
    return ", ".join(s.rule_id for s in signals) or "none"


def _issue_ids(issues: tuple[DiscoveryIssue, ...]) -> str:
    return ", ".join(i.rule_id for i in issues) or "none"


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
class EntityDiscoveryResult(RenderableResult):
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

    def _repr_identity(self) -> str:
        return (
            f"EntityDiscoveryResult datasource={_format_head_scalar(self.datasource.id)} "
            f"table={_format_head_scalar(self.table)}"
        )

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            _scan_status(self.scan, _issue_count(self.issues))
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        if self.table_metadata is not None and self.table_metadata.columns:
            schema_rows = []
            for column in self.table_metadata.columns:
                nullable = "Y" if column.nullable else ("N" if column.nullable is False else "?")
                row = f"{column.name} | {column.type} | {nullable}"
                if column.comment:
                    row += f" | {column.comment}"
                schema_rows.append(row)
            card.listing("schema columns", tuple(schema_rows))
        else:
            card.field("schema columns", "none")
        if self.primary_key_evidence:
            card.listing(
                "primary key evidence",
                tuple(
                    f"{candidate.column} source={candidate.source} "
                    f"evidence={_format_evidence_entries(candidate.evidence)}"
                    for candidate in self.primary_key_evidence
                ),
            )
        else:
            card.field("primary key evidence", "none")
        card.field("time-like columns", ", ".join(self.time_like_columns) or "none")
        card.field("partition columns", ", ".join(self.partition_columns) or "none")
        if self.column_profiles:
            card.listing(
                "column profiles",
                tuple(
                    f"{profile.name} {_format_profile_summary(profile)} "
                    f"top={_format_top_values(profile)} samples={_format_sample_values(profile)}"
                    for profile in self.column_profiles
                ),
            )
        else:
            card.field("column profiles", "none")
        return card


@dataclass(frozen=True, repr=False)
class DimensionDiscoveryResult(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[ColumnDiscovery, ...]

    def _repr_identity(self) -> str:
        return (
            f"DimensionDiscoveryResult datasource={_format_head_scalar(self.datasource.id)} "
            f"columns={len(self.columns)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "profile", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                f"distinct={c.profile.distinct_count} nulls={c.profile.null_count}",
                _signal_ids(c.signals),
                _issue_ids(c.issues),
            )
            for c in self.columns
        )
        return header, rows

    def _card(self) -> Card:
        header, rows = self._table()
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            _scan_status(self.scan, _issue_count(self.issues))
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        return card.table(header, rows)


@dataclass(frozen=True, repr=False)
class TimeDimensionDiscoveryResult(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[TimeColumnDiscovery, ...]

    def _repr_identity(self) -> str:
        return (
            f"TimeDimensionDiscoveryResult datasource={_format_head_scalar(self.datasource.id)} "
            f"columns={len(self.columns)}"
        )

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            _scan_status(self.scan, _issue_count(self.issues))
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        if self.columns:
            card.listing(
                "time column evidence",
                tuple(
                    f"{column.column} {_format_profile_summary(column.profile)} "
                    f"formats={_format_formats(column.detected_formats)} "
                    f"range={_format_time_range(column.value_range)} "
                    f"partition_aligned={column.partition_aligned} "
                    f"signals={_signal_ids(column.signals)} "
                    f"issues={_issue_ids(column.issues)}"
                    for column in self.columns
                ),
            )
        else:
            card.field("time column evidence", "none")
        return card


@dataclass(frozen=True, repr=False)
class MeasureDiscoveryResult(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    table_metadata: TableMetadata | None
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    columns: tuple[ColumnDiscovery, ...]

    def _repr_identity(self) -> str:
        return (
            f"MeasureDiscoveryResult datasource={_format_head_scalar(self.datasource.id)} "
            f"columns={len(self.columns)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "profile", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                f"distinct={c.profile.distinct_count} nulls={c.profile.null_count}",
                _signal_ids(c.signals),
                _issue_ids(c.issues),
            )
            for c in self.columns
        )
        return header, rows

    def _card(self) -> Card:
        header, rows = self._table()
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            _scan_status(self.scan, _issue_count(self.issues))
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        return card.table(header, rows)


@dataclass(frozen=True, repr=False)
class RelationshipDiscoveryResult(RenderableResult):
    evidence: RelationshipDiscoveryEvidence
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _repr_identity(self) -> str:
        e = self.evidence
        from_name = getattr(e.from_side.datasource, "name", e.from_side.datasource)
        return (
            f"RelationshipDiscoveryResult from={_format_head_scalar(from_name)} "
            f"match_rate={e.match_rate:.2f}"
        )

    def _card(self) -> Card:
        e = self.evidence
        status = (
            f"evidence_only sampled_keys={e.sampled_key_count} "
            f"matched={e.matched_key_count} match_rate={e.match_rate:.2f} "
            f"cardinality={e.cardinality_evidence} issues={_issue_count(self.issues)}"
        )
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            status
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        if e.key_type_evidence:
            card.listing(
                "key type evidence",
                tuple(
                    f"{item.side}.{item.column} type_family={item.type_family} "
                    f"data_type={item.data_type}"
                    for item in e.key_type_evidence
                ),
            )
        else:
            card.field("key type evidence", "none")
        card.field("relationship signals", _signal_ids(e.signals))
        if e.issues:
            card.listing("relationship issues", tuple(_format_issue_line(i) for i in e.issues))
        else:
            card.field("relationship issues", "none")
        return card


@dataclass(frozen=True, repr=False)
class DimensionValueDiscoveryResult(RenderableResult):
    datasource: DatasourceRef
    source: TableSource
    column: str
    values: tuple[DimensionValueFact, ...]
    complete: bool
    scan: ScanReport
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _repr_identity(self) -> str:
        return (
            f"DimensionValueDiscoveryResult datasource={_format_head_scalar(self.datasource.id)} "
            f"column={_format_head_scalar(self.column)} values={len(self.values)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("value", "count")
        rows = tuple((str(v.value), str(v.count)) for v in self.values)
        return header, rows

    def _card(self) -> Card:
        header, rows = self._table()
        exhaustive = "exhaustive" if self.complete else "not_exhaustive"
        status = f"{exhaustive} rows={self.scan.rows_scanned} truncated={self.scan.truncated}"
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            status
        )
        if self.issues:
            card.listing("result issues", tuple(_format_issue_line(i) for i in self.issues))
        card.table(header, rows)
        card.field("signals", _signal_ids(self.signals))
        card.field("issues", _issue_ids(self.issues))
        if not self.complete:
            card.field(
                "truncation hint",
                "rerun with a larger limit or narrower scope for more values",
            )
        return card


@dataclass(frozen=True, repr=False)
class PartitionInspectionResult(RenderableResult):
    """Bounded metadata-only partition value inspection result."""

    datasource: DatasourceRef
    source: TableSource
    partition_columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    requested_limit: int
    is_truncated: bool
    warnings: tuple[str, ...]

    def _repr_identity(self) -> str:
        return (
            f"PartitionInspectionResult datasource={_format_head_scalar(self.datasource.id)} "
            f"partitions={len(self.rows)}"
        )

    def _card(self) -> Card:
        column_status = "none" if not self.partition_columns else str(len(self.partition_columns))
        status = (
            f"metadata_only columns={column_status} "
            f"returned={len(self.rows)} limit={self.requested_limit} "
            f"truncated={self.is_truncated} warnings={len(self.warnings)}"
        )
        card = Card(identity=self._repr_identity(), available=(".render()", ".show()")).status(
            status
        )
        card.field("partition columns", ", ".join(self.partition_columns) or "none")
        if self.warnings:
            card.listing("warnings", self.warnings)
        if self.rows:
            card.listing(
                "partition values",
                tuple(
                    f"{', '.join(f'{key}={value}' for key, value in row.items())} "
                    f"-> md.partition({json.dumps(row, ensure_ascii=False)})"
                    for row in self.rows
                ),
            )
        else:
            card.field("partition values", "none")
        return card


@dataclass(frozen=True, repr=False)
class RawSqlResult(RenderableResult):
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
        >>> md.raw_sql(md.ref("datasource.warehouse"), "SELECT 1", reason="check connectivity")

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

    def _repr_identity(self) -> str:
        return (
            f"RawSqlResult datasource={_format_head_scalar(self.datasource.id)} "
            f"rows={self.returned_row_count} escape_hatch"
        )

    def _card(self) -> Card:
        preview_rows = tuple(
            tuple(str(row.get(column)) for column in self.columns) for row in self.rows
        )
        status = f"escape_hatch truncated={self.is_truncated} warnings={len(self.warnings)}"
        return (
            Card(
                identity=self._repr_identity(),
                available=(".rows", ".columns", ".types", ".reason", ".render()", ".show()"),
            )
            .status(status)
            .field("reason", self.reason)
            .table(self.columns, preview_rows, row_count=self.returned_row_count)
        )
