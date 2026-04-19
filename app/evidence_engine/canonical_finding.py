"""Canonical finding types and identity helpers for the evidence pipeline.

This module defines the stable Python type contract for the canonical
``finding`` persistence structure:

    artifact -> finding -> proposition -> assessment -> action proposal

All TypedDicts here correspond directly to the schema defined in
``docs/analysis/evidence-engine/schemas/finding.md``.

Identity rules (from artifact-finding-generation-rules.md):
- ``finding_id = stable_hash(artifact_id, finding_type, canonical_item_key)``
- ``canonical_item_key``: stable key first; index only when contract fixes order
- ``rank``, ``extractor_version``, ``projection_ref``, summary text must NOT
  enter ``finding_id`` generation
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal, Required, TypedDict, Union

# ---------------------------------------------------------------------------
# ArtifactItemRef
# ---------------------------------------------------------------------------

ArtifactItemRefCollection = Literal[
    "value", "rows", "buckets", "candidates", "points", "result", "summary"
]


class ArtifactItemRef(TypedDict):
    """Structured reference to a single item inside an artifact payload.

    Rules:
    - When a stable ``key`` is available it MUST be non-null.
    - ``index`` is only allowed when the artifact contract explicitly fixes
      canonical order and no stable key exists.
    - ``index`` must NOT come from a projection-truncated local ordering.
    """

    collection: ArtifactItemRefCollection
    index: int | None
    key: str | None


class ArtifactItemRefRef(TypedDict):
    """Cross-artifact reference used in finding payloads (left_ref / right_ref)."""

    artifact_id: str
    item_ref: ArtifactItemRef


# ---------------------------------------------------------------------------
# StepRef
# ---------------------------------------------------------------------------


class StepRef(TypedDict):
    session_id: str
    step_id: str
    step_type: str


# ---------------------------------------------------------------------------
# FindingSubject
# ---------------------------------------------------------------------------

FindingAnalysisAxis = Literal[
    "scalar", "time", "segment", "decomposition", "correlation", "test", "forecast"
]


class FindingSubject(TypedDict):
    """Semantic anchor of the finding — what metric/entity/slice it is about.

    ``slice`` defaults to ``{}`` (overall / unsliced); must NOT be null.
    """

    metric: str | None
    entity: str | None
    slice: dict[str, str | int | float | bool | None]
    grain: Literal["hour", "day", "week", "month"] | None
    analysis_axis: FindingAnalysisAxis


# ---------------------------------------------------------------------------
# FindingQuality
# ---------------------------------------------------------------------------

FindingQualityStatus = Literal["ready", "needs_attention", "not_ready"]


class FindingQuality(TypedDict):
    """Data-quality metadata only — does NOT express judgment or confidence.

    Null semantics (v1):
    - ``data_complete = None``:  unknown
    - ``sample_size = None``:    not_applicable
    - ``row_count = None``:      not_applicable
    - ``null_rate = None``:      not_applicable
    - ``quality_status = None``: not_applicable
    """

    data_complete: bool | None
    sample_size: int | None
    row_count: int | None
    null_rate: float | None
    quality_status: FindingQualityStatus | None
    quality_warnings: list[str]


# ---------------------------------------------------------------------------
# FindingProvenance
# ---------------------------------------------------------------------------


class FindingProvenance(TypedDict):
    """Provenance / extractor metadata for a canonical finding.

    Reserved stable fields:
    - ``canonical_item_key``: the key used as input to ``make_finding_id``;
      stored separately both here (for structured access) and as a dedicated
      column in the ``findings`` table (for UNIQUE-constrained replay).
    - ``artifact_item_ref``: structured reference to the artifact item.
    - ``extractor_name`` / ``extractor_version``: extractor identity for audit.
    - ``artifact_schema_version``: which artifact contract was used.

    Note: ``extractor_version`` and ``artifact_schema_version`` are part of
    provenance/audit only — they do NOT enter ``finding_id`` generation.
    """

    source_step_type: str
    extractor_name: str
    extractor_version: str
    artifact_schema_version: str | None
    canonical_item_key: str  # stable key used as input to make_finding_id
    artifact_item_ref: ArtifactItemRef
    projection_ref: str | None


# ---------------------------------------------------------------------------
# ResolvedTimeScope
# ---------------------------------------------------------------------------


class ResolvedTimeScope(TypedDict, total=False):
    """Kind-discriminated resolved time scope.

    Required field: ``kind``.
    Variant fields depend on ``kind``:
    - ``range``:            ``start``, ``end``
    - ``snapshot_now``:     ``observed_at``
    - ``latest_available``: ``data_as_of``
    - ``as_of``:            ``at``
    """

    kind: Required[Literal["range", "snapshot_now", "latest_available", "as_of"]]
    # range
    start: str
    end: str
    # snapshot_now
    observed_at: str
    # latest_available
    data_as_of: str
    # as_of
    at: str


# ---------------------------------------------------------------------------
# FindingRef
# ---------------------------------------------------------------------------


class FindingRef(TypedDict):
    session_id: str
    finding_id: str


# ---------------------------------------------------------------------------
# FindingBase
# ---------------------------------------------------------------------------

FindingType = Literal[
    "observation",
    "delta",
    "decomposition_item",
    "anomaly_candidate",
    "correlation_result",
    "test_result",
    "forecast_point",
]


class FindingBase(TypedDict):
    """Common fields shared by all canonical finding subtypes.

    The ``payload`` field is typed as ``dict[str, Any]`` at the base level;
    concrete subtypes refine it to the appropriate payload TypedDict.
    """

    finding_id: str
    finding_type: FindingType
    artifact_id: str
    step_ref: StepRef
    subject: FindingSubject
    observed_window: ResolvedTimeScope | None
    quality: FindingQuality
    provenance: FindingProvenance
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Observation payload subtypes
# ---------------------------------------------------------------------------


class ScalarObservationPayload(TypedDict):
    observation_kind: Literal["scalar"]
    value: float | None
    unit: str | None


class TimeBucketObservationPayload(TypedDict):
    observation_kind: Literal["time_bucket"]
    bucket_start: str
    bucket_end: str
    value: float | None
    unit: str | None


class SegmentObservationPayload(TypedDict):
    observation_kind: Literal["segment"]
    keys: dict[str, str | int | float | bool | None]
    value: float | None
    unit: str | None
    rank: int | None


class SampleSummaryObservationPayload(TypedDict):
    observation_kind: Literal["sample_summary"]
    sample_kind: Literal["numeric", "rate"]
    summary: dict[str, float | None]


ObservationPayload = (
    ScalarObservationPayload
    | TimeBucketObservationPayload
    | SegmentObservationPayload
    | SampleSummaryObservationPayload
)


# ---------------------------------------------------------------------------
# Delta payload
# ---------------------------------------------------------------------------

DeltaKind = Literal["scalar_delta", "segmented_delta", "time_series_delta"]
DeltaDirection = Literal["increase", "decrease", "flat", "undefined"]
DeltaPresence = Literal["both", "left_only", "right_only"]


class CalendarAlignmentCoverageSummary(TypedDict):
    aligned_bucket_count: int
    unpaired_bucket_count: int
    aligned_ratio: float


class MetricDataCoverageSummary(TypedDict, total=False):
    expected_bucket_count: int
    present_bucket_count: int
    missing_bucket_count: int
    coverage_ratio: float
    aligned_expected_bucket_count: int
    aligned_present_current_bucket_count: int
    aligned_present_baseline_bucket_count: int
    aligned_present_both_bucket_count: int


class CalendarAlignmentReuseSummary(TypedDict):
    reuse_source: str
    policy_ref: str
    comparison_basis: str
    resolved_calendar_source: str
    resolved_calendar_version: str
    comparability_warnings: list[str]
    rollup_safe: bool
    left_coverage_summary: CalendarAlignmentCoverageSummary
    right_coverage_summary: CalendarAlignmentCoverageSummary
    effective_coverage_summary: CalendarAlignmentCoverageSummary
    left_data_coverage_summary: MetricDataCoverageSummary | None
    right_data_coverage_summary: MetricDataCoverageSummary | None
    effective_data_coverage_summary: MetricDataCoverageSummary | None


class ComparabilityIssue(TypedDict, total=False):
    code: str
    severity: str
    message: str
    details: dict[str, Any]


class ComparabilitySummary(TypedDict):
    status: str
    issues: list[ComparabilityIssue]


class DeltaPayloadBase(TypedDict):
    """Required delta payload fields shared by all delta findings."""

    delta_kind: DeltaKind
    left_ref: ArtifactItemRefRef
    right_ref: ArtifactItemRefRef
    left_value: float | None
    right_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    direction: DeltaDirection
    presence: DeltaPresence | None
    unit: str | None


class DeltaPayload(DeltaPayloadBase, total=False):
    comparability: ComparabilitySummary
    calendar_alignment: CalendarAlignmentReuseSummary


# ---------------------------------------------------------------------------
# Decomposition item payload
# ---------------------------------------------------------------------------


class DecompositionItemPayload(TypedDict):
    dimension: str
    keys: dict[str, str | int | float | bool | None]
    contribution_value: float | None
    contribution_share: float | None
    rank: int | None
    direction: DeltaDirection
    scope_delta_ref: FindingRef


# ---------------------------------------------------------------------------
# Anomaly candidate payload
# ---------------------------------------------------------------------------

FlagLevel = Literal["high", "medium", "low"]


class AnomalyCandidatePayload(TypedDict):
    candidate_ref: ArtifactItemRefRef
    score: float | None
    flag_level: FlagLevel | None
    actual_value: float | None
    expected_value: float | None
    deviation_absolute: float | None
    deviation_relative: float | None


# ---------------------------------------------------------------------------
# Correlation result payload
# ---------------------------------------------------------------------------

CorrelationMethod = Literal["pearson", "spearman"]


class CorrelationResultPayload(TypedDict):
    left_ref: ArtifactItemRefRef
    right_ref: ArtifactItemRefRef
    method: CorrelationMethod
    coefficient: float | None
    p_value: float | None
    n: int | None
    join_basis: str | None


# ---------------------------------------------------------------------------
# Test result payload
# ---------------------------------------------------------------------------

TestMethod = Literal["welch_t", "two_proportion_z"]
StatisticName = Literal["t", "z"]


class TestResultPayloadBase(TypedDict):
    left_ref: ArtifactItemRefRef
    right_ref: ArtifactItemRefRef
    method: str
    estimate_value: float | None
    statistic_name: str
    statistic_value: float | None
    p_value: float | None
    reject_null: bool | None
    alpha: float


class TestResultPayload(TestResultPayloadBase, total=False):
    comparability: ComparabilitySummary
    calendar_alignment: CalendarAlignmentReuseSummary


# ---------------------------------------------------------------------------
# Forecast point payload
# ---------------------------------------------------------------------------


class PredictionInterval(TypedDict):
    lower: float | None
    upper: float | None
    level: float | None


class ForecastPointPayload(TypedDict):
    bucket_start: str
    bucket_end: str
    predicted_value: float | None
    prediction_interval: PredictionInterval | None
    horizon_index: int


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

_FINDING_ID_PREFIX = "fnd_"
_FINDING_ID_HASH_LEN = 24  # hex chars from SHA-256 digest


def make_canonical_item_key(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> str:
    """Build the canonical item key for a single artifact item.

    Priority rules (from artifact-finding-generation-rules.md D2):
    1. If ``key`` is not None → ``f"{collection}:{key}"``
    2. If ``index`` is not None → ``f"{collection}:{index}"``
    3. Otherwise → ``collection`` (used for single-item artifacts like
       scalar or overall-result artifacts)

    The caller must only pass ``index`` when the artifact contract
    explicitly fixes canonical order.  Do NOT pass an index derived
    from a projection-truncated local ordering.
    """
    if key is not None:
        return f"{collection}:{key}"
    if index is not None:
        return f"{collection}:{index}"
    return collection


def make_finding_id(
    artifact_id: str,
    finding_type: str,
    canonical_item_key: str,
) -> str:
    """Generate a stable, deterministic finding_id.

    Formula:
        finding_id = "fnd_" + sha256(f"{artifact_id}|{finding_type}|{canonical_item_key}")[:24]

    Inputs that must NOT be passed here:
    - ``extractor_version``
    - ``artifact_schema_version``
    - ``projection_ref``
    - ``rank`` or any summary/explanation text

    The same (artifact_id, finding_type, canonical_item_key) triple must
    always produce the same finding_id, enabling safe replay / idempotency.
    """
    raw = f"{artifact_id}|{finding_type}|{canonical_item_key}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{_FINDING_ID_PREFIX}{digest[:_FINDING_ID_HASH_LEN]}"


def make_artifact_item_ref(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> ArtifactItemRef:
    """Build an ArtifactItemRef applying the same D2 priority rules as make_canonical_item_key.

    Priority (from artifact-finding-generation-rules.md D2):
    1. If ``key`` is not None → ``ArtifactItemRef(collection, index=None, key=key)``
    2. If ``key`` is None and ``index`` is not None → ``ArtifactItemRef(collection, index=index, key=None)``
    3. Both None → ``ArtifactItemRef(collection, index=None, key=None)``

    The schema rule *"有稳定 key 时，index 必须为 None"* is enforced here:
    ``index`` is always set to ``None`` in the returned ref when a stable ``key``
    is provided, matching the string produced by ``make_canonical_item_key``.

    Use ``make_item_identity`` to generate both this ref and the canonical_item_key
    string in a single call.
    """
    if key is not None:
        return ArtifactItemRef(collection=collection, index=None, key=key)
    if index is not None:
        return ArtifactItemRef(collection=collection, index=index, key=None)
    return ArtifactItemRef(collection=collection, index=None, key=None)


def make_item_identity(
    collection: ArtifactItemRefCollection,
    key: str | None = None,
    index: int | None = None,
) -> tuple[str, ArtifactItemRef]:
    """Co-generate canonical_item_key and ArtifactItemRef atomically.

    Both outputs are derived from the same (collection, key, index) inputs
    using the identical D2 priority rule, guaranteeing they always agree:

        canonical_item_key, artifact_item_ref = make_item_identity(collection, key=k)

    Extractors should call this instead of calling ``make_canonical_item_key``
    and ``make_artifact_item_ref`` separately, which would risk divergence if
    different priority branches were accidentally chosen for each.

    Returns
    -------
    canonical_item_key : str
        The string key used as input to ``make_finding_id``.
    artifact_item_ref : ArtifactItemRef
        The structured ref stored in ``FindingProvenance.artifact_item_ref``.
    """
    return (
        make_canonical_item_key(collection, key=key, index=index),
        make_artifact_item_ref(collection, key=key, index=index),
    )


# ---------------------------------------------------------------------------
# Concrete finding subtypes (Phase 4a-4)
#
# Each subtype narrows FindingBase.finding_type and FindingBase.payload to the
# specific Literal / payload TypedDict for that family.  The # type: ignore
# comments suppress mypy's TypedDict field-override warning; the narrowing is
# intentional and safe at runtime.
# ---------------------------------------------------------------------------


class ObservationFinding(FindingBase):
    finding_type: Literal["observation"]  # type: ignore[misc]
    payload: ObservationPayload  # type: ignore[misc]


class DeltaFinding(FindingBase):
    finding_type: Literal["delta"]  # type: ignore[misc]
    payload: DeltaPayload  # type: ignore[misc]


class DecompositionItemFinding(FindingBase):
    finding_type: Literal["decomposition_item"]  # type: ignore[misc]
    payload: DecompositionItemPayload  # type: ignore[misc]


class AnomalyCandidateFinding(FindingBase):
    finding_type: Literal["anomaly_candidate"]  # type: ignore[misc]
    payload: AnomalyCandidatePayload  # type: ignore[misc]


class CorrelationResultFinding(FindingBase):
    finding_type: Literal["correlation_result"]  # type: ignore[misc]
    payload: CorrelationResultPayload  # type: ignore[misc]


class TestResultFinding(FindingBase):
    finding_type: Literal["test_result"]  # type: ignore[misc]
    payload: TestResultPayload  # type: ignore[misc]


class ForecastPointFinding(FindingBase):
    finding_type: Literal["forecast_point"]  # type: ignore[misc]
    payload: ForecastPointPayload  # type: ignore[misc]


# Union of all concrete finding subtypes — use when a function can return any
# finding family (e.g. extractor output lists).
# typing.Union is used (not X | Y syntax) so that typing.get_args() works
# correctly on Python 3.9 where X | Y produces types.UnionType, not
# typing.Union, and get_args() returns an empty tuple on that form.
AnyFinding = Union[  # noqa: UP007
    ObservationFinding,
    DeltaFinding,
    DecompositionItemFinding,
    AnomalyCandidateFinding,
    CorrelationResultFinding,
    TestResultFinding,
    ForecastPointFinding,
]


# ---------------------------------------------------------------------------
# Extractor output contract (Phase 4a-4)
#
# Every finding extractor must return a FindingExtractionResult.  The
# ``finding_count`` field is intentionally redundant with ``len(findings)``
# so that the commit path can do a cheap empty-semantics check without an
# extra len() call.
# ---------------------------------------------------------------------------


class FindingExtractionResult(TypedDict):
    """Unified output contract for all finding extractors.

    Rules (from artifact-finding-generation-rules.md, D4):
    - ``findings`` must contain exactly ``finding_count`` items.
    - Whether ``finding_count == 0`` is a legal success outcome depends on the
      artifact family; use ``family_contract.check_finding_count`` to validate.
    - ``artifact_schema_version`` mirrors the artifact contract version the
      extractor was applied against; NULL means the artifact predates versioning
      (treat as 'v1' by convention).
    """

    findings: list[AnyFinding]
    extractor_name: str
    extractor_version: str
    artifact_schema_version: str | None
    finding_count: int  # must equal len(findings)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "AnomalyCandidateFinding",
    "AnomalyCandidatePayload",
    "AnyFinding",
    "ArtifactItemRef",
    # Ref types
    "ArtifactItemRefCollection",
    "ArtifactItemRefRef",
    # Correlation payload
    "CorrelationMethod",
    "CorrelationResultFinding",
    "CorrelationResultPayload",
    "DecompositionItemFinding",
    # Decomposition payload
    "DecompositionItemPayload",
    "DeltaDirection",
    "DeltaFinding",
    # Delta payloads
    "DeltaKind",
    "DeltaPayload",
    "DeltaPresence",
    # Subject
    "FindingAnalysisAxis",
    "FindingBase",
    # Extractor output contract (Phase 4a-4)
    "FindingExtractionResult",
    # Provenance
    "FindingProvenance",
    "FindingQuality",
    # Quality
    "FindingQualityStatus",
    # Finding ref
    "FindingRef",
    "FindingSubject",
    # Base
    "FindingType",
    # Anomaly payload
    "FlagLevel",
    "ForecastPointFinding",
    "ForecastPointPayload",
    # Concrete finding subtypes (Phase 4a-4)
    "ObservationFinding",
    "ObservationPayload",
    # Forecast payload
    "PredictionInterval",
    # Time scope
    "ResolvedTimeScope",
    "SampleSummaryObservationPayload",
    # Observation payloads
    "ScalarObservationPayload",
    "SegmentObservationPayload",
    "StatisticName",
    # Step ref
    "StepRef",
    # Test payload
    "TestMethod",
    "TestResultFinding",
    "TestResultPayload",
    "TimeBucketObservationPayload",
    # Identity helpers
    "make_artifact_item_ref",
    "make_canonical_item_key",
    "make_finding_id",
    "make_item_identity",
]
