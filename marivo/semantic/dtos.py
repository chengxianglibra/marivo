"""Public DTOs for skill-driven semantic authoring and assessment."""

from __future__ import annotations

from dataclasses import dataclass, field
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


def derive_brief_status(
    issues: tuple[AssessmentIssue, ...],
    questions: tuple[AuthoringQuestion, ...],
) -> BriefStatus:
    """Derive BriefStatus from issues and questions.

    Mirrors :func:`derive_status` but returns the ``BriefStatus``
    vocabulary (``"sufficient"`` instead of ``"supported"``).
    """
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
    return "sufficient"


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


# Shared gloss for the common Brief envelope fields. Centralized so the wording
# stays consistent across the eight Brief dataclasses; help('<Brief>') renders
# these via FieldInfo.description.
_STATUS_DOC = (
    "Authoring readiness: 'sufficient' (author one object, then verify_object), "
    "'needs_input' (answer blocking AuthoringQuestions), or 'blocked' (fix the "
    "blocker or record authoring_abandoned)."
)
_QUESTIONS_DOC = "Unresolved business decisions that block authoring until answered."
_ISSUES_DOC = "Structured problems found during preparation."
_MATCHES_DOC = "Already-registered candidates with the basis on which they matched."
_SCAN_DOC = "Scan scope and truncation details for the datasource read."


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
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    proposed_name: str = field(
        metadata={"description": "The domain name passed to prepare_domain."}
    )
    existing_domains: tuple[DomainBriefSummary, ...] = field(
        metadata={"description": "Already-registered domains with descriptions and object counts."}
    )
    matches: tuple[RegisteredMatch, ...] = field(
        metadata={"description": "name_exact or synonym_exact matches against existing domains."}
    )
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})

    def _repr_identity(self) -> str:
        return f"DomainBrief proposed_name={self.proposed_name} status={self.status}"


@dataclass(frozen=True, repr=False)
class EntityBrief(_BriefResult):
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    datasource: str = field(
        metadata={"description": "Datasource name the entity source reads from."}
    )
    source: EntitySourceIR = field(  # from marivo.datasource.ir
        metadata={"description": "Physical source (table or file) for the entity."}
    )
    domain: str = field(metadata={"description": "Target domain name for the entity."})
    table: TableMetadata = field(  # from marivo.datasource.metadata
        metadata={"description": "Full source metadata including columns and partitions."}
    )
    column_profiles: tuple[ScanColumnProfile, ...] = field(  # from marivo.datasource.scan
        metadata={"description": "Bounded-sample profiles for all columns."}
    )
    primary_key_candidates: tuple[PrimaryKeyCandidate, ...] = field(
        metadata={"description": "Columns sampled as unique, candidate primary keys."}
    )
    versioning_hints: VersioningHints = field(
        metadata={"description": "Snapshot, cadence, and validity evidence for the source."}
    )
    time_like_columns: tuple[str, ...] = field(
        metadata={"description": "Columns whose values match temporal formats."}
    )
    matches: tuple[RegisteredMatch, ...] = field(metadata={"description": _MATCHES_DOC})
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})
    scan: ScanReport = field(  # from marivo.datasource.scan
        metadata={"description": _SCAN_DOC}
    )

    def _repr_identity(self) -> str:
        return f"EntityBrief domain={self.domain} datasource={self.datasource} status={self.status}"

    def render(self) -> str:
        profile_rows = [
            [p.column, p.data_type, str(p.distinct_count), str(p.null_count)]
            for p in self.column_profiles[:8]
        ]
        parts: list[str] = [f"questions={len(self.questions)} issues={len(self.issues)}"]
        if self.primary_key_candidates:
            pk_desc = ", ".join(
                "(" + ", ".join(c.columns) + f" distinct={c.distinct_ratio:.2f})"
                for c in self.primary_key_candidates[:5]
            )
            parts.append(f"pk_candidates=[{pk_desc}]")
        if self.time_like_columns:
            parts.append(f"time_like=[{', '.join(self.time_like_columns[:8])}]")
        vh = self.versioning_hints
        vh_parts: list[str] = []
        if vh.snapshot_partition:
            vh_parts.append(f"snapshot={vh.snapshot_partition}")
        if vh.cadence_estimate:
            vh_parts.append(f"cadence={vh.cadence_estimate}")
        if vh.validity_pair:
            vh_parts.append(f"validity={vh.validity_pair[0]}/{vh.validity_pair[1]}")
        if vh_parts:
            parts.append(" ".join(vh_parts))
        return format_bounded_card(
            identity=self._repr_identity(),
            status=" ".join(parts),
            columns=["column", "type", "distinct", "nulls"],
            rows=profile_rows,
            row_count=len(self.column_profiles),
            preview_truncation_hint="inspect .column_profiles for all columns",
            available=(".render()", ".show()"),
        )


@dataclass(frozen=True)
class FormatCandidate:
    strptime: str
    match_rate: float
    backend_caveats: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class DimensionBrief(_BriefResult):
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    entity: str = field(metadata={"description": "Entity ref the dimension column belongs to."})
    column: str = field(metadata={"description": "The inspected source column."})
    profile: ScanColumnProfile = field(  # from marivo.datasource.scan
        metadata={"description": "Bounded-sample profile for the column."}
    )
    value_shape: Literal[
        "enum_like", "id_like", "numeric", "boolean_like", "temporal_like", "free_text"
    ] = field(metadata={"description": "Inferred value shape guiding the dimension kind."})
    matches: tuple[RegisteredMatch, ...] = field(metadata={"description": _MATCHES_DOC})
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})
    scan: ScanReport = field(  # from marivo.datasource.scan
        metadata={"description": _SCAN_DOC}
    )

    def _repr_identity(self) -> str:
        return f"DimensionBrief entity={self.entity} column={self.column} status={self.status}"


