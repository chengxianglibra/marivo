"""Public evidence and assessment DTOs for skill-driven semantic authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.semantic.ir import DatasetSourceIR, FileSourceIR, TableSourceIR

EvidenceKind = Literal[
    "catalog_metadata",
    "table_comment",
    "column_comment",
    "schema",
    "raw_preview_profile",
    "source_sql",
    "knowledge_document",
    "user_confirmation",
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
    "needs_evidence",
    "blocked",
]

SourceKind = Literal["table", "file"]

AuthoringObjectKind = Literal[
    "dataset",
    "field",
    "time_field",
    "metric",
    "relationship",
]

NextCheck = Literal[
    "inspect_source_context",
    "inspect_column_context",
    "check_authoring_inputs",
    "write_semantic_python",
    "reload_project",
    "inspect_authored_object",
    "preview_dataset",
    "preview_field",
    "preview_metric",
    "parity_check",
    "readiness",
    "richness",
    "ask_user",
]

SampleMode = Literal["metadata_only", "bounded_profile", "selected_columns_profile"]
RedactionStatus = Literal["redacted", "not_redacted"]
ReadinessEffect = Literal["blocks", "warns", "advisory"]
SampleScope = Literal["none", "bounded_sample"]
FileFormat = Literal["parquet", "csv"]


@dataclass(frozen=True)
class DatasetSource:
    kind: SourceKind
    table: str | None = None
    database: str | tuple[str, ...] | None = None
    path: str | None = None
    format: FileFormat | None = None

    def __post_init__(self) -> None:
        if self.kind == "table":
            if self.table is None:
                raise ValueError("table source requires table")
            if self.path is not None or self.format is not None:
                raise ValueError("table source does not accept file fields")
            return

        if self.kind != "file":
            raise ValueError(f"unsupported dataset source kind: {self.kind!r}")

        if self.table is not None or self.database is not None:
            raise ValueError("file source does not accept table fields")
        if self.path is None:
            raise ValueError("file source requires path")
        if self.format is None:
            raise ValueError("file source requires format")
        if self.format not in {"parquet", "csv"}:
            raise ValueError(f"unsupported file source format: {self.format!r}")

    def to_dict(self) -> dict[str, object]:
        database: str | list[str] | None = (
            list(self.database) if isinstance(self.database, tuple) else self.database
        )
        return {
            "kind": self.kind,
            "table": self.table,
            "database": database,
            "path": self.path,
            "format": self.format,
        }

    def to_ir(self) -> DatasetSourceIR:
        if self.kind == "table":
            if self.table is None:
                raise ValueError("table source requires table")
            return TableSourceIR(table=self.table, database=self.database)

        if self.kind == "file":
            if self.path is None:
                raise ValueError("file source requires path")
            if self.format not in {"parquet", "csv"}:
                raise ValueError(f"unsupported file source format: {self.format!r}")
            return FileSourceIR(path=self.path, format=self.format)

        raise ValueError(f"unsupported dataset source kind: {self.kind!r}")

    @classmethod
    def from_ir(cls, source: DatasetSourceIR) -> DatasetSource:
        if isinstance(source, TableSourceIR):
            return cls(kind="table", table=source.table, database=source.database)
        if isinstance(source, FileSourceIR):
            return cls(kind="file", path=source.path, format=source.format)
        raise TypeError(f"unsupported dataset source IR: {type(source).__name__}")


@dataclass(frozen=True)
class SamplePolicy:
    mode: SampleMode
    limit: int | None = None
    columns: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_profiled_columns: int | None = None
    redact: bool = True

    def reads_rows(self) -> bool:
        return self.mode in {"bounded_profile", "selected_columns_profile"}

    def validate(self) -> None:
        if self.reads_rows() and self.limit is None:
            raise ValueError("limit is required for sample policies that read rows")
        if self.mode == "selected_columns_profile" and not self.columns:
            raise ValueError("columns are required for selected_columns_profile")
        if self.mode in {"metadata_only", "bounded_profile"} and self.columns:
            raise ValueError(f"columns are not supported for {self.mode}")


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
    next_checks: tuple[NextCheck, ...] = ()


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
    next_checks: tuple[NextCheck, ...] = ()


@dataclass(frozen=True)
class AuthoringEvidenceInput:
    kind: Literal["source_sql", "knowledge_document", "user_confirmation"]
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
        return "needs_evidence"
    if any(question.readiness_effect == "warns" for question in questions):
        return "needs_evidence"
    return "supported"
