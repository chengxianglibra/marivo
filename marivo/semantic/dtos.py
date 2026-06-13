"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from marivo.render import format_bounded_card, result_repr
from marivo.semantic.ir import (
    EntitySourceIR,
    FileSourceIR,
    TableSourceIR,
)

if TYPE_CHECKING:
    from marivo.datasource.metadata import TableMetadata
    from marivo.datasource.scan import ColumnProfile as ScanColumnProfile
    from marivo.datasource.scan import JoinKeyProbe, ScanReport

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
    "metric",
    "derived_metric",
    "relationship",
]

AuthoringSourceRole = Literal["primary", "from", "to", "component"]

ReadinessEffect = Literal["blocks", "warns", "advisory"]
FileFormat = Literal["parquet", "csv", "json"]


TableSource = TableSourceIR
FileSource = FileSourceIR
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


# ---------------------------------------------------------------------------
# Stepwise authoring: Brief DTOs and result objects
# ---------------------------------------------------------------------------

BriefStatus = Literal["sufficient", "needs_input", "blocked"]

RegisteredMatchBasis = Literal[
    "name_exact",
    "same_source",
    "same_column",
    "same_endpoints",
    "synonym_exact",
]


@dataclass(frozen=True)
class RegisteredMatch:
    ref: str
    basis: RegisteredMatchBasis


@dataclass(frozen=True)
class PrimaryKeyCandidate:
    columns: tuple[str, ...]
    sampled_unique: bool
    distinct_ratio: float


@dataclass(frozen=True)
class VersioningHints:
    snapshot_partition: str | None
    cadence_estimate: str | None
    validity_pair: tuple[str, str] | None


@dataclass(frozen=True)
class DomainBriefSummary:
    name: str
    description: str | None
    default: bool
    object_counts: dict[str, int]


class _BriefResult:
    """Shared AgentResult rendering for authoring briefs.

    Subclasses are frozen dataclasses that expose ``status``, ``questions``,
    and ``issues`` and implement ``_repr_identity``. This mixin is local to
    the brief family; it is not a cross-module result base.
    """

    status: BriefStatus
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        raise NotImplementedError

    def render(self) -> str:
        return format_bounded_card(
            identity=self._repr_identity(),
            status=f"questions={len(self.questions)} issues={len(self.issues)}",
            available=(".render()", ".show()"),
        )

    def __repr__(self) -> str:
        return result_repr(self._repr_identity())

    def show(self) -> None:
        print(self.render())


@dataclass(frozen=True, repr=False)
class DomainBrief(_BriefResult):
    status: BriefStatus
    proposed_name: str
    existing_domains: tuple[DomainBriefSummary, ...]
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        return f"DomainBrief proposed_name={self.proposed_name} status={self.status}"


@dataclass(frozen=True, repr=False)
class EntityBrief(_BriefResult):
    status: BriefStatus
    datasource: str
    source: EntitySourceIR  # from marivo.datasource.ir
    domain: str
    table: TableMetadata  # from marivo.datasource.metadata
    column_profiles: tuple[ScanColumnProfile, ...]  # from marivo.datasource.scan
    primary_key_candidates: tuple[PrimaryKeyCandidate, ...]
    versioning_hints: VersioningHints
    time_like_columns: tuple[str, ...]
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport  # from marivo.datasource.scan

    def _repr_identity(self) -> str:
        return f"EntityBrief domain={self.domain} datasource={self.datasource} status={self.status}"


@dataclass(frozen=True)
class FormatCandidate:
    strptime: str
    match_rate: float
    backend_caveats: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class DimensionBrief(_BriefResult):
    status: BriefStatus
    entity: str
    column: str
    profile: ScanColumnProfile  # from marivo.datasource.scan
    value_shape: Literal[
        "enum_like", "id_like", "numeric", "boolean_like", "temporal_like", "free_text"
    ]
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport  # from marivo.datasource.scan

    def _repr_identity(self) -> str:
        return f"DimensionBrief entity={self.entity} column={self.column} status={self.status}"


