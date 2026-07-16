"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._authoring.model import AuthoringContract
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
    "nested_derived_unsupported",
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
        return Card(identity=self._repr_identity(), available=(".render()", ".show()")).table(
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
class VerifyResult(RenderableResult):
    status: Literal["passed", "failed"]
    ref: str
    kind: AuthoringObjectKind
    validation_level: Literal["static"]
    runtime_checked: Literal[False]
    issues: tuple[AssessmentIssue, ...]
    warnings: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        return f"VerifyResult status={self.status} ref={self.ref} kind={self.kind}"

    def _card(self) -> Card:
        status_parts: list[str] = [self.status]
        if self.issues:
            status_parts.append(f"{len(self.issues)} issue{'s' if len(self.issues) != 1 else ''}")
        if self.warnings:
            status_parts.append(
                f"{len(self.warnings)} warning{'s' if len(self.warnings) != 1 else ''}"
            )
        card = Card(
            identity=self._repr_identity(),
            available=(".issues", ".warnings"),
        ).status(", ".join(status_parts))
        card = card.field("validation_level", self.validation_level).field(
            "runtime_checked", "false"
        )
        if self.issues:
            card = card.listing(
                label="issues",
                items=tuple(f"[{i.severity}] {i.kind}: {i.message}" for i in self.issues),
            )
        if self.warnings:
            card = card.listing(
                label="warnings",
                items=tuple(f"[{w.severity}] {w.kind}: {w.message}" for w in self.warnings),
            )
        if self.status == "passed":
            card = card.listing(
                label="Next step",
                items=("continue the batch or run catalog.readiness(refs=...)",),
            )
        else:
            card = card.listing(
                label="Next step",
                items=("repair this object, then re-run catalog.verify_object(ref)",),
            )
        return card

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this verification result.

        The contract exposes ``preview`` as a result-local continuation after
        explicit verification, with the ``semantic.verified`` current state and
        the ``semantic.previewed`` produced state.

        Returns
        -------
        AuthoringContract
            Normalized contract scoped to ``self.ref``.
        """
        from marivo.semantic._capabilities.contracts import contract_for_verify_result

        return contract_for_verify_result(self.ref)
