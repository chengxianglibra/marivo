"""Private immutable v3 Evidence values, independent of eager Evidence results."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from types import UnionType
from typing import Literal, TypeAlias, Union, get_args, get_origin, get_type_hints

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.datasets.descriptors import DatasetFieldId, DatasetFieldIdentity
from marivo.analysis.errors import AnalysisRepair
from marivo.analysis.materialization.contracts import invalid
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.render import Card, RenderableResult

Scalar: TypeAlias = str | int | float | bool | Decimal | date | datetime | None
Number: TypeAlias = int | float | Decimal
FindingType: TypeAlias = Literal[
    "association", "delta", "contribution", "forecast_point", "funnel_delta"
]
IntegrityStatus: TypeAlias = Literal["valid", "invalid", "unverifiable"]
StorageStatus: TypeAlias = Literal["readable", "unauthorized", "missing", "mutated", "unknown"]
IntegrityAxis: TypeAlias = Literal["artifact_integrity", "storage_authority", "evidence_integrity"]
CoordinatePresence: TypeAlias = Literal["matched", "current_only", "baseline_only"]


def _text(value: str, *, limit: int = 4096) -> None:
    if not value or len(value.encode("utf-8")) > limit or "\n" in value or "\r" in value:
        raise invalid("invalid bounded Evidence text")


def _count(value: int, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise invalid("invalid Evidence count")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise invalid("Evidence timestamp is not timezone-aware")


def _matches(value: object, annotation: object) -> bool:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, item) for item in get_args(annotation))
    if origin is Literal:
        return any(type(value) is type(item) and value == item for item in get_args(annotation))
    if origin is tuple:
        arguments = get_args(annotation)
        return (
            type(value) is tuple
            and len(value) <= 4096
            and all(_matches(item, arguments[0]) for item in value)
        )
    if annotation is type(None):
        return value is None
    if annotation is DatasetFieldIdentity:
        return isinstance(value, DatasetFieldIdentity)
    return isinstance(annotation, type) and type(value) is annotation


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class _Value(RenderableResult):
    """Validate exact annotations without coercion, retaining scalar identity."""

    def __post_init__(self) -> None:
        hints = get_type_hints(type(self))
        for field in fields(self):
            value: object = getattr(self, field.name)
            if not _matches(value, hints[field.name]):
                raise invalid("Evidence value does not match its closed field type")
            if type(value) is str:
                if type(self) is FindingCoordinateV1 and field.name == "value":
                    if len(value.encode("utf-8")) > 4096:
                        raise invalid("Finding coordinate scalar byte bound exceeded")
                else:
                    _text(value)
            if type(value) is float and not math.isfinite(value):
                raise invalid("non-finite Evidence number")
            if isinstance(value, Decimal) and not value.is_finite():
                raise invalid("non-finite Evidence decimal")

    def _repr_identity(self) -> str:
        identity: object = getattr(self, "finding_id", getattr(self, "artifact_ref", None))
        if identity is not None:
            return type(self).__name__ + f" ref={str(identity)[:100]}"
        field_id: object = getattr(self, "field_id", None)
        if isinstance(field_id, DatasetFieldId):
            return type(self).__name__ + f" field={field_id.value[:100]}"
        for name in ("producer_id", "evidence_digest", "kind"):
            value: object = getattr(self, name, None)
            if isinstance(value, str):
                return type(self).__name__ + f" {name}={value[:100]}"
        return type(self).__name__ + " immutable"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".show()",))
        for field in fields(self):
            card.field(field.name, repr(getattr(self, field.name))[:256])
        return card


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactEvidenceSummary(_Value):
    quality_summary_digest: str
    typed_issue_digest: str
    evidence_digest: str
    finding_count: int
    finding_set_digest: str

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.finding_count)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactDigest(ArtifactEvidenceSummary):
    artifact_ref: ArtifactRef
    extractor_contract_versions: tuple[str, ...]
    digest_version: Literal["v3"] = "v3"
    evidence_schema: Literal["marivo.dataset_evidence/v1"] = "marivo.dataset_evidence/v1"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactIssueCounts(_Value):
    warning: int
    blocking: int

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.warning)
        _count(self.blocking)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactRevalidationIssue(_Value):
    axis: IntegrityAxis
    kind: str
    safe_message: str
    expected: str | None = None
    received: str | None = None
    repair: AnalysisRepair | None = None

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _text(self.safe_message, limit=1024)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ArtifactRevalidation(_Value):
    artifact_ref: ArtifactRef
    checked_at: datetime
    artifact_integrity: IntegrityStatus
    storage_authority: StorageStatus
    evidence_integrity: IntegrityStatus
    issues: tuple[ArtifactRevalidationIssue, ...] = ()
    revalidation_version: Literal["v2"] = "v2"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _aware(self.checked_at)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FindingCoordinateV1(_Value):
    field_id: DatasetFieldId
    identity: DatasetFieldIdentity
    value: Scalar

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        if self.identity.kind == "entity_identity":
            raise invalid("identity-bearing Finding coordinate")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationFindingSubjectV1(_Value):
    metric_a: DatasetFieldIdentity
    metric_b: DatasetFieldIdentity
    kind: Literal["association"] = "association"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class MetricFindingSubjectV1(_Value):
    metric: DatasetFieldIdentity
    kind: Literal["metric"] = "metric"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FunnelFindingSubjectV1(_Value):
    subject_entity_ref: RefPayloadV1
    pattern_fingerprint: str
    kind: Literal["event_funnel"] = "event_funnel"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        if self.subject_entity_ref.kind is not SemanticKind.ENTITY:
            raise invalid("Funnel Finding subject must identify an Entity")


FindingSubjectV1: TypeAlias = (
    AssociationFindingSubjectV1 | MetricFindingSubjectV1 | FunnelFindingSubjectV1
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FindingDerivationV1(_Value):
    producer_id: str
    extractor_contract_id: str
    extractor_contract_version: str
    source_artifact_refs: tuple[ArtifactRef, ...]
    source_fields: tuple[DatasetFieldId, ...]

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        if len(set(self.source_artifact_refs)) != len(self.source_artifact_refs) or len(
            set(self.source_fields)
        ) != len(self.source_fields):
            raise invalid("duplicate Finding derivation source")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class NoAssociationLagV1(_Value):
    kind: Literal["none"] = "none"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationLagV1(_Value):
    lag_offset: int
    selected_for_pair: bool
    matched_observation_count: int
    lag_boundary_drop_count: int
    kind: Literal["lag"] = "lag"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.matched_observation_count)
        _count(self.lag_boundary_drop_count)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DefinedFindingRatioV1(_Value):
    value: Number
    kind: Literal["defined"] = "defined"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class UndefinedRelativeDeltaV1(_Value):
    reason: Literal["baseline_zero"] = "baseline_zero"
    kind: Literal["undefined"] = "undefined"


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class UndefinedFindingShareV1(_Value):
    reason: Literal["zero_total_delta", "empty_positive_pool", "empty_negative_pool"]
    kind: Literal["undefined"] = "undefined"


RelativeDeltaV1: TypeAlias = DefinedFindingRatioV1 | UndefinedRelativeDeltaV1
FindingShareV1: TypeAlias = DefinedFindingRatioV1 | UndefinedFindingShareV1


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class AssociationFindingValueV1(_Value):
    method: Literal["pearson", "spearman", "kendall"]
    coefficient: Number
    input_observation_count: int
    null_pair_count: int
    complete_pair_count: int
    lag: NoAssociationLagV1 | AssociationLagV1
    causal_claim: Literal["none"] = "none"
    kind: Literal["association"] = "association"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.input_observation_count)
        _count(self.null_pair_count)
        _count(self.complete_pair_count, minimum=2)
        if self.null_pair_count + self.complete_pair_count > self.input_observation_count:
            raise invalid("inconsistent Association pair counts")
        if not -1 <= self.coefficient <= 1:
            raise invalid("Association coefficient outside [-1, 1]")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class DeltaFindingValueV1(_Value):
    coordinate_presence: CoordinatePresence
    current_value: Number
    baseline_value: Number
    delta: Number
    relative_delta: RelativeDeltaV1
    calculation_status: Literal["ok"] = "ok"
    kind: Literal["delta"] = "delta"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        if len({type(self.current_value), type(self.baseline_value), type(self.delta)}) != 1:
            raise invalid("Delta numbers do not preserve one lossless common type")
        if (self.baseline_value == 0) != isinstance(self.relative_delta, UndefinedRelativeDeltaV1):
            raise invalid("relative Delta baseline-zero status mismatch")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ContributionFindingValueV1(_Value):
    method: str
    active_axis_mask: tuple[bool, ...]
    other_mask: tuple[bool, ...]
    contribution_kind: Literal["metric", "loss", "denominator_mix"]
    current_value: Number
    baseline_value: Number
    overall_delta: Number
    contribution: Number
    share_of_total_delta: FindingShareV1
    share_of_positive_pool: FindingShareV1
    share_of_negative_pool: FindingShareV1
    contribution_rank: int
    status: Literal["ok", "zero_total_delta"]
    causal_claim: Literal["none"] = "none"
    kind: Literal["contribution"] = "contribution"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.contribution_rank, minimum=1)
        if len(self.active_axis_mask) != len(self.other_mask) or any(
            other and not active
            for active, other in zip(self.active_axis_mask, self.other_mask, strict=True)
        ):
            raise invalid("inconsistent contribution masks")
        for share, reason in (
            (self.share_of_total_delta, "zero_total_delta"),
            (self.share_of_positive_pool, "empty_positive_pool"),
            (self.share_of_negative_pool, "empty_negative_pool"),
        ):
            if isinstance(share, UndefinedFindingShareV1) and share.reason != reason:
                raise invalid("share unavailable reason does not match its pool")
        if (self.overall_delta == 0) != (self.status == "zero_total_delta") or (
            self.overall_delta == 0
        ) != isinstance(self.share_of_total_delta, UndefinedFindingShareV1):
            raise invalid("contribution zero-total status mismatch")
        if any(
            isinstance(share, DefinedFindingRatioV1) and share.value < 0
            for share in (
                self.share_of_positive_pool,
                self.share_of_negative_pool,
            )
        ):
            raise invalid("negative contribution pool share")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class ForecastPointFindingValueV1(_Value):
    model: Literal["naive@v1", "drift@v1", "seasonal_naive@v1"]
    interval_level: Number
    horizon_ordinal: int
    forecast_value: Number
    interval_lower: Number
    interval_upper: Number
    training_row_count: int
    interval_method: Literal["normal_residual@v1"] = "normal_residual@v1"
    kind: Literal["forecast_point"] = "forecast_point"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _count(self.horizon_ordinal, minimum=1)
        _count(self.training_row_count, minimum=1)
        if not 0 < self.interval_level < 1 or not (
            self.interval_lower <= self.forecast_value <= self.interval_upper
        ):
            raise invalid("invalid Forecast interval")


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class FunnelDeltaFindingValueV1(_Value):
    step_key: str
    coordinate_presence: CoordinatePresence
    current_cohort_count: int
    baseline_cohort_count: int
    current_resolved_cohort_count: int
    baseline_resolved_cohort_count: int
    current_entry_count: int
    baseline_entry_count: int
    current_resolved_entry_count: int
    baseline_resolved_entry_count: int
    current_reached_count: int
    baseline_reached_count: int
    current_lost_count: int
    baseline_lost_count: int
    current_coverage_censored_count: int
    baseline_coverage_censored_count: int
    current_loss_rate_from_previous: Number
    baseline_loss_rate_from_previous: Number
    loss_rate_delta: Number
    calculation_status: Literal["ok"] = "ok"
    kind: Literal["funnel_delta"] = "funnel_delta"

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        for field in fields(self):
            if field.name.endswith("_count"):
                _count(getattr(self, field.name))
        if self.current_coverage_censored_count != 0 or self.baseline_coverage_censored_count != 0:
            raise invalid("censored Funnel row is ineligible for a Finding")


FindingValueV1: TypeAlias = (
    AssociationFindingValueV1
    | DeltaFindingValueV1
    | ContributionFindingValueV1
    | ForecastPointFindingValueV1
    | FunnelDeltaFindingValueV1
)


@dataclass(frozen=True, slots=True, repr=False, kw_only=True)
class Finding(_Value):
    finding_id: str
    artifact_ref: ArtifactRef
    session_id: str
    finding_type: FindingType
    epistemic_kind: Literal["algebraic", "estimated", "predicted"]
    subject: FindingSubjectV1
    coordinates: tuple[FindingCoordinateV1, ...]
    canonical_item_key: str
    value: FindingValueV1
    derivation: FindingDerivationV1
    committed_at: datetime

    def __post_init__(self) -> None:
        _Value.__post_init__(self)
        _aware(self.committed_at)
        if self.finding_type != self.value.kind:
            raise invalid("Finding kind does not match its exact value")
        expected = {"association": "estimated", "forecast_point": "predicted"}.get(
            self.finding_type, "algebraic"
        )
        if self.epistemic_kind != expected:
            raise invalid("Finding epistemic kind does not match its producer")
        subject = "metric"
        if self.finding_type == "association":
            subject = "association"
        elif self.finding_type == "funnel_delta" or (
            isinstance(self.value, ContributionFindingValueV1)
            and self.value.contribution_kind != "metric"
        ):
            subject = "event_funnel"
        if self.subject.kind != subject:
            raise invalid("Finding subject does not match its value family")
        if len({item.field_id for item in self.coordinates}) != len(self.coordinates):
            raise invalid("duplicate Finding coordinate")


@dataclass(frozen=True, slots=True, repr=False)
class FindingPage(_BoundedPage[Finding]):
    """Exact Artifact-scoped bounded Finding page."""

    def __post_init__(self) -> None:
        _BoundedPage.__post_init__(self)
        if (
            type(self.limit) is not int
            or type(self.has_more) is not bool
            or type(self.items) is not tuple
        ):
            raise invalid("invalid Finding page type")
        if any(type(item) is not Finding for item in self.items):
            raise invalid("invalid Finding page item")
