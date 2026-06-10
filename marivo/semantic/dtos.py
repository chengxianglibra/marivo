"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.semantic.ir import (
    BoundedProfilePolicyIR,
    FileSourceIR,
    MetadataOnlyPolicyIR,
    SelectedColumnsPolicyIR,
    TableSourceIR,
)

Severity = Literal["blocker", "warning", "info"]

IssueKind = Literal[
    "missing_evidence",
    "stale_metadata_evidence",
    "missing_source",
    "missing_column",
    "static_check_failed",
    "authored_object_invalid",
]

ReviewStatus = Literal[
    "supported",
    "needs_input",
    "blocked",
]

AuthoringObjectKind = Literal[
    "entity",
    "dimension",
    "time_dimension",
    "metric",
    "derived_metric",
    "relationship",
]

AuthoringSourceRole = Literal["primary", "from", "to", "component"]

ReadinessEffect = Literal["blocks", "warns", "advisory"]
SampleScope = Literal["none", "bounded_sample"]
FileFormat = Literal["parquet", "csv", "json"]


@dataclass(frozen=True)
class TableSource:
    table: str
    database: str | tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        return {"kind": "table", "table": self.table, "database": database}

    def to_ir(self) -> TableSourceIR:
        return TableSourceIR(table=self.table, database=self.database)


@dataclass(frozen=True)
class FileSource:
    path: str
    format: FileFormat

    def to_dict(self) -> dict[str, object]:
        return {"kind": "file", "path": self.path, "format": self.format}

    def to_ir(self) -> FileSourceIR:
        return FileSourceIR(path=self.path, format=self.format)


DatasetSource = TableSource | FileSource


@dataclass(frozen=True)
class AuthoringSourceInput:
    role: AuthoringSourceRole
    datasource: str
    source: DatasetSource
    columns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "datasource": self.datasource,
            "source": self.source.to_dict(),
            "columns": list(self.columns),
        }


@dataclass(frozen=True)
class MetadataOnlyPolicy:
    timeout_seconds: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "metadata_only",
            "timeout_seconds": self.timeout_seconds,
        }

    def to_ir(self) -> MetadataOnlyPolicyIR:
        return MetadataOnlyPolicyIR(timeout_seconds=self.timeout_seconds)


@dataclass(frozen=True)
class BoundedProfilePolicy:
    limit: int
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "bounded_profile",
            "limit": self.limit,
            "timeout_seconds": self.timeout_seconds,
            "max_profiled_columns": self.max_profiled_columns,
        }

    def to_ir(self) -> BoundedProfilePolicyIR:
        return BoundedProfilePolicyIR(
            limit=self.limit,
            timeout_seconds=self.timeout_seconds,
            max_profiled_columns=self.max_profiled_columns,
        )


@dataclass(frozen=True)
class SelectedColumnsPolicy:
    limit: int
    columns: tuple[str, ...]
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "selected_columns_profile",
            "limit": self.limit,
            "columns": list(self.columns),
            "timeout_seconds": self.timeout_seconds,
            "max_profiled_columns": self.max_profiled_columns,
        }

    def to_ir(self) -> SelectedColumnsPolicyIR:
        return SelectedColumnsPolicyIR(
            limit=self.limit,
            columns=self.columns,
            timeout_seconds=self.timeout_seconds,
            max_profiled_columns=self.max_profiled_columns,
        )


SamplePolicy = MetadataOnlyPolicy | BoundedProfilePolicy | SelectedColumnsPolicy


@dataclass(frozen=True)
class EvidenceFact:
    id: str
    label: str
    value: object


@dataclass(frozen=True)
class ColumnProfile:
    column: str
    data_type: str
    nullable: bool | None
    comment: str | None
    null_count: int | None = None
    empty_count: int | None = None
    distinct_count: int | None = None
    top_values: tuple[tuple[object, int], ...] = ()
    min_value: object | None = None
    max_value: object | None = None
    observed_formats: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    sample_scope: SampleScope = "bounded_sample"
    approximate: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "column": self.column,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "comment": self.comment,
            "null_count": self.null_count,
            "empty_count": self.empty_count,
            "distinct_count": self.distinct_count,
            "top_values": [[value, count] for value, count in self.top_values],
            "min_value": self.min_value,
            "max_value": self.max_value,
            "observed_formats": list(self.observed_formats),
            "warnings": list(self.warnings),
            "sample_scope": self.sample_scope,
            "approximate": self.approximate,
        }


@dataclass(frozen=True)
class SourceEvidencePack:
    datasource: str
    source: DatasetSource
    schema: tuple[tuple[str, str], ...]
    table_comment: str | None
    column_comments: tuple[tuple[str, str], ...]
    nullable: tuple[tuple[str, bool | None], ...]
    partition_hints: tuple[str, ...]
    key_hints: tuple[tuple[str, ...], ...]
    column_profiles: tuple[ColumnProfile, ...]
    metadata_warnings: tuple[str, ...]
    sample_policy: SamplePolicy
    truncated: bool

    @property
    def schema_by_column(self) -> dict[str, str]:
        return dict(self.schema)

    @property
    def nullable_by_column(self) -> dict[str, bool | None]:
        return dict(self.nullable)

    @property
    def column_comments_by_column(self) -> dict[str, str]:
        return dict(self.column_comments)

    @property
    def column_profiles_by_column(self) -> dict[str, ColumnProfile]:
        return {profile.column: profile for profile in self.column_profiles}

    def to_dict(self) -> dict[str, object]:
        return {
            "datasource": self.datasource,
            "source": self.source.to_dict(),
            "schema": [list(item) for item in self.schema],
            "table_comment": self.table_comment,
            "column_comments": [list(item) for item in self.column_comments],
            "nullable": [list(item) for item in self.nullable],
            "partition_hints": list(self.partition_hints),
            "key_hints": [list(item) for item in self.key_hints],
            "column_profiles": [profile.to_dict() for profile in self.column_profiles],
            "metadata_warnings": list(self.metadata_warnings),
            "sample_policy": self.sample_policy.to_dict(),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class AssessmentIssue:
    kind: IssueKind
    severity: Severity
    refs: tuple[str, ...]
    message: str
    rule_id: str


@dataclass(frozen=True)
class ColumnEvidence:
    datasource: str
    source: DatasetSource
    column: str
    profile: ColumnProfile
    issues: tuple[AssessmentIssue, ...] = ()


@dataclass(frozen=True)
class AuthoringQuestion:
    id: str
    decision_kind: str
    subject_refs: tuple[str, ...]
    prompt: str
    reason: str
    options: tuple[str, ...] = ()
    default_option: str | None = None
    readiness_effect: ReadinessEffect = "blocks"


@dataclass(frozen=True)
class AuthoringAssessment:
    status: ReviewStatus
    facts: tuple[EvidenceFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]


def derive_status(
    issues: tuple[AssessmentIssue, ...],
    questions: tuple[AuthoringQuestion, ...],
) -> ReviewStatus:
    if any(issue.severity == "blocker" for issue in issues):
        return "blocked"
    if any(question.readiness_effect == "blocks" for question in questions):
        return "blocked"
    if any(
        issue.kind in {"missing_evidence", "missing_source"} and issue.severity != "info"
        for issue in issues
    ):
        return "needs_input"
    if any(question.readiness_effect == "warns" for question in questions):
        return "needs_input"
    return "supported"
