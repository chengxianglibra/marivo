"""Discovery evidence vocabulary for the datasource discovery surface.

Defines the frozen evidence, signal, issue, judgment-target, candidate, and
result types used by ``md.discover_*`` (wired in a later plan). Nothing here
infers business meaning; rules describe evidence shape only.
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
JudgmentOwner = Literal["agent", "user_or_project_context"]


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
class SemanticJudgmentTarget:
    """One semantic authoring field that still requires a judgment.

    Targets are deterministic templates per discover kind, not conclusions.
    They never carry evidence refs, sufficiency flags, confidence scores, or
    recommended actions.

    Attributes:
        object_kind: Semantic object kind the target belongs to.
        field_path: Real semantic authoring field path (e.g.
            ``measure.ai_context.business_definition``).
        question: The judgment the agent or user must resolve.
        owner: ``agent`` for evidence-derived selections; ``user_or_project_context``
            for business meaning or policy.
    """

    object_kind: DiscoveryObjectKind
    field_path: str
    question: str
    owner: JudgmentOwner


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


# ----- Candidate types (datasource evidence, not semantic objects) -----


@dataclass(frozen=True)
class EntityDiscoveryCandidate:
    """Entity-level evidence for one source table.

    Attributes:
        table: Table name.
        primary_key_candidates: Sampled or declared primary-key candidates.
        time_like_columns: Columns with temporal type or parseable date strings.
        partition_columns: Metadata partition columns.
        column_profiles: Bounded per-column profiles.
        signals: Candidate-scope signals for this entity.
        issues: Candidate-scope issues for this entity.
    """

    table: str
    primary_key_candidates: tuple[str, ...]
    time_like_columns: tuple[str, ...]
    partition_columns: tuple[str, ...]
    column_profiles: tuple[ColumnProfile, ...]
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class ColumnDiscoveryCandidate:
    """Column-level evidence for a dimension or measure candidate.

    Attributes:
        column: Column name.
        profile: Bounded column profile.
        signals: Candidate-scope signals for this column.
        issues: Candidate-scope issues for this column.
    """

    column: str
    profile: ColumnProfile
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
class TimeColumnDiscoveryCandidate:
    """Column-level evidence for a time-dimension candidate.

    Attributes:
        column: Column name.
        profile: Bounded column profile.
        detected_formats: Supported parse candidates (populated in Plan 2).
        value_range: Typed inclusive sampled value range.
        partition_aligned: Whether the column is a metadata partition column.
        signals: Candidate-scope signals for this column.
        issues: Candidate-scope issues for this column.
    """

    column: str
    profile: ColumnProfile
    detected_formats: tuple[str, ...]
    value_range: TimeValueRange
    partition_aligned: bool
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]


@dataclass(frozen=True)
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
    key_type_evidence: tuple[str, ...]
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


# ----- Shared card formatter -----


_MAX_TARGETS = 8
_MAX_TABLE_ROWS = 8


def _format_discovery_card(
    *,
    identity: str,
    status: str,
    judgment_targets: tuple[SemanticJudgmentTarget, ...],
    table_header: tuple[str, ...] | None = None,
    table_rows: tuple[tuple[str, ...], ...] | None = None,
    available: tuple[str, ...],
) -> str:
    """Render a bounded discovery result card without a trailing newline."""
    lines: list[str] = [identity, f"status: {status}"]
    if judgment_targets:
        lines.append("judgment targets:")
        for target in judgment_targets[:_MAX_TARGETS]:
            lines.append(f"- {target.field_path}: {target.question}")
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
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
    candidates: tuple[EntityDiscoveryCandidate, ...]

    def _identity(self) -> str:
        return (
            f"EntityDiscoveryResult datasource={self.datasource.id} "
            f"candidates={len(self.candidates)}"
        )

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            judgment_targets=self.judgment_targets,
            available=(".candidates", ".signals", ".issues", ".judgment_targets", ".scan", ".render()", ".show()"),
        )

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
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
    candidates: tuple[ColumnDiscoveryCandidate, ...]

    def _identity(self) -> str:
        return (
            f"DimensionDiscoveryResult datasource={self.datasource.id} "
            f"candidates={len(self.candidates)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                _signal_ids(c.signals),
                str(_issue_count(c.issues)),
            )
            for c in self.candidates
        )
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            judgment_targets=self.judgment_targets,
            table_header=header,
            table_rows=rows,
            available=(".candidates", ".signals", ".issues", ".judgment_targets", ".scan", ".render()", ".show()"),
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
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
    candidates: tuple[TimeColumnDiscoveryCandidate, ...]

    def _identity(self) -> str:
        return (
            f"TimeDimensionDiscoveryResult datasource={self.datasource.id} "
            f"candidates={len(self.candidates)}"
        )

    def render(self) -> str:
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            judgment_targets=self.judgment_targets,
            available=(".candidates", ".signals", ".issues", ".judgment_targets", ".scan", ".render()", ".show()"),
        )

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
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
    candidates: tuple[ColumnDiscoveryCandidate, ...]

    def _identity(self) -> str:
        return (
            f"MeasureDiscoveryResult datasource={self.datasource.id} "
            f"candidates={len(self.candidates)}"
        )

    def _table(self) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
        header = ("column", "type", "signals", "issues")
        rows = tuple(
            (
                c.column,
                c.profile.data_type,
                _signal_ids(c.signals),
                str(_issue_count(c.issues)),
            )
            for c in self.candidates
        )
        return header, rows

    def render(self) -> str:
        header, rows = self._table()
        return _format_discovery_card(
            identity=self._identity(),
            status=_scan_status(self.scan, _issue_count(self.issues)),
            judgment_targets=self.judgment_targets,
            table_header=header,
            table_rows=rows,
            available=(".candidates", ".signals", ".issues", ".judgment_targets", ".scan", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class RelationshipDiscoveryResult:
    evidence: RelationshipDiscoveryEvidence
    judgment_targets: tuple[SemanticJudgmentTarget, ...]
    signals: tuple[DiscoverySignal, ...]
    issues: tuple[DiscoveryIssue, ...]

    def _identity(self) -> str:
        e = self.evidence
        from_name = getattr(e.from_side.datasource, "name", e.from_side.datasource)
        return (
            "RelationshipDiscoveryResult "
            f"from={from_name} "
            f"match_rate={e.match_rate:.2f}"
        )

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
            judgment_targets=self.judgment_targets,
            available=(".evidence", ".signals", ".issues", ".judgment_targets", ".render()", ".show()"),
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
    judgment_targets: tuple[SemanticJudgmentTarget, ...]

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
            judgment_targets=self.judgment_targets,
            table_header=header,
            table_rows=rows,
            available=(".values", ".complete", ".signals", ".issues", ".judgment_targets", ".scan", ".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._identity())

    def show(self) -> None:
        print(self.render())
