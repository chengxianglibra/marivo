# DEPRECATED: Pure extraction logic extracted to app.core.evidence.finding_extraction.extract_decompose_findings.

"""delta_decomposition artifact → decomposition_item finding extractor (Phase 4d-3).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2.

Artifact type: ``"delta_decomposition"``   Schema version: ``"v1"``   Family: ``"decompose"``

Maps each contribution row in ``rows`` to one :class:`DecompositionItemFinding`.

Empty semantics (D4):
---------------------
``decompose`` does NOT allow success-empty.  ``validate_for_commit("decompose", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
fails with ``NOT_ATTRIBUTABLE`` before writing an empty artifact, so this gate is
a belt-and-suspenders check at the commit boundary.

Canonical item key:
-------------------
Each row's stable key is ``"{dim_escaped}:{key_escaped}"``, where:
- ``dim_escaped`` = percent-encoded dimension name
- ``key_escaped`` = percent-encoded string representation of ``row["key"]``

This binds the canonical item boundary to the (dimension, key) pair, consistent
with the design rule ``dimension + normalized key tuple``.

``scope_delta_ref`` derivation:
--------------------------------
The :class:`DecompositionItemPayload` requires a ``scope_delta_ref``
(:class:`FindingRef`) pointing to the upstream ``delta`` finding that this
decomposition item explains.

For ``scalar_delta`` upstream compares, the canonical item key is ``"result"``.
For ``time_series_delta`` upstream compares, ``decompose`` explains the aligned
summary delta, whose canonical item key is ``"summary"``.  Its ``finding_id`` is
therefore deterministic:

    delta_finding_id = make_finding_id(compare_artifact_id, "delta", key)

``compare_artifact_id`` is taken from ``payload["compare_ref"]["artifact_id"]``.
If this field is absent or empty, extraction fails with a ``ValueError`` because
the extractor has no other means to identify the upstream delta finding.

Rank:
-----
``rows`` in the ``delta_decomposition`` artifact are already sorted canonically
(``abs(contribution_share) desc, abs(absolute_contribution) desc, key asc``).
The extractor assigns 1-based ``rank`` from this preserved sort order.
"""

from __future__ import annotations

from typing import Any, cast

from marivo.evidence_engine.canonical_finding import (
    DecompositionItemFinding,
    DecompositionItemPayload,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingRef,
    FindingSubject,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from marivo.evidence_engine.finding_extractor_registry import FindingExtractor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset({"increase", "decrease", "flat", "undefined"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _empty_quality() -> FindingQuality:
    return FindingQuality(
        data_complete=None,
        sample_size=None,
        row_count=None,
        null_rate=None,
        quality_status=None,
        quality_warnings=[],
    )


def _escape_component(s: str) -> str:
    """Percent-encode characters that are structural separators in the item key format.

    The decompose stable key format is ``dim:key``.  Without escaping, a
    dimension name or value containing ``:`` or ``%`` can produce the same key
    string as a different (dimension, key) pair, causing a ``finding_id``
    collision between distinct items.

    Escaping order matters: ``%`` must be escaped first to avoid double-encoding.
    """
    return s.replace("%", "%25").replace(":", "%3A")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class DecomposeArtifactExtractor(FindingExtractor):
    """Extract :class:`DecompositionItemFinding`\\s from ``delta_decomposition`` artifacts."""

    artifact_type = "delta_decomposition"
    artifact_schema_version = "v1"
    family = "decompose"
    extractor_name = "decompose_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        dimension: str = artifact_payload.get("dimension") or ""
        if not dimension:
            raise ValueError(
                "DecomposeArtifactExtractor: artifact payload is missing required 'dimension' field."
            )

        # Resolve scope_delta_ref from compare_ref.artifact_id
        compare_ref: dict[str, Any] = artifact_payload.get("compare_ref") or {}
        compare_artifact_id: str = compare_ref.get("artifact_id") or ""
        if not compare_artifact_id:
            raise ValueError(
                "DecomposeArtifactExtractor: compare_ref.artifact_id is required to compute "
                "scope_delta_ref.finding_id but is absent or empty in the artifact payload."
            )

        compare_type: str = compare_ref.get("comparison_type") or ""
        delta_collection: str
        if compare_type == "time_series_delta":
            delta_collection = "summary"
        elif compare_type in ("", "scalar_delta"):
            delta_collection = "result"
        else:
            raise ValueError(
                "DecomposeArtifactExtractor: compare_ref.comparison_type="
                f"{compare_type!r} is not supported for scope_delta_ref derivation."
            )

        delta_canonical_key, _ = make_item_identity(cast("Any", delta_collection))
        delta_finding_id = make_finding_id(compare_artifact_id, "delta", delta_canonical_key)
        scope_delta_ref = FindingRef(session_id=session_id, finding_id=delta_finding_id)

        metric: str | None = artifact_payload.get("metric")
        rows: list[dict[str, Any]] = artifact_payload.get("rows") or []

        findings: list[DecompositionItemFinding] = []
        for rank_0, row in enumerate(rows):
            key: Any = row.get("key")
            key_str: str = "" if key is None else str(key)
            # Preserve raw typed value in dicts; cast non-JSON-scalar types to str for safety.
            key_typed: str | int | float | bool | None = (
                key if isinstance(key, (str, int, float, bool)) or key is None else str(key)
            )

            stable_key = f"{_escape_component(dimension)}:{_escape_component(key_str)}"
            canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
            finding_id = make_finding_id(artifact_id, "decomposition_item", canonical_item_key)

            direction_raw = row.get("direction") or "undefined"
            direction = direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined"

            provenance = FindingProvenance(
                source_step_type=step_ref["step_type"],
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
                artifact_schema_version=self.artifact_schema_version,
                canonical_item_key=canonical_item_key,
                artifact_item_ref=item_ref,
                projection_ref=None,
            )

            finding = DecompositionItemFinding(
                finding_id=finding_id,
                finding_type="decomposition_item",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice={dimension: key_typed},
                    grain=None,
                    analysis_axis="decomposition",
                ),
                observed_window=None,
                quality=_empty_quality(),
                provenance=provenance,
                payload=DecompositionItemPayload(
                    dimension=dimension,
                    keys={dimension: key_typed},
                    contribution_value=_to_float_or_none(row.get("absolute_contribution")),
                    contribution_share=_to_float_or_none(row.get("contribution_share")),
                    rank=rank_0 + 1,  # 1-based, from artifact canonical sort order
                    direction=direction,  # type: ignore[typeddict-item]
                    scope_delta_ref=scope_delta_ref,
                ),
            )
            findings.append(finding)

        return FindingExtractionResult(
            findings=findings,  # type: ignore[typeddict-item]
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=len(findings),
        )
