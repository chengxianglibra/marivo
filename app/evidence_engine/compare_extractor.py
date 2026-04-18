"""compare artifact → delta finding extractor (Phase 4d-3).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2.

Artifact type: ``"compare_artifact"``   Schema version: ``"v1"``   Family: ``"compare"``

Maps two ``comparison_type`` variants to :class:`DeltaFinding`:

- ``scalar_delta``    → 1 finding (:class:`DeltaPayload`, ``delta_kind="scalar_delta"``)
- ``segmented_delta`` → 1 finding per row (:class:`DeltaPayload`, ``delta_kind="segmented_delta"``)

Empty semantics (D4):
---------------------
``compare`` does NOT allow success-empty.  ``validate_for_commit("compare", result)``
raises :class:`FamilyEmptyError` if ``finding_count == 0``.  The runner already
fails with ``NOT_COMPARABLE`` before writing an empty segmented artifact, so this
gate is a belt-and-suspenders check at the commit boundary.

left_ref / right_ref artifact_id limitation (v1):
-------------------------------------------------
The v1 ``compare_artifact`` payload stores ``left_ref`` and ``right_ref`` as step
refs (``session_id``, ``step_id``, ``step_type``) without embedding the upstream
observation ``artifact_id``.  The extractor therefore cannot resolve the upstream
artifact IDs without accessing session state, which is outside its authority
boundary.  ``DeltaPayload.left_ref.artifact_id`` and ``.right_ref.artifact_id``
are set to ``""`` as a v1 placeholder.  This will be resolved when the compare
runner is updated to embed artifact IDs in its output refs.
"""

from __future__ import annotations

from typing import Any, cast

from app.evidence_engine.canonical_finding import (
    ArtifactItemRefRef,
    DeltaDirection,
    DeltaFinding,
    DeltaPayload,
    FindingExtractionResult,
    FindingProvenance,
    FindingQuality,
    FindingSubject,
    StepRef,
    make_finding_id,
    make_item_identity,
)
from app.evidence_engine.finding_extractor_registry import FindingExtractor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset({"increase", "decrease", "flat", "undefined"})
_VALID_PRESENCES = frozenset({"both", "left_only", "right_only"})


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


def _extract_comparability_payload(payload: dict[str, Any]) -> dict[str, Any]:
    comparability = payload.get("comparability")
    resolved = payload.get("resolved_input_summary") or {}
    calendar_alignment = resolved.get("calendar_alignment")

    extracted: dict[str, Any] = {}
    if isinstance(comparability, dict):
        extracted["comparability"] = {
            "status": comparability.get("status") or "needs_attention",
            "issues": list(comparability.get("issues") or []),
        }
    if isinstance(calendar_alignment, dict):
        extracted["calendar_alignment"] = dict(calendar_alignment)
    return extracted


def _attach_comparability_payload(
    delta_payload: DeltaPayload,
    comparability_payload: dict[str, Any],
) -> DeltaPayload:
    comparability = comparability_payload.get("comparability")
    if comparability is not None:
        delta_payload["comparability"] = comparability
    calendar_alignment = comparability_payload.get("calendar_alignment")
    if calendar_alignment is not None:
        delta_payload["calendar_alignment"] = calendar_alignment
    return delta_payload


def _escape_seg_component(s: str) -> str:
    """Percent-encode segment key separators to prevent key collisions.

    Mirrors the same encoding used in observe_extractor and detect_extractor:
    the stable segment key format is ``k=v|k=v``; values containing ``|`` or
    ``=`` must be escaped to avoid collisions between distinct segments.
    Escaping order matters: ``%`` must be escaped first to avoid double-encoding.
    """
    return s.replace("%", "%25").replace("|", "%7C").replace("=", "%3D")


