"""Public evidence and assessment DTOs for skill-driven semantic authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.semantic.ir import (
    BoundedProfilePolicyIR,
    DatasetSourceIR,
    FileSourceIR,
    MetadataOnlyPolicyIR,
    SamplePolicyIR,
    SelectedColumnsPolicyIR,
    TableSourceIR,
)

EvidenceKind = Literal[
    "catalog_metadata",
    "table_comment",
    "column_comment",
    "schema",
    "raw_preview_profile",
    "source_sql",
    "knowledge_document",
    "user_confirmation",
    "relationship_confirmation",
]

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
    "dataset",
    "field",
    "time_field",
    "metric",
    "derived_metric",
    "relationship",
]

AuthoringSourceRole = Literal["primary", "from", "to", "component"]

RedactionStatus = Literal["redacted", "not_redacted"]
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


def _dataset_source_from_ir(source: DatasetSourceIR) -> DatasetSource:
    if isinstance(source, TableSourceIR):
        return TableSource(table=source.table, database=source.database)
    if isinstance(source, FileSourceIR):
        return FileSource(path=source.path, format=source.format)
    raise TypeError(f"unsupported dataset source IR: {type(source).__name__}")


@dataclass(frozen=True)
class MetadataOnlyPolicy:
    timeout_seconds: int | None = None
    redact: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "metadata_only",
            "timeout_seconds": self.timeout_seconds,
            "redact": self.redact,
        }

    def to_ir(self) -> MetadataOnlyPolicyIR:
        return MetadataOnlyPolicyIR(timeout_seconds=self.timeout_seconds, redact=self.redact)


@dataclass(frozen=True)
class BoundedProfilePolicy:
    limit: int
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    redact: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "bounded_profile",
            "limit": self.limit,
            "timeout_seconds": self.timeout_seconds,
            "max_profiled_columns": self.max_profiled_columns,
            "redact": self.redact,
        }

    def to_ir(self) -> BoundedProfilePolicyIR:
        return BoundedProfilePolicyIR(
            limit=self.limit,
            timeout_seconds=self.timeout_seconds,
            max_profiled_columns=self.max_profiled_columns,
            redact=self.redact,
        )


@dataclass(frozen=True)
class SelectedColumnsPolicy:
    limit: int
    columns: tuple[str, ...]
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    redact: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": "selected_columns_profile",
            "limit": self.limit,
            "columns": list(self.columns),
            "timeout_seconds": self.timeout_seconds,
            "max_profiled_columns": self.max_profiled_columns,
            "redact": self.redact,
        }

    def to_ir(self) -> SelectedColumnsPolicyIR:
        return SelectedColumnsPolicyIR(
            limit=self.limit,
            columns=self.columns,
            timeout_seconds=self.timeout_seconds,
            max_profiled_columns=self.max_profiled_columns,
            redact=self.redact,
        )


SamplePolicy = MetadataOnlyPolicy | BoundedProfilePolicy | SelectedColumnsPolicy


def _sample_policy_from_ir(policy: SamplePolicyIR) -> SamplePolicy:
    if isinstance(policy, MetadataOnlyPolicyIR):
        return MetadataOnlyPolicy(timeout_seconds=policy.timeout_seconds, redact=policy.redact)
    if isinstance(policy, BoundedProfilePolicyIR):
        return BoundedProfilePolicy(
            limit=policy.limit,
            timeout_seconds=policy.timeout_seconds,
            max_profiled_columns=policy.max_profiled_columns,
            redact=policy.redact,
        )
    if isinstance(policy, SelectedColumnsPolicyIR):
        return SelectedColumnsPolicy(
            limit=policy.limit,
            columns=policy.columns,
            timeout_seconds=policy.timeout_seconds,
            max_profiled_columns=policy.max_profiled_columns,
            redact=policy.redact,
        )
    raise TypeError(f"unsupported sample policy IR: {type(policy).__name__}")


@dataclass(frozen=True)
class AiContextInput:
    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "business_definition": self.business_definition,
            "guardrails": list(self.guardrails),
            "synonyms": list(self.synonyms),
            "examples": list(self.examples),
            "instructions": self.instructions,
            "owner_notes": self.owner_notes,
        }


@dataclass(frozen=True)
class EvidenceRef:
    id: str
    kind: EvidenceKind
    datasource: str | None
    source: DatasetSource | None
    collected_at: str
    structural_fingerprint: str | None = None
    content_fingerprint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "datasource": self.datasource,
            "source": self.source.to_dict() if self.source is not None else None,
            "collected_at": self.collected_at,
            "structural_fingerprint": self.structural_fingerprint,
            "content_fingerprint": self.content_fingerprint,
        }


@dataclass(frozen=True)
class EvidenceFact:
    id: str
    label: str
    value: object
    evidence_refs: tuple[str, ...]


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
    evidence_refs: tuple[EvidenceRef, ...]
    sample_policy: SamplePolicy
    redaction_status: RedactionStatus
    truncated: bool


@dataclass(frozen=True)
class AssessmentIssue:
    kind: IssueKind
    severity: Severity
    refs: tuple[str, ...]
    message: str
    rule_id: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ColumnEvidence:
    datasource: str
    source: DatasetSource
    column: str
    profile: ColumnProfile
    issues: tuple[AssessmentIssue, ...] = ()
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthoringQuestion:
    id: str
    decision_kind: str
    subject_refs: tuple[str, ...]
    prompt: str
    reason: str
    evidence_refs: tuple[str, ...]
    options: tuple[str, ...] = ()
    default_option: str | None = None
    readiness_effect: ReadinessEffect = "blocks"


@dataclass(frozen=True)
class AssessmentResult:
    status: ReviewStatus
    facts: tuple[EvidenceFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]


@dataclass(frozen=True)
class AuthoringAssessment:
    status: ReviewStatus
    facts: tuple[EvidenceFact, ...]
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]


@dataclass(frozen=True)
class AuthoringEvidenceInput:
    kind: Literal[
        "source_sql", "knowledge_document", "user_confirmation", "relationship_confirmation"
    ]
    subject_refs: tuple[str, ...]
    content: str
    source_document: str | None = None
    source_dialect: str | None = None
    content_fingerprint: str | None = None


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
