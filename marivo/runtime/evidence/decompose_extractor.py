"""attribution_frame artifact → decomposition_item finding extractor (Phase 4d-3).

Registered via ``_bootstrap_finding_extractors()`` in
``finding_extractor_registry.py`` — same bootstrap pattern as 4d-1/4d-2.

Artifact type: ``"attribution_frame"``   Schema version: ``"v1"``   Family: ``"decompose"``

Maps each canonical contribution point in ``payload.series`` to one
:class:`DecompositionItemFinding`.

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
- ``key_escaped`` = percent-encoded string representation of ``row["keys"][dimension]``

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

``compare_artifact_id`` is taken from canonical ``compare_ref`` if present, then
from compatibility lineage/source aliases emitted by current runtimes.  If no
upstream compare artifact id can be found, extraction fails with a ``ValueError``.

Rank:
-----
``payload.series`` in the ``attribution_frame`` artifact is already sorted canonically
(``abs(contribution_pct) desc, abs(contribution_abs) desc, key asc``).
The extractor assigns 1-based ``rank`` from this preserved sort order.
"""

from __future__ import annotations

from typing import Any, cast

from marivo.core.evidence.canonical_finding import (
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
from marivo.runtime.evidence.finding_extractor_registry import FindingExtractor

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


def _dimension_from_axes(artifact_payload: dict[str, Any]) -> str:
    axes = artifact_payload.get("axes") or []
    for axis in axes:
        if isinstance(axis, dict) and axis.get("kind") == "dimension":
            name = axis.get("name")
            if isinstance(name, str) and name:
                return name
    raise ValueError("DecomposeArtifactExtractor: attribution_frame is missing dimension axis.")


def _iter_ranked_contribution_rows(artifact_payload: dict[str, Any]) -> list[dict[str, Any]]:
    payload = artifact_payload.get("payload") or {}
    series = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(series, list):
        return []

    rows: list[dict[str, Any]] = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        keys = entry.get("keys") or {}
        points = entry.get("points") or []
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            rows.append({"keys": keys, **point})
    return rows


def _extract_artifact_id(ref: Any) -> str:
    if not isinstance(ref, dict):
        return ""
    artifact_id = ref.get("artifact_id")
    return str(artifact_id) if artifact_id else ""


def _compare_ref_from_payload(artifact_payload: dict[str, Any]) -> dict[str, Any]:
    compare_ref = artifact_payload.get("compare_ref")
    if isinstance(compare_ref, dict) and compare_ref.get("artifact_id"):
        return compare_ref

    source_lineage = artifact_payload.get("source_lineage") or {}
    if isinstance(source_lineage, dict):
        source_compare_ref = source_lineage.get("compare_artifact")
        if isinstance(source_compare_ref, dict) and source_compare_ref.get("artifact_id"):
            return source_compare_ref

    source_compare_ref = artifact_payload.get("source_compare_ref")
    if isinstance(source_compare_ref, dict) and source_compare_ref.get("artifact_id"):
        return source_compare_ref

    lineage = artifact_payload.get("lineage") or {}
    if isinstance(lineage, dict):
        source_ids = lineage.get("source_artifact_ids") or []
        if isinstance(source_ids, list) and source_ids:
            source_id = source_ids[0]
            if source_id:
                return {"artifact_id": str(source_id)}

    return {}


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class DecomposeArtifactExtractor(FindingExtractor):
    """Extract :class:`DecompositionItemFinding`\\s from ``attribution_frame`` artifacts."""

    artifact_type = "attribution_frame"
    artifact_schema_version = "v1"
    family = "decompose"
    extractor_name = "attribution_frame_v1"
    extractor_version = "1.0.0"
    finding_schema_version = "v1"

    def extract(
        self,
        artifact_id: str,
        artifact_payload: dict[str, Any],
        step_ref: StepRef,
        session_id: str,
    ) -> FindingExtractionResult:
        dimension = _dimension_from_axes(artifact_payload)

        compare_ref = _compare_ref_from_payload(artifact_payload)
        compare_artifact_id = _extract_artifact_id(compare_ref)
        if not compare_artifact_id:
            raise ValueError(
                "DecomposeArtifactExtractor: upstream compare artifact id is required to compute "
                "scope_delta_ref.finding_id but no compare_ref, lineage.source_artifact_ids, "
                "or source_lineage.compare_artifact id was found."
            )

        compare_type: str = compare_ref.get("shape") or ""
        delta_collection: str
        if compare_type == "time_series_delta":
            delta_collection = "summary"
        elif compare_type in ("", "scalar_delta", "panel_delta"):
            delta_collection = "result"
        else:
            raise ValueError(
                "DecomposeArtifactExtractor: compare_ref.shape="
                f"{compare_type!r} is not supported for scope_delta_ref derivation."
            )

        delta_canonical_key, _ = make_item_identity(cast("Any", delta_collection))
        delta_finding_id = make_finding_id(compare_artifact_id, "delta", delta_canonical_key)
        scope_delta_ref = FindingRef(session_id=session_id, finding_id=delta_finding_id)

        metric: str | None = artifact_payload.get("metric")
        rows = _iter_ranked_contribution_rows(artifact_payload)

        findings: list[DecompositionItemFinding] = []
        for rank_0, row in enumerate(rows):
            keys_raw = row.get("keys") or {}
            keys = keys_raw if isinstance(keys_raw, dict) else {}
            key: Any = keys.get(dimension)
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
                    contribution_value=_to_float_or_none(row.get("contribution_abs")),
                    contribution_share=_to_float_or_none(row.get("contribution_pct")),
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