def _segment_stable_key(keys: dict[str, Any]) -> str:
    """Derive a stable segment key from a dimension key-value dict.

    Produces a deterministic ``k=v|k=v`` string from sorted dimension KV pairs,
    with each component percent-encoded.  Mirrors observe_extractor behaviour so
    that delta findings for a given segment are stable under re-extraction.
    """
    return "|".join(
        f"{_escape_seg_component(str(k))}={_escape_seg_component(str(v))}"
        for k, v in sorted(keys.items())
    )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class CompareArtifactExtractor(FindingExtractor):
    """Extract :class:`DeltaFinding`\\s from ``compare_artifact`` artifacts."""

    artifact_type = "compare_artifact"
    artifact_schema_version = "v1"
    family = "compare"
    extractor_name = "compare_artifact_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        comparison_type: str = artifact_payload.get("comparison_type") or ""

        if comparison_type == "scalar_delta":
            findings = self._extract_scalar_delta(artifact_id, artifact_payload, step_ref)
        elif comparison_type == "segmented_delta":
            findings = self._extract_segmented_delta(artifact_id, artifact_payload, step_ref)
        else:
            raise ValueError(
                f"CompareArtifactExtractor: unknown comparison_type={comparison_type!r}. "
                "Expected one of: scalar_delta, segmented_delta."
            )

        return FindingExtractionResult(
            findings=findings,  # type: ignore[typeddict-item]
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            finding_count=len(findings),
        )

    # ------------------------------------------------------------------
    # Private per-mode helpers
    # ------------------------------------------------------------------

    def _make_provenance(
        self,
        step_ref: StepRef,
        canonical_item_key: str,
        item_ref: Any,
    ) -> FindingProvenance:
        return FindingProvenance(
            source_step_type=step_ref["step_type"],
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            artifact_schema_version=self.artifact_schema_version,
            canonical_item_key=canonical_item_key,
            artifact_item_ref=item_ref,
            projection_ref=None,
        )

    def _extract_scalar_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        canonical_item_key, item_ref = make_item_identity("result")
        finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

        resolved = payload.get("resolved_input_summary") or {}
        left_scope: dict[str, Any] = resolved.get("left_scope") or {}
        left_time_scope = resolved.get("left_time_scope")

        direction_raw = payload.get("direction") or "undefined"
        direction = cast(
            "DeltaDirection",
            direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
        )

        # v1 limitation: obs artifact_ids are not embedded in compare_artifact payload.
        # Both left_ref and right_ref use artifact_id="" as placeholder (see module docstring).
        # v1: obs artifact_ids not embedded; both refs use the same placeholder item_ref.
        _, obs_item_ref = make_item_identity("value")
        left_ref = ArtifactItemRefRef(artifact_id="", item_ref=obs_item_ref)
        right_ref = ArtifactItemRefRef(artifact_id="", item_ref=obs_item_ref)
        comparability_payload = _extract_comparability_payload(payload)

        delta_payload = DeltaPayload(
            delta_kind="scalar_delta",
            left_ref=left_ref,
            right_ref=right_ref,
            left_value=_to_float_or_none(payload.get("left_value")),
            right_value=_to_float_or_none(payload.get("right_value")),
            absolute_delta=_to_float_or_none(payload.get("absolute_delta")),
            relative_delta=_to_float_or_none(payload.get("relative_delta")),
            direction=direction,
            presence="both",  # scalar_delta always compares two defined scopes
            unit=payload.get("unit"),
        )
        delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

        finding = DeltaFinding(
            finding_id=finding_id,
            finding_type="delta",
            artifact_id=artifact_id,
            step_ref=step_ref,
            subject=FindingSubject(
                metric=payload.get("metric"),
                entity=None,
                slice=left_scope,
                grain=None,
                analysis_axis="scalar",
            ),
            observed_window=left_time_scope,
            quality=_empty_quality(),
            provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
            payload=delta_payload,
        )
        return [finding]

    def _extract_segmented_delta(
        self,
        artifact_id: str,
        payload: dict[str, Any],
        step_ref: StepRef,
    ) -> list[DeltaFinding]:
        rows: list[dict[str, Any]] = payload.get("rows") or []
        metric: str | None = payload.get("metric")
        unit: str | None = payload.get("unit")
        resolved = payload.get("resolved_input_summary") or {}
        left_time_scope = resolved.get("left_time_scope")
        comparability_payload = _extract_comparability_payload(payload)

        findings: list[DeltaFinding] = []
        for row in rows:
            keys: dict[str, Any] = row.get("keys") or {}
            stable_key = _segment_stable_key(keys)

            canonical_item_key, item_ref = make_item_identity("rows", key=stable_key)
            finding_id = make_finding_id(artifact_id, "delta", canonical_item_key)

            direction_raw = row.get("direction") or "undefined"
            direction = cast(
                "DeltaDirection",
                direction_raw if direction_raw in _VALID_DIRECTIONS else "undefined",
            )

            presence_raw = row.get("presence")
            presence = presence_raw if presence_raw in _VALID_PRESENCES else None

            # v1 limitation: obs artifact_ids not available (see module docstring).
            # Separate calls mirror future split when artifact_ids become available.
            _, left_item_ref = make_item_identity("rows", key=stable_key)
            _, right_item_ref = make_item_identity("rows", key=stable_key)
            left_ref = ArtifactItemRefRef(artifact_id="", item_ref=left_item_ref)
            right_ref = ArtifactItemRefRef(artifact_id="", item_ref=right_item_ref)

            delta_payload = DeltaPayload(
                delta_kind="segmented_delta",
                left_ref=left_ref,
                right_ref=right_ref,
                left_value=_to_float_or_none(row.get("left_value")),
                right_value=_to_float_or_none(row.get("right_value")),
                absolute_delta=_to_float_or_none(row.get("absolute_delta")),
                relative_delta=_to_float_or_none(row.get("relative_delta")),
                direction=direction,
                presence=presence,
                unit=unit,
            )
            delta_payload = _attach_comparability_payload(delta_payload, comparability_payload)

            finding = DeltaFinding(
                finding_id=finding_id,
                finding_type="delta",
                artifact_id=artifact_id,
                step_ref=step_ref,
                subject=FindingSubject(
                    metric=metric,
                    entity=None,
                    slice=dict(keys),
                    grain=None,
                    analysis_axis="segment",
                ),
                observed_window=left_time_scope,
                quality=_empty_quality(),
                provenance=self._make_provenance(step_ref, canonical_item_key, item_ref),
                payload=delta_payload,
            )
            findings.append(finding)

        return findings
