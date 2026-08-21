"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.preview import PreviewResult
from marivo.render import Card, RenderableResult
from marivo.semantic.ir import (
    CsvSourceIR,
    EntitySourceIR,
    JsonSourceIR,
    ParquetSourceIR,
    TableSourceIR,
)

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
    "datasource",
    "entity",
    "dimension",
    "time_dimension",
    "measure",
    "metric",
    "derived_metric",
    "relationship",
    "event",
    "state_model",
    "period_calendar",
    "temporal_set",
    "work_schedule",
]

AuthoringSourceRole = Literal["primary", "from", "to", "component"]

FileFormat = Literal["parquet", "csv", "json"]


TableSource = TableSourceIR
FileSource = ParquetSourceIR | CsvSourceIR | JsonSourceIR
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


@dataclass(frozen=True, repr=False)
class AuthoringAssessment(RenderableResult):
    status: ReviewStatus
    issues: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        return f"AuthoringAssessment status={self.status} issues={len(self.issues)}"

    def _card(self) -> Card:
        issue_rows = [[str(issue.kind), str(issue.severity)] for issue in self.issues]
        return Card(identity=self._repr_identity(), available=(".show()",)).table(
            columns=["issue", "severity"], rows=issue_rows, row_count=len(self.issues)
        )


def derive_status(
    issues: tuple[AssessmentIssue, ...],
) -> ReviewStatus:
    if any(issue.severity == "blocker" for issue in issues):
        return "blocked"
    if any(
        issue.kind in {"missing_evidence", "missing_source"} and issue.severity != "info"
        for issue in issues
    ):
        return "needs_input"
    return "supported"


@dataclass(frozen=True, repr=False)
class PreviewBatchResult(RenderableResult):
    """Successful bounded previews for an explicitly requested ref batch."""

    results: tuple[PreviewResult, ...]
    status: Literal["passed"] = "passed"

    @property
    def refs(self) -> tuple[str, ...]:
        """Return previewed semantic refs in caller-supplied order."""
        return tuple(result.ref for result in self.results)

    def _repr_identity(self) -> str:
        return f"PreviewBatchResult status={self.status} refs={len(self.results)}"

    def _card(self) -> Card:
        return Card(
            identity=self._repr_identity(),
            available=(".results", ".refs", ".show()"),
        ).listing(
            label=f"previews ({len(self.results)})",
            items=tuple(
                f"{result.ref}: {result.kind}, rows={result.returned_row_count}, "
                f"warnings={len(result.warnings)}"
                for result in self.results
            ),
        )