@dataclass(frozen=True, repr=False)
class TimeDimensionBrief(_BriefResult):
    status: BriefStatus
    entity: str
    column: str
    profile: ScanColumnProfile
    detected_formats: tuple[FormatCandidate, ...]
    value_range: tuple[object | None, object | None]
    partition_aligned: bool
    granularity_evidence: str | None
    cadence_estimate: tuple[int, str] | None
    existing_time_dimensions: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

    def _repr_identity(self) -> str:
        return f"TimeDimensionBrief entity={self.entity} column={self.column} status={self.status}"


@dataclass(frozen=True)
class DimensionValueFact:
    dimension: str
    top_values: tuple[tuple[object, int], ...]


@dataclass(frozen=True, repr=False)
class MetricBrief(_BriefResult):
    status: BriefStatus
    entity: str
    measure_profiles: tuple[ScanColumnProfile, ...]
    filter_dimension_values: tuple[DimensionValueFact, ...]
    time_dimensions: tuple[str, ...]
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

    def _repr_identity(self) -> str:
        return f"MetricBrief entity={self.entity} status={self.status}"


@dataclass(frozen=True, repr=False)
class RelationshipBrief(_BriefResult):
    status: BriefStatus
    from_entity: str
    to_entity: str
    from_dimensions: tuple[str, ...]
    to_dimensions: tuple[str, ...]
    probe: JoinKeyProbe  # from marivo.datasource.scan
    to_entity_versioning: str | None
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        return f"RelationshipBrief from={self.from_entity} to={self.to_entity} status={self.status}"


@dataclass(frozen=True)
class JoinPathFact:
    from_ref: str
    to_ref: str
    relationship: str
    cardinality: str
    fanout_risk: bool


@dataclass(frozen=True, repr=False)
class CrossEntityMetricBrief(_BriefResult):
    status: BriefStatus
    root_entity: str
    entities: tuple[str, ...]
    join_paths: tuple[JoinPathFact, ...]
    unreachable_entities: tuple[str, ...]
    measure_profiles: tuple[ScanColumnProfile, ...]
    root_time_dimensions: tuple[str, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]
    scan: ScanReport

    def _repr_identity(self) -> str:
        return f"CrossEntityMetricBrief root_entity={self.root_entity} status={self.status}"


@dataclass(frozen=True)
class ComponentFact:
    ref: str
    role: Literal["numerator", "denominator", "weight"]
    additivity: str
    decomposition_kind: str
    verification_status: str
    unit: str | None


@dataclass(frozen=True, repr=False)
class DerivedMetricBrief(_BriefResult):
    status: BriefStatus
    decomposition_kind: Literal["ratio", "weighted_average"]
    components: tuple[ComponentFact, ...]
    propagated_verification: str
    unit_hint: str | None
    matches: tuple[RegisteredMatch, ...]
    questions: tuple[AuthoringQuestion, ...]
    issues: tuple[AssessmentIssue, ...]

    def _repr_identity(self) -> str:
        return f"DerivedMetricBrief decomposition={self.decomposition_kind} status={self.status}"


@dataclass(frozen=True)
class VerifyResult:
    status: Literal["passed", "failed"]
    ref: str
    kind: AuthoringObjectKind
    issues: tuple[AssessmentIssue, ...]
    warnings: tuple[AssessmentIssue, ...]
    scan: ScanReport | None
    auto_recorded: tuple[str, ...]

    def __repr__(self) -> str:
        return f"<VerifyResult status={self.status} ref={self.ref} kind={self.kind}>"

    def render(self) -> str:
        return (
            f"VerifyResult status={self.status} ref={self.ref} kind={self.kind} "
            f"issues={len(self.issues)} warnings={len(self.warnings)}"
        )

    def show(self) -> None:
        print(self.render())
