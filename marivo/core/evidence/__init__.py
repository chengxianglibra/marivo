"""Pure computation for the evidence pipeline.

Extracted from ``marivo.evidence_engine`` as part of Phase 3c.  All functions
here are pure: they accept pre-loaded data and return deterministic results
without accessing databases, repositories, or other I/O-bound services.

Modules
-------
finding_extraction
    Artifact-to-finding extraction logic for all canonical finding families.
proposition_seeding
    Proposition materialization rules (T1-T6 templates) and key parsing.
assessment
    Assessment status resolution, canonical diff, and confidence shaping.
"""

from marivo.core.evidence.assessment import (
    compute_canonical_diff,
    evaluate_calendar_alignment_requirements,
    make_assessment_id,
    resolve_assessment_status,
)
from marivo.core.evidence.family_contract import (
    ALLOWS_EMPTY_ARTIFACT_TYPES,
    FAMILY_ALLOWS_EMPTY,
    ArtifactFamily,
    FamilyEmptyError,
    check_finding_count,
)
from marivo.core.evidence.finding_extraction import (
    extract_compare_findings,
    extract_correlate_findings,
    extract_decompose_findings,
    extract_detect_findings,
    extract_forecast_findings,
    extract_observe_findings,
    extract_test_findings,
    make_finding_id,
    make_item_identity,
    to_float_or_none,
)
from marivo.core.evidence.proposition_seeding import (
    bilateral_focus_anchor,
    canonical_subject_key,
    decode_seg_component,
    parse_correlation_join_basis,
    parse_segment_key,
)

__all__ = [
    "ALLOWS_EMPTY_ARTIFACT_TYPES",
    "FAMILY_ALLOWS_EMPTY",
    "ArtifactFamily",
    "FamilyEmptyError",
    "bilateral_focus_anchor",
    "canonical_subject_key",
    "check_finding_count",
    "compute_canonical_diff",
    "decode_seg_component",
    "evaluate_calendar_alignment_requirements",
    "extract_compare_findings",
    "extract_correlate_findings",
    "extract_decompose_findings",
    "extract_detect_findings",
    "extract_forecast_findings",
    "extract_observe_findings",
    "extract_test_findings",
    "make_assessment_id",
    "make_finding_id",
    "make_item_identity",
    "parse_correlation_join_basis",
    "parse_segment_key",
    "resolve_assessment_status",
    "to_float_or_none",
]
