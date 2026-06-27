"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from marivo.render import format_bounded_card, result_repr
from marivo.semantic.ir import (
    CsvSourceIR,
    EntitySourceIR,
    ParquetSourceIR,
    TableSourceIR,
)

if TYPE_CHECKING:
    from marivo.datasource.scan import ScanReport

Severity = Literal["blocker", "warning", "info"]

IssueKind = Literal[
    "missing_evidence",
    "stale_metadata_evidence",
    "missing_source",
    "missing_column",
    "missing_prerequisite",
    "datasource_unreachable",
    "static_check_failed",
    "authored_object_invalid",
    "unreachable_entity",
    "ibis_attribute_shadowing",
    "project_load_failed",
]

ReviewStatus = Literal[
    "supported",
    "needs_input",
    "blocked",
]

AuthoringObjectKind = Literal[
    "domain",
    "entity",
    "dimension",
    "time_dimension",
    "measure",
    "metric",
    "derived_metric",
    "relationship",
]

AuthoringSourceRole = Literal["primary", "from", "to", "component"]

ReadinessEffect = Literal["blocks", "warns", "advisory"]
FileFormat = Literal["parquet", "csv"]


TableSource = TableSourceIR
FileSource = ParquetSourceIR | CsvSourceIR
DatasetSource = EntitySourceIR


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
class AssessmentIssue:
    kind: IssueKind
    severity: Severity
    refs: tuple[str, ...]
    message: str
    rule_id: str


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


@dataclass(frozen=True, repr=False)
class AuthoringAssessment:
    status: ReviewStatus
    issues: tuple[AssessmentIssue, ...]
    questions: tuple[AuthoringQuestion, ...]

    def _repr_identity(self) -> str:
        return (
            f"AuthoringAssessment status={self.status} "
            f"issues={len(self.issues)} questions={len(self.questions)}"
        )

    def render(self) -> str:
        issue_rows = [[str(issue.kind), str(issue.severity)] for issue in self.issues]
        return format_bounded_card(
            identity=self._repr_identity(),
            columns=["issue", "severity"],
            rows=issue_rows,
            row_count=len(self.issues),
            preview_truncation_hint="inspect .issues / .questions",
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


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


@dataclass(frozen=True, repr=False)
class VerifyResult:
    status: Literal["passed", "failed"]
    ref: str
    kind: AuthoringObjectKind
    issues: tuple[AssessmentIssue, ...]
    warnings: tuple[AssessmentIssue, ...]
    scan: ScanReport | None
    auto_recorded: tuple[str, ...]

    _MAX_DETAIL_ITEMS = 5

    def __repr__(self) -> str:
        return result_repr(f"VerifyResult status={self.status} ref={self.ref} kind={self.kind}")

    def render(self) -> str:
        identity = f"VerifyResult status={self.status} ref={self.ref} kind={self.kind}"
        if not self.issues and not self.warnings:
            return format_bounded_card(
                identity=identity,
                status=self.status,
                available=(".issues", ".warnings", ".scan"),
            )
        parts: list[str] = [identity]
        issue_count = len(self.issues)
        warning_count = len(self.warnings)
        status_parts: list[str] = [self.status]
        if issue_count:
            status_parts.append(f"{issue_count} issue{'s' if issue_count != 1 else ''}")
        if warning_count:
            status_parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        parts.append(f"status: {', '.join(status_parts)}")
        if self.issues:
            parts.append("issues:")
            for issue in self.issues[: self._MAX_DETAIL_ITEMS]:
                parts.append(f"  [{issue.severity}] {issue.kind}: {issue.message}")
            if len(self.issues) > self._MAX_DETAIL_ITEMS:
                parts.append(
                    f"  ... {len(self.issues) - self._MAX_DETAIL_ITEMS} more issues; "
                    "inspect .issues for all"
                )
        if self.warnings:
            parts.append("warnings:")
            for warning in self.warnings[: self._MAX_DETAIL_ITEMS]:
                parts.append(f"  [{warning.severity}] {warning.kind}: {warning.message}")
            if len(self.warnings) > self._MAX_DETAIL_ITEMS:
                parts.append(
                    f"  ... {len(self.warnings) - self._MAX_DETAIL_ITEMS} more warnings; "
                    "inspect .warnings for all"
                )
        parts.append("available:")
        for entry in (".issues", ".warnings", ".scan"):
            parts.append(f"- {entry}")
        return "\n".join(parts)

    def show(self) -> None:
        print(self.render())