@dataclass(frozen=True, repr=False)
class TimeDimensionBrief(_BriefResult):
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    entity: str = field(metadata={"description": "Entity ref the time column belongs to."})
    column: str = field(metadata={"description": "The inspected source column."})
    profile: ScanColumnProfile = field(
        metadata={"description": "Bounded-sample profile for the column."}
    )
    detected_formats: tuple[FormatCandidate, ...] = field(
        metadata={"description": "strptime format matches with backend caveats."}
    )
    value_range: tuple[object | None, object | None] = field(
        metadata={"description": "Sample-local (min, max) of the column."}
    )
    partition_aligned: bool = field(
        metadata={"description": "Whether this column is a partition key of the source."}
    )
    granularity_evidence: str | None = field(
        metadata={"description": "Granularity inferred from sampled values, if any."}
    )
    cadence_estimate: tuple[int, str] | None = field(
        metadata={"description": "Sampled interval evidence as (count, unit), if any."}
    )
    existing_time_dimensions: tuple[str, ...] = field(
        metadata={"description": "Time dimensions already registered on this entity."}
    )
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})
    scan: ScanReport = field(metadata={"description": _SCAN_DOC})

    def _repr_identity(self) -> str:
        return f"TimeDimensionBrief entity={self.entity} column={self.column} status={self.status}"


@dataclass(frozen=True)
class DimensionValueFact:
    dimension: str
    top_values: tuple[tuple[object, int], ...]


@dataclass(frozen=True, repr=False)
class MetricBrief(_BriefResult):
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    entity: str = field(metadata={"description": "Entity ref the measure columns belong to."})
    measure_profiles: tuple[ScanColumnProfile, ...] = field(
        metadata={"description": "Range, negatives, and null profiles for the measure columns."}
    )
    filter_dimension_values: tuple[DimensionValueFact, ...] = field(
        metadata={"description": "Top values for any filter dimensions."}
    )
    time_dimensions: tuple[str, ...] = field(
        metadata={
            "description": "Time dimensions on the entity; empty triggers a ladder-order advisory."
        }
    )
    matches: tuple[RegisteredMatch, ...] = field(metadata={"description": _MATCHES_DOC})
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})
    scan: ScanReport = field(metadata={"description": _SCAN_DOC})

    def _repr_identity(self) -> str:
        return f"MetricBrief entity={self.entity} status={self.status}"


@dataclass(frozen=True, repr=False)
class RelationshipBrief(_BriefResult):
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    from_entity: str = field(metadata={"description": "From-side entity ref."})
    to_entity: str = field(metadata={"description": "To-side entity ref."})
    from_dimensions: tuple[str, ...] = field(
        metadata={"description": "From-side join-key dimension refs."}
    )
    to_dimensions: tuple[str, ...] = field(
        metadata={"description": "To-side join-key dimension refs."}
    )
    probe: JoinKeyProbe = field(  # from marivo.datasource.scan
        metadata={"description": "Key match rate, cardinality, and scan reports for the join."}
    )
    to_entity_versioning: str | None = field(
        metadata={"description": "Snapshot or validity interaction note for the to-side entity."}
    )
    matches: tuple[RegisteredMatch, ...] = field(metadata={"description": _MATCHES_DOC})
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})

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
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    root_entity: str = field(metadata={"description": "Root entity ref the metric is measured on."})
    entities: tuple[str, ...] = field(
        metadata={"description": "Target entity refs to join from the root entity."}
    )
    join_paths: tuple[JoinPathFact, ...] = field(
        metadata={"description": "Relationship paths between participating entities."}
    )
    unreachable_entities: tuple[str, ...] = field(
        metadata={"description": "Entities with no relationship path (blocking)."}
    )
    measure_profiles: tuple[ScanColumnProfile, ...] = field(
        metadata={"description": "Profiles for the root-entity measure columns."}
    )
    root_time_dimensions: tuple[str, ...] = field(
        metadata={"description": "Time dimensions on the root entity."}
    )
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})
    scan: ScanReport = field(metadata={"description": _SCAN_DOC})

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
    status: BriefStatus = field(metadata={"description": _STATUS_DOC})
    decomposition_kind: Literal["ratio", "weighted_average"] = field(
        metadata={"description": "Inferred decomposition type from the supplied components."}
    )
    components: tuple[ComponentFact, ...] = field(
        metadata={"description": "Component metrics with additivity and verification facts."}
    )
    propagated_verification: str = field(
        metadata={"description": "Projected verification status derived from components."}
    )
    unit_hint: str | None = field(
        metadata={"description": "Suggested unit inferred from component units, if any."}
    )
    matches: tuple[RegisteredMatch, ...] = field(metadata={"description": _MATCHES_DOC})
    questions: tuple[AuthoringQuestion, ...] = field(metadata={"description": _QUESTIONS_DOC})
    issues: tuple[AssessmentIssue, ...] = field(metadata={"description": _ISSUES_DOC})

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
